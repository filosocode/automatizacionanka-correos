"""`Caso` (anka/dominio/caso.py): campos nombrados que además se leen como un mapa de solo lectura."""

from tests import _isolate  # noqa: F401  (debe ir primero)

import unittest
from dataclasses import FrozenInstanceError, replace

from anka.dominio.caso import Caso
from anka.dominio.status import QuoteStatus


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


# Cubre: T-12
class RegistrosTests(unittest.TestCase):
    ROW = {"id": 1, "thread_id": "t", "message_id": "m", "sender_email": "a@b.co", "sender_name": "", "subject": "s",
           "snippet": "x", "occurred_at": "2026-10-01T10:00:00-05:00", "has_code": 1, "is_contact": 0,
           "same_company": 1, "state": "pending", "reply_sent_at": None, "contact_notified_at": None,
           "decided_at": None, "otra_columna": "se ignora"}

    def test_a_change_turns_the_stored_flags_into_booleans_and_ignores_extra_columns(self):
        from anka.dominio.registros import Cambio
        cambio = Cambio.from_row(self.ROW)
        self.assertIs(cambio.has_code, True)
        self.assertIs(cambio["is_contact"], False)
        self.assertNotIn("otra_columna", cambio)
        self.assertEqual({**cambio}["state"], "pending")

    def test_a_missing_column_fails_loudly_instead_of_silently(self):
        from anka.dominio.registros import Vinculo
        with self.assertRaises(KeyError):
            Vinculo.from_row({"id": 1})


# Cubre: T-12
class CasoRulesTests(unittest.TestCase):
    def test_a_closed_case_only_admits_mail_if_the_client_wrote_again(self):
        sent = replace(sample(), status=QuoteStatus.RESPONSE_SENT)
        self.assertTrue(sent.cerrado)
        self.assertFalse(sent.admite_correo(reabierto=sent.reabierto(False), revision_tecnica=False))
        self.assertTrue(sent.admite_correo(reabierto=sent.reabierto(True), revision_tecnica=False))
        self.assertFalse(replace(sample(), status=QuoteStatus.IGNORED).reabierto(True))   # lo descartado no se reabre

    def test_mail_needs_a_valid_recipient_and_no_technical_block(self):
        open_case = sample()
        self.assertTrue(open_case.admite_correo(reabierto=False, revision_tecnica=False))
        self.assertFalse(open_case.admite_correo(reabierto=False, revision_tecnica=True))
        self.assertFalse(replace(open_case, recipient="sin-arroba").admite_correo(
            reabierto=False, revision_tecnica=False))

    def test_the_information_is_validated_by_scope_code_or_a_persons_decision(self):
        self.assertFalse(sample().informacion_validada)
        self.assertTrue(replace(sample(), status=QuoteStatus.READY_REVIEW).informacion_validada)
        self.assertTrue(replace(sample(), quote_code="26145").informacion_validada)
        decided = replace(sample(), analysis={"manual_review": {"decision": "sufficient"}})
        self.assertTrue(decided.informacion_validada)

    def test_it_waits_for_the_client_only_after_something_was_sent(self):
        self.assertFalse(sample().espera_al_cliente)                       # esperando, pero sin correo enviado
        self.assertTrue(replace(sample(), sent_at="2026-10-01T10:00:00-05:00").espera_al_cliente)
