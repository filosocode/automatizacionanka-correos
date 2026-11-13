"""T-60: una propuesta de cambio pendiente de revisión humana sobrevive a los seguimientos posteriores.

SQLite temporal y Gmail falso; el importador de Work, `store.save` y las decisiones de la persona son los de siempre.
La omisión de una propuesta en un retorno nuevo no es su rechazo; solo una decisión humana la resuelve.

Cubre: T-59
"""

from tests import _isolate  # noqa: F401  (debe ir primero)

import copy
import json
import unittest

from anka.dominio import tabla_acumulada
from anka.infra import store
from anka.servicios.revision_tecnica import recuperar_propuesta, resolve_model_proposal
from tests.test_continuidad_de_casos import ENVIO, Contexto, fila

DESPUES = "2026-09-04T09:00:00-05:00"
TERCERO = "2026-09-05T09:00:00-05:00"


def levantamiento(cantidad="10", estado="confirmado", entregable="Plano", **extra):
    return fila("Levantamiento topográfico", cantidad, "ha", estado, entregable=entregable, **extra)


class PropuestaPendiente(Contexto):
    """El caso t-1 con una propuesta de 12 ha (sobre 10 ha confirmadas) ya pendiente de revisión."""

    def setUp(self):
        super().setUp()
        self.primera = self.seguimiento([levantamiento("12", fuente="correo 2")])
        self.assertEqual(self.primera["pending_model_activities"][0]["quantity"], "12")

    def propuesto(self, despues, campo="quantity", actividad="Levantamiento topográfico"):
        return next(a[campo] for a in despues["pending_model_activities"] if a["activity"] == actividad)

    def entradas(self, despues, tipo="dato_distinto", campo="quantity"):
        return [r for r in despues["revision_de_tabla"] if r["tipo"] == tipo and r["campo"] == campo]

    def test_un_seguimiento_que_conserva_la_cantidad_vigente_no_pierde_la_propuesta_de_magnitud(self):
        # el caso de T-60: Work devuelve las 10 ha vigentes y ratifica las 12 solo en el texto
        despues = self.seguimiento([levantamiento("10", entregable="Plano PDF y DWG", fuente="correo 3")],
                                   mensaje="m-3", llegada=DESPUES)
        self.assertEqual(despues["activities"][0]["quantity"], "10")                  # nada se acepta solo
        self.assertEqual(self.propuesto(despues), "12")
        self.assertEqual(self.propuesto(despues, "entregable"), "Plano PDF y DWG")    # y lo nuevo también se propone
        (cantidad,) = self.entradas(despues)
        self.assertEqual((cantidad["anterior"], cantidad["propuesto"], cantidad["fuente"]), ("10", "12", "correo 2"))
        self.assertEqual(len(self.entradas(despues, campo="entregable")), 1)

    def test_una_propuesta_ratificada_por_otro_mensaje_no_se_duplica(self):
        despues = self.seguimiento([levantamiento("12", fuente="correo 3")], mensaje="m-3", llegada=DESPUES)
        self.assertEqual(len(self.entradas(despues)), 1)
        self.assertEqual(self.propuesto(despues), "12")
        self.assertEqual(despues["activities"][0]["quantity"], "10")
        self.assertEqual(len(despues["pending_model_activities"]), 2)

    def test_un_seguimiento_que_omite_la_actividad_o_viene_vacio_no_rechaza_la_propuesta(self):
        for tabla in ([], [fila("Fotogrametría", "3", "ha", "confirmado", servicio="Fotogrametría")]):
            with self.subTest(tabla=len(tabla)):
                despues = self.seguimiento(tabla, mensaje="m-3", llegada=DESPUES)
                self.assertEqual(self.propuesto(despues), "12")
                self.assertEqual(len(self.entradas(despues)), 1)
                self.assertEqual(despues["activities"][0]["quantity"], "10")

    def test_un_reanalisis_repetido_da_lo_mismo_y_no_crea_versiones_ni_eventos(self):
        nueva = [levantamiento("10", entregable="Plano PDF y DWG", fuente="correo 3")]
        primero = self.seguimiento(nueva, mensaje="m-3", llegada=DESPUES)
        versiones, eventos = self.versiones(), len(self.eventos_de("table_merge"))
        segundo = self.seguimiento(nueva, mensaje="m-3", llegada=DESPUES)
        for clave in ("activities", "pending_model_activities", "revision_de_tabla"):
            self.assertEqual(segundo[clave], primero[clave], clave)
        self.assertEqual((self.versiones(), len(self.eventos_de("table_merge"))), (versiones, eventos))

    def test_adoptada_por_una_persona_se_da_por_resuelta_y_no_vuelve(self):
        resolve_model_proposal("t-1", True)
        despues = self.seguimiento([], mensaje="m-3", llegada=DESPUES)
        self.assertEqual(despues["activities"][0]["quantity"], "12")
        self.assertNotIn("pending_model_activities", despues)
        self.assertNotIn("revision_de_tabla", despues)

    def test_descartada_explicitamente_no_vuelve_pero_se_puede_recuperar(self):
        resolve_model_proposal("t-1", False)
        despues = self.seguimiento([], mensaje="m-3", llegada=DESPUES)
        self.assertEqual(despues["activities"][0]["quantity"], "10")
        self.assertNotIn("pending_model_activities", despues)
        self.assertEqual(despues["propuesta_descartada"][0]["quantity"], "12")        # la huella de la decisión queda
        recuperar_propuesta("t-1")
        self.assertEqual(self.propuesto(self.analisis()), "12")

    def test_una_propuesta_adoptada_ya_no_aparece_como_motivo_de_revision_para_work(self):
        from anka.dominio.contexto_previo import _motivos
        self.assertIn("dato_distinto", _motivos(self.analisis()))
        resolve_model_proposal("t-1", True)
        self.assertEqual(_motivos(self.analisis()), "")

    def test_una_propuesta_nueva_y_distinta_manda_en_la_tabla_y_la_anterior_sigue_a_la_vista(self):
        despues = self.seguimiento([levantamiento("15", fuente="correo 3")], mensaje="m-3", llegada=DESPUES)
        self.assertEqual(self.propuesto(despues), "15")
        self.assertEqual(sorted(r["propuesto"] for r in self.entradas(despues)), ["12", "15"])
        self.assertEqual(despues["activities"][0]["quantity"], "10")

    def test_un_dato_con_propuesta_pendiente_no_se_reemplaza_solo_aunque_estuviera_sin_confirmar(self):
        with store.transaccion() as db:
            analisis = json.loads(db.execute("SELECT analysis_json FROM quotes WHERE thread_id='t-1'").fetchone()[0])
            analisis["activities"][0]["estado"] = "pendiente"          # ahora el 10 es un dato sin confirmar
            db.execute("UPDATE quotes SET analysis_json=? WHERE thread_id='t-1'", (json.dumps(analisis),))
        despues = self.seguimiento([levantamiento("15", fuente="correo 3")], mensaje="m-3", llegada=DESPUES)
        self.assertEqual(despues["activities"][0]["quantity"], "10")
        self.assertEqual(self.propuesto(despues), "15")

    def test_filas_duplicadas_y_contradicciones_pendientes_tambien_sobreviven(self):
        self.seguimiento([
            fila("Batimetría", "5", "km", "confirmado", servicio="Batimetría", ubicacion="Embalse"),
            fila("Batimetría", "7", "km", "confirmado", servicio="Batimetría", ubicacion="Embalse"),
            levantamiento("10", estado="contradictorio", fuente="correo 3")], mensaje="m-3", llegada=DESPUES)
        despues = self.seguimiento([], mensaje="m-4", llegada=TERCERO)
        self.assertEqual(sum(a["activity"] == "Batimetría" for a in despues["pending_model_activities"]), 2)
        self.assertEqual({r["tipo"] for r in despues["revision_de_tabla"]},
                         {"dato_distinto", "duplicada", "contradiccion"})
        self.assertEqual(despues["activities"][0]["estado"], "contradictorio")
        self.assertEqual(self.propuesto(despues), "12")

    def test_una_eliminacion_pedida_pendiente_sigue_pendiente(self):
        self.seguimiento([fila("Batimetría", servicio="Batimetría", ubicacion="Embalse",
                               notes="El cliente indica que ya no se requiere")], mensaje="m-3", llegada=DESPUES)
        despues = self.seguimiento([], mensaje="m-4", llegada=TERCERO)
        self.assertEqual([a["activity"] for a in despues["pending_model_activities"]], ["Levantamiento topográfico"])
        self.assertEqual(len(despues["activities"]), 2)
        self.assertIn("eliminacion_pedida", {r["tipo"] for r in despues["revision_de_tabla"]})

    def test_la_edicion_humana_de_la_tabla_se_respeta_y_la_propuesta_sigue_pendiente(self):
        with store.transaccion() as db:
            analisis = json.loads(db.execute("SELECT analysis_json FROM quotes WHERE thread_id='t-1'").fetchone()[0])
            analisis["manual_edit"] = {"at": ENVIO, "by": "persona"}
            analisis["activities"][1]["quantity"] = "9"
            db.execute("UPDATE quotes SET analysis_json=? WHERE thread_id='t-1'", (json.dumps(analisis),))
        mia = self.tabla()
        despues = self.seguimiento([], mensaje="m-3", llegada=DESPUES)
        self.assertEqual(despues["activities"], mia)
        self.assertEqual(despues["manual_edit"], {"at": ENVIO, "by": "persona"})
        self.assertEqual(self.propuesto(despues), "12")

    def test_la_ronda_enviada_y_las_decisiones_historicas_no_se_reactivan(self):
        preguntas = ["¿Cuál es el sistema de referencia?", "¿Qué formatos esperan?"]
        with store.transaccion() as db:
            analisis = json.loads(db.execute("SELECT analysis_json FROM quotes WHERE thread_id='t-1'").fetchone()[0])
            analisis["manual_review"] = {"decision": "ask_client", "at": ENVIO, "questions": preguntas}
            analisis["ultimas_preguntas"] = preguntas
            db.execute("UPDATE quotes SET analysis_json=? WHERE thread_id='t-1'", (json.dumps(analisis),))
        self.enviar_preguntas(preguntas)
        rondas = copy.deepcopy(self.analisis()["rondas_de_preguntas"])
        despues = self.seguimiento([], mensaje="m-3", llegada=DESPUES)
        self.assertEqual(len(despues["rondas_de_preguntas"]), 1)
        self.assertEqual(despues["rondas_de_preguntas"][0]["mensaje"], rondas[0]["mensaje"])
        self.assertEqual([p["texto"] for p in despues["rondas_de_preguntas"][0]["preguntas"]], preguntas)
        self.assertEqual(despues["preguntas_enviadas"]["preguntas"], preguntas)
        self.assertNotIn("manual_review", despues)           # ask_client ya se ejecutó: no vuelve como decisión activa
        self.assertNotIn("ultimas_preguntas", despues)
        self.assertEqual(self.propuesto(despues), "12")
        otra = self.seguimiento([], mensaje="m-4", llegada=TERCERO)
        self.assertEqual(len(otra["rondas_de_preguntas"]), 1)


# Cubre: T-59
class PropuestaPendientePura(unittest.TestCase):
    ANTERIOR = [fila("Levantamiento", "6", "ha", "pendiente", entregable="Curvas")]

    def anterior(self, **extra):
        propuesta = copy.deepcopy(self.ANTERIOR)
        propuesta[0]["quantity"] = "6,5"
        return {"activities": copy.deepcopy(self.ANTERIOR), "pending_model_activities": propuesta,
                "revision_de_tabla": [{"tipo": "dato_distinto", "actividad": "Levantamiento", "campo": "quantity",
                                       "anterior": "6", "propuesto": "6,5", "fuente": "correo 2"}], **extra}

    def test_sin_propuesta_pendiente_no_cambia_nada(self):
        res = tabla_acumulada.integrar(self.ANTERIOR, [])
        self.assertIs(tabla_acumulada.conservar_propuesta({"activities": self.ANTERIOR}, res), res)

    def test_la_propuesta_se_reconstruye_sin_aplicar_nada_a_la_tabla_vigente(self):
        anterior = self.anterior()
        res = tabla_acumulada.conservar_propuesta(anterior, tabla_acumulada.integrar(anterior["activities"], []))
        self.assertEqual(res.vigente[0]["quantity"], "6")
        self.assertEqual(res.propuesta[0]["quantity"], "6,5")
        self.assertEqual([(r["campo"], r["anterior"], r["propuesto"], r["fuente"]) for r in res.revisar],
                         [("quantity", "6", "6,5", "correo 2")])

    def test_lo_que_la_vigente_ya_adopto_se_da_por_resuelto(self):
        anterior = self.anterior()
        anterior["activities"][0]["quantity"] = "6,5"
        res = tabla_acumulada.conservar_propuesta(anterior, tabla_acumulada.integrar(anterior["activities"], []))
        self.assertEqual((res.revisar, res.propuesta), ([], res.vigente))

    def test_no_muta_sus_entradas_y_es_determinista(self):
        anterior = self.anterior()
        copia = copy.deepcopy(anterior)
        base = tabla_acumulada.integrar(anterior["activities"], [])
        primero = tabla_acumulada.conservar_propuesta(anterior, base)
        segundo = tabla_acumulada.conservar_propuesta(anterior, base)
        self.assertEqual(anterior, copia)
        self.assertEqual(primero, segundo)

    def test_los_campos_protegidos_son_los_que_tienen_cambio_pendiente(self):
        self.assertEqual(tabla_acumulada.campos_protegidos(self.anterior()), frozenset({("levantamiento", "quantity")}))
        self.assertEqual(tabla_acumulada.campos_protegidos({"activities": self.ANTERIOR}), frozenset())

    def test_un_dato_protegido_no_se_reemplaza_solo(self):
        nueva = [fila("Levantamiento", "7", "ha", "confirmado", entregable="Curvas")]
        libre = tabla_acumulada.integrar(self.ANTERIOR, nueva)
        protegido = tabla_acumulada.integrar(self.ANTERIOR, nueva, frozenset({("levantamiento", "quantity")}))
        self.assertEqual(libre.vigente[0]["quantity"], "7")
        self.assertEqual(protegido.vigente[0]["quantity"], "6")
        self.assertEqual(protegido.propuesta[0]["quantity"], "7")


if __name__ == "__main__":
    unittest.main()
