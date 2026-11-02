"""Lectura por tramos (T-25): división sin pérdidas, hallazgos, caché, consolidación y fallos. Cubre: T-25"""

from tests import _isolate  # noqa: F401  (debe ir primero)

import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from anka.dominio import work_contract
from anka.dominio.errores import ContextoInsuficiente, ErrorDeAnka, SalidaDesbordada
from anka.dominio import reception
from anka.dominio.tramos import compendio, dividir_texto, estimar_tokens, unir_hallazgos
from anka.infra import cache_hallazgos, llm, settings
from anka.servicios import lectura_por_tramos
from tests.test_app import complete_analysis, encoded_message


def hallazgo(dato, valor="1", localizador="doc.pdf, página 1", tema="alcance", actividad="", cita=""):
    return {"tema": tema, "actividad": actividad, "dato": dato, "valor": valor, "localizador": localizador,
            "cita": cita}


class DividirTextoTests(unittest.TestCase):
    def test_a_short_text_is_a_single_chunk(self):
        self.assertEqual(dividir_texto("hola", 1000), ["hola"])

    def test_every_line_of_a_long_text_lands_in_some_chunk_and_none_is_too_big(self):
        texto = "\n".join(f"Línea {n}: dato del alcance número {n}" for n in range(3000))
        tramos = dividir_texto(texto, 5000)
        self.assertGreater(len(tramos), 10)
        self.assertTrue(all(len(tramo) <= 5000 for tramo in tramos))
        cubiertas = {linea for tramo in tramos for linea in tramo.split("\n")}
        self.assertTrue(all(linea in cubiertas for linea in texto.split("\n")))
        self.assertTrue(tramos[0].startswith("Línea 0:"))
        self.assertTrue(tramos[-1].endswith("número 2999"))

    def test_chunks_overlap_so_a_fact_on_the_border_is_not_split(self):
        texto = "\n".join(f"Línea {n} " + "x" * 40 for n in range(400))
        tramos = dividir_texto(texto, 3000, solape=500)
        for anterior, siguiente in zip(tramos, tramos[1:], strict=False):
            ultima_del_anterior = anterior.split("\n")[-1]
            self.assertIn(ultima_del_anterior, siguiente)

    def test_it_prefers_to_cut_before_a_page_header(self):
        paginas = [f"[Página {n}]\n" + ("texto de la página. " * 30 + "\n") * 3 for n in range(1, 20)]
        tramos = dividir_texto("\n".join(paginas), 4000, solape=0)
        self.assertTrue(all(tramo.startswith("[Página") for tramo in tramos[1:]))

    def test_a_single_endless_line_is_cut_hard_without_looping_forever(self):
        tramos = dividir_texto("a" * 10_000, 3000, solape=100)
        self.assertGreater(len(tramos), 3)
        self.assertTrue(all(len(tramo) <= 3000 for tramo in tramos))
        self.assertEqual(max(len(tramo) for tramo in tramos), 3000)

    def test_a_tiny_chunk_size_is_rejected(self):
        with self.assertRaises(ValueError):
            dividir_texto("x" * 5000, 100)


class HallazgosTests(unittest.TestCase):
    def test_unir_removes_repeats_from_the_overlap_but_keeps_distinct_findings(self):
        a = hallazgo("Área del parque", "544.5 ha")
        repetido = hallazgo("  área del PARQUE ", "544.5  ha")
        otro = hallazgo("Área de la bahía", "1 ha")
        self.assertEqual(unir_hallazgos([[a], [repetido, otro]]), [a, otro])

    def test_compendio_keeps_the_locator_and_the_quote(self):
        texto = compendio([hallazgo("Área", "12 ha", "ET.pdf, página 3", "magnitud", "Parque", "El área es de 12 ha")])
        self.assertEqual(texto, "- [magnitud] Parque: Área: 12 ha | ET.pdf, página 3 | «El área es de 12 ha»")

    def test_token_estimate_is_conservative(self):
        self.assertGreaterEqual(estimar_tokens("x" * 1000), 400)


class ModeloTests(unittest.TestCase):
    def test_limit_depends_on_the_provider(self):
        self.assertEqual(llm.limite_de_tokens({"provider": "ollama"}), llm.DEFAULT_NUM_CTX_MAX)
        self.assertEqual(llm.limite_de_tokens({"provider": "ollama", "num_ctx_max": 65536}), 65536)
        self.assertEqual(llm.limite_de_tokens({"provider": "openai"}), llm.DEFAULT_CONTEXT_TOKENS)
        self.assertEqual(llm.limite_de_tokens({"provider": "openai", "context_tokens": 300000}), 300000)

    def test_fits_or_not(self):
        config = {"provider": "ollama", "num_ctx_max": 8192}
        self.assertTrue(llm.cabe_en_el_modelo("x" * 5000, config))
        self.assertFalse(llm.cabe_en_el_modelo("x" * 20000, config))

    def test_chunk_size_never_exceeds_what_the_model_can_take(self):
        self.assertEqual(llm.caracteres_por_tramo({"provider": "ollama"}), llm.DEFAULT_CHUNK_CHARS_LOCAL)
        self.assertEqual(llm.caracteres_por_tramo({"provider": "openai"}), llm.DEFAULT_CHUNK_CHARS)
        pequena = llm.caracteres_por_tramo({"provider": "ollama", "num_ctx_max": 8192})
        self.assertLess(pequena, llm.DEFAULT_CHUNK_CHARS_LOCAL)
        self.assertEqual(llm.caracteres_por_tramo({"provider": "ollama", "chunk_chars": 5000}), 5000)

    def test_extraction_does_not_repeat_the_same_prompt_and_fails_loudly(self):
        # con temperatura 0 repetir el prompt daría el mismo fallo: reintentar es cosa de quien divide el tramo
        valida = {"hallazgos": [{"tema": "alcance", "actividad": "", "dato": "d", "valor": "v",
                                 "localizador": "l", "cita": "c"}]}
        with patch.object(llm, "generar_json", return_value=valida) as call:
            self.assertEqual(llm.extraer_hallazgos("p", {})[0]["dato"], "d")
        self.assertEqual(call.call_count, 1)
        for mala in (ValueError("json roto o truncado"), {"otra": 1}):
            with self.subTest(mala=repr(mala)):
                efecto = {"side_effect": mala} if isinstance(mala, Exception) else {"return_value": mala}
                with patch.object(llm, "generar_json", **efecto) as call:
                    with self.assertRaises(ErrorDeAnka):
                        llm.extraer_hallazgos("p", {})
                self.assertEqual(call.call_count, 1)

    def test_ollama_output_is_capped_so_a_runaway_generation_ends(self):
        captured = {}

        def fake_urlopen(request, timeout):
            import json
            captured["options"] = json.loads(request.data.decode("utf-8"))["options"]
            raise TimeoutError("timed out")

        with patch("urllib.request.urlopen", fake_urlopen):
            with self.assertRaises(RuntimeError) as caught:
                llm.generar_json("p", {}, {"provider": "ollama", "base_url": "http://h:1", "timeout_seconds": 7})
        self.assertEqual(captured["options"]["num_predict"], llm.OUTPUT_RESERVE_TOKENS)
        self.assertIn("no respondió en 7 s", str(caught.exception))

    def test_a_runaway_output_is_its_own_error_and_a_real_context_overflow_is_not(self):
        def respuesta(**campos):
            import json
            lectura = MagicMock()
            lectura.__enter__.return_value.read.return_value = json.dumps(
                {"response": "{", "done_reason": "length", **campos}).encode()
            return lectura

        config = {"provider": "ollama", "base_url": "http://h:1"}
        with patch("urllib.request.urlopen", return_value=respuesta(eval_count=llm.OUTPUT_RESERVE_TOKENS)):
            with self.assertRaises(SalidaDesbordada) as caught:
                llm.generar_json("p", {}, config)
        self.assertIn("bucle de repetición", str(caught.exception))
        with patch("urllib.request.urlopen", return_value=respuesta(eval_count=100)):
            with self.assertRaises(ContextoInsuficiente):
                llm.generar_json("p", {}, config)

    def test_extra_options_reach_ollama_only_when_requested(self):
        import json
        enviadas = []

        def fake(request, timeout):
            enviadas.append(json.loads(request.data.decode())["options"])
            lectura = MagicMock()
            lectura.__enter__.return_value.read.return_value = b'{"response": "{}"}'
            return lectura

        config = {"provider": "ollama", "base_url": "http://h:1"}
        with patch("urllib.request.urlopen", fake):
            llm.generar_json("p", {}, config)
            llm.generar_json("p", {}, config, opciones=llm.OPCIONES_ANTI_REPETICION)
        self.assertEqual(enviadas[0]["temperature"], 0)
        self.assertNotIn("repeat_penalty", enviadas[0])
        self.assertEqual(enviadas[1]["repeat_penalty"], 1.2)

    def test_generar_json_uses_the_configured_provider(self):
        with patch.object(llm, "_json_de_ollama", return_value={"a": 1}) as ollama:
            self.assertEqual(llm.generar_json("p", {}, {"provider": "ollama"}), {"a": 1})
        ollama.assert_called_once()
        with patch.object(llm, "_json_de_openai", return_value={"b": 2}) as openai:
            self.assertEqual(llm.generar_json("p", {}, {"provider": "openai"}), {"b": 2})
        openai.assert_called_once()
        with self.assertRaises(ValueError):
            llm.generar_json("p", {}, {"provider": "otro"})  # type: ignore[typeddict-item]

    def test_the_wait_grows_with_the_prompt_size(self):
        config = {"provider": "ollama", "timeout_seconds": 100}
        self.assertEqual(llm.espera_maxima("corto", config), 100)
        largo = "x" * int(10_000 * llm.CHARS_PER_TOKEN)  # ≈ 14 000 tokens con la reserva de salida
        self.assertEqual(llm.espera_maxima(largo, config), 200)
        self.assertEqual(llm.espera_maxima("corto", {"provider": "ollama"}), 180)

    def test_the_findings_schema_is_valid_for_strict_structured_output(self):
        item = llm.HALLAZGOS_SCHEMA["properties"]["hallazgos"]["items"]
        self.assertEqual(sorted(item["required"]), sorted(item["properties"]))
        self.assertIs(item["additionalProperties"], False)


class CacheTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        patcher = patch.object(settings, "DATA_DIR", Path(self.temp.name))
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_roundtrip_and_miss(self):
        clave = cache_hallazgos.clave("prompt", "m1", 1)
        self.assertIsNone(cache_hallazgos.leer(clave))
        cache_hallazgos.guardar(clave, [hallazgo("d")])
        self.assertEqual(cache_hallazgos.leer(clave), [hallazgo("d")])

    def test_the_key_changes_with_prompt_model_and_version(self):
        base = cache_hallazgos.clave("p", "m", 1)
        self.assertEqual(base, cache_hallazgos.clave("p", "m", 1))
        for otra in (("p2", "m", 1), ("p", "m2", 1), ("p", "m", 2)):
            self.assertNotEqual(base, cache_hallazgos.clave(*otra))

    def test_a_damaged_entry_is_a_miss_not_an_error(self):
        clave = cache_hallazgos.clave("p", "m", 1)
        cache_hallazgos.guardar(clave, [])
        (Path(self.temp.name) / "cache_hallazgos" / f"{clave}.json").write_text("{roto", encoding="utf-8")
        self.assertIsNone(cache_hallazgos.leer(clave))

    def test_it_keeps_at_most_the_configured_number_of_entries(self):
        with patch.object(cache_hallazgos, "MAX_ENTRADAS", 3):
            for n in range(6):
                cache_hallazgos.guardar(cache_hallazgos.clave(f"p{n}", "m", 1), [hallazgo(f"d{n}")])
        self.assertEqual(len(list((Path(self.temp.name) / "cache_hallazgos").glob("*.json"))), 3)


CONFIG = {"provider": "ollama", "model": "m1", "num_ctx_max": 8192}  # prompt máximo ≈ 10 000 caracteres


def linea(n):
    return f"Línea {n}: el alcance incluye el levantamiento del sector {n} con precisión de 5 cm"


def adjunto(nombre, lineas, estado="extraido"):
    texto = "\n".join(linea(n) for n in range(lineas))
    return {"filename": nombre, "status": estado, "coverage": "completa", "reason": "", "text": texto,
            "source_message_id": "m1", "extracted_chars": len(texto)}


class OrquestadorTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        patcher = patch.object(settings, "DATA_DIR", Path(self.temp.name))
        patcher.start()
        self.addCleanup(patcher.stop)
        self.messages = [encoded_message("m1", "cliente@example.com", "Solicito cotización del levantamiento.", 1)]
        self.prompts = []

    def extraer(self, prompt, config, anti_repeticion=False):
        self.prompts.append(prompt)
        n = len(self.prompts)
        return [hallazgo(f"dato {n}", str(n), f"ET.pdf, tramo {n}")]

    def analizar(self, attachments, extraer=None, analyze=None):
        analyze = analyze or (lambda *args: complete_analysis())
        with patch.object(llm, "extraer_hallazgos", side_effect=extraer or self.extraer), \
                patch.object(llm, "analyze", side_effect=analyze) as analyze_mock:
            resultado = lectura_por_tramos.analizar_hilo(self.messages, ["ubicación"], CONFIG, attachments)
        return resultado, analyze_mock

    def test_a_thread_that_fits_is_analyzed_in_one_call_without_reading_by_chunks(self):
        resultado, analyze = self.analizar([adjunto("chico.pdf", 5)])
        analyze.assert_called_once()
        self.assertEqual(self.prompts, [])
        self.assertNotIn("lectura_por_tramos", resultado)

    def test_a_long_attachment_is_read_completely_by_chunks_and_the_final_analysis_sees_the_findings(self):
        grande = adjunto("ET.pdf", 600)
        original = dict(grande)
        pequeno = adjunto("nota.txt", 3)
        resultado, analyze = self.analizar([grande, pequeno])
        # todo el texto pasó por el modelo: cada línea está en algún tramo
        leido = "\n".join(self.prompts)
        self.assertTrue(all(linea(n) in leido for n in range(600)))
        # el análisis final recibe el compendio del grande y el pequeño tal cual
        enviados = analyze.call_args.args[3]
        self.assertTrue(enviados[0]["text"].startswith("LECTURA POR TRAMOS"))
        self.assertIn("ET.pdf, tramo 1", enviados[0]["text"])
        self.assertEqual(enviados[1]["text"], pequeno["text"])
        # los adjuntos originales no se tocan (la evidencia se calcula sobre el texto completo)
        self.assertEqual(grande, original)
        traza = resultado["lectura_por_tramos"]
        self.assertEqual([item["adjunto"] for item in traza["adjuntos"]], ["ET.pdf"])
        self.assertEqual(traza["adjuntos"][0]["tramos"], len(self.prompts))
        self.assertEqual(traza["niveles"], 0)

    def test_blocked_attachments_are_never_sent_to_the_model(self):
        bloqueado = adjunto("plano.dwg", 600, estado="bloqueado")
        self.analizar([adjunto("ET.pdf", 600), bloqueado])
        self.assertFalse(any("plano.dwg" in prompt for prompt in self.prompts))

    def test_partially_read_attachments_are_read_too(self):
        self.analizar([adjunto("ET.pdf", 600, estado="lectura_parcial")])
        self.assertTrue(self.prompts)

    def test_rereading_the_same_thread_uses_the_cache(self):
        self.analizar([adjunto("ET.pdf", 600)])
        llamadas = len(self.prompts)
        self.assertGreater(llamadas, 1)
        self.analizar([adjunto("ET.pdf", 600)])
        self.assertEqual(len(self.prompts), llamadas)  # ninguna llamada nueva al modelo

    def test_a_chunk_the_model_cannot_answer_is_split_and_retried(self):
        def extraer(prompt, config, anti_repeticion=False):
            self.prompts.append(prompt)
            if len(prompt) > 6000:  # un modelo que se pierde con tramos largos
                raise ErrorDeAnka("respuesta truncada")
            return [hallazgo(f"dato {len(self.prompts)}", "1", f"ET.pdf, parte {len(self.prompts)}")]

        resultado, analyze = self.analizar([adjunto("ET.pdf", 600)], extraer=extraer)
        enviados = analyze.call_args.args[3][0]["text"]
        self.assertIn("ET.pdf, parte", enviados)
        leido = "\n".join(self.prompts)
        self.assertTrue(all(linea(n) in leido for n in range(600)))  # nada quedó sin leer al dividir
        self.assertGreater(resultado["lectura_por_tramos"]["adjuntos"][0]["hallazgos"], 1)

    def test_a_chunk_that_cannot_be_split_gets_a_last_attempt_that_punishes_repetition(self):
        llamadas = []

        def extraer(prompt, config, anti_repeticion=False):
            llamadas.append(anti_repeticion)
            if not anti_repeticion:
                raise ErrorDeAnka("salida sin fin")
            return [hallazgo("dato", "1", "ET.pdf, página 1")]

        corto = adjunto("ET.pdf", 600)
        corto["text"] = corto["text"][:2500]  # un tramo corto: ya no se puede dividir
        with patch.object(llm, "caracteres_por_tramo", return_value=2500),                 patch.object(lectura_por_tramos, "MIN_TRAMO_REINTENTO", 1500):
            self.analizar([corto, adjunto("otro.txt", 600)], extraer=extraer)
        self.assertIn(True, llamadas)
        self.assertEqual(llamadas[:2], [False, True])  # primero el intento normal, luego el anti-repetición

    def test_a_chunk_that_never_works_even_split_stops_the_analysis(self):
        def siempre_falla(prompt, config, anti_repeticion=False):
            self.prompts.append(prompt)
            raise ErrorDeAnka("no responde")

        with self.assertRaises(ErrorDeAnka):
            self.analizar([adjunto("ET.pdf", 600)], extraer=siempre_falla)
        # 1 intento + 2 divisiones en cadena: no reintenta sin fin
        self.assertLessEqual(len(self.prompts), 1 + 3 + 9 + 9)  # + el último recurso de cada hoja

    def test_a_chunk_that_fails_stops_the_analysis_instead_of_completing_it_with_gaps(self):
        def falla(prompt, config, anti_repeticion=False):
            raise ErrorDeAnka("el modelo no respondió")

        with self.assertRaises(ErrorDeAnka):
            self.analizar([adjunto("ET.pdf", 600)], extraer=falla)

    def test_a_huge_compendium_is_consolidated_until_it_fits(self):
        def extraer(prompt, config, anti_repeticion=False):
            self.prompts.append(prompt)
            if "Estas líneas son hallazgos" in prompt:
                return [hallazgo("consolidado", "1")]
            return [hallazgo(f"dato {len(self.prompts)} " + "z" * 400, "1", f"ET.pdf, {len(self.prompts)}")
                    for _ in range(12)]

        resultado, analyze = self.analizar([adjunto("ET.pdf", 600)], extraer=extraer)
        self.assertGreaterEqual(resultado["lectura_por_tramos"]["niveles"], 1)
        self.assertTrue(any("consolidación" in prompt for prompt in self.prompts))
        self.assertIn("consolidado", analyze.call_args.args[3][0]["text"])

    def test_when_nothing_makes_it_fit_it_refuses_instead_of_cutting(self):
        def extraer(prompt, config, anti_repeticion=False):
            self.prompts.append(prompt)
            return [hallazgo(f"dato {n} " + "z" * 300, "1", f"p{n}") for n in range(60)]

        with self.assertRaises(ContextoInsuficiente):
            self.analizar([adjunto("ET.pdf", 600)], extraer=extraer)

    def test_an_email_too_long_for_the_model_is_refused_with_a_clear_reason(self):
        self.messages = [encoded_message("m1", "c@example.com", "texto del cliente " * 3000, 1)]
        with self.assertRaises(ContextoInsuficiente) as caught:
            self.analizar([adjunto("ET.pdf", 600)])
        self.assertIn("correo", str(caught.exception))


class CoberturaDeWorkTests(unittest.TestCase):
    """Con Work el paquete entrega todo el texto; el contrato avisa si la cobertura declarada no es coherente."""

    def avisos(self, *adjuntos):
        return work_contract.attachment_coverage_warnings({"attachments": list(adjuntos)})

    def test_a_complete_reading_must_leave_a_trace_of_what_was_reviewed(self):
        self.assertEqual(self.avisos({"filename": "ET.pdf", "status": "extraido", "coverage": "completa",
                                      "reviewed_sections": ["páginas 1-40"]}), [])
        self.assertEqual(self.avisos({"filename": "ET.pdf", "coverage": "completa",
                                      "evidence": [{"topic": "a"}]}), [])
        avisos = self.avisos({"filename": "ET.pdf", "status": "Extraído", "coverage": "completa"})
        self.assertEqual(len(avisos), 1)
        self.assertIn("sin secciones revisadas", avisos[0])

    def test_a_partial_or_unreadable_reading_must_say_why(self):
        self.assertEqual(self.avisos({"filename": "plano.dwg", "status": "bloqueado", "coverage": "no_legible",
                                      "reason": "plano CAD"}), [])
        for estado in ("lectura_parcial", "No legible", "bloqueado", "Parcial"):
            with self.subTest(estado=estado):
                avisos = self.avisos({"filename": "x.pdf", "status": estado, "reason": " "})
                self.assertEqual(len(avisos), 1)
                self.assertIn("sin indicar el motivo", avisos[0])

    def test_malformed_payloads_do_not_fail(self):
        self.assertEqual(work_contract.attachment_coverage_warnings({}), [])
        self.assertEqual(work_contract.attachment_coverage_warnings({"attachments": ["x", None, 3]}), [])
        self.assertEqual(work_contract.attachment_coverage_warnings("no es un dict"), [])  # type: ignore[arg-type]


class NotaDeRevisionTests(unittest.TestCase):
    def test_the_review_note_says_which_documents_were_read_by_chunks(self):
        analisis = complete_analysis()
        analisis["lectura_por_tramos"] = {"adjuntos": [{"adjunto": "ET.pdf", "tramos": 5, "hallazgos": 41}],
                                          "niveles": 0}
        nota = reception.review_note(analisis, [])
        self.assertIn("Lectura completa por tramos", nota)
        self.assertIn("ET.pdf: 5 tramos, 41 hallazgos", nota)
        self.assertNotIn("Lectura completa por tramos", reception.review_note(complete_analysis(), []))


class AislamientoDeHilosTests(unittest.TestCase):
    def reviewer(self, process):
        from unittest.mock import MagicMock
        from anka.servicios.pipeline import RevisorDeHilos
        reviewer = RevisorDeHilos({}, MagicMock(), MagicMock(), None, set(), False, True)
        reviewer.pending_thread_ids = lambda: {"t-1", "t-2", "t-3"}  # type: ignore[method-assign]
        reviewer.process_thread = process  # type: ignore[method-assign]
        return reviewer

    def test_a_thread_that_fails_does_not_stop_the_others(self):
        procesados = []

        def process(thread_id, thread):
            if thread_id == "t-1":
                raise RuntimeError("Ollama no respondió")
            procesados.append(thread_id)

        with self.assertLogs("anka.servicios.pipeline", level="ERROR") as logs:
            self.reviewer(process).run()
        self.assertEqual(procesados, ["t-2", "t-3"])
        self.assertTrue(any("t-1" in line and "no se pudo analizar" in line for line in logs.output))

    def test_a_mode_change_still_stops_the_whole_pass(self):
        from anka.dominio.errores import ModoNoPermitido

        def process(thread_id, thread):
            raise ModoNoPermitido("cambió a Work")

        with self.assertRaises(ModoNoPermitido):
            self.reviewer(process).run()


if __name__ == "__main__":
    unittest.main()


class LocalizadorLimpioTests(unittest.TestCase):
    def test_internal_chunk_labels_are_stripped_from_locators(self):
        casos = {
            "Página 10, tramo 2 de 2": "Página 10",
            "ET.pdf, página 3; parte 1 de 3": "ET.pdf, página 3",
            "Hoja Datos, celda B4, consolidación 2 de 5": "Hoja Datos, celda B4",
            "Párrafo 7": "Párrafo 7",
            "": "",
        }
        for sucio, limpio in casos.items():
            with self.subTest(sucio=sucio):
                self.assertEqual(llm._localizador_limpio(sucio), limpio)

    def test_validation_cleans_every_finding_and_the_prompt_warns_the_model(self):
        crudo = {"hallazgos": [{"tema": "alcance", "actividad": "", "dato": "d", "valor": "v",
                                "localizador": "Página 10, tramo 2 de 2", "cita": "c"}]}
        self.assertEqual(llm._validar_hallazgos(crudo)[0]["localizador"], "Página 10")
        self.assertIn("nunca incluyas", llm.hallazgos_prompt("ctx", "a.pdf", "texto", "tramo 1 de 2"))


class LineasRepetidasTests(unittest.TestCase):
    def test_repeated_long_lines_are_read_once_and_short_or_distinct_ones_are_kept(self):
        from anka.dominio.tramos import quitar_lineas_repetidas

        pie = "Grupo Anka - Especificaciones técnicas - Proyecto Llanos 8 - Confidencial"
        texto = "\n".join(["[Página 1]", "Área 100 ha", pie, "[Página 2]", "Área 100 ha", "Área 5 ha", pie, "Total", "Total"])
        limpio, quitadas = quitar_lineas_repetidas(texto)
        self.assertEqual(quitadas, 1)  # solo el pie repetido: «Área 100 ha» y «Total» son cortas
        self.assertEqual(limpio.count(pie), 1)
        self.assertEqual(limpio.count("Área 100 ha"), 2)
        self.assertEqual(limpio.count("Total"), 2)

    def test_whitespace_and_case_do_not_hide_a_repeat_and_nothing_else_changes(self):
        from anka.dominio.tramos import quitar_lineas_repetidas

        linea = "El plazo de ejecución es de 30 días calendario contados desde la orden"
        limpio, quitadas = quitar_lineas_repetidas(f"{linea}\n  {linea.upper()}  \notra línea distinta\n")
        self.assertEqual((quitadas, limpio), (1, f"{linea}\notra línea distinta\n"))

    def test_the_reader_by_chunks_collapses_repeats_before_splitting(self):
        pie = "Grupo Anka - Especificaciones técnicas - Proyecto Llanos 8 - Confidencial"
        texto = "\n".join(f"{pie}\nDato {n}: área de {n} ha en el sector {n}" for n in range(300))
        prompts = []

        def extraer(prompt, config, anti_repeticion=False):
            prompts.append(prompt)
            return [hallazgo("d", "1", "doc.pdf, página 1")]

        with patch.object(llm, "extraer_hallazgos", side_effect=extraer):
            lectura_por_tramos._leer_por_tramos(texto, "doc.pdf", {"provider": "ollama", "model": "m"}, "ctx")
        self.assertEqual("".join(prompts).count(pie), 1)  # el pie, que estaba 300 veces, se lee una sola vez
        self.assertTrue(all(f"Dato {n}:" in "".join(prompts) for n in range(300)))


class CalidadDeHallazgosTests(unittest.TestCase):
    """Defectos que solo se vieron con el modelo real: hallazgos vacíos y localizadores sin contexto de página."""

    def test_empty_findings_the_model_pads_the_schema_with_are_dropped(self):
        crudo = {"hallazgos": [
            {"tema": "precision", "actividad": "", "dato": "", "valor": "", "localizador": "", "cita": ""},
            {"tema": "norma", "actividad": "", "dato": "  ", "valor": " ", "localizador": "p1", "cita": "x"},
            {"tema": "magnitud", "actividad": "", "dato": "Área", "valor": "", "localizador": "p1", "cita": ""},
            {"tema": "plazo", "actividad": "", "dato": "", "valor": "30 días", "localizador": "p2", "cita": ""},
        ]}
        self.assertEqual([h["tema"] for h in llm._validar_hallazgos(crudo)], ["magnitud", "plazo"])

    def test_the_last_page_marker_is_found_so_the_next_chunk_keeps_its_context(self):
        from anka.dominio.tramos import ultimo_encabezado

        texto = "[Página 1]\nuno\n[Página 2]\ndos\nHoja: Datos\ncelda"
        self.assertEqual(ultimo_encabezado(texto), "Hoja: Datos")
        self.assertEqual(ultimo_encabezado("[Página 7; sin texto extraíble]\nx"), "[Página 7; sin texto extraíble]")
        self.assertEqual(ultimo_encabezado("[Diapositiva 3]\ny"), "[Diapositiva 3]")
        self.assertEqual(ultimo_encabezado("sin marcadores\nni uno"), "")

    def test_each_chunk_is_told_which_page_it_continues_in(self):
        texto = "\n".join(["[Página 1]"] + [f"Línea {n} del primer bloque con bastante texto de relleno" for n in range(60)]
                          + ["[Página 2]"] + [f"Línea {n} del segundo bloque con bastante texto de relleno" for n in range(60)])
        prompts = []

        def extraer(prompt, config, anti_repeticion=False):
            prompts.append(prompt)
            return [hallazgo("d", "1", "doc.pdf, página 1")]

        with patch.object(llm, "extraer_hallazgos", side_effect=extraer), \
                patch.object(llm, "caracteres_por_tramo", return_value=1500):
            lectura_por_tramos._leer_por_tramos(texto, "doc.pdf", {"provider": "ollama", "model": "m"}, "ctx")
        self.assertNotIn("continúa dentro de", prompts[0])  # el primero trae su propio marcador
        self.assertTrue(all("continúa dentro de: [Página" in prompt for prompt in prompts[1:]))
        self.assertIn("continúa dentro de: [Página 2]", prompts[-1])

    def test_the_prompt_asks_to_group_repetitive_series(self):
        prompt = llm.hallazgos_prompt("ctx", "a.pdf", "texto", "tramo 1 de 2")
        self.assertIn("agrupa en UNO las series repetitivas", prompt)


class SinAdvertenciasTests(unittest.TestCase):
    def test_the_chunking_module_emits_no_warnings_when_it_is_imported(self):
        # Una expresión regular ambigua (`[[]`) daba un FutureWarning en cada arranque de la línea de comandos. Se prueba en
        # un proceso nuevo: `re` guarda en caché los patrones ya compilados y recargar el módulo no los vuelve a compilar.
        import subprocess
        import sys
        from pathlib import Path

        resultado = subprocess.run(
            [sys.executable, "-W", "error", "-c", "import anka.dominio.tramos"],
            cwd=Path(__file__).resolve().parents[1], capture_output=True, text=True, check=False,
        )
        self.assertEqual(resultado.returncode, 0, resultado.stderr[-400:])
