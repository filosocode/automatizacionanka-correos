"""Copia local del manifiesto y del texto extraído que publica el preparador, para verificar a Work (T-37, T-38)."""

from __future__ import annotations

import json
import re
from datetime import datetime
from pathlib import Path
from typing import Any

from anka.infra.logging_setup import get_logger

log = get_logger(__name__)
MAX_TEXTO_POR_MENSAJE = 200_000   # caracteres de un mensaje guardado aparte (T-69); más allá se marca `truncado`
MAX_TEXTO_LOCAL = 8_000_000   # caracteres por hilo; más allá la comprobación de fuentes queda como «no comprobable»


def _ruta(directorio: Path, thread_id: str) -> Path:
    return directorio / (re.sub(r"[^\w-]", "_", thread_id) + ".json")


def _segmentos(registros: object, archivo: str = "") -> list[dict]:
    """Solo identificador y nombre de cada segmento publicado: lo que Work puede citar, sin contenido del cliente.

    Con `archivo` (el adjunto del que sale el segmento) lo anota, para poder decir qué archivo quedó sin leer.
    """
    segmentos = [{"id": str(r["id"]), "name": str(r.get("name", ""))}
                 for r in registros if isinstance(r, dict) and r.get("id")] if isinstance(registros, list) else []
    return [{**s, "archivo": archivo} for s in segmentos] if archivo else segmentos


def guardar(directorio: Path, manifiesto: dict) -> None:
    """Escribe de forma atómica la instantánea del manifiesto vigente; un fallo del disco no frena la preparación."""
    try:
        instantanea = {
            "thread_id": manifiesto["thread_id"], "revision": manifiesto["revision"],
            "message_ids": list(manifiesto.get("message_ids") or []),
            "email_segments": _segmentos(manifiesto.get("email_segments")),
            "attachment_segments": [segmento for adjunto in manifiesto.get("attachments") or []
                                    if isinstance(adjunto, dict)
                                    for segmento in _segmentos(adjunto.get("text_segments"),
                                                               str(adjunto.get("filename") or ""))],
            "saved_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        }
        directorio.mkdir(parents=True, exist_ok=True)
        destino = _ruta(directorio, str(manifiesto["thread_id"]))
        temporal = destino.with_suffix(".tmp")
        temporal.write_text(json.dumps(instantanea, ensure_ascii=False, indent=1), encoding="utf-8")
        temporal.replace(destino)
    except (OSError, KeyError, TypeError):
        log.warning("No se pudo guardar la instantánea del manifiesto de %s", manifiesto.get("thread_id"),
                    exc_info=True)


def cargar(directorio: Path, thread_id: str) -> dict | None:
    """La instantánea del hilo, o `None` si no hay (preparador apagado, expediente anterior) o está dañada."""
    try:
        valor = json.loads(_ruta(directorio, thread_id).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return valor if isinstance(valor, dict) and valor.get("thread_id") == thread_id else None


def _ruta_textos(directorio: Path, thread_id: str) -> Path:
    return directorio / (re.sub(r"[^\w-]", "_", thread_id) + ".textos.json")


def guardar_textos(directorio: Path, thread_id: str, revision: str, correo: str, adjuntos: list[dict],
                   mensajes: list[dict] | None = None) -> None:
    """Guarda en este equipo el texto extraído del expediente (correo y adjuntos) para comprobar la tabla (T-38).

    Cada adjunto lleva `filename`, `source_id`, `coverage` y `text` (vacío si no hay texto). Si el total supera el
    tope se recorta y se marca `incompleto`: la comprobación lo trata como no concluyente, nunca como diferencia.
    `mensajes` (T-69) conserva por separado cada mensaje recibido (`message_id`, `fecha`, `remitente`, `direccion`,
    `texto`, `truncado`) para saber quién dijo qué y cuándo; si se omite, el hilo se guarda como antes.
    """
    restante = MAX_TEXTO_LOCAL
    recortado = False
    guardados = []
    for adjunto in adjuntos:
        texto = str(adjunto.get("text") or "")
        if len(texto) > restante:
            texto, recortado = texto[:restante], True
        restante -= len(texto)
        guardados.append({**adjunto, "text": texto})
    texto_correo = correo[:max(restante, 0)]
    recortado = recortado or len(texto_correo) < len(correo)
    try:
        directorio.mkdir(parents=True, exist_ok=True)
        destino = _ruta_textos(directorio, thread_id)
        temporal = destino.with_suffix(".tmp")
        contenido: dict[str, Any] = {"thread_id": thread_id, "revision": revision, "correo": texto_correo,
                                     "adjuntos": guardados, "incompleto": recortado}
        if mensajes is not None:
            contenido["mensajes"] = _mensajes_acotados(mensajes)
        temporal.write_text(json.dumps(contenido, ensure_ascii=False), encoding="utf-8")
        temporal.replace(destino)
    except OSError:
        log.warning("No se pudo guardar el texto extraído de %s", thread_id, exc_info=True)


def _mensajes_acotados(mensajes: list[dict]) -> list[dict]:
    """Cada mensaje con su texto recortado al tope por mensaje; el recorte queda marcado (`truncado`)."""
    salida = []
    for mensaje in mensajes:
        texto = str(mensaje.get("texto") or "")
        salida.append({**mensaje, "texto": texto[:MAX_TEXTO_POR_MENSAJE],
                       "truncado": bool(mensaje.get("truncado")) or len(texto) > MAX_TEXTO_POR_MENSAJE})
    return salida


def cargar_textos(directorio: Path, thread_id: str, revision: str | None = None) -> dict | None:
    """El texto extraído del hilo, o `None` si no hay, está dañado o es de otra revisión que la pedida."""
    try:
        valor = json.loads(_ruta_textos(directorio, thread_id).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(valor, dict) or valor.get("thread_id") != thread_id:
        return None
    return valor if revision is None or valor.get("revision") == revision else None
