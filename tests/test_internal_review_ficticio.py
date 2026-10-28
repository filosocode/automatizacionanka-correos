"""T-103: en un caso ficticio las preguntas pertinentes de `internal_review` cuentan como reconocimiento de un faltante (laboratorio).

PRECISO_V6.1.3 manda que, en un caso ficticio, lo que se preguntaría al cliente va en `internal_review` (ask_client solo si es real).
El evaluador lo reconoce SIN convertirlo en `ask_client` y lo deja para revisión humana. No toca el contrato ni `anka/`.
"""

from __future__ import annotations

from tests import _isolate  # noqa: F401  (debe ir primero)

import copy
import unittest

from laboratorio.banco_documental import evaluador_inverso as ev
from tests.test_evaluador_inverso import (
    AUTORIZADO,
    DOCUMENTOS,
    _bytes,
    _codigos,
    _documento,
    _referencia,
    retorno_sintetico,
)

NUEVO = "faltante_reconocido_solo_en_revision_interna"


def evaluar(caso: str, payload: dict, referencia: dict | None = None) -> dict:
    return ev.evaluar_retorno(_bytes(payload), AUTORIZADO, referencia or _referencia(caso), _documento(caso), DOCUMENTOS[caso],
                              origen_del_retorno="sintetico")


def con_disposicion(caso: str, disposicion: str, solo_ids: set[str] | None = None) -> dict:
    """El retorno sintético con las preguntas de los faltantes `preguntas_cliente` movidas a `disposicion`."""
    payload = retorno_sintetico(caso)
    for faltante, pregunta in zip(_referencia(caso)["faltantes"], payload["question_audit"], strict=True):
        if "preguntas_cliente" in faltante["reconoce_en"] and (solo_ids is None or faltante["id"] in solo_ids):
            pregunta["disposition"] = disposicion
    return payload


def faltantes_de_cliente(caso: str) -> set[str]:
    """Los que la referencia espera solo como pregunta al cliente (si ya admite `revisiones_internas`, no hay nada nuevo que marcar)."""
    return {f["id"] for f in _referencia(caso)["faltantes"]
            if "preguntas_cliente" in f["reconoce_en"] and "revisiones_internas" not in f["reconoce_en"]}


class InternalReviewFicticioTest(unittest.TestCase):
    # Cubre: T-103
    def test_01_base_sin_cambios_ask_client_se_reconoce_sin_marca_humana(self) -> None:
        for caso in ("C1", "C2", "C3"):
            informe = evaluar(caso, retorno_sintetico(caso))
            self.assertNotIn("faltante_sin_reconocer", _codigos(informe), caso)
            self.assertNotIn(NUEVO, _codigos(informe), caso)

    # Cubre: T-103
    def test_02_en_caso_ficticio_internal_review_reconoce_el_faltante_y_queda_para_revision_humana(self) -> None:
        for caso in ("C1", "C2", "C3"):
            informe = evaluar(caso, con_disposicion(caso, "internal_review"))
            self.assertEqual(informe["estado"], ev.EVALUADO, caso)
            self.assertNotIn("faltante_sin_reconocer", _codigos(informe), caso)
            nuevos = [h for h in informe["hallazgos"] if h["codigo"] == NUEVO]
            self.assertEqual({h["ref"] for h in nuevos}, faltantes_de_cliente(caso), caso)
            self.assertTrue(all(h["gravedad"] == "revision_humana" and h["verificacion"] == "humana" for h in nuevos), caso)
            self.assertEqual(informe["errores_criticos"], 0, (caso, _codigos(informe, "critico")))

    # Cubre: T-103
    def test_03_no_se_convierte_internal_review_en_ask_client(self) -> None:
        for caso in ("C1", "C2", "C3"):
            informe = evaluar(caso, con_disposicion(caso, "internal_review"))
            comparable = informe["extraccion_comparable"]
            self.assertEqual(comparable["preguntas_cliente"], [], caso)
            self.assertTrue(comparable["revisiones_internas"], caso)

    # Cubre: T-103
    def test_04_sin_la_pregunta_en_ninguna_parte_sigue_siendo_error(self) -> None:
        payload = con_disposicion("C2", "internal_review")
        payload["question_audit"] = [p for p in payload["question_audit"] if p["disposition"] != "internal_review"]
        informe = evaluar("C2", payload)
        criticos = [h["ref"] for h in informe["hallazgos"] if h["codigo"] == "faltante_sin_reconocer" and h["gravedad"] == "critico"]
        self.assertIn("C2-F1", criticos)          # la ubicación (bloqueante) sin pregunta de ningún tipo
        self.assertEqual(informe["errores_criticos"] >= 1, True)

    # Cubre: T-103
    def test_05_una_pregunta_interna_que_no_toca_el_faltante_no_cuenta(self) -> None:
        payload = con_disposicion("C2", "internal_review")
        for pregunta in payload["question_audit"]:
            if pregunta["disposition"] == "internal_review":
                pregunta["question"] = "¿Hay que confirmar la frecuencia de la ecosonda?"
        informe = evaluar("C2", payload)
        self.assertIn("faltante_sin_reconocer", _codigos(informe))
        self.assertNotIn(NUEVO, _codigos(informe))

    # Cubre: T-103
    def test_06_en_un_caso_real_internal_review_no_sustituye_a_ask_client(self) -> None:
        referencia = copy.deepcopy(_referencia("C2"))
        referencia["ficticio"] = False
        informe = evaluar("C2", con_disposicion("C2", "internal_review"), referencia)
        self.assertIn("faltante_sin_reconocer", _codigos(informe))
        self.assertNotIn(NUEVO, _codigos(informe))
        self.assertGreaterEqual(informe["errores_criticos"], 1)

    # Cubre: T-103
    def test_07_un_faltante_que_no_se_espera_como_pregunta_al_cliente_no_se_ve_afectado(self) -> None:
        referencia = copy.deepcopy(_referencia("C2"))
        for faltante in referencia["faltantes"]:
            faltante["reconoce_en"] = ["revisiones_internas"]          # ya no es una pregunta al cliente
        informe = evaluar("C2", con_disposicion("C2", "internal_review"), referencia)
        self.assertNotIn(NUEVO, _codigos(informe))

    # Cubre: T-103
    def test_08_el_borrador_real_de_t100_no_evaluable_sigue_detenido(self) -> None:
        informe = ev.evaluar_retorno("BORRADOR INTERNO PARA REVISIÓN HUMANA — NO ENVIAR.\n\nTexto libre".encode(), AUTORIZADO,
                                     _referencia("C3"), _documento("C3"), DOCUMENTOS["C3"])
        self.assertEqual(informe["estado"], ev.DETENIDO_RETORNO_INVALIDO)


if __name__ == "__main__":
    unittest.main()
