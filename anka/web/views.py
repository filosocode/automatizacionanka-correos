"""Fragmentos HTML del dashboard sin acceso a datos ni a Gmail/Drive."""

from __future__ import annotations

import re
from collections import Counter
from collections.abc import Mapping, Sequence
from typing import Any

from markupsafe import Markup

from anka.dominio.preguntas import INTERNA, identificador, limpiar
from anka.dominio.activities import ACTIVITY_COLUMNS, ESTADOS, normalize_estado
from anka.dominio.attachments import compactar_paginas
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


# Qué enlace de la navegación es la página actual (aria-current, U-28). Las demás páginas no marcan ninguno.
NAV_ACTUAL = {"Resumen comercial": "/", "Solicitudes": "/requests", "Descartadas": "/discarded", "Archivo": "/archive"}


def render_page(title: str, body: str, message: str = "", unread_count: int = 0,
                mode: str | None = None, api_key_set: bool = True) -> str:
    """Página completa: encabezado, avisos, selector de modo y el contenido recibido."""
    return str(render(
        "layout.html", title=title, body=body, message=message, unread_count=unread_count,
        mode_selector=mode_selector(mode, api_key_set), is_work=(mode == "chatgpt_work"),
        nav_actual=NAV_ACTUAL.get(title, ""),
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


_PALABRAS = {
    "extraido": "Leído", "completa": "Completa", "parcial": "Parcial", "lectura_parcial": "Lectura parcial",
    "no_legible": "No se pudo leer", "unreadable": "No se pudo leer", "bloqueado": "Bloqueado: lo revisa una persona",
}


def _palabra(codigo: str) -> str:
    """Un código interno de lectura, dicho como lo diría una persona; lo desconocido pasa igual."""
    return _PALABRAS.get(codigo.strip().casefold().replace(" ", "_"), codigo)


_SIN_LEER = frozenset({"bloqueado", "lectura_parcial", "no_legible", "unreadable", "parcial", "partial"})


def _codigo(valor: object) -> str:
    return str(valor or "").casefold().replace(" ", "_")


def adjunto_requiere_atencion(item: Mapping[str, Any]) -> bool:
    """Un adjunto que no se leyó por completo: una persona debe revisarlo (U-31)."""
    return (_codigo(item.get("status")) in _SIN_LEER
            or _codigo(item.get("coverage")) in {"parcial", "no_legible", "unreadable"})


def attachments_panel(attachments: Sequence[Mapping[str, Any]]) -> Markup:
    """Los archivos en una línea (U-31): lo que hay que revisar a la vista y lo leído plegado."""
    items = [item for item in attachments if isinstance(item, Mapping)]
    atencion = [item for item in items if adjunto_requiere_atencion(item)]
    leidos = [item for item in items if not adjunto_requiere_atencion(item)]
    if not items:
        resumen = "Sin archivos adjuntos"
    else:
        cuantos = f"{len(items)} archivo{'' if len(items) == 1 else 's'}"
        resumen = f"{cuantos} · {f'{len(atencion)} por revisar' if atencion else 'todos leídos'}"
    return render("attachments_panel.html", resumen=resumen, atencion=len(atencion),
                  tabla_atencion=attachments_table(atencion) if atencion else Markup(""),
                  leidos=len(leidos), tabla_leidos=attachments_table(leidos) if leidos else Markup(""))


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
            _palabra(str(item.get("status") or "")),
            _palabra(str(item.get("coverage") or "sin detalle")),
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
    for item in attachments:
        if not isinstance(item, Mapping):
            continue
        filename = str(item.get("filename") or "adjunto")
        if not adjunto_requiere_atencion(item):
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
    questions_in_decision: bool = False,
    decisiones: Sequence[Mapping[str, Any]] = (),
) -> Markup:
    """Resume revisiones y dependencias desde las fuentes disponibles, incluidos payloads antiguos.

    Con `questions_in_decision` las preguntas para el cliente se editan en la decisión: aquí solo se cuentan.
    """
    audit_text = _audit_search_text(audit)
    internal_text = _internal_review_search_text(audit)
    # Lo que una persona ya decidió (U-22) se muestra según su decisión, no según la sugerencia del análisis.
    decididas = {str(p.get("id")) for p in decisiones if p.get("decision")}

    def sin_decidir(texto: object) -> bool:
        return identificador(limpiar(texto)) not in decididas

    candidates = (
        [("Revisión interna pendiente", str(p.get("texto")), "Lo decidiste resolver dentro de Anka")
         for p in decisiones if p.get("decision") == INTERNA]
        + _audit_review_rows([i for i in audit if not isinstance(i, Mapping) or sin_decidir(i.get("question"))],
                             waiting_for_client)
        + [
            ("Esperando respuesta del cliente" if waiting_for_client else "Pregunta sugerida al cliente",
             str(question), "")
            for question in other_questions if str(question).strip() and sin_decidir(question)
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
            rows.append((status, task, compactar_paginas(reason)))
    moved = 0
    if questions_in_decision:
        moved = sum(status == "Pregunta sugerida al cliente" for status, _, _ in rows)
        rows = [row for row in rows if row[0] != "Pregunta sugerida al cliente"]
    return render("review_tasks.html", rows=rows, moved_questions=moved)


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


def _sin_cambios(ultima: Mapping[str, Any] | None) -> str:
    """El texto de la barra de guardado cuando no hay nada por guardar: la última versión que guardó una persona."""
    if not ultima:
        return "Sin cambios por guardar"
    cuando = str(ultima["created_at"])[:16].replace("T", " ")
    return f"Sin cambios por guardar · última versión: {ultima['version']} ({cuando})"


MAX_FILAS_VISIBLES = 8


def _resumen_de_actividades(activities: Sequence[Mapping[str, Any]]) -> str:
    """«4 actividades · 2 por completar»: cuántas hay y cuántas no están confirmadas (R12). Vacío si no hay ninguna."""
    if not activities:
        return ""
    faltan = sum(1 for item in activities if normalize_estado(item.get("estado")) != "confirmado")
    cuantas = f"{len(activities)} actividad{'' if len(activities) == 1 else 'es'}"
    return f"{cuantas} · {f'{faltan} por completar' if faltan else 'todas completas'}"


def activities_editor(thread_id: str, activities: Sequence[Mapping[str, Any]],
                      reasons: list[tuple[str, str]] | None = None, hoja: Mapping[str, Any] | None = None,
                      ultima: Mapping[str, Any] | None = None) -> Markup:
    """Tabla editable: colores por estado, celdas vacias en gris y una fila nueva al final."""
    def cells(item: Mapping[str, Any]) -> list[dict]:
        return [{"key": key, "value": str(item.get(key) or "")} for key, _ in ACTIVITY_COLUMNS]

    rows = [
        {"index": index, "is_new": False, "cells": cells(item), "by": {c["key"]: c["value"] for c in cells(item)},
         # con más de 8, las confirmadas más allá de la octava se pliegan; las incompletas siempre se ven (U-31)
         "extra": index >= MAX_FILAS_VISIBLES and normalize_estado(item.get("estado")) == "confirmado"}
        for index, item in enumerate(activities)
    ]
    new_row = {key: "" for key, _ in ACTIVITY_COLUMNS} | {"estado": "pendiente"}
    rows.append({"index": len(activities), "is_new": True, "extra": False, "cells": cells(new_row),
                 "by": {c["key"]: c["value"] for c in cells(new_row)}})
    return render(
        "activities_editor.html",
        thread_id=thread_id, row_count=len(activities), rows=rows, states=ESTADOS,
        resumen=_resumen_de_actividades(activities), escondidas=sum(1 for row in rows if row["extra"]),
        total=len(activities),
        reasons=reasons or [],
        sin_cambios=_sin_cambios(ultima),
        hoja={"url": "", "error": "", "tiene_carpeta": False, "actualizada": ""} | dict(hoja or {}),
        headers=[label for _, label in ACTIVITY_COLUMNS],
    )
