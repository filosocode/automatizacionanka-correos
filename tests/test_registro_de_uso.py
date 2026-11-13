"""Cada análisis deja constancia de los tokens que gastó (U-91).

Cubre: U-91
"""

from tests import _isolate  # noqa: F401  (debe ir primero)

import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from anka.infra import llm, registro_de_uso, settings


def respuesta_gemini(uso: dict | None) -> MagicMock:
    cuerpo = {"candidates": [{"content": {"parts": [{"text": '{"a": "x"}'}]}, "finishReason": "STOP"}]}
    if uso is not None:
        cuerpo["usageMetadata"] = uso
    objeto = MagicMock()
    objeto.read.return_value = json.dumps(cuerpo).encode("utf-8")
    objeto.__enter__.return_value = objeto
    return objeto


# Cubre: U-91
class MedicionDeUso(unittest.TestCase):
    def test_sin_medicion_abierta_registrar_no_hace_nada(self):
        registro_de_uso.registrar("gemini", "m", 10, 5)   # no debe lanzar

    def test_suma_las_llamadas_y_ignora_lo_que_no_es_un_numero(self):
        with registro_de_uso.medir({}) as medicion:
            registro_de_uso.registrar("gemini", "m", 100, 20, 30)
            registro_de_uso.registrar("gemini", "m", "raro", None)
            registro_de_uso.registrar("gemini", "m", True, -5)
        self.assertEqual(sum(i.entrada for i in medicion.llamadas), 100)
        self.assertEqual(sum(i.salida for i in medicion.llamadas), 20)
        self.assertEqual(len(medicion.llamadas), 3)

    def test_el_texto_dice_modelo_llamadas_y_tokens_pero_no_inventa_costo(self):
        with registro_de_uso.medir({}) as medicion:
            registro_de_uso.registrar("gemini", "gemini-x", 12000, 800)
        texto = registro_de_uso.texto_del_uso(medicion)
        self.assertIn("gemini-x", texto)
        self.assertIn("1 llamada, 12,000 tokens de entrada y 800 de salida", texto)
        self.assertNotIn("costo", texto)

    def test_con_precios_estima_el_costo(self):
        with registro_de_uso.medir({"precios": {"entrada": 1.0, "salida": 4.0}}) as medicion:
            registro_de_uso.registrar("gemini", "m", 1_000_000, 500_000)
        self.assertIn("costo estimado US$ 3.0000", registro_de_uso.texto_del_uso(medicion))

    def test_precios_incompletos_o_raros_no_producen_costo(self):
        for precios in ({"entrada": 1.0}, {"entrada": "x", "salida": 2}, "caro", [1, 2]):
            with self.subTest(precios=precios), registro_de_uso.medir({"precios": precios}) as medicion:
                registro_de_uso.registrar("gemini", "m", 10, 10)
                self.assertNotIn("costo", registro_de_uso.texto_del_uso(medicion))


# Cubre: U-91
class LibroDeUso(unittest.TestCase):
    def test_anotar_escribe_una_linea_por_analisis_sin_el_texto_del_correo(self):
        with tempfile.TemporaryDirectory() as carpeta, patch.object(settings, "DATA_DIR", Path(carpeta)):
            with registro_de_uso.medir({}) as medicion:
                registro_de_uso.registrar("gemini", "gemini-x", 100, 50, 10)
                registro_de_uso.registrar("gemini", "gemini-x", 200, 70)
            registro_de_uso.anotar("hilo-1", medicion)
            lineas = (Path(carpeta) / registro_de_uso.LIBRO).read_text(encoding="utf-8").splitlines()
        (linea,) = [json.loads(x) for x in lineas]
        self.assertEqual((linea["thread_id"], linea["proveedor"], linea["modelo"]), ("hilo-1", "gemini", "gemini-x"))
        self.assertEqual((linea["llamadas"], linea["tokens_entrada"], linea["tokens_salida"]), (2, 300, 120))
        self.assertEqual(linea["tokens_cacheados"], 10)
        self.assertIsNone(linea["costo_usd"])

    def test_sin_llamadas_no_escribe_nada(self):
        with tempfile.TemporaryDirectory() as carpeta, patch.object(settings, "DATA_DIR", Path(carpeta)):
            with registro_de_uso.medir({}) as medicion:
                pass
            registro_de_uso.anotar("hilo-1", medicion)
            self.assertFalse((Path(carpeta) / registro_de_uso.LIBRO).exists())

    def test_un_fallo_al_escribir_no_frena_el_analisis(self):
        with registro_de_uso.medir({}) as medicion:
            registro_de_uso.registrar("gemini", "m", 1, 1)
        with patch.object(settings, "DATA_DIR", Path("Z:/no/existe/ni/se/puede/crear")), \
                patch("pathlib.Path.mkdir", side_effect=OSError("sin disco")):
            registro_de_uso.anotar("hilo-1", medicion)   # no debe lanzar


# Cubre: U-91
class UsoDeLosProveedores(unittest.TestCase):
    def test_gemini_registra_entrada_salida_razonamiento_y_cacheados(self):
        uso = {"promptTokenCount": 1000, "candidatesTokenCount": 200, "thoughtsTokenCount": 50,
               "cachedContentTokenCount": 300}
        with patch.dict("os.environ", {"GEMINI_API_KEY": "k"}), \
                patch("urllib.request.urlopen", return_value=respuesta_gemini(uso)), \
                registro_de_uso.medir({}) as medicion:
            llm._json_de_gemini("p", {"type": "object"}, {"model": "gemini-x"})
        (llamada,) = medicion.llamadas
        self.assertEqual((llamada.proveedor, llamada.modelo), ("gemini", "gemini-x"))
        self.assertEqual((llamada.entrada, llamada.salida, llamada.cacheados), (1000, 250, 300))

    def test_gemini_sin_datos_de_uso_cuenta_cero(self):
        with patch.dict("os.environ", {"GEMINI_API_KEY": "k"}), \
                patch("urllib.request.urlopen", return_value=respuesta_gemini(None)), \
                registro_de_uso.medir({}) as medicion:
            llm._json_de_gemini("p", {"type": "object"}, {"model": "gemini-x"})
        self.assertEqual((medicion.llamadas[0].entrada, medicion.llamadas[0].salida), (0, 0))

    def test_openai_registra_sus_tokens(self):
        respuesta = SimpleNamespace(output_text='{"a": "x"}', usage=SimpleNamespace(input_tokens=700, output_tokens=90))
        cliente = MagicMock()
        cliente.return_value.responses.create.return_value = respuesta
        with patch.dict("os.environ", {"OPENAI_API_KEY": "k"}), patch("openai.OpenAI", cliente), \
                registro_de_uso.medir({}) as medicion:
            llm._json_de_openai("p", {"type": "object"}, {"model": "gpt-x"}, "analisis")
        self.assertEqual((medicion.llamadas[0].entrada, medicion.llamadas[0].salida), (700, 90))


if __name__ == "__main__":
    unittest.main()
