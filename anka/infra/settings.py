"""Ajustes y carpeta de datos local (fuera del repositorio)."""

from __future__ import annotations

from anka.dominio.configuracion import Configuracion
from anka.dominio.errores import ErrorDeAnka

import json
import os
from collections.abc import Mapping
from typing import Any, cast
from pathlib import Path




def default_data_dir() -> Path:
    """Carpeta fuera del repositorio con credenciales, token, config y datos de casos."""
    override = os.getenv("ANKA_DATA_DIR")
    if override:
        return Path(override).expanduser().resolve()
    base = os.getenv("LOCALAPPDATA") or str(Path.home() / ".local" / "share")
    return Path(base) / "AnkaCorreos"


DATA_DIR = default_data_dir()


ACTIVE_MODES = {"local", "api", "chatgpt_work"}


KNOWN_CONFIG_KEYS = {
    "active_mode", "gmail_query", "own_addresses", "test_sender_addresses", "forwarded_reply_policy", "llm",
    "required_fields", "commercial_filter_terms", "drive_parent_id", "folder_prefix",
    "work_sync_account",
    "work_drive_enabled", "work_drive_parent_id", "work_drive_query",
    "ocr_segundos_por_archivo", "ocr_segundos_por_caso", "ocr_omitir_escaneados",
}
FORWARDED_POLICIES = {"internal_reviewer", "original_sender"}
PROVIDERS = {"ollama", "openai"}


def _is_text_list(value: object) -> bool:
    return isinstance(value, list) and all(isinstance(item, str) for item in value)


def validate_config(config: Mapping[str, Any]) -> list[str]:
    """Lista todos los problemas de config.json (vacía si es válido). No lanza ni imprime."""
    problems: list[str] = []
    mode = config.get("active_mode", "local")
    if mode not in ACTIVE_MODES:
        problems.append("active_mode debe ser 'local', 'api' o 'chatgpt_work'.")

    for key in ("gmail_query", "drive_parent_id", "folder_prefix", "work_sync_account"):
        if key in config and not isinstance(config[key], str):
            problems.append(f"{key} debe ser texto.")
    for key in ("own_addresses", "test_sender_addresses", "commercial_filter_terms"):
        if key in config and not _is_text_list(config[key]):
            problems.append(f"{key} debe ser una lista de textos.")
    for key in ("own_addresses", "test_sender_addresses"):
        for address in config.get(key, []) if _is_text_list(config.get(key, [])) else []:
            if "@" not in address:
                problems.append(f"{key} contiene '{address}', que no parece un correo.")
    if "required_fields" not in config:
        problems.append("required_fields es obligatorio (puede ser una lista vacía).")
    elif not _is_text_list(config["required_fields"]):
        problems.append("required_fields debe ser una lista de textos.")
    problems.extend(_work_drive_problems(config))
    problems.extend(_ocr_problems(config))
    if config.get("forwarded_reply_policy", "internal_reviewer") not in FORWARDED_POLICIES:
        problems.append("forwarded_reply_policy debe ser 'internal_reviewer' u 'original_sender'.")

    engine = config.get("llm")
    if not isinstance(engine, dict):
        problems.append("llm debe ser un objeto con al menos provider.")
        return problems
    problems.extend(_engine_problems(engine, mode))
    return problems


def _ocr_problems(config: Mapping[str, Any]) -> list[str]:
    problems = []
    for key in ("ocr_segundos_por_archivo", "ocr_segundos_por_caso"):
        value = config.get(key, 0)
        if isinstance(value, bool) or not isinstance(value, (int, float)) or value < 0:
            problems.append(f"{key} debe ser un número de segundos (0 = sin límite).")
    if not isinstance(config.get("ocr_omitir_escaneados", False), bool):
        problems.append("ocr_omitir_escaneados debe ser booleano.")
    return problems


def _work_drive_problems(config: Mapping[str, Any]) -> list[str]:
    problems = []
    if not isinstance(config.get("work_drive_enabled", False), bool):
        problems.append("work_drive_enabled debe ser booleano.")
    for key in ("work_drive_parent_id", "work_drive_query"):
        value = config.get(key, "")
        if not isinstance(value, str):
            problems.append(f"{key} debe ser texto.")
        elif config.get("work_drive_enabled") and not value.strip():
            problems.append(f"{key} es obligatorio al activar expedientes de Work.")
    if config.get("work_drive_enabled"):
        if config.get("active_mode") != "chatgpt_work":
            problems.append("work_drive_enabled requiere active_mode 'chatgpt_work'.")
        if config.get("work_drive_parent_id") == config.get("drive_parent_id"):
            problems.append("work_drive_parent_id debe ser una carpeta de recepción distinta de drive_parent_id.")
        account = config.get("work_sync_account", "")
        if not isinstance(account, str) or "@" not in account:
            problems.append("work_sync_account es obligatorio al activar expedientes de Work.")
    return problems


def _engine_problems(engine: dict, mode: str) -> list[str]:
    """Problemas del bloque `llm` de config.json."""
    problems: list[str] = []
    provider = engine.get("provider")
    if provider not in PROVIDERS:
        problems.append("llm.provider debe ser 'ollama' o 'openai'.")
    elif mode == "local" and provider != "ollama":
        problems.append("active_mode 'local' requiere llm.provider 'ollama'.")
    elif mode == "api" and provider != "openai":
        problems.append("active_mode 'api' requiere llm.provider 'openai'.")
    if "model" in engine and (not isinstance(engine["model"], str) or not engine["model"].strip()):
        problems.append("llm.model debe ser un texto no vacío.")
    base_url = engine.get("base_url")
    if provider == "ollama" and not base_url:
        problems.append("Define llm.base_url para el motor Ollama.")
    elif base_url is not None and (
        not isinstance(base_url, str) or not base_url.startswith(("http://", "https://"))
    ):
        problems.append("llm.base_url debe empezar por http:// o https://.")
    if "timeout_seconds" in engine:
        timeout = engine["timeout_seconds"]
        if isinstance(timeout, bool) or not isinstance(timeout, (int, float)) or timeout <= 0:
            problems.append("llm.timeout_seconds debe ser un número mayor que cero.")
    for key, minimum in (("chunk_chars", 1000), ("context_tokens", 8192)):
        if key in engine:
            value = engine[key]
            if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
                problems.append(f"llm.{key} debe ser un entero de al menos {minimum}.")
    for key in ("num_ctx", "num_ctx_max"):
        if key in engine:
            context = engine[key]
            if isinstance(context, bool) or not isinstance(context, int) or context < 2048:
                problems.append(f"llm.{key} debe ser un entero de al menos 2048.")
    return problems


def unknown_config_keys(config: Mapping[str, Any]) -> list[str]:
    """Claves de primer nivel que el programa no usa (suelen ser errores de tipeo)."""
    return sorted(set(config) - KNOWN_CONFIG_KEYS)


def load_config() -> Configuracion:
    """Lee y valida `config.json` de la carpeta de datos; termina con un mensaje claro si falta o es inválido."""
    path = DATA_DIR / "config.json"
    if not path.exists():
        raise ErrorDeAnka(f"Crea {path} a partir de config.example.json.")
    try:
        config = json.loads(path.read_text(encoding="utf-8-sig"))
    except json.JSONDecodeError as exc:
        raise ErrorDeAnka(
            f"{path} no es JSON válido (línea {exc.lineno}, columna {exc.colno}): {exc.msg}."
        ) from exc
    if not isinstance(config, dict):
        raise ErrorDeAnka(f"{path} debe contener un objeto JSON, no {type(config).__name__}.")
    problems = validate_config(config)
    if problems:
        raise ErrorDeAnka(
            f"{path.name} tiene {len(problems)} problema(s):\n"
            + "\n".join(f" - {problem}" for problem in problems)
        )
    return cast(Configuracion, config)   # ya validada: tiene la forma de `Configuracion`


def write_config(config: Mapping[str, Any]) -> None:
    """Guarda `config.json` solo si la configuración resultante es válida."""
    problems = validate_config(config)
    if problems:
        raise ErrorDeAnka(
            "No se guardó config.json; la configuración resultante es inválida:\n"
            + "\n".join(f" - {problem}" for problem in problems)
        )
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    (DATA_DIR / "config.json").write_text(
        json.dumps(config, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
