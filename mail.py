"""Lectura y envío de correo en Gmail: mensajes, adjuntos y enrutamiento."""

from __future__ import annotations

from typing import Any
import base64
import html
import re
from datetime import datetime
from email.message import EmailMessage
from email.utils import getaddresses, parseaddr
from attachments import extract_attachment_text




FORWARDED_SUBJECT_PATTERN = re.compile(r"^(?:fwd?|rv|reenv):\s*", re.IGNORECASE)


FORWARDED_BODY_PATTERN = re.compile(
    r"^-{2,}\s*(?:mensaje reenviado|forwarded message)\s*-{2,}$",
    re.IGNORECASE | re.MULTILINE,
)


def header(message: dict, name: str) -> str:
    for item in message.get("payload", {}).get("headers", []):
        if item["name"].lower() == name.lower():
            return item.get("value", "")
    return ""


def message_text(payload: dict) -> str:
    plain = []
    markup = []

    def visit(part: dict) -> None:
        if part.get("parts"):
            for child in part["parts"]:
                visit(child)
            return
        data = part.get("body", {}).get("data")
        if not data:
            return
        decoded = base64.urlsafe_b64decode(data + "=" * (-len(data) % 4)).decode(
            "utf-8", errors="replace"
        )
        mime = part.get("mimeType", "")
        if mime == "text/plain":
            plain.append(decoded)
        elif mime == "text/html":
            markup.append(re.sub(r"<[^>]+>", " ", html.unescape(decoded)))

    visit(payload)
    return "\n".join(plain or markup).strip()


def has_attachments(payload: dict) -> bool:
    if payload.get("filename") or payload.get("body", {}).get("attachmentId"):
        return True
    return any(has_attachments(part) for part in payload.get("parts", []))


def _part_header(part: dict, name: str) -> str:
    for item in part.get("headers", []):
        if item.get("name", "").casefold() == name.casefold():
            return item.get("value", "")
    return ""


def attachment_parts(payload: dict) -> list[dict]:
    parts = []
    filename = payload.get("filename", "").strip()
    attachment_id = payload.get("body", {}).get("attachmentId")
    if filename or attachment_id:
        parts.append({
            "filename": filename or "adjunto-sin-nombre",
            "mime_type": payload.get("mimeType", "application/octet-stream"),
            "attachment_id": attachment_id,
            "data": payload.get("body", {}).get("data"),
            "disposition": _part_header(payload, "Content-Disposition"),
            "content_id": _part_header(payload, "Content-ID"),
        })
    for part in payload.get("parts", []):
        parts.extend(attachment_parts(part))
    return parts


def attachment_names(payload: dict) -> list[str]:
    return [
        part["filename"] for part in attachment_parts(payload)
        if not is_inline_signature_image(part)
    ]


def _decode_websafe(data: str) -> bytes:
    return base64.urlsafe_b64decode(data + "=" * (-len(data) % 4))


def is_inline_signature_image(part: dict) -> bool:
    if not part.get("mime_type", "").casefold().startswith("image/"):
        return False
    disposition = part.get("disposition", "").casefold()
    return disposition.startswith("inline") or bool(part.get("content_id"))


def extract_message_attachments(gmail: Any, message: dict) -> list[dict]:
    results = []
    for part in attachment_parts(message.get("payload", {})):
        record = {
            "filename": part["filename"],
            "mime_type": part["mime_type"],
            "source_message_id": message.get("id", "no-indicado"),
        }
        if is_inline_signature_image(part):
            record.update({
                "status": "ignorado",
                "text": "",
                "reason": "imagen incrustada de firma o contenido del correo",
            })
            results.append(record)
            continue
        try:
            encoded = part.get("data")
            if not encoded and part.get("attachment_id"):
                response = gmail.users().messages().attachments().get(
                    userId="me", messageId=message["id"], id=part["attachment_id"]
                ).execute()
                encoded = response.get("data")
            if not encoded:
                text, reason = "", "Gmail no devolvió contenido para el adjunto"
            else:
                text, reason = extract_attachment_text(
                    part["filename"], part["mime_type"], _decode_websafe(encoded)
                )
        except Exception as exc:
            text, reason = "", f"error al descargar o leer: {exc}"
        record.update({
            "status": "extraido" if text else "bloqueado",
            "text": text,
            "reason": reason,
        })
        results.append(record)
    return results


def message_datetime(message: dict) -> str:
    value = message.get("internalDate")
    if value:
        try:
            return datetime.fromtimestamp(int(value) / 1000).strftime("%Y-%m-%d %H:%M")
        except (TypeError, ValueError, OSError):
            pass
    return header(message, "Date") or "no indicado"


def normalized_address(value: str) -> str:
    return parseaddr(value)[1].strip().casefold()


def inbound_messages(thread: dict, own_addresses: list[str] | set[str] | None = None) -> list[dict]:
    own = {normalized_address(address) for address in (own_addresses or []) if address}

    def is_external(message: dict) -> bool:
        if "SENT" in message.get("labelIds", []):
            return False
        senders = {address.casefold() for _, address in getaddresses([
            header(message, "From"), header(message, "Sender"),
        ]) if address}
        return not bool(own & senders)

    return sorted(
        (m for m in thread.get("messages", []) if is_external(m)),
        key=lambda m: int(m.get("internalDate", "0")),
    )


def own_addresses(config: dict, gmail: Any) -> set[str]:
    addresses = {
        normalized_address(value) for value in config.get("own_addresses", []) if value
    }
    try:
        primary = gmail.users().getProfile(userId="me").execute().get("emailAddress", "")
        if primary:
            addresses.add(normalized_address(primary))
    except Exception as exc:
        print(f"No se pudo consultar la cuenta principal de Gmail: {exc}")
    return {address for address in addresses if address}


def forwarded_routing(message: dict, config: dict) -> dict:
    envelope_from = parseaddr(header(message, "Reply-To") or header(message, "From"))[1]
    subject = header(message, "Subject")
    body = message_text(message.get("payload", {}))
    is_forwarded = bool(
        FORWARDED_SUBJECT_PATTERN.search(subject) or FORWARDED_BODY_PATTERN.search(body)
    )
    original_from = ""
    original_email = ""
    if is_forwarded:
        marker = FORWARDED_BODY_PATTERN.search(body)
        forwarded_body = body[marker.end():] if marker else body
        match = re.search(
            r"^(?:De|From):[ \t]*(.+?)\s*$", forwarded_body,
            re.IGNORECASE | re.MULTILINE,
        )
        if match:
            original_from = match.group(1).strip()
            original_email = parseaddr(original_from)[1]
    policy = config.get("forwarded_reply_policy", "internal_reviewer")
    recipient = original_email if (
        is_forwarded and policy == "original_sender" and original_email
    ) else envelope_from
    return {
        "is_forwarded": is_forwarded,
        "internal_forwarder": envelope_from if is_forwarded else "",
        "original_sender": original_from,
        "original_sender_email": original_email,
        "reply_policy": policy,
        "selected_recipient": recipient,
    }


def reply_email(
    thread: dict, recipient: str, body: str,
    own_email_addresses: list[str] | set[str] | None = None,
) -> EmailMessage:
    last = inbound_messages(thread, own_email_addresses)[-1]
    message_id = header(last, "Message-ID")
    subject = header(last, "Subject")
    email = EmailMessage()
    email["To"] = recipient
    email["Subject"] = subject if subject.lower().startswith("re:") else f"Re: {subject}"
    if message_id:
        email["In-Reply-To"] = message_id
        previous = header(last, "References")
        email["References"] = f"{previous} {message_id}".strip()
    email.set_content(body)
    return email


def send_reply(
    gmail: Any, thread: dict, recipient: str, body: str,
    own_email_addresses: list[str] | set[str] | None = None,
) -> str:
    email = reply_email(thread, recipient, body, own_email_addresses)
    raw = base64.urlsafe_b64encode(email.as_bytes()).decode("ascii")
    result = gmail.users().messages().send(
        userId="me", body={"raw": raw, "threadId": thread["id"]}
    ).execute()
    return result["id"]


def create_reply_draft(
    gmail: Any, thread: dict, recipient: str, body: str,
    own_email_addresses: list[str] | set[str] | None = None,
) -> str:
    email = reply_email(thread, recipient, body, own_email_addresses)
    raw = base64.urlsafe_b64encode(email.as_bytes()).decode("ascii")
    result = gmail.users().drafts().create(
        userId="me", body={"message": {"raw": raw, "threadId": thread["id"]}}
    ).execute()
    return result["id"]


def valid_email_address(value: str) -> bool:
    address = parseaddr(value or "")[1]
    return bool(re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", address))


def candidate_thread_ids(gmail: Any, query: str) -> list[str]:
    ids: set[str] = set()
    page_token = None
    while True:
        result = gmail.users().messages().list(
            userId="me", q=query, maxResults=500, pageToken=page_token
        ).execute()
        ids.update(m["threadId"] for m in result.get("messages", []))
        page_token = result.get("nextPageToken")
        if not page_token:
            return sorted(ids)
