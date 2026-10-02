"""Las páginas del dashboard: cada una arma su HTML a partir de los casos (sin servidor ni formularios).

Aquí viven las pantallas (resumen, solicitudes, caso, acta, descartadas) y los paneles que las componen.
El servidor HTTP, la protección CSRF y las acciones de los formularios están en `server.py` y `seguridad.py`.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import replace
from typing import Any
import json
import os
import re
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime
from markupsafe import Markup
from anka.dominio.errores import ErrorDeAnka
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
from anka.servicios.internal_context import internal_commercial_context
from anka.infra.logging_setup import get_logger
from anka.infra.settings import load_config
from anka.web.templating import render

from anka.servicios.case_actions import resolve_case_recipient
from anka.servicios.pipeline import sync_status_text
from anka.servicios import followup_reminders as fup
from anka.servicios.change_control import (
    STAGE_LABELS, code_label, required_messages, suggest_number,
)
from anka.servicios.gmail_followup import case_contacts
from anka.dominio.registros import Cambio, Evento
from anka.servicios.quotes import case_conversation, case_change_log, chain_status, case_has_links, case_people, reason_label, ago_text, minutes_since, REPLY_OVERDUE_MINUTES, CLIENT_TYPES, discarded_quotes, SECTORS, STATUS_LABELS, all_quotes, get_quote, thread_events, unread_notifications
from anka.servicios.ediciones import EDIT_REASONS, last_manual_version, table_versions

log = get_logger(__name__)


def page(title: str, body: Markup, message: str = "") -> str:
    """Página completa con el encabezado, el selector de modo y las novedades sin leer."""
    try:
        mode = load_config().get("active_mode")
    except (ErrorDeAnka, OSError, ValueError):
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


def render_index(message: str = "") -> str:
    """Resumen: «qué hacer hoy», novedades y las métricas de las solicitudes."""
    quotes = all_quotes()
    notifications = unread_notifications(8)
    unread_threads = {item["thread_id"] for item in notifications}
    services = Counter(service for quote in quotes for service in quote["services"])
    clients = Counter(q["client_name"] for q in quotes if q["client_name"])
    stages = {q["thread_id"]: case_stage(q) for q in quotes}
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
        todo=[quote_row(q, q["thread_id"] in unread_threads) for q in todo_cases],
    )
    return page("Resumen comercial", body, message)


def quote_row(quote: Mapping[str, Any], is_new: bool = False, selectable: bool = False) -> Markup:
    """Fila de la lista de solicitudes: proyecto, cliente, etapa y siguiente paso."""
    project = quote["analysis"].get("project_name") or "Solicitud sin nombre"
    services = ", ".join(quote["services"]) or "Servicio por clasificar"
    return render(
        "quote_row.html",
        project=project, services=services, is_new=is_new, selectable=selectable,
        search=(project + " " + quote["client_name"] + " " + services).casefold(),
        status=quote["status"], client_type=quote["client_type"],
        client_name=quote["client_name"], sector=quote["sector"],
        badge_class="warn" if case_stage(quote)[1] else "info",
        status_label=case_stage(quote)[0],
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


def reply_template(kind: str, review: Mapping[str, Any], change: Cambio | None = None) -> str:
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
    label, urgent = case_stage(review)
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


def render_requests(message: str = "") -> str:
    """Lista de todas las solicitudes, las urgentes primero."""
    quotes = all_quotes()
    unread_threads = {item["thread_id"] for item in unread_notifications(100)}
    body = render(
        "requests.html",
        rows=[quote_row(quote, quote["thread_id"] in unread_threads, selectable=True)
              for quote in sorted(quotes, key=lambda q: not case_stage(q)[1])],  # primero lo que pide acción
        status_options=list(STATUS_LABELS.items()),
        type_options=CLIENT_TYPES,
    )
    return page("Solicitudes", body, message)


def render_discarded(message: str = "") -> str:
    """Hilos descartados con su motivo y el botón de rescatarlos."""
    body = render("discarded.html", rows=discarded_quotes())
    return page("Descartadas", body, message)


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
        return "form", reply_template(template, review, change), False
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


def _timeline_panel(thread_id: str, review: Mapping[str, Any], analysis: dict, events: Sequence[Evento],
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


def render_case(thread_id: str, message: str = "", template: str = "", change_id: int = 0,
                 to_kind: str = "") -> str:
    """Pantalla de un caso: etapa, siguiente paso, tabla, conversación, respuesta y seguimiento."""
    review = get_quote(thread_id)
    if not review:
        return page("Caso no encontrado", render("empty.html", text="La solicitud no existe."), message)
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
    return page("Revisar caso", body, message)


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


def render_acta(thread_id: str) -> str:
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
        return page("Caso no encontrado", render("empty.html", text="No hay historial para ese caso."))
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
    return page("Acta de cambios", body)


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
    label, urgent = case_stage(review)
    contacted = bool(follow.get("contacted_at")) and follow["contacted_at"] >= since
    return render(
        "followup.html", thread_id=review["thread_id"], headline=label, urgent=urgent,
        since=since[:16].replace("T", " "), days=fup.waiting_days(since),
        auto=bool(follow.get("auto_enabled")), paused=bool(follow.get("paused")), contacted=contacted,
        reminders=[ref.split("|")[-1] for ref in review.get("reminders", [])],
        max_reminders=fup.MAX_REMINDERS, call_days=fup.CALL_BUSINESS_DAYS)


def _outgoing_pending(changes: Sequence[Cambio]) -> bool:
    """¿Hay un cambio confirmado al que aún le faltan correos por enviar?"""
    for change in changes:
        if change["state"] != "change":
            continue
        for purpose, _ in required_messages(bool(change["has_code"]), bool(change["is_contact"])):
            if not (change["contact_notified_at"] if purpose == "notify_contact" else change["reply_sent_at"]):
                return True
    return False


def case_stage(quote: Mapping[str, Any]) -> tuple[str, bool]:
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
