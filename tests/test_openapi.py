"""Swagger y OpenAPI (anka/web/openapi.py, spec/07-api.md): documentan las rutas reales y se pueden usar de verdad."""

from tests import _isolate  # noqa: F401  (debe ir primero)

import io
import json
import tempfile
import unittest
import urllib.parse
from pathlib import Path
from unittest.mock import patch

from anka.dominio.status import QuoteStatus
from anka.infra import settings, store
from anka.web import openapi, seguridad
from anka.web import server as web
from anka.web import acciones
from tests.test_app import test_config


def request(method, path, body=b"", host="127.0.0.1:8765", origin=None):
    """Atiende una petición con el manejador real y devuelve lo que habría enviado."""
    handler = web.ReviewHandler.__new__(web.ReviewHandler)
    sent = {}
    handler.headers = {"Host": host, "Content-Length": str(len(body))}
    if origin:
        handler.headers["Origin"] = origin
    handler.path = path
    handler.rfile = io.BytesIO(body)
    handler.client_address = ("127.0.0.1", 1)
    handler._send_bytes = lambda status, ctype, data, extra=None: sent.update(
        status=status, type=ctype, body=data, headers=extra or {})
    handler._redirect = lambda location: sent.update(status=303, location=location)
    getattr(handler, "do_" + method)()
    return sent


# Cubre: T-13
class DocumentationCoversTheRealRoutes(unittest.TestCase):
    def test_every_get_route_is_documented_and_nothing_extra_is(self):
        documented_get, _ = openapi.rutas_documentadas()
        real = set(web.GET_ROUTES) | set(web.GET_PATTERNS)
        self.assertEqual(real - documented_get, set(), "Rutas GET sin documentar en anka/web/openapi.py")
        self.assertEqual(documented_get - real, set(), "Rutas GET documentadas que ya no existen")

    def test_every_post_route_is_documented_and_nothing_extra_is(self):
        _, documented_post = openapi.rutas_documentadas()
        self.assertEqual(set(acciones.POST_ROUTES) - documented_post, set(), "Rutas POST sin documentar")
        self.assertEqual(documented_post - set(acciones.POST_ROUTES), set(), "Rutas POST documentadas que ya no existen")

    def test_every_get_route_points_to_a_real_method(self):
        for method in list(web.GET_ROUTES.values()) + list(web.GET_PATTERNS.values()):
            self.assertTrue(callable(getattr(web.ReviewHandler, method, None)), method)


# Cubre: T-13
class SpecShape(unittest.TestCase):
    def setUp(self):
        self.spec = openapi.build_spec()

    def test_it_is_a_complete_openapi_3_document(self):
        self.assertTrue(self.spec["openapi"].startswith("3."))
        self.assertTrue(self.spec["info"]["title"])
        tags = {t["name"] for t in self.spec["tags"]}
        operation_ids = []
        for path, methods in self.spec["paths"].items():
            for method, operation in methods.items():
                with self.subTest(route=f"{method.upper()} {path}"):
                    self.assertIn(operation["tags"][0], tags)
                    self.assertTrue(operation["summary"] and operation["description"])
                    self.assertTrue(operation["responses"])
                    operation_ids.append(operation["operationId"])
                    for name in [p for p in path.replace("}", "{").split("{")[1::2]]:
                        self.assertIn(name, [p["name"] for p in operation["parameters"] if p["in"] == "path"])
        self.assertEqual(len(operation_ids), len(set(operation_ids)), "operationId repetido")

    def test_every_action_requires_the_csrf_token_but_the_json_does_not_carry_it(self):
        # Cubre: T-07
        for path, methods in self.spec["paths"].items():
            if "post" not in methods:
                continue
            with self.subTest(route=path):
                schema = methods["post"]["requestBody"]["content"]["application/x-www-form-urlencoded"]["schema"]
                self.assertIn("csrf_token", schema["required"])
                self.assertNotIn("default", schema["properties"]["csrf_token"])
                self.assertNotIn("example", schema["properties"]["csrf_token"])

    def test_actions_with_real_effects_say_so_in_their_summary(self):
        for operation in openapi.OPERACIONES:
            flagged = operation.resumen.startswith("⚠")
            with self.subTest(route=operation.ruta):
                self.assertEqual(flagged, operation.efecto in openapi.EFECTOS_EXTERNOS)
        by_route = {o.ruta: o for o in openapi.OPERACIONES if o.metodo == "post"}
        for route in ("/send", "/approve", "/delete", "/refresh"):
            self.assertTrue(by_route[route].resumen.startswith("⚠"), route)

    def test_required_fields_and_allowed_values_come_from_the_real_lists(self):
        post = self.spec["paths"]["/metadata"]["post"]["requestBody"]["content"][
            "application/x-www-form-urlencoded"]["schema"]
        from anka.dominio.caso import CLIENT_TYPES, SECTORS
        self.assertEqual(post["properties"]["client_type"]["enum"], CLIENT_TYPES)
        self.assertEqual(post["properties"]["sector"]["enum"], SECTORS)
        self.assertIn("thread_id", post["required"])
        delete = self.spec["paths"]["/delete"]["post"]["requestBody"]["content"][
            "application/x-www-form-urlencoded"]
        self.assertEqual(delete["schema"]["properties"]["thread_id"]["type"], "array")
        self.assertTrue(delete["encoding"]["thread_id"]["explode"])

    def test_the_case_schema_follows_the_fields_of_the_domain_object(self):
        from dataclasses import fields

        from anka.dominio.caso import Caso
        self.assertEqual(set(self.spec["components"]["schemas"]["Caso"]["properties"]),
                         {f.name for f in fields(Caso)})


# Cubre: T-13
class SwaggerPage(unittest.TestCase):
    def test_the_page_loads_pinned_assets_with_integrity_hashes_and_the_local_spec(self):
        page = openapi.docs_page("TOKEN-DE-PRUEBA", "NONCE1")
        self.assertIn('fetch("/openapi.json"', page)
        self.assertIn('<meta name="anka-csrf" content="TOKEN-DE-PRUEBA">', page)
        self.assertIn('<script nonce="NONCE1">', page)
        self.assertEqual(page.count("<script>"), 0)          # ningún script en línea sin el nonce de la CSP
        self.assertIn(f"swagger-ui-dist@{openapi.SWAGGER_VERSION}", page)
        self.assertEqual(page.count("integrity=\"sha384-"), 2)
        self.assertEqual(page.count('crossorigin="anonymous"'), 2)


# Cubre: T-13
class ServedByTheRealHandler(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.folder = Path(self._tmp.name)
        for target in ("anka.web.paginas.load_config", "anka.web.server.load_config",
                       "anka.web.pantalla_caso.load_config"):
            patcher = patch(target, return_value=test_config())
            patcher.start()
            self.addCleanup(patcher.stop)
        patcher = patch.object(settings, "DATA_DIR", self.folder)
        patcher.start()
        self.addCleanup(patcher.stop)
        db = store.open_db(self.folder)
        store.save(db, "caso-1", "m1", QuoteStatus.WAITING, "ana@acme.com",
                   {"project_name": "Llanos 8", "received_at": "2026-10-01 09:00"})
        store.save(db, "caso-2", "m2", QuoteStatus.IGNORED, "x@y.co", {"project_name": "Ruido"})
        db.close()

    def test_docs_and_spec_are_served(self):
        page = request("GET", "/docs")
        self.assertEqual((page["status"], page["type"].split(";")[0]), (200, "text/html"))
        self.assertIn(b"SwaggerUIBundle", page["body"])
        spec = request("GET", "/openapi.json")
        self.assertEqual(spec["status"], 200)
        self.assertIn("/send", json.loads(spec["body"])["paths"])

    def test_the_token_is_in_the_docs_page_but_never_in_the_json(self):
        # Cubre: T-07
        self.assertNotIn(seguridad.CSRF_TOKEN.encode(), request("GET", "/openapi.json")["body"])
        page = request("GET", "/docs")
        self.assertIn(seguridad.CSRF_TOKEN.encode(), page["body"])
        csp = page["headers"]["Content-Security-Policy"]
        nonce = csp.split("'nonce-")[1].split("'")[0]
        self.assertIn(f'<script nonce="{nonce}">'.encode(), page["body"])
        self.assertNotIn("'unsafe-inline'", csp.split("script-src")[1].split(";")[0])
        self.assertNotEqual(nonce, request("GET", "/docs")["headers"]["Content-Security-Policy"].split("'nonce-")[1]
                            .split("'")[0])                     # un nonce nuevo por respuesta

    def test_the_case_list_hides_discarded_ones_unless_asked(self):
        listed = json.loads(request("GET", "/api/casos")["body"])
        self.assertEqual([c["thread_id"] for c in listed], ["caso-1"])
        self.assertEqual(listed[0]["project_name"], "Llanos 8")
        everything = json.loads(request("GET", "/api/casos?descartados=1")["body"])
        self.assertEqual({c["thread_id"] for c in everything}, {"caso-1", "caso-2"})

    def test_one_case_comes_complete_and_an_unknown_one_is_a_404_in_json(self):
        found = request("GET", "/api/casos/caso-1")
        self.assertEqual(found["status"], 200)
        detail = json.loads(found["body"])
        self.assertEqual((detail["thread_id"], detail["status"]), ("caso-1", "waiting"))
        self.assertIn("analysis", detail)
        missing = request("GET", "/api/casos/no-existe")
        self.assertEqual(missing["status"], 404)
        self.assertIn("error", json.loads(missing["body"]))

    def test_an_id_with_special_characters_is_decoded(self):
        self.assertEqual(request("GET", "/api/casos/" + urllib.parse.quote("a b/c"))["status"], 404)
        self.assertEqual(request("GET", "/api/casos/caso%2D1")["status"], 200)

    def test_the_state_route_still_answers_json(self):
        state = request("GET", "/state")
        self.assertEqual(state["status"], 200)
        self.assertIn("v", json.loads(state["body"]))

    def test_the_documentation_is_only_served_to_local_hosts(self):
        self.assertEqual(request("GET", "/docs", host="atacante.example")["status"], 403)
        self.assertEqual(request("GET", "/openapi.json", host="atacante.example")["status"], 403)
        self.assertEqual(request("GET", "/api/casos", host="atacante.example")["status"], 403)

    def test_an_action_works_with_the_token_the_docs_page_provides_and_not_without_it(self):
        pagina = request("GET", "/docs")["body"].decode("utf-8")
        token = pagina.split('<meta name="anka-csrf" content="')[1].split('"')[0]
        body = urllib.parse.urlencode({"csrf_token": token}).encode()
        done = request("POST", "/notifications/read", body, origin="http://127.0.0.1:8765")
        self.assertEqual(done["status"], 303)
        self.assertIn("message=", done["location"])
        refused = request("POST", "/notifications/read", b"csrf_token=otro", origin="http://127.0.0.1:8765")
        self.assertEqual(refused["status"], 403)

    def test_a_list_field_is_accepted_repeated_as_the_spec_describes(self):
        body = urllib.parse.urlencode(
            [("csrf_token", seguridad.CSRF_TOKEN), ("thread_id", "caso-1"), ("thread_id", "caso-2")]).encode()
        done = request("POST", "/delete", body, origin="http://127.0.0.1:8765")
        self.assertEqual(done["status"], 303)
        self.assertIn("2", urllib.parse.unquote(done["location"]))
        self.assertEqual(json.loads(request("GET", "/api/casos?descartados=1")["body"]), [])


if __name__ == "__main__":
    unittest.main()
