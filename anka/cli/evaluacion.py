"""Evaluación del análisis con solicitudes reales del histórico: `python -m anka.cli.evaluacion <comando>`."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from anka.infra.settings import load_config
from anka.servicios import evaluacion

RAIZ_DEL_HISTORICO = Path("I:/T&E")


def _preparar(args: argparse.Namespace) -> None:
    config = load_config()
    candidatas = evaluacion.candidatas(args.raiz)
    if args.servicios:
        candidatas = [c for c in candidatas if c["servicio"] in args.servicios]
    muestra = evaluacion.muestrear(candidatas, args.n, args.semilla)
    existentes = {f["id"] for f in evaluacion.todas_las_fichas()}
    siguiente = 1
    for candidata in muestra:
        while f"c{siguiente:02d}" in existentes:
            siguiente += 1
        identificador = f"c{siguiente:02d}"
        existentes.add(identificador)
        ficha = evaluacion.preparar_ficha(candidata, identificador, config["llm"])
        evaluacion.guardar_ficha(ficha)
        esperado = ficha["esperado"]
        print(f"{identificador} [{ficha['servicio']}] {len(esperado['actividades'])} actividad(es), "
              f"ubicación {esperado['ubicacion']}, faltante {esperado['faltante']}", flush=True)


def _mostrar(args: argparse.Namespace) -> None:
    print(json.dumps(evaluacion.leer_ficha(args.id), ensure_ascii=False, indent=1))


def _validar(args: argparse.Namespace) -> None:
    for identificador in args.id:
        evaluacion.validar(identificador)
        print(f"{identificador}: validada")


def _evaluar(args: argparse.Namespace) -> None:
    config = load_config()
    informe = evaluacion.evaluar(
        lambda ficha: evaluacion.analizar_ficha(ficha, config),
        modelo=str(config["llm"].get("model", "")), version_del_prompt=args.prompt)
    print(json.dumps(informe["resumen"], ensure_ascii=False, indent=1))
    print("Informe:", informe["archivo"])
    for fallido in informe["fallidos"]:
        print("Falló", fallido["id"], fallido["error"])


def main() -> None:
    """Punto de entrada de la línea de comandos."""
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="comando", required=True)
    preparar = sub.add_parser("preparar", help="muestrea el histórico y deja fichas en borrador")
    preparar.add_argument("--n", type=int, default=8)
    preparar.add_argument("--semilla", type=int, default=1)
    preparar.add_argument("--raiz", type=Path, default=RAIZ_DEL_HISTORICO)
    preparar.add_argument("--servicios", nargs="*", default=[], help="solo estos servicios (lidar, topografia…)")
    preparar.set_defaults(accion=_preparar)
    mostrar = sub.add_parser("mostrar", help="muestra una ficha")
    mostrar.add_argument("id")
    mostrar.set_defaults(accion=_mostrar)
    validar = sub.add_parser("validar", help="da por buenas una o varias fichas")
    validar.add_argument("id", nargs="+")
    validar.set_defaults(accion=_validar)
    evaluar = sub.add_parser("evaluar", help="corre la cadena real sobre las fichas validadas")
    evaluar.add_argument("--prompt", default="", help="versión del prompt que se prueba (se anota en el informe)")
    evaluar.set_defaults(accion=_evaluar)
    args = parser.parse_args()
    args.accion(args)


if __name__ == "__main__":
    main()
