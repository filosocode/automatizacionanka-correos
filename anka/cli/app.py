"""Línea de comandos de Anka: `python app.py <comando>`.

Comandos: auth, drive-tree, mode / set-mode, run (revisar Gmail), reviews / approve, review-ui (dashboard),
analyze-text (probar el modelo) y dashboard-upsert (importar un borrador de Work). Cada comando es una función
`_cmd_*` registrada en `COMMANDS`; el trabajo real vive en `anka.servicios` y `anka.infra`.
"""

from __future__ import annotations

import argparse
import sys
import json
from pathlib import Path

from anka.dominio.errores import ErrorDeAnka
from anka.infra.google_services import authorize_interactive, services, list_folders
from anka.dominio.reception import (
    reception_questions,
)
from anka.infra.llm import analyze_text
from anka.infra.modes import (
    print_mode, set_mode,
)
from anka.infra.logging_setup import configure_logging
from anka.infra.settings import ACTIVE_MODES, load_config, unknown_config_keys
from anka.servicios.cola_del_piloto import pending_reviews
from anka.servicios.work_import import dashboard_upsert
from anka.servicios.work_attachment_packet import prepare_work_attachment_packet
from anka.servicios.case_actions import approve_review
from anka.servicios.estado_de_revision import watch
from anka.servicios.pipeline import run
from anka.web.server import review_ui













































































































































































































































def warn_unknown_config_keys() -> None:
    """Avisa de claves de config.json que el programa no usa (suelen ser errores de tipeo)."""
    try:
        unknown = unknown_config_keys(load_config())
    except ErrorDeAnka:
        return  # el comando informará del problema de configuración por su cuenta
    if unknown:
        print(
            "Aviso: config.json tiene claves que el programa no usa: " + ", ".join(unknown),
            file=sys.stderr,
        )


COMMANDS_ORDER = (
    "auth", "drive-tree", "mode", "run", "reviews", "approve", "review-ui",
    "set-mode", "analyze-text", "dashboard-upsert", "prepare-work-packet", "work-drive-sync",
)


def _add_run_options(parser: argparse.ArgumentParser) -> None:
    """Opciones de la revisión de Gmail (`run`)."""
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


def _add_text_options(parser: argparse.ArgumentParser) -> None:
    """Opciones para probar el analizador con un texto (`analyze-text`)."""
    text_source = parser.add_mutually_exclusive_group()
    text_source.add_argument("--text", help="Texto de correo para probar el analizador local/API.")
    text_source.add_argument("--file", help="Archivo UTF-8 con el texto del correo de prueba.")
    parser.add_argument(
        "--required-field", action="append", default=[],
        help="Dato que se desea verificar; puede repetirse.",
    )


def _add_case_options(parser: argparse.ArgumentParser) -> None:
    """Opciones que actúan sobre un caso (`approve`, `dashboard-upsert`)."""
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


def _add_setup_options(parser: argparse.ArgumentParser) -> None:
    """Opciones del servidor local, la autorización y el modo de operación."""
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


def _add_packet_options(parser: argparse.ArgumentParser) -> None:
    """Opciones para preparar textos de adjuntos y evidencia que se adjuntan a Work."""
    parser.add_argument(
        "--attachment", action="append", default=[],
        help="Ruta local de un adjunto descargado; repite la opción para varios archivos.",
    )
    parser.add_argument(
        "--output-dir",
        help="Carpeta nueva donde guardar manifiesto y textos segmentados.",
    )


def build_parser() -> argparse.ArgumentParser:
    """Los comandos y opciones de la línea de comandos."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=list(COMMANDS_ORDER))
    for add_options in (
        _add_run_options, _add_text_options, _add_case_options, _add_setup_options, _add_packet_options,
    ):
        add_options(parser)
    return parser


def _cmd_dashboard_upsert(args: argparse.Namespace, parser: argparse.ArgumentParser) -> None:
    if not args.payload_base64:
        parser.error("dashboard-upsert requiere --payload-base64.")
    dashboard_upsert(args.payload_base64)
    print("Registro del dashboard actualizado.")


def _cmd_prepare_work_packet(args: argparse.Namespace, parser: argparse.ArgumentParser) -> None:
    if not args.attachment:
        parser.error("prepare-work-packet requiere al menos un --attachment.")
    if not args.output_dir:
        parser.error("prepare-work-packet requiere --output-dir.")
    result = prepare_work_attachment_packet(args.attachment, args.output_dir)
    print(json.dumps(result, ensure_ascii=False, indent=2))


def _cmd_work_drive_sync(args: argparse.Namespace, parser: argparse.ArgumentParser) -> None:
    from anka.cli.work_drive import run as prepare

    result = prepare(args.watch_seconds, args.thread_id)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    if result.get("failures"):
        raise ErrorDeAnka("Preparación incompleta; consulte work_drive_status.json y el registro.")


def _cmd_analyze_text(args: argparse.Namespace, parser: argparse.ArgumentParser) -> None:
    if not args.text and not args.file:
        parser.error("analyze-text requiere --text o --file.")
    config = load_config()
    text = args.text if args.text is not None else Path(args.file).read_text(encoding="utf-8")
    required = args.required_field or config["required_fields"]
    result = analyze_text(text, required, config["llm"])
    print(json.dumps({
        "provider": config["llm"]["provider"],
        "model": config["llm"].get("model"),
        "analysis": result,
        "questions": reception_questions(result, required),
    }, ensure_ascii=False, indent=2))


def _cmd_auth(args: argparse.Namespace, parser: argparse.ArgumentParser) -> None:
    authorize_interactive()   # `--force-auth` se conserva por compatibilidad: `auth` siempre pide una autorización nueva
    print("Autorización lista.")


def _cmd_drive_tree(args: argparse.Namespace, parser: argparse.ArgumentParser) -> None:
    _, drive = services()
    for folder in list_folders(drive):
        print(f"{folder['name']} [{folder['id']}]")
        for child in list_folders(drive, folder["id"]):
            print(f"  {child['name']} [{child['id']}]")


def _cmd_mode(args: argparse.Namespace, parser: argparse.ArgumentParser) -> None:
    print_mode()


def _cmd_set_mode(args: argparse.Namespace, parser: argparse.ArgumentParser) -> None:
    if not args.active_mode:
        parser.error("set-mode requiere --active-mode.")
    print(set_mode(args.active_mode))


def list_reviews() -> None:
    """Imprime las revisiones pendientes del piloto local (comando `reviews`)."""
    reviews = pending_reviews()
    if not reviews:
        print("No hay revisiones humanas pendientes.")
        return
    for review in reviews:
        packet = review["packet"]
        suffix = f" -> {packet}" if packet.exists() else " -> sin archivo en review_queue"
        print(f"{review['thread_id']} [{review['status']}] {review['recipient']}{suffix}")


def _cmd_reviews(args: argparse.Namespace, parser: argparse.ArgumentParser) -> None:
    list_reviews()


def _cmd_approve(args: argparse.Namespace, parser: argparse.ArgumentParser) -> None:
    if not args.thread_id:
        parser.error("approve requiere --thread-id.")
    approve_review(
        args.thread_id, args.body_file,
        client_name=args.client_name, new_client=args.new_client,
    )


def _cmd_review_ui(args: argparse.Namespace, parser: argparse.ArgumentParser) -> None:
    review_ui(args.host, args.port, not args.no_browser)


def _cmd_run(args: argparse.Namespace, parser: argparse.ArgumentParser) -> None:
    if args.watch_seconds:
        watch(args.apply, args.review_only, args.watch_seconds, args.gmail_drafts)
    else:
        run(args.apply, args.review_only, args.gmail_drafts)


COMMANDS = {
    "auth": _cmd_auth,
    "drive-tree": _cmd_drive_tree,
    "mode": _cmd_mode,
    "run": _cmd_run,
    "reviews": _cmd_reviews,
    "approve": _cmd_approve,
    "review-ui": _cmd_review_ui,
    "set-mode": _cmd_set_mode,
    "analyze-text": _cmd_analyze_text,
    "dashboard-upsert": _cmd_dashboard_upsert,
    "prepare-work-packet": _cmd_prepare_work_packet,
    "work-drive-sync": _cmd_work_drive_sync,
}


def main() -> None:
    """Punto de entrada de la línea de comandos: lee el comando y ejecuta su función de `COMMANDS`."""
    parser = build_parser()
    args = parser.parse_args()
    configure_logging()
    if args.command != "auth":
        warn_unknown_config_keys()
    try:
        COMMANDS[args.command](args, parser)
    except ErrorDeAnka as exc:
        raise SystemExit(str(exc)) from exc   # a la terminal: el mensaje y código de salida 1


if __name__ == "__main__":
    main()
