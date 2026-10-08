"""La trazabilidad requisito → prueba (check_spec.py) y la integridad de la especificación.

Estas pruebas hacen que el CI falle si un requisito queda sin prueba, si una etiqueta apunta a algo que no existe o
si falta un documento de la especificación.
"""

from tests import _isolate  # noqa: F401  (debe ir primero)

import json
import tempfile
import unittest
from pathlib import Path

import check_spec

REPO = Path(__file__).resolve().parents[1]


def make_repo(root: Path, requirements: str, tests: dict[str, str], rules: str = "") -> None:
    (root / "spec").mkdir(parents=True)
    (root / "spec" / "requisitos.md").write_text(requirements, encoding="utf-8")
    for name in check_spec.REQUIRED_FILES:
        target = root / name
        if not target.exists():
            target.write_text("{}" if name.endswith(".json") else "x", encoding="utf-8")
    rules_path = root / check_spec.RULES.relative_to(check_spec.ROOT)
    rules_path.parent.mkdir(parents=True, exist_ok=True)
    rules_path.write_text(rules, encoding="utf-8")
    (root / "tests").mkdir(exist_ok=True)
    for filename, body in tests.items():
        (root / "tests" / filename).write_text(body + "\ndef test_behavior():\n    assert True\n", encoding="utf-8")


class RealRepository(unittest.TestCase):
    def test_every_requirement_has_a_test_and_every_tag_points_to_a_requirement(self):
        self.assertEqual(check_spec.check(), [])

    def test_the_specification_documents_exist(self):
        for name in check_spec.REQUIRED_FILES:
            self.assertTrue((REPO / name).exists(), name)

    def test_requirement_ids_are_unique(self):
        text = (REPO / "spec" / "requisitos.md").read_text(encoding="utf-8")
        ids = check_spec.HEADING.findall(text)
        self.assertEqual(len(ids), len(set(ids)))

    def test_transition_ids_are_unique_and_documented(self):
        rows = json.loads((REPO / "spec" / "transiciones.json").read_text(encoding="utf-8"))["transiciones"]
        ids = [row["id"] for row in rows]
        self.assertEqual(len(ids), len(set(ids)))

    def test_every_rule_in_the_rules_file_is_listed_in_the_rules_index(self):
        index = (REPO / "spec" / "02-reglas.md").read_text(encoding="utf-8")
        for rule in check_spec.RULE_ROW.findall(check_spec.RULES.read_text(encoding="utf-8")):
            self.assertIn(f"| {rule} |", index)


class Checker(unittest.TestCase):
    def run_check(self, requirements, tests, rules=""):
        with tempfile.TemporaryDirectory() as temp:
            make_repo(Path(temp), requirements, tests, rules)
            return check_spec.check(Path(temp))

    def test_a_complete_specification_passes(self):
        problems = self.run_check("### F1-01 · Leer\n", {"test_a.py": "# Cubre: F1-01\n"})
        self.assertEqual(problems, [])

    def test_a_requirement_without_a_test_is_reported(self):
        problems = self.run_check("### F1-01 · Leer\n### F1-02 · Identificar\n", {"test_a.py": "# Cubre: F1-01\n"})
        self.assertEqual(len(problems), 1)
        self.assertIn("F1-02", problems[0])

    def test_a_tag_that_points_nowhere_is_reported(self):
        problems = self.run_check("### F1-01 · Leer\n", {"test_a.py": "# Cubre: F1-01, X-99\n"})
        self.assertTrue(any("X-99" in p for p in problems))

    def test_rules_count_as_requirements(self):
        rules = "| R1 | Descarte |\n| R2 | Adjuntos |\n"
        problems = self.run_check("", {"test_a.py": "# Cubre: R1\n"}, rules)
        self.assertEqual(len(problems), 1)
        self.assertIn("R2", problems[0])

    def test_tags_in_docstrings_and_lists_are_read(self):
        problems = self.run_check("### F1-01 · A\n### T-01 · B\n",
                                  {"test_a.py": '"""Pruebas.\n\nCubre: F1-01, T-01\n"""\n'})
        self.assertEqual(problems, [])

    def test_a_missing_specification_document_is_reported(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            make_repo(root, "### F1-01 · Leer\n", {"test_a.py": "# Cubre: F1-01\n"})
            (root / "spec" / "00-alcance.md").unlink()
            self.assertTrue(any("00-alcance.md" in p for p in check_spec.check(root)))


if __name__ == "__main__":
    unittest.main()
