"""Un solo texto por decisión y por acción: una prueba de vocabulario impide que vuelvan los sinónimos. Cubre: U-31"""

import unittest
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[1]
WEB = RAIZ / "anka" / "web"
# Lo retirado y el texto único que lo reemplaza (U-31.24).
RETIRADOS = {
    "marcar como suficiente": "Confirmar que alcanza",
    "redactar de nuevo": "Preparar el correo de nuevo",
    "volver a redactar": "Cambiar las preguntas",
    "volver a elegir": "Volver a decidir",
    "armar correo": "Preparar el correo",
    "enviar ahora": "Enviar al cliente",
    "la información es suficiente": "Con esto alcanza",
}
UNICOS = ("Con esto alcanza", "Confirmar que alcanza", "Hay que preguntarle al cliente", "Preparar el correo",
          "Cambiar las preguntas", "Preparar el correo de nuevo", "Volver a decidir", "No preguntar: con esto alcanza",
          "Enviar al cliente", "Completar la tabla yo")


def textos_de_la_pantalla() -> dict[str, str]:
    archivos = list((WEB / "templates").glob("*.html")) + [WEB / "pantalla_caso.py", WEB / "paginas.py", WEB / "acciones.py", WEB / "views.py"]
    archivos.append(RAIZ / "anka" / "servicios" / "quotes.py")
    return {str(a.relative_to(RAIZ)): a.read_text(encoding="utf-8") for a in archivos}


class Vocabulario(unittest.TestCase):
    def test_no_queda_ningun_sinonimo_retirado(self) -> None:
        # Cubre: U-31
        for archivo, texto in textos_de_la_pantalla().items():
            plano = texto.casefold()
            for viejo, nuevo in RETIRADOS.items():
                with self.subTest(archivo=archivo, retirado=viejo):
                    self.assertNotIn(viejo, plano, f"«{viejo}» ya no se usa: di «{nuevo}»")

    def test_los_textos_unicos_existen_en_la_pantalla_del_caso(self) -> None:
        # Cubre: U-31
        caso = (WEB / "templates" / "case.html").read_text(encoding="utf-8")
        for texto in UNICOS:
            with self.subTest(texto=texto):
                self.assertIn(texto, caso)

    def test_el_dialogo_de_envio_dice_enviar_al_cliente(self) -> None:
        # Cubre: U-31
        caso = (WEB / "templates" / "case.html").read_text(encoding="utf-8")
        self.assertIn('data-confirm-boton="Sí, enviar al cliente"', caso)


if __name__ == "__main__":
    unittest.main()
