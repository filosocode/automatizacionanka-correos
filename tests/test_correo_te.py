"""Los correos con el código de la oferta: «proyecto recibido» (T) y «oferta aprobada» (T&E). Nunca se envían solos (R8)."""

from tests import _isolate  # noqa: F401  (debe ir primero)

import urllib.parse
import unittest
from unittest.mock import patch

from anka.dominio.caso import Caso
from anka.web import pantalla_caso, seguridad
from tests.test_app import complete_analysis, test_config
from tests.test_openapi import request


def case(stage="T", code="26004", status="ready"):
    return Caso(
        thread_id="t-1", recipient="ana@acme.com", client_email="ana@acme.com", status=status,
        status_label="Proyecto creado", analysis=complete_analysis(), reception_assessment={}, services=[],
        draft_body="", draft_id=None, folder_id="f1", sent_at="", client_name="ACME", client_name_saved="ACME",
        client_type="No clasificado", sector="No clasificado", notes="", first_seen_at="", updated_at="",
        reply_pending="", proposal=[], quote_code=code, code_stage=stage, contact_email="ana@acme.com",
    )


def page(review):
    patches = [patch("anka.web.pantalla_caso.get_quote", return_value=review),
               patch("anka.web.pantalla_caso.case_has_links", return_value=bool(review.quote_code)),
               patch("anka.web.pantalla_caso.load_config", return_value=test_config()),
               patch("anka.web.paginas.load_config", return_value=test_config()),
               patch("anka.servicios.internal_context.load_internal_commercial_index", return_value={})]
    for item in patches:
        item.start()
    try:
        return pantalla_caso.render_case("t-1")
    finally:
        for item in patches:
            item.stop()


# Cubre: U-08
class ApprovedOfferEmail(unittest.TestCase):
    def test_the_text_confirms_the_approval_with_the_te_code_and_asks_to_cite_it(self):
        texto = pantalla_caso.reply_template("te_aprobada", case(stage="TE"))
        self.assertIn("fue aprobada", texto)
        self.assertIn("T&E26004", texto)
        self.assertIn("ana@acme.com", texto)                      # recomienda escribir desde el correo registrado
        self.assertIn("«" + complete_analysis()["project_name"] + "»", texto)

    def test_a_case_that_is_still_t_does_not_get_an_approval_text(self):
        texto = pantalla_caso.reply_template("te_aprobada", case(stage="T"))
        self.assertNotIn("fue aprobada", texto)                   # no se afirma una aprobación que no ocurrió

    def test_the_received_email_still_carries_the_t_code(self):
        texto = pantalla_caso.reply_template("received", case(stage="T"))
        self.assertIn("Hemos generado el código T26004", texto)


# Cubre: U-08
class ReplyBoxOffersTheCodeEmails(unittest.TestCase):
    def test_a_coded_case_always_offers_the_received_email_even_when_the_box_opens_blank(self):
        html = page(case(stage="T"))
        self.assertIn("template=received#respuesta", html)         # antes solo se podía llegar con el redirect de emitir la T
        self.assertNotIn("template=te_aprobada#respuesta\">oferta aprobada", html)   # aún no es T&E

    def test_an_approved_case_also_offers_the_approval_email(self):
        html = page(case(stage="TE"))
        self.assertIn("template=received#respuesta", html)
        self.assertIn("template=te_aprobada#respuesta", html)

    def test_a_case_without_code_offers_neither(self):
        html = page(case(stage="", code=""))
        self.assertNotIn("Correos con el código", html)

    def test_the_prepared_text_is_in_the_box_and_nothing_is_sent(self):
        review = case(stage="TE")
        with patch("anka.web.pantalla_caso.get_quote", return_value=review), \
                patch("anka.web.pantalla_caso.case_has_links", return_value=True), \
                patch("anka.web.pantalla_caso.load_config", return_value=test_config()), \
                patch("anka.web.paginas.load_config", return_value=test_config()), \
                patch("anka.servicios.internal_context.load_internal_commercial_index", return_value={}), \
                patch("anka.servicios.case_actions.send_reply") as enviar:
            html = pantalla_caso.render_case("t-1", template="te_aprobada")
        self.assertIn("fue aprobada y quedó", html)
        enviar.assert_not_called()                                 # solo se prepara: una persona pulsa «Enviar ahora»


# Cubre: U-08
class ApprovingPreparesTheEmail(unittest.TestCase):
    def test_promoting_to_te_redirects_to_the_prepared_email_and_sends_nothing(self):
        body = urllib.parse.urlencode({"csrf_token": seguridad.CSRF_TOKEN, "thread_id": "t-1"}).encode()
        with patch("anka.web.acciones.promote_to_te", return_value="Oferta aprobada: ahora es T&E26004."), \
                patch("anka.servicios.case_actions.send_reply") as enviar:
            hecho = request("POST", "/code-approve", body, origin="http://127.0.0.1:8765")
        self.assertEqual(hecho["status"], 303)
        self.assertIn("template=te_aprobada", hecho["location"])
        self.assertTrue(hecho["location"].endswith("#respuesta"))
        enviar.assert_not_called()

    def test_the_button_says_it_prepares_the_email_and_that_it_is_not_sent_alone(self):
        html = seguridad.add_csrf_tokens(page(case(stage="T")))
        self.assertIn("preparar el correo", html)
        self.assertIn("no se envía solo", html)


if __name__ == "__main__":
    unittest.main()
