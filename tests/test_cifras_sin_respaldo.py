"""Una cifra que ningún documento respalda no queda confirmada (U-93).

Cubre: U-93
"""

from tests import _isolate  # noqa: F401  (debe ir primero)

import unittest
from unittest.mock import patch

from anka.dominio.verificacion_de_evidencia import marcar_cifras_sin_respaldo, textos_del_analisis

PDF = "[Página 1]\nSe requieren 5 parejas de puntos de control a lo largo del eje.\n[Página 5]\nUn tramo de 300 m."


def adjunto(texto=PDF, status="extraido"):
    return {"filename": "Especificaciones.pdf", "status": status, "text": texto}


def fila(nombre, cantidad, unidad, estado="confirmado", fuente="Especificaciones.pdf, Página 1"):
    return {"activity": nombre, "quantity": cantidad, "unit": unidad, "estado": estado, "notes": "", "fuente": fuente}


def analisis(*filas):
    return {"activities": list(filas)}


# Cubre: U-93
class CifrasSinRespaldo(unittest.TestCase):
    def test_una_cifra_que_aparece_en_el_documento_queda_confirmada(self):
        a = analisis(fila("Control horizontal puntos de control", "5", "parejas"))
        self.assertEqual(marcar_cifras_sin_respaldo(a, "", [adjunto()]), [])
        self.assertEqual(a["activities"][0]["estado"], "confirmado")
        self.assertEqual(a["activities"][0]["notes"], "")

    def test_una_cifra_que_ningun_documento_respalda_baja_a_pendiente_con_una_nota(self):
        a = analisis(fila("Control horizontal puntos de control", "9", "parejas"))
        self.assertEqual(marcar_cifras_sin_respaldo(a, "", [adjunto()]), ["Control horizontal puntos de control"])
        self.assertEqual(a["activities"][0]["estado"], "pendiente")
        self.assertIn("no coincide con los documentos", a["activities"][0]["notes"])
        self.assertEqual(a["evidence_check"]["estado"], "con_diferencias")

    def test_lo_que_ya_era_pendiente_no_se_toca(self):
        a = analisis(fila("Control horizontal puntos de control", "9", "parejas", estado="pendiente"))
        self.assertEqual(marcar_cifras_sin_respaldo(a, "", [adjunto()]), [])
        self.assertEqual(a["activities"][0]["notes"], "")

    def test_sin_cantidad_no_hay_nada_que_verificar(self):
        a = analisis(fila("Levantamiento LiDAR", "no indicado", ""))
        self.assertEqual(marcar_cifras_sin_respaldo(a, "", [adjunto()]), [])
        self.assertEqual(a["activities"][0]["estado"], "confirmado")

    def test_con_lectura_ilegible_no_se_concluye_nada(self):
        a = analisis(fila("Control horizontal puntos de control", "9", "parejas"))
        self.assertEqual(marcar_cifras_sin_respaldo(a, "", [adjunto("", status="bloqueado")]), [])
        self.assertEqual(a["activities"][0]["estado"], "confirmado")

    def test_solo_baja_la_fila_afectada(self):
        a = analisis(fila("Control horizontal puntos de control", "5", "parejas"),
                     fila("Catastro de redes", "999", "m", fuente="Especificaciones.pdf, Página 5"))
        marcar_cifras_sin_respaldo(a, "", [adjunto()])
        self.assertEqual([f["estado"] for f in a["activities"]], ["confirmado", "pendiente"])

    def test_un_fallo_de_la_comprobacion_nunca_frena_el_analisis(self):
        a = analisis(fila("Control horizontal puntos de control", "9", "parejas"))
        with patch("anka.dominio.verificacion_de_evidencia.comprobar_tabla", side_effect=RuntimeError("x")):
            self.assertEqual(marcar_cifras_sin_respaldo(a, "", [adjunto()]), [])
        self.assertEqual(a["activities"][0]["estado"], "confirmado")

    def test_el_expediente_distingue_lo_leido_de_lo_parcial_y_lo_ilegible(self):
        textos = textos_del_analisis("hola", [adjunto(), adjunto(status="lectura_parcial"), adjunto("", "bloqueado")])
        self.assertEqual([x["coverage"] for x in textos["adjuntos"]], ["completa", "parcial", "no_legible"])
        self.assertEqual(textos["correo"], "hola")


if __name__ == "__main__":
    unittest.main()
