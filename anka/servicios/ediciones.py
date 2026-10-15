"""Edición de la tabla de actividades por una persona: motivos, diferencias entre versiones y guardado.

Cada edición guarda una versión nueva con lo que cambió y su motivo, y marca la tabla como editada a mano para
que un reproceso del modelo no la pise (R11/R12 en spec/02-reglas.md).
"""

from __future__ import annotations
from anka.servicios.repositorio import actualizar_caso

import json
import re
from datetime import datetime
from typing import Any
from uuid import uuid4

from anka.dominio.activities import ACTIVITY_COLUMNS, huella_de_tabla
from anka.dominio.analisis import Actividad
from anka.infra.logging_setup import get_logger
from anka.servicios.hoja_de_calculo import programar_actualizacion_de_hoja
from anka.servicios.repositorio import RepositorioDeCasos
from anka.infra.store import add_table_version, connect, latest_table_version, record_event
from anka.dominio.caso import load_analysis

log = get_logger(__name__)


EDIT_REASONS = {
    "": "Sin motivo",
    "redundante": "Redundante o unir filas",
    "modelo": "El modelo se lo inventó",
    "correccion": "Corrección mía",
    "cliente": "El cliente lo pidió",
    "otro": "Otro",
}

_QUANTITY = re.compile(r"(\d{1,3}(?:\.\d{3})+(?:,\d+)?|\d+(?:[.,]\d+)?)\s*(ha|km|m2|m²|m|puntos?|und|unidades)\b",
                       re.IGNORECASE)


def activity_totals(rows: list[Actividad]) -> dict[str, float]:
    """Suma de cantidades por unidad (ha, km...) de una tabla, para ver si una edición cambia el alcance."""
    totals: dict[str, float] = {}
    for row in rows:
        text = f"{row.get('quantity') or ''} {row.get('unit') or ''}"
        for number, unit in _QUANTITY.findall(text):
            clean = number.replace(".", "") if re.fullmatch(r"\d{1,3}(?:\.\d{3})+(?:,\d+)?", number) else number
            value = float(clean.replace(",", "."))
            key = unit.lower().replace("²", "2")
            totals[key] = round(totals.get(key, 0.0) + value, 3)
    return totals


def diff_activities(old: list[Actividad], new: list[Actividad]) -> dict:
    """Filas agregadas, quitadas y modificadas entre dos versiones de la tabla (por nombre de actividad)."""
    def key(row: Actividad) -> str:
        return str(row.get("activity", "")).strip().casefold()

    before = {key(row): row for row in old if isinstance(row, dict)}
    after = {key(row): row for row in new if isinstance(row, dict)}
    changed = []
    for name, row in after.items():
        if name in before:
            fields = [f for f in ("quantity", "unit", "ubicacion", "entregable", "plazo_restriccion",
                                  "servicio_tecnica", "estado")
                      if str(before[name].get(f) or "") != str(row.get(f) or "")]
            if fields:
                changed.append({"activity": row.get("activity", ""), "fields": fields})
    return {
        "added": [after[n].get("activity", "") for n in after if n not in before],
        "removed": [before[n].get("activity", "") for n in before if n not in after],
        "changed": changed,
    }


def describe_edit(diff: dict, old_totals: dict, new_totals: dict) -> str:
    """Frase que resume qué cambió en una edición de la tabla (filas añadidas, quitadas y cambiadas)."""
    parts = []
    if diff["added"]:
        parts.append(f"+{len(diff['added'])} fila(s): " + ", ".join(str(a) for a in diff["added"][:2]))
    if diff["removed"]:
        parts.append(f"−{len(diff['removed'])} fila(s): " + ", ".join(str(a) for a in diff["removed"][:2]))
    if diff["changed"]:
        parts.append(f"{len(diff['changed'])} modificada(s): " + ", ".join(
            f"{c['activity']} ({', '.join(c['fields'])})" for c in diff["changed"][:2]))
    if not parts:
        parts.append("sin cambios de contenido")
    if old_totals != new_totals:
        moved = ", ".join(f"{unit} {old_totals.get(unit, 0)} → {new_totals.get(unit, 0)}"
                          for unit in sorted(set(old_totals) | set(new_totals))
                          if old_totals.get(unit, 0) != new_totals.get(unit, 0))
        parts.append("totales: " + moved)
    return "; ".join(parts)


def table_versions(thread_id: str) -> list[dict]:
    """Versiones guardadas de la tabla de actividades del caso, de la primera a la última."""
    return RepositorioDeCasos().versiones_de_tabla(thread_id)


def last_manual_version(thread_id: str) -> dict | None:
    """Última edición de una persona y si ya se avisó al cliente de ella."""
    db = connect()
    try:
        row = db.execute(
            "SELECT * FROM table_versions WHERE thread_id=? AND source='dashboard' "
            "ORDER BY version DESC LIMIT 1", (thread_id,)).fetchone()
        if not row:
            return None
        notified = db.execute(
            "SELECT 1 FROM case_events WHERE thread_id=? AND kind='scope_update_sent' "
            "AND recorded_at >= ?", (thread_id, row["created_at"])).fetchone()
    finally:
        db.close()
    return {**dict(row), "notified": notified is not None}


def _celdas(activities: list[Any]) -> list[dict[str, str]]:
    """Las actividades reducidas a sus columnas y a su forma normal, para saber si una edición cambió algo."""
    return [{key: (str(item.get(key) or "").strip() or ("pendiente" if key == "estado" else ""))
             for key, _ in ACTIVITY_COLUMNS} for item in activities]


def save_activities(thread_id: str, activities: list[Actividad], reason: str = "", base_version: str = "",
                    filas_enviadas: int | None = None) -> str:
    """Guarda la tabla editada a mano y la marca para que un reproceso no la pise.

    `base_version` es la huella de la tabla que la persona tenía a la vista: si cambió desde entonces se rechaza
    sin perder lo escrito (U-34.5). `filas_enviadas` es el `row_count` del formulario: 0 con una tabla que tiene
    filas es un formulario truncado. Una edición que no cambia nada no crea versión.
    """
    db = connect()
    try:
        row = db.execute(
            "SELECT analysis_json FROM quotes WHERE thread_id=?", (thread_id,)
        ).fetchone()
        if not row:
            raise ValueError("Este caso ya no existe. Vuelve a Mis casos.")
        analysis = load_analysis(row["analysis_json"])
        now = datetime.now().astimezone().isoformat(timespec="seconds")
        before = analysis.get("activities") or []
        if base_version and base_version != huella_de_tabla(before):
            raise ValueError("La tabla cambió desde que la abriste (otra edición o un análisis nuevo), así que no se "
                             "guardó. Lo que escribiste sigue en el editor: compáralo con la tabla de arriba y "
                             "vuelve a guardar.")
        if filas_enviadas == 0 and before:
            raise ValueError("El formulario llegó sin las filas de la tabla, así que no se guardó nada. Recarga el "
                             "caso y vuelve a intentarlo.")
        if _celdas(before) == _celdas(activities):
            return "No hay cambios que guardar: la tabla sigue igual."
        if latest_table_version(db, thread_id) is None:
            # la tabla que había antes de la primera edición (la del modelo si nadie la tocó)
            add_table_version(db, thread_id, before, "previa", "Tabla anterior a la primera edición")
        diff = diff_activities(before, activities)
        summary = describe_edit(diff, activity_totals(before), activity_totals(activities))
        reason = reason if reason in EDIT_REASONS else ""
        version = add_table_version(db, thread_id, activities, "dashboard", summary, reason)
        analysis["activities"] = activities
        analysis["manual_edit"] = {"edited_at": now, "rows": len(activities)}
        record_event(db, thread_id, "table_edit", "dashboard", f"v{version}-{uuid4().hex[:6]}",
                     f"Editaste la tabla (versión {version}): {summary}."
                     + (f" Motivo: {EDIT_REASONS[reason]}." if reason else ""), now)
        actualizar_caso(db, thread_id,
            analysis_json=json.dumps(analysis, ensure_ascii=False),
            updated_at=now)
        db.commit()
    finally:
        db.close()
    programar_actualizacion_de_hoja(thread_id)   # la hoja de Google se actualiza sola
    return f"Tabla guardada. {summary}."
