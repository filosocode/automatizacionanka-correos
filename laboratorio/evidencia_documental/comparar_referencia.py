"""Compara la evidencia extraída con las referencias v2 del banco (T-118). Solo laboratorio.

La referencia se lee **aquí**, después de extraer: el extractor no la importa ni la recibe. Una cifra esperada «se encuentra»
si hay una evidencia con la misma cantidad literal y la misma unidad literal (si la referencia la trae) en la misma página
(PDF) o en la misma hoja con la celda esperada dentro del rango citado (Excel). No se convierte ni se redondea nada.

El extractor observa cifras con unidad; no interpreta. Por eso una referencia sin unidad en el texto (un número de sello), un
conteo derivado o una restricción cualitativa se informan como «no cubiertas por diseño», no como errores.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from openpyxl.utils.cell import range_boundaries

from laboratorio.evidencia_documental.fuentes import plano, sin_espacios

REFERENCIAS = Path(__file__).resolve().parent.parent / "banco_documental" / "referencias_v2"


def cargar(caso: str) -> dict[str, Any]:
    """La referencia v2 del caso."""
    return json.loads((REFERENCIAS / f"{caso}.referencia.v2.json").read_text(encoding="utf-8"))


def _celda_en(rango: str, celda: str) -> bool:
    try:
        c1, f1, c2, f2 = range_boundaries(rango)
        c, f, _, _ = range_boundaries(celda)
    except (ValueError, TypeError):
        return False
    if c1 is None or f1 is None or c2 is None or f2 is None or c is None or f is None:
        return False
    return c1 <= c <= c2 and f1 <= f <= f2


def _esperados(ref: dict[str, Any]) -> list[dict[str, Any]]:
    salida: list[dict[str, Any]] = []
    for a in ref["actividades"]:
        salida.append({"id": a["id"], "clase": "actividad", "cantidad": a["cantidad_literal"], "unidad": a.get("unidad_literal"), "origen": a["origen"]})
    for t in ref["trampas"]:
        salida.append({"id": t["id"], "clase": "trampa", "cantidad": t["valor_literal"], "unidad": t.get("unidad_distinta"), "origen": t["origen"],
                       "nota": t["es"]})
    for k in ref["contradicciones"]:
        for i, v in enumerate(k.get("valores", [])):
            salida.append({"id": f"{k['id']}.{i + 1}", "clase": "valor_de_contradiccion", "cantidad": v["literal"], "unidad": v.get("unidad"), "origen": v["origen"]})
    return salida


def _coincide(e: dict[str, Any], esperado: dict[str, Any]) -> bool:
    if e["tipo"] != "cantidad" or e.get("cantidad_literal") != esperado["cantidad"]:
        return False
    if esperado["unidad"] and plano(e.get("unidad_literal") or "") != plano(esperado["unidad"]):
        return False
    loc, origen = e["localizador"], esperado["origen"]
    if "pagina" in origen:
        return loc.get("pagina") == origen["pagina"]
    if "hoja" not in origen or loc.get("hoja") != origen["hoja"]:
        return False
    celda = origen.get("celda", "")
    referencia = loc.get("celda") or loc.get("rango") or ""
    if ":" in celda:
        return False   # el origen es un rango derivado (conteo), no una celda con la cifra
    return _celda_en(referencia if ":" in referencia else f"{referencia}:{referencia}", celda) if celda else False


def _razon_de_omision(esperado: dict[str, Any]) -> str:
    origen = esperado["origen"]
    if ":" in origen.get("celda", ""):
        return "valor derivado: es un conteo de celdas, no una cifra escrita (el extractor no cuenta)"
    if not esperado["unidad"]:
        return "cifra sin unidad en el texto (p. ej. un número de sello o de nota): el extractor exige cifra + unidad"
    return "no se encontró la cifra con esa unidad en esa página u hoja"


def comparar(caso: str, doc: dict[str, Any]) -> dict[str, Any]:
    """Resultado de comparar la evidencia extraída (`doc`) con la referencia v2 del caso."""
    ref = cargar(caso)
    evidencias = [e for e in doc["evidencias"] if e["estado_de_lectura"] != "no_encontrado"]
    esperados = _esperados(ref)
    encontrados, omitidos = [], []
    usadas: set[str] = set()
    for esperado in esperados:
        coincidencias = [e for e in evidencias if _coincide(e, esperado)]
        if coincidencias:
            usadas.update(e["id"] for e in coincidencias)
            encontrados.append({"id": esperado["id"], "clase": esperado["clase"], "cantidad": esperado["cantidad"], "unidad": esperado["unidad"],
                                "evidencias": [e["id"] for e in coincidencias]})
        else:
            omitidos.append({"id": esperado["id"], "clase": esperado["clase"], "cantidad": esperado["cantidad"], "unidad": esperado["unidad"],
                             "razon": _razon_de_omision(esperado)})
    restricciones: dict[str, list[dict[str, Any]]] = {"cubiertas": [], "no_cubiertas_por_diseno": []}
    for r in ref["restricciones"]:
        grupos = r["grupos"]
        cubierta = any(all(any(sin_espacios(t) in sin_espacios(" ".join(str(e.get(k) or "") for k in ("texto_literal", "cantidad_literal", "unidad_literal")))
                               for t in g) for g in grupos) for e in evidencias if e["tipo"] == "cantidad")
        restricciones["cubiertas" if cubierta else "no_cubiertas_por_diseno"].append({"id": r["id"], "grupos": grupos})
    visuales = []
    for v in ref["elementos_visuales"]:
        pendientes = [e for e in doc["evidencias"] if e["tipo"] == "elemento_visual" and e["localizador"].get("pagina") == v["pagina"]]
        visuales.append({"id": v["id"], "pagina": v["pagina"], "descripcion": v["descripcion"],
                         "pendiente_registrado": bool(pendientes) and all(e["estado_de_lectura"] == "pendiente_revision_visual" for e in pendientes),
                         "leido_como_hecho": any(e["estado_de_lectura"] == "leido" for e in pendientes)})
    extras = [e["id"] for e in evidencias if e["tipo"] == "cantidad" and e["id"] not in usadas]
    return {"caso": caso, "esperados": len(esperados), "encontrados": encontrados, "omitidos": omitidos, "evidencias_adicionales": extras,
            "restricciones": restricciones, "elementos_visuales": visuales,
            "exhaustividad": round(len(encontrados) / len(esperados), 3) if esperados else None,
            "valores_normalizados_o_convertidos": [e["id"] for e in evidencias if e.get("valor_normalizado")]}


def resumen_en_texto(resultado: dict[str, Any]) -> str:
    """Una línea por caso, para el informe."""
    cifras = re.sub(r"\s+", " ", f"{resultado['caso']}: {len(resultado['encontrados'])}/{resultado['esperados']} cifras esperadas; "
                    f"{len(resultado['omitidos'])} omitidas; {len(resultado['evidencias_adicionales'])} adicionales")
    return cifras
