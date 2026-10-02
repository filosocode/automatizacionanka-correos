"""Contrato entre ChatGPT Work y el puente: valida el JSON del borrador contra `spec/contrato_work.schema.json`.

El esquema es la fuente de verdad (campos, tipos y estados permitidos). Se valida un subconjunto de
JSON Schema —type, required, enum, properties, items, minLength— sin dependencias externas. Un valor
`null` en un campo opcional se trata como ausente: Work a veces lo escribe así y no debe rechazar el caso.
"""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any

SCHEMA_PATH = Path(__file__).resolve().parent / "spec" / "contrato_work.schema.json"

_TYPES: dict[str, type | tuple[type, ...]] = {
    "string": str, "array": list, "object": dict, "boolean": bool,
    "number": (int, float), "integer": int,
}


@lru_cache(maxsize=1)
def load_schema() -> dict:
    return json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))


def _type_ok(value: Any, expected: str | list) -> bool:
    names = expected if isinstance(expected, list) else [expected]
    for name in names:
        python_type = _TYPES[name]
        if isinstance(value, bool) and name in ("number", "integer"):
            continue
        if isinstance(value, python_type):
            return True
    return False


def validate(instance: Any, schema: dict | None = None, path: str = "") -> list[str]:
    """Lista de errores (vacía si el valor cumple el esquema). No lanza."""
    schema = schema if schema is not None else load_schema()
    where = path or "el JSON"
    errors: list[str] = []
    if "type" in schema and not _type_ok(instance, schema["type"]):
        expected = schema["type"] if isinstance(schema["type"], str) else " o ".join(schema["type"])
        return [f"{where} debe ser de tipo {expected}."]
    if "enum" in schema and instance not in schema["enum"]:
        errors.append(f"{where} tiene un valor no permitido: {instance!r}.")
    if isinstance(instance, str) and "minLength" in schema and len(instance.strip()) < schema["minLength"]:
        errors.append(f"{where} no puede estar vacío.")
    if isinstance(instance, dict):
        for key in schema.get("required", []):
            if instance.get(key) is None:
                errors.append(f"Falta el campo {key}.")
        for key, sub in schema.get("properties", {}).items():
            if instance.get(key) is not None:
                errors.extend(validate(instance[key], sub, f"{path + '.' if path else ''}{key}"))
    if isinstance(instance, list) and "items" in schema:
        for index, item in enumerate(instance):
            errors.extend(validate(item, schema["items"], f"{where}[{index}]"))
    return errors
