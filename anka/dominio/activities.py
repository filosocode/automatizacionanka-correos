"""Edicion y exportacion de la tabla de actividades (sin acceso a Gmail ni Drive)."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from io import BytesIO
from typing import Any

ACTIVITY_COLUMNS = [
    ("activity", "Actividad"),
    ("servicio_tecnica", "Servicio/técnica"),
    ("ubicacion", "Ubicación"),
    ("quantity", "Cantidad"),
    ("unit", "Unidad"),
    ("entregable", "Entregable"),
    ("plazo_restriccion", "Plazo/restricción"),
    ("fuente", "Fuente"),
    ("estado", "Estado"),
    ("notes", "Notas"),
]
ESTADOS = ("confirmado", "pendiente", "contradictorio")


def normalize_estado(value: object) -> str:
    """Lleva cualquier texto de estado a uno de los tres valores (R12).

    «confirmado; soporte pendiente» o «confirmado en fuentes; revisión pendiente» no están
    confirmados del todo: si el texto menciona algo pendiente o por aclarar, es `pendiente`.
    """
    text = str(value or "").casefold()
    if "contradic" in text:
        return "contradictorio"
    if any(mark in text for mark in ("pendiente", "revisi", "requiere", "aclar", "falta", "no indicad")):
        return "pendiente"
    return "confirmado" if text.startswith("confirmado") else "pendiente"
MAX_ROWS = 200
MAX_CELL = 2000


def parse_activities_form(form: dict[str, list[str]]) -> list[dict]:
    """Reconstruye las actividades desde el formulario; ignora filas borradas o vacías."""
    try:
        count = min(int(form.get("row_count", ["0"])[0]), MAX_ROWS)
    except ValueError:
        raise ValueError("Formulario de actividades no válido.") from None
    activities = []
    for index in range(count + 1):  # +1: fila nueva al final
        if form.get(f"a{index}_del", [""])[0]:
            continue
        item = {
            key: (form.get(f"a{index}_{key}", [""])[0] or "").strip()[:MAX_CELL]
            for key, _ in ACTIVITY_COLUMNS
        }
        if not any(item[key] for key, _ in ACTIVITY_COLUMNS if key != "estado"):
            continue
        if item["estado"] not in ESTADOS:
            item["estado"] = "pendiente"
        activities.append(item)
    return activities


def _cell(value: object) -> str:
    text = "" if value is None else str(value)
    # evita que Excel interprete el texto como formula
    return "'" + text if text[:1] in ("=", "+", "-", "@") else text


def build_workbook(quotes: Sequence[Mapping[str, Any]]) -> bytes:
    """Excel con una hoja «Actividades»: una fila por actividad de cada caso, con su proyecto, cliente y estado, coloreada por confirmación."""
    from openpyxl import Workbook
    from openpyxl.styles import Alignment, Font, PatternFill
    from openpyxl.utils import get_column_letter
    from openpyxl.worksheet.worksheet import Worksheet

    fills = {
        "confirmado": PatternFill("solid", fgColor="D9EAD3"),
        "pendiente": PatternFill("solid", fgColor="FFF2CC"),
        "contradictorio": PatternFill("solid", fgColor="F4CCCC"),
    }
    workbook = Workbook()
    sheet = workbook.active
    assert isinstance(sheet, Worksheet)
    sheet.title = "Actividades"
    headers = ["Hilo", "Proyecto", "Cliente", "Estado del caso"] + [
        label for _, label in ACTIVITY_COLUMNS
    ]
    sheet.append(headers)
    for header_cell in sheet[1]:
        header_cell.font = Font(bold=True, color="FFFFFF")
        header_cell.fill = PatternFill("solid", fgColor="126A62")
        header_cell.alignment = Alignment(vertical="center", wrap_text=True)
    for quote in quotes:
        analysis = quote.get("analysis") or {}
        base = [
            quote.get("thread_id", ""),
            analysis.get("project_name") or "",
            quote.get("client_name") or "",
            quote.get("status_label") or quote.get("status") or "",
        ]
        for item in analysis.get("activities") or [{}]:
            row = [_cell(value) for value in base] + [
                _cell(item.get(key) or ("no indicado" if key != "estado" and item else ""))
                for key, _ in ACTIVITY_COLUMNS
            ]
            sheet.append(row)
            state_cell = sheet.cell(row=sheet.max_row, column=len(base) + 9)
            fill = fills.get(str(state_cell.value))
            if fill:
                state_cell.fill = fill
    widths = [14, 28, 24, 26, 34, 22, 22, 12, 12, 26, 26, 30, 14, 30]
    for position, width in enumerate(widths, 1):
        sheet.column_dimensions[get_column_letter(position)].width = width
    for sheet_row in sheet.iter_rows(min_row=2):
        for body_cell in sheet_row:
            body_cell.alignment = Alignment(vertical="top", wrap_text=True)
    sheet.freeze_panes = "E2"
    sheet.auto_filter.ref = sheet.dimensions
    buffer = BytesIO()
    workbook.save(buffer)
    return buffer.getvalue()
