"""T-127: las filas con cantidad de una hoja de cálculo que la tabla de actividades no recoge."""

from __future__ import annotations

import unittest

from anka.dominio.filas_de_hoja import filas_de_hoja, filas_sin_cubrir, pistas_de_hoja
from anka.servicios import work_import
from anka.web.views import filas_de_hoja_panel

PLANTILLA = """# plantilla.xlsx
# motivo:
Hoja: PLANTILLA PR35 - PR41 ; visibilidad: visible; rango declarado: A1:L28
B2=ESTUDIOS Y DISEÑOS
B4=COSTOS DIRECTOS | C4=CANTIDAD | E4=UNIDAD | F4=VR. UNITARIO ($) | G4=VALOR TOTAL ($)
C5=Cantidad | D5=Veces
B6=Sondeos mecánicos en depósitos coluviales y roca. Incluye descripción del perfil | C6=240 | D6=1 | E6=ml | F6=775000 | G6==+C6*D6*F6 [valor guardado: 186000000]
B7=Apiques para diseño de pavimentos. Hasta 1.5 ml de profundidad | C7==(L5*1000)/250 [valor guardado: 24] | D7=1 | E7=und | F7=500000
B8=Celaduría del equipo, a cargo del cliente | C8=3 | D8=55 | E8=día | F8=0
B9=Línea de refracción sísmica | C9=0 | D9=1 | E9=und
B10=Total costos directos | C10=1 | G10==SUM(G6:G8) [valor guardado: 1]
B11=IVA | C11=19 | E11=%
B12=Texto de la descripción que sigue en
otra línea de la celda | C12=2 | D12=1 | E12=Gl
"""


def _hoja(nombre: str, cuerpo: str, visibilidad: str = "visible") -> str:
    return f"Hoja: {nombre} ; visibilidad: {visibilidad}; rango declarado: A1:L28\n{cuerpo}\n"


# Cubre: T-127
class LecturaDeFilasTests(unittest.TestCase):
    def setUp(self) -> None:
        self.filas = filas_de_hoja("plantilla.xlsx", PLANTILLA)

    def test_lee_descripcion_cantidad_y_unidad_de_cada_item(self) -> None:
        primera = self.filas[0]
        self.assertEqual((primera["fila"], primera["cantidad"], primera["unidad"]), (6, 240.0, "ml"))
        self.assertTrue(primera["descripcion"].startswith("Sondeos mecánicos"))
        self.assertEqual(primera["hoja"], "PLANTILLA PR35 - PR41")

    def test_el_valor_de_una_formula_es_el_valor_guardado(self) -> None:
        self.assertEqual(self.filas[1]["cantidad"], 24.0)

    def test_la_columna_veces_se_conserva(self) -> None:
        self.assertEqual((self.filas[2]["cantidad"], self.filas[2]["veces"]), (3.0, 55.0))
        self.assertEqual(self.filas[0]["veces"], 1.0)

    def test_ignora_cantidad_cero_totales_e_iva(self) -> None:
        descripciones = " ".join(f["descripcion"] for f in self.filas)
        self.assertNotIn("refracción", descripciones)
        self.assertNotIn("Total", descripciones)
        self.assertNotIn("IVA", descripciones)

    def test_una_celda_con_salto_de_linea_sigue_siendo_una_sola_fila(self) -> None:
        ultima = self.filas[-1]
        self.assertEqual((ultima["fila"], ultima["cantidad"]), (12, 2.0))
        self.assertEqual(ultima["descripcion"], "Texto de la descripción que sigue en otra línea de la celda")

    def test_una_hoja_oculta_no_aporta_filas(self) -> None:
        oculta = _hoja("Borrador", "B4=ITEM | C4=CANTIDAD\nB5=Levantamiento de lotes | C5=4", visibilidad="oculta")
        self.assertEqual(filas_de_hoja("a.xlsx", oculta), [])

    def test_una_hoja_sin_encabezado_de_cantidad_no_aporta_filas(self) -> None:
        predios = _hoja("Predios", "A1=Predio | B1=Área\nA2=Lote 1 | B2=3925\nA3=Lote 2 | B3=3400")
        self.assertEqual(filas_de_hoja("predios.xlsx", predios), [])

    def test_varias_hojas_y_encabezados_en_otra_columna(self) -> None:
        texto = (_hoja("ANKA v1", "B3=ITEM | C3=LEVANTAMIENTO | D3=CANTIDAD | E3=UNIDAD\nB4=1 | C4=Levantamiento LiDAR del lote | D4=250 | E4=Ha")
                 + _hoja("V2", "B3=ITEM | C3=LEVANTAMIENTO | D3=CANTIDAD | E3=UNIDAD\nB4=1 | C4=Levantamiento LiDAR del lote | D4=7 | E4=Salida"))
        filas = filas_de_hoja("formulario.xlsx", texto)
        self.assertEqual([(f["hoja"], f["cantidad"], f["unidad"]) for f in filas], [("ANKA v1", 250.0, "Ha"), ("V2", 7.0, "Salida")])

    def test_tope_de_filas_por_archivo(self) -> None:
        cuerpo = "B1=Detalle | C1=Cantidad\n" + "\n".join(f"B{n}=Ítem número {n} del listado | C{n}=1" for n in range(2, 200))
        self.assertEqual(len(filas_de_hoja("grande.xlsx", _hoja("H", cuerpo))), 60)


# Cubre: T-127
class FilasSinCubrirTests(unittest.TestCase):
    ADJUNTOS = [{"filename": "plantilla.xlsx", "text": PLANTILLA}]

    def sin_cubrir(self, actividades: list[dict]) -> list[str]:
        return [f["descripcion"].split()[0] for f in filas_sin_cubrir(self.ADJUNTOS, actividades)]

    def test_con_la_tabla_vacia_se_senalan_todas_las_filas(self) -> None:
        self.assertEqual(len(filas_sin_cubrir(self.ADJUNTOS, [])), 4)

    def test_una_actividad_que_habla_de_lo_mismo_cubre_la_fila(self) -> None:
        pendientes = self.sin_cubrir([{"activity": "Sondeos mecánicos en depósitos y roca", "quantity": "240"}])
        self.assertNotIn("Sondeos", pendientes)
        self.assertEqual(len(pendientes), 3)

    def test_las_raices_cubren_plurales_y_derivados(self) -> None:
        self.assertNotIn("Apiques", self.sin_cubrir([{"activity": "Apique para pavimento", "quantity": "24"}]))

    def test_una_sola_raiz_comun_con_otra_cantidad_no_cubre(self) -> None:
        pendientes = self.sin_cubrir([{"activity": "Diseño de la estructura", "quantity": "9"}])
        self.assertIn("Apiques", pendientes)

    def test_una_raiz_y_la_misma_cantidad_si_cubren(self) -> None:
        self.assertNotIn("Apiques", self.sin_cubrir([{"activity": "Pavimentos", "quantity": "24"}]))

    def test_solo_lee_archivos_de_hoja_de_calculo_con_texto_de_hoja(self) -> None:
        self.assertEqual(filas_sin_cubrir([{"filename": "oferta.pdf", "text": PLANTILLA}], []), [])
        self.assertEqual(filas_sin_cubrir([{"filename": "x.xlsx", "text": "sin hojas"}], []), [])

    def test_sin_adjuntos_o_con_adjuntos_mal_formados_no_falla(self) -> None:
        self.assertEqual(filas_sin_cubrir([], []), [])
        self.assertEqual(filas_sin_cubrir([None, "x", {"filename": None}], []), [])   # type: ignore[list-item]

    def test_pistas_de_hoja_devuelve_la_clave_del_analisis(self) -> None:
        self.assertEqual(len(pistas_de_hoja(self.ADJUNTOS, [])["filas_de_hoja_sin_cubrir"]), 4)


# Cubre: T-127
class FilasEnElFlujoTests(unittest.TestCase):
    def test_el_import_de_work_conserva_las_filas(self) -> None:
        fila = {"archivo": "a.xlsx", "hoja": "H", "fila": 6, "descripcion": "Sondeos", "cantidad": 240.0, "veces": 1.0, "unidad": "ml"}
        analisis = work_import._work_analysis({
            "status": "Pendiente de datos", "project_name": "P", "client_email": "c@x.co", "thread_id": "t1",
            "filas_de_hoja_sin_cubrir": [fila]})
        self.assertEqual(analisis["filas_de_hoja_sin_cubrir"], [fila])

    def test_sin_filas_queda_una_lista_vacia(self) -> None:
        analisis = work_import._work_analysis({
            "status": "Pendiente de datos", "project_name": "P", "client_email": "c@x.co", "thread_id": "t1"})
        self.assertEqual(analisis["filas_de_hoja_sin_cubrir"], [])


# Cubre: T-127
class PanelDeFilasTests(unittest.TestCase):
    FILA = {"archivo": "plantilla.xlsx", "hoja": "H", "fila": 8, "descripcion": "Celaduría", "cantidad": 3.0,
            "veces": 55.0, "unidad": "día"}

    def test_muestra_la_fila_con_veces_y_sin_agregarla_a_la_tabla(self) -> None:
        html = str(filas_de_hoja_panel([self.FILA]))
        self.assertIn("Filas de hojas de cálculo que la tabla no recoge", html)
        self.assertIn("3 × 55", html)
        self.assertIn("No se agregaron a la tabla", html)

    def test_sin_filas_no_hay_panel(self) -> None:
        self.assertEqual(str(filas_de_hoja_panel([])), "")

    def test_muestra_veinticinco_y_resume_el_resto(self) -> None:
        html = str(filas_de_hoja_panel([{**self.FILA, "fila": n, "veces": 1.0} for n in range(40)]))
        self.assertEqual(html.count('class="fila-resumen"'), 25)
        self.assertIn("y 15 filas más", html)

    def test_no_inyecta_html_desde_el_archivo(self) -> None:
        html = str(filas_de_hoja_panel([{**self.FILA, "descripcion": "<script>x</script>", "archivo": "<b>a</b>.xlsx"}]))
        self.assertNotIn("<script>", html)
        self.assertNotIn("<b>a</b>", html)


if __name__ == "__main__":
    unittest.main()
