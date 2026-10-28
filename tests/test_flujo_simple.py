"""Flujo simple (U-40, D-105): una acción principal por estado; lo demás, enlace o menú plegado."""

from tests import _isolate  # noqa: F401  (debe ir primero)

import unittest
from dataclasses import replace
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch

from anka.dominio.status import QuoteStatus
from anka.infra import store
from anka.servicios import change_control, quotes
from anka.web import paginas, pantalla_caso
from anka.web import server as web
from anka.web.views import render_page
from tests.test_app import complete_analysis
from tests.test_formularios_pendientes import Base as BaseConPendientes

PLANTILLAS = Path(web.__file__).with_name("templates")
RAIZ = Path(web.__file__).parents[2]


class Base(BaseConPendientes):
    def crear_caso(self, estado: QuoteStatus = QuoteStatus.REVIEW_PENDING, analisis: dict | None = None,
                   mensajes: tuple[tuple[str, str], ...] = (), thread_id: str = "t-1", **extra: object) -> None:
        datos = complete_analysis(location="")
        datos["other_questions"] = ["¿Cuál es el datum del levantamiento?"]
        datos.update(analisis or {})
        db = store.open_db(Path(self.temp.name))
        store.save(db, thread_id, "m-1", estado, "cliente@empresa.co", datos)
        store.add_message(db, thread_id, "m-1", "received", "cliente@empresa.co", "Cotización",
                          "Hola, necesitamos un levantamiento.", "2026-10-01T09:00:00+00:00")
        for numero, (direccion, cuando) in enumerate(mensajes, 2):
            store.add_message(db, thread_id, f"m-{numero}", direccion, "x@y.co", "Re: Cotización", "texto", cuando)
        for columna, valor in extra.items():
            db.execute(f"UPDATE quotes SET {columna}=? WHERE thread_id=?", (valor, thread_id))
        db.commit()
        db.close()


class CorreoPorRevisar(Base):
    def test_las_salidas_secundarias_van_en_un_solo_menu_plegado(self) -> None:
        # Cubre: U-40
        self.crear_caso(QuoteStatus.REVIEW)
        quotes.resolve_technical_review("t-1", "ask_client", "¿Cuál es el datum?")
        pagina = pantalla_caso.render_case("t-1")
        formulario = pagina[pagina.index('class="correo-form"'):pagina.index('id="cambiar-idea"')]
        self.assertIn(">Enviar al cliente</button>", formulario)
        self.assertIn(">Guardar borrador en Gmail</button>", formulario)
        for texto in ("Cambiar las preguntas", "Volver a decidir", "No preguntar: con esto alcanza"):
            self.assertNotIn(texto, formulario)
        menu = pagina[pagina.index('id="cambiar-idea"'):]
        self.assertTrue(menu.startswith('id="cambiar-idea">'))                       # plegado con el correo ya armado
        self.assertIn("<summary>Cambiar de idea</summary>", menu)
        for texto in ("Cambiar las preguntas", "Preparar el correo de nuevo", "Volver a decidir",
                      "No preguntar: con esto alcanza"):
            self.assertIn(texto, menu)
        self.assertEqual(pagina.count("<summary>Cambiar de idea</summary>"), 1)

    def test_la_casilla_de_esperar_solo_se_ofrece_si_el_correo_no_pregunta(self) -> None:
        # Cubre: U-40
        self.crear_caso(QuoteStatus.REVIEW)
        quotes.resolve_technical_review("t-1", "ask_client", "¿Cuál es el datum?")
        con_preguntas = pantalla_caso.render_case("t-1")
        self.assertIn('<div id="caja-esperar" hidden>', con_preguntas)
        sin_preguntas = pantalla_caso.render_case("t-1", template="libre")
        self.assertIn('<div id="caja-esperar">', sin_preguntas)
        self.assertIn("el caso pasará a «Esperando al cliente»", sin_preguntas)       # dice qué hace
        self.assertIn('aria-describedby="efecto-esperar"', sin_preguntas)


class EsperandoAlCliente(Base):
    def test_esperar_no_es_una_tarea_y_muestra_lo_que_se_pregunto(self) -> None:
        # Cubre: U-40
        ayer = (datetime.now() - timedelta(days=1)).isoformat()
        enviadas = {"en": ayer, "preguntas": ["¿Datum?", "¿Plazo?"], "esperada": True}
        self.crear_caso(QuoteStatus.WAITING, {"preguntas_enviadas": enviadas}, (("sent", ayer),), sent_at=ayer)
        aviso = str(pantalla_caso._next_step_banner(quotes.get_quote("t-1"), False))
        self.assertIn("Nada que hacer", aviso)
        self.assertIn("3 días de trabajo", aviso)
        self.assertIn("7 para llamarlo", aviso)
        self.assertNotIn('class="button"', aviso)                  # sin botón principal
        self.assertIn('class="enlace-accion" href="#seguimiento"', aviso)
        self.assertIn("Lo que le preguntamos (2)", aviso)
        self.assertIn("<li>¿Datum?</li>", aviso)

    def test_llamar_trae_ya_hable_en_la_misma_tarjeta(self) -> None:
        # Cubre: U-40
        hace_un_mes = (datetime.now() - timedelta(days=30)).isoformat()
        self.crear_caso(QuoteStatus.WAITING, {}, (("sent", hace_un_mes),), sent_at=hace_un_mes)
        aviso = str(pantalla_caso._next_step_banner(quotes.get_quote("t-1"), False))
        self.assertIn("Llamar al cliente", aviso)
        self.assertIn('action="/followup"', aviso)
        self.assertIn('name="action" value="contacted"', aviso)
        self.assertIn("Ya hablé con el cliente", aviso)


class SegundaRonda(Base):
    ENVIADAS = {"en": "2026-10-02T10:00:00+00:00", "preguntas": ["¿Datum?"], "esperada": True}

    def test_el_cliente_respondio_se_distingue_de_la_primera_ronda(self) -> None:
        # Cubre: U-40
        self.crear_caso(QuoteStatus.REVIEW, {"preguntas_enviadas": self.ENVIADAS},
                        (("sent", "2026-10-02T10:00:00+00:00"), ("received", "2026-10-03T09:00:00+00:00")))
        pagina = pantalla_caso.render_case("t-1")
        self.assertIn("El cliente respondió (ronda 2): revisa lo que faltaba.", pagina)
        self.assertIn("<li>¿Datum?</li>", pagina)
        self.assertIn("· ronda 2", pagina)                                        # sub-línea del paso 1
        self.assertLess(pagina.index('id="ronda-aviso"'), pagina.index("Actividades detectadas"))

    def test_la_primera_ronda_no_lleva_aviso_de_respuesta(self) -> None:
        # Cubre: U-40
        self.crear_caso(QuoteStatus.REVIEW)
        pagina = pantalla_caso.render_case("t-1")
        self.assertNotIn('id="ronda-aviso"', pagina)
        self.assertIn("· ronda 1", pagina)

    def test_enviar_sin_respuesta_no_es_segunda_ronda(self) -> None:
        # Cubre: U-40
        self.crear_caso(QuoteStatus.REVIEW, {"preguntas_enviadas": self.ENVIADAS},
                        (("sent", "2026-10-02T10:00:00+00:00"),))
        self.assertNotIn('id="ronda-aviso"', pantalla_caso.render_case("t-1"))


class EmitirLaT(Base):
    def _pagina(self, analisis: dict | None = None) -> str:
        self.crear_caso(QuoteStatus.READY_REVIEW, analisis)
        return pantalla_caso.render_case("t-1")

    def test_dice_lo_que_va_a_pasar_antes_del_boton(self) -> None:
        # Cubre: U-40
        panel = self._pagina()
        panel = panel[panel.index('id="emitir-t"'):]
        lista = panel[panel.index('<ol class="que-pasa">'):panel.index("</ol>")]
        self.assertEqual(lista.count("<li>"), 3)
        self.assertIn("carpeta en Drive", lista)
        self.assertIn("cliente@empresa.co", lista)
        self.assertLess(panel.index('<ol class="que-pasa">'), panel.index("Emitir la T, crear la carpeta"))
        self.assertIn("(obligatorio)", panel)

    def test_el_correo_va_plegado_y_solo_emitir_es_visible(self) -> None:
        # Cubre: U-40
        panel = self._pagina()
        panel = panel[panel.index('id="emitir-t"'):]
        plegable = panel[panel.index('<details class="more correo-de-la-t">'):panel.index("</details>")]
        self.assertIn("Ver o editar el correo", plegable)
        self.assertIn("<textarea", plegable)
        self.assertGreater(panel.index("Solo emitir la T"), panel.index("</details>"))   # fuera de lo plegado
        self.assertNotIn("Otras opciones", panel)

    def test_corregir_el_cliente_vive_en_el_panel_y_la_carpeta_en_mas_datos(self) -> None:
        # Cubre: U-40
        pagina = self._pagina()
        panel = pagina[pagina.index('id="emitir-t"'):pagina.index("</section>", pagina.index('id="emitir-t"'))]
        self.assertIn("Corregir el cliente", panel)
        self.assertIn('action="/metadata"', panel)
        mas_datos = pagina[pagina.index('id="p-det"'):]
        self.assertNotIn("<h2>Información comercial</h2>", mas_datos)        # el formulario no se repite
        self.assertIn("Si la carpeta falló: crearla a mano", mas_datos)
        self.assertIn('action="/approve"', mas_datos)
        self.assertNotIn('action="/approve"', pagina[:pagina.index('id="p-det"')])   # sale del paso de emitir

    def test_si_la_carpeta_fallo_el_aviso_sigue_a_la_vista(self) -> None:
        # Cubre: U-40
        pagina = self._pagina({"drive": {"pending": "No se pudo crear la carpeta."}})
        self.assertIn('<section class="section" id="carpeta">', pagina)       # visible, no plegada
        self.assertNotIn("Si la carpeta falló: crearla a mano", pagina)

    def test_sin_el_panel_el_formulario_comercial_queda_en_mas_datos(self) -> None:
        # Cubre: U-40
        self.crear_caso(QuoteStatus.REVIEW)
        pagina = pantalla_caso.render_case("t-1")
        self.assertIn("<h2>Información comercial</h2>", pagina[pagina.index('id="p-det"'):])


class TablaCompacta(Base):
    def test_el_resumen_marca_lo_que_falta_y_hay_un_solo_vocabulario(self) -> None:
        # Cubre: U-40
        pendiente = complete_analysis(location="")["activities"][0] | {"entregable": "", "estado": "pendiente"}
        self.crear_caso(analisis={"activities": [pendiente]})
        pagina = pantalla_caso.render_case("t-1")
        resumen = pagina[pagina.index('id="tabla-resumen"'):pagina.index('id="editar-tabla"')]
        self.assertIn("Falta ubicación, entregable, plazo", resumen)
        self.assertIn(">Corregir la tabla</button>", pagina)
        self.assertNotIn("Ajustar cajas al texto", pagina)
        editor = pagina[pagina.index('id="editar-tabla"'):]
        self.assertIn("<option value='pendiente' selected>Por completar</option>", editor)   # etiqueta, mismo valor
        self.assertIn("<option value='confirmado'>Completa</option>", editor)
        self.assertNotIn("<strong>confirmado</strong>", editor)

    def test_lo_confirmado_no_dice_que_falta_algo(self) -> None:
        # Cubre: U-40
        completa = complete_analysis()["activities"][0] | {"plazo_restriccion": "30 días"}
        self.crear_caso(analisis={"activities": [completa]})
        self.assertNotIn('class="faltan"', pantalla_caso.render_case("t-1"))

    def test_el_motivo_sigue_apareciendo_solo_con_cambios(self) -> None:
        # Cubre: U-40
        layout = ((PLANTILLAS / "layout.html").read_text(encoding="utf-8") + (PLANTILLAS / "app.js").read_text(encoding="utf-8") + (PLANTILLAS / "tema.js").read_text(encoding="utf-8"))
        self.assertIn("motivo.hidden = !hay;", layout)


class SeccionesVacias(Base):
    def test_lo_que_no_tiene_contenido_no_se_dibuja(self) -> None:
        # Cubre: U-40
        self.crear_caso(QuoteStatus.REVIEW)
        with patch("anka.web.pantalla_caso.internal_commercial_context", return_value={}):
            pagina = pantalla_caso.render_case("t-1")
        mas_datos = pagina[pagina.index('id="p-det"'):]
        self.assertNotIn("Lo que sabemos del cliente", mas_datos)
        self.assertNotIn("Revisión de las preguntas", mas_datos)
        self.assertNotIn('<h2 class="subhead">Pendientes</h2>', mas_datos)
        self.assertIn("De dónde vino el correo", mas_datos)                 # lo que siempre sirve, se queda

    def test_con_contenido_vuelven_a_aparecer(self) -> None:
        # Cubre: U-40
        self.crear_caso(QuoteStatus.REVIEW, {"question_audit": [{"question": "¿Datum?", "disposition": "ask_client"}]})
        contexto = {"client_profiles": 1, "project_types": [], "sectors": [], "usual_requests": []}
        with patch("anka.web.pantalla_caso.internal_commercial_context", return_value=contexto):
            pagina = pantalla_caso.render_case("t-1")
        mas_datos = pagina[pagina.index('id="p-det"'):]
        self.assertIn("Lo que sabemos del cliente", mas_datos)
        self.assertIn("Revisión de las preguntas", mas_datos)


class Callejones(Base):
    def test_descartada_se_puede_recuperar_desde_el_aviso(self) -> None:
        # Cubre: U-40
        self.crear_caso(QuoteStatus.IGNORED)
        aviso = str(pantalla_caso._next_step_banner(quotes.get_quote("t-1"), False))
        self.assertIn('action="/rescue"', aviso)
        self.assertIn("Sí era una solicitud: analizarla", aviso)

    def test_envio_interrumpido_ofrece_abrir_gmail(self) -> None:
        # Cubre: U-40
        self.crear_caso(QuoteStatus.SENDING)
        aviso = str(pantalla_caso._next_step_banner(quotes.get_quote("t-1"), False))
        self.assertIn('href="https://mail.google.com/mail/u/0/#all/t-1"', aviso)
        self.assertIn('rel="noopener noreferrer"', aviso)

    def test_el_caso_perdido_se_puede_borrar_pero_pidiendo_confirmacion(self) -> None:
        # Cubre: U-40
        self.crear_caso(QuoteStatus.MISSING)
        aviso = str(pantalla_caso._next_step_banner(quotes.get_quote("t-1"), False))
        self.assertIn('action="/delete"', aviso)
        self.assertIn("Borrar este caso", aviso)
        self.assertIn("data-confirm=", aviso)
        self.assertIn("Volver a Mis casos", aviso)

    def test_un_plano_que_no_se_pudo_leer_dice_que_hacer(self) -> None:
        # Cubre: U-40
        self.crear_caso(QuoteStatus.REVIEW, {"attachments": [{"filename": "plano.dwg", "status": "no_legible"}]})
        pagina = pantalla_caso.render_case("t-1")
        self.assertIn("No pudimos leer plano.dwg: ábrelo tú o pídele al cliente un PDF.", pagina)
        self.assertIn("No se pudo leer: ábrelo tú o pídele al cliente un PDF", pagina)

    def test_la_carpeta_creada_dice_que_sigue(self) -> None:
        # Cubre: U-40
        self.crear_caso(QuoteStatus.READY_REVIEW)
        change_control.assign_code("t-1", "26145")
        db = store.open_db(Path(self.temp.name))
        db.execute("UPDATE quotes SET folder_id='f1', status='ready' WHERE thread_id='t-1'")
        db.commit()
        db.close()
        aviso = str(pantalla_caso._next_step_banner(quotes.get_quote("t-1"), False))
        self.assertIn("Siguiente paso: preparar la cotización con precios fuera de esta herramienta.", aviso)
        self.assertIn("Abrir carpeta en Drive", aviso)


class UnaSolaPrioridad(Base):
    def _con_cambios(self):
        self.crear_caso(QuoteStatus.REVIEW)
        caso = quotes.get_quote("t-1")
        base = {"id": 1, "has_code": True, "is_contact": True, "same_company": True, "subject": "Cambio",
                "snippet": "otro plazo", "sender_name": "Ana", "sender_email": "ana@empresa.co",
                "contact_notified_at": "", "reply_sent_at": ""}
        return replace(caso, changes=[base | {"state": "pending"}, base | {"id": 2, "state": "change"}])

    def test_una_tarjeta_principal_y_los_demas_plegados(self) -> None:
        # Cubre: U-40
        principal, mas = pantalla_caso._avisos(self._con_cambios(), False)
        self.assertIn("escribió después de crear la T", str(principal))
        self.assertIn("y además: 2 avisos</summary>", str(mas))
        self.assertIn('class="more y-ademas"', str(mas))
        self.assertEqual(str(principal).count("next-step"), 1)

    def test_sin_avisos_extra_no_hay_linea(self) -> None:
        # Cubre: U-40
        self.crear_caso(QuoteStatus.REVIEW)
        principal, mas = pantalla_caso._avisos(quotes.get_quote("t-1"), False)
        self.assertIn("Falta tu decisión", str(principal))
        self.assertEqual(str(mas), "")

    def test_con_la_tarjeta_ahora_el_aviso_del_estado_no_se_repite(self) -> None:
        # Cubre: U-40
        _, mas = pantalla_caso._avisos(self._con_cambios(), False, oculto=True)
        self.assertNotIn("Falta tu decisión", str(mas))

    def test_el_orden_de_prioridades_esta_documentado(self) -> None:
        # Cubre: U-40
        ux = (RAIZ / "spec" / "04-ux.md").read_text(encoding="utf-8")
        self.assertIn("Orden de los avisos del caso", ux)


class AjustesYNovedades(Base):
    def test_tema_avisos_y_motor_viven_dentro_de_ajustes(self) -> None:
        # Cubre: U-40
        with patch("anka.web.paginas.load_config", return_value={"active_mode": "chatgpt_work"}), \
                patch("anka.web.paginas.unread_notifications", return_value=[]):
            html = paginas.page("Prueba", "<p>x</p>")
        cabecera = html[html.index("<header>"):html.index('<details class="settings"')]
        self.assertNotIn('id="theme-toggle"', cabecera)
        self.assertNotIn('id="avisos-navegador"', cabecera)
        ajustes = html[html.index('<details class="settings"'):html.index("</header>")]
        self.assertIn('id="theme-toggle"', ajustes)
        self.assertIn('id="avisos-navegador"', ajustes)
        avanzado = ajustes[ajustes.index('<details class="more ajustes-avanzados">'):]
        self.assertIn("Ajustes avanzados (administración)", avanzado)
        self.assertIn("Solo para quien administra la herramienta", avanzado)
        self.assertIn("Ahora los correos los analiza ChatGPT Work.", avanzado)
        self.assertIn('<label for="mode">Quién analiza los correos</label>', avanzado)
        self.assertNotIn(">Motor<", html)

    def test_el_modo_activo_se_dice_en_llano(self) -> None:
        # Cubre: U-40
        for modo, texto in (("local", "Ollama"), ("api", "API de OpenAI"), ("chatgpt_work", "ChatGPT Work")):
            self.assertIn(texto, render_page("x", "", mode=modo))
        self.assertIn("Todavía no se eligió", render_page("x", "", mode=None))

    def test_las_novedades_de_un_caso_son_una_sola_fila(self) -> None:
        # Cubre: U-40
        base = {"project_name": "Norte", "thread_id": "t-1", "created_at": "2026-10-02T10:00:00"}
        panel = str(paginas._notifications_panel([
            base | {"title": "Respuesta sugerida lista", "message": "a"},
            base | {"title": "Correo nuevo recibido", "message": "b"},
            base | {"title": "Otro", "message": "c", "thread_id": "t-2"}], plegar=True))
        self.assertEqual(panel.count('<article class="notification">'), 2)
        self.assertIn("También en este caso: Correo nuevo recibido", panel)
        self.assertIn('<details class="more"><summary>Novedades (2)', panel)

    def test_un_campo_obligatorio_dentro_de_algo_plegado_lo_abre(self) -> None:
        # Cubre: U-40
        layout = ((PLANTILLAS / "layout.html").read_text(encoding="utf-8") + (PLANTILLAS / "app.js").read_text(encoding="utf-8") + (PLANTILLAS / "tema.js").read_text(encoding="utf-8"))
        self.assertIn("addEventListener('invalid'", layout)


if __name__ == "__main__":
    unittest.main()
