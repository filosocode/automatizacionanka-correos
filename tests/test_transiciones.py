"""Transiciones de estado: cada fila de spec/transiciones.json se ejecuta contra el código real.

Cubre: T-01
"""

from tests import _isolate  # noqa: F401  (debe ir primero)

import json
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

from anka.servicios import case_actions
from anka.servicios import gmail_followup
from anka.servicios import quotes
from anka.servicios import work_import
from anka.infra import settings
from anka.servicios.quotes import connect
from anka.dominio.status import QuoteStatus
from tests.test_work_sync import FollowupGmail, _Exec, b64, mail, payload

SPEC = json.loads((Path(__file__).resolve().parents[1] / "spec" / "transiciones.json").read_text(encoding="utf-8"))
ALL_STATES = set(SPEC["estados"])


class FakeGmail:
    def __init__(self):
        self.sent = []

    def users(self):
        return self

    def messages(self):
        return self

    def send(self, **kw):
        self.sent.append(kw)
        return _Exec({"id": f"sent-{len(self.sent)}"})


class Transitions(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        patcher = patch.object(settings, "DATA_DIR", Path(self.temp.name))
        patcher.start()
        self.addCleanup(patcher.stop)
        self.counter = 0

    def seed(self, status, coded=False):
        self.counter += 1
        thread = f"S{self.counter}"
        work_import.dashboard_upsert(b64(json.dumps(payload(
            thread=thread, message="m1", received_at="2026-10-01T10:00:00-05:00"))))
        db = connect()
        db.execute("UPDATE quotes SET status=?, recipient='c@example.com', "
                   "quote_code=?, code_stage=? WHERE thread_id=?",
                   (status, "26001" if coded else None, "T" if coded else None, thread))
        db.commit()
        db.close()
        return thread

    def status(self, thread):
        return quotes.get_quote(thread)["status"]

    def rows(self, event):
        return [row for row in SPEC["transiciones"] if row["evento"] == event]

    def test_every_state_in_the_spec_is_a_real_state(self):
        self.assertEqual(ALL_STATES, {s.value for s in QuoteStatus})
        for row in SPEC["transiciones"]:
            self.assertLessEqual(set(row["desde"]), ALL_STATES, row["id"])
            self.assertTrue(row["hacia"] in ALL_STATES | {"igual", "error"}, row["id"])

    def test_work_imports(self):
        for row in self.rows("work_import"):
            for label in row["estado_work"]:
                for origin in row["desde"]:
                    with self.subTest(row=row["id"], etiqueta=label, desde=origin):
                        thread = self.seed(origin)
                        extra = {}
                        if row["dependencias_pendientes"]:
                            extra["scope_dependencies"] = [
                                {"description": "KML", "source": "a.kml", "status": "pendiente"}]
                        work_import.dashboard_upsert(b64(json.dumps(payload(
                            thread=thread, status=label, message="m1" if row["mismo_mensaje"] else "m2",
                            **extra))))
                        expected = origin if row["hacia"] == "igual" else row["hacia"]
                        self.assertEqual(self.status(thread), expected)

    def test_technical_review_decisions(self):
        for row in self.rows("decidir_revision_tecnica"):
            for origin in row["desde"]:
                with self.subTest(row=row["id"], desde=origin):
                    thread = self.seed(origin)
                    questions = "¿Datum?" if row["decision"] == "ask_client" else ""
                    if row["hacia"] == "error":
                        with self.assertRaises(ValueError):
                            quotes.resolve_technical_review(thread, row["decision"], questions)
                    else:
                        quotes.resolve_technical_review(thread, row["decision"], questions)
                        self.assertEqual(self.status(thread), row["hacia"])

    def con_decision_de_preguntar(self, thread):
        db = connect()
        row = db.execute("SELECT analysis_json FROM quotes WHERE thread_id=?", (thread,)).fetchone()
        analysis = json.loads(row["analysis_json"] or "{}")
        analysis["manual_review"] = {"decision": "ask_client", "at": "2026-10-01T10:30:00-05:00", "questions": ["¿Datum?"]}
        db.execute("UPDATE quotes SET analysis_json=? WHERE thread_id=?", (json.dumps(analysis), thread))
        db.commit()
        db.close()

    def test_going_back_before_sending(self):
        for row in self.rows("volver_a_decidir"):
            for origin in row["desde"]:
                with self.subTest(row=row["id"], desde=origin):
                    thread = self.seed(origin)
                    if row.get("con_decision"):
                        self.con_decision_de_preguntar(thread)
                    if row["hacia"] == "error":
                        with self.assertRaises(ValueError):
                            quotes.volver_a_decidir(thread, "reconsiderar")
                    else:
                        quotes.volver_a_decidir(thread, row["accion"])
                        self.assertEqual(self.status(thread), row["hacia"])

    def test_going_on_without_the_clients_answer(self):
        for row in self.rows("seguir_sin_respuesta"):
            for origin in row["desde"]:
                with self.subTest(row=row["id"], desde=origin):
                    thread = self.seed(origin)
                    if row["hacia"] == "error":
                        with self.assertRaises(ValueError):
                            quotes.seguir_sin_respuesta(thread)
                    else:
                        quotes.seguir_sin_respuesta(thread)
                        self.assertEqual(self.status(thread), row["hacia"])

    def con_pendiente_interno(self, thread):
        db = connect()
        row = db.execute("SELECT analysis_json FROM quotes WHERE thread_id=?", (thread,)).fetchone()
        analysis = json.loads(row["analysis_json"] or "{}")
        analysis["preguntas"] = [{"id": "q1", "texto": "Revisar el KMZ", "decision": "internal"}]
        db.execute("UPDATE quotes SET analysis_json=? WHERE thread_id=?", (json.dumps(analysis), thread))
        db.commit()
        db.close()

    def send(self, thread, gmail, texto="", esperar=False):
        quote = quotes.get_quote(thread)
        context = (quote, gmail, {"id": thread, "messages": []}, set())
        with patch.object(case_actions, "case_email_context", return_value=context), \
                patch.object(case_actions, "reply_email",
                             return_value=__import__("email.message", fromlist=["EmailMessage"]).EmailMessage()):
            return case_actions.send_dashboard_reply(thread, texto or f"Texto para {thread}", esperar=esperar)

    def test_sending_from_the_dashboard(self):
        for row in self.rows("enviar_desde_dashboard"):
            for origin in row["desde"]:
                with self.subTest(row=row["id"], desde=origin):
                    thread = self.seed(origin, coded=row.get("con_codigo", False))
                    if row["hacia"] == "error":
                        # el filtro real vive en case_email_context; aquí se comprueba con la función real
                        with self.assertRaises(ValueError):
                            case_actions.case_email_context(thread)
                    else:
                        if row.get("con_pendientes_internos"):
                            self.con_pendiente_interno(thread)
                        self.send(thread, FakeGmail(), "¿Cuál es el datum del levantamiento?"
                                  if row.get("con_preguntas") else "", esperar=row.get("esperar_respuesta", False))
                        expected = origin if row["hacia"] == "igual" else row["hacia"]
                        self.assertEqual(self.status(thread), expected)

    def test_a_reply_sent_from_gmail(self):
        sent_at = int(datetime.fromisoformat("2026-10-01T10:10:00-05:00").timestamp())
        for row in self.rows("correo_enviado_desde_gmail"):
            for origin in row["desde"]:
                with self.subTest(row=row["id"], desde=origin):
                    thread = self.seed(origin)
                    gmail = FollowupGmail({"s1": mail(thread, True, sent_at, "c@example.com")})
                    gmail_followup.sync_thread_activity(gmail, set())
                    expected = origin if row["hacia"] == "igual" else row["hacia"]
                    self.assertEqual(self.status(thread), expected)


if __name__ == "__main__":
    unittest.main()
