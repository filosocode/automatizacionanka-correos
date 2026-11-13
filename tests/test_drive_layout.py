"""Estructura de Drive: Año / Mes / Cliente / Solicitud / anexos, sin crear carpetas de cliente por error (U-102)."""

from tests import _isolate  # noqa: F401  (debe ir primero)

import unittest
from datetime import datetime, UTC

from anka.dominio.errores import ErrorDeValidacion
from anka.servicios import drive_layout
from anka.servicios.drive_layout import (
    AmbiguousClient, ClientNotFound, DriveLayoutError, SimilarClient, SimilarProject, create_project_folder,
    normalize_client_name, nombre_de_solicitud, nombre_del_mes, padre_invalido, project_title, trash_project_folder,
)
from tests.fake_drive import FakeDriveTree

NOVIEMBRE = datetime(2026, 11, 9, 10, 0, tzinfo=UTC)
OCTUBRE = datetime(2026, 10, 20, 10, 0, tzinfo=UTC)
ENERO_2027 = datetime(2027, 1, 5, 10, 0, tzinfo=UTC)
SOLICITUD = "T&E26145 Mina Norte"
BASE = "Proyectos/2026/11-Noviembre"


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
        self.assertEqual(drive_layout.PATH_TEMPLATE, "{año}/{mes}/{cliente}/{solicitud}/anexos")


# Cubre: U-102
class NamesTests(unittest.TestCase):
    def test_the_month_folder_carries_its_number_and_name(self):
        self.assertEqual(nombre_del_mes(datetime(2026, 1, 5)), "01-Enero")
        self.assertEqual(nombre_del_mes(NOVIEMBRE), "11-Noviembre")
        self.assertEqual(nombre_del_mes(datetime(2026, 12, 31)), "12-Diciembre")

    def test_the_request_folder_is_named_with_the_final_code(self):
        self.assertEqual(nombre_de_solicitud("26145", "Mina Norte"), "T&E26145 Mina Norte")
        self.assertEqual(nombre_de_solicitud("26145", None), "T&E26145 Sin nombre")
        self.assertEqual(nombre_de_solicitud("26145", "Obra: Norte/Sur"), "T&E26145 Obra Norte Sur")
        self.assertLessEqual(len(nombre_de_solicitud("26145", "x" * 400)), drive_layout.MAX_FOLDER_NAME)

    def test_the_project_title_is_found_inside_both_folder_names(self):
        self.assertEqual(project_title("T&E26145 Mina Norte"), "Mina Norte")
        self.assertEqual(project_title("Cotizacion - Mina Norte - ab12cd34"), "Mina Norte")
        self.assertEqual(project_title("Mina Norte"), "Mina Norte")


# Cubre: R5, R6, U-102
class ClientTests(unittest.TestCase):
    def crear(self, drive, cliente, **kwargs):
        kwargs.setdefault("emitida", NOVIEMBRE)
        return create_project_folder(drive, drive.root_id, cliente, SOLICITUD, **kwargs)

    def test_an_existing_client_is_found_ignoring_case_accents_and_suffixes(self):
        drive = FakeDriveTree.de_clientes({"Energía Norte S.A.S.": {}}, fecha=NOVIEMBRE)
        resultado = self.crear(drive, "ENERGIA NORTE")
        self.assertEqual(resultado.client["name"], "Energía Norte S.A.S.")
        self.assertFalse(resultado.client_created)
        self.assertEqual(drive.created, [f"{BASE}/Energía Norte S.A.S./{SOLICITUD}",
                                         f"{BASE}/Energía Norte S.A.S./{SOLICITUD}/anexos"])

    def test_an_unknown_client_is_not_created_without_confirmation_and_nothing_is_created(self):
        drive = FakeDriveTree({})
        with self.assertRaises(ClientNotFound):
            self.crear(drive, "Cliente Nuevo")
        self.assertEqual(drive.created, [])           # ni siquiera el año ni el mes

    def test_a_confirmed_new_client_creates_year_month_client_request_and_annexes(self):
        drive = FakeDriveTree({})
        resultado = self.crear(drive, " Cliente Nuevo ", create_client=True)
        self.assertTrue(resultado.client_created)
        self.assertEqual(drive.created, ["Proyectos/2026", BASE, f"{BASE}/Cliente Nuevo",
                                         f"{BASE}/Cliente Nuevo/{SOLICITUD}", f"{BASE}/Cliente Nuevo/{SOLICITUD}/anexos"])

    def test_the_client_name_is_sanitized_for_drive(self):
        drive = FakeDriveTree({})
        resultado = self.crear(drive, "Obra: Norte/Sur?", create_client=True)
        self.assertEqual(resultado.client["name"], "Obra Norte Sur")

    def test_an_empty_client_name_is_rejected_even_when_creating(self):
        with self.assertRaises(ErrorDeValidacion):
            self.crear(FakeDriveTree({}), "  ", create_client=True)

    def test_a_client_that_exists_in_another_month_is_reused_without_asking(self):
        drive = FakeDriveTree({"2026": {"10-Octubre": {"Minera Sur": {}}}})
        resultado = self.crear(drive, "minera sur", auto_create_client=False)
        self.assertFalse(resultado.client_created)
        self.assertEqual(drive.created, [BASE, f"{BASE}/Minera Sur", f"{BASE}/Minera Sur/{SOLICITUD}",
                                         f"{BASE}/Minera Sur/{SOLICITUD}/anexos"])

    def test_a_client_from_the_previous_year_is_also_recognised(self):
        drive = FakeDriveTree({"2026": {"12-Diciembre": {"Minera Sur": {}}}})
        resultado = self.crear(drive, "Minera Sur", emitida=ENERO_2027)
        self.assertFalse(resultado.client_created)
        self.assertEqual(drive.path(resultado.folder["id"]), f"Proyectos/2027/01-Enero/Minera Sur/{SOLICITUD}")

    def test_a_client_two_years_back_is_not_known(self):
        drive = FakeDriveTree({"2025": {"05-Mayo": {"Minera Sur": {}}}})
        with self.assertRaises(ClientNotFound):
            self.crear(drive, "Minera Sur", emitida=ENERO_2027)

    def test_a_similar_client_in_another_month_blocks_the_automatic_creation(self):
        drive = FakeDriveTree({"2026": {"10-Octubre": {"Solaris Energy Colombia": {}}}})
        with self.assertRaises(SimilarClient) as caught:
            self.crear(drive, "Solaris Energy", auto_create_client=True)
        self.assertIn("Solaris Energy Colombia", str(caught.exception))
        self.assertEqual(drive.created, [])

    def test_ambiguous_names_across_months_need_an_exact_match(self):
        drive = FakeDriveTree({"2026": {"10-Octubre": {"Acme": {}}, "11-Noviembre": {"ACME SAS": {}}}})
        with self.assertRaises(AmbiguousClient):
            self.crear(drive, "acme corp", create_client=True)
        resultado = self.crear(drive, "Acme")
        self.assertEqual(resultado.client["name"], "Acme")
        self.assertFalse(resultado.client_created)


# Cubre: R4, R7, U-102
class ProjectFolderTests(unittest.TestCase):
    def test_it_is_idempotent_and_the_annexes_folder_exists_once(self):
        drive = FakeDriveTree.de_clientes({"Minera Sur": {}}, fecha=NOVIEMBRE)
        primera = create_project_folder(drive, drive.root_id, "Minera Sur", SOLICITUD, emitida=NOVIEMBRE)
        otra_vez = create_project_folder(drive, drive.root_id, "MINERA SUR S.A.S.", SOLICITUD, emitida=NOVIEMBRE)
        self.assertEqual(drive.path(primera.folder["id"]), f"{BASE}/Minera Sur/{SOLICITUD}")
        self.assertEqual(primera.folder["id"], otra_vez.folder["id"])
        self.assertEqual(len(drive.created), 2)       # la solicitud y su `anexos`, una sola vez

    def test_the_year_and_the_month_come_from_the_issue_date(self):
        drive = FakeDriveTree.de_clientes({"Minera Sur": {}}, fecha=NOVIEMBRE)
        resultado = create_project_folder(drive, drive.root_id, "Minera Sur", SOLICITUD, emitida=ENERO_2027,
                                          create_client=True)
        self.assertEqual(drive.path(resultado.folder["id"]), f"Proyectos/2027/01-Enero/Minera Sur/{SOLICITUD}")

    def test_the_same_request_in_another_month_is_not_duplicated(self):
        drive = FakeDriveTree({"2026": {"10-Octubre": {"Minera Sur": {SOLICITUD: {"anexos": {}}}}}})
        with self.assertRaises(SimilarProject) as caught:
            create_project_folder(drive, drive.root_id, "Minera Sur", SOLICITUD, emitida=NOVIEMBRE)
        self.assertIn("otro mes", str(caught.exception))
        self.assertEqual(drive.created, [])

    def test_a_similar_name_in_another_month_blocks_unless_a_person_allows_it(self):
        drive = FakeDriveTree({"2026": {"10-Octubre": {"Minera Sur": {"T&E26100 Mina Norte Fase 1": {}}}}})
        with self.assertRaises(SimilarProject) as caught:
            create_project_folder(drive, drive.root_id, "Minera Sur", SOLICITUD, emitida=NOVIEMBRE)
        self.assertIn("nombre parecido", str(caught.exception))
        create_project_folder(drive, drive.root_id, "Minera Sur", SOLICITUD, emitida=NOVIEMBRE,
                              allow_similar_project=True)
        self.assertIn(f"{BASE}/Minera Sur/{SOLICITUD}", drive.created)

    def test_the_same_attachment_inside_the_annexes_of_another_request_is_found(self):
        drive = FakeDriveTree({"2026": {"10-Octubre": {"Minera Sur": {"T&E26100 Estudio vial": {"anexos": {}}}}}})
        anexos = drive.folder_id("2026/10-Octubre/Minera Sur/T&E26100 Estudio vial/anexos")
        drive.add_file(anexos, "ET Topografia Llanos 8.pdf")
        with self.assertRaises(SimilarProject) as caught:
            create_project_folder(drive, drive.root_id, "Minera Sur", SOLICITUD, emitida=NOVIEMBRE,
                                  attachment_names=["et topografia llanos 8.PDF"])
        self.assertIn("mismo archivo", str(caught.exception))


# Cubre: R4, U-102
class ParentFolderTests(unittest.TestCase):
    def test_a_parent_named_after_a_year_is_flagged(self):
        drive = FakeDriveTree(root_name="2026")
        aviso = padre_invalido(drive, drive.root_id)
        self.assertIn("2026", aviso)
        self.assertIn("Proyectos", aviso)
        self.assertIn("drive_parent_id", aviso)

    def test_projects_or_any_other_name_is_fine(self):
        for nombre in ("Proyectos", "Cotizaciones", "2026 Proyectos"):
            with self.subTest(nombre=nombre):
                drive = FakeDriveTree(root_name=nombre)
                self.assertIsNone(padre_invalido(drive, drive.root_id))

    def test_nothing_is_created_under_a_year_folder(self):
        drive = FakeDriveTree({"Minera Sur": {}}, root_name="2026")
        with self.assertRaises(DriveLayoutError):
            create_project_folder(drive, drive.root_id, "Minera Sur", SOLICITUD, emitida=NOVIEMBRE)
        self.assertEqual(drive.created, [])


# Cubre: U-102
class TrashTests(unittest.TestCase):
    def test_a_request_in_the_new_layout_can_be_trashed(self):
        drive = FakeDriveTree.de_clientes({"Minera Sur": {SOLICITUD: {}}}, fecha=NOVIEMBRE)
        carpeta = drive.folder_id(f"2026/11-Noviembre/Minera Sur/{SOLICITUD}")
        self.assertTrue(trash_project_folder(drive, drive.root_id, carpeta))
        self.assertIn(carpeta, drive.trashed)

    def test_a_request_made_with_the_previous_layout_can_still_be_trashed(self):
        drive = FakeDriveTree({"Minera Sur": {"Cotizacion - Proyecto A - aaaaaaaa": {}}})
        carpeta = drive.folder_id("Minera Sur/Cotizacion - Proyecto A - aaaaaaaa")
        self.assertTrue(trash_project_folder(drive, drive.root_id, carpeta))

    def test_a_client_or_month_folder_is_never_trashed(self):
        drive = FakeDriveTree.de_clientes({"Minera Sur": {SOLICITUD: {}}}, fecha=NOVIEMBRE)
        for ruta in ("2026", "2026/11-Noviembre", "2026/11-Noviembre/Minera Sur"):
            with self.subTest(ruta=ruta), self.assertRaises(DriveLayoutError):
                trash_project_folder(drive, drive.root_id, drive.folder_id(ruta))
        self.assertEqual(drive.trashed, set())

    def test_a_folder_outside_the_configured_parent_is_never_trashed(self):
        drive = FakeDriveTree({"Minera Sur": {SOLICITUD: {}}})
        otro = FakeDriveTree({"X": {"Y": {}}}, root_name="Otra", root_id="otra")
        with self.assertRaises(DriveLayoutError):
            trash_project_folder(otro, drive.root_id, otro.folder_id("X/Y"))


if __name__ == "__main__":
    unittest.main()
