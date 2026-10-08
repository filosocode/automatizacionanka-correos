"""Análisis de un hilo con lectura completa aunque no quepa en el modelo (T-25).

Si el correo y sus adjuntos caben en una llamada se analizan como siempre. Si no, cada adjunto largo se lee por tramos:
el modelo extrae de cada tramo los hallazgos con su localizador y cita, se unen en un compendio y el análisis final se
hace sobre el correo y los compendios. Nada se recorta; si algo no se puede leer, se detiene con un aviso.
"""

from __future__ import annotations

from typing import Any

from anka.dominio.analisis import Adjunto, Analisis
from anka.dominio.configuracion import ConfigLlm
from anka.dominio.correo import header, message_text
from anka.dominio.errores import ContextoInsuficiente, ErrorDeAnka
from anka.dominio.tramos import (
    SOLAPE_CARACTERES, Hallazgo, compendio, dividir_texto, quitar_lineas_repetidas, ultimo_encabezado,
    unir_hallazgos,
)
from anka.infra import cache_hallazgos, llm
from anka.infra.logging_setup import get_logger

log = get_logger(__name__)

LEIDOS = {"extraido", "lectura_parcial"}  # estados de adjunto cuyo texto se entrega al modelo
FRACCION_CRUDA = 0.4   # parte del espacio libre que pueden ocupar, tal cual, los adjuntos pequeños
MAX_NIVELES = 3        # veces que se consolida un compendio demasiado grande antes de rendirse
MAX_DIVISIONES = 2     # veces que se divide en dos un tramo que el modelo no logra responder
MIN_TRAMO_REINTENTO = 1500  # un tramo más corto que el doble de esto ya no se divide


def analizar_hilo(
    messages: list[dict], required_fields: list[str], config_llm: ConfigLlm, attachments: list[Adjunto] | None = None,
) -> Analisis:
    """Analiza el hilo completo; usa lectura por tramos solo cuando no cabe en una llamada."""
    attachments = attachments or []
    if llm.cabe_en_el_modelo(llm.analysis_prompt(messages, required_fields, attachments), config_llm):
        return llm.analyze(messages, required_fields, config_llm, attachments)
    contexto = _contexto(messages, required_fields)
    condensados, traza = _condensar(messages, required_fields, config_llm, attachments, contexto)
    resultado = llm.analyze(messages, required_fields, config_llm, condensados)
    resultado["lectura_por_tramos"] = traza
    return resultado


def _contexto(messages: list[dict], required_fields: list[str]) -> str:
    asunto = header(messages[-1], "Subject") if messages else ""
    return f"Asunto del correo: {asunto}\nDatos que interesan especialmente: {', '.join(required_fields) or 'ninguno'}"


def _espacio_libre(messages: list[dict], required_fields: list[str], config_llm: ConfigLlm) -> int:
    """Caracteres que quedan para adjuntos una vez colocados el correo y las instrucciones del análisis."""
    total = int((llm.limite_de_tokens(config_llm) - llm.OUTPUT_RESERVE_TOKENS) * llm.CHARS_PER_TOKEN)
    libre = total - len(llm.analysis_prompt(messages, required_fields, []))
    if libre <= 0:
        cuerpo = sum(len(message_text(m.get("payload", {}))) for m in messages)
        raise ContextoInsuficiente(
            f"El correo por sí solo (≈ {cuerpo:,} caracteres) no deja espacio para analizar los adjuntos en la "
            "ventana del modelo. No se analizó para no perder contenido: requiere revisión manual."
        )
    return libre


def _condensar(
    messages: list[dict], required_fields: list[str], config_llm: ConfigLlm, attachments: list[Adjunto], contexto: str,
) -> tuple[list[Adjunto], dict[str, Any]]:
    """Sustituye el texto de los adjuntos grandes por el compendio de sus hallazgos; los pequeños quedan intactos."""
    libre = _espacio_libre(messages, required_fields, config_llm)
    legibles = sorted(
        (i for i, item in enumerate(attachments) if item.get("status") in LEIDOS and item.get("text")),
        key=lambda i: len(attachments[i]["text"]),
    )
    crudo, ocupado = set(), 0
    for i in legibles:
        if ocupado + len(attachments[i]["text"]) <= libre * FRACCION_CRUDA:
            crudo.add(i)
            ocupado += len(attachments[i]["text"])
    resultado = [dict(item) for item in attachments]
    hallazgos_de: dict[int, list[Hallazgo]] = {}
    traza: dict[str, Any] = {"adjuntos": [], "niveles": 0}
    for i in legibles:
        if i in crudo:
            continue
        nombre = attachments[i].get("filename", "adjunto")
        hallazgos, tramos = _leer_por_tramos(attachments[i]["text"], nombre, config_llm, contexto)
        hallazgos_de[i] = hallazgos
        traza["adjuntos"].append({"adjunto": nombre, "tramos": tramos, "hallazgos": len(hallazgos)})
    for nivel in range(MAX_NIVELES + 1):
        for i, hallazgos in hallazgos_de.items():
            resultado[i]["text"] = _texto_del_compendio(attachments[i], hallazgos, traza["adjuntos"])
        prompt = llm.analysis_prompt(messages, required_fields, resultado)  # type: ignore[arg-type]
        if llm.cabe_en_el_modelo(prompt, config_llm):
            return resultado, traza  # type: ignore[return-value]
        if nivel == MAX_NIVELES:
            break
        traza["niveles"] = nivel + 1
        for i in hallazgos_de:
            nombre = attachments[i].get("filename", "adjunto")
            hallazgos_de[i] = _consolidar(hallazgos_de[i], nombre, config_llm, contexto)
    raise ContextoInsuficiente(
        "Los hallazgos de los documentos de este hilo siguen sin caber en la ventana del modelo tras consolidarlos "
        f"{MAX_NIVELES} veces. No se analizó para no perder contenido: requiere revisión manual o un modelo con "
        "más contexto."
    )


def _texto_del_compendio(adjunto: Adjunto, hallazgos: list[Hallazgo], resumen: list[dict[str, Any]]) -> str:
    nombre = adjunto.get("filename", "adjunto")
    tramos = next((item["tramos"] for item in resumen if item["adjunto"] == nombre), 1)
    return (
        f"LECTURA POR TRAMOS: el texto completo de este adjunto ({len(adjunto.get('text', '')):,} caracteres) se leyó "
        f"en {tramos} tramos. Estos son los hallazgos extraídos de todos ellos, con su localizador y su cita:\n"
        + compendio(hallazgos)
    )


def _leer_por_tramos(texto: str, origen: str, config_llm: ConfigLlm, contexto: str) -> tuple[list[Hallazgo], int]:
    texto, repetidas = quitar_lineas_repetidas(texto)
    if repetidas:
        log.info("%s: %d líneas repetidas (encabezados, pies) se leen una sola vez", origen, repetidas)
    tramos = dividir_texto(texto, llm.caracteres_por_tramo(config_llm))
    listas = []
    vigente = ""  # último marcador de página/hoja visto: el siguiente tramo continúa dentro de él
    for numero, tramo in enumerate(tramos, 1):
        log.info("Leyendo %s por tramos: %d de %d", origen, numero, len(tramos))
        posicion = f"tramo {numero} de {len(tramos)}"
        listas.append(_hallazgos_del_tramo(tramo, origen, posicion, config_llm, contexto, vigente=vigente))
        vigente = ultimo_encabezado(tramo) or vigente
    return unir_hallazgos(listas), len(tramos)


def _hallazgos_del_tramo(
    tramo: str, origen: str, posicion: str, config_llm: ConfigLlm, contexto: str, profundidad: int = 0,
    vigente: str = "",
) -> list[Hallazgo]:
    """Hallazgos de un tramo; si el modelo no logra responderlo, lo divide en dos y lo vuelve a intentar.

    Repetir el mismo prompt con temperatura 0 daría el mismo fallo; uno más corto suele salir bien. Si ni dividiendo
    se logra, el error sube: el hilo no se completa con tramos faltantes.
    """
    prompt = llm.hallazgos_prompt(contexto, origen, tramo, posicion, vigente=vigente)
    try:
        return _hallazgos(prompt, config_llm)
    except ErrorDeAnka:
        if profundidad >= MAX_DIVISIONES or len(tramo) < 2 * MIN_TRAMO_REINTENTO:
            # ya no se puede dividir: último recurso, otra decodificación que castiga la repetición
            log.warning("%s (%s): el modelo no respondió; segundo intento contra repeticiones", origen, posicion)
            return _hallazgos(prompt, config_llm, anti_repeticion=True)
        log.warning("%s (%s): el modelo no respondió; se divide el tramo y se reintenta", origen, posicion)
        mitades = dividir_texto(tramo, max(MIN_TRAMO_REINTENTO, len(tramo) // 2 + SOLAPE_CARACTERES))
        return unir_hallazgos(
            _hallazgos_del_tramo(mitad, origen, f"{posicion}, parte {n} de {len(mitades)}", config_llm, contexto,
                                 profundidad + 1, vigente)
            for n, mitad in enumerate(mitades, 1)
        )


def _consolidar(hallazgos: list[Hallazgo], origen: str, config_llm: ConfigLlm, contexto: str) -> list[Hallazgo]:
    partes = dividir_texto(compendio(hallazgos), llm.caracteres_por_tramo(config_llm))
    if len(partes) == 1 and len(partes[0]) <= llm.caracteres_por_tramo(config_llm) // 2:
        return hallazgos  # ya es pequeño: otra pasada no lo reduciría
    prompts = [
        llm.hallazgos_prompt(contexto, origen, parte, f"consolidación {n} de {len(partes)}", consolidar=True)
        for n, parte in enumerate(partes, 1)
    ]
    return unir_hallazgos(_hallazgos(prompt, config_llm) for prompt in prompts)


def _hallazgos(prompt: str, config_llm: ConfigLlm, anti_repeticion: bool = False) -> list[Hallazgo]:
    """Hallazgos del tramo: de la caché si ya se extrajeron, y si no del modelo (y se guardan)."""
    identificador = cache_hallazgos.clave(prompt, config_llm.get("model", ""), llm.VERSION_PROMPT_HALLAZGOS)
    guardados = cache_hallazgos.leer(identificador)
    if guardados is not None:
        return guardados
    hallazgos = llm.extraer_hallazgos(prompt, config_llm, anti_repeticion=anti_repeticion)
    cache_hallazgos.guardar(identificador, hallazgos)
    return hallazgos
