"""Pantalla del caso más clara: un camino para las preguntas, confirmaciones propias y accesibilidad. Cubre: U-28"""

from tests import _isolate  # noqa: F401  (debe ir primero)

import re
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from anka.dominio import preguntas as dominio
from anka.dominio.status import QuoteStatus
from anka.infra import settings, store
from anka.servicios import quotes
from anka.web import pantalla_caso, paginas
from tests.test_app import complete_analysis, test_config

PLANTILLAS = Path(__file__).resolve().parents[1] / "anka" / "web" / "templates"
PUNTO = "¿Cuál es el datum del levantamiento?"


class Base(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        for parche in (patch.object(settings, "DATA_DIR", Path(self.temp.name)),
                       patch("anka.web.paginas.load_config", return_value=test_config()),
                       patch("anka.web.pantalla_caso.load_config", return_value=test_config())):
            parche.start()
            self.addCleanup(parche.stop)
        analisis = complete_analysis(location="")
        analisis["other_questions"] = [PUNTO]
        analisis["contradictions"] = [{"topic": "área", "details": "10 ha frente a 100 ha", "sources": [],
                                       "status": "pendiente"}]
        db = store.open_db(Path(self.temp.name))
        store.save(db, "t-1", "m-1", QuoteStatus.REVIEW, "cliente@empresa.co", analisis)
        store.add_message(db, "t-1", "m-1", "received", "cliente@empresa.co", "Cotización levantamiento",
                          "Hola, necesitamos un levantamiento.", "2026-10-01T09:00:00-05:00")
        db.commit()
        db.close()

    def preguntar(self) -> None:
        candidatas = dominio.con_decisiones(dominio.candidatas(quotes.get_quote("t-1")["analysis"], {}), None)
        elecciones = {c["id"]: ("ask_client" if c["texto"] == PUNTO else dominio.INTERNA, c["texto"]) for c in candidatas}
        quotes.resolve_technical_review("t-1", "ask_client", elecciones=elecciones)


class UnSoloCamino(Base):
    def test_la_decision_va_justo_debajo_del_aviso_de_siguiente_paso(self) -> None:
        # Cubre: U-28
        pagina = pantalla_caso.render_case("t-1")
        decision = pagina.index('id="decision"')
        self.assertLess(pagina.index('class="next-step'), decision)
        self.assertLess(decision, pagina.index("Conversación con el cliente"))
        self.assertLess(decision, pagina.index('id="attachments-review"'))
        self.assertIn('href="#attachments-review">Ver los archivos y lo que falta resolver', pagina)

    def test_las_preguntas_elegidas_llegan_al_campo_del_correo_y_este_queda_plegado(self) -> None:
        # Cubre: U-28
        self.preguntar()
        pagina = pantalla_caso.render_case("t-1")
        campo = pagina.split('name="preguntas"')[1].split("</textarea>")[0]
        self.assertIn(PUNTO, campo)                                       # la misma lista, no otra
        self.assertIn("Son las que elegiste para el cliente", pagina)
        plegable = pagina[pagina.index('id="armar"') - 60:pagina.index('id="armar"') + 40]
        self.assertNotIn(" open", plegable)                                # el correo ya tiene texto: plegado
        self.assertIn("Cambiar las preguntas del correo", pagina)
        self.assertIn('id="armar-correo"', pagina)                        # el script pide confirmar si se editó

    def test_sin_texto_en_el_correo_el_campo_queda_abierto_y_sin_decision_no_se_rellena(self) -> None:
        # Cubre: U-28
        db = store.open_db(Path(self.temp.name))
        store.save(db, "t-2", "m-2", QuoteStatus.READY_REVIEW, "cliente@empresa.co", complete_analysis())
        db.commit()
        db.close()
        pagina = pantalla_caso.render_case("t-2", template="libre")
        self.assertIn('id="armar" open', pagina)
        self.assertEqual(pagina.split('name="preguntas"')[1].split("</textarea>")[0].split(">", 1)[1], "")

    def test_rearmar_tras_editar_pide_confirmar(self) -> None:
        # Cubre: U-28
        layout = (PLANTILLAS / "layout.html").read_text(encoding="utf-8")
        self.assertIn("getElementById('armar-correo')", layout)
        self.assertIn("se reemplazará lo que escribiste en el cuadro", layout)


class BorradorPrimeroYEnvioAparte(Base):
    def test_guardar_es_primario_y_enviar_va_aparte_con_su_confirmacion_y_su_resumen(self) -> None:
        # Cubre: U-28, R8
        self.preguntar()
        pagina = pantalla_caso.render_case("t-1")
        borrador = re.search(r'<button type="submit" formaction="/draft"[^>]*>', pagina).group(0)
        self.assertNotIn("class=", borrador)                              # el botón primario
        zona = pagina[pagina.index('class="send-zone"'):]
        enviar = re.search(r'<button type="submit" formaction="/send"[^>]*>', zona, re.S).group(0)
        self.assertIn('class="send-action"', enviar)
        self.assertNotIn("danger", enviar)                                # enviar no es destructivo
        self.assertIn("data-confirm=", enviar)                            # y sigue pidiendo confirmación (R8)
        self.assertIn('data-resumen="envio"', enviar)
        self.assertIn('data-para="cliente@empresa.co"', enviar)
        self.assertIn('data-asunto="Re: Cotización levantamiento"', enviar)
        self.assertIn("Es definitivo", zona)
        self.assertLess(pagina.index('formaction="/draft"'), pagina.index('class="send-zone"'))


class ConfirmacionPropia(unittest.TestCase):
    def setUp(self) -> None:
        self.layout = (PLANTILLAS / "layout.html").read_text(encoding="utf-8")

    def test_un_dialogo_accesible_con_vuelta_al_confirm_nativo(self) -> None:
        # Cubre: U-28
        dialogo = self.layout[self.layout.index('<dialog id="confirmar"'):self.layout.index("</dialog>")]
        self.assertIn('aria-labelledby="confirmar-titulo"', dialogo)
        self.assertIn('<form method="dialog">', dialogo)
        self.assertIn('value="no" class="secondary" autofocus>Cancelar', dialogo)   # el foco empieza en cancelar
        for pieza in ("typeof dialogo.showModal !== 'function'", "confirm(boton.dataset.confirm)", "dialogo.showModal()",
                      "dialogo.returnValue === 'si'", "boton.focus()", "textContent = valor",
                      "'Adjuntos', 'Ninguno: la respuesta va sin archivos'", "getElementById('aviso-espera')"):
            self.assertIn(pieza, self.layout)
        self.assertNotIn("innerHTML", self.layout)                         # los datos del cliente van como texto
        self.assertEqual(self.layout.count("<script>"), 2)                 # sin script nuevo
        self.assertNotRegex(self.layout, r"\son[a-z]+=")                   # ni manejadores en línea


class Accesibilidad(unittest.TestCase):
    def pagina(self, titulo: str) -> str:
        with patch("anka.web.paginas.load_config", return_value={"active_mode": "local"}), \
                patch("anka.web.paginas.unread_thread_count", return_value=3):
            return paginas.page(titulo, "<p>x</p>")

    def test_la_navegacion_marca_la_pagina_actual_y_hay_salto_al_contenido(self) -> None:
        # Cubre: U-28
        html = self.pagina("Solicitudes")
        self.assertIn('<a href="/requests" aria-current="page">Solicitudes</a>', html)
        nav = html[html.index("<nav"):html.index("</nav>")]
        self.assertEqual(nav.count('aria-current="page"'), 1)
        otra = self.pagina("Revisar caso")
        self.assertNotIn("aria-current", otra[otra.index("<nav"):otra.index("</nav>")])
        self.assertLess(html.index('class="skip-link" href="#contenido"'), html.index("<header>"))
        self.assertIn('<main id="contenido" tabindex="-1">', html)

    def test_un_solo_reautorizar_google(self) -> None:
        # Cubre: U-28
        html = self.pagina("Solicitudes")
        self.assertEqual(html.count(">Reautorizar Google</button>"), 1)

    def test_rojo_solo_para_lo_destructivo_y_textos_pequenos_legibles(self) -> None:
        # Cubre: U-28
        css = (PLANTILLAS / "style.css").read_text(encoding="utf-8")
        self.assertEqual(len(re.findall(r"^[^{]*\.danger\b[^{]*\{", css, re.M)), 3)   # una regla, su hover y deshabilitado
        self.assertNotIn(".danger { background: var(--warn)", css)
        self.assertNotIn("font-size: 11px", css)
        self.assertIn('nav a[aria-current="page"]', css)

    def test_el_boton_de_tema_dice_el_actual_y_el_siguiente(self) -> None:
        # Cubre: U-28
        layout = (PLANTILLAS / "layout.html").read_text(encoding="utf-8")
        self.assertIn('aria-label="Tema actual: el del sistema. Pulsa para usar el claro"', layout)
        self.assertIn("boton.setAttribute('aria-label', ayuda)", layout)

    def test_glosario_y_sin_estilos_en_linea_en_el_caso(self) -> None:
        # Cubre: U-28
        caso = (PLANTILLAS / "case.html").read_text(encoding="utf-8")
        self.assertNotIn("style=", caso)
        self.assertNotIn("<style>", (PLANTILLAS / "activities_editor.html").read_text(encoding="utf-8"))
        pasos = (PLANTILLAS / "stepper.html").read_text(encoding="utf-8")   # el glosario va con los pasos (U-31)
        glosario = pasos[pasos.index('class="more glosario"'):pasos.index("</details>")]
        for termino in ("<dt>T</dt>", "<dt>T&amp;E</dt>", "<dt>Lectura realizada</dt>", "<dt>Aplicabilidad</dt>"):
            self.assertIn(termino, glosario)


class AvisoDelCorreo(Base):
    def test_ir_al_correo_solo_sale_si_la_pantalla_tiene_el_cuadro_de_respuesta(self) -> None:
        # Cubre: U-28
        db = store.open_db(Path(self.temp.name))
        store.save(db, "t-2", "m-2", QuoteStatus.REVIEW_PENDING, "cliente@empresa.co", complete_analysis(location=""))
        db.commit()
        db.close()
        caso = quotes.get_quote("t-2")
        self.assertIn('href="#respuesta"', str(pantalla_caso._next_step_banner(caso, True, hay_correo=True)))
        # en revisión técnica interna la sección no se dibuja: un enlace a #respuesta sería un enlace muerto
        aviso = str(pantalla_caso._next_step_banner(caso, True, hay_correo=False))
        self.assertNotIn("Ir al correo", aviso)
        self.assertNotIn("#respuesta", aviso)


if __name__ == "__main__":
    unittest.main()
