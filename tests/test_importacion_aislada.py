"""Importación aislada de un borrador de Work (piloto 03): un hilo, un mensaje, origen verificado y sin efectos laterales.

Cubre: T-47
"""

from tests import _isolate  # noqa: F401  (debe ir primero)

import contextlib
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from anka.cli import work_sync as sync
from anka.infra import instantaneas_de_manifiesto, settings
from anka.infra.store import lectura, transaccion
from anka.servicios import quotes, work_import
from tests.test_work_sync import b64, draft_message, payload

ID = "1a149ae2f36b4147"
OTRO = "18f0000000000002"
PROPIAS = {"ankacomercialia@gmail.com"}


class _Exec:
    def __init__(self, value):
        self.value = value

    def execute(self):
        if isinstance(self.value, Exception):
            raise self.value
        return self.value


class GmailAislado:
    """Gmail de pruebas que cuenta qué borradores y mensajes se descargaron."""

    def __init__(self, borradores, mensajes=None, hilos=None):
        self.borradores = borradores          # draft_id -> mensaje del borrador
        self.mensajes = mensajes if mensajes is not None else {ID: origen()}
        self.hilos = hilos or {}
        self.draft_gets: list[str] = []
        self.message_gets: list[str] = []
        self.thread_gets: list[str] = []
        self.falla_origen: Exception | None = None

    def users(self):
        return self

    def getProfile(self, **_):
        return _Exec({"emailAddress": "ankacomercialia@gmail.com"})

    def drafts(self):
        gmail = self

        class _Borradores:
            def list(self, **_):
                return _Exec({"drafts": [{"id": key, "message": {"id": m["id"], "threadId": m["threadId"]}}
                                         for key, m in gmail.borradores.items()]})

            def get(self, id, **_):
                gmail.draft_gets.append(id)
                return _Exec({"id": id, "message": gmail.borradores[id]})
        return _Borradores()

    def messages(self):
        gmail = self

        class _Mensajes:
            def get(self, id, **_):
                gmail.message_gets.append(id)
                if gmail.falla_origen:
                    return _Exec(gmail.falla_origen)
                return _Exec(gmail.mensajes[id])
        return _Mensajes()

    def threads(self):
        gmail = self

        class _Hilos:
            def get(self, id, **_):
                gmail.thread_gets.append(id)
                return _Exec({"id": id, "messages": gmail.hilos.get(id, [])})
        return _Hilos()


def origen(mid=ID, thread=ID, de="Ana <c@example.com>", etiquetas=("INBOX",), fecha="1700000000000"):
    return {"id": mid, "threadId": thread, "internalDate": fecha, "labelIds": list(etiquetas),
            "payload": {"mimeType": "text/plain", "headers": [{"name": "From", "value": de}]}}


def con_cuerpo(cuerpo, hilo=ID, gmail_id="g1"):
    """Un borrador de Gmail con ese texto exacto (para borradores sin bloque o con JSON roto)."""
    return {"id": gmail_id, "threadId": hilo, "internalDate": "1000",
            "payload": {"mimeType": "text/plain", "body": {"data": b64(cuerpo)}}}


def borrador(hilo=ID, mensaje=ID, gmail_id="g1", **extra):
    return draft_message(payload(thread=hilo, message=mensaje, **extra), thread=hilo, message_id=gmail_id)


class Base(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.datos = Path(self.temp.name)
        for parche in (patch.object(settings, "DATA_DIR", self.datos),
                       patch.object(work_import, "programar_actualizacion_de_hoja", MagicMock()),
                       patch.object(sync, "sync_thread_activity", MagicMock())):
            parche.start()
            self.addCleanup(parche.stop)
        self.hoja = work_import.programar_actualizacion_de_hoja
        self.actividad = sync.sync_thread_activity
        self.instantaneas = self.datos / "manifiestos"

    def importar(self, gmail, hilo=ID, mensaje=ID):
        return sync.importar_aislado(gmail, lambda raw: work_import.dashboard_upsert(raw, actualizar_hoja=False),
                                     self.instantaneas, hilo, mensaje, PROPIAS)

    def casos(self):
        return sorted(row["thread_id"] for row in quotes.all_quotes())

    def analisis(self):
        return quotes.get_quote(ID)["analysis"]


class AislamientoTests(Base):
    def test_solo_se_descarga_el_borrador_del_hilo_elegido(self):
        gmail = GmailAislado({
            "dA": borrador(hilo="A", mensaje="mA", gmail_id="gA"),
            "dB": borrador(hilo="B", mensaje="mB", gmail_id="gB"),
            "dT": borrador(gmail_id="gT"),
        })
        resultado = self.importar(gmail)
        self.assertEqual(resultado["resultado"], "importado")
        self.assertEqual(gmail.draft_gets, ["dT"])                    # los demás hilos ni se leen
        self.assertEqual(gmail.message_gets, [ID])
        self.assertEqual(self.casos(), [ID])

    def test_un_borrador_roto_de_otro_hilo_no_crea_caso_minimo_ni_rechazo(self):
        cuerpo = "\n".join(["[REVISAR ANTES DE ENVIAR - BORRAR ESTA SECCION]", sync.START, "{no es json", sync.END])
        roto = con_cuerpo(cuerpo, hilo="B", gmail_id="gB")
        gmail = GmailAislado({"dB": roto, "dT": borrador(gmail_id="gT")})
        self.importar(gmail)
        self.assertEqual(gmail.draft_gets, ["dT"])
        self.assertEqual(self.casos(), [ID])
        with lectura() as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM work_rejections").fetchone()[0], 0)

    def test_un_borrador_del_hilo_con_otro_message_id_se_ignora_sin_verificar_su_origen(self):
        gmail = GmailAislado({"dX": borrador(mensaje=OTRO, gmail_id="gX")})
        resultado = self.importar(gmail)
        self.assertEqual(resultado["resultado"], "sin_borrador")
        self.assertEqual(resultado["ignorados"], [{"borrador": "dX", "causa": "mensaje_ajeno"}])
        self.assertEqual(gmail.message_gets, [])                      # ni siquiera se consulta Gmail por el otro mensaje
        self.assertEqual(self.casos(), [])

    def test_un_borrador_con_el_hilo_equivocado_dentro_del_json_se_rechaza(self):
        # el borrador está en el hilo elegido pero su JSON declara otro hilo
        malo = draft_message(payload(thread=OTRO, message=ID), thread=ID, message_id="gm")
        gmail = GmailAislado({"dM": malo})
        resultado = self.importar(gmail)
        self.assertNotEqual(resultado["resultado"], "importado")
        self.assertEqual(self.casos(), [])


class OrigenTests(Base):
    def test_el_origen_se_verifica_aunque_no_haya_version_ni_preflight(self):
        datos = payload(thread=ID, message=ID)
        self.assertNotIn("skill_version", datos)
        self.assertNotIn("DRIVE_PREFLIGHT", datos)
        gmail = GmailAislado({"dT": draft_message(datos, thread=ID)})
        self.assertEqual(self.importar(gmail)["resultado"], "importado")
        self.assertEqual(gmail.message_gets, [ID])                    # el modo general no lo habría consultado
        self.assertNotIn("skill_version", self.analisis())            # no se fingió una versión

    def test_el_modo_general_sigue_sin_verificar_un_borrador_sin_version(self):
        gmail = GmailAislado({})
        datos = payload(thread=ID, message=ID)
        self.assertIs(sync._verify_origin(gmail, datos), datos)
        self.assertEqual(gmail.message_gets, [])

    def test_un_remitente_distinto_rechaza(self):
        gmail = GmailAislado({"dT": borrador()}, {ID: origen(de="Otra <otra@example.com>")})
        resultado = self.importar(gmail)
        self.assertEqual(resultado["resultado"], "rechazado")
        self.assertEqual(resultado["rechazados"][0]["causa"], "origen")
        self.assertEqual(self.casos(), [])

    def test_un_mensaje_origen_que_es_un_borrador_rechaza(self):
        gmail = GmailAislado({"dT": borrador()}, {ID: origen(etiquetas=("DRAFT",))})
        self.assertEqual(self.importar(gmail)["resultado"], "rechazado")
        self.assertEqual(self.casos(), [])

    def test_un_origen_en_otro_hilo_rechaza(self):
        gmail = GmailAislado({"dT": borrador()}, {ID: origen(thread=OTRO)})
        self.assertEqual(self.importar(gmail)["resultado"], "rechazado")
        self.assertEqual(self.casos(), [])

    def test_si_gmail_no_responde_no_se_importa(self):
        gmail = GmailAislado({"dT": borrador()})
        gmail.falla_origen = TimeoutError("sin red")
        resultado = self.importar(gmail)
        self.assertEqual(resultado["resultado"], "error_de_origen")
        self.assertEqual(resultado["error"], "TimeoutError")
        self.assertEqual(self.casos(), [])


class AmbiguedadTests(Base):
    def test_dos_borradores_validos_para_el_mismo_mensaje_no_importan_ninguno(self):
        nuevo = borrador(gmail_id="g2", project_name="Segunda versión")
        nuevo["internalDate"] = "2000"
        gmail = GmailAislado({"d1": borrador(gmail_id="g1"), "d2": nuevo})
        resultado = self.importar(gmail)
        self.assertEqual(resultado["resultado"], "ambiguo")
        self.assertEqual(sorted(resultado["borradores"]), ["d1", "d2"])
        self.assertEqual(self.casos(), [])

    def test_un_auxiliar_sin_bloque_no_vuelve_ambiguo_el_hilo(self):
        auxiliar = con_cuerpo("Hola, solo un texto", gmail_id="gaux")
        gmail = GmailAislado({"d1": borrador(), "daux": auxiliar})
        resultado = self.importar(gmail)
        self.assertEqual(resultado["resultado"], "importado")
        self.assertEqual(resultado["ignorados"], [{"borrador": "daux", "causa": "sin_bloque"}])


class EfectosLateralesTests(Base):
    def test_no_toca_estado_latido_hoja_actividad_rechazos_ni_recepcion(self):
        with transaccion() as db:
            # la tabla la crea la migración de recepción (U-81); aquí basta con que exista para comprobar que no se toca
            db.execute("""CREATE TABLE IF NOT EXISTS recepcion_mensajes (account TEXT NOT NULL, thread_id TEXT NOT NULL,
                message_id TEXT NOT NULL, state TEXT NOT NULL, disposition TEXT NOT NULL DEFAULT '',
                detected_at TEXT NOT NULL, updated_at TEXT NOT NULL, PRIMARY KEY(account, thread_id, message_id))""")
            db.execute("""INSERT INTO recepcion_mensajes(account,thread_id,message_id,state,disposition,detected_at,updated_at)
                VALUES('ankacomercialia@gmail.com',?,?,'pendiente_de_procesar','pendiente_de_interpretacion_work','t','t')""",
                       (ID, ID))
            for hilo, mensaje in ((ID, OTRO), ("B", "mB")):          # otro mensaje del mismo hilo y uno de otro hilo
                db.execute("""INSERT INTO recepcion_mensajes(account,thread_id,message_id,state,disposition,detected_at,
                    updated_at) VALUES('ankacomercialia@gmail.com',?,?,'pendiente_de_procesar',
                    'pendiente_de_interpretacion_work','t','t')""", (hilo, mensaje))
        antes = {fila[2]: fila for fila in self.recepcion()}
        gmail = GmailAislado({"dT": borrador(), "dOtro": borrador(hilo="B", mensaje="mB", gmail_id="gB")})
        self.assertEqual(self.importar(gmail)["resultado"], "importado")
        despues = {fila[2]: fila for fila in self.recepcion()}
        self.assertEqual(despues[OTRO], antes[OTRO])                  # T-48: solo se concilia el mensaje elegido
        self.assertEqual(despues["mB"], antes["mB"])
        self.assertEqual(despues[ID][3:5], ("procesado", "interpretado_por_work"))
        self.hoja.assert_not_called()
        self.actividad.assert_not_called()
        for nombre in ("work_dashboard_sync_state.json", "work_sync_heartbeat.json"):
            self.assertFalse((self.datos / nombre).exists(), nombre)
        with lectura() as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM work_rejections").fetchone()[0], 0)
        self.assertEqual(self.casos(), [ID])

    def recepcion(self):
        with lectura() as db:
            return [tuple(r) for r in db.execute("SELECT * FROM recepcion_mensajes ORDER BY thread_id,message_id")]

    def test_importar_dos_veces_no_duplica_la_auditoria(self):
        gmail = GmailAislado({"dT": borrador()})
        self.importar(gmail)
        self.importar(gmail)
        with lectura() as db:
            hechos = db.execute("SELECT COUNT(*) FROM case_events WHERE thread_id=? AND kind='analysis'",
                                (ID,)).fetchone()[0]
        self.assertEqual(hechos, 1)

    def test_deja_el_analisis_y_su_auditoria_del_caso_elegido(self):
        gmail = GmailAislado({"dT": borrador()})
        self.importar(gmail)
        with lectura() as db:
            tipos = [r[0] for r in db.execute("SELECT kind FROM case_events WHERE thread_id=?", (ID,))]
        self.assertIn("analysis", tipos)
        self.assertEqual(quotes.get_quote(ID)["thread_id"], ID)


class ManifiestoDeDriveTests(Base):
    """Un mensaje sin adjuntos y sin versión V6 sigue el camino del manifiesto (T-37), que no se omite."""

    def test_sin_instantanea_no_hay_nada_que_reconciliar_ni_se_inventa_un_aviso(self):
        gmail = GmailAislado({"dT": borrador(analysis_complete=True)})
        self.assertEqual(self.importar(gmail)["resultado"], "importado")
        analisis = self.analisis()
        self.assertTrue(analisis.get("analysis_complete"))
        self.assertNotIn("manifiesto", json.dumps(analisis, ensure_ascii=False).lower())

    def test_con_instantanea_que_no_incluye_el_mensaje_queda_incompleto_con_aviso(self):
        instantaneas_de_manifiesto.guardar(self.instantaneas, {"thread_id": ID, "revision": "r1",
                                                               "message_ids": [OTRO], "attachments": []})
        gmail = GmailAislado({"dT": borrador(analysis_complete=True)})
        self.assertEqual(self.importar(gmail)["resultado"], "importado")
        analisis = self.analisis()
        self.assertFalse(analisis.get("analysis_complete"))
        self.assertIn("no incluye el mensaje analizado", json.dumps(analisis, ensure_ascii=False))

    def test_con_instantanea_que_si_lo_incluye_no_hay_aviso(self):
        instantaneas_de_manifiesto.guardar(self.instantaneas, {"thread_id": ID, "revision": "r1",
                                                               "message_ids": [ID], "attachments": []})
        gmail = GmailAislado({"dT": borrador(analysis_complete=True)})
        self.importar(gmail)
        self.assertNotIn("no incluye el mensaje analizado", json.dumps(self.analisis(), ensure_ascii=False))

    def test_con_version_v6_la_lectura_se_verifica_contra_gmail_sin_guardar_texto_en_el_puente(self):
        hilo = [{"id": ID, "threadId": ID, "internalDate": "1700000000000", "labelIds": ["INBOX"],
                 "payload": {"mimeType": "text/plain", "headers": [{"name": "From", "value": "Ana <c@example.com>"}],
                             "body": {"data": b64("Hola")}}}]
        gmail = GmailAislado({"dT": borrador(skill_version="PRECISO_V6.1", analysis_complete=True, attachments=[])},
                             hilos={ID: hilo})
        resultado = self.importar(gmail)
        self.assertEqual(resultado["resultado"], "importado")
        self.assertEqual(gmail.thread_gets, [ID])                     # T-41: el hilo se consulta en Gmail
        self.assertFalse(self.instantaneas.exists() and any(self.instantaneas.iterdir()))


class LineaDeComandosTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.datos = Path(self.temp.name)
        parche = patch.object(settings, "DATA_DIR", self.datos)
        parche.start()
        self.addCleanup(parche.stop)

    def ejecutar(self, *argumentos, gmail=None):
        servicios = MagicMock(return_value=(gmail, None))
        salida = io.StringIO()
        with patch.object(sys, "argv", ["work_dashboard_sync.py", *argumentos]), \
                patch.object(sync, "services", servicios), \
                patch.object(sync, "load_config", return_value={"active_mode": "chatgpt_work"}), \
                patch.object(sync, "configure_logging"), \
                patch.object(work_import, "programar_actualizacion_de_hoja", MagicMock()), \
                contextlib.redirect_stdout(salida), contextlib.redirect_stderr(io.StringIO()):
            with self.assertRaises(SystemExit) as salio:
                sync.main()
        return salio.exception.code, salida.getvalue(), servicios

    def test_los_argumentos_incompletos_o_sin_once_se_niegan_antes_de_llamar_a_gmail(self):
        for argumentos in (("--once", "--only-thread-id", ID),
                           ("--once", "--only-message-id", ID),
                           ("--only-thread-id", ID, "--only-message-id", ID),
                           ("--once", "--only-thread-id", " ", "--only-message-id", ID),
                           ("--once", "--only-thread-id", ID, "--only-message-id", "")):
            codigo, _, servicios = self.ejecutar(*argumentos)
            self.assertEqual(codigo, 2, argumentos)
            servicios.assert_not_called()

    def test_el_comando_exacto_del_piloto_importa_un_borrador_y_termina(self):
        gmail = GmailAislado({"dT": borrador(), "dOtro": borrador(hilo="B", mensaje="mB", gmail_id="gB")})
        codigo, salida, _ = self.ejecutar("--once", "--only-thread-id", ID, "--only-message-id", ID, gmail=gmail)
        self.assertEqual(codigo, 0)
        resultado = json.loads(salida.strip().splitlines()[-1])
        self.assertEqual(resultado["resultado"], "importado")
        self.assertEqual(gmail.draft_gets, ["dT"])
        self.assertFalse((self.datos / "work_sync_heartbeat.json").exists())
        self.assertFalse((self.datos / "work_dashboard_sync_state.json").exists())

    def test_sin_borrador_termina_con_error(self):
        gmail = GmailAislado({})
        codigo, salida, _ = self.ejecutar("--once", "--only-thread-id", ID, "--only-message-id", ID, gmail=gmail)
        self.assertEqual(codigo, 1)
        self.assertEqual(json.loads(salida.strip().splitlines()[-1])["resultado"], "sin_borrador")


if __name__ == "__main__":
    unittest.main()
