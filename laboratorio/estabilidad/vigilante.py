"""Vigilante de ANKA (T-97): mantiene el lanzador vivo sin depender de una consola abierta y deja la causa de cada salida.

Es un proceso residente y pequeño que arranca el lanzador existente (`supervisor.py`), lo espera y, si termina, anota hora,
duración, código de salida y qué hacía ANKA al final, y lo vuelve a lanzar. No toca `anka/`: reutiliza su arranque y su
limpieza de instancias. Lo ejecuta una tarea del Programador de tareas (ver `PROCEDIMIENTO_ESTABILIDAD.md`).

Garantías (todas con prueba en `test_vigilante.py`):
- una sola instancia del vigilante (candado del sistema operativo) y nunca arranca ANKA si ya hay otro lanzador vivo;
- una detención deliberada (`detener`) crea `DETENIDO.flag`: mientras exista no se arranca nada y no hay reinicios;
- una salida con el código 2 («no reiniciar») y una tormenta de salidas (4 arranques en 15 minutos) dejan una alerta y esperan;
- cada salida queda en `logs/vigilante.log` con su clasificación; los errores no capturados del lanzador, en
  `logs/supervisor_stderr.log` (solo el flujo de errores: la consola de ANKA puede llevar datos de clientes y se descarta).

Uso: python -m laboratorio.estabilidad.vigilante {ejecutar|detener|reanudar|estado}
"""

from __future__ import annotations

import json
import os
import shlex
import subprocess
import sys
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any, Protocol

ROOT = Path(__file__).resolve().parents[2]
SALIDA_NO_REINICIAR = 2             # EXIT_STOP de anka/cli/supervisor.py
CTRL_C_O_CIERRE = 0xC000013A        # el proceso se cerró con Ctrl+C, al cerrar la consola o al cerrar la sesión
MAX_ARRANQUES = 4                   # arranques permitidos dentro de VENTANA_S antes de parar y avisar
VENTANA_S = 900
ESPERA_MAX_S = 60
ESPERA_TORMENTA_S = 300
AVISO_EXTERNA_CADA_S = 600
REVISAR_EXTERNA_CADA_S = 30          # con otro lanzador vivo, se vuelve a mirar cada 30 s (consultar procesos no es gratis)
TOPE_LOG = 1_000_000
CREATE_NO_WINDOW = 0x08000000


class Proceso(Protocol):
    """Lo que el vigilante usa de un proceso hijo (el real es `subprocess.Popen`)."""

    pid: int

    def poll(self) -> int | None:
        """Código de salida o None si sigue vivo."""

    def terminate(self) -> None:
        """Pide terminar (el real termina todo el árbol)."""


def datos() -> Path:
    """La carpeta de datos de ANKA (la de `ANKA_DATA_DIR` en las pruebas)."""
    return Path(os.environ.get("ANKA_DATA_DIR") or Path(os.environ["LOCALAPPDATA"]) / "AnkaCorreos")


def clasificar_salida(codigo: int | None, duracion_s: float, detenida_a_proposito: bool = False) -> str:
    """Una frase con lo que se sabe de la salida; nunca afirma una causa que el código de salida no demuestra."""
    if detenida_a_proposito:
        return "detenida a propósito (DETENIDO.flag)"
    if codigo is None:
        return "sin código de salida"
    unsigned = codigo & 0xFFFFFFFF
    if unsigned == CTRL_C_O_CIERRE:
        return "cerrada desde fuera: Ctrl+C, cierre de la consola o cierre de sesión (código 0xC000013A)"
    if codigo == 0:
        return "salida normal (código 0); el lanzador solo termina así tras un Ctrl+C atendido"
    if codigo == SALIDA_NO_REINICIAR:
        return "pidió no reiniciarse (código 2)"
    if codigo == 1:
        return "terminó con un error no capturado (código 1): ver logs/supervisor_stderr.log"
    if unsigned >= 0xC0000000:
        return f"terminada por el sistema (código 0x{unsigned:08X})"
    return f"código de salida {codigo}" + (" tras menos de 10 s" if duracion_s < 10 else "")


def _es_el_lanzador(linea: str) -> bool:
    """¿La línea de comandos es «python … supervisor.py …»? Compara palabra por palabra: un texto que solo menciona el
    nombre (un `python -c "…supervisor.py…"`, un `ruff check supervisor.py`) no cuenta."""
    try:
        palabras = shlex.split(linea, posix=False)
    except ValueError:
        return False
    if not palabras or "python" not in Path(palabras[0].strip('"').replace("\\", "/")).name.lower():
        return False
    if any(p.strip('"') in ("-c", "-m") for p in palabras[1:3]):
        return False
    return any(Path(p.strip('"').replace("\\", "/")).name.lower() == "supervisor.py" for p in palabras[1:])


def instancias_externas(filas: list[dict[str, Any]], excluir: set[int]) -> list[int]:
    """Procesos de Python que ejecutan el lanzador (`supervisor.py`) y no son del propio vigilante."""
    return [int(f["ProcessId"]) for f in filas
            if int(f["ProcessId"]) not in excluir and _es_el_lanzador(f.get("CommandLine") or "")]


def procesos_del_sistema() -> list[dict[str, Any]]:
    """Lista de procesos de Windows (`ProcessId`, `ParentProcessId`, `CommandLine`); vacía si no se pudo consultar."""
    script = "Get-CimInstance Win32_Process | Select-Object ProcessId,ParentProcessId,CommandLine | ConvertTo-Json -Compress"
    try:
        salida = subprocess.run(["powershell", "-NoProfile", "-Command", script], capture_output=True, text=True,
                                timeout=30, creationflags=CREATE_NO_WINDOW).stdout
        filas = json.loads(salida or "[]")
    except (OSError, ValueError, subprocess.SubprocessError):
        return []
    return [filas] if isinstance(filas, dict) else filas


def matar_arbol(pid: int) -> None:
    """Termina un proceso y sus descendientes (Windows)."""
    subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"], capture_output=True, creationflags=CREATE_NO_WINDOW)


class Registro:
    """Archivo de texto simple con tope de tamaño; no depende del registro de ANKA para seguir funcionando si este falla."""

    def __init__(self, ruta: Path, reloj: Callable[[], float] = time.time) -> None:
        self.ruta, self.reloj = ruta, reloj

    def escribir(self, nivel: str, mensaje: str) -> None:
        """Agrega una línea con fecha y hora local; si el archivo pasa del tope lo rota a `.1`."""
        try:
            self.ruta.parent.mkdir(parents=True, exist_ok=True)
            if self.ruta.exists() and self.ruta.stat().st_size > TOPE_LOG:
                self.ruta.replace(self.ruta.with_name(self.ruta.name + ".1"))
            marca = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(self.reloj()))
            with self.ruta.open("a", encoding="utf-8", newline="\n") as archivo:
                archivo.write(f"{marca} {nivel} {mensaje}\n")
        except OSError:
            pass


def actividad_de_anka(carpeta: Path) -> dict[str, str]:
    """Lo último que se sabe de ANKA antes de quedarse sin lanzador: último éxito del receptor y último ciclo (solo lectura)."""
    import sqlite3
    resultado: dict[str, str] = {}
    base = carpeta / "quotes.sqlite3"
    try:
        if base.exists():
            db = sqlite3.connect(f"file:{base.as_posix()}?mode=ro", uri=True, timeout=3)
            try:
                fila = db.execute("SELECT last_success_at FROM recepcion_control ORDER BY updated_at DESC LIMIT 1").fetchone()
            finally:
                db.close()
            if fila and fila[0]:
                resultado["receptor_ultimo_exito"] = str(fila[0])
        candado = carpeta / "work_coordinacion.lock"
        if candado.exists():
            resultado["ultimo_ciclo"] = time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(candado.stat().st_mtime))
    except (OSError, sqlite3.Error):
        pass
    return resultado


def arranque_del_equipo() -> float | None:
    """Instante (época) en que arrancó Windows, o None si no se puede saber."""
    try:
        import ctypes
        return time.time() - ctypes.windll.kernel32.GetTickCount64() / 1000.0   # type: ignore[attr-defined,unused-ignore]
    except (AttributeError, OSError):
        return None


def lanzar_real(stderr_ruta: Path) -> subprocess.Popen[bytes]:
    """Arranca `supervisor.py` (el lanzador de siempre) sin ventana; sus errores no capturados quedan en un archivo."""
    stderr_ruta.parent.mkdir(parents=True, exist_ok=True)
    errores = stderr_ruta.open("ab")
    exe = sys.executable.replace("pythonw.exe", "python.exe")
    return subprocess.Popen([exe, str(ROOT / "supervisor.py")], cwd=ROOT, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                            stderr=errores, creationflags=CREATE_NO_WINDOW)


class Vigilante:
    """Un paso de decisión cada vez (`paso`); `ejecutar` solo lo repite. Todo lo del mundo exterior entra por parámetros."""

    def __init__(self, carpeta: Path, lanzar: Callable[[], Proceso], procesos: Callable[[], list[dict[str, Any]]] = procesos_del_sistema,
                 matar: Callable[[int], None] = matar_arbol, reloj: Callable[[], float] = time.time,
                 equipo_arranco: Callable[[], float | None] = arranque_del_equipo, pid_propio: int | None = None) -> None:
        self.carpeta, self.lanzar, self.procesos, self.matar, self.reloj = carpeta, lanzar, procesos, matar, reloj
        self.equipo_arranco = equipo_arranco
        self.pid_propio = os.getpid() if pid_propio is None else pid_propio
        self.registro = Registro(carpeta / "logs" / "vigilante.log", reloj)
        self.hijo: Proceso | None = None
        self.inicio_hijo = 0.0
        self.fallos = 0
        self.no_antes_de = 0.0
        self.ultimo_aviso: dict[str, float] = {}
        self.detenida = False
        self.arranques: list[float] = []

    @property
    def bandera(self) -> Path:
        """El archivo cuya existencia significa «detenida a propósito»."""
        return self.carpeta / "DETENIDO.flag"

    def _avisar(self, clave: str, nivel: str, mensaje: str, cada: float = AVISO_EXTERNA_CADA_S) -> None:
        """Escribe un aviso repetido como mucho una vez cada `cada` segundos (para no llenar el archivo)."""
        ahora = self.reloj()
        if ahora - self.ultimo_aviso.get(clave, -1e18) >= cada:
            self.ultimo_aviso[clave] = ahora
            self.registro.escribir(nivel, mensaje)

    def paso(self) -> str:
        """Hace lo que toca ahora y devuelve qué hizo: detenido, corriendo, salio, esperando, externa, tormenta o arrancado."""
        if self.bandera.exists():
            return self._detener_por_bandera()
        if self.detenida:
            self.detenida = False
            self.registro.escribir("INFO", "DETENIDO.flag retirado: se reanuda la vigilancia")
        if self.hijo is not None:
            codigo = self.hijo.poll()
            if codigo is None:
                return "corriendo"
            return self._registrar_salida(codigo)
        if self.reloj() < self.no_antes_de:
            return "esperando"
        externas = instancias_externas(self.procesos(), {self.pid_propio})
        if externas:
            self._avisar("externa", "AVISO", f"hay otro lanzador de ANKA vivo (PID {externas}): no se arranca otro")
            self.no_antes_de = self.reloj() + REVISAR_EXTERNA_CADA_S
            return "externa"
        recientes = [t for t in self.arranques if self.reloj() - t < VENTANA_S]
        if len(recientes) >= MAX_ARRANQUES:
            self._avisar("tormenta", "ALERTA", f"{len(recientes)} arranques en {VENTANA_S // 60} min: se espera {ESPERA_TORMENTA_S // 60} min "
                         "antes de insistir; revisar logs/supervisor_stderr.log", cada=ESPERA_TORMENTA_S)
            self._alerta(f"{len(recientes)} arranques de ANKA en {VENTANA_S // 60} minutos")
            self.no_antes_de = self.reloj() + ESPERA_TORMENTA_S
            return "tormenta"
        return self._arrancar()

    def _detener_por_bandera(self) -> str:
        if self.hijo is not None:
            pid, codigo = self.hijo.pid, self.hijo.poll()
            if codigo is None:
                self.matar(pid)
            self.registro.escribir("INFO", f"ANKA detenida a propósito (PID {pid}): {clasificar_salida(codigo, 0, True)}")
            self.hijo = None
        if not self.detenida:
            self.registro.escribir("INFO", "DETENIDO.flag presente: no se arranca ni se reinicia nada")
        self.detenida = True
        return "detenido"

    def _registrar_salida(self, codigo: int) -> str:
        duracion = self.reloj() - self.inicio_hijo
        self.hijo = None
        self.fallos = self.fallos + 1 if duracion < 120 else 1
        espera = min(ESPERA_MAX_S, 5 * self.fallos)
        self.no_antes_de = self.reloj() + espera
        causa = clasificar_salida(codigo, duracion)
        self.registro.escribir("AVISO", f"ANKA terminó tras {duracion:.0f} s: {causa}; "
                               f"actividad previa {json.dumps(actividad_de_anka(self.carpeta))}; se reinicia en {espera} s")
        if codigo == SALIDA_NO_REINICIAR:
            self._alerta("el lanzador pidió no reiniciarse (código 2)")
            self.no_antes_de = self.reloj() + ESPERA_TORMENTA_S
        return "salio"

    def _arrancar(self) -> str:
        previa = actividad_de_anka(self.carpeta)
        arranque = self.equipo_arranco()
        ultimo = self._ultima_actividad_epoch(previa)
        nota = " (el equipo se reinició después de la última actividad)" if arranque and ultimo and arranque > ultimo else ""
        self.hijo = self.lanzar()
        self.inicio_hijo = self.reloj()
        self.arranques.append(self.inicio_hijo)
        self.registro.escribir("INFO", f"ANKA arrancada (PID {self.hijo.pid}); actividad previa {json.dumps(previa)}{nota}")
        return "arrancado"

    @staticmethod
    def _ultima_actividad_epoch(actividad: dict[str, str]) -> float | None:
        epochs = []
        for valor in actividad.values():
            try:
                epochs.append(time.mktime(time.strptime(valor[:19], "%Y-%m-%dT%H:%M:%S")))
            except ValueError:
                continue
        return max(epochs) if epochs else None

    def _alerta(self, texto: str) -> None:
        try:
            (self.carpeta / "ALERTA_ANKA.txt").write_text(
                f"{time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(self.reloj()))} {texto}. Ver logs/vigilante.log\n", encoding="utf-8")
        except OSError:
            pass


def bloqueo_unico(ruta: Path) -> Any:
    """Candado del sistema operativo: devuelve el archivo abierto si es la única instancia, None si ya hay otra."""
    ruta.parent.mkdir(parents=True, exist_ok=True)
    archivo = ruta.open("a+")
    try:
        if os.name == "nt":
            import msvcrt
            msvcrt.locking(archivo.fileno(), msvcrt.LK_NBLCK, 1)   # type: ignore[attr-defined,unused-ignore]
        else:
            import fcntl
            fcntl.flock(archivo.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)   # type: ignore[attr-defined,unused-ignore]
    except OSError:
        archivo.close()
        return None
    return archivo


def ejecutar(carpeta: Path | None = None) -> int:
    """Bucle residente: lo que lanza la tarea programada. Sale en 0 si ya hay un vigilante vivo."""
    carpeta = carpeta or datos()
    candado = bloqueo_unico(carpeta / "vigilante.lock")
    if candado is None:
        return 0
    vigilante = Vigilante(carpeta, lambda: lanzar_real(carpeta / "logs" / "supervisor_stderr.log"))
    vigilante.registro.escribir("INFO", f"vigilante iniciado (PID {os.getpid()})")
    try:
        while True:
            try:
                vigilante.paso()
            except Exception as exc:                      # nada debe matar al vigilante: se anota y se sigue
                vigilante.registro.escribir("ERROR", f"fallo del vigilante ({type(exc).__name__}): {exc}")
                time.sleep(10)
            time.sleep(2)
    finally:
        vigilante.registro.escribir("AVISO", "vigilante terminado")
        candado.close()


def detener(carpeta: Path | None = None, procesos: Callable[[], list[dict[str, Any]]] = procesos_del_sistema,
            matar: Callable[[int], None] = matar_arbol) -> str:
    """Parada deliberada: crea DETENIDO.flag y cierra los lanzadores vivos. Devuelve un resumen."""
    carpeta = carpeta or datos()
    (carpeta / "DETENIDO.flag").write_text(time.strftime("%Y-%m-%d %H:%M:%S") + "\n", encoding="utf-8")
    pids = instancias_externas(procesos(), {os.getpid()})
    for pid in pids:
        matar(pid)
    Registro(carpeta / "logs" / "vigilante.log").escribir("INFO", f"detención deliberada: lanzadores cerrados {pids}")
    return f"DETENIDO.flag creado; lanzadores cerrados: {pids or 'ninguno'}"


def reanudar(carpeta: Path | None = None) -> str:
    """Retira DETENIDO.flag; el vigilante arranca ANKA en su siguiente paso (unos segundos)."""
    carpeta = carpeta or datos()
    (carpeta / "DETENIDO.flag").unlink(missing_ok=True)
    (carpeta / "ALERTA_ANKA.txt").unlink(missing_ok=True)
    return "DETENIDO.flag retirado; el vigilante arrancará ANKA en unos segundos"


def estado(carpeta: Path | None = None, procesos: Callable[[], list[dict[str, Any]]] = procesos_del_sistema) -> dict[str, Any]:
    """Resumen de solo lectura para la persona que opera."""
    carpeta = carpeta or datos()
    registro = carpeta / "logs" / "vigilante.log"
    return {"lanzadores": instancias_externas(procesos(), {os.getpid()}), "detenida_a_proposito": (carpeta / "DETENIDO.flag").exists(),
            "alerta": (carpeta / "ALERTA_ANKA.txt").exists(), "actividad": actividad_de_anka(carpeta),
            "ultimas_lineas": registro.read_text(encoding="utf-8").splitlines()[-5:] if registro.exists() else []}


def main(argv: list[str] | None = None) -> int:
    """Punto de entrada de la línea de órdenes."""
    args = sys.argv[1:] if argv is None else argv
    orden = args[0] if args else "estado"
    if orden == "ejecutar":
        return ejecutar()
    if orden == "detener":
        print(detener())
    elif orden == "reanudar":
        print(reanudar())
    elif orden == "estado":
        print(json.dumps(estado(), ensure_ascii=False, indent=1))
    else:
        print(__doc__)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
