from tests import _isolate  # noqa: F401

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from anka.infra import gmail_reception_auth, settings
from anka.dominio.errores import AutorizacionDeGoogle


class GmailReceptionAuthTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.directory = Path(self.temp.name)
        self.patch_data = patch.object(settings, "DATA_DIR", self.directory)
        self.patch_data.start()
        self.addCleanup(self.patch_data.stop)

    def test_dedicated_token_path_is_not_commercial_token_path(self):
        self.assertEqual(gmail_reception_auth.token_path(), self.directory / "gmail_reception_token.json")
        self.assertNotEqual(gmail_reception_auth.token_path(), self.directory / "token.json")

    def test_missing_dedicated_token_fails_closed(self):
        with self.assertRaises(AutorizacionDeGoogle):
            gmail_reception_auth.credentials()

    def test_loads_only_dedicated_file_and_requires_exact_readonly_scope(self):
        path = gmail_reception_auth.token_path()
        path.write_text(json.dumps({"token": "test", "scopes": [gmail_reception_auth.READONLY_SCOPE]}),
                        encoding="utf-8")
        creds = MagicMock()
        creds.scopes = [gmail_reception_auth.READONLY_SCOPE]
        creds.granted_scopes = [gmail_reception_auth.READONLY_SCOPE]
        creds.expired = False
        creds.valid = True
        creds.token = "test"
        with patch("google.oauth2.credentials.Credentials.from_authorized_user_file", return_value=creds) as load:
            self.assertIs(gmail_reception_auth.credentials(), creds)
        load.assert_called_once_with(str(path), [gmail_reception_auth.READONLY_SCOPE])

    def test_broad_token_is_rejected_before_google_credentials_are_loaded(self):
        path = gmail_reception_auth.token_path()
        path.write_text(json.dumps({"scopes": [gmail_reception_auth.READONLY_SCOPE,
                                                "https://www.googleapis.com/auth/gmail.send"]}), encoding="utf-8")
        with patch("google.oauth2.credentials.Credentials.from_authorized_user_file") as load:
            with self.assertRaises(AutorizacionDeGoogle):
                gmail_reception_auth.credentials()
        load.assert_not_called()


if __name__ == "__main__":
    unittest.main()
