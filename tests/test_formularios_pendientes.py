"""Lo que la persona escribió en un formulario que falló se le devuelve, en todas las rutas que guardan texto. Cubre: U-31"""

from tests import _isolate  # noqa: F401  (debe ir primero)

import io
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from anka.dominio.status import QuoteStatus
from anka.infra import settings, store
from anka.web import formularios_pendientes as pendientes
from anka.web import pantalla_caso, seguridad
from anka.web import server as web
from tests.test_app import complete_analysis, test_config

CSRF = seguridad.CSRF_TOKEN


class Base(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        for parche in (patch.object(settings, "DATA_DIR", Path(self.temp.name)),
                       patch("anka.web.paginas.load_config", return_value=test_config()),
                       patch("anka.web.pantalla_caso.load_config", return_value=test_config())):
            parche.start()
            self.addCleanup(parche.stop)
        pendientes.limpiar_todo()
        self.addCleanup(pendientes.limpiar_todo)

    def crear(self, thread_id: str = "t-1", estado: QuoteStatus = QuoteStatus.REVIEW_PENDING) -> None:
        analisis = complete_analysis(location="")
        analisis["other_questions"] = ["¿Cuál es el datum del levantamiento?"]
        db = store.open_db(Path(self.temp.name))
        store.save(db, thread_id, "m-1", estado, "cliente@empresa.co", analisis)
        store.add_message(db, thread_id, "m-1", "received", "cliente@empresa.co", "Cotización",
                          "Hola, necesitamos un levantamiento.", "2026-10-01T09:00:00-05:00")
        db.commit()
        db.close()

    def post(self, ruta: str, campos: str) -> dict:
        cuerpo = (campos + f"&csrf_token={CSRF}").encode()
        handler = web.ReviewHandler.__new__(web.ReviewHandler)
        enviado: dict = {}
        handler.headers = {"Host": "127.0.0.1:1", "Content-Length": str(len(cuerpo))}
        handler.path = ruta
        handler.rfile = io.BytesIO(cuerpo)
        handler._send_html = lambda contenido, status=200: enviado.update(status=status, page=contenido)
        handler._redirect = lambda ubicacion: enviado.update(status=303, location=ubicacion)
        handler.do_POST()
        return enviado


class ElMecanismo(Base):
    def test_guarda_sin_el_token_y_recorta_lo_enorme(self) -> None:
        # Cubre: U-31
        pendientes.guardar("t-1", "/send", {"body": ["x" * 200_000], "csrf_token": ["secreto"], "thread_id": ["t-1"]})
        ruta, campos = pendientes.ver("t-1") or ("", {})
        self.assertEqual(ruta, "/send")
        self.assertNotIn("csrf_token", campos)
        self.assertEqual(len(campos["body"][0]), pendientes.MAX_CARACTERES)

    def test_caduca_y_no_crece_sin_limite(self) -> None:
        # Cubre: U-31
        pendientes.guardar("t-viejo", "/send", {"body": ["hola"]})
        with patch.object(time, "time", return_value=time.time() + pendientes.VIGENCIA_S + 5):
            self.assertIsNone(pendientes.ver("t-viejo"))
        for n in range(pendientes.MAX_CASOS + 20):
            pendientes.guardar(f"t-{n}", "/send", {"body": ["x"]})
        self.assertLessEqual(pendientes.cantidad(), pendientes.MAX_CASOS)

    def test_solo_las_rutas_que_guardan_texto(self) -> None:
        # Cubre: U-31
        self.assertEqual(pendientes.RUTAS_CON_TEXTO,
                         frozenset({"/send", "/draft", "/code-issue", "/review-decision", "/activities"}))


class AlFallarElEnvio(Base):
    def test_el_mensaje_editado_vuelve_a_la_pantalla_si_gmail_falla(self) -> None:
        # Cubre: U-31
        self.crear()
        with patch("anka.web.acciones.send_dashboard_reply", side_effect=RuntimeError("Gmail no responde")), \
                patch("anka.web.server.log"):
            respuesta = self.post("/send", "thread_id=t-1&body=Mi+mensaje+editado+con+cuidado&esperar=1")
        self.assertNotEqual(respuesta["status"], 303)                      # una página de error, no una redirección
        self.assertIn("Lo que escribiste se conserva", respuesta["page"])
        pagina = pantalla_caso.render_case("t-1")
        caja = pagina.split('id="cuerpo-correo"')[1].split("</textarea>")[0]
        self.assertIn("Mi mensaje editado con cuidado", caja)
        self.assertIn("Lo que escribiste sigue aquí", pagina)
        self.assertRegex(pagina, r'id="esperar-respuesta"[^>]*checked')

    def test_un_aviso_de_la_accion_tambien_conserva_lo_escrito(self) -> None:
        # Cubre: U-31
        self.crear()
        with patch("anka.web.acciones.save_dashboard_draft", side_effect=ValueError("No se pudo guardar el borrador.")):
            respuesta = self.post("/draft", "thread_id=t-1&body=Texto+que+no+quiero+perder")
        self.assertEqual(respuesta["status"], 303)
        caja = pantalla_caso.render_case("t-1").split('id="cuerpo-correo"')[1].split("</textarea>")[0]
        self.assertIn("Texto que no quiero perder", caja)

    def test_un_envio_exitoso_no_deja_nada_guardado(self) -> None:
        # Cubre: U-31
        self.crear()
        with patch("anka.web.acciones.send_dashboard_reply", return_value="Correo enviado."):
            respuesta = self.post("/send", "thread_id=t-1&body=Hola")
        self.assertEqual(respuesta["status"], 303)
        self.assertIsNone(pendientes.ver("t-1"))
        self.assertNotIn("Lo que escribiste sigue aquí", pantalla_caso.render_case("t-1"))

    def test_si_el_caso_ya_no_admite_el_formulario_se_descarta_sin_avisar_en_falso(self) -> None:
        # Cubre: U-31
        self.crear(estado=QuoteStatus.IGNORED)                       # un caso descartado ya no admite responder
        pendientes.guardar("t-1", "/send", {"body": ["Hola"]})
        self.assertNotIn("Lo que escribiste sigue aquí", pantalla_caso.render_case("t-1"))
        self.assertIsNone(pendientes.ver("t-1"))


class OtrasRutas(Base):
    def test_las_preguntas_de_la_decision_vuelven_al_cuadro(self) -> None:
        # Cubre: U-31
        self.crear(estado=QuoteStatus.REVIEW)
        with patch("anka.web.acciones.resolve_technical_review", side_effect=ValueError("No se pudo guardar.")):
            self.post("/review-decision", "thread_id=t-1&decision=ask_client&questions=%C2%BFQu%C3%A9+plazo+esperan%3F")
        pagina = pantalla_caso.render_case("t-1")
        decision = pagina[pagina.index('id="decision"'):pagina.index("</section>", pagina.index('id="decision"'))]
        self.assertIn("¿Qué plazo esperan?", decision.split('name="questions"')[1].split("</textarea>")[0])

    def test_el_numero_y_el_correo_de_la_t_vuelven_al_panel(self) -> None:
        # Cubre: U-31
        self.crear(estado=QuoteStatus.READY_REVIEW)
        with patch("anka.web.acciones.issue_code_and_notify", side_effect=ValueError("El número ya existe.")):
            self.post("/code-issue", "thread_id=t-1&number=26199&body=Correo+de+la+T+editado&suggested_number=26150")
        panel = pantalla_caso.render_case("t-1")
        panel = panel[panel.index('id="emitir-t"'):]
        self.assertIn('value="26199"', panel)
        self.assertIn("Correo de la T editado", panel.split('name="body"')[1].split("</textarea>")[0])

    def test_las_actividades_editadas_vuelven_a_la_tabla_y_el_editor_queda_abierto(self) -> None:
        # Cubre: U-31
        self.crear(estado=QuoteStatus.REVIEW)
        campos = ("thread_id=t-1&row_count=1&a0_activity=Mi+actividad+nueva&a0_estado=confirmado&a0_quantity=7&a0_unit=ha"
                  "&a1_activity=&reason=")
        with patch("anka.web.acciones.save_activities", side_effect=RuntimeError("Disco lleno")), \
                patch("anka.web.server.log"):
            self.post("/activities", campos)
        pagina = pantalla_caso.render_case("t-1")
        self.assertIn("Mi actividad nueva", pagina)
        self.assertRegex(pagina, r'id="editar-toggle" class="toggle-oculto" checked')


if __name__ == "__main__":
    unittest.main()
