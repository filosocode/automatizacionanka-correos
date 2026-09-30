"""Acciones sobre un caso desde el dashboard: destinatario, borrador, envío, aprobación."""

from __future__ import annotations

import base64
import json
from datetime import datetime
from pathlib import Path
from typing import Any
from status import (
    CLOSED_STATUSES, QuoteStatus,
)
from google_services import services, ensure_folder
from mail import (
    forwarded_routing, inbound_messages, own_addresses, reply_email,
    send_reply, valid_email_address,
)
from reception import (
    question_email, reception_questions, safe_folder_name,
)
from settings import load_config
from logging_setup import SENSITIVE, get_logger
from store import add_dashboard_notification, save

from quotes import get_quote, init_db

log = get_logger(__name__)


def resolve_case_recipient(thread_id: str) -> dict:
    quote = get_quote(thread_id)
    if not quote:
        raise ValueError("La solicitud ya no existe.")
    config = load_config()
    gmail, _ = services()
    account_addresses = own_addresses(config, gmail)
    thread = gmail.users().threads().get(
        userId="me", id=thread_id, format="full"
    ).execute()
    inbound = inbound_messages(thread, account_addresses)
    if not inbound:
        raise ValueError("No se encontró un mensaje entrante del cliente en el hilo.")
    routing = forwarded_routing(inbound[-1], config)
    recipient = routing.get("selected_recipient", "")
    if not valid_email_address(recipient):
        raise ValueError("Gmail no devolvió una dirección válida para responder.")
    analysis = quote["analysis"]
    analysis["routing"] = routing
    db = init_db()
    try:
        db.execute("""UPDATE quotes SET recipient=?, analysis_json=?, updated_at=?
                      WHERE thread_id=?""", (
            recipient, json.dumps(analysis, ensure_ascii=False),
            datetime.now().astimezone().isoformat(timespec="seconds"), thread_id,
        ))
        db.commit()
    finally:
        db.close()
    return {"quote": get_quote(thread_id), "gmail": gmail, "thread": thread,
            "account_addresses": account_addresses}


def _case_email_context(thread_id: str) -> tuple[dict, Any, dict, set[str]]:
    quote = get_quote(thread_id)
    if not quote:
        raise ValueError("La solicitud ya no existe.")
    if quote["status"] in CLOSED_STATUSES:
        raise ValueError("Esta solicitud no admite una respuesta desde el dashboard.")
    context = resolve_case_recipient(thread_id)
    quote = context["quote"]
    gmail = context["gmail"]
    thread = context["thread"]
    account_addresses = context["account_addresses"]
    return quote, gmail, thread, account_addresses


def save_dashboard_draft(thread_id: str, body: str) -> str:
    body = body.strip()
    if not body:
        raise ValueError("El borrador no puede estar vacío.")
    quote, gmail, thread, account_addresses = _case_email_context(thread_id)
    email = reply_email(thread, quote["recipient"], body, account_addresses)
    raw = base64.urlsafe_b64encode(email.as_bytes()).decode("ascii")
    if quote["draft_id"]:
        result = gmail.users().drafts().update(
            userId="me", id=quote["draft_id"],
            body={"message": {"raw": raw, "threadId": thread_id}},
        ).execute()
    else:
        result = gmail.users().drafts().create(
            userId="me", body={"message": {"raw": raw, "threadId": thread_id}}
        ).execute()
    draft_id = result["id"]
    now = datetime.now().astimezone().isoformat(timespec="seconds")
    db = init_db()
    try:
        db.execute(
            f"""UPDATE quotes SET status='{QuoteStatus.DRAFT_CREATED}', draft_id=?, draft_body=?,
                                 updated_at=? WHERE thread_id=?""",
            (draft_id, body, now, thread_id),
        )
        add_dashboard_notification(
            db, thread_id, "draft_saved", draft_id,
            "Borrador guardado en Gmail",
            f"El borrador quedó guardado para {quote['recipient']} y todavía no ha sido enviado.",
        )
        db.commit()
    finally:
        db.close()
    log.info("%s: borrador guardado en Gmail desde el dashboard", thread_id)
    return f"Borrador guardado en Gmail para {quote['recipient']}."


def send_dashboard_reply(thread_id: str, body: str) -> str:
    body = body.strip()
    if not body:
        raise ValueError("El correo no puede estar vacío.")
    quote, gmail, thread, account_addresses = _case_email_context(thread_id)
    email = reply_email(thread, quote["recipient"], body, account_addresses)
    raw = base64.urlsafe_b64encode(email.as_bytes()).decode("ascii")
    if quote["draft_id"]:
        gmail.users().drafts().update(
            userId="me", id=quote["draft_id"],
            body={"message": {"raw": raw, "threadId": thread_id}},
        ).execute()
        result = gmail.users().drafts().send(
            userId="me", body={"id": quote["draft_id"]}
        ).execute()
    else:
        result = gmail.users().messages().send(
            userId="me", body={"raw": raw, "threadId": thread_id}
        ).execute()
    now = datetime.now().astimezone().isoformat(timespec="seconds")
    db = init_db()
    try:
        db.execute(
            f"""UPDATE quotes SET status='{QuoteStatus.RESPONSE_SENT}', sent_message_id=?,
                                 draft_id=NULL, draft_body=?, sent_at=?, updated_at=?
               WHERE thread_id=?""",
            (result["id"], body, now, now, thread_id),
        )
        add_dashboard_notification(
            db, thread_id, QuoteStatus.RESPONSE_SENT, result["id"],
            "Respuesta enviada",
            f"El correo fue enviado a {quote['recipient']} en el mismo hilo.",
        )
        db.commit()
    finally:
        db.close()
    log.info("%s: correo enviado desde el dashboard", thread_id)
    return f"Correo enviado a {quote['recipient']} en el mismo hilo."


def approve_review(
    thread_id: str, body_file: str | None = None, body_text: str | None = None
) -> str:
    config = load_config()
    gmail, drive = services()
    account_addresses = own_addresses(config, gmail)
    db = init_db()
    try:
        row = db.execute(
            """SELECT last_inbound_id, status, recipient, analysis_json
               FROM quotes WHERE thread_id=?""",
            (thread_id,),
        ).fetchone()
        if not row:
            raise SystemExit(f"No existe revisión registrada para el hilo {thread_id}.")
        last_id, status, recipient, analysis_json = row
        analysis = json.loads(analysis_json)
        if status == QuoteStatus.REVIEW_PENDING:
            thread = gmail.users().threads().get(
                userId="me", id=thread_id, format="full"
            ).execute()
            if body_text is not None:
                body = body_text
            elif body_file:
                body = Path(body_file).read_text(encoding="utf-8")
            else:
                body = question_email(
                    reception_questions(analysis, config["required_fields"]),
                    analysis.get("routing", {}).get("is_forwarded", False),
                )
            save(db, thread_id, last_id, QuoteStatus.SENDING, recipient, analysis)
            sent_id = send_reply(gmail, thread, recipient, body, account_addresses)
            save(db, thread_id, last_id, QuoteStatus.WAITING, recipient, analysis,
                 sent_message_id=sent_id)
            message = f"{thread_id}: correo enviado a {recipient}."
            log.info("%s: correo enviado tras aprobación", thread_id)
            log.info("%s", message, extra=SENSITIVE)
            return message
        elif status == QuoteStatus.READY_REVIEW:
            if not config.get("drive_parent_id"):
                raise SystemExit("Para aprobar alcance completo define drive_parent_id en config.json.")
            name = safe_folder_name(
                analysis["project_name"], thread_id,
                config.get("folder_prefix", "Cotizacion"),
            )
            folder = ensure_folder(drive, config["drive_parent_id"], name)
            save(db, thread_id, last_id, QuoteStatus.READY, recipient, analysis,
                 folder_id=folder["id"])
            message = f"{thread_id}: carpeta creada/encontrada: {folder['id']}"
            log.info("%s", message)
            return message
        else:
            raise SystemExit(
                f"El hilo {thread_id} está en estado '{status}', no pendiente de aprobación."
            )
    finally:
        db.close()
