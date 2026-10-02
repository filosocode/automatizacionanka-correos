"""Los enlaces relativos de la documentación apuntan a archivos que existen.

Mover o renombrar un documento sin actualizar sus enlaces es una de las formas más baratas de que la
documentación se pudra; esta prueba la detecta.

Cubre: T-11
"""

from tests import _isolate  # noqa: F401  (debe ir primero)

import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SKIP = {".venv", ".git", "simulaciones", "golden", "__pycache__", "archivo_local", ".ruff_cache", ".uv-cache"}
LINK = re.compile(r"\[[^\]]*\]\(([^)\s]+)\)")


def markdown_files():
    return sorted(p for p in ROOT.rglob("*.md") if not (set(p.relative_to(ROOT).parts) & SKIP))


class DocumentationLinks(unittest.TestCase):
    def test_every_relative_link_points_to_an_existing_file(self):
        broken = []
        for path in markdown_files():
            for target in LINK.findall(path.read_text(encoding="utf-8")):
                if target.startswith(("http://", "https://", "mailto:", "#")):
                    continue
                file_part = target.split("#", 1)[0]
                if file_part and not (path.parent / file_part).exists():
                    broken.append(f"{path.relative_to(ROOT)} → {target}")
        self.assertEqual(broken, [], "Enlaces rotos en la documentación")

    def test_every_document_in_docs_is_listed_in_its_index(self):
        index = (ROOT / "docs" / "README.md").read_text(encoding="utf-8")
        for path in (ROOT / "docs").iterdir():
            if path.is_file() and path.name != "README.md" and path.suffix in {".md", ".txt"}:
                self.assertIn(path.name, index, f"docs/{path.name} no está en docs/README.md")


if __name__ == "__main__":
    unittest.main()
