"""Comprobación de las conexiones del sistema: base, configuración, puente de Work, Gmail, Drive y motor de análisis.

Alimenta `GET /api/salud` (spec/07-api.md). Por defecto solo mira cosas locales y rápidas; con `profundo=True` hace
además una consulta real de solo lectura a Gmail y a Drive. Un problema nunca se lanza como excepción: queda como
una comprobación en estado `error` con una explicación corta (sin datos de clientes ni claves).
"""

from __future__ import annotations

import os
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import TypedDict

from anka.dominio.configuracion import Configuracion
from anka.dominio.errores import ErrorDeAnka
from anka.infra import settings
from anka.infra.google_services import credentials, services
from anka.infra.llm import check_ollama
from anka.infra.store import SCHEMA_VERSION, connect
from anka.infra.work_heartbeat import bridge_state

OK, ERROR, NO_APLICA = "ok", "error", "no_aplica"


class ResultadoDeComprobacion(TypedDict):
    """Una comprobación tal como sale en el JSON de `/api/salud`."""

    componente: str
    estado: str
    detalle: str
    ms: int


class InformeDeSalud(TypedDict):
    """El informe completo de `/api/salud`."""

    estado: str          # ok | error
    profundo: bool
    comprobaciones: list[ResultadoDeComprobacion]


@dataclass(frozen=True)
class Comprobacion:
    """El resultado de mirar una conexión."""

    componente: str
    estado: str        # ok | error | no_aplica
    detalle: str
    ms: int


def _medir(componente: str, mirar: Callable[[], tuple[str, str]]) -> Comprobacion:
    """Ejecuta una comprobación y la convierte en resultado, midiendo cuánto tardó y sin dejar escapar errores."""
    inicio = time.perf_counter()
    try:
        estado, detalle = mirar()
    except ErrorDeAnka as exc:
        estado, detalle = ERROR, str(exc)            # nuestros mensajes ya están pensados para la persona
    except Exception as exc:
        estado, detalle = ERROR, f"{type(exc).__name__} al consultar {componente}"   # sin el texto: puede traer datos
    return Comprobacion(componente, estado, detalle, int((time.perf_counter() - inicio) * 1000))


def _base_de_datos() -> tuple[str, str]:
    db = connect()
    try:
        version = db.execute("PRAGMA user_version").fetchone()[0]
        db.execute("SELECT 1 FROM quotes LIMIT 1").fetchall()
    finally:
        db.close()
    if version != SCHEMA_VERSION:
        return ERROR, f"Esquema {version}, se esperaba {SCHEMA_VERSION}."
    return OK, f"Base local lista (esquema {version})."


def _configuracion() -> tuple[str, str]:
    config = settings.load_config()
    return OK, f"config.json válido; modo {config.get('active_mode', 'local')}."


def _puente_work(modo: str) -> tuple[str, str]:
    if modo != "chatgpt_work":
        return NO_APLICA, "Solo se usa en el modo ChatGPT Work."
    estado, texto = bridge_state()
    return (OK if estado == "ok" else ERROR), texto


def _autorizacion_google(profundo: bool) -> tuple[str, str]:
    if not (settings.DATA_DIR / "token.json").exists():
        return ERROR, "Falta autorizar la cuenta de Google (python app.py auth)."
    if profundo:
        credentials()                              # renueva el token si hace falta; falla si ya no sirve
        return OK, "Autorización de Google vigente."
    return OK, "Autorización de Google guardada."


def _gmail() -> tuple[str, str]:
    gmail, _ = services()
    gmail.users().getProfile(userId="me").execute()
    return OK, "Gmail responde (consulta de solo lectura)."


def _drive(config: Configuracion) -> tuple[str, str]:
    parent = config.get("drive_parent_id")
    if not parent:
        return NO_APLICA, "No hay drive_parent_id en config.json."
    _, drive = services()
    drive.files().get(fileId=parent, fields="id").execute()
    return OK, "Drive responde y la carpeta de arranque existe (consulta de solo lectura)."


def _motor(config: Configuracion, modo: str) -> tuple[str, str]:
    if modo == "chatgpt_work":
        return NO_APLICA, "En modo ChatGPT Work el análisis lo hace Work, no Anka."
    if modo == "api":
        return ((OK, "Hay una clave de OpenAI configurada.") if os.getenv("OPENAI_API_KEY")
                else (ERROR, "Falta OPENAI_API_KEY."))
    check_ollama(config["llm"])
    return OK, f"Ollama responde y tiene el modelo {config['llm'].get('model', '')}."


def comprobar(profundo: bool = False) -> list[Comprobacion]:
    """Mira cada conexión. Con `profundo` consulta de verdad a Gmail y a Drive (solo lectura)."""
    resultados = [_medir("base_de_datos", _base_de_datos), _medir("configuracion", _configuracion)]
    if resultados[-1].estado != OK:
        return resultados                          # sin configuración válida no se puede mirar el resto
    config = settings.load_config()
    modo = config.get("active_mode", "local")
    resultados.append(_medir("puente_work", lambda: _puente_work(modo)))
    resultados.append(_medir("autorizacion_google", lambda: _autorizacion_google(profundo)))
    if profundo:
        resultados.append(_medir("gmail", _gmail))
        resultados.append(_medir("drive", lambda: _drive(config)))
    resultados.append(_medir("motor_de_analisis", lambda: _motor(config, modo)))
    return resultados


def informe(profundo: bool = False) -> InformeDeSalud:
    """El informe completo: `estado` global (`ok` si ninguna comprobación falló) y cada comprobación."""
    resultados = comprobar(profundo)
    return {
        "estado": ERROR if any(r.estado == ERROR for r in resultados) else OK,
        "profundo": profundo,
        "comprobaciones": [
            {"componente": r.componente, "estado": r.estado, "detalle": r.detalle, "ms": r.ms} for r in resultados],
    }
