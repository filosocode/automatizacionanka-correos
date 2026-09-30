"""Motor de plantillas del dashboard.

Las plantillas viven en `templates/` con escape automático: todo valor interpolado se
escapa salvo que sea `Markup`. Por eso los fragmentos ya renderizados (`render()` devuelve
`Markup`) se pueden anidar en otras plantillas sin escaparse dos veces, y los datos que
vienen del correo de un cliente nunca llegan al HTML sin escapar.
"""

from __future__ import annotations

import urllib.parse
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, StrictUndefined
from markupsafe import Markup

_env = Environment(
    loader=FileSystemLoader(Path(__file__).with_name("templates")),
    autoescape=True,
    undefined=StrictUndefined,
    keep_trailing_newline=False,
)
_env.filters["urlquote"] = urllib.parse.quote


def render(template: str, **context) -> Markup:
    return Markup(_env.get_template(template).render(**context))
