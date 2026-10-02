"""Los errores que se capturan en un límite dejan rastro en el registro (spec/06-arquitectura.md, D-17)."""

from tests import _isolate  # noqa: F401  (debe ir primero)

import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock

from anka.cli import work_sync as sync
from anka.infra import store
from anka.servicios import commercial_index, gmail_followup


# Cubre: T-08
class BoundariesLeaveATrace(unittest.TestCase):
    def test_a_draft_the_bridge_cannot_import_is_logged_with_its_traceback(self):
        gmail = MagicMock()
        gmail.users().drafts().list().execute.return_value = {"drafts": [{"id": "d1", "message": {"id": "m1"}}]}
        gmail.users().drafts().get().execute.side_effect = RuntimeError("falla de red")
        with tempfile.TemporaryDirectory() as d, \
                self.assertLogs("anka.cli.work_sync", "WARNING") as logs:
            result = sync.sync_once(gmail, MagicMock(), Path(d) / "estado.json")
        self.assertEqual(result["failures"], 1)
        self.assertIn("Borrador d1", logs.output[0])
        self.assertIsNotNone(logs.records[0].exc_info)        # el traceback queda en el registro, no solo en stdout

    def test_a_message_gmail_cannot_return_is_logged_and_kept_as_unavailable(self):
        gmail = MagicMock()
        gmail.users().messages().get().execute.side_effect = RuntimeError("404")
        with tempfile.TemporaryDirectory() as d:
            db = store.open_db(Path(d))
            try:
                store.record_event(db, "t-1", "received", "gmail", "m-9", "Llegó un correo")
                db.commit()
                with self.assertLogs("anka.servicios.gmail_followup", "WARNING") as logs:
                    gmail_followup._backfill_messages(gmail, db)
                kept = db.execute("SELECT body FROM case_messages WHERE message_id='m-9'").fetchone()
            finally:
                db.close()
        self.assertIn("m-9", logs.output[0])
        self.assertIn("no se pudo leer", kept["body"])          # queda un marcador, no un hueco

    def test_an_unreadable_pdf_is_logged_without_the_file_name(self):
        with tempfile.TemporaryDirectory() as d:
            pdf = Path(d) / "cliente-secreto.pdf"
            pdf.write_bytes(b"no es un pdf")
            with self.assertLogs("anka.servicios.commercial_index", "WARNING") as logs:
                self.assertEqual(commercial_index.extract_pdf_text(pdf), "")
        self.assertNotIn("cliente-secreto", " ".join(logs.output))   # el registro no lleva datos de clientes


if __name__ == "__main__":
    unittest.main()
