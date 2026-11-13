"""Gemini como proveedor del modo api (U-86): petición, respuesta, errores y validación de la configuración.

Cubre: U-86
"""

from tests import _isolate  # noqa: F401  (debe ir primero)

import io
import json
import unittest
import urllib.error
from unittest.mock import MagicMock, patch

from anka.dominio.errores import ErrorDeAnka, SalidaDesbordada
from anka.infra import llm, settings

ESQUEMA = {"type": "object", "properties": {"a": {"type": "string"}}, "required": ["a"]}


def respuesta(cuerpo: dict) -> MagicMock:
    objeto = MagicMock()
    objeto.read.return_value = json.dumps(cuerpo).encode("utf-8")
    objeto.__enter__.return_value = objeto
    return objeto


def candidato(texto: str, fin: str = "STOP") -> dict:
    return {"candidates": [{"content": {"parts": [{"text": texto}]}, "finishReason": fin}]}


# Cubre: U-86
class JsonDeGemini(unittest.TestCase):
    def test_sin_clave_falla_con_un_mensaje_claro(self):
        with patch.dict("os.environ", {}, clear=False) as env:
            env.pop("GEMINI_API_KEY", None)
            with self.assertRaisesRegex(ErrorDeAnka, "GEMINI_API_KEY"):
                llm._json_de_gemini("hola", ESQUEMA, {"provider": "gemini"})

    def test_una_clave_con_texto_extra_se_rechaza_sin_mostrarla(self):
        with patch.dict("os.environ", {"GEMINI_API_KEY": "AIzaSecreta123 nombre"}),                 patch("urllib.request.urlopen") as abrir:
            with self.assertRaises(ErrorDeAnka) as ctx:
                llm._json_de_gemini("hola", ESQUEMA, {})
        self.assertNotIn("Secreta123", str(ctx.exception))
        abrir.assert_not_called()

    def test_una_raya_larga_dentro_de_la_clave_se_convierte_en_guion(self):
        with patch.dict("os.environ", {"GEMINI_API_KEY": "AQ.Ab8—sKE"}),                 patch("urllib.request.urlopen", return_value=respuesta(candidato('{"a": "x"}'))) as abrir:
            llm._json_de_gemini("hola", ESQUEMA, {})
        self.assertEqual(abrir.call_args.args[0].get_header("X-goog-api-key"), "AQ.Ab8-sKE")

    def test_envia_la_clave_en_la_cabecera_el_esquema_y_devuelve_el_json(self):
        with patch.dict("os.environ", {"GEMINI_API_KEY": "clave-secreta"}), \
                patch("urllib.request.urlopen", return_value=respuesta(candidato('{"a": "x"}'))) as abrir:
            resultado = llm._json_de_gemini("hola", ESQUEMA, {"provider": "gemini", "model": "gemini-x"})
        self.assertEqual(resultado, {"a": "x"})
        peticion = abrir.call_args.args[0]
        self.assertIn("/models/gemini-x:generateContent", peticion.full_url)
        self.assertNotIn("clave-secreta", peticion.full_url)          # la clave no viaja en la dirección
        self.assertEqual(peticion.get_header("X-goog-api-key"), "clave-secreta")
        cuerpo = json.loads(peticion.data)
        self.assertEqual(cuerpo["generationConfig"]["responseJsonSchema"], ESQUEMA)
        self.assertEqual(cuerpo["contents"][0]["parts"][0]["text"], "hola")

    def test_un_error_http_no_revela_la_clave(self):
        error = urllib.error.HTTPError("u", 429, "cuota", {}, io.BytesIO(b"clave-secreta"))
        with patch.dict("os.environ", {"GEMINI_API_KEY": "clave-secreta"}), \
                patch("urllib.request.urlopen", side_effect=error):
            with self.assertRaises(ErrorDeAnka) as ctx:
                llm._json_de_gemini("hola", ESQUEMA, {})
        self.assertIn("429", str(ctx.exception))
        self.assertNotIn("clave-secreta", str(ctx.exception))

    def test_si_un_modelo_falla_prueba_el_siguiente(self):
        def abrir(peticion, timeout=None):
            if "/models/malo:" in peticion.full_url:
                raise urllib.error.HTTPError(peticion.full_url, 503, "saturado", {}, io.BytesIO(b""))
            return respuesta(candidato('{"a": "ok"}'))
        with patch.dict("os.environ", {"GEMINI_API_KEY": "k"}), patch("urllib.request.urlopen", side_effect=abrir):
            resultado = llm._json_de_gemini("p", ESQUEMA, {"model": "malo", "fallback_models": ["bueno"]})
        self.assertEqual(resultado, {"a": "ok"})

    def test_un_error_que_no_es_de_modelo_no_prueba_otros(self):
        llamadas = []

        def abrir(peticion, timeout=None):
            llamadas.append(peticion.full_url)
            raise urllib.error.HTTPError(peticion.full_url, 403, "sin permiso", {}, io.BytesIO(b""))
        with patch.dict("os.environ", {"GEMINI_API_KEY": "k"}), patch("urllib.request.urlopen", side_effect=abrir):
            with self.assertRaisesRegex(ErrorDeAnka, "403"):
                llm._json_de_gemini("p", ESQUEMA, {"model": "a", "fallback_models": ["b"]})
        self.assertEqual(len(llamadas), 1)

    def test_respuesta_vacia_o_cortada(self):
        with patch.dict("os.environ", {"GEMINI_API_KEY": "k"}):
            with patch("urllib.request.urlopen", return_value=respuesta({"promptFeedback": {}})):
                with self.assertRaises(ErrorDeAnka):
                    llm._json_de_gemini("hola", ESQUEMA, {})
            with patch("urllib.request.urlopen", return_value=respuesta(candidato('{"a"', "MAX_TOKENS"))):
                with self.assertRaises(SalidaDesbordada):
                    llm._json_de_gemini("hola", ESQUEMA, {})

    def test_generar_json_enruta_al_proveedor_gemini(self):
        with patch.object(llm, "_json_de_gemini", return_value={"a": "x"}) as gemini:
            self.assertEqual(llm.generar_json("p", ESQUEMA, {"provider": "gemini"}), {"a": "x"})
        gemini.assert_called_once()


# Cubre: U-86
class ConfiguracionConGemini(unittest.TestCase):
    def test_el_modo_api_acepta_gemini_y_rechaza_proveedores_desconocidos(self):
        self.assertEqual(settings._engine_problems({"provider": "gemini", "model": "gemini-3.6-flash"}, "api"), [])
        self.assertTrue(settings._engine_problems({"provider": "otro"}, "api"))
        self.assertTrue(settings._engine_problems({"provider": "gemini"}, "local"))


if __name__ == "__main__":
    unittest.main()


# Cubre: U-88
class InstruccionDeAnalisis(unittest.TestCase):
    def test_pide_documentos_citados_opciones_abiertas_y_requisitos_generales(self):
        prompt = llm.analysis_prompt([], ["ubicación del proyecto"], [])
        for frase in ("que no está entre las fuentes, regístralo en scope_dependencies como pendiente",
                      "una norma pública (decreto, resolución, circular) no es una dependencia",
                      "no elijas: escribe ambas opciones en notes",
                      "conserva en plazo_restriccion los parámetros de calidad",
                      "cópialos en plazo_restriccion o entregable de cada actividad"):
            with self.subTest(frase=frase):
                self.assertIn(frase, prompt)
