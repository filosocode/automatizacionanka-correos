"""Mientras el caso espera tu decisión, siempre se ven las preguntas del análisis. Cubre: U-30"""

from tests import _isolate  # noqa: F401  (debe ir primero)

import re
import tempfile
import unittest
import urllib.parse
from pathlib import Path
from unittest.mock import patch

from anka.dominio.reception import reception_assessment
from anka.dominio.status import QuoteStatus
from anka.infra import settings, store
from anka.servicios import quotes
from anka.web import pantalla_caso
from tests.test_app import complete_analysis, test_config

CLIENTE = ["¿Cuál es el datum del levantamiento?", "¿Qué entregables necesitan en DWG?"]
UBICACION = "¿Puede indicar la ubicación del proyecto"   # la recepción la pide si el análisis no trae ubicación
PROPIA = "Revisar internamente la cobertura del KML."


class Base(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        for parche in (patch.object(settings, "DATA_DIR", Path(self.temp.name)),
                       patch("anka.web.paginas.load_config", return_value=test_config()),
                       patch("anka.web.pantalla_caso.load_config", return_value=test_config())):
            parche.start()
            self.addCleanup(parche.stop)

    def guardar(self, preguntas: list[str], *, bloqueado: bool = False, estado: str = QuoteStatus.REVIEW,
                ubicacion: str = "") -> None:
        analisis = complete_analysis(location=ubicacion) if ubicacion else complete_analysis(location="")
        analisis["engine"] = "chatgpt_work"
        analisis["other_questions"] = preguntas
        analisis["question_audit"] = [{"question": PROPIA, "reason_missing": "x", "reviewed_sources": ["correo"],
                                       "question_id": "Q1", "target_finding_ids": [], "unresolved": "x",
                                       "disposition": "internal_review"}]
        if bloqueado:
            analisis["reading_audit"] = {"verification": "incompleta", "attachment_files_pending": ["plano.dwg"]}
        db = store.open_db(Path(self.temp.name))
        store.save(db, "t-1", "m-1", estado, "cliente@empresa.co", analisis)
        store.add_message(db, "t-1", "m-1", "received", "cliente@empresa.co", "Levantamiento", "Hola",
                          "2026-10-01T09:00:00-05:00")
        db.commit()
        db.close()

    def decision(self, pagina: str) -> str:
        return pagina.split('id="decision"')[1].split("</section>")[0]


class LosSelectoresSiempreSalen(Base):
    def test_el_caso_en_revision_con_evaluacion_no_tecnica_ve_las_preguntas(self) -> None:
        # Cubre: U-30
        self.guardar(CLIENTE)
        evaluacion = reception_assessment(quotes.get_quote("t-1")["analysis"])
        self.assertNotEqual(evaluacion.get("status"), "technical_review")     # el caso que antes las ocultaba
        decision = self.decision(pantalla_caso.render_case("t-1"))
        self.assertEqual(decision.count('name="q_id"'), 4)           # 2 del análisis + 1 de la auditoría + 1 de la recepción
        self.assertNotIn("no dejó preguntas candidatas", decision)
        elegidas = re.findall(r'<option value="ask_client" selected', decision)
        self.assertEqual(len(elegidas), 3)                           # las sugeridas para el cliente, ya elegidas

    def test_el_aviso_de_sin_candidatas_solo_sale_si_de_verdad_no_hay(self) -> None:
        # Cubre: U-30
        analisis = complete_analysis()
        analisis["engine"] = "chatgpt_work"
        db = store.open_db(Path(self.temp.name))
        store.save(db, "t-1", "m-1", QuoteStatus.REVIEW, "cliente@empresa.co", analisis)
        db.commit()
        db.close()
        decision = self.decision(pantalla_caso.render_case("t-1"))
        self.assertIn("no dejó preguntas candidatas", decision)
        self.assertEqual(decision.count('name="q_id"'), 0)

    def test_con_la_lectura_bloqueada_no_se_ofrece_ni_se_sugiere_nada(self) -> None:
        # Cubre: U-30
        self.guardar(CLIENTE, bloqueado=True)
        pagina = pantalla_caso.render_case("t-1")
        self.assertNotIn('id="decision"', pagina)
        self.assertNotIn("Usar las preguntas sugeridas", pagina)


class UsarLasSugeridas(Base):
    def enlace(self, pagina: str) -> dict[str, list[str]]:
        href = re.search(r'id="usar-sugeridas" href="([^"]+)"', pagina).group(1).replace("&amp;", "&")
        return urllib.parse.parse_qs(urllib.parse.urlparse(href).query)

    def test_el_boton_carga_solo_las_sugeridas_para_el_cliente(self) -> None:
        # Cubre: U-30
        self.guardar(CLIENTE)
        pagina = pantalla_caso.render_case("t-1")
        self.assertIn("El análisis sugiere 3 preguntas para el cliente", pagina)
        consulta = self.enlace(pagina)
        self.assertEqual(consulta["template"], ["preguntas"])
        lineas = consulta["preguntas"][0].splitlines()
        self.assertEqual(len(lineas), 3)
        self.assertTrue(lineas[0].startswith(UBICACION))
        self.assertEqual(lineas[1:], CLIENTE)                                   # la interna no entra

    def test_seguir_el_enlace_arma_el_correo_con_esas_preguntas_y_no_envia_nada(self) -> None:
        # Cubre: U-30, R8
        self.guardar(CLIENTE)
        consulta = self.enlace(pantalla_caso.render_case("t-1"))
        pagina = pantalla_caso.render_case("t-1", template="preguntas", preguntas_texto=consulta["preguntas"][0])
        cuerpo = re.search(r'name="body"[^>]*>(.*?)</textarea>', pagina, re.S).group(1)
        for pregunta in CLIENTE:
            self.assertIn(pregunta.replace("¿", "").replace("?", ""), cuerpo)
        self.assertNotIn(PROPIA, cuerpo)
        self.assertNotIn('id="usar-sugeridas"', pagina)                         # ya están cargadas
        caso = quotes.get_quote("t-1")
        self.assertEqual(caso["status"], QuoteStatus.REVIEW)
        self.assertFalse(caso["sent_at"])

    def test_no_se_ofrece_si_no_hay_sugeridas_o_si_ya_decidiste(self) -> None:
        # Cubre: U-30
        analisis = complete_analysis()
        analisis["engine"] = "chatgpt_work"
        db = store.open_db(Path(self.temp.name))
        store.save(db, "t-1", "m-1", QuoteStatus.REVIEW, "cliente@empresa.co", analisis)
        store.add_message(db, "t-1", "m-1", "received", "cliente@empresa.co", "Levantamiento", "Hola",
                          "2026-10-01T09:00:00-05:00")
        db.commit()
        db.close()
        self.assertNotIn("Usar las preguntas sugeridas", pantalla_caso.render_case("t-1"))
        self.guardar(CLIENTE)
        self.assertIn("Usar las preguntas sugeridas", pantalla_caso.render_case("t-1"))
        quotes.resolve_technical_review("t-1", "ask_client", CLIENTE[0])
        self.assertNotIn("Usar las preguntas sugeridas", pantalla_caso.render_case("t-1"))


if __name__ == "__main__":
    unittest.main()
