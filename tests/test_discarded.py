"""Vista "Descartadas": una persona puede ver por qué se descartó un correo y rescatarlo."""

from tests import _isolate  # noqa: F401  (debe ir primero)

import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from anka.servicios import quotes
from anka.infra import settings
from anka.infra import store
from anka.web import server as web
from anka.web import seguridad
from anka.web import paginas
from anka.dominio.status import QuoteStatus
from tests.test_app import complete_analysis, test_config


def seed(data_dir: Path) -> None:
    db = store.open_db(data_dir)
    store.save(db, "t-news", "n-1", QuoteStatus.IGNORED, "noreply@tienda.com", {
        "ignored_reason": "remitente automático (no-reply)",
        "email": {"subject": "Ofertas de oficina", "from": "Tienda <noreply@tienda.com>"},
        "received_at": "2026-09-01T09:00:00-05:00",
    })
    store.save(db, "t-model", "m-1", QuoteStatus.IGNORED, "ana@empresa.co", {
        "ignored_reason": "El modelo no la identificó como solicitud de cotización",
        "email": {"subject": "Gracias", "from": "Ana <ana@empresa.co>"},
        "is_quote_request": False, "activities": [],
    })
    store.save(db, "t-old", "o-1", QuoteStatus.IGNORED, "viejo@empresa.co", {})  # versión anterior
    store.save(db, "t-real", "r-1", QuoteStatus.REVIEW_PENDING, "cliente@empresa.co",
               complete_analysis())
    db.commit()
    db.close()


# Cubre: F1-02, R3
class DiscardedTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.patches = [
            patch.object(settings, "DATA_DIR", Path(self._tmp.name)),
            patch("anka.web.paginas.load_config", return_value=test_config()), patch("anka.web.pantalla_caso.load_config", return_value=test_config()),
        ]
        for p in self.patches:
            p.start()
            self.addCleanup(p.stop)
        seed(Path(self._tmp.name))

    def rows(self):
        return {row["thread_id"]: row for row in quotes.discarded_quotes()}

    def test_lists_only_discarded_threads_with_reason_sender_and_subject(self):
        rows = self.rows()
        self.assertEqual(set(rows), {"t-news", "t-model", "t-old"})
        news = rows["t-news"]
        self.assertEqual(news["reason"], "remitente automático (no-reply)")
        self.assertEqual(news["subject"], "Ofertas de oficina")
        self.assertIn("noreply@tienda.com", news["sender"])
        self.assertFalse(news["rescued"])

    def test_threads_discarded_by_an_older_version_still_show_up(self):
        old = self.rows()["t-old"]
        self.assertIn("anterior", old["reason"])
        self.assertEqual(old["sender"], "viejo@empresa.co")

    def test_rescue_flags_the_thread_for_the_next_review_without_changing_its_status(self):
        message = quotes.rescue_discarded("t-news")
        self.assertIn("próxima búsqueda de correos", message)
        row = self.rows()["t-news"]
        self.assertTrue(row["rescued"])
        saved = quotes.get_quote("t-news")
        self.assertEqual(saved["status"], QuoteStatus.IGNORED)
        self.assertTrue(saved["analysis"]["force_analysis"])
        self.assertEqual(saved["analysis"]["ignored_reason"], "remitente automático (no-reply)")

    def test_rescue_forces_a_new_look_even_if_the_last_message_is_the_same(self):
        quotes.rescue_discarded("t-news")
        db = store.open_db(Path(self._tmp.name))
        try:
            last = db.execute("SELECT last_inbound_id FROM quotes WHERE thread_id='t-news'").fetchone()[0]
        finally:
            db.close()
        self.assertEqual(last, "")

    def test_only_discarded_threads_can_be_rescued(self):
        with self.assertRaisesRegex(ValueError, "descartad"):
            quotes.rescue_discarded("t-real")
        with self.assertRaisesRegex(ValueError, "no existe"):
            quotes.rescue_discarded("no-existe")

    def test_page_shows_the_reason_and_a_rescue_form_per_thread(self):
        page = paginas.render_discarded()
        self.assertIn("Ofertas de oficina", page)
        self.assertIn("remitente automático (no-reply)", page)
        self.assertEqual(page.count('action="/rescue"'), 3)
        self.assertIn('href="/discarded"', page)  # enlace en la navegación

    def test_page_marks_rescued_threads_instead_of_offering_the_button_again(self):
        quotes.rescue_discarded("t-news")
        page = paginas.render_discarded()
        self.assertEqual(page.count('action="/rescue"'), 2)
        self.assertIn("Se volverá a mirar", page)

    def test_empty_state(self):
        with tempfile.TemporaryDirectory() as other, patch.object(settings, "DATA_DIR", Path(other)):
            self.assertIn("No hay correos aparte", paginas.render_discarded())

    def request(self, method, path, body=b""):
        handler = web.ReviewHandler.__new__(web.ReviewHandler)
        sent = {}
        handler.headers = {"Host": "127.0.0.1:1", "Content-Length": str(len(body))}
        handler.path = path
        handler.rfile = io.BytesIO(body)
        handler._send_html = lambda content, status=200: sent.update(status=status, page=content)
        handler._redirect = lambda location: sent.update(status=303, location=location)
        getattr(handler, f"do_{method}")()
        return sent

    def test_get_discarded_route(self):
        sent = self.request("GET", "/discarded")
        self.assertEqual(sent["status"], 200)
        self.assertIn("Descartadas", sent["page"])

    def test_post_rescue_requires_the_csrf_token_and_then_flags_the_thread(self):
        with self.assertLogs("anka.web.server", "WARNING"):
            denied = self.request("POST", "/rescue", b"thread_id=t-news&csrf_token=malo")
        self.assertEqual(denied["status"], 403)
        self.assertFalse(self.rows()["t-news"]["rescued"])
        ok = self.request("POST", "/rescue", f"thread_id=t-news&csrf_token={seguridad.CSRF_TOKEN}".encode())
        self.assertEqual(ok["status"], 303)
        self.assertTrue(ok["location"].startswith("/discarded?message="))
        self.assertTrue(self.rows()["t-news"]["rescued"])

    def test_rescue_of_a_non_discarded_thread_returns_an_error_page(self):
        body = f"thread_id=t-real&csrf_token={seguridad.CSRF_TOKEN}".encode()
        with self.assertLogs("anka.web.server", "WARNING"):
            sent = self.request("POST", "/rescue", body)
        self.assertEqual(sent["status"], 400)       # lo rechaza la aplicación con su motivo: no es un fallo del servidor
        self.assertIn("descartad", sent["page"])

    def test_rescue_is_audited_without_client_data(self):
        with self.assertLogs("anka.servicios.quotes", "INFO") as logs:
            quotes.rescue_discarded("t-news")
        text = "\n".join(logs.output)
        self.assertIn("t-news", text)
        self.assertNotIn("tienda.com", text)
        self.assertNotIn("Ofertas", text)

    def test_saved_analysis_stays_valid_json_for_the_pipeline(self):
        quotes.rescue_discarded("t-model")
        db = store.open_db(Path(self._tmp.name))
        try:
            raw = db.execute("SELECT analysis_json FROM quotes WHERE thread_id='t-model'").fetchone()[0]
            forced = db.execute(
                "SELECT thread_id FROM quotes WHERE status='ignored' "
                "AND json_extract(analysis_json, '$.force_analysis') = 1"
            ).fetchall()
        finally:
            db.close()
        self.assertTrue(json.loads(raw)["force_analysis"])
        self.assertEqual([r[0] for r in forced], ["t-model"])


if __name__ == "__main__":
    unittest.main()
