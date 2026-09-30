"""Estados del ciclo de vida de una solicitud, definidos en un solo lugar.

QuoteStatus hereda de str: sus miembros se comparan y se guardan en SQLite
como el texto de siempre, así que las filas existentes siguen siendo válidas.
"""

from __future__ import annotations

from enum import Enum


class QuoteStatus(str, Enum):
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

    def __str__(self) -> str:
        return self.value


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
