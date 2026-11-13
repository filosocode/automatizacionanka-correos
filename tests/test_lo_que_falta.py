"""«Qué hace falta» se dice corto y para el comercial, sin fuentes, archivos ni pasos internos (U-103.2)."""

from tests import _isolate  # noqa: F401  (debe ir primero)

import unittest

from anka.dominio.lo_que_falta import LARGO_MAXIMO, lo_pendiente, lo_que_dice_la_nota

LIDAR = ("Se aportó el archivo de ubicación Calle153_BF_100 (1).kmz. Se mantiene como pendiente la definición de la "
         "equidistancia de curvas de nivel (0.50 m o 0.20 m), la cual el cliente indicó que se encuentra definiendo. "
         "Fuentes revisadas: Correo 1a1c0da72620dc44, PDF (pág. 3-5) y Calle153_BF_100 (1).kmz. Cantidad = área "
         "aproximada calculada por el programa del polígono «0»; no la declaró el cliente. Confírmala con el cliente.")
NIVELACION = ("Falta la longitud total del trazado a nivelar. Fuentes revisadas: PDF y KMZ. Siguiente paso: revisión "
              "técnica interna para determinar si la longitud se calcula del polígono o solicitar precisión al cliente.")


# Cubre: U-103
class LoQueFalta(unittest.TestCase):
    def test_lo_ya_recibido_no_sale_y_lo_pendiente_se_dice_como_falta(self) -> None:
        self.assertEqual(lo_pendiente(LIDAR), "Falta definir la equidistancia de curvas de nivel (0.50 m o 0.20 m).")

    def test_las_fuentes_y_el_siguiente_paso_no_salen(self) -> None:
        self.assertEqual(lo_pendiente(NIVELACION), "Falta la longitud total del trazado a nivelar.")
        for texto in (lo_pendiente(NIVELACION), lo_que_dice_la_nota(NIVELACION), lo_que_dice_la_nota(LIDAR)):
            for tecnico in ("Fuente", "Siguiente paso", "KMZ", ".kmz", "revisión técnica"):
                self.assertNotIn(tecnico, texto)

    def test_queda_pendiente_se_vuelve_falta(self) -> None:
        self.assertEqual(lo_pendiente("Queda pendiente confirmar el datum."), "Falta confirmar el datum.")

    def test_una_contradiccion_se_conserva_sin_lo_interno(self) -> None:
        self.assertEqual(lo_que_dice_la_nota("El correo dice 10 ha y el KMZ 12 ha. Fuentes revisadas: correo."),
                         "El correo dice 10 ha y el KMZ 12 ha.")

    def test_si_solo_cuenta_lo_recibido_no_hay_nada_que_decir(self) -> None:
        self.assertEqual(lo_pendiente("Se recibió el KMZ."), "")
        self.assertEqual(lo_que_dice_la_nota("Se recibió el KMZ."), "")
        self.assertEqual(lo_pendiente(""), "")

    def test_nunca_pasa_del_largo_maximo(self) -> None:
        self.assertLessEqual(len(lo_pendiente("Falta " + "definir el alcance del trazado " * 20)), LARGO_MAXIMO + 1)


if __name__ == "__main__":
    unittest.main()
