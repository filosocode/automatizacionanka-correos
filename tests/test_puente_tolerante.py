"""Puente con Work: descuidos de tipo corregidos sin perder el borrador, junto al rechazo por versión. Cubre: T-02"""

from tests import _isolate  # noqa: F401  (debe ir primero)

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from anka.cli import work_sync as sync
from anka.dominio import work_contract
from anka.infra import settings
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

    def test_a_rejected_draft_is_listed_once_and_is_not_a_failure(self):
        malo = self.con_asunto(draft_message(payload(thread="OTRO")), "Re: LLANOS 8. Solicitud cotización")
        gmail = FakeGmail({"d1": malo})
        cache: dict = {}
        primero = self.sincronizar(gmail, cache)
        segundo = self.sincronizar(gmail, cache)
        for resultado in (primero, segundo):
            self.assertEqual(resultado["failures"], 0)  # el contenido no se arregla reintentando
            self.assertEqual(len(resultado["rejected"]), 1)  # sigue visible en el Resumen
            self.assertIn("«thread_id» del análisis no coincide", resultado["rejected"][0]["reason"])
        self.assertEqual(gmail.gets, 1)  # y no se vuelve a bajar
        gmail.items["d1"] = draft_message(payload(), message_id="gm2")  # Work lo corrige: otra versión
        tercero = self.sincronizar(gmail, cache)
        self.assertEqual((tercero["updated"], tercero["rejected"], gmail.gets), (1, [], 2))

    def test_a_draft_with_a_boolean_unresolved_is_not_rejected_but_an_invalid_one_beside_it_is(self):
        gmail = FakeGmail({
            "bueno": draft_message(payload(question_audit=AUDITORIA)),
            "malo": draft_message(payload(thread="OTRO"), thread="T2", message_id="gm9"),
        })
        resultado = self.sincronizar(gmail, {})
        self.assertEqual((resultado["updated"], resultado["failures"], len(resultado["rejected"])), (1, 0, 1))

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


if __name__ == "__main__":
    unittest.main()
