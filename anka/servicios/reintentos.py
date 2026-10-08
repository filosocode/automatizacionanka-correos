"""Registro de los hilos que no se pudieron analizar y de cuándo reintentarlos (T-26)."""

from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta

from anka.dominio.reintentos import RETENCION_DIAS, espera_tras


def _ahora() -> datetime:
    return datetime.now().astimezone()


def registrar_fallo(
    db: sqlite3.Connection, thread_id: str, motivo: str, ahora: datetime | None = None,
) -> tuple[int, datetime]:
    """Anota un fallo más del hilo; devuelve cuántos lleva y cuándo se reintenta. Purga los registros viejos."""
    ahora = ahora or _ahora()
    previo = db.execute("SELECT failures, first_at FROM analysis_failures WHERE thread_id=?", (thread_id,)).fetchone()
    fallos = (previo["failures"] if previo else 0) + 1
    proximo = ahora + espera_tras(fallos)
    db.execute(
        """INSERT INTO analysis_failures (thread_id, failures, last_error, first_at, last_at, next_retry_at)
           VALUES (?, ?, ?, ?, ?, ?)
           ON CONFLICT(thread_id) DO UPDATE SET failures=excluded.failures, last_error=excluded.last_error,
               last_at=excluded.last_at, next_retry_at=excluded.next_retry_at""",
        (thread_id, fallos, motivo[:500], previo["first_at"] if previo else ahora.isoformat(timespec="seconds"),
         ahora.isoformat(timespec="seconds"), proximo.isoformat(timespec="seconds")),
    )
    db.execute("DELETE FROM analysis_failures WHERE last_at < ?",
               ((ahora - timedelta(days=RETENCION_DIAS)).isoformat(timespec="seconds"),))
    db.commit()
    return fallos, proximo


def proximo_intento(db: sqlite3.Connection, thread_id: str, ahora: datetime | None = None) -> datetime | None:
    """Cuándo se podrá reintentar el hilo si todavía debe esperar; `None` si puede analizarse ya."""
    fila = db.execute("SELECT next_retry_at FROM analysis_failures WHERE thread_id=?", (thread_id,)).fetchone()
    if not fila:
        return None
    try:
        proximo = datetime.fromisoformat(fila["next_retry_at"])
    except ValueError:
        return None  # un registro dañado nunca debe bloquear un correo
    return proximo if proximo > (ahora or _ahora()) else None


def limpiar(db: sqlite3.Connection, thread_id: str) -> None:
    """El hilo se analizó bien: olvida sus fallos."""
    if db.execute("DELETE FROM analysis_failures WHERE thread_id=?", (thread_id,)).rowcount:
        db.commit()
