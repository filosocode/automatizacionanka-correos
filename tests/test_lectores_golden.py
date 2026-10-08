"""Caracterización de los lectores geográficos: salida exacta sobre entradas ricas.

Fija el comportamiento actual para poder refactorizar funciones largas sin cambiarlo. Regenerar a propósito con
`UPDATE_GOLDEN=1 python -m unittest tests.test_lectores_golden` y revisar el diff antes de aceptarlo.
Cubre: F1-03, R12
"""

from tests import _isolate  # noqa: F401  (debe ir primero)

import json
import os
import unittest
from pathlib import Path

from anka.dominio import lectores_geograficos as geo
from anka.dominio.lectores_texto import Unreadable

GOLDEN = Path(__file__).parent / "golden" / "lectores_geograficos.json"

KML = """<kml xmlns="http://www.opengis.net/kml/2.2" xmlns:gx="http://www.google.com/kml/ext/2.2"><Document>
<Placemark><name>Eje vial</name><description><![CDATA[<b>Acceso</b> &amp; portería]]></description>
<ExtendedData><Data name="tipo"><value>vía</value></Data><SimpleData name="km">12</SimpleData></ExtendedData>
<LineString><coordinates>-74.1,4.6,2500 -74.09,4.61,2510 -74.08,4.62,2490</coordinates></LineString></Placemark>
<Placemark><name>Lote</name><Polygon><outerBoundaryIs><LinearRing><coordinates>
-74,4,0 -74,4.1,0 -73.9,4.1,0 -74,4,0</coordinates></LinearRing></outerBoundaryIs></Polygon></Placemark>
<Placemark><Point><coordinates>-74.2,4.7</coordinates></Point></Placemark>
<Placemark><name>Fuera de rango</name><Point><coordinates>500000,1000000</coordinates></Point></Placemark>
<Placemark><name>Sin geometría</name></Placemark>
<Placemark><name>Recorrido</name><gx:Track><when>2026-10-01T10:00:00Z</when><when>2026-10-01T11:00:00Z</when>
<gx:coord>-74.1 4.6 100</gx:coord><gx:coord>-74.2 4.7 110</gx:coord></gx:Track></Placemark>
<GroundOverlay><name>Ortofoto</name></GroundOverlay><NetworkLink><name>Vínculo</name></NetworkLink>
</Document></kml>"""

GPX = """<gpx version="1.1" creator="fixture"><wpt lat="4" lon="-74"><name>A</name><ele>100</ele><time>2026-10-01</time></wpt>
<wpt lat="bad" lon="-74"/><wpt lat="100" lon="0"/>
<rte><name>Acceso</name><rtept lat="4" lon="-74"><ele>10.5</ele></rtept><rtept lat="4.01" lon="-74"><ele>bad</ele></rtept></rte>
<rte><rtept lat="5" lon="-75"/></rte>
<trk><name>Campo</name><trkseg><trkpt lat="4" lon="-74"><ele>2500</ele><time>2026-10-01T10:00:00Z</time></trkpt>
<trkpt lat="4.02" lon="-74"><ele>2510</ele><time>2026-10-02T10:00:00Z</time></trkpt></trkseg>
<trkseg><trkpt lat="4.03" lon="-74.01"/></trkseg></trk><trk><trkseg/></trk></gpx>"""

GEOJSON = json.dumps({
    "type": "FeatureCollection",
    "features": [
        {"type": "Feature", "properties": {"nombre": "Eje", "km": 3}, "geometry": {
            "type": "LineString", "coordinates": [[-74.1, 4.6], [-74.09, 4.61], [-74.08, 4.63]]}},
        {"type": "Feature", "properties": None, "geometry": {
            "type": "Polygon", "coordinates": [[[-74, 4], [-74, 4.1], [-73.9, 4.1], [-74, 4]]]}},
        {"type": "Feature", "properties": {"x": "proyectado"}, "geometry": {
            "type": "LineString", "coordinates": [[500000, 1000000], [500100, 1000100]]}},
        {"type": "Feature", "properties": "texto", "geometry": {"type": "GeometryCollection", "geometries": [
            {"type": "Point", "coordinates": [-74.2, 4.7]}, "no-es-dict"]}},
        {"type": "Feature", "properties": {}, "geometry": {"type": "Point", "coordinates": "mal"}},
        "no-es-feature",
    ],
}, ensure_ascii=False)

CSV_COORDS = (
    "orden;nombre;Latitud;Longitud;Altura;nota\n"
    "2;B;4.61;-74.09;2510;segundo\n1;A;4.60;-74.10;2500;primero\n"
    "3;C;abc;-74.0;1;lat inválida\n4;D;4.7;-74.2;;sin altura\nx;E;4.8;-74.3;5;orden no numérico\n"
    "5;F;95;-74.0;1;fuera de rango\n"
)
CSV_PLAIN = "cliente,servicio,area\nMinera Sur,Levantamiento,20 ha\nSolaris,,\n,Batimetría,5 km\n"
CSV_NO_SEQ = "lat,lon,alt\n4.6,-74.1,10\n4.7,-74.2,20\n"

WPL = "QGC WPL 110\n0\t1\t0\t16\t0\t0\t0\t0\t4.6\t-74.1\t2500\t1\n1\t0\t3\t16\t0\t0\t0\t0\t4.61\t-74.09\t100\t1\nbasura\n"
PLAN = json.dumps({"mission": {"items": [
    {"type": "SimpleItem", "command": 16, "params": [0, 0, 0, 0, 4.6, -74.1, 100], "doJumpId": 1},
    {"type": "SimpleItem", "command": 22, "params": [0, 0, 0, 0, 4.61, -74.09, 120], "doJumpId": 2},
    {"type": "ComplexItem", "complexItemType": "survey", "polygon": [[4.6, -74.1], [4.61, -74.1], [4.61, -74.09]]},
]}})
WPML = (
    '<kml xmlns="http://www.opengis.net/kml/2.2" xmlns:wpml="http://www.dji.com/wpmz/1.0.2"><Document>'
    "<wpml:missionConfig><wpml:flyToWaylineMode>safely</wpml:flyToWaylineMode></wpml:missionConfig>"
    "<Folder><wpml:templateId>0</wpml:templateId><Placemark><Point><coordinates>-74.1,4.6</coordinates></Point>"
    "<wpml:height>100</wpml:height></Placemark></Folder></Document></kml>"
)

CASES = {
    "kml": lambda: geo.kml_text_file("fixture.kml", KML.encode()),
    "gpx": lambda: geo.gpx_text(GPX.encode()),
    "geojson": lambda: geo.geojson_text(GEOJSON.encode()),
    "geojson_feature": lambda: geo.geojson_text(json.dumps(
        {"type": "Feature", "properties": {"a": 1}, "geometry": {"type": "Point", "coordinates": [-74, 4]}}).encode()),
    "csv_coordenadas": lambda: geo.csv_text(CSV_COORDS.encode()),
    "csv_sin_orden": lambda: geo.csv_text(CSV_NO_SEQ.encode()),
    "csv_tabla": lambda: geo.csv_text(CSV_PLAIN.encode()),
    "csv_latin1": lambda: geo.csv_text("nombre,área\nMuñoz,12\n".encode("latin-1")),
    "waypoints": lambda: geo.qgc_waypoints_text(WPL.encode()),
    "plan": lambda: geo.qgc_plan_text(PLAN.encode()),
    "wpml": lambda: geo.wpml_text("waylines.wpml", WPML.encode()),
    "kml_invalido": lambda: geo.kml_text_file("roto.kml", b"<kml><Placemark>"),
    "gpx_invalido": lambda: geo.gpx_text(b"<gpx>"),
    "geojson_invalido": lambda: geo.geojson_text(b"[1, 2]"),
}


def observed():
    result = {}
    for name, call in CASES.items():
        try:
            result[name] = call()
        except (Unreadable, Exception) as exc:  # noqa: BLE001 - se fija también el tipo de error
            result[name] = f"{type(exc).__name__}: {exc}"
    return result


class GeographicGoldenTests(unittest.TestCase):
    def test_readers_keep_their_exact_output(self):
        current = observed()
        if os.getenv("UPDATE_GOLDEN") == "1":
            GOLDEN.write_text(json.dumps(current, ensure_ascii=False, indent=1) + "\n", encoding="utf-8", newline="\n")
            return
        self.assertTrue(GOLDEN.exists(), f"Falta {GOLDEN}; genera con UPDATE_GOLDEN=1")
        expected = json.loads(GOLDEN.read_text(encoding="utf-8"))
        self.assertEqual(sorted(current), sorted(expected))
        for name in current:
            with self.subTest(reader=name):
                self.assertEqual(current[name], expected[name])


if __name__ == "__main__":
    unittest.main()
