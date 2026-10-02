"""Paridad entre los tres modos (Ollama, API y ChatGPT Work).

Las reglas de la Fase 1 viven en un solo lugar (`reglas_operativas.md`). Estas pruebas fallan si
el código Python (modos local y api) o la skill (modo Work) se separan de ese documento, o si los
dos motores de Python se comportan distinto con el mismo correo.
"""

from tests import _isolate  # noqa: F401  (debe ir primero)

import base64
import inspect
import json
import re
import tempfile
import unittest
from pathlib import Path
import io
from unittest.mock import MagicMock, patch

from anka.dominio.errores import ErrorDeAnka
from anka.servicios import drive_layout
from anka.infra import llm
from anka.servicios import pipeline
from anka.servicios import quotes
from anka.servicios import work_import
from anka.dominio import reception
from anka.infra import settings, store
from tests.fake_drive import FakeDriveTree
from anka.dominio.status import QuoteStatus
from tests.test_app import FakeGmail, complete_analysis, test_config
from tests.test_run_flow import DriveStructureFlowTests, RunFlowTests

class FakeResponse(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


SKILL_DIR = Path(__file__).resolve().parents[1] / "skills" / "anka-cotizaciones-correo"
RULES_FILE = SKILL_DIR / "references" / "reglas_operativas.md"
CASES_FILE = SKILL_DIR / "references" / "casos_paridad.json"
SKILL_FILE = SKILL_DIR / "SKILL.md"


def load_rules() -> dict:
    text = RULES_FILE.read_text(encoding="utf-8")
    block = re.search(r"```json reglas\n(.*?)\n```", text, re.S)
    assert block, "reglas_operativas.md debe tener el bloque ```json reglas"
    return json.loads(block.group(1))


def load_cases() -> dict:
    return json.loads(CASES_FILE.read_text(encoding="utf-8"))


def build_mail(case: dict) -> dict:
    mail = case["correo"]
    body = base64.urlsafe_b64encode(mail["cuerpo"].encode()).decode()
    headers = [{"name": "From", "value": mail["de"]}, {"name": "Subject", "value": mail["asunto"]}]
    headers += [{"name": k, "value": v} for k, v in mail["cabeceras"].items()]
    return {
        "id": "m-1", "labelIds": mail["etiquetas"] or ["INBOX"],
        "payload": {"headers": headers, "parts": [{"mimeType": "text/plain", "body": {"data": body}}]},
    }


# Cubre: T-11, R1, R4, R6, R7, R8
class RulesMatchTheCode(unittest.TestCase):
    """Modos local y api: el código implementa exactamente lo que dicen las reglas."""

    def setUp(self):
        self.rules = load_rules()

    def test_noise_signals_are_the_same_set_and_order(self):
        self.assertEqual(self.rules["descarte"]["senales"], list(reception.NOISE_SIGNALS))

    def test_every_noise_signal_is_exercised_by_the_shared_cases(self):
        covered = {c["senal"] for c in load_cases()["descarte"] if c["senal"]}
        self.assertEqual(covered, set(reception.NOISE_SIGNALS))

    def test_legal_suffixes_and_path_template(self):
        self.assertEqual(self.rules["drive"]["sufijos_societarios"], list(drive_layout.LEGAL_SUFFIXES))
        self.assertEqual(self.rules["drive"]["ruta"], drive_layout.PATH_TEMPLATE)

    def test_new_client_folders_are_never_created_by_default(self):
        self.assertIs(self.rules["drive"]["crear_cliente_automatico"], False)
        default = inspect.signature(drive_layout.create_project_folder).parameters["create_client"].default
        self.assertIs(default, False)
        self.assertIs(inspect.signature(drive_layout.resolve_client_folder).parameters["create"].default,
                      inspect.Parameter.empty)  # quien llama debe decidirlo de forma explícita

    def test_discard_needs_every_message_and_never_a_human_alone(self):
        self.assertTrue(self.rules["descarte"]["requiere_todos_los_mensajes"])
        self.assertTrue(self.rules["descarte"]["remitente_humano_nunca"])
        automated = build_mail(next(c for c in load_cases()["descarte"] if c["id"] == "no_reply"))
        human = build_mail(next(c for c in load_cases()["descarte"] if c["id"] == "explicita"))
        self.assertIsNotNone(reception.automated_noise_reason([automated, automated]))
        self.assertIsNone(reception.automated_noise_reason([automated, human]))
        self.assertIsNone(reception.automated_noise_reason([human]))

    def test_behavior_flags_match_what_the_pipeline_really_does(self):
        drive_rules = self.rules["drive"]
        flow = DriveStructureFlowTests()
        # sin cliente confirmado no se crea nada (R6 y R7)
        rows, _, _, drive = flow.run_complete()
        self.assertTrue(drive_rules["proyecto_requiere_cliente_confirmado"])
        self.assertEqual((rows[0][1], drive.created), (QuoteStatus.READY_REVIEW, []))
        # carpeta padre de otro año (R4)
        seed = ("t-1", "old", QuoteStatus.WAITING, "cliente@example.com", {})
        rows, _, _, drive = flow.run_complete(
            metadata={"t-1": "Minera Sur"}, seed=seed, drive=FakeDriveTree({"Minera Sur": {}}, root_name="2025"))
        self.assertTrue(drive_rules["bloquear_si_anio_distinto"])
        self.assertEqual((rows[0][1], drive.created), (QuoteStatus.READY_REVIEW, []))

    def test_pilot_mode_never_sends_email_on_its_own(self):
        self.assertIs(self.rules["correo_al_cliente"]["envio_automatico"], False)
        gmail = FakeGmail(RunFlowTests.thread())
        RunFlowTests().run_flow(gmail, complete_analysis(location=""), gmail_drafts=True, review_only=False)
        self.assertEqual(gmail.sent_messages, [])
        self.assertEqual(len(gmail.created_drafts), 1)


# Cubre: T-11
class SkillMirrorsTheRules(unittest.TestCase):
    """Modo Work: la skill copia las reglas y apunta a la fuente única."""

    def setUp(self):
        self.rules = load_rules()
        self.skill = SKILL_FILE.read_text(encoding="utf-8")
        self.rules_text = RULES_FILE.read_text(encoding="utf-8")

    def test_skill_links_the_single_source_of_truth(self):
        self.assertIn("references/reglas_operativas.md", self.skill)
        self.assertIn("casos_prueba.md", self.skill)

    def test_skill_declares_the_same_rules_version(self):
        declared = re.search(r"Reglas operativas \(versión (\d+)\)", self.skill)
        self.assertIsNotNone(declared, "SKILL.md debe decir 'Reglas operativas (versión N)'")
        self.assertEqual(int(declared.group(1)), self.rules["version"])
        in_md = re.search(r"\*\*Versión de las reglas: (\d+)\*\*", self.rules_text)
        self.assertEqual(int(in_md.group(1)), self.rules["version"])

    def test_every_rule_phrase_appears_in_the_skill(self):
        for rule_id, phrase in self.rules["frases_en_skill"].items():
            with self.subTest(rule=rule_id):
                self.assertIn(phrase.casefold(), self.skill.casefold())

    def test_the_same_rule_ids_exist_in_the_rules_file_and_in_the_skill(self):
        json_ids = set(self.rules["frases_en_skill"])
        md_ids = set(re.findall(r"^\| (R\d+) \|", self.rules_text, re.M))
        skill_ids = set(re.findall(r"\*\*(R\d+) ·", self.skill))
        self.assertEqual(json_ids, md_ids)
        self.assertEqual(json_ids, skill_ids)

    def test_the_skill_does_not_promise_an_automatic_send(self):
        self.assertNotRegex(self.skill.casefold(), r"envía(r)? automáticamente")


# Cubre: T-11, R1, R5
class SharedCaseCatalogue(unittest.TestCase):
    """El mismo catálogo que se usa a mano para probar la skill se ejecuta contra el código."""

    def setUp(self):
        self.cases = load_cases()
        self.terms = self.cases["terminos_rescate"]

    def decide(self, case):
        message = build_mail(case)
        reason = reception.automated_noise_reason([message])
        if reason and reception.mentions_commercial_terms([message], self.terms):
            return "pasa", None
        return ("descarta", reason) if reason else ("pasa", None)

    def test_every_case_gets_the_expected_decision(self):
        for case in self.cases["descarte"]:
            with self.subTest(case=case["id"]):
                decision, reason = self.decide(case)
                self.assertEqual(decision, case["esperado"], case["descripcion"])
                if case["senal"]:
                    self.assertEqual(reason, reception.NOISE_SIGNALS[case["senal"]])

    def test_client_names(self):
        for pair in self.cases["clientes"]:
            with self.subTest(a=pair["a"], b=pair["b"]):
                same = drive_layout.normalize_client_name(pair["a"]) == drive_layout.normalize_client_name(pair["b"])
                self.assertEqual(same, pair["igual"])

    def test_year_rule(self):
        for case in self.cases["anios"]:
            with self.subTest(parent=case["carpeta_padre"], date=case["fecha_solicitud"][:10]):
                drive = FakeDriveTree(root_name=case["carpeta_padre"])
                blocked = drive_layout.year_mismatch(drive, drive.root_id, case["fecha_solicitud"]) is not None
                self.assertEqual(blocked, case["bloquea"])

    def test_the_catalogue_is_documented_for_manual_testing_in_work(self):
        doc = (SKILL_DIR / "references" / "casos_prueba.md").read_text(encoding="utf-8")
        self.assertIn("casos_paridad.json", doc)


# Cubre: R11, T-03
class ChangeRuleR11(unittest.TestCase):
    """R11: con T asignada, la tabla solo cambia por el contacto registrado citando el código.

    El mismo catálogo se ejecuta por el camino de Work (dashboard_upsert, con los campos que informa
    la skill) y por el de Python (apply_guard, usado por el piloto); ambos deben decidir igual.
    """

    OLD = [{"activity": "a", "quantity": "40", "unit": "días"}]
    NEW = [{"activity": "a", "quantity": "30", "unit": "días"}]

    def setUp(self):
        self.rules = load_rules()["cambios"]
        self.cases = load_cases()["cambios"]
        self.fresh()

    def fresh(self):
        """Base de datos vacía (el código de una T es único, así que cada caso necesita la suya)."""
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        patcher = patch.object(settings, "DATA_DIR", Path(temp.name))
        patcher.start()
        self.addCleanup(patcher.stop)

    @staticmethod
    def b64(payload):
        return base64.urlsafe_b64encode(json.dumps(payload).encode()).decode()

    def seed(self, thread, code):
        """Caso con tabla de 40 días, contacto Ana y (opcional) T asignada a las 10:00."""
        from anka.servicios import change_control
        work_import.dashboard_upsert(self.b64({
            "thread_id": thread, "message_id": "m0", "status": "Faltan datos", "engine": "chatgpt_work",
            "client_email": "ana@empresa.co", "client_name": "Ana", "project_name": "P",
            "activities": self.OLD, "received_at": "2026-10-01T09:00:00-05:00"}))
        if code:
            change_control.assign_code(thread, code)
            db = store.connect()
            db.execute("UPDATE quotes SET code_assigned_at='2026-10-01T10:00:00-05:00' WHERE thread_id=?",
                       (thread,))
            db.commit()
            db.close()

    def table(self, thread):
        quote = quotes.get_quote(thread)
        return quote["analysis"]["activities"], quote["analysis"].get("pending_model_activities")

    def expected(self, case):
        return (self.OLD, self.NEW) if case["esperado"] == "pendiente" else (self.NEW, None)

    def test_python_constants_match_the_rules_file(self):
        from anka.servicios import change_control
        self.assertIs(self.rules["tabla_solo_contacto_con_codigo"],
                      change_control.TABLE_UPDATE_ONLY_BY_CONTACT_WITH_CODE)
        self.assertIs(self.rules["correo_a_no_contacto_revela_codigo"],
                      change_control.MAIL_TO_NON_CONTACT_REVEALS_CODE)
        self.assertEqual(self.rules["se_exige_desde"], "asignacion_de_la_t")
        self.assertTrue(change_control.CODE_REQUIRED_AFTER_ASSIGNMENT)

    def test_every_case_through_the_work_path(self):
        from anka.servicios import change_control
        for case in self.cases:
            with self.subTest(case=case["id"]):
                self.fresh()
                self.seed(case["id"], case["codigo_del_caso"])
                sender = case["de"].split("<")[-1].strip(" >")
                moment = "2026-10-01T09:30:00-05:00" if case["anterior_a_la_t"] else "2026-10-01T11:00:00-05:00"
                work_import.dashboard_upsert(self.b64({
                    "thread_id": case["id"], "message_id": "m1", "status": "Faltan datos",
                    "engine": "chatgpt_work", "client_email": "ana@empresa.co", "client_name": "Ana",
                    "project_name": "P", "activities": self.NEW, "received_at": moment,
                    "requested_by_email": sender, "requested_by_name": case["de"].split("<")[0].strip(),
                    "cited_codes": sorted(change_control.find_codes(case["cuerpo"]))}))
                self.assertEqual(self.table(case["id"]), self.expected(case), case["descripcion"])

    def test_every_case_through_the_python_path(self):
        from anka.servicios import change_control
        for case in self.cases:
            with self.subTest(case=case["id"]):
                self.fresh()
                self.seed(case["id"], case["codigo_del_caso"])
                analysis = {"activities": [dict(r) for r in self.NEW], "project_name": "P",
                            "received_at": "2026-10-01 09:30" if case["anterior_a_la_t"] else "2026-10-01 11:00"}
                db = store.connect()
                change_control.apply_guard(
                    db, case["id"], analysis, case["de"].split("<")[-1].strip(" >"), "x",
                    sorted(change_control.find_codes(case["cuerpo"])), "m1", "Asunto", "texto", "python")
                db.close()
                expected_table, expected_pending = self.expected(case)
                self.assertEqual(analysis["activities"], expected_table, case["descripcion"])
                self.assertEqual(analysis.get("pending_model_activities"), expected_pending)

    def test_work_must_report_who_wrote_otherwise_the_table_is_kept(self):
        self.seed("sin-autor", "26145")
        work_import.dashboard_upsert(self.b64({
            "thread_id": "sin-autor", "message_id": "m1", "status": "Faltan datos", "engine": "chatgpt_work",
            "client_email": "ana@empresa.co", "project_name": "P", "activities": self.NEW,
            "received_at": "2026-10-01T11:00:00-05:00"}))
        self.assertEqual(self.table("sin-autor"), (self.OLD, self.NEW))
        kinds = {e["kind"] for e in quotes.thread_events("sin-autor")}
        self.assertIn("table_locked", kinds)

    def test_a_blocked_message_becomes_a_pending_change_for_the_dashboard(self):
        self.seed("c1", "26145")
        work_import.dashboard_upsert(self.b64({
            "thread_id": "c1", "message_id": "m1", "status": "Faltan datos", "engine": "chatgpt_work",
            "client_email": "ana@empresa.co", "project_name": "P", "activities": self.NEW,
            "received_at": "2026-10-01T11:00:00-05:00", "requested_by_email": "luis@empresa.co",
            "requested_by_name": "Luis", "cited_codes": []}))
        change = quotes.get_quote("c1")["changes"][0]
        self.assertEqual((change["is_contact"], change["has_code"], change["same_company"]), (0, 0, 1))

    def test_the_pilot_pipeline_applies_the_same_rule(self):
        """Integración: run() de Python con un mensaje de otra persona sobre un caso con T."""
        thread = RunFlowTests.thread("Ahora el plazo es de 30 días", thread_id="t-1")
        gmail = FakeGmail(thread)
        drive = FakeDriveTree({"Minera Sur": {}})
        with patch("anka.servicios.pipeline.load_config", return_value=test_config()), patch(
                "anka.servicios.pipeline.services", return_value=(gmail, drive)), patch(
                "anka.servicios.pipeline.analyze", return_value=complete_analysis(location="")):
            self.seed("t-1", "26145")
            db = store.connect()
            db.execute("UPDATE quotes SET contact_email='ana@empresa.co', last_inbound_id='old', "
                       "status='waiting', code_assigned_at='2000-01-01T00:00:00-05:00' WHERE thread_id='t-1'")
            db.commit()
            db.close()
            with patch("anka.servicios.pipeline.analyze", return_value=complete_analysis(location="")):
                pipeline.run(True, review_only=True)
        activities, pending = self.table("t-1")
        self.assertEqual(activities, self.OLD)          # el cliente de la prueba no es Ana: no cambia
        self.assertTrue(pending)
        self.assertTrue(quotes.get_quote("t-1")["changes"])


# Cubre: T-11
class OllamaAndApiBehaveTheSame(unittest.TestCase):
    """Los modos local y api comparten el flujo: solo cambia quién analiza el correo."""

    def run_mode(self, mode):
        config = test_config() | (
            {"active_mode": "api", "llm": {"provider": "openai", "model": "gpt-x"}} if mode == "api" else {}
        )
        result = complete_analysis(location="") | {"summary": "Resumen del modelo"}
        prompts = []

        def fake_provider(prompt, llm_config):
            prompts.append(prompt)
            return json.loads(json.dumps(result))

        gmail = FakeGmail(RunFlowTests.thread())
        with tempfile.TemporaryDirectory() as d, patch.object(settings, "DATA_DIR", Path(d)), \
                patch("anka.servicios.pipeline.load_config", return_value=config), \
                patch("anka.servicios.pipeline.services", return_value=(gmail, FakeDriveTree())), \
                patch("anka.infra.llm.analyze_ollama", side_effect=fake_provider), \
                patch("anka.infra.llm.analyze_openai", side_effect=fake_provider):
            pipeline.run(True, gmail_drafts=True)
            row = [(q["status"], q["recipient"], q["analysis"], q["draft_body"]) for q in quotes.all_quotes(True)]
        return row, prompts, gmail

    def test_same_email_same_outcome_and_same_prompt(self):
        local_row, local_prompts, local_gmail = self.run_mode("local")
        api_row, api_prompts, api_gmail = self.run_mode("api")
        self.assertEqual(local_row, api_row)
        self.assertEqual(local_prompts, api_prompts)
        self.assertEqual(len(local_gmail.created_drafts), len(api_gmail.created_drafts))
        self.assertEqual(local_gmail.sent_messages, api_gmail.sent_messages)
        self.assertEqual(local_row[0][0], QuoteStatus.DRAFT_CREATED)

    def test_each_mode_calls_only_its_own_engine(self):
        with patch("anka.infra.llm.analyze_ollama", return_value=complete_analysis()) as ollama, \
                patch("anka.infra.llm.analyze_openai", return_value=complete_analysis()) as openai_call:
            llm.analyze([], [], {"provider": "ollama", "base_url": "http://x"})
            llm.analyze([], [], {"provider": "openai"})
        self.assertEqual((ollama.call_count, openai_call.call_count), (1, 1))

    def test_the_prompt_cannot_depend_on_the_engine(self):
        parameters = inspect.signature(llm.analysis_prompt).parameters
        self.assertEqual(list(parameters), ["messages", "required_fields", "attachments"])

    def test_both_engines_send_the_same_output_schema_to_their_service(self):
        sent = {}

        def fake_urlopen(request, timeout):
            sent["ollama"] = json.loads(request.data.decode("utf-8"))["format"]
            return FakeResponse(json.dumps({"response": "{}"}).encode())

        client = MagicMock()
        client.responses.create.return_value.output_text = "{}"
        with patch("urllib.request.urlopen", fake_urlopen):
            with patch.dict("os.environ", {"OPENAI_API_KEY": "sk-test"}):
                with patch("openai.OpenAI", return_value=client):
                    llm.analyze_ollama("p", {"base_url": "http://x"})
                    llm.analyze_openai("p", {})
        sent["openai"] = client.responses.create.call_args.kwargs["text"]["format"]["schema"]
        self.assertEqual(sent["ollama"], sent["openai"])
        self.assertEqual(sent["ollama"], llm.ANALYSIS_SCHEMA)


# Cubre: T-11
class WorkModeIsTheOtherOrchestrator(unittest.TestCase):
    def test_the_three_modes_are_the_ones_the_standard_documents(self):
        self.assertEqual(settings.ACTIVE_MODES, {"local", "api", "chatgpt_work"})
        doc = (Path(__file__).resolve().parents[1] / "docs/ESTANDAR_CAMBIOS.md").read_text(encoding="utf-8")
        for mode in settings.ACTIVE_MODES:
            self.assertIn(mode, doc)

    def test_python_orchestrator_refuses_to_run_in_work_mode(self):
        config = test_config() | {"active_mode": "chatgpt_work"}
        with patch("anka.servicios.pipeline.load_config", return_value=config), \
                patch("anka.servicios.pipeline.services") as services, self.assertRaises(ErrorDeAnka):
            pipeline.run(True, gmail_drafts=True)
        services.assert_not_called()  # ni siquiera toca Gmail

    def test_each_python_mode_requires_its_matching_engine(self):
        base = {"required_fields": [], "llm": {"provider": "openai"}}
        self.assertTrue(settings.validate_config(base | {"active_mode": "local"}))
        self.assertTrue(settings.validate_config(
            base | {"active_mode": "api", "llm": {"provider": "ollama", "base_url": "http://x"}}))
        self.assertEqual(settings.validate_config(base | {"active_mode": "api"}), [])
        self.assertEqual(settings.validate_config(base | {"active_mode": "chatgpt_work"}), [])


if __name__ == "__main__":
    unittest.main()
