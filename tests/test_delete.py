"""Eliminar hilos completos del dashboard (selección con casilla) y novedades solo de lo nuevo."""

from tests import _isolate  # noqa: F401  (debe ir primero)

import io
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from anka.servicios import quotes
from anka.infra import settings
from anka.infra import store
from anka.web import server as web
from anka.dominio.status import QuoteStatus
from tests.test_app import complete_analysis, test_config


def seed(data_dir: Path) -> None:
    db = store.open_db(data_dir)
    for thread_id in ("t-a", "t-b", "t-c"):
        store.save(db, thread_id, f"{thread_id}-1", QuoteStatus.REVIEW_PENDING,
                   f"{thread_id}@empresa.co", complete_analysis())
    db.commit()
    db.close()
    quotes.save_quote_metadata("t-a", "Empresa A", "Empresa privada", "Energía", "nota")
    packet = settings.DATA_DIR / "review_queue"
    packet.mkdir(parents=True, exist_ok=True)
    (packet / "t-a.txt").write_text("paquete", encoding="utf-8")
    (packet / "t-b.txt").write_text("paquete", encoding="utf-8")


# Cubre: T-04
class DeleteTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        for p in (patch.object(settings, "DATA_DIR", Path(self._tmp.name)),
                  patch("anka.web.server.load_config", return_value=test_config())):
            p.start()
            self.addCleanup(p.stop)
        seed(Path(self._tmp.name))

    def ids(self):
        return {q["thread_id"] for q in quotes.all_quotes(True)}

    def count(self, table, thread_id):
        db = store.open_db(Path(self._tmp.name))
        try:
            return db.execute(f"SELECT COUNT(*) FROM {table} WHERE thread_id=?", (thread_id,)).fetchone()[0]
        finally:
            db.close()

    def test_deletes_only_the_selected_threads_with_all_their_data(self):
        message = quotes.delete_quotes(["t-a", "t-b"])
        self.assertEqual(self.ids(), {"t-c"})
        self.assertIn("2 solicitud", message)
        for table in ("quotes", "quote_metadata", "dashboard_notifications"):
            self.assertEqual(self.count(table, "t-a"), 0, table)
        self.assertFalse(quotes.review_packet_path("t-a").exists())
        self.assertFalse(quotes.review_packet_path("t-b").exists())

    def test_notifications_of_deleted_threads_do_not_come_back(self):
        self.assertTrue(any(n["thread_id"] == "t-a" for n in quotes.unread_notifications(100)))
        quotes.delete_quotes(["t-a"])
        # abrir la base vuelve a derivar novedades: no debe resucitar las del hilo borrado
        self.assertEqual({n["thread_id"] for n in quotes.unread_notifications(100)}, {"t-b", "t-c"})

    def test_a_new_thread_after_deleting_everything_is_the_only_notification(self):
        quotes.delete_quotes(["t-a", "t-b", "t-c"])
        self.assertEqual(quotes.unread_notifications(100), [])
        db = store.open_db(Path(self._tmp.name))
        store.save(db, "t-new", "n-1", QuoteStatus.REVIEW_PENDING, "nuevo@empresa.co", complete_analysis())
        db.close()
        self.assertEqual({n["thread_id"] for n in quotes.unread_notifications(100)}, {"t-new"})

    def test_empty_selection_and_unknown_threads_are_harmless(self):
        self.assertIn("ninguna", quotes.delete_quotes([]))
        self.assertIn("ya no existían", quotes.delete_quotes(["no-existe"]))
        self.assertEqual(self.ids(), {"t-a", "t-b", "t-c"})

    def test_duplicate_ids_are_counted_once(self):
        self.assertIn("1 solicitud", quotes.delete_quotes(["t-c", "t-c"]))

    def test_requests_page_has_a_checkbox_per_row_and_the_bulk_form(self):
        page = web._render_requests()
        self.assertEqual(page.count('class="row-check"'), 3)
        self.assertIn('action="/delete"', page)
        self.assertIn('name="thread_id" value="t-a"', page)

    def request(self, path, body=b""):
        handler = web.ReviewHandler.__new__(web.ReviewHandler)
        sent = {}
        handler.headers = {"Host": "127.0.0.1:1", "Content-Length": str(len(body))}
        handler.path = path
        handler.rfile = io.BytesIO(body)
        handler._send_html = lambda content, status=200: sent.update(status=status, page=content)
        handler._redirect = lambda location: sent.update(status=303, location=location)
        handler.do_POST()
        return sent

    def test_post_delete_needs_the_csrf_token_and_accepts_several_threads(self):
        with self.assertLogs("anka.web.server", "WARNING"):
            denied = self.request("/delete", b"thread_id=t-a&csrf_token=malo")
        self.assertEqual(denied["status"], 403)
        self.assertEqual(self.ids(), {"t-a", "t-b", "t-c"})
        ok = self.request("/delete", f"thread_id=t-a&thread_id=t-b&csrf_token={web.CSRF_TOKEN}".encode())
        self.assertEqual(ok["status"], 303)
        self.assertTrue(ok["location"].startswith("/requests?message="))
        self.assertEqual(self.ids(), {"t-c"})



# Cubre: F1-07, T-04
class DeleteWithDriveTests(unittest.TestCase):
    def setUp(self):
        from anka.servicios import case_actions
        from tests.fake_drive import FakeDriveTree
        self.case_actions = case_actions
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.drive = FakeDriveTree({
            "Minera Sur": {"Cotizacion - Proyecto A - aaaaaaaa": {}, "Cotizacion - Proyecto B - bbbbbbbb": {}},
            "Suelta": {},
        })
        for p in (patch.object(settings, "DATA_DIR", Path(self._tmp.name)),
                  patch("anka.servicios.case_actions.load_config", return_value={"drive_parent_id": self.drive.root_id}),
                  patch("anka.servicios.case_actions.services", side_effect=lambda: (object(), self.drive))):
            p.start()
            self.addCleanup(p.stop)
        db = store.open_db(Path(self._tmp.name))
        folder_a = self.drive.folder_id("Minera Sur/Cotizacion - Proyecto A - aaaaaaaa")
        store.save(db, "t-a", "m-1", QuoteStatus.READY, "a@empresa.co", complete_analysis(), folder_id=folder_a)
        store.save(db, "t-nofolder", "m-2", QuoteStatus.REVIEW, "b@empresa.co", complete_analysis())
        store.save(db, "t-odd", "m-3", QuoteStatus.READY, "c@empresa.co", complete_analysis(),
                   folder_id=self.drive.folder_id("Suelta"))
        db.commit()
        db.close()
        self.folder_a = folder_a

    def ids(self):
        return {q["thread_id"] for q in quotes.all_quotes(True)}

    def test_deleting_a_case_moves_only_its_project_folder_to_the_trash(self):
        message = self.case_actions.delete_cases(["t-a"])
        self.assertIn("1 carpeta", message)
        self.assertEqual(self.drive.trashed, {self.folder_a})
        self.assertNotIn("t-a", self.ids())
        names = [f["name"] for f in self.drive.children(self.drive.folder_id("Minera Sur")) if f["id"] not in self.drive.trashed]
        self.assertEqual(names, ["Cotizacion - Proyecto B - bbbbbbbb"])  # la del cliente y las demás quedan

    def test_a_case_without_folder_never_touches_drive(self):
        with patch("anka.servicios.case_actions.services", side_effect=AssertionError("no debe usar Drive")):
            self.case_actions.delete_cases(["t-nofolder"])
        self.assertNotIn("t-nofolder", self.ids())

    def test_a_folder_outside_the_year_client_request_level_is_not_trashed_and_the_case_stays(self):
        with self.assertLogs("anka.servicios.case_actions", "ERROR"):
            message = self.case_actions.delete_cases(["t-odd", "t-nofolder"])
        self.assertEqual(self.drive.trashed, set())
        self.assertIn("No borré 1 solicitud", message)
        self.assertEqual(self.ids(), {"t-a", "t-odd"})

    def test_a_folder_that_is_already_gone_does_not_block_the_deletion(self):
        self.drive.trashed.add(self.folder_a)
        self.case_actions.delete_cases(["t-a"])
        self.assertNotIn("t-a", self.ids())

    def test_a_drive_connection_failure_deletes_nothing(self):
        with patch("anka.servicios.case_actions.services", side_effect=RuntimeError("sin red")):
            with self.assertLogs("anka.servicios.case_actions", "ERROR"):
                message = self.case_actions.delete_cases(["t-a", "t-nofolder"])
        self.assertIn("No borré nada", message)
        self.assertEqual(self.ids(), {"t-a", "t-nofolder", "t-odd"})


if __name__ == "__main__":
    unittest.main()
