"""Emitir la T en un solo paso: código, carpeta de Drive y correo al cliente (spec/requisitos.md T-16, decisión D-29)."""

from tests import _isolate  # noqa: F401  (debe ir primero)

import tempfile
import unittest
import urllib.parse
from pathlib import Path
from unittest.mock import patch

from anka.dominio.status import QuoteStatus
from anka.infra import settings, store
from anka.servicios import case_actions
from anka.web import pantalla_caso, seguridad
from tests.test_app import complete_analysis
from tests.test_openapi import request

TEXTO = ("Hola,\n\nRecibimos toda la información de su proyecto «P». Hemos generado el código T26001.\n\n"
         "Escriba desde ana@acme.com.\n\nSaludos,\nGrupo Anka")


# Cubre: T-16
class IssueCodeInOneStep(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.folder = Path(self._tmp.name)
        patcher = patch.object(settings, "DATA_DIR", self.folder)
        patcher.start()
        self.addCleanup(patcher.stop)
        db = store.open_db(self.folder)
        store.save(db, "t-1", "m1", QuoteStatus.READY_REVIEW, "ana@acme.com", complete_analysis())
        db.commit()
        db.close()
        self.calls = []
        # La comprobación previa a emitir (U-34.2) necesita Gmail y el destinatario: aquí se da por buena.
        for name, valor in (("case_email_context", ({"recipient": "ana@acme.com"}, None, {}, set())),
                            ("_recipient", "ana@acme.com")):
            patcher = patch.object(case_actions, name, return_value=valor)
            patcher.start()
            self.addCleanup(patcher.stop)
        for name, resultado in (("auto_create_project_folder", "Carpeta en Drive: creada."),
                                ("send_dashboard_reply", "Correo enviado a ana@acme.com en el mismo hilo.")):
            patcher = patch.object(case_actions, name, side_effect=self.registrar(name, resultado))
            self.mock = patcher.start()
            self.addCleanup(patcher.stop)

    def registrar(self, nombre, resultado):
        def llamada(*args, **kwargs):
            self.calls.append((nombre, args, kwargs))
            if isinstance(resultado, Exception):
                raise resultado
            return resultado
        return llamada

    def code(self):
        db = store.open_db(self.folder)
        try:
            return db.execute("SELECT quote_code FROM quotes WHERE thread_id='t-1'").fetchone()[0]
        finally:
            db.close()

    def test_one_click_assigns_the_code_creates_the_folder_and_sends_the_email_in_that_order(self):
        message = case_actions.issue_code_and_notify("t-1", "26001", TEXTO, "26001")
        self.assertEqual(self.code(), "26001")
        self.assertEqual([c[0] for c in self.calls], ["auto_create_project_folder", "send_dashboard_reply"])
        _, args, kwargs = self.calls[1]
        self.assertEqual(args[1], TEXTO)                       # el texto que la persona vio es el que sale
        self.assertIs(kwargs["create_folder"], False)          # la carpeta ya se hizo: no se intenta dos veces
        self.assertIn("T26001", message)
        self.assertIn("Carpeta en Drive: creada.", message)
        self.assertIn("Correo enviado", message)

    def test_a_corrected_number_is_reflected_in_the_text_that_goes_out(self):
        case_actions.issue_code_and_notify("t-1", "26045", TEXTO, "26001")
        enviado = self.calls[1][1][1]
        self.assertIn("T26045", enviado)
        self.assertNotIn("T26001", enviado)
        self.assertEqual(self.code(), "26045")

    def test_an_invalid_number_or_an_empty_text_stops_everything_before_touching_anything(self):
        with self.assertRaises(ValueError):
            case_actions.issue_code_and_notify("t-1", "abc", TEXTO, "26001")
        with self.assertRaises(ValueError):
            case_actions.issue_code_and_notify("t-1", "26001", "   ", "26001")
        self.assertIsNone(self.code())
        self.assertEqual(self.calls, [])                       # ni carpeta ni correo

    def test_a_reused_code_sends_nothing(self):
        case_actions.issue_code_and_notify("t-1", "26001", TEXTO, "26001")
        self.calls.clear()
        with self.assertRaises(ValueError):
            case_actions.issue_code_and_notify("t-1", "26002", TEXTO, "26002")      # el caso ya tiene su T
        self.assertEqual(self.calls, [])

    def test_when_gmail_fails_the_code_stays_and_the_message_says_what_is_pending(self):
        patcher = patch.object(case_actions, "send_dashboard_reply", side_effect=RuntimeError("503"))
        patcher.start()
        self.addCleanup(patcher.stop)
        with self.assertLogs("anka.servicios.case_actions", "ERROR"):
            message = case_actions.issue_code_and_notify("t-1", "26001", TEXTO, "26001")
        self.assertEqual(self.code(), "26001")                  # la T no se pierde
        self.assertIn("El correo NO se envió", message)
        self.assertIn("Carpeta en Drive: creada.", message)

    def test_a_duplicate_send_is_explained_not_raised(self):
        patcher = patch.object(case_actions, "send_dashboard_reply", side_effect=ValueError("Ese correo ya salió"))
        patcher.start()
        self.addCleanup(patcher.stop)
        message = case_actions.issue_code_and_notify("t-1", "26001", TEXTO, "26001")
        self.assertIn("⚠ El correo no se envió: Ese correo ya salió", message)

    def test_a_folder_that_cannot_be_created_does_not_stop_the_email(self):
        patcher = patch.object(case_actions, "auto_create_project_folder",
                               return_value="Todavía no se creó la carpeta todavía: Indica la carpeta del cliente.")
        patcher.start()
        self.addCleanup(patcher.stop)
        message = case_actions.issue_code_and_notify("t-1", "26001", TEXTO, "26001")
        self.assertIn("Todavía no se creó la carpeta todavía", message)
        self.assertIn("Correo enviado", message)


# Cubre: T-16
class TheEmailText(unittest.TestCase):
    def test_it_confirms_receipt_gives_the_code_and_explains_the_process(self):
        texto = pantalla_caso.received_email_text("Línea Norte", "T26001", "ana@acme.com")
        for esperado in ("Recibimos toda la información de su proyecto «Línea Norte»", "código T26001",
                         "Cómo continúa el proceso", "1.", "2.", "3.", "cite siempre este código (T26001)",
                         "ana@acme.com", "confirmarlo", "Grupo Anka"):
            self.assertIn(esperado, texto)

    def test_the_same_text_is_what_the_reply_box_prepares_after_the_code_exists(self):
        from anka.dominio.caso import Caso
        review = Caso(
            thread_id="t-1", recipient="ana@acme.com", client_email="ana@acme.com", status="ready_review",
            status_label="x", analysis={"project_name": "Línea Norte"}, reception_assessment={}, services=[],
            draft_body="", draft_id=None, folder_id=None, sent_at="", client_name="ACME", client_name_saved="",
            client_type="", sector="", notes="", first_seen_at="", updated_at="", reply_pending="", proposal=[],
            quote_code="26001", code_stage="T", contact_email="ana@acme.com")
        self.assertEqual(pantalla_caso.reply_template("received", review),
                         pantalla_caso.received_email_text("Línea Norte", "T26001", "ana@acme.com"))


# Cubre: T-16
class Panel(unittest.TestCase):
    def test_the_panel_shows_the_editable_text_with_the_suggested_code_and_both_buttons(self):
        from anka.dominio.caso import Caso
        review = Caso(
            thread_id="t-1", recipient="ana@acme.com", client_email="ana@acme.com", status="ready_review",
            status_label="x", analysis={"project_name": "Línea Norte"}, reception_assessment={}, services=[],
            draft_body="", draft_id=None, folder_id=None, sent_at="", client_name="ACME", client_name_saved="",
            client_type="", sector="", notes="", first_seen_at="", updated_at="", reply_pending="", proposal=[],
            contact_email="ana@acme.com")
        with patch("anka.web.pantalla_caso.suggest_number", return_value="26007"):
            html = str(pantalla_caso._code_panel(review))
        self.assertIn('action="/code-issue"', html)
        self.assertIn('name="suggested_number" value="26007"', html)
        self.assertIn("código T26007", html)                         # el texto ya lleva el número sugerido
        self.assertIn("«Línea Norte»", html)
        self.assertIn("Emitir la T, crear la carpeta y enviar el correo", html)
        self.assertIn('formaction="/code"', html)                    # y la opción de solo emitir
        self.assertIn("SE ENVIARÁ", html)                            # la confirmación dice que sale un correo


# Cubre: T-16
class Route(unittest.TestCase):
    ORIGIN = "http://127.0.0.1:8765"

    def test_the_action_needs_the_token_and_passes_the_form_to_the_service(self):
        with patch("anka.web.acciones.issue_code_and_notify", return_value="T26001 emitida.") as servicio:
            sin_token = request("POST", "/code-issue", b"csrf_token=otro", origin=self.ORIGIN)
            self.assertEqual(sin_token["status"], 403)
            servicio.assert_not_called()
            body = urllib.parse.urlencode({"csrf_token": seguridad.CSRF_TOKEN, "thread_id": "t-1", "number": "26001",
                                           "body": TEXTO, "suggested_number": "26001"}).encode()
            hecho = request("POST", "/code-issue", body, origin=self.ORIGIN)
        self.assertEqual(hecho["status"], 303)
        servicio.assert_called_once_with("t-1", "26001", TEXTO, "26001")
        self.assertIn("thread_id=t-1", hecho["location"])

    def test_a_validation_error_is_shown_as_a_message_not_a_500(self):
        body = urllib.parse.urlencode({"csrf_token": seguridad.CSRF_TOKEN, "thread_id": "t-1", "number": "x",
                                       "body": TEXTO}).encode()
        with patch("anka.web.acciones.issue_code_and_notify", side_effect=ValueError("El número debe tener 5 dígitos.")):
            hecho = request("POST", "/code-issue", body, origin=self.ORIGIN)
        self.assertEqual(hecho["status"], 303)
        self.assertIn("5%20d%C3%ADgitos", hecho["location"])


if __name__ == "__main__":
    unittest.main()
