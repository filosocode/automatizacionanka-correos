"""Copias de seguridad y lógica del lanzador (sin arrancar procesos reales)."""

from tests import _isolate  # noqa: F401  (debe ir primero)

import sqlite3
import tempfile
import time
import unittest
import zipfile
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import MagicMock, patch

from anka.infra import backup
from anka.infra import settings
from anka.cli import supervisor


# Cubre: T-06
class BackupTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        patcher = patch.object(settings, "DATA_DIR", Path(self.temp.name))
        patcher.start()
        self.addCleanup(patcher.stop)
        db = sqlite3.connect(Path(self.temp.name) / "quotes.sqlite3")
        db.execute("CREATE TABLE t (x)")
        db.execute("INSERT INTO t VALUES ('dato')")
        db.commit()
        db.close()
        (Path(self.temp.name) / "config.json").write_text("{}")
        (Path(self.temp.name) / "token.json").write_text("SECRETO")

    def test_backup_contains_database_and_config_but_never_secrets(self):
        path = backup.create_backup()
        with zipfile.ZipFile(path) as archive:
            names = set(archive.namelist())
        self.assertEqual(names, {"quotes.sqlite3", "config.json"})

    def test_restore_brings_back_data_and_keeps_current_db(self):
        path = backup.create_backup()
        db = sqlite3.connect(Path(self.temp.name) / "quotes.sqlite3")
        db.execute("DELETE FROM t")
        db.commit()
        db.close()
        backup.restore(path)
        db = sqlite3.connect(Path(self.temp.name) / "quotes.sqlite3")
        self.assertEqual(db.execute("SELECT x FROM t").fetchone()[0], "dato")
        db.close()
        self.assertTrue(list(Path(self.temp.name).glob("quotes.sqlite3.before-restore-*")))

    def test_prune_keeps_only_newest(self):
        folder = backup.backups_dir()
        folder.mkdir()
        for day in range(1, 6):
            (folder / f"{backup.PREFIX}2026010{day}-000000.zip").write_text("x")
        backup.prune_local(keep=2)
        self.assertEqual(sorted(p.name for p in folder.glob("*.zip")),
                         [f"{backup.PREFIX}20260104-000000.zip", f"{backup.PREFIX}20260105-000000.zip"])

    def test_due_when_never_run_or_older_than_a_day(self):
        self.assertTrue(backup.backup_due())
        result = backup.run_backup(use_drive=False)
        self.assertEqual(result["error"], "")
        self.assertFalse(backup.backup_due())
        self.assertTrue(backup.backup_due(datetime.now().astimezone() + timedelta(hours=25)))

    def test_drive_failure_keeps_local_copy_and_reports_error(self):
        drive = MagicMock()
        drive.files.return_value.list.return_value.execute.side_effect = RuntimeError("sin red")
        result = backup.run_backup(use_drive=True, drive=drive)
        self.assertTrue(result["file"])
        self.assertFalse(result["drive"])
        self.assertIn("Drive:", result["error"])
        self.assertTrue((backup.backups_dir() / result["file"]).exists())

    def test_drive_upload_removes_only_own_old_copies(self):
        drive = MagicMock()
        files = drive.files.return_value
        files.list.return_value.execute.side_effect = [
            {"files": [{"id": "F"}]},  # carpeta existente
            {"files": [{"id": f"id{n}", "name": f"{backup.PREFIX}{n:02d}.zip"} for n in range(16)]},
        ]
        path = backup.create_backup()
        self.assertEqual(backup.upload_to_drive(drive, path, keep=14), 2)
        deleted = [call.kwargs["fileId"] for call in files.delete.call_args_list]
        self.assertEqual(deleted, ["id0", "id1"])


# Cubre: T-06
class SupervisorTests(unittest.TestCase):
    def test_backoff_grows_and_caps(self):
        self.assertEqual([supervisor.backoff(n) for n in (1, 2, 3, 10)], [2, 4, 8, 60])

    def test_snapshot_detects_code_change(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "anka" / "servicios").mkdir(parents=True)
            (root / "anka" / "web" / "templates").mkdir(parents=True)
            code = root / "anka" / "servicios" / "a.py"
            template = root / "anka" / "web" / "templates" / "t.html"
            code.write_text("1")
            template.write_text("x")
            before = supervisor.code_snapshot(root)
            self.assertEqual(before, supervisor.code_snapshot(root))
            self.assertEqual(len(before), 2)                       # ve el código de los paquetes y las plantillas
            import os
            for path in (code, template):
                path.write_text("2")
                os.utime(path, (time.time() + 5, time.time() + 5))
                self.assertNotEqual(before, supervisor.code_snapshot(root))
                before = supervisor.code_snapshot(root)

    def test_child_restarts_after_crash_but_not_after_stop_request(self):
        child = supervisor.Child("X", ["x.py"])
        with patch.object(supervisor.subprocess, "Popen") as popen:
            popen.return_value.poll.return_value = 1
            popen.return_value.pid = 5
            child.check()                       # arranca
            child.check()                       # detecta caída
            self.assertEqual(child.failures, 1)
            child.retry_at = 0
            child.check()                       # reinicia
            self.assertEqual(popen.call_count, 2)
            popen.return_value.poll.return_value = supervisor.EXIT_STOP
            child.check()
            self.assertTrue(child.stopped)
            child.check()
            self.assertEqual(popen.call_count, 2)


if __name__ == "__main__":
    unittest.main()
