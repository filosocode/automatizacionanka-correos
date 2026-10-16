"""El estado que escribe Work: lista cerrada en el prompt y sinónimos conservadores. Cubre: T-33"""

from tests import _isolate  # noqa: F401  (debe ir primero)

import json
import re
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from anka.dominio.status import (
    ESTADOS_CONSERVADORES, SINONIMOS_DE_ESTADO, WORK_STATUS_LABELS, reconocer_estado_de_work,
)
from anka.infra import settings
from anka.servicios import quotes, work_import
from tests.test_work_sync import FakeGmail, b64, draft_message, payload
from anka.cli import work_sync as sync

RAIZ = Path(__file__).resolve().parents[1]


class Reconocimiento(unittest.TestCase):
    def test_an_official_label_is_recognised_without_a_warning_even_without_accents(self):
        for escrito in ("Faltan datos", "faltan datos", "Información suficiente para revisión interna",
                        "Informacion suficiente para revision interna"):
            with self.subTest(escrito=escrito):
                etiqueta, aviso = reconocer_estado_de_work(escrito)
                self.assertIn(etiqueta, WORK_STATUS_LABELS)
                self.assertEqual(aviso, "")

    def test_the_variant_that_really_happened_is_taken_as_internal_technical_review_with_a_warning(self):
        etiqueta, aviso = reconocer_estado_de_work("Requiere revisión humana")
        self.assertEqual(etiqueta, "Requiere revisión técnica interna")
        self.assertIn("Requiere revisión humana", aviso)
        self.assertIn("Requiere revisión técnica interna", aviso)

    def test_synonyms_ignore_case_accents_and_extra_spaces(self):
        for escrito in ("REQUIERE  REVISION HUMANA", "requiere revision manual", "Revisión humana"):
            with self.subTest(escrito=escrito):
                self.assertEqual(reconocer_estado_de_work(escrito)[0], "Requiere revisión técnica interna")

    def test_a_synonym_never_leads_to_a_state_that_advances_or_discards_a_case(self):
        self.assertTrue(SINONIMOS_DE_ESTADO)
        self.assertEqual(set(SINONIMOS_DE_ESTADO.values()) - ESTADOS_CONSERVADORES, set())
        for avanza in ("Alcance completo", "Información suficiente para revisión interna", "Borrador preparado", "Descartada"):
            self.assertNotIn(avanza, ESTADOS_CONSERVADORES)
            self.assertNotIn(avanza, SINONIMOS_DE_ESTADO.values())
        for texto in ("Aprobado", "Listo", "Completo", "Cerrada", "Rechazada", "Finalizado", "OK"):
            with self.subTest(texto=texto):
                self.assertEqual(reconocer_estado_de_work(texto), (None, ""))

    def test_anything_else_is_not_recognised(self):
        for texto in ("", None, "Estado Inventado", "Requiere revisión de la luna", 42):
            with self.subTest(texto=texto):
                self.assertEqual(reconocer_estado_de_work(texto), (None, ""))

    def test_every_synonym_target_is_an_official_label(self):
        self.assertLessEqual(set(SINONIMOS_DE_ESTADO.values()), set(WORK_STATUS_LABELS))


class EnElPuente(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        parche = patch.object(settings, "DATA_DIR", Path(self.temp.name))
        parche.start()
        self.addCleanup(parche.stop)

    def sincronizar(self, estado):
        gmail = FakeGmail({"d1": draft_message(payload(status=estado))})
        return sync.sync_once(gmail, work_import.dashboard_upsert, Path(self.temp.name) / "estado.json", {})

    def test_a_draft_with_the_variant_is_imported_and_the_case_says_so(self):
        resultado = self.sincronizar("Requiere revisión humana")
        self.assertEqual((resultado["updated"], len(resultado["rejected"])), (1, 0))
        caso = quotes.get_quote("T1")
        self.assertEqual(caso["status"], "review")                         # decide una persona, no avanza
        self.assertTrue(any("Requiere revisión humana" in a for a in caso["analysis"]["question_quality_warnings"]))

    def test_a_draft_with_an_invented_state_is_still_rejected_and_visible(self):
        resultado = self.sincronizar("Aprobado por el cliente")
        self.assertEqual((resultado["updated"], len(resultado["rejected"])), (0, 1))
        self.assertIn("status", resultado["rejected"][0]["reason"])

    def test_an_official_state_has_no_extra_warning(self):
        self.sincronizar("Faltan datos")
        avisos = quotes.get_quote("T1")["analysis"]["question_quality_warnings"]
        self.assertFalse(any("Work escribió el estado" in a for a in avisos))

    def test_the_command_line_import_applies_the_same_rule(self):
        work_import.dashboard_upsert(b64(json.dumps(payload(thread="TC", status="Requiere revisión humana"))))
        self.assertEqual(quotes.get_quote("TC")["status"], "review")


class ElPrompt(unittest.TestCase):
    def prompt(self):
        return (RAIZ / "docs" / "PROMPT_WORK_ANKA_PRECISO_V4.txt").read_text(encoding="utf-8")

    def linea_del_estado(self):
        return next(linea for linea in self.prompt().splitlines() if linea.startswith("- `status`:"))

    def test_the_prompt_lists_exactly_the_official_states_and_asks_for_one_of_them(self):
        linea = self.linea_del_estado()
        self.assertIn("exactamente uno", linea)
        self.assertNotIn("por ejemplo", linea)
        listados = re.findall(r"`([^`]+)`", linea.split(":", 1)[1].split("No inventes")[0])      # la lista, no el nombre del campo ni las aclaraciones
        self.assertEqual(sorted(listados), sorted(WORK_STATUS_LABELS))

    def test_the_prompt_tells_work_what_to_do_when_none_fits(self):
        self.assertRegex(self.linea_del_estado(), r"Requiere revisión técnica interna")
        self.assertRegex(self.prompt(), r"No inventes ni traduzcas")

    def test_the_prompt_version_changed_so_the_history_can_tell_the_two_apart(self):
        self.assertRegex(self.prompt(), r"Versión 4\.2")
        self.assertIn("exactamente `PRECISO_V4.2_DRIVE`", self.prompt())


if __name__ == "__main__":
    unittest.main()
