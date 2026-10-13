"""Reinicio de los datos locales para volver a probar desde cero: vista previa, copia de seguridad y vaciado.

    python -m anka.infra.reinicio              muestra qué se borraría (no cambia nada)
    python -m anka.infra.reinicio --confirmar  hace la copia de seguridad y reinicia

Vacía la base del dashboard, el estado del puente y las carpetas derivadas. No toca Gmail, Drive, `config.json`, el
token ni las credenciales, ni `evidence/` ni `backups/` (T-42).
"""

from __future__ import annotations

import argparse
import shutil
import sqlite3
from contextlib import closing
from pathlib import Path

from anka.dominio.errores import ErrorDeAnka
from anka.infra import backup, settings
from anka.infra.logging_setup import configure_logging, get_logger
from anka.infra.work_heartbeat import other_bridge_running

log = get_logger(__name__)

ESTADO_DEL_PUENTE = "work_dashboard_sync_state.json"
CARPETAS_DERIVADAS = ("review_queue", "manifiestos", "cache_hallazgos")
ESPERA_DE_LA_BASE = 5      # segundos que se espera si otro proceso la está escribiendo


def _base() -> Path:
    return settings.DATA_DIR / "quotes.sqlite3"


def _tablas(db: sqlite3.Connection) -> list[str]:
    return [fila[0] for fila in db.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name")]


def _archivos(carpeta: Path) -> int:
    return sum(1 for ruta in carpeta.rglob("*") if ruta.is_file()) if carpeta.is_dir() else 0


def resumen() -> dict:
    """Qué se borraría: filas por tabla, archivos, casos con carpeta en Drive. No cambia nada."""
    filas: dict[str, int] = {}
    con_carpeta = 0
    if _base().exists():
        with closing(sqlite3.connect(f"file:{_base().as_posix()}?mode=ro", uri=True)) as db:
            for tabla in _tablas(db):
                filas[tabla] = db.execute(f"SELECT COUNT(*) FROM {tabla}").fetchone()[0]
            con_carpeta = db.execute(
                "SELECT COUNT(*) FROM quotes WHERE folder_id IS NOT NULL AND folder_id <> ''").fetchone()[0]
    return {
        "filas": filas, "casos_con_carpeta_en_drive": con_carpeta,
        "estado_del_puente": (settings.DATA_DIR / ESTADO_DEL_PUENTE).exists(),
        "archivos": {nombre: _archivos(settings.DATA_DIR / nombre) for nombre in CARPETAS_DERIVADAS},
    }


def reiniciar() -> dict:
    """Hace la copia de seguridad y vacía la base, el estado del puente y las carpetas derivadas.

    Si el puente está corriendo o la copia falla, no borra nada.
    """
    if other_bridge_running():
        raise ErrorDeAnka("El puente está corriendo: cierra el lanzador («Iniciar Anka») y vuelve a intentarlo.")
    antes = resumen()
    copia = backup.create_backup() if _base().exists() else None   # si falla, la excepción corta aquí: no se borra nada
    if _base().exists():
        with closing(sqlite3.connect(_base(), timeout=ESPERA_DE_LA_BASE)) as db:
            try:
                db.execute("BEGIN IMMEDIATE")
            except sqlite3.OperationalError as exc:
                raise ErrorDeAnka("La base está en uso: cierra el lanzador y vuelve a intentarlo.") from exc
            for tabla in _tablas(db):
                db.execute(f"DELETE FROM {tabla}")
            if "sqlite_sequence" in [fila[0] for fila in db.execute("SELECT name FROM sqlite_master")]:
                db.execute("DELETE FROM sqlite_sequence")
            db.commit()
    (settings.DATA_DIR / ESTADO_DEL_PUENTE).unlink(missing_ok=True)
    for nombre in CARPETAS_DERIVADAS:
        carpeta = settings.DATA_DIR / nombre
        if carpeta.is_dir() and carpeta.resolve().is_relative_to(settings.DATA_DIR.resolve()):
            shutil.rmtree(carpeta, ignore_errors=True)
    log.warning("Datos locales reiniciados (copia: %s)", copia.name if copia else "no había base")
    return {"copia": str(copia) if copia else "", "antes": antes}


def _imprimir(datos: dict) -> None:
    print("Filas por tabla:")
    for tabla, cantidad in datos["filas"].items():
        print(f"  {tabla:26} {cantidad}")
    print(f"Estado del puente: {'existe' if datos['estado_del_puente'] else 'no existe'}")
    for nombre, cantidad in datos["archivos"].items():
        print(f"Archivos en {nombre}: {cantidad}")
    if datos["casos_con_carpeta_en_drive"]:
        print(f"\nOJO: {datos['casos_con_carpeta_en_drive']} caso(s) tienen carpeta en Drive. Esto NO las toca; "
              "para moverlas a la papelera elimina esos casos desde el dashboard antes de reiniciar.")


def main() -> None:
    """Comando de reinicio: sin opciones muestra qué se borraría; con `--confirmar` copia y reinicia."""
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--confirmar", action="store_true", help="hace la copia de seguridad y reinicia")
    args = parser.parse_args()
    configure_logging()
    if not args.confirmar:
        _imprimir(resumen())
        print("\nNo se cambió nada. Cierra «Iniciar Anka» y repite con --confirmar para reiniciar "
              "(antes se hace una copia).")
        return
    try:
        resultado = reiniciar()
    except ErrorDeAnka as exc:
        raise SystemExit(str(exc)) from exc
    _imprimir(resultado["antes"])
    print(f"\nListo. Copia de seguridad: {resultado['copia'] or '(no había base)'}")
    print("Para volver atrás: python -m anka.infra.backup --restore <esa copia>")
    print("Gmail y Drive no se tocaron: los correos de prueba, los borradores de Work y las carpetas siguen ahí.")


if __name__ == "__main__":
    main()
