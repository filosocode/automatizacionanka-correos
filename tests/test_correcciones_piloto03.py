"""Cubre: T-86. Respuestas parciales, adopción íntegra y equivalencias comprobables."""
from tests import _isolate  # noqa: F401

import copy
import base64
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from anka.dominio.activities import limpiar_cantidad_y_unidad
from anka.dominio.preguntas import anotar_envio, conservar_rondas
from anka.dominio.tabla_acumulada import conservar_propuesta, integrar
from anka.infra import settings, store
from anka.servicios.revision_tecnica import resolve_model_proposal
from anka.servicios.work_import import dashboard_upsert


class RespuestasParciales(unittest.TestCase):
    def setUp(self):
        self.anterior = {}
        self.textos = ["¿Qué intervalo entre curvas de nivel necesitan?",
                       "¿Cuál es la fecha límite para recibir los productos?",
                       "¿Pueden compartir el polígono o las coordenadas para delimitar el predio?"]
        anotar_envio(self.anterior, "2026-11-03T11:17:00-05:00", "abcdef123456", self.textos, True)
        self.nuevo = {"received_at": "2026-11-03T11:18:00-05:00", "question_audit": [], "evidence_register": []}
        for i, tema in enumerate(("Intervalo de curvas", "Fecha límite", "Delimitación")):
            self.nuevo["question_audit"].append({"question": tema, "question_id": f"Q{i}",
                "target_finding_ids": [f"E{i}"], "disposition": "do_not_ask",
                "reviewed_sources": ["correo fedcba654321, párrafo 2"],
                "reason_missing": "Respondida explícitamente: cada 0,50 m." if i == 0 else "Sigue pendiente.",
                "unresolved": "" if i == 0 else tema})
            self.nuevo["evidence_register"].append({"id": f"E{i}", "source": "correo fedcba654321, párrafo 2",
                "topic": tema, "owner": "client", "location": "párrafo 2",
                "value": "cada 0,50 m" if i == 0 else "todavía no definido", "blocking": i != 0,
                "status": "unconfirmed"})

    def estados(self):
        conservar_rondas(self.anterior, self.nuevo, "fedcba654321")
        return [p["estado"] for p in self.nuevo["rondas_de_preguntas"][0]["preguntas"]]

    def test_resuelve_solo_la_contestada_con_fuente_y_conserva_identidad(self):
        self.assertEqual(self.estados(), ["resuelta", "sigue_abierta", "sigue_abierta"])
        preguntas = self.nuevo["rondas_de_preguntas"][0]["preguntas"]
        self.assertEqual(preguntas[0]["id"], self.anterior["rondas_de_preguntas"][0]["preguntas"][0]["id"])
        self.assertEqual(preguntas[0]["conciliacion"]["mensaje"], "fedcba654321")
        self.assertEqual(preguntas[0]["conciliacion"]["hallazgos"][0]["value"], "cada 0,50 m")

    def test_omision_do_not_ask_o_fuente_antigua_no_resuelven(self):
        for modo in ("sin_auditoria", "sin_fuente", "solo_disposicion", "ambiguo", "contradiccion"):
            with self.subTest(modo=modo):
                nuevo = copy.deepcopy(self.nuevo)
                if modo == "sin_auditoria":
                    nuevo["question_audit"] = []
                elif modo == "sin_fuente":
                    nuevo["evidence_register"][0]["source"] = "correo anterior"
                elif modo == "solo_disposicion":
                    nuevo["question_audit"][0]["reason_missing"] = "No hace falta preguntar."
                elif modo == "ambiguo":
                    nuevo["question_audit"].append(copy.deepcopy(nuevo["question_audit"][0]))
                else:
                    nuevo["contradictions"] = [{"topic": "intervalo"}]
                conservar_rondas(self.anterior, nuevo, "fedcba654321")
                self.assertEqual(nuevo["rondas_de_preguntas"][0]["preguntas"][0]["estado"], "sin_confirmar")

    def test_respuesta_parcial_no_cierra_la_pregunta(self):
        self.nuevo["evidence_register"][0]["blocking"] = True
        self.assertEqual(self.estados()[0], "sigue_abierta")

    def test_una_respuesta_no_cierra_una_pregunta_con_dos_pedidos(self):
        self.anterior["rondas_de_preguntas"][0]["preguntas"][0]["texto"] = (
            "¿Qué intervalo entre curvas necesitan y qué precisión requieren?")
        self.assertEqual(self.estados()[0], "sin_confirmar")


class TablaYUnidades(unittest.TestCase):
    def test_propuesta_traslada_notas_y_fuentes_sin_pisar_la_vigente(self):
        # una fila confirmada: lo que solo agrega a una fila sin confirmar se aplica solo (U-96)
        vieja = {"activity": "Topografía", "entregable": "Curvas", "notes": "Confirmar intervalo",
                 "fuente": "PDF p. 1", "estado": "confirmado"}
        nueva = vieja | {"entregable": "Curvas cada 0,50 m", "notes": "Intervalo confirmado; fecha pendiente",
                         "fuente": "correo posterior, párrafo 2"}
        resultado = integrar([vieja], [nueva])
        self.assertEqual(resultado.vigente[0], vieja)
        self.assertEqual(resultado.propuesta[0]["notes"], nueva["notes"])
        self.assertIn("correo posterior", resultado.propuesta[0]["fuente"])
        conservada = conservar_propuesta({"activities": [vieja], "pending_model_activities": resultado.propuesta,
                                         "revision_de_tabla": resultado.revisar}, integrar([vieja], [vieja]))
        self.assertEqual(conservada.propuesta[0]["notes"], nueva["notes"])

    def test_notas_solas_requieren_aceptacion(self):
        vieja = {"activity": "Topografía", "notes": "Confirmar intervalo", "fuente": "PDF p. 1", "estado": "confirmado"}
        resultado = integrar([vieja], [vieja | {"notes": "Intervalo confirmado", "fuente": "correo 2"}])
        self.assertEqual(resultado.vigente[0]["notes"], vieja["notes"])
        self.assertEqual(resultado.propuesta[0]["notes"], "Intervalo confirmado")
        self.assertTrue(resultado.revisar)

    # Cubre: U-92
    def test_las_notas_de_una_fila_pendiente_se_renuevan_sin_pedir_revision(self):
        vieja = {"activity": "Topografía", "notes": "Falta la ubicación", "fuente": "PDF p. 1", "estado": "pendiente"}
        resultado = integrar([vieja], [vieja | {"notes": "Ubicación recibida en el KMZ", "fuente": "correo 2"}])
        self.assertEqual(resultado.vigente[0]["notes"], "Ubicación recibida en el KMZ")
        self.assertEqual(resultado.revisar, [])

    def test_normaliza_equivalencia_exacta_preservando_literal_fuente_y_pendientes(self):
        fila = {"quantity": "18.400 m² (1,84 ha)", "unit": "m²; ha (misma área)",
                "notes": "Fecha pendiente", "estado": "pendiente", "fuente": "PDF p. 1"}
        limpiar_cantidad_y_unidad(fila)
        self.assertEqual((fila["quantity"], fila["unit"]), ("18400", "m²"))
        self.assertIn("18.400 m² (1,84 ha)", fila["notes"])
        self.assertIn("Fecha pendiente", fila["notes"])
        self.assertEqual((fila["fuente"], fila["estado"]), ("PDF p. 1", "pendiente"))
        copia = copy.deepcopy(fila)
        limpiar_cantidad_y_unidad(fila)
        self.assertEqual(fila, copia)

    def test_no_convierte_areas_distintas_operaciones_ni_unidades_incompatibles(self):
        for cantidad in ("18.400 m² (2 ha)", "18.400 m² + 1,84 ha", "18.400 m² (1,84 km)",
                         "18.400 m² (1,84 ha) aproximadas"):
            fila = {"quantity": cantidad, "unit": "m²; ha", "estado": "pendiente"}
            limpiar_cantidad_y_unidad(fila)
            self.assertEqual(fila["quantity"], cantidad)
            self.assertEqual(fila["estado"], "pendiente")


class ImportacionYAdopcion(RespuestasParciales):
    def test_retorno_real_actualiza_ronda_y_propuesta_y_conserva_historial(self):
        with tempfile.TemporaryDirectory() as temporal, patch.object(settings, "DATA_DIR", Path(temporal)):
            fila = {"activity": "Topografía", "quantity": "18.400 m² (1,84 ha)",
                    "unit": "m²; ha (misma área)", "estado": "confirmado", "notes": "Confirmar intervalo",
                    "entregable": "Curvas", "fuente": "PDF p. 1"}
            base = {"thread_id": "abcdef112233", "message_id": "abcdef000001",
                    "client_email": "cliente@example.com", "project_name": "Piloto PDF",
                    "received_at": "2026-11-03T11:00:00-05:00",
                    "status": "Requiere revisión técnica interna", "analysis_complete": False,
                    "activities": [fila]}

            def importar(p):
                dashboard_upsert(base64.urlsafe_b64encode(json.dumps(p).encode()).decode(), actualizar_hoja=False)

            importar(base)
            with store.transaccion() as db:
                anterior = json.loads(db.execute("SELECT analysis_json FROM quotes").fetchone()[0])
                anotar_envio(anterior, "2026-11-03T11:17:00-05:00", "abcdef123456", self.textos, True)
                db.execute("UPDATE quotes SET analysis_json=?", (json.dumps(anterior),))
            importar(base | self.nuevo | {"message_id": "fedcba654321", "activities": [fila | {
                "notes": "Intervalo confirmado; fecha y delimitación pendientes", "entregable": "Curvas cada 0,50 m",
                "fuente": "correo fedcba654321, párrafo 2"}]})
            with store.lectura() as db:
                a = json.loads(db.execute("SELECT analysis_json FROM quotes").fetchone()[0])
                self.assertIn("Confirmar intervalo", a["activities"][0]["notes"])
                self.assertIn("Intervalo confirmado", a["pending_model_activities"][0]["notes"])
                self.assertEqual([p["estado"] for p in a["rondas_de_preguntas"][0]["preguntas"]],
                                 ["resuelta", "sigue_abierta", "sigue_abierta"],
                                 repr({k: a.get(k) for k in ("question_audit", "evidence_register", "received_at")}))
            resolve_model_proposal("abcdef112233", True)
            with store.lectura() as db:
                a = json.loads(db.execute("SELECT analysis_json FROM quotes").fetchone()[0])
                self.assertEqual((a["activities"][0]["quantity"], a["activities"][0]["unit"]), ("18400", "m²"))
                self.assertNotIn("Confirmar intervalo", a["activities"][0]["notes"])
                self.assertIn("correo fedcba654321", a["activities"][0]["fuente"])
                self.assertFalse(a["analysis_complete"])
                self.assertGreaterEqual(db.execute("SELECT count(*) FROM table_versions").fetchone()[0], 2)
