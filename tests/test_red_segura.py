"""Red con tiempo límite y reintentos solo en lecturas y escrituras idempotentes; nunca al enviar.

Cubre: U-43
"""

from tests import _isolate  # noqa: F401

import unittest
from unittest.mock import MagicMock, patch

import httplib2

from anka.dominio.errores import ErrorDeAnka
from anka.infra import google_services
from anka.servicios import case_actions
from anka.servicios import envios
from anka.web.server import ReviewHandler


class _Respuesta(httplib2.Response):
    def __init__(self, estado):
        super().__init__({"status": estado})


class _Http:
    """Un cliente HTTP falso: devuelve 503 las primeras `fallos` veces y luego 200."""

    def __init__(self, fallos):
        self.fallos, self.llamadas = fallos, 0

    def request(self, uri, method="GET", body=None, headers=None, **kwargs):
        self.llamadas += 1
        if self.llamadas <= self.fallos:
            return _Respuesta(503), b"{}"
        return _Respuesta(200), b"{}"


def _ejecutar(metodo, fallos):
    solicitud = google_services._solicitud_con_reintentos()
    http = _Http(fallos)
    peticion = solicitud(http, lambda r, c: {}, "https://example.test/x", method=metodo, body=None, headers={})
    try:
        with patch("time.sleep"):
            peticion.execute()
    except Exception:
        pass
    return http.llamadas


class Reintentos(unittest.TestCase):
    def test_una_lectura_se_reintenta_con_espera_creciente(self):
        self.assertEqual(_ejecutar("GET", 2), 3)

    def test_una_lectura_que_sigue_fallando_se_rinde_tras_los_reintentos(self):
        self.assertEqual(_ejecutar("GET", 99), google_services.REINTENTOS + 1)

    def test_una_escritura_idempotente_se_reintenta(self):
        self.assertEqual(_ejecutar("PATCH", 1), 2)

    def test_un_post_nunca_se_reintenta(self):
        self.assertEqual(_ejecutar("POST", 2), 1)       # enviar, crear borrador o carpeta

    def test_el_tiempo_limite_es_de_30_segundos(self):
        self.assertEqual(google_services.TIEMPO_LIMITE, 30)
        usados = {}

        def construir(nombre, version, http=None, **kwargs):
            usados[nombre] = http
            return MagicMock()

        with patch.object(google_services, "credentials", return_value=MagicMock()), \
                patch("googleapiclient.discovery.build", construir):
            google_services.services()
        self.assertEqual(set(usados), {"gmail", "drive"})
        for http in usados.values():
            self.assertEqual(http.http.timeout, 30)

    def test_el_servidor_corta_conexiones_mudas(self):
        self.assertEqual(ReviewHandler.timeout, 30)


class Respuestas404(unittest.TestCase):
    """B3-08: un 404 de enviar el borrador no autoriza mandar el correo otra vez como mensaje nuevo."""

    def gmail(self, falla_update=None, falla_send=None):
        gmail = MagicMock()
        drafts = gmail.users.return_value.drafts.return_value
        messages = gmail.users.return_value.messages.return_value
        messages.send.return_value.execute.return_value = {"id": "nuevo"}
        drafts.send.return_value.execute.return_value = {"id": "del-borrador"}
        if falla_update:
            drafts.update.return_value.execute.side_effect = falla_update
        if falla_send:
            drafts.send.return_value.execute.side_effect = falla_send
        return gmail, messages

    @staticmethod
    def error(estado):
        exc = Exception("http")
        exc.resp = MagicMock(status=estado)      # type: ignore[attr-defined]
        return exc

    def test_404_al_actualizar_el_borrador_envia_como_mensaje_nuevo(self):
        gmail, messages = self.gmail(falla_update=self.error(404))
        self.assertEqual(case_actions._send_through_gmail(gmail, "T1", "raw", "d1")["id"], "nuevo")
        messages.send.assert_called_once()

    def test_404_al_enviar_el_borrador_no_manda_otro_correo_y_deja_la_reserva(self):
        gmail, messages = self.gmail(falla_send=self.error(404))
        with self.assertRaises(ErrorDeAnka) as contexto:
            case_actions._send_through_gmail(gmail, "T1", "raw", "d1")
        messages.send.assert_not_called()
        self.assertFalse(envios.fallo_definitivo(contexto.exception))     # incierto: la reserva se conserva

    def test_el_envio_del_borrador_correcto_no_toca_messages_send(self):
        gmail, messages = self.gmail()
        self.assertEqual(case_actions._send_through_gmail(gmail, "T1", "raw", "d1")["id"], "del-borrador")
        messages.send.assert_not_called()


if __name__ == "__main__":
    unittest.main()
