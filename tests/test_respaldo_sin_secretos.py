"""La copia de seguridad no sube secretos a Drive y sigue siendo restaurable (U-78).

Cubre: U-78
"""

from tests import _isolate  # noqa: F401  (debe ir primero)

import io
import json
import sqlite3
import tempfile
import typing
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

from anka.dominio.configuracion import Configuracion, ConfigLlm
from anka.infra import backup, settings

# Valores falsos que jamás deben llegar al paquete: cada uno cubre una vía de detección distinta.
FALSOS = {
    "api_key": "FALSO-clave-uno-9f2a",
    "OPENAI_API_KEY": "FALSO-clave-dos-77b1",
    "client_secret": "FALSO-secreto-cliente-31cd",
    "refresh_token": "FALSO-refresco-aa90",
    "password": "FALSO-contrasena-5e5e",
    "db_pwd": "FALSO-pwd-0042",
    "private_key": "FALSO-privada-be11",
    "Authorization": "FALSO-autorizacion-c3c3",
    "smtp_key": "FALSO-sufijo-key-8d8d",
}
VALOR_CON_ASPECTO_DE_SECRETO = "sk-FALSO0123456789abcdefghij"      # en una clave de nombre inocente
TOKEN_DE_GOOGLE = "ya29.FALSO_0123456789abcdef"
URL_CON_CLAVE = "https://usuario:FALSO-clave-en-url-1a2b@ollama.local:11434"
PEM = "-----BEGIN PRIVATE KEY-----\nFALSO-pem\n-----END PRIVATE KEY-----"


def config_sintetico() -> dict:
    return {
        "active_mode": "local",
        "gmail_query": "newer_than:30d",
        "own_addresses": ["anka@example.com"],
        "drive_parent_id": "carpeta-publica-123",
        "required_fields": ["ubicación"],
        "llm": {"provider": "ollama", "model": "m", "base_url": URL_CON_CLAVE, "context_tokens": 100000,
                "api_key": FALSOS["api_key"], "extra": {"client_secret": FALSOS["client_secret"]}},
        **{clave: valor for clave, valor in FALSOS.items() if clave != "api_key"},
        "nota": VALOR_CON_ASPECTO_DE_SECRETO,
        "lista": [{"refresh_token": "FALSO-en-lista-6f6f"}, TOKEN_DE_GOOGLE, PEM],
    }


def todos_los_falsos() -> list[str]:
    return [*FALSOS.values(), "FALSO-en-lista-6f6f", VALOR_CON_ASPECTO_DE_SECRETO, TOKEN_DE_GOOGLE,
            "FALSO-clave-en-url", "FALSO-pem"]


def texto_del_zip(origen) -> str:
    """Todo lo que lleva el zip, descomprimido, como un solo texto (salvo la base): para buscar secretos."""
    with zipfile.ZipFile(io.BytesIO(origen) if isinstance(origen, bytes) else origen) as archivo:
        return "\n".join(archivo.read(n).decode("utf-8", "replace") for n in archivo.namelist()
                         if n != "quotes.sqlite3")


class _Pedido:
    def __init__(self, resultado):
        self.resultado = resultado

    def execute(self):
        return self.resultado


class DriveQueGuarda:
    """Doble de Drive: guarda los bytes de cada archivo subido."""

    def __init__(self):
        self.subidos: list[bytes] = []

    def files(self):
        doble = self

        class Archivos:
            def list(self, **kwargs):
                return _Pedido({"files": [{"id": "carpeta", "name": "anka-respaldo-0"}]})

            def create(self, body=None, media_body=None, fields=None):
                if media_body is not None:
                    doble.subidos.append(media_body.getbytes(0, media_body.size()))
                return _Pedido({"id": "nuevo"})

            def delete(self, **kwargs):
                return _Pedido({})
        return Archivos()


class Base(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        parche = patch.object(settings, "DATA_DIR", self.root)
        parche.start()
        self.addCleanup(parche.stop)
        db = sqlite3.connect(self.root / "quotes.sqlite3")
        db.execute("CREATE TABLE t (x)")
        db.execute("CREATE TABLE quotes (thread_id)")
        db.commit()
        db.close()
        (self.root / "token.json").write_text("FALSO-token-de-google")
        self.config = self.root / "config.json"

    def poner_config(self, contenido: dict) -> None:
        self.config.write_text(json.dumps(contenido), encoding="utf-8")

    def leer_config(self) -> dict:
        return json.loads(self.config.read_text(encoding="utf-8"))

    def restaurar(self, copia: Path) -> None:
        with patch.object(backup, "_base_en_uso", return_value=False), \
                patch("anka.infra.work_heartbeat.other_bridge_running", return_value=False):
            backup.restore(copia)


# Cubre: U-78
class LaCopiaNoLlevaSecretos(Base):
    def test_ningun_secreto_llega_al_zip_local_ni_a_drive(self):
        self.poner_config(config_sintetico())
        drive = DriveQueGuarda()
        resultado = backup.run_backup(use_drive=True, drive=drive)       # type: ignore[arg-type]
        self.assertEqual(resultado["error"], "")
        self.assertTrue(resultado["drive"])
        local = next(backup.backups_dir().glob("*.zip"))
        with zipfile.ZipFile(local) as archivo:
            self.assertEqual(set(archivo.namelist()), {"quotes.sqlite3", "config.redactado.json"})
        self.assertEqual(len(drive.subidos), 1)
        for texto in (texto_del_zip(local), texto_del_zip(drive.subidos[0])):
            for falso in todos_los_falsos():
                with self.subTest(falso=falso[:20]):
                    self.assertNotIn(falso, texto)
            self.assertNotIn("FALSO-token-de-google", texto)      # token.json tampoco
        self.assertEqual(drive.subidos[0], local.read_bytes())    # lo que recibió Drive es la copia local

    def test_lo_no_sensible_queda_intacto_y_lo_sensible_se_marca(self):
        redactado = backup.redactar_config(config_sintetico())
        self.assertEqual(redactado["active_mode"], "local")
        self.assertEqual(redactado["drive_parent_id"], "carpeta-publica-123")
        self.assertEqual(redactado["own_addresses"], ["anka@example.com"])
        self.assertEqual(redactado["llm"]["context_tokens"], 100000)       # `*_tokens` no es un secreto
        self.assertEqual(redactado["llm"]["base_url"], "https://ollama.local:11434")
        self.assertEqual(redactado["llm"]["api_key"], backup.MARCA_REDACTADA)
        self.assertEqual(redactado["llm"]["extra"]["client_secret"], backup.MARCA_REDACTADA)
        self.assertEqual(redactado["nota"], backup.MARCA_REDACTADA)
        self.assertEqual(redactado["lista"][0]["refresh_token"], backup.MARCA_REDACTADA)
        self.assertEqual(redactado["lista"][1], backup.MARCA_REDACTADA)
        self.assertEqual(redactado["lista"][2], backup.MARCA_REDACTADA)
        for clave in FALSOS:
            if clave != "api_key":
                self.assertEqual(redactado[clave], backup.MARCA_REDACTADA, clave)

    def test_ninguna_clave_que_anka_reconoce_se_redacta(self):
        conocidas = (set(settings.KNOWN_CONFIG_KEYS) | set(typing.get_type_hints(Configuracion))
                     | set(typing.get_type_hints(ConfigLlm)))
        for clave in sorted(conocidas):
            with self.subTest(clave=clave):
                self.assertEqual(backup.redactar_config({clave: "valor"}), {clave: "valor"})

    def test_la_copia_se_verifica_y_se_restaura_sin_pisar_la_configuracion_viva(self):
        self.poner_config(config_sintetico())
        copia = backup.create_backup()
        backup._verificar_zip(copia)
        self.assertTrue(backup.verificar_ultima_copia()["ok"])
        self.restaurar(copia)
        # la configuración viva (con sus secretos, solo en esta máquina) no se toca
        self.assertEqual(self.leer_config()["password"], FALSOS["password"])
        self.assertFalse((self.root / backup.CONFIG_REDACTADO).exists())

    def test_sin_config_viva_la_redactada_se_instala_como_config(self):
        self.poner_config(config_sintetico())
        copia = backup.create_backup()
        self.config.unlink()
        self.restaurar(copia)
        restaurada = self.leer_config()
        self.assertEqual(restaurada["drive_parent_id"], "carpeta-publica-123")
        self.assertEqual(restaurada["password"], backup.MARCA_REDACTADA)
        self.assertFalse((self.root / backup.CONFIG_REDACTADO).exists())

    def test_una_copia_antigua_con_config_json_se_sigue_restaurando(self):
        antigua = self.root / "anka-respaldo-viejo.zip"
        with zipfile.ZipFile(antigua, "w") as archivo:
            archivo.writestr("quotes.sqlite3", (self.root / "quotes.sqlite3").read_bytes())
            archivo.writestr("config.json", json.dumps({"active_mode": "api", "gmail_query": "viejo"}))
        self.poner_config({"active_mode": "local"})
        self.restaurar(antigua)
        self.assertEqual(self.leer_config()["gmail_query"], "viejo")

    def test_un_zip_con_otro_archivo_o_un_script_sigue_rechazado(self):
        malo = self.root / "malo.zip"
        for nombre in ("evil.py", "token.json", "config.redactado.json/../x"):
            with zipfile.ZipFile(malo, "w") as archivo:
                archivo.writestr("quotes.sqlite3", (self.root / "quotes.sqlite3").read_bytes())
                archivo.writestr(nombre, "no se ejecuta")
            with self.subTest(nombre=nombre), self.assertRaises(ValueError):
                self.restaurar(malo)

    def test_la_verificacion_rechaza_una_copia_con_config_json_en_bruto(self):
        copia = self.root / "x.zip"
        with zipfile.ZipFile(copia, "w") as archivo:
            archivo.writestr("quotes.sqlite3", (self.root / "quotes.sqlite3").read_bytes())
            archivo.writestr("config.json", json.dumps({"api_key": "FALSO"}))
        with self.assertRaises(ValueError):
            backup._verificar_zip(copia)

    def test_un_config_roto_no_rompe_la_copia_ni_se_copia_en_bruto(self):
        for contenido in ("{ esto no es json FALSO-roto-1234", "[1, 2, 3]"):
            with self.subTest(contenido=contenido[:10]):
                self.config.write_text(contenido, encoding="utf-8")
                copia = backup.create_backup()
                with zipfile.ZipFile(copia) as archivo:
                    self.assertEqual(set(archivo.namelist()), {"quotes.sqlite3"})
                self.assertNotIn("FALSO-roto", texto_del_zip(copia))
                copia.unlink()

    def test_sin_config_la_copia_sale_solo_con_la_base(self):
        copia = backup.create_backup()
        with zipfile.ZipFile(copia) as archivo:
            self.assertEqual(set(archivo.namelist()), {"quotes.sqlite3"})


if __name__ == "__main__":
    unittest.main()
