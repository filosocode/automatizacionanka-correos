"""`Analisis` y sus partes (anka/dominio/analisis.py) describen lo mismo que el esquema del modelo y la tabla editable."""

from tests import _isolate  # noqa: F401  (debe ir primero)

import unittest
from typing import get_type_hints

from anka.dominio.activities import ACTIVITY_COLUMNS
from anka.dominio.analisis import Actividad, Analisis, Campo
from anka.infra.llm import ANALYSIS_SCHEMA


# Cubre: T-12
class AnalysisShapeTests(unittest.TestCase):
    def test_every_property_the_model_returns_is_declared(self):
        self.assertLessEqual(set(ANALYSIS_SCHEMA["properties"]), set(get_type_hints(Analisis)))

    def test_activity_fields_match_the_editable_table_and_the_model_schema(self):
        declared = set(get_type_hints(Actividad))
        self.assertEqual(declared, {key for key, _ in ACTIVITY_COLUMNS})
        self.assertEqual(declared, set(ANALYSIS_SCHEMA["properties"]["activities"]["items"]["properties"]))

    def test_the_extracted_fields_have_name_and_value(self):
        self.assertEqual(set(get_type_hints(Campo)), {"name", "value"})


if __name__ == "__main__":
    unittest.main()
