"""Cancelación productiva de mensajes y trabajos ficticios (T-83): operación, rechazos, carreras y continuidad.

SQLite temporal, Drive y Gmail simulados y mensajes ficticios (los de `tests.test_coordinacion`): nada toca Google, la
configuración operativa ni los datos reales. Se ejecuta solo con la integración de la cancelación aplicada.

Cubre: T-83
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

import contextlib
import io
from unittest.mock import patch

from anka.cli import work_jobs
from anka.dominio.cancelacion_work import MensajeCancelado
from anka.infra import backup, lapidas
from anka.servicios import coordinacion, estado_operativo, quotes
from tests.test_importacion_aislada import GmailAislado, borrador
from tests.test_importacion_aislada import ID as ID_AISLADO
from tests.test_work_sync import b64, payload

from anka.dominio.trabajo_work import id_de_trabajo
from anka.infra import store
from anka.servicios import despacho, recepcion, trabajos_work, work_import
from anka.servicios.despacho import Alcance
from anka.dominio.cancelacion_work import EVENTO_DE_CANCELACION, Autorizacion, CancelacionRechazada
from anka.servicios import cancelaciones
from tests.test_aislamiento_t79 import ETIQUETA, LAB, REAL
from anka.dominio.coordinacion_work import Plan
from tests.test_coordinacion import CARPETA, plan
from tests.test_recuperacion_t80 import ANTES, DESPUES
from tests.test_recuperacion_t80 import Base as BaseDeT80

MARCA = "[PI-T79-PRUEBA-01]"
HILO_AISLADO = (ID_AISLADO, ID_AISLADO)


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
        return cancelaciones.cancelar_mensaje(aut or autorizacion(), ahora)

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
            return [dict(f) for f in db.execute("SELECT * FROM recepcion_eventos WHERE event_kind=? ORDER BY id", (EVENTO_DE_CANCELACION,))]

    def sembrar_lab(self, asunto: str = ETIQUETA) -> None:
        self.fila(*LAB, asunto=asunto, remitente="prueba.controlada@example.com")


class IdentidadYAlcanceTest(Base):
    # Cubre: T-83
    def test_03_solo_acepta_identificadores_literales_sin_comodines_ni_inyeccion(self) -> None:
        self.sembrar_lab()
        for mal in ("", " ", "T9%", "T*", "T9 OR 1=1", "T9'; DROP TABLE trabajos_work;--", "T9\nT8", "a" * 129):
            self.assertEqual(self.rechazo(autorizacion(thread_id=mal)), "identidad_invalida", repr(mal))
            self.assertEqual(self.rechazo(autorizacion(message_id=mal)), "identidad_invalida", repr(mal))
        self.assertEqual(self.trabajos_en_total(), 0)

    # Cubre: T-83
    def test_04_exige_operador_motivo_y_etiqueta_con_la_forma_correcta(self) -> None:
        self.sembrar_lab()
        self.assertEqual(self.rechazo(autorizacion(operador="  ")), "falta_operador")
        self.assertEqual(self.rechazo(autorizacion(motivo="")), "falta_motivo")
        for etiqueta in ("", "PI-T79", "[PI-]", "[pi-t79-prueba-01]", "[PI-T79] extra", "T79"):
            self.assertEqual(self.rechazo(autorizacion(etiqueta=etiqueta)), "etiqueta_invalida", etiqueta)
        self.assertEqual(self.trabajos_en_total(), 0)

    # Cubre: T-83
    def test_05_no_cancela_un_mensaje_real_aunque_se_conozcan_sus_ids(self) -> None:
        self.sembrar_pausa()
        antes = self.foto()
        for ids in (REAL, ANTES, DESPUES):
            self.assertEqual(self.rechazo(autorizacion(ids)), "no_es_de_prueba", str(ids))
        self.assertEqual(self.foto(), antes, "un rechazo no escribe nada")

    # Cubre: T-83
    def test_06_no_hay_cancelacion_por_asunto_remitente_ni_coincidencia_parcial(self) -> None:
        self.sembrar_pausa()
        self.assertEqual(self.rechazo(autorizacion(("T", "m"))), "mensaje_no_registrado")
        self.assertEqual(self.rechazo(autorizacion((LAB[0], "m"))), "mensaje_no_registrado")
        self.assertEqual(self.rechazo(autorizacion(("T9", "m9x"))), "mensaje_no_registrado")
        self.assertEqual(self.trabajos_en_total(), 0)

    # Cubre: T-83
    def test_07_un_mensaje_que_ya_no_esta_pendiente_se_rechaza(self) -> None:
        self.sembrar_lab()
        with store.transaccion() as db:
            db.execute("UPDATE recepcion_mensajes SET state='procesado',disposition='interpretado_por_work' "
                       "WHERE message_id=?", (LAB[1],))
        self.assertEqual(self.rechazo(), "mensaje_no_pendiente")
        self.assertEqual(self.trabajos_en_total(), 0)


class CancelarTest(Base):
    # Cubre: T-83
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

    # Cubre: T-83
    def test_11_la_lapida_usa_el_mismo_job_id_que_tendria_el_trabajo_real(self) -> None:
        self.sembrar_lab()
        resultado = self.cancelar()
        esperado = id_de_trabajo(recepcion.CUENTA, *LAB, "analizar_solicitud", "ANKA_DASHBOARD_JSON_V1")
        self.assertEqual(resultado["job_id"], esperado)

    # Cubre: T-83
    def test_12_cancelar_antes_de_crear_el_trabajo_impide_que_el_coordinador_lo_cree_o_publique(self) -> None:
        self.sembrar_pausa()
        self.cancelar()
        self.ciclos(8, max_publicar=3)
        self.assertEqual(self.estados(), {"m1": "publicado", "m0": "publicado", "m9": "cancelado", "m2": "publicado"})
        self.assertEqual(len(self.hojas()), 3)
        self.assertEqual(trabajos_work.mensajes_nuevos(self.plan_.desde), [])
        self.sin_duplicados()

    # Cubre: T-83
    def test_13_crear_trabajo_sobre_una_lapida_devuelve_la_lapida_y_no_crea_otro(self) -> None:
        self.sembrar_lab()
        self.cancelar()
        devuelto = trabajos_work.crear_trabajo(*LAB)
        self.assertEqual((devuelto["state"], devuelto["creado"]), ("cancelado", False))
        self.assertEqual(self.trabajos_en_total(), 1)
        self.assertEqual(despacho.despachar(self.drive, CARPETA, Alcance(*LAB))["publicacion"]["resultado"], "cancelado")
        self.assertEqual(len(self.hojas()), 0)

    # Cubre: T-83
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

    # Cubre: T-83
    def test_15_un_pendiente_con_intentos_fallidos_se_cancela_pero_pide_revisar_drive(self) -> None:
        self.sembrar_lab()
        trabajo = trabajos_work.crear_trabajo(*LAB)
        with store.transaccion() as db:
            db.execute("UPDATE trabajos_work SET attempts=5,last_error='error_de_drive:ErrorHttp' WHERE job_id=?",
                       (trabajo["job_id"],))
        resultado = self.cancelar()
        self.assertEqual((resultado["resultado"], resultado["revisar_drive"]), ("cancelado", True))
        self.assertEqual(json.loads(self.eventos_de_cancelacion()[0]["detail_json"])["intentos_previos"], 5)

    # Cubre: T-83
    def test_16_un_pendiente_que_ya_guarda_una_hoja_exige_a_una_persona(self) -> None:
        self.sembrar_lab()
        trabajo = trabajos_work.crear_trabajo(*LAB)
        with store.transaccion() as db:
            db.execute("UPDATE trabajos_work SET sheet_file_id='sheet-x' WHERE job_id=?", (trabajo["job_id"],))
        self.assertEqual(self.rechazo(), "ya_publicado_requiere_persona")
        self.assertEqual(self.estado_lab(), "pendiente_de_publicar")

    # Cubre: T-83
    def test_17_tras_publicar_se_rechaza_y_exige_intervencion_humana_sin_cambiar_nada(self) -> None:
        self.sembrar_pausa()
        despacho.despachar(self.drive, CARPETA, Alcance(*LAB))
        antes, hojas = self.foto(), [h["id"] for h in self.hojas()]
        self.assertEqual(self.rechazo(), "ya_publicado_requiere_persona")
        self.assertEqual((self.foto(), [h["id"] for h in self.hojas()]), (antes, hojas))

    # Cubre: T-83
    def test_18_un_trabajo_conciliado_no_se_toca_nunca(self) -> None:
        self.sembrar(con_real=False)
        despacho.despachar(self.drive, CARPETA, Alcance(*LAB))
        self.work_responde(LAB[1])
        self.ciclos(2)
        self.assertEqual(self.estado_lab(), "conciliado")
        antes = self.foto()
        self.assertIn(self.rechazo(), ("conciliado_intocable", "mensaje_no_pendiente"))
        self.assertEqual(self.foto(), antes)

    # Cubre: T-83
    def test_19_un_trabajo_reclamado_para_publicar_no_se_cancela(self) -> None:
        self.sembrar_lab()
        trabajo = trabajos_work.crear_trabajo(*LAB)
        token, _ = trabajos_work._reclamar(trabajo["job_id"], self.t, "2026-10-17T10:00:00-05:00")
        self.assertTrue(token)
        self.assertEqual(self.rechazo(), "publicando_en_curso")
        self.assertEqual(self.estado_lab(), "publicando")


class IdempotenciaYErroresTest(Base):
    # Cubre: T-83
    def test_20_repetir_la_cancelacion_no_escribe_nada_mas(self) -> None:
        self.sembrar_lab()
        primero = self.cancelar()
        antes = self.foto()
        for _ in range(3):
            otra = self.cancelar()
            self.assertEqual((otra["resultado"], otra["job_id"]), ("ya_cancelado", primero["job_id"]))
        self.assertEqual(self.foto(), antes)
        self.assertEqual(len(self.eventos_de_cancelacion()), 1)

    # Cubre: T-83
    def test_21_cancelar_un_trabajo_pendiente_dos_veces_tampoco_duplica(self) -> None:
        self.sembrar_lab()
        trabajos_work.crear_trabajo(*LAB)
        self.cancelar()
        self.assertEqual(self.cancelar()["resultado"], "ya_cancelado")
        self.assertEqual((len(self.eventos_de_cancelacion()), self.trabajos_en_total()), (1, 1))

    # Cubre: T-83
    def test_22_un_error_a_mitad_de_la_operacion_revierte_todo_y_el_reintento_funciona(self) -> None:
        self.sembrar_lab()
        antes = self.foto()

        def reloj_roto() -> str:
            raise sqlite3.OperationalError("simulado: disco lleno")

        with self.assertRaises(sqlite3.OperationalError):
            self.cancelar(ahora=reloj_roto)
        self.assertEqual(self.foto(), antes)
        self.assertEqual(self.cancelar()["resultado"], "cancelado")

    # Cubre: T-83
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
    # Cubre: T-83
    def test_30_antes_de_registrar_el_mensaje_exige_la_cancelacion_preventiva_explicita(self) -> None:
        self.assertEqual(self.rechazo(), "mensaje_no_registrado")
        self.assertEqual(self.trabajos_en_total(), 0)

    # Cubre: T-83
    def test_31_la_preventiva_deja_una_lapida_no_verificada_y_el_mensaje_no_se_publica_al_registrarse(self) -> None:
        resultado = self.cancelar(autorizacion(preventiva=True))
        self.assertEqual((resultado["resultado"], resultado["verificada"]), ("cancelado", False))
        self.assertEqual(cancelaciones.verificar_lapida_preventiva(autorizacion()), {"estado": "aun_no_registrado"})
        self.sembrar_lab()                                  # el receptor lo registra después
        self.ciclos(5)
        self.assertEqual((self.estado_lab(), len(self.hojas())), ("cancelado", 0))
        self.assertEqual(cancelaciones.verificar_lapida_preventiva(autorizacion()), {"estado": "registrado", "coincide": True})

    # Cubre: T-83
    def test_32_si_el_mensaje_registrado_despues_no_lleva_la_etiqueta_la_verificacion_lo_avisa(self) -> None:
        self.cancelar(autorizacion(preventiva=True))
        self.fila(*LAB, asunto="Solicitud de levantamiento real", remitente="cliente@example.com")
        self.assertEqual(cancelaciones.verificar_lapida_preventiva(autorizacion()), {"estado": "registrado", "coincide": False})

    # Cubre: T-83
    def test_33_la_preventiva_repetida_es_idempotente(self) -> None:
        self.cancelar(autorizacion(preventiva=True))
        self.assertEqual(self.cancelar(autorizacion(preventiva=True))["resultado"], "ya_cancelado")
        self.assertEqual(len(self.eventos_de_cancelacion()), 1)


class CarrerasTest(Base):
    # Cubre: T-83
    def test_40_si_el_coordinador_publica_primero_la_cancelacion_se_rechaza(self) -> None:
        self.sembrar_lab()
        self.ciclo()
        self.assertEqual(self.estado_lab(), "publicado")
        self.assertEqual(self.rechazo(), "ya_publicado_requiere_persona")
        self.assertEqual(len(self.hojas()), 1)

    # Cubre: T-83
    def test_41_si_la_cancelacion_va_primero_el_ciclo_no_publica_nada(self) -> None:
        self.sembrar_lab()
        self.cancelar()
        informe = self.ciclo(max_publicar=5)
        self.assertEqual((informe["publicaciones"], len(self.hojas())), ([], 0))

    # Cubre: T-83
    def test_42_con_hilos_reales_el_resultado_siempre_es_coherente(self) -> None:
        """Veinte carreras: la cancelación y un ciclo del coordinador a la vez sobre el mismo mensaje."""
        resultados = [self._carrera(numero) for numero in range(20)]
        self.assertEqual([r for r in resultados if r.startswith("INCOHERENTE")], [])
        print(f"[T-83] 20 carreras: {sorted(set(resultados))} cancelado={resultados.count('cancelado')}")

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
    # Cubre: T-83
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

    # Cubre: T-83
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

    # Cubre: T-83
    def test_52_el_estado_cancelado_ya_es_conocido_por_el_resto_del_sistema(self) -> None:
        from anka.dominio import trabajo_work
        from anka.servicios import estado_operativo

        self.assertIn("cancelado", trabajo_work.ESTADOS)
        self.assertNotIn("cancelado", trabajo_work.CONCILIABLES)
        self.assertEqual(estado_operativo.ESTADOS_DE_TRABAJO["cancelado"], ("Cancelado", "neutro"))


class PuertasCerradas(Base):
    """Los caminos que T-82 encontró abiertos, ya cerrados con la integración."""

    def sembrar_con_ids_de_gmail(self) -> None:
        self.fila(*HILO_AISLADO, asunto=ETIQUETA, remitente="prueba.controlada@example.com")
        self.cancelar(autorizacion(HILO_AISLADO))

    # Cubre: T-83
    def test_70_la_importacion_directa_se_rechaza_y_no_escribe_nada(self) -> None:
        self.sembrar_con_ids_de_gmail()
        antes = self.foto()
        with self.assertRaises(MensajeCancelado):
            work_import.dashboard_upsert(b64(json.dumps(payload(thread=ID_AISLADO, message=ID_AISLADO))),
                                         actualizar_hoja=False)
        self.assertEqual(self.foto(), antes)
        self.assertEqual([r["thread_id"] for r in quotes.all_quotes()], [])

    # Cubre: T-83
    def test_71_la_importacion_aislada_responde_mensaje_cancelado_y_el_coordinador_no_lo_cuenta_como_fallo(self) -> None:
        self.sembrar_con_ids_de_gmail()
        resultado = importar_aislado(self, GmailAislado({"g1": borrador()}))
        self.assertEqual(resultado["resultado"], "mensaje_cancelado")
        self.assertIn("mensaje_cancelado", coordinacion.IMPORTACIONES_SIN_FALLO)
        self.assertEqual([r["thread_id"] for r in quotes.all_quotes()], [])

    # Cubre: T-83
    def test_72_conciliar_con_work_no_cierra_el_mensaje_de_una_lapida(self) -> None:
        self.sembrar_con_ids_de_gmail()
        with store.transaccion() as db:
            self.assertEqual(recepcion.conciliar_con_work(db, *HILO_AISLADO, "ref"), "cancelado")
            fila = db.execute("SELECT state,disposition FROM recepcion_mensajes WHERE message_id=?", (ID_AISLADO,)).fetchone()
        self.assertEqual(tuple(fila), recepcion.PENDIENTE)

    # Cubre: T-83
    def test_73_un_mensaje_sin_lapida_se_importa_igual_que_antes(self) -> None:
        self.fila(*HILO_AISLADO, asunto="Solicitud de levantamiento", remitente="cliente@example.com")
        resultado = importar_aislado(self, GmailAislado({"g1": borrador()}))
        self.assertEqual(resultado["resultado"], "importado")
        self.assertEqual([r["thread_id"] for r in quotes.all_quotes()], [ID_AISLADO])

    # Cubre: T-83
    def test_74_otro_mensaje_del_mismo_hilo_no_queda_bloqueado_por_la_lapida(self) -> None:
        self.sembrar_con_ids_de_gmail()
        self.fila(ID_AISLADO, "mensaje-posterior", asunto="Respuesta del cliente", remitente="cliente@example.com")
        work_import.dashboard_upsert(b64(json.dumps(payload(thread=ID_AISLADO, message="mensaje-posterior"))),
                                     actualizar_hoja=False)
        self.assertEqual([r["thread_id"] for r in quotes.all_quotes()], [ID_AISLADO])

    # Cubre: T-83
    def test_75_el_despacho_manual_de_una_lapida_responde_cancelado_sin_error(self) -> None:
        self.sembrar_lab()
        self.cancelar()
        resultado = despacho.despachar(self.drive, CARPETA, Alcance(*LAB))
        self.assertEqual(resultado["publicacion"]["resultado"], "cancelado")
        self.assertEqual(len(self.hojas()), 0)

    # Cubre: T-83
    def test_76_el_panel_no_pide_atencion_por_lo_cancelado_pero_lo_cuenta_y_la_clasificacion_lo_marca(self) -> None:
        self.sembrar_pausa()
        self.cancelar()
        panel = estado_operativo.estado_operativo()
        self.assertNotIn(LAB[1], [f["message_id"] for f in panel["atencion"]])
        self.assertIn("cancelado", [t["estado"] for t in panel["trabajos"]])
        marca = {f["message_id"]: f["cancelado"] for f in trabajos_work.clasificar_pendientes()}
        self.assertEqual((marca[LAB[1]], marca[REAL[1]]), (True, False))


class DiarioYRestauracion(Base):
    def restaurar(self, copia: Path) -> None:
        with patch("anka.infra.work_heartbeat.other_bridge_running", return_value=False), \
                patch.object(backup.acciones_en_curso, "acciones_activas", return_value=[]), \
                patch.object(backup, "_base_en_uso", return_value=False):
            backup.restore(copia)

    # Cubre: T-83
    def test_80_cada_cancelacion_deja_una_linea_en_el_diario_fuera_de_sqlite_y_repetirla_no_la_duplica(self) -> None:
        self.sembrar_lab()
        resultado = self.cancelar()
        self.cancelar()
        entradas, rotas = lapidas.leer()
        self.assertEqual((len(entradas), rotas), (1, 0))
        self.assertEqual((entradas[0]["job_id"], entradas[0]["operador"], entradas[0]["etiqueta"]),
                         (resultado["job_id"], "operador.prueba", MARCA))
        self.assertNotIn(lapidas.NOMBRE, backup.EXTRA_FILES, "una copia no debe pisar el diario")

    # Cubre: T-83
    def test_81_restaurar_una_copia_anterior_devuelve_la_lapida_y_el_mensaje_sigue_cancelado(self) -> None:
        self.sembrar(con_real=False)
        copia = backup.create_backup()
        self.cancelar()
        self.restaurar(copia)
        self.assertEqual(self.estado_lab(), "cancelado")
        self.assertEqual(trabajos_work.mensajes_nuevos(self.plan_.desde), [])
        self.assertEqual(self.eventos_de(lapidas.EVENTO_DE_REAPLICACION), 1)
        self.ciclos(4)
        self.assertEqual((self.estado_lab(), len(self.hojas())), ("cancelado", 0))

    # Cubre: T-83
    def test_82_reaplicar_es_idempotente_y_verificar_lapidas_queda_en_orden(self) -> None:
        self.sembrar(con_real=False)
        copia = backup.create_backup()
        self.cancelar()
        self.restaurar(copia)
        self.restaurar(copia)
        self.assertEqual(self.eventos_de(lapidas.EVENTO_DE_REAPLICACION), 1, "la base restaurada es nueva cada vez")
        self.assertEqual(self.trabajos_en_total(), 1)
        informe = cancelaciones.verificar_lapidas()
        self.assertTrue(informe["en_orden_total"], informe)

    # Cubre: T-83
    def test_83_la_verificacion_detecta_una_lapida_que_falta(self) -> None:
        self.sembrar_lab()
        self.cancelar()
        with store.transaccion() as db:
            db.execute("DELETE FROM trabajos_work")
        informe = cancelaciones.verificar_lapidas()
        self.assertFalse(informe["en_orden_total"])
        self.assertEqual(len(informe["faltan"]), 1)

    # Cubre: T-83
    def test_84_un_choque_con_un_trabajo_avanzado_se_informa_y_no_se_modifica(self) -> None:
        self.sembrar(con_real=False)
        copia = backup.create_backup()
        despacho.despachar(self.drive, CARPETA, Alcance(*LAB))                    # la copia de ahora tiene el trabajo publicado
        lapidas.registrar({"job_id": self.trabajo_de(LAB[1], LAB[0])["job_id"], "account": recepcion.CUENTA,
                           "thread_id": LAB[0], "message_id": LAB[1], "job_type": "analizar_solicitud",
                           "contract_version": "ANKA_DASHBOARD_JSON_V1", "operador": "x", "motivo": "x",
                           "cancelado_at": "2026-10-17T10:00:00-05:00"})
        self.assertEqual(self.estado_lab(), "publicado")
        informe = cancelaciones.verificar_lapidas()
        self.assertEqual([c["estado"] for c in informe["chocan"]], ["publicado"])
        del copia

    # Cubre: T-83
    def test_85_una_linea_rota_del_diario_no_tapa_a_las_demas(self) -> None:
        self.sembrar_lab()
        self.cancelar()
        with lapidas.ruta().open("a", encoding="utf-8") as archivo:
            archivo.write("{esto no es json\n")
        entradas, rotas = lapidas.leer()
        self.assertEqual((len(entradas), rotas), (1, 1))
        self.assertFalse(cancelaciones.verificar_lapidas()["en_orden_total"])

    # Cubre: T-83
    def test_86_si_el_diario_falla_la_cancelacion_se_revierte_entera(self) -> None:
        self.sembrar_lab()
        antes = self.foto()
        with patch.object(lapidas, "registrar", side_effect=OSError("disco lleno")), self.assertRaises(OSError):
            self.cancelar()
        self.assertEqual(self.foto(), antes)
        self.assertEqual(self.cancelar()["resultado"], "cancelado")

    # Cubre: T-83
    def test_87_una_lapida_sin_entrada_en_el_diario_se_completa_al_repetir_la_orden(self) -> None:
        self.sembrar_lab()
        self.cancelar()
        lapidas.ruta().unlink()
        self.assertEqual(self.cancelar()["resultado"], "ya_cancelado")
        self.assertEqual(len(lapidas.leer()[0]), 1)

    # Cubre: T-83
    def test_88_una_copia_sin_tabla_de_trabajos_se_restaura_sin_migrarla_y_la_reaplicacion_se_omite(self) -> None:
        import sqlite3

        self.sembrar_lab()
        self.cancelar()
        vieja = self.datos / "vieja.sqlite3"
        with contextlib.closing(sqlite3.connect(vieja)) as conexion:
            conexion.execute("CREATE TABLE quotes(thread_id TEXT)")
            conexion.commit()
        informe = lapidas.reaplicar_en_archivo(vieja, "2026-10-17T10:00:00-05:00")
        self.assertEqual(informe["omitido"], "sin_tabla_de_trabajos")

    # Cubre: T-83
    def test_89_reaplicar_lapidas_por_consola_exige_confirmar_y_repara_lo_que_falta(self) -> None:
        self.sembrar_lab()
        self.cancelar()
        with store.transaccion() as db:
            db.execute("DELETE FROM trabajos_work")
        with contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(work_jobs.main(["reaplicar-lapidas"], lambda texto: None), 2)
        salida: list[str] = []
        self.assertEqual(work_jobs.main(["reaplicar-lapidas", "--confirmar"], salida.append), 0)
        self.assertEqual(self.estado_lab(), "cancelado")
        self.assertEqual(len(json.loads(salida[0])["reaplicadas"]), 1)

    def eventos_de(self, tipo: str) -> int:
        with store.lectura() as db:
            return int(db.execute("SELECT COUNT(*) FROM recepcion_eventos WHERE event_kind=?", (tipo,)).fetchone()[0])


class OrdenDeLaConsola(Base):
    # Cubre: T-83
    def test_90_la_orden_cancelar_exige_confirmar_y_deja_la_lapida(self) -> None:
        self.sembrar_lab()
        base = ["cancelar", "--thread-id", LAB[0], "--message-id", LAB[1], "--etiqueta", MARCA, "--operador", "op",
                "--motivo", "prueba cancelada"]
        with contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(work_jobs.main(base, lambda texto: None), 2)
        self.assertEqual(self.trabajos_en_total(), 0)
        salida: list[str] = []
        self.assertEqual(work_jobs.main([*base, "--confirmar"], salida.append), 0)
        self.assertEqual(json.loads(salida[0])["resultado"], "cancelado")
        self.assertEqual(self.estado_lab(), "cancelado")

    # Cubre: T-83
    def test_91_un_rechazo_sale_con_codigo_2_y_sin_escribir(self) -> None:
        self.sembrar_pausa()
        antes = self.foto()
        with contextlib.redirect_stderr(io.StringIO()):
            codigo = work_jobs.main(["cancelar", "--thread-id", REAL[0], "--message-id", REAL[1], "--etiqueta", MARCA,
                                     "--operador", "op", "--motivo", "m", "--confirmar"], lambda texto: None)
        self.assertEqual(codigo, 2)
        self.assertEqual(self.foto(), antes)

    # Cubre: T-83
    def test_92_verificar_lapidas_es_de_solo_lectura(self) -> None:
        self.sembrar_lab()
        self.cancelar()
        antes = self.foto()
        salida: list[str] = []
        self.assertEqual(work_jobs.main(["verificar-lapidas"], salida.append), 0)
        self.assertTrue(json.loads(salida[0])["en_orden_total"])
        self.assertEqual(self.foto(), antes)


def importar_aislado(caso: Base, gmail: GmailAislado) -> dict:
    from anka.cli import work_sync

    return work_sync.importar_aislado(gmail, lambda raw: work_import.dashboard_upsert(raw, actualizar_hoja=False),
                                      Path(caso.datos) / "manifiestos", ID_AISLADO, ID_AISLADO, {recepcion.CUENTA})


if __name__ == "__main__":
    unittest.main()
