"""Reautorizar Google desde el dashboard (anka/servicios/autorizacion.py, spec/07-api.md, requisito T-15)."""

from tests import _isolate  # noqa: F401  (debe ir primero)

import json
import threading
import unittest
import urllib.parse
from unittest.mock import patch

from anka.dominio.errores import AutorizacionDeGoogle, ErrorDeAnka
from anka.servicios import autorizacion
from anka.servicios.autorizacion import EN_CURSO, FALLIDA, OK, VENCIDA, ReautorizacionDeGoogle
from anka.web import seguridad
from tests.test_openapi import request


class Reloj:
    def __init__(self):
        self.ahora = 1000.0

    def __call__(self):
        return self.ahora


def esperar(hilo, segundos=5):
    hilo.join(segundos)
    assert not hilo.is_alive(), "el hilo de reautorización no terminó"


# Cubre: T-15
class StateTests(unittest.TestCase):
    def test_a_valid_token_is_ok_and_is_not_rechecked_for_five_minutes(self):
        llamadas, reloj = [], Reloj()
        servicio = ReautorizacionDeGoogle(verificar=lambda: llamadas.append(1), reloj=reloj)
        self.assertEqual(servicio.estado().estado, OK)
        servicio.estado()
        servicio.estado()
        self.assertEqual(len(llamadas), 1)                       # una pantalla no hace una llamada a Google
        reloj.ahora += 301
        servicio.estado()
        self.assertEqual(len(llamadas), 2)

    def test_an_expired_authorization_says_so_and_is_rechecked_within_a_minute(self):
        reloj, vencida = Reloj(), [True]

        def verificar():
            if vencida[0]:
                raise AutorizacionDeGoogle("Google ya no acepta el token guardado.")

        servicio = ReautorizacionDeGoogle(verificar=verificar, reloj=reloj)
        estado = servicio.estado()
        self.assertEqual(estado.estado, VENCIDA)
        self.assertIn("Reautorizar Google", estado.texto)
        vencida[0] = False                                       # alguien reautorizó desde la terminal
        reloj.ahora += 61
        self.assertEqual(servicio.estado().estado, OK)

    def test_no_internet_is_unknown_not_expired_so_the_button_does_not_appear(self):
        def verificar():
            raise ConnectionError("sin red")

        estado = ReautorizacionDeGoogle(verificar=verificar).estado()
        self.assertEqual(estado.estado, "desconocido")

    def test_a_check_that_hangs_does_not_freeze_the_page(self):
        liberar = threading.Event()
        servicio = ReautorizacionDeGoogle(verificar=lambda: liberar.wait(10))
        with patch.object(autorizacion, "ESPERA_VERIFICACION", 0.1):
            estado = servicio.estado()
        liberar.set()
        self.assertEqual(estado.estado, "desconocido")


# Cubre: T-15
class ReauthorizeTests(unittest.TestCase):
    def test_it_runs_in_the_background_with_a_time_limit_and_clears_the_expired_state(self):
        recibido = {}
        valido = [False]

        def autorizar(timeout_seconds=None):
            recibido["tope"] = timeout_seconds
            valido[0] = True

        def verificar():
            if not valido[0]:
                raise AutorizacionDeGoogle()

        servicio = ReautorizacionDeGoogle(autorizar=autorizar, verificar=verificar, espera_maxima=300)
        self.assertEqual(servicio.estado().estado, VENCIDA)
        mensaje = servicio.iniciar()
        esperar(servicio._hilo)
        self.assertIn("5 minutos", mensaje)
        self.assertEqual(recibido["tope"], 300)                  # nunca espera para siempre
        self.assertEqual(servicio.estado().estado, OK)           # la caché se descartó: verifica con el token nuevo

    def test_a_second_request_while_one_is_running_does_not_open_another_window(self):
        liberar, abiertas = threading.Event(), []

        def autorizar(timeout_seconds=None):
            abiertas.append(1)
            liberar.wait(5)

        servicio = ReautorizacionDeGoogle(autorizar=autorizar, verificar=lambda: None)
        servicio.iniciar()
        self.assertEqual(servicio.estado().estado, EN_CURSO)
        self.assertIn("Ya hay una autorización en curso", servicio.iniciar())
        liberar.set()
        esperar(servicio._hilo)
        self.assertEqual(len(abiertas), 1)

    def test_closing_the_window_or_timing_out_is_a_failure_with_a_plain_message(self):
        def autorizar(timeout_seconds=None):
            raise TimeoutError("sin respuesta del navegador con datos tecnicos")

        servicio = ReautorizacionDeGoogle(autorizar=autorizar, verificar=lambda: None)
        with self.assertLogs("anka.servicios.autorizacion", "WARNING"):
            servicio.iniciar()
            esperar(servicio._hilo)
        estado = servicio.estado()
        self.assertEqual(estado.estado, FALLIDA)
        self.assertIn("Inténtalo de nuevo", estado.texto)
        self.assertNotIn("datos tecnicos", estado.texto)         # el detalle técnico no llega a la pantalla

    def test_a_missing_client_secret_is_explained_and_trying_again_clears_the_failure(self):
        intentos = []

        def autorizar(timeout_seconds=None):
            intentos.append(1)
            if len(intentos) == 1:
                raise ErrorDeAnka("Falta credentials.json del cliente OAuth de escritorio.")

        servicio = ReautorizacionDeGoogle(autorizar=autorizar, verificar=lambda: None)
        servicio.iniciar()
        esperar(servicio._hilo)
        self.assertIn("credentials.json", servicio.estado().texto)
        servicio.iniciar()
        esperar(servicio._hilo)
        self.assertEqual(servicio.estado().estado, OK)


# Cubre: T-15
class DashboardIntegration(unittest.TestCase):
    ORIGIN = "http://127.0.0.1:8765"

    def test_the_reauthorize_action_needs_the_token_and_answers_with_the_service_message(self):
        with patch("anka.web.acciones.reautorizar_google", return_value="Se abrió la ventana de Google") as lanzar:
            sin_token = request("POST", "/google/reautorizar", b"csrf_token=otro", origin=self.ORIGIN)
            self.assertEqual(sin_token["status"], 403)
            lanzar.assert_not_called()                           # sin el token ni siquiera se abre la ventana
            body = urllib.parse.urlencode({"csrf_token": seguridad.CSRF_TOKEN}).encode()
            hecho = request("POST", "/google/reautorizar", body, origin=self.ORIGIN)
        self.assertEqual(hecho["status"], 303)
        self.assertIn("Se%20abri", hecho["location"])
        lanzar.assert_called_once()

    def test_a_foreign_origin_cannot_trigger_it(self):
        body = urllib.parse.urlencode({"csrf_token": seguridad.CSRF_TOKEN}).encode()
        with patch("anka.web.acciones.reautorizar_google") as lanzar:
            ajeno = request("POST", "/google/reautorizar", body, origin="http://otro-sitio.example")
        self.assertEqual(ajeno["status"], 403)
        lanzar.assert_not_called()

    def test_the_state_route_and_the_api_report_the_google_status(self):
        from anka.servicios.autorizacion import EstadoDeGoogle
        vencida = EstadoDeGoogle(VENCIDA, "La autorización de Google venció o falta.")
        with patch("anka.web.server.estado_de_google", return_value=vencida):
            estado = json.loads(request("GET", "/state")["body"])
            api = json.loads(request("GET", "/api/autorizacion")["body"])
        self.assertEqual(estado["google"], {"estado": "vencida", "texto": vencida.texto})
        self.assertEqual(api, {"estado": "vencida", "texto": vencida.texto})

    def test_the_settings_menu_always_offers_the_button_and_the_alert_one_appears_only_when_needed(self):
        from anka.web import paginas
        with patch("anka.web.paginas.load_config", return_value={"active_mode": "local"}), \
                patch("anka.web.paginas.unread_notifications", return_value=[]):
            html = seguridad.add_csrf_tokens(paginas.page("Prueba", "<p>x</p>"))
        ajustes = html[html.index('<div class="settings-box">'):html.index("</details>")]
        self.assertIn('action="/google/reautorizar"', ajustes)      # visible siempre, dentro de Ajustes
        self.assertIn("Cuenta de Google", ajustes)
        self.assertIn("csrf_token", ajustes)
        self.assertIn("data-confirm", ajustes)
        self.assertEqual(html.count('action="/google/reautorizar"'), 2)   # el de Ajustes y el aviso rojo (oculto)
        self.assertIn('id="google-reauth" hidden', html)

    def test_every_page_carries_the_hidden_button_with_its_token_and_a_confirmation(self):
        from anka.web import paginas
        with patch("anka.web.paginas.load_config", return_value={"active_mode": "local"}), \
                patch("anka.web.paginas.unread_notifications", return_value=[]):
            html = seguridad.add_csrf_tokens(paginas.page("Prueba", "<p>x</p>"))
        formulario = html[html.index('id="google-reauth"'):]
        formulario = formulario[:formulario.index("</form>")]
        self.assertIn("hidden", html[html.index('<form method="post" action="/google/reautorizar"'):][:120])
        self.assertIn("csrf_token", formulario)                  # el servidor le pone el token como a cualquier formulario
        self.assertIn("data-confirm", formulario)                # avisa antes de abrir la ventana de Google
        self.assertNotIn("onclick", formulario)                  # sin manejadores en línea (T-07)


if __name__ == "__main__":
    unittest.main()
