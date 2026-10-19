"""Tabla acumulada de actividades: cómo una tabla nueva (la de un seguimiento) se integra en la vigente (T-59, Fase B).

Lógica pura y determinista, sin base de datos ni Gmail. Un seguimiento no es un reemplazo: que no repita una actividad
no la elimina, y lo que contradice o sustituye algo ya informado no se aplica solo; queda como propuesta para revisar.

Reglas, en orden:
1. Dos filas son la misma actividad si su nombre coincide sin tildes, mayúsculas ni puntuación. Nada más se fusiona.
2. Una fila anterior que la nueva no menciona se conserva tal cual.
3. Una fila nueva sin pareja ni parecido se agrega.
4. De una pareja, la nueva **llena** un dato vacío o **reemplaza** uno que no estaba confirmado (si la fila nueva no es
   dudosa): eso se aplica y suma la fuente. Un dato ya informado y confirmado que cambia, una contradicción, una
   eliminación pedida en el texto, un posible duplicado o una sustitución ambigua **no** se aplican: la tabla vigente
   queda igual y la propuesta lleva el cambio con su motivo.
"""

from __future__ import annotations

import copy
import re
import unicodedata
from collections.abc import Mapping
from typing import Any, NamedTuple

from anka.dominio.reloj import instante


CAMPOS_DE_DATO = ("quantity", "unit", "ubicacion", "entregable", "plazo_restriccion", "servicio_tecnica")
_SIN_DATO = frozenset({"", "no indicado", "no indica", "sin definir", "por definir", "pendiente", "n/a", "ninguno"})
_ELIMINACION = re.compile(
    r"\bya no se requiere\b|\bno se requiere\b|\bse elimina\b|\beliminar\b|\bcancel(?:a|ar|ada|ado|an)\b|"
    r"\bse descarta\b|\bdescartar\b|\bexcluir\b|\bse excluye\b|\bsin este (?:servicio|alcance)\b")
_PARECIDO_MINIMO = 0.5

Fila = dict[str, Any]       # una fila de la tabla (`Actividad`), sin exigir sus claves


class Integracion(NamedTuple):
    """El resultado de integrar: lo que se aplica ya, lo que se propone y por qué hay que revisar."""

    vigente: list[Any]          # la tabla que queda: la anterior con lo seguro aplicado
    propuesta: list[Any]        # la tabla con TODO aplicado (igual a `vigente` si no hay nada que revisar)
    revisar: list[dict[str, str]]
    aplicado: list[str]         # frases de lo que se aplicó solo (para el historial)


def _plano(texto: object) -> str:
    base = unicodedata.normalize("NFKD", str(texto if texto is not None else "").casefold())
    sin_tildes = "".join(c for c in base if not unicodedata.combining(c))
    return " ".join(re.sub(r"[^\w\s]", " ", sin_tildes).split())


def _vacio(valor: object) -> bool:
    return _plano(valor) in _SIN_DATO


def _clave(fila: Fila) -> str:
    return _plano(fila.get("activity", ""))


def _palabras(fila: Fila) -> set[str]:
    return {p for p in _clave(fila).split() if len(p) > 2}


def _parecidas(a: Fila, b: Fila) -> bool:
    """¿Podrían ser la misma actividad con otro nombre? Solo sirve para NO fusionar y pedir revisión."""
    servicio_a, servicio_b = _plano(a.get("servicio_tecnica")), _plano(b.get("servicio_tecnica"))
    mismo_servicio = bool(servicio_a) and servicio_a == servicio_b
    sitio_a, sitio_b = _plano(a.get("ubicacion")), _plano(b.get("ubicacion"))
    if mismo_servicio and (not sitio_a or not sitio_b or sitio_a == sitio_b):
        return True
    palabras_a, palabras_b = _palabras(a), _palabras(b)
    union = palabras_a | palabras_b
    return bool(union) and len(palabras_a & palabras_b) / len(union) >= _PARECIDO_MINIMO


def _pide_eliminar(fila: Fila) -> bool:
    return bool(_ELIMINACION.search(_plano(fila.get("notes")) + " " + _plano(fila.get("estado"))))


def _fuente_sumada(anterior: str, nueva: str) -> str:
    if not nueva.strip() or _plano(nueva) in _plano(anterior):
        return anterior
    return f"{anterior}; {nueva}".strip("; ") if anterior.strip() else nueva


def _pareja(anterior: Fila, nueva: Fila, revisar: list[dict[str, str]]) -> tuple[Fila, Fila, bool]:
    """(fila vigente, fila propuesta, ¿cambió algo seguro?) para una actividad que está en las dos tablas."""
    nombre = str(anterior.get("activity", ""))
    fuente = str(nueva.get("fuente") or "")
    confirma = _plano(nueva.get("estado")) == "confirmado" and _plano(anterior.get("estado")) != "confirmado"
    # un dato ya informado solo lo cambia una fila nueva no dudosa sobre una anterior que aún no estaba confirmada
    reemplaza = _plano(nueva.get("estado")) in ("confirmado", "") and _plano(anterior.get("estado")) != "confirmado"
    seguros: dict[str, str] = {}
    conflictos: dict[str, str] = {}
    for campo in CAMPOS_DE_DATO:
        antes, ahora = anterior.get(campo), nueva.get(campo)
        if _vacio(ahora) or _plano(antes) == _plano(ahora):
            continue
        if _vacio(antes) or reemplaza:
            seguros[campo] = str(ahora)
        else:
            conflictos[campo] = str(ahora)
    vigente = copy.deepcopy(anterior)
    vigente.update(seguros)
    propuesta = copy.deepcopy(vigente)
    propuesta.update(conflictos)
    for campo, valor in conflictos.items():
        revisar.append({"tipo": "dato_distinto", "actividad": nombre, "campo": campo,
                        "anterior": str(anterior.get(campo) or ""), "propuesto": valor, "fuente": fuente})
    if _plano(nueva.get("estado")) == "contradictorio":
        vigente["estado"] = propuesta["estado"] = "contradictorio"
        revisar.append({"tipo": "contradiccion", "actividad": nombre, "campo": "estado", "anterior": "",
                        "propuesto": "contradictorio", "fuente": fuente})
    elif not conflictos and confirma and _plano(anterior.get("estado")) == "pendiente":
        vigente["estado"] = propuesta["estado"] = "confirmado"
    cambio = vigente != anterior
    if cambio and fuente:
        vigente["fuente"] = propuesta["fuente"] = _fuente_sumada(str(anterior.get("fuente") or ""), fuente)
    elif conflictos and fuente:
        propuesta["fuente"] = _fuente_sumada(str(anterior.get("fuente") or ""), fuente)
    return vigente, propuesta, cambio


def integrar(anterior: list[Any], nueva: list[Any]) -> Integracion:
    """Integra la tabla `nueva` (de un seguimiento) en la `anterior` sin perder nada que nadie quitó a propósito."""
    previas = [(i, f) for i, f in enumerate(anterior) if isinstance(f, dict)]
    candidatas = [f for f in nueva if isinstance(f, dict)]
    revisar: list[dict[str, str]] = []
    aplicado: list[str] = []
    vigente = copy.deepcopy(anterior)
    propuesta = copy.deepcopy(anterior)
    emparejadas: dict[int, int] = {}               # índice anterior -> índice de la lista nueva
    sin_pareja: list[int] = []
    for n, fila in enumerate(candidatas):
        libre = next((i for i, f in previas if i not in emparejadas and _clave(f) == _clave(fila) and _clave(f)), None)
        if libre is not None:
            emparejadas[libre] = n
        else:
            sin_pareja.append(n)               # sin pareja por nombre, o el mismo nombre repetido
    quitar: list[int] = []
    for i, n in emparejadas.items():
        fila = candidatas[n]
        if _pide_eliminar(fila):
            revisar.append({"tipo": "eliminacion_pedida", "actividad": str(anterior[i].get("activity", "")),
                            "campo": "", "anterior": "", "propuesto": "", "fuente": str(fila.get("fuente") or "")})
            quitar.append(i)
            continue
        nuevo_vigente, nuevo_propuesto, cambio = _pareja(anterior[i], fila, revisar)
        vigente[i], propuesta[i] = nuevo_vigente, nuevo_propuesto
        if cambio:
            aplicado.append(f"se actualizó «{anterior[i].get('activity', '')}»")
    vistas = {_clave(f) for _, f in previas}
    for n in sin_pareja:
        fila = candidatas[n]
        nombre = str(fila.get("activity", ""))
        if not _clave(fila):
            revisar.append({"tipo": "sin_nombre", "actividad": nombre, "campo": "", "anterior": "", "propuesto": "",
                            "fuente": str(fila.get("fuente") or "")})
            propuesta.append(copy.deepcopy(fila))
            continue
        if _pide_eliminar(fila):
            revisar.append({"tipo": "eliminacion_sin_coincidencia", "actividad": nombre, "campo": "", "anterior": "",
                            "propuesto": "", "fuente": str(fila.get("fuente") or "")})
            continue
        if _clave(fila) in vistas:
            igual = next((f for _, f in previas if _clave(f) == _clave(fila)), None)
            if igual is not None and all(_plano(igual.get(c)) == _plano(fila.get(c)) for c in CAMPOS_DE_DATO):
                continue                                       # repetida tal cual: no es nada nuevo
            revisar.append({"tipo": "duplicada", "actividad": nombre, "campo": "", "anterior": "", "propuesto": "",
                            "fuente": str(fila.get("fuente") or "")})
            propuesta.append(copy.deepcopy(fila))
            continue
        parecida = next((f for i, f in previas if i not in emparejadas and _parecidas(f, fila)), None)
        if parecida is not None:
            revisar.append({"tipo": "sustitucion_ambigua", "actividad": nombre, "campo": "",
                            "anterior": str(parecida.get("activity", "")), "propuesto": nombre,
                            "fuente": str(fila.get("fuente") or "")})
            propuesta.append(copy.deepcopy(fila))
            continue
        vigente.append(copy.deepcopy(fila))
        propuesta.append(copy.deepcopy(fila))
        aplicado.append(f"se agregó «{nombre}»")
        vistas.add(_clave(fila))
    for i in sorted(quitar, reverse=True):
        del propuesta[i]
    if not revisar:
        propuesta = copy.deepcopy(vigente)
    return Integracion(vigente, propuesta, revisar, aplicado)


def es_seguimiento(primera_vez: str, llegada: str, id_anterior: str, id_nuevo: str) -> bool:
    """¿El análisis es de un mensaje posterior al primero del caso (un seguimiento) y no un reanálisis del original?"""
    desde, hasta = instante(primera_vez), instante(llegada)
    if desde and hasta:
        return hasta > desde
    return id_nuevo != id_anterior


def integrar_seguimiento(anterior: Mapping[str, Any], analysis: Any, primera_vez: str, id_anterior: str,
                         id_nuevo: str) -> Integracion | None:
    """Suma la tabla de un seguimiento a la del caso, dentro de `analysis`; `None` si no corresponde (no es un
    seguimiento o el caso aún no tenía tabla). Con la tabla de una persona (`manual_edit`) o bloqueada por R11 nada se
    aplica solo: todo queda como propuesta pendiente."""
    anteriores = anterior.get("activities")
    bloqueada = isinstance(analysis.get("pending_model_activities"), list)
    nuevas = analysis["pending_model_activities"] if bloqueada else analysis.get("activities")
    if not (isinstance(anteriores, list) and anteriores and isinstance(nuevas, list)
            and es_seguimiento(primera_vez, str(analysis.get("received_at") or ""), id_anterior, id_nuevo)):
        return None
    resultado = integrar(anteriores, nuevas)
    if bloqueada or anterior.get("manual_edit"):
        analysis["activities"] = anteriores
        if resultado.propuesta != anteriores:
            analysis["pending_model_activities"] = resultado.propuesta
        else:
            analysis.pop("pending_model_activities", None)
    else:
        analysis["activities"] = resultado.vigente
        if resultado.revisar:
            analysis["pending_model_activities"] = resultado.propuesta
    if resultado.revisar:
        analysis["revision_de_tabla"] = resultado.revisar
    return resultado


def describir(res: Integracion) -> str:
    """Una frase para el historial con lo que se aplicó solo y lo que queda por revisar."""
    partes = []
    if res.aplicado:
        partes.append("; ".join(res.aplicado))
    if res.revisar:
        partes.append(f"{len(res.revisar)} cambio(s) por revisar: " + "; ".join(
            f"{r['tipo'].replace('_', ' ')} en «{r['actividad']}»" for r in res.revisar[:3]))
    return "Seguimiento: " + ("; ".join(partes) if partes else "sin cambios en la tabla") + "."
