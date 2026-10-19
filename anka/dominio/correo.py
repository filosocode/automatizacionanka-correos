"""El correo como dato: leer cabeceras, texto y adjuntos de un mensaje de Gmail y armar una respuesta.

Son funciones puras sobre los diccionarios que devuelve la API de Gmail: no llaman a Gmail ni escriben nada.
Lo que sí habla con Gmail (enviar, crear borradores, descargar adjuntos) vive en `anka.infra.mail`.
"""

from __future__ import annotations

from anka.dominio.configuracion import Configuracion
from anka.dominio.errores import ErrorDeValidacion
from anka.dominio.work_contract import marcadores_internos
from anka.dominio.analisis import Enrutamiento

import base64
import binascii
import html
import math
import re
from datetime import datetime
from email.message import EmailMessage
from email.utils import getaddresses, parseaddr

FORWARDED_SUBJECT_PATTERN = re.compile(r"^(?:fwd?|rv|reenv):\s*", re.IGNORECASE)

FORWARDED_BODY_PATTERN = re.compile(
    r"^-{2,}\s*(?:mensaje reenviado|forwarded message)\s*-{2,}$",
    re.IGNORECASE | re.MULTILINE,
)

_QUOTED_REPLY_START = re.compile(
    # Cabecera real de cita: «El <fecha o correo> … escribió:» con los dos puntos al final de la línea (U-39).
    r"(?:\n\s*(?:El|On)\s[^\n]{0,200}\n?[^\n]{0,160}?(?:escribi[óo]|wrote)\s*:[ \t]*(?=\n|$))"
    r"|(?:\n\s*>[^\n]*\n\s*>)"          # al menos dos líneas seguidas con «>»; una suelta no es una cita
    r"|(?:\n\s*-{2,}\s*(?:Original Message|Mensaje original)\s*-{2,})"
    r"|(?:\n\s*De:\s[^\n]+\n\s*(?:Enviado|Fecha)\s*:)", re.IGNORECASE)
_CABECERA_DE_CITA = re.compile(r"\n\s*(?:El|On)\s", re.IGNORECASE)
_FECHA_O_CORREO = re.compile(r"\d|@")


def _inicios_de_cita(text: str) -> list[re.Match[str]]:
    """Dónde empieza una cita real. «El … escribió:» solo cuenta si trae una fecha o un correo: una frase como
    «El cliente nos escribió:» es texto de la persona."""
    return [c for c in _QUOTED_REPLY_START.finditer("\n" + text)
            if not (_CABECERA_DE_CITA.match(c.group(0)) and not _FECHA_O_CORREO.search(c.group(0)))]


def split_quoted_reply(body: str) -> tuple[str, str]:
    """Separa el texto nuevo del historial de correo citado, si lo hay."""
    text = (body or "").replace("\r\n", "\n").replace("▎", "").strip()
    found = _inicios_de_cita(text)
    if not found or found[0].start() <= 1:
        return text, ""
    cut = found[0].start() - 1
    return text[:cut].strip(), text[cut:].strip()


def reply_body_without_quoted_history(body: str) -> str:
    """Devuelve solo el texto nuevo de un BORRADOR DE WORK, para no reenviar el historial del hilo (U-39).

    Solo se usa al importar o mostrar lo que Work propone: el texto que una persona escribe nunca pasa por aquí.
    """
    return split_quoted_reply(body)[0]


def incluye_mensaje_citado(body: str) -> bool:
    """¿El texto trae un mensaje citado de verdad (cabecera «El … escribió:» o varias líneas con «>»)?

    Solo sirve para avisar antes de enviar: nunca recorta nada (U-39).
    """
    return bool(_inicios_de_cita((body or "").replace("\r\n", "\n").replace("▎", "").strip()))


def _dict(value: object) -> dict:
    """Un objeto de la respuesta de Gmail; `{}` si falta o llegó otra cosa (`None`, un texto)."""
    return value if isinstance(value, dict) else {}


def _list(value: object) -> list:
    """Una lista de la respuesta de Gmail; `[]` si falta o llegó otra cosa."""
    return value if isinstance(value, list) else []


def _text(value: object) -> str:
    """Un texto de la respuesta de Gmail; `''` si falta o no es texto."""
    return value if isinstance(value, str) else ""


def _header_value(headers: object, name: str) -> str:
    """Valor de la cabecera `name` en una lista de cabeceras de Gmail, tolerando entradas incompletas."""
    wanted = name.casefold()
    for item in _list(headers):
        if isinstance(item, dict) and _text(item.get("name")).casefold() == wanted:
            return _text(item.get("value"))
    return ""


def header(message: dict, name: str) -> str:
    """Valor de una cabecera del mensaje ('' si no existe), sin distinguir mayúsculas."""
    return _header_value(_dict(_dict(message).get("payload")).get("headers"), name)


def decode_websafe(data: str) -> bytes:
    """Decodifica el base64 «seguro para URL» que usa Gmail (con o sin relleno)."""
    return base64.urlsafe_b64decode(data + "=" * (-len(data) % 4))


# Lo que va dentro de <script>, <style> o un comentario no es texto del correo: se quita con su contenido.
_HTML_INVISIBLE = re.compile(r"<(script|style)\b[^>]*>.*?(?:</\1\s*>|$)|<!--.*?(?:-->|$)", re.IGNORECASE | re.DOTALL)
# Una etiqueta empieza con una letra, «/» o «!» justo después de «<»: un «<» suelto («a < b») sigue siendo texto.
_HTML_TAG = re.compile(r"</?[A-Za-z!][^<>]*>")


def html_to_text(markup: str) -> str:
    """El texto visible de un HTML: sin scripts, estilos, comentarios ni etiquetas, con las entidades resueltas."""
    return html.unescape(_HTML_TAG.sub(" ", _HTML_INVISIBLE.sub(" ", markup)))


def _decoded_body(part: dict) -> str | None:
    """El cuerpo de una parte en texto, o `None` si no trae datos o su base64 no es válido."""
    data = _dict(part.get("body")).get("data")
    if not data or not isinstance(data, str):
        return None
    try:
        return decode_websafe(data).decode("utf-8", errors="replace")
    except (binascii.Error, ValueError):
        return None


def message_text(payload: dict) -> str:
    """Texto del mensaje: el cuerpo en texto plano y, si no hay, el HTML sin etiquetas."""
    plain = []
    markup = []

    def visit(part: dict) -> None:
        children = _list(part.get("parts"))
        if children:
            for child in children:
                if isinstance(child, dict):
                    visit(child)
            return
        decoded = _decoded_body(part)
        if decoded is None:
            return
        mime = _text(part.get("mimeType"))
        if mime == "text/plain":
            plain.append(decoded)
        elif mime == "text/html":
            markup.append(html_to_text(decoded))

    visit(_dict(payload))
    return "\n".join(plain or markup).strip()


def has_attachments(payload: dict) -> bool:
    """¿El mensaje (o alguna de sus partes) trae archivos adjuntos?"""
    payload = _dict(payload)
    if payload.get("filename") or _dict(payload.get("body")).get("attachmentId"):
        return True
    return any(has_attachments(part) for part in _list(payload.get("parts")))


def _part_header(part: dict, name: str) -> str:
    return _header_value(part.get("headers"), name)


def _size(value: object) -> int:
    """El tamaño que informa Gmail en bytes; 0 si falta o no es un número."""
    if isinstance(value, bool):
        return 0
    if isinstance(value, int):
        return max(0, value)
    if isinstance(value, float):
        return max(0, int(value)) if math.isfinite(value) else 0
    if isinstance(value, str) and re.fullmatch(r"\s*\d{1,15}\s*", value, re.ASCII):
        return int(value)
    return 0


def attachment_parts(payload: dict) -> list[dict]:
    """Todas las partes del mensaje que son un archivo adjunto (también las anidadas)."""
    payload = _dict(payload)
    body = _dict(payload.get("body"))
    parts = []
    filename = _text(payload.get("filename")).strip()
    attachment_id = body.get("attachmentId")
    if filename or attachment_id:
        parts.append({
            "filename": filename or "adjunto-sin-nombre",
            "mime_type": _text(payload.get("mimeType")) or "application/octet-stream",
            "attachment_id": attachment_id,
            "size": _size(body.get("size")),
            "data": body.get("data"),
            "disposition": _part_header(payload, "Content-Disposition"),
            "content_id": _part_header(payload, "Content-ID"),
        })
    for part in _list(payload.get("parts")):
        parts.extend(attachment_parts(part))
    return parts


def is_inline_signature_image(part: dict) -> bool:
    """¿Es una imagen incrustada (logo de firma) y no un documento que mandó el cliente?"""
    if not part.get("mime_type", "").casefold().startswith("image/"):
        return False
    disposition = part.get("disposition", "").casefold()
    if disposition.startswith("attachment"):
        # Outlook y otros clientes ponen Content-ID también a las fotos adjuntas: si el remitente la marcó como
        # adjunto, es un documento del cliente (p. ej. «foto_referencia_1.jpg») y no un logo de firma (T-41)
        return False
    return disposition.startswith("inline") or bool(part.get("content_id"))


def attachment_names(payload: dict) -> list[str]:
    """Nombres de los adjuntos reales (sin los logos incrustados en firmas)."""
    return [
        part["filename"] for part in attachment_parts(payload)
        if not is_inline_signature_image(part)
    ]


def adjuntos_recibidos(thread: dict, own_addresses: list[str] | set[str] | None = None) -> list[dict]:
    """Los adjuntos reales que el cliente mandó en un hilo: mensaje, nombre, tamaño y tipo, sin los logos de firma.

    Es lo que Gmail dice que hay (T-41): no incluye borradores ni mensajes propios, y no trae el contenido.
    """
    return [{"message_id": _text(message.get("id")), "filename": part["filename"], "size": _size(part.get("size")),
             "mime_type": part["mime_type"], "attachment_id": part.get("attachment_id"), "data": part.get("data")}
            for message in inbound_messages(thread, own_addresses) if "DRAFT" not in _list(message.get("labelIds"))
            for part in attachment_parts(_dict(message.get("payload"))) if not is_inline_signature_image(part)]


def imagenes_incrustadas(thread: dict, own_addresses: list[str] | set[str] | None = None) -> list[dict]:
    """Las imágenes incrustadas en el cuerpo (logos de firma, fotos pegadas) de los mensajes recibidos del hilo.

    No cuentan como adjuntos por leer, pero existen en Gmail: si Work reporta una, no es un adjunto «que Gmail no
    tiene» (T-41).
    """
    return [{"message_id": _text(message.get("id")), "filename": part["filename"], "size": _size(part.get("size"))}
            for message in inbound_messages(thread, own_addresses) if "DRAFT" not in _list(message.get("labelIds"))
            for part in attachment_parts(_dict(message.get("payload"))) if is_inline_signature_image(part)]


def _instante(message: dict) -> int:
    """`internalDate` de Gmail (milisegundos) como entero; 0 si falta o no es un número."""
    value = message.get("internalDate")
    if isinstance(value, bool):
        return 0
    if isinstance(value, int):
        return value
    if isinstance(value, str) and re.fullmatch(r"\s*-?\d{1,20}\s*", value, re.ASCII):
        return int(value)
    return 0


def message_datetime(message: dict) -> str:
    """Fecha y hora de llegada («AAAA-MM-DD HH:MM», hora local) o la cabecera Date si falta."""
    value = _instante(message)
    if value:
        try:
            return datetime.fromtimestamp(value / 1000).strftime("%Y-%m-%d %H:%M")
        except (ValueError, OSError, OverflowError):
            # una fecha fuera de rango no se puede mostrar: se usa la cabecera Date, igual que si faltara
            return header(message, "Date") or "no indicado"
    return header(message, "Date") or "no indicado"


def normalized_address(value: str) -> str:
    """Dirección de correo en minúsculas y sin el nombre, para compararla."""
    return parseaddr(value)[1].strip().casefold()


def valid_email_address(value: str) -> bool:
    """¿Parece una dirección de correo completa (usuario@dominio.ext)?"""
    address = parseaddr(value or "")[1]
    return bool(re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", address))


def inbound_messages(thread: dict, own_addresses: list[str] | set[str] | None = None) -> list[dict]:
    """Mensajes de otras personas en el hilo (no los enviados por la cuenta propia), del más viejo al más nuevo."""
    own = {normalized_address(address) for address in (own_addresses or []) if address}

    def is_external(message: dict) -> bool:
        if "SENT" in _list(message.get("labelIds")):
            return False
        senders = {address.casefold() for _, address in getaddresses([
            header(message, "From"), header(message, "Sender"),
        ]) if address}
        return not bool(own & senders)

    return sorted(
        (m for m in _list(_dict(thread).get("messages")) if isinstance(m, dict) and is_external(m)),
        key=_instante,
    )


def forwarded_routing(message: dict, config: Configuracion) -> Enrutamiento:
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


def comprobar_texto_para_el_cliente(body: str, permitir_seccion_interna: bool = False) -> None:
    """Rechaza un texto con delimitadores internos de Work: no debe guardarse como borrador ni enviarse (T-59)."""
    encontrados = marcadores_internos(body, permitir_seccion_interna)
    if encontrados:
        raise ErrorDeValidacion(
            "El correo contiene texto interno de Work (" + "; ".join(encontrados) + ") y no se guardó ni se envió. "
            "Quita esa parte —el cliente solo debe leer la respuesta— y vuelve a intentarlo.")


def reply_email(
    thread: dict, recipient: str, body: str,
    own_email_addresses: list[str] | set[str] | None = None,
    permitir_seccion_interna: bool = False,
) -> EmailMessage:
    """Arma la respuesta al último mensaje del cliente, en el mismo hilo (asunto «Re:», cabeceras de hilo).

    Es el punto por el que pasa todo correo que Anka guarda o envía: aquí se rechaza el texto interno de Work (T-59).
    """
    comprobar_texto_para_el_cliente(body, permitir_seccion_interna)
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
