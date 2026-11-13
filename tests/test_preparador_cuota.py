"""Cubre: T-40. El preparador no relee hilos sin cambios, trabaja en lotes y se detiene ante la cuota de Gmail."""

from tests import _isolate  # noqa: F401  (debe ir primero)

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from googleapiclient.errors import HttpError

from anka.infra import instantaneas_de_manifiesto as copias
from anka.infra import settings
from anka.infra.mail import es_error_de_cuota
from anka.servicios import work_drive as work
from tests.fake_work_drive import MemoryDrive
from tests.test_work_drive import message


def error_http(status, texto):
    return HttpError(SimpleNamespace(status=status, reason="x"), texto.encode())


class _Exec:
    def __init__(self, valor=None, error=None):
        self.valor, self.error = valor, error

    def execute(self):
        if self.error:
            raise self.error
        return self.valor


class GmailDeHilos:
    """Gmail mínimo para el preparador: lista de hilos con su `historyId` y hilos completos que cuentan sus lecturas."""

    def __init__(self, hilos, versiones):
        self.hilos, self.versiones = hilos, versiones
        self.lecturas = []
        self.error_en = {}

    def users(self):
        return self

    def getProfile(self, **_):
        return _Exec({"emailAddress": "anka@example.com"})

    def threads(self):
        return self

    def list(self, **_):
        return _Exec({"threads": [{"id": t, "historyId": v} for t, v in self.versiones.items()]})

    def get(self, id, **_):
        self.lecturas.append(id)
        return _Exec(self.hilos[id], self.error_en.get(id))


class PreparadorTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        patcher = patch.object(settings, "DATA_DIR", Path(self.temp.name))
        patcher.start()
        self.addCleanup(patcher.stop)
        self.drive = MemoryDrive()
        self.config = {"active_mode": "chatgpt_work", "work_drive_enabled": True,
                       "work_drive_parent_id": "reception", "work_drive_query": "label:piloto",
                       "work_sync_account": "anka@example.com", "required_fields": [],
                       "llm": {"provider": "ollama", "base_url": "http://localhost:11434"}}

    def gmail(self, cuantos=2):
        hilos = {f"t{i}": {"id": f"t{i}", "messages": [message(f"m{i}")]} for i in range(cuantos)}
        return GmailDeHilos(hilos, {t: "100" for t in hilos})

    def pasada(self, gmail):
        return work.sync_work_drive(gmail, self.drive, self.config)

    def test_un_hilo_sin_cambios_en_gmail_no_se_vuelve_a_bajar(self):
        # Cubre: T-40
        gmail = self.gmail()
        primera = self.pasada(gmail)
        self.assertEqual((primera["prepared"], primera["skipped"]), (2, 0))
        gmail.lecturas.clear()
        segunda = self.pasada(gmail)
        self.assertEqual((segunda["prepared"], segunda["unchanged"], segunda["skipped"]), (0, 0, 2))
        self.assertEqual(gmail.lecturas, [])

    def test_un_hilo_que_cambio_se_baja_otra_vez(self):
        # Cubre: T-40
        gmail = self.gmail()
        self.pasada(gmail)
        gmail.lecturas.clear()
        gmail.versiones["t1"] = "101"
        resultado = self.pasada(gmail)
        self.assertEqual(gmail.lecturas, ["t1"])
        self.assertEqual((resultado["unchanged"], resultado["skipped"]), (1, 1))   # misma revisión: sin re-OCR

    def test_si_faltan_las_copias_locales_se_baja_aunque_no_haya_cambiado(self):
        # Cubre: T-40
        gmail = self.gmail(1)
        self.pasada(gmail)
        (Path(self.temp.name) / "manifiestos" / "t0.textos.json").unlink()
        gmail.lecturas.clear()
        self.pasada(gmail)
        self.assertEqual(gmail.lecturas, ["t0"])
        self.assertIsNotNone(copias.cargar_textos(Path(self.temp.name) / "manifiestos", "t0"))

    def test_baja_como_maximo_seis_hilos_por_pasada_y_el_resto_espera(self):
        # Cubre: T-40
        gmail = self.gmail(9)
        resultado = self.pasada(gmail)
        self.assertEqual((resultado["prepared"], resultado["deferred"], len(gmail.lecturas)), (6, 3, 6))
        siguiente = self.pasada(gmail)
        self.assertEqual((siguiente["prepared"], siguiente["deferred"], siguiente["skipped"]), (3, 0, 6))

    def test_los_hilos_que_nunca_se_prepararon_van_primero(self):
        # Cubre: T-40
        gmail = self.gmail(3)
        self.pasada(gmail)
        gmail.hilos["t9"] = {"id": "t9", "messages": [message("m9")]}
        gmail.versiones.update({"t9": "100", "t0": "101"})
        gmail.lecturas.clear()
        self.pasada(gmail)
        self.assertEqual(gmail.lecturas, ["t9", "t0"])   # el nuevo antes que el que solo cambió

    def test_ante_la_cuota_de_gmail_la_pasada_termina_sin_contar_fallos(self):
        # Cubre: T-40
        gmail = self.gmail(4)
        gmail.error_en["t1"] = error_http(403, "Quota exceeded for quota metric 'Total Query Cost'")
        resultado = self.pasada(gmail)
        self.assertEqual((resultado["prepared"], resultado["failures"], resultado["deferred"]), (1, 0, 3))
        self.assertTrue(resultado["quota"])
        self.assertEqual(gmail.lecturas, ["t0", "t1"])           # no insiste con los demás
        gmail.error_en.clear()
        gmail.lecturas.clear()
        siguiente = self.pasada(gmail)
        self.assertEqual((siguiente["prepared"], siguiente["quota"]), (3, False))

    def test_otro_error_sigue_siendo_un_fallo_del_hilo_y_no_detiene_la_pasada(self):
        # Cubre: T-40
        gmail = self.gmail(2)
        gmail.error_en["t0"] = error_http(500, "error interno")
        with self.assertLogs(work.log, level="ERROR"):
            resultado = self.pasada(gmail)
        self.assertEqual((resultado["failures"], resultado["prepared"], resultado["quota"]), (1, 1, False))

    def test_pedir_un_hilo_concreto_lo_prepara_siempre(self):
        # Cubre: T-40
        gmail = self.gmail(1)
        self.pasada(gmail)
        gmail.lecturas.clear()
        resultado = work.sync_work_drive(gmail, self.drive, self.config, "t0")
        self.assertEqual((gmail.lecturas, resultado["unchanged"]), (["t0"], 1))

    def test_se_reconoce_el_error_de_cuota(self):
        # Cubre: T-40
        self.assertTrue(es_error_de_cuota(error_http(429, "demasiadas")))
        self.assertTrue(es_error_de_cuota(error_http(403, "rateLimitExceeded")))
        self.assertFalse(es_error_de_cuota(error_http(403, "permiso denegado")))
        self.assertFalse(es_error_de_cuota(ConnectionError("offline")))


if __name__ == "__main__":
    unittest.main()
