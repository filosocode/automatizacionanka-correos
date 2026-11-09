"""El área del único KMZ se pone sola como cantidad cuando es la única fuente (U-101).

Cubre: U-101
"""

from tests import _isolate  # noqa: F401  (debe ir primero)

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from anka.dominio.areas_calculadas import areas_solas_del_caso, poner_areas_solas, se_mide_por_area
from anka.dominio.areas_declaradas import pistas_de_area
from anka.dominio.status import QuoteStatus
from anka.infra import settings, store

AREA = {"archivo": "Calle153.kmz", "poligono": "0", "area_m2": 1364063.1, "area_ha": 136.4063,
        "perimetro_m": 13951.7, "anillos_interiores": 0}
KMZ_TEXTO = ("Polígono «0»: área geodésica aproximada calculada 1364063.1 m² (136.4063 ha); perímetro 13951.7 m; "
             "anillos interiores: 0.")


def fila(nombre, cantidad="", **datos):
    return {"activity": nombre, "quantity": cantidad, "unit": "", "estado": "pendiente", "notes": "", **datos}


# Cubre: U-101
class QueSeMidePorArea(unittest.TestCase):
    def test_el_vuelo_que_cubre_toda_la_franja_si(self):
        for nombre in ("Levantamiento LiDAR aéreo y fotogrametría con dron", "Ortofoto con RPAS",
                       "Aerofotogrametría de la franja"):
            with self.subTest(nombre=nombre):
                self.assertTrue(se_mide_por_area(nombre))

    def test_las_de_longitud_o_puntos_no(self):
        for nombre in ("Levantamiento topográfico de detalles con estación total",
                       "Levantamiento de detalles urbanos", "Nivelación geométrica de precisión para control vertical",
                       "Control horizontal y georreferenciación de puntos", "Inventario y catastro de redes húmedas",
                       "Levantamiento de redes de acueducto", "Estación total para poligonal"):
            with self.subTest(nombre=nombre):
                self.assertFalse(se_mide_por_area(nombre))


# Cubre: U-101
class PonerElAreaSola(unittest.TestCase):
    def test_pone_el_area_en_las_actividades_de_area_sin_cantidad(self):
        filas = [fila("Levantamiento LiDAR"), fila("Nivelación geométrica"), fila("Ortofoto con RPAS", "no indicado")]
        puestas = poner_areas_solas(filas, [AREA], [])
        self.assertEqual(puestas, ["Levantamiento LiDAR", "Ortofoto con RPAS"])
        self.assertEqual((filas[0]["quantity"], filas[0]["unit"], filas[0]["estado"]), ("136,4063", "ha", "pendiente"))
        self.assertIn("la única fuente del área", filas[0]["notes"])
        self.assertIn("no la declaró el cliente", filas[0]["notes"])
        self.assertEqual(filas[1]["quantity"], "")

    def test_el_levantamiento_a_pie_no_recibe_el_area_de_toda_la_franja(self):
        filas = [fila("Levantamiento topográfico de detalles con estación total")]
        self.assertEqual(poner_areas_solas(filas, [AREA], []), [])
        self.assertEqual(filas[0]["quantity"], "")

    def test_nunca_cambia_una_cantidad_escrita(self):
        filas = [fila("Levantamiento LiDAR", "50")]
        self.assertEqual(poner_areas_solas(filas, [AREA], []), [])
        self.assertEqual(filas[0]["quantity"], "50")

    def test_con_dos_poligonos_no_elige(self):
        filas = [fila("Levantamiento LiDAR")]
        self.assertEqual(poner_areas_solas(filas, [AREA, {**AREA, "poligono": "1"}], []), [])
        self.assertEqual(filas[0]["quantity"], "")

    def test_si_otro_documento_o_el_correo_dice_otra_area_no_pone_nada(self):
        filas = [fila("Levantamiento LiDAR")]
        diferencia = [{"escrita": "20 ha", "declarada_ha": 20.0, "poligono": "0", "archivo": "Calle153.kmz",
                       "calculada_ha": 136.4063}]
        self.assertEqual(poner_areas_solas(filas, [AREA], diferencia), [])
        self.assertEqual(filas[0]["quantity"], "")

    def test_sin_area_o_con_area_cero_no_pone_nada(self):
        filas = [fila("Levantamiento LiDAR")]
        self.assertEqual(poner_areas_solas(filas, [], []), [])
        self.assertEqual(poner_areas_solas(filas, [{**AREA, "area_ha": 0}], []), [])
        self.assertEqual(poner_areas_solas(None, [AREA], []), [])

    def test_el_caso_completa_tambien_la_propuesta_y_dice_que_hizo(self):
        analisis = {"activities": [fila("Levantamiento LiDAR")], "areas_calculadas": [AREA],
                    "diferencias_de_area": [], "pending_model_activities": [fila("Levantamiento LiDAR")]}
        frase = areas_solas_del_caso(analisis)
        self.assertIn("136.4063 ha", frase)
        self.assertIn("Levantamiento LiDAR", frase)
        self.assertEqual(analisis["pending_model_activities"][0]["quantity"], "136,4063")
        self.assertEqual(areas_solas_del_caso({"activities": [], "areas_calculadas": []}), "")


# Cubre: U-101
class LosDocumentosCuentanComoElCorreo(unittest.TestCase):
    def test_un_pdf_con_otra_area_es_una_diferencia(self):
        adjuntos = [{"filename": "Calle153.kmz", "text": KMZ_TEXTO},
                    {"filename": "Especificaciones.pdf", "text": "El área de intervención es de 20 ha en total."}]
        pistas = pistas_de_area("Adjunto el KMZ.", adjuntos)
        self.assertEqual([d["escrita"] for d in pistas["diferencias_de_area"]], ["20 ha"])

    def test_un_pdf_que_coincide_con_el_poligono_no_es_diferencia(self):
        adjuntos = [{"filename": "Calle153.kmz", "text": KMZ_TEXTO},
                    {"filename": "Especificaciones.pdf", "text": "Área aproximada: 136,4 ha."}]
        self.assertEqual(pistas_de_area("", adjuntos)["diferencias_de_area"], [])

    def test_el_texto_del_propio_kmz_no_se_compara_consigo_mismo(self):
        self.assertEqual(pistas_de_area("", [{"filename": "Calle153.kmz", "text": KMZ_TEXTO}])["diferencias_de_area"], [])


# Cubre: U-101
class AlGuardarElCaso(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        parche = patch.object(settings, "DATA_DIR", Path(self.temp.name))
        parche.start()
        self.addCleanup(parche.stop)
        self.db = store.open_db(Path(self.temp.name))
        self.addCleanup(self.db.close)

    def analisis(self, **extra):
        from tests.test_app import complete_analysis
        base = complete_analysis()
        base["activities"] = [fila("Levantamiento LiDAR"), fila("Control horizontal", "5")]
        return {**base, **extra}

    def guardar(self, analisis):
        store.save(self.db, "t-1", "m-1", QuoteStatus.REVIEW, "cliente@empresa.co", analisis)
        import json
        return json.loads(self.db.execute("SELECT analysis_json FROM quotes").fetchone()[0])

    def test_el_primer_analisis_con_el_kmz_ya_trae_el_area(self):
        guardado = self.guardar(self.analisis(areas_calculadas=[AREA], diferencias_de_area=[]))
        self.assertEqual(guardado["activities"][0]["quantity"], "136,4063")
        self.assertEqual(guardado["activities"][1]["quantity"], "5")
        eventos = [e["summary"] for e in store.case_events(self.db, "t-1")]
        self.assertTrue(any("Se puso el área de Calle153.kmz" in e for e in eventos))

    def test_un_seguimiento_con_el_kmz_pone_el_area_sin_pedir_revision(self):
        self.guardar(self.analisis())
        guardado = self.guardar(self.analisis(areas_calculadas=[AREA], diferencias_de_area=[]))
        self.assertEqual(guardado["activities"][0]["quantity"], "136,4063")
        self.assertFalse(guardado.get("pending_model_activities"))

    def test_una_edicion_manual_no_se_toca(self):
        primero = self.analisis()
        guardado = self.guardar(primero)
        guardado["manual_edit"] = {"at": "2026-11-06"}
        self.db.execute("UPDATE quotes SET analysis_json=?", (__import__("json").dumps(guardado),))
        self.db.commit()
        guardado = self.guardar(self.analisis(areas_calculadas=[AREA], diferencias_de_area=[]))
        self.assertEqual(guardado["activities"][0]["quantity"], "")


if __name__ == "__main__":
    unittest.main()
