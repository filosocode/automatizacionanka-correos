"""Reserva duradera y reconciliación de correos enviados desde el dashboard."""

from __future__ import annotations

import hashlib
from datetime import datetime, timedelta
from typing import Any
from uuid import uuid4

from anka.infra.store import connect

# Un envío ya confirmado solo sirve para rechazar repeticiones recientes; conservarlos para siempre haría crecer la
# tabla sin beneficio. Los inciertos (`pending`) nunca se purgan: siguen bloqueando hasta reconciliarse.
RETENCION_DIAS = 90


def purgar_confirmados(db: Any, ahora: datetime | None = None) -> int:
    """Borra los envíos confirmados con más de RETENCION_DIAS días; devuelve cuántos."""
    limite = ((ahora or datetime.now().astimezone()) - timedelta(days=RETENCION_DIAS)).isoformat()
    return db.execute("DELETE FROM mail_operations WHERE state='sent' AND created_at < ?", (limite,)).rowcount


def reservar(hilo: str, destinatario: str, texto: str) -> tuple[str, str]:
    """Reserva una intención antes de contactar Gmail; devuelve clave y Message-ID."""
    clave = hashlib.sha256((hilo + "\0" + destinatario.casefold() + "\0" + texto).encode()).hexdigest()
    db = connect()
    try:
        db.execute("BEGIN IMMEDIATE")
        purgar_confirmados(db)
        previo = db.execute("SELECT * FROM mail_operations WHERE operation_key=?", (clave,)).fetchone()
        if previo:
            if previo["state"] == "sent":
                raise ValueError("Este mismo correo ya se envió a este destinatario. No se envió de nuevo.")
            raise ValueError(
                "El envío está en curso o su resultado es incierto. Reintenta para consultar Gmail; "
                "no se enviará otro correo mientras no se confirme el anterior."
            )
        message_id = f"<anka-{uuid4().hex}@anka.local>"
        db.execute(
            "INSERT INTO mail_operations VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                clave,
                hilo,
                destinatario.casefold(),
                message_id,
                "pending",
                "",
                datetime.now().astimezone().isoformat(),
                "",
            ),
        )
        db.commit()
        return clave, message_id
    finally:
        db.close()


def pendiente(hilo: str, destinatario: str, texto: str) -> dict | None:
    """Devuelve una operación anterior de la misma intención, incluso tras reiniciar."""
    clave = hashlib.sha256((hilo + "\0" + destinatario.casefold() + "\0" + texto).encode()).hexdigest()
    db = connect()
    try:
        row = db.execute("SELECT * FROM mail_operations WHERE operation_key=?", (clave,)).fetchone()
        return dict(row) if row else None
    finally:
        db.close()


def confirmar(clave: str, gmail_id: str) -> None:
    """Confirma el identificador remoto; puede repetirse al recuperar un fallo de guardado."""
    db = connect()
    try:
        db.execute("UPDATE mail_operations SET state='sent', gmail_id=? WHERE operation_key=?", (gmail_id, clave))
        db.commit()
    finally:
        db.close()


def fallo_definitivo(error: Exception) -> bool:
    """Indica si Gmail rechazó la petición sin procesarla (4xx salvo 408), y por tanto no hay correo enviado.

    Un timeout, un corte de red o un 5xx son inciertos: el mensaje pudo salir, así que la reserva se conserva.
    """
    estado = getattr(getattr(error, "resp", None), "status", None)
    if not isinstance(estado, int):
        return False
    return 400 <= estado < 500 and estado != 408


def liberar(clave: str) -> None:
    """Quita la reserva de un envío que Gmail rechazó con certeza, para poder corregirlo y reintentar."""
    db = connect()
    try:
        db.execute("DELETE FROM mail_operations WHERE operation_key=? AND state='pending'", (clave,))
        db.commit()
    finally:
        db.close()


def reconciliar(gmail: Any, operacion: dict) -> str:
    """Busca el Message-ID reservado; la ausencia nunca autoriza un reenvío automático."""
    if operacion["gmail_id"]:
        return str(operacion["gmail_id"])
    result = (
        gmail.users()
        .messages()
        .list(
            userId="me",
            q="in:sent rfc822msgid:" + operacion["message_id"].strip("<>"),
            maxResults=2,
        )
        .execute()
    )
    for match in result.get("messages", []):
        if match.get("threadId") == operacion["thread_id"]:
            return str(match["id"])
    return ""
