"""Regresiones de evidencia por formato, límites y datos inválidos. Cubre: F1-03, R12"""

from tests import _isolate  # noqa: F401
import io
import json
import unittest
import zipfile
from unittest.mock import patch

from anka.infra.attachments import extract_attachment_text, extract_attachment_text_for_work_packet
from anka.dominio import lectores_geograficos as geo
from anka.infra import lectores_imagen


def zipped(files):
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w") as archive:
        for name, content in files.items():
            archive.writestr(name, content)
    return stream.getvalue()


class GeographicEvidence(unittest.TestCase):
    def read(self, filename, content):
        return extract_attachment_text(filename, "", content.encode() if isinstance(content, str) else content)

    def test_gpx_preserves_waypoints_routes_tracks_and_invalid_points(self):
        text, reason = self.read(
            "survey.gpx",
            """<gpx version="1.1" creator="fixture">
            <wpt lat="4" lon="-74"><name>A</name><ele>100</ele><time>2026-10-01</time></wpt>
            <wpt lat="bad" lon="-74"/><wpt lat="100" lon="0"/>
            <rte><name>Acceso</name><rtept lat="4" lon="-74"/><rtept lat="4.01" lon="-74"><ele>bad</ele></rtept></rte>
            <trk><name>Campo</name><trkseg><trkpt lat="4" lon="-74"><time>2026-10-01</time></trkpt>
            <trkpt lat="4.02" lon="-74"><time>2026-10-02</time></trkpt></trkseg></trk>
            </gpx>""",
        )
        for expected in ("Waypoint A", "Ruta Acceso: 2 puntos", "Track Campo: 2 puntos", "longitud calculada"):
            self.assertIn(expected, text)
        self.assertIn("parcial", reason)

    def test_geojson_measures_lines_without_inventing_declared_lengths(self):
        geometry = {
            "type": "GeometryCollection",
            "geometries": [
                {"type": "LineString", "coordinates": [[-74, 4], [-74, 4.01]]},
                {"type": "MultiLineString", "coordinates": [[[-74, 4], [-74, 4.02]]]},
            ],
        }
        text, reason = self.read(
            "line.geojson",
            json.dumps({"type": "Feature", "geometry": geometry, "properties": {"servicio": "topografia"}}),
        )
        self.assertIn("servicio=topografia", text)
        self.assertIn("no equivale a longitud declarada", text)
        self.assertEqual(reason, "")

    def test_projected_geojson_is_marked_partial(self):
        text, reason = self.read(
            "line.geojson",
            json.dumps(
                {
                    "type": "Feature",
                    "geometry": {"type": "LineString", "coordinates": [[500000, 1000000], [500001, 1000001]]},
                }
            ),
        )
        self.assertIn("CRS", text)
        self.assertIn("parcial", reason)

    def test_qgc_complex_items_remain_partial(self):
        plan = {
            "version": 1,
            "mission": {
                "vehicleType": 2,
                "firmwareType": 3,
                "plannedHomePosition": [4, -74, 100],
                "items": [
                    {"type": "SimpleItem", "params": [0, 0, 0, 0, 4, -74, 100]},
                    {"type": "ComplexItem", "params": [0, 0, 0, 0, 4.01, -74, 120]},
                    {"type": "unknown", "params": [0, 0, 0, 0, "bad", -74, 100]},
                    "malformed",
                ],
            },
            "geoFence": {"circles": [1]},
            "rallyPoints": {"points": [2]},
        }
        text, reason = self.read("flight.plan", json.dumps(plan))
        self.assertIn("Puntos geográficos: 2", text)
        self.assertIn("geoFence", text)
        self.assertIn("parcial", reason)

    def test_waypoint_csv_keeps_sequence_and_flags_invalid_rows(self):
        text, reason = self.read(
            "flight.csv", "seq,lat,lon,alt\n2,4.01,-74,110\n1,4,-74,100\n3,bad,-74,\n4,100,-74,5\n"
        )
        self.assertLess(text.index("Waypoint 1:"), text.index("Waypoint 2:"))
        self.assertIn("Longitud geodésica aproximada", text)
        self.assertIn("parcial", reason)

    def test_kml_attributes_networklinks_and_line_evidence(self):
        text, reason = self.read(
            "survey.kml",
            """<kml><NetworkLink/><Placemark><name>Acceso</name>
            <description>Corredor</description><ExtendedData><Data name="ancho"><value>10 m</value></Data></ExtendedData>
            <LineString><coordinates>-74,4,100 -74,4.01,110</coordinates></LineString>
            <when>2026-10-01</when></Placemark></kml>""",
        )
        self.assertIn("ancho=10 m", text)
        self.assertIn("Longitud", text)
        self.assertIn("parcial", reason)

    def test_wpml_inside_kmz_preserves_mission_and_coordinates(self):
        content = """<kml><missionConfig><flyToWaylineMode>safely</flyToWaylineMode></missionConfig>
            <Folder><autoFlightSpeed>5</autoFlightSpeed><Placemark><index>0</index>
            <Point><coordinates>-74,4,100</coordinates></Point></Placemark></Folder></kml>"""
        text, reason = self.read("mission.kmz", zipped({"wpmz/waylines.wpml": content}))
        self.assertIn("flyToWaylineMode=safely", text)
        self.assertIn("Waypoint 1", text)
        self.assertEqual(reason, "")

    def test_malformed_geospatial_payloads_are_reported_without_fabrication(self):
        for filename, payload in [
            ("x.gpx", "<bad"),
            ("x.wpml", "<bad"),
            ("x.geojson", "[]"),
            ("x.plan", "[]"),
            ("x.geojson", "{"),
            ("x.plan", "{"),
        ]:
            with self.subTest(filename=filename, payload=payload):
                text, reason = self.read(filename, payload)
                self.assertEqual(text, "")
                self.assertTrue(reason)
        with self.assertRaisesRegex(ValueError, "no contiene"):
            geo.kmz_text(zipped({"empty.txt": "no geometry"}))


class DocumentEvidence(unittest.TestCase):
    def test_docx_headers_do_not_hide_tracked_changes_in_body(self):
        document = "<document><body><p><ins><r><t>Alcance nuevo</t></r></ins></p>"
        document += "<tbl><tr><tc><p><r><t>20 ha</t></r></p></tc></tr></tbl></body></document>"
        content = zipped(
            {
                "word/document.xml": document,
                "word/header1.xml": "<header><p><t>Cabecera</t></p></header>",
                "word/comments.xml": "<comments><comment>Verificar</comment></comments>",
            }
        )
        text, reason = extract_attachment_text("scope.docx", "", content)
        for expected in ("CAMBIOS RASTREADOS", "Alcance nuevo", "20 ha", "Cabecera", "Verificar"):
            self.assertIn(expected, text)
        self.assertIn("parcial", reason)

    def test_html_drops_scripts_and_marks_uninterpreted_images(self):
        text, reason = extract_attachment_text(
            "scope.html", "", b'<p>Area 20 ha</p><script>SECRET</script><img src="x"/>'
        )
        self.assertIn("Area 20 ha", text)
        self.assertNotIn("SECRET", text)
        self.assertIn("parcial", reason)

    def test_rtf_decodes_text_but_marks_graphics(self):
        text, reason = extract_attachment_text("scope.rtf", "", rb"{\rtf1 Area 20 ha\par Bogot\'e1 {\pict 00}}")
        self.assertIn("Bogotá", text)
        self.assertIn("GRÁFICO", text)
        self.assertTrue(reason)

    def test_zip_reports_unsupported_files_and_preserves_nested_locations(self):
        text, reason = extract_attachment_text(
            "scope.zip", "", zipped({"docs/scope.csv": "actividad,area\ntopografia,20 ha", "drawing.dwg": "opaque"})
        )
        self.assertIn("Archivo dentro de ZIP: docs/scope.csv", text)
        self.assertIn("drawing.dwg", text)
        self.assertIn("parcial", reason)

    def test_long_text_is_read_completely_in_every_mode(self):
        # Cubre: T-24
        content = ("dato comprobado\n" * 20000).encode()
        text, reason = extract_attachment_text("scope.txt", "text/plain", content)
        full, packet_reason = extract_attachment_text_for_work_packet("scope.txt", "text/plain", content)
        self.assertEqual(text, full)
        self.assertGreater(len(text), 60_000)
        self.assertEqual((reason, packet_reason), ("", ""))

    def test_legacy_converter_unavailable_is_an_explicit_limitation(self):
        with patch.object(lectores_imagen.shutil, "which", return_value=None):
            text, reason = extract_attachment_text("scope.doc", "", b"old format")
        self.assertEqual(text, "")
        self.assertIn("LibreOffice", reason)
