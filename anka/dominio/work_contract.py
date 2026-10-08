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
import re
import unicodedata

SCHEMA_PATH = Path(__file__).resolve().parents[2] / "spec" / "contrato_work.schema.json"

_TYPES: dict[str, type | tuple[type, ...]] = {
    "string": str, "array": list, "object": dict, "boolean": bool,
    "number": (int, float), "integer": int,
}


@lru_cache(maxsize=1)
def load_schema() -> dict:
    """Carga el esquema JSON del contrato con Work (`spec/contrato_work.schema.json`)."""
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


def _validate_question_evidence(payload: dict) -> list[str]:
    """Calcula advertencias mecánicas sobre el soporte de preguntas del contrato v2."""
    errors: list[str] = []
    by_id = _evidence_findings(payload.get("evidence_register") or [], errors)
    seen_questions: dict[str, int] = {}
    seen_targets: dict[tuple[tuple[str, ...], str], tuple[int, str]] = {}
    for index, audit in enumerate(payload.get("question_audit") or []):
        if isinstance(audit, dict):
            errors.extend(_audit_question(index, audit, by_id, payload))
            if audit.get("disposition") == "ask_client":
                question_key = _question_key(audit.get("question"))
                if question_key:
                    if question_key in seen_questions:
                        errors.append(
                            f"question_audit[{index}] repite la pregunta de question_audit["
                            f"{seen_questions[question_key]}] (ignorando mayúsculas, tildes y puntuación)."
                        )
                    else:
                        seen_questions[question_key] = index
                refs = audit.get("target_finding_ids") or []
                unresolved = _question_key(audit.get("unresolved"))
                target_key = (tuple(sorted(set(refs))), unresolved) if isinstance(refs, list) else ((), "")
                if target_key[0] and unresolved:
                    if target_key in seen_targets and question_key != seen_targets[target_key][1]:
                        errors.append(
                            f"question_audit[{index}] vuelve a preguntar por los mismos hallazgos y dato pendiente "
                            f"de question_audit[{seen_targets[target_key][0]}]."
                        )
                    else:
                        seen_targets.setdefault(target_key, (index, question_key))
    return errors


def _question_key(value: Any) -> str:
    """Normaliza una pregunta para detectar duplicados textuales sin comparación semántica."""
    text = str(value or "").casefold()
    text = "".join(char for char in unicodedata.normalize("NFD", text)
                   if unicodedata.category(char) != "Mn")
    return re.sub(r"[^a-z0-9]+", " ", text).strip()


def _evidence_findings(findings: list, errors: list[str]) -> dict[str, dict]:
    """Indexa hallazgos y registra ids repetidos."""
    by_id: dict[str, dict] = {}
    for index, finding in enumerate(findings):
        if not isinstance(finding, dict):
            continue
        finding_id = str(finding.get("id") or "").strip()
        if finding_id in by_id:
            errors.append(f"evidence_register[{index}].id está duplicado: {finding_id}.")
        elif finding_id:
            by_id[finding_id] = finding
    return by_id


def _audit_question(index: int, audit: dict, by_id: dict[str, dict], payload: dict) -> list[str]:
    """Valida referencias y soporte verificable de una pregunta."""
    errors: list[str] = []
    disposition = audit.get("disposition")
    if disposition not in {"ask_client", "internal_review", "do_not_ask"}:
        return [f"question_audit[{index}] debe indicar disposition para el contrato v2."]
    refs = audit.get("target_finding_ids") or []
    if len(refs) != len(set(refs)):
        errors.append(f"question_audit[{index}] repite un target_finding_id.")
    targets = []
    for ref in refs:
        finding = by_id.get(ref)
        if finding is None:
            errors.append(f"question_audit[{index}] referencia un hallazgo inexistente: {ref}.")
        else:
            targets.append(finding)
    if disposition == "ask_client":
        errors.extend(_ask_client_warnings(index, audit, targets, payload))
    return errors


def _ask_client_warnings(index: int, audit: dict, targets: list[dict], payload: dict) -> list[str]:
    """Comprueba que una pregunta al cliente tenga evidencia de un faltante suyo."""
    if not targets:
        return [f"question_audit[{index}] pregunta al cliente sin hallazgo objetivo."]
    errors: list[str] = []
    if not str(audit.get("unresolved") or "").strip():
        errors.append(f"question_audit[{index}] debe precisar qué subdato sigue sin resolver.")
    eligible_status = {"partial", "absent", "contradictory", "unconfirmed"}
    eligible = [item for item in targets if item.get("status") in eligible_status
                and item.get("owner") in {"client", "contractor"} and item.get("blocking") is True]
    if not eligible:
        errors.append(
            f"question_audit[{index}] pregunta al cliente sin evidencia bloqueante pendiente atribuible "
            "al cliente/contratista."
        )
    if any(item.get("status") == "explicit" for item in targets):
        errors.append(
            f"question_audit[{index}] incluye un hallazgo explícito; separa ese dato de lo que realmente falta."
        )
    if not audit.get("reviewed_sources"):
        errors.append(f"question_audit[{index}] debe registrar las fuentes revisadas antes de preguntar.")
    if any(item.get("status") == "unreadable" for item in targets):
        errors.append(
            f"question_audit[{index}] no debe pedir al cliente resolver una limitación de lectura; "
            "use internal_review."
        )
    errors.extend(_unreadable_attachment_warning(index, audit, payload))
    return errors


def _unreadable_attachment_warning(index: int, audit: dict, payload: dict) -> list[str]:
    """Advierte si la pregunta traslada al cliente un límite de lectura de archivo."""
    question = str(audit.get("question") or "").casefold()
    unreadable_types = {
        str(item.get("filename") or "").rsplit(".", 1)[-1].casefold()
        for item in (payload.get("attachments") or [])
        if isinstance(item, dict)
        and (item.get("coverage") == "no_legible"
             or str(item.get("status") or "").casefold() in {"no_legible", "unreadable", "ilegible"})
        and "." in str(item.get("filename") or "")
    }
    mentioned = sorted(ext for ext in unreadable_types
                       if re.search(rf"(?<![a-z0-9]){re.escape(ext)}(?![a-z0-9])", question))
    if not mentioned:
        return []
    extensions = ", .".join(mentioned)
    return [f"question_audit[{index}] pregunta sobre contenido de un adjunto .{extensions} no legible; "
            "primero requiere revisión interna."]


def question_evidence_warnings(payload: dict) -> list[str]:
    """Advertencias accionables; no rechaza análisis ni hace desaparecer borradores."""
    if not isinstance(payload, dict) or "evidence_register" not in payload:
        return []
    return _validate_question_evidence(payload)


def _plain(value: object) -> str:
    """Texto en minúsculas y sin tildes: Work escribe a veces los estados sin ellas."""
    text = unicodedata.normalize("NFKD", str(value or "").casefold())
    return "_".join("".join(char for char in text if not unicodedata.combining(char)).split())


_COMPLETE = {"completa", "completo", "complete", "extraido"}
_INCOMPLETE = {"parcial", "partial", "lectura_parcial", "no_legible", "ilegible", "unreadable", "bloqueado"}


def attachment_coverage_warnings(payload: dict) -> list[str]:
    """Advertencias cuando la cobertura que Work declara por adjunto no es coherente (T-25).

    Una lectura «completa» debe dejar rastro de lo revisado (secciones o evidencia); una parcial o ilegible debe decir
    por qué. Así una lectura superficial no pasa por exhaustiva. No rechaza el análisis: solo avisa.
    """
    warnings = []
    for index, item in enumerate((payload.get("attachments") if isinstance(payload, dict) else None) or []):
        if not isinstance(item, dict):
            continue
        name = item.get("filename") or f"adjunto {index + 1}"
        declared = {_plain(item.get("coverage")), _plain(item.get("status"))}
        if declared & _INCOMPLETE:
            if not str(item.get("reason") or "").strip():
                warnings.append(
                    f"attachments[{index}] ({name}) declara lectura parcial o ilegible sin indicar el motivo."
                )
        elif declared & _COMPLETE and not item.get("reviewed_sections") and not item.get("evidence"):
            warnings.append(
                f"attachments[{index}] ({name}) declara lectura completa sin secciones revisadas ni evidencia."
            )
    return warnings


def corregir_descuidos_de_tipo(payload: dict) -> list[str]:
    """Corrige en el sitio los descuidos de tipo evidentes de Work y devuelve un aviso por cada uno (T-02).

    Hoy: `question_audit[n].unresolved` escrito como booleano (`true`) donde va un texto con el subdato pendiente. No
    hay forma de recuperar ese texto, pero perder el borrador entero por eso es peor: queda vacío y el aviso lo cuenta.
    """
    avisos = []
    for indice, item in enumerate((payload.get("question_audit") if isinstance(payload, dict) else None) or []):
        if isinstance(item, dict) and isinstance(item.get("unresolved"), bool):
            valor = "true" if item["unresolved"] else "false"
            avisos.append(
                f"question_audit[{indice}].unresolved llegó como {valor} y no como texto; se trató como vacío."
            )
            item["unresolved"] = ""
    return avisos
