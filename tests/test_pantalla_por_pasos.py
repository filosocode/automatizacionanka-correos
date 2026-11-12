"""Pantalla del caso por pasos: pasos arriba, glosario con «?», orden de lectura, resumen de la tabla y de los archivos. Cubre: U-31"""

from tests import _isolate  # noqa: F401  (debe ir primero)

import re
import tempfile
import unittest
from pathlib import Path

from tests.js_de_las_paginas import JS
from unittest.mock import patch

from anka.dominio.status import QuoteStatus
from anka.infra import settings, store
from anka.servicios import change_control, quotes
from anka.web import pantalla_caso
from anka.web import server as web
from tests.test_app import complete_analysis, test_config

PLANTILLAS = Path(__file__).resolve().parents[1] / "anka" / "web" / "templates"


def actividad(nombre: str, estado: str) -> dict:
    return complete_analysis()["activities"][0] | {"activity": nombre, "estado": estado}



def cuadro_de(texto: str) -> str:
    """El correo de la tarjeta de decisión (U-113): lo que hay dentro de su caja, sin la etiqueta."""
    inicio = texto.index('id="correo-unico"')
    return texto[texto.index(">", inicio) + 1:texto.index("</textarea>", inicio)]

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
              lectura_pendiente: str = "", preguntas: list[str] | None = None, proyecto: str = "",
              ultimas: list[str] | None = None) -> None:
        analisis = complete_analysis(location="")
        if proyecto:
            analisis["project_name"] = proyecto
        if actividades is not None:
            analisis["activities"] = actividades
        if adjuntos is not None:
            analisis["attachments"] = adjuntos
        if preguntas is not None:
            analisis["other_questions"] = preguntas
        if ultimas is not None:
            analisis["ultimas_preguntas"] = ultimas
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
        self.assertNotIn("Paso 1 de 4", pagina)                                   # U-107.2: el recorrido ya dice dónde está
        self.assertIn('aria-current="step"><strong>Revisar y aclarar', pagina)
        for paso in ("Revisar y aclarar", "Información validada", "T emitida", "T&amp;E aprobada"):
            self.assertIn(paso, pagina)
        self.assertLess(pagina.index('class="process-step'), pagina.index('class="next-step'))

    def test_el_numero_de_paso_sigue_al_caso(self) -> None:
        # Cubre: U-31
        self.crear()
        quotes.resolve_technical_review("t-1", "sufficient")
        self.assertIn('aria-current="step"><strong>Información validada', pantalla_caso.render_case("t-1"))
        change_control.assign_code("t-1", "26145")
        self.assertIn('aria-current="step"><strong>T emitida', pantalla_caso.render_case("t-1"))
        db = store.open_db(Path(self.temp.name))
        db.execute("UPDATE quotes SET code_stage='TE' WHERE thread_id='t-1'")
        db.commit()
        db.close()
        self.assertIn('aria-current="step"><strong>T&amp;E aprobada', pantalla_caso.render_case("t-1"))


class GlosarioConInterrogacion(Base):
    def test_el_glosario_va_con_los_pasos_y_ya_no_abre_la_pagina(self) -> None:
        # Cubre: U-31
        self.crear()
        pagina = pantalla_caso.render_case("t-1")
        self.assertEqual(pagina.count('class="more glosario"'), 1)
        glosario = pagina[pagina.index('class="more glosario"'):]
        glosario = glosario[:glosario.index("</details>")]
        self.assertIn('>¿Qué significan T y T&amp;E?</summary>', glosario)
        for termino in ("<dt>T</dt>", "<dt>T&amp;E</dt>", "<dt>Lectura realizada</dt>", "<dt>Aplicabilidad</dt>"):
            self.assertIn(termino, glosario)
        self.assertLess(pagina.index('class="more glosario"'), pagina.index('class="process-step'))
        self.assertLess(pagina.index('class="more glosario"'), pagina.index('class="next-step'))


class OrdenDeLectura(Base):
    def test_pasos_tarjeta_actividades_y_luego_las_pestanas(self) -> None:
        # Cubre: U-31
        self.crear()
        pagina = pantalla_caso.render_case("t-1")
        orden = ['class="process-step', "Actividades detectadas", 'id="decision"', 'role="tablist"', 'id="p-conv"',
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
        self.assertIn('<th scope="col">Archivo</th><th scope="col">Qué pasó</th>', tabla)
        self.assertEqual(tabla.count('class="fila-resumen'), 3)
        self.assertLess(tabla.index("planos.dwg"), tabla.index("solicitud.pdf"))       # lo que hay que revisar, primero
        fila = tabla.split('<tr class="fila-resumen falta">')[1].split("</tr>")[0]
        self.assertIn("planos.dwg", fila)
        self.assertIn(">No se pudo leer: ábrelo tú o pídele al cliente un PDF<", fila)
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


# Cubre: U-105, U-112
class EditarDesdeLaTabla(Base):
    def caso(self) -> str:
        filas = [actividad(f"Actividad {n}", "pendiente" if n in (2, 4) else "confirmado")
                 | ({"notes": "Falta el método de validación altimétrica."} if n in (2, 4) else {})
                 for n in range(1, 5)]
        self.crear(actividades=filas)
        return pantalla_caso.render_case("t-1")

    def test_cada_actividad_trae_su_boton_y_su_panel(self) -> None:
        pagina = self.caso()
        botones = re.findall(r'data-panel="panel-(\d+)" aria-controls="panel-\d+" aria-label="(Resolver|Cambiar) ([^"]+)"', pagina)
        self.assertEqual(botones, [("0", "Cambiar", "Actividad 1"), ("1", "Resolver", "Actividad 2"),
                                   ("2", "Cambiar", "Actividad 3"), ("3", "Resolver", "Actividad 4")])
        for indice, _, _ in botones:
            self.assertIn(f'id="panel-{indice}"', pagina)
        self.assertIn('id="panel-nueva"', pagina)

    def test_el_editor_clasico_sigue_con_motivo_y_guardado(self) -> None:
        pagina = self.caso()
        for parte in ('name="reason"', 'id="guardar-tabla"', 'id="editando-una"', 'action="/activities"'):
            self.assertIn(parte, pagina)
        for parte in ("solo-una", "elegida", "Editando: "):
            self.assertIn(parte, JS)


class TablaDelPrototipo(Base):
    # Cubre: U-103
    def filas(self, cuantas: int, incompletas: tuple[int, ...] = ()) -> list[dict]:
        return [actividad(f"Actividad {n}", "pendiente" if n in incompletas else "confirmado")
                | ({"notes": "Falta el método de validación altimétrica."} if n in incompletas else {})
                for n in range(1, cuantas + 1)]

    def vista(self, pagina: str) -> str:
        inicio = pagina.index('id="lista-actividades"')
        return pagina[inicio:pagina.index("</ul>", inicio)]

    def test_salen_todas_con_su_estado_en_palabras_sencillas(self) -> None:
        # Cubre: U-31, U-83, U-103, U-112
        filas = self.filas(4, incompletas=(2,))
        filas[2]["estado"] = "contradictorio"
        self.crear(actividades=filas)
        vista = self.vista(pantalla_caso.render_case("t-1"))
        self.assertEqual(vista.count('class="renglon '), 4)
        self.assertIn('<span class="chip contradictorio">No cuadra</span>', vista)
        self.assertIn('<span class="chip pendiente">Falta un dato</span>', vista)
        self.assertEqual(vista.count('<span class="chip confirmado">Listo</span>'), 2)
        for nombre in ("Actividad 1", "Actividad 2", "Actividad 3", "Actividad 4"):
            self.assertIn(nombre, vista)
        self.assertIn('class="renglon contradictorio" data-estado="contradictorio"', vista)

    def test_los_filtros_cuentan_cada_estado_y_solo_existen_si_hay_de_ese_estado(self) -> None:
        # Cubre: U-112
        filas = self.filas(4, incompletas=(2,))
        self.crear(actividades=filas)
        pagina = pantalla_caso.render_case("t-1")
        self.assertIn('data-filtro="todas"', pagina)
        self.assertIn('data-filtro="pendiente"', pagina)
        self.assertIn('data-filtro="confirmado"', pagina)
        self.assertNotIn('data-filtro="contradictorio"', pagina)
        self.assertIn('id="filtros-actividades" role="group" aria-label="Qué actividades ver" hidden', pagina)  # sin JS no se ven
        self.assertIn("sin-coincidencias", JS)

    def test_dice_puntualmente_que_falta(self) -> None:
        # Cubre: U-103
        pendiente = actividad("Levantamiento", "pendiente") | {"ubicacion": "no indicado", "plazo_restriccion": "", "notes": ""}
        contradictoria = actividad("Curvas de nivel", "contradictorio") | {"notes": "El correo dice 10 ha y el KMZ 12 ha."}
        sin_nota = actividad("Batimetría", "contradictorio") | {"notes": ""}
        self.crear(actividades=[pendiente, contradictoria, sin_nota])
        vista = self.vista(pantalla_caso.render_case("t-1"))
        self.assertIn("Falta ubicación, plazo.", vista)
        self.assertIn("El correo dice 10 ha y el KMZ 12 ha.", vista)
        self.assertIn("Hay información que no coincide", vista)

    def test_la_nota_larga_se_acorta_y_sin_nada_que_decir_pide_confirmar(self) -> None:
        # Cubre: U-103
        completa = {"plazo_restriccion": "30 días"}
        larga = actividad("Levantamiento", "pendiente") | completa | {"notes": "Revisar el acceso al predio " + "por la vía veredal y la finca vecina " * 8}
        vacia = actividad("Replanteo", "pendiente") | completa | {"notes": ""}
        self.crear(actividades=[larga, vacia])
        vista = self.vista(pantalla_caso.render_case("t-1"))
        detalle = vista.split('<p class="detalle">')[1].split("</p>")[0]
        self.assertIn("…", detalle)
        self.assertLess(len(detalle), 220)
        self.assertIn("Falta confirmar la actividad.", vista)

    def test_una_actividad_lista_dice_lo_que_tiene(self) -> None:
        # Cubre: U-112
        self.crear(actividades=self.filas(3))
        pagina = pantalla_caso.render_case("t-1")
        self.assertIn('id="tabla-resumen"', pagina)
        self.assertIn("3 actividades · todas completas", pagina)
        self.assertEqual(pagina.count('class="chip confirmado">Listo<'), 3)

    def test_la_lista_no_trae_formularios_pero_los_paneles_si(self) -> None:
        # Cubre: U-103, U-112
        self.crear(actividades=self.filas(11, incompletas=(10, 11)))
        pagina = pantalla_caso.render_case("t-1")
        lista = self.vista(pagina)
        for retirado in ("Ver todas", "Ver detalle y fuentes", "Cómo leer esta tabla", "ficha-alcance", "<textarea", "<form"):
            self.assertNotIn(retirado, lista)
        self.assertEqual(lista.count('class="renglon '), 11)
        self.assertEqual(pagina.count('action="/activity"'), 2 * 1 + 9 * 3 + 1 + 11)     # 2 por resolver, 9 listas con 3 acciones, 1 nueva, 11 en «Corregir la tabla»

    def test_cada_panel_pide_el_respaldo_y_no_ofrece_suponer(self) -> None:
        # Cubre: U-112
        self.crear(actividades=self.filas(2, incompletas=(1,)))
        pagina = pantalla_caso.render_case("t-1")
        panel = pagina[pagina.index('id="panel-0"'):pagina.index('id="panel-nueva"')]
        for parte in ('value="cliente"', 'value="documento"', 'value="tecnico"', 'name="medio"', 'name="fecha"', 'name="nota"',
                      "¿Ya tenemos la respuesta?", 'name="accion" value="resolver"', 'name="indice" value="0"'):
            self.assertIn(parte, panel)
        self.assertNotIn("supuesto", pagina.lower().replace("supuestos", ""))
        for parte in ("Volver al correo", "Corregir un dato", "Volver a abrirla", "Quitarla de la tabla"):
            self.assertIn(parte, pagina)

    def test_un_panel_rechazado_se_reabre_con_lo_escrito(self) -> None:
        # Cubre: U-112, U-31
        from anka.web import formularios_pendientes
        self.crear(actividades=self.filas(2, incompletas=(1,)))
        formularios_pendientes.guardar("t-1", "/activity", {
            "thread_id": ["t-1"], "accion": ["resolver"], "indice": ["0"], "origen": ["cliente"],
            "nota": ["Me lo dijo por teléfono"], "csrf_token": ["x"]})
        pagina = pantalla_caso.render_case("t-1")
        self.assertIn('data-reabrir="panel-0" data-accion="resolver"', pagina)
        self.assertIn("Me lo dijo por tel", pagina)
        formularios_pendientes.limpiar("t-1")
        self.assertNotIn("data-reabrir", pantalla_caso.render_case("t-1"))

    def test_el_editor_clasico_abre_la_tabla_completa_de_siempre(self) -> None:
        # Cubre: U-31, U-103
        self.crear(actividades=self.filas(2, incompletas=(1,)))
        pagina = pantalla_caso.render_case("t-1")
        cabecera = pagina[pagina.index('class="acts-head"'):pagina.index('id="tabla-resumen"')]
        self.assertIn("<h2>Actividades detectadas</h2>", cabecera)
        self.assertIn('<button type="button" class="button secondary" id="agregar-fila" aria-controls="panel-nueva">+ Agregar</button>', cabecera)
        self.assertIn('id="editar-boton" aria-expanded="false" aria-controls="editar-tabla">Corregir la tabla</button>', cabecera)
        self.assertNotIn("editar-toggle", pagina)
        self.assertNotIn("toggle-oculto", pagina)
        self.assertNotIn('id="editar-boton" aria-expanded="true"', pagina)     # cerrada al cargar
        editor = pagina[pagina.index('class="editor-tabla" id="editar-tabla"'):]
        for conservado in ('<th role="columnheader" scope="col">Tipo de servicio</th>', "Guardar tabla", "Ver historial de cambios", "Bajar en Excel",
                           "Más datos: ubicación, entregable, plazo y fuente"):
            self.assertIn(conservado, editor)
        css = (PLANTILLAS / "style.css").read_text(encoding="utf-8")
        self.assertIn(".editor-tabla:target, .editor-tabla:has(:target)", css)
        self.assertIn(".editor-tabla.abierto { display: block; }", css)
        self.assertGreater(pagina.index('action="/activities"'), pagina.index('id="editar-tabla"'))
        self.assertGreater(pagina.index('id="hoja-google"'), pagina.index('action="/activities"'))
        self.assertEqual(pagina.count("<details"), pagina.count("</details>"))
        self.assertEqual(pagina.count("<div"), pagina.count("</div>"))

    def test_con_la_decision_pendiente_cada_fila_trae_su_casilla_y_el_cuadro_ya_trae_las_marcadas(self) -> None:
        # Cubre: U-103
        pendiente = actividad("Batimetría del lago", "pendiente") | {"ubicacion": "no indicado", "plazo_restriccion": ""}
        con_nota = actividad("Replanteo", "pendiente") | {"plazo_restriccion": "30 días", "notes": "No dice cuántos lotes."}
        contradictoria = actividad("Curvas", "contradictorio") | {"notes": "10 ha en el correo y 12,4 ha en el KMZ"}
        self.crear(actividades=[pendiente, con_nota, contradictoria])
        pagina = pantalla_caso.render_case("t-1")
        vista = self.vista(pagina)
        self.assertEqual(vista.count('name="preguntar_fila"'), 3)
        self.assertEqual(vista.count('form="decision-form"'), 3)
        self.assertIn('id="decision-form"', pagina)
        self.assertEqual(vista.count(" checked"), 1)               # solo la pendiente con datos esenciales vacíos
        self.assertIn('aria-label="Preguntar al cliente por Batimetría del lago"', vista)
        self.assertIn("1. Batimetría del lago: la ubicación y el plazo", cuadro_de(pagina))   # ya escrita en el correo
        self.assertIn('value="Replanteo: No dice cuántos lotes. ¿Nos lo pueden aclarar?"', vista)
        self.assertIn('value="Curvas: 10 ha en el correo y 12,4 ha en el KMZ. ¿Cuál es el dato correcto?"', vista)

    def test_sin_decision_pendiente_la_lista_no_trae_casillas(self) -> None:
        # Cubre: U-103
        self.crear(actividades=self.filas(2, incompletas=(1,)), estado=QuoteStatus.WAITING)
        vista = self.vista(pantalla_caso.render_case("t-1"))
        self.assertNotIn("preguntar_fila", vista)

    def test_lo_que_la_persona_escribio_en_el_cuadro_no_se_reescribe(self) -> None:
        # Cubre: U-103
        pendiente = actividad("Batimetría", "pendiente") | {"ubicacion": "no indicado"}
        self.crear(actividades=[pendiente], ultimas=["Mi propia pregunta"])
        pagina = pantalla_caso.render_case("t-1")
        cuadro = cuadro_de(pagina)
        self.assertIn("Mi propia pregunta", cuadro)
        self.assertNotIn("Batimetría", cuadro)
        self.assertNotIn(" checked", self.vista(pagina))

    def test_el_resumen_duplicado_se_retiro_y_los_archivos_siguen_diciendose(self) -> None:
        # Cubre: U-103
        self.crear(actividades=self.filas(2, incompletas=(2,)), adjuntos=[adjunto("planos.dwg", "bloqueado")])
        pagina = pantalla_caso.render_case("t-1")
        self.assertNotIn("Por resolver", pagina)
        self.assertNotIn("Resumen de la solicitud", pagina)
        self.assertNotIn("actividad por completar", pagina)
        self.assertIn("Faltan: 1 archivo por revisar (planos.dwg).", pagina)
        self.assertLess(pagina.index('id="alcance"'), pagina.index('id="decision"'))    # U-107: actividades a la izquierda
        self.assertLess(pagina.index('id="decision"'), pagina.index('id="analisis-original"'))

    def test_sin_javascript_las_casillas_marcadas_se_suman_al_cuadro_sin_repetir(self) -> None:
        # Cubre: U-103
        from anka.web import acciones
        recibido: dict = {}

        def decidir(thread_id, decision, preguntas, **_):
            recibido.update(decision=decision, preguntas=preguntas)
            return "ok"

        formulario = {"thread_id": ["t-1"], "decision": ["ask_client"], "questions": ["¿Cuál es el plazo de «A»?"],
                      "preguntar_fila": ["¿Cual es el plazo de «A»?", "¿Podría indicar la ubicación de «B»?"]}
        with patch.object(acciones, "resolve_technical_review", decidir):
            acciones._post_review_decision(formulario)
        self.assertEqual(recibido["preguntas"], "¿Cuál es el plazo de «A»?\n¿Podría indicar la ubicación de «B»?")
        with patch.object(acciones, "resolve_technical_review", decidir):
            acciones._post_review_decision(formulario | {"decision": ["sufficient"], "questions": [""]})
        self.assertEqual(recibido["preguntas"], "")          # solo se suman cuando se decide preguntar

    def test_el_editor_siempre_trae_todas_las_filas(self) -> None:
        # Cubre: U-103
        self.crear(actividades=self.filas(11))
        self.assertIn("name='a10_activity'", pantalla_caso.render_case("t-1"))


def tarjeta(pagina: str) -> str:
    inicio = pagina.index('id="decision"')
    return pagina[inicio:pagina.index("</section>", inicio)]


class TarjetaAhora(Base):
    def test_la_decision_es_una_tarjeta_con_la_caja_de_preguntas_y_dos_botones(self) -> None:
        # Cubre: U-31, U-106
        self.crear()
        pagina = pantalla_caso.render_case("t-1")
        self.assertIn('class="section decision-arriba ahora" id="decision"', pagina)
        decision = tarjeta(pagina)
        self.assertNotIn(">Ahora<", decision)                                 # U-107.3b: sin título «AHORA»
        self.assertIn('name="decision" value="sufficient"', decision)
        self.assertNotIn('value="ask_client"', decision)                      # U-113: ya no hay un paso aparte para redactar
        self.assertNotIn('type="radio"', decision)                            # sin elegir antes: el correo siempre está
        self.assertIn('id="correo-unico"', decision)
        self.assertNotIn('value="internal_only"', decision)
        self.assertIn(">Con esto alcanza<", decision)
        self.assertEqual(decision.count('type="submit"'), 3)                  # enviar, guardar borrador o con esto alcanza
        for identificador in ("btn-suficiente", "btn-enviar-preguntas", "btn-borrador-preguntas"):
            self.assertIn(f'id="{identificador}"', decision)
        self.assertIn(">Enviar al cliente<", decision)
        self.assertNotIn(">Redactar el correo<", decision)
        self.assertIn("Nada sale al cliente sin que lo revises y confirmes", decision)
        self.assertIn("<h2>Mensaje para el cliente</h2>", decision)

    def test_el_cuadro_trae_las_preguntas_sugeridas(self) -> None:
        # Cubre: U-31
        self.crear(preguntas=["¿Cuál es el datum del levantamiento?"])
        decision = tarjeta(pantalla_caso.render_case("t-1"))
        cuadro = cuadro_de(decision)
        self.assertIn("¿Cuál es el datum del levantamiento?", cuadro)

    def test_sin_preguntas_sugeridas_el_cuadro_viene_vacio(self) -> None:
        # Cubre: U-31
        analisis = complete_analysis(location="Bogotá")          # información completa: el análisis no sugiere preguntar
        db = store.open_db(Path(self.temp.name))
        store.save(db, "t-1", "m-1", QuoteStatus.REVIEW, "cliente@empresa.co", analisis)
        db.commit()
        db.close()
        decision = tarjeta(pantalla_caso.render_case("t-1"))
        self.assertEqual(cuadro_de(decision).strip(), "")
        self.assertIn(' disabled', decision[decision.index('id="btn-enviar-preguntas"'):decision.index(">Enviar al cliente<")])

    def test_solo_con_esto_alcanza_pide_confirmacion(self) -> None:
        # Cubre: U-31
        self.crear()
        decision = tarjeta(pantalla_caso.render_case("t-1"))
        suficiente = decision[decision.index('id="btn-suficiente"'):decision.index("</button>", decision.index('id="btn-suficiente"'))]
        enviar = decision[decision.index('id="btn-enviar-preguntas"'):decision.index("</button>", decision.index('id="btn-enviar-preguntas"'))]
        borrador = decision[decision.index('id="btn-borrador-preguntas"'):decision.index("</button>", decision.index('id="btn-borrador-preguntas"'))]
        self.assertIn("data-confirm=", suficiente)
        self.assertIn('data-resumen="envio"', enviar)                          # R8: enviar pide la confirmación de siempre
        self.assertIn("data-confirm=", enviar)
        self.assertNotIn("data-confirm", borrador)                             # guardar un borrador no envía nada

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
        self.assertIn('id="correo-unico"', decision)
        for sobrante in ('name="q_id"', "q_decision_", 'class="pregunta"', "<select"):
            self.assertNotIn(sobrante, decision)

    def test_el_cuadro_trae_solo_lo_que_se_le_pregunta_al_cliente_y_cuenta_lo_interno(self) -> None:
        # Cubre: U-31
        self.crear(preguntas=[DATUM, "¿Qué plazo esperan?", INTERNA])
        decision = tarjeta(pantalla_caso.render_case("t-1"))
        cuadro = cuadro_de(decision)
        self.assertIn(DATUM, cuadro)
        self.assertIn("¿Qué plazo esperan?", cuadro)
        self.assertNotIn("Inspeccionar geometrías", cuadro)
        self.assertRegex(decision, r"1 punto[s]? (lo|los) resuelve[n]? el equipo de Anka")

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
        cuadro = cuadro_de(tarjeta(pantalla_caso.render_case("t-1")))
        self.assertIn("¿Hay restricciones de acceso al predio?", cuadro)
        self.assertNotIn(DATUM, cuadro)

    def test_el_aviso_amarillo_repetido_no_se_dibuja(self) -> None:
        # Cubre: U-31
        self.crear()
        pagina = pantalla_caso.render_case("t-1")
        self.assertIn('id="decision"', pagina)
        # El aviso sigue en el HTML (U-09, U-80) pero no se dibuja ni se pega arriba mientras la tarjeta «Ahora» está.
        self.assertIn('class="next-step', pagina)
        aviso = pagina[pagina.index('class="next-step'):]
        self.assertIn("hidden>", aviso.split(">", 1)[0] + ">")                 # el aviso repetido se oculta desde el servidor


class IgualAlPrototipo(Base):
    def test_las_opciones_son_tarjetas_sin_circulo_y_cada_boton_sale_al_elegir(self) -> None:
        # Cubre: U-31
        css = (PLANTILLAS / "style.css").read_text(encoding="utf-8")
        self.assertIn(".opcion input[type=radio] { position: absolute; opacity: 0;", css)
        self.assertNotIn(".elige-opcion", css)                 # ya no se elige antes: la caja y los botones siempre están

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
        self.assertIn(".acts-head .button.secondary { background: transparent; color: var(--accent-dark);", css)

    def test_lo_que_falta_se_dice_antes_de_las_opciones(self) -> None:
        # Cubre: U-31
        self.crear(actividades=[actividad("Planimetría", "confirmado"), actividad("Altimetría", "pendiente")],
                   adjuntos=[adjunto("solicitud.pdf"), adjunto("planos.dwg", "bloqueado")])
        decision = tarjeta(pantalla_caso.render_case("t-1"))
        frase = "Faltan: 1 archivo por revisar (planos.dwg). Míralo en la pestaña «Archivos»."
        self.assertIn(frase, decision)
        self.assertLess(decision.index(frase), decision.index('id="preguntas-bloque"'))


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
        self.assertIn("← Solicitudes de cotización", cabecera)
        self.assertIn("Proyecto Norte", cabecera)
        self.assertIn("cliente@empresa.co", cabecera)
        self.assertIn("recibido el", cabecera)
        self.assertIn('class="badge"', cabecera)
        self.assertNotIn("Levantamiento topográfico solicitado.", cabecera)       # el resumen largo ya no va aquí
        self.assertNotIn("<b>Qué pide:</b>", tarjeta(pagina))
        self.assertIn("Ver análisis original", pagina)


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
        # La marca es texto real (U-58), no contenido generado por CSS: un lector de pantalla la lee.
        self.assertNotIn('content: "Ahora"', css)
        self.assertNotIn('content: "Antes de seguir"', css)
        self.assertIn(".next-step > .marca-paso {", css)
        self.assertIn('<span class="marca-paso">Ahora</span>', (PLANTILLAS / "next_step.html").read_text(encoding="utf-8"))
        self.assertFalse([linea for linea in css.splitlines() if linea.startswith(".next-step {") and "sticky" in linea])


class CorreoComoPrototipo(Base):
    def test_la_tarjeta_trae_para_mensaje_y_tres_acciones_con_enviar_como_principal(self) -> None:
        # Cubre: U-31
        self.crear(estado=QuoteStatus.REVIEW_PENDING)
        pagina = pantalla_caso.render_case("t-1")
        self.assertIn('<section class="section ahora" id="respuesta">', pagina)
        self.assertIn('<div class="dos-columnas dos-columnas-correo">', pagina)   # U-107.11: actividades | correo
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

    def test_al_revisar_el_correo_hay_dos_tarjetas_actividades_a_la_izquierda_y_correo_a_la_derecha(self) -> None:
        # Cubre: U-107
        self.crear(estado=QuoteStatus.REVIEW_PENDING, actividades=[actividad("Control vertical", "pendiente")])
        pagina = pantalla_caso.render_case("t-1")
        pares = pagina[pagina.index('class="dos-columnas dos-columnas-correo"'):]
        pares = pares[:pares.index('class="analisis-original"') if 'class="analisis-original"' in pares else None]
        self.assertLess(pares.index('id="alcance"'), pares.index('id="respuesta"'))     # izquierda, luego derecha
        self.assertIn("Revisa el correo y envíalo", pares)
        self.assertIn('formaction="/send"', pares)
        css = (PLANTILLAS / "style.css").read_text(encoding="utf-8")
        self.assertIn(".dos-columnas-correo { height: auto; }", css)                    # sin alto fijo: el correo se ve entero
        self.assertIn(".dos-columnas-correo > #alcance { align-self: start; position: sticky;", css)   # la tabla queda fija
        self.assertIn("@media (max-width: 860px) { .dos-columnas-correo > #alcance { position: static;", css)  # y no en 1 columna
        # con la decisión pendiente se sigue usando el diseño de siempre, sin la variante del correo
        self.crear("t-2", estado=QuoteStatus.REVIEW, actividades=[actividad("Control vertical", "pendiente")])
        self.assertNotIn("dos-columnas-correo", pantalla_caso.render_case("t-2"))

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
        tarjeta_correo = pagina[pagina.index('id="respuesta"'):]
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
        self.assertIn("Cambiar las preguntas", panel)
        self.assertIn(DATUM, panel.split('name="preguntas"')[1].split("</textarea>")[0])
        self.assertIn(">Preparar el correo de nuevo<", panel)
        self.assertIn('<input type="hidden" name="template" value="preguntas">', panel)
        redactado = pantalla_caso.render_case("t-1", template="preguntas", preguntas_texto=f"{DATUM}\nOtro plazo")
        self.assertIn("1. " + DATUM, redactado.split('id="cuerpo-correo"')[1].split("</textarea>")[0])

    def test_la_casilla_de_esperar_solo_se_muestra_sin_preguntas_y_el_aviso_la_oculta(self) -> None:
        # Cubre: U-31
        pagina = self.correo()
        self.assertIn('id="caja-esperar"', pagina)
        layout = ((PLANTILLAS / "layout.html").read_text(encoding="utf-8") + (PLANTILLAS / "app.js").read_text(encoding="utf-8") + (PLANTILLAS / "tema.js").read_text(encoding="utf-8"))
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
        self.assertIn(">Volver a decidir<", linea)
        self.assertIn(">No preguntar: con esto alcanza<", linea)
        self.assertIn("data-confirm=", linea)


class UnSoloNombreDeEstado(Base):
    VIEJOS = ("Información suficiente para revisión interna", "Requiere revisión técnica interna", "Falta tu decisión técnica",
              "Requiere aclaraciones del cliente")

    def encabezado(self, pagina: str) -> str:
        return pagina[pagina.index('class="caso-cabecera"'):pagina.index('class="next-step')]

    def test_cada_estado_tiene_el_mismo_nombre_en_insignia_nota_y_lista(self) -> None:
        # Cubre: U-31
        from anka.web import paginas
        casos = {QuoteStatus.REVIEW: "Por decidir", QuoteStatus.REVIEW_PENDING: "Correo por revisar",
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
        rechazado = [{"draft": "r-9", "thread": "t-1", "reason": "Hay bloques de datos repetidos o incompletos en el borrador."}]
        with patch("anka.web.paginas.rejected_drafts", return_value=rechazado),              patch("anka.web.paginas._en_modo_work", return_value=True):
            pagina = paginas.render_index()
        self.assertIn("Falta el análisis de un caso", pagina)
        self.assertLess(pagina.index('class="queue"'), pagina.index('id="borradores-rechazados"'))
        aviso = pagina[pagina.index('id="borradores-rechazados"'):]
        aviso = aviso[:aviso.index("</section>")]
        self.assertIn(">Completar la tabla yo<", aviso)
        visible = re.sub(r"<[^>]+>", "", aviso[:aviso.index("<details")])      # lo que se lee, sin los campos ocultos
        self.assertNotIn("JSON", visible)
        self.assertNotIn("r-9", visible)
        detalle = aviso[aviso.index("<details"):]
        self.assertIn("Ver más detalles", detalle)
        self.assertIn("Análisis r-9", detalle)
        self.assertIn("Hay bloques de datos repetidos o incompletos en el borrador.", detalle)


class TodoEstadoTieneAviso(Base):
    def aviso(self, estado: QuoteStatus) -> tuple[str, str]:
        self.crear(f"t-{estado}", estado=estado)
        pagina = pantalla_caso.render_case(f"t-{estado}")
        return pagina, (pagina[pagina.index('class="next-step'):].split("</div>\n</div>")[0] if 'class="next-step' in pagina else "")

    def test_cada_estado_trae_una_tarjeta_ahora_o_un_aviso_con_titulo_y_boton(self) -> None:
        # Cubre: U-31
        for estado in QuoteStatus:
            with self.subTest(estado=estado):
                pagina, aviso = self.aviso(estado)
                tarjeta_ahora = bool(re.search(r'<section class="section[^"]* ahora"', pagina))
                self.assertTrue(tarjeta_ahora or aviso, f"{estado} se queda sin siguiente paso")
                if aviso and not tarjeta_ahora:
                    self.assertRegex(aviso, r"<strong>\s*\S")
                    self.assertRegex(aviso, r'<a class="button[^"]*" href="[^"]+">')

    def test_los_estados_que_antes_quedaban_vacios_dicen_que_paso_y_que_hacer(self) -> None:
        # Cubre: U-31
        esperado = {QuoteStatus.IGNORED: ("Descartada", "/discarded"), QuoteStatus.MISSING: ("Ya no está en Gmail", "Volver a Solicitudes de cotización"),
                    QuoteStatus.SENDING: ("El correo no salió", "#respuesta"),
                    QuoteStatus.RESPONSE_SENT: ("Respuesta enviada", "template=libre#respuesta")}
        for estado, (titulo, destino) in esperado.items():
            with self.subTest(estado=estado):
                if estado == QuoteStatus.RESPONSE_SENT:                           # un acuse: se envió sin preguntas
                    analisis = complete_analysis(location="")
                    analisis["preguntas_enviadas"] = {"preguntas": []}
                    db = store.open_db(Path(self.temp.name))
                    store.save(db, f"t-{estado}", "m-1", estado, "cliente@empresa.co", analisis)
                    db.commit()
                    db.close()
                    pagina = pantalla_caso.render_case(f"t-{estado}")
                    aviso = pagina[pagina.index('class="next-step'):]
                else:
                    _, aviso = self.aviso(estado)
                self.assertIn(titulo, aviso)
                self.assertIn(destino, aviso)


def _contraste(a: str, b: str) -> float:
    def luminancia(hexa: str) -> float:
        r, g, b_ = (int(hexa.lstrip("#")[i:i + 2], 16) / 255 for i in (0, 2, 4))
        f = lambda c: c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4     # noqa: E731
        return 0.2126 * f(r) + 0.7152 * f(g) + 0.0722 * f(b_)
    claro, oscuro = sorted((luminancia(a), luminancia(b)), reverse=True)
    return (claro + 0.05) / (oscuro + 0.05)


class PaginaPrincipalEsLaLista(Base):
    def lista(self, **extra) -> str:
        from anka.web import paginas
        return paginas.render_index(**extra)

    def test_lista_todos_los_casos_con_un_boton_con_verbo_por_fila(self) -> None:
        # Cubre: U-32
        self.crear("t-a", estado=QuoteStatus.REVIEW, proyecto="Proyecto A")
        self.crear("t-b", estado=QuoteStatus.REVIEW_PENDING, proyecto="Proyecto B")
        self.crear("t-c", estado=QuoteStatus.IGNORED, proyecto="Proyecto C")
        separados = {frozenset(par) for par in (("t-a", "t-b"), ("t-a", "t-c"), ("t-b", "t-c"))}     # tres proyectos distintos
        with patch("anka.web.paginas.separated_project_pairs", return_value=separados):
            pagina = self.lista()
        self.assertIn('<h1 class="titulo-principal">Solicitudes de cotización</h1>', pagina)
        self.assertGreaterEqual(pagina.count('class="queue-row"'), 2)
        for hilo in ("t-a", "t-b"):
            self.assertIn(f"/case?thread_id={hilo}", pagina)
        self.assertNotIn("Qué hacer hoy", pagina)
        self.assertEqual(len(re.findall(r'<li class="queue-row"[^>]*>.*?</li>', pagina, re.S)),
                         len(re.findall(r'<li class="queue-row"[^>]*>.*?<a class="button[^"]*" href="[^"]+">\w', pagina, re.S)))

    def test_los_chips_traen_los_numeros_y_todos(self) -> None:
        # Cubre: U-32
        self.crear("t-a", estado=QuoteStatus.REVIEW)
        pagina = self.lista()
        chips = pagina[pagina.index("data-filtro-etapas"):pagina.index('id="casos"')]
        for etiqueta in ("Por atender", "Esperando al cliente", "Listas para la T", "Con código de oferta"):
            self.assertIn(etiqueta, chips)
        self.assertRegex(chips, r'data-etapa="" aria-pressed="false">Todos <b>1</b>')
        self.assertRegex(chips, r'value="atender" class="chip-filter" data-etapa="atender" aria-pressed="true">Por atender')

    def test_sin_casos_por_atender_entra_en_todos(self) -> None:
        # Cubre: U-32
        self.crear("t-a", estado=QuoteStatus.WAITING)
        from datetime import datetime
        db = store.open_db(Path(self.temp.name))
        db.execute("UPDATE quotes SET sent_at=?, draft_body='Hola' WHERE thread_id='t-a'",
                   (datetime.now().astimezone().isoformat(timespec="seconds"),))
        db.commit()
        db.close()
        self.assertRegex(self.lista(), r'data-etapa="" aria-pressed="true">Todos')      # sin nada que atender entra en «Todos»

    def test_el_orden_va_del_mas_urgente_al_menos(self) -> None:
        # Cubre: U-32
        self.crear("t-decidir", estado=QuoteStatus.REVIEW, proyecto="Proyecto A")
        self.crear("t-listo", estado=QuoteStatus.READY_REVIEW, proyecto="Proyecto B")
        with patch("anka.web.paginas.separated_project_pairs", return_value={frozenset(("t-decidir", "t-listo"))}):
            pagina = self.lista()
        self.assertLess(pagina.index("t-listo"), pagina.index("t-decidir"))        # emitir la T (3) va antes que decidir (4)

    def test_el_script_filtra_por_etapa_y_avisa_si_no_hay(self) -> None:
        # Cubre: U-32
        # Desde U-49 la etapa la filtra el servidor (chips que envían el formulario); el script solo afina lo escrito.
        for pieza in ("getElementById('sin-casos')", "fila.dataset.search"):
            self.assertIn(pieza, JS)
        self.assertIn(".queue-row[hidden]", (PLANTILLAS / "style.css").read_text(encoding="utf-8"))

    def test_buscar_o_exportar_y_el_aviso_de_work_siguen_en_la_pagina(self) -> None:
        # Cubre: U-32
        self.crear("t-a", estado=QuoteStatus.REVIEW)
        rechazado = [{"draft": "r-1", "thread": "t-a", "reason": "x"}]
        with patch("anka.web.paginas.rejected_drafts", return_value=rechazado),              patch("anka.web.paginas._en_modo_work", return_value=True):
            pagina = self.lista()
        self.assertNotIn("Buscar o exportar", pagina)
        self.assertIn("Falta el análisis de un caso", pagina)
        # Cubre: U-110 (en modo API el aviso de Work no aparece)
        with patch("anka.web.paginas.rejected_drafts", return_value=rechazado):
            self.assertNotIn("Falta el análisis de un caso", self.lista())


class UnaSolaListaEnElCamino(Base):
    def test_cada_fila_trae_lo_que_se_busca_y_el_script_lo_usa(self) -> None:
        # Cubre: U-32
        from anka.web import paginas
        self.crear("t-a", estado=QuoteStatus.REVIEW, proyecto="Parque Solar Llanos")
        pagina = paginas.render_index()
        fila = pagina[pagina.index('<li class="queue-row"'):]
        fila = fila[:fila.index(">")]
        self.assertIn('data-search="', fila)
        self.assertIn("parque solar llanos", fila)
        self.assertIn("cliente", fila)
        for pieza in ("getElementById('buscar-casos')", "fila.dataset.search", "buscador.addEventListener('input'"):
            self.assertIn(pieza, JS)

    def test_ajustes_lleva_a_administrar_casos_que_trae_el_regreso(self) -> None:
        # Cubre: U-32
        from anka.web import paginas
        self.crear("t-a", estado=QuoteStatus.REVIEW)
        with patch("anka.web.paginas.unread_thread_count", return_value=0):
            admin = paginas.render_requests()
        self.assertIn("<title>Administrar casos - Anka</title>", admin)
        self.assertIn('<a class="atras" href="/">← Solicitudes de cotización</a>', admin)
        self.assertIn("Exportar todo a Excel", admin)
        self.assertIn('href="/requests">Administrar casos (eliminar, exportar)</a>', admin)

    def test_descartadas_regresa_a_mis_casos(self) -> None:
        # Cubre: U-32
        from anka.web import paginas
        self.assertIn('<a class="button secondary" href="/">← Solicitudes de cotización</a>', paginas.render_discarded())


class MisCasos(Base):
    def pagina(self, titulo: str = "Solicitudes de cotización", cuenta: int = 2) -> str:
        from anka.web import paginas
        with patch("anka.web.paginas.unread_thread_count", return_value=cuenta):
            return paginas.page(titulo, "<p>x</p>")

    def test_no_hay_barra_de_pestanas_y_la_marca_es_la_entrada_con_el_contador(self) -> None:
        # Cubre: U-32
        html = self.pagina()
        self.assertNotIn("<nav", html[:html.index('class="espacio')])          # el encabezado no trae pestañas
        for viejo in (">Resumen<", ">Solicitudes<", ">Archivo<", "Resumen comercial"):
            self.assertNotIn(viejo, html[:html.index("<main")])
        marca = html[html.index('<a class="brand"'):html.index("</a>", html.index('<a class="brand"'))]
        self.assertIn('href="/"', marca)
        self.assertIn("2 con novedades", marca)
        self.assertIn("<span class='nav-count'>2</span>", marca)
        self.assertIn("<title>(2) Solicitudes de cotización - Anka</title>", html)
        self.assertIn("document.querySelector('.brand .nav-count')", JS)

    def test_lo_raro_queda_fuera_del_camino(self) -> None:
        # Cubre: U-32
        html = self.pagina()
        self.assertIn('<footer class="pie"><a href="/discarded">Correos que no eran solicitudes</a></footer>', html)
        ajustes = html[html.index('id="ajustes"'):html.index("</details>", html.index('id="ajustes"'))]
        self.assertIn('<a class="enlace-ajustes" href="/archive">Casos borrados</a>', ajustes)

    def test_las_direcciones_antiguas_siguen_existiendo(self) -> None:
        # Cubre: U-32
        for ruta in ("/", "/requests", "/discarded", "/archive"):
            self.assertIn(ruta, web.GET_ROUTES)

    def test_volver_lleva_a_mis_casos(self) -> None:
        # Cubre: U-32
        self.crear()
        self.assertIn('<a class="atras" href="/">← Solicitudes de cotización</a>', pantalla_caso.render_case("t-1"))
        self.crear("t-m", estado=QuoteStatus.MISSING)
        aviso = pantalla_caso.render_case("t-m")
        self.assertIn('href="/">Volver a Solicitudes de cotización</a>', aviso)


class CssOrdenado(Base):
    def css(self) -> str:
        return (PLANTILLAS / "style.css").read_text(encoding="utf-8")

    def test_el_borde_de_los_controles_pasa_3_a_1_en_los_dos_temas(self) -> None:
        # Cubre: U-31
        css = self.css()
        bloques = {"claro": css[css.index(":root {"):css.index("@media (prefers-color-scheme: dark)")],
                   "oscuro": css[css.index(':root[data-theme="dark"]'):css.index("* { box-sizing")]}
        for tema, bloque in bloques.items():
            def token(nombre: str, bloque: str = bloque) -> str:
                return re.search(rf"--{nombre}:\s*(#[0-9a-fA-F]{{6}})", bloque).group(1)      # type: ignore[union-attr]

            for fondo in ("panel", "input-bg"):
                with self.subTest(tema=tema, fondo=fondo):
                    self.assertGreaterEqual(_contraste(token("control-line"), token(fondo)), 3.0)
        self.assertGreaterEqual(css.count("--control-line:"), 3)
        self.assertIn("border: 1px solid var(--control-line)", css)

    def test_cada_selector_de_la_pantalla_se_define_una_vez(self) -> None:
        # Cubre: U-31
        css = self.css()
        for selector in (".next-step", ".paso-cabecera", ".paso-n", ".pregunta", ".armar-correo", ".process-step strong", ".toolbar"):
            with self.subTest(selector=selector):
                self.assertEqual(len(re.findall(rf"(?m)^{re.escape(selector)} \{{", css)), 1)

    def test_pantalla_angosta_y_movimiento_reducido(self) -> None:
        # Cubre: U-31
        css = self.css()
        self.assertIn('.tablist [role="tab"], button.enlace, .seg, .ayuda, .acts-head .button, summary { width: auto; }', css)
        self.assertIn(".act-table .act-main > td[data-etiqueta]::before { content: attr(data-etiqueta);", css)
        self.assertIn("@media (prefers-reduced-motion: reduce)", css)
        self.crear(actividades=[actividad("Planimetría", "confirmado")])
        pagina = pantalla_caso.render_case("t-1")
        for etiqueta in ("Actividad", "Servicio o técnica", "Cantidad y unidad", "Estado", "Notas"):
            self.assertIn(f'data-etiqueta="{etiqueta}"', pagina)


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
        layout = ((PLANTILLAS / "layout.html").read_text(encoding="utf-8") + (PLANTILLAS / "app.js").read_text(encoding="utf-8") + (PLANTILLAS / "tema.js").read_text(encoding="utf-8"))
        for pieza in ("dataset.enviando", "beforeunload", "cuerpo.defaultValue", "history.replaceState",
                      "searchParams.delete('message')", "getElementById('agregar-fila')", "abrirEditor(true)"):
            self.assertIn(pieza, layout)

    def test_el_mensaje_queda_a_la_vista_tras_saltar_a_un_ancla(self) -> None:
        # Cubre: U-31
        self.crear()
        pagina = pantalla_caso.render_case("t-1", message="Listo: guardado.")
        self.assertIn("<div class='notice flash' role=\"status\">Listo: guardado.</div>", pagina)
        self.assertIn(".flash { position: sticky;", (PLANTILLAS / "style.css").read_text(encoding="utf-8"))


class TablaAccesible(Base):
    def editor(self, cuantas: int = 3) -> str:
        self.crear(actividades=[actividad(f"Actividad {n}", "pendiente") for n in range(1, cuantas + 1)])
        pagina = pantalla_caso.render_case("t-1")
        return pagina[pagina.index('id="editar-tabla"'):]

    def test_cada_campo_dice_de_que_actividad_es_y_ninguno_se_repite(self) -> None:
        # Cubre: U-31
        editor = self.editor()
        nombres = re.findall(r"<(?:textarea|select|input)[^>]*aria-label=['\"]([^'\"]+)['\"]", editor)
        self.assertGreater(len(nombres), 20)
        self.assertEqual(len(nombres), len(set(nombres)), "hay campos con el mismo nombre accesible")
        for esperado in ("Notas de Actividad 2", "Estado de Actividad 3", "Actividad de Actividad 1", "Quitar Actividad 2",
                         "Notas de la fila nueva"):
            self.assertIn(esperado, nombres)

    def test_la_tabla_conserva_su_semantica(self) -> None:
        # Cubre: U-31
        editor = self.editor()
        tabla = editor[editor.index('class="act-table"'):editor.index("</table>", editor.index('class="act-table"'))]
        self.assertIn('<thead role="rowgroup">', tabla)
        self.assertEqual(tabla.count('<tbody class="act-row" role="rowgroup">'), len(re.findall(r"<tbody[ >]", tabla)))
        self.assertEqual(len(re.findall(r"<th[ >]", tabla)), tabla.count('<th role="columnheader"'))
        self.assertEqual(len(re.findall(r"<td[ >]", tabla)), tabla.count('<td role="cell"'))
        self.assertNotRegex(tabla, r"<td(?![^>]*role=)")

    def test_errores_como_alerta_foco_no_tapado_y_foco_al_mensaje(self) -> None:
        # Cubre: U-31
        caso = (PLANTILLAS / "case.html").read_text(encoding="utf-8")
        self.assertIn("<div class='notice error' role=\"alert\">", caso)
        css = (PLANTILLAS / "style.css").read_text(encoding="utf-8")
        self.assertIn("scroll-padding-top: calc(var(--header-h, 0px) + 12px)", css)
        layout = ((PLANTILLAS / "layout.html").read_text(encoding="utf-8") + (PLANTILLAS / "app.js").read_text(encoding="utf-8") + (PLANTILLAS / "tema.js").read_text(encoding="utf-8"))
        self.assertIn("mensaje.focus({preventScroll: true})", layout)


class CorreoPorRevisar(Base):
    def test_con_el_correo_sin_enviar_lista_insignia_y_nota_dicen_correo_por_revisar(self) -> None:
        # Cubre: U-31
        from anka.web import paginas
        for estado in (QuoteStatus.REVIEW_PENDING, QuoteStatus.DRAFT_CREATED):
            with self.subTest(estado=estado):
                self.crear(f"t-{estado}", estado=estado)
                caso = quotes.get_quote(f"t-{estado}")
                self.assertEqual(paginas.case_stage(caso), ("Correo por revisar", True))
                self.assertEqual(paginas.accion_de_caso(caso)[1:], ("Revisar correo", "#respuesta"))
                pagina = pantalla_caso.render_case(f"t-{estado}")
                cabecera = pagina[pagina.index('class="caso-cabecera"'):pagina.index('class="next-step')]
                self.assertEqual(cabecera.count("Correo por revisar"), 2)                    # insignia y nota del primer paso
                for viejo in ("Por aclarar con el cliente", "Borrador por revisar", "Borrador preparado"):
                    self.assertNotIn(viejo, cabecera)

    def test_la_lista_lo_muestra_con_el_verbo_revisar_correo(self) -> None:
        # Cubre: U-31
        from anka.web import paginas
        self.crear("t-a", estado=QuoteStatus.REVIEW_PENDING)
        pagina = paginas.render_index()
        fila = pagina[pagina.index('<li class="queue-row"'):pagina.index("</li>", pagina.index('<li class="queue-row"'))]
        self.assertIn("Correo por revisar", fila)
        self.assertIn(">Revisar correo<", fila)
        self.assertIn("#respuesta", fila)
        self.assertNotIn("Por aclarar con el cliente", pagina[:pagina.index("</main>")])

    def test_el_correo_por_revisar_va_antes_que_decidir_y_despues_de_nada_mas_urgente(self) -> None:
        # Cubre: U-31
        from anka.web import paginas
        self.crear("t-decidir", estado=QuoteStatus.REVIEW, proyecto="Proyecto A")
        self.crear("t-correo", estado=QuoteStatus.REVIEW_PENDING, proyecto="Proyecto B")
        with patch("anka.web.paginas.separated_project_pairs", return_value={frozenset(("t-decidir", "t-correo"))}):
            pagina = paginas.render_index()
        self.assertLess(pagina.index("t-correo"), pagina.index("t-decidir"))

    def test_work_sigue_pudiendo_decir_borrador_preparado(self) -> None:
        # Cubre: U-31
        from anka.dominio.status import canonical_work_status
        self.assertEqual(canonical_work_status("borrador preparado"), "Borrador preparado")


class AccionSiempreAlAlcance(Base):
    def test_la_linea_de_preguntas_y_los_botones_van_en_una_barra_pegada_abajo(self) -> None:
        # Cubre: U-31
        self.crear(estado=QuoteStatus.REVIEW_PENDING)
        pagina = pantalla_caso.render_case("t-1")
        barra = pagina[pagina.index('class="barra-accion"'):]
        barra = barra[:barra.index("</form>")]
        self.assertIn('id="aviso-espera"', barra)
        self.assertEqual(barra.count('formaction="/send"'), 1)
        self.assertEqual(barra.count('formaction="/draft"'), 1)
        self.assertLess(pagina.index('id="cuerpo-correo"'), pagina.index('class="barra-accion"'))   # la barra va debajo del mensaje

    def test_el_mensaje_se_ve_completo_y_la_barra_se_pega_al_borde(self) -> None:
        # Cubre: U-31
        css = (PLANTILLAS / "style.css").read_text(encoding="utf-8")
        self.assertIn("field-sizing: content", css[css.index(".correo-form textarea.caja-correo"):][:200])
        regla = css[css.index(".barra-accion {"):][:420]
        self.assertIn("position: sticky; bottom: 0;", regla)
        self.assertIn("env(safe-area-inset-bottom", regla)
        self.assertNotIn("max-height", css[css.index(".correo-form textarea.caja-correo"):][:200])      # R8: nada de texto escondido


class Pestanas(Base):
    def test_hay_cuatro_pestanas_con_sus_paneles(self) -> None:
        # Cubre: U-31
        self.crear()
        pagina = pantalla_caso.render_case("t-1")
        pestanas = _pestanas(pagina)
        for rotulo in ("Conversación", "Archivos (", "Historial", "Más datos"):
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
        layout = ((PLANTILLAS / "layout.html").read_text(encoding="utf-8") + (PLANTILLAS / "app.js").read_text(encoding="utf-8") + (PLANTILLAS / "tema.js").read_text(encoding="utf-8"))
        for pieza in ("data-tabs", "ArrowRight", "closest('[role=\"tabpanel\"]')", "hashchange"):
            self.assertIn(pieza, layout)


class PreguntaIgualAQueHaceFalta(Base):
    """U-107.6: lo que la tabla dice que falta es lo que se le pregunta al cliente."""

    def pregunta(self, nota: str, estado: str = "pendiente") -> str:
        from anka.web import views
        return views._pregunta_de_fila(actividad("Levantamiento de detalles", estado) | {"notes": nota, "quantity": "no indicado"}, estado)

    def test_la_pregunta_usa_las_palabras_de_la_columna_que_hace_falta(self) -> None:
        # Cubre: U-107
        self.assertEqual(self.pregunta("Falta la delimitación y superficie (área en m² o ha) de las manzanas y estaciones."),
                         "Levantamiento de detalles: la delimitación y superficie "
                         "(área en m² o ha) de las manzanas y estaciones.")

    def test_la_razon_interna_de_la_nota_no_llega_al_cliente(self) -> None:
        # Cubre: U-107
        self.assertEqual(self.pregunta("Falta la localización exacta del trazado al no contarse con el archivo KML/KMZ."),
                         "Levantamiento de detalles: la localización exacta del trazado.")

    def test_una_frase_cortada_o_sin_forma_de_pregunta_vuelve_a_los_datos_esenciales(self) -> None:
        # Cubre: U-107
        larga = "Falta " + "la superficie total del área de interés del proyecto " * 5
        self.assertEqual(self.pregunta(larga), "Levantamiento de detalles: la cantidad.")
        self.assertTrue(self.pregunta("El documento deja la opción abierta.").startswith("Levantamiento de detalles:"))

    def test_una_contradiccion_sigue_con_su_redaccion(self) -> None:
        # Cubre: U-107
        self.assertEqual(self.pregunta("Falta la fecha: dice miércoles y viernes.", "contradictorio"),
                         "Levantamiento de detalles: Falta la fecha: dice miércoles y viernes. ¿Cuál es el dato correcto?")


class PreguntasGenerales(Base):
    """U-107.7: lo que el análisis sugiere y no es de una actividad también tiene casilla."""

    def test_las_sugeridas_sin_actividad_salen_como_generales_marcadas(self) -> None:
        # Cubre: U-107
        self.crear(actividades=[actividad("Planimetría", "pendiente") | {"notes": "Falta la ubicación."}],
                   preguntas=["¿Podrían suministrar el Anexo Capítulo 1?"])
        pagina = pantalla_caso.render_case("t-1")
        grupo = pagina[pagina.index('id="preguntas-generales"'):]
        grupo = grupo[:grupo.index("</ul>")]
        self.assertIn("Generales", grupo)
        self.assertIn('name="preguntar_fila" value="¿Podrían suministrar el Anexo Capítulo 1?" form="decision-form" checked', grupo)
        cuadro = cuadro_de(pagina)
        self.assertIn("¿Podrían suministrar el Anexo Capítulo 1?", cuadro)                  # y la caja ya la trae

    def test_una_general_repetida_de_una_fila_o_ya_pedida_no_se_lista_dos_veces(self) -> None:
        # Cubre: U-107
        from anka.web import views
        fila = "Para «A», ¿podría indicar la ubicación?"
        candidatas = [{"texto": "¿Podría indicar la ubicación?", "elegida": "ask_client"},
                      {"texto": "Para «A», ¿podría indicar la ubicación?", "elegida": "ask_client"},
                      {"texto": "¿Cuál es el plazo?", "elegida": "ask_client", "ya_pedida": "2026-10-01"},
                      {"texto": "Resolver el datum internamente", "elegida": "internal"},
                      {"texto": "¿Cuál es el datum?", "elegida": "ask_client"}, {"texto": "¿Cuál es el datum?", "elegida": "ask_client"}]
        generales = views.preguntas_generales(candidatas, {0: (fila, True)}, "¿Cuál es el datum?")
        self.assertEqual([g["pregunta"] for g in generales], ["¿Podría indicar la ubicación?", "¿Cuál es el datum?"])
        self.assertEqual([g["marcada"] for g in generales], [False, True])      # solo está marcada la que está en el cuadro

    def test_las_del_modelo_llevan_etiqueta_y_las_de_reglas_no(self) -> None:
        # Cubre: U-107
        from anka.web import views
        candidatas = [{"texto": "¿Datum?", "elegida": "ask_client", "origen": "análisis"},
                      {"texto": "¿Cuál es el plazo?", "elegida": "ask_client", "origen": "recepción"},
                      {"texto": "¿Qué escala?", "elegida": "ask_client", "origen": "auditoría"}]
        generales = views.preguntas_generales(candidatas, {}, "")
        self.assertEqual([g["del_modelo"] for g in generales], [True, False, True])

    def test_la_etiqueta_se_ve_solo_en_las_del_modelo(self) -> None:
        # Cubre: U-107
        self.crear(actividades=[actividad("Planimetría", "pendiente") | {"notes": "Falta la ubicación."}],
                   preguntas=["¿Podrían suministrar el Anexo Capítulo 1?"])
        pagina = pantalla_caso.render_case("t-1")
        grupo = pagina[pagina.index('id="preguntas-generales"'):]
        grupo = grupo[:grupo.index("</ul>")]
        self.assertEqual(grupo.count("sugerida por el análisis"), grupo.count('type="checkbox"'))

    def test_sin_preguntas_sugeridas_no_hay_grupo_generales(self) -> None:
        # Cubre: U-107
        self.crear(actividades=[actividad("Planimetría", "pendiente") | {"notes": "Falta la ubicación."}], preguntas=[])
        self.assertNotIn('id="preguntas-generales"', pantalla_caso.render_case("t-1"))


class PantallaOrdenada(Base):
    """U-107: lo que hay que hacer primero, el detalle debajo, sin repetir."""

    def test_el_correo_del_cliente_sale_una_vez_en_la_cabecera(self) -> None:
        # Cubre: U-107
        self.crear()
        db = store.open_db(Path(self.temp.name))
        db.execute("INSERT OR REPLACE INTO quote_metadata (thread_id, client_name, client_type, sector, notes, updated_at)"
                   " VALUES ('t-1', 'cliente@empresa.co', 'No clasificado', 'No clasificado', '', '2026-10-01T09:00:00-05:00')")
        db.commit()
        db.close()
        cabecera = pantalla_caso.render_case("t-1")
        cabecera = cabecera[cabecera.index('class="caso-cabecera"'):cabecera.index("</h1>") + 400]
        self.assertEqual(cabecera.count("cliente@empresa.co"), 1)

    def test_la_decision_va_antes_de_la_tabla_y_el_paso_n_de_4_ya_no_se_dice(self) -> None:
        # Cubre: U-107
        self.crear(actividades=[actividad("Planimetría", "pendiente") | {"notes": "Falta la ubicación."}])
        pagina = pantalla_caso.render_case("t-1")
        self.assertLess(pagina.index('class="process-step'), pagina.index('class="dos-columnas"'))
        self.assertLess(pagina.index('class="dos-columnas"'), pagina.index('role="tablist"'))
        columnas = pagina[pagina.index('class="dos-columnas"'):pagina.index('role="tablist"')]
        self.assertLess(columnas.index('id="alcance"'), columnas.index('id="decision"'))   # tabla a la izquierda, mensaje a la derecha
        self.assertIn("<h2>Mensaje para el cliente</h2>", columnas)
        self.assertNotIn("Revisa y decide cómo seguir", pagina)
        self.assertNotRegex(pagina, r"Paso \d de 4")

    def test_las_dos_tarjetas_miden_lo_mismo_y_lo_que_no_cabe_se_desplaza_por_dentro(self) -> None:
        # Cubre: U-107
        css = (PLANTILLAS / "style.css").read_text(encoding="utf-8")
        css = css[css.index("/* U-107: con la decisión pendiente"):]
        self.assertIn("grid-template-columns: minmax(0, 1fr) minmax(0, 1fr)", css)          # mismo ancho
        self.assertIn("height: clamp(440px, calc(100vh - 330px), 560px)", css)             # mismo alto, no depende del contenido
        self.assertIn("align-items: stretch", css)
        self.assertRegex(css, r"#alcance \.alcance-lectura \{[^}]*overflow-y: auto")        # la lista se desplaza por dentro
        self.assertRegex(css, r"\.chat-caja textarea \{[^}]*overflow-y: auto")              # y la caja de mensaje también
        self.assertIn('.dos-columnas:has(#editar-boton[aria-expanded="true"])', css)       # el editor abierto necesita ancho
        self.assertRegex(css, r"@media \(max-width: 860px\) \{ \.dos-columnas \{ grid-template-columns: minmax\(0, 1fr\); height: auto")

    def test_la_caja_del_correo_es_un_solo_campo_con_sus_acciones_dentro(self) -> None:
        # Cubre: U-107, U-113
        self.crear(actividades=[actividad("Planimetría", "pendiente") | {"notes": "Falta la ubicación."}])
        pagina = pantalla_caso.render_case("t-1")
        caja = pagina[pagina.index('<textarea name="body" id="correo-unico"'):]
        caja = caja[:caja.index("</form>")]
        self.assertEqual(caja.count("<textarea"), 1)
        self.assertLess(caja.index("<textarea"), caja.index('id="btn-enviar-preguntas"'))     # los botones van debajo del correo
        self.assertLess(caja.index('id="btn-enviar-preguntas"'), caja.index('id="btn-suficiente"'))   # «Con esto alcanza» al final
        self.assertIn('class="enlace-suave"', caja)
        self.assertIn("Nada sale al cliente sin que lo revises y confirmes", pagina)           # R8 sigue visible

    def test_cada_renglon_dice_su_estado_aunque_todos_sean_iguales(self) -> None:
        # Cubre: U-107, U-112
        self.crear(actividades=[actividad("A", "pendiente"), actividad("B", "pendiente")])
        pagina = pantalla_caso.render_case("t-1")
        self.assertEqual(pagina.count('<span class="chip pendiente">Falta un dato</span>'), 2)
        self.assertNotIn("data-estados", pagina)


if __name__ == "__main__":
    unittest.main()
