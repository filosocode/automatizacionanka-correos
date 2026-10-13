"""Salud de las conexiones (anka/servicios/salud.py y GET /api/salud, spec/07-api.md, requisito T-14)."""

from tests import _isolate  # noqa: F401  (debe ir primero)

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from anka.dominio.errores import ErrorDeAnka
from anka.infra import settings
from anka.infra import store as base
from anka.servicios import salud
from tests.test_openapi import request

LOCAL = {"active_mode": "local", "llm": {"provider": "ollama", "model": "m", "base_url": "http://x"},
         "required_fields": []}
WORK = {"active_mode": "chatgpt_work", "llm": {"provider": "ollama", "model": "m"}, "required_fields": [],
        "drive_parent_id": "carpeta-1"}


def by_name(report):
    return {c["componente"]: c for c in report["comprobaciones"]}


# Cubre: T-14
class HealthTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.folder = Path(self._tmp.name)
        base.open_db(self.folder).close()
        (self.folder / "token.json").write_text("{}", encoding="utf-8")
        patcher = patch.object(settings, "DATA_DIR", self.folder)
        patcher.start()
        self.addCleanup(patcher.stop)

    def config(self, value):
        patcher = patch("anka.servicios.salud.settings.load_config", return_value=value)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_all_good_in_local_mode_is_ok_and_does_not_call_google(self):
        self.config(LOCAL)
        with patch("anka.servicios.salud.check_ollama"), patch("anka.servicios.salud.services") as google:
            report = salud.informe()
        self.assertEqual(report["estado"], "ok")
        self.assertEqual(by_name(report)["puente_work"]["estado"], "no_aplica")
        self.assertNotIn("gmail", by_name(report))
        google.assert_not_called()                       # lo superficial no gasta llamadas a Google

    def test_the_work_bridge_must_be_recent_in_work_mode(self):
        self.config(WORK)
        with patch("anka.servicios.salud.bridge_state", return_value=("ok", "Puente Work activo.")):
            ok = salud.informe()
        with patch("anka.servicios.salud.bridge_state", return_value=("stopped", "Puente Work DETENIDO.")):
            down = salud.informe()
        self.assertEqual((ok["estado"], by_name(ok)["puente_work"]["estado"]), ("ok", "ok"))
        self.assertEqual((down["estado"], by_name(down)["puente_work"]["estado"]), ("error", "error"))
        self.assertIn("DETENIDO", by_name(down)["puente_work"]["detalle"])
        self.assertEqual(by_name(down)["motor_de_analisis"]["estado"], "no_aplica")   # Work analiza, no Anka

    def test_deep_mode_really_asks_gmail_and_drive_and_reports_which_one_fails(self):
        self.config(WORK)
        gmail, drive = MagicMock(), MagicMock()
        drive.files().get().execute.side_effect = RuntimeError("404 <datos que no deben salir>")
        with patch("anka.servicios.salud.bridge_state", return_value=("ok", "ok")), \
                patch("anka.servicios.salud.services", return_value=(gmail, drive)), \
                patch("anka.servicios.salud.credentials"):
            report = salud.informe(profundo=True)
        checks = by_name(report)
        self.assertEqual(checks["gmail"]["estado"], "ok")
        self.assertEqual(checks["drive"]["estado"], "error")
        self.assertEqual(report["estado"], "error")
        self.assertNotIn("datos que no deben salir", json.dumps(report))     # el texto del error no se expone
        gmail.users().getProfile.assert_called()

    def test_a_missing_authorization_is_an_error_without_calling_google(self):
        (self.folder / "token.json").unlink()
        self.config(LOCAL)
        with patch("anka.servicios.salud.check_ollama"):
            report = salud.informe()
        self.assertEqual(by_name(report)["autorizacion_google"]["estado"], "error")
        self.assertIn("python app.py auth", by_name(report)["autorizacion_google"]["detalle"])

    def test_ollama_down_and_missing_openai_key_are_errors_with_the_reason(self):
        self.config(LOCAL)
        with patch("anka.servicios.salud.check_ollama", side_effect=ErrorDeAnka("Ollama no responde en http://x")):
            local = salud.informe()
        self.assertIn("Ollama no responde", by_name(local)["motor_de_analisis"]["detalle"])
        self.config({**LOCAL, "active_mode": "api"})
        with patch.dict("os.environ", {}, clear=True):
            api = salud.informe()
        self.assertEqual(by_name(api)["motor_de_analisis"]["estado"], "error")

    def test_an_invalid_configuration_stops_the_rest_instead_of_crashing(self):
        patcher = patch("anka.servicios.salud.settings.load_config", side_effect=ErrorDeAnka("Crea config.json"))
        patcher.start()
        self.addCleanup(patcher.stop)
        report = salud.informe()
        self.assertEqual([c["componente"] for c in report["comprobaciones"]], ["base_de_datos", "configuracion"])
        self.assertEqual(report["estado"], "error")


# Cubre: T-14
class HealthRoute(unittest.TestCase):
    def test_it_answers_200_when_everything_is_fine_and_503_when_something_fails(self):
        good = {"estado": "ok", "profundo": False, "comprobaciones": []}
        bad = {"estado": "error", "profundo": False, "comprobaciones": [
            {"componente": "puente_work", "estado": "error", "detalle": "DETENIDO", "ms": 0}]}
        with patch("anka.web.server.salud.informe", return_value=good) as informe:
            self.assertEqual(request("GET", "/api/salud")["status"], 200)
            informe.assert_called_with(False)
        with patch("anka.web.server.salud.informe", return_value=bad):
            down = request("GET", "/api/salud?profundo=1")
        self.assertEqual(down["status"], 503)
        self.assertEqual(json.loads(down["body"])["comprobaciones"][0]["componente"], "puente_work")

    def test_the_deep_flag_is_passed_through(self):
        with patch("anka.web.server.salud.informe", return_value={"estado": "ok"}) as informe:
            request("GET", "/api/salud?profundo=1")
        informe.assert_called_with(True)

    def test_it_is_documented_with_its_503(self):
        from anka.web import openapi
        operation = openapi.build_spec()["paths"]["/api/salud"]["get"]
        self.assertIn("503", operation["responses"])
        self.assertEqual(operation["responses"]["200"]["content"]["application/json"]["schema"],
                         {"$ref": "#/components/schemas/Salud"})


if __name__ == "__main__":
    unittest.main()
