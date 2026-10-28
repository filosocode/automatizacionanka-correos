"""Simulación local de la recuperación de una prueba ficticia cancelada en cada etapa (T-80).

SQLite temporal, Drive y Gmail simulados y mensajes ficticios (los de `tests.test_coordinacion`): nada toca Google, la
configuración operativa ni los datos reales. Caracteriza lo que hace el código ACTUAL; no introduce cambios en él.

Cubre: T-80
"""

from tests import _isolate  # noqa: F401  (debe ir primero)

import inspect
import unittest
from datetime import datetime, timedelta, timezone
from typing import Any

from anka.dominio import trabajo_work
from anka.infra import store
from anka.servicios import coordinacion, despacho, recepcion, trabajos_work
from anka.servicios.despacho import Alcance
from tests.test_aislamiento_t79 import ETIQUETA, LAB, REAL
from tests.test_aislamiento_t79 import Base as BaseDeT79
from tests.test_coordinacion import CARPETA
from tests.test_trabajos_work import ErrorHttp

BOGOTA = timezone(timedelta(hours=-5))
ANTES = ("T0", "m0")           # real llegado en la pausa, ANTES del ficticio
DESPUES = ("T2", "m2")         # real llegado en la pausa, DESPUÉS del ficticio
HORA = {REAL: "10:00:00", ANTES: "10:05:00", LAB: "10:10:00", DESPUES: "10:15:00"}


def instante(hora: str) -> datetime:
    return datetime.fromisoformat(f"2026-10-17T{hora}-05:00")


class Base(BaseDeT79):
    def sembrar_pausa(self) -> None:
        """Una pausa con tres reales y el ficticio, cada uno con su hora de detección."""
        self.fila(*REAL, asunto="Solicitud de levantamiento", remitente="cliente@example.com")
        self.fila(*ANTES, asunto="Solicitud de topografía", remitente="otro@example.com")
        self.fila(*LAB, asunto=ETIQUETA, remitente="prueba.controlada@example.com")
        self.fila(*DESPUES, asunto="Solicitud de batimetría", remitente="tercero@example.com")
        with store.transaccion() as db:
            for (hilo, mensaje), hora in HORA.items():
                db.execute("UPDATE recepcion_mensajes SET detected_at=? WHERE thread_id=? AND message_id=?",
                           (f"2026-10-17T{hora}-05:00", hilo, mensaje))

    def estados(self) -> dict[str, str | None]:
        return {mensaje: self.estado_de(mensaje, hilo) for hilo, mensaje in HORA}

    def ciclos(self, cuantos: int, **cambios: Any) -> None:
        for _ in range(cuantos):
            self.ciclo(**cambios)
            self.t += 400                                    # pasa más que un intervalo y que el retroceso inicial

    def sin_duplicados(self) -> None:
        with store.lectura() as db:
            repetidos = db.execute("SELECT COUNT(*) FROM (SELECT thread_id,message_id,COUNT(*) c FROM trabajos_work "
                                   "GROUP BY thread_id,message_id HAVING c>1)").fetchone()[0]
        self.assertEqual(repetidos, 0)
        marcas = [h["props"].get(trabajo_work.CLAVE_DE_MARCA) for h in self.hojas()]
        self.assertEqual(len(marcas), len(set(marcas)), "una sola hoja viva por trabajo")


class EtapasTest(Base):
    # Cubre: T-80
    def test_e0_cancelar_antes_de_recibir_no_deja_nada_y_restaurar_es_inocuo(self) -> None:
        self.fila(*REAL, asunto="Solicitud de levantamiento", remitente="cliente@example.com")
        self.ciclos(3)
        self.assertEqual((self.estado_de(REAL[1]), self.trabajos_en_total()), ("publicado", 1))
        self.sin_duplicados()

    # Cubre: T-80
    def test_e1_cancelar_tras_recibir_y_antes_del_trabajo_el_ficticio_se_publica_al_restaurar(self) -> None:
        self.sembrar_pausa()
        self.assertEqual(self.estados(), {"m1": None, "m0": None, "m9": None, "m2": None})
        self.ciclos(4)
        self.assertEqual(self.estado_lab(), "publicado", "restaurar sin más publica el ficticio: no hay descarte")

    # Cubre: T-80
    def test_e1_no_existe_orden_de_descarte_ni_cancelacion_en_el_codigo_actual(self) -> None:
        origen = inspect.getsource(trabajos_work) + inspect.getsource(despacho) + inspect.getsource(coordinacion)
        self.assertNotIn("SET state='cancelado'", origen)
        self.assertFalse(hasattr(trabajos_work, "cancelar_trabajo"))
        self.assertFalse(hasattr(trabajos_work, "excluir_mensaje"))
        self.assertFalse(hasattr(recepcion, "descartar"))

    # Cubre: T-80
    def test_e1_continuar_hacia_adelante_publicar_solo_el_ficticio_y_restaurar_conserva_a_los_reales(self) -> None:
        self.sembrar_pausa()
        despacho.despachar(self.drive, CARPETA, Alcance(*LAB))
        self.assertEqual(self.estados(), {"m1": None, "m0": None, "m9": "publicado", "m2": None})
        self.ciclos(6)
        self.assertEqual(set(self.estados().values()), {"publicado"})
        self.assertEqual(self.trabajos_en_total(), 4)
        self.sin_duplicados()

    # Cubre: T-80
    def test_e1_adelantar_el_corte_excluye_al_ficticio_pero_tambien_a_los_reales_anteriores(self) -> None:
        self.sembrar_pausa()
        corte = instante(HORA[LAB]) + timedelta(seconds=1)
        self.ciclos(4, desde=corte, max_publicar=3)
        self.assertEqual(self.estados(), {"m1": None, "m0": None, "m9": None, "m2": "publicado"})
        # los reales anteriores siguen pendientes y elegibles: no se pierden, pero ya no entran solos
        with store.lectura() as db:
            pendientes = {f["message_id"] for f in db.execute(
                "SELECT message_id FROM recepcion_mensajes WHERE state=? AND disposition=?", recepcion_pendiente())}
        self.assertTrue({"m1", "m0", "m9"} <= pendientes)
        self.assertEqual(trabajos_work.mensajes_nuevos(corte), [])
        # y el despacho manual de un mensaje concreto no mira el corte
        despacho.despachar(self.drive, CARPETA, Alcance(*ANTES))
        self.assertEqual(self.estado_de(ANTES[1], ANTES[0]), "publicado")

    # Cubre: T-80
    def test_e1_adelantar_el_corte_no_ayuda_si_el_trabajo_del_ficticio_ya_existe(self) -> None:
        self.sembrar_pausa()
        trabajos_work.crear_trabajo(*LAB)
        self.assertEqual(self.estado_lab(), "pendiente_de_publicar")
        self.ciclos(3, desde=instante(HORA[LAB]) + timedelta(seconds=1), max_publicar=3)
        self.assertEqual(self.estado_lab(), "publicado", "los trabajos pendientes se publican sin mirar el corte")

    # Cubre: T-80
    def test_e2_cancelar_con_trabajo_sin_publicar_el_coordinador_lo_publica_primero(self) -> None:
        self.sembrar_pausa()
        trabajos_work.crear_trabajo(*LAB)
        self.ciclo()
        self.assertEqual((self.estado_lab(), self.estado_de(REAL[1])), ("publicado", None),
                         "el trabajo pendiente pasa por delante de los mensajes nuevos")
        self.ciclos(6)
        self.assertEqual(set(self.estados().values()), {"publicado"})
        self.sin_duplicados()

    # Cubre: T-80
    def test_e3_cancelar_tras_publicar_la_hoja_borrada_se_recrea_una_vez_y_el_trabajo_sigue_vivo(self) -> None:
        self.sembrar(con_real=False)
        despacho.despachar(self.drive, CARPETA, Alcance(*LAB))
        hoja = self.trabajo_de(LAB[1], LAB[0])["sheet_file_id"]
        self.drive.archivos[hoja]["trashed"] = True                     # una persona la manda a la papelera
        informes = [self.ciclo() for _ in range(1)]
        self.ciclos(6)
        self.assertEqual(self.estado_lab(), "publicado", "borrar la hoja no cancela el trabajo")
        self.assertEqual(informes[0]["recuperadas"][0]["resultado"], "creado", "Python la vuelve a crear")
        self.assertEqual([h["id"] for h in self.hojas()], ["sheet-2"], "una sola hoja viva, nueva")

    # Cubre: T-80
    def test_e4_cancelar_tras_el_retorno_de_work_se_concilia_al_restaurar(self) -> None:
        self.sembrar(con_real=False)
        despacho.despachar(self.drive, CARPETA, Alcance(*LAB))
        self.work_responde(LAB[1])
        self.ciclos(2)
        self.assertEqual(self.estado_lab(), "conciliado")
        self.ciclos(2)
        self.assertEqual(self.trabajos_en_total(), 1)

    # Cubre: T-80
    def test_e4_dos_borradores_del_mismo_mensaje_requieren_a_una_persona(self) -> None:
        self.sembrar(con_real=False)
        despacho.despachar(self.drive, CARPETA, Alcance(*LAB))
        self.work_responde(LAB[1], "valido", "valido")
        informes = []
        for _ in range(8):
            informes.append(self.ciclo())
            self.t += 4000
        self.assertEqual(self.estado_lab(), "publicado", "nunca se elige entre dos borradores")
        self.assertTrue(any(i.get("requieren_persona") for i in informes))


class ContinuidadTest(Base):
    # Cubre: T-80
    def test_e5_los_reales_acumulados_se_procesan_de_uno_en_uno_en_orden_de_llegada(self) -> None:
        self.sembrar_pausa()
        orden = []
        for _ in range(4):
            informe = self.ciclo()
            orden += [self.trabajo_de(m, h)["message_id"] for p in informe["publicaciones"]
                      for h, m in [self._de_job(p["job_id"])]]
            self.t += 400
        self.assertEqual(orden, ["m1", "m0", "m9", "m2"], "el orden es el de detección, sin privilegiar a nadie")

    def _de_job(self, job_id: str) -> tuple[str, str]:
        trabajo = trabajos_work.obtener(job_id)
        assert trabajo is not None
        return trabajo["thread_id"], trabajo["message_id"]

    # Cubre: T-80
    def test_e6_restaurar_repetidamente_no_duplica_trabajos_hojas_ni_eventos_de_publicacion(self) -> None:
        self.sembrar_pausa()
        self.ciclos(6)
        antes = (self.trabajos_en_total(), len(self.hojas()), self.eventos_de("trabajo_publicado"))
        self.ciclos(5)
        self.assertEqual((self.trabajos_en_total(), len(self.hojas()), self.eventos_de("trabajo_publicado")), antes)
        self.assertEqual(antes, (4, 4, 4))

    def eventos_de(self, tipo: str) -> int:
        with store.lectura() as db:
            return int(db.execute("SELECT COUNT(*) FROM recepcion_eventos WHERE event_kind=?", (tipo,)).fetchone()[0])


class FallosTest(Base):
    # Cubre: T-80
    def test_e7_un_fallo_de_drive_al_recuperar_deja_el_trabajo_pendiente_y_se_reintenta_sin_duplicar(self) -> None:
        self.sembrar(con_real=False)
        self.drive.fallar_listado = ErrorHttp(500)
        self.ciclos(2)
        trabajo = self.trabajo_de(LAB[1], LAB[0])
        self.assertEqual((trabajo["state"], trabajo["attempts"] >= 1), ("pendiente_de_publicar", True))
        self.drive.fallar_listado = None
        self.ciclos(2)
        self.assertEqual((self.estado_lab(), self.trabajos_en_total(), len(self.hojas())), ("publicado", 1, 1))

    # Cubre: T-80
    def test_e7_tras_cinco_fallos_de_drive_el_coordinador_deja_de_reintentar_y_exige_una_persona(self) -> None:
        self.sembrar(con_real=False)
        self.drive.fallar_listado = ErrorHttp(500)
        self.ciclos(despacho.MAXIMO_DE_INTENTOS + 3)
        self.assertEqual(self.trabajo_de(LAB[1], LAB[0])["attempts"], despacho.MAXIMO_DE_INTENTOS)
        self.drive.fallar_listado = None
        self.ciclos(3)
        self.assertEqual(self.estado_lab(), "pendiente_de_publicar", "ya no lo recoge el ciclo: intervención humana")
        resultado = despacho.despachar(self.drive, CARPETA, Alcance(*LAB))
        self.assertEqual(resultado["publicacion"]["resultado"], "agotado", "ni el despacho manual lo fuerza")

    # Cubre: T-80
    def test_e7_un_fallo_de_gmail_al_importar_se_anota_con_retroceso_y_pide_persona_al_quinto(self) -> None:
        self.sembrar(con_real=False)
        despacho.despachar(self.drive, CARPETA, Alcance(*LAB))

        def gmail_caido(hilo: str, mensaje: str) -> dict[str, Any]:
            self.llamadas.append((hilo, mensaje))
            raise ConnectionError("simulado")

        self.coordinador = self.nuevo(importar=gmail_caido)
        informes = []
        for _ in range(8):
            informes.append(self.ciclo())
            self.t += 4000
        self.assertEqual(self.estado_lab(), "publicado")
        self.assertTrue(any(i.get("requieren_persona") for i in informes))

    # Cubre: T-80
    def test_e7_el_estado_de_un_trabajo_ficticio_nunca_pasa_a_cancelado_con_los_comandos_actuales(self) -> None:
        self.sembrar_pausa()
        self.ciclos(8)
        self.assertNotIn("cancelado", set(self.estados().values()))


class ExclusionTest(Base):
    # Cubre: T-80
    def test_b_un_trabajo_cancelado_es_invisible_para_el_coordinador_aunque_nadie_pueda_crearlo_hoy(self) -> None:
        """Opción B: el consumidor ya respeta `cancelado`; solo falta una orden que lo escriba (cambio de producción)."""
        self.sembrar_pausa()
        trabajos_work.crear_trabajo(*LAB)
        with store.transaccion() as db:            # simula la orden futura SOLO en la base temporal de esta prueba
            db.execute("UPDATE trabajos_work SET state=? WHERE thread_id=? AND message_id=?",
                       (trabajo_work.CANCELADO, *LAB))
        self.ciclos(8, max_publicar=3)
        self.assertEqual(self.estado_lab(), "cancelado")
        self.assertEqual({k: v for k, v in self.estados().items() if k != "m9"},
                         {"m1": "publicado", "m0": "publicado", "m2": "publicado"})
        self.assertEqual(len(self.hojas()), 3)
        self.assertEqual(trabajos_work.mensajes_nuevos(instante("00:00:00")), [])
        with self.assertRaises(trabajos_work.TrabajoRechazado):
            trabajos_work.publicar_trabajo(self.drive, self.trabajo_de(LAB[1], LAB[0])["job_id"], CARPETA)

    # Cubre: T-80
    def test_c_la_cuenta_del_buzon_es_una_constante_del_codigo_y_no_se_puede_cambiar_por_configuracion(self) -> None:
        self.assertEqual(recepcion.CUENTA, "ankacomercialia@gmail.com")
        self.assertIs(trabajos_work.CUENTA, recepcion.CUENTA, "los trabajos usan la misma constante, no la configuración")


def recepcion_pendiente() -> tuple[str, str]:
    return trabajos_work.PENDIENTE


if __name__ == "__main__":
    unittest.main()
