import _isolate  # noqa: F401  (debe ir primero)

import io
import logging
import os
import tempfile
import unittest
from logging.handlers import RotatingFileHandler
from pathlib import Path
from unittest.mock import patch

import logging_setup
from logging_setup import SENSITIVE, get_logger


class LoggingTestCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self._tmp.name)
        self.addCleanup(self._cleanup)

    def _cleanup(self):
        logging_setup.reset_logging()  # cierra el archivo antes de borrar la carpeta
        self._tmp.cleanup()

    def configure(self, **kwargs):
        stream = io.StringIO()
        path = logging_setup.configure_logging(self.dir, stream=stream, **kwargs)
        return path, stream

    def file_text(self):
        for handler in logging.getLogger("anka").handlers:
            handler.flush()
        path = self.dir / "anka.log"
        return path.read_text(encoding="utf-8") if path.exists() else ""


# Cubre: T-08
class PrivacyTests(LoggingTestCase):
    def test_sensitive_records_reach_the_console_but_never_the_file(self):
        path, console = self.configure()
        log = get_logger("prueba")
        log.info("t-1: resultado seguro")
        log.info("t-1: detalle del cliente Acme", extra=SENSITIVE)
        self.assertIn("detalle del cliente Acme", console.getvalue())
        self.assertIn("resultado seguro", console.getvalue())
        text = self.file_text()
        self.assertIn("resultado seguro", text)
        self.assertNotIn("Acme", text)
        self.assertTrue(path.exists())

    def test_email_addresses_are_redacted_in_the_file_even_if_not_flagged(self):
        _, console = self.configure()
        get_logger("prueba").info("Se envió a cliente.real+x@empresa.com.co ahora")
        self.assertIn("cliente.real+x@empresa.com.co", console.getvalue())
        text = self.file_text()
        self.assertNotIn("cliente.real", text)
        self.assertNotIn("empresa.com.co", text)
        self.assertIn("<correo>", text)

    def test_email_addresses_are_redacted_inside_tracebacks(self):
        self.configure()
        try:
            raise ValueError("fallo con o'brien@cliente.co adentro")
        except ValueError:
            get_logger("prueba").exception("t-9: error")
        text = self.file_text()
        self.assertIn("ValueError", text)
        self.assertIn("Traceback", text)
        self.assertNotIn("cliente.co", text)

    def test_file_lines_carry_time_level_and_logger_name(self):
        self.configure()
        get_logger("pipeline").warning("t-2: algo raro")
        line = self.file_text().splitlines()[0]
        self.assertRegex(line, r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2},\d{3} WARNING anka\.pipeline: t-2: algo raro$")


# Cubre: T-08
class ConfigurationTests(LoggingTestCase):
    def test_console_message_has_no_decoration_so_the_pilot_window_looks_the_same(self):
        _, console = self.configure()
        get_logger("pipeline").info("t-3: guardado para revisión humana")
        self.assertEqual(console.getvalue(), "t-3: guardado para revisión humana\n")

    def test_configuring_twice_does_not_duplicate_handlers(self):
        self.configure()
        self.configure()
        handlers = logging.getLogger("anka").handlers
        self.assertEqual(len(handlers), 2)  # consola + archivo
        _, console = self.configure()
        get_logger("x").info("una sola vez")
        self.assertEqual(console.getvalue().count("una sola vez"), 1)
        self.assertEqual(self.file_text().count("una sola vez"), 1)

    def test_file_rotates_with_bounded_size(self):
        self.configure()
        rotating = [h for h in logging.getLogger("anka").handlers
                    if isinstance(h, RotatingFileHandler)]
        self.assertEqual(len(rotating), 1)
        self.assertEqual(rotating[0].maxBytes, logging_setup.MAX_BYTES)
        self.assertEqual(rotating[0].backupCount, logging_setup.BACKUP_COUNT)

    def test_level_comes_from_the_environment_and_ignores_garbage(self):
        cases = {"DEBUG": logging.DEBUG, "warning": logging.WARNING, "basura": logging.INFO,
                 "": logging.INFO, "NOTSET": logging.INFO}
        for value, expected in cases.items():
            with self.subTest(value=value), patch.dict(os.environ, {"ANKA_LOG_LEVEL": value}):
                self.configure()
                self.assertEqual(logging.getLogger("anka").level, expected)

    def test_debug_records_are_dropped_at_the_default_level(self):
        _, console = self.configure()
        get_logger("x").debug("ruido")
        self.assertNotIn("ruido", console.getvalue())
        self.assertNotIn("ruido", self.file_text())

    def test_unwritable_log_dir_warns_and_does_not_raise(self):
        blocker = self.dir / "es_un_archivo"
        blocker.write_text("x", encoding="utf-8")
        stream = io.StringIO()
        path = logging_setup.configure_logging(blocker / "logs", stream=stream)
        self.assertIsNone(path)
        self.assertIn("No se pudo abrir el archivo de registro", stream.getvalue())
        get_logger("x").info("sigue funcionando")
        self.assertIn("sigue funcionando", stream.getvalue())

    def test_file_only_mode_has_no_console_handler(self):
        self.configure(console=False)
        kinds = {type(h).__name__ for h in logging.getLogger("anka").handlers}
        self.assertEqual(kinds, {"RotatingFileHandler"})


if __name__ == "__main__":
    unittest.main()
