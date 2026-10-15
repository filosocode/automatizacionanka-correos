"""Vínculos entre conversaciones: correos que parecen de otro caso y proyectos repetidos que se unen o se separan.

Una persona decide siempre: confirma o descarta que un correo de otro hilo pertenece al caso, une dos conversaciones del
mismo proyecto o dice que no lo son. Cada decisión deja su huella en el historial del caso.
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Mapping
from datetime import datetime
from typing import Any

from anka.dominio.analisis import Actividad, Analisis
from anka.dominio.caso import combine_activities, load_analysis
from anka.dominio.errores import ErrorDeValidacion
from anka.dominio.status import QuoteStatus
from anka.infra.store import connect, record_event, register_change
from anka.servicios.estados import cambiar_estado
from anka.servicios.repositorio import RepositorioDeCasos, actualizar_caso

repositorio = RepositorioDeCasos()


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
        raise ErrorDeValidacion("Elige otra conversación para unir.")
    db = connect()
    try:
        target = db.execute("SELECT 1 FROM quotes WHERE thread_id=?", (thread_id,)).fetchone()
        other = repositorio.obtener(other_thread_id)
        if not target or not other:
            raise ErrorDeValidacion("Una de las conversaciones ya no existe.")
        blocker = merge_blocker(other)
        if blocker:
            raise ErrorDeValidacion(f"No se puede unir esa conversación: {blocker}. "
                             "Si no es el mismo proyecto, usa «No es el mismo "
                             "proyecto».")
        now = datetime.now().astimezone().isoformat(timespec="seconds")
        link = {"other_thread_id": other_thread_id, "id": f"project-{other_thread_id}"}
        name = _merge_own_case(db, thread_id, link, now)
        if not name:
            raise ErrorDeValidacion("Esa conversación ya avanzó, por eso no se puede unir.")
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
        raise ErrorDeValidacion("Elige otra conversación.")
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
