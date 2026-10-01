"""Lanzador único: arranca dashboard y puente, los reinicia si se caen o si cambia el código,
y hace la copia de seguridad diaria. Cierra antes cualquier instancia vieja.

    python supervisor.py            (lo usa «Iniciar Anka.bat» y el autoarranque de Windows)
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

import settings
from backup import backup_due, run_backup
from logging_setup import configure_logging, get_logger

log = get_logger(__name__)

ROOT = Path(__file__).resolve().parent
EXIT_STOP = 2                       # el puente pide no reiniciarlo (modo o cuenta incorrectos)
WATCH_EVERY = 3                     # segundos entre revisiones de cambios y de procesos
CHILD_PYTHON = sys.executable.replace("pythonw.exe", "python.exe")
MARKERS = ("app.py review-ui", "work_dashboard_sync.py", "supervisor.py")


def code_snapshot(root: Path = ROOT) -> dict[str, float]:
    """Fecha de modificación del código y las plantillas: si cambia, hay que reiniciar."""
    files = list(root.glob("*.py")) + list((root / "templates").glob("*"))
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


def old_instances(own_pid: int) -> list[int]:
    """PID de dashboards, puentes y lanzadores anteriores (Windows)."""
    script = ("Get-CimInstance Win32_Process | Where-Object { $_.CommandLine } | "
              "Select-Object ProcessId,CommandLine | ConvertTo-Json -Compress")
    try:
        out = subprocess.run(["powershell", "-NoProfile", "-Command", script],
                             capture_output=True, text=True, timeout=30).stdout
        rows = json.loads(out or "[]")
    except (OSError, ValueError, subprocess.SubprocessError):
        return []
    rows = [rows] if isinstance(rows, dict) else rows
    return [int(row["ProcessId"]) for row in rows
            if int(row["ProcessId"]) != own_pid and "python" in row["CommandLine"].lower()
            and any(marker in row["CommandLine"] for marker in MARKERS)]


def stop_old_instances() -> int:
    pids = [pid for pid in old_instances(os.getpid()) if pid != os.getppid()]
    for pid in pids:
        subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"], capture_output=True)
    if pids:
        log.info("Cerradas %d instancia(s) anteriores", len(pids))
        time.sleep(2)  # que el sistema libere el puerto 8765
    return len(pids)


def work_mode() -> bool:
    try:
        return settings.load_config().get("active_mode") == "chatgpt_work"
    except (SystemExit, OSError, ValueError):
        return False


class Child:
    def __init__(self, name: str, args: list[str]):
        self.name, self.args = name, args
        self.process: subprocess.Popen | None = None
        self.failures = 0
        self.started = 0.0
        self.retry_at = 0.0
        self.stopped = False   # salió pidiendo no reiniciar

    def start(self) -> None:
        self.process = subprocess.Popen(
            [CHILD_PYTHON, *self.args], cwd=ROOT,
            creationflags=0x08000000 if sys.stdout is None else 0)  # sin ventana si arranca con Windows
        self.started = time.time()
        log.info("%s iniciado (PID %s)", self.name, self.process.pid)

    def stop(self) -> None:
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


def main() -> None:
    configure_logging(console=sys.stdout is not None)
    stop_old_instances()
    dashboard = Child("Dashboard", ["app.py", "review-ui", "--no-browser"])
    bridge = Child("Puente Work", ["work_dashboard_sync.py", "--seconds", "60"])
    children = [dashboard, bridge]
    snapshot = code_snapshot()
    log.info("Anka iniciado: dashboard en http://127.0.0.1:8765/")
    try:
        while True:
            if code_snapshot() != snapshot:
                time.sleep(2)                       # que terminen de guardarse los archivos
                snapshot = code_snapshot()
                log.info("Cambió el código: reiniciando dashboard y puente")
                for child in children:
                    child.stop()
                    child.stopped, child.failures, child.retry_at = False, 0, 0.0
            if work_mode():
                bridge.check()
            elif bridge.process:
                bridge.stop()                        # cambió a otro modo: el puente sobra
                bridge.stopped = False
            dashboard.check()
            if backup_due():
                run_backup()
            time.sleep(WATCH_EVERY)
    except KeyboardInterrupt:
        log.info("Anka detenido")
    finally:
        for child in children:
            child.stop()


if __name__ == "__main__":
    main()
