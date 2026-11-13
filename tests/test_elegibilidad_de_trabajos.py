"""Elegibilidad de los trabajos Work: el ruido no se publica y las solicitudes y seguimientos sí (T-51, D-155).

Todo con SQLite temporal y Drive simulado; ningún dato comercial real.

Cubre: T-51
"""

from tests import _isolate  # noqa: F401  (debe ir primero)

import json
import tempfile
import threading
import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import patch

from anka.cli import work_jobs
from anka.dominio import trabajo_work as dominio
from anka.dominio.trabajo_work import ELEGIBLE, EXCLUIDO, REVISION, evaluar_elegibilidad
from anka.infra import settings, store
from anka.servicios import recepcion, trabajos_work
from anka.servicios.recepcion import CUENTA
from anka.servicios.trabajos_work import NoElegible, TrabajoRechazado
from tests.test_recepcion import Gmail, event, message
from tests.test_trabajos_work import CARPETA, DriveDeLaboratorio

TERMINOS = ["cotización", "cotizar", "levantamiento", "topográfico"]
PENDIENTE = ("pendiente_de_procesar", "pendiente_de_interpretacion_work")


def fila(sender="Cliente <cliente@example.com>", subject="Solicitud", adjuntos=(), evidencia=None):
    return {"sender": sender, "subject": subject, "evidence_json": json.dumps(evidencia or {}),
            "attachments_json": json.dumps([{"filename": nombre} for nombre in adjuntos])}


class DominioTests(unittest.TestCase):
    def test_una_persona_siempre_es_elegible_aunque_el_asunto_no_diga_nada(self):
        for remitente in ("Ana <ana@empresa.com>", "ana@empresa.com", "persona@openai.com", "Ana <ana@gmail.com>"):
            with self.subTest(remitente=remitente):
                self.assertEqual(evaluar_elegibilidad(fila(remitente), TERMINOS).estado, ELEGIBLE)

    def test_la_notificacion_de_chatgpt_se_excluye_y_no_se_rescata(self):
        for remitente in ("noreply@tm.openai.com", "ChatGPT <tareas@tm.openai.com>", "x@avisos.tm.openai.com"):
            with self.subTest(remitente=remitente):
                veredicto = evaluar_elegibilidad(fila(remitente, "Cotización de levantamiento topográfico"), TERMINOS)
                self.assertEqual((veredicto.estado, veredicto.motivo_id), (EXCLUIDO, "notificacion_de_chatgpt"))

    def test_cada_senal_de_r1_registrada_por_el_receptor_excluye(self):
        for senal in ("remitente_no_reply", "list_unsubscribe", "auto_submitted", "precedence_masivo", "spam_gmail"):
            with self.subTest(senal=senal):
                veredicto = evaluar_elegibilidad(fila("Ofertas <ventas@tienda.com>", "Ofertas de la semana",
                                                      evidencia={"noise_signal": senal}), TERMINOS)
                self.assertEqual((veredicto.estado, veredicto.motivo_id), (EXCLUIDO, senal))

    def test_el_remitente_no_reply_se_excluye_aunque_la_fila_sea_anterior_al_cambio(self):
        for remitente in ("no-reply@x.com", "Notificaciones <notifications@x.com>", "mailer-daemon@x.com",
                          "do_not_reply@x.com"):
            with self.subTest(remitente=remitente):
                self.assertEqual(evaluar_elegibilidad(fila(remitente), TERMINOS).motivo_id, "remitente_no_reply")

    def test_una_senal_desconocida_o_dañada_no_cuenta_como_ruido(self):
        self.assertEqual(evaluar_elegibilidad(fila(evidencia={"noise_signal": "inventada"}), TERMINOS).estado, ELEGIBLE)
        dañada = {"sender": "ana@x.com", "subject": "", "evidence_json": "no es json", "attachments_json": "tampoco"}
        self.assertEqual(evaluar_elegibilidad(dañada, TERMINOS).estado, ELEGIBLE)

    def test_un_aviso_automatico_que_habla_de_cotizaciones_queda_en_revision(self):
        veredicto = evaluar_elegibilidad(fila("no-reply@portal.com", "Su cotización fue recibida"), TERMINOS)
        self.assertEqual((veredicto.estado, veredicto.motivo_id), (REVISION, "aviso_automatico_con_terminos_comerciales"))
        con_adjunto = evaluar_elegibilidad(fila("no-reply@portal.com", "Aviso", adjuntos=["Levantamiento.pdf"]), TERMINOS)
        self.assertEqual(con_adjunto.estado, REVISION)
        sin_termino = evaluar_elegibilidad(fila("no-reply@portal.com", "Boletín de octubre"), TERMINOS)
        self.assertEqual(sin_termino.estado, EXCLUIDO)

    def test_sin_los_terminos_comerciales_un_aviso_automatico_no_se_descarta(self):
        veredicto = evaluar_elegibilidad(fila("no-reply@portal.com", "Boletín"), None)
        self.assertEqual((veredicto.estado, veredicto.motivo_id), (REVISION, "terminos_comerciales_no_disponibles"))
        self.assertEqual(evaluar_elegibilidad(fila("noreply@tm.openai.com"), None).estado, EXCLUIDO)
        self.assertEqual(evaluar_elegibilidad(fila("ana@x.com"), None).estado, ELEGIBLE)

    def test_una_fila_sin_remitente_legible_queda_en_revision(self):
        for remitente in ("", "   ", " , ", "sin arroba"):
            with self.subTest(remitente=remitente):
                veredicto = evaluar_elegibilidad(fila(remitente), TERMINOS)
                self.assertEqual((veredicto.estado, veredicto.motivo_id), (REVISION, "remitente_desconocido"))

    def test_con_varios_remitentes_solo_se_excluye_si_todos_son_automaticos(self):
        self.assertEqual(evaluar_elegibilidad(fila("ana@x.com, no-reply@y.com"), TERMINOS).estado, ELEGIBLE)
        self.assertEqual(evaluar_elegibilidad(fila("no-reply@a.com, noreply@b.com"), TERMINOS).estado, EXCLUIDO)

    def test_el_veredicto_dice_si_se_puede_crear(self):
        self.assertTrue(evaluar_elegibilidad(fila(), TERMINOS).elegible)
        self.assertFalse(evaluar_elegibilidad(fila("noreply@tm.openai.com"), TERMINOS).elegible)

    def test_los_motivos_de_exclusion_incluyen_los_de_r1_sin_copiarlos(self):
        from anka.dominio.reception import NOISE_SIGNALS
        self.assertTrue(set(NOISE_SIGNALS) <= set(dominio.MOTIVOS_DE_EXCLUSION))


class Base(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.datos = Path(self.temp.name)
        base = json.loads(Path("config.example.json").read_text(encoding="utf-8-sig"))
        (self.datos / "config.json").write_text(json.dumps({**base, "commercial_filter_terms": TERMINOS}),
                                                encoding="utf-8")
        parche = patch.object(settings, "DATA_DIR", self.datos)
        parche.start()
        self.addCleanup(parche.stop)
        self.drive = DriveDeLaboratorio()

    def fila(self, hilo, mensaje, remitente="Cliente <cliente@example.com>", asunto="Solicitud", evidencia=None,
             estado=PENDIENTE[0], disposicion=PENDIENTE[1]):
        with store.transaccion() as db:
            db.execute("""INSERT INTO recepcion_mensajes(account,thread_id,message_id,state,disposition,sender,subject,
                internal_date,attachments_json,evidence_json,detected_at,updated_at)
                VALUES(?,?,?,?,?,?,?,'2026-10-17T15:00:00+00:00','[]',?,'2026-10-17T10:00:00-05:00',
                '2026-10-17T10:00:00-05:00')""",
                       (CUENTA, hilo, mensaje, estado, disposicion, remitente, asunto, json.dumps(evidencia or {})))

    def eventos(self, tipo, mensaje=None):
        with store.lectura() as db:
            return [dict(r) for r in db.execute("SELECT * FROM recepcion_eventos WHERE event_kind=? ORDER BY id",
                                                (tipo,)) if mensaje in (None, r["message_id"])]

    def trabajos(self):
        with store.lectura() as db:
            return db.execute("SELECT COUNT(*) FROM trabajos_work").fetchone()[0]

    def estado_de_recepcion(self, hilo, mensaje):
        with store.lectura() as db:
            r = db.execute("SELECT state,disposition,updated_at FROM recepcion_mensajes WHERE thread_id=? "
                           "AND message_id=?", (hilo, mensaje)).fetchone()
        return tuple(r)


class CrearTrabajoTests(Base):
    def test_una_notificacion_de_chatgpt_no_crea_trabajo_y_deja_su_motivo_una_sola_vez(self):
        self.fila("N1", "n1", "ChatGPT <noreply@tm.openai.com>", "[Task Update] ANKA 01 | Recepción y clasificación")
        antes = self.estado_de_recepcion("N1", "n1")
        for _ in range(3):
            with self.assertRaises(NoElegible) as error:
                trabajos_work.crear_trabajo("N1", "n1")
        self.assertEqual(error.exception.veredicto.motivo_id, "notificacion_de_chatgpt")
        self.assertEqual(self.trabajos(), 0)
        evento, = self.eventos("trabajo_excluido")
        self.assertEqual(json.loads(evento["detail_json"]), {"etapa": "crear", "motivo": "notificacion_de_chatgpt"})
        self.assertEqual(self.estado_de_recepcion("N1", "n1"), antes)          # el estado de recepción no cambia
        self.assertNotIn("Task Update", evento["detail_json"])                  # sin asunto ni cuerpo en la auditoría

    def test_una_solicitud_y_un_seguimiento_si_crean_trabajo_con_su_identidad_de_siempre(self):
        self.fila("T1", "m1")
        self.fila("T1", "m2", "Cliente <cliente@example.com>", "Re: Solicitud")      # seguimiento en el mismo hilo
        self.fila("T1", "n1", "no-reply@portal.com", "Boletín")                       # ruido en el mismo hilo
        uno, dos = trabajos_work.crear_trabajo("T1", "m1"), trabajos_work.crear_trabajo("T1", "m2")
        self.assertEqual(uno["job_id"], dominio.id_de_trabajo(CUENTA, "T1", "m1", dominio.TIPO_ANALIZAR,
                                                              dominio.VERSION_DEL_CONTRATO))
        self.assertNotEqual(uno["job_id"], dos["job_id"])
        with self.assertRaises(NoElegible):
            trabajos_work.crear_trabajo("T1", "n1")
        self.assertEqual(self.trabajos(), 2)

    def test_crear_dos_veces_un_elegible_sigue_siendo_idempotente(self):
        self.fila("T1", "m1")
        primero = trabajos_work.crear_trabajo("T1", "m1")
        segundo = trabajos_work.crear_trabajo("T1", "m1")
        self.assertEqual((primero["creado"], segundo["creado"], primero["job_id"]), (True, False, segundo["job_id"]))
        self.assertEqual(self.trabajos(), 1)

    def test_un_aviso_automatico_con_terminos_comerciales_queda_con_la_clasificacion_pendiente(self):
        self.fila("T2", "a1", "no-reply@portal.com", "Su cotización de levantamiento")
        with self.assertRaises(NoElegible) as error:
            trabajos_work.crear_trabajo("T2", "a1")
            self.fail("no debía crearse")
        self.assertEqual(error.exception.veredicto.motivo_id, "aviso_automatico_con_terminos_comerciales")
        self.assertIn("forzar", str(error.exception))
        self.assertEqual(len(self.eventos("trabajo_clasificacion_pendiente")), 1)
        self.assertEqual(self.eventos("trabajo_excluido"), [])
        self.assertEqual(self.estado_de_recepcion("T2", "a1")[:2], PENDIENTE)

    def test_una_fila_sin_remitente_queda_pendiente_de_clasificar(self):
        self.fila("T3", "s1", "", "")
        with self.assertRaises(NoElegible):
            trabajos_work.crear_trabajo("T3", "s1")
        evento, = self.eventos("trabajo_clasificacion_pendiente")
        self.assertEqual(json.loads(evento["detail_json"])["motivo"], "remitente_desconocido")

    def test_sin_configuracion_un_aviso_automatico_no_se_descarta(self):
        (self.datos / "config.json").unlink()
        self.fila("T4", "b1", "no-reply@portal.com", "Boletín")
        with self.assertRaises(NoElegible) as error:
            trabajos_work.crear_trabajo("T4", "b1")
        self.assertEqual(error.exception.veredicto.motivo_id, "terminos_comerciales_no_disponibles")
        self.fila("T5", "c1", "noreply@tm.openai.com", "[Task Update]")
        with self.assertRaises(NoElegible) as error:
            trabajos_work.crear_trabajo("T5", "c1")
        self.assertEqual(error.exception.veredicto.estado, EXCLUIDO)

    def test_los_rechazos_previos_a_t51_siguen_igual(self):
        with self.assertRaises(TrabajoRechazado):
            trabajos_work.crear_trabajo("T9", "no-existe")
        self.fila("T6", "p1", estado="procesado", disposicion="interpretado_por_work")
        with self.assertRaises(TrabajoRechazado) as error:
            trabajos_work.crear_trabajo("T6", "p1")
        self.assertNotIsInstance(error.exception, NoElegible)
        self.assertEqual(self.eventos("trabajo_excluido"), [])


class PublicarTests(Base):
    def test_un_trabajo_que_ya_no_es_elegible_no_toca_drive(self):
        self.fila("T1", "m1")
        job = trabajos_work.crear_trabajo("T1", "m1")["job_id"]
        with store.transaccion() as db:            # el trabajo se creó antes de la regla y el remitente era automático
            db.execute("UPDATE recepcion_mensajes SET sender='ChatGPT <noreply@tm.openai.com>'")
        resultado = trabajos_work.publicar_trabajo(self.drive, job, CARPETA)
        self.assertEqual((resultado["resultado"], resultado["motivo"]), ("no_elegible", "notificacion_de_chatgpt"))
        self.assertEqual((self.drive.creados, self.drive.consultas, self.drive.archivos), (0, [], {}))
        self.assertEqual(trabajos_work.obtener(job)["state"], "pendiente_de_publicar")
        trabajos_work.publicar_trabajo(self.drive, job, CARPETA)
        self.assertEqual(len(self.eventos("trabajo_excluido")), 1)

    def test_un_trabajo_dudoso_queda_con_la_clasificacion_pendiente_y_no_se_publica(self):
        self.fila("T1", "m1")
        job = trabajos_work.crear_trabajo("T1", "m1")["job_id"]
        with store.transaccion() as db:
            db.execute("UPDATE recepcion_mensajes SET sender='no-reply@portal.com', subject='Su cotización'")
        resultado = trabajos_work.publicar_trabajo(self.drive, job, CARPETA)
        self.assertEqual(resultado["resultado"], "clasificacion_pendiente")
        self.assertEqual(self.drive.archivos, {})

    def test_un_elegible_se_publica_como_siempre(self):
        self.fila("T1", "m1")
        job = trabajos_work.crear_trabajo("T1", "m1")["job_id"]
        self.assertEqual(trabajos_work.publicar_trabajo(self.drive, job, CARPETA)["resultado"], "creado")
        self.assertEqual(trabajos_work.publicar_trabajo(self.drive, job, CARPETA)["resultado"], "actualizado")
        self.assertEqual((self.drive.creados, len(self.drive.vivos(job))), (1, 1))

    def test_publicar_sigue_rechazando_lo_inexistente_y_lo_cerrado(self):
        with self.assertRaises(TrabajoRechazado):
            trabajos_work.publicar_trabajo(self.drive, "ANKAJOB-0000000000000000", CARPETA)
        self.fila("T1", "m1")
        job = trabajos_work.crear_trabajo("T1", "m1")["job_id"]
        with store.transaccion() as db:
            db.execute("UPDATE trabajos_work SET state='conciliado'")
        with self.assertRaises(TrabajoRechazado):
            trabajos_work.publicar_trabajo(self.drive, job, CARPETA)


class ForzarTests(Base):
    def test_una_persona_puede_rescatar_un_mensaje_y_desde_entonces_se_crea_y_se_publica(self):
        self.fila("T1", "a1", "no-reply@portal.com", "Su cotización")
        with self.assertRaises(NoElegible):
            trabajos_work.crear_trabajo("T1", "a1")
        trabajos_work.forzar_elegibilidad("T1", "a1")
        trabajos_work.forzar_elegibilidad("T1", "a1")
        self.assertEqual(len(self.eventos("trabajo_elegibilidad_forzada")), 1)
        job = trabajos_work.crear_trabajo("T1", "a1")["job_id"]
        self.assertEqual(trabajos_work.publicar_trabajo(self.drive, job, CARPETA)["resultado"], "creado")

    def test_forzar_no_inventa_filas_ni_sirve_para_un_mensaje_ya_interpretado(self):
        with self.assertRaises(TrabajoRechazado):
            trabajos_work.forzar_elegibilidad("T1", "no-existe")
        self.fila("T2", "p1", estado="procesado", disposicion="interpretado_por_work")
        with self.assertRaises(TrabajoRechazado):
            trabajos_work.forzar_elegibilidad("T2", "p1")
        self.assertEqual(self.eventos("trabajo_elegibilidad_forzada"), [])

    def test_forzar_un_mensaje_no_libera_a_los_demas(self):
        self.fila("T1", "n1", "noreply@tm.openai.com", "[Task Update]")
        self.fila("T1", "n2", "noreply@tm.openai.com", "[Task Update]")
        trabajos_work.forzar_elegibilidad("T1", "n1")
        trabajos_work.crear_trabajo("T1", "n1")
        with self.assertRaises(NoElegible):
            trabajos_work.crear_trabajo("T1", "n2")

    def test_la_cli_crea_con_forzar_y_se_niega_sin_el(self):
        self.fila("T1", "a1", "no-reply@portal.com", "Su cotización")
        salida = []
        self.assertEqual(work_jobs.main(["crear", "--thread-id", "T1", "--message-id", "a1"], salida=salida.append), 2)
        self.assertEqual(self.trabajos(), 0)
        self.assertEqual(work_jobs.main(["crear", "--thread-id", "T1", "--message-id", "a1", "--forzar-elegibilidad"],
                                        salida=salida.append), 0)
        self.assertEqual(self.trabajos(), 1)


class ListadoTests(Base):
    def test_el_listado_es_de_solo_lectura_y_no_muestra_asuntos(self):
        self.fila("T1", "m1", asunto="ASUNTO CONFIDENCIAL")
        self.fila("N1", "n1", "noreply@tm.openai.com", "[Task Update] ANKA 01")
        self.fila("T2", "x1", "no-reply@portal.com", "Su cotización")
        self.fila("T3", "p1", estado="procesado", disposicion="interpretado_por_work")
        antes = len(self.eventos("trabajo_excluido")) + len(self.eventos("trabajo_clasificacion_pendiente"))
        lista = trabajos_work.clasificar_pendientes()
        self.assertEqual({(f["thread_id"], f["veredicto"], f["motivo"]) for f in lista},
                         {("T1", "elegible", ""), ("N1", "excluido", "notificacion_de_chatgpt"),
                          ("T2", "revision", "aviso_automatico_con_terminos_comerciales")})
        self.assertNotIn("CONFIDENCIAL", json.dumps(lista))
        self.assertEqual(len(self.eventos("trabajo_excluido")) + len(self.eventos("trabajo_clasificacion_pendiente")),
                         antes)
        salida = []
        self.assertEqual(work_jobs.main(["elegibilidad"], salida=salida.append), 0)
        self.assertEqual(len(json.loads(salida[0])["pendientes"]), 3)
        salida.clear()
        work_jobs.main(["elegibilidad", "--thread-id", "N1"], salida=salida.append)
        self.assertEqual([f["message_id"] for f in json.loads(salida[0])["pendientes"]], ["n1"])

    def test_la_base_queda_integra(self):
        self.fila("N1", "n1", "noreply@tm.openai.com", "[Task Update]")
        with self.assertRaises(NoElegible):
            trabajos_work.crear_trabajo("N1", "n1")
        with store.lectura() as db:
            self.assertEqual(db.execute("PRAGMA integrity_check").fetchone()[0], "ok")


class ReceptorGuardaLaSenalTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.when = datetime(2026, 10, 16, 15, tzinfo=UTC)
        self.gmail = Gmail()
        for parche in (patch.object(recepcion, "_sondeo_lock", threading.Lock()),
                       patch.object(settings, "DATA_DIR", Path(self.temp.name))):
            parche.start()
            self.addCleanup(parche.stop)
        recepcion.sondear(self.gmail, {"own_addresses": [CUENTA]}, self.when)       # primera pasada: fija el cursor

    def registrar(self, mensaje, minuto=1):
        self.gmail.history_results = [event({"id": mensaje["id"], "threadId": mensaje["threadId"]})]
        self.gmail.mail_items[mensaje["id"]] = mensaje
        recepcion.sondear(self.gmail, {"own_addresses": [CUENTA]}, self.when + timedelta(minutes=minuto))
        with store.lectura() as db:
            fila = db.execute("SELECT state,disposition,evidence_json FROM recepcion_mensajes WHERE message_id=?",
                              (mensaje["id"],)).fetchone()
        return fila["state"], fila["disposition"], json.loads(fila["evidence_json"])

    def test_un_mensaje_con_cabecera_de_lista_guarda_la_senal_y_sigue_pendiente(self):
        boletin = message("b1", "tb1")
        boletin["payload"]["headers"].append({"name": "List-Unsubscribe", "value": "<mailto:baja@x.com>"})
        estado, disposicion, evidencia = self.registrar(boletin)
        self.assertEqual((estado, disposicion), PENDIENTE)                       # el receptor no cambia su criterio
        self.assertEqual(evidencia["noise_signal"], "list_unsubscribe")
        fila_ = {"sender": "Cliente <cliente@example.com>", "subject": "Boletín", "attachments_json": "[]",
                 "evidence_json": json.dumps(evidencia)}
        self.assertEqual(evaluar_elegibilidad(fila_, TERMINOS).estado, EXCLUIDO)

    def test_un_mensaje_de_una_persona_no_guarda_senal(self):
        estado, disposicion, evidencia = self.registrar(message("h1", "th1"))
        self.assertEqual((estado, disposicion), PENDIENTE)
        self.assertNotIn("noise_signal", evidencia)

    def test_spam_de_gmail_y_auto_submitted_guardan_su_senal(self):
        spam = message("s1", "ts1", labels=["SPAM"])
        auto = message("s2", "ts2")
        auto["payload"]["headers"].append({"name": "Auto-Submitted", "value": "auto-generated"})
        self.assertEqual(self.registrar(spam, 1)[2]["noise_signal"], "spam_gmail")
        self.assertEqual(self.registrar(auto, 2)[2]["noise_signal"], "auto_submitted")


if __name__ == "__main__":
    unittest.main()
