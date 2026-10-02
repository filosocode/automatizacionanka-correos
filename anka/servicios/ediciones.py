"""Edición de la tabla de actividades por una persona: motivos, diferencias entre versiones y guardado.

Cada edición guarda una versión nueva con lo que cambió y su motivo, y marca la tabla como editada a mano para
que un reproceso del modelo no la pise (R11/R12 en spec/02-reglas.md).
"""

from __future__ import annotations

import json
import re
from datetime import datetime
from uuid import uuid4

from anka.dominio.analisis import Actividad
from anka.infra.logging_setup import get_logger
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
    db = connect()
    try:
        rows = db.execute("SELECT * FROM table_versions WHERE thread_id=? ORDER BY version",
                          (thread_id,)).fetchall()
    finally:
        db.close()
    return [dict(row) for row in rows]


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


def save_activities(thread_id: str, activities: list[Actividad], reason: str = "") -> str:
    """Guarda la tabla editada a mano y la marca para que un reproceso no la pise."""
    db = connect()
    try:
        row = db.execute(
            "SELECT analysis_json FROM quotes WHERE thread_id=?", (thread_id,)
        ).fetchone()
        if not row:
            raise ValueError("La solicitud ya no existe.")
        analysis = load_analysis(row["analysis_json"])
        now = datetime.now().astimezone().isoformat(timespec="seconds")
        before = analysis.get("activities") or []
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
        db.execute(
            "UPDATE quotes SET analysis_json=?, updated_at=? WHERE thread_id=?",
            (json.dumps(analysis, ensure_ascii=False), now, thread_id),
        )
        db.commit()
    finally:
        db.close()
    return f"Tabla guardada (versión {version}): {summary}."
