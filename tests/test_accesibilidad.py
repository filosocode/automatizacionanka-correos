"""Accesibilidad de la pantalla (U-37, D-102): marcas que no son color, avisos como alerta, botones reales y sin desborde."""

from tests import _isolate  # noqa: F401  (debe ir primero)

import re
import unittest
from pathlib import Path

from anka.dominio.status import QuoteStatus
from anka.web import pantalla_caso
from anka.web import server as web
from anka.web.views import MARCA_DE_ERROR, marcar_como_error, render_page
from tests.test_formularios_pendientes import Base

PLANTILLAS = Path(web.__file__).with_name("templates")
CSS = (PLANTILLAS / "style.css").read_text(encoding="utf-8")


def plantilla(nombre: str) -> str:
    texto = (PLANTILLAS / nombre).read_text(encoding="utf-8")
    if nombre == "layout.html":            # el JavaScript vive en archivos con huella (U-49): se busca en todo
        texto += (PLANTILLAS / "app.js").read_text(encoding="utf-8") + (PLANTILLAS / "tema.js").read_text(encoding="utf-8")
    return texto


class AvanceYElecciones(Base):
    def test_el_avance_es_una_lista_con_un_solo_paso_actual(self) -> None:
        # Cubre: U-37
        self.crear()
        pagina = pantalla_caso.render_case("t-1")
        self.assertIn('<ol class="process">', pagina)
        self.assertEqual(pagina.count('aria-current="step"'), 1)
        self.assertIn("Paso actual. ", pagina)
        self.assertIn("Pendiente. ", pagina)
        self.assertIn('.process-step.done strong::before { content: "✓ "; }', CSS)
        self.assertIn('.process-step.current strong::before { content: "▶ "; }', CSS)

    def test_lo_elegido_lleva_marca_y_alto_contraste(self) -> None:
        # Cubre: U-37
        self.assertIn('.opcion strong::before { content: "○ "; }', CSS)
        self.assertIn('.opcion:has(input:checked) strong::before { content: "● "; }', CSS)
        self.assertIn('.chip-filter[aria-pressed="true"]::before', CSS)
        self.assertIn("@media (forced-colors: active)", CSS)
        self.assertIn("Highlight", CSS)
        caso = plantilla("case.html")
        self.assertIn('<fieldset class="opciones"><legend class="solo-lectores">Cómo seguir con este caso</legend>', caso)
        self.assertNotIn('role="radiogroup"', caso)


class Avisos(Base):
    def test_un_fallo_se_anuncia_como_alerta_y_un_exito_como_estado(self) -> None:
        # Cubre: U-37
        self.crear()
        malo = render_page("t", "x", marcar_como_error("No se pudo guardar."))
        self.assertIn('role="alert"', malo)
        self.assertIn('<span class="solo-lectores">Error: </span>No se pudo guardar.', malo)
        self.assertNotIn(MARCA_DE_ERROR, malo)
        bueno = render_page("t", "x", "Guardado.")
        self.assertIn("class='notice flash' role=\"status\">Guardado.", bueno)
        self.assertNotIn("Error: ", bueno)

    def test_la_direccion_marca_el_fallo_antes_del_ancla(self) -> None:
        # Cubre: U-37
        self.assertEqual(web._senalar_fallo("/case?thread_id=a&message=x#respuesta"),
                         "/case?thread_id=a&message=x&aviso=error#respuesta")
        self.assertEqual(web._senalar_fallo("/otra"), "/otra")        # sin mensaje no hay nada que marcar
        self.assertEqual(web._mensaje({"message": ["hola"], "aviso": ["error"]}), MARCA_DE_ERROR + "hola")
        self.assertEqual(web._mensaje({"message": ["hola"]}), "hola")

    def test_una_accion_que_termina_con_aviso_redirige_con_aviso_error(self) -> None:
        # Cubre: U-37
        self.crear(estado=QuoteStatus.REVIEW_PENDING)
        resultado = self.post("/send", "thread_id=t-1&body=")        # «Escribe el correo antes de enviar.»
        self.assertEqual(resultado["status"], 303)
        self.assertIn("aviso=error", resultado["location"])
        pagina = pantalla_caso.render_case("t-1", message=marcar_como_error("Escribe el correo antes de enviar."))
        self.assertIn('role="alert"', pagina)

    def test_los_estados_que_cambian_en_silencio_y_el_contador_se_anuncian(self) -> None:
        # Cubre: U-37
        layout = plantilla("layout.html")
        self.assertIn('id="anuncio-estado" class="solo-lectores" role="status"', layout)
        self.assertIn("anunciar([", layout)
        self.assertIn("if (aviso.textContent !== texto)", layout)
        self.assertIn("if (estado.textContent !== textoEstado)", layout)
        self.assertIn('id="n-visibles"', plantilla("index.html"))
        self.assertIn('id="n-visibles"', plantilla("requests.html"))
        self.assertNotIn('aria-live="polite"', plantilla("activities_editor.html"))

    def test_el_aviso_de_una_seleccion_vacia_no_usa_alert(self) -> None:
        # Cubre: U-37
        banner = plantilla("link_banner.html")
        self.assertNotIn("alert(", banner)
        self.assertIn('data-links-error role="alert" hidden', banner)


class PantallaAngostaYZoom(unittest.TestCase):
    def test_el_desborde_ya_no_se_esconde(self) -> None:
        # Cubre: U-37
        cuerpo = CSS[CSS.index("\nbody {"):CSS.index("}", CSS.index("\nbody {"))]
        self.assertNotIn("overflow-x", cuerpo)
        self.assertIn(".layout { display: grid; grid-template-columns: minmax(0, 1fr);", CSS)
        self.assertIn(".scroll-x { overflow-x: auto; position: relative; }", CSS)

    def test_ajustes_selector_zoom_y_pantallas_bajas(self) -> None:
        # Cubre: U-37
        self.assertIn(".settings-box { position: static;", CSS)
        self.assertIn(".mode-form { flex-wrap: wrap; }", CSS)
        self.assertIn("html { scroll-padding-bottom: 9rem; }", CSS)
        self.assertIn("@media (max-height: 600px)", CSS)
        self.assertIn("header, .barra-accion, .flash { position: static; }", CSS)
        self.assertIn("evento.key !== 'Escape'", plantilla("layout.html"))
        self.assertIn("evento.key === 'Home'", plantilla("layout.html"))

    def test_tamanos_en_rem_y_objetivos_tactiles(self) -> None:
        # Cubre: U-37
        for px in re.findall(r"font-size:\s*(\d+)px", CSS):
            self.assertGreaterEqual(int(px), 14, "texto por debajo de 14 px")
        self.assertEqual(re.findall(r"font-size:\s*1[0-3]px", CSS), [])
        self.assertIn("font-size: max(.875rem, 14px);", CSS)
        self.assertIn('input[type="checkbox"], input[type="radio"] { width: 24px; height: 24px; flex: none; }', CSS)
        self.assertIn(".link-actions a, .link-bulk-item a, .timeline a {", CSS)
        self.assertIn(".secondary, .chip-filter { border: 1px solid var(--control-line); }", CSS)


class Campos(unittest.TestCase):
    def test_etiquetas_visibles_y_obligatorios(self) -> None:
        # Cubre: U-37
        self.assertNotIn('class="solo-lectores" for="buscar-casos"', plantilla("index.html"))
        self.assertIn('<label for="buscar-casos">Buscar un caso</label>', plantilla("index.html"))
        caso = plantilla("case.html")
        self.assertNotIn('class="solo-lectores" for="cuerpo-correo"', caso)
        self.assertIn('Carpeta del cliente <span class="meta">(obligatorio)</span>', caso)
        codigo = plantilla("code_panel.html")
        self.assertIn("(obligatorio)", codigo)
        self.assertIn('inputmode="numeric"', codigo)

    def test_el_boton_de_tema_contiene_su_texto_visible(self) -> None:
        # Cubre: U-37
        layout = plantilla("layout.html")
        self.assertIn('aria-label="Tema: sistema. Pulsa para usar el claro"', layout)
        self.assertIn("const ayuda = 'Tema: ' + nombres[actual]", layout)


class TablasYBotones(Base):
    def test_ningun_th_queda_sin_scope_ni_vacio(self) -> None:
        # Cubre: U-37
        self.assertTrue(list(PLANTILLAS.glob("*.html")))
        for archivo in PLANTILLAS.glob("*.html"):
            texto = archivo.read_text(encoding="utf-8")
            self.assertEqual(re.findall(r"<th>", texto), [], archivo.name)
            self.assertEqual(re.findall(r"<th[^>]*></th>", texto), [], archivo.name)

    def test_editar_tabla_y_ver_todas_son_botones_con_aria_expanded(self) -> None:
        # Cubre: U-37
        self.crear()
        pagina = pantalla_caso.render_case("t-1")
        self.assertIn('aria-label="Actividades detectadas, editables"', pagina)
        self.assertIn('id="editar-boton" aria-expanded="false" aria-controls="editar-tabla"', pagina)
        self.assertNotIn('class="toggle-oculto"', pagina)
        self.assertIn(".editor-tabla.abierto { display: block; }", CSS)
        layout = plantilla("layout.html")
        self.assertIn("<noscript><style>.editor-tabla { display: block; }", layout)
        self.assertIn(".act-table .del input {", CSS)
        self.assertIn("@media (max-width: 900px) {\n  .act-table { min-width: 0; }", CSS)

    def test_las_acciones_largas_dicen_que_hacen_y_se_liberan(self) -> None:
        # Cubre: U-37
        layout = plantilla("layout.html")
        for pieza in ("'/draft': 'Guardando el borrador…'", "'/code-issue': 'Creando la carpeta y enviando…'",
                      "formulario.setAttribute('aria-busy', 'true')", "id=\"cargando\"", "}, 30000);",
                      "if (event.defaultPrevented) return;", "No hubo respuesta."):
            self.assertIn(pieza, layout)

    def test_limpieza_menor(self) -> None:
        # Cubre: U-37
        self.assertIn("<h2>{{ project }}", plantilla("quote_row.html"))
        self.assertIn("(se abre en una pestaña nueva)", plantilla("timeline.html"))
        self.assertIn("(se abre en una pestaña nueva)", plantilla("acta.html"))
        self.assertIn("(se abre en una pestaña nueva)", plantilla("activities_editor.html"))
        self.assertIn("Nuevo: ", plantilla("notifications_panel.html"))


if __name__ == "__main__":
    unittest.main()
