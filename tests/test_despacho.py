"""Despacho de trabajos a Work: PENDIENTES / CERRADOS, recuperación, idempotencia y límite del piloto (T-53, D-157).

SQLite temporal, Drive simulado y mensajes ficticios; nada toca Google.

Cubre: T-53
"""

from tests import _isolate  # noqa: F401  (debe ir primero)

import copy
import json
import unittest
from unittest.mock import patch

from anka.cli import work_jobs
from anka.dominio import trabajo_work as dominio
from anka.infra import settings, store
from anka.servicios import despacho, trabajos_work
from anka.servicios.despacho import Alcance, CarpetaAmbigua
from anka.servicios.recepcion import CUENTA
from tests.test_settings import VALID
from tests.test_trabajos_work import CARPETA, DriveDeLaboratorio, ErrorHttp, _Respuesta
from tests.test_trabajos_work import Base as BaseDeTrabajos


class DriveConCarpetas(DriveDeLaboratorio):
    """El Drive de laboratorio con subcarpetas, padres y el movimiento entre carpetas."""

    def __init__(self):
        super().__init__()
        self.carpetas: dict[str, dict] = {}
        self.movimientos: list[tuple] = []

    def sembrar_carpeta(self, nombre, padre=CARPETA, papelera=False):
        carpeta_id = f"folder-{len(self.carpetas) + 1}"
        self.carpetas[carpeta_id] = {"id": carpeta_id, "name": nombre, "parents": [padre], "trashed": papelera}
        return carpeta_id

    def id_de(self, nombre):
        return [c["id"] for c in self.carpetas.values() if c["name"] == nombre and not c["trashed"]]

    def list(self, q, fields=None, pageSize=None, pageToken=None):
        if despacho.CARPETA_MIME in q:
            padre = q.split("'")[1]
            nombre = q.split("name = '")[1].rsplit("'", 1)[0]
            return _Respuesta({"files": [dict(c) for c in self.carpetas.values()
                                         if padre in c["parents"] and c["name"] == nombre and not c["trashed"]]})
        return super().list(q, fields, pageSize, pageToken)

    def create(self, body, fields=None, media_body=None):
        if body.get("mimeType") == despacho.CARPETA_MIME:
            def crear():
                return {"id": self.sembrar_carpeta(body["name"], body["parents"][0])}
            return _Respuesta(crear)
        return super().create(body, fields, media_body)

    def get(self, fileId, fields=None):
        def leer():
            archivo = self.archivos.get(fileId)
            if archivo is None:
                raise ErrorHttp(404)
            return {"id": fileId, "trashed": archivo["trashed"], "appProperties": dict(archivo["props"]),
                    "parents": list(archivo["parents"])}
        return _Respuesta(leer)

    def update(self, fileId, body=None, media_body=None, fields=None, addParents=None, removeParents=None):
        if addParents or removeParents:
            def mover():
                archivo = self.archivos[fileId]
                quitar = set((removeParents or "").split(","))
                archivo["parents"] = [p for p in archivo["parents"] if p not in quitar]
                if addParents and addParents not in archivo["parents"]:
                    archivo["parents"].append(addParents)
                self.movimientos.append((fileId, addParents, removeParents))
                return {"id": fileId, "parents": list(archivo["parents"])}
            return _Respuesta(mover)
        return super().update(fileId, body, media_body, fields)


class Base(BaseDeTrabajos):
    def setUp(self):
        super().setUp()
        config = copy.deepcopy(VALID)
        config["commercial_filter_terms"] = ["cotización", "levantamiento"]
        (self.datos / "config.json").write_text(json.dumps(config), encoding="utf-8")
        self.drive = DriveConCarpetas()
        self.alcance = Alcance("T1", "m1")

    def pasada(self, alcance=None):
        return despacho.despachar(self.drive, CARPETA, alcance or self.alcance)

    def trabajos(self):
        with store.lectura() as db:
            return db.execute("SELECT COUNT(*) FROM trabajos_work").fetchone()[0]

    def hojas(self):
        return self.drive.vivos()

    def padres(self, hoja):
        return self.drive.archivos[hoja["id"]]["parents"]

    def importar(self, hilo="T1", mensaje="m1", **extra):
        work_import_ = __import__("anka.servicios.work_import", fromlist=["x"])
        from tests.test_work_sync import b64, payload
        work_import_.dashboard_upsert(b64(json.dumps(payload(thread=hilo, message=mensaje, **extra))))


class PublicacionTests(Base):
    def test_la_primera_pasada_crea_las_carpetas_y_publica_en_pendientes_y_repetir_no_duplica(self):
        self.fila("T1", "m1")
        primera = self.pasada()
        pendientes, cerrados = self.drive.id_de("PENDIENTES"), self.drive.id_de("CERRADOS")
        self.assertEqual((len(pendientes), len(cerrados)), (1, 1))
        self.assertEqual(primera["publicacion"]["resultado"], "creado")
        hoja, = self.hojas()
        self.assertEqual(self.padres(hoja), pendientes)
        for _ in range(3):
            otra = self.pasada()
            self.assertEqual(otra["publicacion"]["resultado"], "ya_publicado")
        self.assertEqual((len(self.hojas()), len(self.drive.carpetas), self.drive.creados), (1, 2, 1))
        self.assertEqual(self.trabajos(), 1)

    def test_el_job_id_y_el_estado_de_t49_no_cambian(self):
        self.fila("T1", "m1")
        informe = self.pasada()
        job = informe["publicacion"]["job_id"]
        self.assertEqual(job, dominio.id_de_trabajo(CUENTA, "T1", "m1", dominio.TIPO_ANALIZAR,
                                                    dominio.VERSION_DEL_CONTRATO))
        self.assertEqual(self.estado(job), "publicado")

    def test_dos_carpetas_con_el_mismo_nombre_detienen_el_despacho_sin_elegir(self):
        self.fila("T1", "m1")
        self.drive.sembrar_carpeta("PENDIENTES")
        self.drive.sembrar_carpeta("PENDIENTES")
        with self.assertRaises(CarpetaAmbigua):
            self.pasada()
        self.assertEqual((self.hojas(), self.trabajos()), ([], 0))

    def test_una_carpeta_en_la_papelera_no_cuenta_y_se_crea_una_nueva(self):
        self.drive.sembrar_carpeta("PENDIENTES", papelera=True)
        self.fila("T1", "m1")
        self.pasada()
        self.assertEqual(len(self.drive.id_de("PENDIENTES")), 1)

    def test_el_ruido_y_la_duda_de_t51_no_generan_trabajo_ni_hoja(self):
        casos = {"N1": ("ChatGPT <noreply@tm.openai.com>", "[Task Update] ANKA 01", "no_elegible"),
                 "N2": ("no-reply@tienda.com", "Ofertas de la semana", "no_elegible"),
                 "N3": ("no-reply@portal.com", "Su cotización fue recibida", "clasificacion_pendiente"),
                 "N4": ("", "Sin remitente", "clasificacion_pendiente")}
        for hilo, (remitente, asunto, esperado) in casos.items():
            with self.subTest(hilo=hilo):
                self.fila(hilo, "m1", remitente=remitente, asunto=asunto)
                informe = self.pasada(Alcance(hilo, "m1"))
                self.assertEqual(informe["publicacion"]["resultado"], esperado)
        self.assertEqual((self.hojas(), self.trabajos()), ([], 0))

    def test_solo_se_despacha_el_mensaje_del_alcance(self):
        for hilo in ("T1", "T2", "T3"):
            self.fila(hilo, "m1")
        self.pasada(Alcance("T2", "m1"))
        self.assertEqual([t["thread_id"] for t in self._todos()], ["T2"])
        self.assertEqual(len(self.hojas()), 1)

    def _todos(self):
        with store.lectura() as db:
            return [dict(r) for r in db.execute("SELECT * FROM trabajos_work")]

    def test_sin_mensaje_concreto_no_hay_despacho(self):
        from anka.dominio.errores import ErrorDeAnka
        self.fila("T1", "m1")
        for alcance in (Alcance("", ""), Alcance("T1", ""), Alcance("", "m1")):
            with self.assertRaises(ErrorDeAnka):
                self.pasada(alcance)
        self.assertEqual((self.drive.carpetas, self.hojas(), self.trabajos()), ({}, [], 0))

    def test_un_mensaje_que_ya_no_esta_pendiente_no_genera_trabajo(self):
        self.fila("T1", "m1", estado="procesado", disposicion="interpretado_por_work")
        self.assertEqual(self.pasada()["publicacion"]["resultado"], "mensaje_no_pendiente")
        self.assertEqual((self.hojas(), self.trabajos()), ([], 0))

    def test_un_mensaje_ambiguo_para_work_se_publica_con_su_clasificacion(self):
        self.fila("T1", "m1", asunto="Hola, ¿podemos hablar?")
        informe = self.pasada()
        self.assertEqual(informe["publicacion"]["resultado"], "creado")
        self.assertEqual(informe["publicacion"]["clasificacion"], "REVISION_MANUAL")


class RecuperacionTests(Base):
    def test_un_fallo_al_publicar_deja_el_trabajo_pendiente_con_su_error_y_se_reintenta(self):
        self.fila("T1", "m1")
        self.drive.fallar_listado = ErrorHttp(500)
        fallida = self.pasada()
        self.assertEqual(fallida["publicacion"]["resultado"], "error")
        job = fallida["publicacion"]["job_id"]
        self.assertEqual((self.estado(job), trabajos_work.obtener(job)["attempts"]), ("pendiente_de_publicar", 1))
        self.assertEqual(self.hojas(), [])
        self.drive.fallar_listado = None
        buena = self.pasada()
        self.assertEqual((buena["publicacion"]["resultado"], len(self.hojas()), self.estado(job)),
                         ("creado", 1, "publicado"))

    def test_tras_el_maximo_de_intentos_queda_agotado_y_no_se_vuelve_a_intentar(self):
        self.fila("T1", "m1")
        self.drive.fallar_listado = ErrorHttp(500)
        job = self.pasada()["publicacion"]["job_id"]
        with store.transaccion() as db:
            db.execute("UPDATE trabajos_work SET attempts=? WHERE job_id=?", (despacho.MAXIMO_DE_INTENTOS, job))
        self.drive.fallar_listado = None
        informe = self.pasada()
        self.assertEqual(informe["publicacion"]["resultado"], "agotado")
        self.assertEqual((self.hojas(), self.drive.creados), ([], 0))

    def test_una_respuesta_perdida_al_crear_la_hoja_se_adopta_en_la_pasada_siguiente(self):
        self.fila("T1", "m1")
        self.drive.perder_respuesta_de_crear = True
        self.assertEqual(self.pasada()["publicacion"]["resultado"], "error")
        self.drive.perder_respuesta_de_crear = False
        self.assertEqual(self.pasada()["publicacion"]["resultado"], "adoptado")
        self.assertEqual((len(self.hojas()), self.drive.creados), (1, 1))

    def test_un_trabajo_ambiguo_no_se_toca(self):
        self.fila("T1", "m1")
        job = trabajos_work.crear_trabajo("T1", "m1")["job_id"]
        self.drive.sembrar_carpeta("PENDIENTES")
        pendientes = self.drive.id_de("PENDIENTES")[0]
        self.drive.sembrar_carpeta("CERRADOS")
        self.drive.sembrar(job, "a", pendientes)
        self.drive.sembrar(job, "b", pendientes)
        self.assertEqual(self.pasada()["publicacion"]["resultado"], "ambiguo")
        self.assertEqual(self.pasada()["publicacion"]["resultado"], "ambiguo")
        self.assertEqual(len(self.hojas()), 2)

    def test_sqlite_manda_si_la_hoja_desaparece_de_drive_se_vuelve_a_publicar(self):
        self.fila("T1", "m1")
        job = self.pasada()["publicacion"]["job_id"]
        self.drive.archivos.clear()
        self.assertEqual(self.pasada()["publicacion"]["resultado"], "creado")
        self.assertEqual((self.estado(job), len(self.hojas())), ("publicado", 1))


class ArchivadoTests(Base):
    def conciliar(self):
        self.fila("T1", "m1")
        job = self.pasada()["publicacion"]["job_id"]
        self.importar("T1", "m1")
        self.assertEqual(self.estado(job), "conciliado")
        return job

    def test_un_trabajo_conciliado_se_mueve_a_cerrados_una_sola_vez(self):
        job = self.conciliar()
        hoja, = self.hojas()
        cerrados = self.drive.id_de("CERRADOS")
        informe = self.pasada()
        self.assertEqual(informe["archivados"], [{"job_id": job, "resultado": "archivado"}])
        self.assertEqual(self.padres(hoja), cerrados)
        for _ in range(2):
            self.assertEqual(self.pasada()["archivados"], [{"job_id": job, "resultado": "ya_archivado"}])
        self.assertEqual(len(self.drive.movimientos), 1)
        self.assertEqual(len(self.eventos("trabajo_archivado", job)), 1)
        self.assertEqual(trabajos_work.obtener(job)["folder_id"], cerrados[0])
        self.assertEqual(self.estado(job), "conciliado")
        self.assertEqual(self.pasada()["publicacion"]["resultado"], "ya_conciliado")
        self.assertEqual((len(self.hojas()), self.drive.creados), (1, 1))

    def test_una_hoja_sin_marca_o_en_la_papelera_no_se_mueve(self):
        job = self.conciliar()
        hoja, = self.hojas()
        pendientes = self.drive.id_de("PENDIENTES")
        self.drive.archivos[hoja["id"]]["props"] = {}
        self.assertEqual(self.pasada()["archivados"][0]["resultado"], "hoja_sin_marca_o_en_papelera")
        self.drive.archivos[hoja["id"]]["props"] = {dominio.CLAVE_DE_MARCA: job}
        self.drive.archivos[hoja["id"]]["trashed"] = True
        self.assertEqual(self.pasada()["archivados"][0]["resultado"], "hoja_sin_marca_o_en_papelera")
        self.assertEqual((self.padres(hoja), self.drive.movimientos, self.eventos("trabajo_archivado")),
                         (pendientes, [], []))

    def test_una_hoja_ausente_se_informa_sin_romper_la_pasada(self):
        self.conciliar()
        self.drive.archivos.clear()
        self.assertEqual(self.pasada()["archivados"][0]["resultado"], "hoja_ausente")

    def test_un_fallo_al_mover_se_informa_y_se_reintenta(self):
        job = self.conciliar()

        def falla(*args, **kwargs):
            raise ErrorHttp(500)
        with patch.object(DriveConCarpetas, "update", falla):
            self.assertEqual(self.pasada()["archivados"], [{"job_id": job, "resultado": "error", "error": "ErrorHttp"}])
        self.assertEqual(self.eventos("trabajo_archivado"), [])
        self.assertEqual(self.pasada()["archivados"][0]["resultado"], "archivado")

    def test_solo_se_archiva_lo_del_mensaje_del_alcance(self):
        self.conciliar()
        self.fila("T9", "m9")
        otro = trabajos_work.crear_trabajo("T9", "m9")["job_id"]
        self.pasada(Alcance("T9", "m9"))
        self.importar("T9", "m9")
        self.assertEqual(self.estado(otro), "conciliado")
        self.pasada(Alcance("T9", "m9"))
        viejo = [h for h in self.hojas() if self.padres(h) == self.drive.id_de("PENDIENTES")]
        self.assertEqual(len(viejo), 1)                       # la de T1/m1 sigue en PENDIENTES: fuera del alcance


class ConfiguracionTests(Base):
    def config(self, **cambios):
        config = copy.deepcopy(VALID)
        config.update(cambios)
        return config

    def test_por_defecto_el_despacho_automatico_esta_apagado(self):
        self.assertIsNone(despacho.alcance_automatico(self.config()))
        self.assertFalse(settings.validate_config(self.config()))

    def test_activarlo_exige_modo_work_y_los_tres_textos(self):
        completa = dict(work_dispatch_enabled=True, work_dispatch_folder_id="raiz", work_dispatch_thread_id="T1",
                        work_dispatch_message_id="m1", active_mode="chatgpt_work")
        self.assertEqual(despacho.alcance_automatico(self.config(**completa)), ("raiz", Alcance("T1", "m1")))
        for faltante in ("work_dispatch_folder_id", "work_dispatch_thread_id", "work_dispatch_message_id"):
            with self.subTest(faltante=faltante):
                self.assertTrue(any(faltante in p for p in settings.validate_config(self.config(**{**completa,
                                                                                                    faltante: " "}))))
        self.assertTrue(settings.validate_config(self.config(**{**completa, "active_mode": "local"})))
        self.assertTrue(settings.validate_config(self.config(work_dispatch_enabled="si")))

    def test_la_cli_automatica_apagada_no_toca_drive_ni_pide_confirmacion(self):
        salida = []
        with patch("anka.infra.google_services.services", side_effect=AssertionError("no debe tocar Google")):
            self.assertEqual(work_jobs.main(["despachar", "--automatico"], salida=salida.append), 0)
        self.assertEqual(json.loads(salida[0]), {"resultado": "desactivado"})

    def test_la_cli_exige_confirmar_drive_y_un_mensaje(self):
        with patch("anka.infra.google_services.services", side_effect=AssertionError("no debe tocar Google")):
            self.assertEqual(work_jobs.main(["despachar", "--carpeta-id", "raiz", "--thread-id", "T1",
                                             "--message-id", "m1"], salida=lambda _: None), 2)

    def test_la_cli_despacha_un_solo_mensaje_con_confirmacion(self):
        self.fila("T1", "m1")
        self.fila("T2", "m1")
        salida = []
        with patch("anka.infra.google_services.services", return_value=(None, self.drive)):
            codigo = work_jobs.main(["despachar", "--carpeta-id", CARPETA, "--thread-id", "T1", "--message-id", "m1",
                                     "--confirmar-drive"], salida=salida.append)
        self.assertEqual(codigo, 0)
        self.assertEqual(json.loads(salida[0])["publicacion"]["resultado"], "creado")
        self.assertEqual(self.trabajos(), 1)

    def test_la_cli_sin_mensaje_se_niega(self):
        with patch("anka.infra.google_services.services", return_value=(None, self.drive)):
            self.assertEqual(work_jobs.main(["despachar", "--carpeta-id", CARPETA, "--confirmar-drive"],
                                            salida=lambda _: None), 2)
        self.assertEqual(self.drive.carpetas, {})


class InstruccionesTests(unittest.TestCase):
    def test_el_documento_de_la_tarea_work_dice_lo_esencial(self):
        from pathlib import Path
        texto = (Path(__file__).resolve().parents[1] / "docs" / "TAREA_WORK_COLA.md").read_text(encoding="utf-8")
        for frase in ("PENDIENTES", "un solo trabajo", "classification", "[ANKA_DASHBOARD_JSON_V1]",
                      "No muevas", "ANKA 01"):
            self.assertIn(frase, texto)


if __name__ == "__main__":
    unittest.main()
