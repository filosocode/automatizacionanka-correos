"""Cubre: T-39. El prompt corto de Work no se separa del contrato: su ejemplo, sus estados y sus columnas se prueban."""

from tests import _isolate  # noqa: F401  (debe ir primero)

import json
import re
import unittest
from pathlib import Path

from anka.dominio.activities import ACTIVITY_COLUMNS
from anka.dominio.work_contract import (
    load_schema, reconciliar_lectura, reading_summary, usa_verificacion_por_adjuntos, validate, verificar_adjuntos,
)
from anka.servicios import work_import

PROMPT = Path(__file__).resolve().parents[1] / "docs" / "PROMPT_WORK_ANKA_V5.txt"


def texto():
    return PROMPT.read_text(encoding="utf-8")


def ejemplo():
    bloque = re.search(r"\[ANKA_DASHBOARD_JSON_V1\]\s*(.*?)\s*\[/ANKA_DASHBOARD_JSON_V1\]", texto(), re.S)
    return json.loads(bloque.group(1))


class PromptCortoTests(unittest.TestCase):
    def test_es_corto_y_llega_completo(self):
        # Cubre: T-39
        contenido = texto()
        self.assertLess(len(contenido), 12000)
        self.assertTrue(contenido.rstrip().endswith("FIN_PROMPT_ANKA_V5.0"))
        self.assertIn("[REVISAR ANTES DE ENVIAR - BORRAR ESTA SECCION]", contenido)

    def test_el_ejemplo_cumple_el_esquema_y_trae_lo_obligatorio(self):
        # Cubre: T-39
        datos = ejemplo()
        self.assertEqual(validate(datos), [])
        self.assertTrue(set(load_schema()["required"]) <= set(datos))
        self.assertEqual(datos["skill_version"], "PRECISO_V5.0")

    def test_el_ejemplo_usa_las_diez_columnas_de_la_tabla(self):
        # Cubre: T-39
        columnas = [clave for clave, _ in ACTIVITY_COLUMNS]
        for fila in ejemplo()["activities"]:
            self.assertEqual(list(fila), columnas)

    def test_los_estados_del_prompt_son_los_del_esquema(self):
        # Cubre: T-39
        linea = next(x for x in texto().splitlines() if x.startswith("Nueva solicitud ·"))
        self.assertEqual({e.strip() for e in linea.split("·")}, set(load_schema()["properties"]["status"]["enum"]))
        self.assertIn(ejemplo()["status"], linea)

    def test_el_dashboard_importa_el_ejemplo_sin_avisos_de_formato(self):
        # Cubre: T-39
        datos = ejemplo()
        analisis = work_import._work_analysis(datos)
        self.assertEqual(analisis["project_name"], datos["project_name"])
        self.assertEqual(len(analisis["activities"]), 1)
        self.assertEqual(analisis["question_quality_warnings"], [])

    def test_con_el_manifiesto_el_puente_completa_lo_que_el_prompt_no_pide(self):
        # Cubre: T-39
        datos = ejemplo()
        datos["reading_audit"] = {"email_segment_ids_read": ["e1"], "attachment_segment_ids_read": ["a1"]}
        instantanea = {"thread_id": datos["thread_id"], "revision": "rev", "message_ids": [datos["message_id"]],
                       "email_segments": [{"id": "e1", "name": "n1"}],
                       "attachment_segments": [{"id": "a1", "name": "n2"}, {"id": "a2", "name": "n3"}]}
        datos["DRIVE_PREFLIGHT"]["manifest_revision"] = "rev"
        avisos = reconciliar_lectura(datos, instantanea)
        self.assertEqual(datos["reading_audit"]["attachment_segment_ids_pending"], ["a2"])
        self.assertTrue(any("sin lectura verificada" in aviso for aviso in avisos))
        self.assertEqual(reading_summary(datos)["status"], "pendiente")


PROMPT_V6 = Path(__file__).resolve().parents[1] / "docs" / "PROMPT_WORK_ANKA_V6.txt"


def texto_v6():
    return PROMPT_V6.read_text(encoding="utf-8")


def ejemplo_v6():
    bloque = re.search(r"\[ANKA_DASHBOARD_JSON_V1\]\s*(.*?)\s*\[/ANKA_DASHBOARD_JSON_V1\]", texto_v6(), re.S)
    return json.loads(bloque.group(1))


class PromptV6Tests(unittest.TestCase):
    def test_es_corto_llega_completo_y_no_usa_drive(self):
        # Cubre: T-39, T-41
        contenido = texto_v6()
        self.assertLess(len(contenido), 14500)
        self.assertTrue(contenido.rstrip().endswith("FIN_PROMPT_ANKA_V6.1.3"))
        self.assertIn("[REVISAR ANTES DE ENVIAR - BORRAR ESTA SECCION]", contenido)
        for palabra in ("DRIVE_PREFLIGHT", "MANIFIESTO", "reading_audit", "segmento"):
            self.assertNotIn(palabra, contenido)

    def test_pide_abrir_en_raw_lo_que_el_lector_no_admite_o_trunca(self):
        # Cubre: T-41
        contenido = texto_v6()
        for frase in ("read_attachment_supported=false", "content_truncated=true", "formato raw"):
            self.assertIn(frase, contenido)

    def test_el_ejemplo_cumple_el_esquema_y_reporta_el_tamano_de_cada_adjunto(self):
        # Cubre: T-39, T-41
        datos = ejemplo_v6()
        self.assertEqual(validate(datos), [])
        self.assertTrue(set(load_schema()["required"]) <= set(datos))
        self.assertEqual(datos["skill_version"], "PRECISO_V6.1.3")
        self.assertTrue(usa_verificacion_por_adjuntos(datos))
        self.assertTrue(datos["attachments"])
        for adjunto in datos["attachments"]:
            self.assertIsInstance(adjunto["size_bytes"], int)
        self.assertNotIn("DRIVE_PREFLIGHT", datos)

    def test_el_ejemplo_usa_las_diez_columnas_y_los_estados_del_esquema(self):
        # Cubre: T-39
        columnas = [clave for clave, _ in ACTIVITY_COLUMNS]
        for fila in ejemplo_v6()["activities"]:
            self.assertEqual(list(fila), columnas)
        linea = next(x for x in texto_v6().splitlines() if x.startswith("Nueva solicitud ·"))
        self.assertEqual({e.strip() for e in linea.split("·")}, set(load_schema()["properties"]["status"]["enum"]))

    def test_el_dashboard_importa_el_ejemplo_sin_avisos_de_formato(self):
        # Cubre: T-39, T-41
        datos = ejemplo_v6()
        analisis = work_import._work_analysis(datos)
        self.assertEqual(len(analisis["activities"]), 1)
        self.assertEqual(analisis["question_quality_warnings"], [])

    def test_define_el_vocabulario_de_evidencia_que_valida_el_puente(self):
        # Cubre: T-41
        contenido = texto_v6()
        for palabra in ("explicit", "partial", "absent", "unreadable", "contradictory", "unconfirmed",
                        "client", "anka", "contractor", "unknown"):
            self.assertIn(palabra, contenido)
        for hallazgo in ejemplo_v6()["evidence_register"]:     # y su propio ejemplo la respeta
            self.assertIn(hallazgo["status"], {"explicit", "partial", "absent", "unreadable", "contradictory",
                                               "unconfirmed"})
            self.assertIn(hallazgo["owner"], {"client", "anka", "contractor", "unknown"})

    def test_dice_cuando_usar_cada_estado_de_pregunta_y_no_usa_servicios_externos(self):
        # Cubre: T-41
        contenido = texto_v6()
        for frase in ("«Requiere aclaraciones del cliente»: hay preguntas ask_client",
                      "«Faltan datos»: falta información", "«Borrador preparado»: solo hay un borrador",
                      "Usar visores, convertidores ni servicios externos"):
            self.assertIn(frase, contenido)

    def test_pide_un_solo_borrador_al_final(self):
        # Cubre: T-41
        self.assertIn("UNA sola vez, al final, ya completo y con el bloque JSON", texto_v6())

    def test_el_puente_verifica_el_ejemplo_contra_gmail(self):
        # Cubre: T-41
        datos = ejemplo_v6()
        adjunto = datos["attachments"][0]
        avisos = verificar_adjuntos(datos, [{"filename": adjunto["filename"], "size": adjunto["size_bytes"]}])
        self.assertEqual(avisos, [])
        self.assertEqual(datos["reading_audit"]["verification"], "verificada")


if __name__ == "__main__":
    unittest.main()
