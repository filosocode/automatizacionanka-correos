"""Servidor HTTP local del dashboard: enruta las páginas (GET) y las acciones de los formularios (POST)."""

from __future__ import annotations

from typing import Any
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import re
import secrets
import select
import socket
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import webbrowser
from datetime import datetime
from anka.dominio.errores import AutorizacionDeGoogle, ErrorDeAnka
from anka.dominio.activities import build_workbook
from anka.infra.logging_setup import get_logger
from anka.infra.store import request_scope
from anka.infra.settings import load_config
from anka.web.templating import render

from anka.servicios import salud
from anka.servicios.autorizacion import estado_de_google
from anka.infra.work_heartbeat import bridge_state
from anka.servicios.quotes import (
    all_quotes, archived_threads, change_marker, get_quote, unread_thread_count,
)

from anka.web import estaticos, openapi, paginas
from anka.web.marcador import MarcadorCompartido
from anka.web import formularios_pendientes as pendientes
from anka.web.acciones import POST_ROUTES, field, int_field
from anka.web.api import a_json, resumen_de_caso
from anka.web import pantalla_caso
from anka.web.views import marcar_como_error
from anka.web.seguridad import (
    CSRF_TOKEN, CuerpoNoValido, add_csrf_tokens, cabeceras_de_seguridad, csp_docs, es_mensaje_para_la_persona,
    id_de_error, is_local_host_header, is_same_origin, longitud_del_cuerpo, nombre_seguro,
)

log = get_logger(__name__)


# Rutas GET: ruta -> método del manejador. `openapi.py` las documenta y una prueba comprueba que no falte ninguna.
GET_ROUTES: dict[str, str] = {
    "/": "_get_index",
    "/requests": "_get_requests",
    "/discarded": "_get_discarded",
    "/case": "_get_case",
    "/acta": "_get_acta",
    "/archive": "_get_archive",
    "/export.xlsx": "_get_export",
    "/state": "_get_state",
    "/eventos": "_get_eventos",
    "/api/casos": "_get_api_cases",
    "/api/salud": "_get_api_health",
    "/api/autorizacion": "_get_api_authorization",
    "/docs": "_get_docs",
    "/openapi.json": "_get_openapi",
}
GET_PATTERNS: dict[str, str] = {"/api/casos/{thread_id}": "_get_api_case", "/static/{archivo}": "_get_static"}
_GET_PATTERNS = [(re.compile(re.sub(r"\{\w+\}", "([^/]+)", template)), name) for template, name in GET_PATTERNS.items()]


# Canal de avisos en vivo (U-23): la página deja una conexión abierta y el servidor le avisa cuando algo cambia.
STREAM_ROUTES = frozenset({"/eventos"})
MAX_STREAMS = 16            # pestañas conectadas a la vez; las demás siguen con la consulta periódica de siempre
STREAM_SECONDS = 600        # la conexión se renueva sola: el navegador reconecta (no queda colgada para siempre)
STREAM_CHECK = 1.0          # cada cuánto mira si cambió algo (una consulta ligera)
STREAM_KEEPALIVE = 15       # latido para que nada corte una conexión inactiva
_streams = threading.BoundedSemaphore(MAX_STREAMS)
# El marcador lo calcula una sola consulta por segundo para todas las pestañas conectadas (U-48).
_marcador_compartido = MarcadorCompartido(lambda: change_marker(), STREAM_CHECK)

MAX_CAMPOS = 10_000         # campos de un formulario: la tabla más grande (200 filas × 11) cabe de sobra


def _mensaje(query: dict[str, list[str]]) -> str:
    """El aviso de la dirección; con `aviso=error` se marca como fallo para anunciarlo como alerta (U-37)."""
    texto = field(query, "message")
    return marcar_como_error(texto) if field(query, "aviso") == "error" else texto


def _senalar_fallo(location: str) -> str:
    """Añade `aviso=error` a la dirección a la que se redirige tras una acción que terminó con un aviso de fallo."""
    ruta, almohadilla, ancla = location.partition("#")
    if "message=" not in ruta:
        return location
    return ruta + ("&" if "?" in ruta else "?") + "aviso=error" + almohadilla + ancla


class ReviewHandler(BaseHTTPRequestHandler):
    """Servidor HTTP local del dashboard: páginas por GET y acciones por POST con token."""
    timeout = 30      # una conexión que no habla en 30 s se corta y libera su hilo (U-43)
    protocol_version = "HTTP/1.1"      # la conexión se reutiliza; toda respuesta lleva su longitud (U-48)

    def _send_bytes(self, status: int, content_type: str, data: bytes,
                    extra_headers: dict[str, str] | None = None) -> None:
        self.send_response(status)
        headers = cabeceras_de_seguridad()
        headers["Content-Type"] = content_type
        headers.update(extra_headers or {})       # p. ej. la CSP propia de /docs o el nombre del Excel
        comprimido = estaticos.comprimir(data, content_type, self.headers.get("Accept-Encoding", ""))
        if estaticos.comprimible(content_type):
            headers["Vary"] = "Accept-Encoding"
        if comprimido is not None:                # texto grande y el cliente acepta gzip (U-48)
            data, headers["Content-Encoding"] = comprimido, "gzip"
        for name, value in headers.items():
            self.send_header(name, value)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _send_html(self, content: str, status: int = 200) -> None:
        self._send_bytes(status, "text/html; charset=utf-8", add_csrf_tokens(content).encode("utf-8"))

    def _send_json(self, value: Any, status: int = 200) -> None:
        self._send_bytes(status, "application/json; charset=utf-8", a_json(value))

    def _read_form(self) -> dict[str, list[str]]:
        """El formulario del POST; sin longitud válida, de más de 2 MiB o mal codificado lanza `CuerpoNoValido`."""
        length = longitud_del_cuerpo(self.headers.get("Content-Length"))
        try:
            data = self.rfile.read(length).decode("utf-8")
            return urllib.parse.parse_qs(data, max_num_fields=MAX_CAMPOS)
        except ValueError as exc:   # UnicodeDecodeError o demasiados campos
            raise CuerpoNoValido(400, "El formulario no se pudo leer.") from exc

    def _aviso(self, titulo: str, texto: str, status: int, thread_id: str = "",
               exc: BaseException | None = None) -> None:
        """Una página de aviso con salida: «Volver al caso» si se sabe cuál, «← Mis casos» y, si la causa fue la
        autorización de Google, el botón para reautorizar (solo la persona lo pulsa; D-27)."""
        pagina = render("empty.html", text=texto, salidas=True, volver_caso=thread_id,
                        reautorizar=isinstance(exc, AutorizacionDeGoogle))
        self._send_html(paginas.page(titulo, pagina), status)

    def _error_inesperado(self, metodo: str, ruta: str, exc: Exception, thread_id: str = "") -> None:
        """Un fallo del servidor: la persona ve un mensaje genérico con un código; el detalle queda solo en el registro.

        Un `ErrorDeAnka` sí muestra su texto, porque se escribió para la persona (D-17), y no pide «avisar con el
        código»: lo que dice ya es lo que hay que hacer (U-34.1)."""
        codigo = id_de_error()
        log.exception("Error atendiendo %s %s (código %s)", metodo, ruta, codigo)
        if isinstance(exc, ErrorDeAnka):
            texto = str(exc)
        else:
            texto = ("Ocurrió un error inesperado. " if not es_mensaje_para_la_persona(exc) else f"{exc} ")
            texto += f"Si se repite, avisa con el código de error {codigo}."
        if metodo == "POST" and ruta in pendientes.RUTAS_CON_TEXTO:
            texto += " Lo que escribiste se conserva: vuelve al caso y lo verás ahí."
        self._aviso("Error", texto, 500, thread_id, exc)

    def _reject(self, reason: str, thread_id: str = "") -> None:
        origin = getattr(self, "client_address", ("?",))[0]
        log.warning("Solicitud rechazada (%s) desde %s", reason, origin)
        self._aviso("Solicitud rechazada", reason, 403, thread_id)

    def do_GET(self) -> None:
        """Atiende las páginas del dashboard; un error se registra y se muestra sin romper el servidor."""
        try:
            if urllib.parse.urlparse(self.path).path in STREAM_ROUTES:
                return self._get()      # una conexión larga no puede retener la conexión a la base de la petición
            with request_scope():   # una sola conexión a la base para toda la petición
                self._get()
        except ValueError as exc:
            self._rechazada("GET", self.path.split("?")[0], exc, self._caso_de_la_url())
        except Exception as exc:
            self._error_inesperado("GET", self.path.split("?")[0], exc, self._caso_de_la_url())

    def _caso_de_la_url(self) -> str:
        """El caso que nombra la URL (`thread_id`), para ofrecer «Volver al caso» si la página falla."""
        consulta = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
        return field(consulta, "thread_id")[:200]

    def _get(self) -> None:
        if not is_local_host_header(self.headers.get("Host", "")):
            return self._reject("Host no permitido.")
        parsed = urllib.parse.urlparse(self.path)
        query = urllib.parse.parse_qs(parsed.query)
        method = GET_ROUTES.get(parsed.path)
        arguments: tuple[str, ...] = ()
        if method is None:
            for pattern, name in _GET_PATTERNS:
                found = pattern.fullmatch(parsed.path)
                if found:
                    method, arguments = name, tuple(urllib.parse.unquote(g) for g in found.groups())
                    break
        if method is None:
            return self._aviso("No encontrado", "Página no encontrada.", 404)
        getattr(self, method)(query, *arguments)

    # --- páginas (GET)
    def _get_index(self, query: dict[str, list[str]]) -> None:
        self._send_html(paginas.render_index(
            _mensaje(query), int_field(query, "mostrar"), field(query, "q"),
            field(query, "etapa") if "etapa" in query else None))

    def _get_requests(self, query: dict[str, list[str]]) -> None:
        agrupar = field(query, "agrupar", "1") != "0"
        self._send_html(paginas.render_requests(
            _mensaje(query), agrupar_hilos=agrupar, mostrar=int_field(query, "mostrar"), q=field(query, "q"),
            etapa=field(query, "etapa") if "etapa" in query else None, tipo=field(query, "tipo"),
            orden=field(query, "orden", "reciente")))

    def _get_discarded(self, query: dict[str, list[str]]) -> None:
        self._send_html(paginas.render_discarded(_mensaje(query)))

    def _get_case(self, query: dict[str, list[str]]) -> None:
        thread_id = field(query, "thread_id")
        if not get_quote(thread_id):        # un caso que no existe es un 404 con salida, no una pantalla vacía (U-34.1)
            return self._aviso("Caso no encontrado", "La solicitud no existe.", 404)
        try:
            pagina = pantalla_caso.render_case(
                thread_id, _mensaje(query), field(query, "template"),
                int_field(query, "change_id"), field(query, "to"), field(query, "preguntas"))
        except ErrorDeAnka as exc:          # p. ej. config.json incompleto: se dice qué falta, con salida (U-34.1)
            log.warning("%s: la pantalla del caso no se pudo armar (%s)", thread_id, exc, exc_info=True)
            return self._aviso("No se pudo abrir el caso", str(exc), 500, exc=exc)
        self._send_html(pagina)

    def _get_acta(self, query: dict[str, list[str]]) -> None:
        self._send_html(pantalla_caso.render_acta(field(query, "thread_id")))

    def _get_static(self, query: dict[str, list[str]], archivo: str) -> None:
        fijo = estaticos.buscar(archivo)
        if fijo is None:
            return self._aviso("No encontrado", "Página no encontrada.", 404)
        cabeceras = {"Cache-Control": fijo.cache, "ETag": fijo.etag}
        if estaticos.coincide_etag(self.headers.get("If-None-Match", ""), fijo.etag):
            return self._send_bytes(304, fijo.tipo, b"", cabeceras)       # el navegador ya tiene esta versión
        self._send_bytes(200, fijo.tipo, fijo.datos, cabeceras)

    def _get_archive(self, query: dict[str, list[str]]) -> None:
        self._send_html(paginas.page("Archivo", render("archive.html", items=archived_threads())))

    def _get_export(self, query: dict[str, list[str]]) -> None:
        thread_id = field(query, "thread_id")
        quotes = [get_quote(thread_id)] if thread_id else all_quotes()
        quotes = [item for item in quotes if item]
        if thread_id and not quotes:
            return self._aviso("No encontrado", "La solicitud no existe.", 404)
        stamp = datetime.now().strftime("%Y%m%d-%H%M")
        caso = (nombre_seguro(thread_id)[:8] or "caso") if thread_id else "todas"   # el id viene de la URL
        name = f"actividades-{caso}-{stamp}.xlsx"
        self._send_bytes(
            200, "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", build_workbook(quotes),
            {"Content-Disposition": f'attachment; filename="{name}"'})

    # --- lectura JSON y documentación (GET)
    def _get_state(self, query: dict[str, list[str]]) -> None:
        try:
            work = load_config().get("active_mode") == "chatgpt_work"
        except (ErrorDeAnka, OSError, ValueError):
            work = False
        state, text = bridge_state() if work else ("", "")
        google = estado_de_google()
        self._send_json({"v": change_marker(), "sin_leer": unread_thread_count(), "bridge": state, "text": text,
                         "google": {"estado": google.estado, "texto": google.texto}})

    def _get_eventos(self, query: dict[str, list[str]]) -> None:
        """Avisa a la página en cuanto cambia algo (U-23), en vez de esperar a su consulta periódica."""
        if not _streams.acquire(blocking=False):
            return self._send_json({"error": "Demasiadas pestañas conectadas."}, 503)
        try:
            self.send_response(200)
            for name, value in cabeceras_de_seguridad().items():
                self.send_header(name, value)
            self.send_header("Content-Type", "text/event-stream; charset=utf-8")
            self.send_header("Connection", "close")          # no tiene longitud: termina cuando se cierra
            self.end_headers()
            self.wfile.write(b"retry: 3000\n\n")
            last, quiet, started = None, 0.0, time.monotonic()
            while time.monotonic() - started < STREAM_SECONDS:
                marker = _marcador_compartido.valor()
                if marker != last:
                    last, quiet = marker, 0.0
                    self.wfile.write(f"data: {marker}\n\n".encode())
                elif quiet >= STREAM_KEEPALIVE:
                    quiet = 0.0
                    self.wfile.write(b": latido\n\n")
                self.wfile.flush()
                if self._pestana_cerrada():
                    break
                time.sleep(STREAM_CHECK)
                quiet += STREAM_CHECK
        except OSError:
            pass                        # la pestaña se cerró: es lo normal
        finally:
            _streams.release()
            self.close_connection = True

    def _pestana_cerrada(self) -> bool:
        """¿Cerró el navegador esta conexión? Se sabe sin escribir nada, para liberar el cupo al instante."""
        try:
            conexion = self.connection
            if not select.select([conexion], [], [], 0)[0]:
                return False
            return conexion.recv(1, socket.MSG_PEEK) == b""
        except (AttributeError, OSError, ValueError):
            return False          # sin socket (pruebas) o ya roto: el siguiente envío lo decide

    def _get_api_cases(self, query: dict[str, list[str]]) -> None:
        include_discarded = field(query, "descartados").lower() in ("1", "true", "si", "sí")
        self._send_json([resumen_de_caso(caso) for caso in all_quotes(include_discarded)])

    def _get_api_authorization(self, query: dict[str, list[str]]) -> None:
        google = estado_de_google()
        self._send_json({"estado": google.estado, "texto": google.texto})

    def _get_api_health(self, query: dict[str, list[str]]) -> None:
        report = salud.informe(field(query, "profundo").lower() in ("1", "true", "si", "sí"))
        self._send_json(report, 200 if report["estado"] == "ok" else 503)

    def _get_api_case(self, query: dict[str, list[str]], thread_id: str) -> None:
        caso = get_quote(thread_id)
        if not caso:
            return self._send_json({"error": "La solicitud no existe."}, 404)
        self._send_json(caso)

    def _get_docs(self, query: dict[str, list[str]]) -> None:
        # La página lleva el token como cualquier pantalla con formularios; `/openapi.json` ya no (D-71).
        nonce = secrets.token_urlsafe(16)
        self._send_bytes(200, "text/html; charset=utf-8", openapi.docs_page(CSRF_TOKEN, nonce).encode("utf-8"),
                         {"Content-Security-Policy": csp_docs(nonce)})

    def _get_openapi(self, query: dict[str, list[str]]) -> None:
        self._send_json(openapi.build_spec())

    def do_POST(self) -> None:
        """Valida host, origen y token y ejecuta la acción de `POST_ROUTES`; redirige al resultado."""
        host = self.headers.get("Host", "")
        if not is_local_host_header(host):
            self.close_connection = True          # el cuerpo no se leyó: no se reutiliza la conexión
            return self._reject("Host no permitido.")
        if not is_same_origin(self.headers.get("Origin", ""), host):
            self.close_connection = True
            return self._reject("Origen no permitido.")
        path = urllib.parse.urlparse(self.path).path
        try:
            form = self._read_form()
        except CuerpoNoValido as exc:
            log.warning("POST %s rechazado (%s): %s", path, exc.status, exc)
            self.close_connection = True          # el cuerpo no se leyó: no se reutiliza la conexión
            return self._aviso("No se pudo completar", str(exc), exc.status)
        if not secrets.compare_digest(field(form, "csrf_token"), CSRF_TOKEN):
            return self._reject("Token de seguridad inválido. Recarga la página e inténtalo de nuevo.",
                                field(form, "thread_id")[:200])
        action = POST_ROUTES.get(path)
        if action is None:
            return self._aviso("No encontrado", "Página no encontrada.", 404)
        hilo = field(form, "thread_id") if path in pendientes.RUTAS_CON_TEXTO else ""
        if hilo:
            pendientes.guardar(hilo, path, form)         # si algo falla, la persona recupera lo que escribió (U-31)
        try:
            with request_scope():
                location = action(form)
            hubo_fallo = pendientes.hubo_fallo()
            if hilo and not hubo_fallo:
                pendientes.limpiar(hilo)
            self._redirect(_senalar_fallo(location) if hubo_fallo else location)
        except ValueError as exc:
            self._rechazada("POST", path, exc, field(form, "thread_id")[:200])
        except Exception as exc:
            self._error_inesperado("POST", path, exc, field(form, "thread_id")[:200])

    def _rechazada(self, metodo: str, ruta: str, exc: ValueError, thread_id: str = "") -> None:
        """Una petición que la aplicación rechaza con su motivo (caso inexistente, dato no válido) no es un fallo del
        servidor: se responde 400 con el mensaje y se anota como aviso, con su traza por si ocultara un error propio.

        Solo se muestra el texto si Anka lo escribió para la persona; el de Python o de una librería se cambia por uno
        genérico (D-71)."""
        log.warning("%s %s rechazada: %s", metodo, ruta, exc, exc_info=True)
        texto = str(exc) if es_mensaje_para_la_persona(exc) else "Los datos enviados no son válidos."
        self._aviso("No se pudo completar", texto, 400, thread_id)

    def _redirect(self, location: str) -> None:
        self.send_response(303)
        for name, value in cabeceras_de_seguridad().items():
            self.send_header(name, value)
        self.send_header("Location", location)
        self.send_header("Content-Length", "0")
        self.end_headers()

    def log_message(self, format: str, *args: Any) -> None:
        """Silencia el registro por defecto de http.server (usamos el de la aplicación)."""
        return


def review_ui(host: str, port: int, open_browser: bool = True) -> None:
    """Arranca el servidor del dashboard en el host y puerto dados, hasta Ctrl+C."""
    url = f"http://{host}:{port}"
    print(f"Bandeja de revisión comercial: {url}")
    print("Deja esta ventana abierta. Presiona Ctrl+C para cerrar.")
    if open_browser:
        webbrowser.open(url)
    server = ThreadingHTTPServer((host, port), ReviewHandler)
    server.daemon_threads = True
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nBandeja cerrada.")
    finally:
        server.server_close()
