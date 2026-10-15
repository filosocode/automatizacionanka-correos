"""Los errores de la API tienen una sola forma: `{"error": {"codigo", "mensaje"}}` (U-50, spec/07-api.md)."""

from tests import _isolate  # noqa: F401  (debe ir primero)

import io
import json
import unittest
from pathlib import Path
from unittest.mock import patch

from anka.web import api, openapi
from anka.web import server as web
from tests.test_seguridad_http import Base, atender, formulario

RUTAS_JSON = sorted({r for r in web.GET_ROUTES if api.es_ruta_de_api(r)} - {"/eventos"})
RUTAS_DE_PAGINA = sorted(set(web.GET_ROUTES) - set(RUTAS_JSON) - {"/eventos"})


def error_de(cuerpo):
    datos = json.loads(cuerpo)
    assert set(datos) == {"error"}, datos
    return datos["error"]


def con_forma_unica(prueba, estado, cabeceras, cuerpo, codigo):
    prueba.assertTrue(cabeceras["Content-Type"].startswith("application/json"), cabeceras["Content-Type"])
    error = error_de(cuerpo)
    prueba.assertEqual(error["codigo"], codigo)
    prueba.assertRegex(error["codigo"], r"^[a-z_]+$")
    prueba.assertTrue(error["mensaje"].strip())
    prueba.assertEqual(api.CODIGOS_DE_ERROR[estado], error["codigo"])
    return error


# Cubre: U-50
class ErroresJsonBajoLaApi(Base):
    def test_el_conjunto_de_rutas_json_incluye_todo_lo_que_cuelga_de_api(self):
        self.assertTrue({"/api/casos", "/api/salud", "/api/autorizacion", "/openapi.json", "/state"} <= set(RUTAS_JSON))

    def test_una_ruta_inexistente_bajo_api_es_404_json(self):
        for ruta in ("/api/xyz", "/api/casos/x/y", "/api/", "/api"):
            with self.subTest(ruta=ruta):
                estado, cab, cuerpo, _ = atender("GET", ruta)
                self.assertEqual(estado, 404)
                con_forma_unica(self, estado, cab, cuerpo, "no_encontrado")

    def test_un_caso_inexistente_es_404_json_con_mensaje_en_espanol(self):
        estado, cab, cuerpo, _ = atender("GET", "/api/casos/no-existe")
        error = con_forma_unica(self, estado, cab, cuerpo, "no_encontrado")
        self.assertEqual(error["mensaje"], "La solicitud no existe.")

    def test_un_host_no_permitido_es_403_json_en_todas_las_rutas_de_api(self):
        for ruta in RUTAS_JSON:
            with self.subTest(ruta=ruta):
                estado, cab, cuerpo, _ = atender("GET", ruta, cabeceras={"Host": "evil.example"})
                self.assertEqual(estado, 403)
                con_forma_unica(self, estado, cab, cuerpo, "solicitud_rechazada")

    def test_un_post_a_una_ruta_de_lectura_es_405_json_con_allow(self):
        for ruta in RUTAS_JSON:
            with self.subTest(ruta=ruta):
                estado, cab, cuerpo, _ = atender("POST", ruta, formulario())
                self.assertEqual(estado, 405)
                self.assertEqual(cab["Allow"], "GET, HEAD")
                con_forma_unica(self, estado, cab, cuerpo, "metodo_no_permitido")

    def test_otros_metodos_son_405_json_y_en_ruta_inexistente_404(self):
        for metodo in ("PUT", "DELETE", "PATCH", "OPTIONS"):
            for ruta in RUTAS_JSON:
                with self.subTest(metodo=metodo, ruta=ruta):
                    estado, cab, cuerpo, handler = atender(metodo, ruta)
                    self.assertEqual(estado, 405)
                    self.assertIn("GET", cab["Allow"])
                    con_forma_unica(self, estado, cab, cuerpo, "metodo_no_permitido")
                    self.assertTrue(handler.close_connection)
            estado, cab, cuerpo, _ = atender(metodo, "/api/nada")
            self.assertEqual(estado, 404)
            con_forma_unica(self, estado, cab, cuerpo, "no_encontrado")

    def test_un_metodo_que_http_server_no_conoce_sale_en_json_bajo_api(self):
        _, _, _, handler = atender("GET", "/api/casos")
        handler.path, handler.wfile = "/api/casos", io.BytesIO()
        handler.send_error(501, "Unsupported method ('FOO')")
        cuerpo = handler.wfile.getvalue().partition(b"\r\n\r\n")[2].decode()
        self.assertEqual(error_de(cuerpo)["codigo"], "metodo_no_implementado")

    def test_head_responde_las_mismas_cabeceras_sin_cuerpo(self):
        for ruta in RUTAS_JSON + ["/"]:
            with self.subTest(ruta=ruta):
                estado_g, cab_g, _, _ = atender("GET", ruta)
                estado_h, cab_h, cuerpo_h, _ = atender("HEAD", ruta)
                self.assertEqual(estado_h, estado_g)
                self.assertEqual(cuerpo_h, "")
                self.assertEqual(cab_h["Content-Type"], cab_g["Content-Type"])
                self.assertEqual(cab_h["Content-Length"], cab_g["Content-Length"])

    def test_head_del_canal_de_avisos_no_abre_la_conexion_larga(self):
        estado, cab, cuerpo, _ = atender("HEAD", "/eventos")
        self.assertEqual((estado, cuerpo), (200, ""))
        self.assertTrue(cab["Content-Type"].startswith("text/event-stream"))

    def test_un_fallo_inesperado_es_500_json_con_id_y_sin_detalle_interno(self):
        with patch.object(web, "all_quotes", side_effect=RuntimeError("C:\\secreto token=abc")), \
                self.assertLogs("anka.web.server", "ERROR") as registro:
            estado, cab, cuerpo, _ = atender("GET", "/api/casos")
        self.assertEqual(estado, 500)
        error = con_forma_unica(self, estado, cab, cuerpo, "error_interno")
        self.assertNotIn("secreto", cuerpo)
        self.assertRegex(error["id"], r"^[0-9a-f]{8}$")
        self.assertIn(error["id"], "\n".join(registro.output))
        self.assertIn(error["id"], error["mensaje"])

    def test_un_valor_rechazado_es_400_json(self):
        with patch.object(web, "all_quotes", side_effect=ValueError("no")), \
                self.assertLogs("anka.web.server", "WARNING"):
            estado, cab, cuerpo, _ = atender("GET", "/api/casos")
        self.assertEqual(estado, 400)
        con_forma_unica(self, estado, cab, cuerpo, "solicitud_no_valida")

    def test_la_salud_con_fallos_sigue_siendo_el_informe_con_503(self):
        informe = {"estado": "error", "profundo": False, "comprobaciones": []}
        with patch("anka.web.server.salud.informe", return_value=informe):
            estado, _, cuerpo, _ = atender("GET", "/api/salud")
        self.assertEqual((estado, json.loads(cuerpo)), (503, informe))     # no es un error de la API: es el informe

    def test_el_cupo_lleno_de_avisos_es_503_json(self):
        with patch.object(web._streams, "acquire", return_value=False):
            estado, cab, cuerpo, _ = atender("GET", "/eventos")
        self.assertEqual(estado, 503)
        con_forma_unica(self, estado, cab, cuerpo, "no_disponible")

    def test_un_cuerpo_demasiado_grande_a_una_ruta_de_api_tambien_es_json(self):
        estado, cab, cuerpo, _ = atender("POST", "/api/casos", b"", {"Content-Length": str(10 ** 9)})
        self.assertEqual(estado, 413)
        con_forma_unica(self, estado, cab, cuerpo, "cuerpo_demasiado_grande")

    def test_cada_codigo_http_tiene_un_codigo_de_maquina_distinto(self):
        self.assertEqual(set(api.CODIGOS_DE_ERROR), {400, 403, 404, 405, 413, 500, 501, 503})
        self.assertEqual(len(set(api.CODIGOS_DE_ERROR.values())), len(api.CODIGOS_DE_ERROR))


# Cubre: U-50
class LasPaginasHtmlNoCambian(Base):
    def test_un_error_en_una_pagina_sigue_siendo_html(self):
        estado, cab, cuerpo, _ = atender("GET", "/no-existe")
        self.assertEqual(estado, 404)
        self.assertTrue(cab["Content-Type"].startswith("text/html"))
        self.assertIn("Página no encontrada.", cuerpo)

    def test_una_accion_pedida_con_get_es_405_html_con_allow(self):
        estado, cab, _, _ = atender("GET", "/mode")
        self.assertEqual((estado, cab["Allow"]), (405, "POST"))
        self.assertTrue(cab["Content-Type"].startswith("text/html"))

    def test_un_post_a_una_pagina_es_405_html(self):
        for ruta in RUTAS_DE_PAGINA:
            with self.subTest(ruta=ruta):
                estado, cab, _, _ = atender("POST", ruta, formulario())
                self.assertEqual(estado, 405)
                self.assertTrue(cab["Content-Type"].startswith("text/html"))

    def test_un_post_con_token_invalido_sigue_siendo_403_antes_que_405(self):
        estado, _, _, _ = atender("POST", "/requests", b"csrf_token=mal")
        self.assertEqual(estado, 403)

    def test_un_fallo_en_una_pagina_muestra_el_codigo_en_html(self):
        with patch.object(web.paginas, "render_index", side_effect=RuntimeError("x")), \
                self.assertLogs("anka.web.server", "ERROR"):
            estado, cab, cuerpo, _ = atender("GET", "/")
        self.assertEqual(estado, 500)
        self.assertTrue(cab["Content-Type"].startswith("text/html"))
        self.assertRegex(cuerpo, r"código de error [0-9a-f]{8}")


# Cubre: U-50
class ElEsquemaErrorEstaDocumentado(unittest.TestCase):
    def setUp(self):
        self.spec = openapi.build_spec()

    def test_el_esquema_error_existe_y_lo_usan_las_rutas_json(self):
        esquema = self.spec["components"]["schemas"]["Error"]
        self.assertEqual(esquema["required"], ["error"])
        interno = esquema["properties"]["error"]
        self.assertEqual(set(interno["required"]), {"codigo", "mensaje"})
        self.assertEqual(set(interno["properties"]["codigo"]["enum"]), set(api.CODIGOS_DE_ERROR.values()))
        for ruta in ("/api/casos", "/api/casos/{thread_id}", "/api/salud", "/api/autorizacion", "/state"):
            respuestas = self.spec["paths"][ruta]["get"]["responses"]
            for codigo in ("403", "405", "500"):
                with self.subTest(ruta=ruta, codigo=codigo):
                    media = respuestas[codigo]["content"]["application/json"]
                    self.assertEqual(media["schema"], {"$ref": "#/components/schemas/Error"})
                    self.assertIn("error", media["example"])
        self.assertIn("404", self.spec["paths"]["/api/casos/{thread_id}"]["get"]["responses"])

    def test_los_ejemplos_no_son_el_texto_suelto_de_antes(self):
        self.assertNotIn('{"error": "', json.dumps(self.spec))

    def test_la_documentacion_de_la_api_explica_la_forma(self):
        pagina = (Path(__file__).resolve().parents[1] / "spec" / "07-api.md").read_text(encoding="utf-8")
        self.assertIn('{"error": {"codigo"', pagina)
        for codigo in api.CODIGOS_DE_ERROR.values():
            self.assertIn(codigo, pagina)


if __name__ == "__main__":
    unittest.main()
