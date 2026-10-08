"""Pruebas contra un Ollama REAL: la lectura por tramos con el modelo que se usa en producción. Cubre: T-25

Se omiten salvo que se pidan, porque tardan minutos y dependen del modelo instalado:

    set ANKA_PRUEBA_OLLAMA=1
    .venv\\Scripts\\python.exe -m unittest tests.test_ollama_real -v

Existen porque con modelos simulados no se ven los fallos que solo ocurren con un modelo real (salidas sin fin,
esperas cortas, etiquetas internas copiadas al localizador). Las aserciones son deliberadamente holgadas: un modelo
de 7B no es determinista entre versiones; lo que se exige es que la lectura sea completa y la salida, válida.
Correrlas cuando cambien los prompts, el esquema de hallazgos, el modelo o los límites de `llm`.
"""

from tests import _isolate  # noqa: F401  (debe ir primero)

import json
import os
import unittest
import urllib.request
from unittest.mock import patch

from anka.infra import llm
from anka.servicios import lectura_por_tramos
from tests.test_app import encoded_message

DATOS_CLAVE = ("100 ha", "5 km", "1 ha", "30 d")  # datos que aparecen una sola vez en PAGINAS
ACTIVADA = os.getenv("ANKA_PRUEBA_OLLAMA") == "1"

def pagina(numero, hecho):
    """Una página con 20 líneas de relleno todas distintas (las repetidas se colapsan) y un dato clave."""
    relleno = "\n".join(
        f"Sector {numero}.{k}: se ejecutará el replanteo de los puntos de control {numero}{k} en la zona norte del predio."
        for k in range(1, 21)
    )
    return f"[Página {numero}]\n{relleno}\n{hecho}"


PAGINAS = [
    pagina(1, "El parque solar ocupa un área de 100 ha en Villavicencio, Meta, y requiere levantamiento topográfico "
              "con GNSS RTK y LiDAR."),
    pagina(2, "La línea de interconexión tiene una longitud de 5 km y se entregarán nube de puntos clasificada, MDT "
              "y ortofoto georreferenciada."),
    pagina(3, "La bahía de ampliación abarca 1 ha. El plazo de ejecución es de 30 días calendario desde la orden de "
              "inicio."),
]


def configuracion_de_prueba():
    """El modelo a probar (`ANKA_OLLAMA_MODEL`, por defecto el de producción), con una ventana y unos tramos pequeños
    que obligan a leer por tramos. No usa `config.json`: las pruebas están aisladas de los datos reales."""
    return {
        "provider": "ollama",
        "model": os.getenv("ANKA_OLLAMA_MODEL", "qwen2.5:7b"),
        "base_url": os.getenv("ANKA_OLLAMA_URL", "http://127.0.0.1:11434"),
        "num_ctx_max": 8192,
        "chunk_chars": 1500,
    }


@unittest.skipUnless(ACTIVADA, "pruebas con Ollama real: defina ANKA_PRUEBA_OLLAMA=1")
class OllamaRealTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.config = configuracion_de_prueba()
        try:
            with urllib.request.urlopen(cls.config["base_url"].rstrip("/") + "/api/tags", timeout=5) as respuesta:
                modelos = [m["name"] for m in json.loads(respuesta.read())["models"]]
        except OSError as exc:
            raise unittest.SkipTest(f"Ollama no responde en {cls.config['base_url']}: {exc}") from exc
        if cls.config["model"] not in modelos:
            raise unittest.SkipTest(f"El modelo {cls.config['model']} no está descargado en Ollama.")

    def test_findings_from_a_chunk_have_a_valid_shape_and_clean_locators(self):
        prompt = llm.hallazgos_prompt("Asunto del correo: Llanos 8", "ET.pdf", PAGINAS[1][-1400:], "tramo 2 de 3")
        hallazgos = llm.extraer_hallazgos(prompt, self.config)
        self.assertGreaterEqual(len(hallazgos), 1)
        for hallazgo in hallazgos:
            self.assertIn(hallazgo["tema"], llm.TEMAS_DE_HALLAZGO)
            self.assertNotRegex(hallazgo["localizador"].casefold(), r"\btramo\s+\d+\s+de\s+\d+")
        valores = " ".join(f"{h['dato']} {h['valor']} {h['cita']}" for h in hallazgos)
        self.assertTrue("5 km" in valores or "20 ha" in valores or "0,50" in valores, valores)

    def test_a_long_document_is_read_completely_by_chunks_with_the_real_model(self):
        mensajes = [encoded_message("m1", "cliente@example.com",
                                    "Solicito cotización del levantamiento del proyecto Llanos 8. Ver especificaciones.", 1)]
        texto = "\n".join(PAGINAS)
        adjunto = {"filename": "ET.pdf", "status": "extraido", "coverage": "completa", "reason": "", "text": texto,
                   "source_message_id": "m1", "extracted_chars": len(texto)}
        self.assertFalse(llm.cabe_en_el_modelo(llm.analysis_prompt(mensajes, ["ubicación del proyecto"], [adjunto]),
                                               self.config), "la prueba debe forzar la lectura por tramos")
        with patch.object(llm, "analyze", wraps=llm.analyze) as analisis:
            resultado = lectura_por_tramos.analizar_hilo(mensajes, ["ubicación del proyecto"], self.config, [adjunto])
        traza = resultado["lectura_por_tramos"]["adjuntos"][0]
        self.assertGreaterEqual(traza["tramos"], 2)
        # Lo que garantiza el sistema: el compendio que recibe el análisis final contiene TODOS los datos del documento.
        compendio = analisis.call_args.args[3][0]["text"].casefold()
        faltan = [dato for dato in DATOS_CLAVE if dato not in compendio]
        self.assertEqual(faltan, [], f"la lectura por tramos dejó fuera {faltan}")
        # Lo que depende del modelo: el análisis final puede perder detalles; se exige solo una cota holgada.
        self.assertTrue(resultado["is_quote_request"])
        self.assertGreaterEqual(len(resultado["activities"]), 1)
        contenido = json.dumps(resultado, ensure_ascii=False).casefold()
        conservados = [dato for dato in DATOS_CLAVE if dato in contenido]
        self.assertGreaterEqual(len(conservados), 2, f"el análisis final solo conserva {conservados}")

if __name__ == "__main__":
    unittest.main()
