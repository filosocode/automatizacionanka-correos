"""CI con límites, `check.bat quick`, la documentación de la API sin deriva, la animación sin foco, el envío como dato y
los protocolos de Gmail y Drive (U-65).

Cubre: U-65
"""

from tests import _isolate  # noqa: F401  (debe ir primero)

import dataclasses
import re
import sys
import unittest
from pathlib import Path

from anka.infra import clientes_google, google_services
from anka.servicios import case_actions
from anka.web import openapi

RAIZ = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RAIZ / "tools"))
import pruebas_rapidas  # noqa: E402


def leer(*partes):
    return RAIZ.joinpath(*partes).read_text(encoding="utf-8")


# Cubre: U-65
class IntegracionContinua(unittest.TestCase):
    def test_todo_trabajo_tiene_tiempo_limite(self):
        texto = leer(".github", "workflows", "ci.yml")
        trabajos = re.findall(r"^  (\w+):\n((?:    .*\n|\n)+)", texto.split("\njobs:\n", 1)[1], re.MULTILINE)
        self.assertGreaterEqual(len(trabajos), 2)
        for nombre, cuerpo in trabajos:
            self.assertIn("timeout-minutes:", cuerpo, f"el trabajo {nombre} no tiene timeout-minutes")

    def test_una_corrida_nueva_cancela_la_vieja_de_la_misma_rama(self):
        texto = leer(".github", "workflows", "ci.yml")
        self.assertRegex(texto, r"concurrency:\n\s+group: .*github\.ref.*\n\s+cancel-in-progress: true")


# Cubre: U-65
class VerificacionRapida(unittest.TestCase):
    def test_check_bat_reconoce_quick_y_deja_el_modo_completo_como_estaba(self):
        texto = leer("check.bat")
        self.assertIn('"%~1"=="quick"', texto)
        self.assertIn("tools\\pruebas_rapidas.py --rapido", texto)
        # el modo completo sigue corriendo todo con cobertura
        self.assertLess(texto.index("quick"), texto.index("coverage run"))
        for paso in ("ruff check", "mypy", "check_spec.py"):
            self.assertLess(texto.index(paso), texto.index('"%~1"=="quick"'), f"quick debe correr {paso} antes")

    def test_todo_modulo_lento_es_un_modulo_de_pruebas_que_existe(self):
        for modulo in pruebas_rapidas.LENTOS:
            self.assertTrue((RAIZ / (modulo.replace(".", "/") + ".py")).exists(), modulo)

    def test_el_equivalente_sin_bat_esta_documentado(self):
        texto = leer("docs", "ESTANDAR_CAMBIOS.md")
        self.assertIn("check.bat quick", texto)
        self.assertIn("python tools/pruebas_rapidas.py --rapido", texto)

    def test_agrupa_por_modulo_y_el_modo_rapido_salta_los_lentos(self):
        import unittest as ut

        class Falsa(ut.TestCase):
            def test_a(self):
                pass

        Falsa.__module__ = "tests.test_ollama_real"
        grupos = pruebas_rapidas.por_modulo(ut.TestSuite([Falsa("test_a")]))
        self.assertEqual(list(grupos), ["tests.test_ollama_real"])
        self.assertIn("tests.test_ollama_real", pruebas_rapidas.LENTOS)


# Cubre: U-65
class DocumentacionDeLaApiAlDia(unittest.TestCase):
    def test_el_documento_nombra_cada_ruta_get_que_documenta_openapi(self):
        spec = leer("spec", "07-api.md")
        lecturas, _ = openapi.rutas_documentadas()
        faltan = sorted(ruta for ruta in lecturas if ruta not in spec)
        self.assertEqual(faltan, [], "spec/07-api.md no nombra estas rutas")

    def test_la_tabla_de_lectura_incluye_los_canales_de_autorizacion_y_avisos(self):
        spec = leer("spec", "07-api.md")
        fila = next(linea for linea in spec.splitlines() if linea.startswith("| **Lectura (JSON)**"))
        for ruta in ("/api/autorizacion", "/eventos", "/api/salud", "/api/casos"):
            self.assertIn(ruta, fila)

    def test_la_politica_de_origen_del_documento_es_la_vigente(self):
        spec = leer("spec", "07-api.md")
        for dicho in ("Sec-Fetch-Site", "puerto", "firma", "/mark-sent"):
            self.assertIn(dicho, spec)

    def test_las_acciones_nuevas_estan_en_las_rutas_reales(self):
        from anka.web import acciones
        _, posts = openapi.rutas_documentadas()
        self.assertEqual(set(acciones.POST_ROUTES), posts)


# Cubre: U-65
class AnimacionSinFoco(unittest.TestCase):
    JS = leer("anka", "web", "templates", "app.js")

    def test_se_detiene_al_perder_el_foco_y_arranca_al_recuperarlo(self):
        self.assertRegex(self.JS, r"window\.addEventListener\('blur', \(\) => \{ soltar\(\); detener\(\); \}\)")
        self.assertIn("window.addEventListener('focus', arrancar);", self.JS)
        self.assertIn("document.hasFocus && !document.hasFocus()", self.JS)

    def test_la_fisica_validada_no_cambio(self):
        self.assertIn("fps: 60, maxDt: 100", self.JS)
        self.assertIn("60 * Math.min(transcurrido, NODOS.maxDt) / 1000", self.JS)


# Cubre: U-65
class EnvioComoDato(unittest.TestCase):
    def test_lo_que_se_anota_tras_enviar_es_un_dato_inmutable(self):
        envio = case_actions.RespuestaEnviada("T1", "m1", "Hola", "a@b.co", "waiting", "2026-10-15T10:00:00-05:00")
        self.assertEqual(envio.preguntas, ())
        self.assertFalse(envio.esperar)
        with self.assertRaises(dataclasses.FrozenInstanceError):
            envio.body = "otro"      # type: ignore[misc]

    def test_la_funcion_ya_no_recibe_nueve_parametros(self):
        import inspect
        self.assertEqual(list(inspect.signature(case_actions._record_sent_reply).parameters), ["quote", "envio"])


# Cubre: U-65
class ProtocolosDeGmailYDrive(unittest.TestCase):
    def test_no_queda_gmail_ni_drive_como_any(self):
        for ruta in (RAIZ / "anka").rglob("*.py"):
            texto = ruta.read_text(encoding="utf-8")
            self.assertNotRegex(texto, r"\b(gmail|drive): Any\b", ruta.name)

    def test_services_devuelve_los_protocolos(self):
        anotaciones = google_services.services.__annotations__["return"]
        self.assertIn("ClienteGmail", anotaciones)
        self.assertIn("ClienteDrive", anotaciones)

    def test_los_dobles_de_prueba_cumplen_los_protocolos(self):
        from tests.fake_drive import FakeDriveTree
        from tests.test_app import FakeGmail
        self.assertTrue(callable(getattr(FakeGmail({"id": "t"}), "users", None)))
        self.assertTrue(callable(getattr(FakeDriveTree({}), "files", None)))
        self.assertEqual(set(clientes_google.ClienteGmail.__dict__) & {"users"}, {"users"})
        self.assertEqual(set(clientes_google.ClienteDrive.__dict__) & {"files"}, {"files"})

    def test_el_glosario_esta_en_la_arquitectura(self):
        spec = leer("spec", "06-arquitectura.md")
        self.assertIn("## Glosario", spec)
        for termino in ("`quote`", "`thread_id`", "**puente**", "**lanzador**", "**huella**"):
            self.assertIn(termino, spec)


if __name__ == "__main__":
    unittest.main()
