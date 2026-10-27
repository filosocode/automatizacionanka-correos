"""Importación del borrador de ChatGPT Work al dashboard (`dashboard_upsert`).

Decodifica el JSON del contrato (spec/03-contrato-work.md), lo convierte al análisis interno, aplica el control de
cambios y guarda el caso sin hacer retroceder uno que ya avanzó en el dashboard.
"""

from __future__ import annotations

import hashlib
from datetime import datetime
import json
import re
import sqlite3
from collections.abc import Callable
from typing import Any

from anka.dominio.analisis import Analisis
from anka.dominio.errores import ErrorDeAnka, ErrorDeValidacion
from anka.dominio.activities import limpiar_cantidad_y_unidad, normalize_estado, unidades_en_cantidad
from anka.dominio.correo import decode_websafe, reply_body_without_quoted_history
from anka.dominio.reception import unresolved_scope_issues
from anka.dominio.status import QuoteStatus, estado_tras_un_analisis
from anka.dominio.tolerancia_work import Rechazo, comprobar_identidad, normalizar_analisis
from anka.infra.logging_setup import get_logger
from anka.infra import rechazos_de_work
from anka.infra.store import begin_immediate, record_event, save, transaccion
from anka.dominio.cancelacion_work import MensajeCancelado
from anka.servicios.cancelaciones import mensaje_cancelado
from anka.servicios.change_control import apply_guard
from anka.servicios.estados import verificar_transicion
from anka.servicios.hoja_de_calculo import programar_actualizacion_de_hoja
from anka.servicios.quotes import save_quote_metadata
from anka.dominio.clasificacion import AVANZAN, NO_SOLICITUD, REVISION_MANUAL
from anka.servicios.recepcion import conciliar_con_work
from anka.servicios.trabajos_work import marcar_conciliado
from anka.dominio.caso import CLIENT_TYPES, SECTORS
from anka.dominio.work_contract import (
    attachment_coverage_warnings,
    question_evidence_warnings,
    reading_summary,
    respuesta_utilizable,
)

log = get_logger(__name__)


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
        # un «\ud800» suelto es JSON válido pero no se puede guardar como texto: se rechaza aquí, no al guardar
        json.dumps(payload, ensure_ascii=False).encode("utf-8")
    except (ValueError, UnicodeError, TypeError) as exc:
        raise ErrorDeAnka(f"Registro de dashboard no válido: {exc}") from exc
    if not isinstance(payload, dict):
        raise ErrorDeAnka("Registro de dashboard no válido: el contrato debe ser un objeto JSON.")
    try:        # la misma política que el puente (U-51): identidad, estado, reparación con aviso y esquema
        comprobar_identidad(payload)
        normalizar_analisis(payload)
    except Rechazo as exc:
        if exc.causa == "estado_desconocido":     # la importación a mano sí dice qué estado llegó (T-33)
            raise ErrorDeAnka("Estado de Work no reconocido en el campo status: "
                              f"«{_declarado(payload.get('status'), 80)}».") from exc
        raise ErrorDeAnka(f"Registro de dashboard no válido: {exc.motivo}") from exc
    return raw, payload


def _como_lista(valor: Any) -> list:
    """Una lista del borrador: un texto suelto es un elemento; `None` o vacío, ninguno."""
    if isinstance(valor, list):
        return valor
    if isinstance(valor, str):
        return [valor] if valor.strip() else []
    return []


def _work_activities(payload: dict) -> list:
    """Tabla de actividades del borrador, aceptando también un texto suelto o filas que son solo texto."""
    activities = payload.get("activities")
    if not isinstance(activities, (list, str)):
        activities = []          # `null` en un campo opcional es lo mismo que no escribirlo
    if isinstance(activities, list):
        normalized = []
        for item in activities:
            if item is None:
                continue         # una fila vacía no es una actividad (no se escribe «None» en la tabla)
            if not isinstance(item, dict):
                normalized.append(item)
                continue
            item = dict(item)
            quantity = item.get("quantity")
            if isinstance(quantity, (int, float)) and not isinstance(quantity, bool):
                item["quantity"] = str(quantity)
            limpiar_cantidad_y_unidad(item)
            if "estado" in item:
                item["estado"] = normalize_estado(item["estado"])
            normalized.append(item)
        activities = normalized
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


def _work_missing_data(value: Any) -> list[str]:
    """Normaliza faltantes legacy (texto) y el formato de Work ({name, type})."""
    items = _list_or_wrapped(value, lambda text: text)
    if not isinstance(items, list):
        return []
    normalized = []
    for item in items:
        if isinstance(item, dict):
            name = str(item.get("name") or "").strip()
            kind = str(item.get("type") or "").strip()
            if name:
                normalized.append(f"{name} ({kind})" if kind else name)
        elif item is not None:
            text = str(item).strip()
            if text:
                normalized.append(text)
    return normalized


def _work_analysis(payload: dict) -> Analisis:
    """El análisis interno (el mismo formato que produce el piloto de Python) a partir del borrador de Work."""
    source = payload.get("source", "")
    # El historial citado se quita aquí, del borrador de Work y de nadie más (U-39).
    reply_text = respuesta_utilizable(reply_body_without_quoted_history(str(payload.get("draft_reply") or "")).strip())
    if payload.get("analysis_complete") is False:
        reply_text = ""  # un registro interno bloqueado no propone un correo para enviar al cliente
    modelo = _declarado(payload.get("model"), MAX_MODELO)
    version = _declarado(payload.get("skill_version"), MAX_VERSION)
    raw_activities = payload.get("activities", [])
    unit_warnings = []
    unit_dependencies = []
    if isinstance(raw_activities, list):
        for index, row in enumerate(raw_activities):
            if not isinstance(row, dict):
                continue
            units = unidades_en_cantidad(row.get("quantity"))
            if len(units) > 1:
                label = str(row.get("activity") or f"fila {index + 1}")
                details = ", ".join(sorted(units))
                message = (f"Actividad «{label}»: la cantidad mezcla varias unidades ({details}); "
                           "revisar y separar las magnitudes antes de cerrar el alcance.")
                unit_warnings.append(message)
                unit_dependencies.append({"description": message, "source": row.get("fuente") or source,
                                          "status": "pendiente"})
    clasificacion = str(payload.get("classification") or "")
    avanza = not clasificacion or clasificacion in AVANZAN          # T-52: solo lo confirmado sigue a actividades
    coverage_warnings = attachment_coverage_warnings(payload)
    reading = reading_summary(payload)
    visual_dependencies = [{"description": warning, "source": source, "status": "pendiente"}
                           for warning in coverage_warnings if "lectura visual no certificada" in warning]
    warnings = (question_evidence_warnings(payload) + coverage_warnings + unit_warnings
                + [str(aviso) for aviso in _como_lista(payload.get("_avisos_del_puente"))])
    dependencies = _list_or_wrapped(payload.get("scope_dependencies", []), lambda text: {
        "description": text, "source": source, "status": "pendiente"})
    if not isinstance(dependencies, list):
        dependencies = []
    reading_dependencies = [{"description": warning, "source": "DRIVE_PREFLIGHT / reading_audit",
                             "status": "pendiente"} for warning in reading["warnings"]]
    dependencies = [*dependencies, *unit_dependencies, *visual_dependencies, *reading_dependencies]
    return {
        "is_quote_request": payload["status"] != "Descartada" and clasificacion != NO_SOLICITUD,
        **({"classification": clasificacion,
            "classification_reason": _declarado(payload.get("classification_reason"), 300)} if clasificacion else {}),
        "project_name": payload["project_name"],
        "summary": payload.get("summary", ""),
        "activities": _work_activities(payload) if avanza else [],
        "fields": payload.get("fields", []),
        "question_audit": payload.get("question_audit", []),
        "evidence_register": payload.get("evidence_register", []),
        "question_quality_warnings": warnings,
        **({"correcciones_de_work": [str(a) for a in _como_lista(payload.get("_correcciones_de_work"))]}
           if _como_lista(payload.get("_correcciones_de_work")) else {}),
        **({"sin_analisis_de_work": _rechazo_guardado(payload.get("_rechazo_de_work"))}
           if payload.get("_caso_minimo") is True else {}),
        "other_questions": _work_missing_data(payload.get("missing_data", [])) if avanza else [],
        "scope_dependencies": dependencies,
        "contradictions": _list_or_wrapped(payload.get("contradictions", []), lambda text: {
            "topic": "dato técnico", "details": text, "sources": [], "status": "pendiente"}),
        "attachments": _list_or_wrapped(payload.get("attachments", []), lambda text: {
            "filename": text, "status": "registrado", "reason": "", "blocked_fields": []}),
        "work_reply": ({"text": reply_text, "draft_id": str(payload.get("draft_id") or "")}
                       if reply_text and avanza else {}),
        "received_at": payload.get("received_at", ""),
        "work_message_id": payload.get("message_id") or "",
        "gmail_url": payload.get("gmail_url", ""),
        "engine": payload.get("engine", ""),
        "DRIVE_PREFLIGHT": payload.get("DRIVE_PREFLIGHT") or {},
        "reading_audit": payload.get("reading_audit") or {},
        "reading_summary": reading,
        "evidence_check": payload.get("evidence_check") or {},
        "areas_calculadas": payload.get("areas_calculadas") or [],
        "diferencias_de_area": payload.get("diferencias_de_area") or [],
        "filas_de_hoja_sin_cubrir": payload.get("filas_de_hoja_sin_cubrir") or [],
        "related_threads": payload.get("related_threads") or [],
        "applicability": payload.get("applicability") or {},
        **({"analysis_complete": payload["analysis_complete"]} if "analysis_complete" in payload else {}),
        **({"work_model": modelo} if modelo else {}),
        **({"work_skill_version": version} if version else {}),
        "routing": {
            "original_sender": payload.get("client_name", ""),
            "original_sender_email": payload["client_email"],
            "internal_forwarder": payload.get("internal_sender", ""),
            "selected_recipient": payload.get("internal_sender") or payload["client_email"],
            "is_forwarded": bool(payload.get("internal_sender")),
        },
    }


def _rechazo_guardado(valor: Any) -> dict[str, Any]:
    """De un rechazo solo se guarda el motivo, los nombres de campo y la fecha (T-36): nunca contenido del cliente."""
    valor = valor if isinstance(valor, dict) else {}
    campos = valor.get("campos")
    return {"motivo": _declarado(valor.get("motivo"), 300),
            "campos": [_declarado(c, 80) for c in campos][:10] if isinstance(campos, list) else [],
            "fecha": _declarado(valor.get("fecha"), 10)}


def _internal_status(payload: dict, analysis: Analisis) -> str:
    """Estado interno que corresponde al borrador de Work."""
    # el estado ya se validó al decodificar; si aun así no se reconoce, decide una persona (nunca se guarda tal cual)
    status = _WORK_STATUS_MAP.get(payload["status"], QuoteStatus.REVIEW)
    if payload.get("classification") == NO_SOLICITUD:           # T-52: Work confirma que no es una solicitud
        return QuoteStatus.IGNORED
    if payload.get("classification") == REVISION_MANUAL:        # T-52: decide una persona, sin avanzar el caso
        return QuoteStatus.REVIEW
    if payload.get("analysis_complete") is False or analysis.get("reading_summary", {}).get("status") == "pendiente":
        return QuoteStatus.REVIEW
    if status in (QuoteStatus.REVIEW_PENDING, QuoteStatus.READY_REVIEW, QuoteStatus.DRAFT_CREATED
                  ) and unresolved_scope_issues(analysis):
        # Igual que el piloto de Python (decide_analysis): con dependencias o contradicciones
        # pendientes el caso pasa a revisión técnica interna, donde una persona decide.
        return QuoteStatus.REVIEW
    return status


SIN_ANALISIS = ("El análisis de Work no se pudo leer; el caso se creó solo con los datos del correo "
                "(quién, asunto y fecha).")
MAX_MODELO = 80
MAX_VERSION = 40
_CONTROL = re.compile(r"[\x00-\x1f\x7f]+")


def _declarado(valor: object, tope: int) -> str:
    """Un dato que Work escribe sobre sí mismo (modelo, versión del prompt): una sola línea, sin caracteres de control y
    con tope, porque lo redacta el propio modelo y no es de fiar (T-32)."""
    return " ".join(_CONTROL.sub(" ", str(valor or "")).split())[:tope]


def _modelo_declarado(payload: dict) -> str:
    """' Modelo declarado por Work: X; prompt: Y.' para el historial, o '' si Work no los informó."""
    modelo = _declarado(payload.get("model"), MAX_MODELO)
    version = _declarado(payload.get("skill_version"), MAX_VERSION)
    partes = ([f"Modelo declarado por Work: {modelo}"] if modelo else []) + ([f"prompt: {version}"] if version else [])
    return (" " + "; ".join(partes) + ".") if partes else ""


def _demora_del_puente(payload: dict) -> str:
    """Cuánto pasó entre que el borrador de Work apareció en Gmail y que llegó al dashboard (T-29); '' si no se sabe."""
    try:
        desde = datetime.fromisoformat(str(payload.get("draft_at") or ""))
        if desde.tzinfo is None:
            # sin zona horaria no se sabe cuánto pasó; el dato es solo informativo y no descarta el borrador
            return ""
        segundos = max(0, int((datetime.now().astimezone() - desde).total_seconds()))
    except (ValueError, OverflowError):
        return ""
    log.info("%s: el borrador de Work tardó %d s en llegar al dashboard", payload.get("thread_id"), segundos)
    return f" El borrador estaba en Gmail desde las {desde:%H:%M:%S}; llegó al dashboard {segundos} s después."


def _store_work_analysis(db: sqlite3.Connection, raw: str, payload: dict, analysis: Analisis) -> None:
    """Guarda el análisis de Work respetando el control de cambios y sin hacer retroceder un caso avanzado."""
    thread_id = payload["thread_id"]
    message_id = payload.get("message_id") or thread_id
    status = _internal_status(payload, analysis)
    begin_immediate(db)   # el estado se lee y se escribe bajo el mismo candado: no se pisa al dashboard (U-42)
    if mensaje_cancelado(db, thread_id, payload.get("message_id")):
        raise MensajeCancelado("Ese mensaje fue cancelado (T-83): el análisis de Work no se importa.")
    apply_guard(
        db, thread_id, analysis, str(payload.get("requested_by_email") or ""),
        str(payload.get("requested_by_name") or ""),
        [str(code) for code in _como_lista(payload.get("cited_codes"))], message_id,
        str(payload.get("subject") or payload.get("project_name") or ""),
        str(payload.get("summary") or "")[:160], "work", confirmar=False)
    row = db.execute(
        "SELECT status, last_inbound_id FROM quotes WHERE thread_id=?", (thread_id,)
    ).fetchone()
    reanalysis_of_same_message = bool(row) and row["last_inbound_id"] == message_id
    if row and status == QuoteStatus.IGNORED and payload.get("classification") == NO_SOLICITUD:
        status = QuoteStatus.REVIEW       # T-52: un mensaje que no es solicitud no descarta un expediente existente
    if row:
        # un caso ya avanzado en el dashboard no retrocede por un borrador posterior; solo una
        # respuesta nueva del cliente puede mover un caso que estaba esperando su respuesta (U-47)
        status = estado_tras_un_analisis(row["status"], status, reanalysis_of_same_message)
    if row:
        verificar_transicion(db, thread_id, status, "work_import")
    save(db, thread_id, message_id, status,
         payload.get("internal_sender") or payload["client_email"], analysis)
    version = hashlib.sha1(raw.encode("utf-8")).hexdigest()[:12]
    hecho = (SIN_ANALISIS if payload.get("_caso_minimo") is True else
             f"Work analizó el caso: {payload['status']}.{_modelo_declarado(payload)}{_demora_del_puente(payload)}")
    record_event(db, thread_id, "analysis", "work", version, hecho, payload.get("received_at", ""))
    _conciliar_recepcion(db, payload, analysis, version)
    db.commit()


def _conciliar_recepcion(db: sqlite3.Connection, payload: dict, analysis: Analisis, version: str) -> None:
    """Cierra en la cola de recepción el mensaje que este análisis interpretó (T-48), dentro de la transacción.

    Solo cuenta un análisis de Work de verdad (no el caso mínimo del puente) de un mensaje que el borrador nombra. Un
    fallo de la cola no pierde el análisis: se revierte solo la conciliación y se avisa sin datos del correo.
    """
    message_id = payload.get("message_id")
    if payload.get("_caso_minimo") is True or not isinstance(message_id, str) or not message_id.strip():
        return
    db.execute("SAVEPOINT conciliacion_recepcion")
    try:
        resultado = conciliar_con_work(db, payload["thread_id"], message_id, version, analysis.get("analysis_complete"),
                                       str(payload.get("status") or ""),
                                       clasificacion=analysis.get("classification", ""))
        log.info("Conciliación de recepción: %s", resultado)
        if resultado in ("conciliado", "ya_conciliado"):
            marcar_conciliado(db, payload["thread_id"], message_id, version)      # T-49: cierra el trabajo del mensaje
    except sqlite3.Error as exc:
        db.execute("ROLLBACK TO conciliacion_recepcion")
        log.warning("No se pudo conciliar la cola de recepción; el análisis se guarda igual (%s)", type(exc).__name__)
    finally:
        db.execute("RELEASE conciliacion_recepcion")


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


def dashboard_upsert(payload_base64: str, actualizar_hoja: bool = True) -> None:
    """Importa al dashboard el borrador de Work (JSON en base64): crea o actualiza el caso y sus datos comerciales.

    `actualizar_hoja=False` (importación aislada, T-47) no programa la actualización de la hoja de Google.
    """
    raw, payload = _decode_work_payload(payload_base64)
    analysis = _work_analysis(payload)
    thread_id = payload["thread_id"]
    with transaccion(inmediata=False) as db:      # `_store_work_analysis` toma el candado y confirma lo suyo
        _store_work_analysis(db, raw, payload, analysis)
        saved = db.execute(
            "SELECT client_type, sector, client_name, notes FROM quote_metadata WHERE thread_id=?",
            (thread_id,),
        ).fetchone()
    client_name, client_type, sector, notes = _commercial_metadata(payload, saved)
    save_quote_metadata(thread_id, client_name, client_type, sector, notes)
    if actualizar_hoja:
        programar_actualizacion_de_hoja(thread_id)   # si el análisis cambió la tabla, la hoja de Google lo refleja


def dejar_para_despues_el_rechazo(draft_id: str, message_id: str, thread_id: str = "") -> str:
    """La persona deja para después el aviso de un análisis de Work que no se pudo leer (U-41).

    No cambia el caso ni envía nada: oculta el aviso del Resumen y, si el caso existe, deja constancia en su historial.
    """
    if not draft_id.strip():
        raise ErrorDeValidacion("No encontramos ese aviso. Recarga la página.")
    if not rechazos_de_work.dejar_para_despues(draft_id, message_id):
        raise ErrorDeValidacion("Ese aviso ya no está a la vista. Recarga la página.")
    if thread_id.strip():
        ahora = datetime.now().astimezone().isoformat(timespec="seconds")
        with transaccion(inmediata=False) as db:
            if db.execute("SELECT 1 FROM quotes WHERE thread_id=?", (thread_id,)).fetchone():
                ref = f"{ahora}-{hashlib.sha1(draft_id.encode()).hexdigest()[:6]}"
                record_event(db, thread_id, "analysis_notice", "dashboard", ref,
                             "Una persona dejó para después el aviso de que el análisis de Work no se pudo leer.",
                             ahora)
    return "Listo: el aviso queda para después. Si Work vuelve a mandar el análisis, se revisa solo."
