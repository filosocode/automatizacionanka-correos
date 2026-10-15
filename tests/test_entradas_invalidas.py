"""Entradas inválidas se responden con claridad y sin errores internos. Cubre: T-31, F1-03"""

from tests import _isolate  # noqa: F401  (debe ir primero)

import io
import re
import tempfile
import unittest
import urllib.parse
from pathlib import Path
from unittest.mock import patch

from anka.dominio.activities import limpiar_cantidad_y_unidad
from anka.infra import settings
from anka.servicios.drive_layout import clean_folder_name
from anka.web import seguridad
from anka.web import server as web


def pedir(metodo, ruta, campos=None):
    handler = web.ReviewHandler.__new__(web.ReviewHandler)
    crudo = urllib.parse.urlencode({"csrf_token": seguridad.CSRF_TOKEN, **(campos or {})}).encode() if metodo == "POST" else b""
    handler.headers = {"Host": "127.0.0.1:1", "Content-Length": str(len(crudo))}
    handler.path = ruta
    handler.rfile = io.BytesIO(crudo)
    visto = {}
    handler._send_html = lambda contenido, status=200: visto.update(status=status, page=contenido)
    handler._redirect = lambda destino: visto.update(status=303, location=destino)
    (handler.do_GET if metodo == "GET" else handler.do_POST)()
    return visto


def texto_visible(pagina):
    principal = re.search(r"<main>(.*?)</main>", pagina, re.S)
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", principal.group(1) if principal else pagina)).strip()


class Base(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        for parche in (patch.object(settings, "DATA_DIR", Path(self.temp.name)),
                       patch("anka.web.paginas.load_config", return_value={"active_mode": "local", "required_fields": []}),
                       patch("anka.web.pantalla_caso.load_config", return_value={"active_mode": "local", "required_fields": []})):
            parche.start()
            self.addCleanup(parche.stop)


class RespuestasWeb(Base):
    def test_a_garbled_number_in_the_url_is_not_an_internal_error(self):
        # Cubre: T-31
        for valor in ("abc", "1e5", "-", "9" * 40, "-" + "9" * 40, "ñ"):
            with self.subTest(valor=valor):
                respuesta = pedir("GET", f"/case?thread_id=x&change_id={urllib.parse.quote(valor)}")
                self.assertLess(respuesta["status"], 500)
                self.assertNotIn("invalid literal", respuesta["page"])

    def test_a_huge_number_in_a_form_does_not_reach_the_database(self):
        # Cubre: T-31
        campos = {"thread_id": "t", "decision": "accept", "change_id": "9" * 40}
        self.assertEqual(pedir("POST", "/change", campos)["status"], 303)

    def test_a_rejected_action_is_a_client_error_with_its_message_not_a_server_error(self):
        # Cubre: T-31
        casos = [("/metadata", {"thread_id": "no-existe"}),
                 ("/rescue", {"thread_id": "no-existe"})]
        for ruta, campos in casos:
            with self.subTest(ruta=ruta):
                with self.assertLogs("anka.web.server", "WARNING"):
                    respuesta = pedir("POST", ruta, campos)
                self.assertEqual(respuesta["status"], 400)
                self.assertTrue(texto_visible(respuesta["page"]))
                self.assertNotIn("Traceback", respuesta["page"])

    def test_a_real_bug_is_still_a_server_error(self):
        # Cubre: T-31
        with patch.dict(web.POST_ROUTES, {"/mode": lambda form: 1 / 0}), self.assertLogs("anka.web.server", "ERROR"):
            self.assertEqual(pedir("POST", "/mode", {"mode": "local"})["status"], 500)


class Nombres(unittest.TestCase):
    def test_a_folder_name_has_a_length_limit_and_does_not_end_mid_word(self):
        # Cubre: T-31
        largo = "Levantamiento topográfico y batimetría del embalse " * 20
        limpio = clean_folder_name(largo)
        self.assertLessEqual(len(limpio), 150)
        self.assertFalse(limpio.endswith((" ", ".")))
        self.assertEqual(clean_folder_name("x" * 400), "x" * 150)      # sin espacios: corte duro
        self.assertEqual(clean_folder_name("Proyecto Norte"), "Proyecto Norte")


class Unidades(unittest.TestCase):
    def test_equivalent_units_are_shown_the_same_way(self):
        # Cubre: F1-03
        casos = {"hectáreas": "ha", "Ha": "ha", "HA": "ha", "hectarea": "ha", "kilómetros": "km", "Km": "km",
                 "kilometro": "km", "M": "m", "metros": "m", "mts": "m", "Km²": "km²", "km2": "km²",
                 "metros cuadrados": "m²", "m2": "m²"}
        for escrita, canonica in casos.items():
            with self.subTest(unidad=escrita):
                fila = {"quantity": "5", "unit": escrita}
                limpiar_cantidad_y_unidad(fila)
                self.assertEqual(fila["unit"], canonica)

    def test_a_unit_that_is_not_a_known_synonym_is_left_as_written(self):
        # Cubre: F1-03
        for unidad in ("puntos", "vuelos", "ha, km", "estaciones", "Jornadas"):
            with self.subTest(unidad=unidad):
                fila = {"quantity": "3", "unit": unidad}
                limpiar_cantidad_y_unidad(fila)
                self.assertEqual(fila["unit"], unidad)

    def test_the_quantity_that_repeats_the_unit_is_still_cleaned(self):
        # Cubre: F1-03
        fila = {"quantity": "544,5 hectáreas", "unit": "hectáreas"}
        limpiar_cantidad_y_unidad(fila)
        self.assertEqual((fila["quantity"], fila["unit"]), ("544,5", "ha"))


if __name__ == "__main__":
    unittest.main()
