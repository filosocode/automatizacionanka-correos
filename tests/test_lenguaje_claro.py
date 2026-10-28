"""Lenguaje claro: sin jerga en la pantalla y mensajes que dicen qué pasó y qué hacer (U-35, D-100)."""

from tests import _isolate  # noqa: F401  (debe ir primero)

import re
import unittest
from pathlib import Path
from unittest.mock import patch

from anka.dominio.errores import ErrorDeAnka
from anka.dominio.status import QuoteStatus
from anka.servicios import quotes
from anka.web import acciones, paginas, pantalla_caso
from anka.web import formularios_pendientes as pendientes
from tests.js_de_las_paginas import JS
from tests.test_pantalla_por_pasos import Base

RAIZ = Path(__file__).resolve().parents[1]
PLANTILLAS = RAIZ / "anka" / "web" / "templates"
# Palabras de jerga que ya no se ven (el identificador `thread_id`, la ruta /google/reautorizar y los correos al
# cliente de R11 no cuentan). Cada una va con lo que se dice en su lugar.
RETIRADAS = {
    "Reautorizar": "Volver a conectar Google",
    "Detalle técnico": "Más datos",
    "Enrutamiento": "De dónde vino el correo",
    "Evaluación de recepción": "Primera revisión del correo",
    "Contexto comercial interno": "Lo que sabemos del cliente",
    "Hilo no disponible": "Ya no está en Gmail",
    "Top 6": "Los 6 más pedidos",
    "Puente activo": "Revisión de correos: activa",
    "Revisar ahora": "Buscar correos nuevos",
    "Actualizar correo": "Buscar correos nuevos",
    "Integridad": "Registro intacto",
}


def texto_de(carpeta: Path) -> dict[str, str]:
    return {a.name: a.read_text(encoding="utf-8") for a in carpeta.glob("*.html")}


class PalabrasRetiradas(unittest.TestCase):
    def test_ninguna_plantilla_dice_hilo_ni_las_palabras_retiradas(self) -> None:
        # Cubre: U-35
        for nombre, texto in texto_de(PLANTILLAS).items():
            sin_ids = re.sub(r"thread_id|hilo_id", "", texto)
            with self.subTest(plantilla=nombre, palabra="hilo"):
                self.assertIsNone(re.search(r"\bhilos?\b", sin_ids, re.I), f"{nombre}: di «conversación» o «correo»")
            for vieja, nueva in RETIRADAS.items():
                with self.subTest(plantilla=nombre, palabra=vieja):
                    # La ruta del botón de Google conserva su nombre; solo cuenta el texto que se lee.
                    legible = sin_ids.replace("/google/reautorizar", "").replace('id="google-reauth"', "")
                    self.assertNotIn(vieja, legible, f"{nombre}: «{vieja}» ya no se usa: di «{nueva}»")

    def test_lo_unico_de_d97_y_el_envio_siguen_en_el_caso(self) -> None:
        # Cubre: U-35
        caso = (PLANTILLAS / "case.html").read_text(encoding="utf-8")
        for texto in ("Con esto alcanza", "Hay que preguntarle al cliente", "Enviar al cliente", "Preparar el correo",
                      'data-confirm-boton="Sí, enviar al cliente"'):
            with self.subTest(texto=texto):
                self.assertIn(texto, caso)

    def test_el_glosario_conserva_sus_cuatro_terminos_y_explica_con_palabras_simples(self) -> None:
        # Cubre: U-35
        glosario = (PLANTILLAS / "stepper.html").read_text(encoding="utf-8")
        for termino in ("<dt>T</dt>", "<dt>T&amp;E</dt>", "<dt>Lectura realizada</dt>", "<dt>Aplicabilidad</dt>"):
            self.assertIn(termino, glosario)
        self.assertIn("¿Qué significan T y T&amp;E?", glosario)

    def test_los_valores_del_contrato_no_cambian(self) -> None:
        # Cubre: U-35, R12
        from anka.dominio.activities import ESTADOS
        self.assertEqual(set(ESTADOS), {"confirmado", "pendiente", "contradictorio"})


class EncabezadoYAvisos(unittest.TestCase):
    def test_los_dos_modos_ofrecen_buscar_correos_nuevos(self) -> None:
        # Cubre: U-35
        for modo in ("local", "chatgpt_work"):
            with self.subTest(modo=modo):
                pagina = paginas.render_page("t", "x", "", 0, modo)
                self.assertIn("Buscar correos nuevos", pagina)
                self.assertNotIn("Revisar ahora", pagina)

    def test_los_avisos_de_la_revision_de_correos_no_dicen_puente(self) -> None:
        # Cubre: U-35
        pagina = paginas.render_page("t", "x", "", 0, "chatgpt_work") + JS     # los avisos los escribe el guion (U-49)
        for frase in ("Revisión de correos: activa", "Revisión de correos: con avisos", "Revisión de correos: detenida",
                      "Revisión de correos: sin iniciar", "Volver a conectar"):
            with self.subTest(frase=frase):
                self.assertIn(frase, pagina)
        self.assertNotIn("Puente", pagina)

    def test_el_boton_de_google_dice_que_pasa_y_sigue_pidiendo_confirmar(self) -> None:
        # Cubre: U-35
        pagina = paginas.render_page("t", "x", "", 0, "local")
        self.assertIn('action="/google/reautorizar"', pagina)
        self.assertIn(">Volver a conectar Google</button>", pagina)
        self.assertIn("Se abrirá una ventana de Google. Entra con tu cuenta y pulsa «Permitir». ¿Seguimos?", pagina)


class MensajesConSalida(unittest.TestCase):
    def setUp(self) -> None:
        pendientes.limpiar("x")

    def test_un_error_de_python_no_se_muestra_tal_cual(self) -> None:
        # Cubre: U-35
        def falla() -> str:
            raise ValueError("invalid literal for int() with base 10: 'abc'")
        mensaje = acciones._guidance(falla)
        self.assertNotIn("invalid literal", mensaje)
        self.assertIn("Lo que escribiste sigue aquí", mensaje)

    def test_un_mensaje_escrito_por_anka_se_muestra(self) -> None:
        # Cubre: U-35
        def falla() -> str:
            raise ErrorDeAnka("Este caso ya no existe. Vuelve a Mis casos.")
        self.assertEqual(acciones._guidance(falla), "Este caso ya no existe. Vuelve a Mis casos.")

    def test_los_mensajes_de_decision_dicen_que_hacer(self) -> None:
        # Cubre: U-35
        with patch.object(quotes, "connect"):
            with self.assertRaisesRegex(ValueError, "Recarga la página"):
                quotes.volver_a_decidir("t-1", "borrar-todo")
        with self.assertRaisesRegex(ValueError, "Recarga la página"):
            quotes.resolve_technical_review("t-1", "otra")

    def test_borrar_casos_habla_de_casos_y_dice_como_seguir(self) -> None:
        # Cubre: U-35
        self.assertIn("Marca uno o más", quotes.delete_quotes([]))

    def test_los_mensajes_del_servicio_no_llevan_el_identificador_interno(self) -> None:
        # Cubre: U-35
        fuente = (RAIZ / "anka" / "servicios" / "case_actions.py").read_text(encoding="utf-8")
        for linea in fuente.splitlines():
            if "return " in linea or "raise " in linea:
                with self.subTest(linea=linea.strip()):
                    self.assertNotIn("type(exc).__name__", linea)
                    self.assertNotRegex(linea, r'f"\{thread_id\}:')


class UnSoloNombreParaListo(Base):
    def test_un_caso_en_ready_sin_codigo_se_llama_informacion_validada_en_la_insignia(self) -> None:
        # Cubre: U-35
        self.crear("t-r", estado=QuoteStatus.READY)
        pagina = pantalla_caso.render_case("t-r")
        cabecera = pagina[pagina.index('class="caso-cabecera"'):pagina.index('class="next-step')]
        self.assertIn("Información validada", cabecera)
        self.assertNotIn("Proyecto creado", cabecera)
        self.assertEqual(paginas.case_stage(quotes.get_quote("t-r"))[0], "Información validada")


if __name__ == "__main__":
    unittest.main()
