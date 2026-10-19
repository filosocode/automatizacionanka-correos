"""Coordinación automática Python–Work: ciclo de archivar, importar y despachar con límites y recuperación (T-55).

SQLite temporal, Drive y Gmail simulados y mensajes ficticios; nada toca Google ni datos comerciales reales.

Cubre: T-55
"""

from tests import _isolate  # noqa: F401  (debe ir primero)

import contextlib
import copy
import inspect
import io
import json
import threading
import unittest
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

from anka.cli import supervisor, work_coordinacion, work_jobs
from anka.cli import work_sync as sync
from anka.dominio import coordinacion_work
from anka.dominio.errores import ErrorDeAnka
from anka.dominio.coordinacion_work import Plan, plan_desde_config, problemas_de_configuracion, retroceso
from anka.infra import settings, store
from anka.infra.instancia_unica import InstanciaUnica
from anka.servicios import coordinacion, despacho, trabajos_work, work_import
from anka.servicios.coordinacion import Coordinador
from anka.servicios.recepcion import CUENTA
from tests.test_despacho import Base as BaseDeDespacho
from tests.test_importacion_aislada import GmailAislado, borrador
from tests.test_settings import VALID
from tests.test_trabajos_work import CARPETA, ErrorHttp
from tests.test_work_sync import b64, payload

BOGOTA = timezone(timedelta(hours=-5))
CORTE = datetime(2026, 10, 17, 0, 0, tzinfo=BOGOTA)           # los mensajes de prueba se detectan el 17 a las 10:00


def plan(**cambios):
    base = Plan(raiz=CARPETA, desde=CORTE, max_publicar=1, max_importar=1, max_archivar=2)
    return replace(base, **cambios)


class Base(BaseDeDespacho):
    def setUp(self):
        super().setUp()
        self.t = 1000.0
        self.borradores: dict[str, list[str]] = {}
        self.llamadas: list[tuple[str, str]] = []
        self.coordinador = self.nuevo()

    def reloj(self):
        return self.t

    def nuevo(self, importar=None):
        return Coordinador(self.drive, importar or self.importar_simulado, self.datos / "coordinacion.lock", self.reloj)

    def importar_simulado(self, hilo, mensaje):
        """Lo que haría `importar_aislado` según los borradores de Work que hay para el mensaje."""
        self.llamadas.append((hilo, mensaje))
        lista = self.borradores.get(mensaje, [])
        if not lista:
            return {"resultado": "sin_borrador"}
        if len(lista) > 1:
            return {"resultado": "ambiguo"}
        if lista[0] == "invalido":
            return {"resultado": "rechazado"}
        work_import.dashboard_upsert(b64(json.dumps(payload(thread=hilo, message=mensaje))), actualizar_hoja=False)
        return {"resultado": "importado"}

    def work_responde(self, mensaje, *borradores):
        self.borradores[mensaje] = list(borradores or ["valido"])

    def ciclo(self, **cambios):
        return self.coordinador.ciclo(plan(**cambios))

    def trabajo_de(self, mensaje, hilo="T1"):
        encontrados = trabajos_work.trabajos_del_mensaje(hilo, mensaje)
        return encontrados[0] if encontrados else None

    def estado_de(self, mensaje, hilo="T1"):
        trabajo = self.trabajo_de(mensaje, hilo)
        return trabajo["state"] if trabajo else None

    def pasar_a_antes_del_corte(self, mensaje):
        with store.transaccion() as db:
            db.execute("UPDATE recepcion_mensajes SET detected_at='2026-10-01T10:00:00-05:00' WHERE message_id=?",
                       (mensaje,))


class ConfiguracionTests(unittest.TestCase):
    ACTIVA = {**copy.deepcopy(VALID), "active_mode": "chatgpt_work", "work_coordination_enabled": True,
              "work_coordination_folder_id": "raiz", "work_coordination_since": "2026-10-18T20:00:00-05:00"}

    def test_apagada_por_defecto_y_el_ejemplo_la_trae_apagada(self):
        self.assertIsNone(plan_desde_config(VALID))
        ejemplo = json.loads((Path(__file__).resolve().parents[1] / "config.example.json").read_text(encoding="utf-8"))
        self.assertIs(ejemplo["work_coordination_enabled"], False)
        self.assertEqual(settings.unknown_config_keys(ejemplo), [])
        self.assertEqual(problemas_de_configuracion(ejemplo), [])

    def test_activa_y_completa_produce_un_plan_con_limites_conservadores(self):
        plan_ = plan_desde_config(self.ACTIVA)
        self.assertEqual((plan_.max_publicar, plan_.max_importar, plan_.max_archivar, plan_.intervalo), (1, 1, 2, 300))
        self.assertEqual(plan_.desde, datetime(2026, 10, 18, 20, 0, tzinfo=BOGOTA))
        self.assertFalse(plan_.de_laboratorio)
        self.assertEqual(settings.validate_config(self.ACTIVA), [])

    def test_una_configuracion_incompleta_o_fuera_de_rango_se_rechaza(self):
        casos = {
            "work_coordination_folder_id": "", "work_coordination_since": "",
            "work_coordination_since ": "2026-10-18T20:00:00",            # sin zona horaria
            "work_coordination_max_publish": 6, "work_coordination_max_import": 0,
            "work_coordination_max_archive": 11, "work_coordination_interval_seconds": 30,
            "work_coordination_enabled ": "si",
        }
        for clave, valor in casos.items():
            config = {**self.ACTIVA, clave.strip(): valor}
            self.assertTrue(problemas_de_configuracion(config), clave)
            self.assertTrue(settings.validate_config(config), clave)
        with self.assertRaises(ErrorDeAnka):
            plan_desde_config({**self.ACTIVA, "work_coordination_folder_id": ""})

    def test_exige_modo_work_un_solo_despachador_y_los_ids_juntos(self):
        self.assertTrue(problemas_de_configuracion({**self.ACTIVA, "active_mode": "local"}))
        doble = {**self.ACTIVA, "work_dispatch_enabled": True}
        self.assertTrue(any("dos despachadores" in p for p in problemas_de_configuracion(doble)))
        self.assertTrue(problemas_de_configuracion({**self.ACTIVA, "work_coordination_thread_id": "T1"}))
        laboratorio = plan_desde_config({**self.ACTIVA, "work_coordination_thread_id": "T1",
                                         "work_coordination_message_id": "m1"})
        self.assertTrue(laboratorio.de_laboratorio)

    def test_el_retroceso_se_duplica_y_tiene_tope(self):
        self.assertEqual([retroceso(n) for n in (1, 2, 3, 4)], [60, 120, 240, 480])
        self.assertEqual(retroceso(99), 3600)


class DespachoTests(Base):
    def test_un_mensaje_elegible_nuevo_se_publica_y_repetir_no_duplica(self):
        self.fila("T1", "m1")
        primero = self.ciclo()
        self.assertEqual(primero["publicaciones"][0]["resultado"], "creado")
        for _ in range(3):
            self.ciclo()
        self.assertEqual((len(self.hojas()), self.trabajos(), self.estado_de("m1")), (1, 1, "publicado"))
        hoja, = self.hojas()
        self.assertEqual(self.padres(hoja), self.drive.id_de("PENDIENTES"))

    def test_lo_anterior_al_corte_el_ruido_y_la_duda_no_generan_trabajo(self):
        self.fila("T0", "viejo")
        self.pasar_a_antes_del_corte("viejo")
        self.fila("T2", "ruido", remitente="noreply@tm.openai.com", asunto="Tu tarea terminó")
        self.fila("T3", "duda", remitente="no-reply@portal.com", asunto="Su cotización fue recibida")
        self.ciclo(max_publicar=5)
        self.assertEqual((self.trabajos(), self.hojas()), (0, []))

    def test_con_varios_mensajes_y_limite_uno_publica_uno_por_ciclo(self):
        for numero in (1, 2, 3):
            self.fila(f"T{numero}", f"m{numero}")
        publicados = []
        for _ in range(3):
            publicados.append(len(self.ciclo()["publicaciones"]))
            self.t += 1
        self.assertEqual(publicados, [1, 1, 1])
        self.assertEqual((self.trabajos(), len(self.hojas())), (3, 3))
        self.assertEqual(self.ciclo()["publicaciones"], [])

    def test_el_limite_del_ciclo_se_respeta_aunque_haya_mas(self):
        for numero in (1, 2, 3):
            self.fila(f"T{numero}", f"m{numero}")
        self.assertEqual(len(self.ciclo(max_publicar=2)["publicaciones"]), 2)
        self.assertEqual(self.trabajos(), 2)

    def test_alcance_de_laboratorio_solo_toca_el_mensaje_nombrado(self):
        self.fila("T1", "m1")
        self.fila("T2", "m2")
        self.ciclo(thread_id="T2", message_id="m2", max_publicar=5)
        self.assertEqual((self.estado_de("m1"), self.estado_de("m2", "T2")), (None, "publicado"))

    def test_un_fallo_de_drive_al_publicar_se_anota_y_el_ciclo_siguiente_lo_recupera(self):
        self.fila("T1", "m1")
        self.drive.fallar_listado = ErrorHttp(500)
        primero = self.ciclo()
        self.assertEqual(self.estado_de("m1"), "pendiente_de_publicar")
        self.assertTrue(primero["errores"])
        self.drive.fallar_listado = None
        segundo = self.ciclo()
        self.assertEqual((self.estado_de("m1"), self.trabajos(), len(self.hojas())), ("publicado", 1, 1))
        self.assertEqual(segundo["publicaciones"][0]["job_id"], self.trabajo_de("m1")["job_id"])

    def test_tras_cinco_intentos_fallidos_el_trabajo_deja_de_reintentarse(self):
        self.fila("T1", "m1")
        self.drive.fallar_listado = ErrorHttp(500)
        for _ in range(despacho.MAXIMO_DE_INTENTOS + 2):
            self.ciclo()
        self.assertEqual(self.trabajo_de("m1")["attempts"], despacho.MAXIMO_DE_INTENTOS)
        self.drive.fallar_listado = None
        self.assertEqual(self.ciclo()["publicaciones"], [])
        self.assertEqual(self.hojas(), [])

    def test_un_reclamo_vencido_se_retoma_y_uno_vigente_no_cuenta(self):
        trabajo = self.trabajo("T1", "m1")
        with store.transaccion() as db:
            db.execute("UPDATE trabajos_work SET state='publicando',claim_token='x',claim_until=? WHERE job_id=?",
                       (self.t + 500, trabajo["job_id"]))
        self.assertEqual(self.ciclo()["publicaciones"], [])
        self.t += 1000
        self.assertEqual(len(self.ciclo()["publicaciones"]), 1)
        self.assertEqual(self.estado_de("m1"), "publicado")


class CicloCompletoTests(Base):
    def test_publicar_importar_y_archivar_sin_duplicar_y_con_un_coordinador_nuevo_a_mitad(self):
        self.fila("T1", "m1")
        self.ciclo()
        self.assertEqual(self.llamadas, [])                                     # recién publicado: aún no se consulta
        self.ciclo()
        self.assertEqual(self.estado_de("m1"), "publicado")                     # sin borrador no es fallo
        self.assertEqual(self.coordinador.fallos, {})
        self.work_responde("m1")
        self.coordinador = self.nuevo()                                         # reinicio: todo sale de SQLite
        importado = self.ciclo()
        self.assertEqual(importado["importaciones"], [{"job_id": self.trabajo_de("m1")["job_id"],
                                                        "resultado": "importado"}])
        self.assertEqual(self.estado_de("m1"), "conciliado")
        archivado = self.ciclo()
        self.assertEqual(archivado["archivados"][0]["resultado"], "archivado")
        hoja, = self.hojas()
        self.assertEqual(self.padres(hoja), self.drive.id_de("CERRADOS"))
        llamadas = len(self.llamadas)
        for _ in range(3):                                                      # estado final estable
            otro = self.ciclo()
            self.assertEqual([otro["importaciones"], otro["publicaciones"]], [[], []])
            self.assertEqual([a["resultado"] for a in otro["archivados"]], [])
        self.assertEqual((len(self.llamadas), len(self.hojas()), self.trabajos()), (llamadas, 1, 1))
        self.assertEqual(len(self.eventos("trabajo_archivado")), 1)
        self.assertEqual(self.estado_de("m1"), "conciliado")

    def test_un_trabajo_conciliado_no_se_vuelve_a_importar_ni_a_publicar(self):
        self.fila("T1", "m1")
        self.ciclo()
        self.work_responde("m1")
        self.ciclo()
        self.ciclo()
        creados = self.drive.creados
        self.work_responde("m1", "valido", "valido")                            # aunque Work dejara otro borrador
        for _ in range(3):
            self.ciclo()
        self.assertEqual((self.drive.creados, self.estado_de("m1"), len(self.llamadas)), (creados, "conciliado", 1))

    def test_las_importaciones_rotan_entre_los_trabajos_publicados(self):
        for numero in (1, 2, 3):
            self.fila(f"T{numero}", f"m{numero}")
        self.ciclo(max_publicar=5)
        for _ in range(3):
            self.ciclo()
        self.assertEqual(sorted(m for _, m in self.llamadas), ["m1", "m2", "m3"])

    def test_importado_sin_conciliar_no_se_da_por_hecho(self):
        self.fila("T1", "m1")
        self.ciclo()
        coordinador = self.nuevo(lambda hilo, mensaje: {"resultado": "importado"})
        informe = coordinador.ciclo(plan())
        self.assertEqual(informe["importaciones"][0]["resultado"], "importado_sin_conciliar")
        self.assertEqual(coordinador.fallos[self.trabajo_de("m1")["job_id"]], 1)

    def test_un_fallo_al_archivar_se_reintenta_en_el_ciclo_siguiente(self):
        self.fila("T1", "m1")
        self.ciclo()
        self.work_responde("m1")
        self.ciclo()
        with patch.object(despacho, "archivar_trabajo", side_effect=ErrorHttp(503)):
            fallido = self.ciclo()
        self.assertTrue(fallido["errores"])
        self.assertEqual(self.padres(self.hojas()[0]), self.drive.id_de("PENDIENTES"))
        self.ciclo()
        self.assertEqual(self.padres(self.hojas()[0]), self.drive.id_de("CERRADOS"))
        self.assertEqual(len(self.eventos("trabajo_archivado")), 1)

    def test_un_carpeta_duplicada_detiene_las_fases_de_drive_pero_no_la_importacion(self):
        self.fila("T1", "m1")
        self.ciclo()
        self.drive.sembrar_carpeta("PENDIENTES", CARPETA)                       # ahora hay dos
        self.work_responde("m1")
        informe = self.ciclo()
        self.assertIn("carpeta_ambigua", informe["errores"])
        self.assertEqual(self.estado_de("m1"), "conciliado")                    # importar no necesita Drive


class ImportacionConProblemasTests(Base):
    def setUp(self):
        super().setUp()
        self.fila("T1", "m1")
        self.ciclo()
        self.job = self.trabajo_de("m1")["job_id"]

    def avanzar(self, veces=1):
        informes = []
        for _ in range(veces):
            self.t += 4000                                                      # más que cualquier retroceso
            informes.append(self.ciclo())
        return informes

    def test_dos_borradores_validos_no_se_importan_y_el_trabajo_queda_para_una_persona(self):
        self.work_responde("m1", "valido", "valido")
        informes = self.avanzar(coordinacion_work.MAXIMO_DE_FALLOS + 2)
        self.assertEqual(self.estado_de("m1"), "publicado")
        self.assertEqual(len([i for i in informes if i["importaciones"]]), coordinacion_work.MAXIMO_DE_FALLOS)
        primero, = self.eventos("coordinacion_importacion", self.job)
        self.assertEqual(json.loads(primero["detail_json"])["resultado"], "ambiguo")
        parada, = self.eventos("coordinacion_requiere_persona", self.job)
        self.assertEqual(json.loads(parada["detail_json"])["fallos"], coordinacion_work.MAXIMO_DE_FALLOS)
        self.assertIn(self.job, informes[-1]["requieren_persona"])
        self.assertEqual(len(self.llamadas), coordinacion_work.MAXIMO_DE_FALLOS)
        self.assertEqual(len(self.hojas()), 1)

    def test_el_retroceso_espera_entre_intentos_del_mismo_trabajo(self):
        self.work_responde("m1", "valido", "valido")
        self.ciclo()
        self.assertEqual(len(self.llamadas), 1)
        self.t += 30
        self.assertEqual(self.ciclo()["importaciones"], [])                      # aún dentro de la espera
        self.t += 60
        self.assertEqual(len(self.ciclo()["importaciones"]), 1)

    def test_un_borrador_invalido_se_rechaza_y_el_trabajo_sigue_publicado(self):
        self.work_responde("m1", "invalido")
        informe = self.avanzar()[0]
        self.assertEqual(informe["importaciones"][0]["resultado"], "rechazado")
        self.assertEqual(self.estado_de("m1"), "publicado")
        self.assertEqual(len(self.eventos("coordinacion_importacion", self.job)), 1)
        with store.lectura() as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM quotes").fetchone()[0], 0)

    def test_si_el_borrador_se_corrige_el_trabajo_se_importa_despues_de_un_fallo(self):
        self.work_responde("m1", "invalido")
        self.avanzar()
        self.work_responde("m1")
        self.avanzar(2)
        self.assertEqual(self.estado_de("m1"), "conciliado")
        self.assertEqual(self.coordinador.fallos, {})

    def test_una_excepcion_del_importador_cuenta_como_fallo_y_no_detiene_el_ciclo(self):
        def explota(hilo, mensaje):
            raise ConnectionError("red caída")
        coordinador = self.nuevo(explota)
        informe = coordinador.ciclo(plan())
        self.assertEqual(informe["importaciones"][0]["resultado"], "fallo_de_importacion")
        self.assertEqual(self.estado_de("m1"), "publicado")
        self.assertNotIn("red caída", json.dumps(informe))

    def test_un_reinicio_vuelve_a_intentar_un_trabajo_detenido_sin_efectos_dobles(self):
        self.work_responde("m1", "valido", "valido")
        self.avanzar(coordinacion_work.MAXIMO_DE_FALLOS)
        self.assertIn(self.job, self.coordinador.detenidos)
        self.work_responde("m1")                                                # la persona dejó un solo borrador
        self.coordinador = self.nuevo()
        self.avanzar(2)
        self.assertEqual((self.estado_de("m1"), len(self.hojas())), ("conciliado", 1))


class ConcurrenciaTests(Base):
    def test_un_segundo_ciclo_a_la_vez_responde_concurrente_sin_hacer_nada(self):
        self.fila("T1", "m1")
        with InstanciaUnica(self.datos / "coordinacion.lock", "otro ciclo"):
            self.assertEqual(self.ciclo(), {"resultado": "concurrente"})
        self.assertEqual((self.trabajos(), self.hojas(), self.llamadas), (0, [], []))
        self.ciclo()
        self.assertEqual(self.trabajos(), 1)

    def test_dos_hilos_a_la_vez_publican_una_sola_hoja(self):
        self.fila("T1", "m1")
        resultados = []

        def correr():
            resultados.append(self.nuevo().ciclo(plan()))

        hilos = [threading.Thread(target=correr) for _ in range(4)]
        for hilo in hilos:
            hilo.start()
        for hilo in hilos:
            hilo.join()
        self.assertEqual((len(self.hojas()), self.trabajos()), (1, 1))
        self.assertTrue(all(r["resultado"] in ("ok", "concurrente") for r in resultados))


class IntegracionConLaImportacionRealTests(Base):
    def test_con_la_importacion_aislada_real_el_ciclo_concilia_y_archiva(self):
        self.fila("T1", "m1")
        gmail = GmailAislado({"dT": borrador(hilo="T1", mensaje="m1")}, mensajes={"m1": {
            "id": "m1", "threadId": "T1", "internalDate": "1700000000000", "labelIds": ["INBOX"],
            "payload": {"mimeType": "text/plain", "headers": [{"name": "From", "value": "Ana <c@example.com>"}]}}})

        def importar(hilo, mensaje):
            return sync.importar_aislado(gmail, lambda raw: work_import.dashboard_upsert(raw, actualizar_hoja=False),
                                         self.datos / "manifiestos", hilo, mensaje, {CUENTA})

        self.coordinador = self.nuevo(importar)
        for _ in range(3):
            self.ciclo()
        self.assertEqual(self.estado_de("m1"), "conciliado")
        self.assertEqual(self.padres(self.hojas()[0]), self.drive.id_de("CERRADOS"))
        self.assertEqual(gmail.draft_gets, ["dT"])


class SeguridadTests(Base):
    def test_el_modulo_no_envia_ni_crea_borradores(self):
        for modulo in (coordinacion, work_coordinacion):
            codigo = inspect.getsource(modulo)
            for prohibido in ("messages().send", "drafts().create", "drafts().update", "drafts().send", ".modify(",
                              "anka.infra.mail", "envios", "smtplib"):
                self.assertNotIn(prohibido, codigo, (modulo.__name__, prohibido))

    def test_desactivada_no_toca_google(self):
        with patch("anka.cli.work_coordinacion.services", side_effect=AssertionError("no debe tocar Google")):
            self.assertEqual(work_coordinacion.ejecutar_un_ciclo(VALID), {"resultado": "desactivado"})
            self.assertEqual(work_coordinacion.ejecutar_un_ciclo(
                {**VALID, "active_mode": "local", "work_coordination_enabled": True}), {"resultado": "desactivado"})

    def test_los_mensajes_historicos_no_se_procesan_aunque_la_cola_sea_larga(self):
        for numero in range(10):
            self.fila(f"H{numero}", f"h{numero}")
            self.pasar_a_antes_del_corte(f"h{numero}")
        self.fila("T1", "m1")
        self.ciclo(max_publicar=5)
        self.assertEqual([t["message_id"] for t in trabajos_work.trabajos_por_estado(["publicado"])], ["m1"])


class OrdenYLanzadorTests(Base):
    def ejecutar(self, *argv):
        salida, error = io.StringIO(), io.StringIO()
        with contextlib.redirect_stderr(error):
            codigo = work_jobs.main(list(argv), salida=lambda texto: salida.write(texto))
        return codigo, salida.getvalue(), error.getvalue()

    def test_la_orden_exige_confirmacion_y_desactivada_no_hace_nada(self):
        with patch("anka.cli.work_coordinacion.services", side_effect=AssertionError("no debe tocar Google")):
            codigo, texto, _ = self.ejecutar("coordinar", "--una-vez")
            self.assertEqual((codigo, json.loads(texto)), (0, {"resultado": "desactivado"}))
            activa = {**VALID, "active_mode": "chatgpt_work", "work_coordination_enabled": True,
                      "work_coordination_folder_id": CARPETA, "work_coordination_since": "2026-10-17T00:00:00-05:00"}
            with patch.object(settings, "load_config", return_value=activa):
                codigo, _, error = self.ejecutar("coordinar", "--una-vez")
                self.assertEqual(codigo, 2)
                self.assertIn("--confirmar", error)
                self.assertEqual(self.ejecutar("coordinar", "--una-vez", "--thread-id", "T1")[0], 2)

    def test_la_orden_con_confirmacion_ejecuta_un_ciclo_de_laboratorio(self):
        self.fila("T1", "m1")
        self.fila("T2", "m2")
        activa = {**VALID, "active_mode": "chatgpt_work", "work_coordination_enabled": True,
                  "work_coordination_folder_id": CARPETA, "work_coordination_since": "2026-10-17T00:00:00-05:00"}
        with patch.object(settings, "load_config", return_value=activa), \
                patch.object(work_coordinacion, "nuevo_coordinador", return_value=self.coordinador):
            codigo, texto, _ = self.ejecutar("coordinar", "--una-vez", "--confirmar",
                                             "--thread-id", "T2", "--message-id", "m2")
        self.assertEqual(codigo, 0)
        self.assertEqual(json.loads(texto)["publicaciones"][0]["resultado"], "creado")
        self.assertEqual((self.estado_de("m1"), self.estado_de("m2", "T2")), (None, "publicado"))

    def test_el_lanzador_solo_arranca_el_hilo_si_esta_activada_y_no_solapa_ciclos(self):
        hilo = supervisor.CoordinadorWork()
        with patch("anka.cli.work_coordinacion.plan_de", return_value=None) as plan_de:
            hilo.check(False, 300)
            self.assertIsNone(hilo.thread)
            hilo.check(True, 300)
            hilo.thread.join(5)
            self.assertEqual(plan_de.call_count, 1)
            hilo.check(True, 300)                                             # aún dentro del intervalo
            self.assertEqual(plan_de.call_count, 1)

    def test_un_error_del_ciclo_no_tumba_el_lanzador_y_reconecta_despues(self):
        hilo = supervisor.CoordinadorWork()
        with patch("anka.cli.work_coordinacion.plan_de", return_value=plan()), \
                patch("anka.cli.work_coordinacion.nuevo_coordinador", side_effect=ConnectionError("x")):
            hilo._ronda()
        self.assertIsNone(hilo.coordinador)
        coordinador = MagicMock()
        coordinador.ciclo.return_value = {"resultado": "ok"}
        with patch("anka.cli.work_coordinacion.plan_de", return_value=plan()), \
                patch("anka.cli.work_coordinacion.nuevo_coordinador", return_value=coordinador):
            hilo._ronda()
            hilo._ronda()
        self.assertEqual(coordinador.ciclo.call_count, 2)


if __name__ == "__main__":
    unittest.main()
