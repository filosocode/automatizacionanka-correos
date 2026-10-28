"""Preguntas de un caso (U-22): candidatas, decisión de una persona y detección de las que salen en un correo.

Lógica pura, sin base de datos ni Gmail. Una *candidata* es una pregunta que propone el análisis (de Work, del modelo
local o de recepción); la persona decide por separado qué hacer con cada una: preguntarla al cliente, resolverla dentro
de Anka o descartarla. Lo que se le pregunta al cliente sale de esas decisiones, y el estado «esperando al cliente» sale
de las preguntas que de verdad quedaron en el correo enviado.
"""

from __future__ import annotations

from anka.dominio.errores import ErrorDeValidacion
from anka.dominio.reloj import instante

import copy
import hashlib
import re
import unicodedata
from collections.abc import Iterable, Mapping, Sequence
from typing import Any, TypedDict

PREGUNTAR = "ask_client"       # se le pregunta al cliente
INTERNA = "internal"           # la resuelve Anka por dentro (queda en los pendientes)
DESCARTAR = "discard"          # no hace falta
DECISIONES = (PREGUNTAR, INTERNA, DESCARTAR)
MAX_PREGUNTAS = 20
MAX_TEXTO = 500

# Lo que el análisis sugiere según la disposición que dio a cada pregunta de su auditoría.
_SUGERENCIA_DE_DISPOSICION = {"ask_client": PREGUNTAR, "internal_review": INTERNA, "do_not_ask": DESCARTAR}


class Pregunta(TypedDict, total=False):
    """Una pregunta del caso: su texto, qué sugirió el análisis y qué decidió la persona (si ya decidió)."""

    id: str
    texto: str
    sugerida: str       # PREGUNTAR | INTERNA | DESCARTAR
    origen: str         # «auditoría», «recepción», «análisis» o «persona»
    decision: str       # vacío si nadie ha decidido todavía
    por: str
    en: str


_INTERNA = re.compile(r"\bintern(?:o|a|os|as|amente)\b", re.IGNORECASE)
# Lo que Anka no pudo leer o comprobar: es de Anka, no del cliente, tenga o no la forma de una pregunta.
_LIMITE_DE_LECTURA = re.compile(
    r"no inspeccion|sin inspeccionar|no verificad|parcialmente|no legible|ilegible|no se pudo (?:leer|abrir)",
    re.IGNORECASE)
_PREFIJOS = ("crítico:", "critico:", "recomendable:")
_TIPO_AL_FINAL = re.compile(
    r"\s*\((?:critical|recommended|crítico|recomendable|bloqueantes?|blocking)\)\s*$", re.IGNORECASE)
# Un verbo de trabajo interno no se le pide al cliente: «Resolver la contradicción…» es «La contradicción…» (U-33).
_VERBO_INTERNO = re.compile(r"^\s*resolver\s+(?=\S)", re.IGNORECASE)
_URL = re.compile(r"https?://\S+|www\.\S+", re.IGNORECASE)
_INTRODUCCION = "\u00bfpodria confirmar lo siguiente?"     # la frase de la plantilla, sin tildes ni mayúsculas
_PREGUNTA = re.compile(r"¿[^?¿]*\?|[^\n¿.!?]{6,}\?")


def _plano(texto: str) -> str:
    descompuesto = unicodedata.normalize("NFD", texto)
    return " ".join("".join(c for c in descompuesto if unicodedata.category(c) != "Mn").casefold().split())


def identificador(texto: str) -> str:
    """Identificador estable de una pregunta, calculado con su texto original (sin tildes, mayúsculas ni espacios)."""
    return "q" + hashlib.sha1(_plano(texto).encode("utf-8")).hexdigest()[:8]


def limpiar(texto: object) -> str:
    """El texto de una pregunta sin las marcas del análisis («Crítico:», «(recommended)») y con mayúscula inicial."""
    limpio = str(texto or "").strip()
    for prefijo in _PREFIJOS:
        if limpio.casefold().startswith(prefijo):
            limpio = limpio[len(prefijo):].strip()
    limpio = _TIPO_AL_FINAL.sub("", limpio).strip()
    return (limpio[:1].upper() + limpio[1:])[:MAX_TEXTO]


def como_peticion(texto: str) -> str:
    """El punto tal como se le pide al cliente: sin el verbo de trabajo interno del principio (U-33).

    Un texto que ya es una pregunta, o que no empieza por ese verbo, queda igual.
    """
    if "?" in texto or not _VERBO_INTERNO.match(texto):
        return texto
    resto = _VERBO_INTERNO.sub("", texto, count=1).strip()
    return resto[:1].upper() + resto[1:]


def es_interna(texto: str) -> bool:
    """¿Es una tarea de Anka y no una pregunta al cliente? Su encabezado dice «interno», «interna» o «internamente».

    Solo se mira el encabezado (lo anterior a los dos puntos) y nunca un texto que ya es una pregunta: «¿Cuál es el
    área interna del lote?» sí se le puede hacer al cliente. Un límite de lectura («KML no inspeccionable») también es
    de Anka.
    """
    if "?" in texto:
        return False
    return bool(_INTERNA.search(texto.split(":", 1)[0]) or _LIMITE_DE_LECTURA.search(texto))


def _sugerencia(texto: str) -> str:
    """Qué sugerir de un punto del análisis: lo de Anka (tareas internas, límites de lectura) no va al cliente."""
    return INTERNA if es_interna(texto) else PREGUNTAR


def candidatas(analysis: Mapping[str, Any], assessment: Mapping[str, Any] | None = None) -> list[Pregunta]:
    """Todas las preguntas que el análisis propone, sin repetir, cada una con lo que sugiere hacer con ella."""
    encontradas: dict[str, Pregunta] = {}

    def agregar(texto: object, sugerida: str, origen: str) -> None:
        limpio = limpiar(texto)
        if not limpio or len(encontradas) >= MAX_PREGUNTAS:
            return
        clave = identificador(limpio)
        if clave not in encontradas:
            encontradas[clave] = Pregunta(id=clave, texto=limpio, sugerida=sugerida, origen=origen)

    for item in analysis.get("question_audit") or []:
        if isinstance(item, Mapping):
            texto = limpiar(item.get("question"))
            sugerida = _SUGERENCIA_DE_DISPOSICION.get(str(item.get("disposition") or ""), _sugerencia(texto))
            agregar(texto, sugerida, "auditoría")
    for texto in (assessment or {}).get("questions", []):
        agregar(texto, _sugerencia(limpiar(texto)), "recepción")
    for texto in analysis.get("other_questions") or []:
        agregar(texto, _sugerencia(limpiar(texto)), "análisis")
    return list(encontradas.values())


def con_decisiones(candidatas_: Sequence[Pregunta], guardadas: Iterable[Mapping[str, Any]] | None) -> list[Pregunta]:
    """Las candidatas con lo que la persona ya decidió (texto editado incluido) y las preguntas que ella agregó."""
    por_id = {str(item.get("id")): item for item in guardadas or [] if isinstance(item, Mapping)}
    resultado: list[Pregunta] = []
    usadas: set[str] = set()
    for pregunta in candidatas_:
        guardada = por_id.get(pregunta["id"])
        resultado.append(_con_guardado(pregunta, guardada) if guardada else Pregunta(**pregunta))
        usadas.add(pregunta["id"])
    for identidad, guardada in por_id.items():
        if identidad not in usadas and guardada.get("origen") == "persona":
            resultado.append(_con_guardado(Pregunta(id=identidad, texto=limpiar(guardada.get("texto")),
                                                    sugerida=PREGUNTAR, origen="persona"), guardada))
    return resultado


def _con_guardado(pregunta: Pregunta, guardada: Mapping[str, Any]) -> Pregunta:
    decision = str(guardada.get("decision") or "")
    return Pregunta(
        id=pregunta["id"], texto=limpiar(guardada.get("texto")) or pregunta["texto"], sugerida=pregunta["sugerida"],
        origen=pregunta["origen"], decision=decision if decision in DECISIONES else "",
        por=str(guardada.get("por") or ""), en=str(guardada.get("en") or ""),
    )


def elegida(pregunta: Pregunta) -> str:
    """Lo que muestra el selector: la decisión de la persona o, si no ha decidido, la sugerencia del análisis."""
    return pregunta.get("decision") or pregunta["sugerida"]


def decidir(
    actuales: Sequence[Pregunta], elecciones: Mapping[str, tuple[str, str]], nuevas: Iterable[str], por: str, en: str,
) -> list[Pregunta]:
    """Aplica lo que eligió la persona: `elecciones` es id → (decisión, texto editado); `nuevas` son preguntas suyas.

    Una decisión desconocida se rechaza (`ValueError`); una pregunta sin elección conserva lo que tenía. Las que la
    persona agrega son siempre para el cliente.
    """
    resultado: list[Pregunta] = []
    for pregunta in actuales:
        eleccion = elecciones.get(pregunta["id"])
        if eleccion is None:
            resultado.append(Pregunta(**pregunta))
            continue
        decision, texto = eleccion
        if decision not in DECISIONES:
            raise ErrorDeValidacion("No entendimos esa opción. Recarga la página e inténtalo otra vez.")
        resultado.append(Pregunta(
            id=pregunta["id"], texto=limpiar(texto) or pregunta["texto"], sugerida=pregunta["sugerida"],
            origen=pregunta["origen"], decision=decision, por=por, en=en))
    existentes = {p["id"] for p in resultado}
    for texto in nuevas:
        limpio = limpiar(texto)
        if limpio and identificador(limpio) not in existentes:
            resultado.append(Pregunta(id=identificador(limpio), texto=limpio, sugerida=PREGUNTAR, origen="persona",
                                      decision=PREGUNTAR, por=por, en=en))
            existentes.add(identificador(limpio))
    if len(resultado) > MAX_PREGUNTAS:
        raise ErrorDeValidacion(f"Hay demasiadas preguntas (máximo {MAX_PREGUNTAS}).")
    return resultado


_VINETA = re.compile(r"^\s*(?:\d+\s*[.)\-:]|[-*\u2022\u00b7])\s*")


def separar(texto: str) -> list[str]:
    """Las preguntas que una persona escribió, una por línea (U-24).

    Se ignoran las líneas vacías, la numeración o viñeta que traigan y las repetidas. Cada línea es un punto de la
    lista del correo, tal como se escribió.
    """
    resultado: list[str] = []
    vistas: set[str] = set()
    for linea in str(texto or "").splitlines():
        limpia = _VINETA.sub("", linea).strip()[:MAX_TEXTO].rstrip(" .;:")
        if not limpia:
            continue
        clave = identificador(limpia)
        if clave not in vistas:
            vistas.add(clave)
            resultado.append(limpia)
        if len(resultado) >= MAX_PREGUNTAS:
            break
    return resultado


def para_el_cliente(preguntas: Iterable[Pregunta]) -> list[str]:
    """Los textos de las preguntas que la persona decidió hacerle al cliente, en el orden en que aparecen."""
    return [como_peticion(p["texto"]) for p in preguntas if p.get("decision") == PREGUNTAR]


def internas(preguntas: Iterable[Pregunta]) -> list[str]:
    """Los textos de las preguntas que la persona decidió resolver por dentro."""
    return [p["texto"] for p in preguntas if p.get("decision") == INTERNA]


def era_un_acuse(analysis: Mapping[str, Any]) -> bool:
    """¿El último correo enviado desde el dashboard no llevaba preguntas? Un caso antiguo, sin el dato, no lo es."""
    enviadas = analysis.get("preguntas_enviadas")
    return isinstance(enviadas, Mapping) and not enviadas.get("preguntas") and not enviadas.get("esperada")


def hay_pendientes_internos(analysis: Mapping[str, Any]) -> bool:
    """¿La persona decidió resolver por dentro alguna pregunta? Mientras tanto el caso sigue siendo trabajo de Anka."""
    return bool(internas(analysis.get("preguntas") or []))


_FRASE_DE_LISTA = re.compile(r"confirm(?:ar|e|en|arnos)\s+lo\s+siguiente")
_PUNTO_DE_LISTA = re.compile(r"^\s*(?:\d+\s*[.)]|[-*\u2022])\s+(\S.*?)\s*$")


def _puntos_de_la_lista(lineas: Sequence[str]) -> list[str]:
    """Los puntos numerados que siguen a «…confirmar lo siguiente»: lo que el correo pide confirmar."""
    puntos: list[str] = []
    for i, linea in enumerate(lineas):
        if not _FRASE_DE_LISTA.search(_plano(linea)):
            continue
        hay = False
        for siguiente in lineas[i + 1:]:
            if not siguiente.strip():
                if hay:
                    break           # una línea en blanco tras los puntos cierra la lista
                continue
            punto = _PUNTO_DE_LISTA.match(siguiente)
            if not punto:
                break
            hay = True
            puntos.append(" ".join(punto.group(1).split()))
    return puntos


def en_el_correo(cuerpo: str) -> list[str]:
    """Lo que un correo le pide al cliente (enlaces aparte): de ello depende que se espere respuesta.

    Son los puntos de la lista que presenta «¿podría confirmar lo siguiente?» y las preguntas con «?» que lleve fuera de
    ella. La frase que presenta la lista es la pregunta, no un punto más (U-26).
    """
    sin_enlaces = _URL.sub("", cuerpo)
    vistos = _puntos_de_la_lista(sin_enlaces.splitlines())
    for coincidencia in _PREGUNTA.findall(sin_enlaces):
        pregunta = " ".join(coincidencia.split())
        plana = _plano(pregunta)
        if plana == _INTRODUCCION or any(plana in _plano(punto) for punto in vistos):
            continue
        vistos.append(pregunta)
    unicos: list[str] = []
    for punto in vistos:
        if punto not in unicos:
            unicos.append(punto)
    return unicos


# --- Rondas de preguntas (T-59, Fase B): lo que de verdad se envió, aparte de lo que Work propuso ----------
#
# Una pregunta pasa por cuatro momentos que no se confunden: *propuesta* (la candidata del análisis), *aprobada* (la
# persona decidió «preguntar al cliente»; vive en `preguntas`), *enviada* (salió en un correo, con su mensaje de Gmail)
# y
# *atendida*. Solo el envío crea una ronda. Que el cliente haya escrito después se anota en la ronda (`respuesta`); de
# cada pregunta solo se afirma lo que se ve: sigue abierta si el análisis nuevo la vuelve a pedir, y «sin confirmar» si
# no, porque que ya no aparezca no prueba que la hayan respondido.

ENVIADA = "enviada"
ABIERTA = "sigue_abierta"
SIN_CONFIRMAR = "sin_confirmar"
MAX_RONDAS = 20


def huella_de_pregunta(texto: str) -> str:
    """El texto de una pregunta sin tildes, mayúsculas ni puntuación: para reconocer la misma pregunta."""
    return " ".join(re.sub(r"[^\w\s]", " ", _plano(texto)).split())


def nueva_ronda(en: str, mensaje: str, preguntas: Iterable[str], esperada: bool = False) -> dict[str, Any]:
    """La ronda de un correo que ya salió: cuándo, con qué mensaje de Gmail y qué preguntas llevaba."""
    return {"en": en, "mensaje": mensaje, "esperada": bool(esperada), "respuesta": None,
            "preguntas": [{"texto": texto, "estado": ENVIADA} for texto in preguntas]}


def anotar_envio(analysis: dict[str, Any], en: str, mensaje: str, preguntas: Iterable[str], esperada: bool) -> None:
    """Deja en el análisis la última ronda (`preguntas_enviadas`) y la suma al historial (sin duplicar)."""
    texto = list(preguntas)
    analysis["preguntas_enviadas"] = {"en": en, "mensaje": mensaje, "preguntas": texto, "esperada": bool(esperada)}
    rondas = [r for r in analysis.get("rondas_de_preguntas") or [] if isinstance(r, dict)]
    if not any(r.get("mensaje") == mensaje and mensaje for r in rondas):
        rondas.append(nueva_ronda(en, mensaje, texto, esperada))
    analysis["rondas_de_preguntas"] = rondas[-MAX_RONDAS:]


def conservar_rondas(anterior: Mapping[str, Any], nuevo: Any, mensaje_id: str) -> None:
    """Lleva al análisis nuevo lo que se le preguntó al cliente y anota si el mensaje analizado es su respuesta.

    Un expediente anterior a las rondas (solo `preguntas_enviadas`) se convierte en una ronda al pasar por aquí.
    Nunca se inventa una correspondencia: la ronda se liga a UN mensaje posterior a su envío y distinto de él, y
    cada pregunta queda `sigue_abierta` solo si el análisis nuevo la vuelve a pedir.
    """
    enviadas = anterior.get("preguntas_enviadas")
    rondas = [copy.deepcopy(r) for r in anterior.get("rondas_de_preguntas") or [] if isinstance(r, dict)]
    if isinstance(enviadas, Mapping) and enviadas:
        nuevo.setdefault("preguntas_enviadas", copy.deepcopy(dict(enviadas)))
        if not rondas and enviadas.get("preguntas"):
            rondas = [nueva_ronda(str(enviadas.get("en") or ""), str(enviadas.get("mensaje") or ""),
                                  enviadas["preguntas"], bool(enviadas.get("esperada")))]
    if not rondas:
        return
    llegada = instante(str(nuevo.get("received_at") or ""))
    abiertas = {huella_de_pregunta(t) for c in candidatas(nuevo) if c["sugerida"] == PREGUNTAR
                for t in (c["texto"], como_peticion(c["texto"]))}
    for ronda in rondas:
        salida = instante(str(ronda.get("en") or ""))
        if (not ronda.get("respuesta") and llegada and salida and mensaje_id and llegada > salida
                and mensaje_id != ronda.get("mensaje")):
            ronda["respuesta"] = {"mensaje": mensaje_id, "en": str(nuevo.get("received_at"))}
        if ronda.get("respuesta"):
            for pregunta in ronda.get("preguntas") or []:
                pregunta["estado"] = ABIERTA if huella_de_pregunta(pregunta["texto"]) in abiertas else SIN_CONFIRMAR
    nuevo["rondas_de_preguntas"] = rondas[-MAX_RONDAS:]


# --- El historial de rondas tal como lo ve la persona (T-61) ---------------------------------------------------

_ID_DE_GMAIL = re.compile(r"[0-9a-f]{10,32}")
_ESTADOS_DE_PREGUNTA = {
    ENVIADA: ("enviada", "Enviada", "Se envió y todavía no hay una respuesta registrada en esta ronda."),
    ABIERTA: ("abierta", "Sigue abierta",
              "Llegó una respuesta y el análisis nuevo vuelve a pedir este dato."),
    SIN_CONFIRMAR: ("sin-confirmar", "Sin confirmar",
                    "Llegó una respuesta, pero no se verificó que conteste esta: no es lo mismo que resuelta.")}


def _id_verificado(valor: object) -> str:
    """El identificador de un mensaje de Gmail solo si tiene la forma de uno (hexadecimal); si no, vacío."""
    texto = str(valor or "").strip()
    return texto if _ID_DE_GMAIL.fullmatch(texto) else ""


def _fecha_visible(valor: object) -> str:
    return str(valor or "").strip()[:16].replace("T", " ")


def historial_de_rondas(analysis: Mapping[str, Any]) -> list[dict[str, Any]]:
    """Cada correo con preguntas que salió al cliente, en orden cronológico, tal como lo guarda el expediente.

    No une preguntas con partes de la respuesta: solo dice, por pregunta, el estado que ya guardó el análisis. Un
    expediente anterior a las rondas (solo `preguntas_enviadas`) se muestra como una ronda marcada `historica`.
    Función pura: no escribe nada y no inventa un dato que falte.
    """
    rondas = [r for r in analysis.get("rondas_de_preguntas") or [] if isinstance(r, Mapping)]
    historica = False
    enviadas = analysis.get("preguntas_enviadas")
    if not rondas and isinstance(enviadas, Mapping) and enviadas.get("preguntas"):
        historica = True
        rondas = [{"en": enviadas.get("en"), "mensaje": enviadas.get("mensaje"), "respuesta": None,
                   "preguntas": [{"texto": t, "estado": ENVIADA} for t in enviadas["preguntas"]]}]
    vistas = sorted(enumerate(rondas), key=lambda par: (instante(str(par[1].get("en") or "")) or 0, par[0]))
    return [_ronda_visible(n, ronda, historica) for n, (_, ronda) in enumerate(vistas, 1)]


def _ronda_visible(numero: int, ronda: Mapping[str, Any], historica: bool) -> dict[str, Any]:
    respuesta = ronda.get("respuesta") if isinstance(ronda.get("respuesta"), Mapping) else None
    preguntas = []
    for pregunta in ronda.get("preguntas") or []:
        texto = str(pregunta.get("texto") if isinstance(pregunta, Mapping) else pregunta or "").strip()
        estado = str(pregunta.get("estado") or "") if isinstance(pregunta, Mapping) else ""
        clase, titulo, ayuda = _ESTADOS_DE_PREGUNTA.get(
            estado, ("desconocido", estado or "Sin estado", "El expediente no dice en qué quedó esta pregunta."))
        preguntas.append({"texto": texto, "clase": clase, "titulo": titulo, "ayuda": ayuda})
    return {
        "numero": numero, "historica": historica, "preguntas": preguntas,
        "enviada_en": _fecha_visible(ronda.get("en")), "mensaje": str(ronda.get("mensaje") or "").strip(),
        "mensaje_id": _id_verificado(ronda.get("mensaje")),
        "con_respuesta": respuesta is not None,
        "respuesta_en": _fecha_visible(respuesta.get("en")) if respuesta else "",
        "respuesta_mensaje": str(respuesta.get("mensaje") or "").strip() if respuesta else "",
        "respuesta_id": _id_verificado(respuesta.get("mensaje")) if respuesta else ""}
