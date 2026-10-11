"""Lanzador único: arranca dashboard y puente, los reinicia si se caen o si cambia el código,
y hace la copia de seguridad diaria. Cierra antes cualquier instancia vieja.

    python supervisor.py            (lo usa «Iniciar Anka.bat» y el autoarranque de Windows)

Son dos procesos: un guardián mínimo (`guardian`) que no tiene lógica propia y el servicio (`servicio`) que hace el trabajo.
Cuando cambia el código el servicio sale con `EXIT_RELANZAR` y el guardián lo lanza de nuevo con el código nuevo: así el
lanzador tampoco se queda con código anterior (T-27).
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

from anka.dominio.errores import ErrorDeAnka
from anka.infra import settings
from anka.infra.backup import backup_due, run_backup
from anka.servicios.followup_reminders import run_due_reminders
from anka.infra.logging_setup import configure_logging, get_logger

log = get_logger(__name__)

ROOT = Path(__file__).resolve().parents[2]
EXIT_STOP = 2                       # el puente pide no reiniciarlo (modo o cuenta incorrectos)
EXIT_RELANZAR = 75                  # el servicio pide que el guardián lo lance de nuevo (cambió el código)
SERVICIO = "--servicio"             # argumento con el que el guardián lanza el servicio
REMINDER_EVERY = 300                # segundos entre revisiones de recordatorios
MODE_RECHECK = 30          # segundos entre lecturas de config.json para saber si el puente debe estar activo
WATCH_EVERY = 3                     # segundos entre revisiones de cambios y de procesos
CHILD_PYTHON = sys.executable.replace("pythonw.exe", "python.exe")
MARKERS = ("app.py review-ui", "work_dashboard_sync.py", "supervisor.py", "app.py work-drive-sync")


def code_snapshot(root: Path = ROOT) -> dict[str, float]:
    """Fecha de modificación del código y las plantillas: si cambia, hay que reiniciar."""
    files = (list(root.glob("*.py")) + list((root / "anka").rglob("*.py"))
             + list((root / "anka" / "web" / "templates").glob("*")))
    snapshot = {}
    for path in files:
        try:
            snapshot[str(path)] = path.stat().st_mtime
        except OSError:
            pass
    return snapshot


def backoff(failures: int) -> int:
    """Espera antes de reiniciar un proceso que se cae seguido (2, 4, 8... hasta 60 s)."""
    return min(60, 2 ** max(1, failures))


def instancias_anteriores(rows: list[dict], own_pid: int) -> list[int]:
    """De la lista de procesos, los dashboards, puentes y lanzadores anteriores: nunca el propio ni sus ascendentes.

    El guardián (y su intérprete) son ascendentes del servicio y llevan «supervisor.py» en su línea de comandos: sin esta
    exclusión el servicio cerraría a quien lo vuelve a lanzar.
    """
    parent_of = {int(row["ProcessId"]): int(row.get("ParentProcessId") or 0) for row in rows}
    ancestors, pid = set(), own_pid
    while pid and pid not in ancestors:
        ancestors.add(pid)
        pid = parent_of.get(pid, 0)
    return [int(row["ProcessId"]) for row in rows
            if int(row["ProcessId"]) not in ancestors and "python" in (row.get("CommandLine") or "").lower()
            and any(marker in row["CommandLine"] for marker in MARKERS)]


def old_instances(own_pid: int) -> list[int]:
    """PID de dashboards, puentes y lanzadores anteriores (Windows)."""
    script = ("Get-CimInstance Win32_Process | "
              "Select-Object ProcessId,ParentProcessId,CommandLine | ConvertTo-Json -Compress")
    try:
        out = subprocess.run(["powershell", "-NoProfile", "-Command", script],
                             capture_output=True, text=True, timeout=30).stdout
        rows = json.loads(out or "[]")
    except (OSError, ValueError, subprocess.SubprocessError):
        return []
    return instancias_anteriores([rows] if isinstance(rows, dict) else rows, own_pid)


def stop_old_instances() -> int:
    """Cierra los lanzadores, dashboards y puentes que quedaron de una ejecución anterior; devuelve cuántos cerró."""
    pids = old_instances(os.getpid())
    for pid in pids:
        subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"], capture_output=True)
    if pids:
        log.info("Cerradas %d instancia(s) anteriores", len(pids))
        time.sleep(2)  # que el sistema libere el puerto 8765
    return len(pids)


def work_mode() -> bool:
    """¿Está activo el modo ChatGPT Work? (solo en ese modo hace falta el puente de borradores)."""
    try:
        return settings.load_config().get("active_mode") == "chatgpt_work"
    except (ErrorDeAnka, OSError, ValueError):
        return False


def work_drive_enabled() -> bool:
    """¿Se activó explícitamente el preparador de expedientes del modo Work?"""
    try:
        config = settings.load_config()
        return config.get("active_mode") == "chatgpt_work" and config.get("work_drive_enabled", False)
    except (ErrorDeAnka, OSError, ValueError):
        return False


class Child:
    """Un proceso hijo vigilado: se inicia, se detiene y se reinicia con espera creciente si falla."""
    def __init__(self, name: str, args: list[str]):
        self.name, self.args = name, args
        self.process: subprocess.Popen | None = None
        self.failures = 0
        self.started = 0.0
        self.retry_at = 0.0
        self.stopped = False   # salió pidiendo no reiniciar

    def start(self) -> None:
        """Lanza el proceso hijo (sin ventana si el lanzador arrancó con Windows)."""
        self.process = subprocess.Popen(
            [CHILD_PYTHON, *self.args], cwd=ROOT,
            creationflags=0x08000000 if sys.stdout is None else 0)  # sin ventana si arranca con Windows
        self.started = time.time()
        log.info("%s iniciado (PID %s)", self.name, self.process.pid)

    def stop(self) -> None:
        """Termina el proceso hijo y sus procesos descendientes."""
        if self.process and self.process.poll() is None:
            subprocess.run(["taskkill", "/PID", str(self.process.pid), "/T", "/F"], capture_output=True)
        self.process = None

    def check(self) -> None:
        """Reinicia si murió, con espera creciente si se cae seguido."""
        if self.stopped:
            return
        if self.process is None:
            if time.time() >= self.retry_at:
                self.start()
            return
        code = self.process.poll()
        if code is None:
            if time.time() - self.started > 120:
                self.failures = 0
            return
        self.process = None
        if code == EXIT_STOP:
            self.stopped = True
            log.warning("%s se detuvo pidiendo no reiniciar; revisa su mensaje", self.name)
            return
        self.failures += 1
        wait = backoff(self.failures)
        self.retry_at = time.time() + wait
        log.warning("%s terminó (código %s); se reinicia en %d s", self.name, code, wait)


def servicio() -> int:
    """Arranca dashboard y puente, los reinicia si se caen, copia a diario y pide relanzarse si cambia el código."""
    configure_logging(console=sys.stdout is not None)
    stop_old_instances()
    dashboard = Child("Dashboard", ["app.py", "review-ui", "--no-browser"])
    bridge = Child("Puente Work", ["work_dashboard_sync.py", "--seconds", "30"])
    preparer = Child("Expedientes Work", ["app.py", "work-drive-sync", "--watch-seconds", "60"])
    children = [dashboard, bridge, preparer]
    snapshot = code_snapshot()
    last_reminders = 0.0
    mode_checked, in_work_mode = 0.0, False   # config.json se relee cada MODE_RECHECK s, no en cada vuelta
    prepare_enabled = False
    log.info("Anka iniciado: dashboard en http://127.0.0.1:8765/")
    try:
        while True:
            if code_snapshot() != snapshot:
                time.sleep(2)                       # que terminen de guardarse los archivos
                log.info("Cambió el código: el lanzador se reinicia solo, con el dashboard y el puente")
                return EXIT_RELANZAR                # el `finally` detiene a los hijos; el guardián lanza el código nuevo
            if time.time() - mode_checked >= MODE_RECHECK:
                mode_checked, in_work_mode = time.time(), work_mode()
                prepare_enabled = work_drive_enabled()
            if in_work_mode:
                bridge.check()
            elif bridge.process:
                bridge.stop()                        # cambió a otro modo: el puente sobra
                bridge.stopped = False
            if prepare_enabled:
                preparer.check()
            elif preparer.process:
                preparer.stop()
                preparer.stopped = False
            dashboard.check()
            if backup_due():
                run_backup()
            if time.time() - last_reminders >= REMINDER_EVERY:
                last_reminders = time.time()
                try:
                    run_due_reminders()
                except Exception:
                    log.exception("Error al revisar los recordatorios")
            time.sleep(WATCH_EVERY)
    except KeyboardInterrupt:
        log.info("Anka detenido")
    finally:
        for child in children:
            child.stop()
    return 0


def guardian() -> int:
    """Lanza el servicio y lo vuelve a lanzar cada vez que pide reiniciarse; con otra salida termina igual que él."""
    configure_logging(console=sys.stdout is not None)
    while True:
        started = time.time()
        try:
            code = subprocess.call([CHILD_PYTHON, str(ROOT / "supervisor.py"), SERVICIO], cwd=ROOT,
                                   creationflags=0x08000000 if sys.stdout is None else 0)   # sin ventana con Windows
        except KeyboardInterrupt:
            return 0
        if code != EXIT_RELANZAR:
            return code
        log.info("El lanzador se vuelve a lanzar con el código nuevo")
        if time.time() - started < 5:
            time.sleep(2)                           # que no gire sin descanso si algo lo pide sin parar


def main() -> None:
    """Punto de entrada: el servicio si se pide con `--servicio`; si no, el guardián que lo lanza y relanza."""
    raise SystemExit(servicio() if SERVICIO in sys.argv else guardian())


if __name__ == "__main__":
    main()
