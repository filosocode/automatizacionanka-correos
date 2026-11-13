"""El panel «Decisiones que puedes deshacer» de la pestaña Historial del caso (U-57).

Reúne lo que una persona decidió y todavía se puede revertir sin consecuencias: un mensaje marcado «No es un cambio»
y la propuesta de Work que se dejó de lado con «Dejar mi tabla». Cada botón lleva a una acción que deja huella.
"""

from __future__ import annotations

from markupsafe import Markup

from anka.dominio.analisis import Analisis
from anka.servicios.change_control import cambios_descartados
from anka.web.templating import render


def panel_deshacer(thread_id: str, analysis: Analisis) -> Markup:
    """El panel, o nada si no hay ninguna decisión que se pueda deshacer."""
    cambios = cambios_descartados(thread_id)
    descartada = analysis.get("propuesta_descartada")
    propuesta = len(descartada) if isinstance(descartada, list) and not analysis.get("pending_model_activities") else 0
    if not cambios and not propuesta:
        return Markup("")
    return render("deshacer.html", thread_id=thread_id, cambios=cambios, propuesta=propuesta)
