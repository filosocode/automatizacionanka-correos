"""Fronteras con el exterior: LLM (Ollama/OpenAI), Google (Drive/OAuth) e índice comercial.

Ningún test sale a la red: urlopen, OpenAI y los servicios de Google se sustituyen.
"""

from tests import _isolate  # noqa: F401  (debe ir primero)

import io
import json
import sys
import tempfile
import unittest
import urllib.error
import zipfile
from pathlib import Path
from unittest.mock import MagicMock, patch

from anka.dominio.errores import AutorizacionDeGoogle, ErrorDeAnka
from anka.servicios import commercial_index
from anka.infra import google_services
from anka.infra import llm
from anka.infra import settings
from tests.test_app import complete_analysis, encoded_message

LLM_OLLAMA = {"provider": "ollama", "base_url": "http://ollama.local:11434/", "model": "m1"}


def model_result(**extra):
    result = complete_analysis()
    result.update(extra)
    return result


class FakeHttpResponse(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def ollama_reply(result):
    return FakeHttpResponse(json.dumps({"response": json.dumps(result)}).encode("utf-8"))


# Cubre: T-11
class OllamaTests(unittest.TestCase):
    def test_sends_schema_and_zero_temperature_to_the_configured_server(self):
        captured = {}

        def fake_urlopen(request, timeout):
            captured["url"] = request.full_url
            captured["body"] = json.loads(request.data.decode("utf-8"))
            captured["timeout"] = timeout
            return ollama_reply({"is_quote_request": True})

        with patch("urllib.request.urlopen", fake_urlopen):
            result = llm.analyze_ollama("prompt", {**LLM_OLLAMA, "timeout_seconds": 7})
        self.assertEqual(result, {"is_quote_request": True})
        self.assertEqual(captured["url"], "http://ollama.local:11434/api/generate")
        self.assertEqual(captured["timeout"], 7)
        self.assertEqual(captured["body"]["model"], "m1")
        self.assertIs(captured["body"]["stream"], False)
        self.assertEqual(captured["body"]["options"], {"temperature": 0})
        self.assertEqual(captured["body"]["format"], llm.ANALYSIS_SCHEMA)

    def test_connection_error_becomes_a_readable_runtime_error(self):
        with patch("urllib.request.urlopen", side_effect=urllib.error.URLError("rechazada")):
            with self.assertRaisesRegex(RuntimeError, "No se pudo conectar con Ollama"):
                llm.analyze_ollama("prompt", LLM_OLLAMA)

    def test_model_output_that_is_not_json_raises(self):
        broken = FakeHttpResponse(json.dumps({"response": "no soy json"}).encode())
        with patch("urllib.request.urlopen", return_value=broken):
            with self.assertRaises(ValueError):
                llm.analyze_ollama("prompt", LLM_OLLAMA)


# Cubre: T-11
class OpenAiTests(unittest.TestCase):
    def test_requires_api_key(self):
        with patch.dict("os.environ", {}, clear=True):
            with self.assertRaisesRegex(ErrorDeAnka, "OPENAI_API_KEY"):
                llm.analyze_openai("prompt", {"provider": "openai"})

    def test_uses_strict_schema_without_storing(self):
        client = MagicMock()
        client.responses.create.return_value.output_text = json.dumps({"ok": 1})
        with patch.dict("os.environ", {"OPENAI_API_KEY": "sk-test"}), \
                patch("openai.OpenAI", return_value=client):
            result = llm.analyze_openai("prompt", {"provider": "openai", "model": "gpt-x"})
        self.assertEqual(result, {"ok": 1})
        kwargs = client.responses.create.call_args.kwargs
        self.assertEqual(kwargs["model"], "gpt-x")
        self.assertIs(kwargs["store"], False)
        self.assertIs(kwargs["text"]["format"]["strict"], True)
        self.assertEqual(kwargs["text"]["format"]["schema"], llm.ANALYSIS_SCHEMA)


# Cubre: F1-03, T-11
class AnalyzeTests(unittest.TestCase):
    messages = [encoded_message("c-1", "cliente@example.com", "Hola, necesito información.", 1)]

    def analyze_with(self, result, messages=None, provider="ollama"):
        with patch("anka.infra.llm.analyze_ollama", return_value=result), \
                patch("anka.infra.llm.analyze_openai", return_value=result):
            return llm.analyze(messages or self.messages, ["ubicación"],
                               {**LLM_OLLAMA, "provider": provider})

    def test_dispatches_to_the_configured_provider_only(self):
        with patch("anka.infra.llm.analyze_ollama", return_value=model_result()) as ollama, \
                patch("anka.infra.llm.analyze_openai") as openai_call:
            llm.analyze(self.messages, [], LLM_OLLAMA)
        ollama.assert_called_once()
        openai_call.assert_not_called()

    def test_unknown_provider_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "no soportado"):
            self.analyze_with(model_result(), provider="otro")

    def test_non_dict_or_bad_activities_are_rejected(self):
        for bad in ([], "texto", model_result(activities="no es lista")):
            with self.subTest(bad=bad), self.assertRaisesRegex(ValueError, "inesperada"):
                self.analyze_with(bad)

    def test_missing_required_keys_raise_a_clear_error_not_keyerror(self):
        incomplete = {"activities": []}
        with self.assertRaisesRegex(ValueError, "is_quote_request"):
            self.analyze_with(incomplete)

    def test_explicit_request_overrides_a_false_negative(self):
        explicit = [encoded_message(
            "c-2", "cliente@example.com", "Solicitamos una cotización de levantamiento.", 1)]
        result = self.analyze_with(model_result(is_quote_request=False), explicit)
        self.assertTrue(result["is_quote_request"])

    def test_vague_message_keeps_the_model_negative(self):
        result = self.analyze_with(model_result(is_quote_request=False))
        self.assertFalse(result["is_quote_request"])

    def test_analyze_text_wraps_plain_text_as_a_message(self):
        with patch("anka.infra.llm.analyze_ollama", return_value=model_result()) as call:
            llm.analyze_text("Necesito cotizar un levantamiento", ["x"], LLM_OLLAMA)
        prompt = call.call_args.args[0]
        self.assertIn("Necesito cotizar un levantamiento", prompt)
        self.assertIn("Mensaje de prueba", prompt)


# Cubre: T-11, R12
class PromptTests(unittest.TestCase):
    def test_prompt_treats_email_as_untrusted_and_includes_readable_attachments_only(self):
        messages = [encoded_message("c-1", "cliente@example.com", "Texto del cliente.", 1)]
        attachments = [
            {"status": "extraido", "filename": "alcance.csv", "source_message_id": "c-1",
             "text": "ubicación,área"},
            {"status": "bloqueado", "filename": "plano.dwg", "source_message_id": "c-1",
             "text": "NO DEBE APARECER"},
        ]
        prompt = llm.analysis_prompt(messages, ["ubicación del proyecto"], attachments)
        self.assertIn("datos no confiables", prompt)
        self.assertIn("Texto del cliente.", prompt)
        self.assertIn("alcance.csv", prompt)
        self.assertIn("ubicación,área", prompt)
        self.assertNotIn("NO DEBE APARECER", prompt)
        self.assertIn('"ubicación del proyecto"', prompt)


class FakeDrive:
    """Drive mínimo: files().list() con paginación y files().create()."""

    def __init__(self, pages):
        self.pages = pages
        self.queries = []
        self.created = []

    def files(self):
        return self

    def list(self, q, fields, pageSize, pageToken):
        self.queries.append(q)
        index = 0 if pageToken is None else int(pageToken)
        page = dict(self.pages[index])
        if index + 1 < len(self.pages):
            page["nextPageToken"] = str(index + 1)
        return MagicMock(execute=lambda: page)

    def create(self, body, fields):
        self.created.append(body)
        return MagicMock(execute=lambda: {"id": "new-1", "name": body["name"]})


# Cubre: F1-07
class DriveTests(unittest.TestCase):
    def test_list_folders_follows_pages_and_sorts_case_insensitively(self):
        drive = FakeDrive([
            {"files": [{"id": "1", "name": "beta"}]},
            {"files": [{"id": "2", "name": "Alfa"}, {"id": "3", "name": "cuarta"}]},
        ])
        names = [f["name"] for f in google_services.list_folders(drive, "parent")]
        self.assertEqual(names, ["Alfa", "beta", "cuarta"])
        self.assertEqual(len(drive.queries), 2)

    def test_list_folders_escapes_quotes_in_the_parent_id(self):
        drive = FakeDrive([{"files": []}])
        google_services.list_folders(drive, "a'b\\c")
        self.assertIn("'a\\'b\\\\c' in parents", drive.queries[0])
        self.assertIn("trashed = false", drive.queries[0])

    def test_ensure_folder_reuses_an_existing_folder(self):
        drive = FakeDrive([{"files": [{"id": "9", "name": "Cotizacion X"}]}])
        folder = google_services.ensure_folder(drive, "parent", "Cotizacion X")
        self.assertEqual(folder["id"], "9")
        self.assertEqual(drive.created, [])

    def test_ensure_folder_creates_it_under_the_parent_when_missing(self):
        drive = FakeDrive([{"files": []}])
        folder = google_services.ensure_folder(drive, "parent", "Cotizacion Y")
        self.assertEqual(folder["id"], "new-1")
        self.assertEqual(drive.created[0]["parents"], ["parent"])
        self.assertEqual(drive.created[0]["mimeType"], google_services.FOLDER_MIME)


# Cubre: T-06
class CredentialsTests(unittest.TestCase):
    def test_a_missing_token_asks_to_authorize_and_never_opens_a_browser(self):
        with tempfile.TemporaryDirectory() as d, patch.object(settings, "DATA_DIR", Path(d)),                 patch("google_auth_oauthlib.flow.InstalledAppFlow.from_client_secrets_file") as flow:
            with self.assertRaisesRegex(AutorizacionDeGoogle, "Reautorizar Google"):
                google_services.credentials()
            flow.assert_not_called()                 # sin nadie delante no se abre ningún consentimiento

    def test_authorizing_needs_the_client_secret_file(self):
        with tempfile.TemporaryDirectory() as d, patch.object(settings, "DATA_DIR", Path(d)):
            with self.assertRaisesRegex(ErrorDeAnka, "credentials.json"):
                google_services.authorize_interactive()

    def test_valid_saved_token_is_reused_without_rewriting_it(self):
        creds = MagicMock(expired=False, valid=True)
        creds.has_scopes.return_value = True
        creds.to_json.return_value = '{"token": "t"}'
        with tempfile.TemporaryDirectory() as d, patch.object(settings, "DATA_DIR", Path(d)),                 patch("google.oauth2.credentials.Credentials.from_authorized_user_file",
                      return_value=creds) as load:
            (Path(d) / "token.json").write_text("{}", encoding="utf-8")
            result = google_services.credentials()
            self.assertIs(result, creds)
            load.assert_called_once()
            self.assertEqual((Path(d) / "token.json").read_text(encoding="utf-8"), "{}")   # no hizo falta renovar

    def test_a_renewed_token_is_saved(self):
        creds = MagicMock(expired=True, refresh_token="r", valid=True)
        creds.has_scopes.return_value = True
        creds.to_json.return_value = '{"token": "renovado"}'
        with tempfile.TemporaryDirectory() as d, patch.object(settings, "DATA_DIR", Path(d)), \
                patch("google.oauth2.credentials.Credentials.from_authorized_user_file", return_value=creds), \
                patch("google.auth.transport.requests.Request"):
            (Path(d) / "token.json").write_text("{}", encoding="utf-8")
            google_services.credentials()
            self.assertEqual((Path(d) / "token.json").read_text(encoding="utf-8"), '{"token": "renovado"}')

    def test_a_token_google_no_longer_accepts_asks_to_reauthorize(self):
        from google.auth.exceptions import RefreshError
        creds = MagicMock(expired=True, refresh_token="r")
        creds.has_scopes.return_value = True
        creds.refresh.side_effect = RefreshError("invalid_grant")
        with tempfile.TemporaryDirectory() as d, patch.object(settings, "DATA_DIR", Path(d)),                 patch("google.oauth2.credentials.Credentials.from_authorized_user_file", return_value=creds):
            (Path(d) / "token.json").write_text("{}", encoding="utf-8")
            with self.assertRaises(AutorizacionDeGoogle) as raised:
                google_services.credentials()
        self.assertIn("python app.py auth", str(raised.exception))
        self.assertNotIn("invalid_grant", str(raised.exception))     # el texto técnico de Google no se muestra

    def test_token_with_missing_scopes_is_not_trusted(self):
        creds = MagicMock()
        creds.has_scopes.return_value = False
        with tempfile.TemporaryDirectory() as d, patch.object(settings, "DATA_DIR", Path(d)),                 patch("google.oauth2.credentials.Credentials.from_authorized_user_file",
                      return_value=creds):
            (Path(d) / "token.json").write_text("{}", encoding="utf-8")
            with self.assertRaisesRegex(AutorizacionDeGoogle, "permisos"):
                google_services.credentials()

    def test_a_completed_authorization_replaces_the_token_and_a_failed_one_keeps_the_old(self):
        new = MagicMock()
        new.to_json.return_value = '{"token": "nuevo"}'
        with tempfile.TemporaryDirectory() as d, patch.object(settings, "DATA_DIR", Path(d)),                 patch("google_auth_oauthlib.flow.InstalledAppFlow.from_client_secrets_file") as flow:
            (Path(d) / "credentials.json").write_text("{}", encoding="utf-8")
            (Path(d) / "token.json").write_text('{"token": "viejo"}', encoding="utf-8")
            flow.return_value.run_local_server.side_effect = TimeoutError("sin respuesta")
            with self.assertRaises(TimeoutError):
                google_services.authorize_interactive(timeout_seconds=1)
            self.assertEqual((Path(d) / "token.json").read_text(encoding="utf-8"), '{"token": "viejo"}')
            flow.return_value.run_local_server.side_effect = None
            flow.return_value.run_local_server.return_value = new
            google_services.authorize_interactive(timeout_seconds=300)
            self.assertEqual((Path(d) / "token.json").read_text(encoding="utf-8"), '{"token": "nuevo"}')
            self.assertEqual(flow.return_value.run_local_server.call_args.kwargs["timeout_seconds"], 300)

    def test_services_builds_gmail_and_drive_with_the_same_credentials(self):
        with patch("anka.infra.google_services.credentials", return_value="CREDS"), \
                patch("googleapiclient.discovery.build", side_effect=lambda *a, **k: (a, k)) as build:
            gmail, drive = google_services.services()
        self.assertEqual(gmail[0], ("gmail", "v1"))
        self.assertEqual(drive[0], ("drive", "v3"))
        self.assertEqual(build.call_count, 2)
        self.assertEqual(gmail[1]["credentials"], "CREDS")


def make_docx(path: Path, text: str) -> None:
    xml = ('<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
           f"<w:body><w:p><w:r><w:t>{text}</w:t></w:r></w:p></w:body></w:document>")
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("word/document.xml", xml)


# Cubre: F1-05
class CommercialIndexTests(unittest.TestCase):
    def build(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name)
        client = root / "Energia Solar SAS" / "2024 subestacion"
        client.mkdir(parents=True)
        (client / "Propuesta LiDAR corredor.pdf").write_bytes(b"%PDF-1.4 vacio")
        make_docx(client / "Solicitud cotizacion topografia.docx",
                  "Enviar ubicacion, coordenadas y plazo de entrega. Formato dwg.")
        (client / "Hoja de vida Juan Perez.pdf").write_bytes(b"dato personal")
        (client / "notas.txt").write_text("libre", encoding="utf-8")
        (root / "Carpeta Vacia").mkdir()
        return commercial_index.build_index(root)

    def test_builds_a_profile_per_client_and_skips_empty_folders(self):
        index = self.build()
        self.assertEqual(index["summary"]["client_profiles"], 1)
        profile = index["clients"]["energia solar sas"]
        self.assertEqual(profile["client_name"], "Energia Solar SAS")
        self.assertEqual(profile["company_type"], "Empresa de energia")

    def test_sensitive_personnel_files_are_excluded(self):
        profile = self.build()["clients"]["energia solar sas"]
        self.assertEqual(profile["file_count"], 3)  # 4 archivos menos la hoja de vida
        self.assertNotIn("Hoja de vida", json.dumps(profile))

    def test_project_types_sectors_and_requirements_are_inferred(self):
        profile = self.build()["clients"]["energia solar sas"]
        self.assertIn("LiDAR", profile["project_types"])
        self.assertIn("Energia", profile["sectors"])
        self.assertIn("Ubicacion, poligono o coordenadas", profile["usual_requests"])
        self.assertIn("Plazo o cronograma", profile["usual_requests"])
        self.assertEqual(profile["request_document_count"], 1)

    def test_index_declares_internal_only_and_stores_no_raw_text_or_prices(self):
        index = self.build()
        privacy = index["privacy"]
        self.assertTrue(privacy["internal_only"])
        self.assertFalse(privacy["raw_text_stored"])
        self.assertFalse(privacy["prices_stored"])
        self.assertFalse(privacy["outbound_use_allowed"])
        self.assertNotIn("Enviar ubicacion, coordenadas", json.dumps(index))

    def test_missing_source_is_reported(self):
        with self.assertRaises(FileNotFoundError):
            commercial_index.build_index(Path("no/existe/de/verdad"))

    def test_corrupt_docx_and_pdf_yield_empty_text(self):
        with tempfile.TemporaryDirectory() as d:
            bad_docx = Path(d) / "roto.docx"
            bad_docx.write_bytes(b"no es un zip")
            bad_pdf = Path(d) / "roto.pdf"
            bad_pdf.write_bytes(b"no es un pdf")
            self.assertEqual(commercial_index.extract_docx_text(bad_docx), "")
            self.assertEqual(commercial_index.extract_pdf_text(bad_pdf), "")

    def test_company_type_falls_back_to_generic_private_or_unclassified(self):
        self.assertEqual(commercial_index.inferred_company_type("Acme Corp"),
                         ("Empresa privada", "baja"))
        self.assertEqual(commercial_index.inferred_company_type("Juan"),
                         ("No clasificado", "baja"))

    def test_main_writes_the_index_where_asked(self):
        with tempfile.TemporaryDirectory() as d:
            source = Path(d) / "src"
            (source / "Cliente Uno").mkdir(parents=True)
            (source / "Cliente Uno" / "propuesta.docx").write_bytes(b"x")
            output = Path(d) / "out" / "index.json"
            argv = ["commercial_index.py", "--source", str(source), "--output", str(output)]
            with patch.object(sys, "argv", argv), patch("sys.stdout", new=io.StringIO()):
                commercial_index.main()
            written = json.loads(output.read_text(encoding="utf-8"))
            self.assertEqual(written["schema_version"], 1)
            self.assertIn("cliente uno", written["clients"])


if __name__ == "__main__":
    unittest.main()
