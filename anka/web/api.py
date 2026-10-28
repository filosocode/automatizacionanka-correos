"""Representación JSON de los casos para las rutas de lectura `/api/...` (ver spec/07-api.md)."""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any

from anka.dominio.caso import Caso


def resumen_de_caso(caso: Caso) -> dict[str, Any]:
    """Lo mínimo para listar un caso: quién, qué proyecto, en qué estado y desde cuándo espera."""
    return {
        "thread_id": caso.thread_id,
        "project_name": caso.analysis.get("project_name") or "Solicitud sin nombre",
        "client_name": caso.client_name,
        "recipient": caso.recipient,
        "status": caso.status,
        "status_label": caso.status_label,
        "quote_code": caso.quote_code,
        "waiting_since": caso.waiting_since,
        "updated_at": caso.updated_at,
    }


# Código de máquina de cada error de la API (U-50): estable y sin tildes; el mensaje es para la persona.
CODIGOS_DE_ERROR: dict[int, str] = {
    400: "solicitud_no_valida", 403: "solicitud_rechazada", 404: "no_encontrado", 405: "metodo_no_permitido",
    413: "cuerpo_demasiado_grande", 500: "error_interno", 501: "metodo_no_implementado", 503: "no_disponible",
}
RUTAS_JSON_FUERA_DE_API = frozenset({"/openapi.json", "/state", "/eventos"})


def es_ruta_de_api(ruta: str) -> bool:
    """¿Esta ruta responde JSON? Todo `/api/...` y las lecturas JSON de siempre: sus errores también son JSON."""
    return ruta == "/api" or ruta.startswith("/api/") or ruta in RUTAS_JSON_FUERA_DE_API


def error_de_api(status: int, mensaje: str, id_de_error: str = "") -> bytes:
    """El cuerpo de un error de la API: `{"error": {"codigo", "mensaje"}}`, más `id` en un 500 (el del registro)."""
    error = {"codigo": CODIGOS_DE_ERROR.get(status, "error"), "mensaje": mensaje}
    if id_de_error:
        error["id"] = id_de_error
    return a_json({"error": error})


def a_json(value: Any) -> bytes:
    """Serializa a JSON (UTF-8, con tildes) también los objetos del dominio, que se comportan como mapas.

    Compacto, sin sangrías ni espacios: así pesa un tercio menos (U-48); los navegadores y `jq` lo muestran bonito."""
    return json.dumps(
        value, ensure_ascii=False, separators=(",", ":"),
        default=lambda obj: dict(obj) if isinstance(obj, Mapping) else str(obj),
    ).encode("utf-8")
