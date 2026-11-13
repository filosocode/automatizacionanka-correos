"""Recuperación y exclusión de envíos simultáneos. Cubre: U-05, T-10, T-22, T-23"""

from tests import _isolate  # noqa: F401
import concurrent.futures
import sqlite3
from datetime import datetime, timedelta
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
        # otro destinatario del mismo caso tampoco entra mientras el primero siga sin confirmar (U-59)
        with self.assertRaises(ValueError):
            envios.reservar("T1", "b@example.com", "Hola")
        self.assertIsNotNone(envios.reservar("T2", "b@example.com", "Hola"))      # otro caso sí

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
        self.assertEqual((self.root / "config.json").read_text(), "{}")     # U-78: la configuración viva no se pisa
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


class _GmailError(Exception):
    """Imita googleapiclient.errors.HttpError: lleva `resp.status`."""

    def __init__(self, status):
        super().__init__(f"HTTP {status}")
        self.resp = MagicMock(status=status)


# Cubre: U-05
class SendFailureTests(unittest.TestCase):
    """Un rechazo cierto de Gmail libera la reserva; un fallo incierto la conserva."""

    from tests.test_auto_folder import SendCreatesTheFolderTests as _Base
    setUp = _Base.setUp
    seed = _Base.seed

    def send(self, error):
        from anka.servicios import case_actions
        with patch.object(case_actions, "_send_through_gmail", side_effect=error):
            with self.assertRaises(type(error)):
                case_actions.send_dashboard_reply("t-1", "Hola.")

    def test_a_definitive_rejection_releases_the_reservation_so_it_can_be_retried(self):
        self.seed(client="Minera Sur")
        self.send(_GmailError(403))
        self.assertIsNone(envios.pendiente("t-1", "cliente@example.com", "Hola."))
        from anka.servicios import case_actions
        self.assertIn("Correo enviado", case_actions.send_dashboard_reply("t-1", "Hola."))

    def test_an_uncertain_failure_keeps_the_reservation_and_blocks_a_blind_retry(self):
        self.seed(client="Minera Sur")
        for error in (_GmailError(503), _GmailError(408), TimeoutError("sin respuesta")):
            with self.subTest(error=repr(error)):
                self.send(error)
                self.assertEqual(envios.pendiente("t-1", "cliente@example.com", "Hola.")["state"], "pending")
                db = store.connect()
                db.execute("DELETE FROM mail_operations")
                db.commit()
                db.close()

    def test_only_client_errors_are_definitive(self):
        self.assertTrue(envios.fallo_definitivo(_GmailError(400)))
        self.assertTrue(envios.fallo_definitivo(_GmailError(429)))
        self.assertFalse(envios.fallo_definitivo(_GmailError(408)))
        self.assertFalse(envios.fallo_definitivo(_GmailError(500)))
        self.assertFalse(envios.fallo_definitivo(OSError("red")))

    def test_a_sent_operation_is_never_released(self):
        key, _ = envios.reservar("t-9", "a@example.com", "Hola")
        envios.confirmar(key, "g1")
        envios.liberar(key)
        self.assertEqual(envios.pendiente("t-9", "a@example.com", "Hola")["state"], "sent")


# Cubre: U-05
class RetentionTests(unittest.TestCase):
    """La tabla de envíos tiene tope: se purgan los confirmados viejos, nunca los inciertos."""

    setUp = RecoveryTests.setUp

    AHORA = datetime(2026, 6, 1, 12, 0).astimezone()   # reloj fijo: la antigüedad no depende de la hora real

    def seed(self, key, state, days_old):
        created = (self.AHORA - timedelta(days=days_old)).isoformat()
        db = store.connect()
        db.execute("INSERT INTO mail_operations (operation_key, thread_id, recipient, message_id, state, gmail_id,"
                   " created_at, error) VALUES (?, 't', 'a@x.com', ?, ?, 'g', ?, '')",
                   (key, f"<{key}@anka.local>", state, created))
        db.commit()
        db.close()

    def keys(self):
        db = store.connect()
        try:
            return {row[0] for row in db.execute("SELECT operation_key FROM mail_operations")}
        finally:
            db.close()

    def test_old_confirmed_sends_are_purged_but_uncertain_and_recent_ones_stay(self):
        self.seed("viejo-enviado", "sent", envios.RETENCION_DIAS + 1)
        self.seed("reciente-enviado", "sent", 5)
        self.seed("viejo-incierto", "pending", envios.RETENCION_DIAS + 30)
        purgar = envios.purgar_confirmados
        with patch.object(envios, "purgar_confirmados", side_effect=lambda db: purgar(db, ahora=self.AHORA)) as llamado:
            envios.reservar("T9", "z@example.com", "Hola")
        llamado.assert_called_once()                   # reservar purga en la misma transacción
        remaining = self.keys()
        self.assertNotIn("viejo-enviado", remaining)
        self.assertIn("reciente-enviado", remaining)
        self.assertIn("viejo-incierto", remaining)
