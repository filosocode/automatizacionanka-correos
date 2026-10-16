"""Seguridad menor del dashboard y de la lectura de archivos (U-61): avisos de la dirección, origen, puerto, formato de
`gmail_url`, tope de píxeles, ZIP acotados, `base_url` ajeno y auditoría de dependencias.

Cubre: U-61
"""

from tests import _isolate  # noqa: F401  (debe ir primero)

import ast
import io
import unittest
import zipfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from anka.dominio import tolerancia_work, work_contract
from anka.infra import lectores_imagen, lectores_zip, settings
from anka.infra.attachments import _zip_text
from anka.dominio.lectores_texto import Unreadable
from anka.web import seguridad
from anka.web import server as web
from tests.test_seguridad_http import Base, atender, formulario
from tests.test_work_sync import payload

RAIZ = Path(__file__).resolve().parents[1]


# Cubre: U-61
class AvisosDeLaDireccion(Base):
    def test_un_aviso_largo_sin_firma_se_recorta_a_300(self):
        largo = "A" * 1000
        _, _, cuerpo, _ = atender("GET", f"/?message={largo}")
        self.assertIn("A" * (seguridad.MAX_MENSAJE - 1) + "…", cuerpo)
        self.assertNotIn("A" * seguridad.MAX_MENSAJE + "A", cuerpo)

    def test_un_aviso_corto_sin_firma_se_ve_igual(self):
        _, _, cuerpo, _ = atender("GET", "/?message=Listo")
        self.assertIn("Listo", cuerpo)

    def test_el_aviso_largo_de_una_redireccion_propia_se_ve_completo(self):
        largo = "Respuesta " + "larga " * 100 + "FIN"
        with patch.dict(web.POST_ROUTES, {"/mode": lambda form: "/?message=" + largo.replace(" ", "+")}):
            _, cabeceras, _, _ = atender("POST", "/mode", formulario(mode="local"))
        destino = cabeceras["Location"]
        self.assertIn("&firma=", destino)
        _, _, cuerpo, _ = atender("GET", destino)
        self.assertIn("larga FIN", cuerpo)

    def test_una_firma_de_otro_texto_no_sirve(self):
        firma = seguridad.firmar_mensaje("otro texto")
        largo = "B" * 1000
        _, _, cuerpo, _ = atender("GET", f"/?message={largo}&firma={firma}")
        self.assertNotIn("B" * 400, cuerpo)

    def test_la_redireccion_conserva_el_ancla_y_el_marcador_de_fallo(self):
        with patch.dict(web.POST_ROUTES, {"/mode": lambda form: "/case?thread_id=t&message=ok#respuesta"}):
            _, cabeceras, _, _ = atender("POST", "/mode", formulario(mode="local"))
        destino = cabeceras["Location"]
        self.assertTrue(destino.endswith("#respuesta"))
        self.assertRegex(destino, r"message=ok&firma=[0-9a-f]{16}#respuesta")


# Cubre: U-61
class OrigenDeUnPost(Base):
    def enviar(self, **cabeceras):
        with patch.dict(web.POST_ROUTES, {"/mode": lambda form: "/"}):
            return atender("POST", "/mode", formulario(mode="local"), cabeceras)[0]

    def test_sec_fetch_site_ajeno_se_rechaza(self):
        for valor in ("cross-site", "same-site"):
            with self.subTest(valor=valor):
                self.assertEqual(self.enviar(**{"Sec-Fetch-Site": valor}), 403)

    def test_sec_fetch_site_propio_pasa(self):
        for valor in ("same-origin", "none"):
            with self.subTest(valor=valor):
                self.assertEqual(self.enviar(**{"Sec-Fetch-Site": valor}), 303)

    def test_origin_ajeno_o_null_se_rechaza_y_el_propio_pasa(self):
        self.assertEqual(self.enviar(Origin="http://evil.example"), 403)
        self.assertEqual(self.enviar(Origin="null"), 403)
        self.assertEqual(self.enviar(Origin="http://127.0.0.1:8765"), 303)

    def test_sin_origin_ni_sec_fetch_site_pasa_porque_manda_el_token(self):
        self.assertEqual(self.enviar(), 303)

    def test_sin_token_sigue_rechazado_aunque_el_origen_sea_el_propio(self):
        estado, _, _, _ = atender("POST", "/mode", b"mode=local", {"Origin": "http://127.0.0.1:8765"})
        self.assertEqual(estado, 403)


# Cubre: U-61
class PuertoDelHost(unittest.TestCase):
    def test_el_puerto_del_host_debe_ser_el_del_servidor(self):
        local = seguridad.is_local_host_header
        self.assertTrue(local("127.0.0.1:8765", 8765))
        self.assertTrue(local("localhost:8765", 8765))
        self.assertTrue(local("[::1]:8765", 8765))
        self.assertTrue(local("127.0.0.1", 8765))        # sin puerto, se acepta
        for malo in ("127.0.0.1:9999", "localhost:80", "[::1]:1", "127.0.0.1:abc", "127.0.0.1:"):
            with self.subTest(host=malo):
                self.assertFalse(local(malo, 8765))
        self.assertFalse(local("evil.example:8765", 8765))
        self.assertTrue(local("127.0.0.1:9999"))         # sin puerto conocido solo se mira el nombre

    def test_el_servidor_rechaza_un_puerto_ajeno_con_403(self):
        servidor = SimpleNamespace(server_address=("127.0.0.1", 8765))
        with patch.object(web.ReviewHandler, "server", servidor, create=True), \
                patch.dict(web.POST_ROUTES, {"/mode": lambda form: "/"}):
            self.assertEqual(atender("GET", "/")[0], 200)
            antes = atender("GET", "/", cabeceras={"Host": "127.0.0.1:9999"})[0]
            post = atender("POST", "/mode", formulario(mode="local"), {"Host": "127.0.0.1:9999"})[0]
        self.assertEqual((antes, post), (403, 403))


# Cubre: U-61
class FormatoDeGmailUrl(unittest.TestCase):
    def normalizar(self, url):
        datos = payload(gmail_url=url)
        aviso = tolerancia_work.reparar(datos)
        return datos["gmail_url"], aviso

    def test_un_enlace_de_gmail_se_conserva(self):
        url = "https://mail.google.com/mail/u/0/#all/18f3a9c2d4e5b601"
        self.assertEqual(self.normalizar(url), (url, ""))

    def test_vacio_o_ausente_es_valido(self):
        self.assertEqual(work_contract.validate(payload(gmail_url="")), [])
        self.assertEqual(work_contract.validate(payload()), [])

    def test_otro_esquema_se_deja_vacio_con_aviso_y_no_rechaza_el_analisis(self):
        for malo in ("javascript:alert(1)", "http://mail.google.com/x", "https://evil.example/mail.google.com/",
                     "data:text/html,<script>1</script>"):
            with self.subTest(url=malo):
                url, aviso = self.normalizar(malo)
                self.assertEqual(url, "")
                self.assertIn("gmail_url", aviso)
                datos = payload(gmail_url=malo)
                tolerancia_work.normalizar_analisis(datos)      # no lanza Rechazo
                self.assertEqual(datos["gmail_url"], "")

    def test_el_validador_comprueba_pattern(self):
        esquema = {"type": "object", "properties": {"a": {"type": "string", "pattern": "^ok"}}}
        self.assertEqual(work_contract.validate({"a": "ok!"}, esquema), [])
        self.assertTrue(work_contract.validate({"a": "no"}, esquema))

    def test_el_esquema_declara_el_patron(self):
        self.assertIn("pattern", work_contract.load_schema()["properties"]["gmail_url"])


# Cubre: U-61
class ImagenesYZip(unittest.TestCase):
    def test_el_tope_de_pillow_es_explicito_e_igual_al_del_lector(self):
        from PIL import Image
        from anka.dominio.attachments import MAX_OCR_PIXELS
        previo = Image.MAX_IMAGE_PIXELS
        self.addCleanup(setattr, Image, "MAX_IMAGE_PIXELS", previo)
        buffer = io.BytesIO()
        Image.new("RGB", (4, 4)).save(buffer, "PNG")
        with patch.object(lectores_imagen, "ocr_pil_image", return_value="x"):
            lectores_imagen.ocr_image(buffer.getvalue())
        self.assertEqual(Image.MAX_IMAGE_PIXELS, MAX_OCR_PIXELS)

    def test_un_zip_con_demasiadas_entradas_se_rechaza_desde_el_lector_de_paquetes(self):
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w") as archivo:
            for numero in range(5):
                archivo.writestr(f"a{numero}.txt", "x")
        with patch("anka.infra.lectores_zip.MAX_ARCHIVE_FILES", 2):
            with self.assertRaises(Unreadable) as caido:
                lectores_zip.zip_acotado(buffer.getvalue(), "paquete ZIP")
        self.assertIn("paquete ZIP excede", str(caido.exception))
        with patch("anka.infra.lectores_zip.MAX_ARCHIVE_FILES", 2):
            with self.assertRaises(Unreadable):
                _zip_text(buffer.getvalue())

    def test_todo_zip_de_un_adjunto_pasa_por_zip_acotado(self):
        """Los únicos `zipfile.ZipFile(` de `anka/` son los que tienen su propio tope equivalente, nombrados aquí."""
        permitidos = {
            "anka/infra/lectores_zip.py",             # es zip_acotado
            "anka/dominio/lectores_geograficos.py",   # KMZ: comprueba entradas y tamaño expandido (dominio no importa infra)
            "anka/infra/backup.py",                   # copias propias de Anka (zip de salida y de restauración validada)
            "anka/servicios/commercial_index.py",     # .docx locales del archivo histórico: tope al XML que lee
        }
        encontrados = set()
        for ruta in (RAIZ / "anka").rglob("*.py"):
            arbol = ast.parse(ruta.read_text(encoding="utf-8"))
            for nodo in ast.walk(arbol):
                if (isinstance(nodo, ast.Call) and isinstance(nodo.func, ast.Attribute) and nodo.func.attr == "ZipFile"
                        and isinstance(nodo.func.value, ast.Name) and nodo.func.value.id == "zipfile"):
                    encontrados.add(ruta.relative_to(RAIZ).as_posix())
        self.assertEqual(encontrados - permitidos, set(), "Un lector abre un ZIP sin pasar por zip_acotado")

    def test_un_docx_local_con_xml_enorme_no_se_lee(self):
        from anka.servicios import commercial_index
        ruta = RAIZ / "tests" / "_docx_enorme.tmp"
        self.addCleanup(ruta.unlink, missing_ok=True)
        with zipfile.ZipFile(ruta, "w", zipfile.ZIP_DEFLATED) as archivo:
            archivo.writestr("word/document.xml", "<w:document xmlns:w='x'><w:t>hola</w:t></w:document>")
        self.assertIn("hola", commercial_index.extract_docx_text(ruta))
        with patch.object(commercial_index, "MAX_DOCX_XML_BYTES", 10):
            self.assertEqual(commercial_index.extract_docx_text(ruta), "")


# Cubre: U-61
class BaseUrlFueraDelEquipo(unittest.TestCase):
    def test_un_motor_ajeno_avisa_y_uno_local_no(self):
        ajeno = settings.config_warnings({"llm": {"base_url": "http://10.0.0.5:11434"}})
        self.assertEqual(len(ajeno), 1)
        self.assertIn("10.0.0.5", ajeno[0])
        for local in ("http://127.0.0.1:11434", "http://localhost:11434", "http://[::1]:11434", "http://LOCALHOST"):
            with self.subTest(url=local):
                self.assertEqual(settings.config_warnings({"llm": {"base_url": local}}), [])

    def test_sin_base_url_o_con_un_valor_raro_no_avisa_ni_falla(self):
        for config in ({}, {"llm": {}}, {"llm": {"base_url": 5}}, {"llm": "x"}, {"llm": {"base_url": "ftp://x"}}):
            with self.subTest(config=config):
                self.assertEqual(settings.config_warnings(config), [])

    def test_el_comando_lo_imprime_al_arrancar(self):
        from anka.cli import app
        config = {"llm": {"base_url": "http://192.168.1.9:11434"}}
        with patch.object(app, "load_config", return_value=config), patch("sys.stderr", new=io.StringIO()) as salida:
            app.warn_unknown_config_keys()
        self.assertIn("192.168.1.9", salida.getvalue())


# Cubre: U-61
class AuditoriaDeDependenciasEnElCI(unittest.TestCase):
    def test_el_workflow_corre_pip_audit_sobre_los_lock(self):
        texto = (RAIZ / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
        self.assertRegex(texto, r"pip[_-]audit")
        self.assertIn("requirements.lock", texto)
        self.assertIn("requirements-dev.lock", texto)

    def test_no_es_una_dependencia_del_proyecto(self):
        for nombre in ("requirements.txt", "requirements-dev.txt", "requirements.lock", "requirements-dev.lock"):
            self.assertNotIn("pip-audit", (RAIZ / nombre).read_text(encoding="utf-8").lower(), nombre)

    def test_el_workflow_tiene_tiempo_limite_y_cancela_corridas_viejas(self):
        texto = (RAIZ / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
        self.assertIn("concurrency:", texto)
        self.assertIn("cancel-in-progress: true", texto)
        self.assertGreaterEqual(texto.count("timeout-minutes:"), 2)

    def test_la_nota_para_el_desarrollador_existe(self):
        self.assertIn("pip-audit", (RAIZ / "docs" / "ESTANDAR_CAMBIOS.md").read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
