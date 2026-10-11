"""Cubre: T-34, T-24. Expedientes, reintentos, versiones, límites y aislamiento del flujo comercial."""

from tests import _isolate  # noqa: F401

import base64
import copy
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from anka.cli import work_drive as worker
from anka.dominio.errores import ErrorDeAnka
from anka.infra.drive_exchange import DriveExchange, preparation_lock
from anka.infra.settings import validate_config
from anka.servicios import work_drive as work
from tests.fake_work_drive import MemoryDrive


def message(mid="m1", content=b"Area 82 ha", name="alcance.txt", sender="cliente@example.com"):
    return {"id": mid, "internalDate": "1", "labelIds": ["INBOX"], "payload": {
        "headers": [{"name": "From", "value": sender}, {"name": "Subject", "value": "Solicitud"}],
        "parts": [{"mimeType": "text/plain", "body": {"data": base64.urlsafe_b64encode(b"Correo").decode()}},
                  {"filename": name, "mimeType": "text/plain" if name.endswith(".txt") else "application/octet-stream", "body": {
                      "data": base64.urlsafe_b64encode(content).decode(), "size": len(content)}}]}}


class WorkDriveTests(unittest.TestCase):
    def setUp(self):
        self.drive = MemoryDrive()
        self.exchange = DriveExchange(self.drive)
        self.gmail = MagicMock()
        self.thread = {"id": "t1", "messages": [message()]}
        self.config = {"active_mode": "chatgpt_work", "work_drive_enabled": True,
                       "work_drive_parent_id": "reception", "work_drive_query": "label:piloto",
                       "work_sync_account": "anka@example.com", "required_fields": [],
                       "llm": {"provider": "ollama", "base_url": "http://localhost:11434"}}
        self.gmail.users().getProfile().execute.return_value = {"emailAddress": "anka@example.com"}
        self.gmail.users().threads().get().execute.return_value = self.thread

    def prepare(self):
        return work.prepare_thread(self.gmail, self.exchange, "reception", self.thread, {"anka@example.com"})

    def current(self):
        folder = self.exchange.find("reception", "thread:t1")
        return self.exchange.read_json(self.exchange.find(folder["id"], "current"))

    def test_full_text_and_original_bytes_survive_transport(self):
        self.thread["messages"] = [message(content=("Area 82 ha\n" * 5000).encode())]
        result = self.prepare()
        self.assertEqual(result["status"], "preparado")
        manifest = self.current()
        self.assertEqual(manifest["message_ids"], ["m1"])
        record = manifest["attachments"][0]
        text = "".join(self.drive.contents[s["id"]].decode() for s in record["text_segments"])
        self.assertEqual(text.count("Area 82 ha"), 5000)
        self.assertEqual(record["text_chars"], len(text))
        self.assertEqual(record["text_segments"][-1]["end_char"], len(text))
        self.assertEqual(self.drive.contents[record["original"]["id"]], ("Area 82 ha\n" * 5000).encode())
        self.gmail.users().messages().send.assert_not_called()
        self.gmail.users().drafts().create.assert_not_called()

    def test_rerun_and_label_changes_do_not_duplicate_or_repeat_ocr(self):
        self.prepare()
        writes = self.drive.writes
        self.thread["messages"][0]["labelIds"] = ["IMPORTANT"]
        self.thread["messages"][0]["historyId"] = "changed"
        with patch.object(work, "extract_attachment_text_for_work_packet") as read:
            self.assertEqual(self.prepare()["status"], "sin_cambios")
            read.assert_not_called()
        self.assertEqual(self.drive.writes, writes)

    def test_same_filename_new_message_keeps_both_versions_and_reuses_extraction(self):
        self.prepare()
        original = self.current()["attachments"][0]["original"]["id"]
        self.thread["messages"].append(message("m2", b"Area 90 ha"))
        with patch.object(work, "extract_attachment_text_for_work_packet", wraps=work.extract_attachment_text_for_work_packet) as read:
            self.prepare()
        self.assertEqual(read.call_count, 1)
        records = self.current()["attachments"]
        self.assertEqual([r["message_id"] for r in records], ["m1", "m2"])
        self.assertEqual(records[0]["original"]["id"], original)
        self.assertNotEqual(records[0]["sha256"], records[1]["sha256"])
        manifests = [v for v in self.drive.items.values() if v["name"] == work.MANIFEST_NAME]
        self.assertEqual(len(manifests), 3)  # 2 instantáneas y un índice vigente

    def test_upload_failure_never_publishes_ready_and_retry_reuses_uploaded_files(self):
        self.drive.fail_name = ".txt"
        with self.assertRaises(ConnectionError):
            self.prepare()
        folder = self.exchange.find("reception", "thread:t1")
        self.assertIsNone(self.exchange.find(folder["id"], "current"))
        self.drive.fail_name = ""
        self.prepare()
        self.assertEqual(self.current()["status"], "listo")
        self.assertEqual(len([i for i in self.drive.items.values() if i["name"].endswith("-alcance.txt")]), 1)

    def test_missing_download_is_retryable_not_missing_client_information(self):
        self.thread["messages"][0]["payload"]["parts"][1]["body"] = {"attachmentId": "a1"}
        self.gmail.users().messages().attachments().get().execute.return_value = {}
        with self.assertRaises(ErrorDeAnka):
            self.prepare()
        self.gmail.users().messages().attachments().get().execute.return_value = {"data": "YXJlYQ"}
        self.prepare()
        self.assertEqual(self.current()["status"], "listo")

    def test_own_messages_drafts_and_inline_images_are_not_analyzed(self):
        self.thread["messages"].append(message("m2", sender="anka@example.com"))
        draft = message("m3")
        draft["labelIds"] = ["DRAFT"]
        self.thread["messages"].append(draft)
        logo = {"filename": "logo.png", "mimeType": "image/png", "headers": [
            {"name": "Content-Disposition", "value": "inline"}], "body": {"data": "eA"}}
        self.thread["messages"][0]["payload"]["parts"].append(logo)
        self.prepare()
        self.assertEqual(self.current()["message_ids"], ["m1"])
        self.assertEqual(self.current()["attachments"][-1]["status"], "ignorado")
        self.thread["messages"] = [draft]
        self.assertEqual(self.prepare()["status"], "sin_entradas")

    def test_unsupported_and_partial_stay_internal_even_when_manifest_ready(self):
        self.thread["messages"] = [message(name="plano.dwg")]
        self.prepare()
        manifest = self.current()
        self.assertTrue(manifest["review_required"])
        self.assertEqual(manifest["attachments"][0]["coverage"], "no_legible")
        self.thread["messages"].append(message("m2", name="plano.pdf"))
        with patch.object(work, "extract_attachment_text_for_work_packet", return_value=("[Página 1]\n82 ha", "parcial")):
            self.prepare()
        self.assertEqual(self.current()["attachments"][-1]["coverage"], "parcial")

    def test_limits_record_every_omission_without_download(self):
        self.thread["messages"] = [message("m1"), message("m2")]
        with patch.object(work, "MAX_PACKET_FILES", 1):
            self.prepare()
        self.assertEqual(self.current()["attachments"][1]["status"], "bloqueado")
        self.thread["messages"] = [message("m3")]
        with patch.object(work, "MAX_ATTACHMENT_BYTES", 2):
            self.prepare()
        self.assertIn("declarado", self.current()["attachments"][0]["reason"])
        self.thread["messages"] = [message("m4")]
        self.thread["messages"][0]["payload"]["parts"][1]["body"]["size"] = 0
        with patch.object(work, "MAX_ATTACHMENT_BYTES", 2):
            self.prepare()
        self.assertIn("real", self.current()["attachments"][0]["reason"])

    def test_account_mode_parent_and_query_are_checked_before_writes(self):
        for changes in ({"work_drive_enabled": False}, {"active_mode": "local"},
                        {"work_sync_account": "otra@example.com"}, {"work_drive_query": ""},
                        {"drive_parent_id": "reception"}):
            with self.subTest(changes=changes), self.assertRaises(ErrorDeAnka):
                work.sync_work_drive(self.gmail, self.drive, {**self.config, **changes})
        self.assertEqual(self.drive.writes, 0)
        self.drive.items["reception"]["trashed"] = True
        with self.assertRaises(ErrorDeAnka):
            work.sync_work_drive(self.gmail, self.drive, self.config)

    def test_failed_thread_does_not_stop_the_next_and_query_is_used(self):
        self.gmail.users().threads().get().execute.side_effect = [ConnectionError("offline"), self.thread]
        with patch.object(work, "candidate_thread_versions", return_value={"broken": "1", "t1": "1"}) as candidates:
            with self.assertLogs(work.log, level="ERROR"):
                report = work.sync_work_drive(self.gmail, self.drive, self.config)
        self.assertEqual((report["failures"], report["prepared"]), (1, 1))
        candidates.assert_called_once_with(self.gmail, "label:piloto")

    def test_current_pointer_can_be_retried_without_reextracting(self):
        self.prepare()
        self.thread["messages"].append(message("m2"))
        original_put = self.exchange.put

        def fail_current(parent, key, *args, **kwargs):
            if key == "current":
                raise ConnectionError("pointer")
            return original_put(parent, key, *args, **kwargs)

        with patch.object(self.exchange, "put", side_effect=fail_current), self.assertRaises(ConnectionError):
            self.prepare()
        self.assertEqual(self.current()["message_ids"], ["m1"])
        with patch.object(work, "extract_attachment_text_for_work_packet") as read:
            self.prepare()
            read.assert_not_called()
        self.assertEqual(self.current()["message_ids"], ["m1", "m2"])

    def test_pagination_detects_duplicate_keys_and_wrong_folder_type(self):
        folder = self.exchange.folder("reception", "x", "X")
        self.drive.items[folder["id"]]["mimeType"] = "text/plain"
        with self.assertRaises(ErrorDeAnka):
            self.exchange.folder("reception", "x", "X")
        for i in range(3):
            self.drive.items["copy" + str(i)] = copy.deepcopy(self.drive.items[folder["id"]])
        with self.assertRaisesRegex(ErrorDeAnka, "duplicadas"):
            self.exchange.find("reception", "x")
        self.drive.contents["bad"] = b"[]"
        with self.assertRaises(ErrorDeAnka):
            self.exchange.read_json({"id": "bad"})

    def test_configuration_is_explicit_and_validated(self):
        self.assertEqual(validate_config(self.config), [])
        for key, value in (("work_drive_enabled", "yes"), ("work_drive_query", []),
                           ("work_drive_parent_id", ""), ("work_sync_account", ""), ("active_mode", "local")):
            self.assertTrue(validate_config({**self.config, key: value}))


class WorkerTests(unittest.TestCase):
    def test_exclusive_lock_releases_after_exception(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "lock"
            with preparation_lock(path):
                with self.assertRaises(ErrorDeAnka), preparation_lock(path):
                    pass
            with preparation_lock(path):
                pass

    def test_disabled_worker_never_connects_and_cycle_saves_report(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(worker.settings, "DATA_DIR", Path(directory)):
            with patch.object(worker.settings, "load_config", return_value={}), patch.object(worker, "services") as services:
                self.assertEqual(worker.run()["status"], "desactivado")
                services.assert_not_called()
            with patch.object(worker.settings, "load_config", return_value={"active_mode": "chatgpt_work", "work_drive_enabled": True}), patch.object(worker, "services", return_value=(1, 2)), patch.object(worker, "sync_work_drive", return_value={"prepared": 1}):
                self.assertEqual(worker.run()["prepared"], 1)
            self.assertIn("at", json.loads((Path(directory) / "work_drive_status.json").read_text()))
            with self.assertRaises(ErrorDeAnka):
                worker.run(2)

    def test_watch_reloads_configuration_and_reports_connection_failure(self):
        configs = [{"active_mode": "chatgpt_work", "work_drive_enabled": True}, {}]
        with tempfile.TemporaryDirectory() as directory, patch.object(worker.settings, "DATA_DIR", Path(directory)), patch.object(worker.settings, "load_config", side_effect=configs), patch.object(worker, "services", side_effect=ConnectionError()), patch.object(worker.time, "sleep") as sleep:
            with self.assertLogs(worker.log, level="ERROR"):
                self.assertEqual(worker.run(30)["status"], "desactivado")
            sleep.assert_called_once_with(30)
            self.assertEqual(json.loads((Path(directory) / "work_drive_status.json").read_text())["failures"], 1)
