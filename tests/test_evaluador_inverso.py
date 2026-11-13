"""Pruebas del evaluador inverso del contrato Work (T-77, solo laboratorio).

Los retornos de estas pruebas son SINTÉTICOS (construidos desde la referencia v2): no son respuestas de Work y no miden su desempeño.
"""

from __future__ import annotations

import copy
import hashlib
import json
import unittest
from pathlib import Path
from typing import Any

from laboratorio.banco_documental import evaluador_inverso as ev
from laboratorio.banco_documental import mapeador

BANCO = Path(__file__).resolve().parent.parent / "laboratorio" / "banco_documental"
DOCUMENTOS = {"C1": "solicitud_01.pdf", "C2": "tdr_02.pdf", "C3": "cantidades_03.xlsx"}
AUTORIZADO = {"thread_id": "PRUEBA-HILO-T77", "message_id": "PRUEBA-MENSAJE-T77"}


def _referencia(caso: str) -> dict:
    return json.loads((BANCO / "referencias_v2" / f"{caso}.referencia.v2.json").read_text(encoding="utf-8"))


def _documento(caso: str) -> bytes:
    return (BANCO / "documentos" / DOCUMENTOS[caso]).read_bytes()


def _texto_de(grupos: list[list[str]]) -> str:
    return " ".join(grupo[0] for grupo in grupos)


def retorno_sintetico(caso: str) -> dict:
    """Un retorno con la forma del contrato, armado desde la referencia (NO es de Work)."""
    referencia, nombre = _referencia(caso), DOCUMENTOS[caso]
    preguntas = [{"question": _texto_de(f["grupos"]), "reason_missing": "sintético", "reviewed_sources": [nombre],
                  "disposition": "ask_client" if "preguntas_cliente" in f["reconoce_en"] else "internal_review"}
                 for f in referencia["faltantes"]]
    payload = {
        "thread_id": AUTORIZADO["thread_id"], "message_id": AUTORIZADO["message_id"], "client_email": "cliente.ficticio@example.invalid",
        "project_name": f"Sintético {caso}", "status": "Requiere revisión técnica interna", "engine": "chatgpt_work",
        "model": "SINTETICO-T77", "skill_version": "SINTETICO-T77-NO-ES-WORK", "analysis_complete": False,
        "activities": [{"activity": a["nombre"], "quantity": a["cantidad_literal"], "unit": a["unidad_literal"], "entregable": a["entregable"],
                        "fuente": mapeador.formatear_fuente(nombre, {k: v for k, v in a["origen"].items() if k not in ("documento", "parrafo")}),
                        "estado": "pendiente"} for a in referencia["actividades"]],
        "_restricciones": "; ".join(_texto_de(r["grupos"]) for r in referencia["restricciones"]),
        "contradictions": [{"topic": c["tema"] if "tema" in c else "", "details": _texto_de(c["grupos"]), "status": "pendiente"}
                           for c in referencia["contradicciones"]],
        "question_audit": preguntas, "attachments": [{"filename": nombre, "status": "registrado"}]}
    payload["activities"][0]["plazo_restriccion"] = payload.pop("_restricciones")      # las restricciones solo caben en esta columna
    return payload


def _bytes(payload: Any) -> bytes:
    return json.dumps(payload, ensure_ascii=False).encode("utf-8")


def _evaluar(caso: str, payload: dict, **extra: Any) -> dict:
    return ev.evaluar_retorno(_bytes(payload), AUTORIZADO, _referencia(caso), _documento(caso), DOCUMENTOS[caso],
                              origen_del_retorno=extra.pop("origen_del_retorno", "sintetico"), **extra)


def _codigos(informe: dict, gravedad: str | None = None) -> set[str]:
    return {h["codigo"] for h in informe["hallazgos"] if gravedad is None or h["gravedad"] == gravedad}


class ConversionTest(unittest.TestCase):
    # Cubre: T-77
    def test_01_los_retornos_sinteticos_se_convierten_a_estructuras_comparables_y_literales(self) -> None:
        for caso in DOCUMENTOS:
            informe = _evaluar(caso, retorno_sintetico(caso))
            referencia = _referencia(caso)
            self.assertEqual(informe["estado"], ev.EVALUADO, caso)
            comparables = informe["extraccion_comparable"]["actividades"]
            self.assertEqual(len(comparables), len(referencia["actividades"]))
            for obtenida, esperada in zip(comparables, referencia["actividades"], strict=True):
                self.assertEqual((obtenida["cantidad"], obtenida["unidad"]), (esperada["cantidad_literal"], esperada["unidad_literal"]))
                for clave, valor in obtenida["origen"].items():
                    self.assertEqual(str(esperada["origen"][clave]), str(valor), f"{caso} {clave}")
            self.assertEqual(informe["errores_criticos"], 0, (caso, _codigos(informe, "critico")))
            self.assertEqual(informe["actividades_con_valor_y_unidad_correctos"], len(referencia["actividades"]))
            self.assertEqual(informe["fuentes_inconsistentes"], [], caso)
            self.assertIn("NO describe el desempeño de Work", informe["aviso"])

    # Cubre: T-77
    def test_02_las_contradicciones_pasan_literales_y_las_filas_no_se_confirman(self) -> None:
        payload = retorno_sintetico("C1")
        informe = _evaluar("C1", payload)
        self.assertEqual(informe["contradicciones_conservadas"], [c["details"] if not c["topic"] else f"{c['topic']} | {c['details']}"
                                                                   for c in payload["contradictions"]])
        self.assertTrue(informe["contradicciones_conservadas"])
        self.assertEqual({a["estado_work"] for a in informe["extraccion_comparable"]["actividades"]}, {"pendiente"})
        sin_contradicciones = {**payload, "contradictions": []}
        self.assertIn("contradiccion_omitida", _codigos(_evaluar("C1", sin_contradicciones), "critico"))

    # Cubre: T-77
    def test_03_se_detectan_cantidades_y_unidades_incorrectas(self) -> None:
        for caso, campo, valor in (("C1", "quantity", "99,9"), ("C1", "unit", "km"), ("C2", "quantity", "99,9"), ("C3", "quantity", "99.9")):
            payload = retorno_sintetico(caso)
            payload["activities"][0][campo] = valor
            informe = _evaluar(caso, payload)
            self.assertIn("cantidad_o_unidad_incorrecta", _codigos(informe, "critico"), (caso, campo))
            self.assertFalse(informe["sin_errores_criticos_automaticos"])
        trampa = retorno_sintetico("C1")
        trampa["activities"][0]["quantity"] = "21,0"            # el área total de la escritura, no la del levantamiento
        self.assertTrue({"asociacion_incorrecta", "cifra_trampa_como_actividad", "cantidad_o_unidad_incorrecta"} & _codigos(_evaluar("C1", trampa), "critico"))

    # Cubre: T-77
    def test_04_una_fuente_que_contradice_el_documento_se_marca(self) -> None:
        payload = retorno_sintetico("C3")
        payload["activities"][0]["fuente"] = "cantidades_03.xlsx, hoja Resumen, celda C5"        # C5 guarda 3.625, no 33.7
        informe = _evaluar("C3", payload)
        self.assertIn("valor_no_coincide_con_la_fuente", {h["codigo"] for h in informe["fuentes_inconsistentes"]})
        self.assertFalse(informe["sin_errores_criticos_automaticos"])
        payload["activities"][0]["fuente"] = "cantidades_03.xlsx, hoja Fantasma, celda A1"
        self.assertIn("fuente_inexistente", {h["codigo"] for h in _evaluar("C3", payload)["fuentes_inconsistentes"]})
        pdf = retorno_sintetico("C1")
        pdf["activities"][0]["fuente"] = "solicitud_01.pdf, p. 9"
        self.assertIn("fuente_inexistente", {h["codigo"] for h in _evaluar("C1", pdf)["fuentes_inconsistentes"]})

    # Cubre: T-77
    def test_05_no_se_convierte_ni_se_recalcula_y_se_registra_lo_que_se_toca(self) -> None:
        payload = retorno_sintetico("C1")
        payload["activities"][0].update(quantity="18,5 ha", unit="ha")
        payload["activities"][1].update(quantity=2.35, unit="km")
        payload["activities"][2].update(quantity="48250", unit="m2")
        informe = _evaluar("C1", payload)
        comparables = informe["extraccion_comparable"]["actividades"]
        self.assertEqual([comparables[0]["cantidad"], comparables[1]["cantidad"], comparables[2]["cantidad"]], ["18,5", "2.35", "48250"])
        self.assertEqual(comparables[2]["unidad"], "m2", "la unidad no se convierte a m²")
        self.assertEqual(len(informe["transformaciones"]), 2)
        self.assertEqual(comparables[0]["fuente_literal"], payload["activities"][0]["fuente"])

    # Cubre: T-77
    def test_06_el_localizador_se_estructura_sin_inventar(self) -> None:
        self.assertEqual(ev.parsear_fuente("a.pdf, p. 2, tabla Cuadro 1, fila A"), ("a.pdf", {"pagina": 2, "tabla": "Cuadro 1", "fila": "A"}, []))
        self.assertEqual(ev.parsear_fuente("a.xlsx, hoja Resumen, celda C4"), ("a.xlsx", {"hoja": "Resumen", "celda": "C4"}, []))
        self.assertEqual(ev.parsear_fuente("a.pdf, p. 2, numeral 3"), ("a.pdf", {"pagina": 2}, ["numeral 3"]))
        self.assertEqual(ev.parsear_fuente("correo, párrafo 1"), ("correo", {}, ["párrafo 1"]))
        self.assertEqual(ev.parsear_fuente(""), ("", {}, []))
        payload = retorno_sintetico("C1")
        payload["activities"][0]["fuente"] = "solicitud_01.pdf, p. 1, numeral 3"
        informe = _evaluar("C1", payload)
        self.assertTrue(any("numeral 3" in limitacion for limitacion in informe["limitaciones"]))
        self.assertEqual(informe["extraccion_comparable"]["actividades"][0]["fuente_literal"], "solicitud_01.pdf, p. 1, numeral 3")


class VigenciaTest(unittest.TestCase):
    # Cubre: T-77
    def test_10_lo_historico_retirado_o_de_otro_archivo_no_entra_como_vigente(self) -> None:
        payload = retorno_sintetico("C3")
        base = payload["activities"][0]
        payload["activities"] += [
            {**base, "activity": "Levantamiento (borrador)", "quantity": "31.2", "fuente": "cantidades_03.xlsx, hoja Borrador_anterior, celda C2"},
            {**base, "activity": "Actividad retirada", "quantity": "5", "notes": "Ya no se requiere según el cliente"},
            {**base, "activity": "Otro archivo", "quantity": "7", "fuente": "otro.pdf, p. 1"},
            {**base, "activity": "Del correo", "quantity": "9", "fuente": "correo, párrafo 1"},
            "texto suelto sin estructura"]
        informe = _evaluar("C3", payload, hojas_no_vigentes=frozenset({"Borrador_anterior"}))
        motivos = " | ".join(d["motivo"] for d in informe["actividades_descartadas"])
        self.assertEqual(len(informe["actividades_descartadas"]), 5)
        for texto in ("no vigente", "retiró", "otro.pdf", "correo", "fila escrita como texto"):
            self.assertIn(texto, motivos)
        cantidades = [a["cantidad"] for a in informe["extraccion_comparable"]["actividades"]]
        for ajena in ("31.2", "5", "7", "9"):
            self.assertNotIn(ajena, cantidades)
        self.assertEqual(informe["errores_criticos"], 0)
        self.assertNotIn("actividad_no_esperada", _codigos(informe))

    # Cubre: T-77
    def test_11_si_no_se_declara_la_hoja_historica_el_valor_aparece_y_el_comparador_lo_ve(self) -> None:
        payload = retorno_sintetico("C3")
        payload["activities"].append({**payload["activities"][0], "activity": "Levantamiento de lotes", "quantity": "31.2",
                                      "fuente": "cantidades_03.xlsx, hoja Borrador_anterior, celda C2"})
        informe = _evaluar("C3", payload)
        self.assertEqual(len(informe["actividades_descartadas"]), 0)
        self.assertIn("31.2", [a["cantidad"] for a in informe["extraccion_comparable"]["actividades"]])
        self.assertIn("cifra_trampa_como_actividad", _codigos(informe, "critico"), "sin declarar la hoja, el comparador sí ve el valor histórico")
        self.assertFalse(informe["sin_errores_criticos_automaticos"])

    # Cubre: T-77
    def test_12_los_duplicados_se_cuentan_y_no_inflan_la_tabla(self) -> None:
        payload = retorno_sintetico("C1")
        payload["activities"].append(copy.deepcopy(payload["activities"][0]))
        informe = _evaluar("C1", payload)
        self.assertEqual(len(informe["actividades_duplicadas"]), 1)
        self.assertEqual(len(informe["extraccion_comparable"]["actividades"]), len(_referencia("C1")["actividades"]))

    # Cubre: T-77
    def test_13_la_evidencia_visual_sigue_sin_certificar(self) -> None:
        payload = retorno_sintetico("C2")
        payload["attachments"][0].update(visual_interpretation="no_certificada")
        informe = _evaluar("C2", payload)
        self.assertEqual(informe["evidencia_visual_declarada"], [{"archivo": "tdr_02.pdf", "estado": "no_certificada", "texto": ""}])
        self.assertEqual(informe["extraccion_comparable"]["elementos_visuales"], [])
        payload["attachments"][0]["visual_interpretation"] = "Cifra impresa tachada con corrección manuscrita 41,8 ha"
        payload["scope_dependencies"] = [{"description": "Lectura visual no certificada: sello", "status": "pendiente"}]
        informe = _evaluar("C2", payload)
        self.assertEqual(informe["evidencia_visual_declarada"][0]["estado"], "no_certificada")
        self.assertEqual(len(informe["extraccion_comparable"]["elementos_visuales"]), 2)
        self.assertTrue(informe["para_revision_humana"] >= 1)
        self.assertEqual({h["verificacion"] for h in informe["hallazgos"] if h["gravedad"] == "revision_humana"}, {"humana"})


class DetencionTest(unittest.TestCase):
    # Cubre: T-77
    def test_20_un_identificador_distinto_o_ausente_detiene_la_evaluacion(self) -> None:
        payload = retorno_sintetico("C1")
        casos = [({**payload, "thread_id": "OTRO"}, AUTORIZADO), ({**payload, "message_id": "OTRO"}, AUTORIZADO),
                 (payload, {"thread_id": AUTORIZADO["thread_id"]}), (payload, {"thread_id": "", "message_id": ""}), (payload, {})]
        for retorno, autorizado in casos:
            informe = ev.evaluar_retorno(_bytes(retorno), autorizado, _referencia("C1"), _documento("C1"), "solicitud_01.pdf")
            self.assertEqual(informe["estado"], ev.DETENIDO_IDENTIDAD)
            self.assertNotIn("hallazgos", informe)
            self.assertNotIn("extraccion_comparable", informe)
            self.assertFalse(informe["sin_errores_criticos_automaticos"])

    # Cubre: T-77
    def test_21_un_retorno_corrupto_o_invalido_no_se_analiza(self) -> None:
        valido = _bytes(retorno_sintetico("C1"))
        original_c3 = (BANCO / "evidencias_t72_t74" / "respuesta_work_C3.raw.txt").read_bytes()
        sin_estado = {k: v for k, v in retorno_sintetico("C1").items() if k != "status"}
        estado_raro = {**retorno_sintetico("C1"), "status": "Cotización lista"}
        for contenido, esperado in ((valido[:200], "sintaxis"), (b"", "sintaxis"), (b"\xef\xbb\xbf" + valido, "sintaxis"), (valido.replace(b'"thread_id"', b'"thread_id": "x", "thread_id"', 1), "sintaxis"),
                                    (original_c3, "sintaxis"), (b"[1, 2]", "la raíz del retorno no es un objeto"),
                                    (_bytes(sin_estado), "esquema"), (_bytes(estado_raro), "esquema")):
            informe = ev.evaluar_retorno(contenido, AUTORIZADO, _referencia("C1"), _documento("C1"), "solicitud_01.pdf")
            self.assertEqual(informe["estado"], ev.DETENIDO_RETORNO_INVALIDO, contenido[:30])
            self.assertEqual(informe["motivo"], esperado)
            self.assertNotIn("hallazgos", informe)
            self.assertEqual(informe["sha256_del_retorno"], hashlib.sha256(contenido).hexdigest())
        self.assertEqual(ev.evaluar_retorno(original_c3, AUTORIZADO, _referencia("C3"))["error_de_sintaxis"]["linea"], 378)

    # Cubre: T-77
    def test_22_la_identidad_se_comprueba_despues_del_formato_y_antes_de_las_actividades(self) -> None:
        sin_estado = {k: v for k, v in retorno_sintetico("C1").items() if k != "status"}
        informe = ev.evaluar_retorno(_bytes({**sin_estado, "thread_id": "OTRO"}), AUTORIZADO, _referencia("C1"))
        self.assertEqual(informe["estado"], ev.DETENIDO_RETORNO_INVALIDO)
        sin_correo = {k: v for k, v in retorno_sintetico("C1").items() if k != "client_email"}
        informe = ev.evaluar_retorno(_bytes(sin_correo), AUTORIZADO, _referencia("C1"))
        self.assertEqual((informe["estado"], informe["motivo"]), (ev.DETENIDO_RETORNO_INVALIDO, "esquema"), "el contrato exige client_email")
        informe = ev.evaluar_retorno(_bytes({**retorno_sintetico("C1"), "thread_id": "OTRO"}), AUTORIZADO, _referencia("C1"))
        self.assertEqual(informe["estado"], ev.DETENIDO_IDENTIDAD)

    # Cubre: T-77
    def test_23_una_clasificacion_que_no_avanza_detiene_la_evaluacion(self) -> None:
        for clasificacion in ("REVISION_MANUAL", "NO_SOLICITUD"):
            informe = _evaluar("C1", {**retorno_sintetico("C1"), "classification": clasificacion})
            self.assertEqual(informe["estado"], ev.DETENIDO_CLASIFICACION, clasificacion)
        self.assertEqual(_evaluar("C1", {**retorno_sintetico("C1"), "classification": "SOLICITUD_NUEVA"})["estado"], ev.EVALUADO)


class PerdidasYLimitesTest(unittest.TestCase):
    # Cubre: T-77
    def test_30_se_registran_los_campos_no_comparables_y_las_perdidas(self) -> None:
        payload = retorno_sintetico("C1")
        payload["activities"][0].update(ubicacion="Predio ficticio", servicio_tecnica="Topografía")
        payload.update(fields=[{"name": "n", "value": "v", "source": "s"}], missing_data=[{"name": "dato", "type": "critical"}],
                       evidence_register=[], summary="resumen")
        informe = _evaluar("C1", payload)
        self.assertEqual(informe["no_comparables"], {"fields": 1, "missing_data": 1, "summary": 1})
        self.assertEqual(len(informe["perdidas"]), 2)
        self.assertTrue(all("estructura del banco" in p for p in informe["perdidas"]))

    # Cubre: T-77
    def test_31_un_faltante_solo_en_missing_data_se_senala_a_una_persona(self) -> None:
        payload = retorno_sintetico("C1")
        faltante = next(f for f in _referencia("C1")["faltantes"] if f["id"] == "C1-F1")
        payload["question_audit"] = [q for q in payload["question_audit"] if q["question"] != _texto_de(faltante["grupos"])]
        antes = _evaluar("C1", payload)
        self.assertIn("C1-F1", {h["ref"] for h in antes["hallazgos"] if h["codigo"] == "faltante_sin_reconocer"})
        self.assertEqual(antes["pistas_para_revision_humana"], [])
        payload["missing_data"] = [{"name": _texto_de(faltante["grupos"]), "type": "critical"}]
        despues = _evaluar("C1", payload)
        self.assertIn("C1-F1", {p["ref"] for p in despues["pistas_para_revision_humana"]})
        self.assertIn("faltante_sin_reconocer", _codigos(despues, "critico"), "la pista no cambia el resultado automático")

    # Cubre: T-77
    def test_32_el_evaluador_no_altera_el_retorno_ni_inventa_datos(self) -> None:
        payload = retorno_sintetico("C2")
        payload["attachments"][0]["visual_interpretation"] = "no_certificada"
        copia = copy.deepcopy(payload)
        conversion = ev.convertir(payload, AUTORIZADO, "tdr_02.pdf", frozenset(), "C2")
        self.assertEqual(payload, copia)
        for actividad in conversion["comparable"]["actividades"]:
            self.assertNotIn("servicio", actividad)
            self.assertLessEqual(set(actividad), {"nombre", "cantidad", "unidad", "entregable", "origen", "fuente_literal", "estado_work", "notas_work"})
        contenido = _bytes(payload)
        ev.evaluar_retorno(contenido, AUTORIZADO, _referencia("C2"))
        self.assertEqual(contenido, _bytes(payload))

    # Cubre: T-77
    def test_33_el_origen_del_retorno_condiciona_el_aviso(self) -> None:
        payload = retorno_sintetico("C1")
        self.assertIn("NO describe", _evaluar("C1", payload, origen_del_retorno="sintetico")["aviso"])
        self.assertIn("NO describe", ev.evaluar_retorno(_bytes(payload), AUTORIZADO, _referencia("C1"))["aviso"])
        self.assertNotIn("NO describe", _evaluar("C1", payload, origen_del_retorno="work_real")["aviso"])


class AislamientoTest(unittest.TestCase):
    # Cubre: T-77
    def test_40_no_toca_produccion_ni_las_evidencias(self) -> None:
        lineas = (BANCO / "evaluador_inverso.py").read_text(encoding="utf-8").splitlines()
        importaciones = " ".join(linea for linea in lineas if linea.startswith(("import ", "from ")))
        for prohibido in ("sqlite3", "google", "anka.infra", "anka.servicios", "anka.web", "anka.cli", "requests", "urllib"):
            self.assertNotIn(prohibido, importaciones, prohibido)
        for ruta in (Path(__file__).resolve().parent.parent / "anka").rglob("*.py"):
            texto = ruta.read_text(encoding="utf-8")
            self.assertNotIn("from laboratorio", texto, ruta.name)
            self.assertNotIn("import laboratorio", texto, ruta.name)
        manifiesto = json.loads((BANCO / "evidencias_t72_t74" / "manifiesto_evidencias.json").read_text(encoding="utf-8"))
        for entrada in manifiesto["archivos"]:
            self.assertEqual(hashlib.sha256((BANCO / "evidencias_t72_t74" / entrada["archivo"]).read_bytes()).hexdigest(), entrada["sha256"])
        for entrada in manifiesto["referencias_v1"]:
            self.assertEqual(hashlib.sha256((BANCO / "referencias" / entrada["archivo"]).read_bytes()).hexdigest(), entrada["sha256"])

    # Cubre: T-77
    def test_41_el_plan_integrado_explica_el_uso_en_e7(self) -> None:
        plan = (BANCO / "PLAN_PRUEBA_INTEGRADA.md").read_text(encoding="utf-8")
        for texto in ("T-77", "evaluador inverso", "Qué se recupera de Gmail y de Drive", "retorno_sintetico", "work_real"):
            self.assertIn(texto, plan)
        self.assertIn("DISEÑADO, NO EJECUTADO", plan)


if __name__ == "__main__":
    unittest.main()
