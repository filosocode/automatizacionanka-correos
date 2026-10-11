"""Verifica la lectura de Work contra los adjuntos que Gmail tiene en el hilo y deja su texto local para T-38 (T-41)."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from anka.dominio.attachments import MAX_ATTACHMENT_BYTES
from anka.dominio.correo import adjuntos_recibidos, decode_websafe, inbound_messages, message_text
from anka.dominio.verificacion_de_evidencia import avisos_de, comprobar_tabla
from anka.dominio.work_contract import verificar_adjuntos
from anka.infra import instantaneas_de_manifiesto
from anka.infra.attachments import extract_attachment_text_for_work_packet
from anka.infra.logging_setup import get_logger

log = get_logger(__name__)
VERSION_DE_TEXTOS = "gmail-v1"   # sube cuando cambia cómo se extrae el texto: invalida los textos guardados


def _huella_del_hilo(mensajes: list[dict], adjuntos: list[dict]) -> str:
    """Identifica lo que hay en el hilo: si llega un mensaje o un adjunto nuevo, los textos guardados dejan de valer."""
    contenido = {"v": VERSION_DE_TEXTOS, "mensajes": [m["id"] for m in mensajes],
                 "adjuntos": sorted((a["message_id"], a["filename"], a["size"]) for a in adjuntos)}
    return hashlib.sha256(json.dumps(contenido, sort_keys=True).encode()).hexdigest()


def _contenido(gmail: Any, adjunto: dict) -> bytes:
    """Los bytes del adjunto: vienen en el mensaje si son pequeños y, si no, se piden a Gmail."""
    codificado = adjunto.get("data")
    if not codificado and adjunto.get("attachment_id"):
        codificado = gmail.users().messages().attachments().get(
            userId="me", messageId=adjunto["message_id"], id=adjunto["attachment_id"]).execute().get("data")
    return decode_websafe(codificado) if codificado else b""


def _textos_del_hilo(gmail: Any, thread: dict, adjuntos: list[dict], propias: set[str],
                     directorio: Path) -> dict | None:
    """El texto del correo y de cada adjunto, extraído con Python y guardado solo en este equipo (T-38).

    Se baja y se lee cada adjunto una sola vez: si el hilo no cambió se reutiliza lo guardado. Si un adjunto no se
    puede bajar, el resultado se devuelve sin guardarlo para reintentar en la siguiente revisión.
    """
    mensajes = [m for m in inbound_messages(thread, propias) if "DRAFT" not in m.get("labelIds", [])]
    huella = _huella_del_hilo(mensajes, adjuntos)
    guardados = instantaneas_de_manifiesto.cargar_textos(directorio, thread["id"], huella)
    if guardados is not None:
        return guardados
    leidos: list[dict] = []
    completo = True
    vistos: set[tuple[str, int]] = set()
    for adjunto in adjuntos:
        clave = (adjunto["filename"], adjunto["size"])
        if clave in vistos:
            continue
        vistos.add(clave)
        texto, motivo, huella_del_archivo = "", "", ""
        try:
            contenido = _contenido(gmail, adjunto)
            if contenido and len(contenido) <= MAX_ATTACHMENT_BYTES:
                huella_del_archivo = hashlib.sha256(contenido).hexdigest()
                texto, motivo = extract_attachment_text_for_work_packet(
                    adjunto["filename"], adjunto["mime_type"], contenido)
            elif not contenido:
                completo = False
        except Exception:   # un adjunto que no baja no frena la verificación: la tabla queda «no comprobable»
            completo = False
            log.warning("No se pudo bajar un adjunto de %s para comprobar la tabla", thread["id"], exc_info=True)
        leidos.append({"filename": adjunto["filename"], "source_id": huella_del_archivo,
                       "coverage": "no_legible" if not texto else "parcial" if motivo else "completa", "text": texto})
    correo = "\n\n".join(message_text(m.get("payload", {})) for m in mensajes)
    if completo:
        instantaneas_de_manifiesto.guardar_textos(directorio, thread["id"], huella, correo, leidos)
        return instantaneas_de_manifiesto.cargar_textos(directorio, thread["id"], huella)
    return {"thread_id": thread["id"], "revision": huella, "correo": correo, "adjuntos": leidos, "incompleto": True}


def verificar_lectura(gmail: Any, payload: dict, propias: set[str], directorio: Path) -> tuple[list[str], bool]:
    """Verifica la lectura que Work declaró (T-41) y comprueba la tabla contra el texto local (T-38).

    Devuelve (avisos para el caso, si se pudo consultar Gmail). Si Gmail no responde (cuota, red) el borrador no se
    descarta: queda «sin verificar» y quien llama lo reintenta en la siguiente revisión.
    """
    try:
        thread = gmail.users().threads().get(userId="me", id=payload["thread_id"], format="full").execute()
        adjuntos = adjuntos_recibidos(thread, propias)
    except Exception:   # cuota, red o permisos: no es culpa del borrador
        log.warning("No se pudo consultar el hilo %s para verificar la lectura", payload.get("thread_id"),
                    exc_info=True)
        return verificar_adjuntos(payload, None), False
    avisos = verificar_adjuntos(payload, adjuntos)
    try:
        resultado = comprobar_tabla(payload, _textos_del_hilo(gmail, thread, adjuntos, propias, directorio))
    except Exception:   # la comprobación de la tabla es un apoyo: nunca debe frenar la importación
        log.warning("No se pudo comprobar la tabla de %s contra sus textos", payload.get("thread_id"), exc_info=True)
        resultado = None
    if resultado:
        payload["evidence_check"] = resultado
        avisos += avisos_de(resultado)
    return avisos, True
