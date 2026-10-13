"""Estados del ciclo de vida de una solicitud, definidos en un solo lugar.

QuoteStatus es un StrEnum: sus miembros se comparan y se guardan en SQLite
como el texto de siempre, así que las filas existentes siguen siendo válidas.
"""

from __future__ import annotations

import unicodedata
from enum import StrEnum


class QuoteStatus(StrEnum):
    """Estados internos de un caso (la etiqueta que ve la persona sale de `STATUS_LABELS`)."""
    REVIEW_PENDING = "review_pending"  # faltan datos; hay borrador de preguntas por revisar
    READY_REVIEW = "ready_review"      # alcance completo; pendiente de revisión interna
    REVIEW = "review"                  # requiere revisión técnica manual
    SENDING = "sending"                # envío en curso o interrumpido
    WAITING = "waiting"                # preguntas enviadas; espera respuesta del cliente
    DRAFT_CREATED = "draft_created"    # borrador creado en Gmail
    RESPONSE_SENT = "response_sent"    # respuesta enviada desde el dashboard
    READY = "ready"                    # carpeta de proyecto creada
    MISSING = "missing"                # el hilo ya no existe en Gmail
    IGNORED = "ignored"                # no es solicitud comercial


# Estados en los que ya no se debe redactar ni enviar correo al cliente.
CLOSED_STATUSES = frozenset({
    QuoteStatus.IGNORED, QuoteStatus.MISSING, QuoteStatus.READY, QuoteStatus.RESPONSE_SENT,
})
# Estados que esperan una acción humana.
ACTION_STATUSES = frozenset({
    QuoteStatus.REVIEW_PENDING, QuoteStatus.READY_REVIEW, QuoteStatus.SENDING,
    QuoteStatus.REVIEW, QuoteStatus.MISSING,
})
# Estados que aparecen en la cola de revisión.
REVIEW_QUEUE_STATUSES = (
    QuoteStatus.REVIEW_PENDING, QuoteStatus.READY_REVIEW,
    QuoteStatus.SENDING, QuoteStatus.REVIEW,
)


def sql_in(*statuses: QuoteStatus) -> str:
    """Lista `('a', 'b')` para una cláusula IN; solo admite miembros del enum."""
    return "(" + ", ".join(f"'{QuoteStatus(s).value}'" for s in statuses) + ")"


# Etiquetas de estado que escribe ChatGPT Work en el JSON del borrador.
WORK_STATUS_LABELS = (
    "Nueva solicitud", "En análisis", "Faltan datos", "Requiere aclaraciones del cliente",
    "Revisión técnica", "Requiere revisión técnica interna", "Borrador preparado",
    "Alcance completo", "Información suficiente para revisión interna", "Descartada",
)


def _plain(text: object) -> str:
    """El texto para comparar estados: sin tildes, mayúsculas, espacios de más ni caracteres invisibles.

    NFKC lleva las formas de ancho completo («Ｆａｌｔａｎ») y las ligaduras a letras normales; los caracteres de
    formato (ancho cero, marcas de dirección) se quitan. Un valor que no es texto no se reconoce (falla segura).
    """
    if not isinstance(text, str):
        return ""
    compatible = unicodedata.normalize("NFKC", text)
    decomposed = unicodedata.normalize("NFD", compatible)
    kept = "".join(c for c in decomposed if unicodedata.category(c) not in ("Mn", "Cf"))
    return " ".join(kept.casefold().split())


def canonical_work_status(value: object) -> str | None:
    """Etiqueta oficial de un estado de Work, sin importar tildes, mayúsculas ni espacios.

    Work puede escribir «Informacion suficiente para revision interna» sin tildes: es el mismo estado.
    """
    wanted = _plain(value)
    for label in WORK_STATUS_LABELS:
        if _plain(label) == wanted:
            return label
    return None


# Variantes evidentes que Work ha escrito (o podría escribir) en lugar de una etiqueta oficial (T-33). Solo llevan a
# estados conservadores, donde una persona decide o se piden datos: nunca a uno que avance el caso ni a «Descartada».
_SINONIMOS_POR_ESTADO = {
    "Requiere revisión técnica interna": (
        "requiere revisión humana", "revisión humana", "requiere revisión manual", "revisión manual",
        "requiere revisión interna", "revisión interna", "requiere revisión técnica", "revisión técnica interna",
        "requiere revisión de una persona",
    ),
    "Faltan datos": (
        "faltan datos críticos", "datos faltantes", "falta información", "información faltante",
        "faltan datos del cliente",
    ),
    "Requiere aclaraciones del cliente": (
        "requiere aclaración del cliente", "requiere aclaraciones", "requiere aclaración", "aclaración del cliente",
    ),
}
ESTADOS_CONSERVADORES = frozenset(_SINONIMOS_POR_ESTADO)
SINONIMOS_DE_ESTADO = {
    _plain(texto): etiqueta for etiqueta, textos in _SINONIMOS_POR_ESTADO.items() for texto in textos
}


def reconocer_estado_de_work(valor: object) -> tuple[str | None, str]:
    """(etiqueta oficial, aviso) para el estado que escribió Work; (None, '') si no se reconoce.

    Una etiqueta oficial se reconoce sin aviso (aunque venga sin tildes). Un sinónimo evidente de un estado conservador
    se acepta con un aviso para que el caso diga qué escribió Work y cómo se tomó (T-33).
    """
    oficial = canonical_work_status(valor)
    if oficial is not None:
        return oficial, ""
    etiqueta = SINONIMOS_DE_ESTADO.get(_plain(valor))
    if etiqueta is None:
        return None, ""
    escrito = " ".join(str(valor).split())[:80]
    aviso = f"Work escribió el estado «{escrito}», que no es uno de los permitidos; se tomó como «{etiqueta}»."
    return etiqueta, aviso
