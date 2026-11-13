"""La lista de casos sale de un resumen guardado y no interpreta el análisis completo de cada caso (U-49, D-114).

Cubre: U-49
"""

from tests import _isolate  # noqa: F401  (debe ir primero)

import dataclasses
import json
import sqlite3
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from anka.dominio import caso as dominio_caso
from anka.dominio.caso import AnalisisPerezoso, resumen_de_analisis
from anka.dominio.status import QuoteStatus
from anka.infra import settings, store
from anka.servicios.repositorio import RepositorioDeCasos
from tests.test_app import complete_analysis


def analisis_variado(i: int) -> dict:
    """Análisis de formas distintas: con y sin remitente original, adjuntos, propuesta del modelo, preguntas enviadas…"""
    a = complete_analysis()
    a.update(project_name=f"Obra {i:03d} de prueba", received_at=f"2026-10-{1 + i % 9:02d}T09:00:00-05:00")
    if i % 3 == 0:
        a["routing"] = {"original_sender_email": f"origen{i}@ejemplo.test", "original_sender": f"Origen {i}"}
    if i % 4 == 0:
        a["attachments"] = [{"filename": f"plano{i}.pdf", "extracted_chars": 100 + i, "status": "leido"}]
    if i % 5 == 0:
        a["pending_model_activities"] = [{"activity": "Actividad nueva", "quantity": "3", "unit": "ha"}]
    if i % 6 == 0:
        a["preguntas_enviadas"] = {"en": "2026-10-02T10:00:00+00:00", "preguntas": [], "esperada": False}
    if i % 7 == 0:
        del a["received_at"]
    return a


class Base(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.dir = Path(self._tmp.name)
        patcher = patch.object(settings, "DATA_DIR", self.dir)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.db = store.open_db(self.dir)
        self.addCleanup(self.db.close)
        self.repo = RepositorioDeCasos()

    def sembrar(self, n: int) -> None:
        estados = [QuoteStatus.REVIEW_PENDING, QuoteStatus.WAITING, QuoteStatus.READY, QuoteStatus.RESPONSE_SENT,
                   QuoteStatus.IGNORED, QuoteStatus.REVIEW]
        for i in range(n):
            store.save(self.db, f"t-{i:04d}", f"m-{i}", estados[i % len(estados)], f"c{i}@ejemplo.test", analisis_variado(i))
        # casos de forma rara: análisis vacío y análisis dañado
        self.db.execute("UPDATE quotes SET analysis_json='{}' WHERE thread_id='t-0001'")
        self.db.execute("UPDATE quotes SET analysis_json='no es json' WHERE thread_id='t-0002'")
        if n > 3:
            self.db.execute("INSERT INTO quote_metadata (thread_id, client_name, client_type, sector, notes, updated_at) "
                            "VALUES ('t-0003', 'Cliente Confirmado SAS', 'Empresa privada', 'Energía', 'nota', '2026-10-01')")
        self.db.commit()


class Equivalencia(Base):
    def test_la_lista_es_igual_a_leer_el_analisis_completo_campo_por_campo(self) -> None:
        # Cubre: U-49
        self.sembrar(60)
        rapida = self.repo.listar(True)
        completa = self.repo.listar(True, completo=True)
        self.assertEqual([c.thread_id for c in rapida], [c.thread_id for c in completa])      # mismo orden
        self.assertEqual(len(rapida), 60)
        for a, b in zip(rapida, completa, strict=True):
            for campo in dataclasses.fields(a):
                with self.subTest(caso=a.thread_id, campo=campo.name):
                    self.assertEqual(getattr(a, campo.name), getattr(b, campo.name))
            self.assertEqual(dict(a.analysis), dict(b.analysis))
            self.assertEqual(dict(a.reception_assessment), dict(b.reception_assessment))
            self.assertEqual(bool(a.analysis), bool(b.analysis))

    def test_sin_ignorados_la_lista_tampoco_los_trae(self) -> None:
        # Cubre: U-49
        self.sembrar(24)
        self.assertEqual([c.thread_id for c in self.repo.listar()], [c.thread_id for c in self.repo.listar(completo=True)])
        self.assertTrue(all(c.status != QuoteStatus.IGNORED for c in self.repo.listar()))

    def test_el_orden_es_el_de_siempre_con_fechas_vacias_y_empates(self) -> None:
        # Cubre: U-49
        self.sembrar(12)
        self.db.execute("UPDATE quotes SET updated_at=NULL, first_seen_at=NULL WHERE thread_id='t-0005'")
        self.db.execute("UPDATE quotes SET updated_at='2026-10-05T10:00:00' WHERE thread_id IN ('t-0006', 't-0007', 't-0008')")
        self.db.execute("UPDATE quotes SET updated_at=NULL, first_seen_at='2026-10-05T10:00:00' WHERE thread_id='t-0009'")
        self.db.commit()
        con_sql = [r[0] for r in self.db.execute(
            "SELECT thread_id FROM quotes WHERE status <> 'ignored' "
            "ORDER BY COALESCE(updated_at, first_seen_at, '') DESC, thread_id DESC")]
        self.assertEqual([c.thread_id for c in self.repo.listar()], con_sql)

    def test_el_detalle_sigue_leyendo_el_analisis_completo(self) -> None:
        # Cubre: U-49
        self.sembrar(6)
        detalle = self.repo.obtener("t-0000")
        assert detalle is not None
        self.assertIsInstance(detalle.analysis, dict)                      # un diccionario de verdad, no el perezoso
        self.assertTrue(detalle.analysis["activities"])
        self.assertEqual(detalle.analysis, json.loads(
            self.db.execute("SELECT analysis_json FROM quotes WHERE thread_id='t-0000'").fetchone()[0]))
        de_lista = next(c for c in self.repo.listar(True) if c.thread_id == "t-0000")
        self.assertEqual(dict(de_lista.analysis), detalle.analysis)        # leer `activities` en la lista da lo mismo
        self.assertIsNone(self.repo.obtener("no-existe"))


class NoLeeElAnalisis(Base):
    def test_una_lista_no_lee_el_analisis_completo_hasta_que_alguien_lo_pide(self) -> None:
        # Cubre: U-49
        self.sembrar(30)
        lecturas = []
        original = RepositorioDeCasos._analisis_de
        with patch.object(RepositorioDeCasos, "_analisis_de", lambda self, hilo: lecturas.append(hilo) or original(self, hilo)):
            casos = self.repo.listar()
            for c in casos:                                                 # lo que pintan las listas
                c.analysis.get("project_name"), c.analysis.get("received_at"), c.analysis.get("routing")
                c.analysis.get("attachments"), c.analysis.get("preguntas_enviadas")
                bool(c.analysis), c.services, c.proposal, c.status_label, c.client_email, c.waiting_since
            self.assertEqual(lecturas, [])
            self.assertFalse(any(c.analysis.cargado for c in casos))
            primero = casos[0]
            self.assertTrue(primero.analysis["activities"])               # otra clave: se lee ese caso, y solo ese
            self.assertEqual(lecturas, [primero.thread_id])
            primero.analysis.get("activities")
            self.assertEqual(lecturas, [primero.thread_id])               # una sola vez
            self.assertTrue(primero.reception_assessment["status"])       # la evaluación se calcula al mirarla
            self.assertEqual(len(lecturas), 1)

    def test_no_interpreta_ningun_json_de_analisis_al_listar(self) -> None:
        # Cubre: U-49
        self.sembrar(30)
        self.repo.listar()                                                   # la primera vez puede completar resúmenes
        llamadas = []
        original = dominio_caso.load_analysis
        with patch("anka.servicios.repositorio.load_analysis", lambda v: llamadas.append(1) or original(v)):
            self.repo.listar()
        self.assertEqual(llamadas, [])

    def test_5000_casos_se_listan_rapido_con_los_resumenes_puestos(self) -> None:
        # Cubre: U-49
        act = [{"id": i, "descripcion": "x" * 80, "ubicacion": "y" * 40} for i in range(25)]
        self.db.execute("BEGIN")
        for i in range(5000):
            a = json.dumps({"activities": act, "summary": "s" * 500, "received_at": "2026-01-01T00:00:00-05:00",
                            "project_name": f"Obra {i}"})
            self.db.execute("INSERT INTO quotes(thread_id,last_inbound_id,status,recipient,analysis_json,first_seen_at,updated_at)"
                            " VALUES(?,?,?,?,?,?,?)", (f"t{i:05d}", f"m{i}", "review", "c@x.com", a,
                                                       "2026-01-01T00:00:00-05:00", f"2026-0{1 + i % 9}-01T00:00:00-05:00"))
        self.db.commit()
        self.repo.listar()                                                   # rellena los resúmenes que faltan
        mejor = 99.0
        for _ in range(3):
            inicio = time.perf_counter()
            casos = self.repo.listar()
            mejor = min(mejor, time.perf_counter() - inicio)
        self.assertEqual(len(casos), 5000)
        self.assertLess(mejor, 1.2, "umbral holgado: antes de U-49 era ~0,8 s en una máquina de desarrollo; aquí solo se evita lo cuadrático/completo")


class MigracionYResumen(Base):
    def resumenes_faltantes(self) -> int:
        return self.db.execute("SELECT COUNT(*) FROM quotes WHERE resumen_json IS NULL OR resumen_v IS NOT ?",
                               (dominio_caso.VERSION_DEL_RESUMEN,)).fetchone()[0]

    def test_una_base_en_el_esquema_17_pasa_al_18_con_todos_los_resumenes(self) -> None:
        # Cubre: U-49
        self.sembrar(20)
        self.db.execute("DROP TRIGGER trg_quotes_resumen_vencido")
        self.db.execute("ALTER TABLE quotes DROP COLUMN resumen_json")
        self.db.execute("ALTER TABLE quotes DROP COLUMN resumen_v")
        self.db.execute("PRAGMA user_version = 17")
        self.db.commit()
        self.db.close()
        self.db = store.open_db(self.dir)                                      # migra de 17 a 18
        self.addCleanup(self.db.close)
        self.assertEqual(self.db.execute("PRAGMA user_version").fetchone()[0], store.SCHEMA_VERSION)
        self.assertGreaterEqual(store.SCHEMA_VERSION, 18)
        self.assertEqual(self.db.execute("SELECT COUNT(*) FROM quotes").fetchone()[0], 20)
        self.assertEqual(self.resumenes_faltantes(), 0)
        fila = self.db.execute("SELECT resumen_json FROM quotes WHERE thread_id='t-0000'").fetchone()[0]
        self.assertEqual(json.loads(fila)["claves"]["project_name"], "Obra 000 de prueba")

    def test_la_migracion_es_idempotente(self) -> None:
        # Cubre: U-49
        self.sembrar(10)
        store._migration_18_resumen_de_casos(self.db)                           # la primera vez rellena los casos sin resumen
        self.db.commit()
        self.assertEqual(self.resumenes_faltantes(), 0)
        foto = [tuple(r) for r in self.db.execute("SELECT thread_id, resumen_json, resumen_v FROM quotes ORDER BY thread_id")]
        for _ in range(2):
            store._migration_18_resumen_de_casos(self.db)                       # repetirla no falla ni cambia nada
        self.db.commit()
        self.assertEqual(foto, [tuple(r) for r in self.db.execute(
            "SELECT thread_id, resumen_json, resumen_v FROM quotes ORDER BY thread_id")])
        triggers = [r[0] for r in self.db.execute("SELECT name FROM sqlite_master WHERE type='trigger'")]
        self.assertEqual(triggers.count("trg_quotes_resumen_vencido"), 1)

    def test_el_resumen_guardado_es_el_que_se_calcula_del_analisis(self) -> None:
        # Cubre: U-49
        self.sembrar(10)
        self.repo.listar(True)
        for hilo, texto, guardado in self.db.execute("SELECT thread_id, analysis_json, resumen_json FROM quotes").fetchall():
            self.assertEqual(json.loads(guardado), resumen_de_analisis(dominio_caso.load_analysis(texto)), hilo)

    def test_cambiar_el_analisis_borra_el_resumen_y_la_lista_lo_recalcula(self) -> None:
        # Cubre: U-49
        self.sembrar(6)
        self.repo.listar(True)
        self.assertEqual(self.resumenes_faltantes(), 0)
        nuevo = analisis_variado(0)
        nuevo["project_name"] = "Nombre nuevo"
        store.save(self.db, "t-0000", "m-9", QuoteStatus.REVIEW_PENDING, "c0@ejemplo.test", nuevo)    # por el camino normal
        self.assertIsNone(self.db.execute("SELECT resumen_json FROM quotes WHERE thread_id='t-0000'").fetchone()[0])
        self.db.execute("UPDATE quotes SET analysis_json=? WHERE thread_id='t-0001'", (json.dumps({"project_name": "Directo"}),))
        self.db.commit()                                                       # y por una escritura directa cualquiera
        self.assertEqual(self.resumenes_faltantes(), 2)
        por_hilo = {c.thread_id: c for c in self.repo.listar(True)}
        self.assertEqual(por_hilo["t-0000"].analysis.get("project_name"), "Nombre nuevo")
        self.assertEqual(por_hilo["t-0001"].analysis.get("project_name"), "Directo")
        self.assertEqual(self.resumenes_faltantes(), 0)                        # la lista dejó guardados los resúmenes

    def test_escribir_el_mismo_analisis_no_borra_el_resumen_ni_mueve_el_marcador(self) -> None:
        # Cubre: U-49
        self.sembrar(3)
        self.repo.listar(True)
        marcador = store.change_marker(self.db) if hasattr(store, "change_marker") else None
        self.db.execute("UPDATE quotes SET analysis_json=analysis_json")
        self.db.commit()
        self.assertEqual(self.resumenes_faltantes(), 0)
        if marcador is not None:
            self.assertEqual(store.change_marker(self.db), marcador)

    def test_un_resumen_de_otra_version_se_recalcula(self) -> None:
        # Cubre: U-49
        self.sembrar(4)
        self.repo.listar(True)
        self.db.execute("UPDATE quotes SET resumen_v = 0, resumen_json = '{\"v\": 0, \"vacio\": true, \"claves\": {}, "
                        "\"servicios\": [], \"propuesta\": []}'")
        self.db.commit()
        casos = self.repo.listar(True)
        self.assertTrue(any(c.analysis.get("project_name") for c in casos))      # no usó el resumen viejo
        self.assertEqual(self.resumenes_faltantes(), 0)

    def test_con_la_base_ocupada_la_lista_sale_igual_sin_esperar(self) -> None:
        # Cubre: U-49
        self.sembrar(8)
        self.db.execute("UPDATE quotes SET resumen_json=NULL, resumen_v=NULL")
        self.db.commit()
        propia = store.open_db(self.dir)                                         # la conexión se abre antes de que alguien escriba
        self.addCleanup(propia.close)
        repo = RepositorioDeCasos(lambda: propia)
        otro = sqlite3.connect(self.dir / "quotes.sqlite3", isolation_level=None)
        self.addCleanup(otro.close)
        otro.execute("BEGIN IMMEDIATE")                                          # alguien escribe: nadie más puede
        inicio = time.perf_counter()
        casos = repo.listar(True)
        self.assertLess(time.perf_counter() - inicio, 5, "no debe esperar los 15 s del candado")
        self.assertEqual(len(casos), 8)
        self.assertEqual(sum(bool(c.analysis.get("project_name")) for c in casos), 6)   # sale con lo calculado (2 sin análisis)
        otro.execute("ROLLBACK")
        self.db.rollback()
        self.assertEqual(self.resumenes_faltantes(), 8)                          # no alcanzó a guardarlos
        self.repo.listar(True)
        self.assertEqual(self.resumenes_faltantes(), 0)                          # la siguiente vez sí

    def test_la_derivacion_de_novedades_conserva_el_orden_por_rowid(self) -> None:
        # Cubre: U-49
        for i, estado in enumerate([QuoteStatus.REVIEW_PENDING, QuoteStatus.REVIEW, QuoteStatus.DRAFT_CREATED] * 4):
            store.save(self.db, f"t-{i:02d}", f"m-{i}", estado, "a@x.co", complete_analysis())
        self.db.commit()
        self.db.execute("DELETE FROM dashboard_notifications")
        store._derive_notifications(self.db)
        self.db.commit()
        orden = [r[0] for r in self.db.execute(
            "SELECT thread_id FROM dashboard_notifications WHERE kind='draft_suggested' ORDER BY id")]
        self.assertEqual(orden, sorted(orden))


class ElAnalisisPerezoso(unittest.TestCase):
    def test_se_comporta_como_el_diccionario(self) -> None:
        # Cubre: U-49
        completo = {"project_name": "P", "activities": [1, 2], "extra": True}
        cargas = []
        a = AnalisisPerezoso({"project_name": "P"}, False, lambda: cargas.append(1) or completo)  # type: ignore[arg-type]
        self.assertEqual(a.get("project_name"), "P")
        self.assertIn("project_name", a)
        self.assertNotIn("received_at", a)                       # una clave de lista que no estaba: ausente, sin leer
        self.assertIsNone(a.get("received_at"))
        self.assertTrue(a)
        self.assertEqual(cargas, [])
        self.assertEqual(a["activities"], [1, 2])
        self.assertEqual(len(a), 3)
        self.assertEqual(dict(a), completo)
        self.assertEqual(a, completo)
        self.assertEqual(cargas, [1])
        with self.assertRaises(KeyError):
            a["no_existe"]
        self.assertFalse(AnalisisPerezoso({}, True, lambda: {}))  # type: ignore[arg-type]


if __name__ == "__main__":
    unittest.main()
