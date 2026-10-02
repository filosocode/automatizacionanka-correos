"""Arquitectura en capas (spec/06-arquitectura.md): las dependencias van en una sola dirección y no hay ciclos.

Estas pruebas leen el código con `ast`; no ejecutan nada. Si una falla, el cambio mete una dependencia que el
diseño no permite: se corrige moviendo el código a la capa correcta, no relajando la regla.

Cubre: T-12
"""

from tests import _isolate  # noqa: F401  (debe ir primero)

import ast
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PACKAGE = ROOT / "anka"
# Una capa solo puede importar de capas del mismo nivel o inferiores.
LEVEL = {"dominio": 0, "infra": 1, "servicios": 2, "web": 3, "cli": 4}
# Excepciones a la regla «web y cli no se importan entre sí».
ENTRY_POINTS = {"app.py", "supervisor.py", "work_dashboard_sync.py", "check_spec.py"}


def modules():
    for path in sorted(PACKAGE.rglob("*.py")):
        if "__pycache__" in path.parts or path.name == "__init__.py":
            continue
        relative = path.relative_to(ROOT).with_suffix("")
        yield ".".join(relative.parts), path


def imports_of(path):
    tree = ast.parse(path.read_text(encoding="utf-8"))
    found = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module and node.module.startswith("anka"):
            found.append((node.module, [a.name for a in node.names]))
        elif isinstance(node, ast.Import):
            found.extend((a.name, []) for a in node.names if a.name.startswith("anka"))
    return found


def top_level_imports(path):
    """Importaciones de módulo (las que forman ciclos al cargar), sin las hechas dentro de funciones."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    found = []
    for node in tree.body:
        if isinstance(node, ast.ImportFrom) and node.module and node.module.startswith("anka"):
            for alias in node.names:
                found.append(f"{node.module}.{alias.name}")
            found.append(node.module)
    return found


def layer_of(dotted):
    parts = dotted.split(".")
    return parts[1] if len(parts) > 2 and parts[1] in LEVEL else None


# Cubre: T-12
class LayerRules(unittest.TestCase):
    def test_dependencies_only_point_down_the_layers(self):
        violations = []
        for dotted, path in modules():
            layer = layer_of(dotted)
            for target, _ in imports_of(path):
                other = layer_of(target if target.count(".") >= 2 else target + ".x")
                if other and LEVEL[other] > LEVEL[layer]:
                    violations.append(f"{dotted} ({layer}) importa {target} ({other})")
        self.assertEqual(violations, [], "Una capa no puede depender de una de arriba")

    def test_the_web_and_the_commands_do_not_depend_on_each_other(self):
        for dotted, path in modules():
            layer = layer_of(dotted)
            for target, _ in imports_of(path):
                if layer == "web":
                    self.assertFalse(target.startswith("anka.cli"), f"{dotted} importa {target}")
                if layer in ("dominio", "infra", "servicios"):
                    self.assertFalse(target.startswith(("anka.web", "anka.cli")), f"{dotted} importa {target}")

    def test_there_are_no_import_cycles_between_modules(self):
        graph = {}
        for dotted, path in modules():
            graph[dotted] = {t for t in top_level_imports(path) if t in {d for d, _ in modules()}} - {dotted}
        index, low, stack, on, cycles = {}, {}, [], set(), []
        counter = [0]

        def strong(node):
            index[node] = low[node] = counter[0]
            counter[0] += 1
            stack.append(node)
            on.add(node)
            for other in graph.get(node, ()):
                if other not in index:
                    strong(other)
                    low[node] = min(low[node], low[other])
                elif other in on:
                    low[node] = min(low[node], index[other])
            if low[node] == index[node]:
                component = []
                while True:
                    member = stack.pop()
                    on.discard(member)
                    component.append(member)
                    if member == node:
                        break
                if len(component) > 1:
                    cycles.append(sorted(component))

        for node in list(graph):
            if node not in index:
                strong(node)
        self.assertEqual(cycles, [], "Ciclos de importación entre módulos")

    def test_no_module_imports_a_private_name_from_another_module(self):
        offenders = []
        for dotted, path in modules():
            for target, names in imports_of(path):
                private = [n for n in names if n.startswith("_") and not n.startswith("__")]
                if private:
                    offenders.append(f"{dotted} importa {private} de {target}")
        self.assertEqual(offenders, [], "Lo privado de un módulo no se usa desde fuera: hazlo público o muévelo")

    def test_only_the_command_line_ends_the_program(self):
        """Un servicio explica el problema con `ErrorDeAnka`; terminar el programa es cosa de `anka/cli` y de los `main`."""
        offenders = []
        for dotted, path in modules():
            if layer_of(dotted) == "cli":
                continue
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for function in [n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and n.name != "main"]:
                for node in ast.walk(function):
                    if isinstance(node, ast.Raise) and "SystemExit" in ast.dump(node):
                        offenders.append(f"{dotted}.{function.name}")
        self.assertEqual(offenders, [], "Lanza ErrorDeAnka (anka.dominio.errores) en vez de SystemExit")

    def test_a_broad_except_never_swallows_the_error_silently(self):
        """Un `except Exception` es válido en un límite (bucle, petición, tarea opcional), pero deja rastro: registra,
        relanza o usa el error para explicarlo. Callarlo hace que un fallo real pase desapercibido."""
        silent = []
        for dotted, path in modules():
            for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
                if not (isinstance(node, ast.ExceptHandler) and node.type is not None
                        and ast.unparse(node.type) == "Exception"):
                    continue
                body = ast.Module(body=node.body, type_ignores=[])
                logs = any(isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
                           and isinstance(n.func.value, ast.Name) and n.func.value.id == "log"
                           for n in ast.walk(body))
                raises = any(isinstance(n, ast.Raise) for n in ast.walk(body))
                uses_error = node.name is not None and any(
                    isinstance(n, ast.Name) and n.id == node.name for n in ast.walk(body))
                if not (logs or raises or uses_error):
                    silent.append(f"{dotted}:{node.lineno}")
        self.assertEqual(silent, [], "except Exception sin registrar, relanzar ni usar el error")

    def test_the_package_never_depends_on_the_tests(self):
        for dotted, path in modules():
            for target, _ in imports_of(path):
                self.assertFalse(target.startswith("tests"), f"{dotted} importa {target}")


# Cubre: T-12
class Organization(unittest.TestCase):
    def test_every_module_explains_its_purpose(self):
        missing = [dotted for dotted, path in modules()
                   if not ast.get_docstring(ast.parse(path.read_text(encoding="utf-8")))]
        self.assertEqual(missing, [], "Cada módulo empieza con un docstring que dice para qué sirve")

    def test_every_layer_has_a_description(self):
        for layer in LEVEL:
            init = PACKAGE / layer / "__init__.py"
            self.assertTrue(ast.get_docstring(ast.parse(init.read_text(encoding="utf-8"))), layer)

    def test_the_root_only_holds_thin_entry_points(self):
        for path in ROOT.glob("*.py"):
            self.assertIn(path.name, ENTRY_POINTS, f"{path.name} no debería estar en la raíz")
            if path.name != "check_spec.py":
                self.assertLessEqual(len(path.read_text(encoding="utf-8").splitlines()), 12,
                                     f"{path.name} debe ser solo un punto de entrada")

    def test_the_root_stays_tidy(self):
        allowed_folders = {"anka", "tests", "docs", "spec", "tools", "skills", "evaluacion", "archivo_local",
                           "simulaciones", "__pycache__", ".github", ".claude", ".venv", ".git", ".uv-cache",
                           ".ruff_cache", ".mypy_cache", "htmlcov"}
        for path in ROOT.iterdir():
            if path.is_dir():
                self.assertIn(path.name, allowed_folders, f"carpeta nueva en la raíz: {path.name}")
        self.assertLessEqual(len([p for p in ROOT.iterdir() if p.is_file()]), 25,
                             "La raíz acumula archivos: muévelos a docs/, tools/ o spec/")


if __name__ == "__main__":
    unittest.main()
