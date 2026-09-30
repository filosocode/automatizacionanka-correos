"""Dashboard local: páginas HTML y servidor HTTP con protección CSRF."""

from __future__ import annotations

from collections import Counter
import html
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import os
import re
import secrets
import urllib.error
import urllib.parse
import urllib.request
import webbrowser
from datetime import datetime
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

from case_actions import approve_review, resolve_case_recipient, save_dashboard_draft, send_dashboard_reply
from pipeline import checked_run, describe_check, sync_status_text
from quotes import CLIENT_TYPES, SECTORS, STATUS_LABELS, all_quotes, get_quote, mark_notifications_read, save_activities, save_quote_metadata, unread_notifications


def _page(title: str, body: str, message: str = "") -> str:
    try:
        mode = load_config().get("active_mode")
    except (SystemExit, OSError, ValueError):
        mode = None
    return render_page(title, body, message, len(unread_notifications(100)), mode,
                       bool(os.getenv("OPENAI_API_KEY")))


def _review_summary(review: dict) -> str:
    analysis = review["analysis"]
    summary = analysis.get("summary") or "Sin resumen disponible"
    project = analysis.get("project_name") or "Sin nombre de proyecto"
    return f"""
<div class="case">
  <span class="badge">{html.escape(review['status_label'])}</span>
  <h2>{html.escape(project)}</h2>
  <div>{html.escape(summary)}</div>
  <div class="meta">Cliente: {html.escape(review['recipient'])}</div>
  <div class="meta">Hilo: {html.escape(review['thread_id'])}</div>
  <a class="button" href="/case?thread_id={urllib.parse.quote(review['thread_id'])}">
    Revisar caso
  </a>
</div>"""


def _dashboard_insights(quotes: list[dict], services: Counter, clients: Counter) -> str:
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
    if not items:
        return "<p class='meta'>Las conclusiones aparecerán cuando ingresen solicitudes.</p>"
    return "".join(
        f"<div class='insight'><strong>{html.escape(title)}</strong>"
        f"<span class='meta'>{html.escape(detail)}</span></div>"
        for title, detail in items
    )


def _notifications_panel(notifications: list[dict]) -> str:
    if not notifications:
        return ""
    rows = []
    for item in notifications:
        timestamp = item["created_at"][:16].replace("T", " ") if item["created_at"] else ""
        rows.append(f"""
<article class="notification">
  <span class="notification-dot"></span>
  <div><strong>{html.escape(item['title'])}</strong>
    <div>{html.escape(item['project_name'])}</div>
    <div class="meta">{html.escape(item['message'])} {html.escape(timestamp)}</div>
  </div>
  <a class="button secondary" href="/case?thread_id={urllib.parse.quote(item['thread_id'])}">Revisar</a>
</article>""")
    return f"""
<section class="notifications">
  <div class="section-head"><div><h2>Novedades</h2><div class="meta">Correos y respuestas que requieren tu atención.</div></div>
    <form method="post" action="/notifications/read"><button class="secondary" type="submit">Marcar como revisadas</button></form>
  </div>
  {''.join(rows)}
</section>"""


def _render_index(message: str = "") -> str:
    quotes = all_quotes()
    notifications = unread_notifications(8)
    unread_threads = {item["thread_id"] for item in notifications}
    sync_text = sync_status_text()
    total = len(quotes)
    unique_clients = len({q["client_email"].casefold() for q in quotes if q["client_email"]})
    action_count = sum(q["status"] in ACTION_STATUSES for q in quotes)
    completed = sum(q["status"] in {QuoteStatus.READY, QuoteStatus.READY_REVIEW} for q in quotes)
    services = Counter(service for quote in quotes for service in quote["services"])
    statuses = Counter(q["status_label"] for q in quotes)
    clients = Counter(q["client_name"] for q in quotes if q["client_name"])
    client_types = Counter(q["client_type"] for q in quotes)
    recent_action = [q for q in quotes if q["status"] in ACTION_STATUSES][:5]
    recent_html = "".join(
        _quote_row(q, q["thread_id"] in unread_threads) for q in recent_action
    )
    if not recent_html:
        recent_html = "<div class='empty'>No hay solicitudes que requieran atención.</div>"
    body = f"""
<div class="page-head">
  <div><h1>Resumen comercial</h1><p>Qué está llegando, quién lo solicita y dónde hace falta actuar. {html.escape(sync_text)}</p></div>
  <a class="button secondary" href="/requests">Ver todas las solicitudes</a>
</div>
{_notifications_panel(notifications)}
<div class="grid kpis">
  <div class="kpi"><div class="kpi-label">Solicitudes registradas</div><div class="kpi-value">{total}</div><div class="kpi-note">Excluye correos descartados</div></div>
  <div class="kpi"><div class="kpi-label">Clientes identificados</div><div class="kpi-value">{unique_clients}</div><div class="kpi-note">Por correo de cliente</div></div>
  <div class="kpi"><div class="kpi-label">Requieren atención</div><div class="kpi-value">{action_count}</div><div class="kpi-note">Faltantes o revisión técnica</div></div>
  <div class="kpi"><div class="kpi-label">Información suficiente</div><div class="kpi-value">{completed}</div><div class="kpi-note">Listas para revisión interna</div></div>
</div>
<div class="grid dashboard-grid">
  <section class="section"><div class="section-head"><h2>Servicios solicitados</h2><span class="meta">Top 6</span></div>{_metric_rows(services)}</section>
  <section class="section"><div class="section-head"><h2>Estado de solicitudes</h2><span class="meta">Actual</span></div>{_metric_rows(statuses, 'blue')}</section>
  <section class="section"><div class="section-head"><h2>Tipos de cliente</h2><span class="meta">Clasificación comercial</span></div>{_metric_rows(client_types)}</section>
  <section class="section"><div class="section-head"><h2>Lectura rápida</h2></div>{_dashboard_insights(quotes, services, clients)}</section>
</div>
<section style="margin-top:24px">
  <div class="section-head" style="margin-bottom:12px"><h2>Requieren atención</h2><a href="/requests" class="meta">Ver todo</a></div>
  <div class="grid">{recent_html}</div>
</section>"""
    return _page("Resumen comercial", body, message)


def _quote_row(quote: dict, is_new: bool = False) -> str:
    analysis = quote["analysis"]
    project = analysis.get("project_name") or "Solicitud sin nombre"
    services = ", ".join(quote["services"]) or "Servicio por clasificar"
    badge_class = "warn" if quote["status"] in ACTION_STATUSES else "info"
    new_badge = " <span class='badge'>Nuevo</span>" if is_new else ""
    return f"""
<article class="case case-row" data-search="{html.escape((project + ' ' + quote['client_name'] + ' ' + services).casefold())}"
         data-status="{html.escape(quote['status'])}" data-type="{html.escape(quote['client_type'])}">
  <div><h3>{html.escape(project)}{new_badge}</h3><div class="meta">{html.escape(quote['client_name'])}</div></div>
  <div><strong>{html.escape(services)}</strong><div class="meta">{html.escape(quote['sector'])}</div></div>
  <span class="badge {badge_class}">{html.escape(quote['status_label'])}</span>
  <div class="meta">{html.escape((quote['updated_at'] or quote['first_seen_at'] or 'Sin fecha')[:10])}</div>
  <a class="button secondary" href="/case?thread_id={urllib.parse.quote(quote['thread_id'])}">Abrir</a>
</article>"""


def _render_requests(message: str = "") -> str:
    quotes = all_quotes()
    unread_threads = {item["thread_id"] for item in unread_notifications(100)}
    rows = "".join(
        _quote_row(quote, quote["thread_id"] in unread_threads) for quote in quotes
    )
    if not rows:
        rows = "<div class='empty'>Todavía no hay solicitudes registradas.</div>"
    status_options = "".join(
        f"<option value='{html.escape(key)}'>{html.escape(label)}</option>"
        for key, label in STATUS_LABELS.items()
    )
    type_options = "".join(
        f"<option value='{html.escape(value)}'>{html.escape(value)}</option>"
        for value in CLIENT_TYPES
    )
    body = f"""
<div class="page-head"><div><h1>Solicitudes</h1><p>Consulta y completa la información comercial de cada oportunidad.</p></div><a class="button secondary" href="/export.xlsx">Exportar todo a Excel</a></div>
<div class="filters">
  <label>Buscar<input id="search" type="search" placeholder="Proyecto, cliente o servicio"></label>
  <label>Estado<select id="status"><option value="">Todos</option>{status_options}</select></label>
  <label>Tipo de cliente<select id="client-type"><option value="">Todos</option>{type_options}</select></label>
</div>
<div id="requests" class="grid">{rows}</div>
<div id="no-results" class="empty" hidden>No hay solicitudes con esos filtros.</div>
<script>
const search = document.querySelector('#search');
const statusFilter = document.querySelector('#status');
const typeFilter = document.querySelector('#client-type');
const rows = [...document.querySelectorAll('.case-row')];
function filterRows() {{
  const term = search.value.trim().toLocaleLowerCase('es');
  let visible = 0;
  rows.forEach(row => {{
    const show = (!term || row.dataset.search.includes(term)) &&
      (!statusFilter.value || row.dataset.status === statusFilter.value) &&
      (!typeFilter.value || row.dataset.type === typeFilter.value);
    row.hidden = !show;
    if (show) visible += 1;
  }});
  document.querySelector('#no-results').hidden = visible !== 0;
}}
[search, statusFilter, typeFilter].forEach(input => input.addEventListener('input', filterRows));
</script>"""
    return _page("Solicitudes", body, message)


def _internal_context_panel(client_name: str) -> str:
    context = internal_commercial_context(client_name)
    if not context:
        return "<p class='meta'>El índice comercial interno todavía no está disponible.</p>"

    def compact_list(items: list[tuple[str, int]], limit: int = 5) -> str:
        if not items:
            return "<p class='meta'>Sin patrones suficientes.</p>"
        return "<ul class='context-list'>" + "".join(
            f"<li><span>{html.escape(str(label))}</span><strong>{int(count)}</strong></li>"
            for label, count in items[:limit]
        ) + "</ul>"

    profile = context.get("profile")
    if profile:
        project_types = list(profile.get("project_types", {}).items())[:5]
        sectors = list(profile.get("sectors", {}).items())[:4]
        requests = list(profile.get("usual_requests", {}).items())[:6]
        profile_html = f"""
<div class="context-profile">
  <div><span class="meta">Coincidencia histórica</span><strong>{html.escape(profile.get('client_name', 'Cliente'))}</strong></div>
  <div><span class="meta">Tipo de empresa inferido</span><strong>{html.escape(profile.get('company_type', 'No clasificado'))}</strong><small>Confianza {html.escape(profile.get('company_type_confidence', 'baja'))}</small></div>
  <div><span class="meta">Documentos comerciales</span><strong>{int(profile.get('commercial_document_count', 0))}</strong></div>
</div>
<div class="context-grid">
  <div><h3>Proyectos asociados</h3>{compact_list(project_types)}</div>
  <div><h3>Sectores asociados</h3>{compact_list(sectors)}</div>
  <div><h3>Datos solicitados habitualmente</h3>{compact_list(requests)}</div>
</div>"""
    else:
        profile_html = f"""
<div class="notice internal-note"><strong>Sin coincidencia de cliente.</strong> Escribe el nombre de la empresa en Información comercial y guarda para consultar su historial. Mientras tanto se muestran patrones generales de {int(context.get('client_profiles', 0))} perfiles.</div>
<div class="context-grid">
  <div><h3>Proyectos más frecuentes</h3>{compact_list(context.get('project_types', []))}</div>
  <div><h3>Sectores más frecuentes</h3>{compact_list(context.get('sectors', []))}</div>
  <div><h3>Datos solicitados habitualmente</h3>{compact_list(context.get('usual_requests', []))}</div>
</div>"""

    return f"""
<div class="notice internal-note"><strong>Uso interno confidencial.</strong> Este historial sirve para orientar la revisión. No se copia al borrador, no contiene precios y no debe compartirse con el cliente.</div>
{profile_html}
<p class="meta" style="margin-bottom:0">Fuente: archivo histórico T&amp;E, lectura agregada del {html.escape(context.get('generated_at', ''))}.</p>"""


def _render_case(thread_id: str, message: str = "") -> str:
    review = get_quote(thread_id)
    if not review:
        return _page("Caso no encontrado", "<div class='empty'>La solicitud no existe.</div>", message)
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
    email_allowed = (
        valid_email_address(review["recipient"])
        and review["status"] not in CLOSED_STATUSES
        and assessment.get("status") != "technical_review"
    )
    if email_allowed:
        email_body = review["draft_body"] or suggested_reply(
            analysis, config["required_fields"]
        )
        draft_note = (
            "<div class='notice'>Ya existe un borrador en Gmail. Al guardarlo de nuevo se actualizará.</div>"
            if review["draft_id"] else
            "<div class='notice'><strong>Respuesta sugerida lista.</strong> Todavía no está guardada ni enviada en Gmail.</div>"
        )
        email_form = f"""
{draft_note}
<form method="post">
  <input type="hidden" name="thread_id" value="{html.escape(thread_id)}">
  <label>Respuesta para {html.escape(review['recipient'])}
    <textarea name="body" style="min-height:260px">{html.escape(email_body)}</textarea>
  </label>
  <div class="toolbar" style="margin-top:14px">
    <button type="submit" formaction="/draft">Guardar borrador en Gmail</button>
    <button type="submit" formaction="/send" class="danger"
      data-confirm="Se enviará este correo a {html.escape(review['recipient'])} en el mismo hilo. ¿Confirmas el envío?">Enviar ahora</button>
  </div>
</form>"""
    elif review["status"] == QuoteStatus.RESPONSE_SENT:
        email_form = (
            f"<div class='notice'>Respuesta enviada el {html.escape(review['sent_at'] or 'sin fecha registrada')}.</div>"
            f"<pre>{html.escape(review['draft_body'])}</pre>"
        )
    else:
        if recipient_error:
            email_form = f"<div class='notice' style='border-color:#e2b8b3;background:#faecea'>{html.escape(recipient_error)}</div>"
        elif assessment.get("status") == "technical_review":
            email_form = "<div class='notice'><strong>Respuesta en pausa.</strong> El equipo de Anka debe revisar el caso antes de solicitar información al cliente.</div>"
        else:
            email_form = "<p class='meta'>Esta solicitud no admite respuesta desde el dashboard en su estado actual.</p>"
    suggestion_complete = review["status"] not in {QuoteStatus.IGNORED, QuoteStatus.MISSING}
    draft_complete = bool(review["draft_id"] or review["status"] == QuoteStatus.RESPONSE_SENT)
    sent_complete = review["status"] == QuoteStatus.RESPONSE_SENT
    process_html = f"""
<div class="process">
  <div class="process-step done"><strong>1. Correo recibido</strong><span>Detectado y registrado</span></div>
  <div class="process-step done"><strong>2. Lectura inicial</strong><span>{html.escape(assessment.get('service_family', 'Por clasificar'))}</span></div>
  <div class="process-step {'done' if suggestion_complete else ''}"><strong>3. Aclaración sugerida</strong><span>{'Lista para revisión' if suggestion_complete else 'No disponible'}</span></div>
  <div class="process-step {'done' if draft_complete or sent_complete else 'current'}"><strong>4. Gestión de respuesta</strong><span>{'Enviada' if sent_complete else ('Borrador guardado' if draft_complete else 'Pendiente de aprobación')}</span></div>
</div>"""
    project_form = ""
    if assessment.get("status") == "sufficient_for_internal_review":
        project_form = (
            "<div class='notice'><strong>Recepción completa.</strong> La información puede "
            "pasar a revisión interna. Este dashboard todavía no prepara precios ni cotizaciones.</div>"
        )
    client_type_options = "".join(
        f"<option value='{html.escape(value)}'"
        f"{' selected' if value == review['client_type'] else ''}>{html.escape(value)}</option>"
        for value in CLIENT_TYPES
    )
    sector_options = "".join(
        f"<option value='{html.escape(value)}'"
        f"{' selected' if value == review['sector'] else ''}>{html.escape(value)}</option>"
        for value in SECTORS
    )
    metadata_form = f"""
<form method="post" action="/metadata">
  <input type="hidden" name="thread_id" value="{html.escape(thread_id)}">
  <div class="filters">
    <label>Cliente o empresa<input name="client_name" value="{html.escape(review['client_name'])}"></label>
    <label>Tipo de cliente<select name="client_type">{client_type_options}</select></label>
    <label>Sector<select name="sector">{sector_options}</select></label>
  </div>
  <label>Notas comerciales<textarea name="notes" style="min-height:90px">{html.escape(review['notes'])}</textarea></label>
  <div class="toolbar" style="margin-top:12px"><button type="submit">Guardar datos comerciales</button></div>
</form>"""
    body = f"""
<div class="page-head"><div><h1>{html.escape(analysis.get('project_name') or 'Solicitud sin nombre')}</h1><p>{html.escape(analysis.get('summary') or 'Sin resumen disponible')}</p></div><a class="button secondary" href="/requests">Volver</a></div>
<div class="layout">
  <section class="section">
    <span class="badge">{html.escape(review['status_label'])}</span>
    <div class="meta">Destinatario: {html.escape(review['recipient'])}</div>
    <div class="meta">Hilo: {html.escape(thread_id)}</div>
    {process_html}
  </section>
  <section class="section">
    <div class="section-head"><h2>Evaluación de recepción</h2><span class="badge info">Sin cotizar</span></div>
    {_reception_assessment_panel(assessment)}
  </section>
  <section class="section"><h2>Información comercial</h2>{metadata_form}</section>
  <section class="section">
    <div class="section-head"><h2>Contexto comercial interno</h2><span class="badge info">Solo Anka</span></div>
    {_internal_context_panel(review['client_name'])}
  </section>
  <section class="section">
    <h2>Actividades detectadas</h2>
    {activities_editor(thread_id, analysis.get('activities', []))}
  </section>
  <section class="section">
    <h2>Adjuntos</h2>
    {_attachments_table(analysis.get('attachments', []))}
  </section>
  <section class="section">
    <h2>Enrutamiento y validaciones</h2>
    <pre>{html.escape(routing_summary)}</pre>
    <h2 style="margin-top:16px;">Asuntos pendientes</h2>
    <pre>{html.escape(issues_summary)}</pre>
  </section>
  <section class="section">
    <h2>Respuesta al cliente</h2>
    <p class="meta">Revisa el destinatario y el contenido. Ningún correo se envía sin pulsar “Enviar ahora”.</p>
    {email_form}
    <div style="margin-top:14px">{project_form}</div>
  </section>
</div>"""
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
        self._send_html(_page("Solicitud rechazada", f"<div class='empty'>{html.escape(reason)}</div>"), 403)

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
                return self._send_html(_page("No encontrado", "<div class='empty'>La solicitud no existe.</div>"), 404)
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
            self._send_html(_page("No encontrado", "<div class='empty'>Página no encontrada.</div>"), 404)

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
                self._send_html(_page("No encontrado", "<div class='empty'>Página no encontrada.</div>"), 404)
        except (Exception, SystemExit) as exc:
            self._send_html(_page("Error", f"<div class='empty'>{html.escape(str(exc))}</div>"), 500)

    def _redirect(self, location: str) -> None:
        self.send_response(303)
        self.send_header("Location", location)
        self.end_headers()

    def log_message(self, format: str, *args) -> None:
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
