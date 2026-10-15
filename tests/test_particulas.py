"""Nodos animados de fondo (U-38, D-103): canvas oculto a lectores, sin librerías y respetando las preferencias."""

from tests import _isolate  # noqa: F401  (debe ir primero)

import re
import unittest
from pathlib import Path

from anka.web import server as web

PLANTILLAS = Path(web.__file__).with_name("templates")
LAYOUT = (PLANTILLAS / "layout.html").read_text(encoding="utf-8")
CSS = (PLANTILLAS / "style.css").read_text(encoding="utf-8")


def _script() -> str:
    inicio = LAYOUT.index("const lienzo = document.getElementById('particulas')")
    return LAYOUT[inicio:LAYOUT.index("})();", inicio)]


class NodosDeFondo(unittest.TestCase):
    def test_el_canvas_esta_en_el_layout_oculto_a_lectores_y_detras_de_todo(self) -> None:
        # Cubre: U-38
        self.assertEqual(LAYOUT.count('<canvas id="particulas" aria-hidden="true" hidden></canvas>'), 1)
        regla = re.search(r"#particulas \{([^}]*)\}", CSS)
        assert regla
        for propiedad in ("position: fixed", "inset: 0", "z-index: -1", "pointer-events: none"):
            self.assertIn(propiedad, regla.group(1))
        self.assertRegex(CSS, r"@media \(forced-colors: active\), print \{ #particulas \{ display: none; \} \}")

    def test_respeta_movimiento_reducido_visibilidad_y_pantalla(self) -> None:
        # Cubre: U-38
        js = _script()
        self.assertIn("matchMedia('(prefers-reduced-motion: reduce)')", js)
        self.assertRegex(js, r"if \(cuadro \|\| document\.hidden \|\| sinMovimiento\.matches\) return;")
        self.assertIn("sinMovimiento.matches ? apagar() : arrancar()", js)       # cambia en vivo
        self.assertIn("addEventListener('visibilitychange', () => document.hidden ? detener() : arrancar())", js)
        self.assertIn("requestAnimationFrame", js)
        self.assertIn("window.devicePixelRatio", js)
        self.assertIn("window.addEventListener('resize'", js)
        self.assertIn("window.addEventListener('mousemove'", js)                  # el canvas no recibe eventos

    def test_sin_librerias_ni_red(self) -> None:
        # Cubre: U-38
        js = _script()
        for prohibido in ("import ", "fetch(", "XMLHttpRequest", "http://", "https://", "WebSocket", "eval("):
            self.assertNotIn(prohibido, js)
        self.assertNotRegex(LAYOUT, r"<script[^>]+\bsrc=")
        self.assertNotRegex(LAYOUT.lower(), r"tsparticles|particles\.js")

    def test_parametros_del_original(self) -> None:
        # Cubre: U-38
        js = _script()
        for fragmento in ("PARTICULAS = 40", "DISTANCIA_LINEA = 140", "OPACIDAD_LINEA = 0.35", "VELOCIDAD = 0.6",
                          "DISTANCIA_REPULSION = 100", "FPS = 60", "RADIO_MIN = 1.5", "RADIO_MAX = 3.5",
                          "'#2f4d73'", "'#f47c27'", "INTENSIDAD = 0.55"):
            self.assertIn(fragmento, js)
        self.assertRegex(js, r"INTENSIDAD = 0\.[1-6]\d*")        # baja en pantallas de trabajo: nunca a plena fuerza


if __name__ == "__main__":
    unittest.main()
