"""El correo de la tarjeta de decisión (U-113), tal como lo leen las pruebas de la pantalla del caso."""


def cuadro_de(texto: str) -> str:
    """Lo que hay dentro de la caja del correo (`id="correo-unico"`), sin la etiqueta."""
    inicio = texto.index('id="correo-unico"')
    return texto[texto.index(">", inicio) + 1:texto.index("</textarea>", inicio)]


def preguntas_de(texto: str) -> list[str]:
    """Las preguntas numeradas del correo de la tarjeta de decisión (una por línea «1. …»)."""
    import re
    return [m.group(1) for linea in cuadro_de(texto).splitlines() if (m := re.match(r"\s*\d+\.\s+(.+)", linea))]
