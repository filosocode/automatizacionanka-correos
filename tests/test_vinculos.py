"""Un correo pertenece a un solo caso; también se pueden descartar vínculos y aislar pruebas."""

from tests import _isolate  # noqa: F401  (debe ir primero)

import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from anka.dominio.status import QuoteStatus
from anka.infra import settings, store
from anka.servicios import quotes
from anka.servicios.repositorio import RepositorioDeCasos

PROYECTOS = {"t-a": "Línea Norte", "t-b": "Volumetría mensual cantera Los Pinos", "t-c": "Embalse"}


def add_link(db, thread, message, state="pending", reason="sender"):
    db.execute(
        """INSERT INTO case_links (thread_id, other_thread_id, message_id, sender_email, sender_name, subject,
                                    snippet, reason, state, occurred_at, has_code)
           VALUES (?, 'otro-hilo', ?, 'ana@acme.com', 'Ana', 'Precisión técnica', 'ajustes', ?, ?, ?, 0)""",
        (thread, message, reason, state, "2026-10-02T10:00:00-05:00"))
    store.add_dashboard_notification(db, thread, "possible_update", message, "Posible actualización", "Ana escribió")


# Cubre: T-18, T-20
class OneEmailOneCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.folder = Path(self._tmp.name)
        patcher = patch.object(settings, "DATA_DIR", self.folder)
        patcher.start()
        self.addCleanup(patcher.stop)
        db = store.open_db(self.folder)
        for thread, nombre in PROYECTOS.items():
            store.save(db, thread, f"m-{thread}", QuoteStatus.WAITING, "ana@acme.com", {"project_name": nombre})
            add_link(db, thread, "correo-1", reason="sender+subject" if thread == "t-b" else "sender")
        add_link(db, "t-a", "correo-2")                                  # otro correo, otro aviso: no debe tocarse
        db.commit()
        db.close()
        self.repo = RepositorioDeCasos()

    def link_id(self, thread, message="correo-1"):
        db = store.open_db(self.folder)
        try:
            return db.execute("SELECT id FROM case_links WHERE thread_id=? AND message_id=?", (thread, message)).fetchone()[0]
        finally:
            db.close()

    def states(self, message="correo-1"):
        db = store.open_db(self.folder)
        try:
            return {r["thread_id"]: r["state"] for r in db.execute(
                "SELECT thread_id, state FROM case_links WHERE message_id=?", (message,))}
        finally:
            db.close()

    def test_confirming_in_one_case_closes_the_same_notice_in_the_others(self):
        mensaje = quotes.resolve_link("t-b", self.link_id("t-b"), accept=True)
        self.assertEqual(self.states(), {"t-a": "assigned_elsewhere", "t-b": "linked", "t-c": "assigned_elsewhere"})
        self.assertIn("2 caso(s) más", mensaje)                          # se le dice cuántos avisos se cerraron
        self.assertEqual([v.message_id for v in self.repo.obtener("t-a").links], ["correo-2"])   # solo queda el otro correo
        self.assertEqual(self.repo.obtener("t-c").links, [])             # ya no se ve como posible actualización

    def test_the_other_cases_keep_a_trace_of_where_the_email_went(self):
        quotes.resolve_link("t-b", self.link_id("t-b"), accept=True)
        eventos = [e for e in self.repo.eventos("t-a") if e["kind"] == "link_assigned"]
        self.assertEqual(len(eventos), 1)
        self.assertIn("Volumetría mensual cantera Los Pinos", eventos[0]["summary"])
        self.assertIn("ya no es una posible actualización de este", eventos[0]["summary"])
        self.assertEqual(eventos[0]["actor_email"], "ana@acme.com")      # queda quién escribió

    def test_their_notifications_are_marked_as_read(self):
        quotes.resolve_link("t-b", self.link_id("t-b"), accept=True)
        db = store.open_db(self.folder)
        try:
            pendientes = {r["thread_id"] for r in db.execute(
                "SELECT thread_id FROM dashboard_notifications WHERE kind='possible_update' AND read_at IS NULL "
                "AND source_key='correo-1'")}
        finally:
            db.close()
        self.assertEqual(pendientes, {"t-b"}.intersection(pendientes))   # ni t-a ni t-c quedan con aviso sin leer
        self.assertNotIn("t-a", pendientes)
        self.assertNotIn("t-c", pendientes)

    def test_a_different_email_of_the_same_sender_is_not_touched(self):
        quotes.resolve_link("t-b", self.link_id("t-b"), accept=True)
        self.assertEqual(self.states("correo-2"), {"t-a": "pending"})

    def test_dismissing_in_one_case_does_not_close_the_others(self):
        quotes.resolve_link("t-a", self.link_id("t-a"), accept=False)
        self.assertEqual(self.states(), {"t-a": "dismissed", "t-b": "pending", "t-c": "pending"})

    def test_bulk_dismiss_resolves_selected_links_without_merging_or_deleting_cases(self):
        ids = [self.link_id("t-a", "correo-1"), self.link_id("t-a", "correo-2")]
        result = quotes.dismiss_pending_links("t-a", ids)
        self.assertIn("Descartaste 2", result)
        self.assertEqual(self.states("correo-1"), {"t-a": "dismissed", "t-b": "pending", "t-c": "pending"})
        self.assertEqual(self.states("correo-2"), {"t-a": "dismissed"})
        self.assertEqual(self.repo.obtener("t-a").status, QuoteStatus.WAITING)
        self.assertEqual(self.repo.obtener("t-b").status, QuoteStatus.WAITING)

    def test_bulk_dismiss_requires_selection(self):
        self.assertIn("Selecciona al menos una", quotes.dismiss_pending_links("t-a", []))

    def test_bulk_dismiss_can_resolve_suggestions_across_cases(self):
        refs = [("t-a", self.link_id("t-a")), ("t-b", self.link_id("t-b"))]
        result = quotes.dismiss_pending_links_many(refs)
        self.assertIn("Descartaste 2", result)
        self.assertEqual(self.states(), {"t-a": "dismissed", "t-b": "dismissed", "t-c": "pending"})

    def test_a_notice_already_closed_elsewhere_is_not_reopened_or_recounted(self):
        db = store.open_db(self.folder)
        db.execute("UPDATE case_links SET state='dismissed' WHERE thread_id='t-c' AND message_id='correo-1'")
        db.commit()
        db.close()
        mensaje = quotes.resolve_link("t-b", self.link_id("t-b"), accept=True)
        self.assertEqual(self.states()["t-c"], "dismissed")              # lo que una persona ya descartó se respeta
        self.assertIn("1 caso(s) más", mensaje)

    def test_a_closed_notice_does_not_reopen_a_finished_case(self):
        self.assertTrue(self.repo.tiene_vinculos("t-a"))                 # aún tiene avisos pendientes
        quotes.resolve_link("t-b", self.link_id("t-b"), accept=True)
        db = store.open_db(self.folder)
        db.execute("DELETE FROM case_links WHERE thread_id='t-c' AND message_id<>'correo-1'")
        db.commit()
        db.close()
        self.assertFalse(self.repo.tiene_vinculos("t-c"))               # asignado a otro caso: nada nuevo aquí
        self.assertTrue(self.repo.tiene_vinculos("t-b"))                 # el caso donde sí se confirmó, sí avanzó


# Cubre: T-18
class MergeOwnCase(OneEmailOneCase):
    """El correo confirmado ya había creado su propio caso (hilo nuevo): se une al caso elegido."""

    def setUp(self):
        super().setUp()
        db = store.open_db(self.folder)
        store.save(db, "otro-hilo", "m-otro", QuoteStatus.WAITING, "ana@acme.com", {"project_name": "Los Pinos - precisión"})
        db.commit()
        db.close()

    def status(self, thread):
        db = store.open_db(self.folder)
        try:
            return db.execute("SELECT status FROM quotes WHERE thread_id=?", (thread,)).fetchone()[0]
        finally:
            db.close()

    def test_the_case_born_from_that_thread_is_merged_and_can_be_rescued(self):
        mensaje = quotes.resolve_link("t-b", self.link_id("t-b"), accept=True)
        self.assertEqual(self.status("otro-hilo"), QuoteStatus.IGNORED)
        self.assertIn("se unió a este", mensaje)
        caso = self.repo.obtener("otro-hilo")
        self.assertIn("Unido al caso «Volumetría mensual cantera Los Pinos»", caso.analysis["ignored_reason"])
        self.assertEqual(caso.analysis["merged_into"], "t-b")
        self.assertIn("otro-hilo", [d["thread_id"] for d in self.repo.descartados()])   # sigue ahí, rescatable
        self.assertTrue([e for e in self.repo.eventos("otro-hilo") if e["kind"] == "merged"])

    def set_activities(self, thread, activities):
        db = store.open_db(self.folder)
        analysis = json.loads(db.execute("SELECT analysis_json FROM quotes WHERE thread_id=?", (thread,)).fetchone()[0])
        analysis["activities"] = activities
        db.execute("UPDATE quotes SET analysis_json=? WHERE thread_id=?", (json.dumps(analysis), thread))
        db.commit()
        db.close()

    def test_what_the_merged_email_brought_becomes_a_proposal_without_losing_rows(self):
        self.set_activities("t-b", [{"activity": "Vuelo", "quantity": "10", "unit": "ha"},
                                    {"activity": "Informe", "quantity": "1", "unit": "un"}])
        self.set_activities("otro-hilo", [{"activity": "Vuelo", "quantity": "15", "unit": "ha"},
                                          {"activity": "Modelo 3D", "quantity": "1", "unit": "un"}])
        mensaje = quotes.resolve_link("t-b", self.link_id("t-b"), accept=True)
        caso = self.repo.obtener("t-b")
        self.assertEqual([a["quantity"] for a in caso.analysis["activities"]], ["10", "1"])      # su tabla no se toca
        propuesta = {a["activity"]: a["quantity"] for a in caso.analysis["pending_model_activities"]}
        self.assertEqual(propuesta, {"Vuelo": "15", "Informe": "1", "Modelo 3D": "1"})           # Informe no se pierde
        self.assertIn("propuesta", mensaje)
        quotes.resolve_model_proposal("t-b", True)                                                # adoptarla es un clic
        self.assertEqual(len(self.repo.obtener("t-b").analysis["activities"]), 3)

    def test_no_proposal_when_the_merged_email_changes_nothing(self):
        self.set_activities("t-b", [{"activity": "Vuelo", "quantity": "10", "unit": "ha"}])
        self.set_activities("otro-hilo", [{"activity": "vuelo", "quantity": "10", "unit": "ha"}])
        quotes.resolve_link("t-b", self.link_id("t-b"), accept=True)
        self.assertNotIn("pending_model_activities", self.repo.obtener("t-b").analysis)

    def test_a_case_that_already_advanced_is_never_merged(self):
        db = store.open_db(self.folder)
        db.execute("UPDATE quotes SET quote_code='26010' WHERE thread_id='otro-hilo'")
        db.commit()
        db.close()
        mensaje = quotes.resolve_link("t-b", self.link_id("t-b"), accept=True)
        self.assertEqual(self.status("otro-hilo"), QuoteStatus.WAITING)
        self.assertNotIn("se unió a este", mensaje)

    def test_dismissing_does_not_merge_anything(self):
        quotes.resolve_link("t-b", self.link_id("t-b"), accept=False)
        self.assertEqual(self.status("otro-hilo"), QuoteStatus.WAITING)


# Cubre: T-18
class MigrationCleansWhatWasAlreadyRepeated(unittest.TestCase):
    def test_pending_notices_for_an_email_already_confirmed_elsewhere_are_closed(self):
        with tempfile.TemporaryDirectory() as d:
            db = store.open_db(Path(d))
            for thread in ("t-a", "t-b"):
                store.save(db, thread, f"m-{thread}", QuoteStatus.WAITING, "ana@acme.com", {})
                add_link(db, thread, "correo-1", state="linked" if thread == "t-b" else "pending")
            add_link(db, "t-a", "correo-2")                              # sin confirmar en ningún caso: sigue pendiente
            db.commit()
            db.execute("PRAGMA user_version = 10")                       # como la base que ya existía antes de este arreglo
            db.commit()
            db.close()
            db = store.open_db(Path(d))                                  # la migración 11 corre al abrir
            db.row_factory = sqlite3.Row
            try:
                estados = {(r["thread_id"], r["message_id"]): r["state"] for r in db.execute(
                    "SELECT thread_id, message_id, state FROM case_links")}
                sin_leer = {r["source_key"] for r in db.execute(
                    "SELECT source_key FROM dashboard_notifications WHERE thread_id='t-a' AND kind='possible_update' "
                    "AND read_at IS NULL")}
            finally:
                db.close()
        self.assertEqual(estados[("t-a", "correo-1")], "assigned_elsewhere")
        self.assertEqual(estados[("t-b", "correo-1")], "linked")
        self.assertEqual(estados[("t-a", "correo-2")], "pending")
        self.assertEqual(sin_leer, {"correo-2"})                         # el aviso del correo ya asignado se marcó leído


if __name__ == "__main__":
    unittest.main()
