"""«Preguntar al cliente» en un solo paso: el correo sale ya escrito con las preguntas marcadas. Cubre: U-113"""

from tests import _isolate  # noqa: F401  (debe ir primero)

import unittest
from unittest.mock import patch

from anka.dominio.errores import ErrorDeValidacion
from anka.dominio.status import QuoteStatus
from anka.servicios import preguntar_y_enviar as modulo
from anka.servicios.quotes import get_quote
from anka.web.correo_marcado import correo_de_preguntas_marcadas
from tests.test_pantalla_por_pasos import Base

CORREO = "Hola, para cotizar necesitamos:\n1. ¿Cuál es el área en hectáreas?\n\nGracias."


class PreguntarYEnviar(Base):
    def test_decide_y_envia_en_un_solo_paso(self) -> None:
        # Cubre: U-113
        self.crear()
        with patch.object(modulo, "send_dashboard_reply", return_value="enviado") as envio:
            respuesta = modulo.preguntar_y_enviar("t-1", CORREO, "cliente@empresa.co", "¿Cuál es el área?")
        self.assertEqual(respuesta, "enviado")
        envio.assert_called_once_with("t-1", CORREO, "cliente@empresa.co")
        self.assertNotEqual(get_quote("t-1").status, QuoteStatus.REVIEW)

    def test_como_borrador_no_envia(self) -> None:
        # Cubre: U-113
        self.crear()
        with patch.object(modulo, "send_dashboard_reply") as envio, \
                patch.object(modulo, "save_dashboard_draft", return_value="guardado") as borrador:
            respuesta = modulo.preguntar_y_enviar("t-1", CORREO, "a@b.co", "¿Área?", borrador=True)
        self.assertEqual(respuesta, "guardado")
        envio.assert_not_called()
        borrador.assert_called_once()

    def test_sin_preguntas_o_sin_texto_no_toca_el_caso(self) -> None:
        # Cubre: U-113
        self.crear()
        for correo, preguntas in ((CORREO, "  "), ("  ", "¿Área?")):
            with self.assertRaises(ErrorDeValidacion):
                modulo.preguntar_y_enviar("t-1", correo, "a@b.co", preguntas)
        self.assertEqual(get_quote("t-1").status, QuoteStatus.REVIEW)

    def test_caso_que_cambio_no_se_envia(self) -> None:
        # Cubre: U-113
        self.crear()
        with patch.object(modulo, "send_dashboard_reply") as envio:
            with self.assertRaises(ErrorDeValidacion) as error:
                modulo.preguntar_y_enviar("t-1", CORREO, "a@b.co", "¿Área?", estado_visto="otra-marca")
        envio.assert_not_called()
        self.assertIn("cambió", str(error.exception))
        self.assertEqual(get_quote("t-1").status, QuoteStatus.REVIEW)

    def test_si_el_envio_falla_la_decision_queda_y_el_aviso_lo_dice(self) -> None:
        # Cubre: U-113
        from anka.dominio.errores import ErrorDeAnka
        self.crear()
        with patch.object(modulo, "send_dashboard_reply", side_effect=ErrorDeAnka("Gmail no responde")):
            with self.assertRaises(ErrorDeValidacion) as error:
                modulo.preguntar_y_enviar("t-1", CORREO, "a@b.co", "¿Área?")
        self.assertIn("quedó guardada", str(error.exception))
        self.assertIn("Gmail no responde", str(error.exception))


class CorreoDeLasMarcadas(Base):
    def test_arma_la_lista_numerada_con_las_preguntas(self) -> None:
        # Cubre: U-113
        self.crear()
        resultado = correo_de_preguntas_marcadas("t-1", ["¿Cuál es el área?", "¿Dónde queda el predio?"])
        self.assertIsNotNone(resultado)
        self.assertIn("1. ", resultado["cuerpo"])
        self.assertIn("2. ", resultado["cuerpo"])
        self.assertEqual(len(resultado["lineas"]), 2)

    def test_caso_inexistente_devuelve_nada(self) -> None:
        # Cubre: U-113
        self.assertIsNone(correo_de_preguntas_marcadas("no-existe", ["¿Área?"]))


if __name__ == "__main__":
    unittest.main()
