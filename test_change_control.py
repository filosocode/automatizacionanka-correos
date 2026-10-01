"""Código de la oferta (T / T&E), control de cambios y evidencia del historial."""

import _isolate  # noqa: F401  (debe ir primero)

import base64
import json
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

import case_actions
import change_control
import gmail_followup
import quotes
import settings
import web
from quotes import init_db
from status import QuoteStatus
from store import record_event, verify_chain
from test_work_sync import FollowupGmail, _Exec, b64, mail, payload

CONFIG = {"active_mode": "chatgpt_work", "required_fields": []}


class CodedGmail(FollowupGmail):
    """Como FollowupGmail, pero entrega el correo completo (format=raw) con su cuerpo."""

    def get(self, id, format="metadata", **_):
        self.gets += 1
        message = self.messages_[id]
        if format == "raw":
            text = f"Subject: {message.get('subject', '')}\r\nFrom: x\r\n\r\n{message.get('body', '')}"
            return _Exec({"raw": base64.urlsafe_b64encode(text.encode()).decode()})
        return _Exec(message)


def coded_mail(thread, who, when, body="", subject="Cambio", sent=False):
    message = mail(thread, sent, when, who)
    message["body"], message["subject"] = body, subject
    message["snippet"] = body[:100]
    return message


class Base(unittest.TestCase):
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

    def assign(self, number="26145"):
        change_control.assign_code("T1", number)
        db = init_db()
        db.execute("UPDATE quotes SET code_assigned_at='2026-10-01T10:30:00-05:00' WHERE thread_id='T1'")
        db.commit()
        db.close()

    def sync(self, messages):
        gmail = CodedGmail(messages)
        return gmail_followup.sync_thread_activity(gmail, set())

    def changes(self):
        return quotes.case_change_log("T1")


class CodeTests(unittest.TestCase):
    def test_codes_are_found_in_any_common_spelling(self):
        for text in ("T26145", "T&E26145", "TE26145", "Ref: T-26145", "T&E 26145", "re: t26145 ok"):
            self.assertEqual(change_control.find_codes(text), {"26145"}, text)
        for text in ("T2614", "hora 26145", "T261455", "Total 12345"):
            self.assertEqual(change_control.find_codes(text), set(), text)
        self.assertTrue(change_control.mentions_code("ver T&E26145", "26145"))
        self.assertFalse(change_control.mentions_code("ver T26146", "26145"))

    def test_required_messages_follow_the_agreed_matrix(self):
        rm = change_control.required_messages
        self.assertEqual(rm(True, True), [("ack", "sender")])
        self.assertEqual(rm(True, False), [("ack", "sender"), ("notify_contact", "contact")])
        self.assertEqual(rm(False, True), [("ask_code", "sender")])
        self.assertEqual(rm(False, False), [("ask_code", "sender"), ("notify_contact", "contact")])


class LifecycleTests(Base):
    def test_assign_validates_and_records(self):
        with self.assertRaises(ValueError):
            change_control.assign_code("T1", "123")
        self.assertEqual(change_control.suggest_number(2026), "26001")
        self.assign()
        stored = quotes.get_quote("T1")
        self.assertEqual((stored["quote_code"], stored["code_stage"]), ("26145", "T"))
        self.assertEqual(stored["contact_email"], "ana@acme.com")
        self.assertEqual(change_control.suggest_number(2026), "26146")
        with self.assertRaises(ValueError):
            change_control.assign_code("T1", "26150")      # ya tiene código
        self.assertIn("code_assigned", {e["kind"] for e in quotes.thread_events("T1")})

    def test_code_cannot_be_reused_and_can_be_promoted(self):
        self.assign()
        quotes.dashboard_upsert(b64(json.dumps(payload(thread="T2"))))
        with self.assertRaises(ValueError):
            change_control.assign_code("T2", "T&E26145")
        change_control.promote_to_te("T1")
        self.assertEqual(quotes.get_quote("T1")["code_stage"], "TE")
        self.assertIn("ya figuraba", change_control.promote_to_te("T1"))


class ChangeDetectionTests(Base):
    def test_contact_citing_the_code_is_a_change_with_code(self):
        self.assign()
        self.sync({"m1": coded_mail("T1", "Ana <ana@acme.com>", self.t0 + 7200,
                                    "Cambiar plazo a 30 días. Ref T&E26145")})
        change = self.changes()[0]
        self.assertEqual((change["has_code"], change["is_contact"]), (1, 1))
        self.assertTrue(list(Path(self.temp.name, "evidence", "T1").glob("m1.eml")))

    def test_other_person_without_code_is_flagged(self):
        self.assign()
        self.sync({"m1": coded_mail("T1", "Luis <luis@acme.com>", self.t0 + 7200, "Cambiar plazo a 30 días")})
        change = self.changes()[0]
        self.assertEqual((change["has_code"], change["is_contact"], change["same_company"]), (0, 0, 1))

    def test_messages_before_the_code_are_not_changes(self):
        self.assign()
        self.sync({"m1": coded_mail("T1", "Ana <ana@acme.com>", self.t0 + 60, "Una duda")})
        self.assertEqual(self.changes(), [])

    def test_case_without_code_never_creates_changes(self):
        self.sync({"m1": coded_mail("T1", "Luis <luis@acme.com>", self.t0 + 7200, "Cambiar plazo")})
        self.assertEqual(self.changes(), [])

    def test_confirmed_link_in_a_coded_case_becomes_a_change(self):
        self.assign()
        self.sync({"n1": coded_mail("NUEVO", "Luis <luis@acme.com>", self.t0 + 7200,
                                    "Ahora son 30 días. T26145")})
        link = quotes.get_quote("T1")["links"][0]
        self.assertEqual(link["has_code"], 1)
        quotes.resolve_link("T1", link["id"], accept=True)
        self.assertEqual(self.changes()[0]["has_code"], 1)

    def test_decisions_leave_trace_and_drive_the_todo_list(self):
        self.assign()
        self.sync({"m1": coded_mail("T1", "Luis <luis@acme.com>", self.t0 + 7200, "Cambiar plazo")})
        change = self.changes()[0]
        with patch("web.load_config", return_value=CONFIG):
            page = web._render_case("T1")
        self.assertIn("escribió después de la T", page)
        self.assertIn("No cita la T", page)
        change_control.resolve_change("T1", change["id"], True)
        with patch("web.load_config", return_value=CONFIG):
            todo = web._render_case("T1")
        self.assertIn("Pedir el código a", todo)
        self.assertIn("Avisar al contacto registrado", todo)
        change_control.mark_change_sent("T1", change["id"], "ask_code", "luis@acme.com")
        change_control.mark_change_sent("T1", change["id"], "notify_contact", "ana@acme.com")
        with patch("web.load_config", return_value=CONFIG):
            self.assertNotIn("faltan correos por enviar", web._render_case("T1"))
        kinds = {e["kind"] for e in quotes.thread_events("T1")}
        self.assertTrue({"change_candidate", "change_confirmed", "change_reply"} <= kinds)


class EmailTests(Base):
    def test_templates_never_reveal_the_code_to_other_people(self):
        self.assign()
        self.sync({"m1": coded_mail("T1", "Luis <luis@acme.com>", self.t0 + 7200, "Cambiar plazo")})
        review = quotes.get_quote("T1")
        change = review["changes"][0]
        received = web._reply_template("received", review)
        ask = web._reply_template("ask_code", review, change)
        notify = web._reply_template("notify_contact", review, change)
        self.assertIn("T26145", received)                  # el contacto sí lo recibe
        self.assertNotIn("26145", ask)                      # quien no es contacto, no
        self.assertIn("luis@acme.com", notify)
        self.assertIn("correo registrado", received)

    def test_dashboard_only_writes_to_people_already_in_the_case(self):
        self.assign()
        self.sync({"m1": coded_mail("T1", "Luis <luis@acme.com>", self.t0 + 7200, "Cambiar plazo")})
        quote = quotes.get_quote("T1")
        self.assertEqual(case_actions._recipient("T1", quote, "luis@acme.com"), "luis@acme.com")
        self.assertEqual(case_actions._recipient("T1", quote, ""), quote["recipient"])
        with self.assertRaises(ValueError):
            case_actions._recipient("T1", quote, "desconocido@otra.com")

    def test_closed_case_with_code_accepts_replies_with_prepared_text(self):
        db = init_db()
        db.execute("UPDATE quotes SET status=? WHERE thread_id='T1'", (QuoteStatus.READY,))
        db.commit()
        db.close()
        self.assign()
        with patch("web.load_config", return_value=CONFIG):
            page = web._render_case("T1", template="received")
        self.assertIn("Enviar ahora", page)
        self.assertIn("Hemos generado el código T26145", page)


class EvidenceTests(Base):
    def test_chain_detects_tampering_and_removal(self):
        db = init_db()
        for n in range(3):
            record_event(db, "T1", "x", "dashboard", f"r{n}", f"hecho {n}")
        db.commit()
        self.assertTrue(verify_chain(db, "T1")[0])
        db.execute("UPDATE case_events SET summary='alterado' WHERE ref='r1'")
        self.assertFalse(verify_chain(db, "T1")[0])
        db.rollback()
        db.execute("DELETE FROM case_events WHERE ref='r1'")
        self.assertFalse(verify_chain(db, "T1")[0])
        db.close()

    def test_deleting_a_case_keeps_its_history_in_the_archive(self):
        self.assign()
        self.sync({"m1": coded_mail("T1", "Luis <luis@acme.com>", self.t0 + 7200, "Cambiar plazo")})
        quotes.delete_quotes(["T1"])
        self.assertIsNone(quotes.get_quote("T1"))
        self.assertEqual([item["thread_id"] for item in quotes.archived_threads()], ["T1"])
        self.assertEqual(len(self.changes()), 1)             # la solicitud de cambio también se conserva
        self.assertTrue(quotes.chain_status("T1")[0])
        acta = web._render_acta("T1")
        self.assertIn("Cambiar plazo", acta)
        self.assertIn("Verificada", acta)
        self.assertIn("deleted", {e["kind"] for e in quotes.thread_events("T1")})

    def test_person_actions_are_recorded(self):
        quotes.save_activities("T1", [{"activity": "a"}])
        kinds = {e["kind"] for e in quotes.thread_events("T1")}
        self.assertIn("table_edit", kinds)

    def test_acta_for_live_case_lists_people_changes_and_hashes(self):
        self.assign()
        self.sync({"m1": coded_mail("T1", "Luis <luis@acme.com>", self.t0 + 7200, "Cambiar plazo a 30")})
        acta = web._render_acta("T1")
        for expected in ("Acta de cambios", "luis@acme.com", "No cita la T", "T26145", "Historial completo"):
            self.assertIn(expected, acta)


class VersionTests(Base):
    """El comercial edita la tabla: queda historial con diferencias, sin correos ni validaciones."""

    def edit(self, rows, reason=""):
        return quotes.save_activities("T1", rows, reason)

    def test_totals_parser_handles_spanish_numbers_and_compound_units(self):
        rows = [{"quantity": "544,5", "unit": "ha"}, {"quantity": "61 ha / 12,209 km", "unit": "ha / km"},
                {"quantity": "1.250", "unit": "m"}]
        self.assertEqual(quotes.activity_totals(rows), {"ha": 605.5, "km": 12.209, "m": 1250.0})

    def test_first_edit_keeps_the_model_table_and_describes_what_changed(self):
        quotes.save_activities("T1", [{"activity": "a", "quantity": "10", "unit": "ha"},
                                      {"activity": "b", "quantity": "5", "unit": "km"}])
        message = self.edit([{"activity": "a", "quantity": "8", "unit": "ha"}, {"activity": "c"}], "redundante")
        versions = quotes.table_versions("T1")
        self.assertEqual([v["source"] for v in versions], ["model", "dashboard", "dashboard"])
        self.assertIn("+1 fila(s): c", message)
        self.assertIn("−1 fila(s): b", message)
        self.assertIn("1 modificada(s): a (quantity)", message)
        self.assertIn("totales: ha 10.0 → 8.0", message)
        self.assertEqual(versions[-1]["reason"], "redundante")
        original = json.loads(versions[0]["activities_json"])
        self.assertEqual(original[0]["activity"], "a")

    def test_unknown_reason_is_ignored_and_reason_is_optional(self):
        self.edit([{"activity": "a"}], "cualquier cosa")
        self.assertEqual(quotes.table_versions("T1")[-1]["reason"], "")

    def test_internal_edits_send_nothing_and_ask_for_nothing(self):
        self.assign()
        self.edit([{"activity": "a", "quantity": "99", "unit": "ha"}])
        self.assertEqual(self.changes(), [])
        self.assertEqual(quotes.get_quote("T1")["links"], [])
        kinds = {e["kind"] for e in quotes.thread_events("T1")}
        self.assertNotIn("change_reply", kinds)
        self.assertNotIn("scope_update_sent", kinds)
        self.assertIn("table_edit", kinds)

    def test_model_updates_are_versioned_but_never_override_a_manual_table(self):
        quotes.dashboard_upsert(b64(json.dumps(payload(message="m2", activities=[{"activity": "z"}]))))
        self.assertEqual([v["source"] for v in quotes.table_versions("T1")], ["model", "model"])
        self.edit([{"activity": "mía"}])
        quotes.dashboard_upsert(b64(json.dumps(payload(message="m3", activities=[{"activity": "otra"}]))))
        self.assertEqual(quotes.table_versions("T1")[-1]["source"], "dashboard")  # la propuesta no versiona

    def test_adopting_the_proposal_is_a_version(self):
        self.edit([{"activity": "mía"}])
        quotes.dashboard_upsert(b64(json.dumps(payload(message="m3", activities=[{"activity": "otra"}]))))
        quotes.resolve_model_proposal("T1", use_proposal=True)
        self.assertIn("propuesta", quotes.table_versions("T1")[-1]["summary"])

    def test_optional_notice_to_client_is_offered_and_remembered(self):
        self.edit([{"activity": "a"}])
        with patch("web.load_config", return_value=CONFIG):
            page = web._render_case("T1")
        self.assertIn("Solo está en tu historial", page)
        self.assertIn("Preparar un correo de actualización", page)
        db = init_db()
        record_event(db, "T1", "scope_update_sent", "dashboard", "x", "Avisaste")
        db.commit()
        db.close()
        with patch("web.load_config", return_value=CONFIG):
            self.assertIn("Ya avisaste al cliente", web._render_case("T1"))

    def test_notice_can_be_prepared_even_for_a_closed_case(self):
        db = init_db()
        db.execute("UPDATE quotes SET status=? WHERE thread_id='T1'", (QuoteStatus.READY,))
        db.commit()
        db.close()
        self.edit([{"activity": "a"}])
        with patch("web.load_config", return_value=CONFIG):
            page = web._render_case("T1", template="scope_update")
        self.assertIn("Actualizamos la tabla de actividades", page)
        self.assertIn("Enviar ahora", page)

    def test_acta_lists_versions_and_marks_internal_edits_after_the_code(self):
        self.edit([{"activity": "antes"}])
        self.assign()
        db = init_db()
        db.execute("UPDATE table_versions SET created_at='2026-10-01T09:00:00-05:00'")
        db.commit()
        db.close()
        self.edit([{"activity": "después", "quantity": "3", "unit": "ha"}], "correccion")
        acta = web._render_acta("T1")
        self.assertIn("Versiones de la tabla de actividades", acta)
        self.assertIn("Edición interna posterior a la T", acta)
        self.assertIn("Corrección mía", acta)
        self.assertEqual(acta.count("Edición interna posterior a la T"), 1)


if __name__ == "__main__":
    unittest.main()
