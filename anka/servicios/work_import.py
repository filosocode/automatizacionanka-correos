"""Importación del borrador de ChatGPT Work al dashboard (`dashboard_upsert`).

Decodifica el JSON del contrato (spec/03-contrato-work.md), lo convierte al análisis interno, aplica el control de
cambios y guarda el caso sin hacer retroceder uno que ya avanzó en el dashboard.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from collections.abc import Callable
from typing import Any

from anka.dominio.analisis import Analisis
from anka.dominio.errores import ErrorDeAnka
from anka.dominio.activities import normalize_estado
from anka.dominio.correo import decode_websafe
from anka.dominio.reception import unresolved_scope_issues
from anka.dominio.status import QuoteStatus, canonical_work_status
from anka.infra.logging_setup import get_logger
from anka.infra.store import connect, record_event, save
from anka.servicios.change_control import apply_guard
from anka.servicios.hoja_de_calculo import programar_actualizacion_de_hoja
from anka.servicios.quotes import save_quote_metadata
from anka.dominio.caso import CLIENT_TYPES, SECTORS

log = get_logger(__name__)


_DASHBOARD_PROTECTED = frozenset({
    QuoteStatus.READY, QuoteStatus.RESPONSE_SENT, QuoteStatus.SENDING,
})


# Estado que Work escribe en el borrador -> estado interno del caso.
_WORK_STATUS_MAP = {
    "Nueva solicitud": QuoteStatus.REVIEW_PENDING,
    "En análisis": QuoteStatus.REVIEW,
    "Faltan datos": QuoteStatus.REVIEW_PENDING,
    "Requiere aclaraciones del cliente": QuoteStatus.REVIEW_PENDING,
    "Revisión técnica": QuoteStatus.REVIEW,
    "Requiere revisión técnica interna": QuoteStatus.REVIEW,
    "Borrador preparado": QuoteStatus.DRAFT_CREATED,
    "Alcance completo": QuoteStatus.READY_REVIEW,
    "Información suficiente para revisión interna": QuoteStatus.READY_REVIEW,
    "Descartada": QuoteStatus.IGNORED,
}


def _decode_work_payload(payload_base64: str) -> tuple[str, dict]:
    """Decodifica y valida el JSON que Work dejó en el borrador; devuelve el texto crudo y el diccionario."""
    try:
        raw = decode_websafe(payload_base64).decode("utf-8")
        payload = json.loads(raw)
    except (ValueError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ErrorDeAnka(f"Registro de dashboard no válido: {exc}") from exc
    required = {"thread_id", "status", "client_email", "project_name"}
    missing = sorted(field for field in required if not str(payload.get(field, "")).strip())
    if missing:
        raise ErrorDeAnka("Faltan campos del dashboard: " + ", ".join(missing))
    payload["status"] = canonical_work_status(payload["status"]) or payload["status"]
    return raw, payload


def _work_activities(payload: dict) -> list:
    """Tabla de actividades del borrador, aceptando también un texto suelto o filas que son solo texto."""
    activities = payload.get("activities", [])
    if isinstance(activities, list):
        activities = [{**item, "estado": normalize_estado(item["estado"])}
                      if isinstance(item, dict) and "estado" in item else item for item in activities]
    if isinstance(activities, str):
        return [{
            "activity": activities,
            "servicio_tecnica": payload.get("service", ""),
            "ubicacion": payload.get("location", ""),
            "quantity": payload.get("magnitude", ""),
            "unit": "",
            "entregable": payload.get("deliverables", ""),
            "plazo_restriccion": payload.get("deadline", ""),
            "fuente": payload.get("source", ""),
            "estado": "confirmado",
            "notes": "",
        }]
    return [
        item if isinstance(item, dict) else {
            "activity": str(item), "servicio_tecnica": payload.get("service", ""),
            "ubicacion": payload.get("location", ""), "quantity": "", "unit": "",
            "entregable": "", "plazo_restriccion": "", "fuente": "",
            "estado": "pendiente", "notes": "",
        }
        for item in activities
    ]


def _list_or_wrapped(value: Any, wrap: Callable[[str], Any]) -> Any:
    """Work a veces manda un texto donde se espera una lista: un texto no vacío se envuelve en un elemento."""
    if isinstance(value, str):
        return [wrap(value)] if value.strip() else []
    return value


def _work_analysis(payload: dict) -> Analisis:
    """El análisis interno (el mismo formato que produce el piloto de Python) a partir del borrador de Work."""
    source = payload.get("source", "")
    reply_text = str(payload.get("draft_reply") or "").strip()
    return {
        "is_quote_request": payload["status"] != "Descartada",
        "project_name": payload["project_name"],
        "summary": payload.get("summary", ""),
        "activities": _work_activities(payload),
        "fields": payload.get("fields", []),
        "other_questions": _list_or_wrapped(payload.get("missing_data", []), lambda text: text),
        "scope_dependencies": _list_or_wrapped(payload.get("scope_dependencies", []), lambda text: {
            "description": text, "source": source, "status": "pendiente"}),
        "contradictions": _list_or_wrapped(payload.get("contradictions", []), lambda text: {
            "topic": "dato técnico", "details": text, "sources": [], "status": "pendiente"}),
        "attachments": _list_or_wrapped(payload.get("attachments", []), lambda text: {
            "filename": text, "status": "registrado", "reason": "", "blocked_fields": []}),
        "work_reply": ({"text": reply_text, "draft_id": str(payload.get("draft_id") or "")}
                       if reply_text else {}),
        "received_at": payload.get("received_at", ""),
        "gmail_url": payload.get("gmail_url", ""),
        "engine": payload.get("engine", ""),
        "routing": {
            "original_sender": payload.get("client_name", ""),
            "original_sender_email": payload["client_email"],
            "internal_forwarder": payload.get("internal_sender", ""),
            "selected_recipient": payload.get("internal_sender") or payload["client_email"],
            "is_forwarded": bool(payload.get("internal_sender")),
        },
    }


def _internal_status(payload: dict, analysis: Analisis) -> str:
    """Estado interno que corresponde al borrador de Work."""
    status = _WORK_STATUS_MAP.get(payload["status"], payload["status"])
    if status in (QuoteStatus.REVIEW_PENDING, QuoteStatus.READY_REVIEW, QuoteStatus.DRAFT_CREATED
                  ) and unresolved_scope_issues(analysis):
        # Igual que el piloto de Python (decide_analysis): con dependencias o contradicciones
        # pendientes el caso pasa a revisión técnica interna, donde una persona decide.
        return QuoteStatus.REVIEW
    return status


def _store_work_analysis(db: sqlite3.Connection, raw: str, payload: dict, analysis: Analisis) -> None:
    """Guarda el análisis de Work respetando el control de cambios y sin hacer retroceder un caso avanzado."""
    thread_id = payload["thread_id"]
    message_id = payload.get("message_id") or thread_id
    status = _internal_status(payload, analysis)
    apply_guard(
        db, thread_id, analysis, str(payload.get("requested_by_email") or ""),
        str(payload.get("requested_by_name") or ""),
        [str(code) for code in payload.get("cited_codes") or []], message_id,
        str(payload.get("subject") or payload.get("project_name") or ""),
        str(payload.get("summary") or "")[:160], "work")
    row = db.execute(
        "SELECT status, last_inbound_id FROM quotes WHERE thread_id=?", (thread_id,)
    ).fetchone()
    reanalysis_of_same_message = bool(row) and row["last_inbound_id"] == message_id
    protected = _DASHBOARD_PROTECTED if reanalysis_of_same_message else _DASHBOARD_PROTECTED - {
        QuoteStatus.RESPONSE_SENT}
    if row and row["status"] in protected:
        # un caso ya avanzado en el dashboard no retrocede por un borrador posterior; solo una
        # respuesta nueva del cliente puede mover un caso que estaba esperando su respuesta
        status = row["status"]
    save(db, thread_id, message_id, status,
         payload.get("internal_sender") or payload["client_email"], analysis)
    version = hashlib.sha1(raw.encode("utf-8")).hexdigest()[:12]
    record_event(db, thread_id, "analysis", "work", version,
                 f"Work analizó el caso: {payload['status']}.", payload.get("received_at", ""))
    db.commit()


def _commercial_metadata(payload: dict, saved: sqlite3.Row | None) -> tuple[str, str, str, str]:
    """Datos comerciales del caso: lo que una persona ya fijó en el dashboard manda sobre el borrador de Work."""
    client_type = payload.get("client_type", "No clasificado")
    sector = payload.get("sector", "No clasificado")
    if client_type not in CLIENT_TYPES:
        client_type = "No clasificado"
    if sector not in SECTORS:
        sector = "No clasificado"
    client_name = str(payload.get("client_name", "")).strip()
    notes = str(payload.get("notes", "")).strip()
    if saved:
        if saved["client_type"] != "No clasificado":
            client_type = saved["client_type"]
        if saved["sector"] != "No clasificado":
            sector = saved["sector"]
        client_name = saved["client_name"] or client_name
        notes = saved["notes"] or notes
    return client_name, client_type, sector, notes


def dashboard_upsert(payload_base64: str) -> None:
    """Importa al dashboard el borrador de Work (JSON en base64): crea o actualiza el caso y sus datos comerciales."""
    raw, payload = _decode_work_payload(payload_base64)
    analysis = _work_analysis(payload)
    thread_id = payload["thread_id"]
    db = connect()
    try:
        _store_work_analysis(db, raw, payload, analysis)
        saved = db.execute(
            "SELECT client_type, sector, client_name, notes FROM quote_metadata WHERE thread_id=?",
            (thread_id,),
        ).fetchone()
    finally:
        db.close()
    client_name, client_type, sector, notes = _commercial_metadata(payload, saved)
    save_quote_metadata(thread_id, client_name, client_type, sector, notes)
    programar_actualizacion_de_hoja(thread_id)   # si el análisis cambió la tabla, la hoja de Google lo refleja
