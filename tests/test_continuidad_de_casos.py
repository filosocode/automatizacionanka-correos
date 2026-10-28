"""T-59 Fase B: una respuesta del cliente actualiza el mismo expediente sin perder preguntas ni actividades.

SQLite temporal y Gmail falso. El importador de Work, `store.save` y las versiones de la tabla son los de siempre.

Cubre: T-59
"""

from tests import _isolate  # noqa: F401  (debe ir primero)

import copy
import json
import unittest

from anka.dominio import tabla_acumulada
from anka.dominio.preguntas import ABIERTA, ENVIADA, SIN_CONFIRMAR, candidatas
from anka.dominio.status import QuoteStatus
from anka.infra import store
from anka.servicios import case_actions, quotes, work_import
from tests.test_envios_inciertos import Base
from tests.test_work_sync import b64, payload

ENVIO = "2026-09-02T09:00:00-05:00"
RESPUESTA = "2026-09-03T09:00:00-05:00"


def fila(nombre, cantidad="", unidad="", estado="pendiente", **extra):
    return {"activity": nombre, "servicio_tecnica": extra.pop("servicio", "Topografía"),
            "ubicacion": extra.pop("ubicacion", "Bogotá"), "quantity": cantidad, "unit": unidad,
            "entregable": extra.pop("entregable", ""), "plazo_restriccion": "no indicado",
            "fuente": extra.pop("fuente", "correo 1"), "estado": estado, "notes": extra.pop("notes", ""), **extra}


LEVANTAMIENTO = fila("Levantamiento topográfico", "10", "ha", "confirmado", entregable="Plano")
BATIMETRIA = fila("Batimetría", servicio="Batimetría", ubicacion="Embalse")


class Contexto(Base):
    """El caso t-1 con dos actividades (una completa y una a medio definir), listo para recibir seguimientos."""

    def setUp(self):
        super().setUp()
        analisis = {**self.analisis(), "activities": [copy.deepcopy(LEVANTAMIENTO), copy.deepcopy(BATIMETRIA)]}
        db = store.connect()
        try:
            store.save(db, "t-1", "m-1", QuoteStatus.REVIEW, "cliente@example.com", analisis)
        finally:
            db.close()
        self.v0 = self.versiones()           # las versiones de la tabla con que arranca cada prueba

    def analisis(self):
        return copy.deepcopy(quotes.get_quote("t-1")["analysis"])

    def seguimiento(self, actividades, mensaje="m-2", llegada=RESPUESTA, **extra):
        work_import.dashboard_upsert(b64(json.dumps(payload(
            thread="t-1", message=mensaje, classification="SEGUIMIENTO", received_at=llegada,
            activities=actividades, **extra))))
        return self.analisis()

    def tabla(self):
        return self.analisis()["activities"]

    def versiones(self):
        return len(quotes.RepositorioDeCasos().versiones_de_tabla("t-1"))

    def eventos_de(self, tipo):
        db = store.connect()
        try:
            return [r["summary"] for r in db.execute(
                "SELECT summary FROM case_events WHERE thread_id='t-1' AND kind=?", (tipo,))]
        finally:
            db.close()

    def enviar_preguntas(self, preguntas, mensaje="s-1"):
        case_actions._record_sent_reply(quotes.get_quote("t-1"), case_actions.RespuestaEnviada(
            "t-1", mensaje, "Hola\n" + "\n".join(f"{i}. {p}" for i, p in enumerate(preguntas, 1)),
            "cliente@example.com", QuoteStatus.WAITING, ENVIO, preguntas))


# Cubre: T-59
class PreguntasEnviadas(Contexto):
    def test_la_ronda_enviada_conserva_su_mensaje_y_no_se_pierde_al_importar_un_seguimiento(self):
        self.enviar_preguntas(["¿Cuál es el datum?", "¿Plazo de entrega?"])
        antes = self.analisis()["preguntas_enviadas"]
        self.assertEqual((antes["mensaje"], antes["preguntas"]), ("s-1", ["¿Cuál es el datum?", "¿Plazo de entrega?"]))
        despues = self.seguimiento([])
        self.assertEqual(despues["preguntas_enviadas"], antes)
        ronda = despues["rondas_de_preguntas"][0]
        self.assertEqual((ronda["mensaje"], ronda["en"]), ("s-1", ENVIO))

    def test_la_respuesta_se_liga_a_la_ronda_y_cada_pregunta_dice_solo_lo_que_se_ve(self):
        self.enviar_preguntas(["¿Cuál es el datum?", "¿Plazo de entrega?"])
        despues = self.seguimiento([], question_audit=[
            {"question": "¿Plazo de entrega?", "disposition": "ask_client", "evidence_ids": []}])
        ronda = despues["rondas_de_preguntas"][0]
        self.assertEqual(ronda["respuesta"], {"mensaje": "m-2", "en": RESPUESTA})
        self.assertEqual([(p["texto"], p["estado"]) for p in ronda["preguntas"]],
                         [("¿Cuál es el datum?", SIN_CONFIRMAR), ("¿Plazo de entrega?", ABIERTA)])

    def test_una_pregunta_que_work_solo_propuso_no_es_una_pregunta_enviada(self):
        despues = self.seguimiento([], question_audit=[
            {"question": "¿Cuál es el datum?", "disposition": "ask_client", "evidence_ids": []}])
        self.assertEqual([c["sugerida"] for c in candidatas(despues)], ["ask_client"])
        self.assertNotIn("preguntas_enviadas", despues)
        self.assertNotIn("rondas_de_preguntas", despues)

    def test_antes_de_la_respuesta_la_ronda_sigue_en_enviada(self):
        self.enviar_preguntas(["¿Cuál es el datum?"])
        anterior = self.seguimiento([], mensaje="m-0", llegada="2026-09-01T10:00:00-05:00")   # un mensaje previo al envío
        self.assertIsNone(anterior["rondas_de_preguntas"][0]["respuesta"])
        self.assertEqual(anterior["rondas_de_preguntas"][0]["preguntas"][0]["estado"], ENVIADA)

    def test_un_reanalisis_idempotente_no_cambia_nada(self):
        self.enviar_preguntas(["¿Cuál es el datum?"])
        primero = self.seguimiento([])
        versiones = self.versiones()
        segundo = self.seguimiento([])
        self.assertEqual(segundo["rondas_de_preguntas"], primero["rondas_de_preguntas"])
        self.assertEqual(segundo["preguntas_enviadas"], primero["preguntas_enviadas"])
        self.assertEqual(self.versiones(), versiones)

    def test_una_segunda_ronda_se_suma_a_la_primera_y_el_mismo_mensaje_no_la_duplica(self):
        self.enviar_preguntas(["¿Cuál es el datum?"], "s-1")
        self.seguimiento([])
        with store.transaccion() as db:
            db.execute("UPDATE quotes SET status='review' WHERE thread_id='t-1'")
        case_actions._record_sent_reply(quotes.get_quote("t-1"), case_actions.RespuestaEnviada(
            "t-1", "s-2", "Hola\n1. ¿Plazo?", "cliente@example.com", QuoteStatus.WAITING,
            "2026-09-04T09:00:00-05:00", ["¿Plazo?"]))
        case_actions._record_sent_reply(quotes.get_quote("t-1"), case_actions.RespuestaEnviada(
            "t-1", "s-2", "Hola\n1. ¿Plazo?", "cliente@example.com", QuoteStatus.WAITING,
            "2026-09-04T09:00:00-05:00", ["¿Plazo?"]))
        rondas = self.analisis()["rondas_de_preguntas"]
        self.assertEqual([r["mensaje"] for r in rondas], ["s-1", "s-2"])
        self.assertIsNotNone(rondas[0]["respuesta"])
        self.assertIsNone(rondas[1]["respuesta"])

    def test_un_expediente_anterior_a_las_rondas_se_convierte_al_importar(self):
        with store.transaccion() as db:
            analisis = json.loads(db.execute("SELECT analysis_json FROM quotes WHERE thread_id='t-1'").fetchone()[0])
            analisis["preguntas_enviadas"] = {"en": ENVIO, "preguntas": ["¿Cuál es el datum?"], "esperada": False}
            db.execute("UPDATE quotes SET analysis_json=? WHERE thread_id='t-1'", (json.dumps(analisis),))
        despues = self.seguimiento([])
        self.assertEqual(despues["preguntas_enviadas"]["preguntas"], ["¿Cuál es el datum?"])
        ronda = despues["rondas_de_preguntas"][0]
        self.assertEqual((ronda["mensaje"], ronda["respuesta"]["mensaje"]), ("", "m-2"))

    def test_un_caso_sin_preguntas_enviadas_sigue_igual(self):
        despues = self.seguimiento([])
        self.assertNotIn("preguntas_enviadas", despues)
        self.assertNotIn("rondas_de_preguntas", despues)

    def test_las_decisiones_se_renuevan_pero_el_historial_de_lo_enviado_queda(self):
        self.enviar_preguntas(["¿Cuál es el datum?"])
        despues = self.seguimiento([])
        self.assertFalse(despues.get("preguntas"))                 # las decisiones del reanálisis se descartan (U-22)
        self.assertEqual(despues["rondas_de_preguntas"][0]["preguntas"][0]["texto"], "¿Cuál es el datum?")


# Cubre: T-59
class TablaAcumulada(Contexto):
    def test_una_respuesta_que_aclara_un_dato_lo_completa_y_conserva_todo_lo_demas(self):
        despues = self.seguimiento([fila("Batimetría", "5", "km", "confirmado", servicio="Batimetría",
                                         ubicacion="Embalse", fuente="correo 2")])
        nombres = [a["activity"] for a in despues["activities"]]
        self.assertEqual(nombres, ["Levantamiento topográfico", "Batimetría"])
        batimetria = despues["activities"][1]
        self.assertEqual((batimetria["quantity"], batimetria["unit"], batimetria["estado"]), ("5", "km", "confirmado"))
        self.assertEqual(batimetria["fuente"], "correo 1; correo 2")
        self.assertNotIn("pending_model_activities", despues)
        self.assertEqual(self.versiones(), self.v0 + 1)
        self.assertEqual(len(self.eventos_de("table_merge")), 1)

    def test_un_seguimiento_que_no_menciona_las_actividades_anteriores_no_las_elimina(self):
        antes = self.tabla()
        despues = self.seguimiento([])
        self.assertEqual(despues["activities"], antes)
        self.assertEqual(self.versiones(), self.v0)
        self.assertEqual(self.seguimiento([fila("Otra cosa", "1", "u", "confirmado", servicio="Otro")])["activities"][:2],
                         antes)

    def test_una_actividad_nueva_se_agrega(self):
        despues = self.seguimiento([fila("Fotogrametría con dron", "30", "ha", "confirmado", servicio="Fotogrametría",
                                         ubicacion="Sopó", fuente="correo 2")])
        self.assertEqual([a["activity"] for a in despues["activities"]],
                         ["Levantamiento topográfico", "Batimetría", "Fotogrametría con dron"])
        self.assertNotIn("pending_model_activities", despues)

    def test_corregir_un_dato_ya_confirmado_queda_como_propuesta_y_la_tabla_no_cambia(self):
        despues = self.seguimiento([fila("Levantamiento topográfico", "12", "ha", "confirmado", entregable="Plano",
                                         fuente="correo 2")])
        self.assertEqual(despues["activities"][0]["quantity"], "10")
        propuesta = {a["activity"]: a for a in despues["pending_model_activities"]}
        self.assertEqual(propuesta["Levantamiento topográfico"]["quantity"], "12")
        self.assertIn("Batimetría", propuesta)
        self.assertEqual(despues["revision_de_tabla"], [{
            "tipo": "dato_distinto", "actividad": "Levantamiento topográfico", "campo": "quantity", "anterior": "10",
            "propuesto": "12", "fuente": "correo 2"}])
        self.assertIn("por revisar", self.eventos_de("table_merge")[0])

    def test_adoptar_la_propuesta_no_borra_lo_que_el_seguimiento_no_repitio(self):
        from anka.servicios.revision_tecnica import resolve_model_proposal
        self.seguimiento([fila("Levantamiento topográfico", "12", "ha", "confirmado", entregable="Plano")])
        resolve_model_proposal("t-1", True)
        tabla = self.tabla()
        self.assertEqual([(a["activity"], a["quantity"]) for a in tabla],
                         [("Levantamiento topográfico", "12"), ("Batimetría", "")])

    def test_una_eliminacion_pedida_en_el_texto_no_se_aplica_sola(self):
        despues = self.seguimiento([fila("Batimetría", servicio="Batimetría", ubicacion="Embalse",
                                         notes="El cliente indica que ya no se requiere", fuente="correo 2")])
        self.assertEqual(len(despues["activities"]), 2)
        self.assertEqual([a["activity"] for a in despues["pending_model_activities"]], ["Levantamiento topográfico"])
        self.assertEqual(despues["revision_de_tabla"][0]["tipo"], "eliminacion_pedida")

    def test_una_sustitucion_ambigua_conserva_las_dos_versiones_sin_fusionar(self):
        despues = self.seguimiento([fila("Levantamiento topográfico del predio", "10", "ha", "confirmado",
                                         entregable="Plano", fuente="correo 2")])
        self.assertEqual([a["activity"] for a in despues["activities"]], ["Levantamiento topográfico", "Batimetría"])
        self.assertEqual([a["activity"] for a in despues["pending_model_activities"]],
                         ["Levantamiento topográfico", "Batimetría", "Levantamiento topográfico del predio"])
        self.assertEqual(despues["revision_de_tabla"][0]["tipo"], "sustitucion_ambigua")

    def test_dos_filas_con_el_mismo_nombre_pero_distintas_se_marcan_como_duplicadas(self):
        despues = self.seguimiento([
            fila("Batimetría", "5", "km", "confirmado", servicio="Batimetría", ubicacion="Embalse"),
            fila("Batimetría", "7", "km", "confirmado", servicio="Batimetría", ubicacion="Embalse")])
        self.assertEqual([r["tipo"] for r in despues["revision_de_tabla"]], ["duplicada"])
        self.assertEqual(len(despues["activities"]), 2)
        self.assertEqual(sum(a["activity"] == "Batimetría" for a in despues["pending_model_activities"]), 2)

    def test_una_fila_repetida_tal_cual_no_es_nada_nuevo(self):
        despues = self.seguimiento([copy.deepcopy(LEVANTAMIENTO), copy.deepcopy(LEVANTAMIENTO)])
        self.assertEqual(despues["activities"][0], LEVANTAMIENTO)
        self.assertNotIn("revision_de_tabla", despues)

    def test_una_contradiccion_marca_la_fila_y_pide_revision(self):
        despues = self.seguimiento([fila("Levantamiento topográfico", "10", "ha", "contradictorio", entregable="Plano",
                                         fuente="correo 2")])
        self.assertEqual(despues["activities"][0]["estado"], "contradictorio")
        self.assertEqual(despues["revision_de_tabla"][0]["tipo"], "contradiccion")

    def test_la_edicion_de_una_persona_no_se_sobrescribe_nunca(self):
        with store.transaccion() as db:
            analisis = json.loads(db.execute("SELECT analysis_json FROM quotes WHERE thread_id='t-1'").fetchone()[0])
            analisis["manual_edit"] = {"at": ENVIO, "by": "persona"}
            analisis["activities"][1]["quantity"] = "9"
            db.execute("UPDATE quotes SET analysis_json=? WHERE thread_id='t-1'", (json.dumps(analisis),))
        mia = self.tabla()
        despues = self.seguimiento([fila("Batimetría", "5", "km", "confirmado", servicio="Batimetría",
                                         ubicacion="Embalse"),
                                    fila("Fotogrametría", "3", "ha", "confirmado", servicio="Fotogrametría")])
        self.assertEqual(despues["activities"], mia)
        propuesta = despues["pending_model_activities"]
        self.assertEqual([a["activity"] for a in propuesta], ["Levantamiento topográfico", "Batimetría", "Fotogrametría"])
        self.assertEqual(despues["manual_edit"], {"at": ENVIO, "by": "persona"})

    def test_con_la_tabla_bloqueada_por_la_t_la_propuesta_tampoco_pierde_lo_no_repetido(self):
        with store.transaccion() as db:
            db.execute("UPDATE quotes SET quote_code='26001', code_assigned_at=?, contact_email='cliente@example.com' "
                       "WHERE thread_id='t-1'", ("2026-09-01T12:00:00-05:00",))
        despues = self.seguimiento([fila("Fotogrametría", "3", "ha", "confirmado", servicio="Fotogrametría")])
        self.assertEqual([a["activity"] for a in despues["activities"]], ["Levantamiento topográfico", "Batimetría"])
        self.assertEqual([a["activity"] for a in despues["pending_model_activities"]],
                         ["Levantamiento topográfico", "Batimetría", "Fotogrametría"])

    def test_importar_dos_veces_el_mismo_seguimiento_da_lo_mismo(self):
        nueva = [fila("Batimetría", "5", "km", "confirmado", servicio="Batimetría", ubicacion="Embalse",
                      fuente="correo 2"),
                 fila("Fotogrametría", "3", "ha", "confirmado", servicio="Fotogrametría", fuente="correo 2")]
        primero = self.seguimiento(nueva)
        versiones = self.versiones()
        segundo = self.seguimiento(nueva)
        self.assertEqual(segundo["activities"], primero["activities"])
        self.assertEqual(self.versiones(), versiones)
        self.assertEqual(len(self.eventos_de("table_merge")), 1)

    def test_el_reanalisis_del_mensaje_original_y_el_primer_analisis_reemplazan_como_siempre(self):
        despues = self.seguimiento([fila("Solo una", "1", "u", "confirmado", servicio="Otro")], mensaje="m-1",
                                   llegada="2026-09-01T09:00:00-05:00")
        self.assertEqual([a["activity"] for a in despues["activities"]], ["Solo una"])
        self.assertEqual(self.eventos_de("table_merge"), [])

    def test_sin_hora_de_llegada_un_mensaje_distinto_cuenta_como_seguimiento(self):
        despues = self.seguimiento([], mensaje="m-9", llegada="")
        self.assertEqual(len(despues["activities"]), 2)


# Cubre: T-59
class IntegracionPura(unittest.TestCase):
    def test_no_muta_las_tablas_de_entrada_y_es_determinista(self):
        anterior, nueva = [copy.deepcopy(LEVANTAMIENTO)], [fila("Levantamiento topográfico", "12", "ha", "confirmado")]
        copia = copy.deepcopy((anterior, nueva))
        uno, dos = tabla_acumulada.integrar(anterior, nueva), tabla_acumulada.integrar(anterior, nueva)
        self.assertEqual((anterior, nueva), copia)
        self.assertEqual(uno, dos)

    def test_un_dato_pendiente_se_confirma_si_la_fila_nueva_lo_confirma(self):
        anterior = [fila("Levantamiento topográfico", "10", "ha", "pendiente")]
        res = tabla_acumulada.integrar(anterior, [fila("Levantamiento topográfico", "12", "ha", "confirmado")])
        self.assertEqual((res.vigente[0]["quantity"], res.vigente[0]["estado"]), ("12", "confirmado"))
        self.assertEqual(res.revisar, [])

    def test_describir_resume_lo_aplicado_y_lo_pendiente(self):
        res = tabla_acumulada.integrar([copy.deepcopy(LEVANTAMIENTO)], [fila("Nueva", "1", "u", "confirmado",
                                                                                 servicio="Otro")])
        self.assertEqual(tabla_acumulada.describir(res), "Seguimiento: se agregó «Nueva».")
        self.assertEqual(tabla_acumulada.describir(tabla_acumulada.integrar([], [])), "Seguimiento: sin cambios en la tabla.")


if __name__ == "__main__":
    unittest.main()
