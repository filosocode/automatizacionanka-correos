"""Latido del puente Gmail → dashboard (modo chatgpt_work): permite saber si está vivo."""

from __future__ import annotations

import json
import os
import sys
from datetime import datetime

import settings

HEARTBEAT_FILE = "work_sync_heartbeat.json"
DEFAULT_INTERVAL = 60


def _now() -> datetime:
    return datetime.now().astimezone()


def write_heartbeat(interval: int, drafts: int = 0, updated: int = 0, failures: int = 0,
                    error: str = "", events: int = 0) -> None:
    """Escribe el estado del último ciclo. Nunca debe tumbar al puente."""
    data = {
        "at": _now().isoformat(timespec="seconds"), "pid": os.getpid(), "interval": interval,
        "drafts": drafts, "updated": updated, "failures": failures, "error": error, "events": events,
    }
    try:
        settings.DATA_DIR.mkdir(parents=True, exist_ok=True)
        path = settings.DATA_DIR / HEARTBEAT_FILE
        temporary = path.with_suffix(".tmp")
        temporary.write_text(json.dumps(data), encoding="utf-8")
        temporary.replace(path)
    except OSError:
        pass


def read_heartbeat() -> dict | None:
    try:
        data = json.loads((settings.DATA_DIR / HEARTBEAT_FILE).read_text(encoding="utf-8"))
        datetime.fromisoformat(data["at"])
        return data
    except (OSError, ValueError, KeyError, TypeError):
        return None


def pid_alive(pid: int) -> bool:
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


def bridge_state() -> tuple[str, str]:
    """(estado, texto): estado es 'ok', 'error', 'stopped' o 'never'."""
    beat = read_heartbeat()
    if not beat:
        return "never", (
            "Puente Work sin iniciar: abre «Puente Work - Dashboard.bat» para que los "
            "borradores de Gmail lleguen aquí."
        )
    age = (_now() - datetime.fromisoformat(beat["at"])).total_seconds()
    when = beat["at"][11:19]
    interval = int(beat.get("interval") or DEFAULT_INTERVAL)
    if age > max(3 * interval, 180):
        return "stopped", (
            f"Puente Work DETENIDO: última revisión a las {when} ({_ago(age)}). "
            "Abre «Puente Work - Dashboard.bat»."
        )
    if beat.get("error") or beat.get("failures"):
        detail = beat.get("error") or f"{beat['failures']} borrador(es) no importado(s)"
        return "error", f"Puente Work activo con avisos a las {when}: {detail}."
    return "ok", (
        f"Puente Work activo: Gmail revisado a las {when} ({_ago(age)}), "
        f"{beat.get('drafts', 0)} borradores, {beat.get('updated', 0)} actualizados."
    )
