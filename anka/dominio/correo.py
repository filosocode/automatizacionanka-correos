"""El correo como dato: leer cabeceras, texto y adjuntos de un mensaje de Gmail y armar una respuesta.

Son funciones puras sobre los diccionarios que devuelve la API de Gmail: no llaman a Gmail ni escriben nada.
Lo que sí habla con Gmail (enviar, crear borradores, descargar adjuntos) vive en `anka.infra.mail`.
"""

from __future__ import annotations

import base64
import html
import re
from datetime import datetime
from email.message import EmailMessage
from email.utils import getaddresses, parseaddr

FORWARDED_SUBJECT_PATTERN = re.compile(r"^(?:fwd?|rv|reenv):\s*", re.IGNORECASE)

FORWARDED_BODY_PATTERN = re.compile(
    r"^-{2,}\s*(?:mensaje reenviado|forwarded message)\s*-{2,}$",
    re.IGNORECASE | re.MULTILINE,
)


def header(message: dict, name: str) -> str:
    """Valor de una cabecera del mensaje ('' si no existe), sin distinguir mayúsculas."""
    for item in message.get("payload", {}).get("headers", []):
        if item["name"].lower() == name.lower():
            return item.get("value", "")
    return ""


def decode_websafe(data: str) -> bytes:
    """Decodifica el base64 «seguro para URL» que usa Gmail (con o sin relleno)."""
    return base64.urlsafe_b64decode(data + "=" * (-len(data) % 4))


def message_text(payload: dict) -> str:
    """Texto del mensaje: el cuerpo en texto plano y, si no hay, el HTML sin etiquetas."""
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
        decoded = decode_websafe(data).decode("utf-8", errors="replace")
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
    """Todas las partes del mensaje que son un archivo adjunto (también las anidadas)."""
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


def is_inline_signature_image(part: dict) -> bool:
    """¿Es una imagen incrustada (logo de firma) y no un documento que mandó el cliente?"""
    if not part.get("mime_type", "").casefold().startswith("image/"):
        return False
    disposition = part.get("disposition", "").casefold()
    return disposition.startswith("inline") or bool(part.get("content_id"))


def attachment_names(payload: dict) -> list[str]:
    """Nombres de los adjuntos reales (sin los logos incrustados en firmas)."""
    return [
        part["filename"] for part in attachment_parts(payload)
        if not is_inline_signature_image(part)
    ]


def message_datetime(message: dict) -> str:
    """Fecha y hora de llegada («AAAA-MM-DD HH:MM», hora local) o la cabecera Date si falta."""
    value = message.get("internalDate")
    if value:
        try:
            return datetime.fromtimestamp(int(value) / 1000).strftime("%Y-%m-%d %H:%M")
        except (TypeError, ValueError, OSError):
            pass
    return header(message, "Date") or "no indicado"


def normalized_address(value: str) -> str:
    return parseaddr(value)[1].strip().casefold()


def valid_email_address(value: str) -> bool:
    address = parseaddr(value or "")[1]
    return bool(re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", address))


def inbound_messages(thread: dict, own_addresses: list[str] | set[str] | None = None) -> list[dict]:
    """Mensajes de otras personas en el hilo (no los enviados por la cuenta propia), del más viejo al más nuevo."""
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


def forwarded_routing(message: dict, config: dict) -> dict:
    """A quién se responde: el remitente directo o, si un comercial reenvió, el cliente original (según la política)."""
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
    """Arma la respuesta al último mensaje del cliente, en el mismo hilo (asunto «Re:», cabeceras de hilo)."""
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
