from tests import _isolate  # noqa: F401  (debe ir primero)

import copy
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from anka.dominio.errores import ErrorDeAnka
from anka.infra import settings

VALID = {
    "active_mode": "local",
    "gmail_query": "in:inbox newer_than:7d",
    "own_addresses": ["a@anka.com.co"],
    "forwarded_reply_policy": "internal_reviewer",
    "llm": {"provider": "ollama", "model": "m", "base_url": "http://127.0.0.1:11434",
            "timeout_seconds": 180},
    "required_fields": ["ubicación del proyecto"],
    "commercial_filter_terms": ["cotización"],
    "drive_parent_id": "abc",
    "folder_prefix": "Cotizacion",
}


def with_changes(**changes):
    config = copy.deepcopy(VALID)
    for key, value in changes.items():
        if value is settings_MISSING:
            config.pop(key, None)
        else:
            config[key] = value
    return config


settings_MISSING = object()


# Cubre: T-09
class ValidateConfigTests(unittest.TestCase):
    def test_valid_config_has_no_problems(self):
        self.assertEqual(settings.validate_config(VALID), [])

    def test_example_config_shipped_in_the_repo_is_valid(self):
        example = json.loads(Path("config.example.json").read_text(encoding="utf-8"))
        self.assertEqual(settings.validate_config(example), [])

    def test_optional_keys_may_be_omitted(self):
        minimal = {"required_fields": [], "llm": {"provider": "ollama", "base_url": "http://x"}}
        self.assertEqual(settings.validate_config(minimal), [])

    def test_each_wrong_type_is_reported_with_its_key(self):
        cases = {
            "gmail_query": 5,
            "own_addresses": "a@b.co",
            "commercial_filter_terms": "cotizar",
            "drive_parent_id": 12,
            "folder_prefix": ["x"],
            "required_fields": "ubicación",
        }
        for key, bad in cases.items():
            with self.subTest(key=key):
                problems = settings.validate_config(with_changes(**{key: bad}))
                self.assertTrue(any(key in p for p in problems), problems)

    def test_list_items_must_be_text(self):
        for key in ("required_fields", "commercial_filter_terms", "own_addresses"):
            with self.subTest(key=key):
                problems = settings.validate_config(with_changes(**{key: ["ok", 3]}))
                self.assertTrue(any(key in p for p in problems), problems)

    def test_own_addresses_must_look_like_emails(self):
        problems = settings.validate_config(with_changes(own_addresses=["sin-arroba"]))
        self.assertTrue(any("own_addresses" in p and "sin-arroba" in p for p in problems))

    def test_enumerated_values(self):
        self.assertTrue(settings.validate_config(with_changes(active_mode="cloud")))
        self.assertTrue(settings.validate_config(with_changes(forwarded_reply_policy="todos")))

    def test_required_fields_is_mandatory(self):
        problems = settings.validate_config(with_changes(required_fields=settings_MISSING))
        self.assertTrue(any("required_fields" in p for p in problems))

    def test_llm_rules(self):
        base = VALID["llm"]
        bad_llms = [
            {**base, "provider": "gemini"},
            {**base, "base_url": "127.0.0.1:11434"},
            {**base, "base_url": ""},
            {**base, "timeout_seconds": 0},
            {**base, "timeout_seconds": "180"},
            {**base, "timeout_seconds": True},
            {**base, "model": ""},
            "ollama",
        ]
        for llm in bad_llms:
            with self.subTest(llm=llm):
                self.assertTrue(settings.validate_config(with_changes(llm=llm)))

    def test_openai_does_not_need_base_url(self):
        config = with_changes(active_mode="api", llm={"provider": "openai", "model": "gpt-x"})
        self.assertEqual(settings.validate_config(config), [])

    def test_mode_and_provider_must_agree(self):
        local_with_openai = with_changes(llm={"provider": "openai"})
        api_with_ollama = with_changes(active_mode="api")
        self.assertTrue(any("local" in p for p in settings.validate_config(local_with_openai)))
        self.assertTrue(any("api" in p for p in settings.validate_config(api_with_ollama)))

    def test_all_problems_are_reported_together(self):
        config = with_changes(active_mode="x", gmail_query=1, folder_prefix=2)
        self.assertGreaterEqual(len(settings.validate_config(config)), 3)

    def test_unknown_keys_are_detected_to_catch_typos(self):
        config = with_changes(gmail_quary="in:inbox", folder_prefx="X")
        self.assertEqual(settings.unknown_config_keys(config), ["folder_prefx", "gmail_quary"])
        self.assertEqual(settings.unknown_config_keys(VALID), [])


# Cubre: T-09
class LoadConfigTests(unittest.TestCase):
    def load(self, text):
        with tempfile.TemporaryDirectory() as d, patch.object(settings, "DATA_DIR", Path(d)):
            (Path(d) / "config.json").write_text(text, encoding="utf-8")
            return settings.load_config()

    def test_valid_file_loads(self):
        self.assertEqual(self.load(json.dumps(VALID))["folder_prefix"], "Cotizacion")

    def test_file_with_bom_loads(self):
        self.assertEqual(self.load("﻿" + json.dumps(VALID))["active_mode"], "local")

    def test_invalid_json_points_to_line_and_column(self):
        with self.assertRaises(ErrorDeAnka) as raised:
            self.load('{\n  "active_mode": "local",\n  "llm": }')
        message = str(raised.exception)
        self.assertIn("no es JSON válido", message)
        self.assertIn("línea 3", message)

    def test_root_must_be_an_object(self):
        with self.assertRaisesRegex(ErrorDeAnka, "objeto JSON"):
            self.load("[]")

    def test_problems_are_listed_one_per_line(self):
        bad = json.dumps(with_changes(active_mode="x", gmail_query=1))
        with self.assertRaises(ErrorDeAnka) as raised:
            self.load(bad)
        lines = str(raised.exception).splitlines()
        self.assertIn("2 problema", lines[0])
        self.assertTrue(all(line.startswith(" - ") for line in lines[1:]))

    def test_missing_file_explains_how_to_create_it(self):
        with tempfile.TemporaryDirectory() as d, patch.object(settings, "DATA_DIR", Path(d)):
            with self.assertRaisesRegex(ErrorDeAnka, "config.example.json"):
                settings.load_config()


# Cubre: T-09
class WriteConfigTests(unittest.TestCase):
    def test_refuses_to_save_an_invalid_config_and_leaves_the_file_untouched(self):
        with tempfile.TemporaryDirectory() as d, patch.object(settings, "DATA_DIR", Path(d)):
            settings.write_config(VALID)
            before = (Path(d) / "config.json").read_text(encoding="utf-8")
            with self.assertRaisesRegex(ErrorDeAnka, "active_mode"):
                settings.write_config(with_changes(active_mode="cloud"))
            self.assertEqual((Path(d) / "config.json").read_text(encoding="utf-8"), before)

    def test_saved_config_round_trips(self):
        with tempfile.TemporaryDirectory() as d, patch.object(settings, "DATA_DIR", Path(d)):
            settings.write_config(VALID)
            self.assertEqual(settings.load_config(), VALID)


# Cubre: T-09
class CliWarningTests(unittest.TestCase):
    def test_cli_warns_about_unknown_config_keys_on_stderr(self):
        import io
        import sys
        from contextlib import redirect_stderr, redirect_stdout

        from anka.cli import app

        typo = with_changes(gmail_quary="in:inbox")
        with patch("anka.cli.app.load_config", return_value=typo),                 patch.object(sys, "argv", ["app.py", "mode"]),                 patch("anka.infra.modes.load_config", return_value=typo),                 redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()) as err:
            app.main()
        self.assertIn("gmail_quary", err.getvalue())


if __name__ == "__main__":
    unittest.main()
