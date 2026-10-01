"""Puente Gmail → dashboard (modo chatgpt_work): importación, caché, latido y datos de la persona."""

import _isolate  # noqa: F401  (debe ir primero)

import base64
import json
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch

import quotes
import settings
import work_dashboard_sync as sync
import work_heartbeat
import gmail_followup
from store import case_events
from quotes import init_db
from status import QuoteStatus


def b64(text):
    return base64.urlsafe_b64encode(text.encode("utf-8")).decode("ascii")


def payload(thread="T1", message="m1", **extra):
    return {
        "thread_id": thread, "message_id": message, "client_email": "c@example.com",
        "client_name": "Cliente", "project_name": "Proyecto", "status": "Borrador preparado",
        "engine": "chatgpt_work", "activities": [{"activity": "a"}], **extra,
    }


def draft_message(data, thread="T1", message_id="gm1"):
    body = "[REVISAR ANTES DE ENVIAR - BORRAR ESTA SECCION]\n" + sync.START + "\n" \
        + json.dumps(data) + "\n" + sync.END + "\nHola"
    return {"id": message_id, "threadId": thread, "internalDate": "1000",
            "payload": {"mimeType": "text/plain", "body": {"data": b64(body)}}}


class FakeGmail:
    def __init__(self, drafts):
        self.items = drafts  # draft_id -> message
        self.gets = 0

    def users(self):
        return self

    def drafts(self):
        return _Drafts(self)


class _Drafts:
    def __init__(self, gmail):
        self.gmail = gmail

    def list(self, **_):
        items = [{"id": key, "message": {"id": msg["id"], "threadId": msg["threadId"]}}
                 for key, msg in self.gmail.items.items()]
        return _Exec({"drafts": items})

    def get(self, id, **_):
        self.gmail.gets += 1
        return _Exec({"id": id, "message": self.gmail.items[id]})


class _Exec:
    def __init__(self, value):
        self.value = value

    def execute(self):
        return self.value


class BridgeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        patcher = patch.object(settings, "DATA_DIR", Path(self.temp.name))
        patcher.start()
        self.addCleanup(patcher.stop)
        self.checkpoint = Path(self.temp.name) / "state.json"

    def run_sync(self, gmail, cache=None):
        return sync.sync_once(gmail, quotes.dashboard_upsert, self.checkpoint, cache)

    def test_imports_draft_then_skips_unchanged_content(self):
        gmail = FakeGmail({"d1": draft_message(payload())})
        self.assertEqual(self.run_sync(gmail)["updated"], 1)
        self.assertEqual(len(quotes.all_quotes()), 1)
        self.assertEqual(self.run_sync(gmail)["updated"], 0)

    def test_cache_avoids_downloading_unchanged_drafts(self):
        gmail = FakeGmail({"d1": draft_message(payload())})
        cache = {}
        self.run_sync(gmail, cache)
        self.run_sync(gmail, cache)
        self.assertEqual(gmail.gets, 1)
        gmail.items["d1"] = draft_message(payload(summary="nuevo"), message_id="gm2")
        self.assertEqual(self.run_sync(gmail, cache)["updated"], 1)
        self.assertEqual(gmail.gets, 2)

    def test_invalid_draft_counts_as_failure_and_is_not_marked_processed(self):
        bad = draft_message(payload(thread="OTRO"))  # thread_id no coincide con el hilo
        result = self.run_sync(FakeGmail({"d1": bad}))
        self.assertEqual((result["updated"], result["failures"]), (0, 1))
        self.assertEqual(quotes.all_quotes(), [])

    def test_upsert_does_not_move_closed_case_back_or_erase_human_data(self):
        quotes.dashboard_upsert(b64(json.dumps(payload())))
        quotes.save_quote_metadata("T1", "Cliente Confirmado", "Empresa privada",
                                   "Infraestructura", "nota")
        db = init_db()
        db.execute("UPDATE quotes SET status=? WHERE thread_id='T1'", (QuoteStatus.READY,))
        db.commit()
        db.close()
        quotes.dashboard_upsert(b64(json.dumps(payload(message="m2", summary="otro"))))
        stored = quotes.get_quote("T1")
        self.assertEqual(stored["status"], QuoteStatus.READY)
        self.assertEqual(stored["client_type"], "Empresa privada")
        self.assertEqual(stored["sector"], "Infraestructura")
        self.assertEqual(stored["client_name"], "Cliente Confirmado")
        self.assertEqual(stored["notes"], "nota")


class FollowupGmail:
    """messages.list / messages.get de un buzón simulado."""
    def __init__(self, messages):
        self.messages_ = messages  # id -> mensaje con threadId, labelIds, internalDate, headers
        self.gets = 0

    def users(self):
        return self

    def messages(self):
        return self

    def list(self, **_):
        return _Exec({"messages": [{"id": k, "threadId": m["threadId"]}
                                   for k, m in self.messages_.items()]})

    def get(self, id, **_):
        self.gets += 1
        return _Exec(self.messages_[id])


def mail(thread, sent, when, who):
    return {"threadId": thread, "labelIds": ["SENT"] if sent else ["INBOX"],
            "internalDate": str(when * 1000),
            "payload": {"headers": [{"name": "To" if sent else "From", "value": who}]}}


class FollowupTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        patcher = patch.object(settings, "DATA_DIR", Path(self.temp.name))
        patcher.start()
        self.addCleanup(patcher.stop)
        quotes.dashboard_upsert(b64(json.dumps(payload(
            status="Faltan datos", received_at="2026-10-01T10:00:00-05:00"))))
        self.t0 = int(datetime.fromisoformat("2026-10-01T10:00:00-05:00").timestamp())

    def events(self):
        db = init_db()
        try:
            return [(e["kind"], e["source"]) for e in case_events(db, "T1")]
        finally:
            db.close()

    def test_send_from_gmail_moves_case_and_records_origin_once(self):
        gmail = FollowupGmail({"s1": mail("T1", True, self.t0 + 600, "c@example.com")})
        seen = set()
        self.assertEqual(gmail_followup.sync_thread_activity(gmail, seen), {"events": 1, "moved": 1})
        self.assertEqual(quotes.get_quote("T1")["status"], QuoteStatus.WAITING)
        self.assertIn(("sent", "gmail"), self.events())
        # segunda pasada y reinicio del proceso (seen vacío): ni duplica ni vuelve a bajar el mensaje
        gmail_followup.sync_thread_activity(gmail, seen)
        self.assertEqual(gmail_followup.sync_thread_activity(gmail, set())["events"], 0)
        self.assertEqual(gmail.gets, 1)

    def test_send_made_from_dashboard_is_not_recorded_twice(self):
        db = init_db()
        from store import record_event
        record_event(db, "T1", "sent", "dashboard", "s1", "enviado")
        db.commit()
        db.close()
        gmail = FollowupGmail({"s1": mail("T1", True, self.t0 + 600, "c@example.com")})
        gmail_followup.sync_thread_activity(gmail, set())
        self.assertEqual(self.events().count(("sent", "dashboard")), 1)
        self.assertNotIn(("sent", "gmail"), self.events())

    def test_old_sent_message_does_not_change_status(self):
        gmail = FollowupGmail({"s0": mail("T1", True, self.t0 - 86400, "c@example.com")})
        self.assertEqual(gmail_followup.sync_thread_activity(gmail, set())["moved"], 0)
        self.assertEqual(quotes.get_quote("T1")["status"], QuoteStatus.REVIEW_PENDING)

    def test_client_reply_is_recorded_with_notification(self):
        gmail = FollowupGmail({"r1": mail("T1", False, self.t0 + 900, "Cliente <c@example.com>")})
        gmail_followup.sync_thread_activity(gmail, set())
        self.assertIn(("received", "gmail"), self.events())
        self.assertIn("El cliente respondió", {n["title"] for n in quotes.unread_notifications(20)})

    def test_unknown_threads_are_ignored_without_downloading(self):
        gmail = FollowupGmail({"x": mail("OTRO", True, self.t0 + 5, "z@example.com")})
        self.assertEqual(gmail_followup.sync_thread_activity(gmail, set())["events"], 0)
        self.assertEqual(gmail.gets, 0)


class HeartbeatTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        patcher = patch.object(settings, "DATA_DIR", Path(self.temp.name))
        patcher.start()
        self.addCleanup(patcher.stop)

    def beat(self, seconds_ago, **extra):
        data = {"at": (datetime.now().astimezone() - timedelta(seconds=seconds_ago)
                       ).isoformat(timespec="seconds"), "pid": 1, "interval": 60,
                "drafts": 3, "updated": 1, "failures": 0, "error": "", **extra}
        (Path(self.temp.name) / work_heartbeat.HEARTBEAT_FILE).write_text(json.dumps(data))

    def test_states(self):
        self.assertEqual(work_heartbeat.bridge_state()[0], "never")
        self.beat(20)
        self.assertEqual(work_heartbeat.bridge_state()[0], "ok")
        self.beat(20, error="ConnectionError: sin red")
        self.assertEqual(work_heartbeat.bridge_state()[0], "error")
        self.beat(600)
        self.assertEqual(work_heartbeat.bridge_state()[0], "stopped")

    def test_write_then_read_roundtrip(self):
        work_heartbeat.write_heartbeat(60, drafts=2, updated=1)
        self.assertEqual(work_heartbeat.read_heartbeat()["drafts"], 2)
        self.assertFalse(work_heartbeat.other_bridge_running())  # es el propio proceso


if __name__ == "__main__":
    unittest.main()
