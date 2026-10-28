"""Compatibilidad de la lápida de cancelación (T-81) con las funciones PRODUCTIVAS actuales (T-82).

SQLite temporal, Drive y Gmail simulados y mensajes ficticios. Las lápidas se crean con la referencia de laboratorio porque
la orden productiva todavía no existe. Cada prueba documenta si el camino ya es SEGURO hoy o sigue ABIERTO; las abiertas
describen el fallo actual y deben invertirse cuando se integre la cancelación (ver `laboratorio/banco_documental/parche_t82`).

Cubre: T-82
"""

from tests import _isolate  # noqa: F401  (debe ir primero)

import json
import unittest

from anka.cli import work_jobs
from anka.infra import store
from anka.servicios import trabajos_work
from tests.test_aislamiento_t79 import LAB
from tests.test_cancelacion_t81 import Base
from tests.test_importacion_aislada import ID as ID_AISLADO

HILO_AISLADO = (ID_AISLADO, ID_AISLADO)


class CaminosSegurosHoy(Base):
    """Rutas que YA respetan una lápida con el código productivo actual."""

    # Cubre: T-82
    def test_01_la_recepcion_no_vuelve_a_ofrecer_el_mensaje_ni_el_coordinador_lo_crea(self) -> None:
        self.sembrar_pausa()
        self.cancelar()
        self.assertNotIn(LAB[1], [m["message_id"] for m in trabajos_work.mensajes_nuevos(self.plan_.desde)])
        self.ciclos(6)
        self.assertEqual(self.estado_lab(), "cancelado")
        self.assertEqual(len(self.hojas()), 3)

    # Cubre: T-82
    def test_02_crear_trabajo_y_la_orden_crear_devuelven_la_lapida_sin_crear_otro(self) -> None:
        self.sembrar_pausa()
        self.cancelar()
        salida: list[str] = []
        codigo = work_jobs.main(["crear", "--thread-id", LAB[0], "--message-id", LAB[1]], salida.append)
        self.assertEqual(codigo, 0)
        self.assertEqual(json.loads(salida[0])["state"], "cancelado")
        self.assertEqual(self.trabajos_en_total(), 1)

    # Cubre: T-82
    def test_04_marcar_conciliado_y_el_archivado_no_tocan_un_cancelado(self) -> None:
        self.sembrar_pausa()
        self.cancelar()
        with store.transaccion() as db:
            cerrados = trabajos_work.marcar_conciliado(db, *LAB, "ref")
        self.assertEqual(cerrados, [])
        self.assertEqual(self.estado_lab(), "cancelado")
        self.ciclos(3)
        self.assertEqual(self.drive.movimientos if hasattr(self.drive, "movimientos") else [], [])

    # Cubre: T-82
    def test_05_el_coordinador_no_intenta_importar_una_lapida(self) -> None:
        self.sembrar(con_real=False)
        self.cancelar()
        self.ciclos(5)
        self.assertEqual(self.llamadas, [], "ni siquiera consulta Gmail por ese mensaje")


if __name__ == "__main__":
    unittest.main()
