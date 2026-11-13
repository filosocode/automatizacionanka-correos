"""Ninguna actividad completa con un faltante ni faltante con todo completo (U-104, R12)."""

from tests import _isolate  # noqa: F401  (debe ir primero)

import unittest

from anka.dominio.estado_coherente import datos_que_faltan, reconciliar_estados
from anka.servicios.work_import import _work_activities

COMPLETA = {"activity": "Replanteo", "ubicacion": "Lote 4", "quantity": "12", "unit": "puntos",
            "entregable": "Planos", "plazo_restriccion": "15 días", "notes": "", "estado": "confirmado"}


def fila(**cambios):
    return COMPLETA | cambios


# Cubre: U-104
class CompletaSinFaltantes(unittest.TestCase):
    def test_con_un_dato_esencial_vacio_no_queda_completa(self) -> None:
        for vacio in ("", "no indicado", "No indicado", "N/A", "  "):
            with self.subTest(vacio=vacio):
                filas = [fila(quantity=vacio)]
                self.assertEqual(len(reconciliar_estados(filas)), 1)
                self.assertEqual(filas[0]["estado"], "pendiente")
                self.assertIn("Falta cantidad.", filas[0]["notes"])

    def test_si_la_nota_dice_que_falta_algo_no_queda_completa(self) -> None:
        for nota in ("Falta definir el área exacta de las manzanas. Fuentes revisadas: PDF.",
                     "Queda pendiente confirmar el datum.", "Se mantiene como pendiente la definición del plazo.",
                     "Los parámetros están por definir."):
            with self.subTest(nota=nota):
                filas = [fila(notes=nota)]
                reconciliar_estados(filas)
                self.assertEqual(filas[0]["estado"], "pendiente")
                self.assertEqual(filas[0]["notes"], nota)        # la nota ya decía qué falta: no se agrega otra

    def test_una_negacion_no_es_un_faltante(self) -> None:
        for nota in ("No falta nada: todo definido.", "Sin pendientes.", "Ya no falta ningún dato.",
                     "No hay pendientes con el cliente."):
            with self.subTest(nota=nota):
                filas = [fila(notes=nota)]
                self.assertEqual(reconciliar_estados(filas), [])
                self.assertEqual(filas[0]["estado"], "confirmado")


# Cubre: U-104
class FaltanteConTodoCompleto(unittest.TestCase):
    def test_con_todos_los_datos_y_nada_pendiente_pasa_a_completa(self) -> None:
        for nota in ("", "Parámetros definidos explícitamente.", "Se aportó el KMZ con la ubicación."):
            with self.subTest(nota=nota):
                filas = [fila(estado="pendiente", notes=nota)]
                self.assertEqual(len(reconciliar_estados(filas)), 1)
                self.assertEqual(filas[0]["estado"], "confirmado")
                self.assertIn("Completa:", filas[0]["notes"])

    def test_con_un_dato_vacio_sigue_por_completar(self) -> None:
        filas = [fila(estado="pendiente", plazo_restriccion="no indicado")]
        self.assertEqual(reconciliar_estados(filas), [])
        self.assertEqual(filas[0]["estado"], "pendiente")

    def test_con_dudas_en_la_nota_sigue_por_completar(self) -> None:
        for nota in ("Cantidad aproximada calculada por el programa. Confírmala con el cliente.",
                     "Falta confirmar la ubicación.", "Hay que revisar el alcance.", "Valor estimado por el analista.",
                     "Quedó pendiente la equidistancia de curvas."):
            with self.subTest(nota=nota):
                filas = [fila(estado="pendiente", notes=nota)]
                self.assertEqual(reconciliar_estados(filas), [])
                self.assertEqual(filas[0]["estado"], "pendiente")

    def test_las_fuentes_y_el_siguiente_paso_no_cuentan_como_dudas(self) -> None:
        filas = [fila(estado="pendiente", notes="Definido. Fuentes revisadas: PDF. Siguiente paso: revisar el KMZ.")]
        reconciliar_estados(filas)
        self.assertEqual(filas[0]["estado"], "confirmado")


# Cubre: U-104
class LoQueNoSeToca(unittest.TestCase):
    def test_una_contradictoria_se_queda_como_esta(self) -> None:
        filas = [fila(estado="contradictorio", quantity="no indicado"), fila(estado="contradictorio")]
        self.assertEqual(reconciliar_estados(filas), [])
        self.assertEqual([f["estado"] for f in filas], ["contradictorio", "contradictorio"])

    def test_lo_que_no_es_una_fila_se_ignora(self) -> None:
        filas = ["texto suelto", None, {"estado": "confirmado"}, fila()]
        self.assertEqual(reconciliar_estados(filas), [])

    def test_los_datos_que_faltan_se_nombran_en_orden(self) -> None:
        self.assertEqual(datos_que_faltan(fila(ubicacion="", plazo_restriccion="no indicado")), ["ubicación", "plazo"])
        self.assertEqual(datos_que_faltan(COMPLETA), [])

    def test_el_resultado_es_estable(self) -> None:
        filas = [fila(quantity="no indicado"), fila(estado="pendiente")]
        reconciliar_estados(filas)
        self.assertEqual(reconciliar_estados(filas), [])


# Cubre: U-104
class EnElModoWork(unittest.TestCase):
    def test_el_borrador_de_work_llega_con_el_estado_coherente(self) -> None:
        completa_con_falta = fila(notes="Falta la longitud total del trazado.")
        pendiente_completa = fila(estado="pendiente", activity="Nivelación")
        filas = _work_activities({"activities": [completa_con_falta, pendiente_completa]})
        self.assertEqual([f["estado"] for f in filas], ["pendiente", "confirmado"])


if __name__ == "__main__":
    unittest.main()
