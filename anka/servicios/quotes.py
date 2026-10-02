"""Los casos (solicitudes) guardados en SQLite, vistos y modificados desde el dashboard.

  1. lectura: `all_quotes` / `get_quote` arman cada `Caso` con su espera, cambios y vínculos;
  2. decisiones de una persona sobre el caso: datos comerciales, vínculos, propuestas del modelo, descartar/rescatar,
     eliminar y resolver la revisión técnica; más la cola de revisión del piloto local.
La edición de la tabla de actividades vive en `ediciones.py` y la importación de borradores de Work en `work_import.py`.
"""

from __future__ import annotations

from uuid import uuid4
import json
from datetime import datetime
from pathlib import Path
from anka.dominio.status import (
    REVIEW_QUEUE_STATUSES, QuoteStatus, sql_in,
)
from anka.dominio.caso import Caso
from anka.dominio.registros import Cambio, Evento, Vinculo
from anka.infra import settings
from anka.dominio.reception import (
    reception_assessment,
)
from anka.infra.logging_setup import get_logger
from anka.infra.store import add_table_version, case_events, connect, record_event, register_change, verify_chain

log = get_logger(__name__)



def save_review_packet(thread_id: str, recipient: str, analysis: dict,
                       questions: list[str], body: str | None,
                       folder_name: str | None = None) -> Path:
    """Guarda en `review_queue` el paquete de texto para revisar un caso y devuelve su ruta."""
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




def unread_notifications(limit: int = 20) -> list[dict]:
    """Novedades del dashboard sin leer, las más recientes primero."""
    db = connect()
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
        analysis = load_analysis(row["analysis_json"])
        result.append({
            "id": row["id"], "thread_id": row["thread_id"],
            "kind": row["kind"], "title": row["title"],
            "message": row["message"], "created_at": row["created_at"],
            "project_name": analysis.get("project_name") or "Solicitud sin nombre",
        })
    return result


def mark_notifications_read(thread_id: str | None = None) -> None:
    """Marca como leídas las novedades de un caso o todas."""
    now = datetime.now().astimezone().isoformat(timespec="seconds")
    db = connect()
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
    """Ruta del paquete de revisión de un caso."""
    return settings.DATA_DIR / "review_queue" / f"{thread_id}.txt"


def review_action(status: str) -> tuple[str, str]:
    """Etiqueta de estado y acción sugerida para la cola de revisión según el estado."""
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


def load_analysis(value: str) -> dict:
    """El análisis guardado de un caso como diccionario ({} si falta o está dañado)."""
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
    """Minutos transcurridos desde una fecha."""
    return max(0, int((datetime.now().astimezone().timestamp() - _timestamp(value)) // 60))


def ago_text(minutes: int) -> str:
    """«hace un momento», «hace 5 min», «hace 3 h»… según los minutos."""
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
    """Frase legible del motivo por el que un correo parece relacionado con un caso."""
    base, _, hint = reason.partition("+")
    text = REASON_LABELS.get(base, "posible relación")
    return text + (" · el asunto menciona el proyecto" if hint else "")


def resolve_link(thread_id: str, link_id: int, accept: bool) -> str:
    """Una persona confirma o descarta que un correo de otro hilo pertenece a este caso."""
    db = connect()
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


def case_conversation(thread_id: str, limit: int = 12) -> list[dict]:
    """Correos del hilo, el más reciente primero."""
    db = connect()
    try:
        rows = db.execute(
            "SELECT direction, sender, subject, body, occurred_at FROM case_messages WHERE thread_id=? "
            "ORDER BY occurred_at DESC, id DESC LIMIT ?", (thread_id, limit)).fetchall()
    finally:
        db.close()
    return [dict(row) for row in rows]


def case_change_log(thread_id: str) -> list[Cambio]:
    """Todas las solicitudes de cambio del caso (cualquier estado), de la más antigua a la más nueva."""
    db = connect()
    try:
        return [Cambio.from_row(row) for row in db.execute(
            "SELECT * FROM case_changes WHERE thread_id=? ORDER BY occurred_at", (thread_id,))]
    finally:
        db.close()


def chain_status(thread_id: str) -> tuple[bool, int]:
    """¿La cadena de huellas del historial del caso está íntegra? Devuelve (íntegra, cantidad de eventos)."""
    db = connect()
    try:
        return verify_chain(db, thread_id)
    finally:
        db.close()


def archived_threads() -> list[dict]:
    """Casos eliminados del dashboard cuyo historial se conserva como evidencia."""
    db = connect()
    try:
        rows = db.execute(
            """SELECT thread_id, COUNT(*) AS n, MAX(occurred_at) AS last, MIN(occurred_at) AS first
               FROM case_events WHERE thread_id NOT IN (SELECT thread_id FROM quotes)
               GROUP BY thread_id ORDER BY last DESC""").fetchall()
    finally:
        db.close()
    return [dict(row) for row in rows]


def case_has_links(thread_id: str) -> bool:
    """¿El caso ya avanzó más allá del primer análisis: correos vinculados de otros hilos, código de oferta o tabla editada en el dashboard?"""
    db = connect()
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
    db = connect()
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
    db = connect()
    try:
        row = db.execute("SELECT analysis_json FROM quotes WHERE thread_id=?", (thread_id,)).fetchone()
        if not row:
            raise ValueError("La solicitud ya no existe.")
        analysis = load_analysis(row["analysis_json"])
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


def _waiting_since(status: str, sent_at: str, latest: tuple[str, str] | None) -> str:
    """Desde cuándo espera el caso la respuesta del cliente ('' si no está esperando)."""
    if status not in (QuoteStatus.WAITING, QuoteStatus.RESPONSE_SENT) or not sent_at:
        return ""
    if latest and _timestamp(latest[0]) >= _timestamp(sent_at):
        return ""            # el cliente ya respondió después
    return sent_at


def all_quotes(include_ignored: bool = False, thread_id: str | None = None) -> list[Caso]:
    """Todos los casos con sus cambios, vínculos, seguimiento y espera, el más reciente primero.

    Con `thread_id` solo se arma ese caso (y solo se consultan sus filas relacionadas).
    """
    db = connect()
    try:
        conditions = [] if include_ignored else [f"q.status <> '{QuoteStatus.IGNORED}'"]
        scope = (thread_id,) if thread_id is not None else ()
        if thread_id is not None:
            conditions.append("q.thread_id = ?")
        where = ("WHERE " + " AND ".join(conditions)) if conditions else ""
        only = " AND thread_id = ?" if thread_id is not None else ""
        only_where = " WHERE thread_id = ?" if thread_id is not None else ""
        rows = db.execute(f"""SELECT q.*, m.client_type, m.sector,
                                      m.client_name, m.notes
                               FROM quotes q
                               LEFT JOIN quote_metadata m ON m.thread_id = q.thread_id
                               {where}
                               ORDER BY COALESCE(q.updated_at, q.first_seen_at, '') DESC,
                                        q.thread_id DESC""", scope).fetchall()
        latest_reply = {r["thread_id"]: (r["occurred_at"], r["ref"]) for r in db.execute(
            "SELECT thread_id, ref, occurred_at FROM case_events "
            f"WHERE kind='received'{only} ORDER BY occurred_at, id", scope)}
        followups = {r["thread_id"]: dict(r) for r in db.execute(f"SELECT * FROM case_followup{only_where}", scope)}
        reminders: dict[str, list[str]] = {}
        for r in db.execute(
                f"SELECT thread_id, ref FROM case_events WHERE kind='reminder_sent'{only} ORDER BY id", scope):
            reminders.setdefault(r["thread_id"], []).append(r["ref"])
        changes: dict[str, list[Cambio]] = {}
        for change in db.execute(
                f"SELECT * FROM case_changes WHERE state IN ('pending', 'change'){only} ORDER BY occurred_at",
                scope):
            changes.setdefault(change["thread_id"], []).append(Cambio.from_row(change))
        pending_links: dict[str, list[Vinculo]] = {}
        for link in db.execute(
                f"SELECT * FROM case_links WHERE state='pending'{only} ORDER BY occurred_at", scope):
            pending_links.setdefault(link["thread_id"], []).append(Vinculo.from_row(link))
    finally:
        db.close()
    quotes: list[Caso] = []
    for row in rows:
        analysis = load_analysis(row["analysis_json"])
        routing = analysis.get("routing", {})
        client_email = routing.get("original_sender_email") or row["recipient"]
        assessment = reception_assessment(analysis) if analysis else {}
        status_label = STATUS_LABELS.get(row["status"], row["status"])
        review_states = {QuoteStatus.REVIEW_PENDING, QuoteStatus.READY_REVIEW, QuoteStatus.REVIEW}
        if row["status"] in review_states and assessment:
            status_label = assessment["label"]
        quotes.append(Caso(
            thread_id=row["thread_id"],
            status=row["status"],
            status_label=status_label,
            recipient=row["recipient"],
            client_email=client_email,
            client_name=row["client_name"] or routing.get("original_sender") or client_email,
            client_name_saved=row["client_name"] or "",
            client_type=row["client_type"] or "No clasificado",
            sector=row["sector"] or "No clasificado",
            notes=row["notes"] or "",
            analysis=analysis,
            reception_assessment=assessment,
            services=_service_names(analysis),
            first_seen_at=row["first_seen_at"] or analysis.get("received_at") or "",
            updated_at=row["updated_at"] or "",
            folder_id=row["folder_id"],
            draft_id=row["draft_id"],
            draft_body=row["draft_body"] or "",
            sent_at=row["sent_at"] or "",
            reply_pending=_unanalyzed_reply(
                latest_reply.get(row["thread_id"]), row["last_inbound_id"], analysis,
                row["first_seen_at"] or "", row["status"]),
            proposal=model_proposal(analysis),
            links=pending_links.get(row["thread_id"], []),
            changes=changes.get(row["thread_id"], []),
            waiting_since=_waiting_since(row["status"], row["sent_at"] or "", latest_reply.get(row["thread_id"])),
            followup={
                "auto_enabled": bool(followups.get(row["thread_id"], {}).get("auto_enabled")),
                "paused": bool(followups.get(row["thread_id"], {}).get("paused")),
                "contacted_at": followups.get(row["thread_id"], {}).get("contacted_at") or "",
            },
            reminders=[ref for ref in reminders.get(row["thread_id"], [])
                          if ref.startswith((row["sent_at"] or "") + "|")],
            quote_code=row["quote_code"] or "",
            code_stage=row["code_stage"] or "",
            code_assigned_at=row["code_assigned_at"] or "",
            contact_email=row["contact_email"] or "",
        ))
    return quotes


def get_quote(thread_id: str) -> Caso | None:
    """Un caso por su hilo, o `None` si no existe."""
    return next(iter(all_quotes(True, thread_id)), None)


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
    db = connect()
    try:
        row = db.execute(
            "SELECT status, analysis_json FROM quotes WHERE thread_id=?", (thread_id,)
        ).fetchone()
        if not row:
            raise ValueError("La solicitud no existe.")
        if row["status"] != QuoteStatus.IGNORED:
            raise ValueError("Solo se pueden rescatar los correos descartados.")
        analysis = load_analysis(row["analysis_json"])
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
    db = connect()
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
    db = connect()
    try:
        row = db.execute(
            "SELECT status, analysis_json FROM quotes WHERE thread_id=?", (thread_id,)
        ).fetchone()
        if not row:
            raise ValueError("La solicitud ya no existe.")
        if row["status"] != QuoteStatus.REVIEW:
            raise ValueError("Solo se puede decidir sobre casos en revisión técnica interna.")
        now = datetime.now().astimezone().isoformat(timespec="seconds")
        analysis = load_analysis(row["analysis_json"])
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
    """Guarda cliente, tipo, sector y notas de un caso; rechaza valores fuera de las listas."""
    if client_type not in CLIENT_TYPES:
        raise ValueError("Tipo de cliente no válido.")
    if sector not in SECTORS:
        raise ValueError("Sector no válido.")
    if not get_quote(thread_id):
        raise ValueError("La solicitud ya no existe.")
    now = datetime.now().astimezone().isoformat(timespec="seconds")
    db = connect()
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
    db = connect()
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


def thread_events(thread_id: str) -> list[Evento]:
    """Seguimiento del caso, del más reciente al más antiguo."""
    db = connect()
    try:
        rows = case_events(db, thread_id)
    finally:
        db.close()
    return [Evento.from_row(row) for row in reversed(rows)]


def change_marker() -> str:
    """Cambia cuando se añade, edita o borra cualquier caso (para refrescar el dashboard)."""
    db = connect()
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
    """Casos que esperan revisión humana, con su paquete de texto."""
    db = connect()
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
    """Una revisión pendiente por hilo, o `None`."""
    for review in pending_reviews():
        if review["thread_id"] == thread_id:
            return review
    return None


def list_reviews() -> None:
    """Imprime las revisiones pendientes (comando `reviews`)."""
    reviews = pending_reviews()
    if not reviews:
        print("No hay revisiones humanas pendientes.")
        return
    for review in reviews:
        packet = review["packet"]
        suffix = f" -> {packet}" if packet.exists() else " -> sin archivo en review_queue"
        print(f"{review['thread_id']} [{review['status']}] {review['recipient']}{suffix}")
