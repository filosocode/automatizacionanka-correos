"""Reserva duradera y reconciliación de correos enviados desde el dashboard."""

from __future__ import annotations
from anka.infra.clientes_google import ClienteGmail

import hashlib
from datetime import datetime, timedelta
from typing import Any
from uuid import uuid4

from anka.dominio.errores import ErrorDeValidacion
from anka.dominio.reloj import ahora, ahora_iso, instante
from anka.infra.store import lectura, transaccion

# Un envío ya confirmado solo sirve para rechazar repeticiones recientes; conservarlos para siempre haría crecer la
# tabla sin beneficio. Los inciertos (`pending`) nunca se purgan: siguen bloqueando hasta reconciliarse.
RETENCION_DIAS = 90
# Un envío `pending` más reciente que esto aún puede estar saliendo (otra pestaña, otro proceso): se bloquea otro envío
# del mismo caso. Más viejo, ya no está «en curso»: se reconcilia con Gmail (U-59).
MINUTOS_EN_CURSO = 2
MENSAJE_ENVIO_EN_CURSO = ("Ya hay un envío en curso para este caso. Espera un momento y recarga la página, o revisa "
                          "Gmail (carpeta Enviados) para ver si el correo salió. No se envió otro.")


def _en_curso(creado: str, referencia: datetime | None = None) -> bool:
    """¿La reserva se hizo hace menos de `MINUTOS_EN_CURSO`? Una fecha ilegible cuenta como reciente (lo seguro)."""
    momento = instante(creado)
    if momento is None:
        return True
    return (referencia or ahora()) - momento < timedelta(minutes=MINUTOS_EN_CURSO)


_ahora = ahora


def purgar_confirmados(db: Any, ahora: datetime | None = None) -> int:
    """Borra los envíos confirmados con más de RETENCION_DIAS días; devuelve cuántos."""
    limite = ((ahora or _ahora()) - timedelta(days=RETENCION_DIAS)).isoformat()
    return db.execute("DELETE FROM mail_operations WHERE state='sent' AND created_at < ?", (limite,)).rowcount


def reservar(hilo: str, destinatario: str, texto: str) -> tuple[str, str]:
    """Reserva una intención antes de contactar Gmail; devuelve clave y Message-ID."""
    clave = hashlib.sha256((hilo + "\0" + destinatario.casefold() + "\0" + texto).encode()).hexdigest()
    with transaccion() as db:          # candado desde la primera lectura: dos procesos no reservan a la vez
        purgar_confirmados(db)
        previo = db.execute("SELECT * FROM mail_operations WHERE operation_key=?", (clave,)).fetchone()
        if previo:
            if previo["state"] == "sent":
                raise ValueError("Este mismo correo ya se envió a este destinatario. No se envió de nuevo.")
            raise ValueError(
                "El envío está en curso o su resultado es incierto. Reintenta para consultar Gmail; "
                "no se enviará otro correo mientras no se confirme el anterior."
            )
        # Otro texto del mismo caso (dos pestañas, una coma editada) no es el «mismo envío» para la clave, pero sí
        # puede ser un segundo correo al cliente: mientras haya uno reciente sin confirmar, no se reserva otro (U-59).
        for otro in db.execute("SELECT created_at FROM mail_operations WHERE thread_id=? AND state='pending'", (hilo,)):
            if _en_curso(otro["created_at"]):
                raise ErrorDeValidacion(MENSAJE_ENVIO_EN_CURSO)
        message_id = f"<anka-{uuid4().hex}@anka.local>"
        db.execute(
            "INSERT INTO mail_operations (operation_key, thread_id, recipient, message_id, state, gmail_id, created_at,"
            " error, body) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                clave,
                hilo,
                destinatario.casefold(),
                message_id,
                "pending",
                "",
                ahora_iso(),
                "",
                texto,
            ),
        )
    return clave, message_id          # ya confirmado: la reserva es durable antes de contactar a Gmail


def pendiente(hilo: str, destinatario: str, texto: str) -> dict | None:
    """Devuelve una operación anterior de la misma intención, incluso tras reiniciar."""
    clave = hashlib.sha256((hilo + "\0" + destinatario.casefold() + "\0" + texto).encode()).hexdigest()
    with lectura() as db:
        row = db.execute("SELECT * FROM mail_operations WHERE operation_key=?", (clave,)).fetchone()
        return dict(row) if row else None


def confirmar(clave: str, gmail_id: str) -> None:
    """Confirma el identificador remoto; puede repetirse al recuperar un fallo de guardado."""
    with transaccion(inmediata=False) as db:
        db.execute("UPDATE mail_operations SET state='sent', gmail_id=? WHERE operation_key=?", (gmail_id, clave))


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
    with transaccion(inmediata=False) as db:
        db.execute("DELETE FROM mail_operations WHERE operation_key=? AND state='pending'", (clave,))


def reconciliar(gmail: ClienteGmail, operacion: dict) -> str:
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


def pendientes_del_hilo(hilo: str) -> list[dict]:
    """Los envíos sin confirmar de un caso (del más antiguo al más nuevo)."""
    with lectura() as db:
        return [dict(fila) for fila in db.execute(
            "SELECT * FROM mail_operations WHERE thread_id=? AND state='pending' ORDER BY created_at", (hilo,))]


def pendientes_sin_resolver(hilo: str | None = None) -> list[dict]:
    """Los envíos sin confirmar con más de `MINUTOS_EN_CURSO` minutos: ya no pueden estar «saliendo», hay que
    averiguar en Gmail qué pasó. Con `hilo`, solo los de ese caso."""
    with lectura() as db:
        filas = db.execute("SELECT * FROM mail_operations WHERE state='pending'" + (" AND thread_id=?" if hilo else "")
                           + " ORDER BY created_at", (hilo,) if hilo else ()).fetchall()
    referencia = ahora()
    return [dict(fila) for fila in filas if not _en_curso(fila["created_at"], referencia)]


def hay_envios_en_curso() -> bool:
    """¿Hay algún envío reservado hace menos de `MINUTOS_EN_CURSO` minutos? (el lanzador no reinicia mientras tanto)."""
    with lectura() as db:
        filas = db.execute("SELECT created_at FROM mail_operations WHERE state='pending'").fetchall()
    referencia = ahora()
    return any(_en_curso(fila["created_at"], referencia) for fila in filas)
