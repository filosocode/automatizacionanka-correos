"""Clasificación comercial preliminar de un mensaje (T-52, D-156): reglas puras, sin acceso a datos ni a Gmail.

Se apoya en la elegibilidad de T-51 (que se apoya en R1): aquí no se vuelve a decidir qué es ruido. Las palabras
clave solo elevan la confianza en que hay una solicitud; nunca descartan un mensaje.
"""

from __future__ import annotations

import json
import unicodedata
from collections.abc import Mapping, Sequence
from typing import Any, NamedTuple

from anka.dominio.trabajo_work import EXCLUIDO, REVISION, Veredicto

SOLICITUD_NUEVA = "SOLICITUD_NUEVA"
SEGUIMIENTO = "SEGUIMIENTO"
NO_SOLICITUD = "NO_SOLICITUD"
REVISION_MANUAL = "REVISION_MANUAL"
CLASIFICACIONES = (SOLICITUD_NUEVA, SEGUIMIENTO, NO_SOLICITUD, REVISION_MANUAL)
# Lo que, confirmado por Work (o sin el campo, por compatibilidad), puede seguir a actividades y respuesta.
AVANZAN = frozenset({SOLICITUD_NUEVA, SEGUIMIENTO})

MOTIVO_SEGUIMIENTO = "hilo_con_expediente"
MOTIVO_SERVICIO = "servicio_de_anka_mencionado"
MOTIVO_AMBIGUO = "ambiguo_para_work"

# Raíces (sin tildes, en minúsculas) de los servicios de Grupo Anka: sirven para reconocer, no para descartar.
SERVICIOS_DE_ANKA = (
    "topograf", "geomatic", "lidar", "fotogrametr", "batimetr", "inspeccion", "georadar", "gpr", "catastr",
    "escaner terrestre", "escaner laser", "levantamiento", "nube de puntos", "ortofoto", "dron", "curvas de nivel",
    "deslinde", "linderos", "replanteo", "agrimensur",
)


class Clasificacion(NamedTuple):
    """La clase preliminar, su motivo y si el mensaje debe ir a Work para confirmarla."""

    clase: str
    motivo: str
    enviar_a_work: bool


def _plano(texto: str) -> str:
    """En minúsculas y sin tildes: «Batimetría» y «batimetria» valen lo mismo."""
    sin_tildes = unicodedata.normalize("NFKD", texto.casefold())
    return "".join(c for c in sin_tildes if not unicodedata.combining(c))


def _nombres_de_adjuntos(fila: Mapping[str, Any]) -> list[str]:
    try:
        adjuntos = json.loads(str(fila.get("attachments_json") or "[]"))
    except ValueError:
        return []
    return [str(a.get("filename") or "") for a in adjuntos if isinstance(a, dict)] if isinstance(adjuntos, list) else []


def menciona_un_servicio(fila: Mapping[str, Any], terminos_comerciales: Sequence[str] = ()) -> bool:
    """¿El asunto, el fragmento que guarda el receptor o algún adjunto nombra un servicio de Anka o un término de R1?"""
    texto = _plano(" ".join([str(fila.get("subject") or ""), str(fila.get("snippet") or ""),
                             *_nombres_de_adjuntos(fila)]))
    claves = [*SERVICIOS_DE_ANKA, *(_plano(t) for t in terminos_comerciales if str(t).strip())]
    return any(clave in texto for clave in claves)


def clasificar_preliminar(fila: Mapping[str, Any], veredicto: Veredicto, hay_expediente: bool,
                          terminos_comerciales: Sequence[str] = ()) -> Clasificacion:
    """La clasificación preliminar de un mensaje de la cola de recepción.

    `veredicto` es el de T-51. Un mensaje que no es ni ruido ni seguimiento y no nombra ningún servicio no se
    descarta: queda `REVISION_MANUAL` y se envía a Work, que lee el cuerpo (la cola solo guarda el fragmento de Gmail).
    """
    if veredicto.estado == EXCLUIDO:
        return Clasificacion(NO_SOLICITUD, veredicto.motivo_id, False)
    if veredicto.estado == REVISION:
        return Clasificacion(REVISION_MANUAL, veredicto.motivo_id, False)
    if hay_expediente:
        return Clasificacion(SEGUIMIENTO, MOTIVO_SEGUIMIENTO, True)
    if menciona_un_servicio(fila, terminos_comerciales):
        return Clasificacion(SOLICITUD_NUEVA, MOTIVO_SERVICIO, True)
    return Clasificacion(REVISION_MANUAL, MOTIVO_AMBIGUO, True)
