"""Todo JavaScript de las páginas pasa `node --check` (si hay Node): un error de sintaxis no pasa desapercibido.

Desde U-49 las páginas no llevan scripts en línea: el código vive en `templates/app.js` y `templates/tema.js` (se sirven
con huella en `/static`). Se revisan esos archivos y, por si vuelve a aparecer uno en línea, también los `<script>` de las
páginas renderizadas.

El commit 5d9dca3 dejó en `layout.html` una expresión regular con saltos de línea literales; el navegador descartó el segundo
script entero y ninguna prueba lo notó, porque el HTML se comparaba como texto. Esta prueba lo habría detectado.

Cubre: U-41
"""

from tests import _isolate  # noqa: F401  (debe ir primero)

import hashlib
import re
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from anka.cli import work_sync as sync
from anka.infra import settings, work_heartbeat
from anka.servicios import work_import
from anka.web import estaticos, paginas, pantalla_caso
from tests.test_app import test_config
from tests.test_html_render import Pages
from tests.test_puente_determinista import GmailConHilos, borrador_sin_json, recibido

NODE = shutil.which("node")
SCRIPT = re.compile(r"<script(?P<atributos>[^>]*)>(?P<codigo>.*?)</script>", re.DOTALL | re.IGNORECASE)
NO_ES_JAVASCRIPT = re.compile(r"""type\s*=\s*["']?(?:application/(?:ld\+)?json|text/(?:template|plain|html))""", re.I)


def scripts_de(pagina: str) -> list[tuple[str, str]]:
    """(extensión, código) de cada <script> con JavaScript en línea de la página."""
    encontrados = []
    for coincidencia in SCRIPT.finditer(pagina):
        atributos, codigo = coincidencia["atributos"], coincidencia["codigo"]
        if "src=" in atributos.lower() or NO_ES_JAVASCRIPT.search(atributos) or not codigo.strip():
            continue
        encontrados.append((".mjs" if re.search(r"type\s*=\s*[\"']?module", atributos, re.I) else ".js", codigo))
    return encontrados


def revisar(codigo: str, extension: str = ".js") -> str:
    """El error de sintaxis que `node --check` encuentra en el código, o '' si está bien."""
    with tempfile.TemporaryDirectory() as carpeta:
        archivo = Path(carpeta) / ("script" + extension)
        archivo.write_text(codigo, encoding="utf-8")
        resultado = subprocess.run([str(NODE), "--check", str(archivo)], capture_output=True, text=True, timeout=60)
    return "" if resultado.returncode == 0 else (resultado.stderr or resultado.stdout).strip()[:600]


@unittest.skipUnless(NODE, "Node no está instalado: no se puede comprobar la sintaxis de los scripts")
class LaComprobacionFunciona(unittest.TestCase):
    def test_detecta_el_error_del_commit_5d9dca3_una_regex_con_saltos_de_linea_literales(self) -> None:
        # Cubre: U-41
        self.assertEqual(revisar("const ok = /a\\nb/.test('a\\nb');"), "")
        self.assertIn("SyntaxError", revisar("const roto = /a\nb/.test('a');"))

    def test_detecta_otros_errores_y_acepta_modulos(self) -> None:
        # Cubre: U-41
        self.assertIn("SyntaxError", revisar("(function () { const a = ; })();"))
        self.assertEqual(revisar("export const x = 1;", ".mjs"), "")

    def test_solo_toma_los_scripts_con_javascript(self) -> None:
        # Cubre: U-41
        pagina = ('<script src="/x.js"></script><script type="application/json">{"a": 1}</script>'
                  '<script>const a = 1;</script><script type="module">export {};</script><script>  </script>')
        self.assertEqual(scripts_de(pagina), [(".js", "const a = 1;"), (".mjs", "export {};")])


@unittest.skipUnless(NODE, "Node no está instalado: no se puede comprobar la sintaxis de los scripts")
class ScriptsDeLasPaginas(Pages):
    """Las páginas de siempre (lista, descartadas, un caso en cada estado, uno que no existe) y las del rechazo de Work."""

    @classmethod
    def setUpClass(cls) -> None:
        super().setUpClass()
        cls.extra = cls.paginas_del_rechazo()

    @classmethod
    def paginas_del_rechazo(cls) -> dict[str, str]:
        """El caso mínimo (sin análisis de Work) y la lista con el aviso de rechazo y la alerta del día."""
        with tempfile.TemporaryDirectory() as carpeta, patch.object(settings, "DATA_DIR", Path(carpeta)), \
                patch("anka.web.paginas.load_config", return_value=test_config()), \
                patch("anka.web.pantalla_caso.load_config", return_value=test_config()):
            sync.sync_once(GmailConHilos({"d1": borrador_sin_json()}, {"T1": [recibido()]}), work_import.dashboard_upsert,
                           Path(carpeta) / "estado.json", {}, {"ankacomercialia@gmail.com"})
            work_heartbeat.write_heartbeat(30, rejected=[{"draft": "d1", "thread": "T1", "reason": "Motivo.",
                                                          "message": "gm-sin-json", "campos": ["status"],
                                                          "fecha": "2026-10-15"}])
            return {"caso_minimo": pantalla_caso.render_case("T1"), "lista_con_rechazo": paginas.render_index()}

    def test_todos_los_scripts_de_todas_las_paginas_tienen_sintaxis_valida(self) -> None:
        # Cubre: U-41
        unicos: dict[str, tuple[str, str, str]] = {}      # huella -> (página, extensión, código): cada script una vez
        for nombre, pagina in {**self.pages, **self.extra}.items():
            for extension, codigo in scripts_de(pagina):
                unicos.setdefault(hashlib.sha256(codigo.encode()).hexdigest() + extension, (nombre, extension, codigo))
        # Desde U-49 no debería haber ninguno en línea (ver la prueba de los guiones estáticos); si vuelve a aparecer, se revisa.
        for huella, (nombre, extension, codigo) in unicos.items():
            with self.subTest(pagina=nombre, script=huella[:10]):
                self.assertEqual(revisar(codigo, extension), "")

    def test_los_guiones_estaticos_tienen_sintaxis_valida(self) -> None:
        # Cubre: U-49
        for guion, ruta in estaticos.GUIONES.items():
            with self.subTest(guion=guion):
                self.assertTrue(ruta.exists())
                self.assertEqual(revisar(ruta.read_text(encoding="utf-8")), "")

    def test_las_paginas_cargan_los_guiones_con_huella_y_ninguno_en_linea(self) -> None:
        # Cubre: U-49
        for nombre, pagina in {**self.pages, **self.extra}.items():
            with self.subTest(pagina=nombre):
                self.assertEqual(scripts_de(pagina), [], "ninguna página lleva JavaScript en línea")
        indice = self.pages["index"]
        self.assertIn(f'<script src="{estaticos.url_del_guion("tema")}"></script>', indice)
        self.assertIn(f'<script src="{estaticos.url_del_guion("app")}" defer></script>', indice)
        self.assertLess(len(indice), 100_000, "la página ya no repite el JavaScript")


if __name__ == "__main__":
    unittest.main()
