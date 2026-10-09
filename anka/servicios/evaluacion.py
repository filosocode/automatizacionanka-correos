"""Evaluar el análisis con solicitudes reales del histórico: muestrear, guardar fichas, validarlas y medir (T-34).

Las fichas y los informes viven en la carpeta de datos del equipo (nunca en el repositorio) y los archivos del cliente
no se copian: cada ficha apunta a su ubicación original. Una ficha nace como borrador que propone el modelo a partir de
la propuesta aprobada; solo cuenta cuando una persona la valida.
"""

from __future__ import annotations

import json
import random
import re
from collections.abc import Callable, Mapping, Sequence
from datetime import datetime
from pathlib import Path
from typing import Any, cast

import base64
import mimetypes

from anka.dominio import evaluacion as medida
from anka.dominio.analisis import Adjunto
from anka.dominio.configuracion import ConfigLlm
from anka.dominio.attachments import attachment_reviewed_sections, build_attachment_evidence
from anka.dominio.preguntas import PREGUNTAR, candidatas as preguntas_candidatas
from anka.dominio.reception import reception_assessment
from anka.infra import settings
from anka.infra.attachments import extract_attachment_text
from anka.infra.logging_setup import get_logger

log = get_logger(__name__)

BORRADOR = "borrador"
VALIDADA = "validada"
EXTRAIDA = "extraida"       # lo esperado salió de la tabla de cantidades de la solicitud (ni persona ni modelo)

_SOLICITUD = re.compile(
    r"solicitud|rfq|rfp|tdr|t[eé]rminos|alcance|requerimiento|invitaci[oó]n|especificaci|pliego|cuestionario|"
    r"lista de cantidades|anexo", re.IGNORECASE)
_PROPUESTA = re.compile(r"cotizaci[oó]n|propuesta|oferta|\bT&E|\bTE\b|\bT\d{5}", re.IGNORECASE)
_LEGIBLES = frozenset({".pdf", ".docx", ".doc", ".xlsx", ".xls", ".txt", ".kml", ".kmz", ".msg", ".eml"})
_DE_PROPUESTA = frozenset({".pdf", ".docx", ".doc", ".xlsx"})
_SERVICIOS = (
    ("lidar", r"lidar"), ("batimetria", r"batimetr"), ("fotogrametria", r"fotogram|ortofoto|\bdron"),
    ("catastro", r"catastr|predial"), ("monitoreo", r"monitore|deformaci"), ("geodesia", r"geodes|gnss|nivelaci"),
    ("topografia", r"topograf|levantamiento"),
)
MAX_BYTES_A_LEER = 25 * 1024 * 1024


def carpeta_de_evaluacion() -> Path:
    """Dónde viven las fichas y los informes: la carpeta de datos del equipo, no el repositorio."""
    return settings.DATA_DIR / "evaluacion"


def _servicio_de(texto: str) -> str:
    plano = texto.casefold()
    return next((nombre for nombre, patron in _SERVICIOS if re.search(patron, plano)), "otros")


def candidatas(raiz: Path | str) -> list[dict[str, Any]]:
    """Carpetas del histórico con algo parecido a una solicitud del cliente y a una propuesta, en orden estable."""
    raiz = Path(raiz)
    encontradas: list[dict[str, Any]] = []
    for carpeta in sorted({p.parent for p in raiz.rglob("*") if p.is_file()}):
        archivos = [p for p in carpeta.iterdir() if p.is_file()]
        solicitud = [p.name for p in archivos if _SOLICITUD.search(p.name) and p.suffix.lower() in _LEGIBLES]
        propuesta = [p.name for p in archivos if _PROPUESTA.search(p.name) and p.suffix.lower() in _DE_PROPUESTA]
        if not (solicitud and propuesta):
            continue
        relativa = carpeta.relative_to(raiz).parts
        encontradas.append({
            "ruta": str(carpeta), "cliente": relativa[0] if relativa else carpeta.name,
            "solicitud": sorted(solicitud)[:4], "propuesta": sorted(propuesta)[:4],
            "servicio": _servicio_de(str(carpeta) + " " + " ".join(solicitud + propuesta)),
        })
    return encontradas


def muestrear(candidatas_: Sequence[Mapping[str, Any]], cantidad: int, semilla: int = 1) -> list[dict[str, Any]]:
    """Una carpeta por cliente, repartidas entre los servicios por turnos; la misma semilla da la misma muestra."""
    azar = random.Random(semilla)
    por_servicio: dict[str, list[dict[str, Any]]] = {}
    for candidata in candidatas_:
        por_servicio.setdefault(candidata["servicio"], []).append(dict(candidata))
    for grupo in por_servicio.values():
        azar.shuffle(grupo)
    elegidas: list[dict[str, Any]] = []
    clientes: set[str] = set()
    servicios = sorted(por_servicio)
    while len(elegidas) < cantidad and any(por_servicio.values()):
        for servicio in servicios:
            grupo = por_servicio[servicio]
            while grupo and grupo[-1]["cliente"] in clientes:
                grupo.pop()
            if grupo and len(elegidas) < cantidad:
                elegida = grupo.pop()
                clientes.add(elegida["cliente"])
                elegidas.append(elegida)
    return elegidas


# --------------------------------------------------------------------------------------------------- fichas

def _carpeta_de_fichas() -> Path:
    return carpeta_de_evaluacion() / "casos"


def guardar_ficha(ficha: Mapping[str, Any]) -> Path:
    """Guarda (o reemplaza) una ficha en la carpeta de datos del equipo."""
    ruta = _carpeta_de_fichas() / f"{ficha['id']}.json"
    ruta.parent.mkdir(parents=True, exist_ok=True)
    temporal = ruta.with_suffix(".tmp")
    temporal.write_text(json.dumps(dict(ficha), ensure_ascii=False, indent=1), encoding="utf-8")
    temporal.replace(ruta)
    return ruta


def leer_ficha(identificador: str) -> dict[str, Any]:
    """La ficha con ese identificador; falla si no existe."""
    ruta = _carpeta_de_fichas() / f"{identificador}.json"
    if not ruta.exists():
        raise ValueError(f"No existe la ficha {identificador}.")
    return dict(json.loads(ruta.read_text(encoding="utf-8")))


def todas_las_fichas() -> list[dict[str, Any]]:
    """Todas las fichas guardadas, borradores y validadas, por identificador."""
    carpeta = _carpeta_de_fichas()
    if not carpeta.exists():
        return []
    return [json.loads(p.read_text(encoding="utf-8")) for p in sorted(carpeta.glob("*.json"))]


def fichas_validadas() -> list[dict[str, Any]]:
    """Las fichas que una persona validó."""
    return [f for f in todas_las_fichas() if f.get("estado") == VALIDADA]


def fichas_evaluables() -> list[dict[str, Any]]:
    """Las que dan una medida: las validadas por una persona y las extraídas de una tabla de cantidades explícita."""
    return [f for f in todas_las_fichas() if f.get("estado") in (VALIDADA, EXTRAIDA)]


def validar(identificador: str) -> dict[str, Any]:
    """Una persona da por buena la ficha. Necesita un resultado esperado (al menos si es o no una solicitud)."""
    ficha = leer_ficha(identificador)
    if "es_solicitud" not in (ficha.get("esperado") or {}):
        raise ValueError(f"La ficha {identificador} no tiene resultado esperado: no se puede validar.")
    ficha["estado"] = VALIDADA
    ficha["validada_en"] = datetime.now().astimezone().isoformat(timespec="seconds")
    guardar_ficha(ficha)
    return ficha


# --------------------------------------------------------------------------------------------------- evaluación

def evaluar(analizar: Callable[[Mapping[str, Any]], tuple[Mapping[str, Any], Sequence[str]]], modelo: str = "",
            version_del_prompt: str = "", ids: Sequence[str] | None = None, repeticiones: int = 1) -> dict[str, Any]:
    """Corre `analizar` sobre cada ficha validada y mide. Guarda el informe con el modelo, el prompt y la fecha (T-23).

    `analizar(ficha)` devuelve (análisis, preguntas al cliente). Un caso que falla se informa aparte y no frena a los
    demás. Con `ids` solo se evalúan esas fichas. Con `repeticiones` cada ficha se corre varias veces: el modelo no
    responde igual cada vez, y una sola corrida no separa una mejora real del ruido.
    """
    fichas = [f for f in fichas_evaluables() if ids is None or f["id"] in ids]
    if not fichas:
        raise ValueError("No hay fichas validadas ni extraídas: sin ellas no se calcula ninguna precisión.")
    casos: list[dict[str, Any]] = []
    fallidos: list[dict[str, str]] = []
    for ficha in fichas:
        for corrida in range(1, max(1, repeticiones) + 1):
            try:
                analisis, preguntas = analizar(ficha)
                casos.append({"id": ficha["id"], "servicio": ficha.get("servicio", ""), "repeticion": corrida,
                              **medida.comparar(ficha["esperado"], analisis, preguntas)})
            except Exception as exc:  # un caso roto no debe esconder los demás
                log.warning("Evaluación: el caso %s falló (%s)", ficha.get("id"), type(exc).__name__)
                fallidos.append({"id": str(ficha.get("id")), "repeticion": str(corrida),
                                 "error": f"{type(exc).__name__}: {exc}"[:200]})
    ahora = datetime.now().astimezone()
    informe: dict[str, Any] = {
        "modelo": modelo, "version_del_prompt": version_del_prompt, "fecha": ahora.isoformat(timespec="seconds"),
        "fichas": {"extraidas": sum(f.get("estado") == EXTRAIDA for f in fichas),
                   "validadas": sum(f.get("estado") == VALIDADA for f in fichas)},
        "repeticiones": max(1, repeticiones), "estabilidad": medida.estabilidad(casos),
        "resumen": medida.resumir(casos), "casos": casos, "fallidos": fallidos,
    }
    carpeta = carpeta_de_evaluacion() / "informes"
    carpeta.mkdir(parents=True, exist_ok=True)
    nombre = f"{ahora:%Y%m%d-%H%M%S}-{re.sub(r'[^A-Za-z0-9._-]+', '_', modelo or 'sin-modelo')}.json"
    archivo = carpeta / nombre
    archivo.write_text(json.dumps(informe, ensure_ascii=False, indent=1), encoding="utf-8")
    informe["archivo"] = str(archivo)
    return informe


# ------------------------------------------------------------------------------------- lectura y cadena real

CORREO_SINTETICO = ("Buen día,\n\nAdjunto la solicitud de cotización del proyecto con sus documentos. "
                    "Quedo atento a su propuesta.\n\nCordialmente,\nCliente")
MAX_CARACTERES_PARA_EL_BORRADOR = 6000


def adjunto_desde_archivo(ruta: Path) -> Adjunto:
    """Un archivo leído como lo habría leído el correo: con su estado, cobertura y evidencia."""
    tipo = mimetypes.guess_type(ruta.name)[0] or ""
    try:
        if ruta.stat().st_size > MAX_BYTES_A_LEER:
            texto, motivo = "", "archivo demasiado grande para la evaluación"
        else:
            texto, motivo = extract_attachment_text(ruta.name, tipo, ruta.read_bytes())
    except OSError as exc:
        texto, motivo = "", f"no se pudo leer: {exc}"
    registro: Adjunto = {
        "filename": ruta.name, "mime_type": tipo, "source_message_id": "evaluacion",
        "status": "bloqueado" if not texto else "lectura_parcial" if motivo else "extraido",
        "text": texto, "reason": motivo, "coverage": "no_legible" if not texto else "parcial" if motivo else "completa",
        "extracted_chars": len(texto),
        "reviewed_sections": attachment_reviewed_sections(texto) if texto else [],
        "evidence": build_attachment_evidence(texto) if texto else [],
    }
    return registro


def _texto_de(carpeta: Path, nombres: Sequence[str]) -> str:
    partes = [adjunto_desde_archivo(carpeta / nombre).get("text", "") for nombre in nombres[:2]]
    return "\n\n".join(p for p in partes if p)[:MAX_CARACTERES_PARA_EL_BORRADOR]


_ESQUEMA_DEL_BORRADOR: dict[str, Any] = {
    "type": "object",
    "properties": {
        "ubicacion": {"type": "array", "items": {"type": "string"}},
        "actividades": {"type": "array", "items": {"type": "object", "properties": {
            "servicio": {"type": "string"}, "cantidad": {"type": ["number", "null"]}, "unidad": {"type": "string"}},
            "required": ["servicio", "cantidad", "unidad"]}},
    },
    "required": ["ubicacion", "actividades"],
}


def _prompt_del_borrador(solicitud: str, propuesta: str) -> str:
    return (
        "Eres un revisor de ofertas de topografía y geomática. Tienes la SOLICITUD que hizo el cliente y la PROPUESTA "
        "que Anka terminó entregando. Devuelve JSON con: `ubicacion` (municipios y departamentos del proyecto, tal "
        "como aparecen), `actividades` (cada actividad cotizada en la propuesta, con `servicio` en pocas palabras, "
        "`cantidad` numérica o null y `unidad` como ha, km, m, m², km², puntos…). "
        "No inventes: si la propuesta no lo dice, omítelo.\n\n=== SOLICITUD ===\n" + solicitud
        + "\n\n=== PROPUESTA ===\n" + propuesta)


def preparar_ficha(candidata: Mapping[str, Any], identificador: str, config_llm: Mapping[str, Any]) -> dict[str, Any]:
    """La ficha en borrador de una carpeta del histórico: el modelo propone lo esperado y una persona lo valida."""
    from anka.infra import llm  # aquí y no arriba: solo esta función necesita el modelo
    carpeta = Path(candidata["ruta"])
    solicitud = _texto_de(carpeta, candidata["solicitud"])
    propuesta = _texto_de(carpeta, candidata["propuesta"])
    propuesto: dict[str, Any] = {}
    if solicitud and propuesta:
        propuesto = llm.generar_json(
            _prompt_del_borrador(solicitud, propuesta), _ESQUEMA_DEL_BORRADOR, cast(ConfigLlm, dict(config_llm)),
            "ficha_de_evaluacion") or {}
    esperado = {"es_solicitud": True, "ubicacion": propuesto.get("ubicacion", []),
                "actividades": propuesto.get("actividades", []),
                # lo que la solicitud no dice se detecta en su texto: no se le pide al modelo
                "faltante": medida.temas_ausentes(solicitud, propuesto.get("ubicacion", []))}
    return {
        "id": identificador, "estado": BORRADOR, "servicio": candidata["servicio"], "origen": candidata["ruta"],
        "correo": CORREO_SINTETICO, "adjuntos": [str(carpeta / n) for n in candidata["solicitud"][:3]],
        "esperado": esperado,
        "ayuda_para_validar": {
            "solicitud": solicitud[:700], "propuesta": propuesta[:700],
            "limite": "El borrador lo propuso el modelo; revísalo contra los documentos antes de validar."},
    }


def _mensaje_del_cliente(texto: str) -> dict[str, Any]:
    return {"id": "evaluacion", "internalDate": "0", "labelIds": ["INBOX"], "payload": {
        "headers": [{"name": "From", "value": "Cliente <cliente@example.com>"},
                    {"name": "Subject", "value": "Solicitud de cotización"}],
        "mimeType": "text/plain", "body": {"data": base64.urlsafe_b64encode(texto.encode()).decode()}}}


def analizar_ficha(ficha: Mapping[str, Any], config: Mapping[str, Any]) -> tuple[dict[str, Any], list[str]]:
    """Corre la cadena real sobre una ficha: lee sus adjuntos, analiza el hilo y recoge las preguntas al cliente."""
    from anka.servicios.lectura_por_tramos import analizar_hilo
    adjuntos = [adjunto_desde_archivo(Path(ruta)) for ruta in ficha.get("adjuntos", [])]
    mensaje = _mensaje_del_cliente(ficha.get("correo", CORREO_SINTETICO))
    analisis = analizar_hilo([mensaje], list(config["required_fields"]), cast(ConfigLlm, dict(config["llm"])), adjuntos)
    evaluacion_ = reception_assessment(analisis)
    preguntas = list(evaluacion_.get("questions", []))
    preguntas += [c["texto"] for c in preguntas_candidatas(analisis, evaluacion_) if c["sugerida"] == PREGUNTAR]
    return dict(analisis), list(dict.fromkeys(preguntas))


# ---------------------------------------------------------------------------------- fichas extraídas de una tabla

def _hojas_del_excel(ruta: Path) -> list[list[tuple[Any, ...]]]:
    """Las primeras filas de las primeras hojas de un .xlsx; [] si no se puede abrir."""
    import openpyxl
    try:
        libro = openpyxl.load_workbook(ruta, read_only=True, data_only=True)
    except Exception as exc:  # un archivo roto o con otro formato no frena el muestreo, pero se anota
        log.warning("Evaluación: no se pudo abrir %s (%s)", ruta.name, type(exc).__name__)
        return []
    try:
        return [list(hoja.iter_rows(min_row=1, max_row=400, values_only=True)) for hoja in libro.worksheets[:6]]
    except Exception as exc:
        log.warning("Evaluación: no se pudo leer %s (%s)", ruta.name, type(exc).__name__)
        return []
    finally:
        libro.close()


def ficha_extraida(candidata: Mapping[str, Any], identificador: str) -> dict[str, Any] | None:
    """La ficha de una carpeta cuyo Excel de solicitud trae su tabla de cantidades; None si ninguno la trae.

    Lo esperado sale de la tabla con un lector distinto del modelo (no es circular) y sin que nadie tenga que
    recordar el caso. La ficha queda `extraida`: cuenta en la evaluación, marcada como tal.
    """
    carpeta = Path(candidata["ruta"])
    for nombre in candidata["solicitud"]:
        if not nombre.lower().endswith(".xlsx"):
            continue
        hojas = _hojas_del_excel(carpeta / nombre)
        tabla = next((a for a in (medida.extraer_tabla(h) for h in hojas) if a), [])
        if not tabla:
            continue
        adjunto = adjunto_desde_archivo(carpeta / nombre)
        lugares = [lugar for h in hojas for lugar in medida.lugares_declarados(h)]
        return {
            "id": identificador, "estado": EXTRAIDA, "servicio": candidata["servicio"], "origen": candidata["ruta"],
            "origen_esperado": f"tabla de cantidades de {nombre}",
            "correo": CORREO_SINTETICO, "adjuntos": [str(carpeta / nombre)],
            "esperado": {"es_solicitud": True, "ubicacion": list(dict.fromkeys(lugares)), "actividades": tabla,
                         "faltante": medida.temas_ausentes(adjunto.get("text", ""), lugares)},
        }
    return None
