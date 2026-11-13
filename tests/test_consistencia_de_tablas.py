"""Toda tabla con `thread_id` está clasificada y el lector geográfico deja constancia de lo que descarta (U-63).

Cubre: U-63
"""

from tests import _isolate  # noqa: F401  (debe ir primero)

import tempfile
import unittest
from pathlib import Path
from xml.etree import ElementTree

from anka.dominio import lectores_geograficos as geo
from anka.infra import store
from anka.servicios import repositorio


# Cubre: U-63
class ClasificacionDeLasTablas(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.db = store.open_db(Path(self._tmp.name))
        self.addCleanup(self.db.close)

    def tablas_con_hilo(self):
        resultado = set()
        for (nombre,) in self.db.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"):
            if "thread_id" in {fila[1] for fila in self.db.execute(f"PRAGMA table_info({nombre})")}:
                resultado.add(nombre)
        return resultado

    def test_las_tablas_con_hilo_son_exactamente_las_clasificadas(self):
        clasificadas = (set(repositorio.TABLAS_DEL_CASO) | set(repositorio.TABLAS_CON_DOS_HILOS)
                        | set(repositorio.TABLAS_QUE_SE_CONSERVAN))
        reales = self.tablas_con_hilo()
        self.assertEqual(reales - clasificadas, set(), "tabla nueva con thread_id: ¿se borra con el caso o se conserva?")
        self.assertEqual(clasificadas - reales, set(), "una lista nombra una tabla que ya no tiene thread_id")

    def test_ninguna_tabla_esta_en_dos_listas(self):
        listas = [set(repositorio.TABLAS_DEL_CASO), set(repositorio.TABLAS_CON_DOS_HILOS),
                  set(repositorio.TABLAS_QUE_SE_CONSERVAN)]
        for a in range(3):
            for b in range(a + 1, 3):
                self.assertEqual(listas[a] & listas[b], set())

    def test_el_historial_y_su_ancla_se_conservan_al_eliminar_un_caso(self):
        self.assertIn("case_events", repositorio.TABLAS_QUE_SE_CONSERVAN)
        self.assertIn("event_anchors", repositorio.TABLAS_QUE_SE_CONSERVAN)


# Cubre: U-63
class DescartesDelLectorGeografico(unittest.TestCase):
    def registrar(self, cuerpo):
        with self.assertLogs("anka.dominio.lectores_geograficos", "DEBUG") as registro:
            texto = cuerpo()
        return texto, "\n".join(registro.output)

    def test_coordenadas_kml_ilegibles_se_cuentan_y_el_texto_no_cambia(self):
        raiz = ElementTree.fromstring(
            "<kml xmlns='http://www.opengis.net/kml/2.2'><Placemark><LineString><coordinates>"
            "-74.1,4.6,2500 abc,def,1 -74.2 -74.3,4.7,2600"
            "</coordinates></LineString></Placemark></kml>")
        filas, salida = self.registrar(lambda: geo.coordinate_rows(raiz))
        self.assertEqual(len(filas), 2)
        self.assertIn("2 elemento(s) descartado(s)", salida)
        self.assertIn("coordenadas KML ilegibles", salida)

    def test_sin_descartes_no_se_registra_nada(self):
        raiz = ElementTree.fromstring(
            "<kml xmlns='http://www.opengis.net/kml/2.2'><Placemark><Point><coordinates>-74.1,4.6,2500"
            "</coordinates></Point></Placemark></kml>")
        with self.assertNoLogs("anka.dominio.lectores_geograficos", "DEBUG"):
            self.assertEqual(len(geo.coordinate_rows(raiz)), 1)

    def test_una_coordenada_de_mision_fuera_de_rango_o_ilegible_se_anota(self):
        _, fuera = self.registrar(lambda: geo._mission_coordinate([0, 0, 0, 0, 95, 10, 5]))
        self.assertIn("fuera de rango", fuera)
        _, ilegible = self.registrar(lambda: geo._mission_coordinate([0, 0, 0, 0, "x", 10, 5]))
        self.assertIn("ilegible", ilegible)
        self.assertEqual(geo._mission_coordinate([0, 0, 0, 0, 4.6, -74.1, 5]), (-74.1, 4.6, 5.0))

    def test_una_altitud_csv_ilegible_se_anota_y_la_fila_se_conserva(self):
        indices = {"lat": 0, "lon": 1, "alt": 2, "seq": None}
        puntos, salida = self.registrar(lambda: geo._csv_coordinates([["4.6", "-74.1", "mucho"]], indices, []))
        self.assertEqual(len(puntos), 1)
        self.assertIn("altitud CSV ilegible", salida)


if __name__ == "__main__":
    unittest.main()
