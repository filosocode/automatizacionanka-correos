"""El correo con las preguntas marcadas, para que la pantalla lo escriba mientras se marcan casillas (U-113)."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from anka.dominio.analisis import Analisis
from anka.dominio.caso import Caso
from anka.dominio.preguntas import como_peticion, separar
from anka.servicios.quotes import case_conversation, get_quote
from anka.web.views import correo_de_las_preguntas, ronda_del_caso


def correo_de_preguntas_marcadas(thread_id: str, preguntas: Sequence[str]) -> dict[str, Any] | None:
    """El correo con las `preguntas` marcadas y cada una tal como saldrá en él (U-113); `None` si el caso no existe.

    Lo usa la pantalla para escribir el correo mientras se marcan casillas, con la misma plantilla que el envío."""
    review = get_quote(thread_id)
    if not review:
        return None
    analysis = review["analysis"]
    _, segunda_ronda = ronda_del_caso(review, analysis, case_conversation(thread_id, 50))
    limpias = [p for p in (" ".join(str(x).split()) for x in preguntas) if p][:40]
    cuerpo = correo_de_las_preguntas(review, analysis, limpias, segunda_ronda) if limpias else ""
    return {"cuerpo": cuerpo, "lineas": [como_peticion(p, quitar_verbo=False) for p in limpias]}


def asunto_de_respuesta(thread_id: str) -> str:
    """El asunto con que saldría la respuesta («Re: …» del último correo del cliente), para confirmar el envío."""
    recibido = next((m for m in case_conversation(thread_id) if m.get("direction") == "received"), None)
    asunto = str((recibido or {}).get("subject") or "").strip()
    if not asunto:
        return ""
    return asunto if asunto.lower().startswith("re:") else f"Re: {asunto}"


def correo_de_la_decision(review: Caso, analysis: Analisis, cuadro: str, segunda_ronda: bool,
                          escrito: str = "") -> tuple[str, int]:
    """El correo con que arranca la tarjeta de decisión y cuántas preguntas lleva: lo que la persona había escrito, o
    el escrito con las preguntas que ya vienen marcadas (U-113)."""
    marcadas = separar(cuadro)
    sin_escribir = correo_de_las_preguntas(review, analysis, marcadas, segunda_ronda) if marcadas else ""
    return escrito or sin_escribir, len(marcadas)
