"""Proceso independiente de preparación Gmail → OCR → Drive, sin envíos ni decisiones comerciales."""

from __future__ import annotations

import json
import time
from datetime import datetime

from anka.dominio.errores import ErrorDeAnka
from anka.infra import settings
from anka.infra.drive_exchange import preparation_lock
from anka.infra.google_services import services
from anka.infra.logging_setup import get_logger
from anka.servicios.work_drive import sync_work_drive

log = get_logger(__name__)


def run(watch_seconds: int = 0, thread_id: str | None = None) -> dict:
    """Ejecuta una pasada o vigila, releyendo configuración y registrando el último resultado."""
    if watch_seconds and watch_seconds < 30:
        raise ErrorDeAnka("work-drive-sync requiere --watch-seconds 0 o al menos 30.")
    with preparation_lock(settings.DATA_DIR / "work_drive.lock"):
        while True:
            config = settings.load_config()
            if not config.get("work_drive_enabled") or config.get("active_mode") != "chatgpt_work":
                return {"status": "desactivado"}
            try:
                gmail, drive = services()
                report = sync_work_drive(gmail, drive, config, thread_id)
            except Exception as exc:
                log.exception("Error del preparador de expedientes (%s)", type(exc).__name__)
                report = {"failures": 1, "error": type(exc).__name__}
            report["at"] = datetime.now().astimezone().isoformat()
            path = settings.DATA_DIR / "work_drive_status.json"
            temporary = path.with_suffix(".tmp")
            temporary.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
            temporary.replace(path)
            if not watch_seconds:
                return report
            time.sleep(watch_seconds)
