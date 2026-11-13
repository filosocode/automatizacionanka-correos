"""El aviso «Google desconectado» avisa sin gritar y sigue llevando al botón de Ajustes (U-111)."""
from tests import _isolate  # noqa: F401
import unittest
from pathlib import Path

PLANTILLAS = Path(__file__).resolve().parents[1] / "anka" / "web" / "templates"


class AvisoDeGoogle(unittest.TestCase):
    def test_el_aviso_usa_tono_de_atencion_y_una_sola_linea(self):
        # Cubre: U-111
        css = (PLANTILLAS / "style.css").read_text(encoding="utf-8")
        regla = css[css.index('#google-chip[data-state="stopped"]'):]
        regla = regla[:regla.index("}")]
        self.assertIn("var(--caution-bg)", regla)          # atención, no el rojo de error
        self.assertNotIn("var(--bad-bg)", regla)
        self.assertIn("white-space: nowrap", css[css.index("#google-chip {"):])

    def test_el_aviso_sigue_siendo_un_enlace_a_ajustes(self):
        # Cubre: U-111
        html = (PLANTILLAS / "layout.html").read_text(encoding="utf-8")
        self.assertIn('id="google-chip" class="bridge-chip" href="#ajustes"', html)


if __name__ == "__main__":
    unittest.main()
