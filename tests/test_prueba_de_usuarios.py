"""Lo que las pruebas con usuarios simulados pidieron aclarar en la pantalla del caso. Cubre: U-112"""

from tests import _isolate  # noqa: F401  (debe ir primero)

import unittest
from pathlib import Path

PLANTILLAS = Path(__file__).resolve().parent.parent / "anka" / "web" / "templates"


def _leer(nombre: str) -> str:
    return (PLANTILLAS / nombre).read_text(encoding="utf-8")


class AclaracionesDeLaPantalla(unittest.TestCase):
    def test_antes_de_enviar_se_dice_que_pasa_despues(self) -> None:
        # Cubre: U-112
        self.assertIn("el caso queda en «Esperando al cliente»", _leer("case.html"))
        self.assertIn("filas.push(['Después'", _leer("app.js"))

    def test_no_cuadra_explica_sus_dos_salidas(self) -> None:
        # Cubre: U-112
        editor = _leer("activities_editor.html")
        self.assertIn("Si tú sabes cuál vale, toca «Resolver»", editor)
        self.assertIn("marca «Preguntar» y el cliente lo aclara", editor)

    def test_con_esto_alcanza_se_explica(self) -> None:
        # Cubre: U-112
        self.assertIn("«Con esto alcanza» quiere decir: no falta nada", _leer("case.html"))

    def test_el_foco_va_al_titulo_del_panel_y_vuelve_al_boton(self) -> None:
        # Cubre: U-112
        js = _leer("app.js")
        self.assertIn("origenDelPanel = boton || null;", js)
        self.assertIn("titulo.tabIndex = -1; titulo.focus(", js)
        self.assertIn("vuelta.focus(", js)

    def test_la_marca_con_numero_dice_que_cuenta(self) -> None:
        # Cubre: U-112
        self.assertIn("con novedades por revisar'>", _leer("layout.html"))
        self.assertIn("cuenta.title = n +", _leer("app.js"))

    def test_el_aviso_de_google_dice_que_hacer(self) -> None:
        # Cubre: U-112
        self.assertIn("Toca aquí y entra con tu cuenta de Google", _leer("style.css"))

    def test_el_respaldo_pide_menos_esfuerzo_sin_perder_la_regla(self) -> None:
        # Cubre: U-112
        editor = _leer("activities_editor.html")
        self.assertIn('class="medio"', editor)            # el medio se elige con botones, no con menú
        self.assertIn('name="fecha" value="{{ hoy }}"', editor)   # la fecha ya viene puesta en hoy
        self.assertNotIn('<select name="medio">', editor)
        js = _leer("app.js")
        self.assertIn("(mínimo 10 letras)", js)           # la regla de la frase no se toca
        self.assertIn("Elige por qué medio te lo dijo el cliente", js)


if __name__ == "__main__":
    unittest.main()
