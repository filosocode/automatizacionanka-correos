"""Sistema visual de Anka (U-36, D-101): contraste de la paleta, fuente y logo propios, foco y movimiento."""

from tests import _isolate  # noqa: F401  (debe ir primero)

import re
import unittest
from pathlib import Path

from anka.web import server as web
from tests.test_openapi import request

CSS = (Path(web.__file__).with_name("templates") / "style.css").read_text(encoding="utf-8")


def _bloque(inicio: str) -> str:
    i = CSS.index(inicio) + len(inicio)
    nivel, j = 1, i
    while nivel:
        nivel += (CSS[j] == "{") - (CSS[j] == "}")
        j += 1
    return CSS[i:j - 1]


def _variables(texto: str) -> dict[str, str]:
    return dict(re.findall(r"--([a-z0-9-]+):\s*(#[0-9a-fA-F]{6})\b", texto))


CLARO = _variables(CSS[CSS.index(":root {"):CSS.index("@media (prefers-color-scheme: dark)")])
OSCURO = _variables(_bloque(':root[data-theme="dark"] {'))
OSCURO_SISTEMA = _variables(_bloque(':root:not([data-theme="light"]) {'))


def _luz(color: str) -> float:
    canales = [int(color[i:i + 2], 16) / 255 for i in (1, 3, 5)]
    r, g, b = [c / 12.92 if c <= .03928 else ((c + .055) / 1.055) ** 2.4 for c in canales]
    return .2126 * r + .7152 * g + .0722 * b


def contraste(a: str, b: str) -> float:
    mayor, menor = sorted((_luz(a), _luz(b)), reverse=True)
    return (mayor + .05) / (menor + .05)


TEXTOS = [("ink", f) for f in ("bg", "panel", "surface-2", "surface-3", "input-bg", "accent-soft", "note-bg", "proposal-bg", "info-bg")] \
    + [("muted", f) for f in ("bg", "panel", "surface-2", "surface-3")] \
    + [("on-accent", "accent-fill"), ("on-accent", "accent-hover"), ("on-accent", "danger")] \
    + [("accent-dark", f) for f in ("panel", "bg", "accent-soft", "surface-2", "surface-3")] \
    + [("ok-ink", "ok-bg"), ("warn-ink", "warn-bg"), ("caution-ink", "caution-bg"), ("bad-ink", "bad-bg"),
       ("info-ink", "info-bg"), ("blue", "panel"), ("blue", "bg")]
COMPONENTES = [("control-line", f) for f in ("panel", "input-bg", "surface-2")] \
    + [("focus", f) for f in ("bg", "panel", "surface-2")] + [("accent", "panel"), ("accent", "bg")]


# Cubre: U-36
class Contraste(unittest.TestCase):
    def test_las_dos_variantes_oscuras_son_la_misma(self):
        self.assertEqual(OSCURO, OSCURO_SISTEMA)

    def test_cada_par_principal_llega_a_4_5_a_1_y_los_componentes_a_3_a_1(self):
        for nombre, paleta in (("claro", CLARO), ("oscuro", OSCURO)):
            for lista, minimo in ((TEXTOS, 4.5), (COMPONENTES, 3.0)):
                for primero, fondo in lista:
                    with self.subTest(tema=nombre, par=f"{primero} sobre {fondo}"):
                        self.assertGreaterEqual(contraste(paleta[primero], paleta[fondo]), minimo)

    def test_la_paleta_es_la_de_la_marca(self):
        self.assertEqual(CLARO["accent"], "#bd5209")
        self.assertEqual(CLARO["blue"], "#2f4d73")
        self.assertEqual(CLARO["bg"], "#f5f8fb")
        self.assertEqual(OSCURO["bg"], "#0d1117")

    def test_el_naranja_de_marca_y_el_ambar_no_son_color_de_texto(self):
        for paleta in (CLARO, OSCURO):
            for variable in ("ink", "muted", "accent-dark", "on-accent", "ok-ink", "warn-ink", "caution-ink", "bad-ink",
                             "info-ink", "blue"):
                self.assertNotIn(paleta[variable].lower(), ("#f47c27", "#f59e0b"), variable)
        self.assertNotRegex(CSS.lower(), r"color:\s*#(f47c27|f59e0b)")


# Cubre: U-36
class FuenteYLogoPropios(unittest.TestCase):
    def test_se_sirven_desde_el_propio_servidor(self):
        for ruta, tipo, firma in (("/static/inter-latin-wght.woff2", "font/woff2", b"wOF2"),
                                  ("/static/logo-anka.png", "image/png", b"\x89PNG")):
            with self.subTest(ruta=ruta):
                respuesta = request("GET", ruta)
                self.assertEqual(respuesta["status"], 200)
                self.assertEqual(respuesta["type"], tipo)
                self.assertTrue(respuesta["body"].startswith(firma))

    def test_otro_nombre_o_una_ruta_con_saltos_es_404(self):
        for ruta in ("/static/otro.css", "/static/..%2Fstyle.css", "/static/%2e%2e%2f%2e%2e%2fsettings.py"):
            with self.subTest(ruta=ruta):
                self.assertEqual(request("GET", ruta)["status"], 404)

    def test_el_css_carga_inter_con_swap_y_no_usa_terceros(self):
        self.assertIn("@font-face", CSS)
        self.assertIn("font-display: swap", CSS)
        self.assertIn("/static/inter-latin-wght.woff2", CSS)
        self.assertIn('"Segoe UI"', CSS[CSS.index("--font-ui:"):].split(";")[0])
        self.assertNotRegex(CSS.lower(), r"fonts\.googleapis|fonts\.gstatic|https?://")

    def test_la_cabecera_lleva_el_logo(self):
        html = request("GET", "/archive")["body"].decode("utf-8")
        self.assertRegex(html, r'<img class="brand-logo" src="/static/logo-anka\.png" alt="Anka"')
        self.assertIn("Anka, mis casos", html)


# Cubre: U-36
class FormaFocoYMovimiento(unittest.TestCase):
    def test_radios_y_foco(self):
        self.assertIn("--radius-control: .5rem", CSS)
        self.assertIn("--radius-card: .75rem", CSS)
        self.assertIn(":focus-visible { outline: 2px solid var(--focus); outline-offset: 2px; }", CSS)
        self.assertNotIn("outline: 3px", CSS)

    def test_las_animaciones_son_las_definidas_y_se_anulan_al_reducir_movimiento(self):
        self.assertIn("fade-in-up .2s ease-out", CSS)
        self.assertIn("translateY(6px)", CSS)
        self.assertIn("fade-in .15s ease-out", CSS)
        regla = re.search(r"@media \(prefers-reduced-motion: reduce\) \{(.*)\}", CSS).group(1)
        for selector in ("*", "*::before", "*::after"):
            self.assertIn(selector, regla)
        self.assertIn("animation: none !important", regla)
        self.assertIn("transition: none !important", regla)


if __name__ == "__main__":
    unittest.main()
