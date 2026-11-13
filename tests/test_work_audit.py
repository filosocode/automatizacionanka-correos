"""Cubre: T-46. Lectura, aplicabilidad y selección del origen sin usar Gmail real."""

from tests import _isolate  # noqa: F401

import copy
import base64
import json
import unittest
from datetime import datetime, UTC
from unittest.mock import patch

from anka.cli import work_sync
from anka.dominio.work_contract import reading_summary
from anka.servicios.work_import import _work_analysis, _internal_status
from anka.dominio.status import QuoteStatus
from tests import test_work_sync as bridge_fixtures
from tests.test_work_sync import FakeGmail, draft_message, payload
from tests.fake_work_drive import MemoryDrive
from tests import test_work_drive as drive_fixtures


def audited(mid="m1", **extra):
    """Contrato de prueba con lectura completa y procedencia verificable."""
    return payload(message=mid, skill_version="PRECISO_V4.5_DRIVE", analysis_complete=True,
                   DRIVE_PREFLIGHT={"thread_id": "T1", "message_ids": [mid], "manifest_revision": "r1",
                                    "email_segments_read": 1, "email_segments_available": 1,
                                    "attachment_segments_read": 0, "attachment_segments_available": 0},
                   reading_audit={"email_segment_ids_expected": ["e1"], "email_segment_ids_read": ["e1"],
                                  "attachment_segment_ids_expected": [], "attachment_segment_ids_read": []},
                   **extra)


class AuditTests(unittest.TestCase):
    def test_simulated_applicability_does_not_erase_completed_reading(self):
        data = audited(applicability={"status": "simulado", "reason": "prueba de extracción"})
        data["analysis_complete"] = False
        analysis = _work_analysis(data)
        self.assertEqual(analysis["reading_summary"]["status"], "completa declarada")
        self.assertEqual(analysis["applicability"]["status"], "simulado")
        self.assertEqual(_internal_status(data, analysis), QuoteStatus.REVIEW)
        self.assertEqual(analysis["DRIVE_PREFLIGHT"], data["DRIVE_PREFLIGHT"])
        self.assertEqual(analysis["reading_audit"], data["reading_audit"])

    def test_equal_counts_do_not_hide_different_or_duplicate_segment_ids(self):
        for ids in (["otro"], ["e1", "e1"]):
            data = audited()
            data["reading_audit"]["email_segment_ids_read"] = ids
            self.assertEqual(reading_summary(data)["status"], "pendiente")

    def test_visual_unvalidated_and_access_blocked_are_not_complete(self):
        data = audited(attachments=[{"filename": "plano.pdf", "coverage": "completa",
                                     "reviewed_sections": ["página 1"],
                                     "visual_interpretation": "no_certificada"}])
        self.assertEqual(reading_summary(data)["visual"], "pendiente")
        data["attachments"] = [{"filename": "plano.pdf", "coverage": None, "status": "acceso pendiente"}]
        self.assertEqual(reading_summary(data)["status"], "pendiente")

    def test_unknown_counts_and_wrong_thread_are_pending(self):
        for key, value in (("email_segments_available", None), ("email_segments_read", True),
                           ("thread_id", "OTRO"), ("manifest_revision", None)):
            data = audited()
            data["DRIVE_PREFLIGHT"][key] = value
            self.assertEqual(reading_summary(data)["status"], "pendiente")

    def test_missing_ids_and_internal_note_do_not_approve_reading_or_a_client_reply(self):
        data = audited(draft_reply="BORRADOR INTERNO — NO ENVIAR")
        data["reading_audit"] = {}
        data["analysis_complete"] = False
        analysis = _work_analysis(data)
        self.assertEqual(analysis["reading_summary"]["status"], "pendiente")
        self.assertEqual(analysis["work_reply"], {})

    def test_reading_panel_escapes_document_text(self):
        from anka.web.templating import render
        html = str(render("reading_audit.html", reading={"status": "completa declarada"},
                          preflight={"manifest_revision": "r1"}, audit={}, complete=False, blocked=False,
                          applicability={"status": "simulado", "reason": "<script>alert(1)</script>"}))
        self.assertIn("simulado", html)
        self.assertIn("Lectura", html)
        self.assertNotIn("<script>", html)

    def test_pending_reading_notice_names_the_unread_files_and_says_what_to_do(self):
        # Cubre: T-37
        from anka.web import pantalla_caso
        from anka.web.templating import render
        sin_nombres = pantalla_caso._texto_de_lectura_pendiente({"reading_audit": {}})
        self.assertNotIn("Falta leer", sin_nombres)
        self.assertIn("Pide a Work que analice de nuevo", sin_nombres)
        self.assertIn("Ya revisé estos archivos yo", sin_nombres)   # U-29: siempre hay una salida
        texto = pantalla_caso._texto_de_lectura_pendiente(
            {"reading_audit": {"attachment_files_pending": ["plano.kml", "<b>x</b>.pdf"]}})
        self.assertIn("Falta leer: plano.kml, <b>x</b>.pdf.", texto)
        html = str(render("next_step.html", tone="warn", title="Lectura pendiente", text=texto,
                          action_href="#attachments-review", action_label="Ver bloqueo"))
        self.assertNotIn("<b>x</b>", html)   # el nombre del archivo viene del cliente: se escapa

    def test_sent_draft_and_wrong_sender_are_not_valid_origins(self):
        for change in ({"labelIds": ["SENT"]}, {"labelIds": ["DRAFT"]},
                       {"payload": {"headers": [{"name": "From", "value": "otro@example.com"}]}}):
            source = {**origin("m1"), **change}
            gmail = VerifiedGmail({}, {"m1": source})
            with self.assertRaises(ValueError):
                work_sync._verify_origin(gmail, audited())


class VerifiedGmail(FakeGmail):
    def __init__(self, drafts, origins):
        super().__init__(drafts)
        self.origins = origins

    def messages(self):
        return self

    def get(self, id, **_):
        from tests.test_work_sync import _Exec
        return _Exec(self.origins[id])


_ORIGIN_STAMP = int(datetime(2026, 10, 10, 12, tzinfo=UTC).timestamp() * 1000)


def origin(mid, stamp=_ORIGIN_STAMP):
    """Metadatos Gmail de un cliente de prueba."""
    return {"id": mid, "threadId": "T1", "internalDate": str(stamp), "labelIds": ["INBOX"],
            "payload": {"headers": [{"name": "From", "value": "c@example.com"}]}}


class OriginTests(unittest.TestCase):
    setUp = bridge_fixtures.BridgeTests.setUp
    run_sync = bridge_fixtures.BridgeTests.run_sync
    def test_new_client_message_wins_over_later_edit_of_old_draft(self):
        old = draft_message(audited())
        old["internalDate"] = str(_ORIGIN_STAMP + 20_000)  # editada después del análisis nuevo
        new = draft_message(audited("m2"), message_id="gm2")
        new["internalDate"] = str(_ORIGIN_STAMP + 5_000)
        gmail = VerifiedGmail({"d1": old, "d2": new},
                              {"m1": origin("m1"), "m2": origin("m2", _ORIGIN_STAMP + 2000)})
        imported = []
        work_sync.sync_once(gmail, imported.append, self.checkpoint)
        data = json.loads(base64.urlsafe_b64decode(imported[0]))
        self.assertEqual(data["message_id"], "m2")
        self.assertEqual(data["received_at_source"], "Gmail internalDate")
        self.assertNotEqual(data["received_at"], data["draft_at"])

    def test_late_blocked_draft_cannot_replace_final_same_revision_after_restart(self):
        gmail = VerifiedGmail({"d1": draft_message(audited())}, {"m1": origin("m1")})
        self.assertEqual(self.run_sync(gmail)["updated"], 1)
        blocked = audited()
        blocked["analysis_complete"] = False
        msg = draft_message(blocked, message_id="gm2")
        msg["internalDate"] = "9000"
        gmail.items = {"d2": msg}
        self.assertEqual(self.run_sync(gmail)["updated"], 0)

    def test_wrong_origin_rejected_and_network_failure_retried(self):
        bad = origin("m1")
        bad["threadId"] = "OTRO"
        gmail = VerifiedGmail({"d1": draft_message(audited())}, {"m1": bad})
        self.assertEqual(len(self.run_sync(gmail)["rejected"]), 1)
        cache = {}
        with patch.object(gmail, "get", side_effect=ConnectionError("offline")):
            self.assertEqual(self.run_sync(gmail, cache)["failures"], 1)
        gmail.origins["m1"] = origin("m1")
        self.assertEqual(self.run_sync(gmail, cache)["updated"], 1)


class PublicationTests(unittest.TestCase):
    setUp = drive_fixtures.WorkDriveTests.setUp
    prepare = drive_fixtures.WorkDriveTests.prepare
    current = drive_fixtures.WorkDriveTests.current
    def test_ready_messages_survive_unchanged_preparation(self):
        self.assertIsInstance(self.drive, MemoryDrive)
        self.assertEqual(self.prepare()["ready_message_ids"], ["m1"])
        self.assertEqual(self.prepare()["ready_message_ids"], ["m1"])

    def test_unopened_attachment_limit_is_not_an_illegible_document(self):
        with patch("anka.servicios.work_drive.MAX_ATTACHMENT_BYTES", 1):
            self.prepare()
        record = copy.deepcopy(self.current()["attachments"][0])
        self.assertIsNone(record["coverage"])
        self.assertEqual(record["reviewed_sections"], [])
