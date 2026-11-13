"""Corre las pruebas por módulo, midiendo cada uno; con `--rapido` salta los lentos (el modo rápido de `check.bat`).

    python tools/pruebas_rapidas.py                 todas, con el tiempo de cada módulo
    python tools/pruebas_rapidas.py --rapido        sin los módulos lentos (LENTOS), para revisar mientras se trabaja
    python tools/pruebas_rapidas.py --medir         además lista los 10 módulos más lentos (para ajustar LENTOS)

Lo que decide si algo se puede dar por hecho sigue siendo `check.bat` sin argumentos (todo, con cobertura).
"""

from __future__ import annotations

import argparse
import sys
import time
import unittest
from collections import defaultdict
from collections.abc import Iterator
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[1]

# Módulos de `tests/` que tardan más de unos segundos (mídelos con `--medir` y ajusta esta lista cuando cambien).
LENTOS = frozenset({
    "tests.test_base_concurrente", "tests.test_rendimiento", "tests.test_html_render", "tests.test_lectores_golden",
    "tests.test_lector_de_adjuntos", "tests.test_lectura_por_tramos", "tests.test_ollama_real",
})


def casos(suite: unittest.TestSuite) -> Iterator[unittest.TestCase]:
    """Recorre una suite devolviendo sus pruebas una a una."""
    for item in suite:
        if isinstance(item, unittest.TestSuite):
            yield from casos(item)
        else:
            yield item


def por_modulo(suite: unittest.TestSuite) -> dict[str, list[unittest.TestCase]]:
    """Las pruebas agrupadas por el módulo que las define."""
    grupos: dict[str, list[unittest.TestCase]] = defaultdict(list)
    for caso in casos(suite):
        grupos[type(caso).__module__].append(caso)
    return dict(sorted(grupos.items()))


def main(argv: list[str] | None = None) -> int:
    """Descubre las pruebas, corre las que toquen y devuelve 0 si todas pasan."""
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--rapido", action="store_true", help="salta los módulos de LENTOS")
    parser.add_argument("--medir", action="store_true", help="lista los 10 módulos más lentos")
    args = parser.parse_args(argv)
    sys.path.insert(0, str(RAIZ))
    suite = unittest.defaultTestLoader.discover(str(RAIZ / "tests"), top_level_dir=str(RAIZ))
    grupos = por_modulo(suite)
    tiempos: dict[str, float] = {}
    total = unittest.TestResult()
    saltados = []
    for modulo, pruebas in grupos.items():
        if args.rapido and modulo in LENTOS:
            saltados.append(modulo)
            continue
        inicio = time.perf_counter()
        unittest.TestSuite(pruebas).run(total)       # una suite por módulo: así corren sus setUpClass y tearDownClass
        tiempos[modulo] = time.perf_counter() - inicio
    for prueba, traza in [*total.failures, *total.errors]:
        print("=" * 70, f"\nFALLA: {prueba}\n{traza}")
    if args.medir or not args.rapido:
        for modulo, segundos in sorted(tiempos.items(), key=lambda par: -par[1])[:10]:
            print(f"{segundos:7.1f} s  {modulo}")
    corridas = total.testsRun
    print(f"\n{corridas} pruebas, {len(total.failures)} fallas, {len(total.errors)} errores, "
          f"{len(total.skipped)} saltadas" + (f"; sin correr los lentos: {len(saltados)} módulos" if saltados else ""))
    return 0 if total.wasSuccessful() else 1


if __name__ == "__main__":
    raise SystemExit(main())
