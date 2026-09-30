"""Ajustes y carpeta de datos local (fuera del repositorio)."""

from __future__ import annotations

import json
import os
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


def load_config() -> dict:
    path = DATA_DIR / "config.json"
    if not path.exists():
        raise SystemExit(f"Crea {path} a partir de config.example.json.")
    config = json.loads(path.read_text(encoding="utf-8-sig"))
    mode = config.get("active_mode", "local")
    if mode not in ACTIVE_MODES:
        raise SystemExit("active_mode debe ser 'local', 'api' o 'chatgpt_work'.")
    if not isinstance(config.get("required_fields"), list):
        raise SystemExit("required_fields debe ser una lista.")
    if not isinstance(config.get("own_addresses", []), list):
        raise SystemExit("own_addresses debe ser una lista de correos propios o aliases.")
    if config.get("forwarded_reply_policy", "internal_reviewer") not in {
        "internal_reviewer", "original_sender",
    }:
        raise SystemExit(
            "forwarded_reply_policy debe ser 'internal_reviewer' u 'original_sender'."
        )
    engine = config.get("llm", {})
    if not isinstance(engine, dict) or engine.get("provider") not in {"ollama", "openai"}:
        raise SystemExit("Define llm.provider como 'ollama' o 'openai' en config.json.")
    if mode == "local" and engine["provider"] != "ollama":
        raise SystemExit("active_mode 'local' requiere llm.provider 'ollama'.")
    if mode == "api" and engine["provider"] != "openai":
        raise SystemExit("active_mode 'api' requiere llm.provider 'openai'.")
    if engine["provider"] == "ollama" and not engine.get("base_url"):
        raise SystemExit("Define llm.base_url para el motor Ollama.")
    return config


def write_config(config: dict) -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    (DATA_DIR / "config.json").write_text(
        json.dumps(config, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
