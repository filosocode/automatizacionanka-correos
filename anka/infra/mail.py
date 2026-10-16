"""Correo en Gmail: lo que habla con la API (descargar adjuntos, enviar, crear borradores, listar hilos).

El análisis de un mensaje (cabeceras, texto, adjuntos, enrutamiento) son funciones puras que viven en
`anka.dominio.correo`; aquí solo está lo que necesita un cliente de Gmail.
"""

from __future__ import annotations
from anka.infra.clientes_google import ClienteGmail

import base64

from anka.dominio.configuracion import Configuracion
from anka.dominio.analisis import Adjunto
from anka.dominio.attachments import hermanos_de_shapefile
from anka.infra.attachments import (
    attachment_reviewed_sections, build_attachment_evidence, extract_attachment_text,
)
from anka.dominio.correo import (
    attachment_parts, decode_websafe, is_inline_signature_image, normalized_address, reply_email,
)
from anka.infra.logging_setup import get_logger

log = get_logger(__name__)


def descargar_parte(gmail: ClienteGmail, message_id: str, part: dict) -> bytes:
    """Los bytes de un adjunto: vienen en el mensaje si son pequeños y, si no, se piden a Gmail (vacío si no hay)."""
    encoded = part.get("data")
    if not encoded and part.get("attachment_id"):
        encoded = gmail.users().messages().attachments().get(
            userId="me", messageId=message_id, id=part["attachment_id"]).execute().get("data")
    return decode_websafe(encoded) if encoded else b""


def companeros_del_shapefile(gmail: ClienteGmail, message_id: str, partes: list[dict], hermanos: dict[str, str]
                             ) -> dict[str, bytes]:
    """Contenido de los archivos hermanos (`.shx`, `.dbf`, `.prj`, `.cpg`) de un `.shp`, por extensión."""
    por_nombre = {parte["filename"]: parte for parte in partes}
    return {extension: contenido for extension, nombre in hermanos.items()
            if (contenido := descargar_parte(gmail, message_id, por_nombre[nombre]))}


def extract_message_attachments(gmail: ClienteGmail, message: dict) -> list[Adjunto]:
    """Descarga y lee los adjuntos de un mensaje; nunca falla: un adjunto ilegible queda «bloqueado» con su motivo."""
    results: list[Adjunto] = []
    partes = [p for p in attachment_parts(message.get("payload", {})) if not is_inline_signature_image(p)]
    grupos = hermanos_de_shapefile([p["filename"] for p in partes])
    absorbidos = {nombre: shp for shp, hermanos in grupos.items() for nombre in hermanos.values()}
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
        if part["filename"] in absorbidos:
            record.update({"status": "ignorado", "text": "",
                           "reason": f"parte del shapefile {absorbidos[part['filename']]}; se lee junto con él"})
            results.append(record)
            continue
        try:
            contenido = descargar_parte(gmail, message["id"], part)
            if not contenido:
                text, reason = "", "Gmail no devolvió contenido para el adjunto"
            else:
                text, reason = extract_attachment_text(
                    part["filename"], part["mime_type"], contenido,
                    companeros_del_shapefile(gmail, message["id"], partes, grupos.get(part["filename"], {})))
        except Exception as exc:  # un adjunto roto no debe frenar el análisis del correo
            log.warning("Adjunto no legible en el mensaje %s: %s", message.get("id"), type(exc).__name__)
            text, reason = "", f"error al descargar o leer: {exc}"
        record.update({
            "status": "bloqueado" if not text else "lectura_parcial" if reason else "extraido",
            "text": text,
            "reason": reason,
            "coverage": "no_legible" if not text else "parcial" if reason else "completa",
            "extracted_chars": len(text),
            "reviewed_sections": attachment_reviewed_sections(text) if text else [],
            "evidence": build_attachment_evidence(text) if text else [],
        })
        results.append(record)
    return results


def own_addresses(config: Configuracion, gmail: ClienteGmail) -> set[str]:
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
    gmail: ClienteGmail, thread: dict, recipient: str, body: str,
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
    gmail: ClienteGmail, thread: dict, recipient: str, body: str,
    own_email_addresses: list[str] | set[str] | None = None,
) -> str:
    """Guarda la respuesta como borrador de Gmail en el hilo y devuelve el id del borrador."""
    email = reply_email(thread, recipient, body, own_email_addresses)
    raw = base64.urlsafe_b64encode(email.as_bytes()).decode("ascii")
    result = gmail.users().drafts().create(
        userId="me", body={"message": {"raw": raw, "threadId": thread["id"]}}
    ).execute()
    return str(result["id"])


def candidate_thread_versions(gmail: ClienteGmail, query: str) -> dict[str, str]:
    """Hilo → `historyId` de los hilos que coinciden con la búsqueda: cambia cuando el hilo cambia en Gmail (T-40)."""
    versiones: dict[str, str] = {}
    page_token = None
    while True:
        result = gmail.users().threads().list(userId="me", q=query, maxResults=500, pageToken=page_token).execute()
        versiones.update({t["id"]: str(t.get("historyId", "")) for t in result.get("threads", [])})
        page_token = result.get("nextPageToken")
        if not page_token:
            return versiones


def es_error_de_cuota(exc: Exception) -> bool:
    """Si Gmail rechazó la petición por cuota o por exceso de peticiones (403 de cuota o 429): no sirve insistir ya."""
    from googleapiclient.errors import HttpError

    if not isinstance(exc, HttpError):
        return False
    texto = str(exc)
    return exc.resp.status == 429 or (exc.resp.status == 403 and any(
        marca in texto for marca in ("Quota exceeded", "rateLimitExceeded", "userRateLimitExceeded")))


def candidate_thread_ids(gmail: ClienteGmail, query: str) -> list[str]:
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
