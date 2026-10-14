"""Puente Gmail → dashboard (modo chatgpt_work): importación, caché, latido y datos de la persona."""

from tests import _isolate  # noqa: F401  (debe ir primero)

import base64
import re
import json
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch

from anka.servicios import quotes
from anka.servicios import ediciones, work_import
from anka.infra import settings
from anka.web import paginas
from anka.web import pantalla_caso
from anka.cli import work_sync as sync
from anka.infra import work_heartbeat
from anka.servicios import gmail_followup
from anka.infra.store import case_events
from anka.servicios.quotes import connect
from anka.dominio.status import QuoteStatus

AHORA = datetime(2026, 6, 1, 9, 30, 0).astimezone()   # reloj fijo de las pruebas que miden tiempos


class RelojFijo(datetime):
    """`datetime` cuyo `now()` es siempre AHORA (para inyectarlo en el módulo que mide la demora)."""

    @classmethod
    def now(cls, tz=None):
        return AHORA if tz is None else AHORA.astimezone(tz)


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
        return sync.sync_once(gmail, work_import.dashboard_upsert, self.checkpoint, cache)

    def test_imports_draft_then_skips_unchanged_content(self):
        gmail = FakeGmail({"d1": draft_message(payload())})
        self.assertEqual(self.run_sync(gmail)["updated"], 1)
        self.assertEqual(len(quotes.all_quotes()), 1)
        self.assertEqual(self.run_sync(gmail)["updated"], 0)

    def test_the_draft_time_in_gmail_is_measured_against_the_arrival_at_the_dashboard(self):
        # Cubre: T-29
        from anka.infra import store
        mensaje = draft_message(payload())
        mensaje["internalDate"] = str(int((AHORA.timestamp() - 45) * 1000))     # el borrador tiene 45 s
        with patch.object(work_import, "datetime", RelojFijo):                   # llega al dashboard en AHORA
            self.run_sync(FakeGmail({"d1": mensaje}))
        db = store.connect()
        try:
            resumen = db.execute("SELECT summary FROM case_events WHERE thread_id='T1' AND kind='analysis'").fetchone()[0]
        finally:
            db.close()
        self.assertIn("El borrador estaba en Gmail desde las", resumen)
        segundos = int(resumen.split("llegó al dashboard ")[1].split(" s")[0])
        self.assertEqual(segundos, 45)                  # reloj inyectado: no depende de cuánto tarde la prueba

    def test_resaving_a_draft_without_changing_it_is_not_imported_again(self):
        # Cubre: T-29
        gmail = FakeGmail({"d1": draft_message(payload())})
        cache = {}
        self.assertEqual(self.run_sync(gmail, cache)["updated"], 1)
        gmail.items["d1"] = draft_message(payload(), message_id="gm2")
        gmail.items["d1"]["internalDate"] = "99999999"        # Gmail dio otra hora, el contenido es el mismo
        self.assertEqual(self.run_sync(gmail, cache)["updated"], 0)

    def test_without_the_draft_time_the_event_is_written_as_before(self):
        # Cubre: T-29
        from anka.servicios import work_import
        self.assertEqual(work_import._demora_del_puente({"thread_id": "T1"}), "")
        self.assertEqual(work_import._demora_del_puente({"draft_at": "no es una fecha"}), "")

    def test_cache_avoids_downloading_unchanged_drafts(self):
        gmail = FakeGmail({"d1": draft_message(payload())})
        cache = {}
        self.run_sync(gmail, cache)
        self.run_sync(gmail, cache)
        self.assertEqual(gmail.gets, 1)
        gmail.items["d1"] = draft_message(payload(summary="nuevo"), message_id="gm2")
        self.assertEqual(self.run_sync(gmail, cache)["updated"], 1)
        self.assertEqual(gmail.gets, 2)

    def test_invalid_draft_is_rejected_not_a_failure_and_is_not_marked_processed(self):
        # Cubre: T-28
        bad = draft_message(payload(thread="OTRO"))  # thread_id no coincide con el hilo
        result = self.run_sync(FakeGmail({"d1": bad}))
        self.assertEqual((result["updated"], result["failures"], len(result["rejected"])), (0, 0, 1))
        self.assertIn("thread_id", result["rejected"][0]["reason"])
        self.assertEqual(result["rejected"][0]["draft"], "d1")
        self.assertEqual(quotes.all_quotes(), [])

    def test_rejected_draft_is_downloaded_once_and_reviewed_again_only_when_it_changes(self):
        # Cubre: T-28
        gmail = FakeGmail({"d1": draft_message(payload(thread="OTRO"))})
        cache = {}
        for _ in range(3):
            result = self.run_sync(gmail, cache)
            self.assertEqual(len(result["rejected"]), 1)           # se sigue listando, pero no se vuelve a bajar
        self.assertEqual(gmail.gets, 1)
        gmail.items["d1"] = draft_message(payload(), message_id="gm2")   # alguien lo corrigió: mensaje nuevo
        result = self.run_sync(gmail, cache)
        self.assertEqual((gmail.gets, len(result["rejected"]), result["updated"]), (2, 0, 1))
        self.assertEqual(len(quotes.all_quotes()), 1)

    def test_a_draft_with_broken_json_is_rejected_without_stopping_the_others(self):
        # Cubre: T-28
        broken = draft_message(payload(), thread="T2", message_id="gm9")
        texto = "[REVISAR ANTES DE ENVIAR - BORRAR ESTA SECCION]\n" + sync.START + "\n{no es json\n" + sync.END
        broken["payload"]["body"]["data"] = b64(texto)
        result = self.run_sync(FakeGmail({"d1": draft_message(payload()), "d2": broken}))
        self.assertEqual((result["updated"], result["failures"], len(result["rejected"])), (1, 0, 1))

    def test_unresolved_written_as_boolean_is_imported_as_empty(self):
        # Cubre: T-28
        audit = [{"question": "¿Método?", "reason_missing": "no figura", "reviewed_sources": ["correo"],
                  "question_id": "q1", "disposition": "internal_review", "unresolved": True}]
        result = self.run_sync(FakeGmail({"d1": draft_message(payload(question_audit=audit))}))
        self.assertEqual((result["updated"], result["failures"], len(result["rejected"])), (1, 0, 0))
        saved = quotes.all_quotes()[0]["analysis"]["question_audit"]
        self.assertEqual(saved[0]["unresolved"], "")

    def test_upsert_does_not_move_closed_case_back_or_erase_human_data(self):
        work_import.dashboard_upsert(b64(json.dumps(payload())))
        quotes.save_quote_metadata("T1", "Cliente Confirmado", "Empresa privada",
                                   "Infraestructura", "nota")
        db = connect()
        db.execute("UPDATE quotes SET status=? WHERE thread_id='T1'", (QuoteStatus.READY,))
        db.commit()
        db.close()
        work_import.dashboard_upsert(b64(json.dumps(payload(message="m2", summary="otro"))))
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
        work_import.dashboard_upsert(b64(json.dumps(payload(
            status="Faltan datos", received_at="2026-10-01T10:00:00-05:00"))))
        self.t0 = int(datetime.fromisoformat("2026-10-01T10:00:00-05:00").timestamp())

    def events(self):
        db = connect()
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
        db = connect()
        from anka.infra.store import record_event
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
        work_import.dashboard_upsert(b64(json.dumps(payload(
            message="m1", received_at="2026-10-01T10:00:00-05:00"))))

    def reply(self, ref, minutes_after, ago_minutes=0):
        when = datetime.fromisoformat("2026-10-01T10:00:00-05:00") + timedelta(minutes=minutes_after)
        db = connect()
        from anka.infra.store import record_event
        record_event(db, "T1", "received", "gmail", ref, "Correo recibido", when.isoformat())
        db.commit()
        db.close()

    def test_reply_pending_until_analysis_covers_it(self):
        self.reply("m1", 0)                       # el correo original: ya analizado
        self.assertEqual(quotes.get_quote("T1")["reply_pending"], "")
        self.reply("m2", 15)                      # respuesta posterior
        self.assertTrue(quotes.get_quote("T1")["reply_pending"])
        work_import.dashboard_upsert(b64(json.dumps(payload(
            message="m2", received_at="2026-10-01T10:15:00-05:00", summary="con respuesta"))))
        self.assertEqual(quotes.get_quote("T1")["reply_pending"], "")

    def test_small_clock_difference_is_not_a_reply(self):
        self.reply("x1", 1)                       # llegada 1 min después de la fecha del correo
        self.assertEqual(quotes.get_quote("T1")["reply_pending"], "")

    def test_banner_and_chip_show_pending_reply(self):
        self.reply("m2", 15)
        with patch("anka.web.paginas.load_config", return_value={"active_mode": "chatgpt_work", "required_fields": []}), patch("anka.web.pantalla_caso.load_config", return_value={"active_mode": "chatgpt_work", "required_fields": []}):
            page = pantalla_caso.render_case("T1")
            row = paginas.quote_row(quotes.get_quote("T1"))
        self.assertIn("respondió", page)
        self.assertIn("ChatGPT Work", page)       # pasaron más de 30 min desde 2026
        self.assertIn("Respondió", row)

    def test_model_proposal_can_be_adopted_or_dismissed(self):
        ediciones.save_activities("T1", [{"activity": "a", "quantity": "1"}])
        work_import.dashboard_upsert(b64(json.dumps(payload(
            message="m2", activities=[{"activity": "a", "quantity": "2"}, {"activity": "b"}]))))
        proposal = quotes.get_quote("T1")["proposal"]
        self.assertEqual({item["kind"] for item in proposal}, {"nueva", "distinta"})
        quotes.resolve_model_proposal("T1", use_proposal=True)
        stored = quotes.get_quote("T1")
        self.assertEqual([a["activity"] for a in stored["analysis"]["activities"]], ["a", "b"])
        self.assertNotIn("manual_edit", stored["analysis"])
        self.assertEqual(stored["proposal"], [])
        # descartar: la tabla propia se queda y la propuesta desaparece
        ediciones.save_activities("T1", [{"activity": "solo mía"}])
        work_import.dashboard_upsert(b64(json.dumps(payload(message="m3", activities=[{"activity": "z"}]))))
        quotes.resolve_model_proposal("T1", use_proposal=False)
        stored = quotes.get_quote("T1")
        self.assertEqual(stored["analysis"]["activities"], [{"activity": "solo mía"}])
        self.assertEqual(stored["proposal"], [])
        db = connect()
        kinds = [e["kind"] for e in case_events(db, "T1")]
        db.close()
        self.assertEqual(kinds.count("proposal"), 2)


# Cubre: F1-06, T-03, T-21
class RelatedThreadTests(unittest.TestCase):
    """Correos de la misma persona o empresa en otro hilo: se proponen, no se unen solos."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        patcher = patch.object(settings, "DATA_DIR", Path(self.temp.name))
        patcher.start()
        self.addCleanup(patcher.stop)
        self.config_path = self.temp.name
        (Path(self.temp.name) / "config.json").write_text(json.dumps({
            "active_mode": "chatgpt_work", "required_fields": [],
            "own_addresses": ["ankacomercialia@gmail.com"],
            "test_sender_addresses": ["91andresmunoz@gmail.com"],
            "llm": {"provider": "openai"},
        }), encoding="utf-8")
        work_import.dashboard_upsert(b64(json.dumps(payload(
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
        work_import.dashboard_upsert(b64(json.dumps(payload(
            thread="T2", client_email="x@gmail.com", received_at="2026-10-01T10:00:00-05:00"))))
        self.run_sync({"n1": mail("NUEVO", False, self.t0 + 3600, "otro@gmail.com")})
        self.assertEqual(quotes.get_quote("T2")["links"], [])

    def test_unrelated_company_is_ignored_and_not_downloaded(self):
        result, gmail = self.run_sync({"n1": mail("NUEVO", False, self.t0 + 3600, "z@otra.com")})
        self.assertEqual(result["related"], 0)
        self.assertEqual(gmail.gets, 0)

    def test_anka_account_is_not_the_designated_test_sender(self):
        self.assertFalse(gmail_followup._is_test_email(
            "ankacomercialia@gmail.com", "PRUEBA 11 - Mantenimiento GRB/GRC Nuevo"))

    def test_test_sender_is_not_assumed_when_configuration_is_missing(self):
        (Path(self.temp.name) / "config.json").write_text(json.dumps({
            "active_mode": "chatgpt_work", "required_fields": [],
            "own_addresses": ["ankacomercialia@gmail.com"],
            "llm": {"provider": "openai"},
        }), encoding="utf-8")
        self.assertFalse(gmail_followup._is_test_email(
            "91andresmunoz@gmail.com", "PRUEBA 11 - Mantenimiento GRB/GRC Nuevo"))

    def test_own_sender_without_explicit_test_marker_still_creates_normal_prompt(self):
        message = mail("real-new", False, self.t0 + 3600, "Ana <ana@acme.com>")
        message["payload"]["headers"].append({"name": "Subject", "value": "Solicitud GRB/GRC"})
        result, _ = self.run_sync({"n1": message})
        self.assertEqual(result["related"], 1)

    def test_own_sender_with_new_marker_is_treated_as_an_independent_test(self):
        work_import.dashboard_upsert(b64(json.dumps(payload(
            thread="T2", message="m2", client_email="91andresmunoz@gmail.com",
            received_at="2026-10-01T10:00:00-05:00"))))
        message = mail("own-new", False, self.t0 + 3600, "Andrés <91andresmunoz@gmail.com>")
        message["payload"]["headers"].append({"name": "Subject", "value": "Mantenimiento GRB/GRC Nuevo"})
        result, _ = self.run_sync({"n1": message})
        self.assertEqual(result["related"], 0)
        self.assertEqual(quotes.get_quote("T2")["links"], [])

    def test_external_sender_with_test_marker_still_creates_normal_prompt(self):
        message = mail("external-test", False, self.t0 + 3600, "Ana <ana@acme.com>")
        message["payload"]["headers"].append({"name": "Subject", "value": "PRUEBA 12 - Cotización"})
        result, _ = self.run_sync({"n1": message})
        self.assertEqual(result["related"], 1)

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
        db = connect()
        db.execute("UPDATE quotes SET status=? WHERE thread_id='T1'", (QuoteStatus.READY,))
        db.commit()
        db.close()
        self.run_sync({"n1": mail("NUEVO", False, self.t0 + 3600, "Luis <luis@acme.com>")})
        config = {"active_mode": "chatgpt_work", "required_fields": []}
        with patch("anka.web.paginas.load_config", return_value=config), patch("anka.web.pantalla_caso.load_config", return_value=config):
            page = pantalla_caso.render_case("T1")
            verify = pantalla_caso.render_case("T1", template="verify")
        self.assertIn("en otro hilo", page)
        self.assertIn("Preparar respuesta", page)
        self.assertIn("Validar contacto", page)
        self.assertIn("Enviar al cliente", page)                # un caso cerrado con aviso admite respuesta
        self.assertIn("autorizado(a) para solicitar cambios", verify)

    def test_closed_case_without_notices_stays_blocked(self):
        db = connect()
        db.execute("UPDATE quotes SET status=? WHERE thread_id='T1'", (QuoteStatus.READY,))
        db.commit()
        db.close()
        from anka.servicios import case_actions
        with self.assertRaises(ValueError):                   # sin aviso de otro hilo no se reabre
            case_actions.case_email_context("T1")


# Cubre: T-06, U-06
class HeartbeatTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        patcher = patch.object(settings, "DATA_DIR", Path(self.temp.name))
        patcher.start()
        self.addCleanup(patcher.stop)
        reloj = patch.object(work_heartbeat, "_now", return_value=AHORA)   # la edad del latido se mide contra AHORA
        reloj.start()
        self.addCleanup(reloj.stop)

    def beat(self, seconds_ago, **extra):
        data = {"at": (AHORA - timedelta(seconds=seconds_ago)
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

    def test_rejected_drafts_give_their_own_state_not_an_error(self):
        # Cubre: T-28
        self.beat(20, rejected=[{"draft": "d1", "thread": "t1", "reason": "x debe ser de tipo string."}])
        state, text = work_heartbeat.bridge_state()
        self.assertEqual(state, "attention")
        self.assertIn("1 borrador(es) de Work no cumplen el contrato", text)
        self.assertEqual(work_heartbeat.rejected_drafts()[0]["draft"], "d1")
        self.beat(20, failures=1, rejected=[{"draft": "d1", "thread": "t1", "reason": "x"}])
        self.assertEqual(work_heartbeat.bridge_state()[0], "error")      # un fallo real manda sobre el rechazo
        self.beat(600, rejected=[{"draft": "d1", "thread": "t1", "reason": "x"}])
        self.assertEqual(work_heartbeat.bridge_state()[0], "stopped")    # y un puente detenido también
        self.beat(20)
        self.assertEqual(work_heartbeat.bridge_state()[0], "ok")
        self.assertEqual(work_heartbeat.rejected_drafts(), [])

    def test_the_summary_lists_each_rejected_draft_with_its_reason_and_what_to_do(self):
        # Cubre: T-28
        from anka.web import paginas
        self.assertEqual(str(paginas._work_rejected_panel()), "")        # sin rechazados, no hay panel
        self.beat(20, rejected=[{"draft": "r-123", "thread": "hilo-sin-caso",
                                 "reason": "question_audit[0].unresolved debe ser de tipo string."}])
        panel = str(paginas._work_rejected_panel())
        self.assertIn("Un borrador de Work no se pudo leer", panel)
        self.assertIn("Borrador r-123", panel)
        self.assertIn("hilo hilo-sin-caso (aún sin caso)", panel)
        self.assertIn("question_audit[0].unresolved debe ser de tipo string.", panel)
        self.assertIn("se revisa solo cuando el borrador cambie", panel)

    def test_the_chip_and_health_check_treat_the_attention_state_as_a_live_bridge(self):
        # Cubre: T-28
        from anka.servicios import salud
        base = Path(__file__).resolve().parents[1] / "anka" / "web" / "templates"
        layout = (base / "layout.html").read_text(encoding="utf-8")
        self.assertIn("attention: 'Puente activo · borrador por revisar'", layout)
        self.assertIn('.bridge-chip[data-state="attention"]', (base / "style.css").read_text(encoding="utf-8"))
        with patch("anka.servicios.salud.bridge_state", return_value=("attention", "activo, con un borrador")):
            self.assertEqual(salud._puente_work("chatgpt_work")[0], salud.OK)

    def test_write_then_read_roundtrip(self):
        work_heartbeat.write_heartbeat(60, drafts=2, updated=1)
        self.assertEqual(work_heartbeat.read_heartbeat()["drafts"], 2)
        work_heartbeat.write_heartbeat(60, rejected=[{"draft": "d", "thread": "t", "reason": "r"}] * 15)
        self.assertEqual(len(work_heartbeat.rejected_drafts()), 10)      # tope: el latido no crece sin límite
        self.assertFalse(work_heartbeat.other_bridge_running())  # es el propio proceso


if __name__ == "__main__":
    unittest.main()
