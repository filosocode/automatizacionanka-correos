"""Compara la extracción de un sistema con la referencia de un caso y clasifica los errores (T-71).

Formato de la extracción (JSON, uno por caso), lo único que el sistema evaluado debe producir:
    {"caso": "C1", "actividades": [{"nombre", "cantidad", "unidad", "entregable"?, "origen"?: {"pagina"|"hoja"|"celda"|"fila"}}],
     "entregables": [texto], "restricciones": [texto], "contradicciones": [texto], "preguntas_cliente": [texto],
     "revisiones_internas": [texto], "elementos_visuales": [texto]}

Reglas: valor y unidad se comparan tal como están escritos, sin conversión implícita (45.000 m2 no es 4,5 ha). Un JSON que
cumple el esquema no prueba exactitud: cada cifra se contrasta con la referencia. Gravedad:
- crítico: cantidad o unidad equivocada, actividad omitida, cifra de una trampa atada al concepto equivocado,
  contradicción crítica o faltante bloqueante omitidos, restricción crítica omitida;
- no crítico: lectura ambigua aceptada, entregable/restricción/faltante no bloqueante omitido, pregunta de más o mal
  destinada, localizador ausente o incorrecto, actividad extra sin cifra de trampa;
- no evaluable: la interpretación visual (solo una persona puede juzgarla) y todo lo que falte porque no se entregó extracción.
"""

from __future__ import annotations

import json
import re
import unicodedata
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

CRITICO, NO_CRITICO, NO_EVALUABLE = "critico", "no_critico", "no_evaluable"
_UNIDADES = {"ha": "ha", "hectarea": "ha", "hectareas": "ha", "km": "km", "kilometro": "km", "kilometros": "km",
             "m": "m", "metro": "m", "metros": "m", "m2": "m2", "m²": "m2", "metrocuadrado": "m2",
             "metroscuadrados": "m2", "punto": "puntos", "puntos": "puntos", "perfil": "perfiles",
             "perfiles": "perfiles", "quebrada": "quebradas", "quebradas": "quebradas",
             "construccion": "construcciones", "construcciones": "construcciones"}
_ID_INTERNO = re.compile(r"\bC[123]-(?:A|R|K|F|I|T|V)\d\b")


def normalizar(texto: str) -> str:
    """Minúsculas y sin tildes, con espacios simples."""
    sin_tildes = "".join(c for c in unicodedata.normalize("NFD", str(texto)) if unicodedata.category(c) != "Mn")
    return re.sub(r"\s+", " ", sin_tildes.lower()).strip()


def unidad_literal(texto: str) -> str:
    """La unidad escrita, en su forma canónica pero SIN convertirla a otra (ha sigue siendo ha)."""
    limpio = normalizar(texto).replace(" ", "").replace("²", "2")
    return _UNIDADES.get(limpio, limpio)


def _decimal(texto: str) -> Decimal | None:
    limpio = normalizar(texto).replace(" ", "")
    if re.fullmatch(r"\d+(?:[.,]\d+)?", limpio) is None:
        return None
    try:
        return Decimal(limpio.replace(",", "."))
    except InvalidOperation:
        return None


def _mismo_valor(extraido: str, esperado: dict[str, Any]) -> bool:
    if normalizar(extraido).replace(" ", "") == normalizar(esperado["cantidad_literal"]).replace(" ", ""):
        return True
    numero = _decimal(extraido)
    return numero is not None and numero == Decimal(esperado["valor"])


def _valor_alternativo(extraido: str, esperado: dict[str, Any]) -> bool:
    numero = _decimal(extraido)
    return numero is not None and any(numero == Decimal(v) for v in esperado.get("interpretaciones", []))


def _hallazgo(gravedad: str, codigo: str, detalle: str, ref: str = "") -> dict[str, str]:
    return {"gravedad": gravedad, "codigo": codigo, "ref": ref, "detalle": detalle}


def _puntaje(texto: str, claves: list[str]) -> int:
    base = normalizar(texto)
    return sum(1 for clave in claves if normalizar(clave) in base)


def _emparejar(esperadas: list[dict[str, Any]], extraidas: list[dict[str, Any]]) -> dict[int, int]:
    """Asigna a cada actividad esperada la extraída de mayor coincidencia de claves (uno a uno); {esperada: extraída}."""
    candidatos = sorted(((_puntaje(f"{e.get('nombre', '')} {e.get('entregable', '')}", a["claves"]), i, j)
                         for i, a in enumerate(esperadas) for j, e in enumerate(extraidas)), key=lambda t: (-t[0], t[1], t[2]))
    asignadas: dict[int, int] = {}
    usadas: set[int] = set()
    for puntaje, i, j in candidatos:
        if puntaje > 0 and i not in asignadas and j not in usadas:
            asignadas[i] = j
            usadas.add(j)
    return asignadas


_CELDA = re.compile(r"^\$?([A-Z]{1,3})\$?(\d+)$")
MAX_CELDAS_DE_UN_RANGO = 50      # un rango enorme (A1:Z999) no «localiza» nada: no cuenta como cita de una celda


def _columna(letras: str) -> int:
    return sum((ord(c) - 64) * 26 ** i for i, c in enumerate(reversed(letras)))


def celda_en_rango(celda: str, rango: str) -> bool:
    """¿`celda` (C4) está dentro de `rango` (B4:E4 o la misma celda)? Un rango de más de 50 celdas no cuenta."""
    extremos = [_CELDA.match(parte.strip().upper()) for parte in str(rango).split(":")]
    buscada = _CELDA.match(str(celda).strip().upper())
    if not buscada or len(extremos) not in (1, 2) or not all(extremos):
        return False
    (c1, f1), (c2, f2) = [(_columna(m.group(1)), int(m.group(2))) for m in (extremos[0], extremos[-1]) if m]
    columnas, filas = abs(c2 - c1) + 1, abs(f2 - f1) + 1
    if columnas * filas > MAX_CELDAS_DE_UN_RANGO:
        return False
    columna, fila = _columna(buscada.group(1)), int(buscada.group(2))
    return min(c1, c2) <= columna <= max(c1, c2) and min(f1, f2) <= fila <= max(f1, f2)


def _origen_ok(esperado: dict[str, Any], dado: Any) -> bool:
    if not isinstance(dado, dict):
        return False
    if isinstance(dado.get("localizadores"), list) and "celda" in esperado:
        # «Hoja!B4:E4; Otra!A2:D8»: vale si algún localizador es de la hoja esperada y su rango contiene la celda esperada.
        return any(isinstance(par, (list, tuple)) and len(par) == 2
                   and ("hoja" not in esperado or normalizar(str(par[0])) == normalizar(str(esperado["hoja"])))
                   and celda_en_rango(str(esperado["celda"]), str(par[1])) for par in dado["localizadores"])
    for clave in ("pagina", "hoja", "celda", "fila", "tabla"):
        if clave in esperado and clave in dado and normalizar(str(esperado[clave])) != normalizar(str(dado[clave])):
            return False
    return any(clave in dado and clave in esperado for clave in ("pagina", "hoja", "celda", "fila", "tabla"))


def _contiene_todo(textos: list[str], claves: list[str]) -> bool:
    return any(all(normalizar(c) in normalizar(t) for c in claves) for t in textos)


def _contiene_alguno(textos: list[str], claves: list[str]) -> bool:
    return any(any(normalizar(c) in normalizar(t) for c in claves) for t in textos)


def comparar(referencia: dict[str, Any], extraccion: dict[str, Any] | None) -> dict[str, Any]:
    """El resultado de un caso: hallazgos clasificados y contadores con sus denominadores reales."""
    caso = referencia["caso"]
    hallazgos: list[dict[str, str]] = []
    esperadas = referencia["actividades"]
    if extraccion is None:
        hallazgos.append(_hallazgo(NO_EVALUABLE, "sin_extraccion", "No se entregó extracción para este caso.", caso))
        return _resultado(caso, referencia, hallazgos, 0, 0, 0, 0)
    if str(extraccion.get("caso")) != caso:
        raise ValueError(f"La extracción es del caso {extraccion.get('caso')!r}, no de {caso}")
    if _ID_INTERNO.search(json.dumps(extraccion, ensure_ascii=False)):
        hallazgos.append(_hallazgo(CRITICO, "posible_acceso_a_la_referencia",
                                   "La extracción menciona ids internos de la referencia; el resultado no es confiable.", caso))
    extraidas = [a for a in extraccion.get("actividades", []) if isinstance(a, dict)]
    pares = _emparejar(esperadas, extraidas)
    trampas = {Decimal(t["valor"]): t for t in referencia["trampas"]}
    correctas = 0
    for i, esperada in enumerate(esperadas):
        ref = esperada["id"]
        if i not in pares:
            hallazgos.append(_hallazgo(CRITICO, "actividad_omitida", f"No se encontró «{esperada['nombre']}».", ref))
            continue
        dada = extraidas[pares[i]]
        cantidad, unidad = str(dada.get("cantidad", "")), unidad_literal(str(dada.get("unidad", "")))
        mismo_valor = _mismo_valor(cantidad, esperada)
        misma_unidad = unidad == unidad_literal(esperada["unidad_literal"])
        if mismo_valor and misma_unidad:
            correctas += 1
        elif misma_unidad and esperada.get("ambiguo") and _valor_alternativo(cantidad, esperada):
            correctas += 1
            hallazgos.append(_hallazgo(NO_CRITICO, "lectura_ambigua_aceptada",
                                       f"Leyó «{cantidad}» donde el documento dice «{esperada['cantidad_literal']}».", ref))
        else:
            numero = _decimal(cantidad)
            trampa = trampas.get(numero) if numero is not None else None
            motivo = "unidad distinta sin conversión declarada" if mismo_valor else "cantidad distinta"
            if trampa is not None:
                hallazgos.append(_hallazgo(CRITICO, "asociacion_incorrecta",
                                           f"Tomó la cifra «{cantidad}» ({trampa['es']}) como cantidad de la actividad.", ref))
            else:
                hallazgos.append(_hallazgo(CRITICO, "cantidad_o_unidad_incorrecta",
                                           f"Esperaba «{esperada['cantidad_literal']} {esperada['unidad_literal']}» y obtuvo "
                                           f"«{cantidad} {dada.get('unidad', '')}» ({motivo}).", ref))
        if dada.get("origen") is None or not _origen_ok(esperada["origen"], dada.get("origen")):
            hallazgos.append(_hallazgo(NO_CRITICO, "localizador_ausente_o_incorrecto",
                                       "El origen citado falta o no coincide con el esperado.", ref))
        textos_entregable = [str(dada.get("entregable", "")), *map(str, extraccion.get("entregables", []))]
        if esperada["claves_entregable"] and not _contiene_alguno(textos_entregable, esperada["claves_entregable"]):
            hallazgos.append(_hallazgo(NO_CRITICO, "entregable_omitido", f"Falta el entregable: {esperada['entregable']}.", ref))
    for j, dada in enumerate(extraidas):
        if j in pares.values():
            continue
        numero = _decimal(str(dada.get("cantidad", "")))
        trampa = trampas.get(numero) if numero is not None else None
        if trampa is not None:
            hallazgos.append(_hallazgo(CRITICO, "cifra_trampa_como_actividad",
                                       f"Registró «{dada.get('nombre')}» con la cifra {dada.get('cantidad')} ({trampa['es']}).",
                                       trampa["id"]))
        else:
            hallazgos.append(_hallazgo(NO_CRITICO, "actividad_no_esperada", f"Actividad extra: «{dada.get('nombre')}».", caso))
    _comparar_textos(referencia, extraccion, hallazgos)
    return _resultado(caso, referencia, hallazgos, len(esperadas), len(extraidas), correctas, len(pares))


def _comparar_textos(referencia: dict[str, Any], extraccion: dict[str, Any], hallazgos: list[dict[str, str]]) -> None:
    restricciones = [str(t) for t in extraccion.get("restricciones", [])]
    for restriccion in referencia["restricciones"]:
        if not _contiene_todo(restricciones, restriccion["claves"]):
            hallazgos.append(_hallazgo(CRITICO if restriccion["critica"] else NO_CRITICO, "restriccion_omitida",
                                       restriccion["texto"], restriccion["id"]))
    contradicciones = [str(t) for t in extraccion.get("contradicciones", [])]
    for contradiccion in referencia["contradicciones"]:
        if not _contiene_todo(contradicciones, contradiccion["literales_clave"]):
            hallazgos.append(_hallazgo(CRITICO if contradiccion["critica"] else NO_CRITICO, "contradiccion_omitida",
                                       contradiccion["motivo"], contradiccion["id"]))
    preguntas = [str(t) for t in extraccion.get("preguntas_cliente", [])]
    revisiones = [str(t) for t in extraccion.get("revisiones_internas", [])]
    for faltante in referencia["faltantes"]:
        if not _contiene_alguno(preguntas, faltante["palabras"]):
            hallazgos.append(_hallazgo(CRITICO if faltante["bloqueante"] else NO_CRITICO, "faltante_sin_pregunta",
                                       faltante["motivo"], faltante["id"]))
    for revision in referencia["revisiones_internas"]:
        if _contiene_todo(preguntas, revision["palabras"]) and not _contiene_todo(revisiones, revision["palabras"]):
            hallazgos.append(_hallazgo(NO_CRITICO, "revision_interna_enviada_al_cliente", revision["texto"], revision["id"]))
        elif not _contiene_alguno(revisiones, revision["palabras"]):
            hallazgos.append(_hallazgo(NO_CRITICO, "revision_interna_omitida", revision["texto"], revision["id"]))
    for pregunta in preguntas:
        if any(_contiene_alguno([pregunta], f["palabras"]) for f in referencia["faltantes"]):
            continue
        presente = next((d for d in referencia["datos_presentes"] if _contiene_alguno([pregunta], d["palabras"])), None)
        detalle = (f"Preguntó por «{presente['tema']}», que el documento sí trae." if presente
                   else f"Pregunta sin respaldo en la referencia: «{pregunta[:80]}».")
        if not any(_contiene_alguno([pregunta], r["palabras"]) for r in referencia["revisiones_internas"]):
            hallazgos.append(_hallazgo(NO_CRITICO, "pregunta_de_mas", detalle, referencia["caso"]))
    visuales = [str(t) for t in extraccion.get("elementos_visuales", [])]
    for elemento in referencia["elementos_visuales"]:
        visto = _contiene_alguno(visuales, elemento["claves"])
        hallazgos.append(_hallazgo(NO_EVALUABLE, "interpretacion_visual_para_revision_humana",
                                   f"{elemento['descripcion']} — {'mencionado' if visto else 'no mencionado'} en la extracción; "
                                   "juzgar la interpretación requiere una persona.", elemento["id"]))


def _resultado(caso: str, referencia: dict[str, Any], hallazgos: list[dict[str, str]], esperadas: int, obtenidas: int,
               correctas: int, encontradas: int) -> dict[str, Any]:
    cuenta = {g: sum(1 for h in hallazgos if h["gravedad"] == g) for g in (CRITICO, NO_CRITICO, NO_EVALUABLE)}
    return {"caso": caso, "documento": referencia["documento"]["archivo"], "actividades_esperadas": esperadas,
            "actividades_obtenidas": obtenidas, "actividades_encontradas": encontradas,
            "actividades_con_valor_y_unidad_correctos": correctas, "errores_criticos": cuenta[CRITICO],
            "errores_no_criticos": cuenta[NO_CRITICO], "no_evaluables": cuenta[NO_EVALUABLE],
            "sin_errores_criticos": cuenta[CRITICO] == 0 and obtenidas > 0, "hallazgos": hallazgos}


def informe(resultados: list[dict[str, Any]]) -> dict[str, Any]:
    """El agregado con denominadores reales (no se inventan porcentajes si el denominador es cero)."""
    esperadas = sum(r["actividades_esperadas"] for r in resultados)
    correctas = sum(r["actividades_con_valor_y_unidad_correctos"] for r in resultados)
    obtenidas = sum(r["actividades_obtenidas"] for r in resultados)
    return {"casos": resultados, "actividades_esperadas": esperadas, "actividades_obtenidas": obtenidas,
            "actividades_con_valor_y_unidad_correctos": correctas,
            "exhaustividad": f"{correctas}/{esperadas}" if esperadas else "sin denominador",
            "precision": f"{correctas}/{obtenidas}" if obtenidas else "sin denominador",
            "errores_criticos": sum(r["errores_criticos"] for r in resultados),
            "errores_no_criticos": sum(r["errores_no_criticos"] for r in resultados),
            "no_evaluables": sum(r["no_evaluables"] for r in resultados)}


def evaluar_carpetas(referencias: Path, extracciones: Path) -> dict[str, Any]:
    """Compara `<caso>.extraccion.json` de `extracciones` con `<caso>.referencia.json` de `referencias`."""
    resultados = []
    for ruta in sorted(referencias.glob("*.referencia.json")):
        referencia = json.loads(ruta.read_text(encoding="utf-8"))
        candidata = extracciones / f"{referencia['caso']}.extraccion.json"
        extraccion = json.loads(candidata.read_text(encoding="utf-8")) if candidata.is_file() else None
        resultados.append(comparar(referencia, extraccion))
    return informe(resultados)


def main(argumentos: list[str]) -> int:
    """Uso: python -m laboratorio.banco_documental.comparador <carpeta_referencias> <carpeta_extracciones>."""
    if len(argumentos) != 2:
        print(main.__doc__)
        return 2
    print(json.dumps(evaluar_carpetas(Path(argumentos[0]), Path(argumentos[1])), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    import sys

    sys.exit(main(sys.argv[1:]))
