"""La base aguanta tres procesos: WAL, espera de 15 s, migración simultánea, candado al guardar y copias con -wal.

Cubre: U-42
"""

from tests import _isolate  # noqa: F401

import json
import os
import sqlite3
import subprocess
import sys
import tempfile
import threading
import time
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

from anka.infra import backup, settings, store
from anka.servicios import work_import
from tests.test_work_sync import b64, payload

RAIZ = Path(__file__).resolve().parents[1]


class BaseDeTodos(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        patcher = patch.object(settings, "DATA_DIR", self.root)
        patcher.start()
        self.addCleanup(patcher.stop)

    def abrir(self):
        db = store.connect()
        self.addCleanup(db.close)
        return db


class Configuracion(BaseDeTodos):
    def test_b0_1_wal_claves_foraneas_y_espera(self):
        db = self.abrir()
        self.assertEqual(db.execute("PRAGMA journal_mode").fetchone()[0], "wal")
        self.assertEqual(db.execute("PRAGMA foreign_keys").fetchone()[0], 1)
        self.assertEqual(db.execute("PRAGMA busy_timeout").fetchone()[0], 15000)
        self.assertEqual(db.execute("PRAGMA synchronous").fetchone()[0], 2)     # FULL: la reserva de envío es durable

    def test_las_claves_foraneas_se_cumplen(self):
        db = self.abrir()
        with self.assertRaises(sqlite3.IntegrityError):
            db.execute("INSERT INTO quote_metadata(thread_id, updated_at) VALUES ('sin-caso', 'x')")
        db.rollback()

    def test_la_migracion_16_quita_metadatos_sin_caso(self):
        self.abrir().close()
        crudo = sqlite3.connect(self.root / "quotes.sqlite3")
        crudo.execute("INSERT INTO quote_metadata(thread_id, updated_at) VALUES ('huerfano', 'x')")
        crudo.execute("PRAGMA user_version=15")
        crudo.commit()
        crudo.close()
        otra = self.abrir()
        self.assertEqual(otra.execute("SELECT COUNT(*) FROM quote_metadata").fetchone()[0], 0)
        self.assertEqual(otra.execute("PRAGMA foreign_key_check").fetchall(), [])
        self.assertEqual(otra.execute("PRAGMA user_version").fetchone()[0], store.SCHEMA_VERSION)
        self.assertTrue((self.root / "quotes.sqlite3.bak-v15").exists())

    def test_b0_3_un_escritor_espera_en_vez_de_fallar_a_los_5_segundos(self):
        db = self.abrir()
        bloqueo = sqlite3.connect(self.root / "quotes.sqlite3", check_same_thread=False)
        bloqueo.execute("BEGIN IMMEDIATE")
        threading.Timer(1.0, bloqueo.rollback).start()
        inicio = time.monotonic()
        store.record_event(db, "t1", "k", "s", "r", "resumen")      # espera el candado en vez de fallar
        db.commit()
        self.assertGreaterEqual(time.monotonic() - inicio, 0.8)
        bloqueo.close()


class MigracionSimultanea(BaseDeTodos):
    def test_b0_2_seis_hilos_abren_la_misma_base_vacia(self):
        errores, versiones = [], []

        def abrir():
            try:
                db = store.open_db(self.root)
                versiones.append(db.execute("PRAGMA user_version").fetchone()[0])
                db.close()
            except Exception as exc:     # noqa: BLE001 - la prueba recoge cualquier fallo
                errores.append(repr(exc))

        hilos = [threading.Thread(target=abrir) for _ in range(6)]
        for hilo in hilos:
            hilo.start()
        for hilo in hilos:
            hilo.join()
        self.assertEqual(errores, [])
        self.assertEqual(versiones, [store.SCHEMA_VERSION] * 6)

    def test_b0_2_cuatro_procesos_arrancan_a_la_vez(self):
        codigo = ("from anka.infra import store; import pathlib, sys; "
                  "db = store.open_db(pathlib.Path(sys.argv[1])); "
                  "print(db.execute('PRAGMA user_version').fetchone()[0])")
        entorno = {k: v for k, v in os.environ.items() if k != "OPENAI_API_KEY"}
        procesos = [subprocess.Popen([sys.executable, "-c", codigo, str(self.root)], cwd=RAIZ, env=entorno,
                                     stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True) for _ in range(4)]
        for proceso in procesos:
            salida, error = proceso.communicate(timeout=120)
            self.assertEqual(proceso.returncode, 0, error)
            self.assertEqual(salida.strip(), str(store.SCHEMA_VERSION))


class CandadoAlGuardar(BaseDeTodos):
    def test_b0_4_save_lee_bajo_el_candado_y_no_pisa_la_edicion_manual(self):
        db = self.abrir()
        store.save(db, "T9", "m1", "review", "c@example.com", {"activities": [{"a": 1}]})
        editor = sqlite3.connect(self.root / "quotes.sqlite3")
        editor.execute("BEGIN IMMEDIATE")
        editor.execute("UPDATE quotes SET analysis_json=? WHERE thread_id='T9'",
                       (json.dumps({"activities": [{"a": 2}], "manual_edit": {"rows": 1}}),))
        terminado = []

        def guardar():
            otra = store.connect()
            store.save(otra, "T9", "m2", "review", "c@example.com", {"activities": [{"a": 3}]})
            otra.close()
            terminado.append(True)

        hilo = threading.Thread(target=guardar)
        hilo.start()
        time.sleep(0.4)
        self.assertEqual(terminado, [])      # espera el candado: no leyó el análisis viejo
        editor.commit()
        editor.close()
        hilo.join(timeout=30)
        final = json.loads(db.execute("SELECT analysis_json FROM quotes WHERE thread_id='T9'").fetchone()[0])
        self.assertEqual(final["activities"], [{"a": 2}])
        self.assertEqual(final["pending_model_activities"], [{"a": 3}])

    def test_b0_4_el_puente_no_hace_retroceder_un_caso_que_el_dashboard_avanzo_durante_la_espera(self):
        work_import.dashboard_upsert(b64(json.dumps(payload(thread="T8", message="m1", status="Faltan datos"))))
        db = self.abrir()
        dashboard = sqlite3.connect(self.root / "quotes.sqlite3")
        dashboard.execute("BEGIN IMMEDIATE")
        dashboard.execute("UPDATE quotes SET status='ready' WHERE thread_id='T8'")
        hilo = threading.Thread(target=lambda: work_import.dashboard_upsert(
            b64(json.dumps(payload(thread="T8", message="m2", status="Faltan datos")))))
        hilo.start()
        time.sleep(0.4)
        dashboard.commit()
        dashboard.close()
        hilo.join(timeout=30)
        self.assertEqual(db.execute("SELECT status FROM quotes WHERE thread_id='T8'").fetchone()[0], "ready")


class CopiasConWal(BaseDeTodos):
    def preparar(self):
        db = self.abrir()
        store.save(db, "T1", "m1", "review", "c@example.com", {"activities": []})
        return db

    def test_la_copia_es_un_solo_archivo_y_se_puede_verificar(self):
        self.preparar()
        copia = backup.create_backup()
        with zipfile.ZipFile(copia) as archivo:
            self.assertNotIn("quotes.sqlite3-wal", archivo.namelist())
        resultado = backup.verificar_ultima_copia()
        self.assertTrue(resultado["ok"], resultado)
        self.assertEqual(resultado["casos"], 1)
        self.assertTrue((self.root / "quotes.sqlite3").exists())      # la verificación no toca la base viva

    def test_b0_5_restaurar_borra_wal_y_shm_viejos_y_guarda_lo_anterior(self):
        db = self.preparar()
        copia = backup.create_backup()
        store.save(db, "T2", "m1", "review", "c@example.com", {"activities": []})
        db.commit()
        db.close()
        base = self.root / "quotes.sqlite3"
        basura = b"basura que no debe aplicarse"
        base.with_name(base.name + "-wal").write_bytes(basura)
        backup.restore(copia)
        wal = base.with_name(base.name + "-wal")
        self.assertFalse(wal.exists() and wal.read_bytes() == basura)
        nueva = self.abrir()
        self.assertEqual(nueva.execute("PRAGMA integrity_check").fetchone()[0], "ok")
        self.assertEqual([r[0] for r in nueva.execute("SELECT thread_id FROM quotes")], ["T1"])
        anteriores = list(self.root.glob("quotes.sqlite3.before-restore-*"))
        self.assertEqual(len(anteriores), 1)
        previa = sqlite3.connect(anteriores[0])
        self.assertEqual(previa.execute("SELECT COUNT(*) FROM quotes").fetchone()[0], 2)
        previa.close()

    def test_rechaza_un_respaldo_de_un_esquema_mas_nuevo(self):
        self.preparar().close()
        copia = backup.create_backup()
        with zipfile.ZipFile(copia) as archivo:
            archivo.extract("quotes.sqlite3", self.root / "extraida")
        nueva = self.root / "extraida" / "quotes.sqlite3"
        conexion = sqlite3.connect(nueva)
        conexion.execute(f"PRAGMA user_version={store.SCHEMA_VERSION + 1}")
        conexion.commit()
        conexion.close()
        with self.assertRaises(ValueError):
            backup._comprobar_base(nueva)


if __name__ == "__main__":
    unittest.main()
