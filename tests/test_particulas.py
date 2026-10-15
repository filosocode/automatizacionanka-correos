"""Nodos animados de fondo (U-38, D-103): canvas oculto a lectores, sin librerías y respetando las preferencias."""

from tests import _isolate  # noqa: F401  (debe ir primero)

import json
import re
import shutil
import subprocess
import unittest
from pathlib import Path

from anka.web import server as web

PLANTILLAS = Path(web.__file__).with_name("templates")
LAYOUT = (PLANTILLAS / "layout.html").read_text(encoding="utf-8")
CSS = (PLANTILLAS / "style.css").read_text(encoding="utf-8")


def _script() -> str:
    inicio = LAYOUT.index("// <física>")
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
        self.assertNotRegex(LAYOUT.lower(), r"cdn\.|unpkg|jsdelivr|cdnjs|importscripts")

    def test_parametros_del_original(self) -> None:
        # Cubre: U-38
        js = _script()
        for fragmento in ("cantidad: 40", "densidad: 2", "areaBase: 1920 * 1080", "radioMin: 1.5", "radioMax: 3.5", "distanciaLinea: 140",
                          "velocidad: 0.6", "repulsion: 100", "factorRepulsion: 100", "maxEmpuje: 50", "fps: 60", "maxDt: 100",
                          "OPACIDAD = 0.6", "OPACIDAD_LINEA = 0.35", "'#2f4d73'", "'#f47c27'", "1 - (1 - t) * (1 - t)"):
            self.assertIn(fragmento, js)
        self.assertRegex(js, r"INTENSIDAD = 0\.[5-9]\d*,")      # baja, pero la repulsión se ve; nunca a plena fuerza
        self.assertIn("60 * Math.min(transcurrido, NODOS.maxDt) / 1000", js)      # el movimiento sigue al reloj y no da saltos
        self.assertIn("ultimo = performance.now();", js)                          # reloj nuevo al volver de una pestaña oculta
        self.assertIn("document.documentElement.addEventListener('mouseleave'", js)
        self.assertIn("window.addEventListener('blur'", js)


class FisicaDeLosNodos(unittest.TestCase):
    """Ejecuta la física del navegador en Node y la compara con la matemática de tsparticles 3.9.1 (D-103)."""

    @classmethod
    def setUpClass(cls) -> None:
        node = shutil.which("node")
        if not node:
            raise unittest.SkipTest("Node no está instalado: la física se prueba solo donde hay Node")
        prueba = Path(__file__).with_name("fisica_nodos_prueba.js")
        corrida = subprocess.run([node, str(prueba)], capture_output=True, text=True, encoding="utf-8", timeout=60)
        if corrida.returncode:
            raise AssertionError(corrida.stderr)
        cls.medidas = json.loads(corrida.stdout)

    def test_la_velocidad_sigue_al_reloj_y_no_a_los_cuadros(self) -> None:
        # Cubre: U-38
        v = self.medidas["px_por_segundo"]
        for fps in ("f60", "f30", "f144"):
            self.assertAlmostEqual(v[fps], v["esperado"], places=6)      # 0,6 · 60 / 2 = 18 px/s con cualquier frecuencia
        self.assertAlmostEqual(v["esperado"], 18.0)

    def test_el_cuadro_tardio_no_da_saltos(self) -> None:
        # Cubre: U-38
        self.assertLess(self.medidas["tope_dt"]["px_maximos"], 2)         # un cuadro de 100 ms mueve 1,8 px, no 90

    def test_rebota_sin_salirse_y_conserva_la_rapidez(self) -> None:
        # Cubre: U-38
        r = self.medidas["rebote"]
        self.assertEqual(r["fuera"], 0)
        self.assertAlmostEqual(r["rapidezMin"], 1.0, places=6)
        self.assertAlmostEqual(r["rapidezMax"], 1.0, places=6)

    def test_la_repulsion_es_la_del_original(self) -> None:
        # Cubre: U-38
        for fila in self.medidas["empuje"]:
            self.assertAlmostEqual(fila["mide"], fila["ref"], places=6, msg=f"a {fila['d']} px")
        por_distancia = {fila["d"]: fila["mide"] for fila in self.medidas["empuje"]}
        self.assertEqual(por_distancia[50], 50)          # dentro de ~70 px el empuje es el máximo: 50 px por cuadro
        self.assertEqual(por_distancia[100], 0)          # fuera del radio no pasa nada

    def test_con_el_raton_quieto_los_nodos_salen_del_radio(self) -> None:
        # Cubre: U-38
        q = self.medidas["ratón_quieto"]
        self.assertEqual(q["dentroAntes"], 12)
        self.assertGreaterEqual(q["minDistanciaDespues"], 100)
        self.assertGreater(q["maxMovimientoPorCuadro"], 40)       # el empuje se nota: salta decenas de píxeles

    def test_cantidad_de_nodos_segun_la_pantalla(self) -> None:
        # Cubre: U-38
        c = self.medidas["cantidad"]
        self.assertEqual((c["1920x1080"], c["1568x662"], c["3840x2160"]), (80, 40, 320))
        self.assertEqual(c["360x640"], 16)                        # mínimo propio: con 8 apenas se vería nada
        self.assertEqual(self.medidas["ajustar"], [80, 18, 80])


if __name__ == "__main__":
    unittest.main()
