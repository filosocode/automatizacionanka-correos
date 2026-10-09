"""El modelo y la versión del prompt que declara Work quedan en el caso, solo para uso interno. Cubre: T-32"""

from tests import _isolate  # noqa: F401  (debe ir primero)

import json
import re
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from anka.dominio.work_contract import validate
from anka.infra import settings, store
from anka.servicios import quotes, work_import
from anka.web import pantalla_caso
from tests.test_work_sync import b64, payload

RAIZ = Path(__file__).resolve().parents[1]


class Base(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        for parche in (patch.object(settings, "DATA_DIR", Path(self.temp.name)),
                       patch("anka.web.paginas.load_config", return_value={"active_mode": "chatgpt_work", "required_fields": []}),
                       patch("anka.web.pantalla_caso.load_config", return_value={"active_mode": "chatgpt_work",
                                                                                 "required_fields": []})):
            parche.start()
            self.addCleanup(parche.stop)

    def importar(self, **extra):
        work_import.dashboard_upsert(b64(json.dumps(payload(thread="TM", **extra))))

    def resumen_del_analisis(self):
        db = store.open_db(Path(self.temp.name))
        try:
            return db.execute("SELECT summary FROM case_events WHERE thread_id='TM' AND kind='analysis'").fetchone()[0]
        finally:
            db.close()


class EnElContrato(unittest.TestCase):
    def test_the_two_fields_are_optional_strings(self):
        self.assertEqual(validate(payload(model="GPT de prueba", skill_version="PRECISO_V4")), [])
        self.assertEqual(validate(payload()), [])                              # sin ellos sigue siendo válido
        self.assertTrue(validate(payload(model=5)))                            # pero no pueden ser de otro tipo
        self.assertTrue(validate(payload(skill_version=["v4"])))


class EnLaImportacion(Base):
    def test_they_are_kept_in_the_case_and_written_in_the_history(self):
        self.importar(model="GPT de prueba", skill_version="PRECISO_V4")
        caso = quotes.get_quote("TM")
        self.assertEqual((caso["analysis"]["work_model"], caso["analysis"]["work_skill_version"]),
                         ("GPT de prueba", "PRECISO_V4"))
        resumen = self.resumen_del_analisis()
        self.assertIn("Modelo declarado por Work: GPT de prueba", resumen)
        self.assertIn("prompt: PRECISO_V4", resumen)

    def test_without_them_the_history_is_written_as_before(self):
        self.importar()
        self.assertNotIn("Modelo declarado", self.resumen_del_analisis())
        self.assertNotIn("work_model", quotes.get_quote("TM")["analysis"])

    def test_only_one_of_them_is_fine(self):
        self.importar(model="GPT de prueba")
        resumen = self.resumen_del_analisis()
        self.assertIn("GPT de prueba", resumen)
        self.assertNotIn("prompt:", resumen)

    def test_they_are_cleaned_because_the_model_writes_them(self):
        self.importar(model="  GPT\n\tde   prueba\x00\x07  " + "x" * 500, skill_version="v4\r\nOtra línea")
        caso = quotes.get_quote("TM")["analysis"]
        self.assertNotRegex(caso["work_model"], r"[\x00-\x1f]")
        self.assertLessEqual(len(caso["work_model"]), work_import.MAX_MODELO)
        self.assertTrue(caso["work_model"].startswith("GPT de prueba"))
        self.assertNotRegex(caso["work_skill_version"], r"[\x00-\x1f]")
        self.assertLessEqual(len(caso["work_skill_version"]), work_import.MAX_VERSION)

    def test_a_hostile_value_is_shown_escaped(self):
        self.importar(model="<script>alert(1)</script>", skill_version="<img src=x onerror=alert(1)>")
        pagina = pantalla_caso.render_case("TM")
        self.assertNotIn("<script>alert(1)</script>", pagina)
        self.assertNotIn("<img src=x", pagina)

    def test_importing_the_same_draft_with_another_model_is_a_new_analysis_fact(self):
        self.importar(model="Modelo A", skill_version="v3")
        self.importar(model="Modelo B", skill_version="v3")
        db = store.open_db(Path(self.temp.name))
        try:
            resumenes = [f[0] for f in db.execute("SELECT summary FROM case_events WHERE thread_id='TM' AND kind='analysis'")]
        finally:
            db.close()
        self.assertEqual(len(resumenes), 2)
        self.assertTrue(any("Modelo A" in r for r in resumenes) and any("Modelo B" in r for r in resumenes))


class SoloInternos(Base):
    def test_no_email_template_or_client_text_carries_them(self):
        from anka.dominio.reception import correo_con_preguntas, suggested_reply
        self.importar(model="MODELO-SECRETO-XYZ", skill_version="VERSION-SECRETA-XYZ",
                      draft_reply="Hola, gracias por su solicitud.")
        analisis = quotes.get_quote("TM")["analysis"]
        textos = [suggested_reply(analisis, []), correo_con_preguntas(["¿Plazo?"]),
                  (analisis.get("work_reply") or {}).get("text", "")]
        for template in ("received", "acuse", "scope_update", "te_aprobada", "verify"):
            textos.append(pantalla_caso.reply_template(template, quotes.get_quote("TM")))
        for texto in textos:
            self.assertNotIn("SECRET", texto)

    def test_the_client_email_box_does_not_show_them_either(self):
        self.importar(model="MODELO-SECRETO-XYZ", skill_version="VERSION-SECRETA-XYZ")
        pagina = pantalla_caso.render_case("TM", template="preguntas", preguntas_texto="¿Plazo?")
        cuadro = pagina.split('name="body"')[1].split("</textarea>")[0]
        self.assertNotIn("SECRET", cuadro)


class ElPrompt(unittest.TestCase):
    def test_prompt_v4_asks_for_both_fields_and_v3_is_kept(self):
        v4 = (RAIZ / "docs" / "PROMPT_WORK_ANKA_PRECISO_V4.txt").read_text(encoding="utf-8")
        self.assertRegex(v4, r"`model`")
        self.assertRegex(v4, r"`skill_version`")
        self.assertIn("PRECISO_V4", v4)
        self.assertNotIn("PRECISO_V4", (RAIZ / "docs" / "PROMPT_WORK_ANKA_PRECISO_V3.txt").read_text(encoding="utf-8"))

    def test_the_documentation_points_to_the_current_prompt(self):
        texto = (RAIZ / "docs" / "INSTRUCCIONES.txt").read_text(encoding="utf-8")
        self.assertIn("PROMPT_WORK_ANKA_PRECISO_V4.txt", texto)

    def test_the_version_the_prompt_declares_is_the_one_the_prompt_is_named_after(self):
        v4 = (RAIZ / "docs" / "PROMPT_WORK_ANKA_PRECISO_V4.txt").read_text(encoding="utf-8")
        self.assertTrue(re.search(r"exactamente `PRECISO_V4`", v4))


if __name__ == "__main__":
    unittest.main()
