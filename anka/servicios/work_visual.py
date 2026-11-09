"""Publica evidencia de PDF y KML/KMZ antes de exponer la hoja a Work."""

import hashlib
import json
from collections import Counter
from typing import Any

from anka.dominio.attachments import MAX_ATTACHMENT_BYTES
from anka.dominio.correo import attachment_parts
from anka.dominio.errores import ErrorDeAnka
from anka.infra.attachments import extract_attachment_text_for_work_packet
from anka.infra.clientes_google import ClienteGmail
from anka.infra.drive_exchange import DriveExchange
from anka.infra.mail import descargar_parte
from anka.infra.vistas_pdf import vistas_pdf


def _es_pdf(adjunto: dict) -> bool:
    return (adjunto.get("mime_type", "").casefold() == "application/pdf"
            or adjunto.get("filename", "").casefold().endswith(".pdf"))


def _es_geografico(adjunto: dict) -> bool:
    return (adjunto.get('filename', '').casefold().endswith(('.kml', '.kmz'))
            or adjunto.get('mime_type', '').casefold() in
            {'application/vnd.google-earth.kml+xml', 'application/vnd.google-earth.kmz'})


def publicar_vistas(gmail: ClienteGmail, drive: DriveExchange, parent: str, trabajo: dict[str, Any]) -> str:
    """Devuelve el ID del manifiesto, o vacío si no hay adjuntos compatibles."""
    esperados = [adjunto for adjunto in trabajo['referencia'].get('attachments', [])
                 if _es_pdf(adjunto) or _es_geografico(adjunto)]
    if not esperados:
        return ""

    job_id, thread_id, message_id = (trabajo[campo] for campo in ("job_id", "thread_id", "message_id"))
    mensaje = gmail.users().messages().get(userId="me", id=message_id, format="full").execute()
    if mensaje.get("id") != message_id or mensaje.get("threadId") != thread_id:
        raise ErrorDeAnka("Gmail devolvió un mensaje distinto del trabajo.")
    partes = [parte for parte in attachment_parts(mensaje.get('payload', {}))
              if _es_pdf(parte) or _es_geografico(parte)]
    # Gmail puede emitir otro identificador de descarga al consultar el mismo mensaje.
    referencias = Counter((adjunto["filename"], adjunto.get("size_bytes") or 0)
                           for adjunto in esperados)
    recibidos = Counter((parte["filename"], parte.get("size") or 0) for parte in partes)
    if referencias != recibidos:
        raise ErrorDeAnka("Los adjuntos del mensaje no coinciden con la referencia del trabajo.")

    clave = f"visual-v1:{job_id}"
    carpeta = drive.folder(parent, clave, f"Vistas {job_id}")["id"]
    adjuntos = [_publicar_pdf(gmail, drive, carpeta, message_id, indice, parte)
                for indice, parte in enumerate(partes, 1) if _es_pdf(parte)]
    manifiesto = {"format": "anka-work-visual-v1", "job_id": job_id, "thread_id": thread_id,
                  "message_id": message_id, "attachments": adjuntos}
    geografico = [_leer_geografia(gmail, message_id, parte) for parte in partes if _es_geografico(parte)]
    if geografico:
        manifiesto['lectura_geografica'] = geografico
    return drive.put(carpeta, "manifest-v1", "VISTAS_PDF.json",
                     json.dumps(manifiesto, ensure_ascii=False).encode("utf-8"),
                     "application/json", replace=True)["id"]


def _leer_geografia(gmail: ClienteGmail, message_id: str, parte: dict) -> dict:
    registro = {'filename': parte['filename'], 'size_bytes': parte['size'], 'texto': ''}
    if parte['size'] > MAX_ATTACHMENT_BYTES:
        return {**registro, 'motivo': 'el adjunto excede el límite de tamaño'}
    contenido = descargar_parte(gmail, message_id, parte)
    if not contenido or (parte['size'] and len(contenido) != parte['size']):
        raise ErrorDeAnka('Gmail no devolvió el adjunto geográfico completo.')
    if len(contenido) > MAX_ATTACHMENT_BYTES:
        return {**registro, 'motivo': 'el adjunto excede el límite de tamaño'}
    texto, motivo = extract_attachment_text_for_work_packet(parte['filename'], parte['mime_type'], contenido)
    if len(texto) > 24_000:
        texto = texto[:24_000]
        motivo = (motivo + '; ' if motivo else '') + 'el resumen geográfico excede el límite del manifiesto'
    return {**registro, 'size_bytes': len(contenido), 'sha256': hashlib.sha256(contenido).hexdigest(),
            'texto': texto, 'motivo': motivo}


def _publicar_pdf(gmail: ClienteGmail, drive: DriveExchange, carpeta: str, message_id: str,
                  indice: int, parte: dict) -> dict:
    registro = {"filename": parte["filename"], "gmail_attachment_id": parte.get("attachment_id"),
                "size_bytes": parte["size"], "total_paginas": None, "paginas": []}
    if parte["size"] and parte["size"] > MAX_ATTACHMENT_BYTES:
        return {**registro, "motivo": "el PDF excede el límite de tamaño"}
    contenido = descargar_parte(gmail, message_id, parte)
    if not contenido or (parte["size"] and len(contenido) != parte["size"]):
        raise ErrorDeAnka("Gmail no devolvió el PDF completo.")
    if len(contenido) > MAX_ATTACHMENT_BYTES:
        return {**registro, "motivo": "el PDF excede el límite de tamaño"}
    huella = hashlib.sha256(contenido).hexdigest()
    archivos = {}

    def guardar(pagina: int, png: bytes) -> str:
        nombre = f"{indice:02d}-{huella[:12]}-p{pagina:03d}.png"
        clave = f"{indice}:{huella}:{pagina}:{hashlib.sha256(png).hexdigest()}"
        try:
            archivo = drive.put(carpeta, clave, nombre, png, "image/png")
        except Exception as exc:
            # Un fallo de Drive debe llegar al reintento de publicación, no al diagnóstico del render.
            raise ErrorDeAnka("No se pudo publicar una vista del PDF en Drive.") from exc
        archivos[nombre] = {"drive_file_id": archivo["id"], "size_bytes": len(png)}
        return nombre

    vistas = vistas_pdf(contenido, guardar)
    for pagina in vistas["paginas"]:
        pagina.update(archivos[pagina["archivo"]])
    return {**registro, "sha256": huella, **vistas}
