"""Procesa solicitudes de cotización de Gmail y crea carpetas en Drive."""

from __future__ import annotations

import argparse
import sys
import json
from pathlib import Path

from anka.infra.google_services import credentials, services, list_folders
from anka.dominio.reception import (
    reception_questions,
)
from anka.infra.llm import analyze_text
from anka.infra.modes import (
    print_mode, set_mode,
)
from anka.infra.logging_setup import configure_logging
from anka.infra.settings import ACTIVE_MODES, load_config, unknown_config_keys
from anka.servicios.quotes import dashboard_upsert, list_reviews
from anka.servicios.case_actions import approve_review
from anka.servicios.pipeline import run, watch
from anka.web.server import review_ui













































































































































































































































def warn_unknown_config_keys() -> None:
    """Avisa de claves de config.json que el programa no usa (suelen ser errores de tipeo)."""
    try:
        unknown = unknown_config_keys(load_config())
    except SystemExit:
        return  # el comando informará del problema de configuración por su cuenta
    if unknown:
        print(
            "Aviso: config.json tiene claves que el programa no usa: " + ", ".join(unknown),
            file=sys.stderr,
        )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "command",
        choices=[
            "auth", "drive-tree", "mode", "run", "reviews", "approve", "review-ui",
            "set-mode", "analyze-text", "dashboard-upsert",
        ],
    )
    parser.add_argument("--apply", action="store_true")
    parser.add_argument(
        "--review-only", action="store_true",
        help="Procesa y guarda borradores para revisión humana, sin enviar ni crear carpetas.",
    )
    parser.add_argument(
        "--gmail-drafts", action="store_true",
        help="Crea borradores en Gmail con nota de revision; no envia correos.",
    )
    parser.add_argument(
        "--watch-seconds", type=int, default=0,
        help="Revisa Gmail en bucle cada N segundos. Usa 0 para una sola ejecución.",
    )
    text_source = parser.add_mutually_exclusive_group()
    text_source.add_argument("--text", help="Texto de correo para probar el analizador local/API.")
    text_source.add_argument("--file", help="Archivo UTF-8 con el texto del correo de prueba.")
    parser.add_argument(
        "--required-field", action="append", default=[],
        help="Dato que se desea verificar; puede repetirse.",
    )
    parser.add_argument("--thread-id", help="ID del hilo a aprobar.")
    parser.add_argument(
        "--payload-base64", help="Registro JSON codificado para actualizar el dashboard."
    )
    parser.add_argument(
        "--body-file",
        help="Archivo UTF-8 con el correo revisado que se enviará al aprobar una pregunta.",
    )
    parser.add_argument(
        "--client-name",
        help="Carpeta del cliente en Drive al aprobar un alcance completo.",
    )
    parser.add_argument(
        "--new-client", action="store_true",
        help="Confirma que puede crearse la carpeta del cliente si no existe.",
    )
    parser.add_argument("--host", default="127.0.0.1", help=argparse.SUPPRESS)
    parser.add_argument("--port", type=int, default=8765, help=argparse.SUPPRESS)
    parser.add_argument("--no-browser", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument(
        "--force-auth", action="store_true",
        help="Fuerza una nueva autorizacion de Google para actualizar permisos.",
    )
    parser.add_argument(
        "--active-mode",
        choices=sorted(ACTIVE_MODES),
        help="Modo a activar con set-mode.",
    )
    args = parser.parse_args()
    configure_logging()
    if args.command != "auth":
        warn_unknown_config_keys()
    if args.command == "dashboard-upsert":
        if not args.payload_base64:
            parser.error("dashboard-upsert requiere --payload-base64.")
        dashboard_upsert(args.payload_base64)
        print("Registro del dashboard actualizado.")
    elif args.command == "analyze-text":
        if not args.text and not args.file:
            parser.error("analyze-text requiere --text o --file.")
        config = load_config()
        text = args.text if args.text is not None else Path(args.file).read_text(encoding="utf-8")
        result = analyze_text(
            text, args.required_field or config["required_fields"], config["llm"]
        )
        print(json.dumps({
            "provider": config["llm"]["provider"],
            "model": config["llm"].get("model"),
            "analysis": result,
            "questions": reception_questions(
                result, args.required_field or config["required_fields"]
            ),
        }, ensure_ascii=False, indent=2))
    elif args.command == "auth":
        credentials(args.force_auth)
        print("Autorización lista.")
    elif args.command == "drive-tree":
        _, drive = services()
        for folder in list_folders(drive):
            print(f"{folder['name']} [{folder['id']}]")
            for child in list_folders(drive, folder["id"]):
                print(f"  {child['name']} [{child['id']}]")
    elif args.command == "mode":
        print_mode()
    elif args.command == "set-mode":
        if not args.active_mode:
            parser.error("set-mode requiere --active-mode.")
        print(set_mode(args.active_mode))
    elif args.command == "reviews":
        list_reviews()
    elif args.command == "approve":
        if not args.thread_id:
            parser.error("approve requiere --thread-id.")
        approve_review(
            args.thread_id, args.body_file,
            client_name=args.client_name, new_client=args.new_client,
        )
    elif args.command == "review-ui":
        review_ui(args.host, args.port, not args.no_browser)
    else:
        if args.watch_seconds:
            watch(args.apply, args.review_only, args.watch_seconds, args.gmail_drafts)
        else:
            run(args.apply, args.review_only, args.gmail_drafts)


if __name__ == "__main__":
    main()
