"""T-105: el evaluador de laboratorio normaliza números en letras, reconoce rangos de celdas y busca restricciones respaldadas en fields.

Cada corrección tiene su contraparte: lo que NO debe pasar (una omisión verdadera, un rango enorme, una hoja equivocada, un dato sin fuente).
No toca `anka/`, el contrato ni PRECISO_V6.1.3.
"""

from __future__ import annotations

from tests import _isolate  # noqa: F401  (debe ir primero)

import copy
import unittest

from laboratorio.banco_documental import comparador as v1
from laboratorio.banco_documental import comparador_v2 as v2
from laboratorio.banco_documental import evaluador_inverso as ev
from tests.test_evaluador_inverso import AUTORIZADO, DOCUMENTOS, _bytes, _codigos, _documento, _referencia, retorno_sintetico


def evaluar(caso: str, payload: dict, **extra: object) -> dict:
    return ev.evaluar_retorno(_bytes(payload), AUTORIZADO, _referencia(caso), _documento(caso), DOCUMENTOS[caso],
                              origen_del_retorno="sintetico", **extra)


class NumerosEnLetrasTest(unittest.TestCase):
    # Cubre: T-105
    def test_01_siete_equivale_a_7_en_el_texto_y_en_los_terminos(self) -> None:
        self.assertTrue(v2.coincide(["Conciliar seis lotes frente a siete predios"], [["7"], ["lote", "predio"]]))
        self.assertTrue(v2.coincide(["hay 7 predios y 6 lotes"], [["siete"], ["predio"]]))

    # Cubre: T-105
    def test_02_otro_numero_u_otra_palabra_no_coincide(self) -> None:
        self.assertFalse(v2.coincide(["ocho predios"], [["7"], ["predio"]]))
        self.assertFalse(v2.coincide(["la dosis de siete"], [["2"]]))              # «dosis» no es «dos»
        self.assertFalse(v2.coincide(["uno y cero"], [["1"]]))                      # «uno» y «cero» no se tocan
        self.assertFalse(v2.coincide(["siete lotes"], [["7"], ["predio"]]))        # falta el segundo grupo: sigue exigiendo todos

    # Cubre: T-105
    def test_03_sin_numeros_en_letras_el_comportamiento_es_el_de_siempre(self) -> None:
        self.assertTrue(v2.coincide(["Área 41,8 ha rige"], [["41,8"], ["rige"]]))
        self.assertFalse(v2.coincide([], [["x"]]))
        self.assertFalse(v2.coincide(["x"], []))


class RangosTest(unittest.TestCase):
    # Cubre: T-105
    def test_10_celda_dentro_de_un_rango_pequeno(self) -> None:
        for celda, rango, esperado in (("C4", "B4:E4", True), ("C4", "C4", True), ("C4", "B3:D5", True), ("C4", "$B$4:$E$4", True),
                                       ("C4", "D4:E4", False), ("C4", "A1:B3", False), ("C4", "A5:C9", False), ("C4", "B4:B4", False)):
            self.assertEqual(v1.celda_en_rango(celda, rango), esperado, (celda, rango))

    # Cubre: T-105
    def test_11_un_rango_enorme_o_invalido_no_localiza_nada(self) -> None:
        for rango in ("A1:Z999", "A1:G8:H9", "Resumen", "", "4:4"):
            self.assertFalse(v1.celda_en_rango("C4", rango), rango)
        self.assertFalse(v1.celda_en_rango("no es celda", "A1:B2"))

    # Cubre: T-105
    def test_12_el_localizador_exige_la_hoja_correcta(self) -> None:
        esperado = {"hoja": "Resumen", "celda": "C4"}
        self.assertTrue(v1._origen_ok(esperado, {"localizadores": [["Resumen", "B4:E4"]]}))
        self.assertTrue(v1._origen_ok(esperado, {"localizadores": [["Detalle", "A2:D8"], ["Resumen", "B4:E4"]]}))
        self.assertFalse(v1._origen_ok(esperado, {"localizadores": [["Borrador_anterior", "B4:E4"]]}))
        self.assertFalse(v1._origen_ok(esperado, {"localizadores": [["Resumen", "D4:E4"]]}))

    # Cubre: T-105
    def test_13_el_localizador_de_siempre_no_cambia(self) -> None:
        self.assertTrue(v1._origen_ok({"hoja": "Resumen", "celda": "C4"}, {"hoja": "Resumen", "celda": "C4"}))
        self.assertFalse(v1._origen_ok({"hoja": "Resumen", "celda": "C4"}, {"hoja": "Resumen", "celda": "C5"}))
        self.assertFalse(v1._origen_ok({"pagina": 1}, {}))

    # Cubre: T-105
    def test_14_parsear_fuente_con_hoja_y_rango(self) -> None:
        self.assertEqual(ev.parsear_fuente("c.xlsx, Resumen!C4"), ("c.xlsx", {"hoja": "Resumen", "celda": "C4"}, []))
        archivo, origen, sobrantes = ev.parsear_fuente("c.xlsx, Resumen!B4:E4; Detalle!A2:D8")
        self.assertEqual((archivo, origen, sobrantes), ("c.xlsx", {"localizadores": [["Resumen", "B4:E4"], ["Detalle", "A2:D8"]]}, []))
        self.assertEqual(ev.parsear_fuente("a.pdf, p. 2, tabla 1, fila 3")[1], {"pagina": 2, "tabla": "1", "fila": "3"})
        _, origen, sobrantes = ev.parsear_fuente("c.xlsx, Resumen!B4:E4; texto libre")
        self.assertEqual((origen, sobrantes), ({}, ["Resumen!B4:E4; texto libre"]))

    # Cubre: T-105
    def test_15_rango_en_la_fuente_ya_no_genera_localizador_incorrecto_y_una_hoja_equivocada_si(self) -> None:
        payload = retorno_sintetico("C3")
        for fila, rango in zip(payload["activities"], ("Resumen!B4:E4", "Resumen!B5:E5", "Resumen!B6:E6; Supuestos!A2:C2", "Resumen!B7:E7"), strict=True):
            fila["fuente"] = f"cantidades_03.xlsx, {rango}"
        self.assertNotIn("localizador_ausente_o_incorrecto", _codigos(evaluar("C3", payload)))
        payload["activities"][0]["fuente"] = "cantidades_03.xlsx, Detalle!B4:E4"
        informe = evaluar("C3", payload)
        self.assertEqual([h["ref"] for h in informe["hallazgos"] if h["codigo"] == "localizador_ausente_o_incorrecto"], ["C3-A1"])

    # Cubre: T-105
    def test_16_una_actividad_citada_solo_a_una_hoja_no_vigente_se_descarta(self) -> None:
        payload = retorno_sintetico("C3")
        payload["activities"][0]["fuente"] = "cantidades_03.xlsx, Borrador_anterior!C2"
        informe = evaluar("C3", payload, hojas_no_vigentes=frozenset({"Borrador_anterior"}))
        self.assertEqual(len(informe["actividades_descartadas"] if "actividades_descartadas" in informe else informe.get("descartadas", [])), 1)
        self.assertIn("actividad_omitida", _codigos(informe))
        payload["activities"][0]["fuente"] = "cantidades_03.xlsx, Borrador_anterior!C2; Resumen!C4"
        informe = evaluar("C3", payload, hojas_no_vigentes=frozenset({"Borrador_anterior"}))
        self.assertNotIn("actividad_omitida", _codigos(informe))


class RestriccionesEnFieldsTest(unittest.TestCase):
    def base(self) -> dict:
        payload = retorno_sintetico("C3")
        payload["activities"][0].pop("plazo_restriccion")          # las restricciones ya no están en la columna de la tabla
        return payload

    def omitidas(self, payload: dict) -> set[str]:
        return {h["ref"] for h in evaluar("C3", payload)["hallazgos"] if h["codigo"] == "restriccion_omitida"}

    # Cubre: T-105
    def test_20_sin_restricciones_en_ninguna_parte_sigue_siendo_una_omision(self) -> None:
        self.assertEqual(self.omitidas(self.base()), {"C3-R1", "C3-R2"})

    # Cubre: T-105
    def test_21_un_field_con_valor_y_fuente_del_documento_reconoce_la_restriccion(self) -> None:
        payload = self.base()
        payload["fields"] = [{"name": "Equidistancia de curvas", "value": "0.5 m", "source": "cantidades_03.xlsx, Supuestos!A3:C3"},
                             {"name": "Precisión horizontal", "value": "0.02 m", "source": "cantidades_03.xlsx, Supuestos!A4:C4"}]
        self.assertEqual(self.omitidas(payload), set())
        self.assertTrue(any("fields" in t for t in evaluar("C3", payload)["transformaciones"]))

    # Cubre: T-105
    def test_22_un_field_sin_fuente_o_con_fuente_de_otro_archivo_no_cuenta(self) -> None:
        for fuente in ("", "otro_documento.pdf, p. 1"):
            payload = self.base()
            payload["fields"] = [{"name": "Equidistancia de curvas", "value": "0.5 m", "source": fuente},
                                 {"name": "Precisión horizontal", "value": "0.02 m", "source": fuente}]
            self.assertEqual(self.omitidas(payload), {"C3-R1", "C3-R2"}, fuente)

    # Cubre: T-105
    def test_23_un_field_con_otro_valor_o_sin_dato_no_reconoce_la_restriccion(self) -> None:
        payload = self.base()
        payload["fields"] = [{"name": "Equidistancia de curvas", "value": "1 m", "source": "cantidades_03.xlsx, Supuestos!A3:C3"},
                             {"name": "Precisión horizontal", "value": "no indicado", "source": "cantidades_03.xlsx, Supuestos!A4:C4"}]
        self.assertEqual(self.omitidas(payload), {"C3-R1", "C3-R2"})

    # Cubre: T-105
    def test_24_los_fields_no_crean_actividades_ni_cambian_lo_demas(self) -> None:
        sin = evaluar("C3", self.base())
        payload = copy.deepcopy(self.base())
        payload["fields"] = [{"name": "Equidistancia de curvas", "value": "0.5 m", "source": "cantidades_03.xlsx, Supuestos!A3:C3"}]
        con = evaluar("C3", payload)
        self.assertEqual(sin["actividades_obtenidas"], con["actividades_obtenidas"])
        self.assertEqual(con["errores_criticos"], sin["errores_criticos"])
        self.assertEqual(self.omitidas(payload), {"C3-R2"})


class ResultadosExistentesTest(unittest.TestCase):
    # Cubre: T-105
    def test_30_los_retornos_sinteticos_de_los_tres_casos_siguen_sin_criticos(self) -> None:
        for caso in DOCUMENTOS:
            informe = evaluar(caso, retorno_sintetico(caso))
            self.assertEqual(informe["errores_criticos"], 0, (caso, _codigos(informe, "critico")))
            self.assertEqual(informe["actividades_con_valor_y_unidad_correctos"], len(_referencia(caso)["actividades"]))

    # Cubre: T-105
    def test_31_una_cantidad_incorrecta_sigue_siendo_critica(self) -> None:
        payload = retorno_sintetico("C3")
        payload["activities"][0]["quantity"] = "99.9"
        self.assertIn("cantidad_o_unidad_incorrecta", _codigos(evaluar("C3", payload), "critico"))


if __name__ == "__main__":
    unittest.main()
