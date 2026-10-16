"""Cubre: U-09, U-10, U-11, U-11, U-13, U-14, U-15, U-16, U-21 — siguiente paso fijo, una sola acción de emisión, fuentes en rangos y cola de hoy."""
from __future__ import annotations

import re
import unittest
from pathlib import Path

from tests.js_de_las_paginas import JS

from anka.dominio.attachments import compactar_paginas
from anka.dominio.status import QuoteStatus
from anka.web import paginas
from anka.dominio.activities import ACTIVITY_COLUMNS
from anka.web.templating import render
from anka.web.views import activities_editor, attachments_table

RAIZ = Path(__file__).resolve().parents[1]


class FuentesCompactas(unittest.TestCase):
    def test_agrupa_paginas_consecutivas_sin_perder_ninguna(self) -> None:
        # Cubre: U-10
        origen = ("adjunto E.T.pdf, Página 3, Página 5, Página 6, Página 7, Página 10, Página 11, Página 12, "
                  "Página 13, Página 14, Página 15")
        self.assertEqual(compactar_paginas(origen), "adjunto E.T.pdf, págs. 3, 5–7, 10–15")

    def test_una_sola_pagina_y_dos_consecutivas(self) -> None:
        # Cubre: U-10
        self.assertEqual(compactar_paginas("Fuente: Página 4"), "Fuente: Página 4")
        self.assertEqual(compactar_paginas("Página 8; Página 9"), "págs. 8, 9")

    def test_orden_y_repetidas(self) -> None:
        # Cubre: U-10
        self.assertEqual(compactar_paginas("Página 9, Página 2, Página 9, Página 3"), "págs. 2, 3, 9")


class EmisionDeLaT(unittest.TestCase):
    def _panel(self) -> str:
        return str(render("code_panel.html", code="", stage="", stage_label="", thread_id="h1", approved_code="",
                          can_assign=True, suggestion="26006", contact="c@x.co", received_text="Hola"))

    def test_solo_una_accion_primaria_de_emision_visible(self) -> None:
        # Cubre: U-09
        html = self._panel()
        primario = html.index("Emitir la T, crear la carpeta y enviar el correo")
        self.assertIn("Solo emitir la T", html)                      # U-40: visible, como botón secundario
        self.assertEqual(html.count("<button type=\"submit\" data-resumen"), 1)      # una sola acción llena
        self.assertLess(primario, html.index("Solo emitir la T"))
        self.assertNotIn("Otras opciones", html)

    def test_aviso_de_siguiente_paso_ya_no_queda_fijo(self) -> None:
        # Cubre: U-09, U-31
        css = (RAIZ / "anka" / "web" / "templates" / "style.css").read_text(encoding="utf-8")
        self.assertFalse([linea for linea in css.splitlines() if linea.startswith(".next-step {") and "sticky" in linea])
        self.assertIn("position: static", css)


def _caso(hilo: str, estado: QuoteStatus, **extra: object) -> dict:
    return {"thread_id": hilo, "status": estado, "status_label": "x", "client_name": "OTACC", "changes": [],
            "analysis": {"project_name": f"Proyecto {hilo}"}, "services": [], "client_type": "No clasificado",
            "client_email": "", "sector": "No clasificado", **extra}


class ColaDeHoy(unittest.TestCase):
    def test_ordena_por_urgencia_y_conserva_el_orden_entre_iguales(self) -> None:
        # Cubre: U-11
        casos = [
            _caso("decidir-1", QuoteStatus.REVIEW),
            _caso("emitir", QuoteStatus.READY),
            _caso("respondio", QuoteStatus.WAITING, reply_pending="2026-10-08T06:00:00"),
            _caso("decidir-2", QuoteStatus.REVIEW),
            _caso("cotizando", QuoteStatus.READY, quote_code="26005", code_stage="T"),
        ]
        orden = [q["thread_id"] for q in paginas.cola_de_hoy(casos)]
        self.assertEqual(orden, ["respondio", "emitir", "decidir-1", "decidir-2"])

    def test_cada_fila_tiene_un_boton_con_verbo_y_su_ancla(self) -> None:
        # Cubre: U-11
        decidir = str(paginas.queue_row(_caso("h1", QuoteStatus.REVIEW), 3))
        self.assertIn(">Decidir cómo seguir</a>", decidir)
        self.assertIn("/case?thread_id=h1#decision", decidir)
        self.assertEqual(decidir.count("<a "), 1)
        emitir = str(paginas.queue_row(_caso("h2", QuoteStatus.READY), 1))
        self.assertIn(">Emitir la T</a>", emitir)
        self.assertIn("#emitir-t", emitir)
        self.assertNotIn("secondary", emitir)  # el primero es el botón principal
        self.assertIn("secondary", decidir)

    def test_estado_desconocido_cae_al_final_con_abrir(self) -> None:
        # Cubre: U-11
        self.assertEqual(paginas.accion_de_caso(_caso("h", QuoteStatus.MISSING))[1], "Ver el caso")

    def test_mis_casos_lista_todos_los_casos_sin_limite(self) -> None:
        # Cubre: U-32
        casos = [_caso(f"t{n}", QuoteStatus.REVIEW) for n in range(7)]
        from unittest import mock
        with mock.patch.object(paginas, "all_quotes", return_value=casos),                 mock.patch.object(paginas, "unread_notifications", return_value=[]),                 mock.patch.object(paginas, "sync_status_text", return_value=""),                 mock.patch.object(paginas, "page", side_effect=lambda titulo, cuerpo, mensaje="": str(cuerpo)):
            html = paginas.render_index()
        self.assertEqual(html.count('class="queue-row"'), 7)                    # ya no hay tope de cinco (D-92)
        self.assertNotIn("caso(s) más", html)


class ListaDeSolicitudes(unittest.TestCase):
    def test_grupo_de_etapa(self) -> None:
        # Cubre: U-13
        self.assertEqual(paginas.grupo_de_etapa(_caso("a", QuoteStatus.READY)), "listo")
        self.assertEqual(paginas.grupo_de_etapa(_caso("b", QuoteStatus.REVIEW)), "atender")
        self.assertEqual(paginas.grupo_de_etapa(_caso("c", QuoteStatus.WAITING)), "esperando")
        self.assertEqual(paginas.grupo_de_etapa(
            _caso("d", QuoteStatus.READY, quote_code="26005", code_stage="T")), "cotizando")
        self.assertEqual(paginas.grupo_de_etapa(_caso("e", QuoteStatus.IGNORED)), "otras")

    def test_la_fila_lleva_verbo_etapa_y_ancla(self) -> None:
        # Cubre: U-13
        fila = str(paginas.quote_row(_caso("h1", QuoteStatus.REVIEW), selectable=True))
        self.assertIn('data-etapa="atender"', fila)
        self.assertIn(">Decidir cómo seguir</a>", fila)
        self.assertIn("/case?thread_id=h1#decision", fila)
        quieta = str(paginas.quote_row(_caso("h2", QuoteStatus.IGNORED)))
        self.assertIn(">Ver el caso</a>", quieta)

    def _lista(self, casos: list[dict]) -> str:
        from unittest import mock
        with mock.patch.object(paginas, "all_quotes", return_value=casos),                 mock.patch.object(paginas, "unread_notifications", return_value=[]),                 mock.patch.object(paginas, "page", side_effect=lambda titulo, cuerpo, mensaje="": str(cuerpo)):
            return paginas.render_requests()

    def test_filtra_por_etapa_y_oculta_eliminar_en_acciones(self) -> None:
        # Cubre: U-13
        html = self._lista([_caso("decidir", QuoteStatus.REVIEW), _caso("emitir", QuoteStatus.READY),
                            _caso("ignorada", QuoteStatus.IGNORED)])
        for etiqueta in ("Por atender", "Esperando al cliente", "Listas para la T", "Con código de oferta"):
            self.assertIn(etiqueta, html)
        self.assertIn('<details class="more bulk-menu"><summary>Opciones</summary>', html)
        self.assertLess(html.index("bulk-menu"), html.index('id="delete-btn"'))
        self.assertIn("data-confirm", html[html.index('id="delete-btn"'):html.index('id="delete-btn"') + 400])

    def test_por_defecto_la_mas_reciente_primero(self) -> None:
        # Cubre: U-16
        casos = [_caso("vieja", QuoteStatus.REVIEW, first_seen_at="2026-09-01T09:00:00"),
                 _caso("nueva", QuoteStatus.IGNORED, first_seen_at="2026-10-08T06:10:00"),
                 _caso("media", QuoteStatus.READY, first_seen_at="2026-09-25T15:27:00")]
        html = self._lista(casos)
        orden = [html.index(f"thread_id={hilo}") for hilo in ("nueva", "media", "vieja")]
        self.assertEqual(orden, sorted(orden))                 # la fecha manda, no la urgencia
        self.assertIn('<option value="reciente" selected>Más recientes primero</option>', html)
        for opcion in ("Más antiguas primero", "Más urgentes primero"):
            self.assertIn(opcion, html)

    def test_cada_fila_trae_su_fecha_y_urgencia_para_reordenar(self) -> None:
        # Cubre: U-16
        fila = str(paginas.quote_row(_caso("h", QuoteStatus.REVIEW, first_seen_at="2026-10-08T06:10:00")))
        fecha = int(re.search(r'data-fecha="(\d+)"', fila).group(1))  # type: ignore[union-attr]
        self.assertGreater(fecha, 0)
        self.assertIn('data-urgencia="4"', fila)
        quieta = str(paginas.quote_row(_caso("q", QuoteStatus.IGNORED)))
        self.assertIn('data-fecha="0"', quieta)                # sin fecha: al final
        self.assertIn('data-urgencia="10"', quieta)

    def test_la_fecha_de_recepcion_acepta_iso_y_formato_de_correo_y_sin_fecha_es_cero(self) -> None:
        # Cubre: U-16
        iso = paginas.fecha_de_recepcion(_caso("a", QuoteStatus.REVIEW, first_seen_at="2026-10-08T06:10:00"))
        correo = paginas.fecha_de_recepcion(_caso("b", QuoteStatus.REVIEW, first_seen_at="x"))
        self.assertGreater(iso, 0)
        self.assertEqual(correo, 0.0)
        rfc = _caso("c", QuoteStatus.REVIEW, first_seen_at="")
        rfc["analysis"]["received_at"] = "Wed, 08 Oct 2026 06:10:00 -0500"
        self.assertGreater(paginas.fecha_de_recepcion(rfc), 0)
        self.assertEqual(paginas.fecha_de_recepcion(_caso("d", QuoteStatus.REVIEW)), 0.0)

    def test_el_orden_se_puede_cambiar_sin_recargar(self) -> None:
        # Cubre: U-16
        # Desde U-49 el orden lo resuelve el servidor (sobre todos los casos) y el navegador solo envía el formulario.
        plantilla = (RAIZ / "anka" / "web" / "templates" / "requests.html").read_text(encoding="utf-8")
        self.assertIn('name="orden"', plantilla)
        for criterio in ("reciente", "antigua", "urgencia"):
            self.assertIn(criterio, plantilla)
        self.assertIn("campo.form.requestSubmit(buscar)", JS)


class TablaYLenguaje(unittest.TestCase):
    ACTIVIDAD = {"activity": "Parque solar", "servicio_tecnica": "LiDAR", "quantity": "544.5", "unit": "ha",
                 "ubicacion": "Meta", "entregable": "MDT", "plazo_restriccion": "40 días", "fuente": "pág. 3",
                 "estado": "pendiente", "notes": "Falta el KML"}

    def test_cinco_columnas_visibles_y_los_diez_campos_se_siguen_enviando(self) -> None:
        # Cubre: U-14
        html = str(activities_editor("h1", [self.ACTIVIDAD]))
        editor = html[html.index('class="act-table"'):]                # la tabla completa, tras «Corregir la tabla» (U-31)
        cabecera = editor[editor.index("<thead"):editor.index("</thead>")]
        self.assertEqual(cabecera.count("<th role="), 6)         # cinco columnas y la de «Quitar»
        for titulo in ("Actividad", "Tipo de servicio", "Cantidad y unidad", "Estado", "Notas"):
            self.assertIn(f'<th role="columnheader" scope="col">{titulo}</th>', cabecera)
        for clave, _ in ACTIVITY_COLUMNS:                       # el formulario guarda lo mismo que antes
            self.assertIn(f"name='a0_{clave}'", html)
            self.assertIn(f"name='a1_{clave}'", html)           # y la fila nueva
        self.assertIn("Más datos: ubicación, entregable, plazo y fuente", html)

    def test_cantidad_y_unidad_juntas_y_cajas_del_mismo_tamano_sin_barras(self) -> None:
        # Cubre: U-14
        editor = str(activities_editor("h1", [self.ACTIVIDAD]))
        par = editor[editor.index('<div class="cant-par">'):editor.index("</div>", editor.index('<div class="cant-par">'))]
        self.assertIn("name='a0_quantity'", par)
        self.assertIn("name='a0_unit'", par)                                     # juntas, en el mismo contenedor
        self.assertNotIn("<style>", editor)                                      # sus estilos están en style.css (U-80)
        self.assertNotIn('id="ajustar-cajas"', editor)                           # sin botón: las cajas se ajustan solas (U-40)
        self.assertIn('role="row"', editor)                                      # la cuadrícula conserva la semántica de tabla
        css = (RAIZ / "anka" / "web" / "templates" / "style.css").read_text(encoding="utf-8")
        css = css[css.index("/* Tabla de actividades"):]                         # el bloque de la tabla
        self.assertIn("grid-template-columns: repeat(5, minmax(0, 1fr)) 84px", css)   # cinco columnas iguales
        self.assertIn("field-sizing: content", css)                              # la caja se ajusta a su texto
        self.assertIn("resize: vertical", css)                                   # se puede cambiar desde la esquina
        self.assertIn("overflow: auto", css)                                     # y se desplaza por dentro si el texto no cabe
        self.assertNotIn("overflow: hidden", css)
        self.assertIn("flex: 1 1 auto", css)                                     # todas las de la fila igualan la altura
        self.assertNotIn("resize: none", css)
        self.assertNotIn("height: 68px", css)                                    # ni alturas distintas por columna
        self.assertNotIn("width: 29%", css)                                      # ni una columna más ancha que otra

    def test_el_script_iguala_la_fila_al_arrastrar_la_esquina(self) -> None:
        # Cubre: U-14
        layout = ((RAIZ / "anka" / "web" / "templates" / "layout.html").read_text(encoding="utf-8") + (RAIZ / "anka" / "web" / "templates" / "app.js").read_text(encoding="utf-8") + (RAIZ / "anka" / "web" / "templates" / "tema.js").read_text(encoding="utf-8"))
        for pieza in ("document.querySelector('.act-table')", "new MutationObserver", "attributeFilter: ['style']",
                      "caja.closest('.act-main, .act-grid')", "c.style.height = alto"):
            self.assertIn(pieza, layout)
        self.assertEqual(layout.count("<script>"), 0)                             # ningún script en línea (U-49)

    def test_barra_de_guardado_con_motivo_dentro_y_exportar_aparte(self) -> None:
        # Cubre: U-21
        html = str(activities_editor("h1", [self.ACTIVIDAD], ultima={"version": 4, "created_at": "2026-10-08T12:35:10"}))
        barra = html[html.index('id="barra-guardado"'):html.index("</form>")]
        self.assertIn("Sin cambios por guardar · última versión: 4 (2026-10-08 12:35)", barra)
        self.assertIn('id="caja-motivo"', barra)
        self.assertIn('<select name="reason">', barra)                           # el motivo va con «Guardar tabla»
        self.assertIn('id="guardar-tabla"', barra)
        self.assertIn('href="/acta?thread_id=h1">Ver historial de cambios</a>', barra)
        self.assertNotIn("Bajar en Excel", barra)                               # y nada de Excel cerca del motivo
        exportar = html[html.index("</form>"):]
        self.assertIn("Sacar la tabla", exportar)
        self.assertIn('href="/export.xlsx?thread_id=h1">Bajar en Excel</a>', exportar)
        self.assertIn("Cómo funciona la hoja de Google", exportar)               # la explicación larga, plegada
        self.assertNotIn('name="reason"', exportar)

    def test_sin_version_previa_la_barra_solo_dice_que_no_hay_cambios(self) -> None:
        # Cubre: U-21
        html = str(activities_editor("h1", [self.ACTIVIDAD]))
        self.assertIn('data-sin-cambios="Sin cambios por guardar"', html)

    def test_quitar_se_ve_marcado_y_el_script_pide_motivo_solo_con_cambios_y_confirma(self) -> None:
        # Cubre: U-21
        html = str(activities_editor("h1", [self.ACTIVIDAD]))
        self.assertIn('<span class="q-on">Se borrará al guardar. Deshacer</span>', html)
        css = (RAIZ / "anka" / "web" / "templates" / "style.css").read_text(encoding="utf-8")
        self.assertIn(".act-row.por-quitar .act-main textarea", css)             # atenuada y tachada
        self.assertIn("text-decoration: line-through", css)
        layout = ((RAIZ / "anka" / "web" / "templates" / "layout.html").read_text(encoding="utf-8") + (RAIZ / "anka" / "web" / "templates" / "app.js").read_text(encoding="utf-8") + (RAIZ / "anka" / "web" / "templates" / "tema.js").read_text(encoding="utf-8"))
        for pieza in ("getElementById('barra-guardado')", "motivo.hidden = !hay", "guardar.disabled = !hay",
                      "classList.toggle('por-quitar', c.checked || vaciada)", "guardar.dataset.confirm", "Se quitará ", "Se quitarán ",
                      "Cambios sin guardar: "):
            self.assertIn(pieza, layout)
        self.assertEqual(layout.count("<script>"), 0)                             # ningún script en línea (U-49)

    def test_la_guia_habla_al_comercial(self) -> None:
        # Cubre: U-14
        html = str(activities_editor("h1", []))
        self.assertIn("quién lo resuelve", html)
        self.assertNotIn("No asignes nombres", html)

    def test_adjuntos_con_palabras_de_persona(self) -> None:
        # Cubre: U-14
        html = str(attachments_table([
            {"filename": "plano.dwg", "status": "bloqueado", "coverage": "no_legible", "blocked_fields": ["alcance"]},
            {"filename": "et.pdf", "status": "extraido", "coverage": "completa"}]))
        for texto in ("Falta revisar", "Dónde se leyó", "No se pudo leer", "Lo revisa una persona", "Leído"):
            self.assertIn(texto, html)
        for codigo in ("no_legible", "extraido", "Datos bloqueados", "Evidencia y localizador"):
            self.assertNotIn(codigo, html)

    def test_foco_visible_y_controles_de_44_px(self) -> None:
        # Cubre: U-14
        css = (RAIZ / "anka" / "web" / "templates" / "style.css").read_text(encoding="utf-8")
        self.assertIn(":focus-visible { outline: 2px solid", css)
        self.assertNotIn("min-height: 34px", css)
        self.assertNotIn("min-height: 36px", css)
        self.assertNotIn("min-height: 40px", css)


class TemaClaroYOscuro(unittest.TestCase):
    def setUp(self) -> None:
        self.css = (RAIZ / "anka" / "web" / "templates" / "style.css").read_text(encoding="utf-8")

    def _tokens(self, bloque: str) -> dict[str, str]:
        return dict(re.findall(r"(--[\w-]+):\s*([^;]+);", bloque))

    def _bloques(self) -> tuple[dict[str, str], dict[str, str], dict[str, str]]:
        claro = self.css[self.css.index(":root {"):self.css.index("@media (prefers-color-scheme: dark)")]
        sistema = self.css[self.css.index("@media (prefers-color-scheme: dark)"):self.css.index(':root[data-theme="dark"]')]
        manual = self.css[self.css.index(':root[data-theme="dark"]'):self.css.index("* { box-sizing")]
        return self._tokens(claro), self._tokens(sistema), self._tokens(manual)

    def test_los_dos_oscuros_son_iguales_y_redefinen_todos_los_colores_del_claro(self) -> None:
        # Cubre: U-15
        claro, sistema, manual = self._bloques()
        self.assertEqual(sistema, manual)                       # el del sistema y el manual no se desincronizan
        self.assertEqual(set(claro), set(manual))               # ningún token queda solo en un tema
        self.assertNotEqual(claro["--bg"], manual["--bg"])
        self.assertIn(':root:not([data-theme="light"])', self.css)  # una elección «claro» gana sobre el sistema oscuro
        self.assertEqual(len(re.findall(r"^\s+color-scheme: dark;", self.css, re.M)), 2)  # los controles nativos también

    def test_no_quedan_colores_literales_fuera_de_las_variables(self) -> None:
        # Cubre: U-15
        resto = self.css[self.css.index("* { box-sizing"):]
        self.assertEqual(sorted(set(re.findall(r"#[0-9a-fA-F]{3,8}\b", resto))), ["#fff"])
        plantillas = RAIZ / "anka" / "web" / "templates"
        for nombre in ("activities_editor.html", "case.html"):
            self.assertNotRegex((plantillas / nombre).read_text(encoding="utf-8"), r"#[0-9a-fA-F]{3,6}\b")

    def test_el_boton_de_tema_y_su_aplicacion_previa_al_pintado(self) -> None:
        # Cubre: U-15
        layout = ((RAIZ / "anka" / "web" / "templates" / "layout.html").read_text(encoding="utf-8") + (RAIZ / "anka" / "web" / "templates" / "app.js").read_text(encoding="utf-8") + (RAIZ / "anka" / "web" / "templates" / "tema.js").read_text(encoding="utf-8"))
        self.assertIn('id="theme-toggle"', layout)
        self.assertIn("localStorage.getItem('anka-tema')", layout)
        # sin parpadeo: el guion del primer pintado va en el <head>, sin `defer` y antes del CSS
        self.assertLess(layout.index("url_del_script('tema')"), layout.index("url_del_estilo()"))
        self.assertNotIn("url_del_script('tema') }}\" defer", layout)
        for estado in ("system", "light", "dark"):
            self.assertIn(estado, layout)


class ResumenYControles(unittest.TestCase):
    def test_a_igual_urgencia_el_mas_reciente_primero(self) -> None:
        # Cubre: U-11
        casos = [_caso("vieja", QuoteStatus.REVIEW, first_seen_at="2026-09-01T09:00:00"),
                 _caso("nueva", QuoteStatus.REVIEW, first_seen_at="2026-10-08T06:10:00"),
                 _caso("emitir", QuoteStatus.READY, first_seen_at="2026-08-01T09:00:00")]
        self.assertEqual([q["thread_id"] for q in paginas.cola_de_hoy(casos)], ["emitir", "nueva", "vieja"])

    def _resumen(self, casos: list[dict], notificaciones: list[dict]) -> str:
        from unittest import mock
        with mock.patch.object(paginas, "all_quotes", return_value=casos),                 mock.patch.object(paginas, "unread_notifications", return_value=notificaciones),                 mock.patch.object(paginas, "sync_status_text", return_value=""),                 mock.patch.object(paginas, "page", side_effect=lambda titulo, cuerpo, mensaje="": str(cuerpo)):
            return paginas.render_index()

    def _novedad(self, hilo: str) -> dict:
        return {"thread_id": hilo, "title": "Correo nuevo", "project_name": "P", "message": "m", "created_at": "2026-10-08T06:10:00"}

    def test_las_novedades_que_repiten_la_cola_se_pliegan_y_conservan_su_boton(self) -> None:
        # Cubre: U-11
        casos = [_caso("a", QuoteStatus.REVIEW), _caso("b", QuoteStatus.READY)]
        repetida = self._resumen(casos, [self._novedad("a")])
        self.assertIn("también están en tu lista de casos", repetida)
        self.assertIn("Ya las vi todas", repetida)                # el botón sigue dentro del plegado
        ajena = self._resumen(casos, [self._novedad("otro")])
        self.assertNotIn("también están en tu lista de casos", ajena)       # una novedad que no está en la cola queda a la vista

    def test_mis_casos_trae_su_buscador_y_no_el_boton_de_buscar_o_exportar(self) -> None:
        # Cubre: U-32
        html = self._resumen([_caso(f"t{n}", QuoteStatus.REVIEW) for n in range(7)], [])
        self.assertIn('id="buscar-casos" name="q" type="search"', html)
        self.assertNotIn("Buscar o exportar", html)

    def test_controles_de_44_px_y_botones_de_etapa_con_espacio(self) -> None:
        # Cubre: U-14
        css = (RAIZ / "anka" / "web" / "templates" / "style.css").read_text(encoding="utf-8")
        self.assertRegex(css, r"nav a \{\s*display: inline-flex; align-items: center; min-height: 44px;")
        self.assertIn("input:not([type=checkbox]):not([type=radio]), select { min-height: 44px; }", css)
        self.assertRegex(css, r"\.chip-filter \{ display: inline-flex; align-items: center; gap: 6px;")
        self.assertRegex(css, r"\.more > summary \{[^}]*min-height: 44px;")


if __name__ == "__main__":
    unittest.main()
