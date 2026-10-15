"""Los casos (solicitudes) guardados en SQLite, vistos y modificados desde el dashboard.

  1. lectura: `all_quotes` / `get_quote` arman cada `Caso` con su espera, cambios y vínculos;
  2. decisiones de una persona sobre el caso: datos comerciales, vínculos, propuestas del modelo, descartar/rescatar,
     eliminar y resolver la revisión técnica; más la cola de revisión del piloto local.
La edición de la tabla de actividades vive en `ediciones.py` y la importación de borradores de Work en `work_import.py`.
"""

from __future__ import annotations
from anka.servicios.repositorio import actualizar_caso
from anka.servicios.estados import cambiar_estado

import sqlite3
from collections.abc import Mapping
from typing import Any
from uuid import uuid4
import json
from datetime import datetime
from pathlib import Path
from anka.dominio.analisis import Actividad, Analisis
from anka.dominio.novedades import REQUIEREN_ACCION
from anka.dominio.preguntas import DESCARTAR, candidatas, con_decisiones, decidir, internas, para_el_cliente
from anka.dominio.reception import reception_assessment
from anka.dominio.work_contract import archivos_sin_verificar, lectura_sin_verificar
from anka.dominio.status import (
    REVIEW_QUEUE_STATUSES, QuoteStatus, sql_in,
)
from anka.dominio.caso import (
    Caso, CLIENT_TYPES, SECTORS, combine_activities, load_analysis, timestamp,
)
from anka.dominio.registros import Cambio, Evento
from anka.infra import settings
from anka.infra.logging_setup import get_logger
from anka.servicios.hoja_de_calculo import programar_actualizacion_de_hoja
from anka.servicios.repositorio import RepositorioDeCasos
from anka.infra.store import add_table_version, begin_immediate, connect, record_event, register_change

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


_TIPOS_QUE_CUENTAN = ", ".join(f"'{tipo}'" for tipo in sorted(REQUIEREN_ACCION))   # constantes propias, no entrada


def unread_thread_count() -> int:
    """Cuántos casos tienen novedades sin leer que piden acción: lo que muestra el contador (U-25)."""
    db = connect()
    try:
        return int(db.execute(
            f"""SELECT COUNT(DISTINCT n.thread_id) FROM dashboard_notifications n
                JOIN quotes q ON q.thread_id = n.thread_id
                WHERE n.read_at IS NULL AND n.kind IN ({_TIPOS_QUE_CUENTAN})""").fetchone()[0])
    finally:
        db.close()


def unread_notifications(limit: int = 20) -> list[dict]:
    """Novedades del dashboard sin leer, las más recientes primero."""
    db = connect()
    try:
        rows = db.execute(f"""SELECT n.*, q.analysis_json
                             FROM dashboard_notifications n
                             JOIN quotes q ON q.thread_id = n.thread_id
                             WHERE n.read_at IS NULL AND n.kind IN ({_TIPOS_QUE_CUENTAN})
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
        return "Correo por revisar", "Revisar y enviar el correo"
    if status == QuoteStatus.READY_REVIEW:
        return "Información validada", "Emitir la T"
    if status == QuoteStatus.SENDING:
        return "El correo no salió", "Revisar si salió"
    return "Revisión manual", "No disponible"


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


def _close_sibling_links(db: sqlite3.Connection, thread_id: str, link: sqlite3.Row, now: str) -> int:
    """Un correo pertenece a un solo caso: al confirmarlo en uno, se cierra el mismo aviso pendiente en los demás.

    Cada correo crea un aviso por cada caso del mismo remitente; sin esto habría que descartarlo caso por caso. Queda
    anotado en el seguimiento de cada caso a dónde se asignó. Devuelve cuántos avisos cerró.
    """
    target = load_analysis(db.execute("SELECT analysis_json FROM quotes WHERE thread_id=?",
                                      (thread_id,)).fetchone()["analysis_json"]).get("project_name") or "otro caso"
    siblings = db.execute(
        "SELECT id, thread_id FROM case_links WHERE message_id=? AND thread_id<>? AND state='pending'",
        (link["message_id"], thread_id)).fetchall()
    for sibling in siblings:
        db.execute("UPDATE case_links SET state='assigned_elsewhere', decided_at=? WHERE id=?", (now, sibling["id"]))
        db.execute("UPDATE dashboard_notifications SET read_at=? WHERE thread_id=? AND kind='possible_update' "
                   "AND source_key=? AND read_at IS NULL", (now, sibling["thread_id"], link["message_id"]))
        record_event(db, sibling["thread_id"], "link_assigned", "dashboard", f"link-{sibling['id']}",
                     f"El correo «{link['subject'][:80]}» de {link['sender_name'] or link['sender_email']} se asignó "
                     f"al caso «{target}»: ya no es una posible actualización de este.", now,
                     link["sender_email"], link["sender_name"])
    return len(siblings)


SIN_AVANCE = (QuoteStatus.WAITING, QuoteStatus.REVIEW, QuoteStatus.REVIEW_PENDING, QuoteStatus.READY_REVIEW,
              QuoteStatus.DRAFT_CREATED, QuoteStatus.MISSING)


def _propose_activities(db: sqlite3.Connection, thread_id: str, analysis: Analisis, incoming: list[Actividad],
                        source_name: str, now: str) -> None:
    """Deja como propuesta pendiente la tabla de este caso con lo que trajo el correo unido (se adopta o descarta)."""
    current = analysis.get("activities") or []
    combined = combine_activities(current, incoming)
    if not incoming or combined == current:
        return
    analysis["pending_model_activities"] = combined
    actualizar_caso(db, thread_id,
        analysis_json=json.dumps(analysis, ensure_ascii=False),
        updated_at=now)
    record_event(db, thread_id, "table_proposed", "dashboard", f"merged-table-{now}",
                 f"El correo unido («{source_name}») trae cambios para la tabla: quedan como propuesta pendiente "
                 "para que la adoptes o la descartes.", now)


def _merge_own_case(db: sqlite3.Connection, thread_id: str, link: Mapping[str, Any], now: str) -> str:
    """Si el correo confirmado ya generó su propio caso (hilo nuevo), lo une a este: queda descartado como «unido».

    Solo une un caso que no avanzó (sin código de oferta, sin carpeta, sin respuesta enviada); su contenido y su
    historial se conservan y se puede rescatar desde Descartadas. Devuelve el nombre del caso unido o "".
    """
    other = link["other_thread_id"]
    row = db.execute("SELECT status, quote_code, folder_id, analysis_json FROM quotes WHERE thread_id=?",
                     (other,)).fetchone()
    sin_unir = (not other or other == thread_id or not row or row["quote_code"] or row["folder_id"]
                or row["status"] not in SIN_AVANCE)
    if sin_unir:
        return ""
    analysis = load_analysis(row["analysis_json"])
    target_row = db.execute("SELECT analysis_json FROM quotes WHERE thread_id=?", (thread_id,)).fetchone()
    target_analysis = load_analysis(target_row["analysis_json"])
    target = target_analysis.get("project_name") or "otro caso"
    name = analysis.get("project_name") or "este caso"
    _propose_activities(db, thread_id, target_analysis, analysis.get("activities") or [], name, now)
    analysis["ignored_reason"] = f"Unido al caso «{target}» (confirmaste que el correo es de ese caso)."
    analysis["merged_into"] = thread_id
    cambiar_estado(db, other, QuoteStatus.IGNORED, "unir_casos",
        analysis_json=json.dumps(analysis, ensure_ascii=False),
        updated_at=now)
    db.execute("UPDATE dashboard_notifications SET read_at=? WHERE thread_id=? AND read_at IS NULL", (now, other))
    record_event(db, other, "merged", "dashboard", f"merged-{link['id']}",
                 f"Este caso se unió a «{target}» porque confirmaste que su correo pertenece a ese caso.", now)
    return name


def merge_blocker(quote: Mapping[str, Any]) -> str:
    """Por qué un hilo no se puede unir a otro caso, o cadena vacía si se puede (solo se une lo que no avanzó)."""
    if quote["status"] == QuoteStatus.IGNORED:
        return "ya está en Descartadas"
    if quote.get("quote_code"):
        return "ya tiene código de oferta"
    if quote.get("folder_id"):
        return "ya tiene carpeta del proyecto"
    if quote["status"] not in SIN_AVANCE:
        return "ya tiene una respuesta enviada o está en cotización"
    return ""


def _pair(thread_id: str, other_thread_id: str) -> tuple[str, str]:
    return (thread_id, other_thread_id) if thread_id < other_thread_id else (other_thread_id, thread_id)


def separated_project_pairs() -> frozenset[frozenset[str]]:
    """Los pares de hilos que una persona dijo que no son el mismo proyecto."""
    db = connect()
    try:
        return frozenset(frozenset((row["thread_id"], row["other_thread_id"]))
                         for row in db.execute("SELECT thread_id, other_thread_id FROM project_dismissals"))
    finally:
        db.close()


def merge_project_thread(thread_id: str, other_thread_id: str) -> str:
    """Una persona confirma que otro hilo es el mismo proyecto: se une a este caso y queda en Descartadas."""
    if not other_thread_id or other_thread_id == thread_id:
        raise ValueError("Elige otra conversación para unir.")
    db = connect()
    try:
        target = db.execute("SELECT 1 FROM quotes WHERE thread_id=?", (thread_id,)).fetchone()
        other = repositorio.obtener(other_thread_id)
        if not target or not other:
            raise ValueError("Una de las conversaciones ya no existe.")
        blocker = merge_blocker(other)
        if blocker:
            raise ValueError(f"No se puede unir esa conversación: {blocker}. "
                             "Si no es el mismo proyecto, usa «No es el mismo "
                             "proyecto».")
        now = datetime.now().astimezone().isoformat(timespec="seconds")
        link = {"other_thread_id": other_thread_id, "id": f"project-{other_thread_id}"}
        name = _merge_own_case(db, thread_id, link, now)
        if not name:
            raise ValueError("Esa conversación ya avanzó, por eso no se puede unir.")
        record_event(db, thread_id, "project_merged", "dashboard", f"project-{other_thread_id}",
                     f"Confirmaste que la conversación «{name}» es el mismo proyecto: se unió a este caso y quedó en "
                     "Descartadas (se puede rescatar).", now)
        db.commit()
    finally:
        db.close()
    return (f"Unido: «{name}» pasó a Descartadas, por si quieres rescatarlo. Si traía cambios para la tabla, los verás "
            "como propuesta para adoptar o descartar.")


def separate_project_thread(thread_id: str, other_thread_id: str) -> str:
    """Una persona dice que otro hilo NO es el mismo proyecto: no se vuelve a sugerir en ningún caso."""
    if not other_thread_id or other_thread_id == thread_id:
        raise ValueError("Elige otra conversación.")
    db = connect()
    try:
        first, second = _pair(thread_id, other_thread_id)
        now = datetime.now().astimezone().isoformat(timespec="seconds")
        db.execute("INSERT OR IGNORE INTO project_dismissals (thread_id, other_thread_id, decided_at) VALUES (?, ?, ?)",
                   (first, second, now))
        for mine, theirs in ((thread_id, other_thread_id), (other_thread_id, thread_id)):
            if db.execute("SELECT 1 FROM quotes WHERE thread_id=?", (mine,)).fetchone():
                record_event(db, mine, "project_separated", "dashboard", f"project-{theirs}",
                             "Decidiste que otra conversación con nombre o archivo parecido no es el mismo "
                             "proyecto que este.",
                             now)
        db.commit()
    finally:
        db.close()
    return "Listo: no volveremos a sugerir que esas conversaciones son el mismo proyecto."


def resolve_link(thread_id: str, link_id: int, accept: bool) -> str:
    """Una persona confirma o descarta que un correo de otro hilo pertenece a este caso."""
    db = connect()
    try:
        link = db.execute(
            "SELECT * FROM case_links WHERE id=? AND thread_id=? AND state='pending'",
            (link_id, thread_id)).fetchone()
        if not link:
            return "Ese aviso ya estaba resuelto."
        now = datetime.now().astimezone().isoformat(timespec="seconds")
        who = link["sender_name"] or link["sender_email"]
        db.execute("UPDATE case_links SET state=?, decided_at=? WHERE id=?",
                   ("linked" if accept else "dismissed", now, link_id))
        closed = _close_sibling_links(db, thread_id, link, now) if accept else 0
        merged = _merge_own_case(db, thread_id, link, now) if accept else ""
        if accept:
            summary = (f"Confirmaste que el correo «{link['subject'][:80]}» de {who} "
                       f"({reason_label(link['reason'])}) pertenece a este caso: «{link['snippet']}»")
            message = ("Anotado en el seguimiento con su autor y fecha. El agente todavía no lee "
                       "otras conversaciones: actualiza la tabla a mano si hace falta.")
            if closed:
                message += (f" Este mismo correo también aparecía como posible actualización en {closed} caso(s) más: "
                            "ya no se muestra allí.")
            if merged:
                message += (f" Además, el caso «{merged}», que había nacido de esa misma conversación, se unió a este "
                            "(quedó en Descartadas, por si quieres rescatarlo). Si traía cambios para la tabla, "
                            "los verás como propuesta para adoptar o descartar.")
        else:
            summary = f"Descartaste que el correo «{link['subject'][:80]}» de {who} sea de este caso."
            message = "Descartado. No volverá a aparecer."
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


def dismiss_pending_links(thread_id: str, link_ids: list[int]) -> str:
    """Descarta en lote sugerencias de vínculo seleccionadas, sin unir ni borrar casos."""
    ids = sorted({int(value) for value in link_ids if int(value) > 0})
    if not ids:
        return "Selecciona al menos una posible actualización."
    db = connect()
    try:
        now = datetime.now().astimezone().isoformat(timespec="seconds")
        placeholders = ",".join("?" for _ in ids)
        rows = db.execute(
            f"SELECT id, message_id, sender_email, sender_name, subject, snippet FROM case_links "
            f"WHERE thread_id=? AND state='pending' AND id IN ({placeholders})",
            (thread_id, *ids),
        ).fetchall()
        if not rows:
            return "Las posibles actualizaciones seleccionadas ya estaban resueltas."
        for row in rows:
            db.execute("UPDATE case_links SET state='dismissed', decided_at=? WHERE id=?",
                       (now, row["id"]))
            summary = (f"Descartaste en lote que el correo «{row['subject'][:80]}» de "
                       f"{row['sender_name'] or row['sender_email']} sea de este caso.")
            record_event(db, thread_id, "dismissed", "dashboard", f"link-{row['id']}", summary,
                         now, row["sender_email"], row["sender_name"])
        db.commit()
        return f"Descartaste {len(rows)} aviso(s). Los correos y los casos siguen guardados."
    finally:
        db.close()


def dismiss_pending_links_many(references: list[tuple[str, int]]) -> str:
    """Descarta en lote sugerencias de varios casos sin unir ni borrar sus hilos."""
    selected = sorted({(str(thread).strip(), int(link_id)) for thread, link_id in references
                       if str(thread).strip() and int(link_id) > 0})
    if not selected:
        return "Selecciona al menos una posible actualización."
    db = connect()
    try:
        now = datetime.now().astimezone().isoformat(timespec="seconds")
        dismissed = 0
        for thread_id, link_id in selected:
            row = db.execute(
                "SELECT sender_email, sender_name, subject FROM case_links "
                "WHERE id=? AND thread_id=? AND state='pending'", (link_id, thread_id)).fetchone()
            if not row:
                continue
            db.execute("UPDATE case_links SET state='dismissed', decided_at=? WHERE id=? AND thread_id=?",
                       (now, link_id, thread_id))
            record_event(db, thread_id, "dismissed", "dashboard", f"link-{link_id}",
                         f"Descartaste en lote que el correo «{row['subject'][:80]}» de "
                         f"{row['sender_name'] or row['sender_email']} sea de este caso.",
                         now, row["sender_email"], row["sender_name"])
            dismissed += 1
        db.commit()
        if not dismissed:
            return "Las posibles actualizaciones seleccionadas ya estaban resueltas."
        return f"Descartaste {dismissed} aviso(s). Los correos y los casos siguen guardados."
    finally:
        db.close()


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
        begin_immediate(db)
        row = db.execute("SELECT analysis_json FROM quotes WHERE thread_id=?", (thread_id,)).fetchone()
        if not row:
            raise ValueError("Este caso ya no existe. Vuelve a Mis casos.")
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
        actualizar_caso(db, thread_id,
            analysis_json=json.dumps(analysis, ensure_ascii=False),
            updated_at=now)
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
    return "Se volverá a mirar en la próxima búsqueda de correos."


def delete_quotes(thread_ids: list[str]) -> str:
    """Borra del dashboard los hilos indicados: solicitud, datos comerciales, novedades y paquete de revisión.

    Solo toca la base local y `review_queue`; no modifica Gmail, Drive ni Notion.
    """
    if not thread_ids:
        return "No marcaste ningún caso. Marca uno o más y vuelve a pulsar."
    found = repositorio.eliminar(thread_ids)
    for thread_id in found:
        review_packet_path(thread_id).unlink(missing_ok=True)
    log.info("Eliminadas %d solicitud(es) del dashboard: %s", len(found), ", ".join(found))
    if not found:
        return "Esos casos ya no estaban."
    return f"Se borraron {len(found)} caso(s)."


def volver_a_decidir(thread_id: str, accion: str) -> str:
    """Cambiar de idea antes de enviar las preguntas (U-27): volver a decidirlas o desistir de preguntar.

    Solo mientras el correo no ha salido. `reconsiderar` devuelve el caso a la decisión técnica con las elecciones
    conservadas; `suficiente` da la información por suficiente, como en la decisión técnica. No envía nada ni borra el
    borrador que ya esté en Gmail.
    """
    if accion not in ("reconsiderar", "suficiente"):
        raise ValueError("No entendimos esa opción. Recarga la página e inténtalo otra vez.")
    db = connect()
    try:
        begin_immediate(db)
        row = db.execute(
            "SELECT status, analysis_json, sent_at, draft_id FROM quotes WHERE thread_id=?", (thread_id,)).fetchone()
        if not row:
            raise ValueError("Este caso ya no existe. Vuelve a Mis casos.")
        analysis = load_analysis(row["analysis_json"])
        decision = analysis.get("manual_review") or {}
        enviado = bool(row["sent_at"]) and str(row["sent_at"]) >= str(decision.get("at") or "")
        if (row["status"] not in (QuoteStatus.REVIEW_PENDING, QuoteStatus.DRAFT_CREATED)
                or decision.get("decision") != "ask_client" or enviado):
            raise ValueError("Ya no puedes cambiar la decisión: el correo con las preguntas ya salió.")
        now = datetime.now().astimezone().isoformat(timespec="seconds")
        if accion == "reconsiderar":
            analysis.pop("manual_review", None)         # las elecciones (`preguntas`) se conservan
            nuevo = QuoteStatus.REVIEW
            texto = "Volviste a la decisión técnica antes de enviar el correo: puedes cambiar qué preguntas hacer."
            mensaje = "Volviste a decidir. Tus preguntas siguen ahí: cámbialas y prepara el correo."
        else:
            analysis["manual_review"] = {"decision": "sufficient", "at": now, "questions": []}
            nuevo = QuoteStatus.READY_REVIEW
            texto = "Decidiste no preguntar al cliente antes de enviar el correo: con esto alcanza."
            mensaje = "Listo: con esto alcanza. Ya puedes emitir la T y crear la carpeta en Drive."
        if row["draft_id"]:
            mensaje += " El borrador que guardaste en Gmail sigue ahí; no se borra."
        cambiar_estado(db, thread_id, nuevo, "volver_a_decidir", updated_at=now,
                       analysis_json=json.dumps(analysis, ensure_ascii=False))
        record_event(db, thread_id, "review_decision", "dashboard", f"{now}-{uuid4().hex[:6]}", texto, now)
        db.commit()
    finally:
        db.close()
    log.info("%s: volvió a la decisión técnica antes de enviar (%s)", thread_id, accion)
    return mensaje


def aceptar_lectura_revisada(thread_id: str) -> str:
    """Una persona asume la revisión de los archivos que Anka no pudo verificar y el caso sigue su curso (U-29).

    No cambia el estado ni envía nada: levanta el bloqueo de la pantalla y deja constancia en el historial de quién lo
    hizo, cuándo y sobre qué archivos. Un análisis nuevo de Work reemplaza esta marca y se verifica de nuevo.
    """
    db = connect()
    try:
        begin_immediate(db)
        row = db.execute("SELECT analysis_json FROM quotes WHERE thread_id=?", (thread_id,)).fetchone()
        if not row:
            raise ValueError("Este caso ya no existe. Vuelve a Mis casos.")
        analysis = load_analysis(row["analysis_json"])
        if not lectura_sin_verificar(analysis) or analysis.get("reading_review"):
            raise ValueError("La lectura de este caso no está pendiente de verificar.")
        now = datetime.now().astimezone().isoformat(timespec="seconds")
        archivos = archivos_sin_verificar(analysis)
        analysis["reading_review"] = {"at": now, "files": archivos}
        actualizar_caso(db, thread_id, updated_at=now, analysis_json=json.dumps(analysis, ensure_ascii=False))
        cuales = f": {', '.join(archivos)}" if archivos else ""
        record_event(db, thread_id, "reading_reviewed", "dashboard", f"{now}-{uuid4().hex[:6]}",
                     f"Una persona asumió la revisión de los archivos que Anka no pudo verificar{cuales}.", now)
        db.commit()
    finally:
        db.close()
    log.info("%s: una persona asumió la revisión de la lectura sin verificar", thread_id)
    return "Listo: la lectura quedó como revisada por ti. Ya puedes ver los resultados de Work y decidir cómo seguir."


def resolve_technical_review(
    thread_id: str, decision: str, questions_text: str = "", *,
    elecciones: Mapping[str, tuple[str, str]] | None = None, nuevas: str = "",
) -> str:
    """Una persona cierra la revisión técnica: información suficiente, preguntar al cliente o guardar sus decisiones.

    Guarda la decisión en `analysis["manual_review"]` (quita el bloqueo técnico de la evaluación) y mueve el estado. No
    envía nada. Con `elecciones` (id de pregunta → decisión y texto) cada pregunta se decide por separado (U-22): las
    que se le harán al cliente van al correo, las internas quedan en los pendientes de Anka y las descartadas
    desaparecen; `decision="internal_only"` guarda esas decisiones sin pasar el caso a preguntas. Sin `elecciones`,
    `questions_text` (una pregunta por línea) sigue funcionando como antes. Si llega un mensaje nuevo en el hilo, el
    reanálisis reemplaza el análisis y las decisiones se descartan: lo nuevo se revisa otra vez.
    """
    if decision not in ("sufficient", "ask_client", "internal_only"):
        raise ValueError("No entendimos esa opción. Recarga la página e inténtalo otra vez.")
    if decision == "internal_only" and elecciones is None:
        raise ValueError("Falta elegir qué hacer con alguna pregunta.")
    db = connect()
    try:
        begin_immediate(db)
        row = db.execute(
            "SELECT status, analysis_json FROM quotes WHERE thread_id=?", (thread_id,)
        ).fetchone()
        if not row:
            raise ValueError("Este caso ya no existe. Vuelve a Mis casos.")
        if row["status"] != QuoteStatus.REVIEW:
            raise ValueError("Este caso ya no espera una decisión tuya.")
        now = datetime.now().astimezone().isoformat(timespec="seconds")
        analysis = load_analysis(row["analysis_json"])
        if decision == "sufficient" and not analysis.get("activities"):
            raise ValueError("Todavía no hay actividades en la tabla. Agrega al menos una antes de confirmar que "
                             "alcanza.")
        por_dentro = descartadas = 0
        if elecciones is not None:
            actuales = con_decisiones(candidatas(analysis, reception_assessment(analysis)), analysis.get("preguntas"))
            decididas = decidir(actuales, elecciones, nuevas.splitlines(), "dashboard", now)
            analysis["preguntas"] = decididas
            questions = para_el_cliente(decididas)
            por_dentro = len(internas(decididas))
            descartadas = sum(1 for p in decididas if p.get("decision") == DESCARTAR)
        else:
            questions = [line.strip()[:500] for line in questions_text.splitlines() if line.strip()][:10]
        if decision == "ask_client" and not questions:
            raise ValueError("Escribe al menos una pregunta para el cliente o elige «Con esto alcanza».")
        if decision == "ask_client" and elecciones is None:
            # el cuadro de la pantalla las vuelve a traer si la persona reconsidera (U-31)
            analysis["ultimas_preguntas"] = questions
        if decision == "internal_only":
            new_status = QuoteStatus.REVIEW  # la revisión técnica sigue abierta: solo se guardaron decisiones
            texto = (f"Decidiste cómo tratar las preguntas: {por_dentro} por resolver dentro de Anka, "
                     f"{descartadas} descartada(s).")
        else:
            analysis["manual_review"] = {
                "decision": decision, "at": now, "questions": questions if decision == "ask_client" else [],
            }
            new_status = QuoteStatus.READY_REVIEW if decision == "sufficient" else QuoteStatus.REVIEW_PENDING
            aparte = (f"; {por_dentro} por resolver dentro de Anka" if por_dentro else "") + (
                f"; {descartadas} descartada(s)" if descartadas else "")
            texto = ("Decidiste que con esto alcanza." if decision == "sufficient"
                     else f"Decidiste preguntar al cliente ({len(questions)} pregunta(s){aparte}).")
        cambiar_estado(db, thread_id, new_status, "decidir_revision_tecnica",
            analysis_json=json.dumps(analysis, ensure_ascii=False),
            updated_at=now)
        record_event(db, thread_id, "review_decision", "dashboard", f"{now}-{uuid4().hex[:6]}", texto, now)
        db.commit()
    finally:
        db.close()
    log.info("%s: revisión técnica resuelta por una persona (%s)", thread_id, decision)
    if decision == "sufficient":
        return "Listo: con esto alcanza. Ya puedes crear la carpeta del proyecto en Drive."
    if decision == "internal_only":
        return "Guardado. Las preguntas internas las verá el equipo de Anka."
    return "Listo. Lee el correo en «Respuesta al cliente» y envíalo."


def save_quote_metadata(thread_id: str, client_name: str, client_type: str,
                        sector: str, notes: str) -> None:
    """Guarda cliente, tipo, sector y notas de un caso; rechaza valores fuera de las listas."""
    if client_type not in CLIENT_TYPES:
        raise ValueError("Tipo de cliente no válido.")
    if sector not in SECTORS:
        raise ValueError("Sector no válido.")
    if not repositorio.obtener(thread_id):
        raise ValueError("Este caso ya no existe. Vuelve a Mis casos.")
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
            "(SELECT COUNT(*) FROM dashboard_notifications WHERE read_at IS NULL "
            f"AND kind IN ({_TIPOS_QUE_CUENTAN})) AS unread, "
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
