"""Servidor HTTP local del dashboard: enruta las páginas (GET) y las acciones de los formularios (POST)."""

from __future__ import annotations

from typing import Any
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import re
import secrets
import urllib.error
import urllib.parse
import urllib.request
import webbrowser
from datetime import datetime
from anka.dominio.errores import ErrorDeAnka
from anka.dominio.activities import build_workbook
from anka.infra.logging_setup import get_logger
from anka.infra.store import request_scope
from anka.infra.settings import load_config
from anka.web.templating import render

from anka.servicios import salud
from anka.servicios.autorizacion import estado_de_google
from anka.infra.work_heartbeat import bridge_state
from anka.servicios.quotes import (
    all_quotes, archived_threads, change_marker, get_quote,
)

from anka.web import openapi, paginas
from anka.web.acciones import POST_ROUTES, field, int_field
from anka.web.api import a_json, resumen_de_caso
from anka.web import pantalla_caso
from anka.web.seguridad import (
    CSRF_TOKEN, add_csrf_tokens, is_local_host_header, is_same_origin,
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
    "/api/casos": "_get_api_cases",
    "/api/salud": "_get_api_health",
    "/api/autorizacion": "_get_api_authorization",
    "/docs": "_get_docs",
    "/openapi.json": "_get_openapi",
}
GET_PATTERNS: dict[str, str] = {"/api/casos/{thread_id}": "_get_api_case"}
_GET_PATTERNS = [(re.compile(re.sub(r"\{\w+\}", "([^/]+)", template)), name) for template, name in GET_PATTERNS.items()]


class ReviewHandler(BaseHTTPRequestHandler):
    """Servidor HTTP local del dashboard: páginas por GET y acciones por POST con token."""
    def _send_bytes(self, status: int, content_type: str, data: bytes,
                    extra_headers: dict[str, str] | None = None) -> None:
        self.send_response(status)
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Content-Security-Policy", "frame-ancestors 'none'")
        self.send_header("Content-Type", content_type)
        self.send_header("Cache-Control", "no-store")
        for name, value in (extra_headers or {}).items():
            self.send_header(name, value)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _send_html(self, content: str, status: int = 200) -> None:
        self._send_bytes(status, "text/html; charset=utf-8", add_csrf_tokens(content).encode("utf-8"))

    def _send_json(self, value: Any, status: int = 200) -> None:
        self._send_bytes(status, "application/json; charset=utf-8", a_json(value))

    def _read_form(self) -> dict[str, list[str]]:
        length = int(self.headers.get("Content-Length", "0"))
        data = self.rfile.read(length).decode("utf-8")
        return urllib.parse.parse_qs(data)

    def _reject(self, reason: str) -> None:
        origin = getattr(self, "client_address", ("?",))[0]
        log.warning("Solicitud rechazada (%s) desde %s", reason, origin)
        self._send_html(paginas.page("Solicitud rechazada", render("empty.html", text=reason)), 403)

    def do_GET(self) -> None:
        """Atiende las páginas del dashboard; un error se registra y se muestra sin romper el servidor."""
        try:
            with request_scope():   # una sola conexión a la base para toda la petición
                self._get()
        except Exception as exc:
            log.exception("Error atendiendo GET %s", self.path.split("?")[0])
            self._send_html(paginas.page("Error", render("empty.html", text=str(exc))), 500)

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
            return self._send_html(
                paginas.page("No encontrado", render("empty.html", text="Página no encontrada.")), 404)
        getattr(self, method)(query, *arguments)

    # --- páginas (GET)
    def _get_index(self, query: dict[str, list[str]]) -> None:
        self._send_html(paginas.render_index(field(query, "message")))

    def _get_requests(self, query: dict[str, list[str]]) -> None:
        self._send_html(paginas.render_requests(field(query, "message")))

    def _get_discarded(self, query: dict[str, list[str]]) -> None:
        self._send_html(paginas.render_discarded(field(query, "message")))

    def _get_case(self, query: dict[str, list[str]]) -> None:
        self._send_html(pantalla_caso.render_case(
            field(query, "thread_id"), field(query, "message"), field(query, "template"),
            int_field(query, "change_id"), field(query, "to"),
        ))

    def _get_acta(self, query: dict[str, list[str]]) -> None:
        self._send_html(pantalla_caso.render_acta(field(query, "thread_id")))

    def _get_archive(self, query: dict[str, list[str]]) -> None:
        self._send_html(paginas.page("Archivo", render("archive.html", items=archived_threads())))

    def _get_export(self, query: dict[str, list[str]]) -> None:
        thread_id = field(query, "thread_id")
        quotes = [get_quote(thread_id)] if thread_id else all_quotes()
        quotes = [item for item in quotes if item]
        if thread_id and not quotes:
            return self._send_html(
                paginas.page("No encontrado", render("empty.html", text="La solicitud no existe.")), 404)
        stamp = datetime.now().strftime("%Y%m%d-%H%M")
        name = f"actividades-{thread_id[:8] or 'todas'}-{stamp}.xlsx"
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
        self._send_json({"v": change_marker(), "bridge": state, "text": text,
                         "google": {"estado": google.estado, "texto": google.texto}})

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
        self._send_bytes(200, "text/html; charset=utf-8", openapi.docs_page().encode("utf-8"))

    def _get_openapi(self, query: dict[str, list[str]]) -> None:
        self._send_json(openapi.build_spec(CSRF_TOKEN))

    def do_POST(self) -> None:
        """Valida host, origen y token y ejecuta la acción de `POST_ROUTES`; redirige al resultado."""
        host = self.headers.get("Host", "")
        if not is_local_host_header(host):
            return self._reject("Host no permitido.")
        if not is_same_origin(self.headers.get("Origin", ""), host):
            return self._reject("Origen no permitido.")
        path = urllib.parse.urlparse(self.path).path
        form = self._read_form()
        if not secrets.compare_digest(field(form, "csrf_token"), CSRF_TOKEN):
            return self._reject("Token de seguridad inválido. Recarga la página e inténtalo de nuevo.")
        action = POST_ROUTES.get(path)
        if action is None:
            return self._send_html(
                paginas.page("No encontrado", render("empty.html", text="Página no encontrada.")), 404)
        try:
            with request_scope():
                location = action(form)
            self._redirect(location)
        except Exception as exc:
            log.exception("Error atendiendo POST %s", path)
            self._send_html(paginas.page("Error", render("empty.html", text=str(exc))), 500)

    def _redirect(self, location: str) -> None:
        self.send_response(303)
        self.send_header("Location", location)
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
