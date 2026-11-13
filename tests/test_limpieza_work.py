"""Cubre: T-85. Evidencia persistente antes de retirar solo borradores internos importados."""

from tests import _isolate  # noqa: F401

import base64
import copy
import hashlib
import json
import tempfile
import unittest
from email import policy
from email.parser import Parser
from pathlib import Path
from unittest.mock import MagicMock, patch

from anka.infra import settings, store
from anka.servicios import limpieza_work


class LimpiezaWorkTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.datos = Path(self.temp.name)
        parche = patch.object(settings, "DATA_DIR", self.datos)
        parche.start()
        self.addCleanup(parche.stop)
        self.raw = json.dumps({"thread_id": "t1", "message_id": "m1", "draft_id": "d1"})
        self.codificado = base64.urlsafe_b64encode(self.raw.encode()).decode()
        self.version = hashlib.sha1(self.raw.encode()).hexdigest()[:12]
        with store.transaccion() as db:
            db.execute("INSERT INTO quotes(thread_id,last_inbound_id,status,recipient,analysis_json,draft_id) "
                       "VALUES('t1','m1','review','cliente@example.com','{}','d1')")
            store.record_event(db, "t1", "analysis", "work", self.version, "Importado")
        self.borrador = {"id": "d1", "message": {"id": "gm1", "threadId": "t1", "labelIds": ["DRAFT"],
            "payload": {"mimeType": "text/plain", "body": {"data": "Y29udHJhdG8="}, "headers": [
                {"name": "From", "value": "Anka <anka@example.com>"},
                {"name": "To", "value": "anka@example.com"}]}}}
        self.gmail = MagicMock()
        self.recurso = self.gmail.users.return_value.drafts.return_value
        self.recurso.get.return_value.execute.return_value = self.borrador

    def retirar(self):
        return limpieza_work.retirar_importado(self.gmail, self.codificado, {"anka@example.com"}, "gm1")

    def test_respalda_antes_de_borrar_y_conserva_contrato_y_caso(self):
        def borrar():
            archivos = list(self.datos.rglob("*.eml"))
            self.assertEqual(len(archivos), 1)
            correo = Parser(policy=policy.default).parsestr(archivos[0].read_text(encoding="utf-8"))
            contenido = json.loads(next(correo.iter_attachments()).get_content())
            self.assertEqual(contenido["borrador"], self.borrador)
            self.assertEqual(contenido["contrato_importado"]["message_id"], "m1")
        self.recurso.delete.return_value.execute.side_effect = borrar
        self.assertTrue(self.retirar())
        self.recurso.delete.assert_called_once_with(userId="me", id="d1")
        with store.lectura() as db:
            self.assertIsNone(db.execute("SELECT draft_id FROM quotes").fetchone()[0])
            self.assertEqual(db.execute("SELECT count(*) FROM case_events WHERE kind='work_draft_cleaned'")
                             .fetchone()[0], 1)

    def test_sin_importacion_confirmada_no_contacta_gmail(self):
        with store.transaccion() as db:
            db.execute("DELETE FROM case_events")
        self.assertFalse(self.retirar())
        self.gmail.users.assert_not_called()

    def test_borrador_externo_se_conserva(self):
        self.borrador["message"]["payload"]["headers"][1]["value"] = "cliente@example.com"
        self.assertFalse(self.retirar())
        self.recurso.delete.assert_not_called()

    def test_edicion_posterior_se_conserva(self):
        editado = copy.deepcopy(self.borrador)
        editado["message"]["id"] = "gm2"
        self.recurso.get.return_value.execute.side_effect = [self.borrador, editado]
        self.assertFalse(self.retirar())
        self.recurso.delete.assert_not_called()

    def test_edicion_antes_de_la_limpieza_se_conserva(self):
        self.borrador["message"]["id"] = "gm2"
        self.assertFalse(self.retirar())
        self.recurso.delete.assert_not_called()

    def test_sin_evidencia_no_borra(self):
        with patch.object(limpieza_work, "escribir_privado", side_effect=OSError("disco")):
            self.assertFalse(self.retirar())
        self.recurso.delete.assert_not_called()

    def test_fallo_gmail_conserva_evidencia_sin_dar_por_borrado(self):
        self.recurso.delete.return_value.execute.side_effect = TimeoutError()
        self.assertFalse(self.retirar())
        self.assertEqual(len(list(self.datos.rglob("*.eml"))), 1)
        with store.lectura() as db:
            self.assertEqual(db.execute("SELECT draft_id FROM quotes").fetchone()[0], "d1")

    def test_otro_borrador_del_dashboard_no_se_desvincula(self):
        with store.transaccion() as db:
            db.execute("UPDATE quotes SET draft_id='borrador-comercial'")
        self.assertTrue(self.retirar())
        with store.lectura() as db:
            self.assertEqual(db.execute("SELECT draft_id FROM quotes").fetchone()[0], "borrador-comercial")
