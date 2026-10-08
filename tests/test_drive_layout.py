"""Estructura de Drive: Año / Cliente / Solicitud, sin crear carpetas de cliente por error."""

from tests import _isolate  # noqa: F401  (debe ir primero)

import unittest

from anka.servicios import drive_layout
from anka.servicios.drive_layout import (
    AmbiguousClient, ClientNotFound, create_project_folder, find_client_folders,
    normalize_client_name, resolve_client_folder, year_mismatch,
)
from tests.fake_drive import FakeDriveTree


# Cubre: R5
class NormalizeTests(unittest.TestCase):
    def test_same_company_written_differently_gives_the_same_key(self):
        variants = ["Energía Norte S.A.S.", "ENERGIA NORTE SAS", "energia norte", "Energia  Norte Ltda.",
                    "  Energía Norte, S.A.S  "]
        self.assertEqual({normalize_client_name(v) for v in variants}, {"energia norte"})

    def test_only_trailing_legal_suffixes_are_removed(self):
        self.assertEqual(normalize_client_name("SA Pedro Ingeniería"), "sa pedro ingenieria")
        self.assertEqual(normalize_client_name("Acme Corp"), "acme")
        self.assertEqual(normalize_client_name("Acme & Cia"), "acme")

    def test_different_companies_stay_different(self):
        self.assertNotEqual(normalize_client_name("Energía Norte"), normalize_client_name("Norte Energía"))
        self.assertNotEqual(normalize_client_name("Acme Norte"), normalize_client_name("Acme"))

    def test_a_name_made_only_of_suffixes_or_symbols_is_empty(self):
        for name in ("", "   ", "S.A.S.", "---", "Ltda"):
            with self.subTest(name=name):
                self.assertEqual(normalize_client_name(name), "")

    def test_path_template_documents_the_convention(self):
        self.assertEqual(drive_layout.PATH_TEMPLATE, "{año}/{cliente}/{solicitud}")


# Cubre: R5, R6
class FindAndResolveTests(unittest.TestCase):
    def setUp(self):
        self.drive = FakeDriveTree({"Energía Norte S.A.S.": {}, "Minera Sur": {}, "cliente 1": {"x": {}}})

    def test_finds_by_normalized_name_ignoring_accents_and_suffixes(self):
        found = find_client_folders(self.drive, self.drive.root_id, "ENERGIA NORTE")
        self.assertEqual([f["name"] for f in found], ["Energía Norte S.A.S."])

    def test_empty_name_never_matches_anything(self):
        self.assertEqual(find_client_folders(self.drive, self.drive.root_id, "S.A.S."), [])

    def test_single_match_is_reused_without_creating(self):
        folder, created = resolve_client_folder(self.drive, self.drive.root_id, "minera sur", create=False)
        self.assertEqual(folder["name"], "Minera Sur")
        self.assertFalse(created)
        self.assertEqual(self.drive.created, [])

    def test_no_match_without_permission_to_create_is_an_error_and_creates_nothing(self):
        with self.assertRaises(ClientNotFound):
            resolve_client_folder(self.drive, self.drive.root_id, "Cliente Nuevo", create=False)
        self.assertEqual(self.drive.created, [])

    def test_no_match_with_explicit_confirmation_creates_the_client_folder(self):
        folder, created = resolve_client_folder(self.drive, self.drive.root_id, " Cliente Nuevo ", create=True)
        self.assertTrue(created)
        self.assertEqual(self.drive.path(folder["id"]), "2026/Cliente Nuevo")

    def test_client_name_is_sanitized_for_drive(self):
        folder, _ = resolve_client_folder(self.drive, self.drive.root_id, 'Obra: Norte/Sur?', create=True)
        self.assertEqual(self.drive.path(folder["id"]), "2026/Obra Norte Sur")

    def test_empty_client_name_is_rejected_even_when_creating(self):
        with self.assertRaises(ValueError):
            resolve_client_folder(self.drive, self.drive.root_id, "  ", create=True)

    def test_ambiguous_names_need_an_exact_match_to_be_resolved(self):
        drive = FakeDriveTree({"Acme": {}, "ACME SAS": {}})
        with self.assertRaises(AmbiguousClient):
            resolve_client_folder(drive, drive.root_id, "acme corp", create=True)
        folder, created = resolve_client_folder(drive, drive.root_id, "Acme", create=False)
        self.assertEqual((folder["name"], created), ("Acme", False))
        self.assertEqual(drive.created, [])


# Cubre: R4, R7, F1-07
class ProjectFolderTests(unittest.TestCase):
    def test_builds_year_client_request_and_is_idempotent(self):
        drive = FakeDriveTree({"Minera Sur": {}})
        first = create_project_folder(drive, drive.root_id, "Minera Sur", "Cotizacion - Mina Norte - ab12cd34")
        again = create_project_folder(drive, drive.root_id, "MINERA SUR S.A.S.", "Cotizacion - Mina Norte - ab12cd34")
        self.assertEqual(drive.path(first.folder["id"]), "2026/Minera Sur/Cotizacion - Mina Norte - ab12cd34")
        self.assertEqual(first.folder["id"], again.folder["id"])
        self.assertEqual(len(drive.created), 1)
        self.assertFalse(first.client_created)

    def test_unknown_client_is_not_created_unless_confirmed(self):
        drive = FakeDriveTree({"Minera Sur": {}})
        with self.assertRaises(ClientNotFound):
            create_project_folder(drive, drive.root_id, "Otra Empresa", "Solicitud A")
        self.assertEqual(drive.created, [])
        result = create_project_folder(drive, drive.root_id, "Otra Empresa", "Solicitud A", create_client=True)
        self.assertTrue(result.client_created)
        self.assertEqual(drive.path(result.folder["id"]), "2026/Otra Empresa/Solicitud A")
        self.assertEqual(drive.created, ["2026/Otra Empresa", "2026/Otra Empresa/Solicitud A"])


# Cubre: R4
class YearMismatchTests(unittest.TestCase):
    def test_parent_named_after_a_different_year_is_flagged(self):
        drive = FakeDriveTree(root_name="2026")
        message = year_mismatch(drive, drive.root_id, "2027-01-05T09:00:00-05:00")
        self.assertIn("2026", message)
        self.assertIn("2027", message)
        self.assertIn("drive_parent_id", message)

    def test_same_year_or_non_year_parent_or_unknown_date_is_fine(self):
        drive = FakeDriveTree(root_name="2026")
        self.assertIsNone(year_mismatch(drive, drive.root_id, "2026-12-31T23:00:00-05:00"))
        self.assertIsNone(year_mismatch(drive, drive.root_id, ""))
        self.assertIsNone(year_mismatch(drive, drive.root_id, "no es fecha"))
        other = FakeDriveTree(root_name="Cotizaciones")
        self.assertIsNone(year_mismatch(other, other.root_id, "2030-01-01T00:00:00-05:00"))


if __name__ == "__main__":
    unittest.main()
