"""La hoja de Google en la pantalla del caso y la acción que la crea o actualiza (requisito T-17)."""

from tests import _isolate  # noqa: F401  (debe ir primero)

import unittest
import urllib.parse
from dataclasses import replace
from unittest.mock import patch

from anka.servicios.hoja_de_calculo import ResultadoHoja
from anka.web import seguridad
from tests.test_correo_te import case, page
from tests.test_openapi import request

ORIGIN = "http://127.0.0.1:8765"


# Cubre: T-17
class CasePanel(unittest.TestCase):
    def test_a_case_with_a_folder_but_no_sheet_offers_to_create_it(self):
        html = page(case())
        self.assertIn("Crear la hoja de Google", html)
        self.assertNotIn("Abrir en Google Sheets", html)
        self.assertIn('action="/hoja"', html)

    def test_a_case_with_a_sheet_links_to_it_and_says_it_updates_itself(self):
        html = page(replace(case(), sheet_id="abc123", sheet_synced_at="2026-10-02T10:30:00-05:00"))
        self.assertIn('href="https://docs.google.com/spreadsheets/d/abc123"', html)
        self.assertIn('rel="noopener noreferrer"', html)               # la hoja se abre en otra pestaña, sin darle acceso
        self.assertIn("Se actualiza sola cada vez que guardas la tabla", html)
        self.assertIn("última actualización 2026-10-02 10:30", html)
        self.assertIn("Actualizar la hoja ahora", html)

    def test_before_the_folder_exists_it_explains_where_the_sheet_will_come_from(self):
        html = page(replace(case(), folder_id=None))
        self.assertIn("(al emitir la T)", html)
        self.assertIn("Bajar en Excel", html)                          # mientras tanto, el Excel de siempre
        self.assertNotIn("Crear la hoja de Google", html)

    def test_a_failed_update_is_shown_in_the_case(self):
        html = page(replace(case(), sheet_id="abc", sheet_error="No se pudo actualizar la hoja de Google (RuntimeError)."))
        self.assertIn("⚠ No se pudo actualizar la hoja de Google", html)

    def test_the_warning_that_edits_made_in_the_sheet_are_overwritten_is_visible(self):
        html = page(replace(case(), sheet_id="abc"))
        self.assertIn("el cambio se pierde en la próxima actualización", html)
        self.assertIn("solicitud inicial y el historial de cambios", html)


# Cubre: T-17
class SheetAction(unittest.TestCase):
    def body(self, token=None):
        return urllib.parse.urlencode({"csrf_token": token or seguridad.CSRF_TOKEN, "thread_id": "t-1"}).encode()

    def test_it_needs_the_token_and_shows_the_result_in_the_case(self):
        with patch("anka.web.acciones.sincronizar_hoja",
                   return_value=ResultadoHoja("creada", "Hoja de Google creada en la carpeta del proyecto.", "u")) as hoja:
            sin_token = request("POST", "/hoja", self.body("otro"), origin=ORIGIN)
            self.assertEqual(sin_token["status"], 403)
            hoja.assert_not_called()
            hecho = request("POST", "/hoja", self.body(), origin=ORIGIN)
        self.assertEqual(hecho["status"], 303)
        self.assertIn("Hoja%20de%20Google%20creada", hecho["location"])
        self.assertTrue(hecho["location"].endswith("#hoja-google"))
        hoja.assert_called_once_with("t-1")

    def test_a_google_failure_becomes_a_message_and_is_recorded_not_a_500(self):
        with patch("anka.web.acciones.sincronizar_hoja", side_effect=RuntimeError("503 datos tecnicos")), \
                patch("anka.web.acciones.RepositorioDeCasos") as repo, \
                self.assertLogs("anka.web.acciones", "WARNING"):
            hecho = request("POST", "/hoja", self.body(), origin=ORIGIN)
        self.assertEqual(hecho["status"], 303)
        self.assertNotIn("datos%20tecnicos", hecho["location"])
        repo.return_value.registrar_error_de_hoja.assert_called_once()
        self.assertIn("No se pudo actualizar la hoja de Google", repo.return_value.registrar_error_de_hoja.call_args.args[1])


if __name__ == "__main__":
    unittest.main()
