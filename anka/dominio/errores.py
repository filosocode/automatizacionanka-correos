"""Errores propios de Anka.

`ErrorDeAnka` es algo que la persona puede corregir (falta una configuración, un dato inválido, una acción que no
procede): su mensaje se muestra tal cual. La línea de comandos lo convierte en salida con código 1 y el dashboard
en un aviso. Los servicios nunca terminan el programa por sí mismos (antes lanzaban `SystemExit`).
"""

from __future__ import annotations


class ErrorDeAnka(Exception):
    """Un problema con explicación para la persona; `str(error)` es el texto que se le muestra."""
