"""Seguimiento del hilo en Gmail (solo lectura): registra lo enviado y lo recibido, venga de donde venga."""

from __future__ import annotations

import json
from datetime import datetime
from email.utils import parseaddr
from typing import Any

from logging_setup import get_logger
from quotes import init_db
from status import QuoteStatus
from store import add_dashboard_notification, record_event

log = get_logger(__name__)

FOLLOWUP_QUERY = "newer_than:30d -in:drafts"

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


def _list_messages(gmail: Any) -> list[dict]:
    found, page = [], None
    while True:
        result = gmail.users().messages().list(
            userId="me", q=FOLLOWUP_QUERY, maxResults=100, pageToken=page).execute()
        found.extend(result.get("messages", []))
        page = result.get("nextPageToken")
        if not page:
            return found


def sync_thread_activity(gmail: Any, seen: set[str]) -> dict:
    """Anota en cada caso conocido los correos enviados y recibidos y mueve su estado si procede.

    `seen` guarda los mensajes ya revisados en este proceso; la base evita duplicar entre reinicios.
    """
    db = init_db()
    try:
        cases = {row["thread_id"]: row for row in db.execute(
            "SELECT thread_id, status, first_seen_at, last_inbound_id, analysis_json FROM quotes")}
        known = {(row["thread_id"], row["ref"]) for row in db.execute(
            "SELECT thread_id, ref FROM case_events WHERE kind IN ('sent', 'received')")}
        events = moved = 0
        for item in _list_messages(gmail):
            thread_id, message_id = str(item.get("threadId") or ""), item["id"]
            if message_id in seen or thread_id not in cases:
                continue
            if (thread_id, message_id) in known:
                seen.add(message_id)
                continue
            message = gmail.users().messages().get(
                userId="me", id=message_id, format="metadata",
                metadataHeaders=["From", "To", "Subject"]).execute()
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
            if "SENT" in message.get("labelIds", []):
                to = parseaddr(headers.get("to", ""))[1] or "el destinatario"
                if record_event(db, thread_id, "sent", "gmail", message_id,
                                f"Correo enviado desde Gmail a {to}.", _iso(when)):
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
                sender = parseaddr(headers.get("from", ""))[1] or "el cliente"
                if record_event(db, thread_id, "received", "gmail", message_id,
                                f"Correo recibido de {sender}.", _iso(when)):
                    events += 1
                    if when > analyzed and message_id != case["last_inbound_id"]:
                        add_dashboard_notification(
                            db, thread_id, "client_reply", message_id, "El cliente respondió",
                            f"Hay un correo nuevo de {sender} en el hilo.")
            db.commit()
            seen.add(message_id)
        return {"events": events, "moved": moved}
    finally:
        db.close()
