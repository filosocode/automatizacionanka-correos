"""Área y perímetro calculados de los polígonos de un KML/KMZ (T-125)."""

from tests import _isolate  # noqa: F401  (debe ir primero)

import io
import math
import tempfile
import time
import unittest
import zipfile
from pathlib import Path

from anka.dominio.areas_calculadas import areas_calculadas_del_texto, areas_de_los_adjuntos
from anka.dominio.geometria import area_del_poligono
from anka.dominio.lectores_geograficos import kml_text_file, kmz_text
from anka.servicios import work_import
from anka.servicios.lectura_de_adjuntos import verificar_lectura
from anka.web.views import areas_calculadas_panel
from tests.test_lectura_de_adjuntos import (
    PROPIAS, GmailConAdjuntos, borrador, hilo_con_adjunto_por_id, reportado,
)


def celda(lon0, lat0, lado=1.0, cerrada=True):
    """Un cuadrado de `lado` grados con su esquina suroeste en (lon0, lat0)."""
    anillo = [(lon0, lat0), (lon0 + lado, lat0), (lon0 + lado, lat0 + lado), (lon0, lat0 + lado)]
    return anillo + [anillo[0]] if cerrada else anillo


def coordenadas(anillo):
    return " ".join(f"{lon:.9f},{lat:.9f},0" for lon, lat in anillo)


def poligono(nombre, exterior, interiores=()):
    huecos = "".join(f"<innerBoundaryIs><LinearRing><coordinates>{coordenadas(a)}</coordinates></LinearRing>"
                     "</innerBoundaryIs>" for a in interiores)
    return (f"<Placemark><name>{nombre}</name><Polygon><outerBoundaryIs><LinearRing><coordinates>"
            f"{coordenadas(exterior)}</coordinates></LinearRing></outerBoundaryIs>{huecos}</Polygon></Placemark>")


def linea(nombre, puntos):
    return (f"<Placemark><name>{nombre}</name><LineString><coordinates>{coordenadas(puntos)}</coordinates>"
            "</LineString></Placemark>")


def kml(*placemarks):
    return ('<?xml version="1.0"?><kml xmlns="http://www.opengis.net/kml/2.2"><Document>'
            + "".join(placemarks) + "</Document></kml>")


# Cubre: T-125
class AreaDelPoligonoTests(unittest.TestCase):
    def test_una_celda_de_un_grado_en_el_ecuador_mide_lo_que_mide_en_wgs84(self):
        resultado, aviso = area_del_poligono(celda(0, 0), [])
        self.assertEqual(aviso, "")
        # 111,3195 km × 110,5744 km: el valor del elipsoide, no el de una esfera (que daría 12 364 km²)
        self.assertAlmostEqual(resultado["area_m2"] / 1e6, 12_308.8, delta=12)

    def test_el_perimetro_es_el_del_anillo_exterior(self):
        resultado, _ = area_del_poligono(celda(-74.0, 4.6, 0.001), [])
        self.assertAlmostEqual(resultado["perimetro_m"], 4 * 110.9, delta=1.5)

    def test_un_anillo_interior_se_resta(self):
        exterior = celda(-74.0, 4.6, 0.001)
        interior = celda(-73.9995, 4.6005, 0.0001)
        entero, _ = area_del_poligono(exterior, [])
        con_isla, _ = area_del_poligono(exterior, [interior])
        self.assertAlmostEqual(con_isla["area_m2"] / entero["area_m2"], 0.99, delta=0.0005)
        self.assertEqual(con_isla["anillos_interiores"], 1)

    def test_da_igual_que_el_anillo_venga_cerrado_o_abierto_y_su_sentido(self):
        cerrado, _ = area_del_poligono(celda(-74, 4.6, 0.01), [])
        abierto, _ = area_del_poligono(celda(-74, 4.6, 0.01, cerrada=False), [])
        al_reves, _ = area_del_poligono(list(reversed(celda(-74, 4.6, 0.01))), [])
        self.assertAlmostEqual(cerrado["area_m2"], abierto["area_m2"], places=3)
        self.assertAlmostEqual(cerrado["area_m2"], al_reves["area_m2"], places=3)

    def test_un_contorno_que_se_cruza_no_tiene_area_y_dice_por_que(self):
        corbata = [(0, 0), (1, 1), (1, 0), (0, 1), (0, 0)]
        resultado, motivo = area_del_poligono(corbata, [])
        self.assertIsNone(resultado)
        self.assertIn("se cruza a sí mismo", motivo)

    def test_coordenadas_fuera_de_rango_no_tienen_area(self):
        resultado, motivo = area_del_poligono([(200, 0), (201, 0), (201, 1), (200, 1)], [])
        self.assertIsNone(resultado)
        self.assertIn("fuera de longitud/latitud", motivo)

    def test_menos_de_tres_vertices_distintos_no_tienen_area(self):
        resultado, motivo = area_del_poligono([(0, 0), (1, 1), (0, 0)], [])
        self.assertIsNone(resultado)
        self.assertIn("menos de tres vértices distintos", motivo)

    def test_un_anillo_interior_invalido_deja_el_poligono_sin_area(self):
        resultado, motivo = area_del_poligono(celda(0, 0), [[(0.2, 0.2), (0.3, 0.3)]])
        self.assertIsNone(resultado)
        self.assertIn("un anillo interior", motivo)

    @staticmethod
    def circulo(n):
        return [(-74 + 0.01 * math.cos(2 * math.pi * i / n), 4.6 + 0.01 * math.sin(2 * math.pi * i / n))
                for i in range(n)]

    def test_un_contorno_de_miles_de_vertices_se_calcula_y_dice_que_no_comprobo_cruces(self):
        resultado, aviso = area_del_poligono(self.circulo(5001), [])
        self.assertIsNotNone(resultado)
        self.assertIn("no se comprobó si el contorno se cruza", aviso)

    def test_un_contorno_grande_pero_dentro_del_tope_se_comprueba_y_es_rapido(self):
        inicio = time.perf_counter()
        resultado, aviso = area_del_poligono(self.circulo(4000), [])
        self.assertIsNotNone(resultado)
        self.assertEqual(aviso, "")
        self.assertLess(time.perf_counter() - inicio, 2.0)   # comparar todos los lados con todos tardaba ~5 s

    def test_un_cruce_se_detecta_aunque_el_contorno_tenga_muchos_vertices(self):
        anillo = self.circulo(3000)
        anillo[1000], anillo[2000] = anillo[2000], anillo[1000]      # dos vértices cambiados de sitio: se cruza
        resultado, motivo = area_del_poligono(anillo, [])
        self.assertIsNone(resultado)
        self.assertIn("se cruza a sí mismo", motivo)

    def test_un_roce_en_un_vertice_no_es_un_cruce(self):
        pajarita_tocada = [(0, 0), (2, 0), (1, 1), (2, 2), (0, 2), (1, 1)]    # el vértice (1, 1) se visita dos veces
        resultado, _ = area_del_poligono(pajarita_tocada, [])
        self.assertIsNotNone(resultado)

    def test_un_poligono_que_cruza_el_antimeridiano_no_suma_la_vuelta_al_mundo(self):
        cruce, _ = area_del_poligono([(179.5, 0), (-179.5, 0), (-179.5, 1), (179.5, 1)], [])
        junto, _ = area_del_poligono(celda(0, 0, 1.0), [])
        self.assertAlmostEqual(cruce["area_m2"], junto["area_m2"], delta=junto["area_m2"] * 0.001)


# Cubre: T-125
class TextoDelKmlTests(unittest.TestCase):
    def test_cada_poligono_deja_su_area_y_la_etiqueta_como_dato_calculado(self):
        texto = kml_text_file("predios.kml", kml(poligono("Predio A", celda(-74, 4.6, 0.001))).encode())
        linea = next(x for x in texto.splitlines() if x.startswith("Polígono «Predio A»"))
        self.assertIn("área geodésica aproximada calculada", linea)
        self.assertIn("m² (", linea)
        self.assertIn("Dato calculado por el programa, no declarado por el cliente", linea)
        self.assertNotIn("Área total", texto)

    def test_con_varios_poligonos_el_archivo_suma_su_total(self):
        texto = kml_text_file("predios.kml", kml(poligono("A", celda(-74, 4.6, 0.001)),
                                                 poligono("B", celda(-74, 4.7, 0.001))).encode())
        self.assertIn("Área total calculada de 2 polígonos", texto)

    def test_un_poligono_sin_area_lo_dice_y_no_entra_en_el_total(self):
        corbata = [(0, 0), (1, 1), (1, 0), (0, 1), (0, 0)]
        texto = kml_text_file("predios.kml", kml(poligono("Roto", corbata),
                                                 poligono("Bueno", celda(-74, 4.6, 0.001))).encode())
        self.assertIn("Polígono «Roto»: sin área calculada (el contorno se cruza a sí mismo)", texto)
        self.assertNotIn("Área total", texto)

    def test_un_kml_sin_poligonos_no_trae_lineas_de_area(self):
        puntos = ('<kml xmlns="http://www.opengis.net/kml/2.2"><Document><Placemark><name>P</name>'
                  "<Point><coordinates>-74,4.9,0</coordinates></Point></Placemark></Document></kml>")
        texto = kml_text_file("p.kml", puntos.encode())
        self.assertNotIn("Polígono «", texto)
        self.assertEqual(areas_calculadas_del_texto("p.kml", texto), [])

    def test_una_linea_cerrada_se_trata_como_contorno_y_se_rotula_como_tal(self):
        texto = kml_text_file("predio.kml", kml(linea("Predio", celda(-74, 4.6, 0.001))).encode())
        [area] = areas_calculadas_del_texto("predio.kmz", texto)
        self.assertEqual(area["poligono"], "Predio (línea cerrada)")
        self.assertAlmostEqual(area["area_m2"], 12_300, delta=300)

    def test_una_linea_abierta_no_tiene_area(self):
        texto = kml_text_file("ruta.kml", kml(linea("Ruta", celda(-74, 4.6, 0.001)[:-1])).encode())
        self.assertNotIn("Polígono «", texto)

    def test_una_linea_cerrada_sin_area_no_deja_ruido(self):
        corbata = [(0, 0), (1, 1), (1, 0), (0, 1), (0, 0)]
        ida_y_vuelta = [(-74, 4.6), (-74.01, 4.6), (-74.02, 4.6), (-74, 4.6)]
        texto = kml_text_file("rutas.kml", kml(linea("Corbata", corbata), linea("Ida y vuelta", ida_y_vuelta)).encode())
        self.assertNotIn("Polígono «", texto)
        self.assertNotIn("sin área", texto)

    def test_el_kmz_trae_el_area_de_sus_kml(self):
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w") as kmz:
            kmz.writestr("doc.kml", kml(poligono("Lote", celda(-74, 4.6, 0.001))))
        self.assertIn("Polígono «Lote»: área geodésica aproximada calculada", kmz_text(buffer.getvalue()))

    def test_el_texto_se_vuelve_a_leer_como_datos_para_la_pantalla(self):
        texto = kml_text_file("p.kml", kml(poligono("Lote «norte»", celda(-74, 4.6, 0.001))).encode())
        [area] = areas_calculadas_del_texto("lotes.kmz", texto)
        self.assertEqual((area["archivo"], area["poligono"]), ("lotes.kmz", "Lote «norte»"))
        self.assertAlmostEqual(area["area_ha"], area["area_m2"] / 10_000, places=3)
        self.assertEqual(area["anillos_interiores"], 0)

    def test_solo_los_adjuntos_con_poligonos_aportan_areas(self):
        texto = kml_text_file("p.kml", kml(poligono("Lote", celda(-74, 4.6, 0.001))).encode())
        areas = areas_de_los_adjuntos([{"filename": "oferta.pdf", "text": "1,2 ha de predio"},
                                       {"filename": "lotes.kmz", "text": texto}, {"filename": "x", "text": ""}])
        self.assertEqual([a["archivo"] for a in areas], ["lotes.kmz"])
        self.assertEqual(areas_de_los_adjuntos([]), [])


# Cubre: T-125
class AreasEnElFlujoTests(unittest.TestCase):
    KML = kml(poligono("Predio 1", celda(-74, 4.6, 0.001)))

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.directorio = Path(self.temp.name)

    def test_al_verificar_la_lectura_las_areas_del_kml_quedan_en_el_borrador(self):
        gmail = GmailConAdjuntos({}, {})
        hilo_con_adjunto_por_id(gmail, "predios.kml", self.KML)
        datos = borrador(reportado("predios.kml", self.KML))
        verificar_lectura(gmail, datos, PROPIAS, self.directorio)
        self.assertEqual([a["poligono"] for a in datos["areas_calculadas"]], ["Predio 1"])

    def test_sin_poligonos_el_borrador_no_trae_areas(self):
        gmail = GmailConAdjuntos({}, {})
        hilo_con_adjunto_por_id(gmail, "notas.txt", "Área aproximada 126 ha")
        datos = borrador(reportado("notas.txt", "Área aproximada 126 ha"))
        verificar_lectura(gmail, datos, PROPIAS, self.directorio)
        self.assertNotIn("areas_calculadas", datos)

    def test_el_analisis_importado_guarda_las_areas_y_nunca_las_pone_en_la_tabla(self):
        area = {"archivo": "predios.kml", "poligono": "Predio 1", "area_m2": 12_000.0, "area_ha": 1.2,
                "perimetro_m": 440.0, "anillos_interiores": 0}
        analisis = work_import._work_analysis({**borrador(), "areas_calculadas": [area],
                                               "activities": [{"activity": "Levantamiento", "quantity": "no indicado"}]})
        self.assertEqual(analisis["areas_calculadas"], [area])
        for fila in analisis["activities"]:
            self.assertNotIn("1.2", str(fila.get("quantity", "")))
        self.assertEqual(work_import._work_analysis(borrador())["areas_calculadas"], [])


# Cubre: T-125
class PanelDeAreasTests(unittest.TestCase):
    AREA = {"archivo": "lotes.kmz", "poligono": "Lote <b>1</b>", "area_m2": 12_345.67, "area_ha": 1.2346,
            "perimetro_m": 440.04, "anillos_interiores": 2}

    def test_sin_areas_no_aparece_el_panel(self):
        self.assertEqual(str(areas_calculadas_panel([])), "")

    def test_el_panel_muestra_hectareas_metros_y_perimetro_como_dato_a_confirmar(self):
        html = str(areas_calculadas_panel([self.AREA]))
        for esperado in ("Áreas calculadas de los archivos geográficos", "lotes.kmz", "1.2346", "12 345.7", "440.0",
                         "con 2 anillos interiores restados", "no las declaró el cliente"):
            self.assertIn(esperado, html)

    def test_con_muchos_poligonos_muestra_los_mayores_y_resume_el_resto(self):
        areas = [{"archivo": "corredor.kmz", "poligono": f"P{i}", "area_m2": 1000.0 * (i + 1), "area_ha": 0.1 * (i + 1),
                  "perimetro_m": 100.0, "anillos_interiores": 0} for i in range(30)]
        html = str(areas_calculadas_panel(areas))
        self.assertEqual(html.count("<tr class=\"fila-resumen\">"), 25)
        self.assertIn("P29", html)                       # el mayor
        self.assertNotIn(">P0<", html)                   # el menor queda en el resumen
        self.assertIn("y 5 polígonos más, que suman 1.50 ha", html)

    def test_el_nombre_del_poligono_no_inyecta_html(self):
        html = str(areas_calculadas_panel([self.AREA]))
        self.assertNotIn("<b>1</b>", html)
        self.assertIn("&lt;b&gt;1&lt;/b&gt;", html)


if __name__ == "__main__":
    unittest.main()
