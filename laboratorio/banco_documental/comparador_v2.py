"""Comparador v2 (T-75): valida primero el formato y luego compara con una referencia v2; el comparador v1 no se toca.

Cambios frente a la v1 (todos documentados en `referencias_v2/`):
- una respuesta que no pasa el validador estructural NO se compara: queda «no evaluable por formato» y en cuarentena;
- cada coincidencia de texto exige TODOS los grupos de la referencia (cada grupo es un conjunto de sinónimos), nunca una
  palabra suelta; los números aceptan coma o punto decimal cuando la referencia lo declara;
- lo que no se puede juzgar automáticamente (interpretación visual, criterios técnicos opinables, preguntas no previstas) se
  separa como «revisión humana» y no se cuenta como error;
- las preguntas no previstas ya no son «de más»: pasan a revisión humana (con una nota si tocan un dato que el documento trae).

El resultado automático NO es una aprobación: solo dice si hay errores críticos verificables y cuántos puntos exigen una persona.
"""

from __future__ import annotations

import copy
import json
import re
import sys
from pathlib import Path
from typing import Any

from laboratorio.banco_documental import comparador as v1
from laboratorio.banco_documental import validador

CRITICO, NO_CRITICO, REVISION_HUMANA, ACIERTO_ADICIONAL = "critico", "no_critico", "revision_humana", "acierto_adicional"
AUTOMATICA, HUMANA = "automatica", "humana"
NO_EVALUABLE_POR_FORMATO = "NO_EVALUABLE_POR_FORMATO"
Dato = dict[str, Any]
# De lo que devuelve el comparador v1 solo se aprovecha lo de las actividades; los textos los juzga la v2.
_DE_ACTIVIDADES = {"actividad_omitida", "cantidad_o_unidad_incorrecta", "asociacion_incorrecta", "cifra_trampa_como_actividad",
                   "actividad_no_esperada", "lectura_ambigua_aceptada", "localizador_ausente_o_incorrecto",
                   "posible_acceso_a_la_referencia"}


_NUMEROS_EN_LETRAS = {"dos": "2", "tres": "3", "cuatro": "4", "cinco": "5", "seis": "6", "siete": "7", "ocho": "8", "nueve": "9",
                      "diez": "10", "once": "11", "doce": "12", "trece": "13", "catorce": "14", "quince": "15", "dieciseis": "16",
                      "diecisiete": "17", "dieciocho": "18", "diecinueve": "19", "veinte": "20"}
_PALABRA_NUMERICA = re.compile(r"\b(" + "|".join(_NUMEROS_EN_LETRAS) + r")\b")


def _normal(texto: str) -> str:
    """Normaliza y escribe con cifras los números de 2 a 20 escritos con letras («siete predios» = «7 predios»), igual en el
    texto y en los términos de la referencia. «uno» y «cero» no se tocan (son demasiado ambiguos)."""
    return _PALABRA_NUMERICA.sub(lambda m: _NUMEROS_EN_LETRAS[m.group(1)], v1.normalizar(texto))


def coincide(textos: list[str], grupos: list[list[str]]) -> bool:
    """Un texto coincide si contiene al menos un término de CADA grupo (sin tildes ni mayúsculas)."""
    if not grupos:
        return False
    normales = [_normal(texto) for texto in textos]
    return any(all(any(_normal(termino) in texto for termino in grupo) for grupo in grupos) for texto in normales)


def _hallazgo(gravedad: str, codigo: str, detalle: str, ref: str, verificacion: str = AUTOMATICA) -> Dato:
    return {"gravedad": gravedad, "codigo": codigo, "ref": ref, "detalle": detalle, "verificacion": verificacion}


def _textos(extraccion: Dato, campos: list[str]) -> list[str]:
    return [str(texto) for campo in campos for texto in extraccion.get(campo, [])]


def _actividades(referencia: Dato, extraccion: Dato) -> tuple[list[Dato], int, int, int]:
    """Valor, unidad y localizador por la lógica v1 (sin textos ni entregables) y entregables por grupos."""
    base = copy.deepcopy(referencia)
    for clave in ("restricciones", "contradicciones", "faltantes", "revisiones_internas", "elementos_visuales",
                  "datos_presentes"):
        base[clave] = []
    for actividad in base["actividades"]:
        actividad["claves_entregable"] = []
    resultado = v1.comparar(base, extraccion)
    hallazgos = [_hallazgo(h["gravedad"], h["codigo"], h["detalle"], h["ref"]) for h in resultado["hallazgos"]
                 if h["codigo"] in _DE_ACTIVIDADES]
    extraidas = [a for a in extraccion.get("actividades", []) if isinstance(a, dict)]
    pares = v1._emparejar(referencia["actividades"], extraidas)
    globales = [str(t) for t in extraccion.get("entregables", [])]
    for indice, esperada in enumerate(referencia["actividades"]):
        grupos = esperada.get("grupos_entregable", [])
        if not grupos or indice not in pares:
            continue
        propios = [str(extraidas[pares[indice]].get("entregable", ""))]
        if not (coincide(propios, grupos) or coincide(globales, grupos)):
            hallazgos.append(_hallazgo(NO_CRITICO, "entregable_omitido", f"Falta el entregable: {esperada['entregable']}.",
                                       esperada["id"]))
    return hallazgos, resultado["actividades_encontradas"], resultado["actividades_con_valor_y_unidad_correctos"], \
        resultado["actividades_obtenidas"]


def _restricciones_y_contradicciones(referencia: Dato, extraccion: Dato, hallazgos: list[Dato]) -> None:
    restricciones = _textos(extraccion, ["restricciones"])
    for restriccion in referencia["restricciones"]:
        if not coincide(restricciones, restriccion["grupos"]):
            gravedad = CRITICO if restriccion["critica"] else NO_CRITICO
            hallazgos.append(_hallazgo(gravedad, "restriccion_omitida", restriccion["texto"], restriccion["id"]))
    contradicciones = _textos(extraccion, ["contradicciones"])
    for contradiccion in referencia["contradicciones"]:
        if not coincide(contradicciones, contradiccion["grupos"]):
            gravedad = CRITICO if contradiccion["critica"] else NO_CRITICO
            hallazgos.append(_hallazgo(gravedad, "contradiccion_omitida", contradiccion["motivo"], contradiccion["id"]))
    for opcional in referencia["contradicciones_opcionales"]:
        if coincide(contradicciones, opcional["grupos"]):
            hallazgos.append(_hallazgo(ACIERTO_ADICIONAL, "contradiccion_adicional_fundada", opcional["motivo"], opcional["id"]))


def _pregunta_interna_de_caso_ficticio(referencia: Dato, extraccion: Dato, faltante: Dato) -> bool:
    """PRECISO_V6.1.3 manda que en un caso ficticio lo que se preguntaría al cliente vaya en `internal_review` (ask_client solo si es real).

    Solo en esos casos, y solo para un faltante que la referencia espera como pregunta al cliente, la pregunta pertinente de
    `internal_review` (la que contiene los términos del faltante) cuenta como reconocimiento. NO se convierte en `ask_client`:
    `preguntas_cliente` no cambia y el reconocimiento queda para revisión humana.
    """
    return bool(referencia.get("ficticio")) and "preguntas_cliente" in faltante["reconoce_en"]         and coincide(_textos(extraccion, ["revisiones_internas"]), faltante["grupos"])


def _faltantes_y_revisiones(referencia: Dato, extraccion: Dato, hallazgos: list[Dato]) -> None:
    for faltante in referencia["faltantes"]:
        if coincide(_textos(extraccion, faltante["reconoce_en"]), faltante["grupos"]):
            continue
        if _pregunta_interna_de_caso_ficticio(referencia, extraccion, faltante):
            hallazgos.append(_hallazgo(REVISION_HUMANA, "faltante_reconocido_solo_en_revision_interna",
                                       f"{faltante['motivo']} Work lo planteó en internal_review (caso ficticio, PRECISO_V6.1.3), "
                                       "no en ask_client: una persona confirma que la pregunta es pertinente.", faltante["id"], HUMANA))
            continue
        gravedad = CRITICO if faltante["bloqueante"] else NO_CRITICO
        hallazgos.append(_hallazgo(gravedad, "faltante_sin_reconocer", faltante["motivo"], faltante["id"]))
    for ambiguedad in referencia["ambiguedades"]:
        if not coincide(_textos(extraccion, ambiguedad["reconoce_en"]), ambiguedad["grupos"]):
            hallazgos.append(_hallazgo(ambiguedad["gravedad"], "ambiguedad_no_declarada", ambiguedad["motivo"], ambiguedad["id"]))
    for revision in referencia["revisiones_internas"]:
        donde = revision.get("reconoce_en", ["revisiones_internas"])
        if coincide(_textos(extraccion, donde), revision["grupos"]):
            continue
        if revision["tipo"] == "juicio_tecnico":
            hallazgos.append(_hallazgo(REVISION_HUMANA, "criterio_tecnico_para_revision_humana", revision["texto"],
                                       revision["id"], HUMANA))
        else:
            hallazgos.append(_hallazgo(NO_CRITICO, "revision_interna_omitida", revision["texto"], revision["id"]))


def _preguntas_y_visuales(referencia: Dato, extraccion: Dato, hallazgos: list[Dato]) -> None:
    reconocidas = [*referencia["faltantes"], *referencia["ambiguedades"]]
    for pregunta in _textos(extraccion, ["preguntas_cliente"]):
        if any(coincide([pregunta], item["grupos"]) for item in reconocidas):
            continue
        if any(coincide([pregunta], c["grupos"]) for c in [*referencia["contradicciones"], *referencia["contradicciones_opcionales"]]):
            continue
        presente = next((d["tema"] for d in referencia["datos_presentes"] if coincide([pregunta], d["grupos"])), None)
        nota = f" Toca «{presente}», que el documento ya trae." if presente else ""
        hallazgos.append(_hallazgo(REVISION_HUMANA, "pregunta_no_prevista_para_revision_humana",
                                   f"«{pregunta[:100]}».{nota}", referencia["caso"], HUMANA))
    visuales = _textos(extraccion, ["elementos_visuales"])
    for elemento in referencia["elementos_visuales"]:
        visto = coincide(visuales, elemento["grupos"])
        hallazgos.append(_hallazgo(REVISION_HUMANA, "interpretacion_visual_para_revision_humana",
                                   f"{elemento['descripcion']} — {'mencionado' if visto else 'no mencionado'}.",
                                   elemento["id"], HUMANA))


def _comparar_textos(referencia: Dato, extraccion: Dato, hallazgos: list[Dato]) -> None:
    _restricciones_y_contradicciones(referencia, extraccion, hallazgos)
    _faltantes_y_revisiones(referencia, extraccion, hallazgos)
    _preguntas_y_visuales(referencia, extraccion, hallazgos)


def comparar_v2(referencia: Dato, extraccion: Dato) -> Dato:
    """El resultado v2 de un caso ya validado: hallazgos con su gravedad y su tipo de verificación."""
    hallazgos, encontradas, correctas, obtenidas = _actividades(referencia, extraccion)
    _comparar_textos(referencia, extraccion, hallazgos)
    cuenta = {g: sum(1 for h in hallazgos if h["gravedad"] == g)
              for g in (CRITICO, NO_CRITICO, REVISION_HUMANA, ACIERTO_ADICIONAL)}
    return {"caso": referencia["caso"], "referencia": referencia["version"], "estado": "COMPARADA",
            "actividades_esperadas": len(referencia["actividades"]), "actividades_obtenidas": obtenidas,
            "actividades_encontradas": encontradas, "actividades_con_valor_y_unidad_correctos": correctas,
            "errores_criticos": cuenta[CRITICO], "errores_no_criticos": cuenta[NO_CRITICO],
            "para_revision_humana": cuenta[REVISION_HUMANA], "aciertos_adicionales": cuenta[ACIERTO_ADICIONAL],
            "sin_errores_criticos_automaticos": cuenta[CRITICO] == 0 and obtenidas > 0,
            "aviso": "resultado automático; no es una aprobación: quedan puntos que exigen una persona",
            "hallazgos": hallazgos}


def evaluar_respuesta(contenido: bytes, referencia: Dato, destino: Path | None = None, documento: bytes | None = None,
                      nombre_documento: str = "") -> Dato:
    """Valida los bytes y, solo si son estructuralmente válidos, los compara con la referencia v2."""
    caso = referencia["caso"]
    resultado = (validador.procesar(contenido, destino, caso, documento, nombre_documento) if destino is not None
                 else validador.validar(contenido, caso, documento, nombre_documento))
    validacion = resultado.como_dict()
    if not resultado.aceptada or resultado.datos is None:
        return {"caso": caso, "referencia": referencia["version"], "estado": NO_EVALUABLE_POR_FORMATO,
                "validacion": validacion, "errores_criticos": 0, "sin_errores_criticos_automaticos": False,
                "aviso": "no se comparó: la respuesta no es estructuralmente válida y queda en cuarentena"}
    informe = comparar_v2(referencia, resultado.datos)
    informe["validacion"] = validacion
    return informe


def main(argumentos: list[str]) -> int:
    """Uso: python -m laboratorio.banco_documental.comparador_v2 <referencia_v2.json> <respuesta> [carpeta_de_archivo]."""
    if len(argumentos) not in (2, 3):
        print(main.__doc__)
        return 2
    referencia = json.loads(Path(argumentos[0]).read_text(encoding="utf-8"))
    informe = evaluar_respuesta(Path(argumentos[1]).read_bytes(), referencia, Path(argumentos[2]) if len(argumentos) == 3 else None)
    print(json.dumps(informe, ensure_ascii=False, indent=2))
    return 0 if informe["estado"] == "COMPARADA" else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
