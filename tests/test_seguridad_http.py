"""Cabeceras de seguridad, límites del cuerpo, errores sin detalle interno y nombres de descarga (T-07, T-31, D-71)."""

from tests import _isolate  # noqa: F401  (debe ir primero)

import io
import re
import tempfile
import unittest
import urllib.parse
from pathlib import Path
from unittest.mock import patch

from anka.dominio.errores import ErrorDeAnka
from anka.infra import settings
from anka.web import seguridad
from anka.web import server as web

HOST = "127.0.0.1:8765"


def atender(metodo, ruta, cuerpo=b"", cabeceras=None):
    """Atiende la petición con el manejador real, hasta los bytes que saldrían por el socket."""
    handler = web.ReviewHandler.__new__(web.ReviewHandler)
    handler.headers = {"Host": HOST, "Content-Length": str(len(cuerpo)), **(cabeceras or {})}
    handler.path = ruta
    handler.command = metodo
    handler.request_version = "HTTP/1.1"
    handler.requestline = f"{metodo} {ruta} HTTP/1.1"
    handler.client_address = ("127.0.0.1", 1)
    handler.rfile = io.BytesIO(cuerpo)
    handler.wfile = io.BytesIO()
    getattr(handler, "do_" + metodo)()
    crudo = handler.wfile.getvalue()
    cabecera, _, resto = crudo.partition(b"\r\n\r\n")
    lineas = cabecera.decode("latin-1").split("\r\n")
    estado = int(lineas[0].split()[1])
    vistas = dict(linea.split(": ", 1) for linea in lineas[1:])
    return estado, vistas, resto.decode("utf-8", "replace"), handler


def formulario(**campos):
    return urllib.parse.urlencode({"csrf_token": seguridad.CSRF_TOKEN, **campos}).encode()


class Base(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        configuracion = {"active_mode": "local", "required_fields": []}
        for parche in (patch.object(settings, "DATA_DIR", Path(temp.name)),
                       patch("anka.web.paginas.load_config", return_value=configuracion),
                       patch("anka.web.pantalla_caso.load_config", return_value=configuracion),
                       patch("anka.web.server.load_config", return_value=configuracion)):
            parche.start()
            self.addCleanup(parche.stop)


# Cubre: T-07
class CabecerasDeSeguridad(Base):
    ESPERADAS = {"X-Content-Type-Options": "nosniff", "Referrer-Policy": "same-origin", "Cache-Control": "no-store",
                 "X-Frame-Options": "DENY"}

    def comprobar(self, cabeceras):
        for nombre, valor in self.ESPERADAS.items():
            self.assertEqual(cabeceras.get(nombre), valor, nombre)
        self.assertIn("frame-ancestors 'none'", cabeceras["Content-Security-Policy"])
        self.assertIn("default-src 'self'", cabeceras["Content-Security-Policy"])

    def test_las_paginas_json_y_errores_llevan_todas_las_cabeceras(self):
        for ruta in ("/", "/state", "/no-existe", "/openapi.json", "/docs"):
            with self.subTest(ruta=ruta):
                _, cabeceras, _, _ = atender("GET", ruta)
                self.comprobar(cabeceras)

    def test_la_csp_de_las_paginas_no_permite_nada_de_fuera(self):
        _, cabeceras, _, _ = atender("GET", "/")
        csp = cabeceras["Content-Security-Policy"]
        self.assertEqual(csp, seguridad.CSP_PAGINAS)
        for directiva in ("object-src 'none'", "base-uri 'none'", "form-action 'self'", "connect-src 'self'"):
            self.assertIn(directiva, csp)
        self.assertNotIn("http", csp)                       # ningún origen externo en el dashboard

    def test_las_plantillas_no_tienen_manejadores_de_eventos_en_linea(self):
        # La CSP permite <script> en línea pero no protege de `onclick=`: T-07 los prohíbe y esto lo vigila.
        carpeta = Path(web.__file__).parent / "templates"
        for plantilla in carpeta.glob("*.html"):
            with self.subTest(plantilla=plantilla.name):
                self.assertIsNone(re.search(r"\son[a-z]+\s*=", plantilla.read_text(encoding="utf-8")))

    def test_la_redireccion_303_tambien_las_lleva(self):
        with patch.dict(web.POST_ROUTES, {"/mode": lambda form: "/?message=ok"}):
            estado, cabeceras, _, _ = atender("POST", "/mode", formulario(mode="local"))
        self.assertEqual(estado, 303)
        self.assertTrue(cabeceras["Location"].startswith("/?message=ok&firma="), cabeceras["Location"])      # U-61
        self.comprobar(cabeceras)

    def test_el_canal_de_avisos_tambien_las_lleva(self):
        with patch.object(web, "STREAM_SECONDS", 0):
            estado, cabeceras, _, _ = atender("GET", "/eventos")
        self.assertEqual(estado, 200)
        self.assertTrue(cabeceras["Content-Type"].startswith("text/event-stream"))
        self.comprobar(cabeceras)


# Cubre: T-07, T-31
class LimitesDelCuerpo(Base):
    def test_un_cuerpo_de_mas_de_2_mib_es_413_sin_leerlo(self):
        estado, _, pagina, handler = atender("POST", "/mode", b"", {"Content-Length": str(seguridad.MAX_CUERPO + 1)})
        self.assertEqual(estado, 413)
        self.assertTrue(handler.close_connection)
        self.assertIn("demasiado grande", pagina)

    def test_una_longitud_no_numerica_o_negativa_es_400_limpio(self):
        for valor in ("abc", "-5", "1e3", " ", "١٢"):
            with self.subTest(valor=valor), self.assertLogs("anka.web.server", "WARNING"):
                estado, _, pagina, _ = atender("POST", "/mode", b"", {"Content-Length": valor})
                self.assertEqual(estado, 400)
                self.assertNotIn("Traceback", pagina)
                self.assertNotIn("invalid literal", pagina)

    def test_el_limite_justo_se_acepta(self):
        self.assertEqual(seguridad.longitud_del_cuerpo(str(seguridad.MAX_CUERPO)), seguridad.MAX_CUERPO)
        self.assertEqual(seguridad.longitud_del_cuerpo(None), 0)

    def test_un_cuerpo_mal_codificado_es_400_no_500(self):
        with self.assertLogs("anka.web.server", "WARNING"):
            estado, _, _, _ = atender("POST", "/mode", b"csrf_token=\xff\xfe")
        self.assertEqual(estado, 400)


# Cubre: T-07, T-31
class ErroresSinDetalleInterno(Base):
    def test_un_fallo_inesperado_muestra_un_codigo_y_no_el_detalle(self):
        def falla(form):
            raise RuntimeError("C:\\ruta\\secreta token=abc")
        with patch.dict(web.POST_ROUTES, {"/mode": falla}), self.assertLogs("anka.web.server", "ERROR") as registro:
            estado, _, pagina, _ = atender("POST", "/mode", formulario(mode="local"))
        self.assertEqual(estado, 500)
        self.assertNotIn("secreta", pagina)
        codigo = re.search(r"código de error ([0-9a-f]{8})", pagina).group(1)
        self.assertIn(codigo, "\n".join(registro.output))       # el detalle y el código quedan en el registro
        self.assertIn("secreta", "\n".join(registro.output))

    def test_un_fallo_inesperado_en_get_tampoco_muestra_el_detalle(self):
        with patch.object(web.paginas, "render_index", side_effect=KeyError("clave_interna")), \
                self.assertLogs("anka.web.server", "ERROR"):
            estado, _, pagina, _ = atender("GET", "/")
        self.assertEqual(estado, 500)
        self.assertNotIn("clave_interna", pagina)
        self.assertIn("código de error", pagina)

    def test_un_error_de_anka_si_muestra_su_mensaje_para_la_persona(self):
        def falla(form):
            raise ErrorDeAnka("Falta config.json: copia config.example.json.")
        with patch.dict(web.POST_ROUTES, {"/mode": falla}), self.assertLogs("anka.web.server", "ERROR"):
            estado, _, pagina, _ = atender("POST", "/mode", formulario(mode="local"))
        self.assertEqual(estado, 500)
        self.assertIn("Falta config.json", pagina)

    def test_un_valueerror_de_anka_se_muestra_y_uno_de_python_no(self):
        def de_anka(form):
            raise ValueError("La solicitud ya no existe.")

        def de_python(form):
            return str(int("x"))
        with patch.dict(web.POST_ROUTES, {"/mode": de_anka}), self.assertLogs("anka.web.server", "WARNING"):
            estado, _, pagina, _ = atender("POST", "/mode", formulario(mode="local"))
        self.assertEqual(estado, 400)
        self.assertIn("La solicitud ya no existe.", pagina)
        with patch.dict(web.POST_ROUTES, {"/mode": de_python}), self.assertLogs("anka.web.server", "WARNING"):
            estado, _, pagina, _ = atender("POST", "/mode", formulario(mode="local"))
        self.assertEqual(estado, 400)
        self.assertNotIn("invalid literal", pagina)
        self.assertIn("Los datos enviados no son válidos.", pagina)

    def test_la_clasificacion_de_mensajes(self):
        para_la_persona = [ValueError("Elige otro hilo."), ValueError("¿Seguro?"), ErrorDeAnka("lo que sea"),
                           ValueError("Ese hilo no se puede unir: x. Usa «No es el mismo proyecto».")]
        internos = [ValueError("invalid literal for int() with base 10: 'x'"), ValueError("Invalid isoformat string: 'x'"),
                    ValueError(""), UnicodeDecodeError("utf-8", b"\xff", 0, 1, "invalid start byte"),
                    KeyError("Clave."), RuntimeError("Fallo.")]
        for exc in para_la_persona:
            self.assertTrue(seguridad.es_mensaje_para_la_persona(exc), exc)
        for exc in internos:
            self.assertFalse(seguridad.es_mensaje_para_la_persona(exc), repr(exc))


# Cubre: T-07
class NombreDeLaDescarga(Base):
    def test_el_id_de_la_url_no_llega_crudo_a_content_disposition(self):
        with patch.object(web, "get_quote", return_value={"thread_id": "x"}), \
                patch.object(web, "build_workbook", return_value=b"xlsx"):
            estado, cabeceras, _, _ = atender("GET", "/export.xlsx?thread_id=" + urllib.parse.quote('a"b\r\nX: 1;/ñ'))
        self.assertEqual(estado, 200)
        nombre = re.fullmatch(r'attachment; filename="(.+)"', cabeceras["Content-Disposition"]).group(1)
        self.assertRegex(nombre, r"^actividades-[A-Za-z0-9_-]+-\d{8}-\d{4}\.xlsx$")
        self.assertNotIn("X", cabeceras)

    def test_nombre_seguro(self):
        self.assertEqual(seguridad.nombre_seguro('18f3a9c2"; x=\r\n/../ñ'), "18f3a9c2x")


if __name__ == "__main__":
    unittest.main()
