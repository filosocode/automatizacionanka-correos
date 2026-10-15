"""Errores de validación para la persona y fallos externos frente a bugs propios (U-53, D-118)."""

from tests import _isolate  # noqa: F401  (debe ir primero)

import re
import socket
import ssl
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from anka.dominio import activities
from anka.dominio.errores import AutorizacionDeGoogle, ErrorDeAnka, ErrorDeValidacion, codigo_de_error
from anka.dominio.status import QuoteStatus
from anka.infra import settings, store
from anka.infra.google_services import es_fallo_externo
from anka.servicios import case_actions, change_control
from anka.servicios.repositorio import RepositorioDeCasos
from anka.web import seguridad
from anka.web import server as web
from tests.test_app import complete_analysis
from tests.test_emitir_t import TEXTO
from tests.test_seguridad_http import Base, atender, formulario

RAIZ = Path(__file__).resolve().parents[1]
CARPETA_AUTOMATICA = case_actions.auto_create_project_folder      # antes de que las pruebas la sustituyan


# Cubre: U-53
class ErrorDeValidacionEsCompatible(unittest.TestCase):
    def test_es_un_error_de_anka_y_tambien_un_value_error(self):
        error = ErrorDeValidacion("Escribe algo antes de guardar.")
        self.assertIsInstance(error, ErrorDeAnka)
        self.assertIsInstance(error, ValueError)
        self.assertEqual(str(error), "Escribe algo antes de guardar.")
        with self.assertRaises(ValueError):
            raise error

    def test_su_texto_se_muestra_aunque_no_termine_como_una_frase(self):
        self.assertTrue(seguridad.es_mensaje_para_la_persona(ErrorDeValidacion("sin punto final")))
        self.assertFalse(seguridad.es_mensaje_para_la_persona(ValueError("invalid literal for int()")))
        self.assertTrue(seguridad.es_mensaje_para_la_persona(ValueError("Una frase de Anka.")))     # compatibilidad

    def test_las_reglas_principales_lanzan_el_error_de_validacion(self):
        for llamada in (lambda: activities.parse_activities_form({}),
                        lambda: change_control.comprobar_numero("t-x", "1"),
                        lambda: RepositorioDeCasos().marcar_para_reanalisis("no-existe")):
            with self.subTest(llamada=llamada), self.assertRaises(ErrorDeValidacion):
                llamada()

    def test_los_modulos_principales_no_lanzan_value_error_a_pelo(self):
        # Un `ValueError` suelto es lo que Python o una librería lanzan: un bug. Lo escrito para la persona va tipado.
        permitido = {"anka/servicios/repositorio.py": 1}     # 'Campos de caso no permitidos.' es un error de programación
        for relativa in ("anka/dominio/activities.py", "anka/dominio/preguntas.py", "anka/servicios/case_actions.py",
                         "anka/servicios/change_control.py", "anka/servicios/drive_layout.py",
                         "anka/servicios/ediciones.py", "anka/servicios/followup_reminders.py",
                         "anka/servicios/quotes.py", "anka/servicios/repositorio.py", "anka/servicios/work_import.py"):
            with self.subTest(modulo=relativa):
                texto = (RAIZ / relativa).read_text(encoding="utf-8")
                self.assertEqual(len(re.findall(r"raise ValueError\(", texto)), permitido.get(relativa, 0))

    def test_el_codigo_de_error_es_corto_y_distinto_cada_vez(self):
        codigos = {codigo_de_error() for _ in range(20)}
        self.assertEqual(len(codigos), 20)
        self.assertTrue(all(re.fullmatch(r"[0-9a-f]{8}", c) for c in codigos))


# Cubre: U-53
class ElServidorTrataDistintoUnaFraseDeAnkaYUnBug(Base):
    def test_un_error_de_validacion_en_una_accion_se_muestra_con_400(self):
        def rechaza(form):
            raise ErrorDeValidacion("Escribe algo antes de guardar")
        with patch.dict(web.POST_ROUTES, {"/mode": rechaza}), self.assertLogs("anka.web.server", "WARNING"):
            estado, _, pagina, _ = atender("POST", "/mode", formulario())
        self.assertEqual(estado, 400)
        self.assertIn("Escribe algo antes de guardar", pagina)

    def test_un_value_error_de_python_no_muestra_su_texto(self):
        def falla(form):
            raise ValueError("invalid literal for int() with base 10: 'x'")
        with patch.dict(web.POST_ROUTES, {"/mode": falla}), self.assertLogs("anka.web.server", "WARNING"):
            estado, _, pagina, _ = atender("POST", "/mode", formulario())
        self.assertEqual(estado, 400)
        self.assertNotIn("invalid literal", pagina)


# Cubre: U-53
class FalloDeRedOBugPropio(unittest.TestCase):
    def test_se_distingue_lo_externo_de_lo_propio(self):
        from googleapiclient.errors import HttpError
        import httplib2
        respuesta = httplib2.Response({"status": 503})
        externos = [HttpError(respuesta, b"x"), ConnectionError("x"), TimeoutError("x"), socket.gaierror("x"),
                    ssl.SSLError("x"), OSError("x"), httplib2.ServerNotFoundError("x"), AutorizacionDeGoogle()]
        propios = [KeyError("x"), TypeError("x"), AttributeError("x"), ValueError("x"), RuntimeError("x"),
                   IndexError("x"), ErrorDeValidacion("Mal dato.")]
        for error in externos:
            with self.subTest(error=type(error).__name__):
                self.assertTrue(es_fallo_externo(error))
        for error in propios:
            with self.subTest(error=type(error).__name__):
                self.assertFalse(es_fallo_externo(error))


class EmitirLaTConFallos(unittest.TestCase):
    def setUp(self):
        carpeta = tempfile.TemporaryDirectory()
        self.addCleanup(carpeta.cleanup)
        self.carpeta = Path(carpeta.name)
        for parche in (patch.object(settings, "DATA_DIR", self.carpeta),
                       patch.object(case_actions, "case_email_context",
                                    return_value=({"recipient": "ana@acme.com"}, None, {}, set())),
                       patch.object(case_actions, "_recipient", return_value="ana@acme.com"),
                       patch.object(case_actions, "auto_create_project_folder", return_value="Carpeta en Drive: creada.")):
            parche.start()
            self.addCleanup(parche.stop)
        db = store.open_db(self.carpeta)
        store.save(db, "t-1", "m1", QuoteStatus.READY_REVIEW, "ana@acme.com", complete_analysis())
        db.commit()
        db.close()

    def emitir(self, error):
        with patch.object(case_actions, "send_dashboard_reply", side_effect=error):
            return case_actions.issue_code_and_notify("t-1", "26001", TEXTO, "26001")

    # Cubre: U-53
    def test_un_corte_de_red_dice_que_gmail_no_respondio_y_se_registra_como_aviso(self):
        with self.assertLogs("anka.servicios.case_actions", "WARNING") as registro:
            mensaje = self.emitir(ConnectionError("sin red"))
        self.assertIn("El correo NO se envió: Gmail no respondió.", mensaje)
        self.assertNotIn("error del programa", mensaje)
        self.assertTrue(all(r.levelname == "WARNING" for r in registro.records))
        self.assertTrue(any(r.exc_info for r in registro.records))

    # Cubre: U-53
    def test_un_bug_propio_no_culpa_a_gmail_y_deja_un_codigo_con_su_traza(self):
        with self.assertLogs("anka.servicios.case_actions", "ERROR") as registro:
            mensaje = self.emitir(KeyError("campo"))
        self.assertNotIn("Gmail no respondió", mensaje)
        self.assertIn("error del programa, no de Gmail", mensaje)
        codigo = re.search(r"código ([0-9a-f]{8})", mensaje).group(1)
        self.assertTrue(any(codigo in r.getMessage() and r.exc_info for r in registro.records))
        self.assertIn("La T quedó emitida", mensaje)

    # Cubre: U-53
    def test_la_carpeta_tampoco_culpa_a_la_red_de_un_bug(self):
        with patch.object(case_actions, "get_quote", return_value={}), \
                patch.object(case_actions, "populate_project_folder", side_effect=TypeError("x")), \
                self.assertLogs("anka.servicios.case_actions", "ERROR"):
            texto = case_actions._fill_folder(MagicMock(), MagicMock(), "t-1", "f", set())
        self.assertIn("no se pudo copiar su contenido", texto)
        self.assertIn("error del programa", texto)
        with patch.object(case_actions, "get_quote", return_value={}), \
                patch.object(case_actions, "populate_project_folder", side_effect=TimeoutError("x")), \
                self.assertLogs("anka.servicios.case_actions", "WARNING"):
            texto = case_actions._fill_folder(MagicMock(), MagicMock(), "t-1", "f", set())
        self.assertNotIn("error del programa", texto)

    # Cubre: U-53
    def test_la_carpeta_automatica_distingue_red_de_bug(self):
        with patch.object(case_actions, "approve_review", side_effect=AttributeError("x")),                 patch.object(case_actions, "_note_drive_pending"),                 self.assertLogs("anka.servicios.case_actions", "ERROR"):
            texto = CARPETA_AUTOMATICA("t-1")
        self.assertIn("error del programa", texto)
        with patch.object(case_actions, "approve_review", side_effect=ConnectionError("x")),                 patch.object(case_actions, "_note_drive_pending"),                 self.assertLogs("anka.servicios.case_actions", "WARNING"):
            texto = CARPETA_AUTOMATICA("t-1")
        self.assertNotIn("error del programa", texto)
        self.assertIn("No se pudo crear la carpeta sola", texto)


if __name__ == "__main__":
    unittest.main()
