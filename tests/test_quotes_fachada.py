"""`quotes.py` es una fachada fina: lo que no es lectura vive en su módulo y se reexporta (U-54, D-119)."""

from tests import _isolate  # noqa: F401  (debe ir primero)

import ast
import unittest
from pathlib import Path

from anka.cli import app
from anka.servicios import cola_del_piloto, quotes, revision_tecnica, vinculos

RAIZ = Path(__file__).resolve().parents[1]
MOVIDOS = {
    vinculos: ("dismiss_pending_links", "dismiss_pending_links_many", "merge_blocker", "merge_project_thread",
               "reason_label", "resolve_link", "separate_project_thread", "separated_project_pairs"),
    revision_tecnica: ("aceptar_lectura_revisada", "resolve_model_proposal", "resolve_technical_review",
                       "volver_a_decidir"),
    cola_del_piloto: ("get_review", "pending_reviews", "review_action", "review_packet_path", "save_review_packet"),
}


# Cubre: U-54
class QuotesEsUnaFachada(unittest.TestCase):
    def test_cada_nombre_movido_sigue_disponible_en_quotes_y_es_el_mismo_objeto(self):
        for modulo, nombres in MOVIDOS.items():
            for nombre in nombres:
                with self.subTest(nombre=nombre):
                    self.assertIs(getattr(quotes, nombre), getattr(modulo, nombre))

    def test_quotes_ya_no_define_lo_que_se_movio(self):
        definidas = {n.name for n in ast.parse((RAIZ / "anka/servicios/quotes.py").read_text(encoding="utf-8")).body
                     if isinstance(n, ast.FunctionDef)}
        for nombres in MOVIDOS.values():
            self.assertEqual(definidas & set(nombres), set())
        self.assertNotIn("list_reviews", definidas)

    def test_quotes_es_corto(self):
        lineas = (RAIZ / "anka/servicios/quotes.py").read_text(encoding="utf-8").splitlines()
        self.assertLess(len(lineas), 300)

    def test_el_comando_que_imprime_vive_en_la_linea_de_comandos(self):
        self.assertTrue(callable(app.list_reviews))
        self.assertFalse(hasattr(quotes, "list_reviews"))

    def test_los_modulos_nuevos_no_imprimen(self):
        for modulo in (vinculos, revision_tecnica, cola_del_piloto, quotes):
            arbol = ast.parse(Path(modulo.__file__).read_text(encoding="utf-8"))
            llamadas = [n for n in ast.walk(arbol) if isinstance(n, ast.Call) and getattr(n.func, "id", "") == "print"]
            self.assertEqual(llamadas, [], modulo.__name__)


if __name__ == "__main__":
    unittest.main()
