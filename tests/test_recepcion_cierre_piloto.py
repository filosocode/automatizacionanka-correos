"""Cierre del piloto 01 del receptor: destinatarios, contadores del ciclo y auditoría del cursor (U-81, D-151).

Cubre: U-81
"""

from tests import _isolate  # noqa: F401  (debe ir primero)

import json
import sqlite3
import tempfile
import threading
import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import patch

from anka.cli import gmail_reception
from anka.infra import settings, store
from anka.servicios import recepcion
from tests.test_recepcion import Gmail, event, message


def con_destinatarios(to="Anka comercial ia <ankacomercialia@gmail.com>", cc=None, bcc=None, **kwargs):
    """Un mensaje como el de Gmail, con los encabezados de destinatarios que se pidan (None = no existe)."""
    datos = message(**kwargs)
    cabeceras = [h for h in datos["payload"]["headers"] if h["name"] != "To"]
    for nombre, valor in (("To", to), ("Cc", cc), ("Bcc", bcc)):
        if valor is not None:
            cabeceras.append({"name": nombre, "value": valor})
    datos["payload"]["headers"] = cabeceras
    return datos


class DestinatariosTests(unittest.TestCase):
    def test_un_encabezado_vacio_ya_no_borra_a_los_demas(self):
        # el caso real del piloto 01: `To` presente y `Cc`/`Bcc` vacíos daban []
        self.assertEqual(recepcion._addresses("Anka comercial ia <ankacomercialia@gmail.com>", "", ""),
                         ["ankacomercialia@gmail.com"])
        self.assertEqual(recepcion._addresses("", "", ""), [])
        self.assertEqual(recepcion._addresses("   ", "\t"), [])
        self.assertEqual(recepcion._addresses(), [])

    def test_varios_destinatarios_se_normalizan_y_no_se_repiten(self):
        resultado = recepcion._addresses("A <a@x.com>, b@x.com", "C <c@x.com>", "d@x.com, A@X.com")
        self.assertEqual(resultado, ["a@x.com", "b@x.com", "c@x.com", "d@x.com"])
        self.assertEqual(recepcion._addresses("juan@x.com; maria@x.com"), ["juan@x.com", "maria@x.com"])
        self.assertEqual(recepcion._addresses("=?utf-8?q?Andr=C3=A9s?= <andres@x.com>"), ["andres@x.com"])

    def test_un_encabezado_malformado_no_afecta_a_los_otros_y_no_deja_basura(self):
        self.assertEqual(recepcion._addresses("a@x.com", "<<<", ""), ["a@x.com"])
        self.assertEqual(recepcion._addresses("<<<"), [])
        self.assertEqual(recepcion._addresses("basura"), [])                     # sin arroba no es una dirección
        self.assertEqual(recepcion._addresses("Juan <juan@x.com>, basura"), ["juan@x.com"])   # se rescata lo válido
        self.assertEqual(recepcion._addresses('"sin cerrar <j@x.com>'), ["j@x.com"])

    def test_el_remitente_sigue_igual(self):
        self.assertEqual(recepcion._addresses('"Andrés Muñoz" <91andresmunoz@gmail.com>'), ["91andresmunoz@gmail.com"])


class Base(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.directory = Path(self.temp.name)
        self.config = {"own_addresses": [recepcion.CUENTA]}
        self.when = datetime(2026, 10, 16, 15, tzinfo=UTC)
        self.gmail = Gmail()
        for parche in (patch.object(recepcion, "_sondeo_lock", threading.Lock()),
                       patch.object(settings, "DATA_DIR", self.directory)):
            parche.start()
            self.addCleanup(parche.stop)

    def scan(self, minutos=0):
        return recepcion.sondear(self.gmail, self.config, self.when + timedelta(minutes=minutos))

    def eventos(self, tipo):
        with store.lectura() as db:
            return [dict(r) for r in db.execute("SELECT * FROM recepcion_eventos WHERE event_kind=? ORDER BY id",
                                                (tipo,))]

    def control(self):
        with store.lectura() as db:
            return dict(db.execute("SELECT * FROM recepcion_control").fetchone())


class DestinatariosGuardadosTests(Base):
    def test_el_mensaje_registrado_conserva_sus_destinatarios_con_cc_y_bcc_vacios(self):
        self.scan()
        self.gmail.history_results = [event({"id": "m1", "threadId": "t1"})]
        self.gmail.mail_items["m1"] = con_destinatarios(cc="", bcc="")
        self.assertEqual(self.scan(1)["processed"], 1)
        with store.lectura() as db:
            guardado = db.execute("SELECT recipients_json FROM recepcion_mensajes").fetchone()[0]
        self.assertEqual(json.loads(guardado), ["ankacomercialia@gmail.com"])

    def test_sin_to_ni_cc_queda_vacio_sin_error(self):
        self.scan()
        self.gmail.history_results = [event({"id": "m1", "threadId": "t1"})]
        self.gmail.mail_items["m1"] = con_destinatarios(to=None)
        self.assertEqual(self.scan(1)["processed"], 1)
        with store.lectura() as db:
            self.assertEqual(db.execute("SELECT recipients_json FROM recepcion_mensajes").fetchone()[0], "[]")


class ContadoresTests(Base):
    def test_un_mensaje_nuevo_se_cuenta_como_descubierto_y_procesado(self):
        self.scan()
        self.gmail.history_results = [event({"id": "m1", "threadId": "t1"})]
        self.gmail.mail_items["m1"] = con_destinatarios()
        r = self.scan(1)
        self.assertEqual((r["status"], r["discovered"], r["processed"], r["history_seen"], r["already_registered"]),
                         ("ok", 1, 1, 1, 0))

    def test_un_mensaje_que_ya_estaba_registrado_no_es_descubierto_pero_si_visto(self):
        self.scan()
        self.gmail.history_results = [event({"id": "m1", "threadId": "t1"})]
        self.gmail.mail_items["m1"] = con_destinatarios()
        self.scan(1)
        # otra instancia (o un reintento de Gmail) vuelve a reportar el mismo mensaje
        self.gmail.history_results = [event({"id": "m1", "threadId": "t1"})]
        r = self.scan(2)
        self.assertEqual((r["discovered"], r["processed"], r["history_seen"], r["already_registered"]), (0, 0, 1, 1))
        with store.lectura() as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM recepcion_mensajes").fetchone()[0], 1)   # idempotente
        self.assertEqual(len(self.eventos("detectado")), 1)

    def test_los_contadores_cuadran_con_varios_mensajes_nuevos_y_repetidos(self):
        self.scan()
        self.gmail.history_results = [event({"id": "m1", "threadId": "t1"})]
        self.gmail.mail_items.update(m1=con_destinatarios(), m2=con_destinatarios(message_id="m2", thread_id="t2"))
        self.scan(1)
        self.gmail.history_results = [event({"id": "m1", "threadId": "t1"}, {"id": "m2", "threadId": "t2"})]
        r = self.scan(2)
        self.assertEqual((r["discovered"], r["already_registered"], r["history_seen"]), (1, 1, 2))
        self.assertEqual(r["history_seen"], r["discovered"] + r["already_registered"])

    def test_un_ciclo_sin_novedades_informa_ceros_pero_con_los_campos_nuevos(self):
        self.scan()
        r = self.scan(1)
        self.assertEqual({k: r[k] for k in ("status", "discovered", "processed", "retried", "history_seen",
                                            "already_registered")},
                         {"status": "ok", "discovered": 0, "processed": 0, "retried": 0, "history_seen": 0,
                          "already_registered": 0})

    def test_los_campos_de_siempre_se_conservan(self):
        self.scan()
        r = self.scan(1)
        for campo in ("status", "discovered", "processed", "retried", "manual_review", "own_excluded",
                      "baseline_excluded"):
            self.assertIn(campo, r)

    def test_la_primera_ejecucion_conserva_su_forma_exacta(self):
        self.assertEqual(self.scan(), {"status": "inicializado_sin_historial", "discovered": 0})


class AuditoriaDelCursorTests(Base):
    def test_un_sondeo_sin_novedades_no_escribe_evento_pero_si_la_hora_del_exito(self):
        self.scan()
        self.gmail.history_results = [{"historyId": "110", "history": []}]
        self.scan(1)                                              # el cursor pasa de 100 a 110: evento real
        self.assertEqual(len(self.eventos("cursor_actualizado")), 1)
        self.gmail.history_results = [{"historyId": "110", "history": []}]
        self.scan(2)
        self.gmail.history_results = [{"historyId": "110", "history": []}]
        self.scan(3)
        self.assertEqual(len(self.eventos("cursor_actualizado")), 1)          # los idénticos no suman
        self.assertEqual(self.control()["history_id"], "110")
        self.assertEqual(self.control()["last_success_at"], (self.when + timedelta(minutes=3)).isoformat())

    def test_un_cambio_real_deja_el_valor_anterior_y_el_nuevo(self):
        self.scan()
        self.gmail.history_results = [event({"id": "m1", "threadId": "t1"})]
        self.gmail.mail_items["m1"] = con_destinatarios()
        self.scan(1)
        detalle = json.loads(self.eventos("cursor_actualizado")[-1]["detail_json"])
        self.assertEqual((detalle["previous_history_id"], detalle["history_id"]), ("100", "110"))

    def test_los_errores_siguen_auditandose(self):
        self.scan()
        self.gmail.history_error = ConnectionError("sin red")
        r = self.scan(1)
        self.assertEqual(r["status"], "error")
        self.assertEqual(len(self.eventos("fallo_sondeo")), 1)
        self.assertEqual(self.control()["consecutive_errors"], 1)

    def test_la_recuperacion_tras_un_404_sigue_dejando_su_evento(self):
        class NoDisponible(Exception):
            status_code = 404
        self.scan()
        self.scan(1)                                              # sincronización delta confirmada
        self.gmail.history_error = NoDisponible("historial vencido")
        self.gmail.list_result = {"messages": [{"id": "m9", "threadId": "t9"}]}
        self.gmail.mail_items["m9"] = con_destinatarios(message_id="m9", thread_id="t9")
        r = self.scan(2)
        self.assertEqual(r["status"], "ok")
        self.assertEqual(len(self.eventos("hueco_reconstruido_desde_buzon")), 1)
        self.assertEqual((r["discovered"], r["history_seen"]), (1, 1))

    def test_la_base_queda_integra_despues_de_los_ciclos(self):
        self.scan()
        for minuto in range(1, 4):
            self.gmail.history_results = [{"historyId": "110", "history": []}]
            self.scan(minuto)
        with store.lectura() as db:
            self.assertEqual(db.execute("PRAGMA integrity_check").fetchone()[0], "ok")


class ReceptorAisladoTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        parche = patch.object(settings, "DATA_DIR", Path(self.temp.name))
        parche.start()
        self.addCleanup(parche.stop)

    def test_un_segundo_receptor_se_niega_antes_de_llamar_a_gmail(self):
        from anka.infra.instancia_unica import InstanciaUnica
        with InstanciaUnica(Path(self.temp.name) / gmail_reception.LOCK_FILENAME, "receptor Gmail aislado"), \
                patch.object(gmail_reception, "configure_logging"), \
                patch.object(gmail_reception.gmail_reception_auth, "gmail_service") as servicio, \
                self.assertRaises(SystemExit) as salida:
            gmail_reception.main(["--once"])
        self.assertEqual(salida.exception.code, 1)
        servicio.assert_not_called()

    def test_el_receptor_libera_su_candado_al_terminar_y_deja_el_siguiente_arranque_libre(self):
        from anka.infra.instancia_unica import InstanciaUnica, leer_informacion
        ruta = Path(self.temp.name) / gmail_reception.LOCK_FILENAME
        with patch.object(gmail_reception, "configure_logging"), \
                patch.object(gmail_reception.gmail_reception_auth, "gmail_service", return_value=object()), \
                patch.object(gmail_reception, "sondear", return_value={"status": "ok", "discovered": 0}):
            self.assertEqual(gmail_reception.main(["--once"]), 0)
        self.assertIsNone(leer_informacion(ruta))
        with InstanciaUnica(ruta, "receptor Gmail aislado"):
            pass

    def test_si_el_receptor_falla_igual_libera_el_candado(self):
        from anka.infra.instancia_unica import InstanciaUnica
        ruta = Path(self.temp.name) / gmail_reception.LOCK_FILENAME
        with patch.object(gmail_reception, "configure_logging"), \
                patch.object(gmail_reception.gmail_reception_auth, "gmail_service",
                             side_effect=sqlite3.OperationalError("database is locked")):
            self.assertEqual(gmail_reception.main(["--once"]), 1)
        with InstanciaUnica(ruta, "receptor Gmail aislado"):
            pass


if __name__ == "__main__":
    unittest.main()
