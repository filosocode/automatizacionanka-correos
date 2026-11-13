"""Detección conservadora de cambios de magnitud entre los mensajes entrantes de un hilo (T-69, modo sombra).

Compara, por actividad, lo que cada mensaje del cliente dice de la misma cantidad y unidad, con la procedencia de cada
cifra (id del mensaje, remitente, fecha). Solo describe: nunca elige un valor vigente, nunca dice que Anka aceptó una
corrección ni que cambió la tabla, y un valor más reciente no es por eso el vigente. Lógica pura: no lee Gmail ni la
base.

Qué cuenta como declaración: lo que el cliente escribió de su propia mano en un mensaje recibido. Quedan fuera los
mensajes enviados por Anka, el texto citado de correos anteriores («>», «El … escribió:», «De: … Enviado:») y los
adjuntos, que no se asocian a un mensaje de forma inequívoca.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime, UTC
from decimal import Decimal
from typing import Any

from anka.dominio import contexto_de_cantidad as ctx

RECTIFICACION_EXPLICITA = "RECTIFICACION_EXPLICITA"
CAMBIO_POSIBLE = "CAMBIO_POSIBLE"
NO_COMPROBABLE = "NO_COMPROBABLE"

SEVERIDAD = {RECTIFICACION_EXPLICITA: "aviso", CAMBIO_POSIBLE: "aviso", NO_COMPROBABLE: "info"}
MAX_FRAGMENTO = 160

_N = r"\d+(?:[.,]\d+)*"
_HUECO = r"[^0-9\n]{0,40}?"
_MARCA = r"correct[oa]s?|real(?:es)?|nuev[oa]s?|definitiv[oa]s?|vigentes?"
_VERBO = r"es|son|sera|seran|seria|serian|quedo|queda|quedan"
_PREPOSICION = r"(?:de\s+|a\s+)?"
_RECTIFICACIONES: tuple[re.Pattern[str], ...] = (
    re.compile(rf"\b(?:{_MARCA})\b{_HUECO}\b(?:{_VERBO})\b\s*{_PREPOSICION}(?P<nuevo>{_N}){_HUECO}[,;]?\s*"
               rf"\b(?:y\s+)?no\s+(?:son\s+|es\s+|eran\s+|era\s+)?(?P<viejo>{_N})"),
    re.compile(rf"\bno\s+(?:son|es|eran|era)\s+(?P<viejo>{_N}){_HUECO}\b(?:sino|son|es)\b\s*{_PREPOSICION}"
               rf"(?P<nuevo>{_N})"),
    re.compile(rf"\b(?:en lugar de|en vez de|en reemplazo de)\s+(?P<viejo>{_N}){_HUECO}"
               rf"(?:\b(?:considerar|tomar|usar|utilizar|son|es|sera|seria|poner|dejar)\b)?{_HUECO}(?P<nuevo>{_N})"),
    re.compile(rf"\b(?:correccion|rectificacion|rectifico|corrijo|corregimos|aclaro|aclaramos)\b{_HUECO}(?P<nuevo>{_N})"
               rf"(?:{_HUECO}[,;]?\s*\bno\s+(?:son\s+|es\s+)?(?P<viejo>{_N}))?"),
    re.compile(rf"\b(?:actualizo|actualizamos|modifico|modificamos|ajusto|ajustamos)\b{_HUECO}\ba\s+(?P<nuevo>{_N})"),
)
_ADICIONAL = re.compile(
    r"\b(?:ademas|tambien|adicional(?:es)?|otro|otra|otros|otras|nuevo lote|ampliacion|se suma|sumar)\b")
_ENCABEZADO_DE_CITA = re.compile(
    r"^(?:el\s.{3,250}escribi[oó]\s*:?|on\s.{3,250}wrote\s*:?|-{2,}\s*(?:mensaje original|original message|"
    r"forwarded message|mensaje reenviado)\b.*|_{5,})$", re.IGNORECASE)
_CABECERA_DE_REENVIO = re.compile(r"^(?:de|from)\s*:", re.IGNORECASE)
_DATOS_DE_REENVIO = re.compile(r"^(?:enviado|sent|fecha|date|asunto|subject|para|to)\s*:", re.IGNORECASE)
_CORREO = re.compile(r"[^@\s<>]+@[^@\s<>]+\.[^@\s<>]+")


def sin_citas(texto: str) -> str:
    """El texto que el cliente escribió en ese mensaje, sin el correo anterior que su programa de correo cita debajo."""
    lineas = texto.splitlines()
    salida: list[str] = []
    for i, linea in enumerate(lineas):
        limpia = linea.strip()
        if limpia.startswith(">"):
            continue
        siguiente = lineas[i + 1].strip() if i + 1 < len(lineas) else ""
        if (_ENCABEZADO_DE_CITA.match(limpia) or _ENCABEZADO_DE_CITA.match(f"{limpia} {siguiente}")
                or (_CABECERA_DE_REENVIO.match(limpia)
                    and any(_DATOS_DE_REENVIO.match(otra.strip()) for otra in lineas[i + 1:i + 5]))):
            break                         # de aquí en adelante todo es el correo citado
        salida.append(linea)
    return "\n".join(salida)


@dataclass(frozen=True)
class Valor:
    """Una cifra leída con sus valores posibles y su clase de unidad."""

    valores: frozenset[Decimal]
    ambiguo: bool
    unidad: str | None
    token: str

    def distinto_de(self, otro: Valor) -> bool | None:
        """¿Son valores distintos? `None` si una cifra ambigua impide afirmarlo."""
        if self.valores & otro.valores:
            return False
        return None if (self.ambiguo or otro.ambiguo) else True


@dataclass
class Mensaje:
    """Un mensaje recibido del hilo, ya limpio de citas, con su procedencia."""

    orden: int
    message_id: str
    fecha: str
    remitente: str
    texto: str
    segmentos: list[ctx.Segmento] = field(default_factory=list)


@dataclass
class Declaracion:
    """Una cantidad dicha por el cliente en un mensaje: no es una rectificación."""

    mensaje: Mensaje
    valor: Valor
    linea: ctx.Linea
    raices: frozenset[str]
    adicional: bool


@dataclass
class Rectificacion:
    """Una frase que corrige explícitamente una cantidad (nuevo valor y, si lo dice, el anterior)."""

    mensaje: Mensaje
    nuevo: Valor
    viejo: Valor | None
    linea: ctx.Linea
    raices: frozenset[str]


def _instante(fecha: object) -> datetime | None:
    try:
        valor = datetime.fromisoformat(str(fecha))
    except ValueError:
        return None
    return valor if valor.tzinfo else valor.replace(tzinfo=UTC)


def _recibidos(mensajes: Sequence[Any], propias: frozenset[str]) -> tuple[list[Mensaje], list[str]]:
    """Los mensajes del cliente en orden cronológico y los motivos por los que no se puede confiar en la cronología."""
    problemas: list[str] = []
    crudos: list[tuple[datetime | None, int, Mapping[str, Any]]] = []
    for posicion, m in enumerate(mensajes):
        if not isinstance(m, Mapping):
            continue
        remitente = str(m.get("remitente") or "").strip().casefold()
        if m.get("direccion") != "received" or remitente in propias:
            continue                      # un mensaje enviado por Anka nunca prueba lo que el cliente dijo
        crudos.append((_instante(m.get("fecha")), posicion, m))
    if any(fecha is None for fecha, _, _ in crudos):
        problemas.append("falta la fecha verificable de algún mensaje: no se puede ordenar")
    crudos.sort(key=lambda t: (t[0] or datetime.min.replace(tzinfo=UTC), t[1]))
    vistos: set[str] = set()
    salida: list[Mensaje] = []
    for orden, (_, _, m) in enumerate(crudos):
        identidad = str(m.get("message_id") or "").strip()
        if not identidad:
            problemas.append("un mensaje no trae identificador")
        elif identidad in vistos:
            problemas.append(f"el mensaje {identidad} está repetido")
        vistos.add(identidad)
        if not _CORREO.fullmatch(str(m.get("remitente") or "").strip()):
            problemas.append(f"el remitente del mensaje {identidad or orden + 1} no es verificable")
        if m.get("truncado"):
            problemas.append(f"el texto del mensaje {identidad or orden + 1} está incompleto")
        salida.append(Mensaje(orden, identidad, str(m.get("fecha") or ""), str(m.get("remitente") or "").strip(),
                              sin_citas(str(m.get("texto") or ""))))
    return salida, list(dict.fromkeys(problemas))


def _valor(norm: str, token: str, fin: int) -> Valor:
    valores, ambiguo = ctx.valores(token)
    return Valor(valores, ambiguo, ctx.leer_unidad(norm[fin:fin + 40]), token)


def _rectificaciones_de(linea: ctx.Linea, segmento: ctx.Segmento, mensaje: Mensaje) -> list[Rectificacion]:
    norm = ctx.normalizar(linea.texto)
    for patron in _RECTIFICACIONES:
        hallado = patron.search(norm)
        if not hallado:
            continue
        nuevo = _valor(norm, hallado.group("nuevo"), hallado.end("nuevo"))
        grupos = patron.groupindex
        viejo = (_valor(norm, hallado.group("viejo"), hallado.end("viejo"))
                 if "viejo" in grupos and hallado.group("viejo") else None)
        if not nuevo.valores or (viejo is not None and not viejo.valores):
            continue
        if nuevo.unidad is None and viejo is not None:
            nuevo = Valor(nuevo.valores, nuevo.ambiguo, viejo.unidad, nuevo.token)
        if nuevo.unidad is None:
            continue
        marcador = ctx.Candidato(frozenset(), False, None, "", linea, segmento)
        return [Rectificacion(mensaje, nuevo, viejo, linea, frozenset(ctx._raices_de(marcador)))]
    return []


def _leer(mensaje: Mensaje) -> tuple[list[Declaracion], list[Rectificacion]]:
    mensaje.segmentos = ctx.segmentar("mensaje", mensaje.texto, "correo")
    declaraciones: list[Declaracion] = []
    rectificaciones: list[Rectificacion] = []
    for segmento in mensaje.segmentos:
        for linea in segmento.lineas:
            rectificada = _rectificaciones_de(linea, segmento, mensaje)
            if rectificada:
                rectificaciones.extend(rectificada)
                continue
            adicional = bool(_ADICIONAL.search(ctx.normalizar(linea.texto)))
            for c in ctx._numeros(linea, segmento):
                if c.unidad is not None:
                    declaraciones.append(Declaracion(mensaje, Valor(c.valores, c.ambiguo, c.unidad, c.token), linea,
                                                     frozenset(ctx._raices_de(c)), adicional))
    return declaraciones, rectificaciones


def _fragmento(linea: ctx.Linea) -> str:
    return " ".join(linea.texto.split())[:MAX_FRAGMENTO]


@dataclass
class _Actividad:
    indice: int
    datos: Mapping[str, Any]
    valor: Valor
    anclas: ctx.Anclas
    unidad: str | None


def _actividades(actividades: Sequence[Any]) -> list[_Actividad]:
    salida = []
    for indice, datos in enumerate(list(actividades)[:ctx.MAX_ACTIVIDADES], 1):
        if not isinstance(datos, Mapping):
            continue
        cantidad = ctx._cantidad(datos)
        if cantidad is None:
            continue
        unidad = ctx.unidad_citada(datos.get("unit"), datos.get("quantity"))
        salida.append(_Actividad(indice, datos, Valor(cantidad[0], cantidad[1], unidad, cantidad[2]),
                                 ctx.anclas_de(datos), unidad))
    return salida


def _compatible(actividad: _Actividad, *valores: Valor | None) -> bool:
    unidades = {v.unidad for v in valores if v is not None}
    return actividad.unidad is None or actividad.unidad in unidades


def _duenos_por_nombre(rectificacion: Rectificacion, actividades: Sequence[_Actividad]) -> list[_Actividad]:
    return [a for a in actividades
            if _compatible(a, rectificacion.nuevo, rectificacion.viejo) and a.anclas.fuerte(set(rectificacion.raices))]


def _conocidos(actividad: _Actividad, antes: Sequence[Declaracion]) -> set[Decimal]:
    """Los valores que esta actividad ya tenía: los de la tabla y los que el cliente le dijo antes con su nombre."""
    propios = {v for d in antes if d.valor.unidad == actividad.unidad and actividad.anclas.fuerte(set(d.raices))
               for v in d.valor.valores}
    return propios | set(actividad.valor.valores)


def _duenos_por_valor(rectificacion: Rectificacion, actividades: Sequence[_Actividad],
                      declaraciones: Sequence[Declaracion]) -> list[_Actividad]:
    viejo = rectificacion.viejo
    if viejo is None:
        return []
    antes = [d for d in declaraciones if d.mensaje.orden < rectificacion.mensaje.orden]
    return [a for a in actividades
            if (a.unidad is None or a.unidad == viejo.unidad) and viejo.valores & _conocidos(a, antes)]


def _origen(actividad: _Actividad, rectificacion: Rectificacion, declaraciones: Sequence[Declaracion],
            estricto: bool) -> Declaracion | None:
    """La declaración anterior más cercana que dio el valor que se corrige, con su mensaje."""
    previas = [d for d in declaraciones if d.mensaje.orden < rectificacion.mensaje.orden and not d.adicional
               and (actividad.unidad is None or d.valor.unidad == actividad.unidad)]
    viejo = rectificacion.viejo
    for d in sorted(previas, key=lambda d: d.mensaje.orden, reverse=True):
        if estricto and not actividad.anclas.fuerte(set(d.raices)):
            continue
        if viejo is None or viejo.valores & d.valor.valores:
            return d
    return None


def _clasificar_rectificacion(actividad: _Actividad, rectificacion: Rectificacion, grado: str,
                              declaraciones: Sequence[Declaracion]) -> dict[str, Any] | None:
    origen = _origen(actividad, rectificacion, declaraciones, estricto=(grado == "especifica"))
    anterior = rectificacion.viejo or (origen.valor if origen else None)
    if anterior is None:
        return None
    nuevo = rectificacion.nuevo
    valor_distinto = nuevo.distinto_de(anterior)
    unidad_distinta = nuevo.unidad != anterior.unidad
    if valor_distinto is None:
        return _sin_comparar(actividad, "una de las cifras tiene un separador ambiguo: no se afirma que cambie")
    if not valor_distinto and not unidad_distinta:
        return None
    cambio = "valor_y_unidad" if valor_distinto and unidad_distinta else "unidad" if unidad_distinta else "valor"
    explicita = grado in ("especifica", "por_valor_anterior")
    clasificacion = RECTIFICACION_EXPLICITA if explicita else CAMBIO_POSIBLE
    evidencia = {"especifica": "alta", "por_valor_anterior": "media"}.get(grado, "baja")
    motivo = {
        "especifica": "el cliente corrige la cantidad nombrando la actividad o su concepto",
        "por_valor_anterior": "el cliente corrige un valor que coincide con el de esta actividad y de ninguna otra",
        "unidad_unica": "hay una frase de corrección y solo esta actividad usa esa unidad; no se nombra la actividad",
    }[grado]
    if origen is None:
        motivo += "; el mensaje donde se dijo el valor anterior no se localizó"
        if explicita:
            evidencia = "media" if grado == "especifica" else "baja"
    return _resultado(clasificacion, actividad, motivo, cambio=cambio, evidencia=evidencia, anterior=anterior,
                      posterior=nuevo, origen=origen.mensaje if origen else None,
                      fragmento_anterior=_fragmento(origen.linea) if origen else
                      (_fragmento(rectificacion.linea) if rectificacion.viejo else ""),
                      posterior_mensaje=rectificacion.mensaje, fragmento_posterior=_fragmento(rectificacion.linea))


def _resultado(clasificacion: str, actividad: _Actividad, motivo: str, *, cambio: str = "", evidencia: str = "ninguno",
               anterior: Valor | None = None, posterior: Valor | None = None, origen: Mensaje | None = None,
               posterior_mensaje: Mensaje | None = None, fragmento_anterior: str = "",
               fragmento_posterior: str = "") -> dict[str, Any]:
    en_tabla = "sin_cifra"
    if anterior is not None and posterior is not None:
        mismas = [bool(actividad.valor.valores & v.valores) and actividad.unidad == v.unidad
                  for v in (anterior, posterior)]
        en_tabla = ("coincide_con_anterior" if mismas[0] and not mismas[1] else
                    "coincide_con_posterior" if mismas[1] and not mismas[0] else "no_decidible")
    return {
        "actividad": actividad.indice, "nombre": str(actividad.datos.get("activity") or "")[:60],
        "concepto": str(actividad.datos.get("quantity") or "")[:60],
        "clasificacion": clasificacion, "severidad": SEVERIDAD[clasificacion], "cambio": cambio,
        "evidencia": evidencia,
        "message_id_origen": origen.message_id if origen else "", "fecha_origen": origen.fecha if origen else "",
        "message_id_posterior": posterior_mensaje.message_id if posterior_mensaje else "",
        "fecha_posterior": posterior_mensaje.fecha if posterior_mensaje else "",
        "remitente_posterior": posterior_mensaje.remitente if posterior_mensaje else "",
        "valor_anterior": _texto(anterior), "valor_posterior": _texto(posterior),
        "unidad_anterior": (anterior.unidad or "") if anterior else "",
        "unidad_posterior": (posterior.unidad or "") if posterior else "",
        "fragmento_anterior": fragmento_anterior, "fragmento_posterior": fragmento_posterior,
        "tabla": en_tabla, "vigencia": "no_determinada", "motivo": motivo,
    }


def _texto(valor: Valor | None) -> str:
    if valor is None:
        return ""
    return " o ".join(format(v.normalize(), "f") for v in sorted(valor.valores))


def _sin_comparar(actividad: _Actividad, motivo: str) -> dict[str, Any]:
    return _resultado(NO_COMPROBABLE, actividad, motivo)


def _por_declaraciones(actividad: _Actividad, declaraciones: Sequence[Declaracion],
                       actividades: Sequence[_Actividad]) -> dict[str, Any] | None:
    """Compara lo que el cliente dijo de esta actividad en mensajes distintos, sin elegir cuál vale."""
    propias = [d for d in declaraciones if d.valor.unidad == actividad.unidad
               and actividad.anclas.fuerte(set(d.raices))]
    compartidas = [d for d in propias if any(o is not actividad and o.unidad == actividad.unidad
                                             and o.anclas.fuerte(set(d.raices)) for o in actividades)]
    if compartidas and _difieren(propias):
        return _sin_comparar(actividad, "varias actividades semejantes comparten las mismas palabras: no se puede "
                                        "atribuir la cifra a una sola")
    candidatas = [d for d in propias if not d.adicional]
    por_mensaje: dict[int, list[Declaracion]] = {}
    for d in candidatas:
        por_mensaje.setdefault(d.mensaje.orden, []).append(d)
    ordenes = sorted(por_mensaje)
    for anterior_orden, posterior_orden in reversed(list(zip(ordenes, ordenes[1:], strict=False))):
        antes, despues = por_mensaje[anterior_orden], por_mensaje[posterior_orden]
        valores_antes = {v for d in antes for v in d.valor.valores}
        valores_despues = {v for d in despues for v in d.valor.valores}
        if valores_antes & valores_despues:
            continue
        if len({tuple(sorted(d.valor.valores)) for d in antes}) > 1 or len(
                {tuple(sorted(d.valor.valores)) for d in despues}) > 1:
            return _sin_comparar(actividad, "un mismo mensaje da varios valores para este concepto: no se puede "
                                            "decidir cuál es el cambio")
        a, d = antes[0], despues[0]
        if a.valor.distinto_de(d.valor) is None:
            return _sin_comparar(actividad, "una de las cifras tiene un separador ambiguo: no se afirma que cambie")
        return _resultado(CAMBIO_POSIBLE, actividad, "dos mensajes del cliente dan valores distintos para el mismo "
                          "concepto y unidad; ninguno dice que corrija al otro", cambio="valor", evidencia="media",
                          anterior=a.valor, posterior=d.valor, origen=a.mensaje, posterior_mensaje=d.mensaje,
                          fragmento_anterior=_fragmento(a.linea), fragmento_posterior=_fragmento(d.linea))
    return None


def _difieren(declaraciones: Sequence[Declaracion]) -> bool:
    conjuntos = [frozenset(v for d in declaraciones if d.mensaje.orden == o for v in d.valor.valores)
                 for o in sorted({d.mensaje.orden for d in declaraciones})]
    return any(not (a & b) for a, b in zip(conjuntos, conjuntos[1:], strict=False))


def detectar(actividades: Sequence[Any], mensajes: Any, thread_id: str = "",
             propias: Sequence[str] = ()) -> list[dict[str, Any]]:
    """Los posibles cambios de magnitud entre mensajes entrantes del hilo, una observación por actividad (modo sombra).

    `mensajes`: lista de `{message_id, fecha, remitente, direccion, texto, truncado}`. Sin al menos dos mensajes
    recibidos no hay nada que comparar. Si falta identidad, remitente, fecha o texto completo de algún mensaje la
    observación es `NO_COMPROBABLE`. Nunca elige un valor vigente.
    """
    if not isinstance(mensajes, Sequence) or isinstance(mensajes, (str, bytes)):
        return []
    recibidos, problemas = _recibidos(mensajes, frozenset(p.strip().casefold() for p in propias if p))
    if len(recibidos) < 2:
        return []
    lista = _actividades(actividades)
    if problemas:
        return [{"thread_id": thread_id, **_sin_comparar(a, "; ".join(problemas))} for a in lista]
    declaraciones: list[Declaracion] = []
    rectificaciones: list[Rectificacion] = []
    for mensaje in recibidos:
        d, r = _leer(mensaje)
        declaraciones.extend(d)
        rectificaciones.extend(r)
    atribuidas: dict[int, list[tuple[Rectificacion, str]]] = {}
    semejantes: set[int] = set()
    for rectificacion in rectificaciones:
        grado, duenos = "especifica", _duenos_por_nombre(rectificacion, lista)
        if not duenos:
            grado, duenos = "por_valor_anterior", _duenos_por_valor(rectificacion, lista, declaraciones)
        if not duenos:
            grado = "unidad_unica"
            duenos = [a for a in lista if a.unidad is not None
                      and a.unidad in (rectificacion.nuevo.unidad, rectificacion.viejo.unidad if rectificacion.viejo
                                       else None)]
            duenos = duenos if len(duenos) == 1 else []
        if len(duenos) > 1:
            semejantes.update(a.indice for a in duenos)
        if len(duenos) == 1:
            atribuidas.setdefault(duenos[0].indice, []).append((rectificacion, grado))
    observaciones = []
    for a in lista:
        resultado: dict[str, Any] | None = None
        if a.indice in semejantes:
            resultado = _sin_comparar(a, "una frase de corrección podría referirse a más de una actividad semejante")
        elif a.indice in atribuidas:
            ultima, grado = max(atribuidas[a.indice], key=lambda t: t[0].mensaje.orden)
            resultado = _clasificar_rectificacion(a, ultima, grado, declaraciones)
        if resultado is None and a.indice not in semejantes:
            resultado = _por_declaraciones(a, declaraciones, lista)
        if resultado is not None:
            observaciones.append({"thread_id": thread_id, **resultado})
    return observaciones
