"""T-57: una revisión interna no vacía el análisis técnico de una solicitud legible (regresión del PILOTO 6C).

Sin red, sin Gmail, sin Drive y sin base comercial: solo el paquete, el contrato y el importador en memoria.

Cubre: T-57
"""

from tests import _isolate  # noqa: F401  (debe ir primero)

import copy
import json
import re
import tempfile
import unittest
from pathlib import Path

from anka.cli import paquete_work
from anka.cli.paquete_work import (
    EN_REVISION, OBLIGATORIOS, construir, ejemplo_en_revision, ejemplos, incoherencias, verificar_paquete,
)
from anka.dominio.work_contract import validate
from anka.servicios import work_import

PROMPT = paquete_work.PROMPT.read_text(encoding="utf-8")
REVISION = "Requiere revisión técnica interna"


def retorno_vacio_del_6c():
    """La forma del retorno del 6C: SOLICITUD_NUEVA clasificada, pero con la tabla vaciada (histórico)."""
    base = ejemplos(PROMPT)["SOLICITUD_NUEVA"]
    return {**copy.deepcopy(base), "skill_version": "PRECISO_V6.1", "status": REVISION,
            "activities": [], "fields": [], "missing_data": [], "evidence_register": [], "contradictions": [],
            "attachments": [], "analysis_complete": False,
            "question_audit": [{"question": "Validar la vigencia de los datos", "reason_missing": "caso de laboratorio",
                                "reviewed_sources": ["correo"], "question_id": "Q1", "target_finding_ids": [],
                                "disposition": "internal_review", "unresolved": "vigencia"}]}


class ElPromptDistingueRevisionDeBloqueo(unittest.TestCase):
    def test_los_datos_ficticios_no_son_un_bloqueo_y_el_estado_no_es_una_forma(self):
        # Cubre: T-57
        self.assertIn("extrae igual sus datos", PROMPT)
        self.assertIn("no es un BLOQUEO", PROMPT)
        self.assertIn("Es un estado, no una forma de salida: solo el BLOQUEO vacía la tabla", PROMPT)
        self.assertIn("REVISIÓN INTERNA SIN BLOQUEO", PROMPT)
        bloqueo = next(x for x in PROMPT.splitlines() if x.startswith("BLOQUEO"))
        for condicion in ("no puedes leer", "identidad", "REVISION_MANUAL"):
            self.assertIn(condicion, bloqueo)
        self.assertNotIn("ficticio", bloqueo)

    def test_la_regla_de_revision_interna_conserva_sin_inventar_ni_contactar(self):
        # Cubre: T-57
        regla = next(x for x in PROMPT.splitlines() if x.startswith("REVISIÓN INTERNA SIN BLOQUEO"))
        for frase in ("conserva activities", "fuente", "pendiente", "unconfirmed", "analysis_complete=false",
                      "internal_review", "ask_client solo si es real", "sin texto para el cliente", "publicando"):
            self.assertIn(frase, regla)
        self.assertNotIn("confirmado", regla)

    def test_t52_separa_revision_interna_de_bloqueo_y_las_cuatro_dimensiones(self):
        # Cubre: T-57
        t52 = (paquete_work.PLANTILLAS / "T-52_CLASIFICACION.md").read_text(encoding="utf-8")
        for frase in ("Revisión interna no es bloqueo", "bloqueo documentado", "analysis_complete",
                      "estado_del_trabajo", "instantánea", "No inventes métodos"):
            self.assertIn(frase, t52)


class ElEjemploConservaLaTabla(unittest.TestCase):
    def test_es_contractual_y_conserva_actividades_con_fuente(self):
        # Cubre: T-57
        datos = ejemplo_en_revision(PROMPT)
        self.assertEqual(validate(datos), [])
        self.assertEqual((datos["classification"], datos["status"]), ("SOLICITUD_NUEVA", REVISION))
        self.assertGreaterEqual(len(datos["activities"]), 2)
        for fila in datos["activities"]:
            self.assertEqual(fila["estado"], "pendiente")
            self.assertTrue(fila["fuente"])
        self.assertTrue(datos["fields"] and datos["missing_data"] and datos["evidence_register"])
        self.assertTrue(any(e["status"] == "unconfirmed" for e in datos["evidence_register"]))

    def test_no_autoriza_cotizar_ni_contactar_al_cliente(self):
        # Cubre: T-57
        datos = ejemplo_en_revision(PROMPT)
        self.assertIs(datos["analysis_complete"], False)
        self.assertNotIn("draft_reply", datos)
        self.assertTrue(datos["summary"].startswith("BORRADOR INTERNO — NO ENVIAR"))
        self.assertEqual({q["disposition"] for q in datos["question_audit"]}, {"internal_review"})
        self.assertEqual(incoherencias(datos), [])
        texto = json.dumps(datos, ensure_ascii=False).lower()
        self.assertNotRegex(texto, r"precio|\$|cop |valor total|cotizaci[oó]n por")

    def test_no_inventa_magnitudes_que_el_correo_no_da(self):
        # Cubre: T-57
        sin_dato = ejemplo_en_revision(PROMPT)["activities"][1]
        self.assertEqual((sin_dato["quantity"], sin_dato["unit"], sin_dato["entregable"]), ("", "", ""))
        for fila in ejemplo_en_revision(PROMPT)["activities"]:
            self.assertNotRegex(json.dumps(fila, ensure_ascii=False).lower(), r"gnss|estaci[oó]n total|bdron|blidarb|convencional\b")

    def test_va_en_el_paquete_y_el_paquete_lo_verifica(self):
        # Cubre: T-57
        self.assertIn(f"ejemplo_{EN_REVISION}.json", OBLIGATORIOS)
        with tempfile.TemporaryDirectory() as temp:
            carpeta = Path(temp)
            construir(carpeta)
            escrito = json.loads((carpeta / f"ejemplo_{EN_REVISION}.json").read_text(encoding="utf-8"))
            self.assertEqual(escrito, ejemplo_en_revision(PROMPT))
            self.assertEqual(verificar_paquete(carpeta), [])
            manifiesto = json.loads((carpeta / "manifest.json").read_text(encoding="utf-8"))
            self.assertEqual(manifiesto["prompt"]["version"], "PRECISO_V6.1.2")
            self.assertEqual(len(manifiesto["files"]), len(OBLIGATORIOS) - 1)
            ruta = carpeta / f"ejemplo_{EN_REVISION}.json"
            vaciado = {**escrito, "activities": []}
            ruta.write_text(json.dumps(vaciado, ensure_ascii=False), encoding="utf-8")
            self.assertTrue(any("no puede vaciar" in p for p in verificar_paquete(carpeta)))


class LaGuardaSemantica(unittest.TestCase):
    """Las cuatro clasificaciones y las variantes que antes pasaban sin que nada las detuviera."""

    def test_solicitud_nueva_normal_sigue_sin_incoherencias(self):
        # Cubre: T-57
        self.assertEqual(incoherencias(ejemplos(PROMPT)["SOLICITUD_NUEVA"]), [])

    def test_solicitud_nueva_en_revision_interna_con_tabla_vacia_se_rechaza_como_el_6c(self):
        # Cubre: T-57
        self.assertTrue(any("no puede vaciar" in p for p in incoherencias(retorno_vacio_del_6c())))

    def test_un_bloqueo_documentado_si_puede_vaciar_la_tabla(self):
        # Cubre: T-57
        bloqueado = {**retorno_vacio_del_6c(), "attachments": [
            {"filename": "Plano.dwg", "message_id": "18f0000000000001", "size_bytes": 90112, "read_via": "attachment",
             "status": "bloqueado", "reason": "formato no compatible", "blocked_fields": []}]}
        self.assertEqual(incoherencias(bloqueado), [])

    def test_lectura_parcial_documentada_conserva_la_tabla_y_no_se_declara_completa(self):
        # Cubre: T-57
        parcial = ejemplo_en_revision(PROMPT)
        parcial["attachments"] = [
            {"filename": "TdR.pdf", "message_id": parcial["message_id"], "size_bytes": 1000, "read_via": "attachment",
             "status": "extraido", "reason": "", "coverage": "parcial", "reviewed_sections": ["pp. 1-2"],
             "blocked_fields": [], "evidence": []}]
        self.assertEqual(incoherencias(parcial), [])
        parcial["analysis_complete"] = True
        self.assertTrue(any("completo" in p for p in incoherencias(parcial)))

    def test_un_caso_ficticio_no_pregunta_al_cliente_ni_confirma_con_hallazgos_bloqueantes(self):
        # Cubre: T-57
        pregunta = ejemplo_en_revision(PROMPT)
        pregunta["question_audit"][1]["disposition"] = "ask_client"
        self.assertTrue(any("ask_client" in p for p in incoherencias(pregunta)))
        confirmada = ejemplo_en_revision(PROMPT)
        confirmada["activities"][0]["estado"] = "confirmado"
        self.assertTrue(any("confirmada" in p for p in incoherencias(confirmada)))
        con_texto = {**ejemplo_en_revision(PROMPT), "draft_reply": "Hola"}
        self.assertTrue(any("texto para el cliente" in p for p in incoherencias(con_texto)))

    def test_seguimiento_en_revision_interna_tambien_conserva_la_tabla(self):
        # Cubre: T-57
        seguimiento = {**ejemplo_en_revision(PROMPT), "classification": "SEGUIMIENTO",
                       "summary": "BORRADOR INTERNO — NO ENVIAR. Caso de prueba ficticio: seguimiento con alcance extraído."}
        self.assertEqual(incoherencias(seguimiento), [])
        self.assertTrue(incoherencias({**seguimiento, "activities": []}))

    def test_seguimiento_con_informacion_adicional_sigue_como_antes(self):
        # Cubre: T-57
        seguimiento = ejemplos(PROMPT)["SEGUIMIENTO"]
        self.assertEqual(incoherencias(seguimiento), [])
        self.assertTrue(all(a["estado"] != "pendiente" for a in seguimiento["activities"]))

    def test_revision_manual_y_no_solicitud_siguen_vacias(self):
        # Cubre: T-57
        self.assertEqual(incoherencias(ejemplos(PROMPT)["REVISION_MANUAL"]), [])
        self.assertEqual(incoherencias(ejemplos(PROMPT)["NO_SOLICITUD"]), [])
        self.assertEqual(ejemplos(PROMPT)["REVISION_MANUAL"]["activities"], [])
        self.assertEqual(ejemplos(PROMPT)["NO_SOLICITUD"]["activities"], [])
        self.assertEqual(ejemplos(PROMPT)["NO_SOLICITUD"]["status"], "Descartada")


class LaCompatibilidadSeMantiene(unittest.TestCase):
    def test_los_retornos_historicos_siguen_validando_e_importandose(self):
        # Cubre: T-57
        historicos = {"5B (V6.1, tabla completa)": {**ejemplos(PROMPT)["SOLICITUD_NUEVA"], "skill_version": "PRECISO_V6.1"},
                      "6C (V6.1, tabla vaciada)": retorno_vacio_del_6c()}
        for nombre, datos in historicos.items():
            with self.subTest(nombre):
                self.assertEqual(validate(datos), [])
                analisis = work_import._work_analysis(datos)
                self.assertEqual(analisis["work_skill_version"], "PRECISO_V6.1")
                self.assertEqual(len(analisis["activities"]), len(datos["activities"]))

    def test_el_importador_acepta_la_version_nueva_y_la_trata_como_v6(self):
        # Cubre: T-57
        from anka.dominio.work_contract import usa_verificacion_por_adjuntos
        datos = ejemplo_en_revision(PROMPT)
        self.assertTrue(usa_verificacion_por_adjuntos(datos))
        analisis = work_import._work_analysis(datos)
        self.assertEqual(analisis["work_skill_version"], "PRECISO_V6.1.2")
        self.assertEqual(len(analisis["activities"]), 2)
        self.assertEqual(analisis["question_quality_warnings"], [])

    def test_ni_el_esquema_ni_el_importador_cambiaron_su_contrato(self):
        # Cubre: T-57
        esquema = json.loads(paquete_work.ESQUEMA.read_text(encoding="utf-8"))
        self.assertEqual(esquema["required"], ["thread_id", "message_id", "client_email", "project_name", "status", "engine"])
        self.assertEqual(re.findall(r"PRECISO_V6.1.2", PROMPT).count("PRECISO_V6.1.2") >= 2, True)


if __name__ == "__main__":
    unittest.main()
