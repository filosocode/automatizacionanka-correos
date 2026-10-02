import _isolate  # noqa: F401  (debe ir primero)
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import store


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
