"""Un seguimiento no repite filas ni avisa por reformular (U-92).

Cubre: U-92
"""

from tests import _isolate  # noqa: F401  (debe ir primero)

import unittest

from anka.dominio.tabla_acumulada import integrar


def fila(nombre, **datos):
    base = {"activity": nombre, "quantity": "", "unit": "", "estado": "pendiente", "ubicacion": "", "entregable": "",
            "plazo_restriccion": "", "servicio_tecnica": "", "notes": "", "fuente": ""}
    return {**base, **datos}


# Cubre: U-92
class MismaActividadConOtroNombre(unittest.TestCase):
    def test_un_nombre_reformulado_actualiza_la_fila_y_conserva_el_nombre(self):
        antes = [fila("Georreferenciación y materialización de puntos de control horizontal")]
        nueva = [fila("Control horizontal y georreferenciación de puntos de control", quantity="5", unit="parejas")]
        res = integrar(antes, nueva)
        self.assertEqual(len(res.vigente), 1)
        self.assertEqual(res.vigente[0]["activity"], "Georreferenciación y materialización de puntos de control horizontal")
        self.assertEqual(res.vigente[0]["quantity"], "5")
        self.assertEqual(res.revisar, [])

    def test_cada_fila_se_empareja_una_sola_vez_y_gana_la_mas_parecida(self):
        antes = [fila("Catastro e inventario de redes húmedas (Bogotá)"),
                 fila("Catastro e inventario de redes húmedas (La Calera)")]
        nueva = [fila("Inventario y catastro de redes húmedas en zona urbana La Calera", quantity="1.2", unit="km"),
                 fila("Inventario y catastro de redes húmedas en zona urbana Bogotá", quantity="300", unit="m")]
        res = integrar(antes, nueva)
        self.assertEqual([(f["activity"], f["quantity"]) for f in res.vigente],
                         [("Catastro e inventario de redes húmedas (Bogotá)", "300"),
                          ("Catastro e inventario de redes húmedas (La Calera)", "1.2")])

    def test_si_dos_filas_vigentes_compiten_por_el_mismo_nombre_se_revisa(self):
        antes = [fila("Levantamiento topográfico", quantity="5", unit="ha"),
                 fila("Levantamiento altimétrico", quantity="5", unit="ha")]
        res = integrar(antes, [fila("Levantamiento topográfico y altimétrico", quantity="10", unit="ha")])
        self.assertEqual(res.revisar[0]["tipo"], "sustitucion_ambigua")
        self.assertEqual(len(res.propuesta), 3)

    def test_lo_que_sobra_se_junta_por_descarte_si_hay_una_candidata_clara(self):
        antes = [fila("Catastro e inventario de redes húmedas (tramo urbano Bogotá)", quantity="300", unit="m"),
                 fila("Catastro e inventario de redes húmedas (tramo urbano La Calera)", quantity="1.2", unit="km")]
        nueva = [fila("Inventario y catastro de redes húmedas (alcantarillado y acueducto) - Tramo Bogotá",
                      quantity="300", unit="m"),
                 fila("Inventario y catastro de redes húmedas (alcantarillado y acueducto) - Tramo La Calera",
                      quantity="1.2", unit="km")]
        res = integrar(antes, nueva)
        self.assertEqual([f["activity"] for f in res.vigente], [f["activity"] for f in antes])
        self.assertEqual(res.revisar, [])

    def test_un_nombre_corto_contenido_en_el_largo_con_la_misma_cantidad_es_la_misma_actividad(self):
        antes = [fila("Control Horizontal", quantity="5", unit="und"),
                 fila("Levantamiento de detalles"), fila("Control Vertical")]
        nueva = [fila("Control horizontal GNSS", quantity="5", unit="parejas", estado="confirmado"),
                 fila("Levantamiento de detalles topográficos"), fila("Control vertical y nivelación geométrica")]
        res = integrar(antes, nueva)
        self.assertEqual([f["activity"] for f in res.vigente], ["Control Horizontal", "Levantamiento de detalles",
                                                                "Control Vertical"])
        self.assertEqual(res.vigente[0]["estado"], "confirmado")

    def test_el_mismo_nombre_largo_con_otra_cantidad_revisa_la_cantidad(self):
        res = integrar([fila("Control Horizontal", quantity="5", unit="und")],
                         [fila("Control horizontal GNSS", quantity="8", unit="parejas")])
        self.assertEqual(len(res.vigente), 1)
        self.assertEqual([(r["tipo"], r["campo"]) for r in res.revisar], [("dato_distinto", "quantity"),
                                                                          ("dato_distinto", "unit")])

    def test_una_actividad_realmente_nueva_se_agrega(self):
        res = integrar([fila("Batimetría")], [fila("Levantamiento LiDAR aerotransportado y fotogrametría")])
        self.assertEqual(len(res.vigente), 2)


# Cubre: U-92
class MismoDatoEscritoDeOtraForma(unittest.TestCase):
    def antes(self, **datos):
        return [fila("Levantamiento", estado="confirmado", **datos)]

    def test_un_texto_reformulado_con_los_mismos_numeros_no_pide_revision(self):
        res = integrar(self.antes(servicio_tecnica="Estación total y densificación mediante poligonal cerrada"),
                       [fila("Levantamiento", estado="confirmado",
                             servicio_tecnica="Estación total / Poligonal cerrada para densificación")])
        self.assertEqual(res.revisar, [])
        self.assertEqual(res.vigente[0]["servicio_tecnica"], "Estación total y densificación mediante poligonal cerrada")

    def test_un_numero_distinto_si_pide_revision(self):
        res = integrar(self.antes(plazo_restriccion="Entrega en 30 días hábiles"),
                       [fila("Levantamiento", estado="confirmado", plazo_restriccion="Entrega en 45 días hábiles")])
        self.assertEqual([r["campo"] for r in res.revisar], ["plazo_restriccion"])

    def test_lo_que_solo_agrega_palabras_se_propone(self):
        res = integrar(self.antes(entregable="Plano"),
                       [fila("Levantamiento", estado="confirmado", entregable="Plano PDF y DWG")])
        self.assertEqual([r["campo"] for r in res.revisar], ["entregable"])

    def test_la_cantidad_nunca_se_trata_como_reformulacion(self):
        res = integrar(self.antes(quantity="10", unit="ha"),
                       [fila("Levantamiento", estado="confirmado", quantity="12", unit="ha")])
        self.assertEqual([r["campo"] for r in res.revisar], ["quantity"])

    def test_un_texto_totalmente_distinto_sigue_pidiendo_revision(self):
        res = integrar(self.antes(ubicacion="Embalse de Tominé"),
                       [fila("Levantamiento", estado="confirmado", ubicacion="Vereda La Esmeralda, Sopó")])
        self.assertEqual([r["campo"] for r in res.revisar], ["ubicacion"])


if __name__ == "__main__":
    unittest.main()
