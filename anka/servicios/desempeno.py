"""Desempeño con el uso real: agrupa los casos por versión del prompt y modelo y mide las correcciones (T-35)."""

from __future__ import annotations

import json
from typing import Any

from anka.dominio import desempeno as medida
from anka.dominio.caso import load_analysis
from anka.infra.store import connect

SIN_DATO = "sin dato"


def _actividades(texto: str) -> list[dict[str, Any]]:
    try:
        valor = json.loads(texto)
    except ValueError:
        return []
    return [fila for fila in valor if isinstance(fila, dict)] if isinstance(valor, list) else []


def _tablas_del_primer_analisis(versiones: list[Any]) -> tuple[list[dict[str, Any]], list[dict[str, Any]], bool] | None:
    """(tabla que dejó el análisis, tabla con la que quedó la persona, ¿la editó?) hasta el primer reanálisis."""
    inicio = next((i for i, v in enumerate(versiones) if v["source"] == "model"), None)
    if inicio is None:
        return None
    resto = versiones[inicio + 1:]
    limite = next((i for i, v in enumerate(resto) if v["source"] == "model"), len(resto))
    ediciones = [v for v in resto[:limite] if v["source"] == "dashboard"]
    final = ediciones[-1] if ediciones else versiones[inicio]
    return _actividades(versiones[inicio]["activities_json"]), _actividades(final["activities_json"]), bool(ediciones)


def por_grupo() -> list[dict[str, Any]]:
    """Un resumen por (versión del prompt, modelo declarado), con el más usado primero."""
    db = connect()
    try:
        casos: dict[tuple[str, str], list[dict[str, Any]]] = {}
        for fila in db.execute("SELECT thread_id, analysis_json FROM quotes"):
            versiones = db.execute(
                "SELECT version, source, activities_json FROM table_versions WHERE thread_id=? ORDER BY version",
                (fila["thread_id"],)).fetchall()
            tablas = _tablas_del_primer_analisis(versiones)
            if tablas is None:
                continue
            analisis = load_analysis(fila["analysis_json"])
            clave = (str(analisis.get("work_skill_version") or SIN_DATO), str(analisis.get("work_model") or SIN_DATO))
            inicial, final, edito = tablas
            casos.setdefault(clave, []).append({
                "cambios": medida.cambios_de_tabla(inicial, final), "tuvo_edicion": edito,
                "preguntas": medida.concordancia_de_preguntas(analisis.get("preguntas"))})
    finally:
        db.close()
    grupos = []
    for (prompt, modelo), lista in casos.items():
        totales = {clave: sum(c["preguntas"][clave] for c in lista) for clave in lista[0]["preguntas"]}
        grupos.append({"prompt": prompt, "modelo": modelo, "casos": len(lista),
                       "resumen": medida.resumir_grupo(lista), "preguntas": totales,
                       "cambios": {clave: sum(c["cambios"][clave] for c in lista) for clave in lista[0]["cambios"]}})
    return sorted(grupos, key=lambda g: (-g["casos"], g["prompt"], g["modelo"]))
