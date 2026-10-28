"""Una sola forma de escribir: `transaccion()` confirma o revierte todo, `lectura()` solo lee (U-64).

Cubre: U-64
"""

from tests import _isolate  # noqa: F401  (debe ir primero)

import ast
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from anka.dominio.status import QuoteStatus
from anka.infra import settings, store
from anka.infra.store import lectura, request_scope, transaccion

RAIZ = Path(__file__).resolve().parents[1]


class Base(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        parche = patch.object(settings, "DATA_DIR", self.root)
        parche.start()
        self.addCleanup(parche.stop)
        store.open_db(self.root).close()

    def cuantas(self, tabla="dashboard_notifications"):
        with lectura() as db:
            return db.execute(f"SELECT COUNT(*) FROM {tabla}").fetchone()[0]

    def anotar(self, db, clave):
        """Una escritura sin confirmar (`store.save` confirma por su cuenta, así que aquí va SQL directo)."""
        db.execute("INSERT INTO quotes (thread_id, last_inbound_id, status, recipient, analysis_json) "
                   "VALUES (?, 'm', ?, 'a@b.co', '{}')", (f"T-{clave}", QuoteStatus.REVIEW))


# Cubre: U-64
class LaUnidadDeTrabajo(Base):
    def test_confirma_todo_al_salir_bien(self):
        with transaccion() as db:
            self.anotar(db, 1)
            self.anotar(db, 2)
        self.assertEqual(self.cuantas("quotes"), 2)

    def test_revierte_todo_si_algo_falla_a_medias(self):
        with self.assertRaises(RuntimeError):
            with transaccion() as db:
                self.anotar(db, 1)
                raise RuntimeError("se cayó a la mitad")
        self.assertEqual(self.cuantas("quotes"), 0)

    def test_revierte_tambien_con_una_interrupcion(self):
        with self.assertRaises(KeyboardInterrupt):
            with transaccion() as db:
                self.anotar(db, 1)
                raise KeyboardInterrupt
        self.assertEqual(self.cuantas("quotes"), 0)

    def test_toma_el_candado_de_escritura_al_entrar(self):
        with transaccion():
            otro = sqlite3.connect(self.root / "quotes.sqlite3", timeout=0.2)
            self.addCleanup(otro.close)
            with self.assertRaises(sqlite3.OperationalError):
                otro.execute("BEGIN IMMEDIATE")

    def test_sin_inmediata_el_candado_llega_con_la_primera_escritura(self):
        with transaccion(inmediata=False) as db:
            otro = sqlite3.connect(self.root / "quotes.sqlite3", timeout=0.2)
            self.addCleanup(otro.close)
            otro.execute("BEGIN IMMEDIATE")          # todavía nadie escribió
            otro.rollback()
            self.anotar(db, 1)

    def test_libera_la_conexion_y_el_candado_al_terminar(self):
        with transaccion() as db:
            self.anotar(db, 1)
        otro = sqlite3.connect(self.root / "quotes.sqlite3", timeout=0.2)
        self.addCleanup(otro.close)
        otro.execute("BEGIN IMMEDIATE")
        otro.rollback()

    def test_dentro_de_una_peticion_comparte_la_conexion_y_confirma_igual(self):
        with request_scope():
            with transaccion() as db:
                self.anotar(db, 1)
            with self.assertRaises(RuntimeError):
                with transaccion() as db:
                    self.anotar(db, 2)
                    raise RuntimeError("falla")
            self.assertEqual(self.cuantas("quotes"), 1)         # el primero quedó; el segundo se revirtió
        self.assertEqual(self.cuantas("quotes"), 1)

    def test_anidar_en_una_peticion_no_deja_cambios_sin_confirmar_a_la_siguiente_funcion(self):
        with request_scope():
            with lectura() as db:
                db.execute("UPDATE quotes SET status='x'")
            with lectura() as db:
                self.assertFalse(db.in_transaction)


# Cubre: U-64
class SoloLectura(Base):
    def test_lo_que_se_deja_sin_confirmar_se_descarta(self):
        with lectura() as db:
            self.anotar(db, 1)                  # una escritura olvidada en una lectura no llega a la base
        self.assertEqual(self.cuantas("quotes"), 0)

    def test_devuelve_filas(self):
        with transaccion() as db:
            self.anotar(db, 1)
        with lectura() as db:
            self.assertEqual(db.execute("SELECT thread_id FROM quotes").fetchone()["thread_id"], "T-1")


# Cubre: U-64
class NoSeVuelveAlConnectDisperso(unittest.TestCase):
    # Lo que queda con `connect()` + `commit()` a mano fuera de `store.py` (lista de pendientes de U-64; solo puede bajar).
    PENDIENTES = {
        "anka/servicios/case_actions.py": 1,      # approve_review: pasa la conexión a _send_questions/_create_case_folder
        "anka/servicios/change_control.py": 1,    # origen_de_destinatario: usa la conexión que le pasan o abre una de lectura
        "anka/servicios/gmail_followup.py": 1,    # sync_thread_activity: lee Gmail dentro y confirma por tandas
        "anka/servicios/pipeline.py": 1,          # el ciclo de revisión comparte una conexión durante toda la pasada
    }

    def usos(self):
        encontrados = {}
        for ruta in (RAIZ / "anka").rglob("*.py"):
            relativa = ruta.relative_to(RAIZ).as_posix()
            if relativa == "anka/infra/store.py":
                continue
            arbol = ast.parse(ruta.read_text(encoding="utf-8"))
            cuantos = sum(
                1 for nodo in ast.walk(arbol)
                if isinstance(nodo, ast.Assign) and isinstance(nodo.value, ast.IfExp | ast.Call)
                and any(isinstance(llamada, ast.Call) and getattr(llamada.func, "id", "") == "connect"
                        for llamada in ast.walk(nodo.value)))
            if cuantos:
                encontrados[relativa] = cuantos
        return encontrados

    def test_los_servicios_no_abren_una_conexion_a_mano_salvo_los_pendientes(self):
        for ruta, cuantos in self.usos().items():
            self.assertLessEqual(cuantos, self.PENDIENTES.get(ruta, 0),
                                 f"{ruta} abre la base con connect(): usa `with transaccion()` o `with lectura()`")

    def test_la_lista_de_pendientes_no_tiene_nada_que_ya_se_migro(self):
        usos = self.usos()
        for ruta, cuantos in self.PENDIENTES.items():
            self.assertEqual(usos.get(ruta, 0), cuantos, f"{ruta} ya se migró: bájalo de PENDIENTES")


if __name__ == "__main__":
    unittest.main()
