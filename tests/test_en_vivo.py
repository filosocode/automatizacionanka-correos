"""El dashboard se actualiza solo y el puente atiende «Buscar correos nuevos». Cubre: U-23, T-30"""

from tests import _isolate  # noqa: F401  (debe ir primero)

import io
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from anka.infra import settings, work_heartbeat
from anka.web import seguridad
from anka.web import server as web
from anka.web.views import render_page


class Salida(io.BytesIO):
    """Un `wfile` que falla como una pestaña cerrada tras N escrituras."""

    def __init__(self, fallar_en=None):
        super().__init__()
        self.escrituras = 0
        self.fallar_en = fallar_en

    def write(self, datos):
        self.escrituras += 1
        if self.fallar_en and self.escrituras >= self.fallar_en:
            raise BrokenPipeError
        return super().write(datos)


def manejador(salida):
    handler = web.ReviewHandler.__new__(web.ReviewHandler)
    handler.wfile = salida
    handler.headers = {"Host": "127.0.0.1:1"}
    handler.request_version = "HTTP/1.1"
    handler.requestline = "GET /eventos HTTP/1.1"
    handler.client_address = ("127.0.0.1", 1)
    handler.command = "GET"
    handler.path = "/eventos"
    return handler


class AvisosDelServidor(unittest.TestCase):
    def avisar(self, marcas, **ajustes):
        """Corre el canal con `marcas` como valores sucesivos de la marca de cambios; devuelve lo escrito."""
        salida = ajustes.pop("salida", Salida())
        handler = manejador(salida)
        valores = iter(marcas)
        compartido = SimpleNamespace(valor=lambda: next(valores))     # el marcador compartido (U-48), sin espera de 1 s
        with patch.object(web, "_marcador_compartido", compartido), \
                patch.object(web.time, "sleep"), patch.object(web, "STREAM_SECONDS", 0.55), \
                patch.object(web.time, "monotonic", side_effect=[0.0] + [0.1 * n for n in range(1, 400)]):
            handler._get_eventos({})
        return salida.getvalue().decode("utf-8"), handler

    def test_the_page_is_told_the_first_marker_and_then_every_change(self):
        # Cubre: U-23
        texto, _ = self.avisar(["a", "a", "b", "b", "c"])
        self.assertIn("text/event-stream", texto)
        self.assertIn("retry: 3000", texto)
        self.assertEqual(texto.count("data: "), 3)                 # a, b, c: lo que no cambia no se repite
        self.assertLess(texto.index("data: a"), texto.index("data: b"))
        self.assertLess(texto.index("data: b"), texto.index("data: c"))

    def test_a_heartbeat_keeps_an_idle_connection_alive(self):
        # Cubre: U-23
        with patch.object(web, "STREAM_KEEPALIVE", 2):
            texto, _ = self.avisar(["a"] * 6)
        self.assertEqual(texto.count("data: "), 1)
        self.assertIn(": latido", texto)

    def test_a_closed_tab_ends_the_stream_and_frees_its_place(self):
        # Cubre: U-23
        _, handler = self.avisar(["a", "b", "c"], salida=Salida(fallar_en=4))
        self.assertTrue(handler.close_connection)
        for _ in range(web.MAX_STREAMS):                            # el cupo quedó libre: caben todas las pestañas
            self.assertTrue(web._streams.acquire(blocking=False))
        for _ in range(web.MAX_STREAMS):
            web._streams.release()

    def test_a_closed_connection_is_noticed_without_waiting_for_the_next_write(self):
        # Cubre: U-23
        import socket
        servidor, cliente = socket.socketpair()
        handler = manejador(Salida())
        handler.connection = servidor
        self.assertFalse(handler._pestana_cerrada())            # abierta y callada
        cliente.close()
        self.assertTrue(handler._pestana_cerrada())             # el navegador la cerró
        servidor.close()

    def test_beyond_the_limit_a_tab_is_refused_and_keeps_the_periodic_check(self):
        # Cubre: U-23
        tomadas = 0
        while web._streams.acquire(blocking=False):
            tomadas += 1
        try:
            enviado = {}
            handler = manejador(Salida())
            handler._send_json = lambda valor, status=200: enviado.update(status=status)
            handler._get_eventos({})
            self.assertEqual(enviado["status"], 503)
        finally:
            for _ in range(tomadas):
                web._streams.release()

    def test_the_route_is_declared_and_the_page_listens_to_it(self):
        # Cubre: U-23
        self.assertIn("/eventos", web.GET_ROUTES)
        self.assertIn("/eventos", web.STREAM_ROUTES)
        pagina = render_page("t", "x", "", 0, "chatgpt_work")
        self.assertIn("new EventSource('/eventos')", pagina)
        self.assertIn("setInterval(poll", pagina)                   # la consulta periódica sigue de respaldo


class RevisarAhora(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        parche = patch.object(settings, "DATA_DIR", Path(self.temp.name))
        parche.start()
        self.addCleanup(parche.stop)

    def test_the_bridge_wakes_up_as_soon_as_it_is_asked(self):
        # Cubre: T-30
        pasos = []
        work_heartbeat.request_check_now()
        self.assertTrue(work_heartbeat.wait_for_next_cycle(30, sleep=pasos.append))
        self.assertEqual(pasos, [])                                 # no esperó ni un segundo
        self.assertFalse((Path(self.temp.name) / work_heartbeat.WAKE_FILE).exists())   # la señal se consume

    def test_without_a_request_it_waits_the_whole_cycle(self):
        # Cubre: T-30
        pasos = []
        self.assertFalse(work_heartbeat.wait_for_next_cycle(5, sleep=pasos.append))
        self.assertEqual(len(pasos), 5)

    def test_a_request_that_arrives_during_the_wait_is_attended_within_a_second(self):
        # Cubre: T-30
        llamadas = []

        def dormir(segundos):
            llamadas.append(segundos)
            if len(llamadas) == 3:
                work_heartbeat.request_check_now()

        self.assertTrue(work_heartbeat.wait_for_next_cycle(30, sleep=dormir))
        self.assertEqual(len(llamadas), 3)

    def test_the_bridge_cycle_is_thirty_seconds(self):
        # Cubre: T-30
        self.assertEqual(work_heartbeat.DEFAULT_INTERVAL, 30)
        fuente = (Path(__file__).resolve().parents[1] / "anka" / "cli" / "supervisor.py").read_text(encoding="utf-8")
        self.assertIn('"--seconds", "30"', fuente)

    def test_in_work_mode_the_button_wakes_the_bridge_instead_of_reading_gmail(self):
        # Cubre: T-30
        from anka.web import acciones
        with patch.object(acciones, "load_config", return_value={"active_mode": "chatgpt_work"}), \
                patch.object(acciones, "sync_status_text", return_value="Estado."):
            destino = acciones._post_refresh({})
        self.assertTrue((Path(self.temp.name) / work_heartbeat.WAKE_FILE).exists())
        self.assertIn("buscando%20correos%20nuevos", destino)

    def test_the_header_offers_the_button_only_in_work_mode(self):
        # Cubre: T-30
        self.assertIn('class="secondary" title="Busca correos nuevos en Gmail ahora', render_page("t", "x", "", 0, "chatgpt_work"))
        self.assertNotIn('class="secondary" title="Busca correos nuevos en Gmail ahora', render_page("t", "x", "", 0, "local"))
        self.assertIn(seguridad.CSRF_TOKEN, web.add_csrf_tokens(render_page("t", "x", "", 0, "chatgpt_work")))


if __name__ == "__main__":
    unittest.main()
