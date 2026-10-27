"""T-126: las áreas del correo contrastadas con las que el programa calculó de los KML/KMZ."""

from __future__ import annotations

import unittest

from anka.dominio.areas_declaradas import areas_declaradas, diferencias_de_area, pistas_de_area
from anka.servicios import work_import
from anka.web.views import areas_calculadas_panel


def _area(nombre: str, hectareas: float, archivo: str = "poligonos.kmz") -> dict:
    return {"archivo": archivo, "poligono": nombre, "area_m2": hectareas * 1e4, "area_ha": hectareas,
            "perimetro_m": 100.0, "anillos_interiores": 0}


# Cubre: T-126
class AreasEscritasEnElCorreoTests(unittest.TestCase):
    def hectareas(self, texto: str) -> list[list[float]]:
        return [lecturas for _, lecturas in areas_declaradas(texto)]

    def test_lee_hectareas_con_coma_punto_y_miles(self) -> None:
        self.assertEqual(self.hectareas("polígono de 0,8 ha"), [[0.8]])
        self.assertEqual(self.hectareas("una extensión de 0.8 ha"), [[0.8]])
        self.assertEqual(self.hectareas("suelo urbano 1.376,04 ha"), [[1376.04]])
        self.assertEqual(self.hectareas("de unas 1500 hectáreas"), [[1500.0]])

    def test_un_numero_con_un_solo_separador_y_tres_cifras_es_ambiguo_y_guarda_las_dos_lecturas(self) -> None:
        self.assertEqual(self.hectareas("2.677 ha"), [[2.677, 2677.0]])

    def test_convierte_metros_cuadrados_y_kilometros_cuadrados(self) -> None:
        self.assertEqual(self.hectareas("un lote de 10.000 m2"), [[0.001, 1.0]])   # «10.000» puede ser 10 o diez mil
        self.assertAlmostEqual(self.hectareas("lote de 3925 m²")[0][0], 0.3925)
        self.assertAlmostEqual(self.hectareas("embalse de 2 km2")[0][0], 200.0)

    def test_no_confunde_densidades_ni_longitudes_con_areas(self) -> None:
        self.assertEqual(self.hectareas("200 puntos por m2 y 150 pts/m² en 12 km de línea, 30 m de ancho"), [])
        self.assertEqual(self.hectareas("volumen de 5000 m3"), [])

    def test_no_repite_la_misma_cifra(self) -> None:
        self.assertEqual(len(areas_declaradas("250 Ha en total; repito: 250 ha")), 1)


# Cubre: T-126
class DiferenciasDeAreaTests(unittest.TestCase):
    def test_caso_real_el_correo_dice_08_ha_y_el_kmz_mide_48(self) -> None:
        areas = [_area("Zona industrial", 540.4254), _area("Ronda río Roble", 120.9952), _area("Buenavista", 4.8188)]
        correo = "Polígonos de 543 ha, 125 ha y el tercero con 0.8 ha."
        diferencias = diferencias_de_area(correo, areas)
        self.assertEqual(len(diferencias), 1)
        self.assertEqual(diferencias[0]["escrita"], "0.8 ha")
        self.assertEqual(diferencias[0]["poligono"], "Buenavista")
        self.assertAlmostEqual(diferencias[0]["calculada_ha"], 4.8188)

    def test_una_diferencia_pequena_no_se_senala(self) -> None:
        self.assertEqual(diferencias_de_area("área de 16 ha", [_area("Lote", 16.3392)]), [])   # 2 %

    def test_la_cifra_que_es_la_suma_de_los_poligonos_se_respalda(self) -> None:
        areas = [_area("Urbano", 1376.04), _area("Expansión", 475.04)]
        self.assertEqual(diferencias_de_area("Urbano 1.376,04 ha, expansión 475,04 ha, total 1.851,08 ha", areas), [])

    def test_una_cifra_ambigua_se_respalda_si_cualquiera_de_sus_lecturas_coincide(self) -> None:
        self.assertEqual(diferencias_de_area("2.677 ha", [_area("Finca", 2677.0)]), [])
        self.assertEqual(diferencias_de_area("2.677 ha", [_area("Finca", 2.677)]), [])

    def test_sin_poligonos_no_dice_nada(self) -> None:
        self.assertEqual(diferencias_de_area("área de 543 ha", []), [])

    def test_con_demasiados_poligonos_no_compara(self) -> None:
        muchos = [_area(f"P{i}", 1.0 + i) for i in range(201)]
        self.assertEqual(diferencias_de_area("área de 0,001 ha", muchos), [])

    def test_sin_cifras_en_el_correo_no_dice_nada(self) -> None:
        self.assertEqual(diferencias_de_area("Adjunto el polígono para cotizar", [_area("Lote", 5.0)]), [])

    def test_solo_senala_las_diez_primeras(self) -> None:
        correo = " ".join(f"{1000 + 10 * i} ha" for i in range(15))
        self.assertEqual(len(diferencias_de_area(correo, [_area("Lote", 5.0)])), 10)

    def test_pistas_de_area_junta_las_dos_pistas(self) -> None:
        texto = ("Polígono «Buenavista»: área geodésica aproximada calculada 48187.5 m² (4.8188 ha); perímetro 1203.1 m; "
                 "anillos interiores: 0. Dato calculado por el programa, no declarado por el cliente")
        pistas = pistas_de_area("tercer polígono de 0,8 ha", [{"filename": "a.kmz", "text": texto}])
        self.assertEqual(len(pistas["areas_calculadas"]), 1)
        self.assertEqual(pistas["diferencias_de_area"][0]["escrita"], "0,8 ha")
        self.assertEqual(pistas_de_area("sin cifras", []), {"areas_calculadas": [], "diferencias_de_area": []})


# Cubre: T-126
class DiferenciasEnElFlujoTests(unittest.TestCase):
    def test_el_import_de_work_conserva_las_diferencias(self) -> None:
        diferencia = {"escrita": "0,8 ha", "declarada_ha": 0.8, "poligono": "B", "archivo": "a.kmz", "calculada_ha": 4.8}
        analisis = work_import._work_analysis({
            "status": "Pendiente de datos", "project_name": "P", "client_email": "c@x.co", "thread_id": "t1",
            "diferencias_de_area": [diferencia]})
        self.assertEqual(analisis["diferencias_de_area"], [diferencia])

    def test_sin_diferencias_queda_una_lista_vacia(self) -> None:
        analisis = work_import._work_analysis({
            "status": "Pendiente de datos", "project_name": "P", "client_email": "c@x.co", "thread_id": "t1"})
        self.assertEqual(analisis["diferencias_de_area"], [])


# Cubre: T-126
class PanelDeDiferenciasTests(unittest.TestCase):
    AREAS = [_area("Buenavista", 4.8188)]
    DIFERENCIA = {"escrita": "0,8 ha", "declarada_ha": 0.8, "poligono": "Buenavista", "archivo": "a.kmz",
                  "calculada_ha": 4.8188}

    def test_muestra_el_aviso_con_las_dos_cifras_y_sin_decidir_por_la_persona(self) -> None:
        html = str(areas_calculadas_panel(self.AREAS, [self.DIFERENCIA]))
        self.assertIn("El correo y los archivos no coinciden en el área.", html)
        self.assertIn("0,8 ha", html)
        self.assertIn("4.8188", html)
        self.assertIn("No sé cuál de las dos cifras es la correcta", html)

    def test_sin_diferencias_no_hay_aviso(self) -> None:
        self.assertNotIn("no coinciden", str(areas_calculadas_panel(self.AREAS)))

    def test_no_inyecta_html_desde_el_correo(self) -> None:
        peligrosa = {**self.DIFERENCIA, "escrita": "<script>x</script> 1 ha", "poligono": "<b>P</b>"}
        html = str(areas_calculadas_panel(self.AREAS, [peligrosa]))
        self.assertNotIn("<script>", html)
        self.assertNotIn("<b>P</b>", html)


if __name__ == "__main__":
    unittest.main()
