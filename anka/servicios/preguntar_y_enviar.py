"""«Preguntar al cliente» en un solo paso (U-113): registra la decisión con las preguntas marcadas y envía el correo.

Antes la persona decidía («Redactar el correo») y, en otra pantalla, enviaba. Aquí las dos cosas son una sola acción,
con las mismas garantías: nada sale sin que una persona lo haya revisado y confirmado (R8, la confirmación vive en la
pantalla), el texto se comprueba antes de tocar nada y un caso que cambió desde que se abrió la pantalla no se envía.
"""

from __future__ import annotations

from anka.dominio.correo import comprobar_texto_para_el_cliente
from anka.dominio.errores import ErrorDeAnka, ErrorDeValidacion
from anka.servicios.case_actions import save_dashboard_draft, send_dashboard_reply
from anka.servicios.quotes import get_quote, resolve_technical_review


def preguntar_y_enviar(thread_id: str, correo: str, destinatario: str, preguntas: str, *, estado_visto: str = "",
                       borrador: bool = False) -> str:
    """Decide «preguntar al cliente» con `preguntas` (una por línea) y envía `correo` (o lo guarda como borrador).

    Si el envío falla, la decisión ya quedó guardada: el aviso lo dice y el texto vuelve a la caja del correo."""
    if not preguntas.strip():
        raise ErrorDeValidacion("Marca «Preguntar» en lo que falta: sin una pregunta no hay nada que enviar.")
    if not correo.strip():
        raise ErrorDeValidacion("Escribe el correo antes de enviar.")
    comprobar_texto_para_el_cliente(correo.strip())      # antes de decidir nada: un texto rechazado no cambia el caso
    actual = get_quote(thread_id)
    if not actual:
        raise ErrorDeValidacion("Este caso ya no existe. Vuelve a Inicio.")
    if estado_visto and actual.marca_de_estado != estado_visto:
        raise ErrorDeValidacion("Este caso cambió desde que abriste la pantalla, así que el correo no se envió. Lo que "
                                "escribiste sigue aquí: recarga, revisa lo nuevo y vuelve a enviar.")
    resolve_technical_review(thread_id, "ask_client", preguntas)
    try:
        if borrador:
            return save_dashboard_draft(thread_id, correo, destinatario)
        return send_dashboard_reply(thread_id, correo, destinatario)
    except ErrorDeAnka as error:
        raise ErrorDeValidacion(f"Tu decisión de preguntar al cliente quedó guardada, pero el correo no salió. {error}"
                                ) from error
