"""Lo que solo agrega se aplica solo y todo cambio automático deja su detalle (U-96).

Cubre: U-96
"""

from tests import _isolate  # noqa: F401  (debe ir primero)

import unittest

from anka.dominio.tabla_acumulada import integrar


def fila(nombre="Levantamiento de detalles", estado="pendiente", **datos):
    base = {"activity": nombre, "quantity": "", "unit": "", "estado": estado, "ubicacion": "", "entregable": "",
            "plazo_restriccion": "", "servicio_tecnica": "", "notes": "", "fuente": ""}
    return {**base, **datos}


UBICACION = "Manzanas donde están las estaciones del proyecto y vías circundantes"


# Cubre: U-96
class LoQueSoloAgregaSeAplica(unittest.TestCase):
    def test_en_una_fila_sin_confirmar_se_aplica_solo_y_no_pide_revision(self):
        res = integrar([fila(ubicacion=UBICACION, notes="Falta el área")],
                       [fila(ubicacion=UBICACION + " en Calle153.kmz", notes="Falta el área; KMZ recibido")])
        self.assertEqual(res.revisar, [])
        self.assertEqual(res.vigente[0]["ubicacion"], UBICACION + " en Calle153.kmz")
        self.assertEqual(res.vigente[0]["notes"], "Falta el área; KMZ recibido")

    def test_en_una_fila_confirmada_sigue_siendo_una_propuesta(self):
        res = integrar([fila(estado="confirmado", ubicacion=UBICACION)],
                       [fila(estado="confirmado", ubicacion=UBICACION + " en Calle153.kmz")])
        self.assertEqual(res.vigente[0]["ubicacion"], UBICACION)
        self.assertEqual([r["campo"] for r in res.revisar], ["ubicacion"])

    def test_un_texto_distinto_en_una_fila_sin_confirmar_tambien_se_aplica_y_queda_en_el_historial(self):
        res = integrar([fila(ubicacion="Estaciones y a lo largo del trazado", notes="Falta el archivo de trazado")],
                       [fila(ubicacion="Polígono del KMZ y estaciones", notes="Trazado recibido en el KMZ")])
        self.assertEqual(res.revisar, [])
        self.assertEqual(res.vigente[0]["ubicacion"], "Polígono del KMZ y estaciones")
        self.assertIn("Ubicación: «Estaciones y a lo largo del trazado» → «Polígono del KMZ y estaciones»",
                      res.aplicado[0])

    def test_un_cambio_de_cantidad_sigue_pidiendo_revision_aunque_la_fila_no_este_confirmada(self):
        res = integrar([fila(quantity="5", ubicacion="Bogotá")], [fila(quantity="8", ubicacion="Bogotá y La Calera")])
        self.assertEqual(res.vigente[0]["quantity"], "5")
        self.assertEqual([r["campo"] for r in res.revisar], ["quantity"])

    def test_una_edicion_de_una_persona_pendiente_no_se_pisa(self):
        res = integrar([fila(ubicacion=UBICACION)], [fila(ubicacion=UBICACION + " en el KMZ")],
                       frozenset({("levantamiento de detalles", "ubicacion")}))
        self.assertEqual(res.vigente[0]["ubicacion"], UBICACION)


# Cubre: U-96
class ElHistorialGuardaElDetalle(unittest.TestCase):
    def test_lo_aplicado_dice_que_campos_cambiaron_con_el_antes_y_el_ahora(self):
        res = integrar([fila(ubicacion=UBICACION, notes="Falta el área")],
                       [fila(ubicacion=UBICACION + " en Calle153.kmz", notes="KMZ recibido")])
        (linea,) = res.aplicado
        self.assertIn("se actualizó «Levantamiento de detalles»", linea)
        self.assertIn(f"Ubicación: «{UBICACION}» → «{UBICACION} en Calle153.kmz»", linea)
        self.assertIn("Notas: «Falta el área» → «KMZ recibido»", linea)

    def test_un_valor_largo_se_recorta(self):
        largo = "palabra " * 80
        res = integrar([fila(ubicacion="Zona")], [fila(ubicacion="Zona " + largo)])
        self.assertIn("...", res.aplicado[0])
        self.assertLess(len(res.aplicado[0]), 500)


# Cubre: U-96
class ReescrituraMasEstricta(unittest.TestCase):
    def test_dwg_por_pdf_ya_no_se_toma_por_reescritura(self):
        res = integrar([fila(estado="confirmado", entregable="Entregar plano en formato DWG")],
                       [fila(estado="confirmado", entregable="Entregar plano en formato PDF")])
        self.assertEqual([r["campo"] for r in res.revisar], ["entregable"])

    def test_un_texto_que_solo_acorta_se_ignora(self):
        res = integrar([fila(estado="confirmado", servicio_tecnica="Estación total con poligonal cerrada")],
                       [fila(estado="confirmado", servicio_tecnica="Estación total")])
        self.assertEqual(res.revisar, [])

    def test_casi_las_mismas_palabras_se_ignora(self):
        res = integrar([fila(estado="confirmado", servicio_tecnica="Estación total y densificación poligonal cerrada")],
                       [fila(estado="confirmado", servicio_tecnica="Estación total, poligonal cerrada y densificación")])
        self.assertEqual(res.revisar, [])


if __name__ == "__main__":
    unittest.main()
