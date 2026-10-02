import _isolate  # noqa: F401  (debe ir primero)

import io
import sys
import unittest
from contextlib import redirect_stdout
from unittest.mock import patch

import app


# Cubre: T-06
class CliTests(unittest.TestCase):
    def run_cli(self, *argv):
        out = io.StringIO()
        with patch.object(sys, "argv", ["app.py", *argv]), redirect_stdout(out):
            app.main()
        return out.getvalue()

    def test_mode_command_prints_active_mode(self):
        config = {"active_mode": "local", "llm": {"provider": "ollama", "model": "m"}}
        with patch("modes.load_config", return_value=config):
            self.assertIn("Modo activo: local", self.run_cli("mode"))

    def test_unknown_command_is_rejected_by_the_parser(self):
        with self.assertRaises(SystemExit) as raised, redirect_stderr_quiet():
            self.run_cli("borrar-todo")
        self.assertEqual(raised.exception.code, 2)

    def test_review_only_requires_apply(self):
        config = {"active_mode": "local", "required_fields": ["x"], "llm": {}}
        with patch("pipeline.load_config", return_value=config), \
                self.assertRaisesRegex(SystemExit, "--review-only se usa junto con --apply"):
            self.run_cli("run", "--review-only")


def redirect_stderr_quiet():
    from contextlib import redirect_stderr
    return redirect_stderr(io.StringIO())


if __name__ == "__main__":
    unittest.main()
