"""Estado de la revisión de Gmail: cuándo fue la última, cuánto tardó, qué cambió y cómo se cuenta a la persona.

Envuelve a `pipeline.run` para registrar el resultado de cada revisión en `last_check.json`.
"""

from __future__ import annotations

import json
import time
from datetime import datetime
from anka.dominio.errores import ErrorDeAnka
from anka.infra import settings
from anka.infra.work_heartbeat import bridge_state
from anka.infra.logging_setup import get_logger
from anka.infra.modes import ensure_local_or_api_mode
from anka.infra.settings import load_config
from anka.infra.store import connect



from anka.servicios.pipeline import run

log = get_logger(__name__)


LAST_CHECK_FILE = "last_check.json"


def _quote_snapshot() -> dict[str, tuple]:
    db = connect()
    try:
        return {
            row["thread_id"]: (row["status"], row["updated_at"])
            for row in db.execute("SELECT thread_id, status, updated_at FROM quotes")
        }
    finally:
        db.close()


def read_last_check() -> dict | None:
    """Resultado de la última revisión de Gmail, o `None`."""
    try:
        return json.loads((settings.DATA_DIR / LAST_CHECK_FILE).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def checked_run(apply: bool, review_only: bool, gmail_drafts: bool, source: str) -> dict:
    """Ejecuta una revision de Gmail y registra cuando, cuanto tardo y que cambio."""
    before = _quote_snapshot()
    started = time.time()
    error = ""
    try:
        run(apply, review_only, gmail_drafts)
    except Exception as exc:
        error = str(exc) or exc.__class__.__name__
        log.exception("Error en la revisión de Gmail (%s)", source)
    after = _quote_snapshot()
    result = {
        "at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "source": source,
        "seconds": round(time.time() - started, 1),
        "new": len([key for key in after if key not in before]),
        "updated": len([key for key in after if key in before and after[key] != before[key]]),
        "error": error,
    }
    settings.DATA_DIR.mkdir(parents=True, exist_ok=True)
    (settings.DATA_DIR / LAST_CHECK_FILE).write_text(
        json.dumps(result, ensure_ascii=False), encoding="utf-8")
    return result


def _ago(iso: str) -> str:
    try:
        seconds = (datetime.now().astimezone() - datetime.fromisoformat(iso)).total_seconds()
    except (TypeError, ValueError):
        return ""
    if seconds < 90:
        return "hace instantes"
    if seconds < 3600:
        return f"hace {int(seconds // 60)} min"
    if seconds < 86400:
        return f"hace {int(seconds // 3600)} h"
    return f"hace {int(seconds // 86400)} d"


def latest_case_activity() -> str:
    """Marca de tiempo de la actividad más reciente de cualquier caso."""
    db = connect()
    try:
        row = db.execute(
            "SELECT MAX(COALESCE(updated_at, first_seen_at)) AS ts FROM quotes").fetchone()
    finally:
        db.close()
    return row["ts"] or ""


def describe_check(result: dict) -> str:
    """Frase que cuenta cómo salió la última revisión de Gmail."""
    when = result["at"][11:19]
    if result.get("error"):
        return f"La revisión de las {when} falló: {result['error']}"
    return (
        f"Gmail revisado a las {when} en {result['seconds']} s: "
        f"{result['new']} solicitudes nuevas, {result['updated']} actualizadas."
    )


def sync_status_text() -> str:
    """Texto del estado de la sincronización según el modo activo."""
    try:
        mode = load_config().get("active_mode", "local")
    except ErrorDeAnka:
        return "Sin configuración de motor."
    if mode == "chatgpt_work":
        last = latest_case_activity()
        tail = f" Último caso actualizado: {last[:16].replace('T', ' ')} ({_ago(last)})." if last else ""
        return "Motor ChatGPT Work. " + bridge_state()[1] + tail
    label = "Ollama" if mode == "local" else "API de OpenAI"
    last_check = read_last_check()
    if last_check:
        text = describe_check(last_check)
        return f"Motor {label}. {text} ({_ago(last_check['at'])}). El botón Actualizar correo revisa al instante."
    return (
        f"Motor {label}. Aún no se ha revisado Gmail desde este panel. La revisión periódica "
        "solo ocurre si el piloto (Piloto borradores Gmail.bat) está abierto, cada 5 min."
    )


def watch(apply: bool, review_only: bool, seconds: int, gmail_drafts: bool = False) -> None:
    """Repite `run` cada N segundos hasta Ctrl+C."""
    if seconds < 30:
        raise ErrorDeAnka("--watch-seconds debe ser de al menos 30 segundos.")
    log.info("Vigilando Gmail cada %s segundos. Presiona Ctrl+C para detener.", seconds)
    try:
        while True:
            log.info(time.strftime("\n[%Y-%m-%d %H:%M:%S] Revisando buzón..."))
            ensure_local_or_api_mode(load_config())  # en modo Work el bot se detiene
            try:
                checked_run(apply, review_only, gmail_drafts, "piloto")
            except Exception:
                log.exception("Error en ciclo de revisión")
            time.sleep(seconds)
    except KeyboardInterrupt:
        log.info("\nVigilancia detenida por el usuario.")
