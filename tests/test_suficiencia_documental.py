"""T-110: un expediente con un documento ilegible no se confirma como suficiente, y un texto interno de Work no se ofrece
como respuesta comercial. La protección está en el servidor, no solo en los botones.

Los casos se parecen a C2 (PDF escaneado que Work no pudo leer), C1 y C3 (archivos leídos), pero son ficticios y viven en
una base temporal: no tocan los datos reales, Gmail ni Drive.
"""

from tests import _isolate  # noqa: F401  (debe ir primero)

import io
import tempfile
import unittest
import urllib.parse
from pathlib import Path
from unittest.mock import patch

from anka.dominio.errores import ErrorDeValidacion
from anka.dominio.correo import comprobar_texto_para_el_cliente
from anka.dominio.status import QuoteStatus
from anka.dominio.work_contract import (
    archivos_ilegibles,
    lectura_sin_verificar,
    motivo_de_insuficiencia_documental,
    respuesta_utilizable,
)
from anka.infra import settings, store
from anka.servicios import quotes, work_import
from anka.web import pantalla_caso, seguridad
from anka.web import server as web
from tests.test_app import complete_analysis, test_config

INTERNO = "Fin del borrador interno. No enviar."
PDF = "tdr_02.pdf"


def analisis_c2() -> dict:
    """Lo que dejó Work con C2: el escaneado sin leer, una fila genérica sin cantidad y la despedida interna como «respuesta»."""
    return {
        "is_quote_request": True, "engine": "chatgpt_work", "project_name": "Levantamiento batimétrico (ficticio)",
        "summary": "El adjunto es un escaneo que Work no pudo interpretar.",
        "activities": [{"activity": "Trabajos descritos en los términos de referencia no inspeccionados", "quantity": "",
                        "unit": "", "ubicacion": "no indicado", "servicio_tecnica": "Batimetría", "entregable": "",
                        "plazo_restriccion": "no indicado", "fuente": "tdr_02.pdf", "estado": "pendiente",
                        "notes": "Falta inspección visual interna del PDF escaneado."}],
        "fields": [], "other_questions": [], "question_audit": [], "scope_dependencies": [], "contradictions": [],
        "attachments": [{"filename": PDF, "size_bytes": 492471, "status": "bloqueado", "coverage": "parcial",
                         "visual_interpretation": "no_certificada", "reason": "El lector no pudo extraer texto.",
                         "blocked_fields": ["actividades", "cantidades"]}],
        "reading_summary": {"status": "pendiente", "warnings": ["Hay adjuntos con cobertura pendiente o parcial."]},
        "work_reply": {"text": INTERNO, "draft_id": "r-1"},
    }


def analisis_valido() -> dict:
    """Un caso como C1 o C3: archivo leído por completo, actividad con cantidad y unidad."""
    analisis = complete_analysis(location="")
    analisis["engine"] = "chatgpt_work"
    analisis["attachments"] = [{"filename": "cantidades_03.xlsx", "status": "extraido", "coverage": "completa"}]
    analisis["reading_summary"] = {"status": "completa declarada", "warnings": []}
    return analisis


class Base(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        for parche in (patch.object(settings, "DATA_DIR", Path(self.temp.name)),
                       patch("anka.web.paginas.load_config", return_value=test_config()),
                       patch("anka.web.pantalla_caso.load_config", return_value=test_config())):
            parche.start()
            self.addCleanup(parche.stop)

    def guardar(self, thread_id: str, analisis: dict, estado: str = QuoteStatus.REVIEW) -> None:
        db = store.open_db(Path(self.temp.name))
        store.save(db, thread_id, "m-" + thread_id, estado, "cliente@empresa.co", analisis)
        store.add_message(db, thread_id, "m-" + thread_id, "received", "cliente@empresa.co", "Solicitud",
                          "Hola, adjunto los términos de referencia.", "2026-10-22T12:37:00-05:00")
        db.commit()
        db.close()

    def caso(self, thread_id: str):
        return quotes.get_quote(thread_id)

    def reescribir(self, thread_id: str, cambio) -> None:
        """Cambia el análisis guardado como lo haría un análisis nuevo de Work; no pasa por la pantalla."""
        analisis = self.caso(thread_id)["analysis"]
        cambio(analisis)
        db = store.open_db(Path(self.temp.name))
        store.save(db, thread_id, "m-" + thread_id, self.caso(thread_id)["status"], "cliente@empresa.co", analisis)
        db.commit()
        db.close()

    def pedir(self, ruta: str, campos: dict, token: bool = True) -> dict:
        handler = web.ReviewHandler.__new__(web.ReviewHandler)
        datos = ({"csrf_token": seguridad.CSRF_TOKEN} if token else {}) | campos
        crudo = urllib.parse.urlencode(datos).encode()
        handler.headers = {"Host": "127.0.0.1:1", "Content-Length": str(len(crudo))}
        handler.path = ruta
        handler.rfile = io.BytesIO(crudo)
        visto: dict = {}
        handler._send_html = lambda contenido, status=200: visto.update(status=status, html=contenido)
        handler._redirect = lambda destino: visto.update(status=303, location=destino)
        handler.do_POST()
        return visto


class ElDominio(unittest.TestCase):
    # Cubre: T-110
    def test_01_un_adjunto_bloqueado_o_no_legible_es_ilegible_y_uno_parcial_o_ignorado_no(self) -> None:
        base = {"engine": "chatgpt_work"}
        self.assertEqual(archivos_ilegibles({**base, "attachments": analisis_c2()["attachments"]}), [PDF])
        self.assertEqual(archivos_ilegibles({**base, "attachments": [{"filename": "a.jpg", "coverage": "no_legible"}]}),
                         ["a.jpg"])
        sin_problema = [{"filename": "b.pdf", "status": "lectura_parcial", "coverage": "parcial"},
                        {"filename": "logo.png", "status": "ignorado", "coverage": None},
                        {"filename": "c.xlsx", "status": "extraido", "coverage": "completa"}, "no es un diccionario"]
        self.assertEqual(archivos_ilegibles({**base, "attachments": sin_problema}), [])
        self.assertEqual(archivos_ilegibles({"engine": "local", "attachments": analisis_c2()["attachments"]}), [])

    # Cubre: T-110
    def test_02_la_lectura_de_c2_queda_sin_verificar_y_la_de_c1_y_c3_no(self) -> None:
        self.assertTrue(lectura_sin_verificar(analisis_c2()))
        self.assertFalse(lectura_sin_verificar(analisis_valido()))

    # Cubre: T-110
    def test_03_el_motivo_pide_primero_la_revision_y_luego_una_cantidad(self) -> None:
        analisis = analisis_c2()
        self.assertIn(PDF, motivo_de_insuficiencia_documental(analisis))
        self.assertIn("Ya revisé estos archivos yo", motivo_de_insuficiencia_documental(analisis))
        analisis["reading_review"] = {"at": "2026-10-22T13:00:00-05:00", "files": [PDF]}
        self.assertIn("ninguna actividad con cantidad", motivo_de_insuficiencia_documental(analisis))
        analisis["activities"][0]["quantity"] = "45"
        self.assertEqual(motivo_de_insuficiencia_documental(analisis), "")
        self.assertEqual(motivo_de_insuficiencia_documental(analisis_valido()), "")

    # Cubre: T-110
    def test_04_la_lectura_incompleta_de_siempre_tambien_exige_la_revision(self) -> None:
        analisis = analisis_valido()
        analisis["reading_audit"] = {"verification": "incompleta", "attachment_files_pending": ["plano.dwg"]}
        self.assertIn("Ya revisé", motivo_de_insuficiencia_documental(analisis))
        analisis["reading_review"] = {"at": "2026-10-22T13:00:00-05:00", "files": ["plano.dwg"]}
        self.assertEqual(motivo_de_insuficiencia_documental(analisis), "")      # sin adjunto ilegible no pide cantidad

    # Cubre: T-110
    def test_05_los_textos_de_control_de_work_no_son_una_respuesta_utilizable(self) -> None:
        for texto in (INTERNO, "fin del borrador interno", "BORRADOR INTERNO — NO ENVIAR", "Borrador interno: no enviar",
                      "[REVISAR ANTES DE ENVIAR - BORRAR ESTA SECCION]\nTexto"):
            self.assertEqual(respuesta_utilizable(texto), "", texto)
        legitimo = "Buen día. Por favor envíenos el PDF con texto para continuar. Gracias."
        self.assertEqual(respuesta_utilizable(legitimo), legitimo)
        self.assertEqual(respuesta_utilizable("Revisar antes de enviar el plano, por favor."), "Revisar antes de enviar el plano, por favor.")


class ElServidorRechazaC2(Base):
    def setUp(self) -> None:
        super().setUp()
        self.guardar("c2", analisis_c2())
        self.guardar("c3", analisis_valido())

    # Cubre: T-110
    def test_10_c2_no_se_confirma_como_suficiente_y_no_cambia_nada(self) -> None:
        antes = self.caso("c2")
        with self.assertRaisesRegex(ErrorDeValidacion, "no pudo leer tdr_02.pdf"):
            quotes.resolve_technical_review("c2", "sufficient")
        despues = self.caso("c2")
        self.assertEqual(despues["status"], QuoteStatus.REVIEW)
        self.assertNotIn("manual_review", despues["analysis"])
        self.assertEqual(despues["updated_at"], antes["updated_at"])

    # Cubre: T-110
    def test_11_una_peticion_directa_al_servidor_tambien_se_rechaza(self) -> None:
        respuesta = self.pedir("/review-decision", {"thread_id": "c2", "decision": "sufficient"})
        self.assertEqual(respuesta["status"], 303)
        self.assertIn("no+pudo+leer", respuesta["location"].replace("%20", "+"))
        self.assertEqual(self.caso("c2")["status"], QuoteStatus.REVIEW)
        self.assertNotIn("manual_review", self.caso("c2")["analysis"])

    # Cubre: T-110
    def test_12_la_revision_humana_no_se_simula_con_campos_del_formulario(self) -> None:
        trucos = {"thread_id": "c2", "decision": "sufficient", "reading_review": "1", "reviewed": "true",
                  "force": "1", "confirm": "yes", "manual_review": "sufficient"}
        self.pedir("/review-decision", trucos)
        self.assertEqual(self.caso("c2")["status"], QuoteStatus.REVIEW)
        self.assertNotIn("reading_review", self.caso("c2")["analysis"])
        # la revisión tampoco llega con un borrador de Work: el importador no copia esa marca
        analisis = work_import._work_analysis({
            "thread_id": "c2", "message_id": "m", "client_email": "c@x.co", "project_name": "P",
            "status": "Requiere revisión técnica interna", "engine": "chatgpt_work",
            "reading_review": {"at": "2026-10-22T13:00:00-05:00", "files": [PDF]},
            "attachments": analisis_c2()["attachments"]})
        self.assertNotIn("reading_review", analisis)

    # Cubre: T-110
    def test_13_la_revision_solo_la_pone_el_boton_y_no_basta_con_una_fila_generica(self) -> None:
        quotes.aceptar_lectura_revisada("c2")
        self.assertEqual(self.caso("c2")["analysis"]["reading_review"]["files"], [PDF])
        with self.assertRaisesRegex(ErrorDeValidacion, "ninguna actividad con cantidad"):
            quotes.resolve_technical_review("c2", "sufficient")          # la fila genérica sin cantidad no basta
        self.assertEqual(self.caso("c2")["status"], QuoteStatus.REVIEW)

    # Cubre: T-110
    def test_14_con_la_revision_y_una_cantidad_real_avanza_por_el_camino_autorizado(self) -> None:
        quotes.aceptar_lectura_revisada("c2")
        self.reescribir("c2", lambda a: a["activities"][0].update(quantity="45", unit="ha"))
        quotes.resolve_technical_review("c2", "sufficient")
        self.assertEqual(self.caso("c2")["status"], QuoteStatus.READY_REVIEW)
        self.assertEqual(self.caso("c2")["analysis"]["manual_review"]["decision"], "sufficient")

    # Cubre: T-110
    def test_15_un_analisis_nuevo_de_work_vuelve_a_pedir_la_revision(self) -> None:
        quotes.aceptar_lectura_revisada("c2")
        self.reescribir("c2", lambda a: a.pop("reading_review"))
        with self.assertRaises(ErrorDeValidacion):
            quotes.resolve_technical_review("c2", "sufficient")

    # Cubre: T-110
    def test_16_volver_a_decidir_con_suficiente_tambien_esta_protegido(self) -> None:
        self.reescribir("c2", lambda a: a.update(manual_review={"decision": "ask_client", "at": "2026-10-22T13:00:00-05:00",
                                                                  "questions": ["¿Ubicación?"]}))
        db = store.open_db(Path(self.temp.name))
        db.execute("UPDATE quotes SET status=? WHERE thread_id='c2'", (QuoteStatus.REVIEW_PENDING,))
        db.commit()
        db.close()
        with self.assertRaisesRegex(ErrorDeValidacion, "no pudo leer"):
            quotes.volver_a_decidir("c2", "suficiente")
        self.assertEqual(self.caso("c2")["status"], QuoteStatus.REVIEW_PENDING)

    # Cubre: T-110
    def test_17_preguntarle_al_cliente_sigue_siendo_la_accion_segura(self) -> None:
        quotes.resolve_technical_review("c2", "ask_client", "¿Puede enviar el PDF con texto seleccionable?")
        self.assertEqual(self.caso("c2")["status"], QuoteStatus.REVIEW_PENDING)

    # Cubre: T-110
    def test_18_c1_y_c3_siguen_pudiendo_confirmar_que_alcanza(self) -> None:
        quotes.resolve_technical_review("c3", "sufficient")
        self.assertEqual(self.caso("c3")["status"], QuoteStatus.READY_REVIEW)

    # Cubre: T-110
    def test_19_la_pantalla_explica_que_archivo_no_se_leyo_que_falta_y_que_hacer(self) -> None:
        pagina = pantalla_caso.render_case("c2")
        self.assertIn("Lectura pendiente", pagina)
        self.assertIn("Work no pudo leer tdr_02.pdf", pagina)
        self.assertIn("no se puede confirmar «Con esto alcanza»", pagina)
        self.assertIn("Ya revisé estos archivos yo", pagina)
        self.assertIn("PDF con texto", pagina)
        self.assertNotIn('id="btn-suficiente"', pagina)
        self.assertNotIn(INTERNO, pagina)


class LaRespuestaInterna(Base):
    def setUp(self) -> None:
        super().setUp()
        self.guardar("c2", analisis_c2())
        self.guardar("c3", analisis_valido() | {"work_reply": {"text": "Buen día. ¿Nos confirma la ubicación?", "draft_id": "r-2"}},
                     QuoteStatus.REVIEW_PENDING)

    # Cubre: T-110
    def test_20_el_importador_no_guarda_el_texto_de_control_como_respuesta(self) -> None:
        analisis = work_import._work_analysis({
            "thread_id": "c2", "message_id": "m", "client_email": "c@x.co", "project_name": "P",
            "status": "Requiere revisión técnica interna", "engine": "chatgpt_work", "draft_id": "r-1",
            "draft_reply": INTERNO + "\n\nOn Thu, 22 Oct 2026 12:37:09 -0500, Cliente wrote:\n> Buen día."})
        self.assertEqual(analisis["work_reply"], {})
        util = work_import._work_analysis({
            "thread_id": "c3", "message_id": "m", "client_email": "c@x.co", "project_name": "P",
            "status": "Faltan datos", "engine": "chatgpt_work", "draft_id": "r-2",
            "draft_reply": "Buen día. ¿Nos confirma la ubicación?"})
        self.assertEqual(util["work_reply"]["text"], "Buen día. ¿Nos confirma la ubicación?")

    # Cubre: T-110
    def test_21_lo_ya_guardado_con_el_texto_de_control_no_se_ofrece_como_novedad(self) -> None:
        db = store.open_db(Path(self.temp.name))
        store._derive_notifications(db)
        db.commit()
        derivadas = [f["thread_id"] for f in db.execute("SELECT thread_id FROM dashboard_notifications "
                                                        "WHERE kind='draft_suggested'")]
        db.close()
        self.assertIn("c2", derivadas)                   # así quedó C2 en la base real: la fila ya existe
        visibles = [n["thread_id"] for n in quotes.unread_notifications() if n["kind"] == "draft_suggested"]
        self.assertNotIn("c2", visibles)
        self.assertIn("c3", visibles)                    # una respuesta legítima sigue anunciándose

    # Cubre: T-110
    def test_22_la_pantalla_no_precarga_el_texto_de_control_en_el_correo(self) -> None:
        self.reescribir("c2", lambda a: a.update(reading_review={"at": "2026-10-22T13:00:00-05:00", "files": [PDF]}))
        pagina = pantalla_caso.render_case("c2")
        self.assertNotIn(INTERNO, pagina)

    # Cubre: T-110
    def test_23_guardar_o_enviar_ese_texto_se_rechaza_en_el_servidor_sin_tocar_gmail(self) -> None:
        with self.assertRaisesRegex(ErrorDeValidacion, "texto interno de Work"):
            comprobar_texto_para_el_cliente(INTERNO)
        with patch("anka.infra.google_services.services", side_effect=AssertionError("no debe tocar Google")):
            for ruta in ("/draft", "/send"):
                respuesta = self.pedir(ruta, {"thread_id": "c2", "body": INTERNO, "to": "cliente@empresa.co"})
                self.assertIn(respuesta["status"], (200, 303, 400), ruta)
        self.assertFalse(self.caso("c2")["sent_at"])
        self.assertFalse(self.caso("c2")["draft_id"])

    # Cubre: T-110
    def test_24_un_correo_legitimo_con_frases_comunes_sigue_pasando(self) -> None:
        comprobar_texto_para_el_cliente("Buen día. Por favor revise antes de enviar los planos. El borrador del contrato va adjunto.")


if __name__ == "__main__":
    unittest.main()
