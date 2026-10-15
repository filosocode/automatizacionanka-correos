"""La búsqueda y los filtros de las listas se resuelven en el servidor sobre todos los casos, y el JavaScript de las
páginas vive en archivos con huella con una CSP sin scripts en línea (U-49, D-114).

Cubre: U-49
"""

from tests import _isolate  # noqa: F401  (debe ir primero)

import json
import re
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

from anka.dominio.status import QuoteStatus
from anka.infra import settings, store
from anka.web import estaticos, openapi, paginas
from tests.test_app import complete_analysis, test_config
from tests.test_seguridad_http import atender


class Base(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        for p in (patch.object(settings, "DATA_DIR", Path(self._tmp.name)),
                  patch("anka.web.paginas.load_config", return_value=test_config()),
                  patch("anka.web.pantalla_caso.load_config", return_value=test_config())):
            p.start()
            self.addCleanup(p.stop)
        self.db = store.open_db(Path(self._tmp.name))
        self.addCleanup(self.db.close)

    def sembrar(self, n: int, estado: str = QuoteStatus.REVIEW_PENDING, desde: int = 0, fecha: str = "2026-10-01T09:00:00-05:00",
                tipo: str | None = None) -> None:
        for i in range(desde, desde + n):
            analisis = complete_analysis()
            analisis.update(project_name=f"Obra {i} de prueba", received_at=fecha)
            store.save(self.db, f"t-{i:04d}", f"m-{i}", estado, f"c{i}@ejemplo.test", analisis)
            if tipo:
                self.db.execute("INSERT INTO quote_metadata (thread_id, client_name, client_type, sector, notes, updated_at) "
                                "VALUES (?, ?, ?, 'Otro', '', '2026-10-01')", (f"t-{i:04d}", f"Cliente {i}", tipo))
        self.db.commit()


class BuscarEnTodosLosCasos(Base):
    def test_mis_casos_encuentra_un_caso_que_no_estaba_entre_las_200_filas(self) -> None:
        # Cubre: U-49
        self.sembrar(450)
        pagina = paginas.render_index(q="obra 449 de")
        self.assertEqual(pagina.count('class="queue-row"'), 1)
        self.assertIn("/case?thread_id=t-0449", pagina)
        self.assertIn("1 caso a la vista", pagina)
        self.assertNotIn("Se muestran", pagina)
        self.assertNotIn("Ver 200 más", pagina)

    def test_la_busqueda_ignora_mayusculas_y_lo_que_no_hay_da_cero(self) -> None:
        # Cubre: U-49
        self.sembrar(30)
        self.assertEqual(paginas.render_index(q="OBRA 7 DE PRUEBA").count('class="queue-row"'), 1)
        vacia = paginas.render_index(q="no existe esto")
        self.assertEqual(vacia.count('class="queue-row"'), 0)
        self.assertIn("Ningún caso coincide", vacia)
        self.assertNotRegex(vacia, r'<div id="sin-casos" class="empty" hidden>')            # el aviso se ve sin JavaScript
        self.assertRegex(paginas.render_index(), r'<div id="sin-casos" class="empty" hidden>')

    def test_el_contador_dice_el_total_real_y_el_pie_lo_acompana(self) -> None:
        # Cubre: U-49
        self.sembrar(450)
        pagina = paginas.render_index(q="obra")
        self.assertEqual(pagina.count('class="queue-row"'), 200)
        self.assertRegex(pagina, r'id="n-visibles" class="solo-lectores" role="status">450 casos a la vista<')
        self.assertIn("Se muestran 200 de 450 casos.", pagina)
        self.assertIn('href="/?q=obra&amp;etapa=atender&amp;mostrar=400"', pagina)      # «Ver más» conserva la búsqueda
        segunda = paginas.render_index(q="obra", etapa="atender", mostrar=400)
        self.assertEqual(segunda.count('class="queue-row"'), 400)
        self.assertIn("Se muestran 400 de 450 casos.", segunda)
        self.assertIn("Ver 50 más", segunda)

    def test_la_etapa_filtra_antes_de_paginar(self) -> None:
        # Cubre: U-49
        self.sembrar(250)
        self.sembrar(5, QuoteStatus.WAITING, desde=250)
        ahora = datetime.now().astimezone().isoformat(timespec="seconds")
        self.db.execute("UPDATE quotes SET sent_at=?, draft_body='Hola' WHERE status=?", (ahora, QuoteStatus.WAITING))
        self.db.commit()
        esperando = paginas.render_index(etapa="esperando")
        self.assertEqual(esperando.count('class="queue-row"'), 5)
        self.assertIn("5 casos a la vista", esperando)
        self.assertRegex(esperando, r'value="esperando" class="chip-filter" data-etapa="esperando" aria-pressed="true"')
        todos = paginas.render_index(etapa="todos")
        self.assertEqual(todos.count('class="queue-row"'), 200)
        self.assertIn("Se muestran 200 de 255 casos.", todos)
        self.assertRegex(todos, r'data-etapa="" aria-pressed="true">Todos <b>255</b>')
        por_defecto = paginas.render_index()                       # sin etapa: entra en «Por atender»
        self.assertIn("Se muestran 200 de 250 casos.", por_defecto)
        self.assertEqual(paginas.render_index(etapa="inventada").count('class="queue-row"'), 200)   # lo desconocido es «todos»

    def test_los_chips_cuentan_todos_los_casos_aunque_se_busque(self) -> None:
        # Cubre: U-49
        self.sembrar(20)
        pagina = paginas.render_index(q="obra 3 de")
        self.assertRegex(pagina, r'data-etapa="atender" aria-pressed="true">Por atender <b>20</b>')
        self.assertRegex(pagina, r'data-etapa="" aria-pressed="false">Todos <b>20</b>')

    def test_funciona_sin_javascript_formulario_get_con_boton_buscar(self) -> None:
        # Cubre: U-49
        self.sembrar(5)
        pagina = paginas.render_index(q="obra")
        self.assertIn('<form class="filtro-casos" method="get" action="/" role="search" data-filtro-etapas>', pagina)
        self.assertIn('<label for="buscar-casos">Buscar un caso</label>', pagina)
        self.assertIn('id="buscar-casos" name="q" type="search" value="obra"', pagina)
        # el primer botón del formulario es «Buscar» (lo que envía la tecla Intro) y conserva la etapa elegida
        primer_boton = re.search(r"<button[^>]*>", pagina[pagina.index("filtro-casos"):]).group(0)  # type: ignore[union-attr]
        self.assertIn('name="etapa" value="atender"', primer_boton)
        for etapa in ("atender", "esperando", "listo", "cotizando", "todos"):
            self.assertRegex(pagina, rf'<button type="submit" name="etapa" value="{etapa}" class="chip-filter"')

    def test_lo_buscado_se_escapa(self) -> None:
        # Cubre: U-49
        self.sembrar(2)
        hostil = '"><script>alert(1)</script><img src=x onerror=alert(1)>'
        for pagina in (paginas.render_index(q=hostil), paginas.render_requests(q=hostil)):
            self.assertNotIn("<script>alert", pagina)
            self.assertNotIn("<img src=x", pagina)
            self.assertIn("&#34;&gt;&lt;script&gt;", pagina)

    def test_un_texto_larguisimo_se_recorta(self) -> None:
        # Cubre: U-49
        self.sembrar(2)
        pagina = paginas.render_index(q="a" * 5000)
        self.assertLess(len(re.findall(r'value="(a+)"', pagina)[0]), 201)


class AdministrarCasos(Base):
    def test_busca_y_filtra_el_tipo_de_cliente_en_todos_los_casos(self) -> None:
        # Cubre: U-49
        self.sembrar(300)
        self.sembrar(3, desde=300, tipo="Entidad pública")
        pagina = paginas.render_requests(q="obra 30", tipo="Entidad pública")
        self.assertEqual(len(re.findall(r'class="case case-row"', pagina)), 3)
        self.assertIn("3 casos a la vista", pagina)
        self.assertRegex(pagina, r'<option value=\'Entidad pública\' selected>')
        self.assertEqual(len(re.findall(r'class="case case-row"', paginas.render_requests(tipo="Entidad pública"))), 3)
        self.assertEqual(len(re.findall(r'class="case case-row"', paginas.render_requests(tipo="inventado"))), 200)

    def test_el_orden_lo_da_el_servidor_y_vale_para_todos(self) -> None:
        # Cubre: U-49
        self.sembrar(250, fecha="2026-10-05T09:00:00-05:00")
        self.sembrar(1, desde=250, fecha="2026-01-01T09:00:00-05:00")               # la más vieja, fuera de las 200 primeras
        recientes = paginas.render_requests()
        self.assertNotIn("thread_id=t-0250", recientes)
        antiguas = paginas.render_requests(orden="antigua")
        primera = re.search(r'<article class="case case-row".*?thread_id=(t-\d+)', antiguas, re.S)
        self.assertEqual(primera.group(1), "t-0250")                            # type: ignore[union-attr]
        self.assertRegex(antiguas, r'<option value="antigua" selected>')
        self.assertIn('href="/requests?orden=antigua&amp;mostrar=400"', antiguas)         # «Ver más» conserva el orden
        self.assertEqual(paginas.render_requests(orden="inventado").count('class="case case-row"'), 200)

    def test_etapa_y_enlaces_de_agrupar_conservan_los_filtros(self) -> None:
        # Cubre: U-49
        self.sembrar(300)
        pagina = paginas.render_requests(etapa="atender", q="obra")
        self.assertIn('href="/requests?q=obra&amp;etapa=atender&amp;mostrar=400"', pagina)
        self.assertRegex(pagina, r'data-etapa="atender" aria-pressed="true">Por atender <b>300</b>')
        self.assertIn('<form class="filtro-casos" method="get" action="/requests" role="search">', pagina)
        self.assertIn('name="orden"', pagina)
        self.assertIn('name="tipo"', pagina)
        self.assertEqual(paginas.render_requests(q="no existe").count("Ningún caso coincide con esa búsqueda."), 1)
        self.assertNotRegex(paginas.render_requests(q="no existe"), r'<div id="no-results" class="empty" hidden>')

    def test_los_grupos_de_un_proyecto_conservan_solo_los_correos_que_cumplen(self) -> None:
        # Cubre: U-49
        for i, estado in enumerate((QuoteStatus.REVIEW_PENDING, QuoteStatus.WAITING, QuoteStatus.REVIEW_PENDING)):
            analisis = complete_analysis()
            analisis.update(project_name="Mantenimiento GRB", received_at=f"2026-10-0{i + 1}T09:00:00-05:00")
            store.save(self.db, f"g-{i}", f"m-{i}", estado, "mismo@cliente.test", analisis)
        self.db.commit()
        completa = paginas.render_requests()
        self.assertEqual(completa.count('class="grupo"'), 1)
        self.assertIn("3 conversaciones del mismo proyecto", completa)
        parcial = paginas.render_requests(etapa="atender")
        self.assertIn("2 conversaciones del mismo proyecto", parcial)
        self.assertNotIn("thread_id=g-1", parcial)
        una = paginas.render_requests(q="no existe")
        self.assertNotIn('class="grupo"', una)


class OpenApiYSpec(unittest.TestCase):
    def test_las_rutas_documentan_los_parametros_nuevos(self) -> None:
        # Cubre: U-49
        documento = json.dumps(openapi.especificacion()) if hasattr(openapi, "especificacion") else ""
        if not documento:
            _, _, cuerpo, _ = atender("GET", "/openapi.json")
            documento = cuerpo
        spec = json.loads(documento)
        parametros = {ruta: {p["name"] for p in spec["paths"][ruta]["get"].get("parameters", [])} for ruta in ("/", "/requests")}
        self.assertTrue({"q", "etapa", "mostrar"} <= parametros["/"])
        self.assertTrue({"q", "etapa", "mostrar", "tipo", "orden", "agrupar"} <= parametros["/requests"])

    def test_la_spec_nombra_los_parametros(self) -> None:
        # Cubre: U-49
        api = (Path(__file__).resolve().parents[1] / "spec" / "07-api.md").read_text(encoding="utf-8")
        for pieza in ("`q`", "`etapa`", "`mostrar`", "`tipo`", "`orden`", "app.<huella>.js", "script-src 'self'"):
            self.assertIn(pieza, api)


class JavaScriptConHuellaYCsp(Base):
    def test_las_paginas_no_llevan_javascript_en_linea_ni_manejadores(self) -> None:
        # Cubre: U-49
        self.sembrar(3)
        for ruta in ("/", "/requests", "/discarded", "/archive", "/case?thread_id=t-0000"):
            with self.subTest(ruta=ruta):
                estado, _, pagina, _ = atender("GET", ruta)
                self.assertEqual(estado, 200)
                self.assertEqual(re.findall(r"<script(?![^>]*\bsrc=)[^>]*>", pagina), [])
                self.assertNotRegex(pagina, r"\son[a-z]+\s*=")
                self.assertNotIn("javascript:", pagina.lower())
                self.assertIn(f'<script src="{estaticos.url_del_guion("tema")}"></script>', pagina)
                self.assertIn(f'<script src="{estaticos.url_del_guion("app")}" defer></script>', pagina)
        self.assertLess(len(atender("GET", "/")[2]), 120_000)

    def test_el_tema_se_pinta_antes_que_el_css_y_sin_defer(self) -> None:
        # Cubre: U-49
        _, _, pagina, _ = atender("GET", "/")
        self.assertLess(pagina.index(estaticos.url_del_guion("tema")), pagina.index(estaticos.url_del_estilo()))
        self.assertLess(pagina.index(estaticos.url_del_estilo()), pagina.index(estaticos.url_del_guion("app")))

    def test_la_csp_de_las_paginas_no_permite_scripts_en_linea(self) -> None:
        # Cubre: U-49
        for ruta in ("/", "/requests", "/static/logo-anka.png", "/no-existe"):
            with self.subTest(ruta=ruta):
                csp = atender("GET", ruta)[1]["Content-Security-Policy"]
                script = [p for p in csp.split(";") if p.strip().startswith("script-src")][0]
                self.assertEqual(script.strip(), "script-src 'self'")
                self.assertIn("style-src 'self' 'unsafe-inline'", csp)                  # los estilos siguen (ver D-114)
                self.assertIn("object-src 'none'", csp)

    def test_los_guiones_se_sirven_con_huella_cache_larga_etag_y_gzip(self) -> None:
        # Cubre: U-49
        for guion in ("app", "tema"):
            ruta = estaticos.url_del_guion(guion)
            with self.subTest(guion=guion):
                self.assertRegex(ruta, rf"^/static/{guion}\.[0-9a-f]{{12}}\.js$")
                estado, cabeceras, cuerpo, _ = atender("GET", ruta)
                self.assertEqual(estado, 200)
                self.assertEqual(cabeceras["Content-Type"], "text/javascript; charset=utf-8")
                self.assertEqual(cabeceras["Cache-Control"], "public, max-age=31536000, immutable")
                self.assertIn("X-Content-Type-Options", cabeceras)
                self.assertEqual(atender("GET", ruta, cabeceras={"If-None-Match": cabeceras["ETag"]})[0], 304)
                self.assertEqual(atender("GET", f"/static/{guion}.000000000000.js")[0], 404)   # solo la huella vigente
        _, cabeceras, _, _ = atender("GET", estaticos.url_del_guion("app"), cabeceras={"Accept-Encoding": "gzip"})
        self.assertEqual(cabeceras.get("Content-Encoding"), "gzip")

    def test_la_huella_cambia_con_el_contenido_del_guion(self) -> None:
        # Cubre: U-49
        with tempfile.TemporaryDirectory() as carpeta:
            js = Path(carpeta) / "app.js"
            js.write_text("let a = 1;", encoding="utf-8")
            with patch.dict(estaticos.GUIONES, {"app": js}):
                antes = estaticos.nombre_del_guion("app")
                js.write_text("let a = 2;", encoding="utf-8")
                self.assertNotEqual(estaticos.nombre_del_guion("app"), antes)
                self.assertIsNone(estaticos.buscar(antes))

    def test_la_huella_cambia_aunque_la_fecha_y_el_tamano_no_cambien(self) -> None:
        # Cubre: U-56
        # La causa de la intermitencia: dos escrituras seguidas del mismo largo dejan la misma fecha (el reloj del
        # archivo avanza a saltos) y la caché devolvía la huella vieja. Aquí se fuerza esa coincidencia.
        import os
        with tempfile.TemporaryDirectory() as carpeta:
            js = Path(carpeta) / "app.js"
            js.write_text("let a = 1;", encoding="utf-8")
            fecha = js.stat().st_mtime_ns
            with patch.dict(estaticos.GUIONES, {"app": js}):
                antes = estaticos.nombre_del_guion("app")
                js.write_text("let a = 2;", encoding="utf-8")
                os.utime(js, ns=(fecha, fecha))
                self.assertNotEqual(estaticos.nombre_del_guion("app"), antes)

    def test_el_codigo_conserva_lo_que_hacian_los_scripts_de_las_paginas(self) -> None:
        # Cubre: U-49
        js = estaticos.GUIONES["app"].read_text(encoding="utf-8")
        for pieza in ("location.reload()", "dialogo.showModal()", "function resumenDe(boton)", "citaEn(", "localStorage.setItem('anka-tema'",
                      "getElementById('editar-tabla')", "'/send': 'Enviando…'", "function medir()", "prefers-reduced-motion",
                      "// <física>", "// </física>", "new EventSource('/eventos')", "data-banner-vinculos"):
            self.assertIn(pieza, js)
        self.assertNotIn("document.currentScript", js)
        self.assertIn("localStorage.getItem('anka-tema')", estaticos.GUIONES["tema"].read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
