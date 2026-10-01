"""Solicitudes en SQLite: consultas, notificaciones, cola de revisión y edición."""

from __future__ import annotations

import hashlib
from uuid import uuid4
import json
import re
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
from change_control import apply_guard
from store import add_table_version, latest_table_version, case_events, open_db, record_event, register_change, save, verify_chain

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


REPLY_OVERDUE_MINUTES = 30      # una respuesta sin analizar más de este tiempo se resalta
REPLY_TOLERANCE_SECONDS = 120   # la fecha del correo y la de llegada pueden diferir unos segundos


def _timestamp(value: str) -> float:
    try:
        return datetime.fromisoformat(value).timestamp()
    except (TypeError, ValueError):
        return 0.0


def _unanalyzed_reply(latest: tuple[str, str] | None, last_inbound_id: str, analysis: dict,
                      first_seen_at: str, status: str) -> str:
    """Hora de la respuesta del cliente que el análisis todavía no recoge ('' si no hay)."""
    if not latest or status in (QuoteStatus.IGNORED, QuoteStatus.MISSING):
        return ""
    occurred_at, ref = latest
    if ref == last_inbound_id:
        return ""
    analyzed = _timestamp(analysis.get("received_at") or first_seen_at)
    return occurred_at if _timestamp(occurred_at) > analyzed + REPLY_TOLERANCE_SECONDS else ""


def minutes_since(value: str) -> int:
    return max(0, int((datetime.now().astimezone().timestamp() - _timestamp(value)) // 60))


def ago_text(minutes: int) -> str:
    if minutes < 2:
        return "hace un momento"
    if minutes < 90:
        return f"hace {minutes} min"
    if minutes < 2880:
        return f"hace {minutes // 60} h"
    return f"hace {minutes // 1440} días"


def model_proposal(analysis: dict) -> list[dict]:
    """Filas que el modelo propone y que difieren de la tabla que editó una persona."""
    proposed = analysis.get("pending_model_activities")
    if not isinstance(proposed, list):
        return []
    mine = {str(item.get("activity", "")).strip().casefold(): item
            for item in analysis.get("activities", []) if isinstance(item, dict)}
    changes = []
    for item in proposed:
        if not isinstance(item, dict):
            continue
        name = str(item.get("activity", "")).strip()
        current = mine.get(name.casefold())
        detail = " ".join(str(item.get(key) or "") for key in ("quantity", "unit")).strip()
        if current is None:
            changes.append({"kind": "nueva", "activity": name, "detail": detail})
        elif any(str(current.get(key) or "") != str(item.get(key) or "")
                 for key in ("quantity", "unit", "ubicacion", "entregable", "plazo_restriccion")):
            changes.append({"kind": "distinta", "activity": name, "detail": detail})
    return changes


REASON_LABELS = {
    "sender": "la misma persona que ya escribió en este caso",
    "domain": "otra persona de la misma empresa",
}


def reason_label(reason: str) -> str:
    base, _, hint = reason.partition("+")
    text = REASON_LABELS.get(base, "posible relación")
    return text + (" · el asunto menciona el proyecto" if hint else "")


def resolve_link(thread_id: str, link_id: int, accept: bool) -> str:
    """Una persona confirma o descarta que un correo de otro hilo pertenece a este caso."""
    db = init_db()
    try:
        link = db.execute(
            "SELECT * FROM case_links WHERE id=? AND thread_id=? AND state='pending'",
            (link_id, thread_id)).fetchone()
        if not link:
            return "Ese aviso ya se había resuelto."
        now = datetime.now().astimezone().isoformat(timespec="seconds")
        who = link["sender_name"] or link["sender_email"]
        db.execute("UPDATE case_links SET state=?, decided_at=? WHERE id=?",
                   ("linked" if accept else "dismissed", now, link_id))
        if accept:
            summary = (f"Confirmaste que el correo «{link['subject'][:80]}» de {who} "
                       f"({reason_label(link['reason'])}) pertenece a este caso: «{link['snippet']}»")
            message = ("Anotado en el seguimiento con su autor y fecha. El agente todavía no lee "
                       "otros hilos: actualiza la tabla a mano si hace falta.")
        else:
            summary = f"Descartaste que el correo «{link['subject'][:80]}» de {who} sea de este caso."
            message = "Descartado: no se mostrará de nuevo."
        record_event(db, thread_id, "linked" if accept else "dismissed", "dashboard",
                     f"link-{link_id}", summary, now, link["sender_email"], link["sender_name"])
        coded = db.execute("SELECT contact_email, quote_code FROM quotes WHERE thread_id=?",
                           (thread_id,)).fetchone()
        if accept and coded and coded["quote_code"]:
            contact = (coded["contact_email"] or "").casefold()
            register_change(
                db, thread_id, link["message_id"], link["sender_email"], link["sender_name"],
                link["subject"], link["snippet"], link["occurred_at"], bool(link["has_code"]),
                link["sender_email"] == contact, link["reason"].startswith("domain"))
        db.commit()
    finally:
        db.close()
    return message


def case_change_log(thread_id: str) -> list[dict]:
    """Todas las solicitudes de cambio del caso (cualquier estado), de la más antigua a la más nueva."""
    db = init_db()
    try:
        return [dict(row) for row in db.execute(
            "SELECT * FROM case_changes WHERE thread_id=? ORDER BY occurred_at", (thread_id,))]
    finally:
        db.close()


def chain_status(thread_id: str) -> tuple[bool, int]:
    db = init_db()
    try:
        return verify_chain(db, thread_id)
    finally:
        db.close()


def archived_threads() -> list[dict]:
    """Casos eliminados del dashboard cuyo historial se conserva como evidencia."""
    db = init_db()
    try:
        rows = db.execute(
            """SELECT thread_id, COUNT(*) AS n, MAX(occurred_at) AS last, MIN(occurred_at) AS first
               FROM case_events WHERE thread_id NOT IN (SELECT thread_id FROM quotes)
               GROUP BY thread_id ORDER BY last DESC""").fetchall()
    finally:
        db.close()
    return [dict(row) for row in rows]


def case_has_links(thread_id: str) -> bool:
    db = init_db()
    try:
        return db.execute(
            "SELECT 1 FROM case_links WHERE thread_id=? "
            "UNION SELECT 1 FROM quotes WHERE thread_id=? AND quote_code IS NOT NULL AND quote_code <> '' "
            "UNION SELECT 1 FROM table_versions WHERE thread_id=? AND source='dashboard' "
            "LIMIT 1", (thread_id, thread_id, thread_id)).fetchone() is not None
    finally:
        db.close()


def case_people(thread_id: str, contact_emails: set[str]) -> list[dict]:
    """Quién ha escrito en el caso: persona, empresa, cuántos correos y el último."""
    db = init_db()
    try:
        rows = db.execute(
            """SELECT actor_email, MAX(actor_name) AS name, COUNT(*) AS n, MAX(occurred_at) AS last
               FROM case_events WHERE thread_id=? AND actor_email <> ''
                 AND kind IN ('received', 'linked') GROUP BY actor_email ORDER BY MIN(occurred_at)""",
            (thread_id,)).fetchall()
    finally:
        db.close()
    people = []
    for row in rows:
        email = row["actor_email"]
        domain = email.rsplit("@", 1)[-1]
        people.append({
            "name": row["name"] or email, "email": email, "domain": domain,
            "count": row["n"], "last": row["last"][:16].replace("T", " "),
            "role": "Contacto del caso" if email in contact_emails else "Otra persona",
        })
    return people


def resolve_model_proposal(thread_id: str, use_proposal: bool) -> str:
    """La persona adopta la tabla que propone el modelo o conserva la suya."""
    db = init_db()
    try:
        row = db.execute("SELECT analysis_json FROM quotes WHERE thread_id=?", (thread_id,)).fetchone()
        if not row:
            raise ValueError("La solicitud ya no existe.")
        analysis = _load_analysis(row["analysis_json"])
        proposed = analysis.pop("pending_model_activities", None)
        if not isinstance(proposed, list):
            return "No había una propuesta pendiente."
        now = datetime.now().astimezone().isoformat(timespec="seconds")
        if use_proposal:
            add_table_version(db, thread_id, proposed, "dashboard",
                              "Adoptaste la tabla propuesta por el modelo", "otro")
            analysis["activities"] = proposed
            analysis.pop("manual_edit", None)   # vuelve a seguir al modelo
            summary = f"Adoptaste la tabla propuesta por el modelo ({len(proposed)} actividades)."
        else:
            summary = "Mantuviste tu tabla y descartaste la propuesta del modelo."
        db.execute("UPDATE quotes SET analysis_json=?, updated_at=? WHERE thread_id=?",
                   (json.dumps(analysis, ensure_ascii=False), now, thread_id))
        record_event(db, thread_id, "proposal", "dashboard", f"{now}-{uuid4().hex[:6]}", summary, now)
        db.commit()
    finally:
        db.close()
    return summary


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
        latest_reply = {r["thread_id"]: (r["occurred_at"], r["ref"]) for r in db.execute(
            "SELECT thread_id, ref, occurred_at FROM case_events "
            "WHERE kind='received' ORDER BY occurred_at, id")}
        changes: dict[str, list[dict]] = {}
        for change in db.execute(
                "SELECT * FROM case_changes WHERE state IN ('pending', 'change') ORDER BY occurred_at"):
            changes.setdefault(change["thread_id"], []).append(dict(change))
        pending_links: dict[str, list[dict]] = {}
        for link in db.execute(
                "SELECT * FROM case_links WHERE state='pending' ORDER BY occurred_at"):
            pending_links.setdefault(link["thread_id"], []).append(dict(link))
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
            "reply_pending": _unanalyzed_reply(
                latest_reply.get(row["thread_id"]), row["last_inbound_id"], analysis,
                row["first_seen_at"] or "", row["status"]),
            "proposal": model_proposal(analysis),
            "links": pending_links.get(row["thread_id"], []),
            "changes": changes.get(row["thread_id"], []),
            "quote_code": row["quote_code"] or "",
            "code_stage": row["code_stage"] or "",
            "code_assigned_at": row["code_assigned_at"] or "",
            "contact_email": row["contact_email"] or "",
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
            for thread_id in found:  # el historial (case_events) se conserva como evidencia
                record_event(db, thread_id, "deleted", "dashboard",
                             f"{datetime.now().astimezone().isoformat(timespec='seconds')}-{uuid4().hex[:6]}",
                             "El caso se eliminó del dashboard; su historial se conserva en el archivo.")
            for table in ("dashboard_notifications", "quote_metadata", "quotes"):
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
        record_event(db, thread_id, "review_decision", "dashboard", f"{now}-{uuid4().hex[:6]}",
                     "Decidiste que la información es suficiente." if decision == "sufficient"
                     else f"Decidiste preguntar al cliente ({len(questions)} pregunta(s)).", now)
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


EDIT_REASONS = {
    "": "Sin motivo",
    "redundante": "Redundante o unir filas",
    "modelo": "El modelo se lo inventó",
    "correccion": "Corrección mía",
    "cliente": "El cliente lo pidió",
    "otro": "Otro",
}

_QUANTITY = re.compile(r"(\d{1,3}(?:\.\d{3})+(?:,\d+)?|\d+(?:[.,]\d+)?)\s*(ha|km|m2|m²|m|puntos?|und|unidades)\b",
                       re.IGNORECASE)


def activity_totals(rows: list[dict]) -> dict[str, float]:
    """Suma de cantidades por unidad (ha, km...) de una tabla, para ver si una edición cambia el alcance."""
    totals: dict[str, float] = {}
    for row in rows:
        text = f"{row.get('quantity') or ''} {row.get('unit') or ''}"
        for number, unit in _QUANTITY.findall(text):
            clean = number.replace(".", "") if re.fullmatch(r"\d{1,3}(?:\.\d{3})+(?:,\d+)?", number) else number
            value = float(clean.replace(",", "."))
            key = unit.lower().replace("²", "2")
            totals[key] = round(totals.get(key, 0.0) + value, 3)
    return totals


def diff_activities(old: list[dict], new: list[dict]) -> dict:
    """Filas agregadas, quitadas y modificadas entre dos versiones de la tabla (por nombre de actividad)."""
    def key(row: dict) -> str:
        return str(row.get("activity", "")).strip().casefold()

    before = {key(row): row for row in old if isinstance(row, dict)}
    after = {key(row): row for row in new if isinstance(row, dict)}
    changed = []
    for name, row in after.items():
        if name in before:
            fields = [f for f in ("quantity", "unit", "ubicacion", "entregable", "plazo_restriccion",
                                  "servicio_tecnica", "estado")
                      if str(before[name].get(f) or "") != str(row.get(f) or "")]
            if fields:
                changed.append({"activity": row.get("activity", ""), "fields": fields})
    return {
        "added": [after[n].get("activity", "") for n in after if n not in before],
        "removed": [before[n].get("activity", "") for n in before if n not in after],
        "changed": changed,
    }


def describe_edit(diff: dict, old_totals: dict, new_totals: dict) -> str:
    parts = []
    if diff["added"]:
        parts.append(f"+{len(diff['added'])} fila(s): " + ", ".join(str(a) for a in diff["added"][:2]))
    if diff["removed"]:
        parts.append(f"−{len(diff['removed'])} fila(s): " + ", ".join(str(a) for a in diff["removed"][:2]))
    if diff["changed"]:
        parts.append(f"{len(diff['changed'])} modificada(s): " + ", ".join(
            f"{c['activity']} ({', '.join(c['fields'])})" for c in diff["changed"][:2]))
    if not parts:
        parts.append("sin cambios de contenido")
    if old_totals != new_totals:
        moved = ", ".join(f"{unit} {old_totals.get(unit, 0)} → {new_totals.get(unit, 0)}"
                          for unit in sorted(set(old_totals) | set(new_totals))
                          if old_totals.get(unit, 0) != new_totals.get(unit, 0))
        parts.append("totales: " + moved)
    return "; ".join(parts)


def table_versions(thread_id: str) -> list[dict]:
    db = init_db()
    try:
        rows = db.execute("SELECT * FROM table_versions WHERE thread_id=? ORDER BY version",
                          (thread_id,)).fetchall()
    finally:
        db.close()
    return [dict(row) for row in rows]


def last_manual_version(thread_id: str) -> dict | None:
    """Última edición de una persona y si ya se avisó al cliente de ella."""
    db = init_db()
    try:
        row = db.execute(
            "SELECT * FROM table_versions WHERE thread_id=? AND source='dashboard' "
            "ORDER BY version DESC LIMIT 1", (thread_id,)).fetchone()
        if not row:
            return None
        notified = db.execute(
            "SELECT 1 FROM case_events WHERE thread_id=? AND kind='scope_update_sent' "
            "AND recorded_at >= ?", (thread_id, row["created_at"])).fetchone()
    finally:
        db.close()
    return {**dict(row), "notified": notified is not None}


def save_activities(thread_id: str, activities: list[dict], reason: str = "") -> str:
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
        before = analysis.get("activities") or []
        if latest_table_version(db, thread_id) is None:
            # la tabla que había antes de la primera edición (la del modelo si nadie la tocó)
            add_table_version(db, thread_id, before, "previa", "Tabla anterior a la primera edición")
        diff = diff_activities(before, activities)
        summary = describe_edit(diff, activity_totals(before), activity_totals(activities))
        reason = reason if reason in EDIT_REASONS else ""
        version = add_table_version(db, thread_id, activities, "dashboard", summary, reason)
        analysis["activities"] = activities
        analysis["manual_edit"] = {"edited_at": now, "rows": len(activities)}
        record_event(db, thread_id, "table_edit", "dashboard", f"v{version}-{uuid4().hex[:6]}",
                     f"Editaste la tabla (versión {version}): {summary}."
                     + (f" Motivo: {EDIT_REASONS[reason]}." if reason else ""), now)
        db.execute(
            "UPDATE quotes SET analysis_json=?, updated_at=? WHERE thread_id=?",
            (json.dumps(analysis, ensure_ascii=False), now, thread_id),
        )
        db.commit()
    finally:
        db.close()
    return f"Tabla guardada (versión {version}): {summary}."


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
        apply_guard(
            db, thread_id, analysis, str(payload.get("requested_by_email") or ""),
            str(payload.get("requested_by_name") or ""),
            [str(code) for code in payload.get("cited_codes") or []], message_id,
            str(payload.get("subject") or payload.get("project_name") or ""),
            str(payload.get("summary") or "")[:160], "work")
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
