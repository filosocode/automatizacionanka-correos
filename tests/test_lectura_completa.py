"""Lectura completa (T-24): unidad sin duplicar, ventana de Ollama ajustada al prompt y aviso si no cabe. Cubre: T-24"""

from tests import _isolate  # noqa: F401  (debe ir primero)

import unittest
from unittest.mock import MagicMock, patch

from anka.dominio.activities import limpiar_cantidad_y_unidad
from anka.dominio.errores import ContextoInsuficiente
from anka.infra import llm, settings
from anka.servicios.pipeline import RevisorDeHilos
from tests.test_app import encoded_message


class UnidadSinDuplicarTests(unittest.TestCase):
    def limpiar(self, quantity, unit):
        row = {"quantity": quantity, "unit": unit}
        limpiar_cantidad_y_unidad(row)
        return row["quantity"], row["unit"]

    def test_number_plus_unit_in_quantity_keeps_only_the_number(self):
        self.assertEqual(self.limpiar("544.5 ha", "ha"), ("544.5", "ha"))
        self.assertEqual(self.limpiar("12,5 km.", "km"), ("12,5", "km"))
        self.assertEqual(self.limpiar("3 HA", "ha"), ("3", "ha"))

    def test_several_magnitudes_in_one_text_drop_the_repeated_unit(self):
        quantity = "61 ha, longitud aproximada de 12.209 km"
        self.assertEqual(self.limpiar(quantity, "ha, km"), (quantity, ""))
        self.assertEqual(self.limpiar("61 ha y 12 km", "ha y km"), ("61 ha y 12 km", ""))

    def test_a_quantity_without_its_unit_is_left_alone(self):
        self.assertEqual(self.limpiar("544.5", "ha"), ("544.5", "ha"))
        # «no indicado» en ambos campos también se leía dos veces en la tabla: queda una sola
        self.assertEqual(self.limpiar("no indicado", "no indicado"), ("no indicado", ""))
        self.assertEqual(self.limpiar("", "ha"), ("", "ha"))
        self.assertEqual(self.limpiar("10 hectáreas", "ha"), ("10 hectáreas", "ha"))

    def test_the_unit_is_matched_as_a_whole_word(self):
        self.assertEqual(self.limpiar("5 hab", "ha"), ("5 hab", "ha"))

    def test_rows_without_unit_or_with_non_text_values_do_not_fail(self):
        row = {"quantity": 5, "unit": None}
        limpiar_cantidad_y_unidad(row)
        self.assertEqual(row, {"quantity": 5, "unit": None})


class VentanaDeContextoTests(unittest.TestCase):
    def test_a_short_prompt_uses_the_configured_window(self):
        self.assertEqual(llm.ventana_de_contexto("hola", {}), llm.DEFAULT_NUM_CTX)
        self.assertEqual(llm.ventana_de_contexto("hola", {"num_ctx": 8192}), 8192)

    def test_a_long_prompt_grows_the_window_to_fit_without_cutting_anything(self):
        prompt = "x" * 60_000  # ≈ 24.000 tokens + reserva para la respuesta
        window = llm.ventana_de_contexto(prompt, {})
        self.assertGreater(window, llm.DEFAULT_NUM_CTX)
        self.assertGreaterEqual(window, int(len(prompt) / llm.CHARS_PER_TOKEN) + llm.OUTPUT_RESERVE_TOKENS)
        self.assertEqual(window % 1024, 0)
        self.assertLessEqual(window, llm.DEFAULT_NUM_CTX_MAX)

    def test_a_prompt_that_does_not_fit_the_model_is_refused_instead_of_cut(self):
        with self.assertRaises(ContextoInsuficiente) as caught:
            llm.ventana_de_contexto("x" * 200_000, {})
        self.assertIn("revisión manual", str(caught.exception))
        # un modelo con más contexto, declarado en la configuración, sí lo admite
        self.assertGreater(llm.ventana_de_contexto("x" * 200_000, {"num_ctx_max": 131072}), llm.DEFAULT_NUM_CTX_MAX)

    def test_the_window_reaches_ollama(self):
        captured = {}

        def fake_urlopen(request, timeout):
            import json
            captured["options"] = json.loads(request.data.decode("utf-8"))["options"]
            reply = MagicMock()
            reply.__enter__.return_value.read.return_value = b'{"response": "{}"}'
            return reply

        with patch("urllib.request.urlopen", fake_urlopen):
            llm.analyze_ollama("x" * 60_000, {"provider": "ollama", "model": "m", "base_url": "http://h:1"})
        self.assertGreater(captured["options"]["num_ctx"], llm.DEFAULT_NUM_CTX)

    def test_the_email_body_is_never_cut_in_the_prompt(self):
        body = "dato del cliente. " * 3000  # 54.000 caracteres, muy por encima del antiguo tope de 20.000
        prompt = llm.analysis_prompt([encoded_message("m1", "c@example.com", body + "FIN-DEL-CORREO", 1)], [], [])
        self.assertIn("FIN-DEL-CORREO", prompt)

    def test_num_ctx_max_is_validated(self):
        base = {"provider": "ollama", "model": "m", "base_url": "http://h:1"}
        for value in (512, "32768", True):
            with self.subTest(value=value):
                config = {"active_mode": "local", "llm": {**base, "num_ctx_max": value}, "required_fields": ["x"]}
                self.assertTrue(any("num_ctx_max" in p for p in settings.validate_config(config)))


class HiloQueNoCabeTests(unittest.TestCase):
    def test_one_thread_that_does_not_fit_does_not_stop_the_review_of_the_others(self):
        revisor = RevisorDeHilos({}, MagicMock(), MagicMock(), None, set(), False, True)
        revisor.pending_thread_ids = lambda: {"t-1", "t-2"}  # type: ignore[method-assign]
        procesados = []

        def process(thread_id, thread):
            if thread_id == "t-1":
                raise ContextoInsuficiente("no cabe")
            procesados.append(thread_id)

        revisor.process_thread = process  # type: ignore[method-assign]
        with self.assertLogs("anka.servicios.pipeline", level="WARNING") as logs:
            revisor.run()
        self.assertEqual(procesados, ["t-2"])
        self.assertTrue(any("t-1: no cabe" in line for line in logs.output))


if __name__ == "__main__":
    unittest.main()
