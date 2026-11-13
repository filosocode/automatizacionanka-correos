"""Latido del puente Gmail → dashboard (modo chatgpt_work): permite saber si está vivo."""

from __future__ import annotations

import json
import os
import sqlite3
import sys
import time
from collections.abc import Callable, Mapping
from datetime import datetime
from typing import Any

from anka.dominio.coordinacion_work import puente_general_habilitado
from anka.infra import rechazos_de_work, settings, store
from anka.infra.logging_setup import get_logger

HEARTBEAT_FILE = "work_sync_heartbeat.json"
DEFAULT_INTERVAL = 30
WAKE_FILE = "work_sync_wake"
log = get_logger(__name__)


def _now() -> datetime:
    return datetime.now().astimezone()


def write_heartbeat(interval: int, drafts: int = 0, updated: int = 0, failures: int = 0,
                    error: str = "", events: int = 0, rejected: list[dict] | None = None) -> None:
    """Escribe el estado del último ciclo. Nunca debe tumbar al puente.

    `rejected` son los borradores de Work que incumplen el contrato (no es un fallo: se muestran aparte).
    """
    data = {
        "at": _now().isoformat(timespec="seconds"), "pid": os.getpid(), "interval": interval,
        "drafts": drafts, "updated": updated, "failures": failures, "error": error, "events": events,
        "rejected": (rejected or [])[:10],
    }
    try:
        settings.DATA_DIR.mkdir(parents=True, exist_ok=True)
        path = settings.DATA_DIR / HEARTBEAT_FILE
        temporary = path.with_suffix(".tmp")
        temporary.write_text(json.dumps(data), encoding="utf-8")
        temporary.replace(path)
    except OSError as exc:   # el latido es informativo: el puente sigue y lo reescribe en el siguiente ciclo
        log.warning("No se pudo escribir el latido del puente (%s)", type(exc).__name__)


def read_heartbeat() -> dict | None:
    """Última señal de vida del puente, o `None` si no hay o está dañada."""
    try:
        data = json.loads((settings.DATA_DIR / HEARTBEAT_FILE).read_text(encoding="utf-8"))
        datetime.fromisoformat(data["at"])
        return data
    except (OSError, ValueError, KeyError, TypeError):
        return None


def rejected_drafts() -> list[dict]:
    """Los borradores de Work que el puente rechazó en su último ciclo (con `draft`, `thread` y `reason`)."""
    beat = read_heartbeat()
    items = (beat or {}).get("rejected") or []
    return [item for item in items if isinstance(item, dict)]


def pid_alive(pid: int) -> bool:
    """¿Sigue vivo el proceso con ese PID?"""
    if pid <= 0:
        return False
    if sys.platform == "win32":
        import ctypes
        handle = ctypes.windll.kernel32.OpenProcess(0x1000, False, pid)  # QUERY_LIMITED_INFORMATION
        if not handle:
            return False
        code = ctypes.c_ulong()
        ok = ctypes.windll.kernel32.GetExitCodeProcess(handle, ctypes.byref(code))
        ctypes.windll.kernel32.CloseHandle(handle)
        return bool(ok) and code.value == 259  # STILL_ACTIVE
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


def other_bridge_running() -> bool:
    """True si el latido pertenece a otro proceso del puente que sigue vivo."""
    beat = read_heartbeat()
    if not beat or beat.get("pid") == os.getpid():
        return False
    return pid_alive(int(beat.get("pid") or 0))


def _ago(seconds: float) -> str:
    seconds = max(0, int(seconds))
    if seconds < 90:
        return f"hace {seconds} s"
    if seconds < 5400:
        return f"hace {seconds // 60} min"
    return f"hace {seconds // 3600} h"


def _alerta() -> str:
    """« Work está mandando análisis con errores: N hoy…» si ya van más del umbral; vacío si no (U-41)."""
    texto = rechazos_de_work.alerta_del_dia()
    return " " + texto if texto else ""


def _config_de_la_revision() -> Mapping[str, Any] | None:
    """La configuración vigente, o `None` si no se puede leer (entonces manda el latido, como antes)."""
    try:
        return settings.load_config()
    except Exception as exc:     # sin config legible no se adivina: el indicador clásico sigue siendo válido
        log.debug("Sin configuración para el indicador de la revisión (%s)", type(exc).__name__)
        return None


def _revisa_la_recepcion(config: Mapping[str, Any] | None) -> bool:
    """¿Quién revisa los correos es el receptor Gmail del lanzador y no el puente general (D-161, D-163)?

    Lo es cuando el modo es Work, la recepción está habilitada y el puente general no corre: apagado, o desplazado
    por la coordinación. Con el puente general en marcha manda su latido."""
    if not config or config.get("active_mode") != "chatgpt_work" or config.get("gmail_reception_enabled") is not True:
        return False
    puente_corre = puente_general_habilitado(config) and config.get("work_coordination_enabled") is not True
    return not puente_corre


def _intervalo_de_recepcion(config: Mapping[str, Any]) -> int:
    valor = config.get("gmail_reception_interval_seconds", 60)
    return valor if isinstance(valor, int) and not isinstance(valor, bool) and 30 <= valor <= 3600 else 60


def _edad(marca: object) -> float | None:
    """Segundos desde una marca ISO, o `None` si falta o está dañada."""
    try:
        return (_now() - datetime.fromisoformat(str(marca))).total_seconds() if marca else None
    except ValueError:
        return None


def _control_de_recepcion() -> Mapping[str, Any] | None:
    """La fila de `recepcion_control` más reciente (solo lectura); `None` si no hay base, tabla o fila."""
    if not (settings.DATA_DIR / "quotes.sqlite3").exists():
        return None                                   # leer no debe crear una base vacía
    try:
        with store.lectura() as db:
            fila = db.execute("SELECT last_success_at,last_attempt_at,consecutive_errors,last_error_kind,"
                              "last_error_type FROM recepcion_control ORDER BY updated_at DESC LIMIT 1").fetchone()
    except sqlite3.Error as exc:
        log.debug("No se pudo leer el estado de la recepción (%s)", type(exc).__name__)
        return None
    return dict(fila) if fila else None


def _estado_de_la_recepcion(config: Mapping[str, Any]) -> tuple[str, str]:
    """El estado de la revisión de correos según el receptor Gmail (`recepcion_control`), no según el puente."""
    control = _control_de_recepcion()
    intento = _edad(control.get("last_attempt_at")) if control else None
    exito = _edad(control.get("last_success_at")) if control else None
    if control is None or (intento is None and exito is None):
        return "never", (
            "La revisión de correos aún no ha empezado: el receptor de Gmail no ha hecho ninguna revisión. "
            "Abre «Iniciar Anka.bat» y espera un minuto.")
    limite = max(3 * _intervalo_de_recepcion(config), 180)
    errores = int(control.get("consecutive_errors") or 0)
    ultimo = control.get("last_success_at") or ""
    cuando = str(ultimo)[11:19]
    if errores > 0 and intento is not None and intento <= limite:
        causa = control.get("last_error_kind") or control.get("last_error_type") or "error sin detallar"
        return "error", (f"La revisión de correos sigue intentándolo, pero falló {errores} vez/veces seguidas "
                         f"({causa}). Último éxito: {cuando or 'ninguno'}.")
    if exito is None:
        return "never", ("La revisión de correos aún no ha tenido un éxito: el receptor de Gmail ya lo intentó. "
                         "Revisa el registro de Anka si sigue así.")
    if exito > limite:
        return "stopped", (
            f"La revisión de correos está detenida: el receptor de Gmail no revisa desde las {cuando} ({_ago(exito)}). "
            "Comprueba que «Iniciar Anka.bat» siga abierto; si lo está, busca el aviso en el registro de Anka.")
    return "ok", f"La revisión de correos está activa: Gmail revisado a las {cuando} ({_ago(exito)})." + _alerta()


def bridge_state() -> tuple[str, str]:
    """(estado, texto): estado es 'ok', 'attention', 'error', 'stopped' o 'never'.

    Con el receptor Gmail como revisor (recepción habilitada y puente general sin usar) el estado sale de
    `recepcion_control`; si no, del latido del puente general."""
    config = _config_de_la_revision()
    if config is not None and _revisa_la_recepcion(config):
        return _estado_de_la_recepcion(config)
    beat = read_heartbeat()
    if not beat:
        return "never", (
            "La revisión de correos no ha empezado. Abre «Puente Work - Dashboard.bat» para que los "
            "análisis de Work lleguen aquí."
        )
    age = (_now() - datetime.fromisoformat(beat["at"])).total_seconds()
    when = beat["at"][11:19]
    interval = int(beat.get("interval") or DEFAULT_INTERVAL)
    if age > max(3 * interval, 180):
        return "stopped", (
            f"La revisión de correos está detenida: la última fue a las {when} ({_ago(age)}). "
            "Abre «Puente Work - Dashboard.bat» para activarla."
        )
    if beat.get("error") or beat.get("failures"):
        detail = beat.get("error") or f"{beat['failures']} borrador(es) no importado(s)"
        return "error", f"La revisión de correos sigue activa, pero tuvo avisos a las {when}: {detail}."
    rejected = beat.get("rejected") or []
    if rejected:
        return "attention", (
            f"La revisión de correos está activa (Gmail revisado a las {when}), "
            f"pero {len(rejected)} análisis de Work no se pudieron leer; el caso queda solo con los datos del correo. "
            "Qué hacer está en «Mis casos». Si Work vuelve a mandar el análisis, se revisa solo." + _alerta())
    return "ok", (
        f"La revisión de correos está activa: Gmail revisado a las {when} ({_ago(age)}), "
        f"{beat.get('drafts', 0)} análisis, {beat.get('updated', 0)} actualizados." + _alerta()
    )


def request_check_now() -> None:
    """Pide al puente que revise Gmail ya, sin esperar su ciclo (el dashboard no lee Gmail: solo deja la señal)."""
    settings.DATA_DIR.mkdir(parents=True, exist_ok=True)
    (settings.DATA_DIR / WAKE_FILE).write_text(_now().isoformat(timespec="seconds"), encoding="utf-8")


def wait_for_next_cycle(seconds: int, sleep: Callable[[float], None] = time.sleep) -> bool:
    """Espera `seconds` o hasta que el dashboard pida revisar ahora; True si fue por la petición (y la consume)."""
    path = settings.DATA_DIR / WAKE_FILE
    for _ in range(max(1, int(seconds))):
        if path.exists():
            try:
                path.unlink()
            except OSError as exc:   # otro proceso la consumió o está bloqueada: igual se revisa ahora
                log.debug("No se pudo borrar la petición de revisar ya (%s)", type(exc).__name__)
            return True
        sleep(1)
    return False
