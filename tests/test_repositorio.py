"""`RepositorioDeCasos` (anka/servicios/repositorio.py): se prueba con una base propia, sin tocar la carpeta de datos."""

from tests import _isolate  # noqa: F401  (debe ir primero)

import tempfile
import unittest
from pathlib import Path

from anka.dominio.caso import Caso
from anka.dominio.status import QuoteStatus
from anka.infra import store
from anka.servicios.repositorio import RepositorioDeCasos


# Cubre: T-10
class RepositoryTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.folder = Path(self._tmp.name)
        self.repo = RepositorioDeCasos(abrir=lambda: store.open_db(self.folder))   # sin settings.DATA_DIR
        db = store.open_db(self.folder)
        for thread, status in (("t-1", QuoteStatus.REVIEW_PENDING), ("t-2", QuoteStatus.IGNORED)):
            store.save(db, thread, f"m-{thread}", status, "ana@acme.com",
                       {"project_name": f"Proyecto {thread}", "received_at": "2026-10-01 09:00",
                        "activities": [{"activity": "Levantamiento", "servicio_tecnica": "Topografía"}],
                        "ignored_reason": "ruido"})
        db.close()

    def test_lists_and_gets_typed_cases_and_hides_discarded_by_default(self):
        self.assertEqual({c["thread_id"] for c in self.repo.listar()}, {"t-1"})
        self.assertEqual({c["thread_id"] for c in self.repo.listar(True)}, {"t-1", "t-2"})
        caso = self.repo.obtener("t-1")
        self.assertIsInstance(caso, Caso)
        self.assertEqual(caso.services, ["Topografía"])
        self.assertIsNone(self.repo.obtener("no-existe"))

    def test_lists_discarded_cases_with_their_reason(self):
        self.assertEqual([d["reason"] for d in self.repo.descartados()], ["ruido"])

    def test_commercial_data_is_saved_and_shows_in_the_case(self):
        self.repo.guardar_datos_comerciales("t-1", " ACME ", "Empresa privada", "Energía", " nota ")
        caso = self.repo.obtener("t-1")
        self.assertEqual((caso.client_name_saved, caso.client_type, caso.sector, caso.notes),
                         ("ACME", "Empresa privada", "Energía", "nota"))
        self.repo.fijar_cliente("t-1", "ACME S.A.S.")
        self.assertEqual(self.repo.obtener("t-1").client_name_saved, "ACME S.A.S.")
        self.assertEqual(self.repo.obtener("t-1").sector, "Energía")   # no se tocó el resto

    def test_deleting_removes_the_case_but_keeps_the_history_as_evidence(self):
        self.assertEqual(self.repo.eliminar(["t-1", "t-1", "fantasma"]), ["t-1"])
        self.assertIsNone(self.repo.obtener("t-1"))
        self.assertEqual([a["thread_id"] for a in self.repo.archivados()], ["t-1"])
        self.assertEqual(self.repo.integridad("t-1"), (True, 1))
        self.assertEqual(self.repo.eliminar([]), [])

    def test_only_discarded_cases_can_be_marked_for_reanalysis(self):
        with self.assertRaises(ValueError):
            self.repo.marcar_para_reanalisis("t-1")
        with self.assertRaises(ValueError):
            self.repo.marcar_para_reanalisis("no-existe")
        self.repo.marcar_para_reanalisis("t-2")
        self.assertTrue(self.repo.obtener("t-2").analysis["force_analysis"])

    def test_a_case_without_conversation_or_people_is_empty_not_an_error(self):
        self.assertEqual(self.repo.conversacion("t-1"), [])
        self.assertEqual(self.repo.personas("t-1", set()), [])
        self.assertFalse(self.repo.tiene_vinculos("t-1"))


if __name__ == "__main__":
    unittest.main()
