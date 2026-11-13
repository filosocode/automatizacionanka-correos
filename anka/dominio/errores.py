"""Errores propios de Anka.

`ErrorDeAnka` es algo que la persona puede corregir (falta una configuración, un dato inválido, una acción que no
procede): su mensaje se muestra tal cual. La línea de comandos lo convierte en salida con código 1 y el dashboard
en un aviso. Los servicios nunca terminan el programa por sí mismos (antes lanzaban `SystemExit`).
"""

from __future__ import annotations

import secrets


def codigo_de_error() -> str:
    """Código corto de un error inesperado: se muestra a la persona y se anota en el registro con la traza (U-53)."""
    return secrets.token_hex(4)


class ErrorDeAnka(Exception):
    """Un problema con explicación para la persona; `str(error)` es el texto que se le muestra."""


class ErrorDeValidacion(ErrorDeAnka, ValueError):
    """Un dato o una acción que no procede, explicado para la persona («Escribe algo antes de guardar.»; U-53).

    Es un `ErrorDeAnka` —su texto se muestra tal cual— y, por compatibilidad, también un `ValueError`: quien ya
    capturaba `ValueError` sigue capturándolo. Un `ValueError` que NO sea de esta clase es un fallo de Python o de una
    librería (un bug): su texto no se muestra y queda registrado con su traza."""


class ModoNoPermitido(ErrorDeAnka):
    """El modo activo (chatgpt_work) no permite que el bot local lea Gmail: dos orquestadores duplicarían el trabajo."""


class SalidaDesbordada(ErrorDeAnka):
    """El modelo llegó al tope de salida sin terminar su JSON: suele ser un bucle de repetición."""


class ContextoInsuficiente(ErrorDeAnka):
    """El correo y sus adjuntos completos no caben en la ventana del modelo; no se analiza recortado (T-24)."""


class AutorizacionDeGoogle(ErrorDeAnka):
    """La autorización de Google falta, venció o fue revocada: hay que reautorizar (el dashboard ofrece el botón)."""

    MENSAJE = ("La autorización de Google venció o falta. Pulsa «Volver a conectar Google» en el dashboard "
               "o ejecuta: python app.py auth")

    def __init__(self, motivo: str = "") -> None:
        super().__init__(f"{motivo + ' ' if motivo else ''}{self.MENSAJE}".strip())
