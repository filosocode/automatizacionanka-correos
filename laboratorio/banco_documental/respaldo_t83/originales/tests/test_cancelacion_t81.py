"""Cancelación segura de mensajes y trabajos ficticios: implementación de referencia de laboratorio (T-81).

SQLite temporal, Drive y Gmail simulados y mensajes ficticios (los de `tests.test_coordinacion`): nada toca Google, la
configuración operativa ni los datos reales. La cancelación vive en `laboratorio/`; `anka/` no cambia.

Cubre: T-81
"""

from tests import _isolate  # noqa: F401  (debe ir primero)

import json
import sqlite3
import threading
import time
import unittest
from collections.abc import Callable
from dataclasses import replace
from pathlib import Path
from typing import Any

from anka.dominio.trabajo_work import id_de_trabajo
from anka.infra import store
from anka.servicios import despacho, recepcion, trabajos_work, work_import
from anka.servicios.despacho import Alcance
from laboratorio.banco_documental import cancelacion as c
from laboratorio.banco_documental.cancelacion import Autorizacion, CancelacionRechazada
from tests.test_aislamiento_t79 import ETIQUETA, LAB, REAL
from anka.dominio.coordinacion_work import Plan
from tests.test_coordinacion import CARPETA, plan
from tests.test_recuperacion_t80 import ANTES, DESPUES
from tests.test_recuperacion_t80 import Base as BaseDeT80

MARCA = "[PI-T79-PRUEBA-01]"
LABORATORIO = Path(__file__).resolve().parents[1] / "laboratorio" / "banco_documental" / "cancelacion.py"


def autorizacion(ids: tuple[str, str] = LAB, **cambios: Any) -> Autorizacion:
    datos: dict[str, Any] = {"thread_id": ids[0], "message_id": ids[1], "operador": "operador.prueba",
                             "motivo": "prueba integrada cancelada", "etiqueta": MARCA}
    datos.update(cambios)
    return Autorizacion(**datos)


class Base(BaseDeT80):
    @property
    def plan_(self) -> Plan:
        return plan()

    def cancelar(self, aut: Autorizacion | None = None, ahora: Callable[[], str] | None = None) -> dict[str, Any]:
        db = store.connect()
        try:
            return c.cancelar_mensaje(db, aut or autorizacion(), ahora) if ahora else c.cancelar_mensaje(db, aut or autorizacion())
        finally:
            db.close()

    def rechazo(self, aut: Autorizacion | None = None) -> str:
        with self.assertRaises(CancelacionRechazada) as contexto:
            self.cancelar(aut)
        return contexto.exception.codigo

    def foto(self) -> dict[str, list[dict[str, Any]]]:
        with store.lectura() as db:
            return {tabla: [dict(f) for f in db.execute(f"SELECT * FROM {tabla} ORDER BY 1").fetchall()]
                    for tabla in ("recepcion_mensajes", "trabajos_work", "recepcion_eventos")}

    def eventos_de_cancelacion(self) -> list[dict[str, Any]]:
        with store.lectura() as db:
            return [dict(f) for f in db.execute("SELECT * FROM recepcion_eventos WHERE event_kind=? ORDER BY id", (c.EVENTO,))]

    def sembrar_lab(self, asunto: str = ETIQUETA) -> None:
        self.fila(*LAB, asunto=asunto, remitente="prueba.controlada@example.com")


class IdentidadYAlcanceTest(Base):
    # Cubre: T-81
    def test_01_las_constantes_replicadas_coinciden_con_las_del_sistema(self) -> None:
        self.assertEqual((c.CUENTA, c.PENDIENTE), (recepcion.CUENTA, recepcion.PENDIENTE))

    # Cubre: T-81
    def test_02_el_modulo_solo_importa_el_dominio_puro_y_no_conoce_gmail_drive_ni_configuracion(self) -> None:
        lineas = [lin for lin in LABORATORIO.read_text(encoding="utf-8").splitlines() if lin.startswith(("import ", "from "))]
        texto = " ".join(lineas)
        for prohibido in ("anka.infra", "anka.servicios", "anka.cli", "anka.web", "googleapiclient", "requests", "settings"):
            self.assertNotIn(prohibido, texto)
        self.assertIn("anka.dominio", texto)

    # Cubre: T-81
    def test_03_solo_acepta_identificadores_literales_sin_comodines_ni_inyeccion(self) -> None:
        self.sembrar_lab()
        for mal in ("", " ", "T9%", "T*", "T9 OR 1=1", "T9'; DROP TABLE trabajos_work;--", "T9\nT8", "a" * 129):
            self.assertEqual(self.rechazo(autorizacion(thread_id=mal)), "identidad_invalida", repr(mal))
            self.assertEqual(self.rechazo(autorizacion(message_id=mal)), "identidad_invalida", repr(mal))
        self.assertEqual(self.trabajos_en_total(), 0)

    # Cubre: T-81
    def test_04_exige_operador_motivo_y_etiqueta_con_la_forma_correcta(self) -> None:
        self.sembrar_lab()
        self.assertEqual(self.rechazo(autorizacion(operador="  ")), "falta_operador")
        self.assertEqual(self.rechazo(autorizacion(motivo="")), "falta_motivo")
        for etiqueta in ("", "PI-T79", "[PI-]", "[pi-t79-prueba-01]", "[PI-T79] extra", "T79"):
            self.assertEqual(self.rechazo(autorizacion(etiqueta=etiqueta)), "etiqueta_invalida", etiqueta)
        self.assertEqual(self.trabajos_en_total(), 0)

    # Cubre: T-81
    def test_05_no_cancela_un_mensaje_real_aunque_se_conozcan_sus_ids(self) -> None:
        self.sembrar_pausa()
        antes = self.foto()
        for ids in (REAL, ANTES, DESPUES):
            self.assertEqual(self.rechazo(autorizacion(ids)), "no_es_de_prueba", str(ids))
        self.assertEqual(self.foto(), antes, "un rechazo no escribe nada")

    # Cubre: T-81
    def test_06_no_hay_cancelacion_por_asunto_remitente_ni_coincidencia_parcial(self) -> None:
        self.sembrar_pausa()
        self.assertEqual(self.rechazo(autorizacion(("T", "m"))), "mensaje_no_registrado")
        self.assertEqual(self.rechazo(autorizacion((LAB[0], "m"))), "mensaje_no_registrado")
        self.assertEqual(self.rechazo(autorizacion(("T9", "m9x"))), "mensaje_no_registrado")
        self.assertEqual(self.trabajos_en_total(), 0)

    # Cubre: T-81
    def test_07_un_mensaje_que_ya_no_esta_pendiente_se_rechaza(self) -> None:
        self.sembrar_lab()
        with store.transaccion() as db:
            db.execute("UPDATE recepcion_mensajes SET state='procesado',disposition='interpretado_por_work' "
                       "WHERE message_id=?", (LAB[1],))
        self.assertEqual(self.rechazo(), "mensaje_no_pendiente")
        self.assertEqual(self.trabajos_en_total(), 0)


class CancelarTest(Base):
    # Cubre: T-81
    def test_10_cancelar_tras_registrar_y_sin_trabajo_deja_una_lapida_con_toda_la_huella(self) -> None:
        self.sembrar_pausa()
        antes = self.foto()
        resultado = self.cancelar(ahora=lambda: "2026-10-17T10:20:00-05:00")
        self.assertEqual((resultado["resultado"], resultado["estado_previo"], resultado["verificada"]),
                         ("cancelado", "sin_trabajo", True))
        self.assertEqual(self.estado_lab(), "cancelado")
        detalle = json.loads(self.eventos_de_cancelacion()[0]["detail_json"])
        self.assertEqual((detalle["operador"], detalle["motivo"], detalle["etiqueta"], detalle["cancelado_at"]),
                         ("operador.prueba", "prueba integrada cancelada", MARCA, "2026-10-17T10:20:00-05:00"))
        self.assertEqual(detalle["job_id"], resultado["job_id"])
        despues = self.foto()
        self.assertEqual(despues["recepcion_mensajes"], antes["recepcion_mensajes"], "no se borra ni se cambia el mensaje")
        self.assertEqual(len(despues["recepcion_eventos"]), len(antes["recepcion_eventos"]) + 1)
        self.assertTrue(all(f in despues["recepcion_eventos"] for f in antes["recepcion_eventos"]), "nada de la evidencia se pierde")

    # Cubre: T-81
    def test_11_la_lapida_usa_el_mismo_job_id_que_tendria_el_trabajo_real(self) -> None:
        self.sembrar_lab()
        resultado = self.cancelar()
        esperado = id_de_trabajo(c.CUENTA, *LAB, "analizar_solicitud", "ANKA_DASHBOARD_JSON_V1")
        self.assertEqual(resultado["job_id"], esperado)

    # Cubre: T-81
    def test_12_cancelar_antes_de_crear_el_trabajo_impide_que_el_coordinador_lo_cree_o_publique(self) -> None:
        self.sembrar_pausa()
        self.cancelar()
        self.ciclos(8, max_publicar=3)
        self.assertEqual(self.estados(), {"m1": "publicado", "m0": "publicado", "m9": "cancelado", "m2": "publicado"})
        self.assertEqual(len(self.hojas()), 3)
        self.assertEqual(trabajos_work.mensajes_nuevos(self.plan_.desde), [])
        self.sin_duplicados()

    # Cubre: T-81
    def test_13_crear_trabajo_sobre_una_lapida_devuelve_la_lapida_y_no_crea_otro(self) -> None:
        self.sembrar_lab()
        self.cancelar()
        devuelto = trabajos_work.crear_trabajo(*LAB)
        self.assertEqual((devuelto["state"], devuelto["creado"]), ("cancelado", False))
        self.assertEqual(self.trabajos_en_total(), 1)
        with self.assertRaises(trabajos_work.TrabajoRechazado):
            despacho.despachar(self.drive, CARPETA, Alcance(*LAB))
        self.assertEqual(len(self.hojas()), 0)

    # Cubre: T-81
    def test_14_cancelar_con_trabajo_pendiente_lo_pasa_a_cancelado_y_no_se_publica(self) -> None:
        self.sembrar_pausa()
        creado = trabajos_work.crear_trabajo(*LAB)
        resultado = self.cancelar()
        self.assertEqual((resultado["estado_previo"], resultado["job_id"], resultado["revisar_drive"]),
                         ("pendiente_de_publicar", creado["job_id"], False))
        self.ciclos(8, max_publicar=3)
        self.assertEqual(self.estado_lab(), "cancelado")
        self.assertEqual(len(self.hojas()), 3)
        self.assertEqual(self.trabajos_en_total(), 4)

    # Cubre: T-81
    def test_15_un_pendiente_con_intentos_fallidos_se_cancela_pero_pide_revisar_drive(self) -> None:
        self.sembrar_lab()
        trabajo = trabajos_work.crear_trabajo(*LAB)
        with store.transaccion() as db:
            db.execute("UPDATE trabajos_work SET attempts=5,last_error='error_de_drive:ErrorHttp' WHERE job_id=?",
                       (trabajo["job_id"],))
        resultado = self.cancelar()
        self.assertEqual((resultado["resultado"], resultado["revisar_drive"]), ("cancelado", True))
        self.assertEqual(json.loads(self.eventos_de_cancelacion()[0]["detail_json"])["intentos_previos"], 5)

    # Cubre: T-81
    def test_16_un_pendiente_que_ya_guarda_una_hoja_exige_a_una_persona(self) -> None:
        self.sembrar_lab()
        trabajo = trabajos_work.crear_trabajo(*LAB)
        with store.transaccion() as db:
            db.execute("UPDATE trabajos_work SET sheet_file_id='sheet-x' WHERE job_id=?", (trabajo["job_id"],))
        self.assertEqual(self.rechazo(), "ya_publicado_requiere_persona")
        self.assertEqual(self.estado_lab(), "pendiente_de_publicar")

    # Cubre: T-81
    def test_17_tras_publicar_se_rechaza_y_exige_intervencion_humana_sin_cambiar_nada(self) -> None:
        self.sembrar_pausa()
        despacho.despachar(self.drive, CARPETA, Alcance(*LAB))
        antes, hojas = self.foto(), [h["id"] for h in self.hojas()]
        self.assertEqual(self.rechazo(), "ya_publicado_requiere_persona")
        self.assertEqual((self.foto(), [h["id"] for h in self.hojas()]), (antes, hojas))

    # Cubre: T-81
    def test_18_un_trabajo_conciliado_no_se_toca_nunca(self) -> None:
        self.sembrar(con_real=False)
        despacho.despachar(self.drive, CARPETA, Alcance(*LAB))
        self.work_responde(LAB[1])
        self.ciclos(2)
        self.assertEqual(self.estado_lab(), "conciliado")
        antes = self.foto()
        self.assertIn(self.rechazo(), ("conciliado_intocable", "mensaje_no_pendiente"))
        self.assertEqual(self.foto(), antes)

    # Cubre: T-81
    def test_19_un_trabajo_reclamado_para_publicar_no_se_cancela(self) -> None:
        self.sembrar_lab()
        trabajo = trabajos_work.crear_trabajo(*LAB)
        token, _ = trabajos_work._reclamar(trabajo["job_id"], self.t, "2026-10-17T10:00:00-05:00")
        self.assertTrue(token)
        self.assertEqual(self.rechazo(), "publicando_en_curso")
        self.assertEqual(self.estado_lab(), "publicando")


class IdempotenciaYErroresTest(Base):
    # Cubre: T-81
    def test_20_repetir_la_cancelacion_no_escribe_nada_mas(self) -> None:
        self.sembrar_lab()
        primero = self.cancelar()
        antes = self.foto()
        for _ in range(3):
            otra = self.cancelar()
            self.assertEqual((otra["resultado"], otra["job_id"]), ("ya_cancelado", primero["job_id"]))
        self.assertEqual(self.foto(), antes)
        self.assertEqual(len(self.eventos_de_cancelacion()), 1)

    # Cubre: T-81
    def test_21_cancelar_un_trabajo_pendiente_dos_veces_tampoco_duplica(self) -> None:
        self.sembrar_lab()
        trabajos_work.crear_trabajo(*LAB)
        self.cancelar()
        self.assertEqual(self.cancelar()["resultado"], "ya_cancelado")
        self.assertEqual((len(self.eventos_de_cancelacion()), self.trabajos_en_total()), (1, 1))

    # Cubre: T-81
    def test_22_un_error_a_mitad_de_la_operacion_revierte_todo_y_el_reintento_funciona(self) -> None:
        self.sembrar_lab()
        antes = self.foto()

        def reloj_roto() -> str:
            raise sqlite3.OperationalError("simulado: disco lleno")

        with self.assertRaises(sqlite3.OperationalError):
            self.cancelar(ahora=reloj_roto)
        self.assertEqual(self.foto(), antes)
        self.assertEqual(self.cancelar()["resultado"], "cancelado")

    # Cubre: T-81
    def test_23_no_acepta_una_conexion_con_una_transaccion_abierta(self) -> None:
        self.sembrar_lab()
        db = store.connect()
        try:
            db.execute("BEGIN IMMEDIATE")
            with self.assertRaises(RuntimeError):
                c.cancelar_mensaje(db, autorizacion())
            db.execute("ROLLBACK")
        finally:
            db.close()
        self.assertEqual(self.trabajos_en_total(), 0)

    # Cubre: T-81
    def test_24_un_fallo_de_drive_despues_de_cancelar_no_resucita_el_trabajo(self) -> None:
        self.sembrar_lab()
        self.cancelar()
        self.drive.fallar_listado = ErrorHttpSimulado()
        self.ciclos(3)
        self.drive.fallar_listado = None
        self.ciclos(3)
        self.assertEqual((self.estado_lab(), len(self.hojas())), ("cancelado", 0))


def ErrorHttpSimulado() -> Exception:
    from tests.test_trabajos_work import ErrorHttp

    return ErrorHttp(500)


class PreventivaTest(Base):
    # Cubre: T-81
    def test_30_antes_de_registrar_el_mensaje_exige_la_cancelacion_preventiva_explicita(self) -> None:
        self.assertEqual(self.rechazo(), "mensaje_no_registrado")
        self.assertEqual(self.trabajos_en_total(), 0)

    # Cubre: T-81
    def test_31_la_preventiva_deja_una_lapida_no_verificada_y_el_mensaje_no_se_publica_al_registrarse(self) -> None:
        resultado = self.cancelar(autorizacion(preventiva=True))
        self.assertEqual((resultado["resultado"], resultado["verificada"]), ("cancelado", False))
        db = store.connect()
        try:
            self.assertEqual(c.verificar_lapida_preventiva(db, autorizacion()), {"estado": "aun_no_registrado"})
        finally:
            db.close()
        self.sembrar_lab()                                  # el receptor lo registra después
        self.ciclos(5)
        self.assertEqual((self.estado_lab(), len(self.hojas())), ("cancelado", 0))
        db = store.connect()
        try:
            self.assertEqual(c.verificar_lapida_preventiva(db, autorizacion()), {"estado": "registrado", "coincide": True})
        finally:
            db.close()

    # Cubre: T-81
    def test_32_si_el_mensaje_registrado_despues_no_lleva_la_etiqueta_la_verificacion_lo_avisa(self) -> None:
        self.cancelar(autorizacion(preventiva=True))
        self.fila(*LAB, asunto="Solicitud de levantamiento real", remitente="cliente@example.com")
        db = store.connect()
        try:
            self.assertEqual(c.verificar_lapida_preventiva(db, autorizacion()), {"estado": "registrado", "coincide": False})
        finally:
            db.close()

    # Cubre: T-81
    def test_33_la_preventiva_repetida_es_idempotente(self) -> None:
        self.cancelar(autorizacion(preventiva=True))
        self.assertEqual(self.cancelar(autorizacion(preventiva=True))["resultado"], "ya_cancelado")
        self.assertEqual(len(self.eventos_de_cancelacion()), 1)


class CarrerasTest(Base):
    # Cubre: T-81
    def test_40_si_el_coordinador_publica_primero_la_cancelacion_se_rechaza(self) -> None:
        self.sembrar_lab()
        self.ciclo()
        self.assertEqual(self.estado_lab(), "publicado")
        self.assertEqual(self.rechazo(), "ya_publicado_requiere_persona")
        self.assertEqual(len(self.hojas()), 1)

    # Cubre: T-81
    def test_41_si_la_cancelacion_va_primero_el_ciclo_no_publica_nada(self) -> None:
        self.sembrar_lab()
        self.cancelar()
        informe = self.ciclo(max_publicar=5)
        self.assertEqual((informe["publicaciones"], len(self.hojas())), ([], 0))

    # Cubre: T-81
    def test_42_con_hilos_reales_el_resultado_siempre_es_coherente(self) -> None:
        """Veinte carreras: la cancelación y un ciclo del coordinador a la vez sobre el mismo mensaje."""
        resultados = [self._carrera(numero) for numero in range(20)]
        self.assertEqual([r for r in resultados if r.startswith("INCOHERENTE")], [])
        print(f"[T-81] 20 carreras: {sorted(set(resultados))} cancelado={resultados.count('cancelado')}")

    def _carrera(self, numero: int) -> str:
        hilo, mensaje = f"TH{numero}", f"MS{numero}"
        self.fila(hilo, mensaje, asunto=ETIQUETA, remitente="prueba.controlada@example.com")
        coordinador = self.nuevo()
        plan_del_mensaje = replace(self.plan_, max_publicar=5, thread_id=hilo, message_id=mensaje)
        salida: dict[str, Any] = {}
        inicio = threading.Barrier(2)

        def cancelando() -> None:
            inicio.wait()
            time.sleep((numero % 10) * 0.012)          # reparte los choques: unas veces gana la cancelación, otras el ciclo
            try:
                salida["cancelacion"] = self.cancelar(autorizacion((hilo, mensaje)))["resultado"]
            except CancelacionRechazada as exc:
                salida["cancelacion"] = exc.codigo

        def coordinando() -> None:
            inicio.wait()
            salida["ciclo"] = coordinador.ciclo(plan_del_mensaje)

        hilos = [threading.Thread(target=cancelando), threading.Thread(target=coordinando)]
        for h in hilos:
            h.start()
        for h in hilos:
            h.join(60)
        estado = self.estado_de(mensaje, hilo)
        hojas = [x for x in self.hojas() if self._de(x, hilo, mensaje)]
        cancelacion = str(salida.get("cancelacion"))
        if cancelacion == "cancelado":
            ok = estado == "cancelado" and not hojas
        else:
            ok = estado in ("publicado", "publicando") and cancelacion in (
                "ya_publicado_requiere_persona", "publicando_en_curso", "carrera") and len(hojas) == 1
        return cancelacion if ok else f"INCOHERENTE {cancelacion}/{estado}/{len(hojas)}"

    def _de(self, hoja: dict[str, Any], hilo: str, mensaje: str) -> bool:
        trabajo = self.trabajo_de(mensaje, hilo)
        return bool(trabajo) and hoja["props"].get("anka_job_id") == trabajo["job_id"]


class ContinuidadTest(Base):
    # Cubre: T-81
    def test_50_los_reales_acumulados_durante_la_pausa_siguen_su_curso_tras_cancelar_y_restaurar(self) -> None:
        self.sembrar_pausa()
        self.cancelar()
        orden = []
        for _ in range(4):
            informe = self.ciclo()
            orden += [trabajos_work.obtener(p["job_id"])["message_id"] for p in informe["publicaciones"]]
            self.t += 400
        self.assertEqual(orden, ["m1", "m0", "m2"], "mismo orden de llegada, sin el ficticio")
        antes = (self.trabajos_en_total(), len(self.hojas()))
        self.ciclos(5)
        self.assertEqual((self.trabajos_en_total(), len(self.hojas())), antes)
        self.assertEqual(antes, (4, 3))

    # Cubre: T-81
    def test_51_cancelar_no_cambia_a_ningun_otro_mensaje_ni_trabajo(self) -> None:
        self.sembrar_pausa()
        creado = trabajos_work.crear_trabajo(*REAL)
        antes = self.foto()
        self.cancelar()
        despues = self.foto()
        otros = lambda filas: [f for f in filas if f.get("message_id") != LAB[1]]  # noqa: E731
        self.assertEqual(otros(despues["recepcion_mensajes"]), otros(antes["recepcion_mensajes"]))
        self.assertEqual(otros(despues["trabajos_work"]), otros(antes["trabajos_work"]))
        self.assertEqual(trabajos_work.obtener(creado["job_id"])["state"], "pendiente_de_publicar")

    # Cubre: T-81
    def test_52_el_estado_cancelado_ya_es_conocido_por_el_resto_del_sistema(self) -> None:
        from anka.dominio import trabajo_work
        from anka.servicios import estado_operativo

        self.assertIn("cancelado", trabajo_work.ESTADOS)
        self.assertNotIn("cancelado", trabajo_work.CONCILIABLES)
        self.assertEqual(estado_operativo.ESTADOS_DE_TRABAJO["cancelado"], ("Cancelado", "neutro"))


class HuecosDeProduccionTest(Base):
    # Cubre: T-81
    def test_60_el_importador_actual_no_respeta_la_lapida_si_le_llega_un_borrador(self) -> None:
        """Hueco que SIGUE ABIERTO: la lapida frena al coordinador, no a una importación directa (puente general, manual)."""
        self.sembrar_lab()
        self.cancelar()
        self.borradores[LAB[1]] = ["valido"]
        resultado = self.importar_simulado(*LAB)
        self.assertEqual(resultado["resultado"], "importado", "el importador no mira la lápida")
        self.assertEqual(self.estado_lab(), "cancelado", "el trabajo sigue cancelado")
        with store.lectura() as db:
            fila = db.execute("SELECT state,disposition FROM recepcion_mensajes WHERE message_id=?", (LAB[1],)).fetchone()
        self.assertEqual(tuple(fila), recepcion.INTERPRETADO, "pero el mensaje queda interpretado y el análisis, guardado")
        self.assertTrue(callable(work_import.dashboard_upsert))

    # Cubre: T-81
    def test_61_la_cancelacion_no_cambia_nada_fuera_de_las_dos_tablas_de_recepcion_y_trabajos(self) -> None:
        self.sembrar_lab()
        with store.lectura() as db:
            tablas = [f[0] for f in db.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")
                      if f[0] not in ("trabajos_work", "recepcion_eventos", "sqlite_sequence")]
            antes = {t: [tuple(f) for f in db.execute(f"SELECT * FROM {t}")] for t in tablas}
        self.cancelar()
        with store.lectura() as db:
            despues = {t: [tuple(f) for f in db.execute(f"SELECT * FROM {t}")] for t in tablas}
        self.assertEqual(despues, antes)


if __name__ == "__main__":
    unittest.main()
