"""Correo en Gmail: lo que habla con la API (descargar adjuntos, enviar, crear borradores, listar hilos).

El análisis de un mensaje (cabeceras, texto, adjuntos, enrutamiento) son funciones puras que viven en
`anka.dominio.correo`; aquí solo está lo que necesita un cliente de Gmail.
"""

from __future__ import annotations

import base64
from typing import Any

from anka.dominio.analisis import Adjunto
from anka.dominio.attachments import extract_attachment_text
from anka.dominio.correo import (
    attachment_parts, decode_websafe, is_inline_signature_image, normalized_address, reply_email,
)
from anka.infra.logging_setup import get_logger

log = get_logger(__name__)


def extract_message_attachments(gmail: Any, message: dict) -> list[Adjunto]:
    """Descarga y lee los adjuntos de un mensaje; nunca falla: un adjunto ilegible queda «bloqueado» con su motivo."""
    results: list[Adjunto] = []
    for part in attachment_parts(message.get("payload", {})):
        record: Adjunto = {
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
                    part["filename"], part["mime_type"], decode_websafe(encoded)
                )
        except Exception as exc:  # un adjunto roto no debe frenar el análisis del correo
            log.warning("Adjunto no legible en el mensaje %s: %s", message.get("id"), type(exc).__name__)
            text, reason = "", f"error al descargar o leer: {exc}"
        record.update({
            "status": "extraido" if text else "bloqueado",
            "text": text,
            "reason": reason,
        })
        results.append(record)
    return results


def own_addresses(config: dict, gmail: Any) -> set[str]:
    """Direcciones de la cuenta propia (las de config.json más la principal de Gmail)."""
    addresses = {
        normalized_address(value) for value in config.get("own_addresses", []) if value
    }
    try:
        primary = gmail.users().getProfile(userId="me").execute().get("emailAddress", "")
        if primary:
            addresses.add(normalized_address(primary))
    except Exception as exc:  # sin el perfil se sigue con las direcciones de la configuración
        log.warning("No se pudo consultar la cuenta principal de Gmail: %s", exc)
    return {address for address in addresses if address}


def send_reply(
    gmail: Any, thread: dict, recipient: str, body: str,
    own_email_addresses: list[str] | set[str] | None = None,
) -> str:
    """Envía la respuesta en el hilo y devuelve el id del mensaje enviado."""
    email = reply_email(thread, recipient, body, own_email_addresses)
    raw = base64.urlsafe_b64encode(email.as_bytes()).decode("ascii")
    result = gmail.users().messages().send(
        userId="me", body={"raw": raw, "threadId": thread["id"]}
    ).execute()
    return str(result["id"])


def create_reply_draft(
    gmail: Any, thread: dict, recipient: str, body: str,
    own_email_addresses: list[str] | set[str] | None = None,
) -> str:
    """Guarda la respuesta como borrador de Gmail en el hilo y devuelve el id del borrador."""
    email = reply_email(thread, recipient, body, own_email_addresses)
    raw = base64.urlsafe_b64encode(email.as_bytes()).decode("ascii")
    result = gmail.users().drafts().create(
        userId="me", body={"message": {"raw": raw, "threadId": thread["id"]}}
    ).execute()
    return str(result["id"])


def candidate_thread_ids(gmail: Any, query: str) -> list[str]:
    """Ids de los hilos que coinciden con la búsqueda de Gmail (recorre todas las páginas)."""
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
