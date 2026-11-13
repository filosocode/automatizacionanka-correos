"""El contexto previo de un expediente para el trabajo de Work de un SEGUIMIENTO (T-59, Fase C).

Lógica pura: arma, desde el análisis que Python guardó de UN expediente, lo que Work necesita ver para no perder
actividades ni repetir preguntas. Es un estado provisional, no una fuente: la hoja lo dice y Work lo contrasta con
el hilo.
No incluye direcciones de correo, tokens ni rutas, y recorta los textos largos y las listas.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from typing import Any, NamedTuple

from anka.dominio import preguntas as pr

HOJA_DE_CONTEXTO = "CONTEXTO_PREVIO"
HOJA_DE_ACTIVIDADES = "ACTIVIDADES_VIGENTES"
HOJA_DE_PREGUNTAS = "PREGUNTAS"
HOJAS_DE_CONTEXTO = (HOJA_DE_CONTEXTO, HOJA_DE_ACTIVIDADES, HOJA_DE_PREGUNTAS)
COLUMNAS_DE_ACTIVIDADES = ("activity", "servicio_tecnica", "ubicacion", "quantity", "unit", "entregable",
                           "plazo_restriccion", "fuente", "estado", "notes")
COLUMNAS_DE_PREGUNTAS = ("tipo", "ronda", "enviada_el", "mensaje_gmail", "pregunta", "estado", "respondida_en_mensaje",
                         "respondida_el")
TIPO_ENVIADA, TIPO_PROPUESTA, TIPO_INTERNA = "enviada", "propuesta_no_enviada", "interna"
MAX_FILAS = 60
MAX_TEXTO = 400

AVISO = ("Estado provisional que Python guardó del expediente: no es una fuente infalible. Contrástalo con el "
         "hilo de Gmail y con el mensaje por analizar.")
INSTRUCCION_DE_SEGUIMIENTO = (
    "Este mensaje continúa un expediente existente. Lee las hojas CONTEXTO_PREVIO, ACTIVIDADES_VIGENTES y PREGUNTAS "
    "como "
    "estado provisional. Devuelve la tabla ACUMULADA: reutiliza el `activity` exacto de las filas vigentes, no quites "
    "una fila por no mencionarla y marca en `notes` lo que cambias, agregas o ya no se requiere. No vuelvas a pedir lo "
    "ya informado ni una pregunta enviada, y no des una pregunta por respondida sin evidencia en el hilo.")


class Contexto(NamedTuple):
    """Lo que se agrega al libro: los campos, la tabla vigente, las preguntas y la huella del conjunto."""

    campos: list[tuple[str, str]]
    actividades: list[list[str]]
    preguntas: list[list[str]]
    huella: str


def _texto(valor: object) -> str:
    limpio = " ".join(str(valor if valor is not None else "").split())
    return limpio if len(limpio) <= MAX_TEXTO else limpio[:MAX_TEXTO - 1] + "…"


def _si(valor: object) -> str:
    return "si" if valor else "no"


def _actividades(analisis: Mapping[str, Any]) -> tuple[list[list[str]], bool]:
    filas = [a for a in analisis.get("activities") or [] if isinstance(a, Mapping)]
    return ([[_texto(a.get(c)) for c in COLUMNAS_DE_ACTIVIDADES] for a in filas[:MAX_FILAS]], len(filas) > MAX_FILAS)


def _rondas(analisis: Mapping[str, Any]) -> list[dict[str, Any]]:
    rondas = [r for r in analisis.get("rondas_de_preguntas") or [] if isinstance(r, Mapping)]
    enviadas = analisis.get("preguntas_enviadas")
    if not rondas and isinstance(enviadas, Mapping) and enviadas.get("preguntas"):    # anterior a las rondas
        rondas = [pr.nueva_ronda(str(enviadas.get("en") or ""), str(enviadas.get("mensaje") or ""),
                                 enviadas["preguntas"], bool(enviadas.get("esperada")))]
    return [dict(r) for r in rondas]


def _preguntas(analisis: Mapping[str, Any]) -> tuple[list[list[str]], bool]:
    filas: list[list[str]] = []
    enviadas: set[str] = set()
    for numero, ronda in enumerate(_rondas(analisis), 1):
        respuesta = ronda.get("respuesta") if isinstance(ronda.get("respuesta"), Mapping) else {}
        for pregunta in ronda.get("preguntas") or []:
            if not isinstance(pregunta, Mapping):
                continue
            enviadas.add(pr.huella_de_pregunta(str(pregunta.get("texto") or "")))
            filas.append([TIPO_ENVIADA, str(numero), _texto(ronda.get("en")), _texto(ronda.get("mensaje")),
                          _texto(pregunta.get("texto")), _texto(pregunta.get("estado") or pr.ENVIADA),
                          _texto((respuesta or {}).get("mensaje")), _texto((respuesta or {}).get("en"))])
    for pregunta in pr.con_decisiones(pr.candidatas(analisis), analisis.get("preguntas")):
        if pr.huella_de_pregunta(pregunta["texto"]) in enviadas or pr.huella_de_pregunta(
                pr.como_peticion(pregunta["texto"])) in enviadas:
            continue
        elegida = pr.elegida(pregunta)
        tipo = {pr.PREGUNTAR: TIPO_PROPUESTA, pr.INTERNA: TIPO_INTERNA}.get(elegida)
        if tipo:
            filas.append([tipo, "", "", "", _texto(pregunta["texto"]), "", "", ""])
    return filas[:MAX_FILAS], len(filas) > MAX_FILAS


def _motivos(analisis: Mapping[str, Any]) -> str:
    pendiente = analisis.get("pending_model_activities")
    if not (isinstance(pendiente, list) and pendiente):
        return ""               # una propuesta ya resuelta (adoptada o dejada de lado) no deja motivos por revisar
    motivos = [f"{m.get('tipo')}: {m.get('actividad')}" for m in analisis.get("revision_de_tabla") or []
               if isinstance(m, Mapping)]
    return _texto("; ".join(motivos[:10]))


def contexto_de_seguimiento(analisis: Mapping[str, Any], *, thread_id: str, estado: str, codigo_t: str,
                            ultimo_mensaje: str, mensaje_nuevo: str) -> Contexto:
    """El contexto de un expediente para analizar `mensaje_nuevo`; solo usa el análisis y los datos de su hilo."""
    actividades, recortadas = _actividades(analisis)
    preguntas, preguntas_recortadas = _preguntas(analisis)
    filas = [a for a in analisis.get("activities") or [] if isinstance(a, Mapping)]
    contradichas = sum(1 for a in filas if str(a.get("estado") or "").casefold() == "contradictorio")
    contradicciones = analisis.get("contradictions")
    pendiente = analisis.get("pending_model_activities")
    campos = [
        ("aviso", AVISO), ("expediente_thread_id", thread_id), ("proyecto", _texto(analisis.get("project_name"))),
        ("cliente", _texto(analisis.get("client_name"))), ("codigo_t", codigo_t), ("estado_del_expediente", estado),
        ("ultimo_mensaje_analizado", ultimo_mensaje), ("mensaje_por_analizar", mensaje_nuevo),
        ("mensaje_ya_analizado", _si(mensaje_nuevo and mensaje_nuevo == ultimo_mensaje)),
        ("analisis_previo_completo", _si(analisis.get("analysis_complete"))),
        ("tabla_editada_por_una_persona", _si(analisis.get("manual_edit"))),
        ("propuesta_pendiente_de_revision", _si(isinstance(pendiente, list) and pendiente)),
        ("motivos_de_revision", _motivos(analisis)),
        ("contradicciones_abiertas", str(contradichas + (len(contradicciones) if isinstance(contradicciones, list)
                                                         else 0))),
        ("actividades_vigentes", str(len(actividades))), ("preguntas_listadas", str(len(preguntas))),
        ("contexto_recortado", _si(recortadas or preguntas_recortadas)),
    ]
    huella = hashlib.sha256(json.dumps([campos, actividades, preguntas], ensure_ascii=False,
                                       sort_keys=True).encode("utf-8")).hexdigest()
    return Contexto(campos, actividades, preguntas, huella)


def hojas(contexto: Contexto) -> Sequence[tuple[str, Sequence[str], Sequence[Sequence[str]]]]:
    """Las hojas que se agregan al libro: `(nombre, encabezados, filas)`."""
    return (
        (HOJA_DE_CONTEXTO, ("campo", "valor"), [*contexto.campos, ("huella_del_contexto", contexto.huella)]),
        (HOJA_DE_ACTIVIDADES, COLUMNAS_DE_ACTIVIDADES, contexto.actividades),
        (HOJA_DE_PREGUNTAS, COLUMNAS_DE_PREGUNTAS, contexto.preguntas),
    )
