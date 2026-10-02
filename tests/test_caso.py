"""`Caso` (anka/dominio/caso.py): campos nombrados que además se leen como un mapa de solo lectura."""

from tests import _isolate  # noqa: F401  (debe ir primero)

import unittest
from dataclasses import FrozenInstanceError, replace

from anka.dominio.caso import Caso


def sample() -> Caso:
    return Caso(
        thread_id="t-1", status="waiting", status_label="Esperando", recipient="a@b.co", client_email="a@b.co",
        client_name="Ana", client_name_saved="", client_type="No clasificado", sector="No clasificado", notes="",
        analysis={}, reception_assessment={}, services=[], first_seen_at="", updated_at="", folder_id=None,
        draft_id=None, draft_body="", sent_at="", reply_pending="", proposal=[],
    )


# Cubre: T-12
class CasoTests(unittest.TestCase):
    def test_reads_by_attribute_and_as_a_mapping(self):
        caso = sample()
        self.assertEqual(caso.status, "waiting")
        self.assertEqual(caso["status"], "waiting")
        self.assertEqual(caso.get("quote_code"), "")
        self.assertEqual(caso.get("no_existe", "x"), "x")
        self.assertEqual(dict(caso)["thread_id"], "t-1")
        with self.assertRaises(KeyError):
            caso["no_existe"]

    def test_is_immutable_and_changes_by_copy(self):
        caso = sample()
        with self.assertRaises(FrozenInstanceError):
            caso.status = "ready"  # type: ignore[misc]
        self.assertEqual(replace(caso, status="ready").status, "ready")
        self.assertEqual(caso.status, "waiting")


if __name__ == "__main__":
    unittest.main()
