"""Seguimiento cuando el cliente tarda en responder (R13).

- Muestra cuánto lleva esperando el caso, en días hábiles.
- A los 3 días hábiles sin respuesta lo marca para recordar; a los 7, para llamar.
- Si una persona lo activa en ese caso, envía hasta 2 recordatorios automáticos (a 1 y 3 días hábiles),
  solo en horario hábil, con texto fijo y neutro y solo al contacto registrado. Se detienen en cuanto el
  cliente responde por cualquier vía, o si una persona pausa o dice que ya habló con el cliente.
Es la única excepción a R8 (los correos al cliente son borradores que una persona revisa y envía).
"""

from __future__ import annotations

import base64
from datetime import date, datetime, timedelta, timezone
from typing import Any

import settings
from logging_setup import get_logger
from store import open_db, record_event

log = get_logger(__name__)

# R13 (reglas_operativas.md): lo que Python aplica y test_paridad_modos compara con las reglas.
MAX_REMINDERS = 2
REMINDER_BUSINESS_DAYS = (1, 3)       # días hábiles desde el correo con las preguntas
ALERT_BUSINESS_DAYS = 3               # sin recordatorios automáticos: avisar para recordar
CALL_BUSINESS_DAYS = 7                # pasada esta espera, llamar al cliente
BUSINESS_HOURS = (8, 17)              # lunes a viernes, hora local (Colombia)
AUTO_REQUIRES_ACTIVATION = True       # apagado por defecto; lo activa una persona caso por caso
ONLY_TO_REGISTERED_CONTACT = True

ACTIONS = {"auto_on", "auto_off", "pause", "resume", "contacted"}


BOGOTA = timezone(timedelta(hours=-5))   # Colombia no tiene horario de verano: el horario hábil no depende del PC


def _local(moment: datetime) -> datetime:
    return moment.astimezone(BOGOTA) if moment.tzinfo else moment.replace(tzinfo=BOGOTA)


def business_days(start: datetime, end: datetime) -> int:
    """Días hábiles (lunes a viernes) transcurridos entre dos momentos, contando por fecha."""
    first, last = _local(start).date(), _local(end).date()
    days, current = 0, first
    while current < last:
        current += timedelta(days=1)
        if current.weekday() < 5:
            days += 1
    return days


def in_business_hours(now: datetime) -> bool:
    moment = _local(now)
    return moment.weekday() < 5 and BUSINESS_HOURS[0] <= moment.hour < BUSINESS_HOURS[1]


def reminder_text(number: int) -> str:
    """Texto fijo y neutro: sin cifras, sin precios, sin el código de la oferta."""
    if number == 1:
        return ("Hola,\n\nQuedamos atentos a la información que le solicitamos en el correo anterior para "
                "poder avanzar con su solicitud. Si ya la tiene a la mano, puede responder directamente a "
                "este mensaje.\n\nSaludos,\nGrupo Anka")
    return ("Hola,\n\nNos permitimos recordarle que seguimos a la espera de la información solicitada para "
            "continuar con la revisión de su solicitud. Si prefiere, podemos coordinar una llamada para "
            "resolver cualquier duda.\n\nSaludos,\nGrupo Anka")


def _db() -> Any:
    return open_db(settings.DATA_DIR)


def followup_state(db: Any, thread_id: str) -> dict:
    row = db.execute("SELECT auto_enabled, paused, contacted_at FROM case_followup WHERE thread_id=?",
                     (thread_id,)).fetchone()
    return {"auto_enabled": bool(row["auto_enabled"]) if row else False,
            "paused": bool(row["paused"]) if row else False,
            "contacted_at": (row["contacted_at"] or "") if row else ""}


def set_followup(thread_id: str, action: str) -> str:
    """Activa, apaga o pausa el seguimiento automático, o anota que ya se habló con el cliente."""
    if action not in ACTIONS:
        raise ValueError("Acción de seguimiento no válida.")
    db = _db()
    try:
        if not db.execute("SELECT 1 FROM quotes WHERE thread_id=?", (thread_id,)).fetchone():
            raise ValueError("La solicitud ya no existe.")
        now = datetime.now().astimezone().isoformat(timespec="seconds")
        db.execute("INSERT OR IGNORE INTO case_followup (thread_id, updated_at) VALUES (?, ?)", (thread_id, now))
        texts = {
            "auto_on": ("UPDATE case_followup SET auto_enabled=1, paused=0, updated_at=? WHERE thread_id=?",
                        "Activaste el seguimiento automático: se enviarán hasta 2 recordatorios (a 1 y 3 días hábiles) "
                        "y después se te avisará para llamar.",
                        "Seguimiento automático activado."),
            "auto_off": ("UPDATE case_followup SET auto_enabled=0, updated_at=? WHERE thread_id=?",
                         "Desactivaste el seguimiento automático.", "Seguimiento automático desactivado."),
            "pause": ("UPDATE case_followup SET paused=1, updated_at=? WHERE thread_id=?",
                      "Pausaste el seguimiento automático.", "Seguimiento pausado."),
            "resume": ("UPDATE case_followup SET paused=0, updated_at=? WHERE thread_id=?",
                       "Reanudaste el seguimiento automático.", "Seguimiento reanudado."),
            "contacted": ("UPDATE case_followup SET contacted_at=?, updated_at=? WHERE thread_id=?",
                          "Anotaste que ya hablaste con el cliente: se detienen los avisos y recordatorios.",
                          "Anotado: ya hablaste con el cliente."),
        }
        sql, summary, message = texts[action]
        if action == "contacted":
            db.execute(sql, (now, now, thread_id))
        else:
            db.execute(sql, (now, thread_id))
        record_event(db, thread_id, f"followup_{action}", "dashboard", f"{now}-{action}", summary, now)
        db.commit()
    finally:
        db.close()
    return message


def _last_reply(db: Any, thread_id: str) -> str:
    row = db.execute("SELECT MAX(occurred_at) AS last FROM case_events WHERE thread_id=? AND kind='received'",
                     (thread_id,)).fetchone()
    return (row["last"] or "") if row else ""


def _parse(value: str) -> datetime | None:
    try:
        return datetime.fromisoformat(value) if value else None
    except ValueError:
        return None


def due_reminders(now: datetime | None = None) -> list[tuple[str, int]]:
    """(caso, número de recordatorio) que toca enviar ahora. Vacío fuera del horario hábil."""
    now = now or datetime.now().astimezone()
    if not in_business_hours(now):
        return []
    due: list[tuple[str, int]] = []
    db = _db()
    try:
        rows = db.execute(
            """SELECT q.thread_id, q.sent_at, q.recipient, q.contact_email, f.contacted_at
               FROM quotes q JOIN case_followup f ON f.thread_id = q.thread_id
               WHERE q.status='waiting' AND q.sent_at <> '' AND f.auto_enabled=1 AND f.paused=0""").fetchall()
        for row in rows:
            sent = _parse(row["sent_at"])
            if sent is None:
                continue
            contacted = _parse(row["contacted_at"] or "")
            if contacted and contacted >= sent:
                continue
            last_reply = _parse(_last_reply(db, row["thread_id"]))
            if last_reply and last_reply >= sent:
                continue                                       # el cliente ya respondió
            if db.execute("SELECT 1 FROM case_links WHERE thread_id=? AND state='pending' "
                          "UNION SELECT 1 FROM case_changes WHERE thread_id=? AND state='pending'",
                          (row["thread_id"], row["thread_id"])).fetchone():
                continue                                       # hay algo del cliente por revisar
            contact = (row["contact_email"] or "").casefold()
            if ONLY_TO_REGISTERED_CONTACT and contact and contact != str(row["recipient"]).casefold():
                continue
            done = db.execute("SELECT ref, occurred_at FROM case_events WHERE thread_id=? AND kind='reminder_sent' "
                              "AND ref LIKE ? ORDER BY id", (row["thread_id"], row["sent_at"] + "|%")).fetchall()
            number = len(done) + 1
            if number > MAX_REMINDERS:
                continue
            if business_days(sent, now) < REMINDER_BUSINESS_DAYS[number - 1]:
                continue
            if done:
                previous = _parse(done[-1]["occurred_at"])
                if previous and business_days(previous, now) < 1:
                    continue                                   # al menos un día hábil entre recordatorios
            due.append((row["thread_id"], number))
    finally:
        db.close()
    return due


def send_reminder(thread_id: str, number: int) -> None:
    """Envía el recordatorio en el hilo del caso sin tocar su estado ni la fecha del correo original."""
    from case_actions import _case_email_context
    from mail import reply_email

    quote, gmail, thread, account_addresses = _case_email_context(thread_id)
    recipient = str(quote["recipient"])
    contact = (quote.get("contact_email") or "").casefold()
    if ONLY_TO_REGISTERED_CONTACT and contact and contact != recipient.casefold():
        raise ValueError("El recordatorio solo se envía al contacto registrado.")
    email = reply_email(thread, recipient, reminder_text(number), account_addresses)
    raw = base64.urlsafe_b64encode(email.as_bytes()).decode("ascii")
    result = gmail.users().messages().send(userId="me", body={"raw": raw, "threadId": thread_id}).execute()
    now = datetime.now().astimezone().isoformat(timespec="seconds")
    db = _db()
    try:
        record_event(db, thread_id, "reminder_sent", "dashboard", f"{quote['sent_at']}|{number}",
                     f"Recordatorio automático {number} enviado a {recipient}: «{reminder_text(number)[:70]}…» "
                     f"(mensaje {result['id']}).", now)
        db.commit()
    finally:
        db.close()
    log.info("%s: recordatorio %d enviado", thread_id, number)


def run_due_reminders(now: datetime | None = None) -> int:
    """Envía los recordatorios que tocan. Un fallo en un caso no frena a los demás."""
    sent = 0
    for thread_id, number in due_reminders(now):
        try:
            send_reminder(thread_id, number)
            sent += 1
        except (Exception, SystemExit):
            log.exception("%s: no se pudo enviar el recordatorio %d", thread_id, number)
    return sent


def waiting_days(waiting_since: str, now: datetime | None = None) -> int:
    start = _parse(waiting_since)
    return business_days(start, now or datetime.now().astimezone()) if start else 0


def call_due(waiting_since: str, contacted_at: str, now: datetime | None = None) -> bool:
    start = _parse(waiting_since)
    contacted = _parse(contacted_at)
    if not start or (contacted and contacted >= start):
        return False
    return waiting_days(waiting_since, now) >= CALL_BUSINESS_DAYS


def today() -> date:
    return _local(datetime.now().astimezone()).date()
