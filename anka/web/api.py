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


def a_json(value: Any) -> bytes:
    """Serializa a JSON (UTF-8, con tildes) también los objetos del dominio, que se comportan como mapas.

    Compacto, sin sangrías ni espacios: así pesa un tercio menos (U-48); los navegadores y `jq` lo muestran bonito."""
    return json.dumps(
        value, ensure_ascii=False, separators=(",", ":"),
        default=lambda obj: dict(obj) if isinstance(obj, Mapping) else str(obj),
    ).encode("utf-8")
