"""Puente con Work: descuidos de tipo corregidos, rechazo sin repetir y aviso que dice cuál borrador falla. Cubre: T-02"""

from tests import _isolate  # noqa: F401  (debe ir primero)

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from anka.cli import work_sync as sync
from anka.dominio import work_contract
from anka.infra import settings, work_heartbeat
from anka.servicios import quotes, work_import
from tests.test_work_sync import FakeGmail, _Drafts, draft_message, payload

AUDITORIA = [{"question": "¿Área?", "reason_missing": "falta", "reviewed_sources": ["Correo del cliente"],
              "unresolved": True}]


class CorreccionDeTiposTests(unittest.TestCase):
    def test_a_boolean_unresolved_becomes_empty_text_with_a_warning_each(self):
        datos = {"question_audit": [{"unresolved": True}, {"unresolved": False}, {"unresolved": "falta el área"}]}
        avisos = work_contract.corregir_descuidos_de_tipo(datos)
        self.assertEqual([item["unresolved"] for item in datos["question_audit"]], ["", "", "falta el área"])
        self.assertEqual(len(avisos), 2)
        self.assertIn("question_audit[0].unresolved llegó como true", avisos[0])
        self.assertIn("llegó como false", avisos[1])

    def test_valid_and_malformed_payloads_are_left_alone(self):
        for datos in ({}, {"question_audit": None}, {"question_audit": ["x", None, 3]},
                      {"question_audit": [{"unresolved": None}, {"question": "q"}]}):
            with self.subTest(datos=datos):
                self.assertEqual(work_contract.corregir_descuidos_de_tipo(datos), [])
        self.assertEqual(work_contract.corregir_descuidos_de_tipo("no es un dict"), [])  # type: ignore[arg-type]


class PuenteTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        patcher = patch.object(settings, "DATA_DIR", Path(self.temp.name))
        patcher.start()
        self.addCleanup(patcher.stop)
        self.checkpoint = Path(self.temp.name) / "state.json"

    def sincronizar(self, gmail, cache):
        return sync.sync_once(gmail, work_import.dashboard_upsert, self.checkpoint, cache)

    def con_asunto(self, mensaje, asunto):
        mensaje["payload"]["headers"] = [{"name": "Subject", "value": asunto}]
        return mensaje

    def test_a_draft_with_a_boolean_unresolved_is_imported_and_the_case_says_so(self):
        gmail = FakeGmail({"d1": draft_message(payload(question_audit=AUDITORIA))})
        resultado = self.sincronizar(gmail, {})
        self.assertEqual((resultado["updated"], resultado["failures"]), (1, 0))
        advertencias = quotes.get_quote("T1")["analysis"]["question_quality_warnings"]
        self.assertTrue(any("unresolved llegó como true" in aviso for aviso in advertencias), advertencias)

    def test_a_rejected_draft_is_not_downloaded_again_until_work_edits_it(self):
        malo = self.con_asunto(draft_message(payload(thread="OTRO")), "Re: LLANOS 8. Solicitud")
        gmail = FakeGmail({"d1": malo})
        cache: dict = {}
        primero = self.sincronizar(gmail, cache)
        segundo = self.sincronizar(gmail, cache)
        self.assertEqual((primero["failures"], segundo["failures"]), (1, 1))  # el aviso sigue visible
        self.assertEqual(gmail.gets, 1)  # pero no se vuelve a bajar ni a registrar con traza
        gmail.items["d1"] = draft_message(payload(), message_id="gm2")  # Work lo corrige: otra versión
        tercero = self.sincronizar(gmail, cache)
        self.assertEqual((tercero["updated"], tercero["failures"], gmail.gets), (1, 0, 2))

    def test_the_notice_says_which_draft_fails_and_why(self):
        malo = self.con_asunto(draft_message(payload(thread="OTRO")), "Re: LLANOS 8. Solicitud cotización")
        resultado = self.sincronizar(FakeGmail({"d1": malo}), {})
        self.assertEqual(len(resultado["detalles"]), 1)
        detalle = resultado["detalles"][0]
        self.assertIn("«Re: LLANOS 8. Solicitud cotización»", detalle)
        self.assertIn("thread_id no coincide", detalle)
        self.assertNotIn("Contrato de Work no cumplido", detalle)

    def test_a_temporary_failure_is_retried_on_the_next_pass(self):
        gmail = FakeGmail({"d1": draft_message(payload())})
        llamadas = []

        class Intermitente(_Drafts):
            def get(self, id, **kwargs):
                llamadas.append(id)
                if len(llamadas) == 1:
                    raise RuntimeError("503 servicio no disponible")
                return super().get(id, **kwargs)

        gmail.drafts = lambda: Intermitente(gmail)  # type: ignore[method-assign]
        cache: dict = {}
        self.assertEqual(self.sincronizar(gmail, cache)["failures"], 1)
        segundo = self.sincronizar(gmail, cache)
        self.assertEqual((segundo["updated"], segundo["failures"]), (1, 0))  # un fallo de red no se memoriza

    def test_the_bridge_status_names_the_failing_draft(self):
        work_heartbeat.write_heartbeat(60, drafts=16, failures=1,
                                       detalles=["«Re: LLANOS 8»: question_audit[0].unresolved debe ser de tipo string."])
        estado, texto = work_heartbeat.bridge_state()
        self.assertEqual(estado, "error")
        self.assertIn("1 borrador(es) no importado(s): «Re: LLANOS 8»", texto)
        work_heartbeat.write_heartbeat(60, drafts=16, failures=1)  # sin detalle sigue funcionando como antes
        self.assertTrue(work_heartbeat.bridge_state()[1].endswith("no importado(s)."))


if __name__ == "__main__":
    unittest.main()
