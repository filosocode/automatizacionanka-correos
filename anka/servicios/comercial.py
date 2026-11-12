"""El nombre de quien trabaja con el sistema (U-112): sale en el respaldo, en el historial y junto al criterio técnico.

Mientras no haya usuarios por persona (puesta en línea, D-204) es el nombre de esta instalación, guardado en
`config.json`. Sin nombre no se puede registrar un criterio técnico.
"""

from __future__ import annotations

from anka.dominio.errores import ErrorDeAnka, ErrorDeValidacion
from anka.infra.settings import load_config, write_config

MAXIMO = 60
MINIMO = 2


def nombre_del_comercial() -> str:
    """El nombre guardado, o vacío si todavía no se escribió o la configuración no se puede leer."""
    try:
        return " ".join(str(load_config().get("comercial", "")).split())
    except (ErrorDeAnka, OSError, ValueError):
        return ""


def poner_nombre(nombre: str) -> str:
    """Guarda el nombre y devuelve el aviso para la persona."""
    limpio = " ".join(nombre.split())
    if len(limpio) < MINIMO:
        raise ErrorDeValidacion("Escribe tu nombre (al menos dos letras).")
    if len(limpio) > MAXIMO:
        raise ErrorDeValidacion(f"Tu nombre es muy largo: usa hasta {MAXIMO} letras.")
    config = dict(load_config())
    config["comercial"] = limpio
    write_config(config)
    return f"Listo, {limpio}: tu nombre quedará en lo que registres."
