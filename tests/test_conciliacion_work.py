"""Conciliación Work → recepción: una importación confirmada cierra el mensaje que la originó, y nada más (T-48, D-152).

Cubre: T-48
"""

from tests import _isolate  # noqa: F401  (debe ir primero)

import json
import sqlite3
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import MagicMock, patch

from anka.dominio.errores import ErrorDeAnka
from anka.infra import settings, store
from anka.servicios import quotes, recepcion, work_import
from tests.test_work_sync import b64, payload

CUENTA = recepcion.CUENTA
PENDIENTE = ("pendiente_de_procesar", "pendiente_de_interpretacion_work")
INTERPRETADO = ("procesado", "interpretado_por_work")


class Base(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        for parche in (patch.object(settings, "DATA_DIR", Path(self.temp.name)),
                       patch.object(work_import, "programar_actualizacion_de_hoja", MagicMock())):
            parche.start()
            self.addCleanup(parche.stop)

    def fila(self, hilo, mensaje, estado=PENDIENTE[0], disposicion=PENDIENTE[1]):
        with store.transaccion() as db:
            db.execute("""INSERT INTO recepcion_mensajes(account,thread_id,message_id,state,disposition,detected_at,
                updated_at,sender,subject,evidence_json) VALUES(?,?,?,?,?,'2026-10-17T10:00:00-05:00',
                '2026-10-17T10:00:00-05:00','c@example.com','Asunto','{"source":"gmail_history"}')""",
                       (CUENTA, hilo, mensaje, estado, disposicion))

    def importar(self, hilo="T1", mensaje="m1", **extra):
        datos = payload(thread=hilo, message=mensaje, **extra)
        work_import.dashboard_upsert(b64(json.dumps(datos)))

    def filas(self):
        with store.lectura() as db:
            return {(r["thread_id"], r["message_id"]): dict(r) for r in db.execute("SELECT * FROM recepcion_mensajes")}

    def estado(self, hilo, mensaje):
        f = self.filas()[(hilo, mensaje)]
        return f["state"], f["disposition"]

    def eventos(self, tipo=None):
        with store.lectura() as db:
            consulta = "SELECT * FROM recepcion_eventos" + (" WHERE event_kind=?" if tipo else "") + " ORDER BY id"
            return [dict(r) for r in db.execute(consulta, (tipo,) if tipo else ())]

    def ultimo_entrante(self, hilo):
        with store.lectura() as db:
            return db.execute("SELECT last_inbound_id FROM quotes WHERE thread_id=?", (hilo,)).fetchone()[0]

    def analisis(self, hilo="T1"):
        return quotes.get_quote(hilo)["analysis"]


class ConciliacionBasicaTests(Base):
    def test_una_importacion_valida_cierra_el_mensaje_que_la_origino(self):
        self.fila("T1", "m1")
        self.importar("T1", "m1")
        self.assertEqual(self.estado("T1", "m1"), INTERPRETADO)
        evento, = self.eventos("interpretado_por_work")
        self.assertEqual((evento["thread_id"], evento["message_id"]), ("T1", "m1"))
        evidencia = json.loads(self.filas()[("T1", "m1")]["evidence_json"])
        self.assertEqual(evidencia["source"], "gmail_history")                       # lo anterior se conserva
        with store.lectura() as db:
            ref = db.execute("SELECT ref FROM case_events WHERE thread_id='T1' AND kind='analysis'").fetchone()[0]
        self.assertEqual(evidencia["work"]["analysis_ref"], ref)                    # y apunta al hecho de la auditoría
        self.assertEqual(self.ultimo_entrante("T1"), "m1")

    def test_reimportar_el_mismo_borrador_o_uno_nuevo_del_mismo_mensaje_no_cambia_nada(self):
        self.fila("T1", "m1")
        self.importar("T1", "m1")
        despues_de_la_primera = self.filas()[("T1", "m1")]
        self.importar("T1", "m1")                                                    # idéntico
        self.importar("T1", "m1", project_name="Otra versión del mismo análisis")     # versión nueva
        self.assertEqual(self.filas()[("T1", "m1")], despues_de_la_primera)
        self.assertEqual(len(self.eventos("interpretado_por_work")), 1)

    def test_dos_importaciones_simultaneas_del_mismo_mensaje_dejan_un_solo_evento(self):
        self.fila("T1", "m1")
        with ThreadPoolExecutor(2) as pool:
            for futuro in [pool.submit(self.importar, "T1", "m1") for _ in range(2)]:
                futuro.result()
        self.assertEqual(self.estado("T1", "m1"), INTERPRETADO)
        self.assertEqual(len(self.eventos("interpretado_por_work")), 1)

    def test_un_seguimiento_concilia_solo_su_mensaje(self):
        for hilo, mensaje in (("T1", "m1"), ("T1", "m2"), ("T2", "m3")):
            self.fila(hilo, mensaje)
        self.importar("T1", "m1")
        self.importar("T1", "m2", summary="El cliente respondió")       # seguimiento del mismo hilo
        self.assertEqual(self.estado("T1", "m1"), INTERPRETADO)
        self.assertEqual(self.estado("T1", "m2"), INTERPRETADO)
        self.assertEqual(self.estado("T2", "m3"), PENDIENTE)
        self.assertEqual(self.ultimo_entrante("T1"), "m2")

    def test_el_analisis_de_un_mensaje_no_cierra_a_los_otros_del_hilo(self):
        for mensaje in ("m1", "m2", "m3"):
            self.fila("T1", mensaje)
        self.importar("T1", "m2")
        self.assertEqual([self.estado("T1", m) for m in ("m1", "m2", "m3")], [PENDIENTE, INTERPRETADO, PENDIENTE])

    def test_un_mensaje_con_el_mismo_id_en_otro_hilo_no_se_confunde(self):
        self.fila("T9", "m1")
        self.importar("T1", "m1")
        self.assertEqual(self.estado("T9", "m1"), PENDIENTE)


class EstadosIndependientesTests(Base):
    def test_un_analisis_incompleto_esta_importado_y_sigue_requiriendo_revision_tecnica(self):
        self.fila("T1", "m1")
        self.importar("T1", "m1", status="Requiere revisión técnica interna", analysis_complete=False)
        self.assertEqual(self.estado("T1", "m1"), INTERPRETADO)                      # la recepción solo dice «Work lo vio»
        self.assertIs(self.analisis().get("analysis_complete"), False)
        self.assertEqual(quotes.get_quote("T1")["status"], "review")                # el análisis técnico sigue pendiente
        work = json.loads(self.filas()[("T1", "m1")]["evidence_json"])["work"]
        self.assertIs(work["analysis_complete"], False)
        self.assertEqual(work["work_status"], "Requiere revisión técnica interna")

    def test_la_conciliacion_no_cambia_el_estado_del_caso_ni_su_analisis(self):
        self.fila("T1", "m1")
        con = self._importar_y_leer_caso(conciliando=True)
        sin = self._importar_y_leer_caso(conciliando=False)
        self.assertEqual(con, sin)

    def _importar_y_leer_caso(self, conciliando):
        hilo = "TC" if conciliando else "TS"
        if conciliando:
            self.fila(hilo, "m1")
        self.importar(hilo, "m1", status="Alcance completo", analysis_complete=True)
        caso = quotes.get_quote(hilo)
        return caso["status"], {k: v for k, v in caso["analysis"].items() if k not in ("received_at",)}


class SinRegistroYOtrosEstadosTests(Base):
    def test_si_el_mensaje_no_esta_en_la_cola_no_se_crea_ni_se_toca_el_cursor(self):
        with store.transaccion() as db:
            db.execute("INSERT INTO recepcion_control(account,history_id,updated_at) VALUES(?, '777', 't')", (CUENTA,))
            control = dict(db.execute("SELECT * FROM recepcion_control").fetchone())
        self.importar("T1", "m1")
        self.importar("T1", "m1")                                                    # y reimportar no repite el aviso
        self.assertEqual(self.filas(), {})
        with store.lectura() as db:
            self.assertEqual(dict(db.execute("SELECT * FROM recepcion_control").fetchone()), control)
        evento, = self.eventos("conciliacion_sin_registro")
        self.assertEqual((evento["thread_id"], evento["message_id"]), ("T1", "m1"))
        self.assertEqual(len(self.eventos("cursor_actualizado")), 0)

    def test_los_demas_estados_no_cambian_y_dejan_un_solo_evento(self):
        casos = (("por_leer", "", "conciliacion_diferida"),
                 ("requiere_reintento", "", "conciliacion_diferida"),
                 ("revision_manual", "", "conciliacion_omitida"),
                 ("procesado", "mensaje_propio_excluido", "conciliacion_omitida"),
                 ("procesado", "anterior_al_checkpoint_inicial", "conciliacion_omitida"))
        for indice, (estado, disposicion, evento) in enumerate(casos):
            with self.subTest(estado=estado, disposicion=disposicion):
                hilo = f"T{indice}"
                self.fila(hilo, "m1", estado, disposicion)
                antes = self.filas()[(hilo, "m1")]
                self.importar(hilo, "m1")
                self.importar(hilo, "m1", project_name="v2")
                self.assertEqual(self.filas()[(hilo, "m1")], antes)
                self.assertEqual(len([e for e in self.eventos(evento) if e["thread_id"] == hilo]), 1)
                self.assertEqual([e for e in self.eventos("interpretado_por_work") if e["thread_id"] == hilo], [])

    def test_un_caso_minimo_y_un_borrador_sin_message_id_no_concilian(self):
        self.fila("TM", "m1")
        self.fila("TN", "TN")                                       # el mensaje cuyo id coincide con el del hilo
        work_import.dashboard_upsert(b64(json.dumps(payload(thread="TM", message="m1", _caso_minimo=True,
                                                            _rechazo_de_work={"reason": "JSON roto"}))))
        with store.transaccion() as db:    # el importador sustituye un message_id ausente por el del hilo: no concilia
            for sin_id in ({"thread_id": "TN"}, {"thread_id": "TN", "message_id": ""},
                           {"thread_id": "TN", "message_id": None}, {"thread_id": "TN", "message_id": "  "}):
                work_import._conciliar_recepcion(db, sin_id, {}, "v1")
        self.assertEqual(self.estado("TM", "m1"), PENDIENTE)
        self.assertEqual(self.estado("TN", "TN"), PENDIENTE)
        self.assertEqual(self.eventos("interpretado_por_work"), [])


class FallosIntermediosTests(Base):
    def test_si_falla_el_hecho_de_auditoria_la_fila_no_cambia_y_reimportar_la_concilia(self):
        self.fila("T1", "m1")
        with patch.object(work_import, "record_event", side_effect=RuntimeError("disco lleno")):
            with self.assertRaises(RuntimeError):
                self.importar("T1", "m1")
        self.assertEqual(self.estado("T1", "m1"), PENDIENTE)          # el caso pudo guardarse, pero no se confirmó nada
        self.assertEqual(self.eventos(), [])
        self.importar("T1", "m1")                                      # el puente reintenta lo que falló
        self.assertEqual(self.estado("T1", "m1"), INTERPRETADO)
        self.assertEqual(len(self.eventos("interpretado_por_work")), 1)

    def test_si_falla_la_conciliacion_el_analisis_se_guarda_y_la_cola_no_queda_a_medias(self):
        self.fila("T1", "m1")

        def escribe_y_falla(db, hilo, mensaje, *_, **__):
            db.execute("UPDATE recepcion_mensajes SET state='procesado' WHERE thread_id=?", (hilo,))
            db.execute("INSERT INTO recepcion_eventos(account,thread_id,message_id,event_kind,occurred_at) "
                       "VALUES(?,?,?,'interpretado_por_work','t')", (CUENTA, hilo, mensaje))
            raise sqlite3.OperationalError("database is locked")

        with patch.object(work_import, "conciliar_con_work", side_effect=escribe_y_falla):
            self.importar("T1", "m1")
        self.assertIsNotNone(quotes.get_quote("T1"))                                  # el análisis de Work no se pierde
        self.assertEqual(self.estado("T1", "m1"), PENDIENTE)                          # nada a medias
        self.assertEqual(self.eventos(), [])
        with store.lectura() as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM case_events WHERE thread_id='T1' "
                                        "AND kind='analysis'").fetchone()[0], 1)

    def test_el_fallo_de_la_conciliacion_se_repara_despues_con_conciliar_pendientes(self):
        self.fila("T1", "m1")
        with patch.object(work_import, "conciliar_con_work", side_effect=sqlite3.OperationalError("locked")):
            self.importar("T1", "m1")
        self.assertEqual(self.estado("T1", "m1"), PENDIENTE)
        self.assertEqual(recepcion.conciliar_pendientes()["conciliado"], 1)
        self.assertEqual(self.estado("T1", "m1"), INTERPRETADO)

    def test_sin_la_tabla_de_recepcion_el_analisis_se_importa(self):
        with store.transaccion() as db:
            db.execute("DROP TABLE recepcion_eventos")
            db.execute("DROP TABLE recepcion_mensajes")
        self.importar("T1", "m1")
        self.assertIsNotNone(quotes.get_quote("T1"))

    def test_un_borrador_invalido_no_toca_la_cola(self):
        self.fila("T1", "m1")
        with self.assertRaises(ErrorDeAnka):
            work_import.dashboard_upsert(b64(json.dumps({"thread_id": "T1", "message_id": "m1"})))
        self.assertEqual(self.estado("T1", "m1"), PENDIENTE)
        self.assertEqual(self.eventos(), [])


class ConciliarPendientesTests(Base):
    def test_concilia_lo_ya_importado_una_sola_vez_y_respeta_la_identidad(self):
        # el análisis llegó cuando la fila aún no estaba lista: se difirió
        self.fila("T1", "m1", "por_leer", "")
        self.fila("T1", "m2")                                       # otro mensaje del hilo, sin análisis propio
        self.fila("T3", "m5")
        self.importar("T1", "m1")
        self.importar("T3", "m5")
        with store.transaccion() as db:                              # el receptor termina de leer m1
            db.execute("UPDATE recepcion_mensajes SET state=?,disposition=? WHERE thread_id='T1' AND message_id='m1'",
                       PENDIENTE)
        primera = recepcion.conciliar_pendientes()
        self.assertEqual((primera["conciliado"], primera["revisadas"]), (1, 1))     # T3/m5 ya se cerró al importar
        self.assertEqual(self.estado("T1", "m1"), INTERPRETADO)
        self.assertEqual(self.estado("T3", "m5"), INTERPRETADO)
        self.assertEqual(self.estado("T1", "m2"), PENDIENTE)                         # su análisis es de m1, no de m2
        self.assertEqual(recepcion.conciliar_pendientes(), {"revisadas": 0, "conciliado": 0, "otros": 0})
        self.assertEqual(len(self.eventos("interpretado_por_work")), 2)

    def test_un_caso_minimo_no_cuenta_como_interpretacion(self):
        self.fila("TM", "m1")
        work_import.dashboard_upsert(b64(json.dumps(payload(thread="TM", message="m1", _caso_minimo=True,
                                                            _rechazo_de_work={"reason": "JSON roto"}))))
        self.assertEqual(recepcion.conciliar_pendientes()["conciliado"], 0)
        self.assertEqual(self.estado("TM", "m1"), PENDIENTE)

    def test_sin_filas_pendientes_no_hace_nada(self):
        self.assertEqual(recepcion.conciliar_pendientes(), {"revisadas": 0, "conciliado": 0, "otros": 0})

    def test_la_base_queda_integra(self):
        self.fila("T1", "m1")
        self.importar("T1", "m1")
        recepcion.conciliar_pendientes()
        with store.lectura() as db:
            self.assertEqual(db.execute("PRAGMA integrity_check").fetchone()[0], "ok")


class FuncionDeConciliacionTests(Base):
    def test_resultados_directos(self):
        self.fila("T1", "m1")
        with store.transaccion() as db:
            self.assertEqual(recepcion.conciliar_con_work(db, "T1", "m1", "ref1", True), "conciliado")
            self.assertEqual(recepcion.conciliar_con_work(db, "T1", "m1", "ref2", True), "ya_conciliado")
            self.assertEqual(recepcion.conciliar_con_work(db, "T1", "otro", "ref1"), "sin_registro")
            self.assertEqual(recepcion.conciliar_con_work(db, "", "m1", "ref1"), "sin_identidad")
            self.assertEqual(recepcion.conciliar_con_work(db, "T1", "  ", "ref1"), "sin_identidad")

    def test_una_evidencia_ilegible_no_impide_conciliar(self):
        self.fila("T1", "m1")
        with store.transaccion() as db:
            db.execute("UPDATE recepcion_mensajes SET evidence_json='no es json'")
            self.assertEqual(recepcion.conciliar_con_work(db, "T1", "m1", "ref1"), "conciliado")
        self.assertEqual(json.loads(self.filas()[("T1", "m1")]["evidence_json"])["work"]["analysis_ref"], "ref1")


if __name__ == "__main__":
    unittest.main()
