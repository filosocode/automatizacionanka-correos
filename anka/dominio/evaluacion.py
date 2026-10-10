"""Medir el análisis contra lo esperado (T-34): comparar un caso, resumir varios y reconocer de qué habla una pregunta.

Lógica pura. Una ficha dice qué se esperaba de una solicitud real (si lo es, dónde, qué actividades con qué cantidad y
unidad, y qué datos faltaban); aquí se compara con lo que el análisis devolvió. La comparación es tolerante con la
redacción (tildes, mayúsculas, sinónimos de unidad) y estricta con lo que importa: la cantidad y su unidad.
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Iterable, Mapping, Sequence
from typing import Any, TypedDict

from anka.dominio.activities import numero_de_cantidad, unidad_canonica


class ActividadEsperada(TypedDict, total=False):
    """Una actividad que debía salir: de qué servicio, cuánto y en qué unidad."""

    servicio: str
    cantidad: float | None
    unidad: str


class Esperado(TypedDict, total=False):
    """Lo que una persona validó que debía salir de una solicitud."""

    es_solicitud: bool
    ubicacion: list[str]               # lugares que deben aparecer (municipio, departamento…)
    actividades: list[ActividadEsperada]
    faltante: list[str]                # temas que la solicitud no traía y debían preguntarse (claves de TEMAS)


# De qué habla una pregunta: tema → fragmentos (sin tildes y en minúsculas) que lo delatan.
TEMAS: dict[str, tuple[str, ...]] = {
    "ubicacion": ("ubicacion", "localiz", "municipio", "departamento", "donde", "coordenad", "poligono", "kmz", "kml"),
    "magnitud": ("area", "longitud", "magnitud", "cantidad", "hectarea", "extension", "kilometro", "tamano"),
    "plazo": ("plazo", "fecha", "cronograma"),
    "entregables": ("entregable", "formato"),
    "precision": ("precision", "exactitud", "tolerancia", "resolucion", "densidad"),
    "objetivo": ("objetivo", "finalidad", "proposito", "para que"),
    "sistema": ("datum", "crs", "sistema de coordenadas", "sistema de referencia", "magna"),
    "acceso": ("acceso", "permiso", "restriccion"),
}
# Raíces tan generales que coincidir en ellas no dice que dos actividades sean la misma.
_RAICES_GENERICAS = frozenset({"levan", "servi", "estud", "traba", "activ", "proye", "reali", "elabo"})
_PALABRAS_VACIAS = frozenset({"para", "como", "desde", "entre", "sobre", "dentro", "todo", "toda"})
_UNIDAD_TRAS_NUMERO = re.compile(r"[\d.,]+\s*([^\W\d_]+²?|m2|km2)", re.IGNORECASE)


def _plano(texto: object) -> str:
    descompuesto = unicodedata.normalize("NFD", str(texto or ""))
    return " ".join("".join(c for c in descompuesto if unicodedata.category(c) != "Mn").casefold().split())


def numero(texto: object) -> float | None:
    """La primera cantidad de un texto («544,5 ha», «1.200 m», «12.5»), o None si no hay ninguna."""
    return numero_de_cantidad(texto)


def _unidad(cantidad: object, unidad: object) -> str:
    """La unidad escrita aparte o, si no, la que viene pegada a la cantidad («100 ha»), ya unificada."""
    escrita = str(unidad or "").strip()
    if not escrita or _plano(escrita) in ("no indicado", "n/a"):
        pegada = _UNIDAD_TRAS_NUMERO.search(str(cantidad or ""))
        escrita = pegada.group(1) if pegada else ""
    return _plano(unidad_canonica(escrita))


_CIFRA_CON_UNIDAD = re.compile(
    r"\d[\d.,]*\s*(?:ha\b|hect|km|kil[oó]m|m2|m\u00b2|metros|\bm\b|puntos|estaciones)",
    re.IGNORECASE)


def temas_en(texto: object, lugares: Iterable[str] = ()) -> set[str]:
    """Los temas de los que habla un texto (una solicitud, una pregunta).

    Además de las palabras clave, la ubicación cuenta si el texto nombra alguno de los `lugares` y la magnitud si
    trae una cifra con su unidad: «en Bogotá» o «120 ha» dicen dónde y cuánto sin decir «ubicación» ni «área».
    """
    plano = _plano(texto)
    temas = {tema for tema, claves in TEMAS.items() if any(clave in plano for clave in claves)}
    if any(re.search(rf"(?<!\w){re.escape(_plano(lugar))}(?!\w)", plano) for lugar in lugares if _plano(lugar)):
        temas.add("ubicacion")
    if _CIFRA_CON_UNIDAD.search(str(texto or "")):
        temas.add("magnitud")
    return temas


def temas_ausentes(texto: object, lugares: Iterable[str] = ()) -> list[str]:
    """Los temas de los que la solicitud no dice nada: lo que un buen análisis tendría que preguntar."""
    return sorted(set(TEMAS) - temas_en(texto, lugares))


MAX_FILAS_DE_TABLA = 25
_ENCABEZADO_DESCRIPCION = re.compile(r"^(descripcion|actividad|concepto|servicio|detalle|alcance)\b")
_ENCABEZADO_UNIDAD = re.compile(r"^(unidad|und|u/?m|un)\.?$")
_ENCABEZADO_CANTIDAD = re.compile(r"^(cantidad|cant)\.?(\s.*)?$")
_ROTULO_DE_LUGAR = re.compile(r"^(ubicacion|localizacion|municipio|departamento|lugar)\b")
_SOLO_NUMERO = re.compile(r"[\d.,]+")


def _columna(celdas: Sequence[str], patron: re.Pattern[str]) -> int | None:
    return next((i for i, celda in enumerate(celdas) if patron.match(celda)), None)


def _cantidad_de_celda(valor: object) -> float | None:
    if isinstance(valor, bool):
        return None
    if isinstance(valor, (int, float)):
        return float(valor) if valor > 0 else None
    if isinstance(valor, str) and _SOLO_NUMERO.fullmatch(valor.strip()):
        cantidad = numero(valor)
        return cantidad if cantidad and cantidad > 0 else None
    return None


def extraer_tabla(filas: Sequence[Sequence[object]]) -> list[ActividadEsperada]:
    """Las actividades de la tabla de cantidades de una hoja: filas con descripción, unidad y una cantidad positiva.

    La tabla se reconoce por sus encabezados (descripción, unidad y cantidad, en cualquier orden y con otras columnas
    entre medias). Una tabla de más de `MAX_FILAS_DE_TABLA` filas no se usa, y una hoja sin ella da una lista vacía.
    """
    for indice, fila in enumerate(filas[:40]):
        celdas = [_plano(celda) for celda in fila]
        descripcion = _columna(celdas, _ENCABEZADO_DESCRIPCION)
        unidad = _columna(celdas, _ENCABEZADO_UNIDAD)
        cantidad = _columna(celdas, _ENCABEZADO_CANTIDAD)
        if descripcion is None or unidad is None or cantidad is None:
            continue
        actividades: list[ActividadEsperada] = []
        vistas: set[tuple[str, float, str]] = set()
        for resto in filas[indice + 1:]:
            if len(resto) <= max(descripcion, unidad, cantidad):
                continue
            texto, medida, cuanto = resto[descripcion], resto[unidad], _cantidad_de_celda(resto[cantidad])
            if not (isinstance(texto, str) and len(texto.strip()) >= 3 and isinstance(medida, str) and medida.strip()
                    and cuanto):
                continue
            actividad = ActividadEsperada(servicio=texto.strip()[:120], cantidad=cuanto,
                                          unidad=unidad_canonica(medida.strip()))
            clave = (actividad["servicio"], cuanto, actividad["unidad"])
            if clave not in vistas:
                vistas.add(clave)
                actividades.append(actividad)
        return actividades if len(actividades) <= MAX_FILAS_DE_TABLA else []
    return []


def lugares_declarados(filas: Sequence[Sequence[object]]) -> list[str]:
    """Los lugares que la hoja declara en celdas rotuladas («Municipio», «Ubicación»…): la celda que sigue."""
    lugares: list[str] = []
    for fila in filas[:200]:
        for i, celda in enumerate(fila):
            if _ROTULO_DE_LUGAR.match(_plano(celda)):
                valor = next((str(c).strip() for c in fila[i + 1:] if isinstance(c, str) and c.strip()), "")
                if valor and valor not in lugares:
                    lugares.append(valor)
                break
    return lugares


def temas_preguntados(preguntas: Iterable[str]) -> set[str]:
    """Los temas de los que preguntan unas preguntas al cliente."""
    temas: set[str] = set()
    for pregunta in preguntas:
        texto = _plano(pregunta)
        temas.update(tema for tema, claves in TEMAS.items() if any(clave in texto for clave in claves))
    return temas


def _raices(texto: object) -> set[str]:
    palabras = re.findall(r"[a-z]{4,}", _plano(texto))
    return {p[:5] for p in palabras if p not in _PALABRAS_VACIAS} - _RAICES_GENERICAS


def _misma_cantidad(esperada: Mapping[str, Any], obtenida: Mapping[str, Any]) -> bool:
    cantidad = esperada.get("cantidad")
    valor = numero(obtenida.get("quantity"))
    if cantidad is None or valor is None:
        return False
    cerca = abs(valor - float(cantidad)) <= max(abs(float(cantidad)) * 0.005, 1e-9)
    unidad_esperada = _plano(unidad_canonica(esperada.get("unidad", "")))
    return cerca and _unidad(obtenida.get("quantity"), obtenida.get("unit")) == unidad_esperada


def _emparejar(
    esperadas: Sequence[Mapping[str, Any]], obtenidas: Sequence[Mapping[str, Any]],
) -> list[tuple[int, int]]:
    """Cada esperada con a lo más una obtenida, la que más se le parece: (índice de la esperada, de la obtenida)."""
    usadas: set[int] = set()
    pares: list[tuple[int, int]] = []
    for i, esperada in enumerate(esperadas):
        raices = _raices(esperada.get("servicio"))
        mejor, mejor_puntaje = None, 0
        for j, obtenida in enumerate(obtenidas):
            if j in usadas:
                continue
            nombre = f"{obtenida.get('activity', '')} {obtenida.get('servicio_tecnica', '')}"
            servicio = bool(raices & _raices(nombre))
            cantidad = _misma_cantidad(esperada, obtenida)
            puntaje = (2 if servicio and cantidad else 1 if servicio or cantidad else 0)
            if puntaje > mejor_puntaje:
                mejor, mejor_puntaje = j, puntaje
        if mejor is not None:
            usadas.add(mejor)
            pares.append((i, mejor))
    return pares


def _texto_del_analisis(analisis: Mapping[str, Any]) -> str:
    partes = [analisis.get("project_name"), analisis.get("summary")]
    for actividad in analisis.get("activities", []) or []:
        if isinstance(actividad, Mapping):
            partes.extend(str(valor) for valor in actividad.values())
    for campo in analisis.get("fields", []) or []:
        if isinstance(campo, Mapping):
            partes.append(campo.get("value"))
    return _plano(" ".join(str(p) for p in partes if p))


def _fraccion_de_lugares(lugares: Sequence[str], texto: str) -> float | None:
    if not lugares:
        return None
    halladas = sum(bool(re.search(rf"(?<!\w){re.escape(_plano(lugar))}(?!\w)", texto)) for lugar in lugares)
    return halladas / len(lugares)


def comparar(esperado: Mapping[str, Any], analisis: Mapping[str, Any], preguntas: Sequence[str]) -> dict[str, Any]:
    """El resultado de un caso: qué acertó el análisis frente a lo que una persona validó que debía salir."""
    obtenidas = [a for a in analisis.get("activities", []) or []
                 if isinstance(a, Mapping) and _plano(a.get("activity")) not in ("", "no indicado")]
    esperadas = list(esperado.get("actividades", []) or [])
    pares = _emparejar(esperadas, obtenidas)
    con_cantidad = [i for i, e in enumerate(esperadas) if e.get("cantidad") is not None]
    correctas = sum(1 for i, j in pares if i in con_cantidad and _misma_cantidad(esperadas[i], obtenidas[j]))
    faltante = set(esperado.get("faltante", []) or [])
    temas = temas_preguntados(preguntas)
    return {
        "identifica": bool(analisis.get("is_quote_request")) == bool(esperado.get("es_solicitud", True)),
        "actividades_esperadas": len(esperadas),
        "actividades_encontradas": len(pares),
        "actividades_obtenidas": len(obtenidas),
        "cantidades_esperadas": len(con_cantidad),
        "cantidades_correctas": correctas,
        "ubicacion": _fraccion_de_lugares(esperado.get("ubicacion", []) or [], _texto_del_analisis(analisis)),
        "preguntas_acertadas": len(temas & faltante),
        "preguntas_de_mas": len(temas - faltante),
        "preguntas_omitidas": len(faltante - temas),
    }


def _razon(parte: float, total: float) -> float | None:
    return round(parte / total, 3) if total else None


def estabilidad(resultados: Sequence[Mapping[str, Any]]) -> float | None:
    """De los casos corridos varias veces, qué parte dio lo mismo en todas sus corridas; None si ninguno se repitió."""
    por_caso: dict[str, list[tuple[Any, ...]]] = {}
    for r in resultados:
        huella = (r["identifica"], r["actividades_encontradas"], r["actividades_obtenidas"], r["cantidades_correctas"])
        por_caso.setdefault(str(r["id"]), []).append(huella)
    repetidos = [huellas for huellas in por_caso.values() if len(huellas) > 1]
    if not repetidos:
        return None
    return round(sum(len(set(huellas)) == 1 for huellas in repetidos) / len(repetidos), 3)


def resumir(resultados: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """Los resultados de varios casos en cifras globales; None donde no hay nada que medir."""
    suma = {clave: sum(int(r[clave]) for r in resultados) for clave in (
        "actividades_esperadas", "actividades_encontradas", "actividades_obtenidas", "cantidades_esperadas",
        "cantidades_correctas", "preguntas_acertadas", "preguntas_de_mas", "preguntas_omitidas")} if resultados else {}
    ubicaciones = [r["ubicacion"] for r in resultados if r["ubicacion"] is not None]
    if not resultados:
        return {"casos": 0, "identificacion": None, "actividades_recall": None, "actividades_precision": None,
                "cantidades": None, "ubicacion": None, "preguntas_precision": None, "preguntas_recall": None}
    return {
        "casos": len(resultados),
        "identificacion": _razon(sum(bool(r["identifica"]) for r in resultados), len(resultados)),
        "actividades_recall": _razon(suma["actividades_encontradas"], suma["actividades_esperadas"]),
        "actividades_precision": _razon(suma["actividades_encontradas"], suma["actividades_obtenidas"]),
        "cantidades": _razon(suma["cantidades_correctas"], suma["cantidades_esperadas"]),
        "ubicacion": _razon(sum(ubicaciones), len(ubicaciones)),
        "preguntas_precision": _razon(suma["preguntas_acertadas"],
                                      suma["preguntas_acertadas"] + suma["preguntas_de_mas"]),
        "preguntas_recall": _razon(suma["preguntas_acertadas"],
                                   suma["preguntas_acertadas"] + suma["preguntas_omitidas"]),
    }
