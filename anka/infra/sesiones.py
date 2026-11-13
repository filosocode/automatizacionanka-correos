"""Sesiones del dashboard: quién inició sesión y hasta cuándo, y la espera tras intentos fallidos (U-115, D-210).

Las sesiones viven en `sesiones.json` (carpeta de datos), igual que el token CSRF (`seguridad.py`): así sobreviven a
un reinicio del dashboard (el lanzador reinicia al cambiar el código) y nadie queda desconectado sin motivo. Los
intentos fallidos solo se cuentan en memoria del proceso: perderlos al reiniciar es una relajación menor aceptable
(no hay nada que proteger en un reinicio que el propio comercial provoca).
"""

from __future__ import annotations

import json
import secrets
import threading
from datetime import datetime, timedelta
from pathlib import Path
from typing import TypedDict

from anka.dominio.errores import ErrorDeAnka
from anka.dominio.reloj import ahora, instante
from anka.dominio.usuarios import bloqueada_hasta as _bloqueada_hasta
from anka.infra import settings
from anka.infra.archivos_privados import escribir_privado

DURACION_RECORDAR = timedelta(days=14)
DURACION_SIN_RECORDAR = timedelta(days=1)    # tope del lado del servidor; el navegador ya borra la cookie al cerrar


class _Sesion(TypedDict):
    usuario: str
    expira: str


_lock = threading.Lock()


def _ruta() -> Path:
    return settings.DATA_DIR / "sesiones.json"


def _cargar() -> dict[str, _Sesion]:
    path = _ruta()
    if not path.exists():
        return {}
    try:
        datos = json.loads(path.read_text(encoding="utf-8-sig"))
    except json.JSONDecodeError as exc:
        raise ErrorDeAnka(f"{path} no es JSON válido (línea {exc.lineno}, columna {exc.colno}): {exc.msg}.") from exc
    return datos if isinstance(datos, dict) else {}


def _guardar(sesiones: dict[str, _Sesion]) -> None:
    escribir_privado(_ruta(), json.dumps(sesiones, ensure_ascii=False, indent=2) + "\n")


def _purgar(sesiones: dict[str, _Sesion]) -> dict[str, _Sesion]:
    ahora_ = ahora()
    return {sid: s for sid, s in sesiones.items() if (vence := instante(s.get("expira"))) and vence > ahora_}


def crear(usuario: str, recordar: bool) -> str:
    """Abre una sesión para `usuario` y devuelve su id (el valor de la cookie, U-115.3)."""
    with _lock:
        sesiones = _purgar(_cargar())
        session_id = secrets.token_urlsafe(32)
        vence = ahora() + (DURACION_RECORDAR if recordar else DURACION_SIN_RECORDAR)
        sesiones[session_id] = {"usuario": usuario, "expira": vence.isoformat()}
        _guardar(sesiones)
        return session_id


def usuario_de(session_id: str) -> str | None:
    """El usuario de esta sesión, o `None` si no existe o ya venció."""
    if not session_id:
        return None
    with _lock:
        sesiones = _cargar()
        sesion = sesiones.get(session_id)
        if not sesion:
            return None
        vence = instante(sesion.get("expira"))
        if not vence or vence <= ahora():
            return None
        return sesion.get("usuario") or None


def cerrar(session_id: str) -> None:
    """Cierra una sesión (cerrar sesión, E-115h); no falla si ya no existía."""
    if not session_id:
        return
    with _lock:
        sesiones = _cargar()
        if sesiones.pop(session_id, None) is not None:
            _guardar(sesiones)


# --- intentos fallidos (solo en memoria del proceso, D-210)
_fallos: dict[str, tuple[int, datetime]] = {}
_fallos_lock = threading.Lock()


def registrar_fallo(usuario: str) -> None:
    """Cuenta un intento fallido más de `usuario`, con la hora de ahora."""
    with _fallos_lock:
        cantidad, _ = _fallos.get(usuario, (0, ahora()))
        _fallos[usuario] = (cantidad + 1, ahora())


def registrar_exito(usuario: str) -> None:
    """Olvida los fallos anteriores de `usuario` (un intento correcto reinicia el conteo)."""
    with _fallos_lock:
        _fallos.pop(usuario, None)


def bloqueada_hasta(usuario: str) -> datetime | None:
    """¿Hasta cuándo debe esperar `usuario` antes de poder intentarlo de nuevo?"""
    with _fallos_lock:
        cantidad, ultimo = _fallos.get(usuario, (0, None))
    return _bloqueada_hasta(cantidad, ultimo)
