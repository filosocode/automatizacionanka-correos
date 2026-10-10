"""La cantidad de una actividad es una cifra; lo que no lo es se deja pendiente. Cubre: F1-03"""

from tests import _isolate  # noqa: F401  (debe ir primero)

import json
import unittest

from anka.dominio.activities import limpiar_cantidad_y_unidad, tiene_cifra
from anka.infra import llm
from anka.servicios import work_import
from tests.test_work_sync import payload


def fila(cantidad, unidad="", **extra):
    return {"activity": "Levantamiento", "quantity": cantidad, "unit": unidad, "estado": "confirmado", "notes": "", **extra}


def limpia(cantidad, unidad="", **extra):
    f = fila(cantidad, unidad, **extra)
    limpiar_cantidad_y_unidad(f)
    return f


class Cifras(unittest.TestCase):
    def test_a_figure_is_a_digit_or_a_common_number_word(self):
        for texto in ("14", "1.250,5", "544,5 ha", "una hectárea", "Cinco km", "mil"):
            with self.subTest(texto=texto):
                self.assertTrue(tiene_cifra(texto))
        for texto in ("Ha", "Área", "varios", "L.S.", "", None):
            with self.subTest(texto=texto):
                self.assertFalse(tiene_cifra(texto))


class UnidadEnLaCantidad(unittest.TestCase):
    def test_a_unit_without_a_figure_leaves_the_quantity_unknown_and_the_row_pending(self):
        f = limpia("Ha", "ha")
        self.assertEqual((f["quantity"], f["unit"], f["estado"]), ("no indicado", "ha", "pendiente"))
        self.assertIn("cifra", f["notes"])

    def test_the_unit_moves_to_its_place_when_it_was_only_in_the_quantity(self):
        f = limpia("Mojón", "")
        self.assertEqual((f["quantity"], f["unit"]), ("no indicado", "Mojón"))

    def test_a_label_without_a_figure_is_not_a_quantity(self):
        for texto in ("Área", "varios", "grande"):
            with self.subTest(texto=texto):
                self.assertEqual(limpia(texto)["quantity"], "no indicado")

    def test_a_quantity_that_already_says_it_is_unknown_is_left_without_extra_notes(self):
        for texto in ("no indicado", "No indicado", "pendiente", "N/A", "por confirmar"):
            with self.subTest(texto=texto):
                f = limpia(texto, "no indicado")
                self.assertEqual((f["estado"], f["notes"]), ("confirmado", ""))

    def test_real_quantities_are_left_alone(self):
        for cantidad, unidad in (("14", "ha"), ("1.250,5", "m"), ("544,5 ha", ""), ("Cinco", "km"), ("1442", "ha")):
            with self.subTest(cantidad=cantidad):
                f = limpia(cantidad, unidad)
                self.assertEqual((f["estado"], f["notes"]), ("confirmado", ""))
                self.assertNotEqual(f["quantity"], "no indicado")


class ReferenciasDeCelda(unittest.TestCase):
    def test_a_cell_reference_written_before_the_value_is_dropped(self):
        for escrito, esperado in (("G9=1", "1"), ("AB12 = 544,5", "544,5"), ("c3=25", "25")):
            with self.subTest(escrito=escrito):
                self.assertEqual(limpia(escrito, "Global")["quantity"], esperado)

    def test_a_cell_reference_with_no_value_is_not_a_quantity(self):
        self.assertEqual(limpia("G9=", "Global")["quantity"], "no indicado")

    def test_a_quantity_that_is_not_a_cell_reference_is_left_as_it_was(self):
        self.assertEqual(limpia("25 km", "")["quantity"], "25 km")     # sin unidad aparte no se toca
        self.assertEqual(limpia("1.250,5", "m")["quantity"], "1.250,5")


class Dinero(unittest.TestCase):
    def test_money_is_never_a_quantity_and_is_not_kept_anywhere(self):
        casos = [("+SUM(G9:G11) [valor guardado: 21980000]", "COP"), ("$ 21.980.000", ""), ("21980000", "COP"),
                 ("1.500", "USD"), ("3 millones de pesos", ""), ("=SUM(A1:A3)", "")]
        for cantidad, unidad in casos:
            with self.subTest(cantidad=cantidad):
                f = limpia(cantidad, unidad)
                self.assertEqual((f["quantity"], f["unit"], f["estado"]), ("no indicado", "no indicado", "pendiente"))
                self.assertIn("dinero", f["notes"])
                self.assertNotIn("21980000", json.dumps(f))
                self.assertNotIn("21.980.000", json.dumps(f))

    def test_a_note_that_was_already_there_is_kept_and_the_new_one_is_added_once(self):
        f = limpia("21980000", "COP", notes="Fuente: hoja 1.")
        limpiar_cantidad_y_unidad(f)
        self.assertTrue(f["notes"].startswith("Fuente: hoja 1."))
        self.assertEqual(f["notes"].count("dinero"), 1)

    def test_hectares_that_merely_look_like_big_numbers_are_not_money(self):
        f = limpia("21980000", "m²")
        self.assertEqual(f["quantity"], "21980000")


class EnLosDosModos(unittest.TestCase):
    def test_work_drafts_get_the_same_protection(self):
        cuerpo = payload(activities=[{"activity": "Topografía", "quantity": "Ha", "unit": "ha", "estado": "confirmado"},
                                     {"activity": "Total", "quantity": "$ 5.000.000", "unit": "", "estado": "confirmado"}])
        filas = work_import._work_activities(cuerpo)
        self.assertEqual([f["quantity"] for f in filas], ["no indicado", "no indicado"])
        self.assertEqual([f["estado"] for f in filas], ["pendiente", "pendiente"])

    def test_the_prompt_tells_the_model_what_goes_in_each_field(self):
        mensaje = {"id": "m", "internalDate": "0", "labelIds": [], "payload": {"headers": [], "mimeType": "text/plain",
                                                                           "body": {"data": ""}}}
        prompt = llm.analysis_prompt([mensaje], [], [])
        self.assertIn("En quantity escribe solo la cifra", prompt)
        self.assertIn("nunca la unidad ni un valor en dinero", prompt)


if __name__ == "__main__":
    unittest.main()
