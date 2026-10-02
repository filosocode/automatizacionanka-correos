"""Contrato Work ↔ puente: spec/contrato_work.schema.json es la fuente de verdad.

Cubre: T-02
"""

import _isolate  # noqa: F401  (debe ir primero)

import json
import unittest
from pathlib import Path

import work_contract
import work_dashboard_sync as sync
from status import WORK_STATUS_LABELS
from test_work_sync import draft_message, payload

SCHEMA = work_contract.load_schema()
INSTRUCTIONS = (Path(__file__).parent / "docs/INSTRUCCIONES.txt").read_text(encoding="utf-8")
BRIDGE_ONLY = {"draft_reply", "draft_id"}   # los agrega el puente, Work no los escribe


class SchemaTests(unittest.TestCase):
    def test_a_complete_payload_is_valid(self):
        data = payload(
            client_name="Ana", summary="x", received_at="2026-10-01T10:00:00-05:00", gmail_url="https://x",
            requested_by_email="ana@acme.com", requested_by_name="Ana", cited_codes=["T26145"],
            missing_data=["Crítico: datum"], attachments=[{"filename": "a.kml", "status": "legible", "reason": "",
                                                          "blocked_fields": []}],
            contradictions=[{"topic": "plazo", "details": "30/40", "sources": ["a"], "status": "pendiente"}],
            scope_dependencies=[{"description": "KML", "source": "a", "status": "pendiente"}])
        self.assertEqual(work_contract.validate(data), [])

    def test_every_required_field_is_enforced(self):
        for field in SCHEMA["required"]:
            with self.subTest(field=field):
                data = payload()
                data.pop(field)
                self.assertTrue(any(field in error for error in work_contract.validate(data)))

    def test_blank_required_text_is_rejected(self):
        self.assertTrue(work_contract.validate(payload(project_name="   ")))

    def test_status_must_be_an_official_label_and_the_engine_chatgpt_work(self):
        self.assertTrue(work_contract.validate(payload(status="Cualquier cosa")))
        self.assertTrue(work_contract.validate(payload(engine="otro")))

    def test_types_are_checked(self):
        for field, wrong in (("cited_codes", "T26145"), ("activities", {"a": 1}), ("missing_data", "x"),
                             ("requested_by_email", 5), ("attachments", [5]), ("cited_codes", [1])):
            with self.subTest(field=field):
                self.assertTrue(work_contract.validate(payload(**{field: wrong})))

    def test_null_optional_values_are_tolerated_but_not_null_required_ones(self):
        self.assertEqual(work_contract.validate(payload(client_name=None, summary=None, cited_codes=None)), [])
        self.assertTrue(work_contract.validate(payload(project_name=None)))

    def test_activities_may_be_text_rows(self):
        self.assertEqual(work_contract.validate(payload(activities=["Levantamiento de 5 ha", {"activity": "b"}])), [])

    def test_unknown_extra_fields_are_allowed(self):
        self.assertEqual(work_contract.validate(payload(campo_nuevo="x")), [])


class SchemaMatchesTheRestOfTheSystem(unittest.TestCase):
    def test_statuses_in_the_schema_are_the_statuses_the_dashboard_understands(self):
        self.assertEqual(tuple(SCHEMA["properties"]["status"]["enum"]), WORK_STATUS_LABELS)

    def test_every_field_Work_writes_is_documented_in_the_task_instructions(self):
        for field in SCHEMA["properties"]:
            if field not in BRIDGE_ONLY:
                with self.subTest(field=field):
                    self.assertIn(field, INSTRUCTIONS)

    def test_the_schema_only_uses_keywords_the_validator_supports(self):
        supported = {"type", "required", "enum", "properties", "items", "minLength", "$comment"}

        def walk(node):
            yield set(node)
            for sub in node.get("properties", {}).values():
                yield from walk(sub)
            if isinstance(node.get("items"), dict):
                yield from walk(node["items"])

        for keys in walk(SCHEMA):
            self.assertLessEqual(keys, supported)


class BridgeUsesTheSchema(unittest.TestCase):
    def test_a_draft_that_breaks_the_contract_is_rejected_with_the_reason(self):
        bad = payload()
        bad.pop("project_name")
        with self.assertRaises(ValueError) as caught:
            sync.extract(draft_message(bad), "D1")
        self.assertIn("project_name", str(caught.exception))

    def test_a_valid_draft_is_imported_with_the_official_status(self):
        found = sync.extract(draft_message(payload(status="faltan DATOS")), "D1")
        self.assertEqual(found[0]["status"], "Faltan datos")

    def test_the_thread_must_match_the_draft(self):
        with self.assertRaises(ValueError):
            sync.extract(draft_message(payload(thread="OTRO"), thread="T1"), "D1")

    def test_the_schema_file_is_the_one_the_bridge_loads(self):
        self.assertEqual(work_contract.SCHEMA_PATH.name, "contrato_work.schema.json")
        self.assertEqual(json.loads(work_contract.SCHEMA_PATH.read_text(encoding="utf-8")), SCHEMA)


if __name__ == "__main__":
    unittest.main()
