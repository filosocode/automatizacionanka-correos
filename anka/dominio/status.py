"""Estados del ciclo de vida de una solicitud, definidos en un solo lugar.

QuoteStatus es un StrEnum: sus miembros se comparan y se guardan en SQLite
como el texto de siempre, así que las filas existentes siguen siendo válidas.
"""

from __future__ import annotations

import unicodedata
from dataclasses import dataclass
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


@dataclass(frozen=True)
class InfoDeEstado:
    """Todo lo que se sabe de un estado, en un solo lugar (U-52): añadir un estado es añadir una fila a `ESTADOS`."""

    etiqueta: str                  # lo que ve la persona en la insignia («Correo por revisar»)
    etapa: str                     # la etapa de la lista cuando nada más la decide (`case_stage`)
    requiere_accion: bool          # esa etapa pide algo a una persona
    cerrado: bool = False          # ya no se redacta ni se envía correo al cliente
    espera_accion: bool = False    # espera una acción humana
    en_cola: bool = False          # aparece en la cola de revisión
    protegido: bool = False        # un análisis posterior nunca lo mueve (U-47)
    protegido_del_mismo_mensaje: bool = False   # solo un mensaje nuevo del cliente lo mueve (U-47)
    sugiere_respuesta: bool = False             # genera la novedad «Respuesta sugerida lista para revisar»
    etapa_si_acuse: str = ""       # su etapa cuando el último correo del cliente era un simple acuse


# La tabla única de estados. El orden es el de las listas y las consultas; `spec/transiciones.json` dice a qué estados
# se puede pasar desde cada uno. Un estado nuevo se añade aquí y allí, y una prueba exige que no falte ninguno.
ESTADOS: dict[QuoteStatus, InfoDeEstado] = {
    QuoteStatus.REVIEW_PENDING: InfoDeEstado(
        "Correo por revisar", "Correo por revisar", True, espera_accion=True, en_cola=True, sugiere_respuesta=True),
    QuoteStatus.READY_REVIEW: InfoDeEstado(
        "Información validada", "Información validada", True, espera_accion=True, en_cola=True,
        sugiere_respuesta=True),
    QuoteStatus.SENDING: InfoDeEstado(
        "Enviando", "El correo no salió", True, espera_accion=True, en_cola=True, protegido=True),
    QuoteStatus.REVIEW: InfoDeEstado(
        "Por decidir", "Por decidir", True, espera_accion=True, en_cola=True, sugiere_respuesta=True),
    QuoteStatus.WAITING: InfoDeEstado("Esperando al cliente", "Esperando al cliente", False),
    QuoteStatus.DRAFT_CREATED: InfoDeEstado(
        "Correo por revisar", "Correo por revisar", True, sugiere_respuesta=True),
    QuoteStatus.RESPONSE_SENT: InfoDeEstado(
        "Respuesta enviada", "Esperando al cliente", False, cerrado=True, protegido_del_mismo_mensaje=True,
        etapa_si_acuse="Respuesta enviada · sin espera"),
    QuoteStatus.READY: InfoDeEstado(
        "Proyecto creado", "Información validada", True, cerrado=True, protegido=True),
    QuoteStatus.MISSING: InfoDeEstado(
        "Ya no está en Gmail", "Ya no está en Gmail", True, cerrado=True, espera_accion=True),
    QuoteStatus.IGNORED: InfoDeEstado("Descartada", "Descartada", False, cerrado=True),
}


def _estados(condicion: str) -> tuple[QuoteStatus, ...]:
    return tuple(estado for estado, info in ESTADOS.items() if getattr(info, condicion))


# Los grupos de siempre, derivados de la tabla (no se editan a mano).
CLOSED_STATUSES = frozenset(_estados("cerrado"))                  # ya no se redacta ni se envía correo al cliente
ACTION_STATUSES = frozenset(_estados("espera_accion"))            # esperan una acción humana
REVIEW_QUEUE_STATUSES = _estados("en_cola")                       # aparecen en la cola de revisión
ESTADOS_QUE_UN_ANALISIS_NO_MUEVE = frozenset(_estados("protegido"))
ESTADOS_CON_RESPUESTA_SUGERIDA = _estados("sugiere_respuesta")


def info_de_estado(estado: object) -> InfoDeEstado | None:
    """La fila de un estado, o `None` si no es uno conocido (un texto viejo o dañado en la base)."""
    try:
        return ESTADOS[QuoteStatus(str(estado))]
    except (KeyError, ValueError):
        return None


def estado_tras_un_analisis(actual: str | None, propuesto: str, mismo_mensaje: bool) -> str:
    """El estado que queda al guardar un análisis, igual en los tres modos (U-47, TR-07, TR-08).

    `ready` y `sending` se conservan siempre; `response_sent` solo si el análisis es del mismo mensaje del cliente.
    Un caso nuevo (`actual` vacío) toma el estado propuesto.
    """
    if actual in ESTADOS_QUE_UN_ANALISIS_NO_MUEVE:
        return str(actual)
    info = info_de_estado(actual)
    if info is not None and info.protegido_del_mismo_mensaje and mismo_mensaje:
        return str(actual)
    return propuesto


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
    formato (ancho cero, marcas de dirección) y la puntuación de los extremos se quitan. Un valor que no es texto no
    se reconoce (falla segura).
    """
    if not isinstance(text, str):
        return ""
    compatible = unicodedata.normalize("NFKC", text)
    decomposed = unicodedata.normalize("NFD", compatible)
    kept = "".join(c for c in decomposed if unicodedata.category(c) not in ("Mn", "Cf"))
    return " ".join(kept.casefold().split()).strip(" .,;:!?¡¿«»\"'`")   # «Faltan datos.» es el mismo estado


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
