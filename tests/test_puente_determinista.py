"""Cubre: T-36, T-37. El puente no deja casos fuera y verifica la lectura de Work contra el manifiesto del preparador."""

from tests import _isolate  # noqa: F401  (debe ir primero)

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from anka.cli import work_sync as sync
from anka.dominio.caso_minimo import caso_minimo
from anka.dominio.work_contract import reconciliar_lectura
from anka.infra import instantaneas_de_manifiesto, settings
from anka.servicios import work_drive, work_import
from anka.servicios.quotes import connect
from anka.dominio.status import QuoteStatus
from tests.fake_work_drive import MemoryDrive
from tests.test_work_sync import FakeGmail, _Exec, draft_message, payload
from tests.test_work_drive import message as drive_message
from anka.infra.drive_exchange import DriveExchange


def recibido(mid="in1", sender="Ana Pérez <ana@cliente.com>", subject="Re: Cotización LiDAR", stamp="1760000000000"):
    return {"id": mid, "threadId": "T1", "internalDate": stamp, "labelIds": ["INBOX"],
            "payload": {"headers": [{"name": "From", "value": sender}, {"name": "Subject", "value": subject}]}}


def borrador_sin_json(thread="T1", message_id="gm-sin-json"):
    cuerpo = "[REVISAR ANTES DE ENVIAR - BORRAR ESTA SECCION]\nResumen en prosa, sin el bloque."
    import base64
    codificado = base64.urlsafe_b64encode(cuerpo.encode()).decode()
    return {"id": message_id, "threadId": thread, "internalDate": "2000",
            "payload": {"mimeType": "text/plain", "body": {"data": codificado}}}


class GmailConHilos(FakeGmail):
    """El mismo Gmail de las pruebas del puente, con hilos consultables y un contador de consultas."""

    def __init__(self, drafts, threads):
        super().__init__(drafts)
        self.hilos = threads
        self.consultas_de_hilo = 0

    def messages(self):
        class _Messages:
            def get(self, id, **_):
                return _Exec({"id": id, "threadId": "T1", "internalDate": "1760000000000", "labelIds": ["INBOX"],
                              "payload": {"headers": [{"name": "From", "value": "c@example.com"}]}})
        return _Messages()

    def threads(self):
        gmail = self

        class _Threads:
            def get(self, id, **_):
                gmail.consultas_de_hilo += 1
                if id not in gmail.hilos:
                    raise ConnectionError("hilo no disponible")
                return _Exec({"id": id, "messages": gmail.hilos[id]})
        return _Threads()


class CasoMinimoTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        patcher = patch.object(settings, "DATA_DIR", Path(self.temp.name))
        patcher.start()
        self.addCleanup(patcher.stop)
        self.checkpoint = Path(self.temp.name) / "state.json"

    def sincronizar(self, gmail):
        return sync.sync_once(gmail, work_import.dashboard_upsert, self.checkpoint, {}, {"ankacomercialia@gmail.com"})

    def caso(self, thread="T1"):
        db = connect()
        try:
            return db.execute("SELECT status, analysis_json FROM quotes WHERE thread_id=?", (thread,)).fetchone()
        finally:
            db.close()

    def test_un_borrador_sin_json_crea_el_caso_con_lo_minimo_en_revision_tecnica(self):
        # Cubre: T-36
        gmail = GmailConHilos({"d1": borrador_sin_json()}, {"T1": [recibido()]})
        resultado = self.sincronizar(gmail)
        self.assertEqual((resultado["updated"], resultado["failures"], len(resultado["rejected"])), (1, 0, 1))
        fila = self.caso()
        self.assertEqual(fila["status"], QuoteStatus.REVIEW)
        analisis = json.loads(fila["analysis_json"])
        self.assertEqual(analisis["activities"], [])
        self.assertEqual(analisis["project_name"], "Cotización LiDAR")           # sin el «Re:»
        self.assertEqual(analisis["routing"]["original_sender_email"], "ana@cliente.com")
        self.assertEqual(analisis["work_reply"], {})                              # nada para el cliente
        self.assertTrue(any("no pudo leer" in aviso for aviso in analisis["question_quality_warnings"]))

    def test_no_se_repite_mientras_el_borrador_no_cambie(self):
        # Cubre: T-36
        gmail = GmailConHilos({"d1": borrador_sin_json()}, {"T1": [recibido()]})
        self.sincronizar(gmail)
        segundo = self.sincronizar(gmail)
        self.assertEqual(segundo["updated"], 0)
        self.assertEqual(gmail.consultas_de_hilo, 1)

    def test_un_analisis_ya_importado_no_se_reemplaza_por_un_borrador_invalido(self):
        # Cubre: T-36
        gmail = GmailConHilos({"d1": draft_message(payload(thread="T1"))}, {"T1": [recibido()]})
        self.assertEqual(self.sincronizar(gmail)["updated"], 1)
        antes = self.caso()["analysis_json"]
        gmail.items["d2"] = borrador_sin_json(message_id="gm-sin-json-2")
        resultado = self.sincronizar(gmail)
        self.assertEqual(resultado["updated"], 0)
        self.assertEqual(self.caso()["analysis_json"], antes)
        self.assertEqual(gmail.consultas_de_hilo, 0)

    def test_un_borrador_valido_posterior_reemplaza_al_caso_minimo(self):
        # Cubre: T-36
        gmail = GmailConHilos({"d1": borrador_sin_json()}, {"T1": [recibido()]})
        self.sincronizar(gmail)
        gmail.items["d1"] = draft_message(payload(thread="T1", message="in1"), message_id="gm-valido")
        gmail.items["d1"]["internalDate"] = "3000"
        resultado = self.sincronizar(gmail)
        self.assertEqual(resultado["updated"], 1)
        self.assertEqual(json.loads(self.caso()["analysis_json"])["activities"][0]["activity"], "a")
        self.assertEqual(json.loads(self.checkpoint.read_text(encoding="utf-8"))["_minimos"], {})

    def test_si_gmail_no_da_el_hilo_el_borrador_sigue_rechazado_sin_romper_el_ciclo(self):
        # Cubre: T-36
        gmail = GmailConHilos({"d1": borrador_sin_json()}, {})
        resultado = self.sincronizar(gmail)
        self.assertEqual((resultado["updated"], resultado["failures"], len(resultado["rejected"])), (0, 0, 1))
        self.assertIsNone(self.caso())

    def test_el_caso_minimo_solo_usa_datos_del_correo(self):
        # Cubre: T-36
        minimo = caso_minimo("T1", recibido(subject="  Re:  RV: Solicitud   de oferta "), "falta el bloque")
        self.assertEqual(minimo["project_name"], "Solicitud de oferta")
        self.assertEqual(minimo["client_email"], "ana@cliente.com")
        self.assertFalse(minimo["analysis_complete"])
        self.assertEqual((minimo["activities"], minimo["question_audit"]), ([], []))
        self.assertIsNone(caso_minimo("T1", recibido(sender="sin direccion"), "x"))
        self.assertIsNone(caso_minimo("T1", recibido(stamp="0"), "x"))
        self.assertEqual(caso_minimo("T1", recibido(subject=""), "x")["project_name"], "no indicado")


class BorradorSinBloqueTests(unittest.TestCase):
    """Un borrador sin JSON no es un fallo si el hilo ya tiene análisis o si es reciente (T-36, D-69)."""

    AHORA = 1_760_000_000_000   # ms; el borrador se crea en este instante

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        patcher = patch.object(settings, "DATA_DIR", Path(self.temp.name))
        patcher.start()
        self.addCleanup(patcher.stop)
        self.checkpoint = Path(self.temp.name) / "state.json"
        self.cache: dict = {}

    def pasada(self, gmail, segundos_despues=0):
        with patch.object(sync, "_ahora_ms", return_value=self.AHORA + segundos_despues * 1000):
            return sync.sync_once(gmail, work_import.dashboard_upsert, self.checkpoint, self.cache,
                                  {"ankacomercialia@gmail.com"})

    def sin_json(self, message_id="aux"):
        borrador = borrador_sin_json(message_id=message_id)
        borrador["internalDate"] = str(self.AHORA)
        return borrador

    def caso(self, thread="T1"):
        db = connect()
        try:
            return db.execute("SELECT status, analysis_json FROM quotes WHERE thread_id=?", (thread,)).fetchone()
        finally:
            db.close()

    def test_el_borrador_auxiliar_junto_al_completo_se_ignora_y_no_deja_aviso(self):
        # Cubre: T-36
        gmail = GmailConHilos({"d1": draft_message(payload()), "d2": self.sin_json()}, {"T1": [recibido()]})
        resultado = self.pasada(gmail, segundos_despues=3)
        self.assertEqual(resultado["rejected"], [])                                  # el encabezado no dice «por revisar»
        analisis = json.loads(self.caso()["analysis_json"])
        self.assertEqual([a["activity"] for a in analisis["activities"]], ["a"])      # el análisis completo se conserva
        self.assertFalse(any("no pudo leer" in a for a in analisis["question_quality_warnings"]))

    def test_un_borrador_auxiliar_posterior_a_un_analisis_ya_importado_se_ignora(self):
        # Cubre: T-36
        self.pasada(GmailConHilos({"d1": draft_message(payload())}, {"T1": [recibido()]}))
        resultado = self.pasada(GmailConHilos({"d2": self.sin_json()}, {"T1": [recibido()]}), segundos_despues=3600)
        self.assertEqual(resultado["rejected"], [])
        self.assertEqual(len(json.loads(self.caso()["analysis_json"])["activities"]), 1)

    def test_un_borrador_reciente_sin_json_espera_y_pasado_el_plazo_se_trata_como_siempre(self):
        # Cubre: T-36
        gmail = GmailConHilos({"d1": self.sin_json()}, {"T1": [recibido()]})
        resultado = self.pasada(gmail, segundos_despues=60)               # Work puede estar terminando
        self.assertEqual((resultado["rejected"], resultado["updated"]), ([], 0))
        self.assertIsNone(self.caso())
        resultado = self.pasada(gmail, segundos_despues=sync.GRACIA_SIN_BLOQUE_S + 5)   # nunca llegó el completo
        self.assertEqual(len(resultado["rejected"]), 1)
        self.assertEqual(self.caso()["status"], QuoteStatus.REVIEW)

    def test_el_borrador_completo_que_llega_durante_la_espera_evita_el_caso_minimo(self):
        # Cubre: T-36
        gmail = GmailConHilos({"d1": self.sin_json()}, {"T1": [recibido()]})
        self.pasada(gmail, segundos_despues=60)
        gmail.items["d2"] = draft_message(payload(), message_id="gm2")
        resultado = self.pasada(gmail, segundos_despues=90)
        self.assertEqual(resultado["rejected"], [])
        self.assertEqual(len(json.loads(self.caso()["analysis_json"])["activities"]), 1)

    def test_un_json_roto_no_espera_el_plazo(self):
        # Cubre: T-36
        roto = draft_message({"thread_id": "T1"}, message_id="roto")      # JSON presente pero incumple el contrato
        roto["internalDate"] = str(self.AHORA)
        resultado = self.pasada(GmailConHilos({"d1": roto}, {"T1": [recibido()]}), segundos_despues=5)
        self.assertEqual(len(resultado["rejected"]), 1)
        self.assertIn("Contrato de Work no cumplido", resultado["rejected"][0]["reason"])


def instantanea(revision="rev-2", mensajes=("in1",)):
    return {"thread_id": "T1", "revision": revision, "message_ids": list(mensajes),
            "email_segments": [{"id": "e1", "name": "aaaa-0001.txt"}],
            "attachment_segments": [{"id": "a1", "name": "bbbb-0001.txt"}, {"id": "a2", "name": "bbbb-0002.txt"}]}


def con_lectura(**cambios):
    base = payload(thread="T1", message="in1", analysis_complete=True,
                   DRIVE_PREFLIGHT={"thread_id": "T1", "manifest_revision": "rev-2", "message_ids": ["in1"],
                                    "email_segments_read": 1, "email_segments_available": 1,
                                    "attachment_segments_read": 2, "attachment_segments_available": 2},
                   reading_audit={"email_segment_ids_read": ["e1"], "attachment_segment_ids_read": ["a1", "a2"]})
    base.update(cambios)
    return base


class LecturaVerificadaTests(unittest.TestCase):
    def test_lo_que_coincide_con_el_manifiesto_no_cambia_el_resultado(self):
        # Cubre: T-37
        datos = con_lectura()
        self.assertEqual(reconciliar_lectura(datos, instantanea()), [])
        self.assertIs(datos["analysis_complete"], True)
        self.assertEqual(datos["reading_audit"]["attachment_segment_ids_pending"], [])
        self.assertEqual(datos["reading_audit"]["email_segment_ids_expected"], ["e1"])

    def test_los_nombres_de_segmento_se_normalizan_al_identificador(self):
        # Cubre: T-37
        datos = con_lectura(reading_audit={"email_segment_ids_read": ["aaaa-0001.txt"],
                                           "attachment_segment_ids_read": ["bbbb-0002.txt", "a1", "a1"]})
        self.assertEqual(reconciliar_lectura(datos, instantanea()), [])
        self.assertEqual(datos["reading_audit"]["email_segment_ids_read"], ["e1"])
        self.assertEqual(datos["reading_audit"]["attachment_segment_ids_read"], ["a1", "a2"])

    def test_los_segmentos_sin_lectura_verificada_se_calculan_y_dejan_el_analisis_incompleto(self):
        # Cubre: T-37
        datos = con_lectura(reading_audit={"email_segment_ids_read": ["e1"], "attachment_segment_ids_read": ["a1"],
                                           "attachment_segment_ids_pending": []})   # Work dijo que no faltaba nada
        avisos = reconciliar_lectura(datos, instantanea())
        self.assertEqual(datos["reading_audit"]["attachment_segment_ids_pending"], ["a2"])
        self.assertEqual(datos["DRIVE_PREFLIGHT"]["attachment_segments_read"], 1)
        self.assertIs(datos["analysis_complete"], False)
        self.assertTrue(any("1 de 2 segmentos de adjuntos sin lectura verificada" in aviso for aviso in avisos))

    def test_sin_auditoria_de_lectura_todo_queda_pendiente(self):
        # Cubre: T-37
        datos = con_lectura()
        del datos["reading_audit"]
        reconciliar_lectura(datos, instantanea())
        self.assertEqual(datos["reading_audit"]["email_segment_ids_pending"], ["e1"])
        self.assertIs(datos["analysis_complete"], False)

    def test_una_revision_o_un_mensaje_que_no_son_los_vigentes_se_avisan(self):
        # Cubre: T-37
        datos = con_lectura()
        avisos = reconciliar_lectura(datos, instantanea(revision="rev-3"))
        self.assertTrue(any("no es la vigente" in aviso for aviso in avisos))
        self.assertEqual(datos["DRIVE_PREFLIGHT"]["manifest_revision"], "rev-3")
        self.assertIs(datos["analysis_complete"], False)
        datos = con_lectura()
        avisos = reconciliar_lectura(datos, instantanea(mensajes=("otro",)))
        self.assertTrue(any("no incluye el mensaje" in aviso for aviso in avisos))

    def test_los_segmentos_inventados_y_los_conteos_distintos_se_avisan(self):
        # Cubre: T-37
        datos = con_lectura(reading_audit={"email_segment_ids_read": ["e1", "inventado"],
                                           "attachment_segment_ids_read": ["a1", "a2"]})
        datos["DRIVE_PREFLIGHT"]["attachment_segments_available"] = 5
        avisos = reconciliar_lectura(datos, instantanea())
        self.assertTrue(any("no existen en el manifiesto" in aviso for aviso in avisos))
        self.assertTrue(any("declaró 5 segmentos de adjuntos" in aviso for aviso in avisos))

    def test_sin_instantanea_o_de_otro_hilo_no_se_toca_nada(self):
        # Cubre: T-37
        datos = con_lectura()
        copia = json.loads(json.dumps(datos))
        self.assertEqual(reconciliar_lectura(datos, None), [])
        self.assertEqual(reconciliar_lectura(datos, {**instantanea(), "thread_id": "otro"}), [])
        self.assertEqual(datos, copia)

    def test_el_puente_verifica_el_borrador_con_la_instantanea_guardada(self):
        # Cubre: T-37
        with tempfile.TemporaryDirectory() as carpeta:
            checkpoint = Path(carpeta) / "state.json"
            instantaneas_de_manifiesto.guardar(Path(carpeta) / "manifiestos", {
                "thread_id": "T1", "revision": "rev-2", "message_ids": ["in1"],
                "email_segments": [{"id": "e1", "name": "n"}],
                "attachments": [{"text_segments": [{"id": "a1", "name": "n1"}, {"id": "a2", "name": "n2"}]}]})
            gmail = GmailConHilos({"d1": draft_message(con_lectura(reading_audit={
                "email_segment_ids_read": ["e1"], "attachment_segment_ids_read": ["a1"]}))}, {})
            capturado = []
            sync.sync_once(gmail, lambda codificado: capturado.append(codificado), checkpoint, {})
            datos = json.loads(sync.base64.urlsafe_b64decode(capturado[0]))
            self.assertEqual(datos["reading_audit"]["attachment_segment_ids_pending"], ["a2"])
            self.assertIs(datos["analysis_complete"], False)
            self.assertTrue(any("sin lectura verificada" in aviso for aviso in datos["_avisos_del_puente"]))

    def test_los_segmentos_sin_lectura_nombran_el_archivo_que_falta(self):
        # Cubre: T-37
        instantanea_con_archivos = {
            **instantanea(), "attachment_segments": [
                {"id": "a1", "name": "bbbb-0001.txt", "archivo": "plano.kml"},
                {"id": "a2", "name": "bbbb-0002.txt", "archivo": "plano.kml"},
                {"id": "a3", "name": "cccc-0001.txt", "archivo": "tdr.pdf"}]}
        datos = con_lectura(reading_audit={"email_segment_ids_read": ["e1"],
                                           "attachment_segment_ids_read": ["a3"]})
        avisos = reconciliar_lectura(datos, instantanea_con_archivos)
        self.assertEqual(datos["reading_audit"]["attachment_files_pending"], ["plano.kml"])
        self.assertTrue(any("2 de 3 segmentos de adjuntos sin lectura verificada: plano.kml." in a for a in avisos))
        # una instantánea anterior (sin nombres) sigue funcionando, solo que sin ellos
        datos = con_lectura(reading_audit={"email_segment_ids_read": ["e1"], "attachment_segment_ids_read": ["a1"]})
        avisos = reconciliar_lectura(datos, instantanea())
        self.assertEqual(datos["reading_audit"]["attachment_files_pending"], [])
        self.assertTrue(any(a.endswith("sin lectura verificada.") for a in avisos))

    def test_la_instantanea_se_guarda_solo_con_ids_y_nombres_y_se_ignora_si_esta_dañada(self):
        # Cubre: T-37
        with tempfile.TemporaryDirectory() as carpeta:
            directorio = Path(carpeta)
            instantaneas_de_manifiesto.guardar(directorio, {
                "thread_id": "T1", "revision": "r", "message_ids": ["m"], "instructions": "texto largo",
                "email_segments": [{"id": "e1", "name": "n", "sha256": "x"}],
                "attachments": [{"filename": "secreto.pdf", "text_segments": [{"id": "a1", "name": "m"}]}]})
            guardada = instantaneas_de_manifiesto.cargar(directorio, "T1")
            self.assertEqual(guardada["attachment_segments"], [{"id": "a1", "name": "m", "archivo": "secreto.pdf"}])
            self.assertNotIn("texto largo", json.dumps(guardada))
            self.assertNotIn("sha256", json.dumps(guardada))
            (directorio / "T1.json").write_text("{no es json", encoding="utf-8")
            self.assertIsNone(instantaneas_de_manifiesto.cargar(directorio, "T1"))
            self.assertIsNone(instantaneas_de_manifiesto.cargar(directorio, "no-existe"))

    def test_el_preparador_guarda_la_instantanea_al_publicar_y_cuando_no_hay_cambios(self):
        # Cubre: T-37
        drive, gmail = MemoryDrive(), MagicMock()
        hilo = {"id": "t1", "messages": [drive_message()]}
        with tempfile.TemporaryDirectory() as carpeta:
            directorio = Path(carpeta)
            publicar = lambda: work_drive.prepare_thread(  # noqa: E731
                gmail, DriveExchange(drive), "reception", hilo, {"anka@example.com"}, directorio)
            self.assertEqual(publicar()["status"], "preparado")
            primera = instantaneas_de_manifiesto.cargar(directorio, "t1")
            self.assertEqual(primera["message_ids"], ["m1"])
            self.assertTrue(primera["email_segments"] and primera["attachment_segments"])
            (directorio / "t1.json").unlink()
            self.assertEqual(publicar()["status"], "sin_cambios")
            self.assertEqual(instantaneas_de_manifiesto.cargar(directorio, "t1"), primera | {
                "saved_at": instantaneas_de_manifiesto.cargar(directorio, "t1")["saved_at"]})


if __name__ == "__main__":
    unittest.main()
