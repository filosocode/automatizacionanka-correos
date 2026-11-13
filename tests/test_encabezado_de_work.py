"""El puente acepta el encabezado del borrador aunque Work le cambie tildes, mayúsculas o espacios. Cubre: T-43"""

from tests import _isolate  # noqa: F401  (debe ir primero)

import json
import unittest
from pathlib import Path

from anka.cli import work_sync as sync
from anka.dominio.work_contract import empieza_con_el_encabezado_interno
from tests.test_work_sync import b64, payload

ENCABEZADO = "[REVISAR ANTES DE ENVIAR - BORRAR ESTA SECCION]"
PROMPT = Path(__file__).resolve().parents[1] / "docs" / "PROMPT_WORK_ANKA_V6.txt"


def borrador(encabezado: str, datos: dict | None = None, thread: str = "T1") -> dict:
    cuerpo = (encabezado + "\nResumen para la persona.\n" + sync.START + "\n" + json.dumps(datos or payload())
              + "\n" + sync.END + "\nHola")
    return {"id": "gm1", "threadId": thread, "internalDate": "1000",
            "payload": {"mimeType": "text/plain", "body": {"data": b64(cuerpo)}}}


class ElEncabezadoTolerante(unittest.TestCase):
    def test_se_acepta_con_tilde_mayusculas_espacios_y_guiones_tipograficos(self) -> None:
        # Cubre: T-43
        for variante in (ENCABEZADO,
                         "[REVISAR ANTES DE ENVIAR - BORRAR ESTA SECCIÓN]",          # el caso real del 2026-10-13
                         "[revisar antes de enviar - borrar esta sección]",
                         "  [REVISAR  ANTES DE ENVIAR  -  BORRAR ESTA SECCION]",
                         "[REVISAR ANTES DE ENVIAR – BORRAR ESTA SECCIÓN]",            # guion largo
                         "[REVISAR ANTES DE ENVIAR — BORRAR ESTA SECCIÓN]"):
            self.assertTrue(empieza_con_el_encabezado_interno(variante + "\ntexto"), variante)

    def test_no_se_acepta_lo_que_no_es_el_encabezado_al_inicio(self) -> None:
        # Cubre: T-43
        for texto in ("", "Hola\n" + ENCABEZADO, "> " + ENCABEZADO, "[REVISAR ANTES DE ENVIAR]", "[REVISAR ESTA SECCION]"):
            self.assertFalse(empieza_con_el_encabezado_interno(texto), repr(texto))


class ElPuente(unittest.TestCase):
    def test_un_borrador_con_el_encabezado_con_tilde_se_importa(self) -> None:
        # Cubre: T-43
        mensaje = borrador("[REVISAR ANTES DE ENVIAR - BORRAR ESTA SECCIÓN]")
        extraido, _ = sync.extract(mensaje, "d1")
        self.assertEqual(extraido["thread_id"], "T1")
        self.assertEqual(extraido["draft_id"], "d1")

    def test_el_resultado_es_el_mismo_que_con_el_encabezado_exacto(self) -> None:
        # Cubre: T-43
        con_tilde = sync.extract(borrador("[REVISAR ANTES DE ENVIAR - BORRAR ESTA SECCIÓN]"), "d1")[0]
        exacto = sync.extract(borrador(ENCABEZADO), "d1")[0]
        self.assertEqual(con_tilde, exacto)

    def test_un_bloque_dentro_de_un_texto_que_no_empieza_con_el_encabezado_se_sigue_rechazando(self) -> None:
        # Cubre: T-43
        with self.assertRaisesRegex(ValueError, "sección interna inicial"):
            sync.extract(borrador("Hola, te escribo por el proyecto."), "d1")
        with self.assertRaisesRegex(ValueError, "sección interna inicial"):
            sync.extract(borrador("> " + ENCABEZADO), "d1")           # un bloque citado en una respuesta

    def test_con_el_encabezado_con_tilde_y_sin_bloque_se_sigue_reportando(self) -> None:
        # Cubre: T-43
        cuerpo = "[REVISAR ANTES DE ENVIAR - BORRAR ESTA SECCIÓN]\nSolo texto, sin el bloque."
        mensaje = {"id": "gm1", "threadId": "T1", "internalDate": "1000",
                   "payload": {"mimeType": "text/plain", "body": {"data": b64(cuerpo)}}}
        with self.assertRaisesRegex(ValueError, sync.SIN_BLOQUE):
            sync.extract(mensaje, "d1")

    def test_bloques_repetidos_se_siguen_rechazando(self) -> None:
        # Cubre: T-43
        cuerpo = (ENCABEZADO.replace("SECCION", "SECCIÓN") + "\n" + (sync.START + "{}" + sync.END + "\n") * 2)
        mensaje = {"id": "gm1", "threadId": "T1", "internalDate": "1000",
                   "payload": {"mimeType": "text/plain", "body": {"data": b64(cuerpo)}}}
        with self.assertRaisesRegex(ValueError, "repetidos o incompletos"):
            sync.extract(mensaje, "d1")


class ElPrompt(unittest.TestCase):
    def test_pide_copiar_el_encabezado_tal_cual_y_sin_tilde(self) -> None:
        # Cubre: T-43
        texto = PROMPT.read_text(encoding="utf-8")
        self.assertIn("cópiala tal cual, sin tilde en SECCION", texto)
        self.assertIn(ENCABEZADO, texto)
        self.assertLess(len(texto), 14500)


if __name__ == "__main__":
    unittest.main()
