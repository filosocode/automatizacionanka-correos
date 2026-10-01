"""Seguimiento del hilo en Gmail (solo lectura): registra lo enviado y lo recibido, venga de donde venga.

Cada hecho guarda quién lo escribió (persona y empresa) y un extracto de lo que decía, para poder
reconstruir quién pidió qué y cuándo. Además busca correos de la misma persona o de la misma
empresa en OTRO hilo: no los une al caso por su cuenta, los propone para que una persona decida.
"""

from __future__ import annotations

import base64
import email
import email.policy
import json
from datetime import datetime
from email.utils import parseaddr
from pathlib import Path
from typing import Any

import settings
from change_control import company_domain, mentions_code
from logging_setup import get_logger
from quotes import init_db
from status import QuoteStatus
from mail import message_text
from store import add_dashboard_notification, add_message, record_event, register_change

log = get_logger(__name__)

FOLLOWUP_QUERY = "newer_than:30d -in:drafts"
RELATED_CHUNK = 15              # remitentes y dominios por consulta a Gmail
SNIPPET_CHARS = 160

# Estado al que pasa un caso cuando se detecta un correo enviado desde Gmail. Los demás
# estados (carpeta creada, información suficiente, revisión técnica...) solo anotan el hecho.
AFTER_SENT = {
    QuoteStatus.REVIEW_PENDING: QuoteStatus.WAITING,
    QuoteStatus.SENDING: QuoteStatus.WAITING,
    QuoteStatus.DRAFT_CREATED: QuoteStatus.RESPONSE_SENT,
}


def _iso(millis: int) -> str:
    return datetime.fromtimestamp(millis / 1000).astimezone().isoformat(timespec="seconds")


def _millis(value: str) -> int:
    try:
        moment = datetime.fromisoformat(value)
    except (TypeError, ValueError):
        return 0
    return int(moment.astimezone().timestamp() * 1000)


def _headers(message: dict) -> dict:
    return {h["name"].lower(): h["value"] for h in message.get("payload", {}).get("headers", [])}


def case_contacts(recipient: str, analysis: dict) -> set[str]:
    """Correos que ya identifican a este cliente en el caso."""
    routing = analysis.get("routing", {}) if isinstance(analysis, dict) else {}
    values = (recipient, routing.get("original_sender_email", ""), routing.get("selected_recipient", ""))
    return {parseaddr(value)[1].casefold() for value in values if value and parseaddr(value)[1]}


def _hint_words(analysis: dict) -> set[str]:
    name = str(analysis.get("project_name") or "")
    return {word for word in name.casefold().split() if len(word) >= 4 or word.isdigit()}


def match_reason(sender: str, subject: str, contacts: set[str], analysis: dict) -> str:
    """'sender', 'domain' o '' según cómo se parezca el remitente a quienes ya escribieron."""
    if sender in contacts:
        reason = "sender"
    elif company_domain(sender) and company_domain(sender) in {company_domain(c) for c in contacts}:
        reason = "domain"
    else:
        return ""
    hints = _hint_words(analysis)
    if hints and any(word in subject.casefold() for word in hints):
        reason += "+subject"
    return reason


def _read_raw(gmail: Any, message_id: str) -> tuple[bytes, str]:
    """Correo completo (para guardarlo como evidencia) y su texto (asunto + cuerpo)."""
    raw = gmail.users().messages().get(userId="me", id=message_id, format="raw").execute()["raw"]
    data = base64.urlsafe_b64decode(raw + "=" * (-len(raw) % 4))
    message = email.message_from_bytes(data, policy=email.policy.default)
    parts = [str(message.get("Subject", ""))]
    for part in message.walk():
        if part.get_content_type() in ("text/plain", "text/html"):
            try:
                parts.append(str(part.get_content()))
            except (LookupError, ValueError):
                pass
    return data, "\n".join(parts)


def _save_eml(thread_id: str, message_id: str, data: bytes) -> None:
    """Copia del correo original: no depende de que siga existiendo en Gmail."""
    folder = settings.DATA_DIR / "evidence" / thread_id
    folder.mkdir(parents=True, exist_ok=True)
    target = Path(folder / f"{message_id}.eml")
    if not target.exists():
        target.write_bytes(data)


def _list_messages(gmail: Any, query: str = FOLLOWUP_QUERY) -> list[dict]:
    found, page = [], None
    while True:
        result = gmail.users().messages().list(
            userId="me", q=query, maxResults=100, pageToken=page).execute()
        found.extend(result.get("messages", []))
        page = result.get("nextPageToken")
        if not page:
            return found


def _related_queries(cases: dict) -> list[str]:
    """Consultas de Gmail con los remitentes y dominios de empresa de los casos abiertos."""
    terms: set[str] = set()
    for case in cases.values():
        if case["status"] in (QuoteStatus.IGNORED, QuoteStatus.MISSING):
            continue
        try:
            analysis = json.loads(case["analysis_json"])
        except (TypeError, ValueError):
            analysis = {}
        for address in case_contacts(case["recipient"], analysis):
            terms.add(f"from:{address}")
            if company_domain(address):
                terms.add(f"from:{company_domain(address)}")
    ordered = sorted(terms)
    return [
        "newer_than:30d -in:drafts -in:sent (" + " OR ".join(ordered[i:i + RELATED_CHUNK]) + ")"
        for i in range(0, len(ordered), RELATED_CHUNK)
    ]


def sync_thread_activity(gmail: Any, seen: set[str]) -> dict:
    """Anota en cada caso conocido los correos enviados y recibidos y mueve su estado si procede.

    `seen` guarda los mensajes ya revisados en este proceso; la base evita duplicar entre reinicios.
    """
    db = init_db()
    try:
        cases = {row["thread_id"]: row for row in db.execute(
            "SELECT thread_id, status, first_seen_at, last_inbound_id, recipient, analysis_json, "
            "quote_code, code_assigned_at, contact_email FROM quotes")}
        known = {(row["thread_id"], row["ref"]) for row in db.execute(
            "SELECT thread_id, ref FROM case_events WHERE kind IN ('sent', 'received')")}
        linked = {row["message_id"] for row in db.execute("SELECT message_id FROM case_links")}
        events = moved = related = 0
        for item in _list_messages(gmail):
            thread_id, message_id = str(item.get("threadId") or ""), item["id"]
            if message_id in seen or thread_id not in cases:
                continue
            if (thread_id, message_id) in known:
                seen.add(message_id)
                continue
            message = gmail.users().messages().get(
                userId="me", id=message_id, format="full").execute()
            case = cases[thread_id]
            when = int(message.get("internalDate", "0"))
            try:
                analysis = json.loads(case["analysis_json"])
            except (TypeError, ValueError):
                analysis = {}
            analyzed = _millis(analysis.get("received_at") or "")
            if when < _millis(case["first_seen_at"] or ""):
                seen.add(message_id)  # anterior a la solicitud: no es seguimiento
                continue
            headers = _headers(message)
            snippet = " ".join(str(message.get("snippet", "")).split())[:SNIPPET_CHARS]
            add_message(
                db, thread_id, message_id,
                "sent" if "SENT" in message.get("labelIds", []) else "received",
                headers.get("from", ""), headers.get("subject", ""),
                message_text(message.get("payload", {})) or snippet, _iso(when))
            if "SENT" in message.get("labelIds", []):
                to = parseaddr(headers.get("to", ""))[1] or "el destinatario"
                if record_event(db, thread_id, "sent", "gmail", message_id,
                                f"Correo enviado desde Gmail a {to}: «{snippet}»", _iso(when)):
                    events += 1
                new_status = AFTER_SENT.get(case["status"]) if when > analyzed else None
                if new_status:
                    db.execute(
                        """UPDATE quotes SET status=?, sent_message_id=?, draft_id=NULL,
                                             sent_at=?, updated_at=? WHERE thread_id=?""",
                        (new_status, message_id, _iso(when),
                         datetime.now().astimezone().isoformat(timespec="seconds"), thread_id))
                    cases[thread_id] = {**dict(case), "status": new_status}
                    moved += 1
                    log.info("%s: envío detectado en Gmail; el caso pasa a %s", thread_id, new_status)
            else:
                name, address = parseaddr(headers.get("from", ""))
                who = name or address or "el cliente"
                company = company_domain(address)
                label = f"{who} ({company})" if company else who
                if record_event(db, thread_id, "received", "gmail", message_id,
                                f"{label} escribió: «{snippet}»", _iso(when),
                                address.casefold(), name):
                    events += 1
                    if case["quote_code"] and when > _millis(case["code_assigned_at"] or ""):
                        _register_post_code_change(
                            gmail, db, thread_id, case, message_id, address.casefold(), name,
                            headers.get("subject", ""), snippet, _iso(when))
                    if when > analyzed and message_id != case["last_inbound_id"]:
                        add_dashboard_notification(
                            db, thread_id, "client_reply", message_id, "El cliente respondió",
                            f"Hay un correo nuevo de {who} en el hilo.")
            db.commit()
            seen.add(message_id)
        _backfill_messages(gmail, db)
        related = _find_related(gmail, db, cases, seen, linked)
        return {"events": events, "moved": moved, "related": related}
    finally:
        db.close()


def _register_post_code_change(gmail: Any, db: Any, thread_id: str, case: Any, message_id: str,
                                sender: str, name: str, subject: str, snippet: str, when: str) -> None:
    """Mensaje posterior a la T: guarda el correo original y anota si cita el código."""
    try:
        data, text = _read_raw(gmail, message_id)
        _save_eml(thread_id, message_id, data)
    except Exception:
        log.warning("%s: no se pudo guardar el correo original de un posible cambio", thread_id, exc_info=True)
        text = subject
    contact = (case["contact_email"] or "").casefold()
    same_company = bool(company_domain(sender)) and company_domain(sender) == company_domain(contact)
    register_change(db, thread_id, message_id, sender, name, subject, snippet, when,
                    mentions_code(text, case["quote_code"]), sender == contact, same_company)


def _find_related(gmail: Any, db: Any, cases: dict, seen: set[str], linked: set[str]) -> int:
    """Correos de la misma persona o empresa en otro hilo: se proponen, nunca se unen solos."""
    created = 0
    for query in _related_queries(cases):
        for item in _list_messages(gmail, query):
            thread_id, message_id = str(item.get("threadId") or ""), item["id"]
            if thread_id in cases or message_id in seen or message_id in linked:
                continue
            message = gmail.users().messages().get(
                userId="me", id=message_id, format="metadata",
                metadataHeaders=["From", "To", "Subject"]).execute()
            seen.add(message_id)
            if "SENT" in message.get("labelIds", []):
                continue
            headers = _headers(message)
            name, address = parseaddr(headers.get("from", ""))
            sender, subject = address.casefold(), headers.get("subject", "")
            when = int(message.get("internalDate", "0"))
            snippet = " ".join(str(message.get("snippet", "")).split())[:SNIPPET_CHARS]
            for case_thread, case in cases.items():
                if case["status"] in (QuoteStatus.IGNORED, QuoteStatus.MISSING):
                    continue
                if when < _millis(case["first_seen_at"] or ""):
                    continue
                try:
                    analysis = json.loads(case["analysis_json"])
                except (TypeError, ValueError):
                    analysis = {}
                reason = match_reason(sender, subject, case_contacts(case["recipient"], analysis), analysis)
                if not reason:
                    continue
                has_code = False
                if case["quote_code"]:
                    try:
                        data, text = _read_raw(gmail, message_id)
                        _save_eml(case_thread, message_id, data)
                        has_code = mentions_code(text, case["quote_code"])
                    except Exception:
                        log.warning("%s: no se pudo leer el correo completo", case_thread, exc_info=True)
                inserted = db.execute(
                    """INSERT OR IGNORE INTO case_links
                       (thread_id, other_thread_id, message_id, sender_email, sender_name,
                        subject, snippet, reason, occurred_at, has_code)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (case_thread, thread_id, message_id, sender, name, subject[:200], snippet,
                     reason, _iso(when), int(has_code))).rowcount
                if inserted:
                    created += 1
                    who = name or sender
                    record_event(db, case_thread, "related", "gmail", message_id,
                                 f"Llegó un correo de {who} en otro hilo: «{subject[:80]}». "
                                 "Pendiente de confirmar si es de este caso.", _iso(when), sender, name)
                    add_dashboard_notification(
                        db, case_thread, "possible_update", message_id, "Posible actualización",
                        f"{who} escribió en otro hilo; confirma si es de este caso.")
            db.commit()
    return created



def _backfill_messages(gmail: Any, db: Any, limit: int = 20) -> None:
    """Guarda el texto de correos ya anotados antes de existir la lectura de mensajes (no toca el historial)."""
    rows = db.execute(
        """SELECT e.thread_id, e.ref, e.kind FROM case_events e
           WHERE e.kind IN ('sent', 'received') AND NOT EXISTS (
               SELECT 1 FROM case_messages m WHERE m.thread_id = e.thread_id AND m.message_id = e.ref)
           ORDER BY e.id DESC LIMIT ?""", (limit,)).fetchall()
    for row in rows:
        try:
            message = gmail.users().messages().get(userId="me", id=row["ref"], format="full").execute()
        except Exception:
            add_message(db, row["thread_id"], row["ref"], row["kind"], "", "", "(no se pudo leer el mensaje en Gmail)", "")
            continue
        headers = _headers(message)
        add_message(db, row["thread_id"], row["ref"], row["kind"], headers.get("from", ""),
                    headers.get("subject", ""),
                    message_text(message.get("payload", {})) or str(message.get("snippet", "")),
                    _iso(int(message.get("internalDate", "0"))))
    db.commit()
