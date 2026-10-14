"""Pantalla del caso por pasos: pasos arriba, glosario con «?», orden de lectura, resumen de la tabla y de los archivos. Cubre: U-31"""

from tests import _isolate  # noqa: F401  (debe ir primero)

import re
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
        for paso in ("Revisar y aclarar", "Información validada", "T emitida", "T&amp;E aprobada"):
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
    def test_pasos_tarjeta_actividades_y_luego_las_pestanas(self) -> None:
        # Cubre: U-31
        self.crear()
        pagina = pantalla_caso.render_case("t-1")
        orden = ['class="process-step', 'id="decision"', "Actividades detectadas", 'role="tablist"', 'id="p-conv"',
                 'id="attachments-review"', 'id="p-hist"', 'id="p-det"']
        posiciones = [pagina.index(titulo) for titulo in orden]
        self.assertEqual(posiciones, sorted(posiciones))

    def test_con_la_informacion_validada_la_emision_de_la_t_es_la_tarjeta_de_arriba(self) -> None:
        # Cubre: U-31
        self.crear(estado=QuoteStatus.READY_REVIEW)
        pagina = pantalla_caso.render_case("t-1")
        orden = ['id="emitir-t"', "Actividades detectadas", 'role="tablist"', 'id="respuesta"']
        posiciones = [pagina.index(titulo) for titulo in orden]
        self.assertEqual(posiciones, sorted(posiciones))
        self.assertIn('class="section code-panel ahora" id="emitir-t"', pagina)


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
    def panel(self, pagina: str) -> str:
        return pagina[pagina.index('id="attachments-review"'):pagina.index('id="p-hist"')]

    def test_resume_y_lista_los_archivos_como_el_prototipo(self) -> None:
        # Cubre: U-31
        self.crear(adjuntos=[adjunto("solicitud.pdf"), adjunto("lote.kmz"), adjunto("planos.dwg", "bloqueado")])
        panel = self.panel(pantalla_caso.render_case("t-1"))
        self.assertIn("3 archivos · 1 por revisar", panel)
        tabla = panel[panel.index('class="act-resumen"'):panel.index("</table>")]
        self.assertIn("<th>Archivo</th><th>Qué pasó</th>", tabla)
        self.assertEqual(tabla.count('class="fila-resumen'), 3)
        self.assertLess(tabla.index("planos.dwg"), tabla.index("solicitud.pdf"))       # lo que hay que revisar, primero
        fila = tabla.split('<tr class="fila-resumen falta">')[1].split("</tr>")[0]
        self.assertIn("planos.dwg", fila)
        self.assertIn(">No se pudo leer: ábrelo tú<", fila)
        self.assertEqual(tabla.count(">Leído<"), 2)
        self.assertIn('<details class="more archivos-detalle">', panel)               # el detalle técnico, a un clic

    def test_si_todo_se_leyo_nada_pide_atencion(self) -> None:
        # Cubre: U-31
        self.crear(adjuntos=[adjunto("solicitud.pdf"), adjunto("lote.kmz")])
        panel = self.panel(pantalla_caso.render_case("t-1"))
        self.assertIn("2 archivos · todos leídos", panel)
        self.assertNotIn("fila-resumen falta", panel)

    def test_un_archivo_y_ninguno(self) -> None:
        # Cubre: U-31
        self.crear(adjuntos=[adjunto("solicitud.pdf")])
        self.assertIn("1 archivo · todos leídos", pantalla_caso.render_case("t-1"))
        self.crear("t-2", adjuntos=[])
        self.assertIn("Sin archivos adjuntos", pantalla_caso.render_case("t-2"))

    def test_las_tareas_de_revision_siguen_en_la_pestana(self) -> None:
        # Cubre: U-31
        self.crear(adjuntos=[adjunto("planos.dwg", "bloqueado")])
        panel = self.panel(pantalla_caso.render_case("t-1"))
        self.assertIn("Revisar el adjunto planos.dwg", panel)
        self.assertIn("Qué falta resolver", panel)


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
        cabecera = pagina[pagina.index('class="acts-head"'):pagina.index('class="act-resumen"')]
        self.assertIn("<h2>Actividades detectadas</h2>", cabecera)
        self.assertIn('<a class="button secondary" href="#editar-tabla">+ Agregar</a>', cabecera)
        self.assertIn('<label for="editar-toggle" class="button secondary">Editar tabla</label>', cabecera)
        self.assertRegex(pagina, r'<input type="checkbox" id="editar-toggle" class="toggle-oculto">')
        self.assertNotIn('id="editar-toggle" class="toggle-oculto" checked', pagina)     # cerrada al cargar
        editor = pagina[pagina.index('class="editor-tabla" id="editar-tabla"'):]
        for conservado in ("<th>Servicio/técnica</th>", "Guardar tabla", "Ver historial de cambios", "Descargar Excel",
                           "Más datos: ubicación, entregable, plazo y fuente"):
            self.assertIn(conservado, editor)
        css = (PLANTILLAS / "style.css").read_text(encoding="utf-8")
        self.assertIn(".toggle-oculto:checked ~ .editor-tabla, .editor-tabla:target, .editor-tabla:has(:target)", css)
        # el formulario y el cuadro de exportación están dentro del panel que se abre; fuera solo queda la vista compacta
        self.assertGreater(pagina.index('action="/activities"'), pagina.index('id="editar-tabla"'))
        self.assertGreater(pagina.index('id="hoja-google"'), pagina.index('action="/activities"'))
        self.assertEqual(pagina.count("<details"), pagina.count("</details>"))
        self.assertEqual(pagina.count("<div"), pagina.count("</div>"))

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
        self.assertEqual(decision.count('type="submit"'), 2)                  # uno por opción, que aparece al elegirla
        self.assertIn('id="btn-suficiente"', decision)
        self.assertIn('id="btn-preguntar"', decision)
        self.assertIn(">Marcar como suficiente<", decision)
        self.assertIn(">Preparar el correo<", decision)
        self.assertIn("Elige una opción para continuar. Nada se envía al cliente en este paso.", decision)
        self.assertIn("<h2>Revisa y decide cómo seguir</h2>", decision)

    def test_ninguna_opcion_viene_marcada_y_el_cuadro_trae_las_preguntas_sugeridas(self) -> None:
        # Cubre: U-31
        self.crear(preguntas=["¿Cuál es el datum del levantamiento?"])
        decision = tarjeta(pantalla_caso.render_case("t-1"))
        self.assertNotRegex(decision, r"<input[^>]*\bchecked\b")                    # como el prototipo: decide primero
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
        suficiente = decision[decision.index('id="btn-suficiente"'):decision.index("</button>", decision.index('id="btn-suficiente"'))]
        preguntar = decision[decision.index('id="btn-preguntar"'):decision.index("</button>", decision.index('id="btn-preguntar"'))]
        self.assertIn("data-confirm=", suficiente)
        self.assertNotIn("data-confirm", preguntar)

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
        aviso = pagina[pagina.index('class="next-step'):]
        self.assertIn("hidden>", aviso.split(">", 1)[0] + ">")                 # el aviso repetido se oculta desde el servidor


class IgualAlPrototipo(Base):
    def test_las_opciones_son_tarjetas_sin_circulo_y_cada_boton_sale_al_elegir(self) -> None:
        # Cubre: U-31
        css = (PLANTILLAS / "style.css").read_text(encoding="utf-8")
        self.assertIn(".opcion input[type=radio] { position: absolute; opacity: 0;", css)
        self.assertIn("@supports selector(:has(*))", css)
        self.assertIn('.decision-form:has(input[name="decision"]:checked) .elige-opcion { display: none; }', css)

    def test_el_contenido_del_caso_tiene_ancho_de_lectura_y_los_pasos_van_bajo_su_titulo(self) -> None:
        # Cubre: U-31
        css = (PLANTILLAS / "style.css").read_text(encoding="utf-8")
        self.assertIn(".pantalla-caso { max-width: 880px;", css)
        self.crear()
        pagina = pantalla_caso.render_case("t-1")
        self.assertIn('<div class="pantalla-caso">', pagina)
        paso = pagina.index('class="paso-cabecera"')
        self.assertLess(paso, pagina.index('class="section stepper"'))
        self.assertNotIn("1. Revisar", pagina[paso:pagina.index('class="next-step')])

    def test_los_botones_de_la_tabla_son_de_contorno_con_texto_de_la_marca(self) -> None:
        # Cubre: U-31
        css = (PLANTILLAS / "style.css").read_text(encoding="utf-8")
        self.assertIn(".acts-head .button.secondary, .acts-head label.button { background: transparent; color: var(--accent-dark);", css)

    def test_lo_que_falta_se_dice_antes_de_las_opciones(self) -> None:
        # Cubre: U-31
        self.crear(actividades=[actividad("Planimetría", "confirmado"), actividad("Altimetría", "pendiente")],
                   adjuntos=[adjunto("solicitud.pdf"), adjunto("planos.dwg", "bloqueado")])
        decision = tarjeta(pantalla_caso.render_case("t-1"))
        frase = "Faltan: 1 actividad por completar y 1 archivo por revisar (planos.dwg). Míralo en la tabla y en la pestaña «Archivos»."
        self.assertIn(frase, decision)
        self.assertLess(decision.index(frase), decision.index('class="opciones"'))


class PasosComoBarra(Base):
    def test_los_pasos_se_dibujan_como_barra_de_avance(self) -> None:
        # Cubre: U-31
        css = (PLANTILLAS / "style.css").read_text(encoding="utf-8")
        self.assertIn(".process-step::before", css)
        self.assertIn(".process-step.current", css)
        self.assertIn(".section.ahora", css)
        self.assertIn(".opcion:has(input:checked)", css)


def _pestanas(pagina: str) -> str:
    inicio = pagina.index('role="tablist"')
    return pagina[inicio:pagina.index('id="p-det"', inicio)]


class CabeceraDelPrototipo(Base):
    def test_trae_atras_proyecto_cliente_correo_fecha_y_estado_sin_el_resumen_largo(self) -> None:
        # Cubre: U-31
        self.crear()
        pagina = pantalla_caso.render_case("t-1")
        cabecera = pagina[pagina.index('class="caso-cabecera"'):pagina.index('class="section stepper"')]
        self.assertIn("← Solicitudes", cabecera)
        self.assertIn("Proyecto Norte", cabecera)
        self.assertIn("cliente@empresa.co", cabecera)
        self.assertIn("recibido el", cabecera)
        self.assertIn('class="badge"', cabecera)
        self.assertNotIn("Levantamiento topográfico solicitado.", cabecera)       # el resumen largo ya no va aquí
        self.assertIn("<b>Qué pide:</b> Levantamiento topográfico solicitado.", tarjeta(pagina))


class UnaSolaTarjetaAhora(Base):
    def test_cada_paso_tiene_exactamente_una_tarjeta_ahora(self) -> None:
        # Cubre: U-31
        casos = {"revisar": QuoteStatus.REVIEW, "correo": QuoteStatus.REVIEW_PENDING, "validada": QuoteStatus.READY_REVIEW,
                 "espera": QuoteStatus.WAITING}
        for nombre, estado in casos.items():
            with self.subTest(paso=nombre):
                self.crear(f"t-{nombre}", estado=estado)
                if nombre == "espera":
                    db = store.open_db(Path(self.temp.name))
                    db.execute("UPDATE quotes SET sent_at='2026-10-02T10:00:00-05:00', draft_body='Hola' WHERE thread_id=?",
                               (f"t-{nombre}",))
                    db.commit()
                    db.close()
                pagina = pantalla_caso.render_case(f"t-{nombre}")
                tarjetas = len(re.findall(r'<section class="section[^"]* ahora"', pagina))
                aviso = pagina.count('class="next-step')
                self.assertEqual(tarjetas + (1 if nombre == "espera" else 0), 1)
                if tarjetas:
                    etiqueta = pagina[pagina.index('class="next-step'):].split(">", 1)[0]
                    self.assertIn("hidden", etiqueta)
                self.assertLessEqual(aviso, 1)

    def test_el_aviso_es_una_tarjeta_ahora_que_no_se_pega(self) -> None:
        # Cubre: U-31
        css = (PLANTILLAS / "style.css").read_text(encoding="utf-8")
        self.assertIn('.next-step.aviso-ahora::before { content: "Ahora"; }', css)
        self.assertIn('.next-step::before { content: "Antes de seguir";', css)
        self.assertFalse([linea for linea in css.splitlines() if linea.startswith(".next-step {") and "sticky" in linea])


class CorreoComoPrototipo(Base):
    def test_la_tarjeta_trae_para_mensaje_y_tres_acciones_con_enviar_como_principal(self) -> None:
        # Cubre: U-31
        self.crear(estado=QuoteStatus.REVIEW_PENDING)
        pagina = pantalla_caso.render_case("t-1")
        self.assertIn('<section class="section ahora" id="respuesta">', pagina)
        self.assertLess(pagina.index('id="respuesta"'), pagina.index("Actividades detectadas"))
        correo = pagina[pagina.index('class="correo-form"'):]
        correo = correo[:correo.index("</form>")]
        self.assertIn('<span class="para">cliente@empresa.co</span>', correo)
        self.assertIn("Mensaje (puedes editarlo)", correo)
        enviar = re.search(r'<button type="submit" formaction="/send"[^>]*>', correo, re.S).group(0)
        self.assertIn('class="send-action"', enviar)
        self.assertIn("data-confirm=", enviar)
        self.assertIn('data-resumen="envio"', enviar)
        self.assertIn(">Enviar al cliente<", correo)
        self.assertRegex(correo, r'<button type="submit" class="secondary" formaction="/draft">Guardar borrador en Gmail')
        self.assertLess(correo.index('formaction="/send"'), correo.index('formaction="/draft"'))

    def test_enviar_y_guardar_siguen_siendo_las_mismas_rutas(self) -> None:
        # Cubre: U-31
        self.crear(estado=QuoteStatus.REVIEW_PENDING)
        correo = pantalla_caso.render_case("t-1")
        self.assertEqual(correo.count('formaction="/send"'), 1)
        self.assertEqual(correo.count('formaction="/draft"'), 1)


class CorreoAmigable(Base):
    def correo(self, **extra) -> str:
        self.crear(estado=QuoteStatus.REVIEW_PENDING, **extra)
        return pantalla_caso.render_case("t-1")

    def test_solo_dos_botones_y_los_avisos_largos_ya_no_estan(self) -> None:
        # Cubre: U-31
        pagina = self.correo()
        tarjeta_correo = pagina[pagina.index('id="respuesta"'):pagina.index("Actividades detectadas")]
        self.assertEqual(tarjeta_correo.count('formaction="/send"'), 1)
        self.assertEqual(tarjeta_correo.count('formaction="/draft"'), 1)
        for sobrante in ("Respuesta sugerida lista", "Recepción completa", "Revisa el destinatario", "Antes de enviar te pedimos",
                         "Cambiar las preguntas del correo", "send-zone"):
            self.assertNotIn(sobrante, tarjeta_correo)

    def test_volver_a_redactar_trae_las_preguntas_y_redacta_de_nuevo(self) -> None:
        # Cubre: U-31
        from anka.web import acciones
        self.crear(preguntas=[DATUM, "¿Qué plazo esperan?"])
        acciones._post_review_decision({"thread_id": ["t-1"], "decision": ["ask_client"],
                                        "questions": [f"{DATUM}\n¿Qué plazo esperan?"]})
        pagina = pantalla_caso.render_case("t-1")
        panel = pagina[pagina.index('id="armar"'):]
        panel = panel[:panel.index("</details>")]
        self.assertIn("Volver a redactar con otras preguntas", panel)
        self.assertIn(DATUM, panel.split('name="preguntas"')[1].split("</textarea>")[0])
        self.assertIn(">Redactar de nuevo con estas preguntas<", panel)
        self.assertIn('<input type="hidden" name="template" value="preguntas">', panel)
        redactado = pantalla_caso.render_case("t-1", template="preguntas", preguntas_texto=f"{DATUM}\nOtro plazo")
        self.assertIn("1. " + DATUM, redactado.split('id="cuerpo-correo"')[1].split("</textarea>")[0])

    def test_la_casilla_de_esperar_solo_se_muestra_sin_preguntas_y_el_aviso_la_oculta(self) -> None:
        # Cubre: U-31
        pagina = self.correo()
        self.assertIn('id="caja-esperar"', pagina)
        layout = (PLANTILLAS / "layout.html").read_text(encoding="utf-8")
        self.assertIn("cajaEsperar.hidden = n > 0", layout)

    def test_cambiaste_de_idea_es_una_linea_discreta(self) -> None:
        # Cubre: U-31
        from anka.web import acciones
        self.crear(preguntas=[DATUM])
        acciones._post_review_decision({"thread_id": ["t-1"], "decision": ["ask_client"], "questions": [DATUM]})
        pagina = pantalla_caso.render_case("t-1")
        linea = pagina[pagina.index('class="cambiaste-idea"'):]
        linea = linea[:linea.index("</form>")]
        self.assertIn("¿Cambiaste de idea?", linea)
        self.assertIn(">Volver a elegir las preguntas<", linea)
        self.assertIn(">No preguntar: la información es suficiente<", linea)
        self.assertIn("data-confirm=", linea)


class UnSoloNombreDeEstado(Base):
    VIEJOS = ("Información suficiente para revisión interna", "Requiere revisión técnica interna", "Falta tu decisión técnica",
              "Requiere aclaraciones del cliente")

    def encabezado(self, pagina: str) -> str:
        return pagina[pagina.index('class="caso-cabecera"'):pagina.index('class="next-step')]

    def test_cada_estado_tiene_el_mismo_nombre_en_insignia_nota_y_lista(self) -> None:
        # Cubre: U-31
        from anka.web import paginas
        casos = {QuoteStatus.REVIEW: "Por decidir", QuoteStatus.REVIEW_PENDING: "Por aclarar con el cliente",
                 QuoteStatus.READY_REVIEW: "Información validada"}
        for estado, nombre in casos.items():
            with self.subTest(estado=estado):
                self.crear(f"t-{estado}", estado=estado)
                pagina = pantalla_caso.render_case(f"t-{estado}")
                cabecera = pagina[pagina.index('class="caso-cabecera"'):pagina.index('class="next-step')]
                self.assertIn(nombre, cabecera)                                       # insignia y nota del paso
                self.assertEqual(paginas.case_stage(quotes.get_quote(f"t-{estado}"))[0], nombre)   # la lista
                for viejo in self.VIEJOS:
                    self.assertNotIn(viejo, cabecera)

    def test_con_el_correo_enviado_dice_esperando_al_cliente(self) -> None:
        # Cubre: U-31
        from anka.web import paginas
        self.crear("t-w", estado=QuoteStatus.WAITING)
        db = store.open_db(Path(self.temp.name))
        from datetime import datetime
        ahora = datetime.now().astimezone().isoformat(timespec="seconds")
        db.execute("UPDATE quotes SET sent_at=?, draft_body='Hola' WHERE thread_id='t-w'", (ahora,))
        db.commit()
        db.close()
        self.assertIn("Esperando al cliente", paginas.case_stage(quotes.get_quote("t-w"))[0])
        self.assertIn("Esperando al cliente", self.encabezado(pantalla_caso.render_case("t-w")))

    def test_los_textos_que_envia_work_siguen_aceptandose(self) -> None:
        # Cubre: U-31
        from anka.dominio.status import canonical_work_status
        for etiqueta in ("Requiere revisión técnica interna", "Información suficiente para revisión interna"):
            self.assertEqual(canonical_work_status(etiqueta.lower()), etiqueta)


class ResumenSinJerga(Base):
    def test_el_aviso_de_borradores_va_debajo_de_la_cola_y_con_lenguaje_llano(self) -> None:
        # Cubre: U-31
        from anka.web import paginas
        self.crear()
        rechazado = [{"draft": "r-9", "thread": "t-1", "reason": "Hay bloques JSON repetidos o incompletos."}]
        with patch("anka.web.paginas.rejected_drafts", return_value=rechazado):
            pagina = paginas.render_index()
        self.assertIn("Un borrador de Work no se pudo leer", pagina)
        self.assertLess(pagina.index('class="queue"'), pagina.index('id="borradores-rechazados"'))
        aviso = pagina[pagina.index('id="borradores-rechazados"'):]
        aviso = aviso[:aviso.index("</section>")]
        self.assertIn(">Ver el caso<", aviso)
        visible = aviso[:aviso.index("<details")]
        self.assertNotIn("JSON", visible)
        self.assertNotIn("r-9", visible)
        detalle = aviso[aviso.index("<details"):]
        self.assertIn("Ver el detalle técnico", detalle)
        self.assertIn("Borrador r-9", detalle)
        self.assertIn("Hay bloques JSON repetidos o incompletos.", detalle)


class SeguridadDelUsuario(Base):
    def test_el_aviso_de_decision_nunca_se_oculta_y_el_estandar_solo_con_tarjeta(self) -> None:
        # Cubre: U-31
        for nombre in ("change_banner.html", "link_banner.html", "change_todo.html"):
            abre = re.search(r'<(?:div|section) class="next-step[^>]*>', (PLANTILLAS / nombre).read_text(encoding="utf-8")).group(0)
            self.assertNotIn("hidden", abre)                                      # estos avisos piden una decisión
        self.assertIn("oculto is defined and oculto", (PLANTILLAS / "next_step.html").read_text(encoding="utf-8"))
        self.crear("t-e", estado=QuoteStatus.RESPONSE_SENT)
        etiqueta = pantalla_caso.render_case("t-e")
        aviso = etiqueta[etiqueta.index('class="next-step'):] if 'class="next-step' in etiqueta else ""
        self.assertNotIn("hidden", aviso.split(">", 1)[0])                         # sin tarjeta «Ahora», el aviso se ve

    def test_no_hay_regla_css_que_esconda_avisos_ni_textos_huerfanos(self) -> None:
        # Cubre: U-31
        css = (PLANTILLAS / "style.css").read_text(encoding="utf-8")
        self.assertNotIn(":has(~ .ahora)", css)
        self.crear(adjuntos=[adjunto("planos.dwg", "bloqueado")])
        pagina = pantalla_caso.render_case("t-1")
        self.assertNotIn("las ves y las editas", pagina)
        self.assertNotIn("¿Cómo seguimos?", pagina)

    def test_el_script_protege_el_doble_envio_el_salir_con_lo_escrito_y_agregar(self) -> None:
        # Cubre: U-31
        layout = (PLANTILLAS / "layout.html").read_text(encoding="utf-8")
        for pieza in ("dataset.enviando", "beforeunload", "cuerpo.defaultValue", "history.replaceState",
                      "searchParams.delete('message')", "a[href=\"#editar-tabla\"]", "interruptor.checked = true"):
            self.assertIn(pieza, layout)

    def test_el_mensaje_queda_a_la_vista_tras_saltar_a_un_ancla(self) -> None:
        # Cubre: U-31
        self.crear()
        pagina = pantalla_caso.render_case("t-1", message="Listo: guardado.")
        self.assertIn("<div class='notice flash' role=\"status\">Listo: guardado.</div>", pagina)
        self.assertIn(".flash { position: sticky;", (PLANTILLAS / "style.css").read_text(encoding="utf-8"))


class Pestanas(Base):
    def test_hay_cuatro_pestanas_con_sus_paneles(self) -> None:
        # Cubre: U-31
        self.crear()
        pagina = pantalla_caso.render_case("t-1")
        pestanas = _pestanas(pagina)
        for rotulo in ("Conversación", "Archivos (", "Historial", "Detalle técnico"):
            self.assertIn(rotulo, pestanas)
        self.assertEqual(len(re.findall(r'role="tab"', pestanas)), 4)
        for panel in ('id="p-conv"', 'id="attachments-review"', 'id="p-hist"', 'id="p-det"'):
            self.assertIn(panel, pagina)
        self.assertIn("Conversación con el cliente", pagina[pagina.index('id="p-conv"'):pagina.index('id="attachments-review"')])
        self.assertIn("Información comercial", pagina[pagina.index('id="p-det"'):])

    def test_archivos_dice_cuantos_hay_por_revisar_y_es_la_activa(self) -> None:
        # Cubre: U-31
        self.crear(adjuntos=[adjunto("solicitud.pdf"), adjunto("planos.dwg", "bloqueado")])
        pagina = pantalla_caso.render_case("t-1")
        self.assertIn("Archivos (2) · 1 por revisar", pagina)
        self.assertRegex(pagina, r'id="t-arch" aria-controls="attachments-review" aria-selected="true"')
        self.assertRegex(pagina, r'id="t-conv" aria-controls="p-conv" aria-selected="false"')

    def test_sin_nada_por_revisar_abre_la_conversacion(self) -> None:
        # Cubre: U-31
        self.crear(adjuntos=[adjunto("solicitud.pdf")])
        pagina = pantalla_caso.render_case("t-1")
        self.assertIn("Archivos (1)", pagina)
        self.assertNotIn("Archivos (1) · ", pagina)
        self.assertRegex(pagina, r'id="t-conv" aria-controls="p-conv" aria-selected="true"')

    def test_el_script_abre_la_pestana_de_un_enlace_y_responde_a_las_flechas(self) -> None:
        # Cubre: U-31
        layout = (PLANTILLAS / "layout.html").read_text(encoding="utf-8")
        for pieza in ("data-tabs", "ArrowRight", "closest('[role=\"tabpanel\"]')", "hashchange"):
            self.assertIn(pieza, layout)


if __name__ == "__main__":
    unittest.main()
