"""Simulación local del aislamiento de una prueba ficticia frente a la coordinación automática (T-79).

SQLite temporal, Drive simulado y mensajes ficticios (los de `tests.test_coordinacion`): nada toca Google, la configuración
operativa ni los datos reales. Demuestra con el código ACTUAL qué aislamiento es posible sin cambiarlo.

Cubre: T-79
"""

from tests import _isolate  # noqa: F401  (debe ir primero)

import inspect
import unittest
from dataclasses import replace

from anka.cli import supervisor, work_coordinacion
from anka.dominio import coordinacion_work
from anka.dominio.errores import ErrorDeAnka
from anka.dominio.coordinacion_work import plan_desde_config, problemas_de_configuracion
from anka.infra import store
from anka.infra.instancia_unica import InstanciaUnica, OtraInstanciaActiva
from anka.servicios import despacho, trabajos_work
from anka.servicios.despacho import Alcance
from tests.test_coordinacion import CARPETA, ConfiguracionTests
from tests.test_coordinacion import Base as BaseDeCoordinacion

ETIQUETA = "[PI-T79-PRUEBA-01] C1"
REAL = ("T1", "m1")          # una solicitud real ficticia, elegible, pendiente y detectada después del corte
LAB = ("T9", "m9")           # el correo de la prueba


class Base(BaseDeCoordinacion):
    def sembrar(self, con_real: bool = True):
        if con_real:
            self.fila(*REAL, asunto="Solicitud de levantamiento", remitente="cliente@example.com")
        self.fila(*LAB, asunto=ETIQUETA, remitente="prueba.controlada@example.com")

    def trabajos_en_total(self) -> int:
        with store.lectura() as db:
            return int(db.execute("SELECT COUNT(*) FROM trabajos_work").fetchone()[0])

    def estado_lab(self):
        return self.estado_de(LAB[1], LAB[0])


class InterferenciaTest(Base):
    # Cubre: T-79
    def test_01_con_la_coordinacion_activa_sin_alcance_el_correo_ficticio_se_publica_solo(self) -> None:
        self.fila(*LAB, asunto=ETIQUETA, remitente="prueba.controlada@example.com")
        informe = self.ciclo()
        self.assertEqual(self.estado_lab(), "publicado", "no hay paso intermedio que pida autorización")
        self.assertEqual(len(informe["publicaciones"]), 1)

    # Cubre: T-79
    def test_02_una_etiqueta_en_el_asunto_no_aisla_porque_un_remitente_humano_siempre_es_elegible(self) -> None:
        self.sembrar(con_real=False)
        with store.lectura() as db:
            fila = db.execute("SELECT * FROM recepcion_mensajes WHERE message_id=?", (LAB[1],)).fetchone()
            self.assertTrue(trabajos_work.veredicto_del_mensaje(db, fila).elegible)
        self.fila("T8", "m8", asunto=ETIQUETA, remitente="noreply@example.com")
        with store.lectura() as db:
            fila = db.execute("SELECT * FROM recepcion_mensajes WHERE message_id='m8'").fetchone()
            self.assertFalse(trabajos_work.veredicto_del_mensaje(db, fila).elegible,
                             "solo un remitente automático quedaría fuera, y desde Gmail no se puede enviar como tal")

    # Cubre: T-79
    def test_03_el_ciclo_sin_alcance_atiende_primero_a_los_reales_y_el_orden_no_es_controlable(self) -> None:
        self.sembrar()
        self.ciclo()                                             # un trabajo por ciclo
        self.assertEqual((self.estado_de(REAL[1]), self.estado_lab()), ("publicado", None))
        self.ciclo()
        self.assertEqual(self.estado_lab(), "publicado", "el segundo ciclo toma el correo ficticio sin avisar")


class PausaTest(Base):
    # Cubre: T-79
    def test_10_con_la_coordinacion_apagada_no_hay_plan_ni_ciclo(self) -> None:
        apagada = {**ConfiguracionTests.ACTIVA, "work_coordination_enabled": False}
        self.assertIsNone(plan_desde_config(apagada))
        self.assertIsNone(work_coordinacion.plan_de(apagada))
        coordinador = supervisor.CoordinadorWork()
        coordinador.check(False, 300)
        self.assertIsNone(coordinador.thread, "ni siquiera arranca el hilo del ciclo")

    # Cubre: T-79
    def test_11_apagar_la_coordinacion_no_arranca_el_puente_general(self) -> None:
        config = {**ConfiguracionTests.ACTIVA, "work_coordination_enabled": False}
        self.assertFalse(coordinacion_work.puente_general_habilitado(config))
        self.assertFalse(supervisor.puente_general_corre(True, False, coordinacion_work.puente_general_habilitado(config)))

    # Cubre: T-79
    def test_12_el_receptor_depende_solo_de_su_propia_clave(self) -> None:
        codigo = inspect.getsource(supervisor.servicio)
        self.assertIn('reception_enabled = in_work_mode and bool(config.get("gmail_reception_enabled", False))', codigo)
        self.assertIn("receptor.check(reception_enabled, reception_interval)", codigo)
        self.assertIn("coordinador.check(coordination_enabled, coordination_interval)", codigo)
        antes = codigo.index("receptor.check(")
        self.assertNotIn("coordination_enabled", codigo[antes:antes + 60], "el receptor no consulta la coordinación")

    # Cubre: T-79
    def test_13_en_pausa_se_puede_despachar_exactamente_el_mensaje_ficticio_sin_el_despacho_automatico(self) -> None:
        self.sembrar()
        self.assertIsNone(despacho.alcance_automatico(self.config_sin_despacho()))
        resultado = despacho.despachar(self.drive, CARPETA, Alcance(*LAB))
        self.assertEqual(resultado["publicacion"]["resultado"], "creado")
        self.assertEqual((self.estado_lab(), self.estado_de(REAL[1])), ("publicado", None))
        self.assertEqual((self.trabajos_en_total(), len(self.hojas())), (1, 1))
        otra = despacho.despachar(self.drive, CARPETA, Alcance(*LAB))                     # repetir no duplica
        self.assertEqual((self.trabajos_en_total(), len(self.hojas())), (1, 1), otra["publicacion"])

    def config_sin_despacho(self) -> dict:
        return {**ConfiguracionTests.ACTIVA}

    # Cubre: T-79
    def test_14_el_despacho_manual_exige_un_mensaje_concreto(self) -> None:
        self.sembrar()
        for alcance in (Alcance("", ""), Alcance(LAB[0], "")):
            with self.assertRaises(ErrorDeAnka):
                despacho.despachar(self.drive, CARPETA, alcance)
        self.assertEqual(self.trabajos_en_total(), 0)

    # Cubre: T-79
    def test_15_los_ids_reales_se_recuperan_en_solo_lectura_por_la_etiqueta(self) -> None:
        self.sembrar()
        with store.lectura() as db:
            filas = db.execute("SELECT thread_id,message_id FROM recepcion_mensajes WHERE subject=?", (ETIQUETA,)).fetchall()
        self.assertEqual([tuple(f) for f in filas], [LAB])


class CandadoTest(Base):
    # Cubre: T-79
    def test_20_quien_tiene_el_candado_detiene_los_ciclos_sin_tocar_la_configuracion(self) -> None:
        self.sembrar(con_real=False)
        with InstanciaUnica(self.datos / "coordinacion.lock", "laboratorio T-79"):
            informe = self.ciclo()
            self.assertEqual(informe, {"resultado": "concurrente"})
            self.assertEqual((self.trabajos_en_total(), self.estado_lab()), (0, None))
            with self.assertRaises(OtraInstanciaActiva):                                 # y se ve quién lo tiene
                InstanciaUnica(self.datos / "coordinacion.lock", "otro").__enter__()
        self.ciclo()
        self.assertEqual(self.estado_lab(), "publicado", "al soltarlo la coordinación sigue sola: falla hacia lo abierto")

    # Cubre: T-79
    def test_21_el_supervisor_trata_concurrente_como_resultado_normal(self) -> None:
        codigo = inspect.getsource(supervisor.CoordinadorWork._ronda)
        self.assertIn('("ok", "concurrente")', codigo)


class AlcanceDeLaboratorioTest(Base):
    # Cubre: T-79
    def test_30_con_ids_en_el_plan_el_ciclo_solo_toca_el_mensaje_ficticio(self) -> None:
        self.sembrar()
        self.ciclo(thread_id=LAB[0], message_id=LAB[1], max_publicar=5)
        self.assertEqual((self.estado_lab(), self.estado_de(REAL[1])), ("publicado", None))
        self.assertEqual(self.trabajos_en_total(), 1)

    # Cubre: T-79
    def test_31_los_dos_ids_van_juntos_y_con_la_coordinacion_apagada_no_estorban(self) -> None:
        solo_hilo = {**ConfiguracionTests.ACTIVA, "work_coordination_thread_id": LAB[0]}
        self.assertTrue(any("van juntos" in p for p in problemas_de_configuracion(solo_hilo)))
        apagada = {**ConfiguracionTests.ACTIVA, "work_coordination_enabled": False}
        self.assertIsNone(plan_desde_config(apagada))
        completa = {**ConfiguracionTests.ACTIVA, "work_coordination_thread_id": LAB[0], "work_coordination_message_id": LAB[1]}
        plan = plan_desde_config(completa)
        assert plan is not None
        self.assertTrue(plan.de_laboratorio)


class RestauracionTest(Base):
    # Cubre: T-79
    def test_40_flujo_completo_pausa_despacho_importacion_y_restauracion_sin_duplicados(self) -> None:
        self.sembrar()
        despacho.despachar(self.drive, CARPETA, Alcance(*LAB))                          # coordinación en pausa
        self.assertEqual((self.estado_lab(), self.estado_de(REAL[1])), ("publicado", None))
        self.work_responde(LAB[1])
        self.ciclo(thread_id=LAB[0], message_id=LAB[1])                                 # alcance de laboratorio
        self.assertEqual(self.estado_lab(), "conciliado")
        self.assertEqual(self.estado_de(REAL[1]), None, "el real sigue intacto y pendiente")
        self.ciclo(thread_id=LAB[0], message_id=LAB[1])                                 # archivado del ficticio
        self.assertEqual(self.drive.movimientos[-1][0], self.trabajo_de(LAB[1], LAB[0])["sheet_file_id"])
        # restauración: plan completo
        self.ciclo()
        self.assertEqual(self.estado_de(REAL[1]), "publicado", "la solicitud real se procesa después, sin pérdida")
        antes = (self.trabajos_en_total(), len(self.hojas()), len(self.drive.movimientos))
        for _ in range(3):
            self.ciclo()
        self.assertEqual((self.trabajos_en_total(), len(self.hojas()), len(self.drive.movimientos)), antes)
        self.assertEqual(antes[0], 2)
        self.assertEqual(self.estado_lab(), "conciliado")

    # Cubre: T-79
    def test_41_un_mensaje_ficticio_sin_trabajo_se_procesaria_al_restaurar(self) -> None:
        """Riesgo de la restauración: si la prueba se aborta antes de crear el trabajo, el ficticio sigue pendiente y elegible."""
        self.sembrar(con_real=False)
        self.assertEqual(len(trabajos_work.mensajes_nuevos(self.plan_.desde)), 1)
        self.ciclo()
        self.assertEqual(self.estado_lab(), "publicado")

    @property
    def plan_(self):
        from tests.test_coordinacion import plan

        return replace(plan())

    # Cubre: T-79
    def test_42_lo_que_llega_durante_la_pausa_no_se_pierde(self) -> None:
        self.sembrar()
        self.fila("T5", "m5", asunto="Otra solicitud de levantamiento", remitente="otro@example.com")      # llega en la pausa
        despacho.despachar(self.drive, CARPETA, Alcance(*LAB))
        pendientes = {m["message_id"] for m in trabajos_work.mensajes_nuevos(self.plan_.desde)}
        self.assertEqual(pendientes, {REAL[1], "m5"})
        for _ in range(3):
            self.ciclo()
        self.assertEqual({self.estado_de(m, h) for h, m in (REAL, ("T5", "m5"), LAB)}, {"publicado"})
        self.assertEqual(self.trabajos_en_total(), 3)


if __name__ == "__main__":
    unittest.main()
