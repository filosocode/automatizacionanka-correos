"""El caso mínimo para un borrador de Work sin análisis válido, con solo lo verificable en Gmail (T-36)."""

from __future__ import annotations

from datetime import datetime
from email.utils import parseaddr

from anka.dominio.configuracion import Modo
from anka.dominio.correo import header

ESTADO_MINIMO = "Requiere revisión técnica interna"
SIN_ASUNTO = "no indicado"
MAX_ASUNTO = 200


def _asunto(valor: str) -> str:
    """El asunto del correo sin los «Re:» / «RV:» del principio ni espacios de más, con tope."""
    texto = " ".join(valor.split())
    while texto[:3].casefold() in ("re:", "rv:") or texto[:4].casefold() == "fwd:":
        texto = texto.split(":", 1)[1].strip()
    return texto[:MAX_ASUNTO] or SIN_ASUNTO


def caso_minimo(thread_id: str, mensaje: dict, motivo: str) -> dict | None:
    """El borrador de dashboard con solo lo que dice el último mensaje recibido del hilo; `None` si no es utilizable.

    No inventa nada: remitente, asunto y fecha salen del encabezado del correo y de la marca de Gmail; todo lo demás
    queda vacío y el caso pasa a revisión técnica interna con el motivo del rechazo (campos del contrato, nunca
    contenido del cliente) para que la persona sepa qué pedirle a Work.
    """
    nombre, correo = parseaddr(header(mensaje, "From"))
    marca = int(mensaje.get("internalDate") or 0)
    if not correo or "@" not in correo or not mensaje.get("id") or marca <= 0:
        return None
    pendiente = ("Work dejó un análisis que el programa no pudo leer (" + motivo.rstrip(". ") + "). Pídele a Work que "
                 "analice de nuevo esta conversación; mientras tanto, lo único verificado es el remitente, "
                 "el asunto y la fecha.")
    return {
        "thread_id": thread_id, "message_id": mensaje["id"], "client_email": correo.lower(),
        "client_name": nombre.strip(), "project_name": _asunto(header(mensaje, "Subject")),
        "status": ESTADO_MINIMO, "engine": Modo.CHATGPT_WORK.value, "analysis_complete": False,
        "summary": "Caso creado con los datos del correo: Work no entregó un análisis válido para esta conversación.",
        "received_at": datetime.fromtimestamp(marca / 1000).astimezone().isoformat(),
        "received_at_source": "Gmail internalDate", "_source_internal_date": marca,
        "activities": [], "fields": [], "missing_data": [], "question_audit": [],
        "scope_dependencies": [{"description": pendiente, "source": "puente de Work", "status": "pendiente"}],
        "_avisos_del_puente": ["Caso mínimo (T-36): sin análisis de Work. " + pendiente],
    }
