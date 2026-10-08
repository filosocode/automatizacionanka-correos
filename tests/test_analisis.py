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
        self.assertEqual(set(get_type_hints(Campo)), {"name", "value", "source"})


if __name__ == "__main__":
    unittest.main()


# Cubre: T-09
class ConfigurationShapeTests(unittest.TestCase):
    def test_every_documented_config_key_is_declared_and_nothing_extra(self):
        from anka.dominio.configuracion import Configuracion
        from anka.infra.settings import KNOWN_CONFIG_KEYS
        self.assertEqual(set(get_type_hints(Configuracion)), KNOWN_CONFIG_KEYS)

    def test_the_example_config_only_uses_declared_keys(self):
        import json
        from pathlib import Path

        from anka.dominio.configuracion import ConfigLlm, Configuracion
        example = json.loads((Path(__file__).resolve().parents[1] / "config.example.json").read_text(encoding="utf-8"))
        self.assertLessEqual(set(example), set(get_type_hints(Configuracion)))
        self.assertLessEqual(set(example["llm"]), set(get_type_hints(ConfigLlm)))


# Cubre: T-14
class HealthReportShapeTests(unittest.TestCase):
    def test_the_report_type_matches_what_swagger_documents(self):
        from anka.servicios.salud import InformeDeSalud, ResultadoDeComprobacion
        from anka.web import openapi
        schema = openapi.build_spec("t")["components"]["schemas"]["Salud"]["properties"]
        self.assertEqual(set(get_type_hints(InformeDeSalud)), set(schema))
        self.assertEqual(set(get_type_hints(ResultadoDeComprobacion)), set(schema["comprobaciones"]["items"]["properties"]))
