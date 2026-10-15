"""Lo que se le pide al cliente se lee como una petición, sin etiquetas internas. Cubre: U-33"""

from tests import _isolate  # noqa: F401  (debe ir primero)

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from anka.dominio import preguntas as dominio
from anka.dominio.status import QuoteStatus
from anka.infra import settings, store
from anka.web import pantalla_caso
from tests.test_app import complete_analysis, test_config

RAIZ = Path(__file__).resolve().parents[1]
CLL170 = [
    "Resolver la correspondencia del polígono LevantamientoRedes con el proyecto Calle 170",
    "Resolver la contradicción entre miércoles y 30-10-2026",
    "Confirmar relación de Andrés Muñoz con SETEC Gomez Cajiao y destinatario contractual (bloqueante)",
    "Protocolo de entrega de SETEC GCA",
    "Template BIM del contratante",
]


class Limpieza(unittest.TestCase):
    def test_se_quitan_las_etiquetas_internas_al_final(self) -> None:
        # Cubre: U-33
        self.assertEqual(dominio.limpiar("Confirmar relación de Andrés Muñoz con OTACC (bloqueante)"),
                         "Confirmar relación de Andrés Muñoz con OTACC")
        self.assertEqual(dominio.limpiar("Definir el datum (bloqueantes)"), "Definir el datum")
        self.assertEqual(dominio.limpiar("Definir el datum (blocking)"), "Definir el datum")
        self.assertEqual(dominio.limpiar("Crítico: confirmar el datum."), "Confirmar el datum.")

    def test_resolver_se_escribe_como_lo_que_se_le_pide(self) -> None:
        # Cubre: U-33
        self.assertEqual(dominio.como_peticion("Resolver la contradicción entre miércoles y 30-10-2026"),
                         "La contradicción entre miércoles y 30-10-2026")
        self.assertEqual(dominio.como_peticion("resolver la correspondencia del polígono con el proyecto Calle 170"),
                         "La correspondencia del polígono con el proyecto Calle 170")

    def test_lo_demas_no_se_toca(self) -> None:
        # Cubre: U-33
        for texto in ("¿Resolver la contradicción entre fechas?", "Confirmar el datum.", "Protocolo de entrega de SETEC GCA",
                      "Resolución de la cartera", ""):
            self.assertEqual(dominio.como_peticion(texto), texto)


class EnLaPantalla(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        for parche in (patch.object(settings, "DATA_DIR", Path(self.temp.name)),
                       patch("anka.web.paginas.load_config", return_value=test_config()),
                       patch("anka.web.pantalla_caso.load_config", return_value=test_config())):
            parche.start()
            self.addCleanup(parche.stop)
        analisis = complete_analysis(location="")
        analisis["other_questions"] = list(CLL170)
        db = store.open_db(Path(self.temp.name))
        store.save(db, "t-1", "m-1", QuoteStatus.REVIEW, "cliente@empresa.co", analisis)
        store.add_message(db, "t-1", "m-1", "received", "cliente@empresa.co", "CLL 170", "Hola.", "2026-10-01T09:00:00-05:00")
        db.commit()
        db.close()

    def cuadro(self) -> str:
        pagina = pantalla_caso.render_case("t-1")
        decision = pagina[pagina.index('id="decision"'):pagina.index("</section>", pagina.index('id="decision"'))]
        return decision.split('name="questions"')[1].split("</textarea>")[0].split(">", 1)[1]

    def test_el_cuadro_trae_los_puntos_de_cll_170_sin_verbos_internos_ni_etiquetas(self) -> None:
        # Cubre: U-33
        cuadro = self.cuadro()
        self.assertIn("La correspondencia del polígono LevantamientoRedes con el proyecto Calle 170", cuadro)
        self.assertIn("La contradicción entre miércoles y 30-10-2026", cuadro)
        self.assertIn("Confirmar relación de Andrés Muñoz con SETEC Gomez Cajiao y destinatario contractual", cuadro)
        self.assertNotIn("Resolver", cuadro)
        self.assertNotIn("bloqueante", cuadro)

    def test_el_correo_que_se_arma_no_trae_resolver_ni_bloqueante(self) -> None:
        # Cubre: U-33
        pagina = pantalla_caso.render_case("t-1", template="preguntas", preguntas_texto=self.cuadro())
        correo = pagina.split('id="cuerpo-correo"')[1].split("</textarea>")[0]
        self.assertIn("La contradicción entre miércoles y 30-10-2026", correo)
        self.assertNotIn("Resolver", correo)
        self.assertNotIn("bloqueante", correo)

    def test_lo_que_la_persona_ya_escribio_no_se_reescribe(self) -> None:
        # Cubre: U-33
        from anka.web import acciones
        acciones._post_review_decision({"thread_id": ["t-1"], "decision": ["ask_client"],
                                        "questions": ["Resolver mi propia duda con el cliente"]})
        quotes_analisis = pantalla_caso.render_case("t-1")
        self.assertNotIn("Mi propia duda", quotes_analisis.split('id="cuerpo-correo"')[1].split("</textarea>")[0])
        self.assertIn("Resolver mi propia duda con el cliente", quotes_analisis.split('id="cuerpo-correo"')[1].split("</textarea>")[0])


class LaSkillDeWork(unittest.TestCase):
    def test_pide_redactar_para_el_cliente_sin_verbos_internos_ni_etiquetas(self) -> None:
        # Cubre: U-33
        skill = (RAIZ / "skills" / "anka-cotizaciones-correo" / "SKILL.md").read_text(encoding="utf-8")
        self.assertIn("Redacta cada punto para el cliente", skill)
        for pieza in ("Resolver", "Definir", "Reconciliar", "(bloqueante)"):
            self.assertIn(pieza, skill)


if __name__ == "__main__":
    unittest.main()
