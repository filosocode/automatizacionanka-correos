from tests import _isolate  # noqa: F401

import sqlite3
import unittest
from unittest.mock import patch

from anka.cli import gmail_reception


class GmailReceptionCliTests(unittest.TestCase):
    def test_sqlite_failure_logs_diagnostic_without_email_address(self):
        error = sqlite3.OperationalError("no such table: recepcion_eventos")
        with patch.object(gmail_reception, "configure_logging"), \
                patch.object(gmail_reception.gmail_reception_auth, "gmail_service", side_effect=error), \
                patch.object(gmail_reception.log, "error") as log_error:
            result = gmail_reception.main(["--once"])
        self.assertEqual(result, 1)
        log_error.assert_called_once()
        self.assertEqual(log_error.call_args.args[2], "no such table: recepcion_eventos")
        unsafe = sqlite3.OperationalError("no such table: cliente@example.com")
        self.assertEqual(gmail_reception._sqlite_error_summary(unsafe), "detalle omitido por seguridad")
        self.assertNotIn("cliente@example.com", str(log_error.call_args.args))


if __name__ == "__main__":
    unittest.main()
