"""Sondeo aislado de recepción Gmail; no arranca el supervisor comercial."""

from __future__ import annotations

import argparse
import json
import re
import sqlite3
import time
from collections.abc import Sequence

from anka.dominio.configuracion import Configuracion
from anka.dominio.errores import ErrorDeAnka
from anka.infra import gmail_reception_auth, settings
from anka.infra.instancia_unica import InstanciaUnica
from anka.infra.logging_setup import configure_logging, get_logger
from anka.servicios.recepcion import CUENTA, sondear

log = get_logger(__name__)
LOCK_FILENAME = "gmail_reception.lock"
_SQLITE_IDENTIFIER = re.compile(r"[A-Za-z_][A-Za-z0-9_.]*")


def _sqlite_error_summary(exc: sqlite3.Error) -> str:
    """Permite solo diagnósticos SQLite estructurales; omite texto arbitrario o valores de datos."""
    summary = " ".join(str(exc).split())
    for prefix in ("no such table: ", "no such column: ", "duplicate column name: "):
        if summary.startswith(prefix) and _SQLITE_IDENTIFIER.fullmatch(summary[len(prefix):]):
            return summary[:200]
    safe_messages = {
        "database is locked", "database table is locked", "database schema is locked",
        "unable to open database file", "disk I/O error", "attempt to write a readonly database",
        "foreign key constraint failed",
    }
    return summary if summary in safe_messages else "detalle omitido por seguridad"


def build_parser() -> argparse.ArgumentParser:
    """Construye opciones del ejecutor aislado."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--authorize", action="store_true", help="solicita consentimiento Gmail readonly por navegador")
    parser.add_argument("--once", action="store_true", help="realiza un único sondeo; contacta Gmail")
    parser.add_argument("--interval", type=int, default=60, help="segundos entre sondeos (30–3600; predeterminado: 60)")
    return parser


def run(interval: int, once: bool) -> None:
    """Sondea Gmail y solo registra resultados locales hasta Ctrl+C o `--once`; una sola instancia por equipo."""
    with InstanciaUnica(settings.DATA_DIR / LOCK_FILENAME, "receptor Gmail aislado") as instancia:
        if instancia.recuperada_de:
            log.warning("Se recuperó el bloqueo de una instancia anterior que ya no existe (PID %s)",
                        instancia.recuperada_de.get("pid"))
        gmail = gmail_reception_auth.gmail_service()
        config: Configuracion = {"own_addresses": [CUENTA]}
        while True:
            result = sondear(gmail, config)
            print(json.dumps(result, ensure_ascii=True, sort_keys=True), flush=True)
            if once:
                return
            time.sleep(interval)


def main(argv: Sequence[str] | None = None) -> int:
    """Punto de entrada del receptor sin iniciar el supervisor."""
    configure_logging(console=True)
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.authorize and args.once:
        parser.error("--authorize y --once son operaciones separadas")
    if args.authorize and args.interval != 60:
        parser.error("--interval no aplica junto con --authorize")
    if not args.authorize and not 30 <= args.interval <= 3600:
        parser.error("--interval debe estar entre 30 y 3600 segundos")
    try:
        if args.authorize:
            path = gmail_reception_auth.authorize_interactive()
            print(f"Autorización Gmail readonly guardada en {path}")
        else:
            run(args.interval, args.once)
    except ErrorDeAnka as exc:
        parser.exit(1, f"{exc}\n")
    except KeyboardInterrupt:
        print("Receptor detenido.", flush=True)
    except sqlite3.Error as exc:
        log.error("Receptor aislado detenido (SQLite %s: %s)", type(exc).__name__, _sqlite_error_summary(exc))
        return 1
    except Exception as exc:
        log.error("Receptor aislado detenido (%s)", type(exc).__name__)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
