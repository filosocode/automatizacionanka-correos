"""Compatibilidad de la lápida de cancelación (T-81) con las funciones PRODUCTIVAS actuales (T-82).

SQLite temporal, Drive y Gmail simulados y mensajes ficticios. Las lápidas se crean con la referencia de laboratorio porque
la orden productiva todavía no existe. Cada prueba documenta si el camino ya es SEGURO hoy o sigue ABIERTO; las abiertas
describen el fallo actual y deben invertirse cuando se integre la cancelación (ver `laboratorio/banco_documental/parche_t82`).

Cubre: T-82
"""

from tests import _isolate  # noqa: F401  (debe ir primero)

import contextlib
import io
import json
import unittest
from pathlib import Path
from unittest.mock import patch

from anka.cli import work_jobs
from anka.infra import backup, store
from anka.servicios import coordinacion, despacho, estado_operativo, quotes, recepcion, trabajos_work, work_import
from anka.servicios.despacho import Alcance
from tests.test_aislamiento_t79 import ETIQUETA, LAB
from tests.test_cancelacion_t81 import Base, MARCA, autorizacion
from tests.test_coordinacion import CARPETA
from tests.test_importacion_aislada import GmailAislado, borrador, origen
from tests.test_importacion_aislada import ID as ID_AISLADO
from tests.test_work_sync import b64, payload

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
    def test_03_el_despacho_manual_y_publicar_rechazan_la_lapida(self) -> None:
        self.sembrar_pausa()
        resultado = self.cancelar()
        with self.assertRaises(trabajos_work.TrabajoRechazado):
            despacho.despachar(self.drive, CARPETA, Alcance(*LAB))
        with self.assertRaises(trabajos_work.TrabajoRechazado):
            trabajos_work.publicar_trabajo(self.drive, resultado["job_id"], CARPETA)
        self.assertEqual(len(self.hojas()), 0)

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

    # Cubre: T-82
    def test_06_las_importaciones_aceptadas_por_el_coordinador_no_incluyen_el_resultado_cancelado(self) -> None:
        self.assertNotIn("mensaje_cancelado", coordinacion.IMPORTACIONES_SIN_FALLO)


class CaminosAbiertosHoy(Base):
    """Rutas que TODAVÍA aceptan un mensaje cancelado: son los fallos actuales que cierra la integración."""

    def sembrar_con_ids_de_gmail(self) -> None:
        self.fila(*HILO_AISLADO, asunto=ETIQUETA, remitente="prueba.controlada@example.com")
        self.cancelar(autorizacion(HILO_AISLADO))

    # Cubre: T-82
    def test_10_ABIERTO_la_importacion_directa_de_un_borrador_guarda_el_analisis_y_cierra_el_mensaje(self) -> None:
        self.sembrar_con_ids_de_gmail()
        work_import.dashboard_upsert(b64(json.dumps(payload(thread=ID_AISLADO, message=ID_AISLADO))), actualizar_hoja=False)
        self.assertEqual(sorted(r["thread_id"] for r in quotes.all_quotes()), [ID_AISLADO])
        with store.lectura() as db:
            fila = db.execute("SELECT state,disposition FROM recepcion_mensajes WHERE message_id=?",
                              (ID_AISLADO,)).fetchone()
        self.assertEqual(tuple(fila), recepcion.INTERPRETADO)
        self.assertEqual(self.estado_de(ID_AISLADO, ID_AISLADO), "cancelado", "el trabajo sigue cancelado")

    # Cubre: T-82
    def test_11_ABIERTO_la_importacion_aislada_importa_el_borrador_de_un_mensaje_cancelado(self) -> None:
        self.sembrar_con_ids_de_gmail()
        gmail = GmailAislado({"g1": borrador()})
        resultado = importar_aislado(self, gmail)
        self.assertEqual(resultado["resultado"], "importado")

    # Cubre: T-82
    def test_12_ABIERTO_conciliar_con_work_cierra_la_fila_aunque_haya_lapida(self) -> None:
        self.sembrar_con_ids_de_gmail()
        with store.transaccion() as db:
            resultado = recepcion.conciliar_con_work(db, *HILO_AISLADO, "ref")
        self.assertEqual(resultado, "conciliado")

    # Cubre: T-82
    def test_13_ABIERTO_el_comando_dashboard_upsert_de_app_py_usa_la_misma_funcion_sin_comprobar_la_lapida(self) -> None:
        import inspect

        from anka.cli import app

        texto = inspect.getsource(app._cmd_dashboard_upsert)
        self.assertIn("dashboard_upsert(args.payload_base64)", texto)
        self.assertNotIn("cancel", texto.lower())

    # Cubre: T-82
    def test_14_ABIERTO_no_existe_la_orden_cancelar_ni_el_diario_de_lapidas(self) -> None:
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            work_jobs.build_parser().parse_args(["cancelar", "--thread-id", "a", "--message-id", "b"])
        self.assertFalse(hasattr(trabajos_work, "cancelar_mensaje"))
        with self.assertRaises(ImportError):
            __import__("anka.infra.lapidas")

    # Cubre: T-82
    def test_15_ABIERTO_restaurar_una_copia_anterior_pierde_la_lapida_y_el_mensaje_vuelve_a_ser_publicable(self) -> None:
        self.sembrar(con_real=False)
        copia = backup.create_backup()
        self.cancelar()
        self.assertEqual(self.estado_lab(), "cancelado")
        with patch("anka.infra.work_heartbeat.other_bridge_running", return_value=False), \
                patch.object(backup.acciones_en_curso, "acciones_activas", return_value=[]), \
                patch.object(backup, "_base_en_uso", return_value=False):
            backup.restore(copia)
        self.assertIsNone(self.estado_lab(), "la lápida desapareció con la copia")
        self.assertEqual(len(trabajos_work.mensajes_nuevos(self.plan_.desde)), 1, "el ficticio vuelve a ser elegible")

    # Cubre: T-82
    def test_16_ABIERTO_el_panel_muestra_el_mensaje_cancelado_como_pendiente_de_atencion(self) -> None:
        self.sembrar_pausa()
        self.cancelar()
        panel = estado_operativo.estado_operativo()
        self.assertIn(LAB[1], [f["message_id"] for f in panel["atencion"]])
        self.assertEqual([f for f in trabajos_work.clasificar_pendientes() if f["message_id"] == LAB[1]][0].get("cancelado"),
                         None, "la clasificación no distingue lo cancelado")
        self.assertIn("cancelado", [t["estado"] for t in panel["trabajos"]], "los conteos de trabajos sí lo muestran")


def importar_aislado(caso: Base, gmail: GmailAislado) -> dict:
    from anka.cli import work_sync

    return work_sync.importar_aislado(gmail, lambda raw: work_import.dashboard_upsert(raw, actualizar_hoja=False),
                                      Path(caso.datos) / "manifiestos", ID_AISLADO, ID_AISLADO, {recepcion.CUENTA})


_ = (MARCA, origen)


if __name__ == "__main__":
    unittest.main()
