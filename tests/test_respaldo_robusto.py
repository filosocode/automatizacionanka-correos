"""La copia de seguridad no frena al lanzador, se verifica y la cadena de huellas no se puede acortar (U-60).

Cubre: U-60
"""

from tests import _isolate  # noqa: F401  (debe ir primero)

import json
import os
import sqlite3
import tempfile
import time
import unittest
import zipfile
from pathlib import Path
from unittest.mock import MagicMock, patch

from anka.cli import supervisor
from anka.infra import acciones_en_curso, backup, settings, store
from anka.infra.store import record_event, verify_chain


class Base(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        parche = patch.object(settings, "DATA_DIR", self.root)
        parche.start()
        self.addCleanup(parche.stop)


# Cubre: U-60
class LaCopiaVaFueraDelBucle(Base):
    def test_la_vigila_sin_esperarla_y_no_hay_run_backup_en_el_lanzador(self):
        self.assertFalse(hasattr(supervisor, "run_backup"))
        proceso = MagicMock()
        proceso.poll.return_value = None
        copia = supervisor.CopiaDeSeguridad()
        with patch.object(supervisor.subprocess, "Popen", return_value=proceso) as popen, \
                patch.object(supervisor, "backup_due", return_value=True):
            copia.check()
            copia.check()                         # sigue corriendo: no lanza otra ni la espera
        popen.assert_called_once()
        self.assertIn("anka.infra.backup", popen.call_args.args[0])
        proceso.wait.assert_not_called()

    def test_pasado_el_tope_la_cancela_la_anota_y_no_reintenta_en_una_hora(self):
        proceso = MagicMock()
        proceso.poll.return_value = None
        copia = supervisor.CopiaDeSeguridad()
        with patch.object(supervisor.subprocess, "Popen", return_value=proceso), \
                patch.object(supervisor, "backup_due", return_value=True):
            copia.check()
            copia.started -= supervisor.COPIA_TOPE + 1
            with self.assertLogs("anka.cli.supervisor", "ERROR"):
                copia.check()
        proceso.kill.assert_called_once()
        ultima = json.loads((self.root / backup.LAST_FILE).read_text(encoding="utf-8"))
        self.assertIn("se canceló", ultima["error"])
        with patch.object(supervisor.subprocess, "Popen") as popen, \
                patch.object(supervisor, "backup_due", return_value=True):
            copia.check()
        popen.assert_not_called()                 # la espera de una hora
        copia.retry_at = 0
        with patch.object(supervisor.subprocess, "Popen", return_value=proceso) as popen, \
                patch.object(supervisor, "backup_due", return_value=True):
            copia.check()
        popen.assert_called_once()

    def test_una_copia_que_falla_no_se_repite_en_cada_vuelta(self):
        proceso = MagicMock()
        proceso.poll.return_value = 1
        copia = supervisor.CopiaDeSeguridad()
        with patch.object(supervisor.subprocess, "Popen", return_value=proceso) as popen, \
                patch.object(supervisor, "backup_due", return_value=True):
            copia.check()                         # la lanza
            with self.assertLogs("anka.cli.supervisor", "WARNING"):
                copia.check()                     # ve que falló
            copia.check()
            copia.check()
        popen.assert_called_once()

    def test_sin_copia_pendiente_no_lanza_nada(self):
        copia = supervisor.CopiaDeSeguridad()
        with patch.object(supervisor.subprocess, "Popen") as popen, \
                patch.object(supervisor, "backup_due", return_value=False):
            copia.check()
        popen.assert_not_called()


# Cubre: U-60
class LaCopiaSeVerifica(Base):
    def crear_base(self):
        db = store.open_db(self.root)
        db.close()

    def test_una_copia_buena_pasa_y_queda(self):
        self.crear_base()
        path = backup.create_backup()
        self.assertTrue(path.exists())
        backup._verificar_zip(path)

    def test_si_la_verificacion_falla_no_queda_la_copia(self):
        self.crear_base()
        with patch.object(backup, "_verificar_zip", side_effect=ValueError("dañada")):
            with self.assertRaises(ValueError):
                backup.create_backup()
        self.assertEqual(list(backup.backups_dir().glob("*.zip")), [])

    def test_un_zip_con_una_base_corrupta_no_pasa(self):
        malo = self.root / "malo.zip"
        with zipfile.ZipFile(malo, "w") as archivo:
            archivo.writestr("quotes.sqlite3", b"esto no es una base de datos" * 100)
        with self.assertRaises((ValueError, sqlite3.DatabaseError)):
            backup._verificar_zip(malo)

    def test_anotar_fallo_deja_el_motivo_y_hace_que_toque_otra_copia(self):
        backup.anotar_fallo("Se agotó el tiempo.")
        self.assertTrue(backup.backup_due())
        ultima = json.loads((self.root / backup.LAST_FILE).read_text(encoding="utf-8"))
        self.assertIn("Se agotó el tiempo", ultima["error"])


# Cubre: U-60
class RestaurarConTodoParado(Base):
    def test_se_niega_si_otro_proceso_escribe_en_la_base_y_no_la_toca(self):
        store.open_db(self.root).close()
        copia = backup.create_backup()
        antes = (self.root / "quotes.sqlite3").read_bytes()
        escritor = sqlite3.connect(self.root / "quotes.sqlite3", isolation_level=None)
        escritor.execute("BEGIN IMMEDIATE")
        self.addCleanup(escritor.close)
        with patch("anka.infra.work_heartbeat.other_bridge_running", return_value=False):
            with self.assertRaises(ValueError) as caido:
                backup.restore(copia)
        self.assertIn("La base está en uso", str(caido.exception))
        escritor.execute("ROLLBACK")
        self.assertEqual((self.root / "quotes.sqlite3").read_bytes(), antes)

    def test_se_niega_si_hay_una_accion_del_dashboard_en_curso(self):
        store.open_db(self.root).close()
        copia = backup.create_backup()
        with acciones_en_curso.en_curso("/send"), \
                patch("anka.infra.work_heartbeat.other_bridge_running", return_value=False):
            with self.assertRaises(ValueError):
                backup.restore(copia)

    def test_con_todo_parado_restaura(self):
        store.open_db(self.root).close()
        copia = backup.create_backup()
        with patch("anka.infra.work_heartbeat.other_bridge_running", return_value=False):
            self.assertEqual(backup.restore(copia), self.root / "quotes.sqlite3")

    def test_el_comando_se_niega_si_el_dashboard_contesta(self):
        with patch.object(backup, "dashboard_en_marcha", return_value=True), \
                patch("sys.argv", ["backup.py", "--restore", "x.zip"]), patch.object(backup, "configure_logging"):
            with self.assertRaises(SystemExit) as salida:
                backup.main()
        self.assertIn("El dashboard está abierto", str(salida.exception))


# Cubre: U-60
class AnclaDeLaCadena(Base):
    def preparar(self, cuantos=3):
        db = store.open_db(self.root)
        self.addCleanup(db.close)
        for numero in range(cuantos):
            record_event(db, "T1", "nota", "dashboard", f"r{numero}", f"hecho {numero}")
        db.commit()
        return db

    def test_borrar_la_cola_se_detecta(self):
        db = self.preparar()
        self.assertEqual(verify_chain(db, "T1"), (True, 3))
        db.execute("DELETE FROM case_events WHERE id=(SELECT MAX(id) FROM case_events WHERE thread_id='T1')")
        db.commit()
        self.assertFalse(verify_chain(db, "T1")[0])

    def test_borrar_todos_los_hechos_se_detecta(self):
        db = self.preparar()
        db.execute("DELETE FROM case_events WHERE thread_id='T1'")
        db.commit()
        self.assertFalse(verify_chain(db, "T1")[0])

    def test_seguir_anotando_mantiene_la_cadena_integra(self):
        db = self.preparar()
        record_event(db, "T1", "nota", "dashboard", "r9", "otro hecho")
        record_event(db, "T1", "nota", "dashboard", "r9", "otro hecho")       # repetido: no cuenta dos veces
        db.commit()
        self.assertEqual(verify_chain(db, "T1"), (True, 4))
        self.assertEqual(db.execute("SELECT total FROM event_anchors WHERE thread_id='T1'").fetchone()[0], 4)

    def test_un_caso_sin_hechos_no_tiene_ancla_y_es_integro(self):
        db = store.open_db(self.root)
        self.addCleanup(db.close)
        self.assertEqual(verify_chain(db, "nada"), (True, 0))

    def test_la_migracion_20_ancla_lo_que_ya_habia(self):
        db = self.preparar()
        db.execute("DROP TABLE event_anchors")
        db.execute("PRAGMA user_version = 19")
        db.commit()
        db.close()
        db = store.open_db(self.root)
        self.addCleanup(db.close)
        self.assertEqual(db.execute("PRAGMA user_version").fetchone()[0], store.SCHEMA_VERSION)
        self.assertEqual(verify_chain(db, "T1"), (True, 3))
        db.execute("DELETE FROM case_events WHERE id=(SELECT MAX(id) FROM case_events WHERE thread_id='T1')")
        db.commit()
        self.assertFalse(verify_chain(db, "T1")[0])

    def test_eliminar_un_caso_conserva_su_historial_y_su_ancla(self):
        from anka.servicios.repositorio import TABLAS_QUE_SE_CONSERVAN
        self.assertIn("event_anchors", TABLAS_QUE_SE_CONSERVAN)


# Cubre: U-60
class CopiaPreviaAMigrar(Base):
    def base_vieja(self):
        store.open_db(self.root).close()
        crudo = sqlite3.connect(self.root / "quotes.sqlite3")
        crudo.execute("PRAGMA user_version=15")
        crudo.commit()
        crudo.close()

    def test_una_copia_vieja_se_renueva_y_la_anterior_no_se_pierde(self):
        self.base_vieja()
        previa = self.root / "quotes.sqlite3.bak-v15"
        previa.write_bytes(b"copia de un intento anterior")
        hace_una_hora = time.time() - 3600
        os.utime(previa, (hace_una_hora, hace_una_hora))
        store.open_db(self.root).close()
        self.assertEqual((self.root / "quotes.sqlite3.bak-v15.anterior").read_bytes(), b"copia de un intento anterior")
        self.assertNotEqual(previa.read_bytes(), b"copia de un intento anterior")
        base = sqlite3.connect(previa)
        self.addCleanup(base.close)
        self.assertEqual(base.execute("PRAGMA integrity_check").fetchone()[0], "ok")

    def test_una_copia_reciente_de_otro_proceso_se_respeta(self):
        self.base_vieja()
        previa = self.root / "quotes.sqlite3.bak-v15"
        previa.write_bytes(b"recien hecha por otro proceso")
        store.open_db(self.root).close()
        self.assertEqual(previa.read_bytes(), b"recien hecha por otro proceso")
        self.assertFalse((self.root / "quotes.sqlite3.bak-v15.anterior").exists())


if __name__ == "__main__":
    unittest.main()
