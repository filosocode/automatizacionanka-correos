"""Pantalla del caso por pasos, etapa 1: pasos arriba, glosario con «?», orden de lectura y resumen de la tabla. Cubre: U-31"""

from tests import _isolate  # noqa: F401  (debe ir primero)

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from anka.dominio.status import QuoteStatus
from anka.infra import settings, store
from anka.servicios import change_control, quotes
from anka.web import pantalla_caso
from tests.test_app import complete_analysis, test_config

PLANTILLAS = Path(__file__).resolve().parents[1] / "anka" / "web" / "templates"


def actividad(nombre: str, estado: str) -> dict:
    return complete_analysis()["activities"][0] | {"activity": nombre, "estado": estado}


class Base(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        for parche in (patch.object(settings, "DATA_DIR", Path(self.temp.name)),
                       patch("anka.web.paginas.load_config", return_value=test_config()),
                       patch("anka.web.pantalla_caso.load_config", return_value=test_config())):
            parche.start()
            self.addCleanup(parche.stop)

    def crear(self, thread_id: str = "t-1", estado: QuoteStatus = QuoteStatus.REVIEW,
              actividades: list[dict] | None = None) -> None:
        analisis = complete_analysis(location="")
        if actividades is not None:
            analisis["activities"] = actividades
        db = store.open_db(Path(self.temp.name))
        store.save(db, thread_id, "m-1", estado, "cliente@empresa.co", analisis)
        store.add_message(db, thread_id, "m-1", "received", "cliente@empresa.co", "Cotización levantamiento",
                          "Hola, necesitamos un levantamiento.", "2026-10-01T09:00:00-05:00")
        db.commit()
        db.close()


class PasosArriba(Base):
    def test_en_revision_dice_paso_1_de_4_antes_del_aviso(self) -> None:
        # Cubre: U-31
        self.crear()
        pagina = pantalla_caso.render_case("t-1")
        self.assertIn("Paso 1 de 4", pagina)
        for paso in ("1. Revisar y aclarar", "2. Información validada", "3. T emitida", "4. T&amp;E aprobada"):
            self.assertIn(paso, pagina)
        self.assertLess(pagina.index("Paso 1 de 4"), pagina.index('class="next-step'))
        self.assertLess(pagina.index('class="process-step'), pagina.index('class="next-step'))

    def test_el_numero_de_paso_sigue_al_caso(self) -> None:
        # Cubre: U-31
        self.crear()
        quotes.resolve_technical_review("t-1", "sufficient")
        self.assertIn("Paso 2 de 4", pantalla_caso.render_case("t-1"))
        change_control.assign_code("t-1", "26145")
        self.assertIn("Paso 3 de 4", pantalla_caso.render_case("t-1"))
        db = store.open_db(Path(self.temp.name))
        db.execute("UPDATE quotes SET code_stage='TE' WHERE thread_id='t-1'")
        db.commit()
        db.close()
        self.assertIn("Paso 4 de 4", pantalla_caso.render_case("t-1"))


class GlosarioConInterrogacion(Base):
    def test_el_glosario_va_con_los_pasos_y_ya_no_abre_la_pagina(self) -> None:
        # Cubre: U-31
        self.crear()
        pagina = pantalla_caso.render_case("t-1")
        self.assertEqual(pagina.count('class="more glosario"'), 1)
        glosario = pagina[pagina.index('class="more glosario"'):]
        glosario = glosario[:glosario.index("</details>")]
        self.assertIn('aria-label="¿Qué significan T, T&amp;E y los términos de lectura?"', glosario)
        for termino in ("<dt>T</dt>", "<dt>T&amp;E</dt>", "<dt>Lectura realizada</dt>", "<dt>Aplicabilidad</dt>"):
            self.assertIn(termino, glosario)
        self.assertGreater(pagina.index('class="more glosario"'), pagina.index("Paso 1 de 4"))
        self.assertLess(pagina.index('class="more glosario"'), pagina.index('class="next-step'))


class OrdenDeLectura(Base):
    def test_aviso_decision_archivos_actividades_y_luego_la_conversacion(self) -> None:
        # Cubre: U-31
        self.crear()
        pagina = pantalla_caso.render_case("t-1")
        orden = ['class="next-step', 'id="decision"', 'id="attachments-review"', "Actividades detectadas",
                 "Conversación con el cliente", "Detalles y trazabilidad del caso"]
        posiciones = [pagina.index(titulo) for titulo in orden]
        self.assertEqual(posiciones, sorted(posiciones))

    def test_con_respuesta_y_emision_el_orden_sigue_de_arriba_a_abajo(self) -> None:
        # Cubre: U-31
        self.crear(estado=QuoteStatus.READY_REVIEW)
        pagina = pantalla_caso.render_case("t-1")
        orden = ["Actividades detectadas", "Conversación con el cliente", "Respuesta al cliente",
                 "Listo para cotizar: emitir la T", "Detalles y trazabilidad del caso"]
        posiciones = [pagina.index(titulo) for titulo in orden]
        self.assertEqual(posiciones, sorted(posiciones))


class ResumenDeLaTabla(Base):
    def test_cuenta_las_actividades_y_las_que_faltan(self) -> None:
        # Cubre: U-31
        self.crear(actividades=[actividad("Planimetría", "confirmado"), actividad("Altimetría", "pendiente"),
                                actividad("Linderos", "confirmado"), actividad("Planos", "contradictorio")])
        self.assertIn("4 actividades · 2 por completar", pantalla_caso.render_case("t-1"))

    def test_si_todo_esta_confirmado_dice_todas_completas(self) -> None:
        # Cubre: U-31
        self.crear(actividades=[actividad("Planimetría", "confirmado")])
        pagina = pantalla_caso.render_case("t-1")
        self.assertIn("1 actividad · todas completas", pagina)

    def test_sin_actividades_no_hay_linea_de_resumen(self) -> None:
        # Cubre: U-31
        self.crear(actividades=[])
        self.assertNotIn("por completar", pantalla_caso.render_case("t-1"))
        self.assertNotIn("todas completas", pantalla_caso.render_case("t-1"))

    def test_la_explicacion_de_los_estados_queda_plegada(self) -> None:
        # Cubre: U-31
        self.crear()
        pagina = pantalla_caso.render_case("t-1")
        self.assertIn("Cómo leer los estados", pagina)
        resumen = pagina.index("Cómo leer los estados")
        self.assertEqual(pagina[:resumen].rsplit("<details", 1)[1].split(">", 1)[0].count("open"), 0)
        self.assertIn("Guardar tabla", pagina)
        self.assertIn("Ver historial de cambios", pagina)


if __name__ == "__main__":
    unittest.main()
