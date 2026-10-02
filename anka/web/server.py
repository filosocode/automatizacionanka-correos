"""Servidor HTTP local del dashboard: enruta las páginas (GET) y las acciones de los formularios (POST)."""

from __future__ import annotations

from collections.abc import Callable
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
from anka.dominio.activities import build_workbook, parse_activities_form
from anka.infra.modes import (
    set_mode,
)
from anka.infra.logging_setup import get_logger
from anka.infra.store import request_scope
from anka.infra.settings import load_config
from anka.web.templating import render

from anka.servicios.case_actions import approve_review, delete_cases, save_dashboard_draft, send_dashboard_reply
from anka.servicios.pipeline import checked_run, describe_check, sync_status_text
from anka.infra.work_heartbeat import bridge_state
from anka.servicios import followup_reminders as fup
from anka.servicios.change_control import (
    assign_code, promote_to_te, resolve_change,
)
from anka.servicios.quotes import (
    all_quotes, archived_threads, change_marker, get_quote, mark_notifications_read, rescue_discarded,
    resolve_link, resolve_model_proposal, resolve_technical_review, save_quote_metadata,
)
from anka.servicios.ediciones import save_activities

from anka.web import openapi, paginas
from anka.web.api import a_json, resumen_de_caso
from anka.web import pantalla_caso
from anka.web.seguridad import (
    CSRF_TOKEN, add_csrf_tokens, is_local_host_header, is_same_origin,
)

log = get_logger(__name__)


# --- Acciones de los formularios (POST). Cada una recibe el formulario y devuelve a dónde redirigir.
# Un ValueError (o un ErrorDeAnka) es orientación para la persona: se muestra
# como mensaje, no como fallo del servidor.

Form = dict[str, list[str]]


def _field(form: Form, name: str, default: str = "") -> str:
    return form.get(name, [default])[0]


def _int_field(form: Form, name: str) -> int:
    return int(_field(form, name, "0") or 0)


def _to_case(thread_id: str, message: str, suffix: str = "") -> str:
    return ("/case?thread_id=" + urllib.parse.quote(thread_id)
            + "&message=" + urllib.parse.quote(message) + suffix)


def _to(page: str, message: str) -> str:
    return page + "?message=" + urllib.parse.quote(message)


def _guidance(action: Callable[[], str], *errors: type[BaseException]) -> str:
    """Ejecuta la acción; si falla por algo que se le explica a la persona, devuelve ese texto como mensaje."""
    try:
        return action()
    except errors as exc:
        return str(exc)


def _post_refresh(form: Form) -> str:
    if load_config().get("active_mode") == "chatgpt_work":
        return _to("/", "Este botón no lee Gmail en modo ChatGPT Work. " + sync_status_text())
    return _to("/", describe_check(checked_run(True, True, False, "dashboard")))


def _post_approve(form: Form) -> str:
    thread_id = _field(form, "thread_id")
    message = _guidance(lambda: approve_review(
        thread_id, body_text=form.get("body", [None])[0],
        client_name=form.get("client_name", [None])[0],
        new_client=_field(form, "new_client") == "1",
        allow_similar_project=_field(form, "allow_similar") == "1",
    ), ErrorDeAnka)
    return _to_case(thread_id, message)


def _post_metadata(form: Form) -> str:
    thread_id = _field(form, "thread_id")
    save_quote_metadata(
        thread_id, _field(form, "client_name"), _field(form, "client_type", "No clasificado"),
        _field(form, "sector", "No clasificado"), _field(form, "notes"))
    return _to_case(thread_id, "Información comercial guardada.")


def _post_draft(form: Form) -> str:
    thread_id = _field(form, "thread_id")
    message = _guidance(lambda: save_dashboard_draft(
        thread_id, _field(form, "body"), _field(form, "to")), ValueError)
    return _to_case(thread_id, message)


def _post_send(form: Form) -> str:
    thread_id = _field(form, "thread_id")
    # Un ValueError aquí suele ser «el mismo correo ya salió»: se explica, no es un fallo.
    message = _guidance(lambda: send_dashboard_reply(
        thread_id, _field(form, "body"), _field(form, "to"),
        _int_field(form, "change_id"), _field(form, "purpose")), ValueError)
    return _to_case(thread_id, message)


def _post_activities(form: Form) -> str:
    thread_id = _field(form, "thread_id")
    return _to_case(thread_id, save_activities(thread_id, parse_activities_form(form), _field(form, "reason")))


def _post_mode(form: Form) -> str:
    return _to("/", set_mode(_field(form, "mode")))


def _post_rescue(form: Form) -> str:
    return _to("/discarded", rescue_discarded(_field(form, "thread_id")))


def _post_review_decision(form: Form) -> str:
    thread_id = _field(form, "thread_id")
    message = _guidance(lambda: resolve_technical_review(
        thread_id, _field(form, "decision"), _field(form, "questions")), ValueError)
    return _to_case(thread_id, message)


def _post_followup(form: Form) -> str:
    thread_id = _field(form, "thread_id")
    message = _guidance(lambda: fup.set_followup(thread_id, _field(form, "action")), ValueError)
    return _to_case(thread_id, message, "#seguimiento")


def _post_code(form: Form) -> str:
    thread_id = _field(form, "thread_id")
    message = _guidance(lambda: assign_code(thread_id, _field(form, "number")), ValueError)
    return _to_case(thread_id, message, "&template=received#respuesta")


def _post_code_approve(form: Form) -> str:
    thread_id = _field(form, "thread_id")
    return _to_case(thread_id, _guidance(lambda: promote_to_te(thread_id), ValueError))


def _post_change(form: Form) -> str:
    thread_id = _field(form, "thread_id")
    message = _guidance(lambda: resolve_change(
        thread_id, _int_field(form, "change_id"), _field(form, "decision") == "yes"), ValueError)
    return _to_case(thread_id, message)


def _post_link(form: Form) -> str:
    thread_id = _field(form, "thread_id")
    message = _guidance(lambda: resolve_link(
        thread_id, int(_field(form, "link_id", "0")), _field(form, "decision") == "yes"), ValueError)
    return _to_case(thread_id, message)


def _post_proposal(form: Form) -> str:
    thread_id = _field(form, "thread_id")
    message = _guidance(lambda: resolve_model_proposal(
        thread_id, _field(form, "decision") == "use"), ValueError)
    return _to_case(thread_id, message)


def _post_delete(form: Form) -> str:
    return _to("/requests", delete_cases(form.get("thread_id", [])))


def _post_notifications_read(form: Form) -> str:
    mark_notifications_read()
    return _to("/", "Novedades marcadas como revisadas.")


POST_ROUTES: dict[str, Callable[[Form], str]] = {
    "/refresh": _post_refresh,
    "/approve": _post_approve,
    "/metadata": _post_metadata,
    "/draft": _post_draft,
    "/send": _post_send,
    "/activities": _post_activities,
    "/mode": _post_mode,
    "/rescue": _post_rescue,
    "/review-decision": _post_review_decision,
    "/followup": _post_followup,
    "/code": _post_code,
    "/code-approve": _post_code_approve,
    "/change": _post_change,
    "/link": _post_link,
    "/proposal": _post_proposal,
    "/delete": _post_delete,
    "/notifications/read": _post_notifications_read,
}


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
        self._send_html(paginas.render_index(_field(query, "message")))

    def _get_requests(self, query: dict[str, list[str]]) -> None:
        self._send_html(paginas.render_requests(_field(query, "message")))

    def _get_discarded(self, query: dict[str, list[str]]) -> None:
        self._send_html(paginas.render_discarded(_field(query, "message")))

    def _get_case(self, query: dict[str, list[str]]) -> None:
        self._send_html(pantalla_caso.render_case(
            _field(query, "thread_id"), _field(query, "message"), _field(query, "template"),
            _int_field(query, "change_id"), _field(query, "to"),
        ))

    def _get_acta(self, query: dict[str, list[str]]) -> None:
        self._send_html(pantalla_caso.render_acta(_field(query, "thread_id")))

    def _get_archive(self, query: dict[str, list[str]]) -> None:
        self._send_html(paginas.page("Archivo", render("archive.html", items=archived_threads())))

    def _get_export(self, query: dict[str, list[str]]) -> None:
        thread_id = _field(query, "thread_id")
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
        self._send_json({"v": change_marker(), "bridge": state, "text": text})

    def _get_api_cases(self, query: dict[str, list[str]]) -> None:
        include_discarded = _field(query, "descartados").lower() in ("1", "true", "si", "sí")
        self._send_json([resumen_de_caso(caso) for caso in all_quotes(include_discarded)])

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
        if not secrets.compare_digest(_field(form, "csrf_token"), CSRF_TOKEN):
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
