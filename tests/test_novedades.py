"""Novedades: qué cuenta, cuándo se leen y cómo avisan. Cubre: U-25"""

from tests import _isolate  # noqa: F401  (debe ir primero)

import io
import tempfile
import unittest
import urllib.parse
from pathlib import Path
from unittest.mock import patch

from anka.dominio import novedades
from anka.dominio.status import QuoteStatus
from anka.infra import settings, store
from anka.servicios import case_actions, quotes
from anka.web import pantalla_caso, seguridad
from anka.web import server as web
from anka.web.views import render_page
from tests.test_app import FakeGmail, complete_analysis, encoded_message, test_config


def sin_leer(tipo):
    return [n for n in quotes.unread_notifications(100) if n["kind"] == tipo]


class Base(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        for parche in (patch.object(settings, "DATA_DIR", Path(self.temp.name)),
                       patch("anka.web.paginas.load_config", return_value=test_config()),
                       patch("anka.web.pantalla_caso.load_config", return_value=test_config()),
                       patch("anka.servicios.case_actions.load_config", return_value=test_config())):
            parche.start()
            self.addCleanup(parche.stop)
        db = store.open_db(Path(self.temp.name))
        for hilo in ("t-1", "t-2"):
            store.save(db, hilo, "m-" + hilo, QuoteStatus.IGNORED, "cliente@empresa.co", complete_analysis())
        db.commit()
        db.close()

    def nueva(self, hilo, tipo, clave="k1", leida=False):
        db = store.connect()
        try:
            store.add_dashboard_notification(db, hilo, tipo, clave, "Titulo " + tipo, "Mensaje", **({"leida": True} if leida else {}))
            db.commit()
        finally:
            db.close()

    def pedir(self, ruta, campos=None, metodo="POST"):
        handler = web.ReviewHandler.__new__(web.ReviewHandler)
        crudo = urllib.parse.urlencode({"csrf_token": seguridad.CSRF_TOKEN, **(campos or {})}).encode() if metodo == "POST" else b""
        handler.headers = {"Host": "127.0.0.1:1", "Content-Length": str(len(crudo))}
        handler.path = ruta
        handler.rfile = io.BytesIO(crudo)
        visto = {}
        handler._send_html = lambda contenido, status=200: visto.update(status=status, page=contenido)
        handler._send_json = lambda valor, status=200: visto.update(status=status, json=valor)
        handler._redirect = lambda destino: visto.update(status=303, location=destino)
        (handler.do_POST if metodo == "POST" else handler.do_GET)()
        return visto


class QueCuenta(Base):
    def test_only_what_asks_something_of_a_person_counts(self):
        for tipo in ("client_reply", "possible_update", "request_received", "draft_suggested"):
            self.assertTrue(novedades.requiere_accion(tipo), tipo)
        for tipo in ("draft_saved", "response_sent", QuoteStatus.RESPONSE_SENT, "otra_cosa"):
            self.assertFalse(novedades.requiere_accion(tipo), tipo)

    def test_the_unread_list_leaves_out_the_informative_ones(self):
        self.nueva("t-1", "client_reply")
        self.nueva("t-1", "draft_saved", "k2")
        self.nueva("t-2", "response_sent", "k3")
        self.assertEqual([n["kind"] for n in quotes.unread_notifications(100)], ["client_reply"])

    def test_what_the_person_did_is_born_read_and_stays_in_the_table(self):
        self.nueva("t-1", "response_sent", leida=True)
        db = store.connect()
        try:
            fila = db.execute("SELECT read_at FROM dashboard_notifications WHERE kind='response_sent'").fetchone()
        finally:
            db.close()
        self.assertIsNotNone(fila["read_at"])
        self.assertEqual(sin_leer("response_sent"), [])

    def test_sending_a_reply_does_not_leave_an_unread_notification(self):
        db = store.connect()
        db.execute("UPDATE quotes SET status=? WHERE thread_id='t-1'", (QuoteStatus.REVIEW_PENDING,))
        db.commit()
        db.close()
        gmail = FakeGmail({"id": "t-1", "messages": [encoded_message("m-t-1", "cliente@empresa.co", "Solicito cotización.", 1)]})
        with patch("anka.servicios.case_actions.services", return_value=(gmail, object())):
            case_actions.send_dashboard_reply("t-1", "Hola, ¿cuál es el datum?")
        db = store.connect()
        try:
            total = db.execute("SELECT COUNT(*) FROM dashboard_notifications WHERE title='Respuesta enviada'").fetchone()[0]
        finally:
            db.close()
        self.assertEqual(total, 1)                                          # queda constancia
        self.assertEqual([n for n in quotes.unread_notifications(100) if n["title"] == "Respuesta enviada"], [])

    def test_the_counter_counts_cases_not_rows(self):
        self.nueva("t-1", "request_received", "a")
        self.nueva("t-1", "draft_suggested", "b")
        self.nueva("t-2", "client_reply", "c")
        self.assertEqual(quotes.unread_thread_count(), 2)

    def test_the_change_marker_ignores_what_does_not_count(self):
        antes = quotes.change_marker()
        self.nueva("t-1", "draft_saved", "z", leida=False)
        self.assertEqual(quotes.change_marker(), antes)


class CuandoSeLee(Base):
    def test_seeing_a_case_marks_only_its_own_notifications(self):
        self.nueva("t-1", "client_reply", "a")
        self.nueva("t-2", "client_reply", "b")
        respuesta = self.pedir("/notifications/seen", {"thread_id": "t-1"})
        self.assertEqual(respuesta["status"], 303)
        self.assertEqual({n["thread_id"] for n in quotes.unread_notifications(100)}, {"t-2"})

    def test_the_mark_all_button_still_marks_everything(self):
        self.nueva("t-1", "client_reply", "a")
        self.nueva("t-2", "client_reply", "b")
        self.pedir("/notifications/read")
        self.assertEqual(quotes.unread_notifications(100), [])

    def test_merely_rendering_the_case_does_not_mark_it(self):
        # lo marca la página cuando está a la vista; una recarga en segundo plano no debe hacerlo
        self.nueva("t-1", "client_reply", "a")
        pantalla_caso.render_case("t-1")
        self.pedir("/case?thread_id=t-1", metodo="GET")
        self.assertEqual(len(sin_leer("client_reply")), 1)

    def test_the_case_page_carries_the_form_that_marks_it_seen(self):
        pagina = seguridad.add_csrf_tokens(self.pedir("/case?thread_id=t-1", metodo="GET")["page"])
        self.assertIn('id="visto"', pagina)
        self.assertIn('action="/notifications/seen"', pagina)
        self.assertIn('name="thread_id" value="t-1"', pagina)
        self.assertIn(seguridad.CSRF_TOKEN, pagina)

    def test_marking_seen_needs_the_security_token(self):
        self.nueva("t-1", "client_reply", "a")
        handler = web.ReviewHandler.__new__(web.ReviewHandler)
        crudo = b"thread_id=t-1"
        handler.headers = {"Host": "127.0.0.1:1", "Content-Length": str(len(crudo))}
        handler.path = "/notifications/seen"
        handler.rfile = io.BytesIO(crudo)
        visto = {}
        handler._send_html = lambda contenido, status=200: visto.update(status=status)
        handler.do_POST()
        self.assertEqual(visto["status"], 403)
        self.assertEqual(len(sin_leer("client_reply")), 1)


class ComoAvisa(Base):
    def test_the_tab_title_carries_the_number_of_cases_with_news(self):
        self.assertIn("<title>(2) Resumen comercial - Anka</title>", render_page("Resumen comercial", "x", "", 2, "local"))
        self.assertIn("<title>Resumen comercial - Anka</title>", render_page("Resumen comercial", "x", "", 0, "local"))

    def test_the_state_endpoint_reports_the_count_so_the_title_follows_without_reloading(self):
        self.nueva("t-1", "client_reply", "a")
        self.nueva("t-1", "possible_update", "b")
        respuesta = self.pedir("/state", metodo="GET")
        self.assertEqual(respuesta["json"]["sin_leer"], 1)

    def test_the_page_script_updates_the_title_asks_permission_and_marks_seen_only_when_visible(self):
        pagina = render_page("t", "x", "", 0, "local")
        self.assertIn("document.title", pagina)
        self.assertIn("Notification.requestPermission", pagina)
        self.assertIn("new Notification(", pagina)
        self.assertIn("visibilityState", pagina)

    def test_the_browser_alert_is_opt_in_and_hidden_when_already_decided(self):
        pagina = render_page("t", "x", "", 0, "local")
        self.assertIn('id="avisos-navegador"', pagina)
        self.assertIn("hidden", pagina.split('id="avisos-navegador"')[1][:80])    # no se ofrece hasta saber si el navegador lo admite


if __name__ == "__main__":
    unittest.main()
