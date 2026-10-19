"""T-58: registro individual de un mensaje de prueba en la cola de recepción, sin sondeo general ni cursor.

Gmail simulado y SQLite temporal; nunca contacta Gmail real.

Cubre: T-58
"""

from tests import _isolate  # noqa: F401  (debe ir primero)

import base64
import tempfile
import threading
import unittest
from contextlib import redirect_stdout
from datetime import UTC, datetime
from io import StringIO
from pathlib import Path
from unittest.mock import patch

from anka.cli import gmail_reception
from anka.dominio.correo import contenido_nuevo
from anka.dominio.errores import ErrorDeAnka
from anka.infra import settings, store
from anka.servicios import recepcion, recepcion_individual, trabajos_work
from tests.test_recepcion import Gmail

ID = "1a154b6fa97924aa"
REMITENTE = "91andresmunoz@gmail.com"
MARCA = "PRUEBA-T57-001"
CUERPO = ("Solicitamos una cotización preliminar para un proyecto de prueba ubicado en Sopó, Cundinamarca.\n\n"
          "Este mensaje corresponde exclusivamente a una prueba ficticia interna de Grupo Anka.\n\n"
          f"Identificador: {MARCA}.\n")
CURSOR = ("history_id", "history_page_token", "last_success_at", "last_attempt_at", "initial_checkpoint_at",
          "recovery_state", "recovery_gap_start_at", "recovery_gap_end_at", "history_batch_count",
          "history_sync_confirmed", "consecutive_errors", "last_error_kind", "last_error_type")


def mensaje(cuerpo=CUERPO, asunto=f"Solicitud de cotización topografía — {MARCA}", de=REMITENTE, para=recepcion.CUENTA,
            hilo=ID, ident=ID, etiquetas=("INBOX",), adjuntos=()):
    partes = [{"mimeType": "text/plain", "filename": "", "body": {"data": base64.urlsafe_b64encode(cuerpo.encode()).decode()}}]
    partes += [{"filename": nombre, "mimeType": "application/pdf", "body": {"attachmentId": "a1", "size": 10}}
               for nombre in adjuntos]
    return {"id": ident, "threadId": hilo, "labelIds": list(etiquetas), "internalDate": "1792422628000",
            "snippet": "no se guarda", "payload": {"mimeType": "multipart/mixed", "headers": [
                {"name": "From", "value": f"Andrés <{de}>"}, {"name": "To", "value": para},
                {"name": "Subject", "value": asunto}, {"name": "Date", "value": "Mon, 19 Oct 2026 10:10:28 -0500"}],
                "parts": partes}}


class Base(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.directory = Path(self.temp.name)
        self.when = datetime(2026, 10, 19, 15, tzinfo=UTC)
        self.gmail = Gmail()
        self.gmail.mail_items[ID] = mensaje()
        for parche in (patch.object(recepcion, "_sondeo_lock", threading.Lock()),
                       patch.object(settings, "DATA_DIR", self.directory)):
            parche.start()
            self.addCleanup(parche.stop)
        recepcion.sondear(self.gmail, {"own_addresses": [recepcion.CUENTA]}, datetime(2026, 10, 16, 15, tzinfo=UTC))
        self.gmail.calls.clear()

    def registrar(self, **cambios):
        datos = {"thread_id": ID, "message_id": ID, "remitente_esperado": REMITENTE, "marca": MARCA, **cambios}
        return recepcion_individual.registrar_mensaje_individual(self.gmail, config={"own_addresses": [recepcion.CUENTA]},
                                                      now=self.when, **datos)

    def tabla(self, nombre, orden):
        with store.lectura() as db:
            return [dict(f) for f in db.execute(f"SELECT * FROM {nombre} ORDER BY {orden}")]

    def control(self):
        with store.lectura() as db:
            fila = dict(db.execute("SELECT * FROM recepcion_control").fetchone())
        return {k: fila[k] for k in CURSOR}, fila

    def foto(self):
        return (self.tabla("recepcion_mensajes", "message_id"), self.tabla("recepcion_eventos", "id"),
                self.tabla("trabajos_work", "job_id"), self.tabla("quotes", "thread_id"), self.control()[0])

    def sin_escrituras(self, fn, *args, **kwargs):
        antes = self.foto()
        with self.assertRaises(ErrorDeAnka) as ctx:
            fn(*args, **kwargs)
        self.assertEqual(self.foto(), antes)
        return str(ctx.exception)


class RegistroTests(Base):
    def test_registra_exactamente_ese_mensaje_con_el_procesamiento_normal(self):
        # Cubre: T-58
        resultado = self.registrar()
        self.assertEqual(resultado, {"resultado": "registrado", "thread_id": ID, "message_id": ID,
                                     "state": "pendiente_de_procesar", "disposition": "pendiente_de_interpretacion_work"})
        filas = self.tabla("recepcion_mensajes", "message_id")
        self.assertEqual([(f["thread_id"], f["message_id"], f["state"]) for f in filas],
                         [(ID, ID, "pendiente_de_procesar")])
        self.assertEqual(filas[0]["sender"], REMITENTE)
        self.assertEqual(filas[0]["snippet"], "")                       # el cuerpo no se guarda
        self.assertNotIn("Sopó", str(filas[0]))
        eventos = [(e["event_kind"], e["message_id"]) for e in self.tabla("recepcion_eventos", "id") if e["message_id"]]
        self.assertEqual(eventos, [("detectado", ID), ("pendiente_work", ID)])
        self.assertIn("registro_individual", filas[0]["evidence_json"])
        self.assertNotIn(MARCA, str(resultado))                         # la salida no lleva contenido

    def test_es_idempotente_la_segunda_vez_no_escribe_nada(self):
        # Cubre: T-58
        self.registrar()
        antes = self.foto()
        self.gmail.calls.clear()
        resultado = self.registrar()
        self.assertEqual(resultado["resultado"], "ya_registrado")
        self.assertEqual(resultado["state"], "pendiente_de_procesar")
        self.assertEqual(self.foto(), antes)
        self.assertEqual(self.gmail.calls, [])                          # ni siquiera consulta Gmail

    def test_no_toca_el_cursor_ni_las_demas_filas_ni_consulta_el_historial(self):
        # Cubre: T-58
        with store.transaccion() as db:
            for ident, estado in (("otro1", "por_leer"), ("otro2", "requiere_reintento")):
                db.execute("INSERT INTO recepcion_mensajes(account,thread_id,message_id,state,detected_at,updated_at,"
                           "evidence_json) VALUES(?,?,?,?,?,?,?)",
                           (recepcion.CUENTA, "hilo-" + ident, ident, estado, "2026-10-19T09:00:00-05:00",
                            "2026-10-19T09:00:00-05:00", "{}"))
        cursor_antes, completo_antes = self.control()
        ajenas_antes = [f for f in self.tabla("recepcion_mensajes", "message_id") if f["message_id"] != ID]
        self.registrar()
        cursor_despues, completo = self.control()
        self.assertEqual(cursor_despues, cursor_antes)
        self.assertIsNone(completo["lease_token"])                      # la reserva se soltó
        self.assertIsNone(completo["lease_until"])
        self.assertEqual([f for f in self.tabla("recepcion_mensajes", "message_id") if f["message_id"] != ID], ajenas_antes)
        nombres = [c[0] for c in self.gmail.calls]
        self.assertEqual(nombres, ["getProfile", "messages.get", "messages.get"])      # perfil, verificación y registro
        self.assertEqual({c[1] for c in self.gmail.calls if c[0] == "messages.get"}, {ID})
        self.assertNotIn("history.list", nombres)
        self.assertNotIn("messages.list", nombres)

    def test_no_crea_expedientes_ni_trabajos(self):
        # Cubre: T-58
        self.registrar()
        self.assertEqual(self.tabla("trabajos_work", "job_id"), [])
        self.assertEqual(self.tabla("quotes", "thread_id"), [])

    def test_work_jobs_crear_acepta_la_fila_registrada(self):
        # Cubre: T-58
        self.registrar()
        trabajo = trabajos_work.crear_trabajo(ID, ID)
        self.assertTrue(trabajo["creado"])
        self.assertEqual((trabajo["thread_id"], trabajo["message_id"]), (ID, ID))

    def test_un_mensaje_anterior_al_checkpoint_se_clasifica_como_siempre(self):
        # Cubre: T-58
        self.gmail.mail_items[ID]["internalDate"] = "1000"
        resultado = self.registrar()
        self.assertEqual((resultado["state"], resultado["disposition"]), ("procesado", "anterior_al_checkpoint_inicial"))


class RechazosTests(Base):
    def test_message_id_inexistente(self):
        # Cubre: T-58
        self.sin_escrituras(self.registrar, message_id="inexistente", thread_id="inexistente")

    def test_hilo_incorrecto(self):
        # Cubre: T-58
        self.assertIn("hilo", self.sin_escrituras(self.registrar, thread_id="otro-hilo"))

    def test_remitente_incorrecto(self):
        # Cubre: T-58
        self.assertIn("remitente", self.sin_escrituras(self.registrar, remitente_esperado="otra@example.com"))
        self.gmail.mail_items[ID] = mensaje(de="otra@example.com")
        self.assertIn("remitente", self.sin_escrituras(self.registrar))

    def test_cuenta_incorrecta(self):
        # Cubre: T-58
        with patch.object(Gmail, "getProfile", lambda self, userId: type("E", (), {"execute": lambda s: {
                "emailAddress": "otra@gmail.com", "historyId": "1"}})()):
            self.assertIn("autenticado", self.sin_escrituras(self.registrar))
        self.gmail.mail_items[ID] = mensaje(para="otra@gmail.com")
        self.assertIn("destinataria", self.sin_escrituras(self.registrar))

    def test_mensaje_no_ficticio_o_sin_marca(self):
        # Cubre: T-58
        self.gmail.mail_items[ID] = mensaje(cuerpo=f"Necesito una cotización real.\nIdentificador: {MARCA}.")
        self.assertIn("prueba ficticia", self.sin_escrituras(self.registrar))
        self.gmail.mail_items[ID] = mensaje(cuerpo="Es una prueba ficticia, sin identificador.")
        self.assertIn("Identificador", self.sin_escrituras(self.registrar))
        self.gmail.mail_items[ID] = mensaje(asunto="Solicitud de cotización")
        self.assertIn("asunto", self.sin_escrituras(self.registrar))

    def test_borrador_o_enviado(self):
        # Cubre: T-58
        for etiqueta in ("DRAFT", "SENT"):
            self.gmail.mail_items[ID] = mensaje(etiquetas=(etiqueta,))
            self.assertIn("borrador o un mensaje enviado", self.sin_escrituras(self.registrar))

    def test_parametros_invalidos_no_llegan_a_gmail(self):
        # Cubre: T-58
        for cambios in ({"thread_id": ""}, {"message_id": " "}, {"remitente_esperado": "no-es-correo"},
                        {"marca": "T57-001"}, {"marca": "prueba-t57-001"}, {"marca": "PRUEBA-"},
                        {"remitente_esperado": recepcion.CUENTA}):
            with self.subTest(cambios):
                self.sin_escrituras(self.registrar, **cambios)
                self.assertEqual(self.gmail.calls, [])

    def test_sin_recepcion_inicializada_no_la_inicializa(self):
        # Cubre: T-58
        with store.transaccion() as db:
            db.execute("DELETE FROM recepcion_control")
        antes = self.tabla("recepcion_mensajes", "message_id")
        with self.assertRaises(ErrorDeAnka):
            self.registrar()
        with store.lectura() as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM recepcion_control").fetchone()[0], 0)
        self.assertEqual(self.tabla("recepcion_mensajes", "message_id"), antes)
        self.assertEqual(self.gmail.calls, [])

    def test_una_reserva_ajena_vigente_impide_el_registro_sin_cambios(self):
        # Cubre: T-58
        import time
        token = recepcion._acquire(recepcion.CUENTA, time.time(), "2026-10-19T10:00:00-05:00")
        self.addCleanup(recepcion._release, recepcion.CUENTA, token)
        antes = self.tabla("recepcion_mensajes", "message_id")
        with self.assertRaises(ErrorDeAnka):
            self.registrar()
        self.assertEqual(self.tabla("recepcion_mensajes", "message_id"), antes)

    def test_un_fallo_al_leer_para_registrar_deja_la_cola_como_estaba(self):
        # Cubre: T-58
        antes = self.foto()
        with patch.object(recepcion, "_register_message", side_effect=TimeoutError("sin red")):
            with self.assertRaises(ErrorDeAnka):
                self.registrar()
        self.assertEqual(self.foto(), antes)
        self.assertIsNone(self.control()[1]["lease_token"])


class OrdenTests(unittest.TestCase):
    ARGS = ["--registrar-mensaje", "--thread-id", ID, "--message-id", ID, "--remitente-esperado", REMITENTE,
            "--marca", MARCA, "--confirmar"]

    def correr(self, argv):
        with patch.object(gmail_reception, "configure_logging"), patch.object(
                gmail_reception.gmail_reception_auth, "gmail_service",
                side_effect=AssertionError("no debe tocar Gmail")) as servicio, \
                patch.object(gmail_reception, "registrar_mensaje_individual") as registro:
            with self.assertRaises(SystemExit) as salida, redirect_stdout(StringIO()), \
                    patch("sys.stderr", new_callable=StringIO):
                gmail_reception.main(argv)
        servicio.assert_not_called()
        registro.assert_not_called()
        return salida.exception.code

    def test_exige_todos_los_parametros_y_la_confirmacion(self):
        # Cubre: T-58
        self.assertEqual(self.correr([a for a in self.ARGS if a != "--confirmar"]), 2)
        for opcion in ("--thread-id", "--message-id", "--remitente-esperado", "--marca"):
            with self.subTest(opcion):
                i = self.ARGS.index(opcion)
                self.assertEqual(self.correr(self.ARGS[:i] + [opcion, ""] + self.ARGS[i + 2:]), 2)
        self.assertEqual(self.correr(["--registrar-mensaje", "--confirmar"]), 2)

    def test_no_se_combina_con_otras_operaciones_y_los_parametros_no_van_solos(self):
        # Cubre: T-58
        for extra in (["--once"], ["--authorize"], ["--interval", "120"]):
            with self.subTest(extra):
                self.assertEqual(self.correr(self.ARGS + extra), 2)
        self.assertEqual(self.correr(["--once", "--message-id", ID]), 2)
        self.assertEqual(self.correr(["--once", "--confirmar"]), 2)

    def test_con_todo_correcto_usa_el_servicio_con_los_parametros_exactos(self):
        # Cubre: T-58
        with patch.object(gmail_reception, "configure_logging"), \
                patch.object(gmail_reception.gmail_reception_auth, "gmail_service", return_value="gmail") as servicio, \
                patch.object(gmail_reception, "InstanciaUnica"), \
                patch.object(gmail_reception, "registrar_mensaje_individual",
                             return_value={"resultado": "registrado"}) as registro, redirect_stdout(StringIO()) as out:
            codigo = gmail_reception.main(self.ARGS)
        self.assertEqual(codigo, 0)
        servicio.assert_called_once()
        registro.assert_called_once_with("gmail", ID, ID, REMITENTE, MARCA, {"own_addresses": [recepcion.CUENTA]})
        self.assertIn("registrado", out.getvalue())

    def test_un_rechazo_del_servicio_sale_con_error_sin_traza(self):
        # Cubre: T-58
        with patch.object(gmail_reception, "configure_logging"), \
                patch.object(gmail_reception.gmail_reception_auth, "gmail_service", return_value="gmail"), \
                patch.object(gmail_reception, "InstanciaUnica"), \
                patch.object(gmail_reception, "registrar_mensaje_individual", side_effect=ErrorDeAnka("no")), \
                patch("sys.stderr", new_callable=StringIO):
            with self.assertRaises(SystemExit) as salida:
                gmail_reception.main(self.ARGS)
        self.assertEqual(salida.exception.code, 1)


SEGUIMIENTO = "1a155f5531f151d3"
MARCA_SEGUIMIENTO = "PRUEBA-T59-SEGUIMIENTO-001"
ASUNTO_DE_RESPUESTA = f"Re: Solicitud de cotización topografía — {MARCA}"
NUEVO = ("Como seguimiento, confirmamos que el área definitiva es de 6,5 hectáreas.\n\n"
         "Este mensaje es exclusivamente una prueba ficticia interna de Grupo Anka.\n\n"
         f"Identificador: {MARCA_SEGUIMIENTO}.\n")
ATRIBUCION = "El lun, 19 oct 2026 a las 10:10, Andrés <91andresmunoz@gmail.com> escribió:"


def respuesta(nuevo=NUEVO, cita=CUERPO, atribucion=ATRIBUCION):
    """Una respuesta como la de Gmail en texto plano: lo nuevo, la atribución y el historial con «>» en cada línea."""
    citado = "\n".join("> " + linea if linea else ">" for linea in cita.splitlines())
    return f"{nuevo}\n{atribucion}\n{citado}\n"


def seguimiento(cuerpo=None, asunto=ASUNTO_DE_RESPUESTA, **cambios):
    return mensaje(cuerpo=respuesta() if cuerpo is None else cuerpo, asunto=asunto, ident=SEGUIMIENTO, **cambios)


class SeguimientoTests(Base):
    """T-58, D-165: la respuesta a un hilo con expediente se registra con la marca en su contenido NUEVO."""

    def setUp(self):
        super().setUp()
        from anka.dominio.status import QuoteStatus
        with store.transaccion() as db:
            store.save(db, ID, ID, QuoteStatus.REVIEW, "cliente@example.com",
                       {"project_name": "P", "activities": [{"activity": "Levantamiento", "quantity": "6"}],
                        "received_at": "2026-10-19T10:10:28-05:00"})
            store.save(db, "otro-hilo", "o1", QuoteStatus.REVIEW, "otro@example.com",
                       {"project_name": "Otro", "activities": [{"activity": "Otra"}], "received_at": "x"})
        self.gmail.mail_items[SEGUIMIENTO] = seguimiento()
        self.gmail.calls.clear()

    def registrar_seguimiento(self, **cambios):
        return self.registrar(message_id=SEGUIMIENTO, marca=MARCA_SEGUIMIENTO, **cambios)

    def rechazo(self, cuerpo=None, asunto=ASUNTO_DE_RESPUESTA, **cambios):
        self.gmail.mail_items[SEGUIMIENTO] = seguimiento(cuerpo, asunto)
        return self.sin_escrituras(self.registrar_seguimiento, **cambios)

    def test_un_seguimiento_valido_cuyo_asunto_conserva_la_marca_anterior_se_registra(self):
        # Cubre: T-58
        resultado = self.registrar_seguimiento()
        self.assertEqual((resultado["resultado"], resultado["thread_id"], resultado["message_id"]),
                         ("registrado", ID, SEGUIMIENTO))
        filas = [f for f in self.tabla("recepcion_mensajes", "message_id") if f["message_id"] == SEGUIMIENTO]
        self.assertEqual([(f["thread_id"], f["sender"]) for f in filas], [(ID, REMITENTE)])
        self.assertEqual(filas[0]["snippet"], "")
        self.assertNotIn(MARCA_SEGUIMIENTO, str(resultado))
        eventos = [e["event_kind"] for e in self.tabla("recepcion_eventos", "id") if e["message_id"] == SEGUIMIENTO]
        self.assertEqual(eventos, ["detectado", "pendiente_work"])

    def test_un_seguimiento_no_cambia_el_cursor_los_expedientes_ni_crea_trabajos(self):
        # Cubre: T-58
        cursor, _ = self.control()
        quotes_antes, trabajos_antes = self.tabla("quotes", "thread_id"), self.tabla("trabajos_work", "job_id")
        self.registrar_seguimiento()
        self.assertEqual(self.control()[0], cursor)
        self.assertEqual(self.tabla("quotes", "thread_id"), quotes_antes)
        self.assertEqual(self.tabla("trabajos_work", "job_id"), trabajos_antes)
        nombres = [c[0] for c in self.gmail.calls]
        self.assertNotIn("history.list", nombres)
        self.assertNotIn("messages.list", nombres)

    def test_la_segunda_ejecucion_no_duplica_nada(self):
        # Cubre: T-58
        self.registrar_seguimiento()
        antes = self.foto()
        self.gmail.calls.clear()
        self.assertEqual(self.registrar_seguimiento()["resultado"], "ya_registrado")
        self.assertEqual(self.foto(), antes)
        self.assertEqual(self.gmail.calls, [])

    def test_la_marca_solo_en_la_cita_se_rechaza(self):
        # Cubre: T-58
        nuevo = "Confirmamos el área.\n\nEs una prueba ficticia interna de Grupo Anka.\n"
        cita = f"Primera prueba ficticia.\n\nIdentificador: {MARCA_SEGUIMIENTO}.\n"
        self.assertIn("Identificador", self.rechazo(respuesta(nuevo, cita)))

    def test_la_declaracion_ficticia_solo_en_la_cita_se_rechaza(self):
        # Cubre: T-58
        nuevo = f"Confirmamos el área.\n\nIdentificador: {MARCA_SEGUIMIENTO}.\n"
        self.assertIn("prueba ficticia", self.rechazo(respuesta(nuevo)))     # la cita (CUERPO) sí la declara

    def test_la_marca_debe_ser_una_linea_exacta_no_una_frase(self):
        # Cubre: T-58
        for linea in (f"Vea el Identificador: {MARCA_SEGUIMIENTO} de arriba.", f"Identificador: {MARCA_SEGUIMIENTO}-2",
                      f"Identificador {MARCA_SEGUIMIENTO}", f"Id: {MARCA_SEGUIMIENTO}"):
            with self.subTest(linea):
                nuevo = f"Una prueba ficticia interna.\n\n{linea}\n"
                self.assertIn("Identificador", self.rechazo(respuesta(nuevo)))

    def test_la_linea_exacta_admite_espacios_mayusculas_y_punto_final(self):
        # Cubre: T-58
        for linea in (f"Identificador: {MARCA_SEGUIMIENTO}", f"  IDENTIFICADOR:   {MARCA_SEGUIMIENTO}.  "):
            with self.subTest(linea):
                self.gmail.mail_items[SEGUIMIENTO] = seguimiento(respuesta(f"Una prueba ficticia interna.\n\n{linea}\n"))
                self.assertEqual(self.registrar_seguimiento()["resultado"], "registrado")
                with store.transaccion() as db:
                    db.execute("DELETE FROM recepcion_mensajes WHERE message_id=?", (SEGUIMIENTO,))

    def test_un_hilo_sin_expediente_se_rechaza_con_las_reglas_originales(self):
        # Cubre: T-58
        with store.transaccion() as db:
            db.execute("DELETE FROM quotes WHERE thread_id=?", (ID,))
        self.assertIn("asunto", self.rechazo())

    def test_una_solicitud_nueva_conserva_la_validacion_estricta(self):
        # Cubre: T-58
        with store.transaccion() as db:
            db.execute("DELETE FROM quotes WHERE thread_id=?", (ID,))
        self.gmail.mail_items[SEGUIMIENTO] = mensaje(ident=SEGUIMIENTO, asunto=f"Solicitud — {MARCA_SEGUIMIENTO}",
                                                     cuerpo=NUEVO)
        self.assertEqual(self.registrar_seguimiento()["resultado"], "registrado")

    def test_remitente_destinatario_hilo_y_borrador_se_siguen_verificando(self):
        # Cubre: T-58
        self.assertIn("remitente", self.sin_escrituras(self.registrar_seguimiento, remitente_esperado="otra@example.com"))
        self.gmail.mail_items[SEGUIMIENTO] = seguimiento(para="otra@gmail.com")
        self.assertIn("destinataria", self.sin_escrituras(self.registrar_seguimiento))
        self.gmail.mail_items[SEGUIMIENTO] = seguimiento(etiquetas=("DRAFT",))
        self.assertIn("borrador", self.sin_escrituras(self.registrar_seguimiento))
        self.gmail.mail_items[SEGUIMIENTO] = seguimiento()
        self.assertIn("hilo", self.sin_escrituras(self.registrar_seguimiento, thread_id="otro-hilo"))

    def test_la_separacion_ambigua_entre_lo_nuevo_y_lo_citado_se_rechaza(self):
        # Cubre: T-58
        citado = "\n".join("> " + linea for linea in CUERPO.splitlines())
        casos = {
            "cita sin atribución": f"{NUEVO}\n{citado}\n",
            "atribución sin cita": f"{NUEVO}\n{ATRIBUCION}\n",
            "historial de Outlook sin «>»": f"{NUEVO}\nDe: Andrés <91andresmunoz@gmail.com>\nEnviado: lunes\n{CUERPO}",
            "reenvío": f"{NUEVO}\n---------- Forwarded message ---------\n{CUERPO}",
            "respuesta intercalada": f"{ATRIBUCION}\n> {CUERPO.splitlines()[0]}\n{NUEVO}\n> {CUERPO.splitlines()[2]}\n",
            "texto debajo de la cita": respuesta() + "\nGracias de antemano.\n",
            "dos atribuciones": f"{ATRIBUCION}\n{NUEVO}\n{respuesta()}",
        }
        for nombre, cuerpo in casos.items():
            with self.subTest(nombre):
                self.assertIn("fiabilidad", self.rechazo(cuerpo))

    def test_solo_se_toca_ese_mensaje_aunque_haya_otros_en_la_cola(self):
        # Cubre: T-58
        ajenas = [f for f in self.tabla("recepcion_mensajes", "message_id") if f["message_id"] != SEGUIMIENTO]
        self.registrar_seguimiento()
        self.assertEqual([f for f in self.tabla("recepcion_mensajes", "message_id") if f["message_id"] != SEGUIMIENTO],
                         ajenas)
        self.assertEqual({c[1] for c in self.gmail.calls if c[0] == "messages.get"}, {SEGUIMIENTO})


class ContenidoNuevoTests(unittest.TestCase):
    """Cubre: T-58"""

    def test_una_respuesta_de_gmail_separa_lo_nuevo_de_lo_citado(self):
        self.assertEqual(contenido_nuevo(respuesta()), NUEVO.strip())

    def test_la_atribucion_en_dos_lineas_y_en_ingles(self):
        partida = "El lun, 19 oct 2026 a las 10:10, Andrés <\n91andresmunoz@gmail.com> escribió:"
        self.assertEqual(contenido_nuevo(respuesta(atribucion=partida)), NUEVO.strip())
        self.assertEqual(contenido_nuevo(respuesta(atribucion="On Mon, Oct 19, 2026 at 10:10 AM Andres wrote:")),
                         NUEVO.strip())

    def test_un_mensaje_sin_historial_es_todo_nuevo_y_los_finales_de_linea_no_importan(self):
        self.assertEqual(contenido_nuevo(NUEVO), NUEVO.strip())
        self.assertEqual(contenido_nuevo(respuesta().replace("\n", "\r\n")), NUEVO.strip())

    def test_lo_dudoso_devuelve_none(self):
        for texto in (f"{NUEVO}\n> citado sin atribución\n", f"{NUEVO}\n{ATRIBUCION}\n", f"{NUEVO}\n-----Original Message-----\n",
                      f"{NUEVO}\nFrom: a@b.com\nSent: lunes\n"):
            with self.subTest(texto[-30:]):
                self.assertIsNone(contenido_nuevo(texto))


if __name__ == "__main__":
    unittest.main()
