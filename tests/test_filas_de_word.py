"""T-129: las filas con cantidad de una tabla de Word que la tabla de actividades no recoge."""

from tests import _isolate  # noqa: F401  (debe ir primero)

import unittest

from anka.dominio.filas_de_hoja import actividad_de_fila, filas_de_tablas_de_word, filas_sin_cubrir

TABLA = """# invitacion.docx
Párrafo 1: Se invita a cotizar.
Tabla 1, fila 1: columna 1=ÍTEM | columna 2=DESCRIPCIÓN | columna 3=UNIDAD | columna 4=CANTIDAD
( A ) | columna 5=VALOR UNITARIO
( B ) | columna 6=VALOR TOTAL
Tabla 1, fila 2: columna 1=1.1  PERFORACIONES Y ENSAYOS
Tabla 1, fila 3: columna 1=1.1.1 | columna 2=PRUEBAS DE BOMBEO:
Cada prueba dura 24 horas. | columna 3=UN | columna 4=4 | columna 5= | columna 6=-
Tabla 1, fila 4: columna 1=1.1.2 | columna 2=Tomografía eléctrica 2D | columna 3=UN | columna 4=50 | columna 5= | columna 6=-
Tabla 1, fila 5: columna 1=1.1.3 | columna 2=Adquisición Magnetotelúrica | columna 3=UN | columna 4=0
Tabla 1, fila 6: columna 1=SUBTOTAL | columna 2=- | columna 3=UN | columna 4=1
Tabla 1, fila 7: columna 1=IVA (19%) | columna 2=-
Tabla 2, fila 1: columna 1=REV. | columna 2=DESCRIPCIÓN | columna 3=FECHA
Tabla 2, fila 2: columna 1=0 | columna 2=Emisión final | columna 3=09-03-2021
Tabla 3, fila 1: columna 1=PTO | columna 2=Cantidades de obra | columna 3=NORTE
Tabla 3, fila 2: columna 1=1 | columna 2=Dato | columna 3=7
"""


# Cubre: T-129
class LecturaDeTablasDeWordTests(unittest.TestCase):
    def setUp(self) -> None:
        self.filas = filas_de_tablas_de_word("invitacion.docx", TABLA)

    def test_lee_descripcion_cantidad_y_unidad_bajo_el_encabezado_cantidad(self) -> None:
        self.assertEqual([(f["fila"], f["cantidad"], f["unidad"]) for f in self.filas], [(3, 4.0, "UN"), (4, 50.0, "UN")])
        self.assertEqual(self.filas[1]["descripcion"], "Tomografía eléctrica 2D")
        self.assertEqual((self.filas[1]["hoja"], self.filas[1]["archivo"]), ("Tabla 1", "invitacion.docx"))

    def test_una_celda_con_varias_lineas_sigue_siendo_una_sola_celda(self) -> None:
        self.assertEqual(self.filas[0]["descripcion"], "PRUEBAS DE BOMBEO: Cada prueba dura 24 horas.")

    def test_ignora_cantidad_cero_totales_y_filas_sin_cantidad(self) -> None:
        descripciones = " ".join(f["descripcion"] for f in self.filas)
        for ajeno in ("Magnetotelúrica", "SUBTOTAL", "IVA", "PERFORACIONES"):
            self.assertNotIn(ajeno, descripciones)

    def test_otras_tablas_sin_encabezado_de_cantidad_no_aportan_filas(self) -> None:
        # «Cantidades de obra» no es un encabezado de cantidad; la tabla 2 es un historial de revisiones.
        self.assertEqual([f["hoja"] for f in self.filas], ["Tabla 1", "Tabla 1"])

    def test_una_descripcion_enorme_se_recorta_con_puntos_suspensivos(self) -> None:
        texto = ("Tabla 1, fila 1: columna 1=ITEM | columna 2=DESCRIPCION | columna 3=CANTIDAD\n"
                 f"Tabla 1, fila 2: columna 1=1 | columna 2={'condiciones ' * 200} | columna 3=2\n")
        descripcion = filas_de_tablas_de_word("a.docx", texto)[0]["descripcion"]
        self.assertTrue(descripcion.endswith("…"))
        self.assertLessEqual(len(descripcion), 601)

    def test_el_encabezado_de_la_primera_tabla_no_vale_para_la_siguiente(self) -> None:
        texto = (TABLA.split("Tabla 2,")[0]
                 + "Tabla 2, fila 1: columna 1=1 | columna 2=Cosa con texto | columna 3=UN | columna 4=9\n")
        self.assertEqual([f["hoja"] for f in filas_de_tablas_de_word("a.docx", texto)], ["Tabla 1", "Tabla 1"])


# Cubre: T-129
class FilasDeWordSinCubrirTests(unittest.TestCase):
    ADJUNTOS = [{"filename": "invitacion.docx", "text": TABLA}]

    def test_con_la_tabla_vacia_se_senalan_las_filas_del_word(self) -> None:
        self.assertEqual(len(filas_sin_cubrir(self.ADJUNTOS, [])), 2)

    def test_una_actividad_que_habla_de_lo_mismo_cubre_la_fila(self) -> None:
        pendientes = filas_sin_cubrir(self.ADJUNTOS, [{"activity": "Tomografía eléctrica en el sector", "quantity": "50"}])
        self.assertEqual([f["fila"] for f in pendientes], [3])

    def test_solo_lee_docx_con_tablas(self) -> None:
        self.assertEqual(filas_sin_cubrir([{"filename": "carta.docx", "text": "Párrafo 1: hola"}], []), [])
        self.assertEqual(filas_sin_cubrir([{"filename": "invitacion.pdf", "text": TABLA}], []), [])

    def test_agregar_una_fila_de_word_la_deja_pendiente_con_su_fuente(self) -> None:
        fila = filas_sin_cubrir(self.ADJUNTOS, [])[1]
        actividad = actividad_de_fila(fila)
        self.assertEqual((actividad["activity"], actividad["quantity"], actividad["unit"], actividad["estado"]),
                         ("Tomografía eléctrica 2D", "50", "UN", "pendiente"))
        self.assertEqual(actividad["fuente"], "invitacion.docx · hoja «Tabla 1» · fila 4")
        self.assertTrue(filas_sin_cubrir(self.ADJUNTOS, [actividad]) == [filas_sin_cubrir(self.ADJUNTOS, [])[0]])


if __name__ == "__main__":
    unittest.main()
