"""Fragmentos HTML del dashboard sin acceso a datos ni a Gmail/Drive."""

from __future__ import annotations

import re
from collections import Counter
from collections.abc import Mapping, Sequence
from typing import Any

from markupsafe import Markup

from anka.dominio.activities import ACTIVITY_COLUMNS, ESTADOS
from anka.web.templating import render

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
    """Selector de modo de operación; avisa si falta la clave de la API."""
    labels = dict(MODE_LABELS)
    if not api_key_set:
        labels["api"] += " (sin clave)"
    return render("mode_selector.html", current=current, labels=labels)


def render_page(title: str, body: str, message: str = "", unread_count: int = 0,
                mode: str | None = None, api_key_set: bool = True) -> str:
    """Página completa: encabezado, avisos, selector de modo y el contenido recibido."""
    return str(render(
        "layout.html", title=title, body=body, message=message, unread_count=unread_count,
        mode_selector=mode_selector(mode, api_key_set), is_work=(mode == "chatgpt_work"),
    ))


def metric_rows(counter: Counter, color: str = "") -> Markup:
    """Filas de barras con las seis etiquetas más frecuentes de un contador."""
    rows = []
    if counter:
        maximum = max(counter.values()) or 1
        for label, count in counter.most_common(6):
            rows.append((str(label), count, max(5, round(count / maximum * 100))))
    return render("metric_rows.html", rows=rows, color=color)


def _activities_table(activities: list[dict]) -> Markup:
    rows = [[str(item.get(key) or "") for key in ACTIVITY_TABLE_KEYS] for item in activities]
    return render("activities_table.html", rows=rows)


def attachments_table(attachments: Sequence[Mapping[str, Any]]) -> Markup:
    """Tabla de adjuntos con estado, datos bloqueados y citas verificables."""
    rows = []
    for item in attachments:
        evidence = []
        for finding in item.get("evidence") or []:
            if not isinstance(finding, Mapping):
                continue
            parts = [str(finding.get("topic") or "hallazgo")]
            if finding.get("value"):
                parts.append(str(finding["value"]))
            if finding.get("location"):
                parts.append(str(finding["location"]))
            if finding.get("excerpt"):
                parts.append(f"«{finding['excerpt']}»")
            evidence.append(" · ".join(parts))
        rows.append((
            str(item.get("filename") or ""),
            str(item.get("status") or ""),
            str(item.get("coverage") or "sin detalle"),
            str(item.get("reason") or "") or "lectura correcta",
            ", ".join(str(value) for value in (item.get("blocked_fields") or [])) or "ninguno",
            evidence,
            [str(value) for value in (item.get("reviewed_sections") or [])],
        ))
    return render("attachments_table.html", rows=rows)


def question_audit_panel(audit: Sequence[Mapping[str, Any]]) -> Markup:
    """Muestra por qué se propuso cada pregunta y qué fuentes se comprobaron."""
    rows = [
        (str(item.get("question") or ""), str(item.get("reason_missing") or ""),
         ", ".join(str(source) for source in (item.get("reviewed_sources") or [])),
         str(item.get("disposition") or "sin validar"),
         str(item.get("unresolved") or ""),
         ", ".join(str(ref) for ref in (item.get("target_finding_ids") or [])))
        for item in audit if isinstance(item, Mapping)
    ]
    return render("question_audit.html", rows=rows)


def _audit_review_rows(audit: Sequence[Mapping[str, Any]], waiting_for_client: bool) -> list[tuple[str, str, str]]:
    rows = []
    for item in audit:
        if not isinstance(item, Mapping):
            continue
        disposition = str(item.get("disposition") or "")
        if disposition == "internal_review":
            status = "Revisión interna pendiente"
            task = str(item.get("question") or item.get("unresolved") or "Verificar con el equipo de Anka")
        elif disposition == "ask_client":
            status = "Esperando respuesta del cliente" if waiting_for_client else "Pregunta sugerida al cliente"
            task = str(item.get("unresolved") or item.get("question") or "Dato pendiente de confirmar")
        else:
            continue
        rows.append((status, task, str(item.get("reason_missing") or "")))
    return rows


def _audit_search_text(audit: Sequence[Mapping[str, Any]]) -> str:
    parts: list[str] = []
    for item in audit:
        if not isinstance(item, Mapping):
            continue
        parts.extend(str(item.get(key) or "") for key in ("question", "unresolved", "reason_missing"))
        parts.extend(str(source) for source in (item.get("reviewed_sources") or []))
    return " ".join(parts).casefold()


def _internal_review_search_text(audit: Sequence[Mapping[str, Any]]) -> str:
    parts: list[str] = []
    for item in audit:
        if not isinstance(item, Mapping) or item.get("disposition") != "internal_review":
            continue
        parts.extend(str(item.get(key) or "") for key in ("question", "unresolved", "reason_missing"))
        parts.extend(str(source) for source in (item.get("reviewed_sources") or []))
    return " ".join(parts).casefold()


def _dependency_review_rows(
    dependencies: Sequence[Mapping[str, Any]], audit_text: str,
) -> list[tuple[str, str, str]]:
    rows = []
    for item in dependencies:
        if not isinstance(item, Mapping) or item.get("status") != "pendiente":
            continue
        description = str(item.get("description") or "Dependencia de alcance pendiente")
        if description.casefold() not in audit_text:
            source = str(item.get("source") or "")
            rows.append(("Revisión interna pendiente", description, f"Fuente: {source}" if source else ""))
    return rows


def _contradiction_review_rows(
    contradictions: Sequence[Mapping[str, Any]], audit_text: str,
) -> list[tuple[str, str, str]]:
    rows = []
    for item in contradictions:
        if not isinstance(item, Mapping) or item.get("status") != "pendiente":
            continue
        topic = str(item.get("topic") or "dato técnico")
        details = str(item.get("details") or "Fuentes con valores discrepantes")
        sources = [str(source) for source in (item.get("sources") or []) if source]
        if details.casefold() not in audit_text and not (sources and all(s.casefold() in audit_text for s in sources)):
            rows.append(("Revisión interna pendiente", f"Resolver contradicción en {topic}: {details}",
                         f"Fuentes: {'; '.join(sources)}" if sources else ""))
    return rows


def _attachment_review_rows(
    attachments: Sequence[Mapping[str, Any]], audit_text: str,
) -> list[tuple[str, str, str]]:
    rows = []
    unreadable = {"bloqueado", "lectura_parcial", "no_legible", "unreadable", "parcial", "partial"}
    for item in attachments:
        if not isinstance(item, Mapping):
            continue
        filename = str(item.get("filename") or "adjunto")
        status = str(item.get("status") or "").casefold().replace(" ", "_")
        coverage = str(item.get("coverage") or "").casefold().replace(" ", "_")
        if status not in unreadable and coverage not in {"parcial", "no_legible", "unreadable"}:
            continue
        stem = filename.rsplit(".", 1)[0].casefold()
        if filename.casefold() in audit_text or (stem and stem in audit_text):
            continue
        reason = str(item.get("reason") or "El contenido no se pudo revisar por completo.")
        blocked = ", ".join(str(value) for value in (item.get("blocked_fields") or []) if value)
        if blocked:
            reason += f" Datos por verificar: {blocked}."
        rows.append(("Revisión interna pendiente", f"Revisar el adjunto {filename}", reason))
    return rows


def review_tasks_panel(
    audit: Sequence[Mapping[str, Any]],
    other_questions: Sequence[str] = (),
    attachments: Sequence[Mapping[str, Any]] = (),
    dependencies: Sequence[Mapping[str, Any]] = (),
    contradictions: Sequence[Mapping[str, Any]] = (),
    waiting_for_client: bool = False,
) -> Markup:
    """Resume revisiones y dependencias desde las fuentes disponibles, incluidos payloads antiguos."""
    audit_text = _audit_search_text(audit)
    internal_text = _internal_review_search_text(audit)
    candidates = (
        _audit_review_rows(audit, waiting_for_client)
        + [
            ("Esperando respuesta del cliente" if waiting_for_client else "Pregunta sugerida al cliente",
             str(question), "")
            for question in other_questions if str(question).strip()
        ]
        + _dependency_review_rows(dependencies, audit_text)
        + _contradiction_review_rows(contradictions, audit_text)
        + _attachment_review_rows(attachments, internal_text)
    )
    rows = []
    keys = set()
    for status, task, reason in candidates:
        key = re.sub(r"\W+", " ", task.casefold()).strip()
        if key and key not in keys:
            keys.add(key)
            rows.append((status, task, reason))
    return render("review_tasks.html", rows=rows)


def question_quality_panel(warnings: Sequence[str]) -> Markup:
    """Explica controles deterministas que dejaron preguntas sin soporte comprobable."""
    return render("question_quality.html", warnings=[str(item) for item in warnings if str(item).strip()])


def reception_assessment_panel(assessment: dict) -> Markup:
    """Panel con la evaluación de recepción: etiqueta, familia de servicio y motivo."""
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


def activities_editor(thread_id: str, activities: Sequence[Mapping[str, Any]],
                      reasons: list[tuple[str, str]] | None = None, hoja: Mapping[str, Any] | None = None) -> Markup:
    """Tabla editable: colores por estado, celdas vacias en gris y una fila nueva al final."""
    def cells(item: Mapping[str, Any]) -> list[dict]:
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
        hoja={"url": "", "error": "", "tiene_carpeta": False, "actualizada": ""} | dict(hoja or {}),
        headers=[label for _, label in ACTIVITY_COLUMNS],
    )
