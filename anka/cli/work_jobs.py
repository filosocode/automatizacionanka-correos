"""Trabajos Python → Work (T-49): crear, ver, exportar a un archivo local y, solo con confirmación, publicar en Drive.

Nada corre solo ni de forma periódica. `publicar` y `despachar` (T-53) son las únicas órdenes que tocan Drive y
exigen `--confirmar-drive`.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any

from anka.dominio.errores import ErrorDeAnka
from anka.servicios import trabajos_work


def build_parser() -> argparse.ArgumentParser:
    """Construye las órdenes: crear, ver, exportar y publicar."""
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="orden", required=True)
    crear = sub.add_parser("crear", help="crea el trabajo de un mensaje pendiente de la cola de recepción")
    crear.add_argument("--thread-id", required=True)
    crear.add_argument("--message-id", required=True)
    crear.add_argument("--forzar-elegibilidad", action="store_true",
                       help="una persona confirma que el mensaje sí es una solicitud (T-51, R3)")
    eleg = sub.add_parser("elegibilidad", help="muestra (solo lectura) qué mensajes pendientes admiten trabajo")
    eleg.add_argument("--thread-id", default="")
    eleg.add_argument("--message-id", default="")
    eleg.add_argument("--forzar", action="store_true", help="con ambos ids: confirma que es una solicitud")
    ver = sub.add_parser("ver", help="muestra el trabajo (sin datos del cuerpo del correo)")
    ver.add_argument("--job-id", required=True)
    exportar = sub.add_parser("exportar", help="escribe la hoja .xlsx en un archivo local; no usa Drive")
    exportar.add_argument("--job-id", required=True)
    exportar.add_argument("--salida", required=True, type=Path)
    publicar = sub.add_parser("publicar", help="publica la hoja del trabajo en Drive (requiere --confirmar-drive)")
    publicar.add_argument("--job-id", required=True)
    publicar.add_argument("--carpeta-id", required=True, help="carpeta de laboratorio de Drive")
    publicar.add_argument("--confirmar-drive", action="store_true", help="autoriza la escritura real en Drive")
    despachar = sub.add_parser("despachar", help="una pasada del despacho a Work para UN mensaje (T-53)")
    despachar.add_argument("--automatico", action="store_true",
                           help="lee work_dispatch_* de config.json; no hace nada si está desactivado")
    despachar.add_argument("--carpeta-id", default="", help="raíz del laboratorio (con PENDIENTES y CERRADOS)")
    despachar.add_argument("--thread-id", default="")
    despachar.add_argument("--message-id", default="")
    despachar.add_argument("--confirmar-drive", action="store_true", help="autoriza la escritura real en Drive")
    coordinar = sub.add_parser("coordinar", help="un ciclo de la coordinación automática Python-Work (T-55)")
    coordinar.add_argument("--una-vez", action="store_true", required=True, help="una sola pasada; no deja procesos")
    coordinar.add_argument("--thread-id", default="", help="laboratorio: limita el ciclo a este hilo")
    coordinar.add_argument("--message-id", default="", help="laboratorio: y a este mensaje")
    coordinar.add_argument("--confirmar", action="store_true", help="autoriza leer Gmail y escribir en Drive y SQLite")
    sub.add_parser("verificar-piloto", help="solo lectura: qué está habilitado y en marcha antes de activar T-55")
    return parser


def _verificar_piloto() -> dict[str, Any]:
    from anka.cli import work_coordinacion
    from anka.infra import settings

    return work_coordinacion.verificar_piloto(settings.load_config())


def _coordinar(args: argparse.Namespace) -> dict[str, Any]:
    from dataclasses import replace

    from anka.cli import work_coordinacion
    from anka.infra import settings

    if bool(args.thread_id) != bool(args.message_id):
        raise ErrorDeAnka("--thread-id y --message-id van juntos.")
    plan = work_coordinacion.plan_de(settings.load_config())
    if plan is None:
        return {"resultado": "desactivado"}
    if args.thread_id:
        plan = replace(plan, thread_id=args.thread_id.strip(), message_id=args.message_id.strip())
    if not args.confirmar:
        raise ErrorDeAnka("La coordinación lee Gmail y escribe en Drive: agrega --confirmar cuando esté autorizada.")
    return work_coordinacion.nuevo_coordinador().ciclo(plan)


def _despachar(args: argparse.Namespace) -> dict[str, Any]:
    from anka.infra import settings
    from anka.servicios import despacho

    if args.automatico:
        elegido = despacho.alcance_automatico(settings.load_config())
        if elegido is None:
            return {"resultado": "desactivado"}
        raiz, alcance = elegido
    else:
        raiz, alcance = args.carpeta_id, despacho.Alcance(args.thread_id, args.message_id)
    if not args.confirmar_drive:
        raise ErrorDeAnka("El despacho escribe en Drive: agrega --confirmar-drive cuando esté autorizado.")
    from anka.infra.google_services import services

    _gmail, drive = services()
    return despacho.despachar(drive, raiz, alcance)


def _publicar(args: argparse.Namespace) -> dict[str, Any]:
    if not args.confirmar_drive:
        raise ErrorDeAnka("Publicar escribe en Drive: agrega --confirmar-drive cuando esté autorizado.")
    from anka.infra.google_services import services     # solo aquí se toca Google

    _gmail, drive = services()
    return trabajos_work.publicar_trabajo(drive, args.job_id, args.carpeta_id)


def _elegibilidad(args: argparse.Namespace) -> dict[str, Any]:
    if args.forzar:
        if not (args.thread_id and args.message_id):
            raise ErrorDeAnka("Forzar la elegibilidad exige --thread-id y --message-id.")
        return trabajos_work.forzar_elegibilidad(args.thread_id, args.message_id)
    filas = trabajos_work.clasificar_pendientes()
    if args.thread_id or args.message_id:
        filas = [f for f in filas
                 if args.thread_id in ("", f["thread_id"]) and args.message_id in ("", f["message_id"])]
    return {"pendientes": filas}


def _publico(trabajo: dict[str, Any] | None) -> dict[str, Any]:
    if trabajo is None:
        raise ErrorDeAnka("No existe ese trabajo.")
    return {clave: valor for clave, valor in trabajo.items() if clave not in ("claim_token", "message_ref_json")}


def main(argv: Sequence[str] | None = None, salida: Callable[[str], object] = print) -> int:
    """Ejecuta una orden y escribe el resultado como JSON; 0 si salió bien, 2 si se rechazó."""
    args = build_parser().parse_args(argv)
    try:
        if args.orden == "crear":
            if args.forzar_elegibilidad:
                trabajos_work.forzar_elegibilidad(args.thread_id, args.message_id)
            resultado: dict[str, Any] = _publico(trabajos_work.crear_trabajo(args.thread_id, args.message_id))
        elif args.orden == "elegibilidad":
            resultado = _elegibilidad(args)
        elif args.orden == "despachar":
            resultado = _despachar(args)
        elif args.orden == "coordinar":
            resultado = _coordinar(args)
        elif args.orden == "verificar-piloto":
            resultado = _verificar_piloto()
        elif args.orden == "ver":
            resultado = _publico(trabajos_work.obtener(args.job_id))
        elif args.orden == "exportar":
            args.salida.write_bytes(trabajos_work.libro_del_trabajo(args.job_id))
            resultado = {"job_id": args.job_id, "archivo": str(args.salida)}
        else:
            resultado = _publicar(args)
    except ErrorDeAnka as exc:
        print(f"Rechazado: {exc}", file=sys.stderr)
        return 2
    salida(json.dumps(resultado, ensure_ascii=False, sort_keys=True, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
