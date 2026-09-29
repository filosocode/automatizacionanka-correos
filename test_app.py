import base64
import io
import json
import os
import sqlite3
import tempfile
import unittest
import urllib.error
import urllib.request
import zipfile
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

import app
from app import (
    attachment_names, commercial_candidate, draft_body_with_review_note,
    extract_attachment_text, extract_message_attachments, forwarded_routing,
    has_attachments, inbound_messages, message_text, missing_questions,
    safe_folder_name, unresolved_scope_issues,
)


class InternalCommercialContextTests(unittest.TestCase):
    def test_requires_internal_only_flag(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "index.json"
            path.write_text(json.dumps({"privacy": {"internal_only": False}}), encoding="utf-8")
            with patch.object(app, "INTERNAL_CONTEXT_PATH", path):
                self.assertEqual(app.load_internal_commercial_index(), {})

    def test_matches_client_name_without_legal_suffix(self):
        index = {"clients": {"energia norte sas": {"client_name": "Energia Norte S.A.S."}}}
        profile = app._commercial_profile_match(index, "Energía Norte")
        self.assertEqual(profile["client_name"], "Energia Norte S.A.S.")

    def test_suggested_reply_does_not_read_internal_index(self):
        with patch.object(app, "load_internal_commercial_index", side_effect=AssertionError):
            body = app.suggested_reply(complete_analysis(), ["ubicación del proyecto"])
        self.assertIn("Gracias por su solicitud", body)


class FakeRequest:
    def __init__(self, result):
        self.result = result

    def execute(self):
        return self.result


class FakeAttachments:
    def __init__(self, attachment_data):
        self.attachment_data = attachment_data

    def get(self, userId, messageId, id):
        return FakeRequest({"data": self.attachment_data[(messageId, id)]})


class FakeMessages:
    def __init__(self, gmail):
        self.gmail = gmail

    def list(self, **kwargs):
        return FakeRequest({"messages": [{"threadId": self.gmail.thread["id"]}]})

    def attachments(self):
        return FakeAttachments(self.gmail.attachment_data)

    def send(self, **kwargs):
        self.gmail.sent_messages.append(kwargs["body"])
        return FakeRequest({"id": "sent-1"})


class FakeThreads:
    def __init__(self, gmail):
        self.gmail = gmail

    def get(self, **kwargs):
        return FakeRequest(self.gmail.thread)


class FakeDrafts:
    def __init__(self, gmail):
        self.gmail = gmail

    def create(self, **kwargs):
        self.gmail.created_drafts.append(kwargs["body"])
        return FakeRequest({"id": "draft-1"})

    def update(self, **kwargs):
        self.gmail.updated_drafts.append(kwargs)
        return FakeRequest({"id": kwargs["id"]})

    def send(self, **kwargs):
        self.gmail.sent_drafts.append(kwargs["body"]["id"])
        return FakeRequest({"id": "sent-draft-1"})


class FakeUsers:
    def __init__(self, gmail):
        self.gmail = gmail

    def getProfile(self, **kwargs):
        return FakeRequest({"emailAddress": "ankacomercialia@gmail.com"})

    def messages(self):
        return FakeMessages(self.gmail)

    def threads(self):
        return FakeThreads(self.gmail)

    def drafts(self):
        return FakeDrafts(self.gmail)


class FakeGmail:
    def __init__(self, thread, attachment_data=None):
        self.thread = thread
        self.attachment_data = attachment_data or {}
        self.created_drafts = []
        self.updated_drafts = []
        self.sent_drafts = []
        self.sent_messages = []

    def users(self):
        return FakeUsers(self)


def encoded_message(
    message_id, sender, text, internal_date, parts=None, labels=None,
    subject="Solicitud de cotización",
):
    body = base64.urlsafe_b64encode(text.encode()).decode()
    return {
        "id": message_id,
        "internalDate": str(internal_date),
        "labelIds": labels or ["INBOX"],
        "payload": {
            "headers": [
                {"name": "From", "value": sender},
                {"name": "Subject", "value": subject},
                {"name": "Message-ID", "value": f"<{message_id}@example.com>"},
            ],
            "parts": [
                {"mimeType": "text/plain", "body": {"data": body}},
                *(parts or []),
            ],
        },
    }


def complete_analysis(location="Bogotá"):
    return {
        "is_quote_request": True,
        "project_name": "Proyecto Norte",
        "summary": "Levantamiento topográfico solicitado.",
        "activities": [{
            "activity": "Levantamiento topográfico",
            "ubicacion": location or "no indicado",
            "servicio_tecnica": "Topografía convencional",
            "quantity": "10",
            "unit": "ha",
            "entregable": "Plano topográfico",
            "plazo_restriccion": "no indicado",
            "fuente": "correo cliente-1",
            "estado": "confirmado" if location else "pendiente",
            "notes": "",
        }],
        "fields": [
            {"name": "ubicación del proyecto", "value": location},
            {"name": "área o longitud", "value": "10 ha"},
        ],
        "other_questions": [],
        "scope_dependencies": [],
        "contradictions": [],
    }


def test_config():
    return {
        "active_mode": "local",
        "gmail_query": "in:inbox",
        "own_addresses": ["alias@anka.com.co"],
        "forwarded_reply_policy": "internal_reviewer",
        "llm": {"provider": "ollama", "base_url": "http://localhost"},
        "required_fields": ["ubicación del proyecto", "área o longitud"],
        "commercial_filter_terms": ["cotización", "levantamiento"],
        "drive_parent_id": "parent",
        "folder_prefix": "Cotizacion",
    }


class QuoteFlowTests(unittest.TestCase):
    def test_dashboard_reads_real_automation_interval(self):
        with tempfile.TemporaryDirectory() as temp_dir, patch.dict(
            os.environ, {"CODEX_HOME": temp_dir}
        ):
            automation_dir = Path(temp_dir) / "automations" / "cotizaciones-gmail-anka"
            automation_dir.mkdir(parents=True)
            (automation_dir / "automation.toml").write_text(
                'rrule = "RRULE:FREQ=MINUTELY;INTERVAL=1"\n', encoding="utf-8"
            )
            self.assertEqual(app.automation_interval_label(), "cada minuto")

    def test_activity_table_accepts_empty_values_from_real_messages(self):
        rendered = app._activities_table([{
            "activity": "Levantamiento topográfico",
            "quantity": None,
            "unit": None,
            "entregable": None,
        }])
        self.assertIn("Levantamiento topográfico", rendered)

    def test_suggested_reply_requests_missing_supporting_documents(self):
        analysis = complete_analysis(location="")
        analysis["attachments"] = []
        reply = app.suggested_reply(analysis, test_config()["required_fields"])
        self.assertIn("KMZ/KML", reply)
        self.assertIn("Planos, diseños preliminares", reply)
        self.assertIn("normas del contratante", reply)
        self.assertIn("si dispone de ellos", reply.casefold())

    def test_suggested_reply_does_not_request_documents_already_attached(self):
        analysis = complete_analysis(location="")
        analysis["attachments"] = [
            {"filename": "ubicacion.kmz", "status": "extraido"},
            {"filename": "planos.dwg", "status": "bloqueado"},
            {"filename": "TDR_y_normas.pdf", "status": "extraido"},
        ]
        reply = app.suggested_reply(analysis, test_config()["required_fields"])
        self.assertNotIn("KMZ/KML", reply)
        self.assertNotIn("Planos, diseños preliminares", reply)
        self.assertNotIn("normas del contratante", reply)

    def test_extract_plain_text_from_multipart(self):
        body = base64.urlsafe_b64encode("Solicito cotización".encode()).decode()
        payload = {"parts": [
            {"mimeType": "text/plain", "body": {"data": body}},
            {"mimeType": "text/html", "body": {"data": "PGI+T1RSQTwvYj4="}},
        ]}
        self.assertEqual(message_text(payload), "Solicito cotización")

    def test_missing_fields_do_not_repeat_questions(self):
        analysis = {"fields": [
            {"name": "Ubicación", "value": "Bogotá"},
            {"name": "Cantidad", "value": ""},
        ], "other_questions": ["¿Podría indicar Cantidad?", "¿Hay restricciones de acceso?"]}
        self.assertEqual(missing_questions(analysis, ["Ubicación", "Cantidad"]), [
            "¿Podría indicar Cantidad?", "¿Hay restricciones de acceso?"
        ])

    def test_reception_assessment_separates_blockers_from_technical_checks(self):
        analysis = complete_analysis(location="")
        analysis["activities"][0]["quantity"] = ""
        analysis["fields"][1]["value"] = ""
        result = app.reception_assessment(analysis)
        self.assertEqual(result["status"], "needs_client_information")
        self.assertEqual(len(result["questions"]), 2)
        self.assertTrue(any("ubicación" in question for question in result["questions"]))
        self.assertTrue(any("área" in question for question in result["questions"]))
        self.assertIn("Precisión y sistema de referencia", result["technical_checks"])

    def test_reception_assessment_accepts_complete_initial_information(self):
        result = app.reception_assessment(complete_analysis())
        self.assertEqual(result["status"], "sufficient_for_internal_review")
        self.assertEqual(result["questions"], [])

    def test_reception_assessment_pauses_client_reply_for_internal_issue(self):
        analysis = complete_analysis()
        analysis["contradictions"] = [{
            "topic": "área", "details": "Dos valores diferentes",
            "sources": ["correo", "plano"], "status": "pendiente",
        }]
        result = app.reception_assessment(analysis)
        self.assertEqual(result["status"], "technical_review")
        self.assertEqual(app.reception_questions(analysis), [])

    def test_case_view_renders_reception_assessment(self):
        review = {
            "thread_id": "thread-1", "recipient": "cliente@example.com",
            "status": "review_pending", "status_label": "Requiere aclaraciones",
            "analysis": complete_analysis(location=""), "draft_body": "",
            "draft_id": None, "sent_at": "", "client_name": "Cliente",
            "client_type": "No clasificado", "sector": "No clasificado", "notes": "",
        }
        with patch("app.get_quote", return_value=review), patch(
            "app.load_config", return_value=test_config()
        ), patch("app.load_internal_commercial_index", return_value={}):
            page = app._render_case("thread-1")
        self.assertIn("Evaluación de recepción", page)
        self.assertIn("Etapa actual:", page)

    def test_inbound_excludes_our_sent_reply(self):
        thread = {"messages": [
            {"id": "sent", "internalDate": "2", "labelIds": ["SENT"]},
            {"id": "inbound", "internalDate": "1", "labelIds": ["INBOX"]},
        ]}
        self.assertEqual([m["id"] for m in inbound_messages(thread)], ["inbound"])

    def test_inbound_excludes_configured_alias_without_sent_label(self):
        thread = {"messages": [
            encoded_message("client", "cliente@example.com", "Cotización", 1),
            encoded_message("alias", "Anka <alias@anka.com.co>", "Respuesta", 2),
        ]}
        self.assertEqual(
            [m["id"] for m in inbound_messages(thread, {"alias@anka.com.co"})],
            ["client"],
        )

    def test_attachment_requires_review(self):
        self.assertTrue(has_attachments({"parts": [
            {"filename": "plano.pdf", "body": {"attachmentId": "a"}}
        ]}))
        self.assertEqual(attachment_names({"parts": [
            {"filename": "plano.pdf", "body": {"attachmentId": "a"}}
        ]}), ["plano.pdf"])

    def test_extracts_common_text_attachment(self):
        text, reason = extract_attachment_text(
            "alcance.csv", "text/csv", "actividad,area\nlevantamiento,10 ha".encode()
        )
        self.assertIn("levantamiento,10 ha", text)
        self.assertEqual(reason, "")

    def test_extracts_docx_attachment(self):
        content = io.BytesIO()
        with zipfile.ZipFile(content, "w") as document:
            document.writestr(
                "word/document.xml",
                '<w:document xmlns:w="urn:test"><w:body><w:p><w:r>'
                '<w:t>Levantamiento en Medellín</w:t></w:r></w:p></w:body></w:document>',
            )
        text, reason = extract_attachment_text(
            "alcance.docx",
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            content.getvalue(),
        )
        self.assertIn("Levantamiento en Medellín", text)
        self.assertEqual(reason, "")

    def test_extracts_xlsx_attachment(self):
        from openpyxl import Workbook

        content = io.BytesIO()
        workbook = Workbook()
        workbook.active.append(["ubicación", "área"])
        workbook.active.append(["Bogotá", "10 ha"])
        workbook.save(content)
        text, reason = extract_attachment_text(
            "alcance.xlsx",
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            content.getvalue(),
        )
        self.assertIn("Bogotá\t10 ha", text)
        self.assertEqual(reason, "")

    def test_extracts_kmz_polygon_summary(self):
        content = io.BytesIO()
        with zipfile.ZipFile(content, "w") as archive:
            archive.writestr(
                "doc.kml",
                "<kml><Document><name>POLIGONO_RP</name><Placemark>"
                "<name>RIO PAEZ</name><Polygon><coordinates>"
                "-75.8,2.4,0 -75.6,2.5,0 -75.8,2.4,0"
                "</coordinates></Polygon></Placemark></Document></kml>",
            )
        text, reason = extract_attachment_text(
            "POLIGONO_RIOPAEZ.kmz", "application/vnd.google-earth.kmz",
            content.getvalue(),
        )
        self.assertIn("RIO PAEZ", text)
        self.assertIn("Cantidad de coordenadas: 3", text)
        self.assertIn("-75.8, 2.4, -75.6, 2.5", text)
        self.assertEqual(reason, "")

    def test_inline_signature_image_is_ignored(self):
        message = encoded_message(
            "client", "cliente@example.com", "Solicito cotización", 1,
            parts=[{
                "filename": "image.png",
                "mimeType": "image/png",
                "headers": [
                    {"name": "Content-Disposition", "value": "inline"},
                    {"name": "Content-ID", "value": "<firma-1>"},
                ],
                "body": {"attachmentId": "logo"},
            }],
        )
        attachments = extract_message_attachments(FakeGmail({"id": "t"}), message)
        self.assertEqual(attachments[0]["status"], "ignorado")
        self.assertEqual(attachments[0]["reason"], "imagen incrustada de firma o contenido del correo")

    def test_dwg_has_explicit_manual_review_reason(self):
        text, reason = extract_attachment_text(
            "TOPOGRAFIA LLANOS 8.dwg", "application/acad", b"binary"
        )
        self.assertEqual(text, "")
        self.assertEqual(reason, "plano CAD requiere revisión manual")

    def test_forwarded_message_keeps_internal_reviewer_as_recipient(self):
        message = encoded_message(
            "forwarded", "Diego <diego@anka.com.co>",
            "---------- Mensaje reenviado ---------\n"
            "De: Cliente Real <cliente@example.com>\n"
            "Asunto: Solicitud\n\nSolicitamos cotización.",
            1, subject="Fwd: Solicitud de cotización",
        )
        routing = forwarded_routing(message, test_config())
        self.assertTrue(routing["is_forwarded"])
        self.assertEqual(routing["original_sender_email"], "cliente@example.com")
        self.assertEqual(routing["selected_recipient"], "diego@anka.com.co")

    def test_unresolved_scope_dependencies_and_contradictions_are_reported(self):
        analysis = complete_analysis()
        analysis["scope_dependencies"] = [{
            "description": "Aplicar el alcance del proyecto anterior",
            "source": "correo cliente-1",
            "status": "pendiente",
        }]
        analysis["contradictions"] = [{
            "topic": "separación de mojones",
            "details": "2,0 km frente a 1,5 km",
            "sources": ["PDF página 8", "PDF página 12"],
            "status": "pendiente",
        }]
        issues = unresolved_scope_issues(analysis)
        self.assertEqual(len(issues), 2)
        self.assertIn("Dependencia", issues[0])
        self.assertIn("Contradicción", issues[1])

    def test_pdf_without_text_is_explicitly_blocked(self):
        from pypdf import PdfWriter

        content = io.BytesIO()
        writer = PdfWriter()
        writer.add_blank_page(width=100, height=100)
        writer.write(content)
        text, reason = extract_attachment_text(
            "plano.pdf", "application/pdf", content.getvalue()
        )
        self.assertEqual(text, "")
        self.assertIn("sin texto extraíble", reason)

    def test_folder_name_is_safe_and_stable(self):
        self.assertEqual(
            safe_folder_name('Obra: Norte/Sur', '1234567890abcdef', 'Cotizacion'),
            'Cotizacion - Obra Norte Sur - 90abcdef',
        )

    def test_commercial_prefilter_matches_accents(self):
        body = base64.urlsafe_b64encode("Solicito cotizacion".encode()).decode()
        messages = [{"payload": {
            "headers": [{"name": "Subject", "value": "Levantamiento"}],
            "parts": [{"mimeType": "text/plain", "body": {"data": body}}],
        }}]
        self.assertTrue(commercial_candidate(messages, ["cotización"]))
        self.assertFalse(commercial_candidate(messages, ["factura"]))

    def test_watch_handles_keyboard_interrupt_from_sleep(self):
        output = io.StringIO()
        with patch("app.run") as run_mock, patch(
            "app.time.sleep", side_effect=KeyboardInterrupt
        ):
            with redirect_stdout(output):
                app.watch(False, False, 30)
        run_mock.assert_called_once_with(False, False, False)
        self.assertIn("Vigilancia detenida por el usuario.", output.getvalue())

    def test_gmail_draft_body_starts_with_review_note(self):
        client_body = base64.urlsafe_b64encode(
            "Solicito cotizacion para un levantamiento.".encode()
        ).decode()
        analysis = {
            "summary": "Solicitud de levantamiento topografico",
            "activities": [
                {"activity": "levantamiento", "quantity": "", "unit": "", "notes": ""}
            ],
        }
        source_message = {
            "internalDate": "1760000400000",
            "payload": {
                "headers": [
                    {"name": "From", "value": "cliente@example.com"},
                    {"name": "Subject", "value": "Cotizacion"},
                ],
                "parts": [
                    {"mimeType": "text/plain", "body": {"data": client_body}},
                    {"filename": "plano.pdf", "body": {"attachmentId": "a"}},
                ],
            },
        }
        body = draft_body_with_review_note(
            analysis, ["Indicar ubicacion"], "Hola,\n\nGracias.\n", source_message
        )
        self.assertTrue(body.startswith("[REVISAR ANTES DE ENVIAR"))
        self.assertIn("BORRAR ESTA SECCION", body)
        self.assertIn("Correo original del cliente:", body)
        self.assertIn("cliente@example.com", body)
        self.assertIn("plano.pdf", body)
        self.assertIn("Solicito cotizacion para un levantamiento.", body)
        self.assertIn("Hola,\n\nGracias.", body)

    def test_dashboard_upsert_collects_commercial_metrics(self):
        payload = {
            "thread_id": "thread-dashboard",
            "status": "Nueva solicitud",
            "client_email": "cliente@example.com",
            "client_name": "Cliente Ejemplo",
            "client_type": "Empresa privada",
            "sector": "Infraestructura",
            "project_name": "Corredor vial",
            "summary": "Levantamiento para diseño vial",
            "service": "Topografía convencional",
            "activities": "Levantamiento topográfico",
            "received_at": "2026-09-28T10:00:00-05:00",
        }
        encoded = base64.urlsafe_b64encode(
            json.dumps(payload).encode("utf-8")
        ).decode("ascii")
        with tempfile.TemporaryDirectory() as temp_dir, patch.object(
            app, "DATA_DIR", Path(temp_dir)
        ):
            app.dashboard_upsert(encoded)
            quotes = app.all_quotes()
            notifications = app.unread_notifications()
            dashboard = app._render_index()
        self.assertEqual(len(quotes), 1)
        self.assertEqual(quotes[0]["client_type"], "Empresa privada")
        self.assertEqual(quotes[0]["sector"], "Infraestructura")
        self.assertIn("Topografía convencional", dashboard)
        self.assertIn("Cliente Ejemplo", dashboard)
        self.assertEqual(
            {item["kind"] for item in notifications},
            {"request_received", "draft_suggested"},
        )
        self.assertIn("Correo nuevo recibido", dashboard)
        self.assertIn("Respuesta sugerida lista para revisar", dashboard)

    def test_dashboard_draft_can_be_updated_and_sent_in_same_thread(self):
        thread = {"id": "thread-dashboard", "messages": [encoded_message(
            "client-1", "cliente@example.com", "Solicito cotización.", 1,
            subject="Solicitud de cotización",
        )]}
        gmail = FakeGmail(thread)
        analysis = complete_analysis(location="")
        analysis["routing"] = {
            "is_forwarded": False,
            "original_sender_email": "cliente@example.com",
            "selected_recipient": "cliente@example.com",
        }
        with tempfile.TemporaryDirectory() as temp_dir, patch.object(
            app, "DATA_DIR", Path(temp_dir)
        ), patch("app.load_config", return_value=test_config()), patch(
            "app.services", return_value=(gmail, object())
        ):
            db = app.init_db()
            app.save(
                db, "thread-dashboard", "client-1", "review_pending",
                "cliente@example.com", analysis,
            )
            db.close()
            message = app.save_dashboard_draft(
                "thread-dashboard", "Hola,\n\nGracias por su solicitud."
            )
            draft_quote = app.get_quote("thread-dashboard")
            sent_message = app.send_dashboard_reply(
                "thread-dashboard", "Hola,\n\nGracias por su solicitud revisada."
            )
            sent_quote = app.get_quote("thread-dashboard")
            notification_kinds = {
                item["kind"] for item in app.unread_notifications()
            }
        self.assertIn("Borrador guardado", message)
        self.assertEqual(draft_quote["status"], "draft_created")
        self.assertEqual(draft_quote["draft_id"], "draft-1")
        self.assertEqual(gmail.sent_drafts, ["draft-1"])
        self.assertIn("Correo enviado", sent_message)
        self.assertEqual(sent_quote["status"], "response_sent")
        self.assertIn("draft_saved", notification_kinds)
        self.assertIn("response_sent", notification_kinds)


class QuoteFlowIntegrationTests(unittest.TestCase):
    def run_with_workspace(self, gmail, analysis, *, review_only=False, gmail_drafts=False):
        with tempfile.TemporaryDirectory() as temp_dir, patch.object(
            app, "DATA_DIR", Path(temp_dir)
        ), patch("app.load_config", return_value=test_config()), patch(
            "app.services", return_value=(gmail, object())
        ), patch("app.analyze", return_value=analysis) as analyze_mock:
            with redirect_stdout(io.StringIO()):
                app.run(True, review_only=review_only, gmail_drafts=gmail_drafts)
            db = sqlite3.connect(Path(temp_dir) / "quotes.sqlite3")
            try:
                row = db.execute(
                    "SELECT last_inbound_id, status, recipient, analysis_json FROM quotes"
                ).fetchone()
            finally:
                db.close()
            packet_files = list((Path(temp_dir) / "review_queue").glob("*.txt")) \
                if (Path(temp_dir) / "review_queue").exists() else []
            packets = [path.read_text(encoding="utf-8") for path in packet_files]
        return row, analyze_mock, packets

    def test_complete_thread_ignores_own_alias_and_creates_client_draft(self):
        thread = {"id": "thread-alias", "messages": [
            encoded_message(
                "client-1", "Cliente <cliente@example.com>",
                "Solicito cotización de un levantamiento de 10 ha.", 1,
            ),
            encoded_message(
                "own-1", "Anka <alias@anka.com.co>",
                "Gracias, necesitamos la ubicación.", 2,
            ),
        ]}
        gmail = FakeGmail(thread)
        row, analyze_mock, _ = self.run_with_workspace(
            gmail, complete_analysis(location=""), gmail_drafts=True
        )
        self.assertEqual(row[:3], ("client-1", "draft_created", "cliente@example.com"))
        self.assertEqual(len(gmail.created_drafts), 1)
        analyzed_messages = analyze_mock.call_args.args[0]
        self.assertEqual([message["id"] for message in analyzed_messages], ["client-1"])

    def test_complete_thread_uses_readable_attachment_in_analysis(self):
        attachment = base64.urlsafe_b64encode(
            "ubicación,área\nBogotá,10 ha".encode("utf-8")
        ).decode()
        thread = {"id": "thread-csv", "messages": [encoded_message(
            "client-1", "cliente@example.com", "Solicito cotización de levantamiento.", 1,
            parts=[{
                "filename": "alcance.csv",
                "mimeType": "text/csv",
                "body": {"attachmentId": "att-1"},
            }],
        )]}
        gmail = FakeGmail(thread, {("client-1", "att-1"): attachment})
        row, analyze_mock, packets = self.run_with_workspace(
            gmail, complete_analysis(), review_only=True
        )
        self.assertEqual(row[1], "ready_review")
        attachments = analyze_mock.call_args.args[3]
        self.assertEqual(attachments[0]["status"], "extraido")
        self.assertIn("Bogotá,10 ha", attachments[0]["text"])
        self.assertIn('"status": "extraido"', packets[0])

    def test_unreadable_attachment_records_which_fields_are_blocked(self):
        attachment = base64.urlsafe_b64encode(b"binary-data").decode()
        thread = {"id": "thread-dwg", "messages": [encoded_message(
            "client-1", "cliente@example.com", "Solicito cotización de levantamiento.", 1,
            parts=[{
                "filename": "plano.dwg",
                "mimeType": "application/acad",
                "body": {"attachmentId": "att-1"},
            }],
        )]}
        gmail = FakeGmail(thread, {("client-1", "att-1"): attachment})
        row, _, packets = self.run_with_workspace(
            gmail, complete_analysis(location=""), review_only=True
        )
        saved = json.loads(row[3])
        self.assertEqual(row[1], "review")
        self.assertEqual(saved["attachments"][0]["status"], "bloqueado")
        self.assertIn("ubicación del proyecto", saved["attachments"][0]["blocked_fields"])
        self.assertIn("plano.dwg", packets[0])

    def test_forwarded_request_records_original_client_but_drafts_to_internal_reviewer(self):
        text = (
            "---------- Mensaje reenviado ---------\n"
            "De: Cliente Real <cliente@example.com>\n"
            "Asunto: Proyecto solar\n\nSolicitamos cotización de levantamiento."
        )
        thread = {"id": "thread-forwarded", "messages": [encoded_message(
            "forwarded-1", "Diego <diego@anka.com.co>", text, 1,
            subject="Fwd: Solicitud de cotización",
        )]}
        gmail = FakeGmail(thread)
        row, _, packets = self.run_with_workspace(
            gmail, complete_analysis(location=""), gmail_drafts=True
        )
        saved = json.loads(row[3])
        self.assertEqual(row[2], "diego@anka.com.co")
        self.assertEqual(saved["routing"]["original_sender_email"], "cliente@example.com")
        self.assertIn("Destinatario del borrador: diego@anka.com.co", packets[0])

    def test_pending_scope_dependency_forces_manual_review(self):
        analysis = complete_analysis()
        analysis["scope_dependencies"] = [{
            "description": "Mismas características de lo ya levantado",
            "source": "correo client-1",
            "status": "pendiente",
        }]
        thread = {"id": "thread-prior-scope", "messages": [encoded_message(
            "client-1", "cliente@example.com",
            "Solicito cotización con las mismas características de lo ya levantado.", 1,
        )]}
        row, _, packets = self.run_with_workspace(
            FakeGmail(thread), analysis, review_only=True
        )
        self.assertEqual(row[1], "review")
        self.assertIn("Dependencias de alcance", packets[0])





class DashboardSecurityTests(unittest.TestCase):
    def test_add_csrf_tokens_to_every_post_form(self):
        html_in = '<form method="post" action="/send"><button></button></form><form method="get"></form>'
        out = app.add_csrf_tokens(html_in)
        self.assertEqual(out.count('name="csrf_token"'), 1)
        self.assertIn(app.CSRF_TOKEN, out)

    def test_host_header_only_local(self):
        for ok in ("127.0.0.1:8765", "localhost:8765", "localhost", "[::1]:8765"):
            self.assertTrue(app.is_local_host_header(ok), ok)
        for bad in ("evil.example:8765", "127.0.0.1.evil.com", "", "192.168.1.5:8765"):
            self.assertFalse(app.is_local_host_header(bad), bad)

    def test_origin_must_match_host(self):
        self.assertTrue(app.is_same_origin("http://127.0.0.1:8765", "127.0.0.1:8765"))
        self.assertTrue(app.is_same_origin("", "127.0.0.1:8765"))
        self.assertFalse(app.is_same_origin("https://evil.example", "127.0.0.1:8765"))


class HandlerErrorTests(unittest.TestCase):
    def test_system_exit_from_config_returns_500_page(self):
        handler = app.ReviewHandler.__new__(app.ReviewHandler)
        sent = {}
        handler.headers = {"Host": "127.0.0.1:1", "Content-Length": "%d"}
        body = ("csrf_token=" + app.CSRF_TOKEN).encode()
        handler.headers["Content-Length"] = str(len(body))
        handler.path = "/refresh"
        handler.rfile = io.BytesIO(body)
        handler._send_html = lambda content, status=200: sent.update(status=status)
        with tempfile.TemporaryDirectory() as temp_dir, patch.object(
            app, "DATA_DIR", Path(temp_dir)
        ), patch("app.load_config", side_effect=SystemExit("falta config")):
            handler.do_POST()
        self.assertEqual(sent["status"], 500)


class ModeSwitchTests(unittest.TestCase):
    def switch(self, mode, env=None):
        with tempfile.TemporaryDirectory() as temp_dir, patch.object(
            app, "DATA_DIR", Path(temp_dir)
        ), patch.dict(os.environ, env or {}, clear=False):
            (Path(temp_dir) / "config.json").write_text(json.dumps({
                "active_mode": "chatgpt_work",
                "llm": {"provider": "ollama", "model": "qwen2.5:7b",
                        "base_url": "http://127.0.0.1:11434"},
                "required_fields": [],
            }), encoding="utf-8")
            try:
                message = app.set_mode(mode)
            except SystemExit as exc:
                return None, str(exc), json.loads(
                    (Path(temp_dir) / "config.json").read_text(encoding="utf-8"))
            return message, "", json.loads(
                (Path(temp_dir) / "config.json").read_text(encoding="utf-8"))

    def test_switch_to_local_sets_ollama(self):
        with patch("app.check_ollama"):
            message, error, config = self.switch("local")
        self.assertEqual(config["active_mode"], "local")
        self.assertEqual(config["llm"]["provider"], "ollama")
        self.assertIn("Work", message)

    def test_switch_to_local_blocked_when_ollama_down(self):
        with patch("urllib.request.urlopen", side_effect=urllib.error.URLError("refused")):
            message, error, config = self.switch("local")
        self.assertIn("Ollama no responde", error)
        self.assertEqual(config["active_mode"], "chatgpt_work")

    def test_switch_to_local_blocked_when_model_missing(self):
        fake = io.BytesIO(json.dumps({"models": [{"name": "llama3:8b"}]}).encode())
        with patch("urllib.request.urlopen", return_value=fake):
            message, error, config = self.switch("local")
        self.assertIn("falta el modelo", error)
        self.assertEqual(config["active_mode"], "chatgpt_work")

    def test_check_ollama_accepts_latest_tag(self):
        fake = io.BytesIO(json.dumps({"models": [{"name": "qwen2.5:7b"}]}).encode())
        with patch("urllib.request.urlopen", return_value=fake):
            app.check_ollama({"base_url": "http://127.0.0.1:11434", "model": "qwen2.5:7b"})

    def test_switch_to_api_requires_key(self):
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("OPENAI_API_KEY", None)
            message, error, config = self.switch("api")
        self.assertIn("OPENAI_API_KEY", error)
        self.assertEqual(config["active_mode"], "chatgpt_work")

    def test_switch_to_api_with_key(self):
        message, error, config = self.switch("api", {"OPENAI_API_KEY": "x"})
        self.assertEqual(config["active_mode"], "api")
        self.assertEqual(config["llm"]["provider"], "openai")

    def test_invalid_mode_rejected(self):
        message, error, config = self.switch("otro")
        self.assertTrue(error)

    def test_selector_flags_missing_api_key_but_keeps_option(self):
        from dashboard_views import mode_selector
        out = mode_selector("local", api_key_set=False)
        self.assertIn("value='api'", out)
        self.assertIn("sin clave", out)
        self.assertNotIn("sin clave", mode_selector("local", api_key_set=True))

    def test_selector_marks_current_mode(self):
        from dashboard_views import mode_selector
        out = mode_selector("api")
        self.assertIn("value='api' selected", out)
        self.assertNotIn("value='local' selected", out)


if __name__ == "__main__":
    unittest.main()
