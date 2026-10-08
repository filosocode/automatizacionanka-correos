"""Caché en disco de los hallazgos extraídos por tramo (T-25).

Reanalizar un hilo repetiría una llamada al modelo por cada tramo de cada documento largo; la clave incluye el prompt
completo, el modelo y la versión del esquema, así que un cambio en cualquiera de ellos la invalida sola. Tiene tope de
entradas: al superarlo se borran las más antiguas.
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path

from anka.dominio.tramos import Hallazgo
from anka.infra import settings

MAX_ENTRADAS = 2000


def _carpeta() -> Path:
    return settings.DATA_DIR / "cache_hallazgos"


def clave(prompt: str, modelo: str, version: int) -> str:
    """Identificador estable de «este prompt, con este modelo y esta versión del esquema»."""
    return hashlib.sha256(f"{version}\0{modelo}\0{prompt}".encode()).hexdigest()


def leer(identificador: str) -> list[Hallazgo] | None:
    """Los hallazgos guardados para esa clave, o `None` si no hay (o el archivo está dañado)."""
    try:
        datos = json.loads((_carpeta() / f"{identificador}.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return datos if isinstance(datos, list) else None


def guardar(identificador: str, hallazgos: list[Hallazgo]) -> None:
    """Guarda los hallazgos de un tramo; un fallo de disco no debe impedir el análisis."""
    carpeta = _carpeta()
    try:
        carpeta.mkdir(parents=True, exist_ok=True)
        temporal = carpeta / f"{identificador}.tmp"
        temporal.write_text(json.dumps(hallazgos, ensure_ascii=False), encoding="utf-8")
        os.replace(temporal, carpeta / f"{identificador}.json")
        _podar(carpeta)
    except OSError:
        pass


def _podar(carpeta: Path) -> None:
    """Deja como mucho MAX_ENTRADAS archivos, conservando los más recientes."""
    archivos = sorted(carpeta.glob("*.json"), key=lambda ruta: ruta.stat().st_mtime)
    for sobrante in archivos[: max(0, len(archivos) - MAX_ENTRADAS)]:
        sobrante.unlink(missing_ok=True)
