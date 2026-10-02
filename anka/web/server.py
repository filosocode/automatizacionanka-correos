"""Dashboard local: páginas HTML y servidor HTTP con protección CSRF."""

from __future__ import annotations

from collections import Counter
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import replace
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
from anka.dominio.activities import build_workbook, parse_activities_form
from anka.web.views import (
    attachments_table, metric_rows,
    reception_assessment_panel, activities_editor, render_page,
)
from anka.dominio.status import (
    CLOSED_STATUSES, QuoteStatus,
)
from anka.dominio.correo import valid_email_address
from anka.dominio.reception import (
    reception_assessment, reception_questions, suggested_reply, unresolved_scope_issues,
)
from anka.infra.modes import (
    set_mode,
)
from anka.servicios.internal_context import internal_commercial_context
from anka.infra.logging_setup import get_logger
from anka.infra import settings
from anka.infra.settings import load_config
from anka.web.templating import render

from anka.servicios.case_actions import approve_review, delete_cases, resolve_case_recipient, save_dashboard_draft, send_dashboard_reply
from anka.servicios.pipeline import checked_run, describe_check, sync_status_text
from anka.infra.work_heartbeat import bridge_state
from anka.servicios import followup_reminders as fup
from anka.servicios.change_control import (
    STAGE_LABELS, assign_code, code_label, promote_to_te, required_messages, resolve_change,
    suggest_number,
)
from anka.servicios.gmail_followup import case_contacts
from anka.servicios.quotes import EDIT_REASONS, case_conversation, archived_threads, last_manual_version, table_versions, case_change_log, chain_status, case_has_links, case_people, reason_label, resolve_link, ago_text, change_marker, minutes_since, REPLY_OVERDUE_MINUTES, resolve_model_proposal, CLIENT_TYPES, resolve_technical_review, discarded_quotes, rescue_discarded, SECTORS, STATUS_LABELS, all_quotes, get_quote, thread_events, mark_notifications_read, save_activities, save_quote_metadata, unread_notifications

log = get_logger(__name__)


def _page(title: str, body: Markup, message: str = "") -> str:
    try:
        mode = load_config().get("active_mode")
    except (SystemExit, OSError, ValueError):
        mode = None
    return render_page(title, body, message, len(unread_notifications(100)), mode,
                       bool(os.getenv("OPENAI_API_KEY")))


def _dashboard_insights(quotes: Sequence[Mapping[str, Any]], services: Counter, clients: Counter) -> Markup:
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
    stages = {q["thread_id"]: _case_stage(q) for q in quotes}
    todo_cases = [q for q in quotes if stages[q["thread_id"]][1]]
    body = render(
        "index.html",
        sync_text=sync_status_text(),
        notifications=_notifications_panel(notifications),
        total=len(quotes),
        unique_clients=len({q["client_email"].casefold() for q in quotes if q["client_email"]}),
        action_count=len(todo_cases),
        waiting=sum(stages[q["thread_id"]][0] == "Esperando al cliente" for q in quotes),
        quoting=sum(bool(q.get("quote_code")) and q.get("code_stage") != "TE" for q in quotes),
        approved=sum(q.get("code_stage") == "TE" for q in quotes),
        services=metric_rows(services),
        statuses=metric_rows(Counter(stages[q["thread_id"]][0] for q in quotes), "blue"),
        client_types=metric_rows(Counter(q["client_type"] for q in quotes)),
        insights=_dashboard_insights(quotes, services, clients),
        todo=[_quote_row(q, q["thread_id"] in unread_threads) for q in todo_cases],
    )
    return _page("Resumen comercial", body, message)


def _quote_row(quote: Mapping[str, Any], is_new: bool = False, selectable: bool = False) -> Markup:
    project = quote["analysis"].get("project_name") or "Solicitud sin nombre"
    services = ", ".join(quote["services"]) or "Servicio por clasificar"
    return render(
        "quote_row.html",
        project=project, services=services, is_new=is_new, selectable=selectable,
        search=(project + " " + quote["client_name"] + " " + services).casefold(),
        status=quote["status"], client_type=quote["client_type"],
        client_name=quote["client_name"], sector=quote["sector"],
        badge_class="warn" if _case_stage(quote)[1] else "info",
        status_label=_case_stage(quote)[0],
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


def _suggested_questions(analysis: dict, assessment: dict) -> str:
    """Preguntas para el cliente que dejó el análisis (sin los puntos internos), una por línea."""
    found: list[str] = []
    for item in list(assessment.get("questions", [])) + list(analysis.get("other_questions", [])):
        text = str(item).strip()
        if not text or "interno" in text.casefold().split(":")[0]:
            continue
        for prefix in ("Crítico:", "Critico:", "Recomendable:"):
            if text.startswith(prefix):
                text = text[len(prefix):].strip()
        text = text[:1].upper() + text[1:]
        if text and text not in found:
            found.append(text)
    return "\n".join(found)


def _link_banner(review: Mapping[str, Any]) -> Markup:
    link = review["links"][0]
    contacts = case_contacts(review["recipient"], review["analysis"])
    return render(
        "link_banner.html", link=link, who=link["sender_name"] or link["sender_email"],
        reason=reason_label(link["reason"]), thread_id=review["thread_id"],
        more=len(review["links"]) - 1,
        show_verify=link["reason"].startswith("domain") or link["sender_email"] not in contacts)


def _reply_template(kind: str, review: Mapping[str, Any], change: dict | None = None) -> str:
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
    if kind == "acuse":
        return (
            "Hola,\n\nGracias por su solicitud. Confirmamos que la hemos recibido y estamos revisando "
            "la información compartida para definir el alcance. Si necesitamos alguna aclaración "
            "adicional, nos comunicaremos por este mismo hilo.\n\nSaludos,\nGrupo Anka"
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


def _change_banner(review: Mapping[str, Any]) -> Markup:
    change = next(c for c in review["changes"] if c["state"] == "pending")
    return render("change_banner.html", change=change, thread_id=review["thread_id"],
                  who=change["sender_name"] or change["sender_email"])


def _change_todo(review: Mapping[str, Any]) -> Markup:
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


def _info_validated(review: Mapping[str, Any]) -> bool:
    """¿La información del caso ya está validada para empezar a cotizar?

    Sí cuando el alcance está completo, una persona dio la información por suficiente, o ya hay T.
    """
    analysis = review["analysis"]
    return bool(review.get("quote_code")) or review["status"] in (
        QuoteStatus.READY_REVIEW, QuoteStatus.READY
    ) or (analysis.get("manual_review") or {}).get("decision") == "sufficient"


def _stepper(review: Mapping[str, Any]) -> Markup:
    """Recorrido del caso: dónde está y qué sigue, de un vistazo."""
    code = review.get("quote_code", "")
    stage = review.get("code_stage", "")
    validated = _info_validated(review)
    current = 4 if stage == "TE" else 3 if code else 2 if validated else 1
    current_notes = {
        1: review["status_label"], 2: "Listo para emitir la T",
        3: code_label(code, "T") + " · cotizando" if code else "",
        4: code_label(code, "TE") + " · aprobada" if code else "",
    }
    titles = {1: "Revisar y aclarar", 2: "Información validada", 3: "T emitida", 4: "T&E aprobada"}
    steps = [{"title": titles[n],
              "note": "Completado" if n < current else current_notes[n] if n == current else "Pendiente",
              "state": "done" if n < current else "current" if n == current else ""}
             for n in range(1, 5)]
    return render("stepper.html", steps=steps)


def _code_panel(review: Mapping[str, Any]) -> Markup:
    code = review.get("quote_code", "")
    stage = review.get("code_stage", "")
    can_assign = (review["status"] not in (QuoteStatus.IGNORED, QuoteStatus.MISSING)
                  and _info_validated(review))
    if not code and not can_assign:
        return Markup("")
    return render(
        "code_panel.html", code=code_label(code, stage or "T") if code else "", stage=stage,
        stage_label=STAGE_LABELS.get(stage, ""), thread_id=review["thread_id"],
        approved_code=code_label(code, "TE") if code else "", can_assign=can_assign and not code,
        suggestion=suggest_number() if not code else "",
        contact=review.get("contact_email") or review["recipient"])


def _next_step_banner(review: Mapping[str, Any], work_mode: bool) -> Markup:
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
    label, urgent = _case_stage(review)
    if label == "Llamar al cliente":
        return render(
            "next_step.html", tone="warn", title="Llamar al cliente",
            text="Lleva 7 días hábiles sin responder. Llámalo para avanzar y anota que ya hablaste con él.",
            action_href="#seguimiento", action_label="Ver seguimiento")
    if label == "Sin respuesta · recordar":
        return render(
            "next_step.html", tone="warn", title="El cliente no ha respondido",
            text="Lleva 3 días hábiles esperando. Envía un recordatorio o activa el seguimiento automático.",
            action_href="#seguimiento", action_label="Ver seguimiento")
    if review["status"] == QuoteStatus.REVIEW:
        return render(
            "next_step.html", tone="warn", title="Falta tu decisión de revisión técnica",
            text="Decide si la información es suficiente o si hay que preguntar al cliente.",
            action_href="#decision", action_label="Decidir")
    if _info_validated(review) and not review.get("quote_code") and review["status"] != QuoteStatus.IGNORED:
        return render(
            "next_step.html", tone="info", title="Información validada: puedes emitir la T",
            text="Damos por recibido el proyecto y empezamos a cotizar.",
            action_href="#emitir-t", action_label="Emitir la T")
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
        rows=[_quote_row(quote, quote["thread_id"] in unread_threads, selectable=True)
              for quote in sorted(quotes, key=lambda q: not _case_stage(q)[1])],  # primero lo que pide acción
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


def _routing_summary(analysis: dict) -> str:
    """Cómo llegó el correo: directo o reenviado por alguien interno."""
    routing = analysis.get("routing", {})
    if not routing.get("is_forwarded"):
        return "Correo directo"
    return (
        f"Reenvío interno: {routing.get('internal_forwarder') or 'no indicado'}\n"
        f"Cliente original: {routing.get('original_sender') or 'no identificado'}\n"
        f"Destinatario seleccionado: {routing.get('selected_recipient') or 'no indicado'}"
    )


_FREE_FORM_TEMPLATES = ("received", "ack", "ask_code", "notify_contact", "verify", "scope_update", "acuse")


def _compose_state(review: Mapping[str, Any], analysis: dict, config: dict, template: str, change_id: int, *,
                   email_allowed: bool, reopened: bool, technical: bool, recipient_error: str,
                   ) -> tuple[str, str, bool]:
    """Qué muestra el cuadro de respuesta: (modo, texto inicial, ¿en blanco?)."""
    if email_allowed and review["status"] == QuoteStatus.WAITING and not template and review["sent_at"]:
        return "waiting", "", False   # ya se escribió: no se ofrece enviar lo mismo otra vez
    if not email_allowed:
        if review["status"] == QuoteStatus.RESPONSE_SENT:
            return "sent", "", False
        if recipient_error:
            return "recipient_error", "", False
        return ("technical" if technical else "unavailable"), "", False
    if template == "libre" or (template not in _FREE_FORM_TEMPLATES and reopened):
        return "form", "", True       # respuesta libre en blanco (o caso ya avanzado)
    if template in _FREE_FORM_TEMPLATES:
        change = next((c for c in review.get("changes", []) if c["id"] == change_id), None)
        return "form", _reply_template(template, review, change), False
    # Con preguntas por hacer se propone el texto; si no hay nada que preguntar, el cuadro
    # queda en blanco para que la persona escriba lo que quiera (o pida el acuse sugerido).
    manual = analysis.get("manual_review") or {}
    has_questions = bool(reception_questions(analysis, config["required_fields"]) or manual.get("questions"))
    accepted = (review["status"] in (QuoteStatus.READY_REVIEW, QuoteStatus.READY)
                or bool(review.get("quote_code")) or manual.get("decision") == "sufficient")
    work_reply = analysis.get("work_reply") or {}
    blank = (not review["draft_body"] and not work_reply.get("text")
             and (accepted or not has_questions) and template != "acuse")
    if blank:
        return "form", "", True
    own_draft = review["draft_body"] if review["draft_id"] and review["draft_id"] != work_reply.get("draft_id") else ""
    return "form", (own_draft or work_reply.get("text") or review["draft_body"]
                    or suggested_reply(analysis, config["required_fields"])), False


def _conversation_panel(thread_id: str) -> Markup:
    return render("conversation.html", messages=[
        {**m, "when": (m["occurred_at"] or "")[:16].replace("T", " "),
         "fresh": split_quoted(m["body"])[0], "quoted": split_quoted(m["body"])[1]}
        for m in case_conversation(thread_id)])


def _timeline_panel(thread_id: str, review: Mapping[str, Any], analysis: dict, events: list[dict],
                    integrity_ok: bool, event_count: int) -> Markup:
    return render("timeline.html", thread_id=thread_id, integrity_ok=integrity_ok,
                  event_count=event_count, events=[
        {**event, "when": event["occurred_at"][:16].replace("T", " "),
         "origin": SOURCE_LABELS.get(event["source"], event["source"]),
         "who": event["actor_name"] or event["actor_email"],
         "gmail_url": ("https://mail.google.com/mail/u/0/#all/" + event["ref"]
                       if event["kind"] in ("sent", "received", "related") else "")}
        for event in events],
        people=case_people(thread_id, case_contacts(review["recipient"], analysis)))


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
    if review.get("reply_pending"):
        review = replace(review, status_label=REPLY_LABEL)
    analysis = review["analysis"]
    assessment = review.get("reception_assessment") or reception_assessment(analysis)
    config = load_config()
    routing_summary = _routing_summary(analysis)
    scope_issues = unresolved_scope_issues(analysis)
    issues_summary = "\n".join(f"- {issue}" for issue in scope_issues) or "Sin asuntos pendientes."
    technical = assessment.get("status") == "technical_review"
    reopened = review["status"] in (QuoteStatus.READY, QuoteStatus.RESPONSE_SENT) and case_has_links(thread_id)
    email_allowed = (
        valid_email_address(review["recipient"])
        and (review["status"] not in CLOSED_STATUSES or reopened)
        and not technical
    )
    email, email_body, blank_reply = _compose_state(
        review, analysis, config, template, change_id,
        email_allowed=email_allowed, reopened=reopened, technical=technical, recipient_error=recipient_error)
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
        assessment_panel=reception_assessment_panel(assessment),
        client_types=CLIENT_TYPES, sectors=SECTORS,
        internal_context=_internal_context_panel(review["client_name"]),
        activities_editor=activities_editor(thread_id, analysis.get("activities", []), list(EDIT_REASONS.items())),
        attachments_table=attachments_table(analysis.get("attachments", [])),
        routing_summary=routing_summary, issues_summary=issues_summary,
        email=email, email_body=email_body, recipient_error=recipient_error,
        sufficient=assessment.get("status") == "sufficient_for_internal_review",
        drive_pending=(analysis.get("drive") or {}).get("pending", ""),
        can_resolve=review["status"] == QuoteStatus.REVIEW,
        manual_review=_manual_review_note(analysis.get("manual_review")),
        prefill_questions=_suggested_questions(analysis, assessment) if technical else "",
        blank_reply=blank_reply,
        conversation=_conversation_panel(thread_id),
        stepper=_stepper(review),
        followup=_followup_panel(review),
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
        timeline=_timeline_panel(thread_id, review, analysis, events, integrity_ok, event_count),
    )
    return _page("Revisar caso", body, message)


def _acta_versions(thread_id: str, review: Mapping[str, Any] | None) -> list[dict]:
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
    contact = review.get("contact_email", "") if review else ""
    ok, count = chain_status(thread_id)
    code = ""
    stage = review.get("code_stage", "") if review else ""
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


REPLY_LABEL = "Cliente respondió · por analizar"


_QUOTE_START = re.compile(
    r"(?:\n\s*(?:El|On)\s[^\n]{0,200}\n?[^\n]{0,160}?(?:escribi[óo]|wrote)\s*:)"   # «El jue… escribió:»
    r"|(?:\n\s*>)"                                                                    # líneas citadas
    r"|(?:\n\s*-{2,}\s*(?:Original Message|Mensaje original)\s*-{2,})"
    r"|(?:\n\s*De:\s[^\n]+\n\s*(?:Enviado|Fecha)\s*:)", re.IGNORECASE)


def split_quoted(body: str) -> tuple[str, str]:
    """(mensaje nuevo, historial citado). El historial se muestra plegado para no repetir lo ya leído."""
    text = (body or "").replace("\r\n", "\n").replace("▎", "").strip()
    found = _QUOTE_START.search("\n" + text)
    if not found or found.start() <= 1:
        return text, ""
    cut = found.start() - 1
    return text[:cut].strip(), text[cut:].strip()


def _followup_panel(review: Mapping[str, Any]) -> Markup:
    """Seguimiento del cliente: cuánto lleva esperando, recordatorios y qué hacer."""
    since = review.get("waiting_since", "")
    if not since:
        return Markup("")
    follow = review.get("followup", {})
    label, urgent = _case_stage(review)
    contacted = bool(follow.get("contacted_at")) and follow["contacted_at"] >= since
    return render(
        "followup.html", thread_id=review["thread_id"], headline=label, urgent=urgent,
        since=since[:16].replace("T", " "), days=fup.waiting_days(since),
        auto=bool(follow.get("auto_enabled")), paused=bool(follow.get("paused")), contacted=contacted,
        reminders=[ref.split("|")[-1] for ref in review.get("reminders", [])],
        max_reminders=fup.MAX_REMINDERS, call_days=fup.CALL_BUSINESS_DAYS)


def _outgoing_pending(changes: list[dict]) -> bool:
    """¿Hay un cambio confirmado al que aún le faltan correos por enviar?"""
    for change in changes:
        if change["state"] != "change":
            continue
        for purpose, _ in required_messages(bool(change["has_code"]), bool(change["is_contact"])):
            if not (change["contact_notified_at"] if purpose == "notify_contact" else change["reply_sent_at"]):
                return True
    return False


def _case_stage(quote: Mapping[str, Any]) -> tuple[str, bool]:
    """(texto, requiere acción): la etapa del caso o lo que espera de una persona, en una frase."""
    changes = quote.get("changes", [])
    status = quote["status"]
    if any(c["state"] == "pending" for c in changes):
        return "Cambio por revisar", True
    if _outgoing_pending(changes):
        return "Faltan correos por enviar", True
    if quote.get("links"):
        return "Posible actualización", True
    if quote.get("reply_pending"):
        return REPLY_LABEL, True
    if quote.get("waiting_since"):
        days = fup.waiting_days(quote["waiting_since"])
        follow = quote.get("followup", {})
        if fup.call_due(quote["waiting_since"], follow.get("contacted_at", "")):
            return "Llamar al cliente", True
        quiet = follow.get("contacted_at", "") and follow["contacted_at"] >= quote["waiting_since"]
        automatic = follow.get("auto_enabled") and not follow.get("paused")
        if days >= fup.ALERT_BUSINESS_DAYS and not automatic and not quiet:
            return "Sin respuesta · recordar", True
        return ("Esperando al cliente" + (f" · hace {days} d" if days >= 1 else "")), False
    if status == QuoteStatus.REVIEW:
        return "Falta tu decisión técnica", True
    if quote.get("proposal"):
        return "Propuesta del modelo por revisar", True
    code = quote.get("quote_code", "")
    if code:
        if quote.get("code_stage") == "TE":
            return f"{code_label(code, 'TE')} · aprobada", False
        return f"{code_label(code, 'T')} · cotizando", False
    labels = {
        QuoteStatus.REVIEW_PENDING: ("Por aclarar con el cliente", True),
        QuoteStatus.DRAFT_CREATED: ("Borrador por revisar", True),
        QuoteStatus.SENDING: ("Envío interrumpido", True),
        QuoteStatus.MISSING: ("Hilo no disponible", True),
        QuoteStatus.WAITING: ("Esperando al cliente", False),
        QuoteStatus.RESPONSE_SENT: ("Esperando al cliente", False),
        QuoteStatus.READY_REVIEW: ("Listo para emitir la T", True),
        QuoteStatus.READY: ("Listo para emitir la T", True),
        QuoteStatus.IGNORED: ("Descartada", False),
    }
    return labels.get(status, (quote["status_label"], False))
SOURCE_LABELS = {"dashboard": "Dashboard", "gmail": "Gmail", "work": "ChatGPT Work",
                 "python": "Piloto"}


def _persistent_csrf_token() -> str:
    """Token de seguridad que sobrevive a los reinicios del dashboard.

    Antes se generaba en cada arranque, así que una página abierta antes de un reinicio (por
    ejemplo, al cambiar el código) fallaba al enviar con «Token de seguridad inválido».
    """
    path = settings.DATA_DIR / "csrf_token"
    try:
        value = path.read_text(encoding="utf-8").strip()
        if len(value) >= 32:
            return value
    except OSError:
        pass
    value = secrets.token_urlsafe(32)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(value, encoding="utf-8")
    except OSError:
        pass  # sin disco: el token vale solo para este arranque, como antes
    return value


CSRF_TOKEN = _persistent_csrf_token()


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


# --- Acciones de los formularios (POST). Cada una recibe el formulario y devuelve a dónde redirigir.
# Un ValueError (o el SystemExit de una acción de línea de comandos) es orientación para la persona: se muestra
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
    ), SystemExit)
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
        path = urllib.parse.urlparse(self.path).path
        form = self._read_form()
        if not secrets.compare_digest(_field(form, "csrf_token"), CSRF_TOKEN):
            return self._reject("Token de seguridad inválido. Recarga la página e inténtalo de nuevo.")
        action = POST_ROUTES.get(path)
        if action is None:
            return self._send_html(_page("No encontrado", render("empty.html", text="Página no encontrada.")), 404)
        try:
            self._redirect(action(form))
        except (Exception, SystemExit) as exc:
            log.exception("Error atendiendo POST %s", path)
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
