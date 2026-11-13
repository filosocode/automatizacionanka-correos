"""Encabezado compacto en pantallas angostas, verbo primero en las filas, marca «Ahora» como texto y alertas (U-58)."""

from tests import _isolate  # noqa: F401  (debe ir primero)

import re
import unittest
from pathlib import Path
from unittest.mock import patch

from anka.servicios import quotes
from anka.web import acciones, formularios_pendientes as pendientes, paginas, server
from tests.test_change_control import Base

PLANTILLAS = Path(__file__).resolve().parents[1] / "anka" / "web" / "templates"
CSS = (PLANTILLAS / "style.css").read_text(encoding="utf-8")


def bloque_angosto() -> str:
    inicio = CSS.index("Encabezado compacto en pantallas angostas (U-58)")
    return CSS[inicio:]


class EncabezadoCompacto(unittest.TestCase):
    # Cubre: U-58
    def test_en_600_px_o_menos_el_encabezado_es_una_sola_barra(self):
        css = bloque_angosto()
        self.assertIn("@media (max-width: 600px)", css)
        regla = re.search(r"\n  header \{([^}]*)\}", css).group(1)
        self.assertIn("flex-direction: row", regla)
        self.assertIn("flex-wrap: nowrap", regla)
        self.assertIn("position: relative", regla)           # el menú se ancla al encabezado

    # Cubre: U-58
    def test_cada_control_conserva_44_px_y_el_menu_sigue_plegado(self):
        css = bloque_angosto()
        self.assertIn(".brand { min-width: 0; min-height: 44px;", css)
        self.assertIn("header .settings-box { position: absolute;", css)
        layout = (PLANTILLAS / "layout.html").read_text(encoding="utf-8")
        self.assertIn('<details class="settings" id="ajustes"><summary>Ajustes</summary>', layout)
        # El nombre accesible del logo no depende del texto «Solicitudes de cotización» que se oculta a la vista.
        self.assertIn('aria-label="Anka, solicitudes de cotización', layout)
        self.assertIn("font-size: .875rem", CSS)                 # sin texto menor de 14 px (U-37)
        self.assertNotRegex(css, r"font-size:\s*(?:[0-9]|1[0-3])px")


class VerboPrimero(Base):
    # Cubre: U-58
    def test_la_fila_de_la_lista_empieza_por_lo_que_hay_que_hacer(self):
        quote = quotes.get_quote("T1")
        for fila in (paginas.quote_row(quote), paginas.queue_row(quote, 1)):
            texto = re.sub(r"<[^>]+>", "", fila)
            if "case-row" in fila:
                verbo = re.search(r'<span class="verbo" aria-hidden="true">([^<]*?) · </span>', fila).group(1)
                visible = re.sub(r"\s+", " ", texto).strip()
                self.assertTrue(visible.startswith(verbo + " · "), visible)
            else:                                                 # U-107: la cola dice la acción solo en el botón
                self.assertNotIn('class="verbo"', fila)
                verbo = re.search(r'<span class="caso-estado"><b>[^<]*</b>([^<]+)</span>', fila).group(1)
            self.assertTrue(verbo)
            self.assertIn(f">{verbo}</a>" if "case-row" in fila else f"</b>{verbo}</span>", fila)   # conserva su texto
            self.assertIn(quote["client_name"], fila)

    # Cubre: U-107
    def test_la_fila_de_la_cola_es_proyecto_cliente_estado_y_un_boton(self):
        fila = paginas.queue_row(quotes.get_quote("T1"), 1)
        self.assertNotIn('class="rank"', fila)
        self.assertRegex(fila, r"<strong>[^<]+</strong>")
        self.assertEqual(fila.count("<a "), 1)
        self.assertIn("main:has(> .queue) > * { max-width: 1120px;", CSS)

    # Cubre: U-58
    def test_el_verbo_repetido_no_se_lee_dos_veces(self):
        fila = paginas.quote_row(quotes.get_quote("T1"))
        self.assertEqual(fila.count('aria-hidden="true">'), 1)
        self.assertEqual(paginas.queue_row(quotes.get_quote("T1"), 1).count('aria-hidden="true">'), 1)   # solo el icono

    # Cubre: U-107
    def test_la_lista_concuerda_en_singular_y_el_boton_editar_tiene_area_de_toque(self):
        pagina = re.sub(r"<[^>]+>", " ", paginas.render_index())
        self.assertNotRegex(pagina, r"\b1 (esperan|solicitudes)\b")
        self.assertIn(".act-resumen .editar-fila::after", CSS)      # el área de toque llega a 44 px sin cambiar el aspecto


class MarcaAhoraEsTexto(unittest.TestCase):
    # Cubre: U-58
    def test_la_marca_de_los_avisos_es_un_elemento_y_no_contenido_de_css(self):
        self.assertNotIn('content: "Ahora"', CSS)
        self.assertNotIn('content: "Antes de seguir"', CSS)
        for nombre, texto in (("next_step.html", "Ahora"), ("change_banner.html", "Antes de seguir"),
                              ("change_todo.html", "Antes de seguir"), ("link_banner.html", "Antes de seguir")):
            with self.subTest(plantilla=nombre):
                self.assertIn(f'<span class="marca-paso">{texto}</span>',
                              (PLANTILLAS / nombre).read_text(encoding="utf-8"))


class FallosAnunciadosComoAlerta(unittest.TestCase):
    # Cubre: U-58
    def test_buscar_correos_nuevos_que_falla_se_marca_como_fallo(self):
        pendientes.hubo_fallo()
        with patch.object(acciones, "load_config", return_value={"active_mode": "local"}), \
                patch.object(acciones, "checked_run", return_value={"at": "2026-10-15T10:00:00-05:00", "seconds": 1,
                                                                    "new": 0, "updated": 0, "error": "sin red"}):
            destino = acciones.POST_ROUTES["/refresh"]({})
        self.assertIn("No%20se%20pudo%20buscar%20correos", destino)
        self.assertTrue(pendientes.hubo_fallo())
        self.assertIn("aviso=error", server._senalar_fallo(destino))

    # Cubre: U-58
    def test_buscar_correos_nuevos_que_sale_bien_no_es_alerta(self):
        pendientes.hubo_fallo()
        with patch.object(acciones, "load_config", return_value={"active_mode": "local"}), \
                patch.object(acciones, "checked_run", return_value={"at": "2026-10-15T10:00:00-05:00", "seconds": 1,
                                                                    "new": 1, "updated": 0, "error": ""}):
            acciones.POST_ROUTES["/refresh"]({})
        self.assertFalse(pendientes.hubo_fallo())

    # Cubre: U-58
    def test_el_mensaje_con_aviso_error_se_dibuja_como_alerta(self):
        with patch("anka.web.paginas.load_config", return_value={"active_mode": "local"}):
            pagina = paginas.page("Solicitudes de cotización", "", server._mensaje({"message": ["No se pudo"], "aviso": ["error"]}))
        self.assertIn('role="alert"', pagina)


if __name__ == "__main__":
    unittest.main()
