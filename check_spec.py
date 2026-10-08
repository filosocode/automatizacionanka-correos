"""Trazabilidad requisito → prueba.

Comprueba que:
  1. todo requisito (spec/requisitos.md y las reglas R1…Rn) tenga al menos una prueba que lo cubra;
  2. toda etiqueta `Cubre:` de los test_*.py apunte a un requisito que existe;
  3. existan los documentos de la especificación y sus fuentes de verdad.

Las pruebas se etiquetan con un comentario o docstring `Cubre: F1-03, R12`. Uso:

    python check_spec.py              verifica (código de salida 1 si falla)
    python check_spec.py --resumen    además imprime cuántas pruebas cubren cada requisito
"""

from __future__ import annotations

import re
import ast
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
SPEC = ROOT / "spec"
RULES = ROOT / "skills" / "anka-cotizaciones-correo" / "references" / "reglas_operativas.md"
REQUIRED_FILES = (
    "spec/README.md", "spec/00-alcance.md", "spec/01-flujo-y-estados.md", "spec/02-reglas.md",
    "spec/03-contrato-work.md", "spec/04-ux.md", "spec/05-decisiones.md", "spec/06-arquitectura.md", "spec/07-api.md", "spec/requisitos.md",
    "spec/transiciones.json", "spec/contrato_work.schema.json",
)
HEADING = re.compile("^### ([A-Z0-9]+-\\d+) " + chr(183) + " ", re.MULTILINE)
RULE_ROW = re.compile(r"^\| (R\d+) \|", re.MULTILINE)
TAG = re.compile(r"Cubre:\s*([A-Za-z0-9\-,\s]+)")


def requirement_ids(root: Path = ROOT) -> set[str]:
    ids: set[str] = set()
    requisitos = root / "spec" / "requisitos.md"
    if requisitos.exists():
        ids |= set(HEADING.findall(requisitos.read_text(encoding="utf-8")))
    rules = root / RULES.relative_to(ROOT)
    if rules.exists():
        ids |= set(RULE_ROW.findall(rules.read_text(encoding="utf-8")))
    return ids


def coverage(root: Path = ROOT) -> dict[str, list[str]]:
    """Id → métodos ejecutables asociados a etiquetas; no demuestra suficiencia semántica."""
    found: dict[str, list[str]] = {}
    for path in sorted((root / "tests").glob("test_*.py")):
        if path.name == "test_spec.py":
            continue                      # sus ejemplos de etiquetas son datos de prueba, no cobertura
        source = path.read_text(encoding="utf-8")
        tree = ast.parse(source)
        for match in TAG.finditer(source):
            tests = tests_for_tag(source, tree, match.start())
            for item in re.split(r"[,\s]+", match.group(1).strip()):
                if re.fullmatch(r"[A-Z]+\d*(?:-\d+)?", item):
                    found.setdefault(item, [])
                    for test in tests:
                        identity = f'{path.name}:{test.lineno}:{test.name}'
                        if identity not in found[item]:
                            found[item].append(identity)
    return found


def tests_for_tag(source: str, tree: ast.Module, offset: int) -> list[ast.FunctionDef]:
    """Asocia la etiqueta a su método/clase, o al módulo cuando está en la cabecera."""
    line = source.count('\n', 0, offset) + 1
    scopes = [node for node in ast.walk(tree) if isinstance(node, (ast.ClassDef, ast.FunctionDef))]
    enclosing = [node for node in scopes if node.lineno <= line <= (node.end_lineno or node.lineno)]
    scope: ast.AST = min(enclosing, key=lambda node: (node.end_lineno or 0) - node.lineno) if enclosing else tree
    if not enclosing:
        following = sorted((node for node in scopes if node.lineno > line), key=lambda node: node.lineno)
        if following:
            between = source.splitlines()[line:following[0].lineno-1]
            if all(not text.strip() or text.lstrip().startswith(('#', '@')) for text in between):
                scope = following[0]
    return [node for node in ast.walk(scope) if isinstance(node, ast.FunctionDef)
            and node.name.startswith('test_') and any(
                isinstance(child, (ast.Assert, ast.Call)) for child in ast.walk(node))]


def check(root: Path = ROOT) -> list[str]:
    """Lista de problemas (vacía si la especificación está completa y trazada)."""
    problems = [f"Falta {name}" for name in REQUIRED_FILES if not (root / name).exists()]
    required = requirement_ids(root)
    covered = coverage(root)
    for missing in sorted(required - {key for key, tests in covered.items() if tests}, key=_order):
        problems.append(f"El requisito {missing} no tiene ninguna prueba con «Cubre: {missing}».")
    for unknown in sorted(set(covered) - required, key=_order):
        problems.append(f"La etiqueta «Cubre: {unknown}» (en {', '.join(covered[unknown])}) no existe en la especificación.")
    return problems


def _order(identifier: str) -> tuple[str, int]:
    match = re.fullmatch(r"([A-Z0-9]*?[A-Z])(?:-)?(\d+)", identifier)
    return (match.group(1), int(match.group(2))) if match else (identifier, 0)


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    problems = check()
    if "--resumen" in argv:
        covered = coverage()
        for identifier in sorted(requirement_ids(), key=_order):
            print(f"{identifier:7} {len(covered.get(identifier, [])):2} método(s) asociado(s)")
    for problem in problems:
        print("ERROR:", problem)
    if not problems:
        print(f"Especificación trazada: {len(requirement_ids())} requisitos, todos con prueba.")
    return 1 if problems else 0


if __name__ == "__main__":
    raise SystemExit(main())
