"""Código de la oferta (T / T&E) y control de cambios: quién pide qué, con qué código y a quién se avisa.

Antes de la T, el cliente escribe sin código (preguntas, dudas). Cuando se acepta el proyecto se
asigna la T; desde ahí todo cambio debe citarla. Un mensaje sin T no avanza, y si lo envía una
persona distinta del contacto registrado se avisa también al contacto. Todo queda en el
seguimiento del caso. Los correos que resultan son siempre borradores que revisa una persona.
"""

from __future__ import annotations

import json
import re
import sqlite3
from datetime import datetime
from uuid import uuid4

from anka.dominio.analisis import Analisis
from anka.infra.logging_setup import get_logger
from anka.infra.store import connect, record_event, register_change

log = get_logger(__name__)


# T26145, T-26145, TE26145, T&E26145, "T&E 26145": año de 2 dígitos + consecutivo de 3.
CODE_PATTERN = re.compile(
    r"(?<![A-Za-z0-9])T\s?(?:&\s?E|E)?[\s\-_]?(\d{2})[\s\-_]?(\d{3})(?!\d)", re.IGNORECASE)
NUMBER_PATTERN = re.compile(r"^\d{5}$")

STAGE_LABELS = {"T": "En cotización", "TE": "Aprobada"}

# R11 (reglas_operativas.md): lo que Python aplica y test_paridad_modos compara con las reglas.
CODE_REQUIRED_AFTER_ASSIGNMENT = True
TABLE_UPDATE_ONLY_BY_CONTACT_WITH_CODE = True
MAIL_TO_NON_CONTACT_REVEALS_CODE = False

# Dominios de correo personal: coincidir en uno de ellos no dice nada de la empresa.
CONSUMER_DOMAINS = frozenset({
    "gmail.com", "googlemail.com", "hotmail.com", "hotmail.es", "outlook.com", "outlook.es",
    "live.com", "msn.com", "yahoo.com", "yahoo.es", "icloud.com", "me.com", "aol.com",
    "proton.me", "protonmail.com", "gmx.com",
})


def domain_of(address: str) -> str:
    """Dominio de una dirección de correo (después de la @), en minúsculas."""
    return address.rsplit("@", 1)[-1].strip().casefold() if "@" in address else ""


def company_domain(address: str) -> str:
    """Dominio de empresa de un correo ('' si es un correo personal como gmail.com)."""
    domain = domain_of(address)
    return "" if domain in CONSUMER_DOMAINS else domain


def _timestamp(value: str) -> float:
    try:
        return datetime.fromisoformat(value).timestamp()
    except (TypeError, ValueError):
        return 0.0


def cited_numbers(cited_codes: list[str]) -> set[str]:
    """Normaliza lo que informó quien analizó el correo ('T&E26145', '26145'...) a números de oferta."""
    numbers: set[str] = set()
    for item in cited_codes or []:
        text = str(item)
        numbers |= find_codes(text)
        if NUMBER_PATTERN.match(text.strip()):
            numbers.add(text.strip())
    return numbers


def table_update_allowed(quote_code: str, contact_email: str, requested_by_email: str,
                         cited_codes: list[str], after_assignment: bool = True) -> bool:
    """R11: ¿puede este mensaje modificar la tabla de actividades?

    Sin código en el caso, o si el mensaje es anterior a la T, sí. Con código solo si lo envía el
    contacto registrado y cita el código del caso.
    """
    if not quote_code or not after_assignment:
        return True
    sender = (requested_by_email or "").strip().casefold()
    is_contact = bool(sender) and sender == (contact_email or "").strip().casefold()
    return is_contact and quote_code in cited_numbers(cited_codes)


def find_codes(text: str) -> set[str]:
    """Números de oferta (año + consecutivo, p. ej. '26145') que aparecen en un texto."""
    return {year + sequence for year, sequence in CODE_PATTERN.findall(text or "")}


def code_label(number: str, stage: str) -> str:
    """Código tal como se muestra: «T26145» o, si ya fue aprobada, «T&E26145»."""
    return f"T&E{number}" if stage == "TE" else f"T{number}"


def mentions_code(text: str, number: str) -> bool:
    """¿Cita el texto el código de este caso (como T o como T&E)?"""
    return bool(number) and number in find_codes(text)


def required_messages(has_code: bool, is_contact: bool) -> list[tuple[str, str]]:
    """Correos que corresponden a un cambio solicitado: (propósito, destinatario).

    Con código: se confirma el recibido. Sin código: no avanza y se pide. Si quien escribe no es
    el contacto registrado, el contacto siempre recibe un aviso.
    """
    messages = [("ack" if has_code else "ask_code", "sender")]
    if not is_contact:
        messages.append(("notify_contact", "contact"))
    return messages


def apply_guard(db: sqlite3.Connection, thread_id: str, analysis: Analisis, requested_by_email: str,
                requested_by_name: str, cited_codes: list[str], message_id: str, subject: str,
                snippet: str, source: str) -> bool:
    """Aplica R11 a un análisis nuevo (de Work o de Python) antes de guardarlo.

    Si el caso ya tiene T y el mensaje no cumple, la tabla actual se conserva, lo que propone el
    análisis queda como propuesta y el mensaje se anota como posible cambio. True si bloqueó la tabla.
    """
    row = db.execute("SELECT quote_code, contact_email, code_assigned_at, analysis_json FROM quotes "
                     "WHERE thread_id=?", (thread_id,)).fetchone()
    if not row or not row["quote_code"]:
        return False
    when = str(analysis.get("received_at") or "")
    after = not when or _timestamp(when) > _timestamp(row["code_assigned_at"] or "")
    if not after:
        return False
    sender = (requested_by_email or "").strip().casefold()
    contact = (row["contact_email"] or "").strip().casefold()
    if table_update_allowed(row["quote_code"], contact, sender, cited_codes, True):
        return False
    has_code = row["quote_code"] in cited_numbers(cited_codes)
    is_contact = bool(sender) and sender == contact
    same_company = bool(company_domain(sender)) and company_domain(sender) == company_domain(contact)
    previous = json.loads(row["analysis_json"]).get("activities") or []
    proposed = analysis.get("activities") or []
    if sender:
        register_change(db, thread_id, message_id or f"{source}-{when}", sender, requested_by_name,
                        subject, snippet, when or datetime.now().astimezone().isoformat(timespec="seconds"),
                        has_code, is_contact, same_company)
    if proposed != previous:
        analysis["pending_model_activities"] = proposed
        analysis["activities"] = previous
        reason = ("no informó quién escribió el mensaje" if not sender
                  else "no es el contacto registrado" if not is_contact
                  else "no cita el código de la oferta")
        record_event(db, thread_id, "table_locked", source,
                     f"lock-{message_id or when}",
                     f"El análisis propuso cambios en la tabla, pero no se aplicaron (R11): {reason}. "
                     "Quedan como propuesta pendiente.", when)
    db.commit()
    return proposed != previous


def suggest_number(year: int | None = None) -> str:
    """Siguiente consecutivo del año según los códigos que ya registra el dashboard.

    Es una sugerencia: el consecutivo oficial puede ir más adelante, por eso se confirma a mano.
    """
    prefix = f"{(year or datetime.now().year) % 100:02d}"
    db = connect()
    try:
        used = [int(row["quote_code"][2:]) for row in db.execute(
            "SELECT quote_code FROM quotes WHERE quote_code LIKE ? AND length(quote_code)=5",
            (prefix + "%",)) if row["quote_code"][2:].isdigit()]
    finally:
        db.close()
    return f"{prefix}{(max(used) + 1 if used else 1):03d}"


def _action_ref() -> str:
    return f"{datetime.now().astimezone().isoformat(timespec='seconds')}-{uuid4().hex[:6]}"


def normalize_number(number: str) -> str:
    """El número de la T sin prefijo («T26145», «T&E26145» y «26145» son el mismo)."""
    return number.strip().upper().removeprefix("T&E").removeprefix("TE").removeprefix("T").strip()


def assign_code(thread_id: str, number: str) -> str:
    """Acepta el proyecto: le asigna su T. Desde aquí cualquier cambio debe citarla."""
    number = normalize_number(number)
    if not NUMBER_PATTERN.match(number):
        raise ValueError("El número debe tener 5 dígitos: año de 2 cifras y consecutivo de 3 (p. ej. 26145).")
    db = connect()
    try:
        row = db.execute("SELECT quote_code FROM quotes WHERE thread_id=?", (thread_id,)).fetchone()
        if not row:
            raise ValueError("La solicitud ya no existe.")
        if row["quote_code"]:
            raise ValueError(f"Este caso ya tiene el código {row['quote_code']}.")
        other = db.execute("SELECT 1 FROM quotes WHERE quote_code=?", (number,)).fetchone()
        if other:
            raise ValueError(f"El código {number} ya está asignado a otro caso.")
        now = datetime.now().astimezone().isoformat(timespec="seconds")
        db.execute("UPDATE quotes SET quote_code=?, code_stage='T', code_assigned_at=?, updated_at=? "
                   "WHERE thread_id=?", (number, now, now, thread_id))
        record_event(db, thread_id, "code_assigned", "dashboard", _action_ref(),
                     f"Se aceptó el proyecto y se asignó el código {code_label(number, 'T')}. "
                     "Desde aquí todo cambio debe citarlo.", now)
        db.commit()
    finally:
        db.close()
    return (f"Código {code_label(number, 'T')} asignado. Revisa el correo de «proyecto recibido» "
            "y envíalo para informar al cliente.")


def promote_to_te(thread_id: str) -> str:
    """La oferta fue aprobada: la T pasa a T&E (mismo número)."""
    db = connect()
    try:
        row = db.execute("SELECT quote_code, code_stage FROM quotes WHERE thread_id=?",
                         (thread_id,)).fetchone()
        if not row or not row["quote_code"]:
            raise ValueError("Este caso todavía no tiene código.")
        if row["code_stage"] == "TE":
            return "La oferta ya figuraba como aprobada."
        now = datetime.now().astimezone().isoformat(timespec="seconds")
        db.execute("UPDATE quotes SET code_stage='TE', updated_at=? WHERE thread_id=?", (now, thread_id))
        record_event(db, thread_id, "code_approved", "dashboard", _action_ref(),
                     f"La oferta fue aprobada: {code_label(row['quote_code'], 'T')} pasa a "
                     f"{code_label(row['quote_code'], 'TE')}.", now)
        db.commit()
    finally:
        db.close()
    return f"Oferta aprobada: ahora es {code_label(row['quote_code'], 'TE')}."


def resolve_change(thread_id: str, change_id: int, is_change: bool) -> str:
    """Una persona decide si el mensaje posterior a la T pide un cambio o es otra cosa."""
    db = connect()
    try:
        change = db.execute("SELECT * FROM case_changes WHERE id=? AND thread_id=? AND state='pending'",
                            (change_id, thread_id)).fetchone()
        if not change:
            return "Ese mensaje ya se había revisado."
        now = datetime.now().astimezone().isoformat(timespec="seconds")
        who = change["sender_name"] or change["sender_email"]
        db.execute("UPDATE case_changes SET state=?, decided_at=? WHERE id=?",
                   ("change" if is_change else "not_change", now, change_id))
        if is_change:
            summary = (f"Confirmaste que el mensaje de {who} «{change['subject'][:80]}» solicita un cambio "
                       f"({'con' if change['has_code'] else 'sin'} el código).")
            message = "Registrado como solicitud de cambio. Abajo quedan los correos por enviar."
        else:
            summary = f"Marcaste el mensaje de {who} «{change['subject'][:80]}» como no relacionado con un cambio."
            message = "Anotado: no es un cambio."
        record_event(db, thread_id, "change_confirmed" if is_change else "change_dismissed", "dashboard",
                     f"change-{change_id}", summary, now, change["sender_email"], change["sender_name"])
        db.commit()
    finally:
        db.close()
    return message


def known_addresses(thread_id: str) -> set[str]:
    """Direcciones que ya participan en el caso: solo a ellas se puede escribir desde el dashboard."""
    db = connect()
    try:
        found = {row[0].casefold() for row in db.execute(
            "SELECT contact_email FROM quotes WHERE thread_id=? "
            "UNION SELECT recipient FROM quotes WHERE thread_id=? "
            "UNION SELECT actor_email FROM case_events WHERE thread_id=? AND actor_email <> '' "
            "UNION SELECT sender_email FROM case_changes WHERE thread_id=? "
            "UNION SELECT sender_email FROM case_links WHERE thread_id=?",
            (thread_id,) * 5) if row[0]}
    finally:
        db.close()
    return found


def mark_change_sent(thread_id: str, change_id: int, purpose: str, to: str) -> None:
    """Deja constancia de que se envió la respuesta (o el aviso al contacto) de un cambio."""
    column = "contact_notified_at" if purpose == "notify_contact" else "reply_sent_at"
    labels = {"ack": "Se envió «cambio recibido» a", "ask_code": "Se pidió el código a",
              "notify_contact": "Se avisó al contacto registrado,"}
    db = connect()
    try:
        now = datetime.now().astimezone().isoformat(timespec="seconds")
        db.execute(f"UPDATE case_changes SET {column}=? WHERE id=? AND thread_id=?",
                   (now, change_id, thread_id))
        record_event(db, thread_id, "change_reply", "dashboard", f"{purpose}-{change_id}",
                     f"{labels.get(purpose, 'Se envió un correo a')} {to} por el cambio #{change_id}.", now)
        db.commit()
    finally:
        db.close()
