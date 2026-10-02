"""Modo de operación (local, api, chatgpt_work) y su automatización asociada."""

from __future__ import annotations

import os
import re
from pathlib import Path

from anka.infra.llm import check_ollama
from anka.infra.settings import ACTIVE_MODES, load_config, write_config


AUTOMATION_ID = "cotizaciones-gmail-anka"


def automation_interval_label() -> str:
    """Texto con cada cuánto corre la automatización de Codex («cada 5 minutos»), o vacío si no se sabe."""
    codex_root = Path(os.getenv("CODEX_HOME", Path.home() / ".codex"))
    path = codex_root / "automations" / AUTOMATION_ID / "automation.toml"
    try:
        content = path.read_text(encoding="utf-8")
    except OSError:
        return "según la frecuencia configurada"
    match = re.search(r'^rrule\s*=\s*"([^"]+)"', content, re.MULTILINE)
    if not match:
        return "según la frecuencia configurada"
    rule = match.group(1).removeprefix("RRULE:")
    interval_match = re.search(r"(?:^|;)INTERVAL=(\d+)(?:;|$)", rule)
    interval = int(interval_match.group(1)) if interval_match else 1
    if "FREQ=MINUTELY" in rule:
        return "cada minuto" if interval == 1 else f"cada {interval} minutos"
    if "FREQ=HOURLY" in rule:
        return "cada hora" if interval == 1 else f"cada {interval} horas"
    return "según la frecuencia configurada"


def codex_automation_status() -> str | None:
    """Estado (ACTIVE/PAUSED…) de la automatización de Codex, o `None` si no existe."""
    try:
        content = codex_automation_path().read_text(encoding="utf-8")
    except OSError:
        return None
    match = re.search(r'^status\s*=\s*"([A-Z_]+)"', content, re.MULTILINE)
    return match.group(1) if match else None


def ensure_local_or_api_mode(config: dict) -> None:
    """Detiene el comando si el modo es ChatGPT Work: en ese modo Python no lee Gmail."""
    if config.get("active_mode", "local") == "chatgpt_work":
        raise SystemExit(
            "active_mode está en 'chatgpt_work'. No ejecutes el bot local sobre Gmail. "
            "Pausa/cambia esa modalidad en config.json antes de usar run/watch."
        )


def print_mode() -> None:
    """Muestra el modo activo y qué lee el correo en él."""
    config = load_config()
    mode = config.get("active_mode", "local")
    provider = config["llm"]["provider"]
    print(f"Modo activo: {mode}")
    if mode == "local":
        print(f"Motor: Ollama local ({config['llm'].get('model')})")
        print("Orquestador permitido: bot local Python.")
    elif mode == "api":
        print(f"Motor: API ({provider}, {config['llm'].get('model')})")
        print("Orquestador permitido: bot local Python usando API.")
    else:
        print("Motor/orquestador: ChatGPT Work/Plus con skill y conectores.")
        print("Orquestador local bloqueado para evitar duplicados.")


def codex_automation_path() -> Path:
    """Ruta del archivo de la automatización de Codex."""
    codex_root = Path(os.getenv("CODEX_HOME", Path.home() / ".codex"))
    return codex_root / "automations" / AUTOMATION_ID / "automation.toml"


def set_codex_automation(active: bool) -> str:
    """Pausa o reactiva la automatización de Codex/Work cambiando solo su línea `status`."""
    path = codex_automation_path()
    try:
        content = path.read_text(encoding="utf-8")
    except OSError:
        return "No encontré la automatización de Codex/Work; pausa la tarea manualmente si existe."
    wanted = "ACTIVE" if active else "PAUSED"
    match = re.search(r'^status\s*=\s*"([A-Z_]+)"', content, re.MULTILINE)
    if not match:
        return "La automatización de Codex/Work no tiene campo status; pausa la tarea manualmente."
    if match.group(1) == wanted:
        return f"La automatización de Codex/Work ya estaba {'activa' if active else 'pausada'}."
    updated = content[:match.start()] + f'status = "{wanted}"' + content[match.end():]
    path.write_text(updated, encoding="utf-8")
    return f"Automatización de Codex/Work {'activada' if active else 'pausada'}."


def set_mode(mode: str) -> str:
    """Cambia el motor activo en config.json y devuelve un mensaje para el usuario."""
    if mode not in ACTIVE_MODES:
        raise SystemExit("Usa local, api o chatgpt_work.")
    config = load_config()
    if mode == "api" and not os.getenv("OPENAI_API_KEY"):
        raise SystemExit(
            "No se cambió a API: aún no hay clave de OpenAI. Cuando la tengas, ejecuta en "
            "PowerShell: setx OPENAI_API_KEY \"tu_clave\"; cierra y vuelve a abrir el "
            "dashboard, y elige API de nuevo."
        )
    if mode == "local":
        config.setdefault("llm", {})["provider"] = "ollama"
        config["llm"].setdefault("model", "qwen2.5:7b")
        config["llm"].setdefault("base_url", "http://127.0.0.1:11434")
        check_ollama(config["llm"])
    config["active_mode"] = mode
    if mode == "local":
        message = "Modo local activado: Ollama y bot Python. "
        automation_active = False
    elif mode == "api":
        config.setdefault("llm", {})["provider"] = "openai"
        config["llm"].setdefault("model", "gpt-4.1-mini")
        message = (
            "Modo API activado: el análisis usa la API de OpenAI y genera costo. "
        )
        automation_active = False
    else:
        message = (
            "Modo ChatGPT Work activado: el bot local queda bloqueado sobre Gmail. "
        )
        automation_active = True
    write_config(config)
    return message + set_codex_automation(automation_active)
