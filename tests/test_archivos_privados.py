"""El token de Google y el de CSRF se escriben completos y con permiso solo del usuario.

Cubre: U-44
"""

from tests import _isolate  # noqa: F401

import os
import stat
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from anka.infra import archivos_privados, settings
from anka.infra.archivos_privados import escribir_privado
from anka.web import seguridad


class EscrituraPrivada(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def test_escribe_el_contenido_y_no_deja_temporales(self):
        destino = self.root / "token.json"
        escribir_privado(destino, "uno")
        escribir_privado(destino, "dos")
        self.assertEqual(destino.read_text(encoding="utf-8"), "dos")
        self.assertEqual([p.name for p in self.root.iterdir()], ["token.json"])

    def test_un_fallo_a_medias_conserva_el_archivo_anterior(self):
        destino = self.root / "token.json"
        escribir_privado(destino, "bueno")
        with patch.object(archivos_privados.os, "replace", side_effect=OSError("disco")):
            with self.assertRaises(OSError):
                escribir_privado(destino, "nuevo")
        self.assertEqual(destino.read_text(encoding="utf-8"), "bueno")
        self.assertEqual([p.name for p in self.root.iterdir()], ["token.json"])

    @unittest.skipIf(os.name == "nt", "en Windows el permiso es una ACL")
    def test_permiso_0600_en_posix(self):
        destino = self.root / "token.json"
        escribir_privado(destino, "x")
        self.assertEqual(stat.S_IMODE(destino.stat().st_mode), 0o600)

    @unittest.skipUnless(os.name == "nt", "icacls solo existe en Windows")
    def test_en_windows_se_quita_la_herencia(self):
        destino = self.root / "token.json"
        escribir_privado(destino, "x")
        import subprocess
        salida = subprocess.run(["icacls", str(destino)], capture_output=True, text=True, check=False).stdout
        self.assertNotIn("BUILTIN\\Users", salida)
        self.assertNotIn("(I)", salida)       # ningún permiso heredado

    def test_si_no_se_puede_restringir_se_avisa_sin_el_contenido_y_se_guarda_igual(self):
        destino = self.root / "token.json"
        with patch.object(archivos_privados, "_restringir", return_value=False), \
                self.assertLogs(archivos_privados.log, level="WARNING") as registro:
            escribir_privado(destino, "SECRETO-NO-IMPRIMIR")
        self.assertEqual(destino.read_text(encoding="utf-8"), "SECRETO-NO-IMPRIMIR")
        self.assertNotIn("SECRETO-NO-IMPRIMIR", "\n".join(registro.output))

    def test_el_token_csrf_se_guarda_con_la_escritura_privada(self):
        with patch.object(settings, "DATA_DIR", self.root), \
                patch.object(seguridad, "escribir_privado", wraps=escribir_privado) as escribir:
            valor = seguridad.persistent_csrf_token()
        escribir.assert_called_once()
        self.assertEqual((self.root / "csrf_token").read_text(encoding="utf-8"), valor)
        with patch.object(settings, "DATA_DIR", self.root):
            self.assertEqual(seguridad.persistent_csrf_token(), valor)       # sobrevive al reinicio (D-04)


if __name__ == "__main__":
    unittest.main()
