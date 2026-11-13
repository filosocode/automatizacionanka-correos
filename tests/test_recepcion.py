from tests import _isolate  # noqa: F401

import tempfile
import multiprocessing
import sqlite3
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import patch

from anka.infra import settings, store
from anka.servicios import recepcion


class Ejecutable:
    def __init__(self, value):
        self.value = value

    def execute(self):
        if isinstance(self.value, BaseException):
            raise self.value
        return self.value


class NotFound(Exception):
    status_code = 404


def message(message_id="m1", thread_id="t1", sender="cliente@example.com", labels=None):
    return {"id": message_id, "threadId": thread_id, "labelIds": labels or [], "internalDate": "1792164600000",
            "snippet": "Solicitud de información", "payload": {"headers": [
                {"name": "From", "value": f"Cliente <{sender}>"},
                {"name": "To", "value": "ankacomercialia@gmail.com"},
                {"name": "Subject", "value": "Consulta topográfica"},
                {"name": "Date", "value": "Fri, 16 Oct 2026 10:00:00 -0500"},
            ], "mimeType": "multipart/mixed", "parts": [{"filename": "plano.pdf", "mimeType": "application/pdf",
                                                             "body": {"attachmentId": "a1", "size": 123}}]}}


class Mensajes:
    def __init__(self, gmail):
        self.gmail = gmail

    def get(self, userId, id, format):
        self.gmail.calls.append(("messages.get", id))
        return Ejecutable(self.gmail.mail_items.get(id, NotFound("missing")))

    def list(self, **kwargs):
        self.gmail.calls.append(("messages.list", kwargs))
        if self.gmail.list_result is None:
            raise AssertionError("No se esperaba backfill")
        if isinstance(self.gmail.list_result, list):
            return Ejecutable(self.gmail.list_result.pop(0))
        return Ejecutable(self.gmail.list_result)


class History:
    def __init__(self, gmail):
        self.gmail = gmail

    def list(self, **kwargs):
        self.gmail.calls.append(("history.list", kwargs))
        if self.gmail.history_wait:
            self.gmail.history_wait.set()
            self.gmail.history_release.wait(2)
        if self.gmail.history_results:
            result = self.gmail.history_results.pop(0)
            if result.get("historyId"):
                self.gmail.history_cursor = str(result["historyId"])
            return Ejecutable(result)
        if self.gmail.history_error:
            return Ejecutable(self.gmail.history_error)
        result = {"historyId": str(max(int(self.gmail.history_cursor) + 1, 110)), "history": []}
        self.gmail.history_cursor = result["historyId"]
        return Ejecutable(result)


class Gmail:
    def __init__(self):
        self.history_cursor = "100"
        self.history_results = []
        self.history_error = None
        self.mail_items = {}
        self.list_result = None
        self.calls = []
        self.history_wait = None
        self.history_release = threading.Event()

    def users(self):
        return self

    def getProfile(self, userId):
        self.calls.append(("getProfile", userId))
        return Ejecutable({"emailAddress": recepcion.CUENTA, "historyId": self.history_cursor})

    def history(self):
        return History(self)

    def messages(self):
        return Mensajes(self)


def event(*messages):
    return {"historyId": "110", "history": [{"id": "105", "messagesAdded": [
        {"message": {"id": item["id"], "threadId": item["threadId"]}} for item in messages]}]}


def _process_lease_worker(directory, ready, start, release, results):
    import time
    settings.DATA_DIR = Path(directory)
    store.open_db(Path(directory)).close()
    ready.put(True)
    start.wait(5)
    token = recepcion._acquire(recepcion.CUENTA, time.time(), "2026-10-16T15:00:00+00:00")
    results.put(bool(token))
    if token:
        release.wait(5)
        recepcion._release(recepcion.CUENTA, token)


# Cubre: U-81
class TriggerGmailTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.directory = Path(self.temp.name)
        self.config = {"own_addresses": [recepcion.CUENTA]}
        self.when = datetime(2026, 10, 16, 15, tzinfo=UTC)
        self.gmail = Gmail()
        self._lock = patch.object(recepcion, "_sondeo_lock", threading.Lock())
        self._lock.start()
        self.addCleanup(self._lock.stop)
        self._data = patch.object(settings, "DATA_DIR", self.directory)
        self._data.start()
        self.addCleanup(self._data.stop)

    def scan(self, gmail=None, now=None):
        return recepcion.sondear(gmail or self.gmail, self.config, now or self.when)

    def initialize(self):
        return self.scan()

    def confirm_history_sync(self):
        return self.scan(self.gmail, self.when + timedelta(minutes=1))

    def rows(self):
        with store.lectura() as db:
            return [dict(row) for row in db.execute("SELECT * FROM recepcion_mensajes ORDER BY message_id")]

    def test_first_run_establishes_cursor_without_importing_mailbox_history(self):
        result = self.scan()
        self.assertEqual(result, {"status": "inicializado_sin_historial", "discovered": 0})
        self.assertFalse(any(name in ("history.list", "messages.list", "messages.get") for name, _ in self.gmail.calls))
        with store.lectura() as db:
            self.assertEqual(db.execute("SELECT history_id FROM recepcion_control").fetchone()[0], "100")
            self.assertEqual(db.execute("SELECT COUNT(*) FROM recepcion_mensajes").fetchone()[0], 0)

    def test_migration_22_adds_recovery_and_batch_controls_in_isolated_database(self):
        self.initialize()
        with store.lectura() as db:
            columns = {row["name"] for row in db.execute("PRAGMA table_info(recepcion_control)")}
            self.assertEqual(db.execute("PRAGMA user_version").fetchone()[0], store.SCHEMA_VERSION)
        self.assertTrue({"initial_checkpoint_at", "recovery_state", "recovery_gap_start_at",
                         "recovery_gap_end_at", "history_batch_count", "history_sync_confirmed"} <= columns)

    def test_migration_21_to_current_upgrades_existing_receiver_control_in_temp_database(self):
        path = self.directory / "migration_v21.sqlite3"
        db = sqlite3.connect(path)
        db.row_factory = sqlite3.Row
        store.migration_recepcion_gmail(db)
        db.execute("INSERT INTO recepcion_control(account,history_id,updated_at) VALUES(?,?,?)",
                   (recepcion.CUENTA, "123", self.when.isoformat()))
        db.execute("PRAGMA user_version=21")
        db.commit()
        store.migrate(db, path)
        columns = {row["name"] for row in db.execute("PRAGMA table_info(recepcion_control)")}
        control = db.execute("SELECT history_id,history_sync_confirmed,recovery_state FROM recepcion_control").fetchone()
        self.assertEqual(db.execute("PRAGMA user_version").fetchone()[0], store.SCHEMA_VERSION)
        self.assertTrue({"initial_checkpoint_at", "history_sync_confirmed", "history_batch_count"} <= columns)
        self.assertEqual(tuple(control), ("123", 0, "normal"))
        db.close()

    def test_legacy_version_22_without_audit_table_is_repaired_before_initial_checkpoint(self):
        db = store.open_db(self.directory)
        try:
            db.execute("DROP TABLE recepcion_eventos")
            db.execute("PRAGMA user_version=22")
            db.commit()
        finally:
            db.close()

        result = self.scan()

        self.assertEqual(result["status"], "inicializado_sin_historial")
        with store.lectura() as db:
            self.assertEqual(db.execute("PRAGMA user_version").fetchone()[0], store.SCHEMA_VERSION)
            self.assertEqual(db.execute("SELECT event_kind FROM recepcion_eventos").fetchone()[0],
                             "cursor_inicializado")

    def test_history_response_without_a_valid_cursor_does_not_advance_checkpoint(self):
        self.initialize()
        self.gmail.history_results = [{"history": []}]
        result = self.scan(self.gmail, self.when + timedelta(minutes=1))
        self.assertEqual((result["status"], result["error_type"]), ("error", "ValueError"))
        with store.lectura() as db:
            row = db.execute("SELECT history_id,last_success_at FROM recepcion_control").fetchone()
        self.assertEqual(row["history_id"], "100")
        self.assertEqual(row["last_success_at"], self.when.isoformat())

    def test_new_inbound_message_is_registered_and_has_work_reference_packet(self):
        self.initialize()
        self.gmail.history_results = [event({"id": "m1", "threadId": "t1"})]
        self.gmail.mail_items["m1"] = message()
        result = self.scan()
        self.assertEqual((result["status"], result["discovered"], result["processed"]), ("ok", 1, 1))
        packet = recepcion.event_packet("t1", "m1")
        self.assertEqual(packet["state"], "pendiente_de_procesar")
        self.assertEqual(packet["disposition"], "pendiente_de_interpretacion_work")
        self.assertEqual(packet["attachments"][0]["storage_path"], None)
        self.assertEqual(packet["attachments"][0]["gmail_attachment_id"], "a1")
        self.assertEqual(packet["thread_context"]["thread_id"], "t1")
        self.assertEqual([item["event"] for item in packet["audit"]], ["detectado", "pendiente_work"])
        self.assertNotIn("snippet", packet)

    def test_only_read_only_gmail_methods_are_used(self):
        self.initialize()
        self.gmail.history_results = [event({"id": "m1", "threadId": "t1"})]
        self.gmail.mail_items["m1"] = message()
        self.scan()
        self.assertTrue({name for name, _ in self.gmail.calls} <= {
            "getProfile", "history.list", "messages.list", "messages.get"})

    def test_followup_message_in_existing_thread_gets_its_own_stable_identity(self):
        self.initialize()
        first = message("m1", "t1")
        second = message("m2", "t1")
        self.gmail.history_results = [event({"id": "m1", "threadId": "t1"})]
        self.gmail.mail_items["m1"] = first
        self.scan()
        self.gmail.history_results = [event({"id": "m2", "threadId": "t1"})]
        self.gmail.mail_items["m2"] = second
        self.scan(self.gmail, self.when + timedelta(minutes=1))
        self.assertEqual([(r["thread_id"], r["message_id"]) for r in self.rows()], [("t1", "m1"), ("t1", "m2")])
        self.assertIn("m1", recepcion.event_packet("t1", "m2")["thread_context"]["mensajes_detectados"][0]["message_id"])

    def test_duplicate_history_event_does_not_repeat_message_or_process_it_twice(self):
        self.initialize()
        item = {"id": "m1", "threadId": "t1"}
        self.gmail.history_results = [event(item)]
        self.gmail.mail_items["m1"] = message()
        self.scan()
        gets = len([call for call in self.gmail.calls if call[0] == "messages.get"])
        self.gmail.history_results = [event(item)]
        result = self.scan(self.gmail, self.when + timedelta(minutes=1))
        self.assertEqual((len(self.rows()), len([c for c in self.gmail.calls if c[0] == "messages.get"])), (1, gets))
        self.assertEqual(result["processed"], 0)

    def test_restart_uses_sqlite_checkpoint_and_durable_queue(self):
        self.initialize()
        self.gmail.history_results = [event({"id": "m1", "threadId": "t1"})]
        self.gmail.mail_items["m1"] = message()
        self.scan()
        db = store.open_db(self.directory)
        db.close()
        self.gmail.history_results = [event({"id": "m2", "threadId": "t1"})]
        self.gmail.mail_items["m2"] = message("m2")
        self.scan(self.gmail, self.when + timedelta(minutes=2))
        self.assertEqual({r["message_id"] for r in self.rows()}, {"m1", "m2"})

    def test_pending_retry_survives_database_reopen(self):
        self.initialize()
        self.gmail.history_results = [event({"id": "m1", "threadId": "t1"})]
        self.gmail.mail_items["m1"] = ConnectionError("offline")
        self.scan()
        store.open_db(self.directory).close()
        self.gmail.mail_items["m1"] = message()
        result = self.scan(self.gmail, self.when + timedelta(minutes=10))
        self.assertEqual((result["processed"], self.rows()[0]["state"]), (1, "pendiente_de_procesar"))

    def test_existing_queue_is_drained_even_when_history_endpoint_is_unavailable(self):
        self.initialize()
        self.gmail.history_results = [event({"id": "m1", "threadId": "t1"})]
        self.gmail.mail_items["m1"] = ConnectionError("temporary")
        self.scan()
        self.gmail.mail_items["m1"] = message()
        self.gmail.history_error = ConnectionError("history unavailable")
        result = self.scan(self.gmail, self.when + timedelta(minutes=10))
        self.assertEqual(result["status"], "error")
        self.assertEqual(self.rows()[0]["state"], "pendiente_de_procesar")

    def test_temporary_connection_loss_keeps_cursor_then_recovers(self):
        self.initialize()
        self.gmail.history_error = ConnectionError("offline")
        failed = self.scan(self.gmail, self.when + timedelta(minutes=1))
        self.assertEqual(failed["error_kind"], "red")
        with store.lectura() as db:
            self.assertEqual(db.execute("SELECT history_id FROM recepcion_control").fetchone()[0], "100")
        self.gmail.history_error = None
        self.gmail.history_results = [event({"id": "m1", "threadId": "t1"})]
        self.gmail.mail_items["m1"] = message()
        self.assertEqual(self.scan(self.gmail, self.when + timedelta(minutes=2))["processed"], 1)

    def test_gmail_failure_is_classified_without_exposing_exception_text(self):
        self.initialize()
        self.gmail.history_error = RuntimeError("private-token-and-message-body")
        result = self.scan(self.gmail, self.when + timedelta(minutes=1))
        self.assertEqual((result["status"], result["error_kind"]), ("error", "interno"))
        with store.lectura() as db:
            control = db.execute("SELECT last_error_kind,last_error_type FROM recepcion_control").fetchone()
        self.assertEqual((control[0], control[1]), ("interno", "RuntimeError"))

    def test_two_simultaneous_scans_do_not_run_two_history_requests(self):
        self.initialize()
        entered = threading.Event()
        self.gmail.history_wait = entered
        self.gmail.history_release.clear()
        with ThreadPoolExecutor(max_workers=2) as pool:
            first = pool.submit(self.scan, self.gmail, self.when + timedelta(minutes=1))
            self.assertTrue(entered.wait(1))
            second = pool.submit(self.scan, self.gmail, self.when + timedelta(minutes=1))
            self.assertEqual(second.result(timeout=1)["status"], "concurrente")
            self.gmail.history_release.set()
            self.assertEqual(first.result(timeout=2)["status"], "ok")
        self.assertEqual(len([call for call in self.gmail.calls if call[0] == "history.list"]), 1)

    def test_database_lease_allows_only_one_of_two_processes(self):
        self.initialize()
        context = multiprocessing.get_context("spawn")
        ready, start, release = context.Queue(), context.Event(), context.Event()
        results = context.Queue()
        processes = [context.Process(target=_process_lease_worker,
                                     args=(str(self.directory), ready, start, release, results))
                     for _ in range(2)]
        for process in processes:
            process.start()
        try:
            self.assertTrue(ready.get(timeout=10))
            self.assertTrue(ready.get(timeout=10))
            start.set()
            outcomes = sorted((results.get(timeout=10), results.get(timeout=10)))
            self.assertEqual(outcomes, [False, True])
        finally:
            release.set()
            for process in processes:
                process.join(10)
                if process.is_alive():
                    process.terminate()
                    process.join(2)
        self.assertTrue(all(process.exitcode == 0 for process in processes))

    def test_expired_lease_fences_stale_history_commit(self):
        import time
        self.initialize()
        first_owner = recepcion._acquire(recepcion.CUENTA, time.time(), self.when.isoformat())
        self.assertTrue(first_owner)
        entered = threading.Event()
        self.gmail.history_wait = entered
        self.gmail.history_release.clear()
        self.gmail.history_results = [event({"id": "m1", "threadId": "t1"})]
        self.gmail.mail_items["m1"] = message()
        with patch.object(recepcion, "LEASE_SEGUNDOS", 0.05):
            with ThreadPoolExecutor(max_workers=1) as pool:
                stale = pool.submit(recepcion._history_changes, self.gmail, recepcion.CUENTA, first_owner,
                                    "100", None, self.when.isoformat())
                self.assertTrue(entered.wait(1))
                time.sleep(0.08)
                second_owner = recepcion._acquire(recepcion.CUENTA, time.time(), self.when.isoformat())
                self.assertTrue(second_owner)
                self.gmail.history_release.set()
                self.gmail.history_wait = None
                recepcion._history_changes(self.gmail, recepcion.CUENTA, second_owner, "100", None,
                                           self.when.isoformat())
                with self.assertRaises(recepcion.PropiedadReservaPerdida):
                    stale.result(timeout=2)
            with store.lectura() as db:
                self.assertEqual(db.execute("SELECT history_id FROM recepcion_control").fetchone()[0], "111")
                self.assertEqual(db.execute("SELECT COUNT(*) FROM recepcion_mensajes").fetchone()[0], 0)
            recepcion._release(recepcion.CUENTA, second_owner)

    def test_pending_message_is_retried_then_becomes_manually_reviewable(self):
        self.initialize()
        self.gmail.history_results = [event({"id": "m1", "threadId": "t1"})]
        self.gmail.mail_items["m1"] = ConnectionError("offline")
        self.scan()
        row = self.rows()[0]
        self.assertEqual((row["state"], row["attempts"]), ("requiere_reintento", 1))
        self.gmail.mail_items["m1"] = message()
        self.scan(self.gmail, self.when + timedelta(minutes=10))
        self.assertEqual(self.rows()[0]["state"], "pendiente_de_procesar")
        # Un 404 repetido no se trata como borrado; al tope se detiene en revisión manual.
        self.gmail.mail_items["m1"] = NotFound("not found")
        with store.transaccion() as db:
            db.execute("UPDATE recepcion_mensajes SET state='requiere_reintento',attempts=4,retry_at=0")
        self.scan(self.gmail, self.when + timedelta(minutes=11))
        self.assertEqual(self.rows()[0]["state"], "revision_manual")

    def test_account_own_messages_are_recorded_but_excluded_from_work_queue(self):
        self.initialize()
        self.gmail.history_results = [event({"id": "sent", "threadId": "t1"})]
        self.gmail.mail_items["sent"] = message("sent", sender=recepcion.CUENTA)
        result = self.scan()
        packet = recepcion.event_packet("t1", "sent")
        self.assertEqual(result["own_excluded"], 1)
        self.assertEqual((packet["state"], packet["disposition"]), ("procesado", "mensaje_propio_excluido"))

    def test_expired_history_recovers_overlap_candidates_with_deduplication(self):
        self.initialize()
        self.confirm_history_sync()

        self.gmail.history_error = NotFound("expired")
        self.gmail.list_result = {"messages": [{"id": "m1", "threadId": "t1"}]}
        self.gmail.mail_items["m1"] = message()
        result = self.scan(self.gmail, self.when + timedelta(minutes=5))
        self.assertEqual(result["processed"], 1)
        listing = next(call[1] for call in self.gmail.calls if call[0] == "messages.list")
        self.assertIn("after:", listing["q"])
        self.assertTrue(listing["includeSpamTrash"])
        self.assertEqual(len(self.rows()), 1)

    def test_expired_cursor_before_first_successful_history_sync_requires_review(self):
        self.initialize()
        self.gmail.history_error = NotFound("expired immediately")
        result = self.scan(self.gmail, self.when + timedelta(minutes=1))
        self.assertEqual(result["status"], "recuperacion_requiere_revision")
        self.assertFalse(any(call[0] == "messages.list" for call in self.gmail.calls))
        with store.lectura() as db:
            control = db.execute("SELECT history_id,recovery_state,history_sync_confirmed FROM recepcion_control").fetchone()
        self.assertEqual(tuple(control), ("100", "revision_manual", 0))

    def test_expired_history_recovers_a_gap_longer_than_the_old_24_hour_overlap(self):
        start = self.when - timedelta(days=8)
        self.scan(self.gmail, start)
        self.scan(self.gmail, start + timedelta(minutes=1))
        with store.transaccion() as db:
            db.execute("UPDATE recepcion_control SET last_success_at=? WHERE account=?",
                       ((start + timedelta(minutes=1)).isoformat(), recepcion.CUENTA))
        self.gmail.history_error = NotFound("expired")
        self.gmail.history_cursor = "200"
        self.gmail.list_result = {"messages": [{"id": "m1", "threadId": "t1"}]}
        self.gmail.mail_items["m1"] = message()
        result = self.scan(self.gmail, self.when)
        self.assertEqual(result["processed"], 1)
        with store.lectura() as db:
            control = db.execute("SELECT history_id,recovery_state FROM recepcion_control").fetchone()
        self.assertEqual(tuple(control), ("200", "normal"))
        self.assertTrue(any(call[0] == "messages.list" for call in self.gmail.calls))

    def test_expired_history_beyond_automatic_window_requires_review_without_cursor_advance(self):
        self.initialize()
        self.confirm_history_sync()
        old_success = self.when - timedelta(days=recepcion.MAX_RECUPERACION_DIAS + 1)
        with store.transaccion() as db:
            db.execute("UPDATE recepcion_control SET last_success_at=? WHERE account=?",
                       (old_success.isoformat(), recepcion.CUENTA))
        self.gmail.history_error = NotFound("expired")
        result = self.scan(self.gmail, self.when)
        self.assertEqual(result["status"], "recuperacion_requiere_revision")
        self.assertFalse(any(call[0] == "messages.list" for call in self.gmail.calls))
        with store.lectura() as db:
            control = db.execute("SELECT history_id,recovery_state,last_error_kind FROM recepcion_control").fetchone()
            event_row = db.execute("SELECT detail_json FROM recepcion_eventos "
                                   "WHERE event_kind='recuperacion_requiere_revision'").fetchone()
        self.assertEqual(tuple(control), ("110", "revision_manual", "cobertura"))
        self.assertIn('"cursor_advanced":false', event_row[0])
        self.assertEqual(self.scan(self.gmail, self.when + timedelta(minutes=1))["status"],
                         "recuperacion_requiere_revision")

    def test_expired_history_with_unknown_last_coverage_requires_review(self):
        self.initialize()
        self.confirm_history_sync()
        with store.transaccion() as db:
            db.execute("UPDATE recepcion_control SET last_success_at='',initial_checkpoint_at='' WHERE account=?",
                       (recepcion.CUENTA,))
        self.gmail.history_error = NotFound("expired")
        result = self.scan(self.gmail, self.when + timedelta(minutes=5))
        self.assertEqual(result["status"], "recuperacion_requiere_revision")
        self.assertFalse(any(call[0] == "messages.list" for call in self.gmail.calls))
        with store.lectura() as db:
            self.assertEqual(db.execute("SELECT history_id FROM recepcion_control").fetchone()[0], "110")

    def test_recovery_list_failure_does_not_move_cursor_or_publish_partial_candidates(self):
        self.initialize()
        self.confirm_history_sync()
        self.gmail.history_error = NotFound("expired")
        self.gmail.list_result = [
            {"messages": [{"id": "m1", "threadId": "t1"}], "nextPageToken": "p2"},
            ConnectionError("network dropped"),
        ]
        result = self.scan(self.gmail, self.when + timedelta(minutes=5))
        self.assertEqual((result["status"], result["error_kind"]), ("error", "red"))
        self.assertEqual(self.rows(), [])
        with store.lectura() as db:
            self.assertEqual(db.execute("SELECT history_id FROM recepcion_control").fetchone()[0], "110")

    def test_recovery_candidate_overflow_stops_without_cursor_advance_or_partial_queue(self):
        self.initialize()
        self.confirm_history_sync()
        self.gmail.history_error = NotFound("expired")
        self.gmail.list_result = [
            {"messages": [{"id": f"m{i}", "threadId": "t1"} for i in range(500)], "nextPageToken": "p2"},
            {"messages": [{"id": f"m{i}", "threadId": "t1"} for i in range(500, 1000)], "nextPageToken": "p3"},
            {"messages": [{"id": "overflow", "threadId": "t1"}]},
        ]
        result = self.scan(self.gmail, self.when + timedelta(minutes=5))
        self.assertEqual(result["status"], "recuperacion_requiere_revision")
        self.assertEqual(len(self.rows()), 0)
        with store.lectura() as db:
            control = db.execute("SELECT history_id,recovery_state FROM recepcion_control").fetchone()
        self.assertEqual(tuple(control), ("110", "revision_manual"))

    def test_large_unread_history_batch_is_bounded_and_does_not_advance_cursor(self):
        self.initialize()
        def page(start, end, next_token=None):
            result = {"historyId": "200", "history": [{"messagesAdded": [
                {"message": {"id": f"m{i}", "threadId": "t1"}} for i in range(start, end)
            ]}]}
            if next_token:
                result["nextPageToken"] = next_token
            return result
        self.gmail.history_results = [page(0, 500, "p2"), page(500, 1000, "p3"), page(1000, 1001)]
        result = self.scan(self.gmail, self.when + timedelta(minutes=2))
        self.assertEqual(result["status"], "recuperacion_requiere_revision")
        with store.lectura() as db:
            control = db.execute("SELECT history_id,recovery_state,history_batch_count FROM recepcion_control").fetchone()
            count = db.execute("SELECT COUNT(*) FROM recepcion_mensajes").fetchone()[0]
        self.assertEqual(tuple(control), ("100", "revision_manual", 1001))
        self.assertEqual(count, 1000)

    def test_recovery_never_queues_messages_before_the_initial_checkpoint(self):
        self.initialize()
        self.confirm_history_sync()
        self.gmail.history_error = NotFound("expired")
        old = message("before", "t1")
        old["internalDate"] = str(int((self.when - timedelta(days=2)).timestamp() * 1000))
        self.gmail.list_result = {"messages": [{"id": "before", "threadId": "t1"}]}
        self.gmail.mail_items["before"] = old
        self.scan(self.gmail, self.when + timedelta(minutes=5))
        packet = recepcion.event_packet("t1", "before")
        self.assertEqual((packet["state"], packet["disposition"]),
                         ("procesado", "anterior_al_checkpoint_inicial"))

    def test_wrong_gmail_account_is_not_scanned(self):
        self.initialize()
        with patch.object(self.gmail, "getProfile", return_value=Ejecutable({"emailAddress": "otra@example.com",
                                                                               "historyId": "300"})):
            result = self.scan(self.gmail, self.when + timedelta(minutes=1))
        self.assertEqual(result["error_kind"], "acceso")
        self.assertFalse(any(call[0] == "history.list" for call in self.gmail.calls))


if __name__ == "__main__":
    unittest.main()
