"""Recuperación y exclusión de envíos simultáneos. Cubre: U-05, T-10, T-22, T-23"""

from tests import _isolate  # noqa: F401
import concurrent.futures
import sqlite3
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import MagicMock, patch

from anka.infra import backup, settings, store
from anka.servicios import envios
import check_spec
import ast
import csv
import json
from evaluacion.evaluar import evaluar, FIELDS


class RecoveryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.patcher = patch.object(settings, "DATA_DIR", self.root)
        self.patcher.start()
        self.addCleanup(self.patcher.stop)
        store.connect().close()

    def test_only_one_concurrent_sender_can_reserve(self):
        def reserve(_):
            try:
                return envios.reservar("T1", "a@example.com", "Hola")[0]
            except ValueError:
                return None

        with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
            values = list(pool.map(reserve, range(4)))
        self.assertEqual(sum(value is not None for value in values), 1)
        with self.assertRaises(ValueError):
            envios.reservar("T1", "A@example.com", "Hola")
        self.assertIsNotNone(envios.reservar("T1", "b@example.com", "Hola"))

    def test_uncertain_send_is_reconciled_without_resending(self):
        key, message_id = envios.reservar("T1", "a@example.com", "Hola")
        operation = envios.pendiente("T1", "a@example.com", "Hola")
        gmail = MagicMock()
        gmail.users().messages().list().execute.return_value = {"messages": [{"id": "g1", "threadId": "T1"}]}
        self.assertEqual(envios.reconciliar(gmail, operation), "g1")
        self.assertIn(message_id.strip("<>"), gmail.users().messages().list.call_args.kwargs["q"])
        envios.confirmar(key, "g1")
        self.assertEqual(envios.pendiente("T1", "a@example.com", "Hola")["state"], "sent")
        with self.assertRaises(ValueError):
            envios.reservar("T1", "a@example.com", "Hola")
        gmail.users().messages().send.assert_not_called()

    def test_missing_remote_result_keeps_the_reservation(self):
        envios.reservar("T1", "a@example.com", "Hola")
        gmail = MagicMock()
        gmail.users().messages().list().execute.return_value = {"messages": [{"id": "other", "threadId": "T2"}]}
        self.assertEqual(envios.reconciliar(gmail, envios.pendiente("T1", "a@example.com", "Hola")), "")
        self.assertEqual(envios.pendiente("T1", "a@example.com", "Hola")["state"], "pending")

    def test_roundtrip_restores_original_evidence_and_config(self):
        evidence = self.root / "evidence/T1/m1.eml"
        evidence.parent.mkdir(parents=True)
        evidence.write_bytes(b"Subject: original\r\n\r\ncontenido")
        (self.root / "config.json").write_text('{"a":1}')
        (self.root / "token.json").write_text("secret")
        archive = backup.create_backup()
        evidence.write_bytes(b"alterado")
        (self.root / "config.json").write_text("{}")
        backup.restore(archive)
        self.assertEqual(evidence.read_bytes(), b"Subject: original\r\n\r\ncontenido")
        self.assertEqual((self.root / "config.json").read_text(), '{"a":1}')
        self.assertTrue(list(evidence.parent.glob("*.before-restore-*")))
        with zipfile.ZipFile(archive) as handle:
            self.assertNotIn("token.json", handle.namelist())

    def test_invalid_archive_never_replaces_current_database(self):
        before = (self.root / "quotes.sqlite3").read_bytes()
        archive = self.root / "bad.zip"
        for name in ("../escape.eml", "token.json", "evidence/../../escape.eml"):
            with zipfile.ZipFile(archive, "w") as handle:
                handle.writestr("quotes.sqlite3", b"not sqlite")
                handle.writestr(name, b"bad")
            with self.assertRaises(ValueError):
                backup.restore(archive)
            self.assertEqual((self.root / "quotes.sqlite3").read_bytes(), before)
        with zipfile.ZipFile(archive, "w") as handle:
            handle.writestr("quotes.sqlite3", b"not sqlite")
        with self.assertRaises(sqlite3.DatabaseError):
            backup.restore(archive)
        self.assertEqual((self.root / "quotes.sqlite3").read_bytes(), before)

    def test_a_comment_without_executable_tests_is_not_coverage(self):
        (self.root / "tests").mkdir()
        (self.root / "tests/test_empty.py").write_text("# Cubre: T-23\n")
        self.assertEqual(check_spec.coverage(self.root)["T-23"], [])

    def test_class_tag_does_not_claim_unrelated_tests(self):
        source = (
            "# Cubre: T-23\nclass A:\n    def test_one(self):\n        assert True\n"
            "class B:\n    def test_two(self):\n        assert True\n"
        )
        methods = check_spec.tests_for_tag(source, ast.parse(source), source.index("Cubre:"))
        self.assertEqual([method.name for method in methods], ["test_one"])

    def test_evaluation_rejects_empty_labels_and_measures_observed_errors(self):
        corpus, results = self.root / "corpus.csv", self.root / "results.json"
        expected = dict.fromkeys(FIELDS, "no indicado")
        expected.update({"id_caso": "C1", "es_solicitud (si/no)": "si"})
        actual = {target: expected[field] for field, target in FIELDS.items()}
        actual.update({"id_caso": "C1", "es_solicitud": "no"})
        observations = {
            "modelo": "simulado",
            "prompt": "fixture-v1",
            "fecha": "2026-10-08",
            "tipo_corpus": "sintetico",
            "casos": [actual],
        }
        results.write_text(json.dumps(observations))

        def write():
            with corpus.open("w", newline="", encoding="utf-8") as handle:
                writer = csv.DictWriter(handle, fieldnames=expected)
                writer.writeheader()
                writer.writerow(expected)

        write()
        report = evaluar(corpus, results)
        self.assertEqual(report["solicitudes_perdidas"], 1)
        self.assertEqual(report["acuerdo_exacto_por_campo"]["ubicacion"], 1)
        self.assertEqual(report["tipo_corpus"], "sintetico")
        expected["ubicacion_esperada"] = ""
        write()
        with self.assertRaisesRegex(ValueError, "sin etiquetar"):
            evaluar(corpus, results)
