"""Código de la oferta (T / T&E), control de cambios y evidencia del historial."""

from tests import _isolate  # noqa: F401  (debe ir primero)

import base64
import json
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

from anka.servicios import case_actions
from anka.servicios import change_control
from anka.servicios import gmail_followup
from anka.servicios import quotes
from anka.infra import settings
from anka.web import server as web
from anka.servicios.quotes import init_db
from anka.dominio.status import QuoteStatus
from anka.infra.store import record_event, verify_chain
from tests.test_work_sync import FollowupGmail, _Exec, b64, draft_message, mail, payload

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
    message["payload"]["mimeType"] = "text/plain"
    message["payload"]["body"] = {"data": base64.urlsafe_b64encode(body.encode()).decode()}
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


# Cubre: T-03, R11
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


# Cubre: T-03
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


# Cubre: T-03, R11, F1-06
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
        with patch("anka.web.server.load_config", return_value=CONFIG):
            page = web._render_case("T1")
        self.assertIn("escribió después de la T", page)
        self.assertIn("No cita la T", page)
        change_control.resolve_change("T1", change["id"], True)
        with patch("anka.web.server.load_config", return_value=CONFIG):
            todo = web._render_case("T1")
        self.assertIn("Pedir el código a", todo)
        self.assertIn("Avisar al contacto registrado", todo)
        change_control.mark_change_sent("T1", change["id"], "ask_code", "luis@acme.com")
        change_control.mark_change_sent("T1", change["id"], "notify_contact", "ana@acme.com")
        with patch("anka.web.server.load_config", return_value=CONFIG):
            self.assertNotIn("faltan correos por enviar", web._render_case("T1"))
        kinds = {e["kind"] for e in quotes.thread_events("T1")}
        self.assertTrue({"change_candidate", "change_confirmed", "change_reply"} <= kinds)


# Cubre: T-03
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
        with patch("anka.web.server.load_config", return_value=CONFIG):
            page = web._render_case("T1", template="received")
        self.assertIn("Enviar ahora", page)
        self.assertIn("Hemos generado el código T26145", page)


# Cubre: T-04
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


# Cubre: F1-03, T-04
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
        with patch("anka.web.server.load_config", return_value=CONFIG):
            page = web._render_case("T1")
        self.assertIn("Solo está en tu historial", page)
        self.assertIn("Preparar un correo de actualización", page)
        db = init_db()
        record_event(db, "T1", "scope_update_sent", "dashboard", "x", "Avisaste")
        db.commit()
        db.close()
        with patch("anka.web.server.load_config", return_value=CONFIG):
            self.assertIn("Ya avisaste al cliente", web._render_case("T1"))

    def test_notice_can_be_prepared_even_for_a_closed_case(self):
        db = init_db()
        db.execute("UPDATE quotes SET status=? WHERE thread_id='T1'", (QuoteStatus.READY,))
        db.commit()
        db.close()
        self.edit([{"activity": "a"}])
        with patch("anka.web.server.load_config", return_value=CONFIG):
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


# Cubre: T-01, F1-04, F1-05
class TechnicalReviewFlowTests(Base):
    """Un caso de Work con dependencias pendientes no puede quedar bloqueado sin salida."""

    def upsert_with_dependency(self, thread="T9", status="Faltan datos"):
        quotes.dashboard_upsert(b64(json.dumps(payload(
            thread=thread, status=status, scope_dependencies=[
                {"description": "Validar la geometría del KML", "source": "a.kml", "status": "pendiente"}]))))

    def test_pending_dependencies_send_the_case_to_technical_review_like_the_pilot(self):
        for status in ("Faltan datos", "Borrador preparado", "Alcance completo"):
            with self.subTest(status=status):
                self.upsert_with_dependency(status=status)
                self.assertEqual(quotes.get_quote("T9")["status"], QuoteStatus.REVIEW)

    def test_the_person_can_decide_and_then_send_the_questions(self):
        self.upsert_with_dependency()
        with patch("anka.web.server.load_config", return_value=CONFIG):
            page = web._render_case("T9")
        self.assertIn("Decisión de la revisión técnica", page)
        self.assertNotIn('formaction="/send"', page)                 # primero decide una persona
        quotes.resolve_technical_review("T9", "ask_client", "¿Qué precisión requieren?")
        self.assertEqual(quotes.get_quote("T9")["status"], QuoteStatus.REVIEW_PENDING)
        with patch("anka.web.server.load_config", return_value=CONFIG):
            self.assertIn('formaction="/send"', web._render_case("T9"))   # ya puede enviar

    def test_questions_from_the_analysis_are_prefilled_in_the_decision_box(self):
        self.upsert_with_dependency()
        with patch("anka.web.server.load_config", return_value={"active_mode": "chatgpt_work",
                                                    "required_fields": ["ubicación"]}):
            page = web._render_case("T9")
        box = page.split('name="questions"')[1].split("</textarea>")[0]
        self.assertIn("ubicaci", box.casefold())                      # la pregunta viene escrita
        self.assertIn("Son las preguntas que dejó el análisis", page)

    def test_accepted_case_has_a_blank_reply_box_with_an_optional_acknowledgement(self):
        self.upsert_with_dependency()
        quotes.resolve_technical_review("T9", "sufficient")
        with patch("anka.web.server.load_config", return_value=CONFIG):
            page = web._render_case("T9")
            acuse = web._render_case("T9", template="acuse")
        box = page.split('name="body"')[1].split("</textarea>")[0]
        self.assertEqual(box.split(">", 1)[1].strip(), "")            # en blanco
        self.assertIn("usa un acuse de recibo sugerido", page)
        self.assertIn("Confirmamos que la hemos recibido", acuse)     # el acuse sigue disponible

    def test_a_case_with_questions_still_gets_the_suggested_text(self):
        quotes.dashboard_upsert(b64(json.dumps(payload(thread="T8", missing_data=["Crítico: plano del predio"]))))
        with patch("anka.web.server.load_config", return_value={"active_mode": "chatgpt_work",
                                                    "required_fields": ["ubicación"]}):
            page = web._render_case("T8")
        box = page.split('name="body"')[1].split("</textarea>")[0]
        self.assertIn("confirmar lo siguiente", box)

    def test_sufficient_decision_unblocks_the_case_too(self):
        self.upsert_with_dependency()
        quotes.resolve_technical_review("T9", "sufficient")
        self.assertEqual(quotes.get_quote("T9")["status"], QuoteStatus.READY_REVIEW)


# Cubre: F1-05, T-02
class WorkDraftTests(Base):
    """El borrador que deja Work aparece en el dashboard y se consume al enviar."""

    def work_case(self, thread="W1", **extra):
        quotes.dashboard_upsert(b64(json.dumps(payload(
            thread=thread, status="Faltan datos", draft_reply="Hola Ana,\n\n¿Podría confirmar el datum?\n\nSaludos",
            draft_id="D-work", missing_data=["Crítico: confirmar el datum.", "Crítico interno: revisar el KML.",
                                              "Recomendable: confirmar fecha de inicio."], **extra))))

    def test_work_draft_text_fills_the_reply_box_and_questions_fill_the_decision_box(self):
        self.work_case(scope_dependencies=[{"description": "KML", "source": "a.kml", "status": "pendiente"}])
        with patch("anka.web.server.load_config", return_value=CONFIG):
            page = web._render_case("W1")
        questions = page.split('name="questions"')[1].split("</textarea>")[0]
        self.assertIn("Confirmar el datum.", questions)
        self.assertIn("Confirmar fecha de inicio.", questions)
        self.assertNotIn("KML", questions)                           # lo interno no se le pregunta al cliente
        quotes.resolve_technical_review("W1", "ask_client", "¿Datum?")
        with patch("anka.web.server.load_config", return_value=CONFIG):
            page = web._render_case("W1")
        box = page.split('name="body"')[1].split("</textarea>")[0]
        self.assertIn("Podría confirmar el datum", box.replace("¿", ""))   # el borrador de Work

    def test_sending_consumes_the_work_draft_instead_of_leaving_it_orphaned(self):
        self.work_case()
        calls = []

        class Drafts:
            def update(self, **kw):
                calls.append(("update", kw["id"]))
                return _Exec({})

            def send(self, **kw):
                calls.append(("send", kw["body"]["id"]))
                return _Exec({"id": "sent-1"})

        class Users:
            def drafts(self):
                return Drafts()

        class Gmail:
            def users(self):
                return Users()

        quote = quotes.get_quote("W1")
        context = (quote, Gmail(), {"id": "W1", "messages": []}, set())
        with patch.object(case_actions, "case_email_context", return_value=context), \
                patch.object(case_actions, "reply_email", return_value=type("E", (), {"as_bytes": lambda self: b"x"})()):
            case_actions.send_dashboard_reply("W1", "Hola, texto final")
        self.assertEqual(calls, [("update", "D-work"), ("send", "D-work")])
        self.assertNotIn("work_reply", quotes.get_quote("W1")["analysis"])   # ya no se vuelve a proponer

    def test_a_missing_work_draft_falls_back_to_a_new_message(self):
        self.work_case()

        class NotFound(Exception):
            resp = type("R", (), {"status": 404})()

        class Drafts:
            def update(self, **kw):
                raise NotFound()

        class Messages:
            def send(self, **kw):
                return _Exec({"id": "sent-2"})

        class Users:
            def drafts(self):
                return Drafts()

            def messages(self):
                return Messages()

        class Gmail:
            def users(self):
                return Users()

        context = (quotes.get_quote("W1"), Gmail(), {"id": "W1", "messages": []}, set())
        with patch.object(case_actions, "case_email_context", return_value=context), \
                patch.object(case_actions, "reply_email", return_value=type("E", (), {"as_bytes": lambda self: b"x"})()):
            self.assertIn("Correo enviado", case_actions.send_dashboard_reply("W1", "Hola"))


# Cubre: T-07
class CsrfTokenTests(Base):
    def test_token_survives_dashboard_restarts(self):
        first = web._persistent_csrf_token()
        self.assertGreaterEqual(len(first), 32)
        self.assertEqual(web._persistent_csrf_token(), first)          # un reinicio no lo cambia
        Path(self.temp.name, "csrf_token").unlink()
        self.assertNotEqual(web._persistent_csrf_token(), first)        # sin archivo se genera uno nuevo


# Cubre: R12, T-01, U-06
class RowStateAndStatusFlowTests(Base):
    """R12 (estado de las filas) y el avance de un caso tras la respuesta del cliente."""

    def test_state_text_is_normalized_to_one_of_three_values(self):
        from anka.dominio.activities import ESTADOS, normalize_estado
        cases = {
            "confirmado": "confirmado", "Confirmado": "confirmado",
            "confirmado en fuentes; revisión pendiente": "pendiente",
            "confirmado; soporte pendiente": "pendiente",
            "requiere aclaración": "pendiente", "pendiente": "pendiente", "": "pendiente",
            "contradiccion": "contradictorio", "contradictorio": "contradictorio",
        }
        for text, expected in cases.items():
            self.assertEqual(normalize_estado(text), expected, text)
        self.assertEqual(set(cases.values()), set(ESTADOS))

    def test_work_rows_with_free_text_states_arrive_normalized(self):
        quotes.dashboard_upsert(b64(json.dumps(payload(thread="E1", activities=[
            {"activity": "a", "estado": "confirmado en fuentes; revisión pendiente"},
            {"activity": "b", "estado": "confirmado"}]))))
        states = [r["estado"] for r in quotes.get_quote("E1")["analysis"]["activities"]]
        self.assertEqual(states, ["pendiente", "confirmado"])

    def test_rules_file_lists_the_same_states_as_the_dashboard(self):
        from anka.dominio.activities import ESTADOS
        text = Path("skills/anka-cotizaciones-correo/references/reglas_operativas.md").read_text(encoding="utf-8")
        block = json.loads(text.split("```json reglas\n")[1].split("\n```")[0])
        self.assertEqual(tuple(block["actividades"]["estados"]), ESTADOS)

    def set_status(self, status):
        db = init_db()
        db.execute("UPDATE quotes SET status=? WHERE thread_id='T1'", (status,))
        db.commit()
        db.close()

    def test_a_new_client_message_moves_a_case_that_was_waiting_after_our_reply(self):
        self.set_status(QuoteStatus.RESPONSE_SENT)
        quotes.dashboard_upsert(b64(json.dumps(payload(message="m2", status="Alcance completo"))))
        self.assertEqual(quotes.get_quote("T1")["status"], QuoteStatus.READY_REVIEW)

    def test_re_analysing_the_same_message_does_not_move_a_sent_case(self):
        self.set_status(QuoteStatus.RESPONSE_SENT)
        quotes.dashboard_upsert(b64(json.dumps(payload(message="m1", status="Alcance completo"))))
        self.assertEqual(quotes.get_quote("T1")["status"], QuoteStatus.RESPONSE_SENT)

    def test_closed_cases_are_still_protected_from_new_drafts(self):
        self.set_status(QuoteStatus.READY)
        quotes.dashboard_upsert(b64(json.dumps(payload(message="m2", status="Faltan datos"))))
        self.assertEqual(quotes.get_quote("T1")["status"], QuoteStatus.READY)


# Cubre: U-01, U-02, U-03
class GuidedFlowTests(Base):
    """La pantalla del caso sigue un hilo: leer, revisar, responder y, solo con la información validada, emitir la T."""

    def page(self, thread="T1", **kw):
        with patch("anka.web.server.load_config", return_value=CONFIG):
            return web._render_case(thread, **kw)

    def set_status(self, status):
        db = init_db()
        db.execute("UPDATE quotes SET status=? WHERE thread_id='T1'", (status,))
        db.commit()
        db.close()

    def test_the_t_is_only_offered_once_the_information_is_validated(self):
        self.set_status(QuoteStatus.REVIEW_PENDING)
        self.assertNotIn("Emitir la T", self.page())                     # aún se está aclarando
        self.set_status(QuoteStatus.READY_REVIEW)
        page = self.page()
        self.assertIn("Listo para cotizar: emitir la T", page)
        self.assertIn("Información validada: puedes emitir la T", page)    # el aviso de arriba lo guía

    def test_a_manual_sufficient_decision_also_validates(self):
        quotes.dashboard_upsert(b64(json.dumps(payload(
            thread="T5", scope_dependencies=[{"description": "KML", "source": "a", "status": "pendiente"}]))))
        self.assertNotIn("Emitir la T", self.page("T5"))
        quotes.resolve_technical_review("T5", "sufficient")
        self.assertIn("Emitir la T", self.page("T5"))

    def test_the_stepper_shows_where_the_case_is(self):
        page = self.page()
        self.assertIn("1. Revisar y aclarar", page)
        self.assertIn("process-step current", page)
        self.set_status(QuoteStatus.READY_REVIEW)
        self.assign()
        page = self.page()
        self.assertIn("T26145 · cotizando", page)
        self.assertNotIn("Listo para cotizar: emitir la T", page)        # ya se emitió

    def test_sections_follow_a_top_to_bottom_thread(self):
        self.set_status(QuoteStatus.READY_REVIEW)
        page = self.page()
        order = ["Conversación con el cliente", "Actividades detectadas", "Respuesta al cliente",
                 "Listo para cotizar: emitir la T", "Detalles y trazabilidad del caso", "Seguimiento"]
        positions = [page.index(title) for title in order]
        self.assertEqual(positions, sorted(positions))

    def test_conversation_shows_what_the_client_wrote(self):
        self.sync({"m1": coded_mail("T1", "Ana <ana@acme.com>", self.t0 + 600,
                                    "Les confirmo el datum MAGNA-SIRGAS y las coordenadas.")})
        page = self.page()
        self.assertIn("Conversación con el cliente", page)
        self.assertIn("datum MAGNA-SIRGAS", page)
        self.assertEqual(len(quotes.case_conversation("T1")), 1)

    def test_a_case_with_a_pending_reply_says_so_in_its_label(self):
        db = init_db()
        from anka.infra.store import record_event as rec
        rec(db, "T1", "received", "gmail", "r9", "x", "2026-10-01T12:00:00-05:00", "ana@acme.com", "Ana")
        db.commit()
        db.close()
        self.assertIn("Cliente respondió · por analizar", self.page())
        self.assertIn("Cliente respondió · por analizar", web._quote_row(quotes.get_quote("T1")))


# Cubre: T-02
class WorkStatusSpellingTests(unittest.TestCase):
    def test_status_without_accents_is_accepted_as_the_official_label(self):
        from anka.dominio.status import canonical_work_status as canonical
        self.assertEqual(canonical("Informacion suficiente para revision interna"),
                         "Información suficiente para revisión interna")
        self.assertEqual(canonical("  FALTAN   datos "), "Faltan datos")
        self.assertIsNone(canonical("Otra cosa"))

    def test_bridge_imports_a_draft_whose_status_has_no_accents(self):
        from anka.cli import work_sync as sync
        data = payload(status="Informacion suficiente para revision interna")
        found = sync.extract(draft_message(data), "D1")
        self.assertIsNotNone(found)
        self.assertEqual(found[0]["status"], "Información suficiente para revisión interna")
        self.assertEqual(found[0]["draft_id"], "D1")
        self.assertIn("Hola", found[0]["draft_reply"])


# Cubre: U-01, U-02, U-04, U-07
class UxFixesTests(Base):
    """Los cinco arreglos de la evaluación UX: etapa en la lista, textos al día, historial plegado,
    «qué hacer hoy» y un encabezado sin controles peligrosos."""

    def set_status(self, status):
        db = init_db()
        db.execute("UPDATE quotes SET status=? WHERE thread_id='T1'", (status,))
        db.commit()
        db.close()

    def stage(self):
        return web._case_stage(quotes.get_quote("T1"))

    # 1. etapa y siguiente acción
    def test_every_case_says_its_stage_and_whether_it_needs_you(self):
        cases = [
            (QuoteStatus.REVIEW_PENDING, ("Por aclarar con el cliente", True)),
            (QuoteStatus.WAITING, ("Esperando al cliente", False)),
            (QuoteStatus.RESPONSE_SENT, ("Esperando al cliente", False)),
            (QuoteStatus.REVIEW, ("Falta tu decisión técnica", True)),
            (QuoteStatus.READY_REVIEW, ("Listo para emitir la T", True)),
            (QuoteStatus.READY, ("Listo para emitir la T", True)),
        ]
        for status, expected in cases:
            with self.subTest(status=status):
                self.set_status(status)
                self.assertEqual(self.stage(), expected)

    def test_with_a_code_the_stage_is_the_offer_and_urgent_things_come_first(self):
        self.assign()
        self.assertEqual(self.stage(), ("T26145 · cotizando", False))
        change_control.promote_to_te("T1")
        self.assertEqual(self.stage(), ("T&E26145 · aprobada", False))
        self.sync({"m1": coded_mail("T1", "Luis <luis@acme.com>", self.t0 + 7200, "Cambiar plazo")})
        self.assertEqual(self.stage(), ("Cambio por revisar", True))      # lo urgente gana a la etapa

    def test_the_list_shows_the_stage_and_puts_cases_that_need_action_first(self):
        quotes.dashboard_upsert(b64(json.dumps(payload(thread="T2", message="mx"))))
        self.set_status(QuoteStatus.WAITING)                              # T1 espera al cliente
        db = init_db()
        db.execute("UPDATE quotes SET status=? WHERE thread_id='T2'", (QuoteStatus.REVIEW_PENDING,))
        db.commit()
        db.close()
        with patch("anka.web.server.load_config", return_value=CONFIG):
            page = web._render_requests()
        self.assertIn("Esperando al cliente", page)
        self.assertIn("Por aclarar con el cliente", page)
        self.assertLess(page.index("Por aclarar con el cliente"), page.index("Esperando al cliente"))

    # 2. textos al día
    def test_no_stale_prompts_once_the_t_exists(self):
        self.set_status(QuoteStatus.READY)
        self.assign()
        with patch("anka.web.server.load_config", return_value=CONFIG):
            page = web._render_case("T1")
        self.assertNotIn("Respuesta sugerida lista", page)
        self.assertNotIn("Recepción completa", page)
        self.assertNotIn("Listo para emitir la T</span>", page)           # el paso 2 ya no lo repite
        self.assertIn("Completado", page)
        box = page.split('name="body"')[1].split("</textarea>")[0]
        self.assertEqual(box.split(">", 1)[1].strip(), "")                # respuesta libre, en blanco

    # 3. historial citado plegado
    def test_quoted_history_is_split_from_the_new_message(self):
        body = ("Hola, van las coordenadas.\nPC-01: E 1, N 2\n\nEl jue, 1 oct 2026 a las 17:59, Anka comercial ia (<\n"
                "ankacomercialia@gmail.com>) escribió:\n> Hola Andrés, gracias\n> por la información")
        fresh, quoted = web.split_quoted(body)
        self.assertEqual(fresh, "Hola, van las coordenadas.\nPC-01: E 1, N 2")
        self.assertIn("escribió:", quoted)
        self.assertIn("> Hola Andrés", quoted)
        self.assertEqual(web.split_quoted("Solo mensaje nuevo ▎ sin cita")[1], "")
        self.assertEqual(web.split_quoted("A\nDe: x@y.com\nEnviado: ayer\ntexto")[0], "A")
        self.assertEqual(web.split_quoted("A\n-----Original Message-----\nB")[1].startswith("-----"), True)

    def test_conversation_folds_the_quoted_part(self):
        self.sync({"m1": coded_mail(
            "T1", "Ana <ana@acme.com>", self.t0 + 600,
            "Les confirmo el datum.\n\nEl jue, 1 oct 2026 a las 17:59, Anka escribió:\n> pregunta anterior")})
        with patch("anka.web.server.load_config", return_value=CONFIG):
            page = web._render_case("T1")
        self.assertIn("Mostrar el mensaje citado", page)
        self.assertIn("Les confirmo el datum.", page)

    # 4. «qué hacer hoy»
    def test_summary_is_a_to_do_list_first(self):
        self.set_status(QuoteStatus.REVIEW_PENDING)
        with patch("anka.web.server.load_config", return_value=CONFIG):
            page = web._render_index()
        self.assertIn("Qué hacer hoy", page)
        self.assertIn("Por aclarar con el cliente", page)
        self.assertIn("Ver estadísticas", page)                           # las gráficas quedan plegadas
        self.assertLess(page.index("Por aclarar con el cliente"), page.index("Ver estadísticas"))
        self.set_status(QuoteStatus.WAITING)
        with patch("anka.web.server.load_config", return_value=CONFIG):
            self.assertIn("No hay nada pendiente de tu parte", web._render_index())

    # 5. encabezado
    def test_header_hides_the_mode_picker_in_settings_and_the_useless_refresh_in_work(self):
        from anka.web.views import render_page
        work = render_page("t", "x", "", 0, "chatgpt_work")
        local = render_page("t", "x", "", 0, "local")
        self.assertIn("Ajustes", work)
        self.assertNotIn("Actualizar correo", work)                       # en Work no hace nada
        self.assertIn("Actualizar correo", local)


# Cubre: U-05, F1-05
class DoubleSendTests(Base):
    """Un clic = un correo: tras enviar, la pantalla cambia y el mismo texto no sale dos veces."""

    class Gmail:
        def __init__(self):
            self.sent = []

        def users(self):
            return self

        def messages(self):
            return self

        def send(self, **kw):
            self.sent.append(kw)
            return _Exec({"id": f"sent-{len(self.sent)}"})

    def send(self, gmail, body="Hola, ¿datum?"):
        context = (quotes.get_quote("T1"), gmail, {"id": "T1", "messages": []}, set())
        with patch.object(case_actions, "case_email_context", return_value=context), \
                patch.object(case_actions, "reply_email", return_value=type("E", (), {"as_bytes": lambda self: b"x"})()):
            return case_actions.send_dashboard_reply("T1", body)

    def test_the_same_text_is_not_sent_twice_in_a_row(self):
        gmail = self.Gmail()
        self.send(gmail)
        with self.assertRaises(ValueError) as caught:
            self.send(gmail)                                   # segundo clic con el mismo texto
        self.assertIn("ya se envió", str(caught.exception))
        self.assertEqual(len(gmail.sent), 1)                   # solo salió un correo
        self.send(gmail, "Otro texto distinto")                # un texto nuevo sí se puede enviar
        self.assertEqual(len(gmail.sent), 2)

    def test_after_sending_questions_the_case_waits_and_hides_the_send_form(self):
        db = init_db()
        db.execute("UPDATE quotes SET status=? WHERE thread_id='T1'", (QuoteStatus.REVIEW_PENDING,))
        db.commit()
        db.close()
        self.send(self.Gmail())
        self.assertEqual(quotes.get_quote("T1")["status"], QuoteStatus.WAITING)
        with patch("anka.web.server.load_config", return_value=CONFIG):
            page = web._render_case("T1")
            free = web._render_case("T1", template="libre")
        self.assertIn("Correo enviado.", page)
        self.assertNotIn('formaction="/send"', page)            # ya no se ofrece enviar lo mismo
        self.assertIn("Escribir otro correo", page)
        self.assertIn('formaction="/send"', free)               # pero se puede escribir otro, en blanco
        box = free.split('name="body"')[1].split("</textarea>")[0]
        self.assertEqual(box.split(">", 1)[1].strip(), "")

    def test_the_send_button_blocks_itself_after_the_first_click(self):
        from anka.web.views import render_page
        page = render_page("t", "x", "", 0, "chatgpt_work")
        self.assertIn("Enviando", page)
        self.assertIn("formaction') === '/send'", page)


# Cubre: T-05, R13, F1-06
class ClientDelayFollowupTests(Base):
    """El cliente tarda: tiempo de espera, alertas, recordatorios automáticos con condiciones y llamada."""

    SENT = "2026-10-01T10:00:00-05:00"       # jueves

    def at(self, text):
        from datetime import datetime as dt
        return dt.fromisoformat(text)

    def wait_for_client(self, auto=True):
        from anka.servicios import followup_reminders as fup
        db = init_db()
        db.execute("UPDATE quotes SET status=?, sent_at=?, recipient='ana@acme.com' WHERE thread_id='T1'",
                   (QuoteStatus.WAITING, self.SENT))
        db.commit()
        db.close()
        if auto:
            fup.set_followup("T1", "auto_on")

    def due(self, when):
        from anka.servicios import followup_reminders as fup
        return fup.due_reminders(self.at(when))

    # ---- calendario hábil
    def test_business_days_and_hours(self):
        from anka.servicios import followup_reminders as fup
        a = self.at
        self.assertEqual(fup.business_days(a("2026-10-02T09:00:00-05:00"), a("2026-10-05T09:00:00-05:00")), 1)  # vie -> lun
        self.assertEqual(fup.business_days(a("2026-10-05T09:00:00-05:00"), a("2026-10-09T09:00:00-05:00")), 4)
        self.assertEqual(fup.business_days(a("2026-10-03T09:00:00-05:00"), a("2026-10-04T09:00:00-05:00")), 0)  # fin de semana
        self.assertTrue(fup.in_business_hours(a("2026-10-02T08:00:00-05:00")))
        self.assertFalse(fup.in_business_hours(a("2026-10-02T17:00:00-05:00")))
        self.assertFalse(fup.in_business_hours(a("2026-10-03T10:00:00-05:00")))   # sábado
        self.assertTrue(fup.in_business_hours(a("2026-10-02T14:00:00-07:00")))    # 16:00 en Colombia, aunque el PC esté en otra zona

    # ---- recordatorios automáticos
    def test_reminders_follow_the_schedule_and_stop_after_two(self):
        self.wait_for_client()
        self.assertEqual(self.due("2026-10-01T16:00:00-05:00"), [])                  # el mismo día: nada
        self.assertEqual(self.due("2026-10-02T10:00:00-05:00"), [("T1", 1)])         # 1 día hábil
        db = init_db()
        record_event(db, "T1", "reminder_sent", "dashboard", f"{self.SENT}|1", "r1", "2026-10-02T10:30:00-05:00")
        db.commit()
        db.close()
        self.assertEqual(self.due("2026-10-05T10:00:00-05:00"), [])                  # a 2 días hábiles aún no
        self.assertEqual(self.due("2026-10-06T10:00:00-05:00"), [("T1", 2)])         # a 3 días hábiles
        db = init_db()
        record_event(db, "T1", "reminder_sent", "dashboard", f"{self.SENT}|2", "r2", "2026-10-06T10:30:00-05:00")
        db.commit()
        db.close()
        self.assertEqual(self.due("2026-10-13T10:00:00-05:00"), [])                  # máximo dos

    def test_nothing_is_sent_outside_business_hours(self):
        self.wait_for_client()
        self.assertEqual(self.due("2026-10-02T07:59:00-05:00"), [])
        self.assertEqual(self.due("2026-10-02T17:30:00-05:00"), [])
        self.assertEqual(self.due("2026-10-03T10:00:00-05:00"), [])                  # sábado

    def test_automatic_follow_up_is_off_by_default(self):
        self.wait_for_client(auto=False)
        self.assertEqual(self.due("2026-10-02T10:00:00-05:00"), [])

    def test_every_stop_condition_cancels_the_reminder(self):
        from anka.servicios import followup_reminders as fup
        when = "2026-10-02T10:00:00-05:00"
        self.wait_for_client()
        fup.set_followup("T1", "pause")
        self.assertEqual(self.due(when), [])                                          # pausado
        fup.set_followup("T1", "resume")
        self.assertEqual(self.due(when), [("T1", 1)])
        fup.set_followup("T1", "contacted")
        self.assertEqual(self.due(when), [])                                          # ya habló con el cliente
        db = init_db()
        db.execute("UPDATE case_followup SET contacted_at=NULL WHERE thread_id='T1'")
        record_event(db, "T1", "received", "gmail", "r-cliente", "respondió", "2026-10-01T18:00:00-05:00", "ana@acme.com")
        db.commit()
        db.close()
        self.assertEqual(self.due(when), [])                                          # el cliente respondió
        db = init_db()
        db.execute("DELETE FROM case_events WHERE ref='r-cliente'")
        db.execute("UPDATE quotes SET recipient='otra@acme.com' WHERE thread_id='T1'")
        db.commit()
        db.close()
        self.assertEqual(self.due(when), [])                                          # no es el contacto registrado

    def test_something_from_the_client_to_review_cancels_it_too(self):
        self.wait_for_client()
        self.sync({"n1": mail("NUEVO", False, self.t0 + 3600, "Luis <ana@acme.com>")})   # aviso de otro hilo
        self.assertTrue(quotes.get_quote("T1")["links"])
        self.assertEqual(self.due("2026-10-02T10:00:00-05:00"), [])

    def test_sending_a_reminder_does_not_touch_the_case(self):
        from anka.servicios import followup_reminders as fup
        self.wait_for_client()
        sent = []

        class G:
            def users(self):
                return self

            def messages(self):
                return self

            def send(self, **kw):
                sent.append(kw)
                return _Exec({"id": "msg-1"})

        quote = quotes.get_quote("T1")
        context = (quote, G(), {"id": "T1", "messages": []}, set())
        from unittest.mock import patch as p2

        from anka.dominio import correo
        with p2.object(case_actions, "case_email_context", return_value=context), \
                p2.object(correo, "reply_email", return_value=type("E", (), {"as_bytes": lambda self: b"x"})()):
            fup.send_reminder("T1", 1)
        after = quotes.get_quote("T1")
        self.assertEqual((after["status"], after["sent_at"]), (QuoteStatus.WAITING, self.SENT))   # el reloj no se reinicia
        self.assertEqual(len(sent), 1)
        self.assertEqual(after["reminders"], [f"{self.SENT}|1"])
        self.assertFalse(any(ch.isdigit() for ch in fup.reminder_text(1) + fup.reminder_text(2)))   # texto neutro, sin cifras

    def test_a_failure_in_one_case_does_not_stop_the_rest(self):
        from anka.servicios import followup_reminders as fup
        with patch.object(fup, "due_reminders", return_value=[("A", 1), ("B", 1)]), \
                patch.object(fup, "send_reminder", side_effect=[RuntimeError("falla"), None]) as send:
            self.assertEqual(fup.run_due_reminders(), 1)
        self.assertEqual(send.call_count, 2)

    # ---- alertas y pantalla
    def stage(self, now_text):
        from anka.servicios import followup_reminders as fup
        quote = quotes.get_quote("T1")
        with patch.object(fup, "waiting_days", side_effect=lambda since, now=None: fup.business_days(
                self.at(since), self.at(now_text))), \
                patch.object(fup, "call_due", side_effect=lambda since, contacted, now=None: (
                    fup.business_days(self.at(since), self.at(now_text)) >= fup.CALL_BUSINESS_DAYS
                    and not (contacted and contacted >= since))):
            return web._case_stage(quote)

    def test_waiting_time_alerts_and_the_call(self):
        self.wait_for_client(auto=False)
        self.assertEqual(self.stage("2026-10-01T16:00:00-05:00"), ("Esperando al cliente", False))
        self.assertEqual(self.stage("2026-10-05T10:00:00-05:00"), ("Esperando al cliente · hace 2 d", False))
        self.assertEqual(self.stage("2026-10-06T10:00:00-05:00"), ("Sin respuesta · recordar", True))   # 3 días hábiles
        self.assertEqual(self.stage("2026-10-12T10:00:00-05:00"), ("Llamar al cliente", True))         # 7 días hábiles

    def test_with_automatic_follow_up_the_alert_waits_until_the_call(self):
        self.wait_for_client(auto=True)
        self.assertEqual(self.stage("2026-10-06T10:00:00-05:00")[1], False)                           # lo hacen los recordatorios
        self.assertEqual(self.stage("2026-10-12T10:00:00-05:00"), ("Llamar al cliente", True))

    def test_saying_you_already_spoke_silences_the_alerts(self):
        from anka.servicios import followup_reminders as fup
        self.wait_for_client(auto=False)
        fup.set_followup("T1", "contacted")
        self.assertEqual(self.stage("2026-10-12T10:00:00-05:00")[1], False)

    def test_the_case_page_shows_the_follow_up_panel_and_its_controls(self):
        self.wait_for_client(auto=False)
        with patch("anka.web.server.load_config", return_value=CONFIG):
            page = web._render_case("T1")
        self.assertIn("Seguimiento del cliente", page)
        self.assertIn("Activar seguimiento automático", page)
        self.assertIn("Ya hablé con el cliente", page)
        from anka.servicios import followup_reminders as fup
        fup.set_followup("T1", "auto_on")
        with patch("anka.web.server.load_config", return_value=CONFIG):
            page = web._render_case("T1")
        self.assertIn("Pausar seguimiento", page)
        kinds = {e["kind"] for e in quotes.thread_events("T1")}
        self.assertIn("followup_auto_on", kinds)
        with self.assertRaises(ValueError):
            fup.set_followup("T1", "borrar")

    def test_python_constants_match_the_rules_file(self):
        from anka.servicios import followup_reminders as fup
        text = Path("skills/anka-cotizaciones-correo/references/reglas_operativas.md").read_text(encoding="utf-8")
        rules = json.loads(text.split("```json reglas\n")[1].split("\n```")[0])["recordatorios"]
        self.assertEqual(rules["maximo"], fup.MAX_REMINDERS)
        self.assertEqual(tuple(rules["dias_habiles"]), fup.REMINDER_BUSINESS_DAYS)
        self.assertEqual(rules["llamar_dias_habiles"], fup.CALL_BUSINESS_DAYS)
        self.assertEqual(rules["alerta_sin_automaticos_dias_habiles"], fup.ALERT_BUSINESS_DAYS)
        self.assertEqual(tuple(rules["horario"]), fup.BUSINESS_HOURS)
        self.assertIs(rules["requiere_activacion_por_caso"], fup.AUTO_REQUIRES_ACTIVATION)
        self.assertIs(rules["solo_contacto_registrado"], fup.ONLY_TO_REGISTERED_CONTACT)


if __name__ == "__main__":
    unittest.main()
