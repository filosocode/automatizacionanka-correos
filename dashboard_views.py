"""Fragmentos HTML del dashboard sin acceso a datos ni a Gmail/Drive."""

from __future__ import annotations

from collections import Counter

from markupsafe import Markup

from activities import ACTIVITY_COLUMNS, ESTADOS
from templating import render

MODE_LABELS = {
    "local": "Ollama (local)",
    "api": "API de OpenAI",
    "chatgpt_work": "ChatGPT Work",
}
ACTIVITY_TABLE_KEYS = (
    "activity", "servicio_tecnica", "ubicacion", "quantity", "unit",
    "entregable", "plazo_restriccion", "fuente", "estado",
)


def mode_selector(current: str | None, api_key_set: bool = True) -> Markup:
    labels = dict(MODE_LABELS)
    if not api_key_set:
        labels["api"] += " (sin clave)"
    return render("mode_selector.html", current=current, labels=labels)


def render_page(title: str, body: str, message: str = "", unread_count: int = 0,
                mode: str | None = None, api_key_set: bool = True) -> str:
    return str(render(
        "layout.html", title=title, body=body, message=message, unread_count=unread_count,
        mode_selector=mode_selector(mode, api_key_set), is_work=(mode == "chatgpt_work"),
    ))


def _metric_rows(counter: Counter, color: str = "") -> Markup:
    rows = []
    if counter:
        maximum = max(counter.values()) or 1
        for label, count in counter.most_common(6):
            rows.append((str(label), count, max(5, round(count / maximum * 100))))
    return render("metric_rows.html", rows=rows, color=color)


def _activities_table(activities: list[dict]) -> Markup:
    rows = [[str(item.get(key) or "") for key in ACTIVITY_TABLE_KEYS] for item in activities]
    return render("activities_table.html", rows=rows)


def _attachments_table(attachments: list[dict]) -> Markup:
    rows = [
        (
            str(item.get("filename") or ""),
            str(item.get("status") or ""),
            str(item.get("reason") or "") or "lectura correcta",
            ", ".join(str(value) for value in (item.get("blocked_fields") or [])) or "ninguno",
        )
        for item in attachments
    ]
    return render("attachments_table.html", rows=rows)


def _reception_assessment_panel(assessment: dict) -> Markup:
    status_class = {
        "needs_client_information": "warn",
        "technical_review": "info",
        "sufficient_for_internal_review": "",
    }.get(assessment.get("status") or "", "info")
    return render(
        "assessment_panel.html",
        status_class=status_class,
        label=assessment.get("label", "Pendiente de evaluación"),
        service_family=assessment.get("service_family", "Por clasificar"),
        reason=assessment.get("reason", ""),
        questions=[str(q) for q in assessment.get("questions", [])],
        checks=[str(c) for c in assessment.get("technical_checks", [])],
    )


def activities_editor(thread_id: str, activities: list[dict],
                      reasons: list[tuple[str, str]] | None = None) -> Markup:
    """Tabla editable: colores por estado, celdas vacias en gris y una fila nueva al final."""
    def cells(item: dict) -> list[dict]:
        return [{"key": key, "value": str(item.get(key) or "")} for key, _ in ACTIVITY_COLUMNS]

    rows = [
        {"index": index, "is_new": False, "cells": cells(item)}
        for index, item in enumerate(activities)
    ]
    new_row = {key: "" for key, _ in ACTIVITY_COLUMNS} | {"estado": "pendiente"}
    rows.append({"index": len(activities), "is_new": True, "cells": cells(new_row)})
    return render(
        "activities_editor.html",
        thread_id=thread_id, row_count=len(activities), rows=rows, states=ESTADOS,
        reasons=reasons or [],
        headers=[label for _, label in ACTIVITY_COLUMNS],
    )
