"""Motor de plantillas del dashboard.

Las plantillas viven en `templates/` con escape automático: todo valor interpolado se
escapa salvo que sea `Markup`. Por eso los fragmentos ya renderizados (`render()` devuelve
`Markup`) se pueden anidar en otras plantillas sin escaparse dos veces, y los datos que
vienen del correo de un cliente nunca llegan al HTML sin escapar.
"""

from __future__ import annotations

from typing import Any
import urllib.parse
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, StrictUndefined
from markupsafe import Markup

from anka.dominio.caso import explicar_propuesta
from anka.web import estaticos

_env = Environment(
    loader=FileSystemLoader(Path(__file__).with_name("templates")),
    autoescape=True,
    undefined=StrictUndefined,
    keep_trailing_newline=False,
)
_env.filters["urlquote"] = urllib.parse.quote
_env.globals["url_del_estilo"] = estaticos.url_del_estilo     # el CSS compartido va aparte, con huella (U-48)
_env.globals["explicar_propuesta"] = explicar_propuesta       # por qué Work propone cada cambio (T-61)
_env.globals["url_del_script"] = estaticos.url_del_guion     # igual el JavaScript: nada va en línea (U-49)


def render(template: str, **context: Any) -> Markup:
    """Renderiza una plantilla Jinja2 con escape automático y devuelve HTML seguro (`Markup`)."""
    return Markup(_env.get_template(template).render(**context))
