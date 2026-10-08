"""Errores propios de Anka.

`ErrorDeAnka` es algo que la persona puede corregir (falta una configuración, un dato inválido, una acción que no
procede): su mensaje se muestra tal cual. La línea de comandos lo convierte en salida con código 1 y el dashboard
en un aviso. Los servicios nunca terminan el programa por sí mismos (antes lanzaban `SystemExit`).
"""

from __future__ import annotations


class ErrorDeAnka(Exception):
    """Un problema con explicación para la persona; `str(error)` es el texto que se le muestra."""


class ModoNoPermitido(ErrorDeAnka):
    """El modo activo (chatgpt_work) no permite que el bot local lea Gmail: dos orquestadores duplicarían el trabajo."""


class ContextoInsuficiente(ErrorDeAnka):
    """El correo y sus adjuntos completos no caben en la ventana del modelo; no se analiza recortado (T-24)."""


class AutorizacionDeGoogle(ErrorDeAnka):
    """La autorización de Google falta, venció o fue revocada: hay que reautorizar (el dashboard ofrece el botón)."""

    MENSAJE = ("La autorización de Google venció o falta. Pulsa «Reautorizar Google» en el dashboard "
               "o ejecuta: python app.py auth")

    def __init__(self, motivo: str = "") -> None:
        super().__init__(f"{motivo + ' ' if motivo else ''}{self.MENSAJE}".strip())
