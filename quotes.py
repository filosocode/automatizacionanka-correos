"""Solicitudes en SQLite: consultas, notificaciones, cola de revisión y edición."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from datetime import datetime
from pathlib import Path
from status import (
    REVIEW_QUEUE_STATUSES, QuoteStatus, sql_in,
)
import settings
from mail import (
    _decode_websafe,
)
from reception import (
    reception_assessment,
)
from logging_setup import get_logger
from store import case_events, open_db, record_event, save

log = get_logger(__name__)



def save_review_packet(thread_id: str, recipient: str, analysis: dict,
                       questions: list[str], body: str | None,
                       folder_name: str | None = None) -> Path:
    queue = settings.DATA_DIR / "review_queue"
    queue.mkdir(parents=True, exist_ok=True)
    path = queue / f"{thread_id}.txt"
    content = [
        f"Hilo: {thread_id}",
        f"Destinatario: {recipient}",
        f"Estado sugerido: {'preguntar al cliente' if body else 'información suficiente para revisión interna'}",
        "",
        "Resumen:",
        analysis.get("summary", ""),
        "",
        "Actividades:",
        json.dumps(analysis.get("activities", []), ensure_ascii=False, indent=2),
        "",
    ]
    if analysis.get("routing"):
        content.extend([
            "Enrutamiento del correo:",
            json.dumps(analysis["routing"], ensure_ascii=False, indent=2),
            "",
        ])
    if analysis.get("scope_dependencies"):
        content.extend([
            "Dependencias de alcance:",
            json.dumps(analysis["scope_dependencies"], ensure_ascii=False, indent=2),
            "",
        ])
    if analysis.get("contradictions"):
        content.extend([
            "Contradicciones detectadas:",
            json.dumps(analysis["contradictions"], ensure_ascii=False, indent=2),
            "",
        ])
    if analysis.get("attachments"):
        content.extend([
            "Adjuntos y bloqueos:",
            json.dumps(analysis["attachments"], ensure_ascii=False, indent=2),
            "",
        ])
    if questions:
        content.extend(["Preguntas faltantes:", *[f"- {q}" for q in questions], ""])
    if body:
        content.extend(["Borrador de correo:", body, ""])
    if folder_name:
        content.extend(["Carpeta propuesta:", folder_name, ""])
    content.extend([
        "JSON completo:",
        json.dumps(analysis, ensure_ascii=False, indent=2),
        "",
    ])
    path.write_text("\n".join(content), encoding="utf-8")
    return path


def init_db() -> sqlite3.Connection:
    return open_db(settings.DATA_DIR)


def unread_notifications(limit: int = 20) -> list[dict]:
    db = init_db()
    try:
        rows = db.execute("""SELECT n.*, q.analysis_json
                             FROM dashboard_notifications n
                             JOIN quotes q ON q.thread_id = n.thread_id
                             WHERE n.read_at IS NULL
                             ORDER BY n.created_at DESC, n.id DESC
                             LIMIT ?""", (limit,)).fetchall()
    finally:
        db.close()
    result = []
    for row in rows:
        analysis = _load_analysis(row["analysis_json"])
        result.append({
            "id": row["id"], "thread_id": row["thread_id"],
            "kind": row["kind"], "title": row["title"],
            "message": row["message"], "created_at": row["created_at"],
            "project_name": analysis.get("project_name") or "Solicitud sin nombre",
        })
    return result


def mark_notifications_read(thread_id: str | None = None) -> None:
    now = datetime.now().astimezone().isoformat(timespec="seconds")
    db = init_db()
    try:
        if thread_id:
            db.execute("""UPDATE dashboard_notifications SET read_at=?
                          WHERE read_at IS NULL AND thread_id=?""", (now, thread_id))
        else:
            db.execute(
                "UPDATE dashboard_notifications SET read_at=? WHERE read_at IS NULL", (now,)
            )
        db.commit()
    finally:
        db.close()


def review_packet_path(thread_id: str) -> Path:
    return settings.DATA_DIR / "review_queue" / f"{thread_id}.txt"


def review_action(status: str) -> tuple[str, str]:
    if status == QuoteStatus.REVIEW_PENDING:
        return "Requiere aclaraciones", "Enviar preguntas al cliente"
    if status == QuoteStatus.READY_REVIEW:
        return "Información suficiente", "Revisión interna"
    if status == QuoteStatus.SENDING:
        return "Revisar envio interrumpido", "No disponible"
    return "Revision manual", "No disponible"


STATUS_LABELS = {
    QuoteStatus.REVIEW_PENDING: "Requiere aclaraciones",
    QuoteStatus.READY_REVIEW: "Información suficiente para revisión interna",
    QuoteStatus.SENDING: "Enviando",
    QuoteStatus.REVIEW: "Revisión técnica interna",
    QuoteStatus.WAITING: "Esperando cliente",
    QuoteStatus.DRAFT_CREATED: "Borrador preparado",
    QuoteStatus.RESPONSE_SENT: "Respuesta enviada",
    QuoteStatus.READY: "Proyecto creado",
    QuoteStatus.MISSING: "Hilo no disponible",
    QuoteStatus.IGNORED: "Descartada",
}


CLIENT_TYPES = [
    "No clasificado", "Empresa privada", "Entidad pública",
    "Consultor o aliado", "Particular",
]


SECTORS = [
    "No clasificado", "Energía", "Infraestructura", "Hidrocarburos",
    "Ambiente", "Catastro", "Otro",
]


def _load_analysis(value: str) -> dict:
    try:
        return json.loads(value) if value else {}
    except (json.JSONDecodeError, TypeError):
        return {}


def _service_names(analysis: dict) -> list[str]:
    services = []
    for activity in analysis.get("activities", []):
        service = (activity.get("servicio_tecnica") or activity.get("activity") or "").strip()
        if service:
            services.append(service)
    return list(dict.fromkeys(services))


def all_quotes(include_ignored: bool = False) -> list[dict]:
    db = init_db()
    try:
        where = "" if include_ignored else f"WHERE q.status <> '{QuoteStatus.IGNORED}'"
        rows = db.execute(f"""SELECT q.*, m.client_type, m.sector,
                                      m.client_name, m.notes
                               FROM quotes q
                               LEFT JOIN quote_metadata m ON m.thread_id = q.thread_id
                               {where}
                               ORDER BY COALESCE(q.updated_at, q.first_seen_at, '') DESC,
                                        q.thread_id DESC""").fetchall()
    finally:
        db.close()
    quotes = []
    for row in rows:
        analysis = _load_analysis(row["analysis_json"])
        routing = analysis.get("routing", {})
        client_email = routing.get("original_sender_email") or row["recipient"]
        assessment = reception_assessment(analysis) if analysis else {}
        status_label = STATUS_LABELS.get(row["status"], row["status"])
        review_states = {QuoteStatus.REVIEW_PENDING, QuoteStatus.READY_REVIEW, QuoteStatus.REVIEW}
        if row["status"] in review_states and assessment:
            status_label = assessment["label"]
        quotes.append({
            "thread_id": row["thread_id"],
            "status": row["status"],
            "status_label": status_label,
            "recipient": row["recipient"],
            "client_email": client_email,
            "client_name": row["client_name"] or routing.get("original_sender") or client_email,
            "client_name_saved": row["client_name"] or "",
            "client_type": row["client_type"] or "No clasificado",
            "sector": row["sector"] or "No clasificado",
            "notes": row["notes"] or "",
            "analysis": analysis,
            "reception_assessment": assessment,
            "services": _service_names(analysis),
            "first_seen_at": row["first_seen_at"] or analysis.get("received_at") or "",
            "updated_at": row["updated_at"] or "",
            "folder_id": row["folder_id"],
            "draft_id": row["draft_id"],
            "draft_body": row["draft_body"] or "",
            "sent_at": row["sent_at"] or "",
        })
    return quotes


def get_quote(thread_id: str) -> dict | None:
    return next((item for item in all_quotes(True) if item["thread_id"] == thread_id), None)


def discarded_quotes() -> list[dict]:
    """Hilos descartados, con el motivo, para que una persona pueda revisarlos y rescatarlos."""
    result = []
    for quote in all_quotes(True):
        if quote["status"] != QuoteStatus.IGNORED:
            continue
        analysis = quote["analysis"]
        email = analysis.get("email") or {}
        result.append({
            "thread_id": quote["thread_id"],
            "reason": analysis.get("ignored_reason") or "Descartado por una versión anterior (sin motivo guardado)",
            "subject": email.get("subject") or "",
            "sender": email.get("from") or quote["recipient"],
            "date": (quote["updated_at"] or quote["first_seen_at"] or "")[:10],
            "rescued": bool(analysis.get("force_analysis")),
        })
    return result


def rescue_discarded(thread_id: str) -> str:
    """Marca un hilo descartado para que la próxima revisión de Gmail lo analice.

    No cambia su estado: lo reprocesa el ciclo normal (`pipeline.run`), que salta el
    filtro de ruido para los hilos con `force_analysis`.
    """
    db = init_db()
    try:
        row = db.execute(
            "SELECT status, analysis_json FROM quotes WHERE thread_id=?", (thread_id,)
        ).fetchone()
        if not row:
            raise ValueError("La solicitud no existe.")
        if row["status"] != QuoteStatus.IGNORED:
            raise ValueError("Solo se pueden rescatar los correos descartados.")
        analysis = _load_analysis(row["analysis_json"])
        analysis["force_analysis"] = True
        db.execute(
            "UPDATE quotes SET last_inbound_id='', analysis_json=?, updated_at=? WHERE thread_id=?",
            (json.dumps(analysis, ensure_ascii=False),
             datetime.now().astimezone().isoformat(timespec="seconds"), thread_id),
        )
        db.commit()
    finally:
        db.close()
    log.info("%s: rescatado del descarte; se reanalizará en la próxima revisión", thread_id)
    return "Se reanalizará en la próxima revisión de Gmail."


def delete_quotes(thread_ids: list[str]) -> str:
    """Borra del dashboard los hilos indicados: solicitud, datos comerciales, novedades y paquete de revisión.

    Solo toca la base local y `review_queue`; no modifica Gmail, Drive ni Notion.
    """
    ids = list(dict.fromkeys(thread_ids))
    if not ids:
        return "No seleccionaste ninguna solicitud."
    marks = ",".join("?" * len(ids))
    db = init_db()
    try:
        found = [row["thread_id"] for row in db.execute(
            f"SELECT thread_id FROM quotes WHERE thread_id IN ({marks})", ids)]
        if found:
            found_marks = ",".join("?" * len(found))
            for table in ("dashboard_notifications", "quote_metadata", "case_events", "quotes"):
                db.execute(f"DELETE FROM {table} WHERE thread_id IN ({found_marks})", found)
        db.commit()
    finally:
        db.close()
    for thread_id in found:
        review_packet_path(thread_id).unlink(missing_ok=True)
    log.info("Eliminadas %d solicitud(es) del dashboard: %s", len(found), ", ".join(found))
    if not found:
        return "Las solicitudes seleccionadas ya no existían."
    return f"Se eliminaron {len(found)} solicitud(es) del dashboard."


def resolve_technical_review(thread_id: str, decision: str, questions_text: str = "") -> str:
    """Una persona cierra la revisión técnica: pasa a información suficiente o pregunta al cliente.

    Guarda la decisión en `analysis["manual_review"]` (quita el bloqueo técnico de la evaluación)
    y mueve el estado. No envía nada. Si llega un mensaje nuevo en el hilo, el reanálisis
    reemplaza el análisis y la decisión se descarta: lo nuevo se revisa otra vez.
    """
    if decision not in ("sufficient", "ask_client"):
        raise ValueError("Decisión no válida.")
    questions = [line.strip()[:500] for line in questions_text.splitlines() if line.strip()][:10]
    if decision == "ask_client" and not questions:
        raise ValueError("Escribe al menos una pregunta para el cliente (una por línea).")
    db = init_db()
    try:
        row = db.execute(
            "SELECT status, analysis_json FROM quotes WHERE thread_id=?", (thread_id,)
        ).fetchone()
        if not row:
            raise ValueError("La solicitud ya no existe.")
        if row["status"] != QuoteStatus.REVIEW:
            raise ValueError("Solo se puede decidir sobre casos en revisión técnica interna.")
        now = datetime.now().astimezone().isoformat(timespec="seconds")
        analysis = _load_analysis(row["analysis_json"])
        analysis["manual_review"] = {
            "decision": decision, "at": now, "questions": questions if decision == "ask_client" else [],
        }
        new_status = QuoteStatus.READY_REVIEW if decision == "sufficient" else QuoteStatus.REVIEW_PENDING
        db.execute(
            "UPDATE quotes SET status=?, analysis_json=?, updated_at=? WHERE thread_id=?",
            (new_status, json.dumps(analysis, ensure_ascii=False), now, thread_id),
        )
        db.commit()
    finally:
        db.close()
    log.info("%s: revisión técnica resuelta por una persona (%s)", thread_id, decision)
    if decision == "sufficient":
        return "Marcado como información suficiente. Ya puedes crear la carpeta del proyecto en Drive."
    return "Listo: revisa y envía las preguntas en la sección Respuesta al cliente."


def save_quote_metadata(thread_id: str, client_name: str, client_type: str,
                        sector: str, notes: str) -> None:
    if client_type not in CLIENT_TYPES:
        raise ValueError("Tipo de cliente no válido.")
    if sector not in SECTORS:
        raise ValueError("Sector no válido.")
    if not get_quote(thread_id):
        raise ValueError("La solicitud ya no existe.")
    now = datetime.now().astimezone().isoformat(timespec="seconds")
    db = init_db()
    try:
        db.execute("""INSERT INTO quote_metadata
                      (thread_id, client_type, sector, client_name, notes, updated_at)
                      VALUES (?, ?, ?, ?, ?, ?)
                      ON CONFLICT(thread_id) DO UPDATE SET
                      client_type=excluded.client_type, sector=excluded.sector,
                      client_name=excluded.client_name, notes=excluded.notes,
                      updated_at=excluded.updated_at""",
                   (thread_id, client_type, sector, client_name.strip(), notes.strip(), now))
        db.commit()
    finally:
        db.close()


def set_client_name(thread_id: str, client_name: str) -> None:
    """Guarda el cliente confirmado de un caso sin tocar el resto de sus datos comerciales."""
    now = datetime.now().astimezone().isoformat(timespec="seconds")
    db = init_db()
    try:
        db.execute("""INSERT INTO quote_metadata
                      (thread_id, client_type, sector, client_name, notes, updated_at)
                      VALUES (?, 'No clasificado', 'No clasificado', ?, '', ?)
                      ON CONFLICT(thread_id) DO UPDATE SET
                      client_name=excluded.client_name, updated_at=excluded.updated_at""",
                   (thread_id, client_name.strip(), now))
        db.commit()
    finally:
        db.close()


def save_activities(thread_id: str, activities: list[dict]) -> str:
    """Guarda la tabla editada a mano y la marca para que un reproceso no la pise."""
    db = init_db()
    try:
        row = db.execute(
            "SELECT analysis_json FROM quotes WHERE thread_id=?", (thread_id,)
        ).fetchone()
        if not row:
            raise ValueError("La solicitud ya no existe.")
        analysis = _load_analysis(row["analysis_json"])
        now = datetime.now().astimezone().isoformat(timespec="seconds")
        analysis["activities"] = activities
        analysis["manual_edit"] = {"edited_at": now, "rows": len(activities)}
        db.execute(
            "UPDATE quotes SET analysis_json=?, updated_at=? WHERE thread_id=?",
            (json.dumps(analysis, ensure_ascii=False), now, thread_id),
        )
        db.commit()
    finally:
        db.close()
    return f"Tabla guardada ({len(activities)} actividades)."


_DASHBOARD_PROTECTED = frozenset({
    QuoteStatus.READY, QuoteStatus.RESPONSE_SENT, QuoteStatus.SENDING,
})


def dashboard_upsert(payload_base64: str) -> None:
    try:
        raw = _decode_websafe(payload_base64).decode("utf-8")
        payload = json.loads(raw)
    except (ValueError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise SystemExit(f"Registro de dashboard no válido: {exc}") from exc
    required = {"thread_id", "status", "client_email", "project_name"}
    missing = sorted(field for field in required if not str(payload.get(field, "")).strip())
    if missing:
        raise SystemExit("Faltan campos del dashboard: " + ", ".join(missing))
    status_map = {
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
    activities = payload.get("activities", [])
    if isinstance(activities, str):
        activities = [{
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
    elif isinstance(activities, list):
        activities = [
            item if isinstance(item, dict) else {
                "activity": str(item), "servicio_tecnica": payload.get("service", ""),
                "ubicacion": payload.get("location", ""), "quantity": "", "unit": "",
                "entregable": "", "plazo_restriccion": "", "fuente": "",
                "estado": "pendiente", "notes": "",
            }
            for item in activities
        ]
    missing_data = payload.get("missing_data", [])
    if isinstance(missing_data, str):
        missing_data = [missing_data] if missing_data.strip() else []
    contradictions = payload.get("contradictions", [])
    if isinstance(contradictions, str):
        contradictions = ([{
            "topic": "dato técnico", "details": contradictions,
            "sources": [], "status": "pendiente",
        }] if contradictions.strip() else [])
    dependencies = payload.get("scope_dependencies", [])
    if isinstance(dependencies, str):
        dependencies = ([{
            "description": dependencies, "source": payload.get("source", ""),
            "status": "pendiente",
        }] if dependencies.strip() else [])
    attachments = payload.get("attachments", [])
    if isinstance(attachments, str):
        attachments = ([{
            "filename": attachments, "status": "registrado", "reason": "",
            "blocked_fields": [],
        }] if attachments.strip() else [])
    analysis = {
        "is_quote_request": payload["status"] != "Descartada",
        "project_name": payload["project_name"],
        "summary": payload.get("summary", ""),
        "activities": activities,
        "fields": payload.get("fields", []),
        "other_questions": missing_data,
        "scope_dependencies": dependencies,
        "contradictions": contradictions,
        "attachments": attachments,
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
    thread_id = payload["thread_id"]
    message_id = payload.get("message_id") or thread_id
    status = status_map.get(payload["status"], payload["status"])
    db = init_db()
    try:
        row = db.execute(
            "SELECT status, last_inbound_id FROM quotes WHERE thread_id=?", (thread_id,)
        ).fetchone()
        if row and row["status"] in _DASHBOARD_PROTECTED:
            # un caso ya avanzado en el dashboard no retrocede por un borrador posterior
            status = row["status"]
        save(db, thread_id, message_id, status,
             payload.get("internal_sender") or payload["client_email"], analysis)
        version = hashlib.sha1(raw.encode("utf-8")).hexdigest()[:12]
        record_event(db, thread_id, "analysis", "work", version,
                     f"Work analizó el caso: {payload['status']}.", payload.get("received_at", ""))
        db.commit()
        meta = db.execute(
            "SELECT client_type, sector, client_name, notes FROM quote_metadata WHERE thread_id=?",
            (thread_id,),
        ).fetchone()
    finally:
        db.close()
    client_type = payload.get("client_type", "No clasificado")
    sector = payload.get("sector", "No clasificado")
    if client_type not in CLIENT_TYPES:
        client_type = "No clasificado"
    if sector not in SECTORS:
        sector = "No clasificado"
    client_name = str(payload.get("client_name", "")).strip()
    notes = str(payload.get("notes", "")).strip()
    if meta:  # lo que una persona ya fijó en el dashboard tiene prioridad sobre el borrador
        if meta["client_type"] != "No clasificado":
            client_type = meta["client_type"]
        if meta["sector"] != "No clasificado":
            sector = meta["sector"]
        client_name = meta["client_name"] or client_name
        notes = meta["notes"] or notes
    save_quote_metadata(thread_id, client_name, client_type, sector, notes)


def thread_events(thread_id: str) -> list[dict]:
    """Seguimiento del caso, del más reciente al más antiguo."""
    db = init_db()
    try:
        rows = case_events(db, thread_id)
    finally:
        db.close()
    return [dict(row) for row in reversed(rows)]


def change_marker() -> str:
    """Cambia cuando se añade, edita o borra cualquier caso (para refrescar el dashboard)."""
    db = init_db()
    try:
        row = db.execute(
            "SELECT COUNT(*) AS n, MAX(COALESCE(updated_at, first_seen_at)) AS ts, "
            "(SELECT COUNT(*) FROM dashboard_notifications WHERE read_at IS NULL) AS unread, "
            "(SELECT COUNT(*) || MAX(recorded_at) FROM case_events) AS ev FROM quotes"
        ).fetchone()
    finally:
        db.close()
    return f"{row['n']}|{row['ts'] or ''}|{row['unread']}|{row['ev']}"


def pending_reviews() -> list[dict]:
    db = init_db()
    try:
        rows = db.execute(
            f"""SELECT thread_id, status, recipient, analysis_json
               FROM quotes
               WHERE status IN {sql_in(*REVIEW_QUEUE_STATUSES)}
               ORDER BY thread_id"""
        ).fetchall()
    finally:
        db.close()
    reviews = []
    for thread_id, status, recipient, analysis_json in rows:
        try:
            analysis = json.loads(analysis_json) if analysis_json else {}
        except json.JSONDecodeError:
            analysis = {}
        status_label, action_label = review_action(status)
        reviews.append({
            "thread_id": thread_id,
            "status": status,
            "status_label": status_label,
            "action_label": action_label,
            "recipient": recipient,
            "analysis": analysis,
            "packet": review_packet_path(thread_id),
        })
    return reviews


def get_review(thread_id: str) -> dict | None:
    for review in pending_reviews():
        if review["thread_id"] == thread_id:
            return review
    return None


def list_reviews() -> None:
    reviews = pending_reviews()
    if not reviews:
        print("No hay revisiones humanas pendientes.")
        return
    for review in reviews:
        packet = review["packet"]
        suffix = f" -> {packet}" if packet.exists() else " -> sin archivo en review_queue"
        print(f"{review['thread_id']} [{review['status']}] {review['recipient']}{suffix}")
