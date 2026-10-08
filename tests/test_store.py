from tests import _isolate  # noqa: F401  (debe ir primero)
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from anka.infra import store


def columns(db, table):
    return [row[1] for row in db.execute(f"PRAGMA table_info({table})")]


def make_legacy_db(path: Path) -> None:
    """Esquema original: sin columnas nuevas, sin tablas auxiliares, user_version = 0."""
    db = sqlite3.connect(path)
    db.execute("""CREATE TABLE quotes (
        thread_id TEXT PRIMARY KEY, last_inbound_id TEXT NOT NULL,
        status TEXT NOT NULL, recipient TEXT NOT NULL,
        analysis_json TEXT NOT NULL, folder_id TEXT, sent_message_id TEXT)""")
    db.execute(
        "INSERT INTO quotes VALUES ('t-1', 'm-1', 'review_pending', 'a@b.co', '{}', NULL, NULL)"
    )
    db.commit()
    db.close()


# Cubre: T-10
class MigrationTests(unittest.TestCase):
    def test_new_database_is_created_at_latest_version_without_backup(self):
        with tempfile.TemporaryDirectory() as d:
            db = store.open_db(Path(d))
            self.assertEqual(db.execute("PRAGMA user_version").fetchone()[0], store.SCHEMA_VERSION)
            self.assertEqual(list(Path(d).glob("*.bak-*")), [])
            db.close()

    def test_legacy_database_is_upgraded_keeping_rows_and_a_backup(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "quotes.sqlite3"
            make_legacy_db(path)
            db = store.open_db(Path(d))
            try:
                self.assertIn("draft_body", columns(db, "quotes"))
                self.assertEqual(
                    db.execute("SELECT status FROM quotes").fetchone()["status"], "review_pending"
                )
                self.assertEqual(
                    db.execute("PRAGMA user_version").fetchone()[0], store.SCHEMA_VERSION
                )
            finally:
                db.close()
            backup = Path(d) / "quotes.sqlite3.bak-v0"
            self.assertTrue(backup.exists())
            old = sqlite3.connect(backup)
            try:
                self.assertNotIn("draft_body", columns(old, "quotes"))
                self.assertEqual(old.execute("SELECT count(*) FROM quotes").fetchone()[0], 1)
            finally:
                old.close()

    def test_reopening_is_idempotent_and_does_not_duplicate_notifications(self):
        with tempfile.TemporaryDirectory() as d:
            db = store.open_db(Path(d))
            store.save(db, "t-1", "m-1", "review_pending", "a@b.co", {"activities": []})
            db.close()
            counts = []
            for _ in range(2):
                db = store.open_db(Path(d))
                counts.append(db.execute("SELECT count(*) FROM dashboard_notifications").fetchone()[0])
                db.close()
            self.assertEqual(counts[0], counts[1])
            self.assertGreater(counts[0], 0)

    def test_database_from_newer_program_is_rejected_untouched(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "quotes.sqlite3"
            db = sqlite3.connect(path)
            db.execute(f"PRAGMA user_version = {store.SCHEMA_VERSION + 1}")
            db.commit()
            db.close()
            with self.assertRaisesRegex(RuntimeError, "más nueva"):
                store.open_db(Path(d))
            check = sqlite3.connect(path)
            try:
                self.assertEqual(
                    check.execute("PRAGMA user_version").fetchone()[0], store.SCHEMA_VERSION + 1
                )
                self.assertEqual(
                    check.execute("SELECT count(*) FROM sqlite_master").fetchone()[0], 0
                )
            finally:
                check.close()

    def test_failed_migration_rolls_back_and_keeps_previous_version(self):
        def broken(db):
            db.execute("CREATE TABLE half_done (x)")
            raise ValueError("falla a media migración")

        with tempfile.TemporaryDirectory() as d:
            store.open_db(Path(d)).close()
            with patch.object(store, "MIGRATIONS", store.MIGRATIONS + (broken,)), \
                    patch.object(store, "SCHEMA_VERSION", store.SCHEMA_VERSION + 1):
                with self.assertRaises(ValueError):
                    store.open_db(Path(d))
            check = sqlite3.connect(Path(d) / "quotes.sqlite3")
            try:
                self.assertEqual(check.execute("PRAGMA user_version").fetchone()[0], store.SCHEMA_VERSION)
                tables = {r[0] for r in check.execute("SELECT name FROM sqlite_master")}
                self.assertNotIn("half_done", tables)
            finally:
                check.close()


if __name__ == "__main__":
    unittest.main()


# Cubre: T-10
class ResourceUseTests(unittest.TestCase):
    def seed(self, directory):
        db = store.open_db(Path(directory))
        for thread in ("t-1", "t-2"):
            store.save(db, thread, f"m-{thread}", "review_pending", "a@b.co",
                       {"received_at": "2026-10-01 09:00"})
        db.commit()
        db.close()

    def test_get_quote_builds_only_the_requested_case_and_matches_the_full_list(self):
        from anka.infra import settings
        from anka.servicios import quotes
        with tempfile.TemporaryDirectory() as d, patch.object(settings, "DATA_DIR", Path(d)):
            self.seed(d)
            one = quotes.get_quote("t-2")
            self.assertEqual(one, next(q for q in quotes.all_quotes(True) if q["thread_id"] == "t-2"))
            self.assertEqual(quotes.get_quote("t-1")["thread_id"], "t-1")
            self.assertIsNone(quotes.get_quote("no-existe"))

    def test_opening_the_database_does_not_write_when_nothing_is_missing(self):
        with tempfile.TemporaryDirectory() as d:
            self.seed(d)
            first = store.open_db(Path(d))   # deriva las novedades que faltaban
            derived = first.execute("SELECT count(*) FROM dashboard_notifications").fetchone()[0]
            first.close()
            second = store.open_db(Path(d))
            try:
                self.assertGreater(derived, 0)
                self.assertEqual(second.total_changes, 0)
            finally:
                second.close()


# Cubre: T-10
class RequestScopeTests(unittest.TestCase):
    def count_opens(self, directory, body):
        from anka.infra import settings
        opened = []
        real = store.open_db

        def counting(path):
            opened.append(path)
            return real(path)

        with patch.object(settings, "DATA_DIR", Path(directory)), patch.object(store, "open_db", counting):
            body()
        return len(opened)

    def test_inside_a_request_every_connect_shares_one_connection(self):
        def body():
            with store.request_scope():
                for _ in range(5):
                    db = store.connect()
                    db.execute("SELECT 1")
                    db.close()
                with store.request_scope():   # anidar no abre otra
                    store.connect().close()
        with tempfile.TemporaryDirectory() as d:
            self.assertEqual(self.count_opens(d, body), 1)

    def test_outside_a_request_every_connect_opens_its_own(self):
        def body():
            for _ in range(3):
                store.connect().close()
        with tempfile.TemporaryDirectory() as d:
            self.assertEqual(self.count_opens(d, body), 3)

    def test_closing_a_shared_connection_discards_what_was_not_committed(self):
        from anka.infra import settings
        with tempfile.TemporaryDirectory() as d, patch.object(settings, "DATA_DIR", Path(d)):
            with store.request_scope():
                db = store.connect()
                db.execute("INSERT INTO case_followup (thread_id, updated_at) VALUES ('t-1', 'x')")   # sin commit
                db.close()                                                           # como cerrar una conexión propia
                check = store.connect()
                self.assertEqual(check.execute("SELECT count(*) FROM case_followup").fetchone()[0], 0)
                check.close()

    def test_a_committed_change_survives_the_close_and_is_seen_by_the_next_function(self):
        from anka.infra import settings
        with tempfile.TemporaryDirectory() as d, patch.object(settings, "DATA_DIR", Path(d)):
            with store.request_scope():
                db = store.connect()
                store.save(db, "t-1", "m-1", "review_pending", "a@b.co", {})
                db.commit()
                db.close()
                self.assertEqual(store.connect().execute("SELECT count(*) FROM quotes").fetchone()[0], 1)

    def test_a_case_page_opens_one_connection_instead_of_many(self):
        from unittest.mock import patch as p2

        from anka.infra import settings
        from tests import test_html_render as page_tests
        page_tests.GoldenTests.setUpClass()
        try:
            def body():
                with store.request_scope(), p2("anka.web.pantalla_caso.load_config", return_value=page_tests.test_config()), \
                        p2("anka.web.paginas.load_config", return_value=page_tests.test_config()):
                    page_tests.pantalla_caso.render_case("t-sent")
            with patch.object(settings, "DATA_DIR", page_tests.GoldenTests.data_dir):
                opened = []
                real = store.open_db
                with patch.object(store, "open_db", lambda path: (opened.append(path), real(path))[1]):
                    body()
            self.assertEqual(len(opened), 1)
        finally:
            page_tests.GoldenTests.tearDownClass()
