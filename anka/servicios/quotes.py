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
from anka.dominio.analisis import Analisis
from anka.dominio.status import (
    REVIEW_QUEUE_STATUSES, QuoteStatus, sql_in,
)
from anka.dominio.caso import (
    Caso, CLIENT_TYPES, SECTORS, load_analysis, timestamp,
)
from anka.dominio.registros import Cambio, Evento
from anka.infra import settings
from anka.infra.logging_setup import get_logger
from anka.servicios.hoja_de_calculo import programar_actualizacion_de_hoja
from anka.servicios.repositorio import RepositorioDeCasos
from anka.infra.store import add_table_version, connect, record_event, register_change

log = get_logger(__name__)

# Fachada: las funciones de este módulo delegan en el repositorio de casos (ver repositorio.py).
repositorio = RepositorioDeCasos()


def save_review_packet(thread_id: str, recipient: str, analysis: Analisis,
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


def minutes_since(value: str) -> int:
    """Minutos transcurridos desde una fecha."""
    return max(0, int((datetime.now().astimezone().timestamp() - timestamp(value)) // 60))


def ago_text(minutes: int) -> str:
    """«hace un momento», «hace 5 min», «hace 3 h»… según los minutos."""
    if minutes < 2:
        return "hace un momento"
    if minutes < 90:
        return f"hace {minutes} min"
    if minutes < 2880:
        return f"hace {minutes // 60} h"
    return f"hace {minutes // 1440} días"


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
    return repositorio.conversacion(thread_id, limit)


def case_change_log(thread_id: str) -> list[Cambio]:
    """Todas las solicitudes de cambio del caso (cualquier estado), de la más antigua a la más nueva."""
    return repositorio.cambios(thread_id)


def chain_status(thread_id: str) -> tuple[bool, int]:
    """¿La cadena de huellas del historial del caso está íntegra? Devuelve (íntegra, cantidad de eventos)."""
    return repositorio.integridad(thread_id)


def archived_threads() -> list[dict]:
    """Casos eliminados del dashboard cuyo historial se conserva como evidencia."""
    return repositorio.archivados()


def case_has_links(thread_id: str) -> bool:
    """¿El caso avanzó más allá del primer análisis (correos vinculados, código de oferta o tabla editada)?"""
    return repositorio.tiene_vinculos(thread_id)


def case_people(thread_id: str, contact_emails: set[str]) -> list[dict]:
    """Quién ha escrito en el caso: persona, empresa, cuántos correos y el último."""
    return repositorio.personas(thread_id, contact_emails)


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
    programar_actualizacion_de_hoja(thread_id)   # la tabla pudo cambiar: la hoja de Google se actualiza sola
    return summary


def all_quotes(include_ignored: bool = False) -> list[Caso]:
    """Todos los casos con sus cambios, vínculos, seguimiento y espera, el más reciente primero."""
    return repositorio.listar(include_ignored)


def get_quote(thread_id: str) -> Caso | None:
    """Un caso por su hilo, o `None` si no existe."""
    return repositorio.obtener(thread_id)


def discarded_quotes() -> list[dict]:
    """Hilos descartados, con el motivo, para que una persona pueda revisarlos y rescatarlos."""
    return repositorio.descartados()


def rescue_discarded(thread_id: str) -> str:
    """Marca un hilo descartado para que la próxima revisión de Gmail lo analice.

    No cambia su estado: lo reprocesa el ciclo normal (`pipeline.run`), que salta el
    filtro de ruido para los hilos con `force_analysis`.
    """
    repositorio.marcar_para_reanalisis(thread_id)
    log.info("%s: rescatado del descarte; se reanalizará en la próxima revisión", thread_id)
    return "Se reanalizará en la próxima revisión de Gmail."


def delete_quotes(thread_ids: list[str]) -> str:
    """Borra del dashboard los hilos indicados: solicitud, datos comerciales, novedades y paquete de revisión.

    Solo toca la base local y `review_queue`; no modifica Gmail, Drive ni Notion.
    """
    if not thread_ids:
        return "No seleccionaste ninguna solicitud."
    found = repositorio.eliminar(thread_ids)
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
    if not repositorio.obtener(thread_id):
        raise ValueError("La solicitud ya no existe.")
    repositorio.guardar_datos_comerciales(thread_id, client_name, client_type, sector, notes)


def set_client_name(thread_id: str, client_name: str) -> None:
    """Guarda el cliente confirmado de un caso sin tocar el resto de sus datos comerciales."""
    repositorio.fijar_cliente(thread_id, client_name)


def thread_events(thread_id: str) -> list[Evento]:
    """Seguimiento del caso, del más reciente al más antiguo."""
    return repositorio.eventos(thread_id)


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
