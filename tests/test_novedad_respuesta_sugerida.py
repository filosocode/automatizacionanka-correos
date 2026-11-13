"""«Respuesta sugerida lista para revisar» solo anuncia una respuesta que existe (T-50, D-154).

Cubre: T-50
"""

from tests import _isolate  # noqa: F401  (debe ir primero)

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from anka.infra import settings, store
from anka.servicios import work_import
from tests.test_work_sync import b64, payload

INCOMPLETO = {"analysis_complete": False, "status": "Requiere revisión técnica interna"}
RESPUESTA = "Buen día. Para cotizar necesitamos el polígono del predio. Quedamos atentos."


class Base(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        for parche in (patch.object(settings, "DATA_DIR", Path(self.temp.name)),
                       patch.object(work_import, "programar_actualizacion_de_hoja", MagicMock())):
            parche.start()
            self.addCleanup(parche.stop)

    def importar(self, hilo="T1", mensaje="m1", **extra):
        work_import.dashboard_upsert(b64(json.dumps(payload(thread=hilo, message=mensaje, **extra))))

    def novedades(self, hilo="T1"):
        """Reabre la base (la derivación corre al abrir) y devuelve los tipos de novedad del hilo, con su id."""
        with store.lectura() as db:
            return [(r["kind"], r["id"]) for r in db.execute(
                "SELECT id,kind FROM dashboard_notifications WHERE thread_id=? ORDER BY id", (hilo,))]

    def tipos(self, hilo="T1"):
        return [tipo for tipo, _ in self.novedades(hilo)]

    def caso(self, hilo="T1"):
        with store.lectura() as db:
            return tuple(db.execute("SELECT * FROM quotes WHERE thread_id=?", (hilo,)).fetchone())


class RespuestaSugeridaTests(Base):
    def test_un_analisis_incompleto_sin_respuesta_solo_anuncia_la_solicitud_recibida(self):
        self.importar(**INCOMPLETO)
        self.assertEqual(self.tipos(), ["request_received"])
        with store.lectura() as db:
            self.assertEqual(db.execute("SELECT status FROM quotes").fetchone()[0], "review")

    def test_un_incompleto_aunque_work_mande_texto_no_anuncia_una_respuesta(self):
        self.importar(draft_reply=RESPUESTA, **INCOMPLETO)          # el importador descarta ese texto
        self.assertEqual(self.tipos(), ["request_received"])

    def test_un_analisis_completo_con_respuesta_anuncia_ambas_novedades(self):
        self.importar(draft_reply=RESPUESTA, analysis_complete=True)
        self.assertEqual(self.tipos(), ["request_received", "draft_suggested"])

    def test_el_borrador_de_una_persona_si_se_anuncia_aunque_el_analisis_siga_incompleto(self):
        self.importar(**INCOMPLETO)
        self.assertEqual(self.tipos(), ["request_received"])
        with store.transaccion() as db:
            db.execute("UPDATE quotes SET draft_body=?, draft_id='d1'", ("Hola, ya revisé el caso.",))
        self.assertEqual(self.tipos(), ["request_received", "draft_suggested"])

    def test_un_borrador_en_blanco_no_cuenta_como_respuesta(self):
        self.importar(**INCOMPLETO)
        with store.transaccion() as db:
            db.execute("UPDATE quotes SET draft_body='   \n '")
        self.assertEqual(self.tipos(), ["request_received"])

    def test_reimportar_un_incompleto_no_duplica_ni_crea_la_respuesta_sugerida(self):
        self.importar(**INCOMPLETO)
        antes = self.novedades()
        self.importar(**INCOMPLETO)
        self.importar(project_name="otra versión", **INCOMPLETO)
        self.assertEqual(self.novedades(), antes)

    def test_incompleto_y_luego_completo_con_respuesta_crea_la_sugerida_una_sola_vez(self):
        self.importar(**INCOMPLETO)
        self.importar(draft_reply=RESPUESTA, analysis_complete=True)
        self.importar(draft_reply=RESPUESTA, analysis_complete=True)
        self.assertEqual(self.tipos(), ["request_received", "draft_suggested"])

    def test_completo_y_luego_incompleto_conserva_la_novedad_ya_creada(self):
        self.importar(draft_reply=RESPUESTA, analysis_complete=True)
        antes = self.novedades()
        self.importar(**INCOMPLETO)
        self.assertEqual(self.novedades(), antes)                    # no se borra ni se vuelve a crear

    def test_otro_hilo_no_se_ve_afectado(self):
        self.importar("T1", "m1", **INCOMPLETO)
        self.importar("T2", "m2", draft_reply=RESPUESTA, analysis_complete=True)
        self.assertEqual((self.tipos("T1"), self.tipos("T2")),
                         (["request_received"], ["request_received", "draft_suggested"]))

    def test_un_analisis_que_no_declara_estar_completo_se_deriva_como_siempre(self):
        self.importar(draft_reply=RESPUESTA)                         # sin `analysis_complete`
        self.assertEqual(self.tipos(), ["request_received", "draft_suggested"])

    def test_sin_la_marca_de_incompleto_y_sin_texto_tambien_se_deriva_como_siempre(self):
        # los modos local y api (y un borrador de Work que no la declara) no traen `analysis_complete`: no es «incompleto»
        self.importar()
        self.assertEqual(self.tipos(), ["request_received", "draft_suggested"])
        with store.transaccion() as db:
            db.execute("DELETE FROM dashboard_notifications")
            db.execute("UPDATE quotes SET analysis_json=json_remove(analysis_json,'$.analysis_complete')")
        self.assertEqual(self.tipos(), ["request_received", "draft_suggested"])

    def test_la_derivacion_no_cambia_el_caso(self):
        self.importar(**INCOMPLETO)
        antes = self.caso()
        self.novedades()
        self.novedades()
        self.assertEqual(self.caso(), antes)

    def test_un_analisis_danado_no_rompe_la_apertura_y_se_deriva_como_siempre(self):
        self.importar(draft_reply=RESPUESTA, analysis_complete=True)
        with store.transaccion() as db:
            db.execute("DELETE FROM dashboard_notifications")
            db.execute("UPDATE quotes SET analysis_json='esto no es json', status='review'")
        self.assertEqual(self.tipos(), ["request_received", "draft_suggested"])

    def test_una_novedad_historica_de_un_incompleto_no_se_borra_ni_se_marca(self):
        self.importar(**INCOMPLETO)
        with store.transaccion() as db:       # una novedad engañosa que ya existía antes de la corrección
            db.execute("INSERT INTO dashboard_notifications(thread_id,kind,source_key,title,message,created_at) "
                       "VALUES('T1','draft_suggested','m1','Respuesta sugerida lista para revisar','x','2026-10-18')")
        antes = self.novedades()
        with store.lectura() as db:
            leida = db.execute("SELECT read_at FROM dashboard_notifications WHERE kind='draft_suggested'").fetchone()[0]
        self.importar(**INCOMPLETO)
        self.assertEqual(self.novedades(), antes)
        with store.lectura() as db:
            self.assertEqual(db.execute("SELECT read_at FROM dashboard_notifications "
                                        "WHERE kind='draft_suggested'").fetchone()[0], leida)

    def test_la_base_queda_integra(self):
        self.importar(**INCOMPLETO)
        self.novedades()
        with store.lectura() as db:
            self.assertEqual(db.execute("PRAGMA integrity_check").fetchone()[0], "ok")


if __name__ == "__main__":
    unittest.main()
