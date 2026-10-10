"""Edicion y exportacion de la tabla de actividades (sin acceso a Gmail ni Drive)."""

from __future__ import annotations

import re

from anka.dominio.analisis import Actividad

from collections.abc import Mapping, Sequence
from io import BytesIO
from typing import Any, cast

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
_NUMERO = re.compile(r"\d[\d.,]*")


def numero_de_cantidad(texto: object) -> float | None:
    """La primera cantidad de un texto («544,5 ha», «1.200 m», «12.5»), o None si no hay ninguna."""
    encontrado = _NUMERO.search(str(texto or ""))
    if not encontrado:
        return None
    token = encontrado.group(0).rstrip(".,")
    if "." in token and "," in token:
        decimal = "." if token.rfind(".") > token.rfind(",") else ","
        token = token.replace("," if decimal == "." else ".", "").replace(decimal, ".")
    else:
        separador = "." if "." in token else "," if "," in token else ""
        if separador:
            partes = token.split(separador)
            miles = len(partes) > 2 or (len(partes[-1]) == 3 and partes[0] not in ("0", "00") and len(partes[0]) <= 3)
            token = "".join(partes) if miles else partes[0] + "." + partes[1]
    try:
        return float(token)
    except ValueError:
        return None


_UNIDADES = {
    "ha": "ha", "hectarea": "ha", "hectareas": "ha", "hectárea": "ha", "hectáreas": "ha",
    "km": "km", "kilometro": "km", "kilometros": "km", "kilómetro": "km", "kilómetros": "km",
    "m": "m", "mt": "m", "mts": "m", "metro": "m", "metros": "m",
    "km2": "km²", "km²": "km²", "kilometros cuadrados": "km²", "kilómetros cuadrados": "km²",
    "m2": "m²", "m²": "m²", "metros cuadrados": "m²",
}


def unidad_canonica(unidad: str) -> str:
    """La unidad escrita de una sola forma («Ha», «hectáreas» → «ha»); una que no se reconoce queda como vino."""
    return _UNIDADES.get(unidad.strip().rstrip(".").casefold(), unidad)


_NUMERO_EN_PALABRAS = re.compile(
    r"\b(?:un|uno|una|dos|tres|cuatro|cinco|seis|siete|ocho|nueve|diez|once|doce|quince|veinte|treinta|cuarenta|"
    r"cincuenta|cien|ciento|mil|media|medio)\b", re.IGNORECASE)
_DINERO = re.compile(
    r"\$|\b(?:cop|usd|eur|mcop|pesos?|d[oó]lar(?:es)?)\b|valor guardado|[=+]\s*\w+\(", re.IGNORECASE)
_REFERENCIA_DE_CELDA = re.compile(r"[A-Za-z]{1,3}\d+\s*=\s*(.*)")
_SIN_DATO = frozenset(
    {"no indicado", "no especificado", "no aplica", "n/a", "pendiente", "por confirmar", "sin definir", "-"})


def tiene_cifra(texto: object) -> bool:
    """¿Trae una cifra, en dígitos o en una palabra común («una», «cinco», «mil»)? «Ha» o «Área» no son una cantidad."""
    escrito = str(texto or "")
    return bool(re.search(r"\d", escrito) or _NUMERO_EN_PALABRAS.search(escrito))


def _dejar_pendiente(row: dict, motivo: str, unidad: str) -> None:
    """La cantidad no era una medida: queda «no indicado», la fila pendiente y se anota por qué (R12)."""
    row["quantity"] = "no indicado"
    row["unit"] = unidad
    row["estado"] = "pendiente"
    nota = f"{motivo} La fila queda pendiente."
    notas = str(row.get("notes") or "")
    if nota not in notas:
        row["notes"] = f"{notas} {nota}".strip()


def limpiar_cantidad_y_unidad(row: dict) -> None:
    """Evita que la unidad aparezca dos veces: el modelo suele escribir «544.5 ha» en la cantidad y «ha» en la unidad.

    Si la cantidad es solo «número + unidad», se queda con el número; si ya menciona todas las unidades (varias
    magnitudes en un texto), se vacía la unidad. Una cantidad sin la unidad no se toca. Al final la unidad se escribe
    de una sola forma (`unidad_canonica`). Una cantidad que no es una cifra (una unidad suelta, una etiqueta, un valor
    en dinero) queda «no indicado» y la fila pendiente.
    """
    quantity, unit = str(row.get("quantity") or "").strip(), str(row.get("unit") or "").strip()
    celda = _REFERENCIA_DE_CELDA.fullmatch(quantity)
    if celda:      # el texto leído de una hoja trae «G9=1»: el valor es lo que sigue al igual
        quantity = celda.group(1).strip()
        row["quantity"] = quantity or "no indicado"
    if _DINERO.search(quantity) or _DINERO.search(unit):    # la Fase 1 no cotiza: un valor en dinero no es una cantidad
        return _dejar_pendiente(row, "La cantidad que venía era un valor en dinero, no una medida.", "no indicado")
    if quantity and not tiene_cifra(quantity) and quantity.casefold() not in _SIN_DATO:
        return _dejar_pendiente(row, "En la cantidad no venía una cifra.", unit or quantity)
    if quantity and unit:
        _quitar_unidad_repetida(row, quantity, unit)
    if row.get("unit"):
        row["unit"] = unidad_canonica(str(row["unit"]))


def _quitar_unidad_repetida(row: dict, quantity: str, unit: str) -> None:
    plain = re.fullmatch(rf"([\d.,]+)\s*{re.escape(unit)}\.?", quantity, re.IGNORECASE)
    if plain:
        row["quantity"] = plain.group(1)
        return
    tokens = [token for token in re.split(r"\s*(?:[,;/]|\sy\s)\s*", unit) if token]
    if tokens and all(re.search(rf"(?<!\w){re.escape(token)}(?!\w)", quantity, re.IGNORECASE) for token in tokens):
        row["unit"] = ""


MAX_ROWS = 200      # tope de filas que se acepta de un formulario: una tabla desmesurada no se guarda
MAX_CELL = 2000      # tope de caracteres por celda, por la misma razón


def parse_activities_form(form: dict[str, list[str]]) -> list[Actividad]:
    """Reconstruye las actividades desde el formulario; ignora filas borradas o vacías."""
    try:
        count = min(int(form.get("row_count", ["0"])[0]), MAX_ROWS)
    except ValueError:
        raise ValueError("Formulario de actividades no válido.") from None
    activities = []
    for index in range(count + 1):  # +1: fila nueva al final
        if form.get(f"a{index}_del", [""])[0]:
            continue
        item: dict[str, str] = {
            key: (form.get(f"a{index}_{key}", [""])[0] or "").strip()[:MAX_CELL]
            for key, _ in ACTIVITY_COLUMNS
        }
        if not any(item[key] for key, _ in ACTIVITY_COLUMNS if key != "estado"):
            continue
        if item["estado"] not in ESTADOS:
            item["estado"] = "pendiente"
        activities.append(cast(Actividad, item))
    return activities


def _cell(value: object) -> str:
    text = "" if value is None else str(value)
    # evita que Excel interprete el texto como formula
    return "'" + text if text[:1] in ("=", "+", "-", "@") else text


def build_workbook(quotes: Sequence[Mapping[str, Any]]) -> bytes:
    """Excel con una hoja «Actividades»: una fila por actividad con su proyecto, cliente y estado, coloreada."""
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
