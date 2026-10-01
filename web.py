"""Dashboard local: páginas HTML y servidor HTTP con protección CSRF."""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterable
from typing import Any
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
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
from logging_setup import get_logger
from settings import load_config
from templating import render

from case_actions import approve_review, delete_cases, resolve_case_recipient, save_dashboard_draft, send_dashboard_reply
from pipeline import checked_run, describe_check, sync_status_text
from work_heartbeat import bridge_state
from change_control import (
    STAGE_LABELS, assign_code, code_label, promote_to_te, required_messages, resolve_change,
    suggest_number,
)
from gmail_followup import case_contacts
from quotes import EDIT_REASONS, archived_threads, last_manual_version, table_versions, case_change_log, chain_status, case_has_links, case_people, reason_label, resolve_link, ago_text, change_marker, minutes_since, REPLY_OVERDUE_MINUTES, resolve_model_proposal, CLIENT_TYPES, resolve_technical_review, discarded_quotes, rescue_discarded, SECTORS, STATUS_LABELS, all_quotes, get_quote, thread_events, mark_notifications_read, save_activities, save_quote_metadata, unread_notifications

log = get_logger(__name__)


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


def _quote_row(quote: dict, is_new: bool = False, selectable: bool = False) -> Markup:
    project = quote["analysis"].get("project_name") or "Solicitud sin nombre"
    services = ", ".join(quote["services"]) or "Servicio por clasificar"
    return render(
        "quote_row.html",
        project=project, services=services, is_new=is_new, selectable=selectable,
        search=(project + " " + quote["client_name"] + " " + services).casefold(),
        status=quote["status"], client_type=quote["client_type"],
        client_name=quote["client_name"], sector=quote["sector"],
        badge_class="warn" if quote["status"] in ACTION_STATUSES else "info",
        status_label=quote["status_label"],
        date=(quote["updated_at"] or quote["first_seen_at"] or "Sin fecha")[:10],
        thread_id=quote["thread_id"], reply_pending=bool(quote.get("reply_pending", "")),
        code=code_label(quote["quote_code"], quote.get("code_stage") or "T") if quote.get("quote_code") else "",
    )


def _edit_note(thread_id: str) -> Markup:
    """Una línea discreta bajo la tabla: tu última edición y el aviso opcional al cliente."""
    latest = last_manual_version(thread_id)
    if not latest:
        return Markup("")
    return render("edit_note.html", version=latest["version"], thread_id=thread_id,
                  when=latest["created_at"][:16].replace("T", " "), summary=latest["summary"],
                  notified=latest["notified"])


def _link_banner(review: dict) -> Markup:
    link = review["links"][0]
    contacts = case_contacts(review["recipient"], review["analysis"])
    return render(
        "link_banner.html", link=link, who=link["sender_name"] or link["sender_email"],
        reason=reason_label(link["reason"]), thread_id=review["thread_id"],
        more=len(review["links"]) - 1,
        show_verify=link["reason"].startswith("domain") or link["sender_email"] not in contacts)


def _reply_template(kind: str, review: dict, change: dict | None = None) -> str:
    """Texto base de cada correo del flujo (siempre se revisa antes de enviar).

    Los correos a quien no es el contacto registrado nunca incluyen el código del proyecto.
    """
    project = review["analysis"].get("project_name") or "su solicitud"
    code = code_label(review.get("quote_code", ""), review.get("code_stage", "T")) if review.get("quote_code") else ""
    contact = review.get("contact_email") or review.get("recipient", "")
    link = (review.get("links") or [None])[0]
    if kind == "received" and code:
        return (
            f"Hola,\n\nRecibimos su proyecto «{project}» y ya está en proceso de cotización. "
            f"Hemos generado el código {code}.\n\nPara cualquier cambio o solicitud sobre este proyecto, "
            f"por favor cite este código ({code}); las solicitudes que no lo incluyan no podrán "
            f"procesarse. Le recomendamos escribir siempre desde el correo registrado ({contact}) o "
            "desde el dominio de su empresa.\n\nSaludos,\nGrupo Anka"
        )
    if kind == "scope_update":
        latest = last_manual_version(review["thread_id"])
        what = f" ({latest['summary']})" if latest else ""
        return (
            f"Hola,\n\nActualizamos la tabla de actividades de su proyecto «{project}»"
            f"{f' ({code})' if code else ''}{what}.\n\nSi algo no coincide con lo que usted solicitó, "
            "por favor indíquelo respondiendo a este correo.\n\nSaludos,\nGrupo Anka"
        )
    if kind == "ack" and change:
        return (
            f"Hola,\n\nConfirmamos que recibimos su solicitud de cambio sobre el proyecto «{project}»"
            f"{f' ({code})' if code else ''}: «{change['snippet']}».\n\nLa revisaremos y les "
            "confirmaremos cómo queda el alcance.\n\nSaludos,\nGrupo Anka"
        )
    if kind == "ask_code" and change:
        return (
            f"Hola,\n\nRecibimos su mensaje sobre el proyecto «{project}». Para poder procesar "
            "cualquier cambio necesitamos que cite el código del proyecto, que se informó al "
            "contacto registrado. Por favor envíe de nuevo su solicitud incluyéndolo.\n\n"
            "Por seguridad, los cambios deben provenir del correo registrado o del dominio de su "
            "empresa, o ser confirmados por el contacto del proyecto.\n\nSaludos,\nGrupo Anka"
        )
    if kind == "notify_contact" and change:
        who = change["sender_name"] or change["sender_email"]
        cites = "cita" if change["has_code"] else "no cita"
        return (
            f"Hola,\n\nLe informamos que {who} ({change['sender_email']}) escribió sobre el proyecto "
            f"«{project}» solicitando: «{change['snippet']}». El mensaje {cites} el código del proyecto."
            "\n\nPara dejar constancia, ¿puede confirmarnos que está autorizado(a) para solicitar "
            "cambios y que lo indicado es correcto? Le recomendamos que las solicitudes se envíen "
            "siempre desde el correo registrado o desde el dominio de su empresa."
            "\n\nSaludos,\nGrupo Anka"
        )
    if kind == "verify" and link:
        who = link["sender_name"] or link["sender_email"]
        return (
            f"Hola,\n\nRecibimos un correo de {who} ({link['sender_email']}) con una modificación "
            f"sobre el proyecto {project}: «{link['subject']}».\n\nPara dejar constancia, ¿pueden "
            f"confirmarnos que {who} está autorizado(a) para solicitar cambios en esta solicitud y "
            "que lo indicado es correcto?\n\nSaludos,\nGrupo Anka"
        )
    subject = f" «{link['subject']}»" if link else ""
    return (
        f"Hola,\n\nConfirmamos que recibimos su correo{subject} sobre el proyecto {project}. "
        "Lo revisaremos junto con la solicitud y les confirmaremos cómo queda el alcance."
        "\n\nSaludos,\nGrupo Anka"
    )


def _change_banner(review: dict) -> Markup:
    change = next(c for c in review["changes"] if c["state"] == "pending")
    return render("change_banner.html", change=change, thread_id=review["thread_id"],
                  who=change["sender_name"] or change["sender_email"])


def _change_todo(review: dict) -> Markup:
    """Correos que faltan por enviar por cada cambio confirmado."""
    base = "/case?thread_id=" + urllib.parse.quote(review["thread_id"])
    for change in review.get("changes", []):
        if change["state"] != "change":
            continue
        todo = []
        for purpose, target in required_messages(bool(change["has_code"]), bool(change["is_contact"])):
            done = change["contact_notified_at"] if purpose == "notify_contact" else change["reply_sent_at"]
            if done:
                continue
            label = {"ack": "Confirmar el cambio recibido a", "ask_code": "Pedir el código a",
                     "notify_contact": "Avisar al contacto registrado:"}[purpose]
            who = change["sender_name"] or change["sender_email"]
            if target == "contact":
                who = review.get("contact_email") or review["recipient"]
            todo.append({"label": f"{label} {who}",
                         "href": f"{base}&template={purpose}&change_id={change['id']}&to={target}#respuesta"})
        if todo:
            return render("change_todo.html", todo=todo,
                          who=change["sender_name"] or change["sender_email"])
    return Markup("")


def _code_panel(review: dict) -> Markup:
    code = review.get("quote_code", "")
    stage = review.get("code_stage", "")
    can_assign = review["status"] not in (QuoteStatus.IGNORED, QuoteStatus.MISSING)
    if not code and not can_assign:
        return Markup("")
    return render(
        "code_panel.html", code=code_label(code, stage or "T") if code else "", stage=stage,
        stage_label=STAGE_LABELS.get(stage, ""), thread_id=review["thread_id"],
        approved_code=code_label(code, "TE") if code else "", can_assign=can_assign and not code,
        suggestion=suggest_number() if not code else "",
        contact=review.get("contact_email") or review["recipient"])


def _next_step_banner(review: dict, work_mode: bool) -> Markup:
    """Un solo aviso arriba del caso: lo más importante que sigue, con su botón si lo hay."""
    if any(c["state"] == "pending" for c in review.get("changes", [])):
        return _change_banner(review)
    todo = _change_todo(review)
    if todo:
        return todo
    if review.get("links"):
        return _link_banner(review)
    if review.get("reply_pending", ""):
        minutes = minutes_since(review.get("reply_pending", ""))
        late = minutes >= REPLY_OVERDUE_MINUTES
        who = "ChatGPT Work" if work_mode else "el piloto de borradores"
        return render(
            "next_step.html", tone="warn" if late else "info",
            title=f"{review['client_name']} respondió {ago_text(minutes)}",
            text=(f"La tabla aún no recoge esa respuesta. Revisa que {who} esté activo."
                  if late else "Se analizará en el próximo ciclo y la tabla se actualizará sola."),
            action_href="", action_label="")
    if review.get("proposal", []):
        return render(
            "next_step.html", tone="warn",
            title=f"El modelo sugiere {len(review['proposal'])} cambio(s) en tu tabla",
            text="Llegó información nueva y tu tabla editada no se tocó.",
            action_href="#proposal", action_label="Revisar")
    return Markup("")


def _manual_review_note(manual: dict | None) -> str:
    if not manual:
        return ""
    when = (manual.get("at") or "")[:16].replace("T", " ")
    action = ("la marcó como información suficiente" if manual.get("decision") == "sufficient"
              else "decidió preguntar al cliente")
    return f"Una persona revisó este caso y {action} ({when})."


def _render_requests(message: str = "") -> str:
    quotes = all_quotes()
    unread_threads = {item["thread_id"] for item in unread_notifications(100)}
    body = render(
        "requests.html",
        rows=[_quote_row(quote, quote["thread_id"] in unread_threads, selectable=True) for quote in quotes],
        status_options=list(STATUS_LABELS.items()),
        type_options=CLIENT_TYPES,
    )
    return _page("Solicitudes", body, message)


def _render_discarded(message: str = "") -> str:
    body = render("discarded.html", rows=discarded_quotes())
    return _page("Descartadas", body, message)


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


def _render_case(thread_id: str, message: str = "", template: str = "", change_id: int = 0,
                 to_kind: str = "") -> str:
    review = get_quote(thread_id)
    if not review:
        return _page("Caso no encontrado", render("empty.html", text="La solicitud no existe."), message)
    recipient_error = ""
    if not valid_email_address(review["recipient"]):
        try:
            review = resolve_case_recipient(thread_id)["quote"]
        except Exception:
            log.warning("%s: no se pudo confirmar el destinatario en Gmail", thread_id, exc_info=True)
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
    reopened = review["status"] in (QuoteStatus.READY, QuoteStatus.RESPONSE_SENT) and case_has_links(thread_id)
    email_allowed = (
        valid_email_address(review["recipient"])
        and (review["status"] not in CLOSED_STATUSES or reopened)
        and not technical
    )
    email_body = ""
    if email_allowed:
        email = "form"
        change = next((c for c in review.get("changes", []) if c["id"] == change_id), None)
        if template in ("received", "ack", "ask_code", "notify_contact", "verify", "scope_update") or reopened:
            email_body = _reply_template(template or "ack", review, change)
        else:
            email_body = review["draft_body"] or suggested_reply(analysis, config["required_fields"])
    elif review["status"] == QuoteStatus.RESPONSE_SENT:
        email = "sent"
    elif recipient_error:
        email = "recipient_error"
    elif technical:
        email = "technical"
    else:
        email = "unavailable"
    change = next((c for c in review.get("changes", []) if c["id"] == change_id), None)
    compose_to = ""
    if change and to_kind == "sender":
        compose_to = change["sender_email"]
    elif change and to_kind == "contact":
        compose_to = review.get("contact_email") or review["recipient"]
    events = thread_events(thread_id)
    integrity_ok, event_count = chain_status(thread_id)
    # «Enviada» se cumple venga el envío del dashboard, de Gmail o de Work.
    sent_complete = review["status"] == QuoteStatus.RESPONSE_SENT or any(
        event["kind"] == "sent" for event in events)
    no_reply_needed = review["status"] in {QuoteStatus.READY, QuoteStatus.IGNORED}
    body = render(
        "case.html",
        review=review, thread_id=thread_id,
        project_name=analysis.get("project_name") or "Solicitud sin nombre",
        summary=analysis.get("summary") or "Sin resumen disponible",
        service_family=assessment.get("service_family", "Por clasificar"),
        suggestion_complete=review["status"] not in {QuoteStatus.IGNORED, QuoteStatus.MISSING},
        draft_complete=bool(review["draft_id"] or sent_complete),
        sent_complete=sent_complete, no_reply_needed=no_reply_needed,
        assessment_panel=_reception_assessment_panel(assessment),
        client_types=CLIENT_TYPES, sectors=SECTORS,
        internal_context=_internal_context_panel(review["client_name"]),
        activities_editor=activities_editor(thread_id, analysis.get("activities", []), list(EDIT_REASONS.items())),
        attachments_table=_attachments_table(analysis.get("attachments", [])),
        routing_summary=routing_summary, issues_summary=issues_summary,
        email=email, email_body=email_body, recipient_error=recipient_error,
        sufficient=assessment.get("status") == "sufficient_for_internal_review",
        drive_pending=(analysis.get("drive") or {}).get("pending", ""),
        can_resolve=review["status"] == QuoteStatus.REVIEW,
        manual_review=_manual_review_note(analysis.get("manual_review")),
        compose_to=compose_to, compose_change_id=change_id if change else 0,
        compose_purpose=(template if change and template in ("ack", "ask_code", "notify_contact")
                         else "scope_update" if template == "scope_update" else ""),
        edit_note=_edit_note(thread_id),
        code_panel=_code_panel(review),
        code_text=(code_label(review["quote_code"], review.get("code_stage") or "T")
                   + " · " + STAGE_LABELS.get(review.get("code_stage") or "T", "")
                   if review.get("quote_code") else ""),
        next_step=_next_step_banner(review, config.get("active_mode") == "chatgpt_work"),
        proposal_panel=(render("proposal.html", changes=review.get("proposal", []), thread_id=thread_id)
                        if review.get("proposal", []) else Markup("")),
        timeline=render("timeline.html", thread_id=thread_id, integrity_ok=integrity_ok,
                        event_count=event_count, events=[
            {**event, "when": event["occurred_at"][:16].replace("T", " "),
             "origin": SOURCE_LABELS.get(event["source"], event["source"]),
             "who": event["actor_name"] or event["actor_email"],
             "gmail_url": ("https://mail.google.com/mail/u/0/#all/" + event["ref"]
                           if event["kind"] in ("sent", "received", "related") else "")}
            for event in events],
            people=case_people(thread_id, case_contacts(review["recipient"], analysis))),
    )
    return _page("Revisar caso", body, message)


def _acta_versions(thread_id: str, review: dict | None) -> list[dict]:
    """Versiones de la tabla con su origen; las ediciones internas posteriores a la T se rotulan."""
    assigned = ((review or {}).get("code_assigned_at") or "")
    result = []
    for version in table_versions(thread_id):
        rows = json.loads(version["activities_json"])
        origin = {"model": "Modelo", "dashboard": "Comercial", "previa": "Previa"}.get(
            version["source"], version["source"])
        note = ""
        if version["source"] == "dashboard" and assigned and version["created_at"] > assigned:
            note = "Edición interna posterior a la T"
        result.append({
            "version": version["version"], "when": version["created_at"][:16].replace("T", " "),
            "origin": origin, "summary": version["summary"], "note": note,
            "reason": EDIT_REASONS.get(version["reason"], ""), "count": len(rows),
            "rows": [str(r.get("activity", "")) + (f" — {r.get('quantity')} {r.get('unit') or ''}".rstrip()
                                                  if r.get("quantity") else "") for r in rows],
        })
    return result


def _render_acta(thread_id: str) -> str:
    """Acta imprimible: quién pidió qué, cuándo, con qué código y a quién se avisó."""
    review = get_quote(thread_id)
    events = [
        {**event, "when": event["occurred_at"][:16].replace("T", " "),
         "origin": SOURCE_LABELS.get(event["source"], event["source"]),
         "who": event["actor_name"] or event["actor_email"],
         "gmail_url": ("https://mail.google.com/mail/u/0/#all/" + event["ref"]
                       if event["kind"] in ("sent", "received", "related") else "")}
        for event in reversed(thread_events(thread_id))
    ]
    if not review and not events:
        return _page("Caso no encontrado", render("empty.html", text="No hay historial para ese caso."))
    contact = (review or {}).get("contact_email", "")
    ok, count = chain_status(thread_id)
    code = ""
    stage = (review or {}).get("code_stage", "")
    if review and review.get("quote_code"):
        code = code_label(review["quote_code"], stage or "T")
    body = render(
        "acta.html", project=(review["analysis"].get("project_name") if review else "") or "Caso eliminado",
        code=code, contact=contact, stage_label=STAGE_LABELS.get(stage, ""), generated=datetime.now().strftime("%Y-%m-%d %H:%M"),
        integrity_ok=ok, event_count=count, events=events, changes=case_change_log(thread_id),
        people=case_people(thread_id, {contact}) if contact else case_people(thread_id, set()),
        versions=_acta_versions(thread_id, review),
        back=("/case?thread_id=" + urllib.parse.quote(thread_id)) if review else "/archive")
    return _page("Acta de cambios", body)


SOURCE_LABELS = {"dashboard": "Dashboard", "gmail": "Gmail", "work": "ChatGPT Work",
                 "python": "Piloto"}


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
        origin = getattr(self, "client_address", ("?",))[0]
        log.warning("Solicitud rechazada (%s) desde %s", reason, origin)
        self._send_html(_page("Solicitud rechazada", render("empty.html", text=reason)), 403)

    def do_GET(self) -> None:
        try:
            self._get()
        except (Exception, SystemExit) as exc:
            log.exception("Error atendiendo GET %s", self.path.split("?")[0])
            self._send_html(_page("Error", render("empty.html", text=str(exc))), 500)

    def _get(self) -> None:
        if not is_local_host_header(self.headers.get("Host", "")):
            return self._reject("Host no permitido.")
        parsed = urllib.parse.urlparse(self.path)
        query = urllib.parse.parse_qs(parsed.query)
        if parsed.path == "/":
            self._send_html(_render_index(query.get("message", [""])[0]))
        elif parsed.path == "/state":
            try:
                work = load_config().get("active_mode") == "chatgpt_work"
            except (SystemExit, OSError, ValueError):
                work = False
            state, text = bridge_state() if work else ("", "")
            data = json.dumps({"v": change_marker(), "bridge": state, "text": text}).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
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
        elif parsed.path == "/discarded":
            self._send_html(_render_discarded(query.get("message", [""])[0]))
        elif parsed.path == "/case":
            self._send_html(_render_case(
                query.get("thread_id", [""])[0], query.get("message", [""])[0],
                query.get("template", [""])[0],
                int(query.get("change_id", ["0"])[0] or 0), query.get("to", [""])[0],
            ))
        elif parsed.path == "/acta":
            self._send_html(_render_acta(query.get("thread_id", [""])[0]))
        elif parsed.path == "/archive":
            self._send_html(_page("Archivo", render("archive.html", items=archived_threads())))
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
                try:
                    message = approve_review(
                        thread_id, body_text=body,
                        client_name=form.get("client_name", [None])[0],
                        new_client=form.get("new_client", [""])[0] == "1",
                        allow_similar_project=form.get("allow_similar", [""])[0] == "1",
                    )
                except SystemExit as exc:  # orientación para la persona, no un fallo del servidor
                    message = str(exc)
                self._redirect(
                    "/case?thread_id=" + urllib.parse.quote(thread_id)
                    + "&message=" + urllib.parse.quote(message)
                )
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
                message = save_dashboard_draft(thread_id, body, form.get("to", [""])[0])
                self._redirect(
                    "/case?thread_id=" + urllib.parse.quote(thread_id)
                    + "&message=" + urllib.parse.quote(message)
                )
            elif parsed.path == "/send":
                thread_id = form.get("thread_id", [""])[0]
                body = form.get("body", [""])[0]
                message = send_dashboard_reply(
                    thread_id, body, form.get("to", [""])[0],
                    int(form.get("change_id", ["0"])[0] or 0), form.get("purpose", [""])[0])
                self._redirect(
                    "/case?thread_id=" + urllib.parse.quote(thread_id)
                    + "&message=" + urllib.parse.quote(message)
                )
            elif parsed.path == "/activities":
                thread_id = form.get("thread_id", [""])[0]
                message = save_activities(thread_id, parse_activities_form(form),
                                          form.get("reason", [""])[0])
                self._redirect(
                    "/case?thread_id=" + urllib.parse.quote(thread_id)
                    + "&message=" + urllib.parse.quote(message)
                )
            elif parsed.path == "/mode":
                message = set_mode(form.get("mode", [""])[0])
                self._redirect("/?message=" + urllib.parse.quote(message))
            elif parsed.path == "/rescue":
                message = rescue_discarded(form.get("thread_id", [""])[0])
                self._redirect("/discarded?message=" + urllib.parse.quote(message))
            elif parsed.path == "/review-decision":
                thread_id = form.get("thread_id", [""])[0]
                try:
                    message = resolve_technical_review(
                        thread_id, form.get("decision", [""])[0], form.get("questions", [""])[0])
                except ValueError as exc:  # orientación para la persona, no un fallo del servidor
                    message = str(exc)
                self._redirect(
                    "/case?thread_id=" + urllib.parse.quote(thread_id)
                    + "&message=" + urllib.parse.quote(message)
                )
            elif parsed.path in ("/code", "/code-approve", "/change"):
                thread_id = form.get("thread_id", [""])[0]
                suffix = ""
                try:
                    if parsed.path == "/code":
                        message = assign_code(thread_id, form.get("number", [""])[0])
                        suffix = "&template=received#respuesta"
                    elif parsed.path == "/code-approve":
                        message = promote_to_te(thread_id)
                    else:
                        message = resolve_change(thread_id, int(form.get("change_id", ["0"])[0] or 0),
                                                 form.get("decision", [""])[0] == "yes")
                except ValueError as exc:
                    message = str(exc)
                self._redirect(
                    "/case?thread_id=" + urllib.parse.quote(thread_id)
                    + "&message=" + urllib.parse.quote(message) + suffix
                )
            elif parsed.path == "/link":
                thread_id = form.get("thread_id", [""])[0]
                try:
                    message = resolve_link(thread_id, int(form.get("link_id", ["0"])[0]),
                                           form.get("decision", [""])[0] == "yes")
                except ValueError as exc:
                    message = str(exc)
                self._redirect(
                    "/case?thread_id=" + urllib.parse.quote(thread_id)
                    + "&message=" + urllib.parse.quote(message)
                )
            elif parsed.path == "/proposal":
                thread_id = form.get("thread_id", [""])[0]
                try:
                    message = resolve_model_proposal(
                        thread_id, form.get("decision", [""])[0] == "use")
                except ValueError as exc:
                    message = str(exc)
                self._redirect(
                    "/case?thread_id=" + urllib.parse.quote(thread_id)
                    + "&message=" + urllib.parse.quote(message)
                )
            elif parsed.path == "/delete":
                message = delete_cases(form.get("thread_id", []))
                self._redirect("/requests?message=" + urllib.parse.quote(message))
            elif parsed.path == "/notifications/read":
                mark_notifications_read()
                self._redirect("/?message=" + urllib.parse.quote("Novedades marcadas como revisadas."))
            else:
                self._send_html(_page("No encontrado", render("empty.html", text="Página no encontrada.")), 404)
        except (Exception, SystemExit) as exc:
            log.exception("Error atendiendo POST %s", parsed.path)
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
