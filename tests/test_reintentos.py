"""Hilos que fallan: espera creciente, caso visible y recuperación (T-26). Cubre: T-26"""

from tests import _isolate  # noqa: F401  (debe ir primero)

import io
import json
import tempfile
import unittest
from contextlib import redirect_stdout
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch

from anka.dominio import reintentos as dominio
from anka.dominio.errores import ContextoInsuficiente, ErrorDeAnka, ModoNoPermitido
from anka.infra import settings, store
from anka.servicios import estado_de_revision, pipeline, reintentos
from tests.fake_drive import FakeDriveTree
from tests.test_app import FakeGmail, complete_analysis, encoded_message, test_config

AHORA = datetime(2026, 10, 8, 10, 0).astimezone()


class EsperasTests(unittest.TestCase):
    def test_the_wait_grows_with_each_failure_and_never_gives_up(self):
        minutos = [dominio.espera_tras(n) // timedelta(minutes=1) for n in (0, 1, 2, 3, 4, 5, 6, 7, 50)]
        self.assertEqual(minutos, [5, 5, 15, 60, 240, 720, 1440, 1440, 1440])

    def test_the_reason_is_readable_and_short(self):
        self.assertEqual(dominio.motivo_legible(ErrorDeAnka("El modelo no   respondió")), "El modelo no respondió")
        self.assertEqual(dominio.motivo_legible(RuntimeError("Ollama no respondió en 180 s")),
                         "Ollama no respondió en 180 s")
        self.assertEqual(dominio.motivo_legible(ValueError("respuesta inesperada")), "ValueError: respuesta inesperada")
        self.assertEqual(dominio.motivo_legible(KeyError()), "KeyError")
        self.assertLessEqual(len(dominio.motivo_legible(ErrorDeAnka("x" * 5000))), 300)

    def test_the_summary_tells_the_person_what_happened_and_what_to_do(self):
        resumen = dominio.resumen_del_fallo("Ollama no respondió.", 3, AHORA)
        for esperado in ("intento 3", "Ollama no respondió.", "10:00", "Buscar correos nuevos", "a mano"):
            self.assertIn(esperado, resumen)

    def test_the_failure_case_is_a_minimal_technical_review_analysis(self):
        analisis = dominio.analisis_de_fallo("Solicitud Llanos 8", "c@x.co", "2026-10-08T09:00:00-05:00",
                                             {"selected_recipient": "c@x.co"}, "no cupo", 2, AHORA)
        self.assertEqual((analisis["project_name"], analisis["activities"]), ("Solicitud Llanos 8", []))
        self.assertEqual(analisis["analysis_failure"]["intentos"], 2)
        self.assertIn("no cupo", analisis["summary"])


class RegistroTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.db = store.open_db(Path(self.temp.name))
        self.addCleanup(self.db.close)

    def test_each_failure_lengthens_the_wait(self):
        primero = reintentos.registrar_fallo(self.db, "t-1", "m1", AHORA)
        segundo = reintentos.registrar_fallo(self.db, "t-1", "m2", AHORA)
        self.assertEqual((primero[0], segundo[0]), (1, 2))
        self.assertEqual(primero[1] - AHORA, timedelta(minutes=5))
        self.assertEqual(segundo[1] - AHORA, timedelta(minutes=15))

    def test_a_thread_waits_until_its_turn_and_then_is_free(self):
        reintentos.registrar_fallo(self.db, "t-1", "m", AHORA)
        self.assertIsNotNone(reintentos.proximo_intento(self.db, "t-1", AHORA + timedelta(minutes=4)))
        self.assertIsNone(reintentos.proximo_intento(self.db, "t-1", AHORA + timedelta(minutes=6)))
        self.assertIsNone(reintentos.proximo_intento(self.db, "otro", AHORA))

    def test_a_good_analysis_forgets_the_failures(self):
        reintentos.registrar_fallo(self.db, "t-1", "m", AHORA)
        reintentos.limpiar(self.db, "t-1")
        reintentos.limpiar(self.db, "t-1")  # repetirlo no falla
        self.assertEqual(self.db.execute("SELECT COUNT(*) FROM analysis_failures").fetchone()[0], 0)

    def test_old_records_are_purged_and_a_damaged_one_never_blocks_a_mail(self):
        reintentos.registrar_fallo(self.db, "viejo", "m", AHORA - timedelta(days=dominio.RETENCION_DIAS + 1))
        reintentos.registrar_fallo(self.db, "nuevo", "m", AHORA)
        self.assertEqual([f[0] for f in self.db.execute("SELECT thread_id FROM analysis_failures")], ["nuevo"])
        self.db.execute("UPDATE analysis_failures SET next_retry_at='no es una fecha'")
        self.db.commit()
        self.assertIsNone(reintentos.proximo_intento(self.db, "nuevo", AHORA))

    def test_the_table_exists_after_migrating(self):
        tablas = {fila[0] for fila in self.db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        self.assertIn("analysis_failures", tablas)


class FlujoTests(unittest.TestCase):
    """Pasadas reales de `pipeline.run` sobre la misma base, con un análisis que falla o se recupera."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.gmail = FakeGmail({"id": "t-1", "messages": [
            encoded_message("m-1", "cliente@example.com", "Solicito cotización del levantamiento.", 1,
                            subject="Solicitud Llanos 8")]})
        for parche in (
            patch.object(settings, "DATA_DIR", Path(self.temp.name)),
            patch("anka.servicios.pipeline.load_config", return_value=test_config()),
            patch("anka.servicios.pipeline.services", return_value=(self.gmail, FakeDriveTree({"Minera Sur": {}}))),
        ):
            parche.start()
            self.addCleanup(parche.stop)

    def pasada(self, analisis, **flags):
        """Una revisión; `analisis` es lo que devuelve (o lanza) el análisis del hilo."""
        efecto = {"side_effect": analisis} if isinstance(analisis, Exception) else {"return_value": analisis}
        with patch("anka.servicios.pipeline.analizar_hilo", **efecto) as mock, redirect_stdout(io.StringIO()):
            with self.assertLogs("anka.servicios.pipeline", level="DEBUG"):
                pipeline.run(True, review_only=True, **flags)
        return mock

    def estado(self):
        db = store.connect()
        try:
            fallo = db.execute("SELECT failures, last_error FROM analysis_failures").fetchone()
            caso = db.execute("SELECT status, last_inbound_id, analysis_json FROM quotes").fetchone()
        finally:
            db.close()
        return (dict(fallo) if fallo else None), (dict(caso) if caso else None)

    def adelantar_el_reloj(self):
        db = store.connect()
        db.execute("UPDATE analysis_failures SET next_retry_at='2000-01-01T00:00:00+00:00'")
        db.commit()
        db.close()

    def test_a_failing_thread_becomes_a_visible_technical_review_case(self):
        self.pasada(RuntimeError("Ollama no respondió en 180 s"))
        fallo, caso = self.estado()
        self.assertEqual(fallo["failures"], 1)
        self.assertEqual(caso["status"], "review")
        self.assertEqual(caso["last_inbound_id"], "")  # al vencer la espera se vuelve a analizar
        analisis = json.loads(caso["analysis_json"])
        self.assertEqual(analisis["project_name"], "Solicitud Llanos 8")
        self.assertIn("Ollama no respondió en 180 s", analisis["summary"])
        self.assertIn("intento 1", analisis["summary"])

    def test_the_thread_is_not_retried_while_it_waits(self):
        self.pasada(RuntimeError("falla"))
        segunda = self.pasada(RuntimeError("falla"))
        segunda.assert_not_called()
        self.assertEqual(self.estado()[0]["failures"], 1)

    def test_when_the_wait_is_over_it_is_retried_and_the_wait_grows(self):
        self.pasada(RuntimeError("falla"))
        self.adelantar_el_reloj()
        self.pasada(RuntimeError("sigue fallando"))
        fallo, caso = self.estado()
        self.assertEqual((fallo["failures"], fallo["last_error"]), (2, "sigue fallando"))
        self.assertIn("intento 2", json.loads(caso["analysis_json"])["summary"])

    def test_a_person_asking_for_the_review_skips_the_wait(self):
        self.pasada(RuntimeError("falla"))
        manual = self.pasada(RuntimeError("falla"), respetar_espera=False)
        manual.assert_called_once()
        self.assertEqual(self.estado()[0]["failures"], 2)

    def test_a_good_analysis_replaces_the_failure_case_and_clears_the_record(self):
        self.pasada(RuntimeError("falla"))
        self.adelantar_el_reloj()
        self.pasada(complete_analysis() | {"received_at": "2026-10-08T09:00:00-05:00"})
        fallo, caso = self.estado()
        self.assertIsNone(fallo)
        self.assertNotIn("analysis_failure", caso["analysis_json"])
        self.assertEqual(caso["last_inbound_id"], "m-1")

    def test_a_thread_with_a_valid_analysis_is_never_overwritten_by_a_failure_notice(self):
        self.pasada(complete_analysis() | {"received_at": "2026-10-08T09:00:00-05:00"})
        antes = self.estado()[1]["analysis_json"]
        db = store.connect()
        db.execute("UPDATE quotes SET last_inbound_id=''")  # fuerza el reanálisis
        db.commit()
        db.close()
        self.pasada(RuntimeError("falla"))
        fallo, caso = self.estado()
        self.assertEqual(fallo["failures"], 1)  # el fallo se anota...
        self.assertEqual(caso["analysis_json"], antes)  # ...pero el análisis válido queda intacto

    def test_an_input_too_big_for_the_model_is_also_recorded(self):
        self.pasada(ContextoInsuficiente("no cabe en la ventana del modelo"))
        fallo, caso = self.estado()
        self.assertEqual(fallo["failures"], 1)
        self.assertIn("no cabe en la ventana", json.loads(caso["analysis_json"])["summary"])

    def test_a_mode_change_is_not_a_failure_of_the_thread(self):
        with self.assertRaises(ModoNoPermitido):
            self.pasada(ModoNoPermitido("cambió a Work"))
        self.assertIsNone(self.estado()[0])


class ActualizarCorreoTests(unittest.TestCase):
    def llamada(self, fuente):
        with tempfile.TemporaryDirectory() as temp, patch.object(settings, "DATA_DIR", Path(temp)), \
                patch("anka.servicios.estado_de_revision.run") as run:
            estado_de_revision.checked_run(True, False, True, fuente)
        return run.call_args

    def test_the_pilot_respects_the_waits_and_a_button_press_does_not(self):
        self.assertEqual(self.llamada("piloto").kwargs, {})
        self.assertEqual(self.llamada("dashboard").kwargs, {"respetar_espera": False})


if __name__ == "__main__":
    unittest.main()


class PantallasTests(unittest.TestCase):
    """El caso de fallo se pinta en la lista y en su propia pantalla, sin actividades y con el motivo a la vista."""

    def test_the_dashboard_shows_the_failure_case_and_its_reason(self):
        from anka.servicios import quotes
        from anka.web import pantalla_caso, paginas

        with tempfile.TemporaryDirectory() as temp, patch.object(settings, "DATA_DIR", Path(temp)),                 patch("anka.web.pantalla_caso.load_config", return_value=test_config()),                 patch("anka.web.paginas.load_config", return_value=test_config(), create=True):
            db = store.connect()
            analisis = dominio.analisis_de_fallo("Solicitud Llanos 8", "c@example.com", "2026-10-08T09:00:00-05:00",
                                                 {"selected_recipient": "c@example.com", "is_forwarded": False},
                                                 "Ollama no respondió en 180 s.", 2, AHORA)
            store.save(db, "t-fallo", "", "review", "c@example.com", analisis)
            db.close()
            pagina = pantalla_caso.render_case("t-fallo")
            self.assertIn("Ollama no respondió en 180 s.", pagina)
            self.assertIn("intento 2", pagina)
            self.assertIn("Solicitud Llanos 8", paginas.render_index())
            self.assertEqual(quotes.get_quote("t-fallo")["status"], "review")
