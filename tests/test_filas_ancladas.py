"""Cada fila de un seguimiento se identifica por su número, no por su nombre (U-94).

Cubre: U-94
"""

from tests import _isolate  # noqa: F401  (debe ir primero)

import unittest

from anka.dominio.tabla_acumulada import anclar_filas, integrar
from anka.infra import llm

VIGENTE = [
    {"activity": "Control Horizontal", "quantity": "5", "unit": "und", "estado": "pendiente"},
    {"activity": "Levantamiento de detalles", "quantity": "", "unit": "", "estado": "pendiente"},
    {"activity": "Inventario de redes húmedas (Bogotá)", "quantity": "300", "unit": "m", "estado": "pendiente"},
]


def nueva(nombre, fila, **datos):
    return {"activity": nombre, "quantity": "", "unit": "", "estado": "pendiente", "fila_existente": fila, **datos}


# Cubre: U-94
class AnclarFilas(unittest.TestCase):
    def test_cada_fila_recupera_el_nombre_de_la_que_actualiza(self):
        filas = [nueva("Control horizontal GNSS", 1), nueva("Detalles topográficos", 2), nueva("Redes en Bogotá", 3)]
        self.assertEqual(anclar_filas(filas, VIGENTE), 3)
        self.assertEqual([f["activity"] for f in filas], [v["activity"] for v in VIGENTE])

    def test_el_campo_fila_existente_nunca_queda_en_la_tabla(self):
        filas = [nueva("Algo", 1), nueva("Nuevo", 0), nueva("Raro", 99)]
        anclar_filas(filas, VIGENTE)
        self.assertTrue(all("fila_existente" not in f for f in filas))

    def test_cero_numero_invalido_o_fuera_de_rango_no_toca_el_nombre(self):
        filas = [nueva("Nueva A", 0), nueva("Nueva B", 99), nueva("Nueva C", -1), nueva("Nueva D", "2"),
                 nueva("Nueva E", True), nueva("Nueva F", None)]
        self.assertEqual(anclar_filas(filas, VIGENTE), 0)
        self.assertEqual([f["activity"] for f in filas], ["Nueva A", "Nueva B", "Nueva C", "Nueva D", "Nueva E",
                                                         "Nueva F"])

    def test_un_numero_repetido_solo_ancla_la_primera(self):
        filas = [nueva("Primera", 1), nueva("Segunda", 1)]
        self.assertEqual(anclar_filas(filas, VIGENTE), 1)
        self.assertEqual([f["activity"] for f in filas], ["Control Horizontal", "Segunda"])

    def test_sin_tabla_vigente_solo_limpia_el_campo(self):
        filas = [nueva("Control horizontal", 1)]
        self.assertEqual(anclar_filas(filas, None), 0)
        self.assertEqual(filas, [{"activity": "Control horizontal", "quantity": "", "unit": "", "estado": "pendiente"}])

    def test_entradas_raras_no_rompen(self):
        self.assertEqual(anclar_filas(None, VIGENTE), 0)
        self.assertEqual(anclar_filas(["texto", None, 3], VIGENTE), 0)
        self.assertEqual(anclar_filas([nueva("X", 1)], [None, {}]), 0)

    def test_la_fila_vigente_sin_nombre_no_ancla(self):
        filas = [nueva("Algo", 1)]
        self.assertEqual(anclar_filas(filas, [{"activity": "", "quantity": "3"}]), 0)
        self.assertEqual(filas[0]["activity"], "Algo")


# Cubre: U-94
class IntegracionConFilasAncladas(unittest.TestCase):
    def test_renombrar_todo_ya_no_crea_filas_nuevas_ni_avisos_de_sustitucion(self):
        filas = [nueva("Georreferenciación GNSS de puntos", 1, quantity="5", unit="parejas", estado="confirmado"),
                 nueva("Levantamiento topográfico con estación", 2),
                 nueva("Catastro de redes húmedas en zona urbana Bogotá", 3, quantity="300", unit="m")]
        anclar_filas(filas, VIGENTE)
        res = integrar(VIGENTE, filas)
        self.assertEqual([f["activity"] for f in res.vigente], [v["activity"] for v in VIGENTE])
        self.assertNotIn("sustitucion_ambigua", [r["tipo"] for r in res.revisar])
        self.assertEqual(res.vigente[0]["estado"], "confirmado")

    def test_una_actividad_realmente_nueva_se_agrega(self):
        filas = [nueva("Batimetría del embalse", 0, quantity="2", unit="km", estado="confirmado")]
        anclar_filas(filas, VIGENTE)
        res = integrar(VIGENTE, filas)
        self.assertEqual(len(res.vigente), 4)


# Cubre: U-94
class EsquemaYPrompt(unittest.TestCase):
    def test_el_esquema_exige_fila_existente_en_cada_actividad(self):
        items = llm.ANALYSIS_SCHEMA["properties"]["activities"]["items"]
        self.assertEqual(items["properties"]["fila_existente"], {"type": "integer"})
        self.assertIn("fila_existente", items["required"])

    def test_la_tabla_vigente_lleva_el_numero_de_cada_fila(self):
        prompt = llm.tabla_vigente_prompt(VIGENTE)
        for numero in (1, 2, 3):
            self.assertIn(f'"fila": {numero}', prompt)

    def test_el_modelo_recibe_todos_los_campos_y_la_orden_de_copiar_lo_que_no_cambia(self):
        fila = {"activity": "Nivelación", "servicio_tecnica": "Nivel electrónico de precisión",
                "plazo_restriccion": "Error máximo 0.008 km", "ubicacion": "Eje del trazado", "entregable": "Carteras"}
        prompt = llm.tabla_vigente_prompt([fila])
        for texto in fila.values():
            self.assertIn(texto, prompt)
        self.assertIn("Copia TAL CUAL", prompt)

    def test_el_prompt_de_seguimiento_ordena_no_repetir_lo_que_el_cliente_ya_contesto(self):
        prompt = llm.tabla_vigente_prompt(VIGENTE)
        self.assertIn("No repitas una pregunta", prompt)
        self.assertIn("no tiene el dato o el archivo", prompt)

    def test_los_numeros_siguen_la_posicion_aunque_una_fila_no_tenga_nombre(self):
        prompt = llm.tabla_vigente_prompt([{"activity": ""}, {"activity": "Segunda"}])
        self.assertIn('"fila": 2', prompt)
        self.assertNotIn('"fila": 1', prompt)


if __name__ == "__main__":
    unittest.main()
