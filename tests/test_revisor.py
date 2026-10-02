"""`RevisorDeHilos` (anka/servicios/pipeline.py): qué hilos revisa una pasada y qué hace con uno que no puede leer."""

from tests import _isolate  # noqa: F401  (debe ir primero)

import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from anka.dominio.status import QuoteStatus
from anka.infra import store
from anka.servicios.pipeline import RevisorDeHilos


# Cubre: F1-01
class ReviewerTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.db = store.open_db(Path(self._tmp.name))
        self.addCleanup(self.db.close)
        store.save(self.db, "esperando", "m1", QuoteStatus.WAITING, "a@b.co", {})
        store.save(self.db, "rescatado", "m2", QuoteStatus.IGNORED, "a@b.co", {"force_analysis": True})
        store.save(self.db, "descartado", "m3", QuoteStatus.IGNORED, "a@b.co", {})
        store.save(self.db, "borrador", "m4", QuoteStatus.REVIEW_PENDING, "a@b.co", {})

    def reviewer(self, gmail, *, drafts=False, db=True):
        return RevisorDeHilos({"gmail_query": "q"}, gmail, MagicMock(), self.db if db else None,
                              {"yo@anka.co"}, review_only=True, gmail_drafts=drafts)

    def test_a_pass_adds_waiting_threads_and_rescued_ones_to_what_gmail_returns(self):
        with patch("anka.servicios.pipeline.candidate_thread_ids", return_value=["nuevo"]):
            ids = self.reviewer(MagicMock()).pending_thread_ids()
        self.assertEqual(ids, {"nuevo", "esperando", "rescatado"})   # el descartado normal no vuelve

    def test_pending_drafts_are_only_reviewed_when_creating_gmail_drafts(self):
        with patch("anka.servicios.pipeline.candidate_thread_ids", return_value=[]):
            self.assertIn("borrador", self.reviewer(MagicMock(), drafts=True).pending_thread_ids())

    def test_a_simulation_has_no_database_and_only_looks_at_gmail(self):
        with patch("anka.servicios.pipeline.candidate_thread_ids", return_value=["x"]):
            self.assertEqual(self.reviewer(MagicMock(), db=False).pending_thread_ids(), {"x"})

    def test_a_thread_that_gmail_cannot_read_is_marked_missing_and_the_pass_continues(self):
        gmail = MagicMock()
        gmail.users().threads().get().execute.side_effect = RuntimeError("404")
        reviewer = self.reviewer(gmail)
        with patch("anka.servicios.pipeline.candidate_thread_ids", return_value=["esperando"]), \
                patch.object(RevisorDeHilos, "process_thread") as process, self.assertLogs("anka.servicios.pipeline", "WARNING"):
            reviewer.run()
        process.assert_not_called()
        status = self.db.execute("SELECT status FROM quotes WHERE thread_id='esperando'").fetchone()[0]
        self.assertEqual(status, QuoteStatus.MISSING)


if __name__ == "__main__":
    unittest.main()
