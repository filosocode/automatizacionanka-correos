"""Un envío incierto no se duplica ni se queda atascado (U-59). Nada se reenvía solo (R8).

Cubre: U-59
"""

from tests import _isolate  # noqa: F401  (debe ir primero)

import concurrent.futures
import tempfile
import unittest
from datetime import timedelta
from pathlib import Path
from unittest.mock import patch

from anka.cli import supervisor
from anka.dominio.errores import ErrorDeValidacion
from anka.dominio.reloj import ahora
from anka.dominio.status import QuoteStatus
from anka.infra import acciones_en_curso, settings, store
from anka.servicios import case_actions, envios, quotes
from anka.web import acciones, openapi
from tests.fake_drive import FakeDriveTree
from tests.test_app import FakeGmail, FakeMessages, FakeRequest, FakeUsers, complete_analysis, encoded_message, test_config


class _Mensajes(FakeMessages):
    def list(self, **kwargs):
        self.gmail.busquedas.append(kwargs["q"])
        return FakeRequest({"messages": self.gmail.en_enviados})


class _Usuarios(FakeUsers):
    def messages(self):
        return _Mensajes(self.gmail)


class GmailConEnviados(FakeGmail):
    """Gmail falso cuya carpeta Enviados se puede llenar; cuenta los envíos que se le piden."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.en_enviados: list[dict] = []
        self.busquedas: list[str] = []
        self.caido = False

    def users(self):
        if self.caido:
            raise ConnectionError("sin red")
        return _Usuarios(self)

    def envios_pedidos(self) -> int:
        return len(self.sent_messages) + len(self.sent_drafts)


class Base(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        self.drive = FakeDriveTree({"Minera Sur": {}})
        self.gmail = GmailConEnviados({"id": "t-1", "messages": [
            encoded_message("m-1", "cliente@example.com", "Solicito cotización.", 1, subject="Solicitud")]})
        for parche in (
            patch.object(settings, "DATA_DIR", self.root),
            patch("anka.servicios.case_actions.load_config", return_value=test_config()),
            patch("anka.servicios.case_actions.services", side_effect=lambda: (self.gmail, self.drive)),
        ):
            parche.start()
            self.addCleanup(parche.stop)
        analysis = complete_analysis() | {"received_at": "2026-09-01T09:00:00-05:00"}
        db = store.open_db(self.root)
        store.save(db, "t-1", "m-1", QuoteStatus.REVIEW, "cliente@example.com", analysis)
        db.commit()
        db.close()

    def envejecer(self, minutos):
        """Hace más viejas las reservas ya guardadas."""
        db = store.connect()
        db.execute("UPDATE mail_operations SET created_at=?",
                   ((ahora() - timedelta(minutes=minutos)).isoformat(),))
        db.commit()
        db.close()

    def estado_de_las_reservas(self):
        db = store.connect()
        try:
            return [row["state"] for row in db.execute("SELECT state FROM mail_operations ORDER BY created_at")]
        finally:
            db.close()

    def id_enviado(self):
        db = store.connect()
        try:
            return db.execute("SELECT sent_message_id FROM quotes WHERE thread_id='t-1'").fetchone()[0]
        finally:
            db.close()

    def eventos(self, tipo):
        db = store.connect()
        try:
            return [row["summary"] for row in db.execute(
                "SELECT summary FROM case_events WHERE thread_id='t-1' AND kind=?", (tipo,))]
        finally:
            db.close()


# Cubre: U-59
class UnSoloEnvioEnCurso(Base):
    def test_otro_texto_del_mismo_caso_no_se_reserva_mientras_haya_uno_reciente(self):
        envios.reservar("t-1", "cliente@example.com", "Hola.")
        for texto, destino in (("Hola,", "cliente@example.com"), ("Hola.", "otro@example.com")):
            with self.subTest(texto=texto, destino=destino), self.assertRaises(ErrorDeValidacion) as caido:
                envios.reservar("t-1", destino, texto)
            self.assertIn("Ya hay un envío en curso para este caso", str(caido.exception))
        self.assertEqual(self.estado_de_las_reservas(), ["pending"])

    def test_otro_caso_no_se_afecta_y_una_reserva_vieja_no_bloquea_la_reserva(self):
        envios.reservar("t-2", "cliente@example.com", "Hola.")
        self.assertTrue(envios.reservar("t-1", "cliente@example.com", "Hola."))
        self.envejecer(envios.MINUTOS_EN_CURSO + 1)
        self.assertTrue(envios.reservar("t-1", "cliente@example.com", "Otro texto."))

    def test_una_reserva_confirmada_no_bloquea(self):
        clave, _ = envios.reservar("t-1", "cliente@example.com", "Hola.")
        envios.confirmar(clave, "g1")
        self.assertTrue(envios.reservar("t-1", "cliente@example.com", "Otro texto."))

    def test_dos_pestañas_con_textos_distintos_envian_un_solo_correo(self):
        def enviar(texto):
            try:
                case_actions.send_dashboard_reply("t-1", texto)
                return "enviado"
            except ValueError:
                return "rechazado"

        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
            resultados = list(pool.map(enviar, ["Hola, recibimos su solicitud.", "Hola, recibimos su solicitud!"]))
        # el segundo (si coincidió con la ventana del primero) se rechaza; nunca salen dos si se solapan
        self.assertIn("enviado", resultados)
        self.assertLessEqual(self.gmail.envios_pedidos(), 2)
        if resultados.count("enviado") == 2:       # no se solaparon: el primero ya estaba confirmado
            self.assertEqual(self.estado_de_las_reservas(), ["sent", "sent"])

    def test_la_reserva_guarda_el_texto_revisado(self):
        envios.reservar("t-1", "cliente@example.com", "Texto revisado.")
        self.assertEqual(envios.pendientes_del_hilo("t-1")[0]["body"], "Texto revisado.")


# Cubre: U-59
class SeAclaraConGmailSinReenviar(Base):
    def reservar_viejo(self, texto="Hola, recibimos su solicitud."):
        clave, message_id = envios.reservar("t-1", "cliente@example.com", texto)
        self.envejecer(envios.MINUTOS_EN_CURSO + 1)
        return clave, message_id

    def test_en_enviados_se_da_por_enviado_y_el_caso_avanza(self):
        _, message_id = self.reservar_viejo()
        self.gmail.en_enviados = [{"id": "g-9", "threadId": "t-1"}]
        resultado = case_actions.reconciliar_envios_pendientes()
        self.assertEqual(resultado, ["t-1: enviado"])
        self.assertIn(message_id.strip("<>"), self.gmail.busquedas[0])
        self.assertEqual(self.estado_de_las_reservas(), ["sent"])
        caso = quotes.get_quote("t-1")
        self.assertEqual(self.id_enviado(), "g-9")
        self.assertIn(caso["status"], (QuoteStatus.RESPONSE_SENT, QuoteStatus.WAITING, QuoteStatus.REVIEW))
        self.assertEqual(caso["draft_body"], "Hola, recibimos su solicitud.")
        self.assertEqual(len(self.eventos("send_reconciled")), 1)
        self.assertEqual(self.gmail.envios_pedidos(), 0)

    def test_si_no_esta_se_libera_con_aviso_y_no_se_envia_nada(self):
        self.reservar_viejo()
        self.gmail.en_enviados = []
        self.assertEqual(case_actions.reconciliar_envios_pendientes(), ["t-1: liberado"])
        self.assertEqual(self.estado_de_las_reservas(), [])
        self.assertEqual(len(self.eventos("send_released")), 1)
        self.assertEqual(self.gmail.envios_pedidos(), 0)
        self.assertEqual(quotes.get_quote("t-1")["status"], QuoteStatus.REVIEW)       # el caso no avanzó

    def test_con_gmail_caido_no_cambia_nada(self):
        self.reservar_viejo()
        self.gmail.caido = True
        self.assertEqual(case_actions.reconciliar_envios_pendientes(), [])
        self.assertEqual(self.estado_de_las_reservas(), ["pending"])

    def test_una_reserva_reciente_no_se_toca_ni_se_consulta_a_gmail(self):
        envios.reservar("t-1", "cliente@example.com", "Hola.")
        self.assertEqual(case_actions.reconciliar_envios_pendientes(), [])
        self.assertEqual(self.gmail.busquedas, [])

    def test_enviar_aclara_antes_las_viejas_y_ese_envio_no_sale(self):
        self.reservar_viejo("Texto viejo.")
        self.gmail.en_enviados = []
        with self.assertRaises(ErrorDeValidacion) as caido:
            case_actions.send_dashboard_reply("t-1", "Otro texto distinto.")
        self.assertIn("no apareció en Enviados", str(caido.exception))
        self.assertIn("Ahora no se envió nada", str(caido.exception))
        self.assertEqual(self.gmail.envios_pedidos(), 0)
        self.assertEqual(self.estado_de_las_reservas(), [])
        # con la reserva ya liberada, la persona puede volver a enviar
        self.assertIn("Correo enviado", case_actions.send_dashboard_reply("t-1", "Otro texto distinto."))
        self.assertEqual(self.gmail.envios_pedidos(), 1)

    def test_enviar_con_una_vieja_que_si_salio_actualiza_el_caso_y_no_envia(self):
        self.reservar_viejo()
        self.gmail.en_enviados = [{"id": "g-3", "threadId": "t-1"}]
        with self.assertRaises(ErrorDeValidacion) as caido:
            case_actions.send_dashboard_reply("t-1", "Otro texto distinto.")
        self.assertIn("sí había salido", str(caido.exception))
        self.assertEqual(self.gmail.envios_pedidos(), 0)
        self.assertEqual(self.id_enviado(), "g-3")

    def test_enviar_con_gmail_caido_dice_que_no_envio_nada(self):
        self.reservar_viejo()
        pendiente = envios.pendientes_del_hilo("t-1")
        with patch.object(case_actions, "case_email_context",
                          return_value=(quotes.get_quote("t-1"), self._gmail_que_falla(), {}, set())):
            with self.assertRaises(ErrorDeValidacion) as caido:
                case_actions.send_dashboard_reply("t-1", "Otro texto distinto.")
        self.assertIn("ahora no se pudo consultar Gmail", str(caido.exception))
        self.assertEqual(envios.pendientes_del_hilo("t-1"), pendiente)

    @staticmethod
    def _gmail_que_falla():
        class Roto:
            def users(self):
                raise ConnectionError("sin red")
        return Roto()


# Cubre: U-59
class MarcarComoEnviadoALaMano(Base):
    def test_cierra_la_reserva_y_avanza_el_caso_con_huella(self):
        envios.reservar("t-1", "cliente@example.com", "Hola, recibimos su solicitud.")
        mensaje = case_actions.marcar_enviado_a_mano("t-1")
        self.assertIn("marcado como enviado", mensaje)
        self.assertEqual(self.estado_de_las_reservas(), ["sent"])
        self.assertTrue(quotes.get_quote("t-1")["sent_at"])
        self.assertIn("una persona lo marcó como enviado a mano", self.eventos("send_reconciled")[0])
        self.assertEqual(quotes.RepositorioDeCasos().integridad("t-1")[0], True)
        self.assertEqual(self.gmail.envios_pedidos(), 0)
        self.assertEqual(self.gmail.busquedas, [])

    def test_sin_envios_por_confirmar_se_rechaza(self):
        with self.assertRaises(ErrorDeValidacion):
            case_actions.marcar_enviado_a_mano("t-1")

    def test_es_una_ruta_registrada_y_documentada(self):
        self.assertIn("/mark-sent", acciones.POST_ROUTES)
        _, post = openapi.rutas_documentadas()
        self.assertIn("/mark-sent", post)

    def test_la_pantalla_ofrece_el_boton_solo_con_un_envio_sin_confirmar(self):
        from anka.web import pantalla_caso
        with patch("anka.web.pantalla_caso.load_config", return_value=test_config()):
            sin = pantalla_caso.render_case("t-1")
            envios.reservar("t-1", "cliente@example.com", "Hola.")
            con = pantalla_caso.render_case("t-1")
        self.assertNotIn('action="/mark-sent"', sin)
        self.assertIn('action="/mark-sent"', con)
        self.assertIn("Hay un correo sin confirmar", con)


# Cubre: U-59
class CierreOrdenado(Base):
    def test_una_accion_en_curso_deja_una_marca_y_la_quita_al_terminar(self):
        with acciones_en_curso.en_curso("/send"):
            self.assertEqual(acciones_en_curso.acciones_activas(), ["/send"])
        self.assertEqual(acciones_en_curso.acciones_activas(), [])

    def test_una_marca_vieja_no_cuenta(self):
        with acciones_en_curso.en_curso("/send"):
            futuro = lambda: __import__("time").time() + acciones_en_curso.EDAD_MAXIMA + 5  # noqa: E731
            self.assertEqual(acciones_en_curso.acciones_activas(ahora=futuro), [])

    def test_espera_hasta_que_termine_y_se_rinde_al_pasar_el_tope(self):
        with acciones_en_curso.en_curso("/send"):
            tiempo = [0.0]

            def dormir(segundos):
                tiempo[0] += segundos

            self.assertFalse(acciones_en_curso.esperar_a_que_terminen(
                tope=2, pausa=0.5, dormir=dormir, ahora=lambda: tiempo[0]))
        self.assertTrue(acciones_en_curso.esperar_a_que_terminen(tope=2))

    def test_el_lanzador_espera_un_envio_reciente_y_reinicia_igual_pasado_el_tope(self):
        envios.reservar("t-1", "cliente@example.com", "Hola.")
        with patch.object(supervisor.time, "sleep"), patch.object(supervisor.time, "monotonic",
                                                                  side_effect=[0, 1, 2, 3, 99]):
            self.assertFalse(supervisor.cierre_ordenado(tope=5))
        self.envejecer(envios.MINUTOS_EN_CURSO + 1)
        self.assertFalse(envios.hay_envios_en_curso())
        self.assertTrue(supervisor.cierre_ordenado(tope=5))

    def test_el_servidor_marca_las_acciones_mientras_se_ejecutan(self):
        visto = []

        def accion(form):
            visto.append(acciones_en_curso.acciones_activas())
            return "/"

        from tests.test_seguridad_http import atender, formulario
        with patch.dict(acciones.POST_ROUTES, {"/mark-sent": accion}):
            estado, _, _, _ = atender("POST", "/mark-sent", formulario(thread_id="t-1"))
        self.assertEqual(estado, 303)
        self.assertEqual(visto, [["/mark-sent"]])
        self.assertEqual(acciones_en_curso.acciones_activas(), [])


if __name__ == "__main__":
    unittest.main()
