"""Las pruebas no dependen de las variables del entorno de quien las corre (U-56)."""

from tests import _isolate  # noqa: F401  (debe ir primero)

import os
import subprocess
import sys
import unittest
from unittest.mock import patch


class EntornoAisladoTests(unittest.TestCase):
    # Cubre: U-56
    def test_las_claves_del_entorno_no_llegan_a_las_pruebas(self):
        for variable in _isolate.VARIABLES_DEL_ENTORNO_QUE_AFECTAN:
            self.assertNotIn(variable, os.environ, variable)

    # Cubre: U-56
    def test_importar_el_aislamiento_quita_la_clave_aunque_el_entorno_la_defina(self):
        entorno = dict(os.environ, OPENAI_API_KEY="x", ANTHROPIC_API_KEY="x")
        salida = subprocess.run(
            [sys.executable, "-c",
             "from tests import _isolate; import os; print(os.getenv('OPENAI_API_KEY'), os.getenv('ANTHROPIC_API_KEY'))"],
            env=entorno, capture_output=True, text=True, check=True).stdout.strip()
        self.assertEqual(salida, "None None")

    # Cubre: U-56
    def test_una_prueba_que_necesita_la_clave_la_fija_ella_misma(self):
        from anka.web import paginas  # noqa: F401
        with patch.dict(os.environ, {"OPENAI_API_KEY": "x"}):
            self.assertEqual(os.getenv("OPENAI_API_KEY"), "x")
        self.assertIsNone(os.getenv("OPENAI_API_KEY"))


if __name__ == "__main__":
    unittest.main()
