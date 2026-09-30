"""Dashboard local: páginas HTML y servidor HTTP con protección CSRF."""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterable
from typing import Any
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import os
import re
import secrets
import urllib.error
import urllib.parse
import urllib.request
import webbrowser
from datetime import datetime
from markupsafe import Markup
from activities import build_workbook, parse_activities_form
from dashboard_views import (
    _attachments_table, _metric_rows,
    _reception_assessment_panel, activities_editor, render_page,
)
from status import (
    ACTION_STATUSES, CLOSED_STATUSES, QuoteStatus,
)
from mail import (
    valid_email_address,
)
from reception import (
    reception_assessment, suggested_reply, unresolved_scope_issues,
)
from modes import (
    set_mode,
)
from internal_context import internal_commercial_context
from settings import load_config
from templating import render

from case_actions import approve_review, resolve_case_recipient, save_dashboard_draft, send_dashboard_reply
from pipeline import checked_run, describe_check, sync_status_text
from quotes import CLIENT_TYPES, SECTORS, STATUS_LABELS, all_quotes, get_quote, mark_notifications_read, save_activities, save_quote_metadata, unread_notifications


def _page(title: str, body: Markup, message: str = "") -> str:
    try:
        mode = load_config().get("active_mode")
    except (SystemExit, OSError, ValueError):
        mode = None
    return render_page(title, body, message, len(unread_notifications(100)), mode,
                       bool(os.getenv("OPENAI_API_KEY")))


def _dashboard_insights(quotes: list[dict], services: Counter, clients: Counter) -> Markup:
    items = []
    if services:
        service, count = services.most_common(1)[0]
        items.append(("Servicio más solicitado", f"{service} aparece en {count} solicitud(es)."))
    if clients:
        client, count = clients.most_common(1)[0]
        detail = "Es el cliente con más solicitudes registradas." if count > 1 else "Aún no hay clientes recurrentes."
        items.append((client, f"{count} solicitud(es). {detail}"))
    unclassified = sum(q["client_type"] == "No clasificado" for q in quotes)
    if unclassified:
        items.append(("Datos por completar", f"{unclassified} solicitud(es) aún no tienen tipo de cliente."))
    return render("insights.html", items=items)


def _notifications_panel(notifications: list[dict]) -> Markup:
    if not notifications:
        return Markup("")
    items = [
        {
            "title": item["title"], "project_name": item["project_name"],
            "message": item["message"], "thread_id": item["thread_id"],
            "timestamp": item["created_at"][:16].replace("T", " ") if item["created_at"] else "",
        }
        for item in notifications
    ]
    return render("notifications_panel.html", items=items)


def _render_index(message: str = "") -> str:
    quotes = all_quotes()
    notifications = unread_notifications(8)
    unread_threads = {item["thread_id"] for item in notifications}
    services = Counter(service for quote in quotes for service in quote["services"])
    clients = Counter(q["client_name"] for q in quotes if q["client_name"])
    recent_action = [q for q in quotes if q["status"] in ACTION_STATUSES][:5]
    body = render(
        "index.html",
        sync_text=sync_status_text(),
        notifications=_notifications_panel(notifications),
        total=len(quotes),
        unique_clients=len({q["client_email"].casefold() for q in quotes if q["client_email"]}),
        action_count=sum(q["status"] in ACTION_STATUSES for q in quotes),
        completed=sum(q["status"] in {QuoteStatus.READY, QuoteStatus.READY_REVIEW} for q in quotes),
        services=_metric_rows(services),
        statuses=_metric_rows(Counter(q["status_label"] for q in quotes), "blue"),
        client_types=_metric_rows(Counter(q["client_type"] for q in quotes)),
        insights=_dashboard_insights(quotes, services, clients),
        recent=[_quote_row(q, q["thread_id"] in unread_threads) for q in recent_action],
    )
    return _page("Resumen comercial", body, message)


def _quote_row(quote: dict, is_new: bool = False) -> Markup:
    project = quote["analysis"].get("project_name") or "Solicitud sin nombre"
    services = ", ".join(quote["services"]) or "Servicio por clasificar"
    return render(
        "quote_row.html",
        project=project, services=services, is_new=is_new,
        search=(project + " " + quote["client_name"] + " " + services).casefold(),
        status=quote["status"], client_type=quote["client_type"],
        client_name=quote["client_name"], sector=quote["sector"],
        badge_class="warn" if quote["status"] in ACTION_STATUSES else "info",
        status_label=quote["status_label"],
        date=(quote["updated_at"] or quote["first_seen_at"] or "Sin fecha")[:10],
        thread_id=quote["thread_id"],
    )


def _render_requests(message: str = "") -> str:
    quotes = all_quotes()
    unread_threads = {item["thread_id"] for item in unread_notifications(100)}
    body = render(
        "requests.html",
        rows=[_quote_row(quote, quote["thread_id"] in unread_threads) for quote in quotes],
        status_options=list(STATUS_LABELS.items()),
        type_options=CLIENT_TYPES,
    )
    return _page("Solicitudes", body, message)


def _internal_context_panel(client_name: str) -> Markup:
    context = internal_commercial_context(client_name)
    if not context:
        return Markup("<p class='meta'>El índice comercial interno todavía no está disponible.</p>")

    def top(items: Iterable[tuple[Any, Any]], limit: int = 5) -> list[tuple[str, int]]:
        return [(str(label), int(count)) for label, count in list(items)[:limit]]

    profile = context.get("profile")
    profile_ctx = None
    if profile:
        profile_ctx = {
            "client_name": profile.get("client_name", "Cliente"),
            "company_type": profile.get("company_type", "No clasificado"),
            "confidence": profile.get("company_type_confidence", "baja"),
            "documents": int(profile.get("commercial_document_count", 0)),
            "project_types": top(list(profile.get("project_types", {}).items())[:5]),
            "sectors": top(list(profile.get("sectors", {}).items())[:4]),
            "usual_requests": top(list(profile.get("usual_requests", {}).items())[:6]),
        }
    return render(
        "internal_context.html",
        profile=profile_ctx,
        general={
            "profiles": int(context.get("client_profiles", 0)),
            "project_types": top(context.get("project_types", [])),
            "sectors": top(context.get("sectors", [])),
            "usual_requests": top(context.get("usual_requests", [])),
        },
        generated_at=context.get("generated_at", ""),
    )


def _render_case(thread_id: str, message: str = "") -> str:
    review = get_quote(thread_id)
    if not review:
        return _page("Caso no encontrado", render("empty.html", text="La solicitud no existe."), message)
    recipient_error = ""
    if not valid_email_address(review["recipient"]):
        try:
            review = resolve_case_recipient(thread_id)["quote"]
        except Exception:
            recipient_error = (
                "No se pudo confirmar el destinatario en Gmail. "
                "El guardado y el envío están bloqueados para evitar un correo incorrecto."
            )
    analysis = review["analysis"]
    assessment = review.get("reception_assessment") or reception_assessment(analysis)
    config = load_config()
    routing = analysis.get("routing", {})
    routing_summary = "Correo directo"
    if routing.get("is_forwarded"):
        routing_summary = (
            f"Reenvío interno: {routing.get('internal_forwarder') or 'no indicado'}\n"
            f"Cliente original: {routing.get('original_sender') or 'no identificado'}\n"
            f"Destinatario seleccionado: {routing.get('selected_recipient') or 'no indicado'}"
        )
    scope_issues = unresolved_scope_issues(analysis)
    issues_summary = "\n".join(f"- {issue}" for issue in scope_issues) or "Sin asuntos pendientes."
    technical = assessment.get("status") == "technical_review"
    email_allowed = (
        valid_email_address(review["recipient"])
        and review["status"] not in CLOSED_STATUSES
        and not technical
    )
    email_body = ""
    if email_allowed:
        email = "form"
        email_body = review["draft_body"] or suggested_reply(analysis, config["required_fields"])
    elif review["status"] == QuoteStatus.RESPONSE_SENT:
        email = "sent"
    elif recipient_error:
        email = "recipient_error"
    elif technical:
        email = "technical"
    else:
        email = "unavailable"
    sent_complete = review["status"] == QuoteStatus.RESPONSE_SENT
    body = render(
        "case.html",
        review=review, thread_id=thread_id,
        project_name=analysis.get("project_name") or "Solicitud sin nombre",
        summary=analysis.get("summary") or "Sin resumen disponible",
        service_family=assessment.get("service_family", "Por clasificar"),
        suggestion_complete=review["status"] not in {QuoteStatus.IGNORED, QuoteStatus.MISSING},
        draft_complete=bool(review["draft_id"] or sent_complete),
        sent_complete=sent_complete,
        assessment_panel=_reception_assessment_panel(assessment),
        client_types=CLIENT_TYPES, sectors=SECTORS,
        internal_context=_internal_context_panel(review["client_name"]),
        activities_editor=activities_editor(thread_id, analysis.get("activities", [])),
        attachments_table=_attachments_table(analysis.get("attachments", [])),
        routing_summary=routing_summary, issues_summary=issues_summary,
        email=email, email_body=email_body, recipient_error=recipient_error,
        sufficient=assessment.get("status") == "sufficient_for_internal_review",
    )
    return _page("Revisar caso", body, message)


CSRF_TOKEN = secrets.token_urlsafe(32)


POST_FORM_PATTERN = re.compile(r'(<form\b[^>]*\bmethod="post"[^>]*>)', re.IGNORECASE)


LOCAL_HOSTS = {"127.0.0.1", "localhost", "[::1]"}


def add_csrf_tokens(content: str) -> str:
    field = f'<input type="hidden" name="csrf_token" value="{CSRF_TOKEN}">'
    return POST_FORM_PATTERN.sub(lambda m: m.group(1) + field, content)


def is_local_host_header(value: str) -> bool:
    """Acepta solo Host locales; bloquea DNS rebinding."""
    value = (value or "").lower()
    hostname = value.rsplit(":", 1)[0] if value.count(":") == 1 or value.startswith("[") and "]:" in value else value
    return hostname in LOCAL_HOSTS


def is_same_origin(origin: str, host: str) -> bool:
    if not origin:
        return True  # navegadores omiten Origin en algunas navegaciones; el token cubre ese caso
    return urllib.parse.urlparse(origin).netloc.lower() == (host or "").lower()


class ReviewHandler(BaseHTTPRequestHandler):
    def _send_html(self, content: str, status: int = 200) -> None:
        data = add_csrf_tokens(content).encode("utf-8")
        self.send_response(status)
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Content-Security-Policy", "frame-ancestors 'none'")
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _read_form(self) -> dict[str, list[str]]:
        length = int(self.headers.get("Content-Length", "0"))
        data = self.rfile.read(length).decode("utf-8")
        return urllib.parse.parse_qs(data)

    def _reject(self, reason: str) -> None:
        self._send_html(_page("Solicitud rechazada", render("empty.html", text=reason)), 403)

    def do_GET(self) -> None:
        if not is_local_host_header(self.headers.get("Host", "")):
            return self._reject("Host no permitido.")
        parsed = urllib.parse.urlparse(self.path)
        query = urllib.parse.parse_qs(parsed.query)
        if parsed.path == "/":
            self._send_html(_render_index(query.get("message", [""])[0]))
        elif parsed.path == "/export.xlsx":
            thread_id = query.get("thread_id", [""])[0]
            quotes = [get_quote(thread_id)] if thread_id else all_quotes()
            quotes = [item for item in quotes if item]
            if thread_id and not quotes:
                return self._send_html(_page("No encontrado", render("empty.html", text="La solicitud no existe.")), 404)
            data = build_workbook(quotes)
            stamp = datetime.now().strftime("%Y%m%d-%H%M")
            name = f"actividades-{thread_id[:8] or 'todas'}-{stamp}.xlsx"
            self.send_response(200)
            self.send_header("Content-Type", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
            self.send_header("Content-Disposition", f'attachment; filename="{name}"')
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
        elif parsed.path == "/requests":
            self._send_html(_render_requests(query.get("message", [""])[0]))
        elif parsed.path == "/case":
            self._send_html(_render_case(
                query.get("thread_id", [""])[0], query.get("message", [""])[0]
            ))
        else:
            self._send_html(_page("No encontrado", render("empty.html", text="Página no encontrada.")), 404)

    def do_POST(self) -> None:
        host = self.headers.get("Host", "")
        if not is_local_host_header(host):
            return self._reject("Host no permitido.")
        if not is_same_origin(self.headers.get("Origin", ""), host):
            return self._reject("Origen no permitido.")
        parsed = urllib.parse.urlparse(self.path)
        form = self._read_form()
        token = form.get("csrf_token", [""])[0]
        if not secrets.compare_digest(token, CSRF_TOKEN):
            return self._reject("Token de seguridad inválido. Recarga la página e inténtalo de nuevo.")
        try:
            if parsed.path == "/refresh":
                if load_config().get("active_mode") == "chatgpt_work":
                    message = (
                        "Este botón no lee Gmail en modo ChatGPT Work. " + sync_status_text()
                    )
                else:
                    message = describe_check(checked_run(True, True, False, "dashboard"))
                self._redirect("/?message=" + urllib.parse.quote(message))
            elif parsed.path == "/approve":
                thread_id = form.get("thread_id", [""])[0]
                body = form.get("body", [None])[0]
                message = approve_review(thread_id, body_text=body)
                self._redirect("/?message=" + urllib.parse.quote(message))
            elif parsed.path == "/metadata":
                thread_id = form.get("thread_id", [""])[0]
                save_quote_metadata(
                    thread_id,
                    form.get("client_name", [""])[0],
                    form.get("client_type", ["No clasificado"])[0],
                    form.get("sector", ["No clasificado"])[0],
                    form.get("notes", [""])[0],
                )
                message = "Información comercial guardada."
                self._redirect(
                    "/case?thread_id=" + urllib.parse.quote(thread_id)
                    + "&message=" + urllib.parse.quote(message)
                )
            elif parsed.path == "/draft":
                thread_id = form.get("thread_id", [""])[0]
                body = form.get("body", [""])[0]
                message = save_dashboard_draft(thread_id, body)
                self._redirect(
                    "/case?thread_id=" + urllib.parse.quote(thread_id)
                    + "&message=" + urllib.parse.quote(message)
                )
            elif parsed.path == "/send":
                thread_id = form.get("thread_id", [""])[0]
                body = form.get("body", [""])[0]
                message = send_dashboard_reply(thread_id, body)
                self._redirect(
                    "/case?thread_id=" + urllib.parse.quote(thread_id)
                    + "&message=" + urllib.parse.quote(message)
                )
            elif parsed.path == "/activities":
                thread_id = form.get("thread_id", [""])[0]
                message = save_activities(thread_id, parse_activities_form(form))
                self._redirect(
                    "/case?thread_id=" + urllib.parse.quote(thread_id)
                    + "&message=" + urllib.parse.quote(message)
                )
            elif parsed.path == "/mode":
                message = set_mode(form.get("mode", [""])[0])
                self._redirect("/?message=" + urllib.parse.quote(message))
            elif parsed.path == "/notifications/read":
                mark_notifications_read()
                self._redirect("/?message=" + urllib.parse.quote("Novedades marcadas como revisadas."))
            else:
                self._send_html(_page("No encontrado", render("empty.html", text="Página no encontrada.")), 404)
        except (Exception, SystemExit) as exc:
            self._send_html(_page("Error", render("empty.html", text=str(exc))), 500)

    def _redirect(self, location: str) -> None:
        self.send_response(303)
        self.send_header("Location", location)
        self.end_headers()

    def log_message(self, format: str, *args: Any) -> None:
        return


def review_ui(host: str, port: int, open_browser: bool = True) -> None:
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
