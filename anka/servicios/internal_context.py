"""Contexto comercial interno: solo lectura, nunca sale en correos."""

from __future__ import annotations

import json
import re

from anka.dominio.reception import normalized_text
from anka.infra import settings


INTERNAL_CONTEXT_PATH = settings.DATA_DIR / "internal_context" / "commercial_index.json"


def load_internal_commercial_index() -> dict:
    """Carga solo el resumen local; nunca se usa para redactar al cliente."""
    try:
        data = json.loads(INTERNAL_CONTEXT_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    if not data.get("privacy", {}).get("internal_only"):
        return {}
    return data


def _commercial_profile_match(index: dict, client_name: str) -> dict | None:
    name = " ".join(normalized_text(client_name or "").split())
    if len(name) < 3:
        return None
    clients = index.get("clients", {})
    if name in clients:
        return clients[name]
    ignored = {"sas", "s", "sa", "ltda", "grupo", "empresa", "de", "del", "la"}
    wanted = {token for token in re.findall(r"[a-z0-9]+", name)
              if len(token) >= 3 and token not in ignored}
    if not wanted:
        return None
    best_profile = None
    best_score = 0.0
    for key, profile in clients.items():
        available = {token for token in re.findall(r"[a-z0-9]+", key)
                     if len(token) >= 3 and token not in ignored}
        score = len(wanted & available) / max(len(wanted), len(available), 1)
        if score > best_score:
            best_profile, best_score = profile, score
    return best_profile if best_score >= 0.6 else None


def internal_commercial_context(client_name: str) -> dict:
    """Resumen comercial interno del cliente (historial agregado) para quien revisa; vacío si no hay índice."""
    index = load_internal_commercial_index()
    if not index:
        return {}
    summary = index.get("summary", {})
    return {
        "generated_at": index.get("generated_at", ""),
        "profile": _commercial_profile_match(index, client_name),
        "project_types": list(summary.get("project_types", {}).items())[:5],
        "sectors": list(summary.get("sectors", {}).items())[:5],
        "usual_requests": list(summary.get("usual_requests", {}).items())[:7],
        "client_profiles": summary.get("client_profiles", 0),
    }
