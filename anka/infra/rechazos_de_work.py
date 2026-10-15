"""Cuenta por día los análisis de Work que no se pudieron leer y recuerda cuáles se dejaron para después (U-41).

Solo guarda el motivo en lenguaje claro, la causa, los nombres de campo y la fecha: nunca el contenido del cliente ni
el JSON (T-36). Es una métrica de la calidad de lo que Work manda, no un archivo de errores.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from datetime import date, datetime

from anka.infra.logging_setup import get_logger
from anka.infra.store import connect

log = get_logger(__name__)
UMBRAL_POR_DIA = 3      # más de tantos en un día pide revisar el prompt de Work
MAX_MOTIVO = 300


def _ahora() -> datetime:
    return datetime.now().astimezone()


def registrar(draft_id: str, message_id: str, thread_id: str, causa: str, campos: Sequence[str], motivo: str,
              cuando: datetime | None = None) -> None:
    """Anota un análisis rechazado, una sola vez por borrador y mensaje. Nunca lanza: es una métrica."""
    cuando = cuando or _ahora()
    try:
        db = connect()
        try:
            db.execute(
                "INSERT OR IGNORE INTO work_rejections"
                " (draft_id, message_id, thread_id, day, at, cause, fields, reason) VALUES (?,?,?,?,?,?,?,?)",
                (draft_id, message_id, thread_id, cuando.date().isoformat(), cuando.isoformat(timespec="seconds"),
                 causa, json.dumps(list(campos), ensure_ascii=False), motivo[:MAX_MOTIVO]))
            db.commit()
        finally:
            db.close()
    except Exception as exc:   # la métrica no debe frenar al puente
        log.warning("No se pudo anotar el rechazo de Work (%s)", type(exc).__name__, exc_info=True)


def cuenta_del_dia(dia: date | None = None) -> int:
    """Cuántos análisis de Work se rechazaron ese día (hoy por omisión); 0 si no se puede leer."""
    try:
        db = connect()
        try:
            fila = db.execute("SELECT COUNT(*) FROM work_rejections WHERE day=?",
                              ((dia or _ahora().date()).isoformat(),)).fetchone()
        finally:
            db.close()
        return int(fila[0])
    except Exception as exc:
        log.warning("No se pudo contar los rechazos de Work (%s)", type(exc).__name__, exc_info=True)
        return 0


def alerta_del_dia() -> str:
    """El aviso de que Work manda demasiados análisis con errores hoy, o '' si no pasa del umbral."""
    cuenta = cuenta_del_dia()
    if cuenta <= UMBRAL_POR_DIA:
        return ""
    return f"Work está mandando análisis con errores: {cuenta} hoy. Pide revisar el prompt."


def dejar_para_despues(draft_id: str, message_id: str) -> bool:
    """Marca el aviso de un rechazo como pospuesto; True si existía y estaba a la vista."""
    db = connect()
    try:
        cambio = db.execute(
            "UPDATE work_rejections SET dismissed_at=? WHERE draft_id=? AND message_id=? AND dismissed_at IS NULL",
            (_ahora().isoformat(timespec="seconds"), draft_id, message_id)).rowcount
        db.commit()
    finally:
        db.close()
    return bool(cambio)


def pospuestos() -> set[tuple[str, str]]:
    """Los (borrador, mensaje) cuyo aviso se dejó para después; vacío si no se puede leer."""
    try:
        db = connect()
        try:
            filas = db.execute("SELECT draft_id, message_id FROM work_rejections WHERE dismissed_at IS NOT NULL")
            return {(fila[0], fila[1]) for fila in filas}
        finally:
            db.close()
    except Exception as exc:
        log.warning("No se pudieron leer los avisos pospuestos (%s)", type(exc).__name__, exc_info=True)
        return set()
