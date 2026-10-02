"""Puente Gmail → dashboard (modo chatgpt_work): importación, caché, latido y datos de la persona."""

import _isolate  # noqa: F401  (debe ir primero)

import base64
import re
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


# Cubre: F1-01, T-02
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

    def list(self, q="", **_):
        terms = re.findall(r"from:([^\s)]+)", q)
        picked = [
            (k, m) for k, m in self.messages_.items()
            if not terms or (
                "SENT" not in m["labelIds"] and any(
                    t in m["payload"]["headers"][0]["value"].casefold() for t in terms))
        ]
        return _Exec({"messages": [{"id": k, "threadId": m["threadId"]} for k, m in picked]})

    def get(self, id, **_):
        self.gets += 1
        return _Exec(self.messages_[id])


def mail(thread, sent, when, who):
    return {"threadId": thread, "labelIds": ["SENT"] if sent else ["INBOX"],
            "internalDate": str(when * 1000),
            "payload": {"headers": [{"name": "To" if sent else "From", "value": who}]}}


# Cubre: F1-06, T-04
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
        self.assertEqual(gmail_followup.sync_thread_activity(gmail, seen), {"events": 1, "moved": 1, "related": 0})
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
        gmail = FollowupGmail({"x": mail("OTRO", False, self.t0 + 5, "z@otraempresa.com")})
        self.assertEqual(gmail_followup.sync_thread_activity(gmail, set())["events"], 0)
        self.assertEqual(gmail.gets, 0)


# Cubre: F1-06, U-01
class NextStepTests(unittest.TestCase):
    """Respuesta del cliente sin analizar y propuestas del modelo sobre una tabla editada a mano."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        patcher = patch.object(settings, "DATA_DIR", Path(self.temp.name))
        patcher.start()
        self.addCleanup(patcher.stop)
        quotes.dashboard_upsert(b64(json.dumps(payload(
            message="m1", received_at="2026-10-01T10:00:00-05:00"))))

    def reply(self, ref, minutes_after, ago_minutes=0):
        when = datetime.fromisoformat("2026-10-01T10:00:00-05:00") + timedelta(minutes=minutes_after)
        db = init_db()
        from store import record_event
        record_event(db, "T1", "received", "gmail", ref, "Correo recibido", when.isoformat())
        db.commit()
        db.close()

    def test_reply_pending_until_analysis_covers_it(self):
        self.reply("m1", 0)                       # el correo original: ya analizado
        self.assertEqual(quotes.get_quote("T1")["reply_pending"], "")
        self.reply("m2", 15)                      # respuesta posterior
        self.assertTrue(quotes.get_quote("T1")["reply_pending"])
        quotes.dashboard_upsert(b64(json.dumps(payload(
            message="m2", received_at="2026-10-01T10:15:00-05:00", summary="con respuesta"))))
        self.assertEqual(quotes.get_quote("T1")["reply_pending"], "")

    def test_small_clock_difference_is_not_a_reply(self):
        self.reply("x1", 1)                       # llegada 1 min después de la fecha del correo
        self.assertEqual(quotes.get_quote("T1")["reply_pending"], "")

    def test_banner_and_chip_show_pending_reply(self):
        import web
        self.reply("m2", 15)
        with patch("web.load_config", return_value={"active_mode": "chatgpt_work", "required_fields": []}):
            page = web._render_case("T1")
            row = web._quote_row(quotes.get_quote("T1"))
        self.assertIn("respondió", page)
        self.assertIn("ChatGPT Work", page)       # pasaron más de 30 min desde 2026
        self.assertIn("Respondió", row)

    def test_model_proposal_can_be_adopted_or_dismissed(self):
        quotes.save_activities("T1", [{"activity": "a", "quantity": "1"}])
        quotes.dashboard_upsert(b64(json.dumps(payload(
            message="m2", activities=[{"activity": "a", "quantity": "2"}, {"activity": "b"}]))))
        proposal = quotes.get_quote("T1")["proposal"]
        self.assertEqual({item["kind"] for item in proposal}, {"nueva", "distinta"})
        quotes.resolve_model_proposal("T1", use_proposal=True)
        stored = quotes.get_quote("T1")
        self.assertEqual([a["activity"] for a in stored["analysis"]["activities"]], ["a", "b"])
        self.assertNotIn("manual_edit", stored["analysis"])
        self.assertEqual(stored["proposal"], [])
        # descartar: la tabla propia se queda y la propuesta desaparece
        quotes.save_activities("T1", [{"activity": "solo mía"}])
        quotes.dashboard_upsert(b64(json.dumps(payload(message="m3", activities=[{"activity": "z"}]))))
        quotes.resolve_model_proposal("T1", use_proposal=False)
        stored = quotes.get_quote("T1")
        self.assertEqual(stored["analysis"]["activities"], [{"activity": "solo mía"}])
        self.assertEqual(stored["proposal"], [])
        db = init_db()
        kinds = [e["kind"] for e in case_events(db, "T1")]
        db.close()
        self.assertEqual(kinds.count("proposal"), 2)


# Cubre: F1-06, T-03
class RelatedThreadTests(unittest.TestCase):
    """Correos de la misma persona o empresa en otro hilo: se proponen, no se unen solos."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        patcher = patch.object(settings, "DATA_DIR", Path(self.temp.name))
        patcher.start()
        self.addCleanup(patcher.stop)
        quotes.dashboard_upsert(b64(json.dumps(payload(
            client_email="ana@acme.com", client_name="Ana", project_name="Llanos 8",
            received_at="2026-10-01T10:00:00-05:00"))))
        self.t0 = int(datetime.fromisoformat("2026-10-01T10:00:00-05:00").timestamp())

    def run_sync(self, messages):
        gmail = FollowupGmail(messages)
        return gmail_followup.sync_thread_activity(gmail, set()), gmail

    def links(self):
        return quotes.get_quote("T1")["links"]

    def test_same_person_in_another_thread_is_proposed(self):
        result, _ = self.run_sync({"n1": mail("NUEVO", False, self.t0 + 3600, "Ana <ana@acme.com>")})
        self.assertEqual(result["related"], 1)
        self.assertEqual(self.links()[0]["reason"], "sender")
        # el caso no cambia por sí solo
        self.assertEqual(quotes.get_quote("T1")["status"], QuoteStatus.DRAFT_CREATED)

    def test_other_person_of_same_company_is_flagged_as_domain(self):
        self.run_sync({"n1": mail("NUEVO", False, self.t0 + 3600, "Luis <luis@acme.com>")})
        link = self.links()[0]
        self.assertEqual((link["reason"], link["sender_name"]), ("domain", "Luis"))
        self.assertIn("otra persona de la misma empresa", quotes.reason_label(link["reason"]))

    def test_subject_mentioning_the_project_is_noted(self):
        message = mail("NUEVO", False, self.t0 + 3600, "luis@acme.com")
        message["payload"]["headers"].append({"name": "Subject", "value": "Cambio en Llanos 8"})
        self.run_sync({"n1": message})
        self.assertEqual(self.links()[0]["reason"], "domain+subject")

    def test_personal_email_domains_do_not_match_by_company(self):
        quotes.dashboard_upsert(b64(json.dumps(payload(
            thread="T2", client_email="x@gmail.com", received_at="2026-10-01T10:00:00-05:00"))))
        self.run_sync({"n1": mail("NUEVO", False, self.t0 + 3600, "otro@gmail.com")})
        self.assertEqual(quotes.get_quote("T2")["links"], [])

    def test_unrelated_company_is_ignored_and_not_downloaded(self):
        result, gmail = self.run_sync({"n1": mail("NUEVO", False, self.t0 + 3600, "z@otra.com")})
        self.assertEqual(result["related"], 0)
        self.assertEqual(gmail.gets, 0)

    def test_second_pass_does_not_duplicate_the_proposal(self):
        messages = {"n1": mail("NUEVO", False, self.t0 + 3600, "ana@acme.com")}
        self.run_sync(messages)
        result, _ = self.run_sync(messages)
        self.assertEqual(result["related"], 0)
        self.assertEqual(len(self.links()), 1)

    def test_confirming_leaves_trace_with_author_and_dismissing_hides_it(self):
        self.run_sync({"n1": mail("N1", False, self.t0 + 3600, "Luis <luis@acme.com>"),
                       "n2": mail("N2", False, self.t0 + 7200, "ana@acme.com")})
        first, second = self.links()
        quotes.resolve_link("T1", first["id"], accept=True)
        quotes.resolve_link("T1", second["id"], accept=False)
        self.assertEqual(self.links(), [])
        events = {e["kind"]: e for e in quotes.thread_events("T1")}
        self.assertEqual(events["linked"]["actor_email"], "luis@acme.com")
        self.assertIn("dismissed", events)
        people = quotes.case_people("T1", {"ana@acme.com"})
        self.assertEqual([(p["email"], p["role"]) for p in people],
                         [("luis@acme.com", "Otra persona")])
        self.assertIn("ya se había resuelto", quotes.resolve_link("T1", first["id"], True))

    def test_banner_offers_both_reply_templates_and_form_for_closed_case(self):
        import web
        db = init_db()
        db.execute("UPDATE quotes SET status=? WHERE thread_id='T1'", (QuoteStatus.READY,))
        db.commit()
        db.close()
        self.run_sync({"n1": mail("NUEVO", False, self.t0 + 3600, "Luis <luis@acme.com>")})
        config = {"active_mode": "chatgpt_work", "required_fields": []}
        with patch("web.load_config", return_value=config):
            page = web._render_case("T1")
            verify = web._render_case("T1", template="verify")
        self.assertIn("en otro hilo", page)
        self.assertIn("confirmar recibido", page)
        self.assertIn("validar el cambio de persona", page)
        self.assertIn("Enviar ahora", page)                  # un caso cerrado con aviso admite respuesta
        self.assertIn("autorizado(a) para solicitar cambios", verify)

    def test_closed_case_without_notices_stays_blocked(self):
        db = init_db()
        db.execute("UPDATE quotes SET status=? WHERE thread_id='T1'", (QuoteStatus.READY,))
        db.commit()
        db.close()
        import case_actions
        with self.assertRaises(ValueError):                   # sin aviso de otro hilo no se reabre
            case_actions._case_email_context("T1")


# Cubre: T-06, U-06
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
