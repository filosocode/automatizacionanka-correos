"""Pantalla del caso por pasos: pasos arriba, glosario con «?», orden de lectura, resumen de la tabla y de los archivos. Cubre: U-31"""

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
              actividades: list[dict] | None = None, adjuntos: list[dict] | None = None,
              lectura_pendiente: str = "", preguntas: list[str] | None = None) -> None:
        analisis = complete_analysis(location="")
        if actividades is not None:
            analisis["activities"] = actividades
        if adjuntos is not None:
            analisis["attachments"] = adjuntos
        if preguntas is not None:
            analisis["other_questions"] = preguntas
        if lectura_pendiente:
            analisis["engine"] = "chatgpt_work"
            analisis["reading_audit"] = {"verification": "incompleta", "attachment_files_pending": [lectura_pendiente]}
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


def adjunto(nombre: str, estado: str = "extraido") -> dict:
    return {"filename": nombre, "status": estado, "coverage": "completa", "reason": "", "blocked_fields": []}


class ArchivosEnUnaLinea(Base):
    def test_resume_y_deja_a_la_vista_solo_lo_que_hay_que_revisar(self) -> None:
        # Cubre: U-31
        self.crear(adjuntos=[adjunto("solicitud.pdf"), adjunto("lote.kmz"), adjunto("planos.dwg", "bloqueado")])
        pagina = pantalla_caso.render_case("t-1")
        self.assertIn("3 archivos · 1 por revisar", pagina)
        seccion = pagina[pagina.index('id="attachments-review"'):pagina.index("Actividades detectadas")]
        fuera = seccion.split('<details class="more archivos-leidos"')[0]
        self.assertIn("planos.dwg", fuera)
        self.assertNotIn("solicitud.pdf", fuera)
        leidos = seccion.split('<details class="more archivos-leidos"')[1]
        self.assertIn("Archivos leídos (2)", leidos)
        self.assertIn("solicitud.pdf", leidos)
        self.assertNotIn("open", leidos.split(">", 1)[0])

    def test_si_todo_se_leyo_nada_pide_atencion(self) -> None:
        # Cubre: U-31
        self.crear(adjuntos=[adjunto("solicitud.pdf"), adjunto("lote.kmz")])
        pagina = pantalla_caso.render_case("t-1")
        self.assertIn("2 archivos · todos leídos", pagina)
        self.assertIn("Archivos leídos (2)", pagina)

    def test_un_archivo_y_ninguno(self) -> None:
        # Cubre: U-31
        self.crear(adjuntos=[adjunto("solicitud.pdf")])
        self.assertIn("1 archivo · todos leídos", pantalla_caso.render_case("t-1"))
        self.crear("t-2", adjuntos=[])
        self.assertIn("Sin archivos adjuntos", pantalla_caso.render_case("t-2"))

    def test_las_tareas_de_revision_siguen_a_la_vista(self) -> None:
        # Cubre: U-31
        self.crear(adjuntos=[adjunto("planos.dwg", "bloqueado")])
        pagina = pantalla_caso.render_case("t-1")
        self.assertIn("Revisar el adjunto planos.dwg", pagina)
        self.assertLess(pagina.index("Qué falta resolver"), pagina.index("Actividades detectadas"))


class YaRevisePrimero(Base):
    def test_con_lectura_sin_verificar_el_aviso_lleva_a_la_salida_que_esta_debajo(self) -> None:
        # Cubre: U-31
        self.crear(lectura_pendiente="planos.dwg")
        pagina = pantalla_caso.render_case("t-1")
        aviso = pagina.index('class="next-step')
        self.assertIn('href="#lectura-revisada">Elegir cómo seguir', pagina[aviso:aviso + 600])
        self.assertLess(aviso, pagina.index('id="lectura-revisada"'))
        self.assertLess(pagina.index('id="lectura-revisada"'), pagina.index("Actividades detectadas"))
        self.assertNotIn('id="decision"', pagina)
        self.assertNotIn('formaction="/send"', pagina)


class TablaDelPrototipo(Base):
    def filas(self, cuantas: int, incompletas: tuple[int, ...] = ()) -> list[dict]:
        return [actividad(f"Actividad {n}", "pendiente" if n in incompletas else "confirmado")
                | ({"notes": "Falta el método de validación altimétrica."} if n in incompletas else {})
                for n in range(1, cuantas + 1)]

    def vista(self, pagina: str) -> str:
        inicio = pagina.index('class="act-resumen"')
        return pagina[inicio:pagina.index("</table>", inicio)]

    def test_la_vista_compacta_tiene_tres_columnas_con_etiqueta_y_motivo(self) -> None:
        # Cubre: U-31
        self.crear(actividades=self.filas(3, incompletas=(2,)))
        vista = self.vista(pantalla_caso.render_case("t-1"))
        for titulo in ("Actividad", "Cantidad", "Estado"):
            self.assertIn(f"<th>{titulo}</th>", vista)
        self.assertEqual(vista.count("<th>"), 3)
        self.assertIn("10 ha", vista)
        self.assertIn(">Completa<", vista)
        self.assertIn(">Por completar<", vista)
        self.assertIn("Falta el método de validación altimétrica.", vista)

    def test_editar_tabla_abre_la_tabla_completa_de_siempre(self) -> None:
        # Cubre: U-31
        self.crear(actividades=self.filas(2))
        pagina = pantalla_caso.render_case("t-1")
        self.assertIn('<details class="more editar-tabla"', pagina)
        self.assertNotIn('class="more editar-tabla" open', pagina)
        editor = pagina[pagina.index('class="more editar-tabla"'):]
        for conservado in ("<th>Servicio/técnica</th>", "Guardar tabla", "Ver historial de cambios", "Descargar Excel",
                           "Más datos: ubicación, entregable, plazo y fuente"):
            self.assertIn(conservado, editor)
        self.assertIn(">Editar tabla<", editor)

    def test_con_mas_de_ocho_se_ven_ocho_y_ver_todas(self) -> None:
        # Cubre: U-31
        self.crear(actividades=self.filas(11))
        pagina = pantalla_caso.render_case("t-1")
        self.assertEqual(pagina.count('class="fila-resumen extra"'), 3)
        self.assertIn("Ver todas (11)", pagina)
        self.assertIn('id="ver-todas"', pagina)

    def test_las_incompletas_nunca_quedan_escondidas(self) -> None:
        # Cubre: U-31
        self.crear(actividades=self.filas(11, incompletas=(10, 11)))
        pagina = pantalla_caso.render_case("t-1")
        self.assertEqual(pagina.count('class="fila-resumen extra"'), 1)
        for fila in pagina.split('<tr class="fila-resumen extra">')[1:]:
            self.assertIn(">Completa<", fila.split("</tr>")[0])

    def test_hasta_ocho_no_hay_ver_todas_y_el_editor_siempre_trae_todas(self) -> None:
        # Cubre: U-31
        self.crear(actividades=self.filas(8))
        pagina = pantalla_caso.render_case("t-1")
        self.assertNotIn("Ver todas", pagina)
        self.assertNotIn('class="fila-resumen extra"', pagina)
        self.crear("t-2", actividades=self.filas(11))
        grande = pantalla_caso.render_case("t-2")
        self.assertIn("name='a10_activity'", grande)
        self.assertNotIn('class="act-row extra"', grande)


def tarjeta(pagina: str) -> str:
    inicio = pagina.index('id="decision"')
    return pagina[inicio:pagina.index("</section>", inicio)]


class TarjetaAhora(Base):
    def test_la_decision_es_una_tarjeta_con_dos_opciones_y_un_solo_boton(self) -> None:
        # Cubre: U-31
        self.crear()
        pagina = pantalla_caso.render_case("t-1")
        self.assertIn('class="section decision-arriba ahora" id="decision"', pagina)
        decision = tarjeta(pagina)
        self.assertIn(">Ahora<", decision)
        for valor in ("sufficient", "ask_client"):
            self.assertIn(f'<input type="radio" name="decision" value="{valor}"', decision)
        self.assertNotIn('value="internal_only"', decision)
        self.assertIn("Con esto alcanza", decision)
        self.assertIn("Hay que preguntarle al cliente", decision)
        self.assertEqual(decision.count('type="submit"'), 1)
        self.assertIn('id="continuar-decision"', decision)

    def test_con_preguntas_sugeridas_viene_marcada_preguntar_y_el_cuadro_las_trae(self) -> None:
        # Cubre: U-31
        self.crear(preguntas=["¿Cuál es el datum del levantamiento?"])
        decision = tarjeta(pantalla_caso.render_case("t-1"))
        self.assertRegex(decision, r'value="ask_client"[^>]*checked')
        cuadro = decision.split('name="questions"')[1].split("</textarea>")[0]
        self.assertIn("¿Cuál es el datum del levantamiento?", cuadro)

    def test_sin_preguntas_sugeridas_no_viene_marcada_ninguna(self) -> None:
        # Cubre: U-31
        analisis = complete_analysis(location="Bogotá")          # información completa: el análisis no sugiere preguntar
        db = store.open_db(Path(self.temp.name))
        store.save(db, "t-1", "m-1", QuoteStatus.REVIEW, "cliente@empresa.co", analisis)
        db.commit()
        db.close()
        decision = tarjeta(pantalla_caso.render_case("t-1"))
        self.assertNotRegex(decision, r"<input[^>]*\bchecked\b")

    def test_solo_con_esto_alcanza_pide_confirmacion(self) -> None:
        # Cubre: U-31
        self.crear()
        decision = tarjeta(pantalla_caso.render_case("t-1"))
        inicio = decision.index('id="continuar-decision"')
        boton = decision[inicio - 200:decision.index("</button>", inicio)]
        self.assertIn('data-confirm-para="sufficient"', boton)
        self.assertIn("data-confirm=", boton)
        layout = (PLANTILLAS / "layout.html").read_text(encoding="utf-8")
        self.assertIn("confirmPara", layout)

    def test_cada_opcion_hace_lo_mismo_que_antes(self) -> None:
        # Cubre: U-31
        from anka.web import acciones
        self.crear("t-s")
        acciones._post_review_decision({"thread_id": ["t-s"], "decision": ["sufficient"]})
        self.assertTrue(quotes.get_quote("t-s").informacion_validada)
        self.crear("t-a", preguntas=["¿Cuál es el datum del levantamiento?"])
        acciones._post_review_decision({"thread_id": ["t-a"], "decision": ["ask_client"],
                                        "questions": ["¿Cuál es el datum del levantamiento?"]})
        self.assertFalse(quotes.get_quote("t-a").informacion_validada)
        self.assertEqual(quotes.get_quote("t-a")["status"], QuoteStatus.REVIEW_PENDING)


INTERNA = "Inspeccionar geometrías DWG y magnitudes (revisión interna)"
DATUM = "¿Cuál es el datum del levantamiento?"


class CuadroUnico(Base):
    def test_hay_un_solo_cuadro_y_ninguna_tarjeta_por_pregunta(self) -> None:
        # Cubre: U-31
        self.crear(preguntas=[DATUM, "¿Qué plazo esperan?", INTERNA])
        decision = tarjeta(pantalla_caso.render_case("t-1"))
        self.assertEqual(decision.count("<textarea"), 1)
        self.assertIn('name="questions"', decision)
        for sobrante in ('name="q_id"', "q_decision_", 'class="pregunta"', "<select"):
            self.assertNotIn(sobrante, decision)

    def test_el_cuadro_trae_solo_lo_que_se_le_pregunta_al_cliente_y_cuenta_lo_interno(self) -> None:
        # Cubre: U-31
        self.crear(preguntas=[DATUM, "¿Qué plazo esperan?", INTERNA])
        decision = tarjeta(pantalla_caso.render_case("t-1"))
        cuadro = decision.split('name="questions"')[1].split("</textarea>")[0]
        self.assertIn(DATUM, cuadro)
        self.assertIn("¿Qué plazo esperan?", cuadro)
        self.assertNotIn("Inspeccionar geometrías", cuadro)
        self.assertRegex(decision, r"1 punto[s]? (lo|los) resuelve[n]? Anka")

    def test_con_el_cuadro_vacio_pide_escribir_una_pregunta(self) -> None:
        # Cubre: U-31
        from anka.web import acciones
        self.crear(preguntas=[DATUM])
        acciones._post_review_decision({"thread_id": ["t-1"], "decision": ["ask_client"], "questions": ["   "]})
        self.assertEqual(quotes.get_quote("t-1")["status"], QuoteStatus.REVIEW)

    def test_lo_que_se_borro_o_se_escribio_se_conserva_al_volver_a_decidir(self) -> None:
        # Cubre: U-31
        from anka.web import acciones
        self.crear(preguntas=[DATUM, "¿Qué plazo esperan?"])
        acciones._post_review_decision({"thread_id": ["t-1"], "decision": ["ask_client"],
                                        "questions": ["¿Qué plazo esperan?\n¿Hay restricciones de acceso al predio?"]})
        quotes.volver_a_decidir("t-1", "reconsiderar")
        cuadro = tarjeta(pantalla_caso.render_case("t-1")).split('name="questions"')[1].split("</textarea>")[0]
        self.assertIn("¿Hay restricciones de acceso al predio?", cuadro)
        self.assertNotIn(DATUM, cuadro)

    def test_el_aviso_amarillo_repetido_no_se_dibuja(self) -> None:
        # Cubre: U-31
        self.crear()
        pagina = pantalla_caso.render_case("t-1")
        self.assertIn('id="decision"', pagina)
        # El aviso sigue en el HTML (U-09, U-28) pero no se dibuja ni se pega arriba mientras la tarjeta «Ahora» está.
        self.assertIn('class="next-step', pagina)
        css = (PLANTILLAS / "style.css").read_text(encoding="utf-8")
        self.assertIn(".next-step:has(+ .decision-arriba) { display: none; }", css)


class PasosComoBarra(Base):
    def test_los_pasos_se_dibujan_como_barra_de_avance(self) -> None:
        # Cubre: U-31
        css = (PLANTILLAS / "style.css").read_text(encoding="utf-8")
        self.assertIn(".process-step::before", css)
        self.assertIn(".process-step.current", css)
        self.assertIn(".decision-arriba.ahora", css)
        self.assertIn(".opcion:has(input:checked)", css)


if __name__ == "__main__":
    unittest.main()
