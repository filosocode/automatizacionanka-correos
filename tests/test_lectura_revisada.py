"""«Ya revisé estos archivos yo»: la lectura sin verificar no detiene el trabajo. Cubre: U-29"""

from tests import _isolate  # noqa: F401  (debe ir primero)

import io
import tempfile
import unittest
import urllib.parse
from pathlib import Path
from unittest.mock import patch

from anka.dominio.status import QuoteStatus
from anka.infra import settings, store
from anka.servicios import quotes
from anka.web import pantalla_caso, seguridad
from anka.web import server as web
from tests.test_app import complete_analysis, test_config

DWG = "TOPOGRAFIA LLANOS 8.dwg"


class Base(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        for parche in (patch.object(settings, "DATA_DIR", Path(self.temp.name)),
                       patch("anka.web.paginas.load_config", return_value=test_config()),
                       patch("anka.web.pantalla_caso.load_config", return_value=test_config())):
            parche.start()
            self.addCleanup(parche.stop)
        self.guardar("t-1")

    def guardar(self, thread_id: str, *, bloqueado: bool = True) -> None:
        analisis = complete_analysis(location="")
        analisis["engine"] = "chatgpt_work"
        analisis["contradictions"] = [{"topic": "área", "details": "10 ha frente a 100 ha", "sources": [],
                                       "status": "pendiente"}]
        if bloqueado:
            analisis["reading_audit"] = {"verification": "incompleta", "attachment_files_pending": [DWG]}
        db = store.open_db(Path(self.temp.name))
        store.save(db, thread_id, "m-" + thread_id, QuoteStatus.REVIEW, "cliente@empresa.co", analisis)
        store.add_message(db, thread_id, "m-" + thread_id, "received", "cliente@empresa.co", "Levantamiento",
                          "Hola, adjunto el plano.", "2026-10-01T09:00:00-05:00")
        db.commit()
        db.close()

    def caso(self, thread_id: str = "t-1"):
        return quotes.get_quote(thread_id)

    def eventos(self, thread_id: str = "t-1") -> list[str]:
        db = store.open_db(Path(self.temp.name))
        try:
            return [fila["kind"] for fila in store.case_events(db, thread_id)]
        finally:
            db.close()


class LaSalida(Base):
    def test_el_caso_bloqueado_siempre_ofrece_el_boton_y_la_otra_salida(self) -> None:
        # Cubre: U-29
        pagina = pantalla_caso.render_case("t-1")
        self.assertIn("Lectura pendiente", pagina)
        self.assertIn('action="/reading-accept"', pagina)
        self.assertIn("Ya revisé estos archivos yo", pagina)
        self.assertIn("data-confirm", pagina.split('action="/reading-accept"')[1].split("</form>")[0])
        self.assertIn(DWG, pagina)
        self.assertIn("volver a ejecutar la tarea de Work", pagina)

    def test_sin_bloqueo_no_se_ofrece(self) -> None:
        # Cubre: U-29
        self.guardar("t-2", bloqueado=False)
        pagina = pantalla_caso.render_case("t-2")
        self.assertNotIn("Ya revisé estos archivos yo", pagina)
        self.assertNotIn("/reading-accept", pagina)


class AlAsumirLaRevision(Base):
    def test_levanta_el_bloqueo_sin_cambiar_el_estado_ni_enviar_nada(self) -> None:
        # Cubre: U-29, R8
        antes = self.caso()
        with patch("anka.infra.google_services.services", side_effect=AssertionError("no debe tocar Google")):
            mensaje = quotes.aceptar_lectura_revisada("t-1")
        self.assertIn("revisada por ti", mensaje)
        despues = self.caso()
        self.assertEqual(despues["status"], antes["status"])
        self.assertFalse(despues["sent_at"])
        pagina = pantalla_caso.render_case("t-1")
        self.assertNotIn("Lectura pendiente", pagina)
        self.assertNotIn('action="/reading-accept"', pagina)
        self.assertIn('id="decision"', pagina)            # las decisiones vuelven a estar disponibles

    def test_deja_huella_en_el_analisis_y_en_el_historial(self) -> None:
        # Cubre: U-29
        quotes.aceptar_lectura_revisada("t-1")
        revision = self.caso()["analysis"]["reading_review"]
        self.assertEqual(revision["files"], [DWG])
        self.assertTrue(revision["at"])
        self.assertIn("reading_reviewed", self.eventos())

    def test_el_aviso_permanente_dice_que_la_lectura_la_reviso_una_persona(self) -> None:
        # Cubre: U-29
        quotes.aceptar_lectura_revisada("t-1")
        pagina = pantalla_caso.render_case("t-1")
        self.assertIn("Lectura revisada por una persona", pagina)
        self.assertIn(DWG, pagina)
        self.assertIn("declaró Work", pagina)

    def test_solo_si_hace_falta_y_una_sola_vez(self) -> None:
        # Cubre: U-29
        self.guardar("t-2", bloqueado=False)
        with self.assertRaisesRegex(ValueError, "no está pendiente"):
            quotes.aceptar_lectura_revisada("t-2")
        quotes.aceptar_lectura_revisada("t-1")
        with self.assertRaisesRegex(ValueError, "no está pendiente"):
            quotes.aceptar_lectura_revisada("t-1")
        with self.assertRaisesRegex(ValueError, "ya no existe"):
            quotes.aceptar_lectura_revisada("no-existe")
        self.assertEqual(self.eventos("t-2").count("reading_reviewed"), 0)
        self.assertEqual(self.eventos().count("reading_reviewed"), 1)

    def test_un_analisis_nuevo_de_work_reemplaza_la_marca_y_se_verifica_de_nuevo(self) -> None:
        # Cubre: U-29
        quotes.aceptar_lectura_revisada("t-1")
        self.assertFalse(pantalla_caso._reading_is_blocked(self.caso()["analysis"]))
        nuevo = dict(self.caso()["analysis"])
        nuevo.pop("reading_review")                      # lo que pasa cuando el puente importa un borrador nuevo
        self.assertTrue(pantalla_caso._reading_is_blocked(nuevo))

    def test_tambien_cubre_el_expediente_de_drive_incompleto(self) -> None:
        # Cubre: U-29
        analisis = {"engine": "chatgpt_work", "DRIVE_PREFLIGHT": {"message_ids": []}}
        self.assertTrue(pantalla_caso._reading_is_blocked(analisis))
        analisis["reading_review"] = {"at": "2026-10-13T10:00:00-05:00", "files": []}
        self.assertFalse(pantalla_caso._reading_is_blocked(analisis))


class ElFormulario(Base):
    def pedir(self, campos, token=True):
        handler = web.ReviewHandler.__new__(web.ReviewHandler)
        datos = ({"csrf_token": seguridad.CSRF_TOKEN} if token else {}) | campos
        crudo = urllib.parse.urlencode(datos).encode()
        handler.headers = {"Host": "127.0.0.1:1", "Content-Length": str(len(crudo))}
        handler.path = "/reading-accept"
        handler.rfile = io.BytesIO(crudo)
        visto = {}
        handler._send_html = lambda contenido, status=200: visto.update(status=status)
        handler._redirect = lambda destino: visto.update(status=303, location=destino)
        handler.do_POST()
        return visto

    def test_el_formulario_asume_la_revision_y_vuelve_al_caso(self) -> None:
        # Cubre: U-29
        respuesta = self.pedir({"thread_id": "t-1"})
        self.assertEqual(respuesta["status"], 303)
        self.assertIn("/case?thread_id=t-1", respuesta["location"])
        self.assertIn("reading_review", self.caso()["analysis"])

    def test_sin_token_no_hace_nada(self) -> None:
        # Cubre: U-29
        self.pedir({"thread_id": "t-1"}, token=False)
        self.assertNotIn("reading_review", self.caso()["analysis"])


if __name__ == "__main__":
    unittest.main()
