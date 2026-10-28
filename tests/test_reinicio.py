"""Eliminar un caso no deja restos y se puede reiniciar todo para probar desde cero. Cubre: T-42"""

from tests import _isolate  # noqa: F401  (debe ir primero)

import json
import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from unittest.mock import patch

from anka.dominio.errores import ErrorDeAnka
from anka.dominio.status import QuoteStatus
from anka.infra import backup, reinicio, settings, store
from anka.servicios import quotes, repositorio
from tests.test_app import complete_analysis

AHORA = "2026-10-13T10:00:00-05:00"


class Base(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.dir = Path(self.temp.name)
        parche = patch.object(settings, "DATA_DIR", self.dir)
        parche.start()
        self.addCleanup(parche.stop)
        db = store.open_db(self.dir)
        self.addCleanup(db.close)
        for hilo in ("t-1", "t-2", "t-3"):
            store.save(db, hilo, "m-" + hilo, QuoteStatus.REVIEW, "c@x.co", complete_analysis(location=""))
            store.add_message(db, hilo, "m-" + hilo, "received", "c@x.co", "Asunto", "Hola", AHORA)
            store.add_table_version(db, hilo, [], "model", "inicial")
            db.execute("INSERT INTO case_followup (thread_id, updated_at) VALUES (?, ?)", (hilo, AHORA))
            db.execute("INSERT INTO analysis_failures (thread_id, failures, last_error, first_at, last_at, next_retry_at) "
                       "VALUES (?, 1, 'x', ?, ?, ?)", (hilo, AHORA, AHORA, AHORA))
        db.execute("INSERT INTO case_links (thread_id, other_thread_id, message_id, sender_email, reason, occurred_at) "
                   "VALUES ('t-1', 't-2', 'x1', 'a@b.co', 'mismo', ?)", (AHORA,))
        db.execute("INSERT INTO case_links (thread_id, other_thread_id, message_id, sender_email, reason, occurred_at) "
                   "VALUES ('t-3', 't-2', 'x2', 'a@b.co', 'mismo', ?)", (AHORA,))
        db.execute("INSERT INTO project_dismissals (thread_id, other_thread_id, decided_at) VALUES ('t-1', 't-2', ?)",
                   (AHORA,))
        db.commit()

    @staticmethod
    def _tiene(db: sqlite3.Connection, tabla: str, columna: str) -> bool:
        return columna in [fila[1] for fila in db.execute(f"PRAGMA table_info({tabla})")]

    def cuenta(self, tabla: str, donde: str = "1=1") -> int:
        with closing(sqlite3.connect(self.dir / "quotes.sqlite3")) as db:
            return db.execute(f"SELECT COUNT(*) FROM {tabla} WHERE {donde}").fetchone()[0]


class AlEliminarUnCaso(Base):
    def test_no_quedan_restos_del_caso_salvo_la_evidencia(self) -> None:
        # Cubre: T-42
        self.assertGreater(self.cuenta("case_messages", "thread_id='t-1'"), 0)
        self.assertGreater(self.cuenta("table_versions", "thread_id='t-1'"), 0)
        quotes.delete_quotes(["t-1"])
        for tabla in repositorio.TABLAS_DEL_CASO:
            self.assertEqual(self.cuenta(tabla, "thread_id='t-1'"), 0, tabla)
        self.assertEqual(self.cuenta("case_links", "thread_id='t-1' OR other_thread_id='t-1'"), 0)
        self.assertEqual(self.cuenta("project_dismissals", "thread_id='t-1' OR other_thread_id='t-1'"), 0)
        self.assertGreater(self.cuenta("case_events", "thread_id='t-1'"), 0)          # la evidencia se conserva

    def test_los_otros_casos_no_se_tocan(self) -> None:
        # Cubre: T-42
        quotes.delete_quotes(["t-1"])
        self.assertEqual(self.cuenta("quotes", "thread_id IN ('t-2','t-3')"), 2)
        self.assertEqual(self.cuenta("case_messages", "thread_id IN ('t-2','t-3')"), 2)
        self.assertEqual(self.cuenta("case_links", "thread_id='t-3'"), 1)             # no involucra al hilo borrado

    def test_un_vinculo_cuenta_aunque_el_hilo_borrado_sea_el_otro_lado(self) -> None:
        # Cubre: T-42
        quotes.delete_quotes(["t-2"])
        self.assertEqual(self.cuenta("case_links"), 0)

    def test_toda_tabla_con_hilo_esta_en_una_de_las_listas(self) -> None:
        # Cubre: T-42
        conocidas = (set(repositorio.TABLAS_DEL_CASO) | set(repositorio.TABLAS_CON_DOS_HILOS)
                     | set(repositorio.TABLAS_QUE_SE_CONSERVAN))
        with closing(sqlite3.connect(self.dir / "quotes.sqlite3")) as db:
            con_hilo = {n for (n,) in db.execute("SELECT name FROM sqlite_master WHERE type='table' "
                                                 "AND name NOT LIKE 'sqlite_%'")
                        if self._tiene(db, n, "thread_id")}
        self.assertEqual(con_hilo - conocidas, set(), "tabla nueva con thread_id: decide si se borra o se conserva")


class ElReinicio(Base):
    def preparar_archivos(self) -> None:
        (self.dir / reinicio.ESTADO_DEL_PUENTE).write_text(json.dumps({"t-1": "abc"}), encoding="utf-8")
        for carpeta in reinicio.CARPETAS_DERIVADAS:
            (self.dir / carpeta).mkdir()
            (self.dir / carpeta / "algo.txt").write_text("x", encoding="utf-8")
        (self.dir / "config.json").write_text("{}", encoding="utf-8")
        (self.dir / "token.json").write_text("secreto", encoding="utf-8")
        (self.dir / "evidence").mkdir()
        (self.dir / "evidence" / "m.eml").write_text("correo", encoding="utf-8")

    def test_sin_confirmar_solo_muestra_y_no_cambia_nada(self) -> None:
        # Cubre: T-42
        self.preparar_archivos()
        datos = reinicio.resumen()
        self.assertEqual(datos["filas"]["quotes"], 3)
        self.assertTrue(datos["estado_del_puente"])
        self.assertEqual(datos["archivos"]["review_queue"], 1)
        self.assertEqual(self.cuenta("quotes"), 3)
        self.assertTrue((self.dir / reinicio.ESTADO_DEL_PUENTE).exists())

    def test_con_confirmar_hace_la_copia_vacia_todo_y_respeta_lo_demas(self) -> None:
        # Cubre: T-42
        self.preparar_archivos()
        with patch("anka.infra.reinicio.other_bridge_running", return_value=False):
            resultado = reinicio.reiniciar()
        self.assertTrue(Path(resultado["copia"]).exists())                            # la copia existe y es restaurable
        with closing(sqlite3.connect(self.dir / "quotes.sqlite3")) as db:
            for (tabla,) in db.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"):
                self.assertEqual(db.execute(f"SELECT COUNT(*) FROM {tabla}").fetchone()[0], 0, tabla)
        self.assertFalse((self.dir / reinicio.ESTADO_DEL_PUENTE).exists())
        for carpeta in reinicio.CARPETAS_DERIVADAS:
            self.assertFalse((self.dir / carpeta).exists(), carpeta)
        for conservado in ("config.json", "token.json", "evidence/m.eml"):
            self.assertTrue((self.dir / conservado).exists(), conservado)
        self.assertTrue(list((self.dir / "backups").glob("*.zip")))

    def test_la_copia_conserva_lo_que_se_borro(self) -> None:
        # Cubre: T-42
        with patch("anka.infra.reinicio.other_bridge_running", return_value=False):
            resultado = reinicio.reiniciar()
        restaurada = self.dir / "restaurada"
        restaurada.mkdir()
        import zipfile
        with zipfile.ZipFile(resultado["copia"]) as zf:
            zf.extract("quotes.sqlite3", restaurada)
        with closing(sqlite3.connect(restaurada / "quotes.sqlite3")) as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM quotes").fetchone()[0], 3)

    def test_se_niega_si_el_puente_esta_corriendo_y_no_borra_nada(self) -> None:
        # Cubre: T-42
        with patch("anka.infra.reinicio.other_bridge_running", return_value=True):
            with self.assertRaisesRegex(ErrorDeAnka, "puente está corriendo"):
                reinicio.reiniciar()
        self.assertEqual(self.cuenta("quotes"), 3)

    def test_si_la_copia_falla_no_borra_nada(self) -> None:
        # Cubre: T-42
        with patch("anka.infra.reinicio.other_bridge_running", return_value=False), \
                patch.object(backup, "create_backup", side_effect=OSError("disco lleno")):
            with self.assertRaises(OSError):
                reinicio.reiniciar()
        self.assertEqual(self.cuenta("quotes"), 3)

    def test_la_base_en_uso_no_se_vacia(self) -> None:
        # Cubre: T-42
        bloqueo = sqlite3.connect(self.dir / "quotes.sqlite3", timeout=1)
        self.addCleanup(bloqueo.close)
        bloqueo.execute("BEGIN IMMEDIATE")
        with patch("anka.infra.reinicio.other_bridge_running", return_value=False),                 patch.object(reinicio, "ESPERA_DE_LA_BASE", 0.1):
            with self.assertRaisesRegex(ErrorDeAnka, "base está en uso"):
                reinicio.reiniciar()
        bloqueo.rollback()
        self.assertEqual(self.cuenta("quotes"), 3)

if __name__ == "__main__":
    unittest.main()
