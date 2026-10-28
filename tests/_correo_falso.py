"""Doble de `reply_email` para las pruebas que no necesitan un hilo real: un mensaje con el texto pedido."""

from email.message import EmailMessage


def correo_falso(thread, recipient, body, own_email_addresses=None, **_):
    email = EmailMessage()
    email["To"] = recipient
    email.set_content(body)
    return email
