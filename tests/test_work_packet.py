"""Paquete local de evidencia para Work: manifiesto, segmentos, cobertura y límites. Cubre: F1-03"""

from tests import _isolate  # noqa: F401  (debe ir primero)

import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from anka.dominio.errores import ErrorDeAnka
from anka.servicios import work_attachment_packet as packet


class PacketTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.out = self.root / "paquete"

    def write(self, name, content=b"Levantamiento topografico de 12 ha en Bogota.\nEntregable: plano.\n"):
        path = self.root / name
        path.write_bytes(content if isinstance(content, bytes) else content.encode("utf-8"))
        return str(path)

    def manifest(self):
        return json.loads((self.out / "manifest.json").read_text(encoding="utf-8"))

    def test_a_readable_file_yields_manifest_hash_and_ordered_segments(self):
        source = self.write("alcance.txt")
        result = packet.prepare_work_attachment_packet([source], str(self.out))
        self.assertEqual(result["extracted_files"], 1)
        record = self.manifest()["attachments"][0]
        self.assertEqual(record["sha256"], hashlib.sha256(Path(source).read_bytes()).hexdigest())
        self.assertEqual((record["status"], record["coverage"]), ("extraido", "completa"))
        text = (self.out / record["text_files"][0]).read_text(encoding="utf-8")
        self.assertTrue(text.startswith("[ANKA WORK PACKET | file=alcance.txt"))
        self.assertIn(f"sha256={record['sha256']}", text)
        self.assertTrue((self.out / "LEEME.md").is_file())

    def test_long_text_is_split_into_contiguous_segments_that_rebuild_the_original(self):
        original = "".join(f"Linea {n} del alcance del proyecto\n" for n in range(1200))
        self.assertGreater(len(original), packet.PACKET_CHUNK_CHARS * 2)
        packet.prepare_work_attachment_packet([self.write("largo.txt", original)], str(self.out))
        record = self.manifest()["attachments"][0]
        self.assertGreater(len(record["text_segments"]), 2)
        expected_start = 0
        rebuilt = ""
        for segment in record["text_segments"]:
            self.assertEqual(segment["start_char"], expected_start)
            expected_start = segment["end_char"]
            body = (self.out / segment["filename"]).read_text(encoding="utf-8").split("\n", 1)[1]
            self.assertEqual(len(body), segment["end_char"] - segment["start_char"])
            rebuilt += body
        # El extractor antepone localizadores («Línea N: »); lo que importa es que no se pierda ninguna línea.
        self.assertEqual(len(rebuilt), record["text_chars"])
        self.assertEqual(expected_start, record["text_chars"])
        self.assertTrue(all(f"Linea {n} del alcance" in rebuilt for n in (0, 599, 1199)))
        self.assertEqual(rebuilt.count("Línea "), 1200)

    def test_chunks_prefer_line_breaks_and_never_drop_text(self):
        text = ("x" * 7000 + "\n") * 5
        chunks = packet.chunk_text(text)
        self.assertEqual("".join(chunks), text)
        self.assertTrue(all(len(chunk) <= packet.PACKET_CHUNK_CHARS for chunk in chunks))
        self.assertTrue(all(chunk.endswith("\n") for chunk in chunks[:-1]))
        self.assertEqual(packet.chunk_text("x" * 30_000)[0], "x" * packet.PACKET_CHUNK_CHARS)

    def test_unreadable_format_is_blocked_not_treated_as_missing_data(self):
        self.write("plano.dwg", b"\x00\x01binario")
        packet.prepare_work_attachment_packet([str(self.root / "plano.dwg")], str(self.out))
        record = self.manifest()["attachments"][0]
        self.assertEqual((record["status"], record["coverage"]), ("bloqueado", "no_legible"))
        self.assertTrue(record["reason"])
        self.assertEqual(record["text_files"], [])
        self.assertEqual(record["evidence"], [])

    def test_partial_reading_is_reported_with_its_reason(self):
        with patch.object(packet, "extract_attachment_text_for_work_packet",
                          return_value=("Texto parcial", "solo se leyeron 5 de 90 páginas")):
            packet.prepare_work_attachment_packet([self.write("grande.pdf", b"%PDF-1.4")], str(self.out))
        record = self.manifest()["attachments"][0]
        self.assertEqual((record["status"], record["coverage"]), ("lectura_parcial", "parcial"))
        self.assertIn("5 de 90", record["reason"])

    def test_oversized_file_is_blocked_without_reading_its_content(self):
        source = self.write("enorme.txt")
        with patch.object(packet, "MAX_WORK_PACKET_ATTACHMENT_BYTES", 5):
            result = packet.prepare_work_attachment_packet([source], str(self.out))
        record = self.manifest()["attachments"][0]
        self.assertEqual(result["blocked_files"], 1)
        self.assertIsNone(record["sha256"])
        self.assertIn("supera", record["reason"])

    def test_unreadable_file_on_disk_is_blocked_with_the_error_type(self):
        source = self.write("sin_permiso.txt")
        with patch.object(Path, "read_bytes", side_effect=PermissionError):
            packet.prepare_work_attachment_packet([source], str(self.out))
        self.assertIn("PermissionError", self.manifest()["attachments"][0]["reason"])

    def test_manifest_does_not_leak_local_paths(self):
        packet.prepare_work_attachment_packet([self.write("alcance.txt")], str(self.out))
        for path in self.out.iterdir():
            self.assertNotIn(str(self.root), path.read_text(encoding="utf-8"))

    def test_input_is_validated_before_anything_is_written(self):
        source = self.write("a.txt")
        casos = [
            ([], "al menos un archivo"),
            ([source, source], "repetidas"),
            ([str(self.root / "no_existe.txt")], "No se encuentra"),
        ]
        for paths, message in casos:
            with self.subTest(message=message), self.assertRaises(ErrorDeAnka) as caught:
                packet.prepare_work_attachment_packet(paths, str(self.out))
            self.assertIn(message, str(caught.exception))
        with patch.object(packet, "MAX_PACKET_FILES", 1), self.assertRaises(ErrorDeAnka):
            packet.prepare_work_attachment_packet([source, self.write("b.txt")], str(self.out))
        with patch.object(packet, "MAX_PACKET_TOTAL_BYTES", 3), self.assertRaises(ErrorDeAnka):
            packet.prepare_work_attachment_packet([source], str(self.out))
        self.assertFalse(self.out.exists())

    def test_existing_output_directory_is_never_overwritten(self):
        self.out.mkdir()
        (self.out / "previo.txt").write_text("no tocar")
        with self.assertRaises(ErrorDeAnka):
            packet.prepare_work_attachment_packet([self.write("a.txt")], str(self.out))
        self.assertEqual((self.out / "previo.txt").read_text(), "no tocar")

    def test_a_failure_midway_leaves_no_partial_packet_or_staging_folder(self):
        with patch.object(packet, "MAX_PACKET_TEXT_BYTES", 5), self.assertRaises(ErrorDeAnka):
            packet.prepare_work_attachment_packet([self.write("a.txt")], str(self.out))
        self.assertFalse(self.out.exists())
        self.assertEqual([p.name for p in self.root.iterdir() if p.name.startswith(".")], [])


if __name__ == "__main__":
    unittest.main()
