"""La pantalla de un caso y su acta: etapa, siguiente paso, tabla, conversación, respuesta y seguimiento.

Las demás pantallas (resumen, solicitudes, descartadas) y la página común están en `paginas.py`;
este módulo depende de él, nunca al revés.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import replace
from typing import Any, NamedTuple
import json
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime
from markupsafe import Markup
from anka.dominio.configuracion import Configuracion
from anka.dominio.caso import Caso
from anka.dominio.analisis import Analisis, RevisionManual
from anka.web.views import (
    attachments_table, reception_assessment_panel, activities_editor, question_audit_panel,
    question_quality_panel, review_tasks_panel,
)
from anka.dominio.status import (
    QuoteStatus,
)
from anka.dominio.correo import (
    normalized_address, reply_body_without_quoted_history, split_quoted_reply, valid_email_address,
)
from anka.dominio.preguntas import candidatas, con_decisiones, elegida, separar
from anka.dominio.reception import (
    correo_con_preguntas, reception_assessment, reception_questions, suggested_reply, unresolved_scope_issues,
)
from anka.servicios.internal_context import internal_commercial_context
from anka.infra.logging_setup import get_logger
from anka.infra.settings import load_config
from anka.web.templating import render

from anka.servicios.case_actions import resolve_case_recipient
from anka.servicios import followup_reminders as fup
from anka.servicios.change_control import (
    STAGE_LABELS, code_label, required_messages, suggest_number,
)
from anka.servicios.gmail_followup import case_contacts
from anka.dominio.registros import Cambio, Evento
from anka.servicios.proyectos_repetidos import agrupar, motivo_entre
from anka.servicios.quotes import (
    ago_text, all_quotes, case_change_log, case_conversation, case_has_links, case_people, chain_status, get_quote,
    merge_blocker, minutes_since, reason_label, separated_project_pairs, thread_events,
)
from anka.dominio.caso import CLIENT_TYPES, REPLY_OVERDUE_MINUTES, SECTORS
from anka.servicios.ediciones import EDIT_REASONS, last_manual_version, table_versions

from anka.web.paginas import REPLY_LABEL, case_stage, etiqueta_de_recepcion, page

log = get_logger(__name__)


def _edit_note(thread_id: str) -> Markup:
    """Una línea discreta bajo la tabla: tu última edición y el aviso opcional al cliente."""
    latest = last_manual_version(thread_id)
    if not latest:
        return Markup("")
    return render("edit_note.html", version=latest["version"], thread_id=thread_id,
                  when=latest["created_at"][:16].replace("T", " "), summary=latest["summary"],
                  notified=latest["notified"])


_ORIGEN_DE_PREGUNTA = {"auditoría": "Auditoría de preguntas", "recepción": "Evaluación de recepción",
                       "análisis": "Pendientes del análisis", "persona": "Escrita por una persona"}
_SUGERENCIA = {"ask_client": "preguntar al cliente", "internal": "resolver internamente", "discard": "descartar"}


def _preguntas_del_caso(analysis: Analisis, assessment: dict) -> list[dict[str, Any]]:
    """Las preguntas candidatas para los selectores: texto, qué sugirió el análisis y qué está elegido (U-22)."""
    return [
        {**pregunta, "elegida": elegida(pregunta), "sugerencia": _SUGERENCIA[pregunta["sugerida"]],
         "origen_texto": _ORIGEN_DE_PREGUNTA.get(pregunta["origen"], pregunta["origen"]),
         "decidida": bool(pregunta.get("decision"))}
        for pregunta in con_decisiones(candidatas(analysis, assessment), analysis.get("preguntas"))
    ]


def _link_banner(review: Mapping[str, Any]) -> Markup:
    links = review["links"]
    contacts = case_contacts(review["recipient"], review["analysis"])
    items = [{
        "link": link,
        "who": link["sender_name"] or link["sender_email"],
        "reason": reason_label(link["reason"]),
        "show_verify": link["reason"].startswith("domain") or link["sender_email"] not in contacts,
    } for link in links]
    return render("link_banner.html", links=items, thread_id=review["thread_id"])


class _ReplyData(NamedTuple):
    """Lo que comparten los textos de los correos de un caso."""

    project: str
    code: str
    contact: str
    link: Mapping[str, Any] | None


def received_email_text(project: str, code: str, contact: str) -> str:
    """Correo de «proyecto recibido»: confirma que se tiene toda la información, da el código y explica cómo sigue."""
    return (
        f"Hola,\n\nRecibimos toda la información de su proyecto «{project}» y ya está en proceso de cotización. "
        f"Hemos generado el código {code}.\n\n"
        "Cómo continúa el proceso:\n"
        "1. Revisaremos la información que nos compartió y le enviaremos la cotización por este mismo hilo.\n"
        f"2. Para cualquier cambio o solicitud sobre este proyecto, cite siempre este código ({code}); "
        "las solicitudes que no lo incluyan no podrán procesarse.\n"
        f"3. Le recomendamos escribir siempre desde el correo registrado ({contact}) o desde el dominio de su "
        "empresa. Si otra persona de su organización necesita pedir un cambio, el contacto registrado deberá "
        "confirmarlo.\n\nSaludos,\nGrupo Anka"
    )


def _tpl_received(review: Mapping[str, Any], d: _ReplyData, change: Cambio | None) -> str | None:
    """Correo de «proyecto recibido» con la T ya emitida (solo si ya hay código)."""
    if not d.code:
        return None
    return received_email_text(d.project, d.code, d.contact)


def _tpl_acuse(review: Mapping[str, Any], d: _ReplyData, change: Cambio | None) -> str | None:
    """Acuse sugerido cuando no hay nada que preguntar."""
    return (
        "Hola,\n\nGracias por su solicitud. Confirmamos que la hemos recibido y estamos revisando "
        "la información compartida para definir el alcance. Si necesitamos alguna aclaración "
        "adicional, nos comunicaremos por este mismo hilo.\n\nSaludos,\nGrupo Anka"
    )


def _tpl_scope_update(review: Mapping[str, Any], d: _ReplyData, change: Cambio | None) -> str | None:
    """Aviso al cliente de que se actualizó la tabla de actividades."""
    latest = last_manual_version(review["thread_id"])
    what = f" ({latest['summary']})" if latest else ""
    return (
        f"Hola,\n\nActualizamos la tabla de actividades de su proyecto «{d.project}»"
        f"{f' ({d.code})' if d.code else ''}{what}.\n\nSi algo no coincide con lo que usted solicitó, "
        "por favor indíquelo respondiendo a este correo.\n\nSaludos,\nGrupo Anka"
    )


def _tpl_ack(review: Mapping[str, Any], d: _ReplyData, change: Cambio | None) -> str | None:
    """Confirmación de que se recibió una solicitud de cambio."""
    if not change:
        return None
    return (
        f"Hola,\n\nConfirmamos que recibimos su solicitud de cambio sobre el proyecto «{d.project}»"
        f"{f' ({d.code})' if d.code else ''}: «{change['snippet']}».\n\nLa revisaremos y les "
        "confirmaremos cómo queda el alcance.\n\nSaludos,\nGrupo Anka"
    )


def _tpl_ask_code(review: Mapping[str, Any], d: _ReplyData, change: Cambio | None) -> str | None:
    """Pide citar el código de la T para poder procesar un cambio."""
    if not change:
        return None
    return (
        f"Hola,\n\nRecibimos su mensaje sobre el proyecto «{d.project}». Para poder procesar "
        "cualquier cambio necesitamos que cite el código del proyecto, que se informó al "
        "contacto registrado. Por favor envíe de nuevo su solicitud incluyéndolo.\n\n"
        "Por seguridad, los cambios deben provenir del correo registrado o del dominio de su "
        "empresa, o ser confirmados por el contacto del proyecto.\n\nSaludos,\nGrupo Anka"
    )


def _tpl_notify_contact(review: Mapping[str, Any], d: _ReplyData, change: Cambio | None) -> str | None:
    """Avisa al contacto registrado de que otra persona pidió un cambio."""
    if not change:
        return None
    who = change["sender_name"] or change["sender_email"]
    cites = "cita" if change["has_code"] else "no cita"
    return (
        f"Hola,\n\nLe informamos que {who} ({change['sender_email']}) escribió sobre el proyecto "
        f"«{d.project}» solicitando: «{change['snippet']}». El mensaje {cites} el código del proyecto."
        "\n\nPara dejar constancia, ¿puede confirmarnos que está autorizado(a) para solicitar "
        "cambios y que lo indicado es correcto? Le recomendamos que las solicitudes se envíen "
        "siempre desde el correo registrado o desde el dominio de su empresa."
        "\n\nSaludos,\nGrupo Anka"
    )


def _tpl_verify(review: Mapping[str, Any], d: _ReplyData, change: Cambio | None) -> str | None:
    """Pide confirmar que quien escribió desde otro hilo está autorizado."""
    link = d.link
    if not link:
        return None
    who = link["sender_name"] or link["sender_email"]
    return (
        f"Hola,\n\nRecibimos un correo de {who} ({link['sender_email']}) con una modificación "
        f"sobre el proyecto {d.project}: «{link['subject']}».\n\nPara dejar constancia, ¿pueden "
        f"confirmarnos que {who} está autorizado(a) para solicitar cambios en esta solicitud y "
        "que lo indicado es correcto?\n\nSaludos,\nGrupo Anka"
    )


def _tpl_te_approved(review: Mapping[str, Any], d: _ReplyData, change: Cambio | None) -> str | None:
    """Confirma al cliente que la oferta fue aprobada y quedó como T&E (solo si el caso ya es T&E)."""
    if review.get("code_stage") != "TE" or not d.code:
        return None
    return (
        f"Hola,\n\nLe confirmamos que su oferta para el proyecto «{d.project}» fue aprobada y quedó "
        f"registrada con el código {d.code}.\n\nPara cualquier cambio o solicitud sobre este proyecto, "
        f"por favor cite este código ({d.code}) y escriba desde el correo registrado ({d.contact}) o "
        "desde el dominio de su empresa.\n\nSaludos,\nGrupo Anka"
    )


def _tpl_default(d: _ReplyData) -> str:
    """Confirmación de recibido genérica (también cuando la plantilla pedida no aplica al caso)."""
    subject = f" «{d.link['subject']}»" if d.link else ""
    return (
        f"Hola,\n\nConfirmamos que recibimos su correo{subject} sobre el proyecto {d.project}. "
        "Lo revisaremos junto con la solicitud y les confirmaremos cómo queda el alcance."
        "\n\nSaludos,\nGrupo Anka"
    )


_REPLY_TEMPLATES = {
    "received": _tpl_received, "acuse": _tpl_acuse, "scope_update": _tpl_scope_update, "ack": _tpl_ack,
    "ask_code": _tpl_ask_code, "notify_contact": _tpl_notify_contact, "verify": _tpl_verify,
    "te_aprobada": _tpl_te_approved,
}


def reply_template(kind: str, review: Mapping[str, Any], change: Cambio | None = None) -> str:
    """Texto base de cada correo del flujo (siempre se revisa antes de enviar).

    Los correos a quien no es el contacto registrado nunca incluyen el código del proyecto.
    """
    quote_code = review.get("quote_code", "")
    data = _ReplyData(
        project=review["analysis"].get("project_name") or "su solicitud",
        code=code_label(quote_code, review.get("code_stage", "T")) if quote_code else "",
        contact=review.get("contact_email") or review.get("recipient", ""),
        link=(review.get("links") or [None])[0],
    )
    build = _REPLY_TEMPLATES.get(kind)
    text = build(review, data, change) if build else None
    return text if text is not None else _tpl_default(data)


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


def _stepper(review: Caso) -> Markup:
    """Recorrido del caso: dónde está y qué sigue, de un vistazo."""
    code = review.get("quote_code", "")
    stage = review.get("code_stage", "")
    validated = review.informacion_validada
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


def _code_panel(review: Caso) -> Markup:
    code = review.get("quote_code", "")
    stage = review.get("code_stage", "")
    can_assign = (review["status"] not in (QuoteStatus.IGNORED, QuoteStatus.MISSING)
                  and review.informacion_validada)
    if not code and not can_assign:
        return Markup("")
    suggestion = suggest_number() if not code else ""
    contact = review.get("contact_email") or review["recipient"]
    project = review["analysis"].get("project_name") or "su solicitud"
    return render(
        "code_panel.html", code=code_label(code, stage or "T") if code else "", stage=stage,
        stage_label=STAGE_LABELS.get(stage, ""), thread_id=review["thread_id"],
        approved_code=code_label(code, "TE") if code else "", can_assign=can_assign and not code,
        suggestion=suggestion, contact=contact,
        received_text=received_email_text(project, code_label(suggestion, "T"), contact) if suggestion else "")


def _project_panel(thread_id: str) -> Markup:
    """«Parece el mismo proyecto»: los hilos parecidos a este, con «Unir a este caso» y «No es el mismo proyecto»."""
    quotes = all_quotes()
    mine = next((q for q in quotes if q["thread_id"] == thread_id), None)
    group = next((g for g in agrupar(quotes, separated_project_pairs())
                  if any(q["thread_id"] == thread_id for q in g.miembros)), None)
    if mine is None or group is None or len(group.miembros) < 2:
        return Markup("")
    candidates = []
    for other in group.miembros:
        if other["thread_id"] == thread_id:
            continue
        reason = motivo_entre(mine, other) or group.motivo
        candidates.append({
            "thread_id": other["thread_id"], "project": other["analysis"].get("project_name") or "Solicitud sin nombre",
            "who": other["client_name"] or other["recipient"], "when": etiqueta_de_recepcion(other),
            "stage": case_stage(other)[0], "reason": reason, "verify": reason == "nombre parecido",
            "blocker": merge_blocker(other)})
    return render("project_suggestion.html", thread_id=thread_id, candidates=candidates)


def _next_step_banner(review: Caso, work_mode: bool) -> Markup:
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
    if review["status"] in (QuoteStatus.REVIEW_PENDING, QuoteStatus.DRAFT_CREATED):
        return render(
            "next_step.html", tone="info", title="Revisa el correo y envíalo",
            text="Nada sale al cliente hasta que pulses «Enviar ahora».",
            action_href="#respuesta", action_label="Ir al correo")
    if review["status"] == QuoteStatus.REVIEW:
        return render(
            "next_step.html", tone="warn", title="Falta tu decisión de revisión técnica",
            text="Decide si la información es suficiente o si hay que preguntar al cliente.",
            action_href="#decision", action_label="Decidir")
    if review.informacion_validada and not review.get("quote_code") and review["status"] != QuoteStatus.IGNORED:
        return render(
            "next_step.html", tone="info", title="Información validada: puedes emitir la T",
            text="Damos por recibido el proyecto y empezamos a cotizar.",
            action_href="#emitir-t", action_label="Emitir la T")
    code = review.get("quote_code", "")
    if code:
        stage = review.get("code_stage") or "T"
        title = f"{code_label(code, stage)} · " + ("oferta aprobada" if stage == "TE" else "cotizando")
        folder = review.get("folder_id")
        if folder:
            return render(
                "next_step.html", tone="info", title=title,
                text="La T está emitida y la carpeta del proyecto ya existe en Drive.",
                action_href="https://drive.google.com/drive/folders/" + urllib.parse.quote(folder),
                action_label="Abrir carpeta en Drive")
        return render(
            "next_step.html", tone="warn", title=title,
            text="La T está emitida, pero la carpeta del proyecto aún no existe en Drive.",
            action_href="#carpeta", action_label="Ir a la carpeta")
    if label.startswith("Esperando al cliente"):
        return render(
            "next_step.html", tone="info", title="Esperando al cliente",
            text=f"{label}. Si no responde, el seguimiento prepara un recordatorio que tú revisas antes de enviarlo.",
            action_href="#seguimiento", action_label="Ver seguimiento")
    return Markup("")


def _manual_review_note(manual: RevisionManual | None) -> str:
    if not manual:
        return ""
    when = (manual.get("at") or "")[:16].replace("T", " ")
    action = ("la marcó como información suficiente" if manual.get("decision") == "sufficient"
              else "decidió preguntar al cliente")
    return f"Una persona revisó este caso y {action} ({when})."


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


def _routing_summary(analysis: Analisis) -> str:
    """Cómo llegó el correo: directo o reenviado por alguien interno."""
    routing = analysis.get("routing", {})
    if not routing.get("is_forwarded"):
        return "Correo directo"
    return (
        f"Reenvío interno: {routing.get('internal_forwarder') or 'no indicado'}\n"
        f"Cliente original: {routing.get('original_sender') or 'no identificado'}\n"
        f"Destinatario seleccionado: {routing.get('selected_recipient') or 'no indicado'}"
    )


_FREE_FORM_TEMPLATES = ("received", "ack", "ask_code", "notify_contact", "verify", "scope_update", "acuse",
                        "te_aprobada")


def _puede_volver_atras(review: Caso, analysis: Analisis) -> bool:
    """¿Sigue sin salir el correo de la decisión de preguntar? Entonces se puede cambiar de idea (U-27)."""
    decision = analysis.get("manual_review") or {}
    if review["status"] not in (QuoteStatus.REVIEW_PENDING, QuoteStatus.DRAFT_CREATED):
        return False
    if decision.get("decision") != "ask_client":
        return False
    return not (review.get("sent_at") and str(review["sent_at"]) >= str(decision.get("at") or ""))


def _codigo_para_el_contacto(review: Caso) -> str:
    """El código de la oferta para citarlo en el correo, o '' si no hay T o el destinatario no es el contacto registrado
    (R11: a quien no es el contacto nunca se le incluye el código)."""
    numero = review.get("quote_code", "")
    if not numero:
        return ""
    contacto = review.get("contact_email") or review["recipient"]
    if normalized_address(contacto) != normalized_address(review["recipient"]):
        return ""
    return code_label(numero, review.get("code_stage", "T"))


def _compose_state(review: Caso, analysis: Analisis, config: Configuracion, template: str, change_id: int, *,
                   email_allowed: bool, reopened: bool, technical: bool, recipient_error: str,
                   preguntas_escritas: Sequence[str] = ()) -> tuple[str, str, bool]:
    """Qué muestra el cuadro de respuesta: (modo, texto inicial, ¿en blanco?)."""
    if email_allowed and review.espera_al_cliente and not template:
        return "waiting", "", False   # ya se escribió: no se ofrece enviar lo mismo otra vez
    if not email_allowed:
        if review["status"] == QuoteStatus.RESPONSE_SENT:
            return "sent", "", False
        if recipient_error:
            return "recipient_error", "", False
        return ("technical" if technical else "unavailable"), "", False
    if template == "preguntas":      # U-24: el correo con las preguntas que la persona escribió, en cualquier etapa
        if not preguntas_escritas:
            return "form", "", True
        reenviado = bool(analysis.get("routing", {}).get("is_forwarded"))
        codigo = _codigo_para_el_contacto(review)
        proyecto = analysis.get("project_name") or ""
        correo = correo_con_preguntas(list(preguntas_escritas), reenviado, proyecto=proyecto, codigo=codigo)
        return "form", correo, False
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
    # Las preguntas que una persona eligió (U-22) mandan sobre el borrador de Work: si no, el correo saldría sin ellas.
    elegidas = bool(manual.get("decision") == "ask_client" and manual.get("questions"))
    body = (own_draft or (suggested_reply(analysis, config["required_fields"]) if elegidas else "")
            or work_reply.get("text") or review["draft_body"]
            or suggested_reply(analysis, config["required_fields"]))
    return "form", reply_body_without_quoted_history(body), False


def _conversation_panel(thread_id: str) -> Markup:
    return render("conversation.html", messages=[
        {**m, "when": (m["occurred_at"] or "")[:16].replace("T", " "),
         "fresh": split_quoted(m["body"])[0], "quoted": split_quoted(m["body"])[1]}
        for m in case_conversation(thread_id)])


def _timeline_panel(thread_id: str, review: Mapping[str, Any], analysis: Analisis, events: Sequence[Evento],
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


def _load_review(thread_id: str) -> tuple[Caso | None, str]:
    """El caso de la pantalla y, si no se pudo confirmar el destinatario en Gmail, el aviso que lo bloquea."""
    review = get_quote(thread_id)
    if not review:
        return None, ""
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
    return review, recipient_error


def _compose_to(review: Mapping[str, Any], change: Cambio | None, to_kind: str) -> str:
    """Destinatario que se propone al responder por un cambio: quien lo escribió o el contacto registrado."""
    if change and to_kind == "sender":
        return change["sender_email"]
    if change and to_kind == "contact":
        return review.get("contact_email") or review["recipient"]
    return ""


def _code_text(review: Mapping[str, Any]) -> str:
    """«T26145 · En cotización» (o «T&E…· Aprobada»); vacío mientras no haya código."""
    if not review.get("quote_code"):
        return ""
    stage = review.get("code_stage") or "T"
    return code_label(review["quote_code"], stage) + " · " + STAGE_LABELS.get(stage, "")


def _case_panels(thread_id: str, review: Caso, analysis: Analisis, assessment: dict, config: Configuracion,
                 events: list[Evento], integrity_ok: bool, event_count: int,
                 questions_in_decision: bool = False) -> dict[str, Any]:
    """Los paneles de la pantalla del caso, cada uno ya renderizado."""
    proposal = review.get("proposal", [])
    return {
        "assessment_panel": reception_assessment_panel(assessment),
        "internal_context": _internal_context_panel(review["client_name"]),
        "activities_editor": activities_editor(
            thread_id, analysis.get("activities", []), list(EDIT_REASONS.items()),
            hoja={"url": review.sheet_url, "error": review.sheet_error, "tiene_carpeta": bool(review.folder_id),
                  "actualizada": review.sheet_synced_at[:16].replace("T", " ")},
            ultima=last_manual_version(thread_id)),
        "attachments_table": attachments_table(analysis.get("attachments", [])),
        "review_tasks": review_tasks_panel(
            analysis.get("question_audit", []),
            other_questions=analysis.get("other_questions", []),
            attachments=analysis.get("attachments", []),
            dependencies=analysis.get("scope_dependencies", []),
            contradictions=analysis.get("contradictions", []),
            waiting_for_client=review["status"] == QuoteStatus.RESPONSE_SENT,
            questions_in_decision=questions_in_decision,
            decisiones=analysis.get("preguntas") or (),
        ),
        "question_audit": question_audit_panel(analysis.get("question_audit", [])),
        "question_quality": question_quality_panel(analysis.get("question_quality_warnings", [])),
        "manual_review": _manual_review_note(analysis.get("manual_review")),
        "conversation": _conversation_panel(thread_id),
        "stepper": _stepper(review),
        "followup": _followup_panel(review),
        "edit_note": _edit_note(thread_id),
        "code_panel": _code_panel(review),
        "code_text": _code_text(review),
        "next_step": _next_step_banner(review, config.get("active_mode") == "chatgpt_work"),
        "project_panel": _project_panel(thread_id),
        "proposal_panel": render("proposal.html", changes=proposal, thread_id=thread_id) if proposal else Markup(""),
        "timeline": _timeline_panel(thread_id, review, analysis, events, integrity_ok, event_count),
    }


def render_case(thread_id: str, message: str = "", template: str = "", change_id: int = 0,
                to_kind: str = "", preguntas_texto: str = "") -> str:
    """Pantalla de un caso: etapa, siguiente paso, tabla, conversación, respuesta y seguimiento."""
    review, recipient_error = _load_review(thread_id)
    if not review:
        return page("Caso no encontrado", render("empty.html", text="La solicitud no existe."), message)
    analysis = review["analysis"]
    assessment = review.get("reception_assessment") or reception_assessment(analysis)
    config = load_config()
    issues_summary = "\n".join(f"- {issue}" for issue in unresolved_scope_issues(analysis)) or "Sin asuntos pendientes."
    technical = assessment.get("status") == "technical_review"
    reopened = review.reabierto(case_has_links(thread_id))
    email_allowed = review.admite_correo(reabierto=reopened, revision_tecnica=technical)
    email, email_body, blank_reply = _compose_state(
        review, analysis, config, template, change_id,
        email_allowed=email_allowed, reopened=reopened, technical=technical, recipient_error=recipient_error,
        preguntas_escritas=separar(preguntas_texto) if template == "preguntas" else ())
    change = next((c for c in review.get("changes", []) if c["id"] == change_id), None)
    events = thread_events(thread_id)
    integrity_ok, event_count = chain_status(thread_id)
    # «Enviada» se cumple venga el envío del dashboard, de Gmail o de Work.
    sent_complete = review["status"] == QuoteStatus.RESPONSE_SENT or any(
        event["kind"] == "sent" for event in events)
    preguntas = _preguntas_del_caso(analysis, assessment) if technical else []
    body = render(
        "case.html",
        review=review, thread_id=thread_id,
        project_name=analysis.get("project_name") or "Solicitud sin nombre",
        summary=analysis.get("summary") or "Sin resumen disponible",
        service_family=assessment.get("service_family", "Por clasificar"),
        suggestion_complete=review["status"] not in {QuoteStatus.IGNORED, QuoteStatus.MISSING},
        draft_complete=bool(review["draft_id"] or sent_complete),
        plegar_respuesta=bool(review.get("quote_code")) or (
            review.informacion_validada and review["status"] not in {QuoteStatus.IGNORED, QuoteStatus.MISSING}),
        sent_complete=sent_complete, no_reply_needed=review["status"] in {QuoteStatus.READY, QuoteStatus.IGNORED},
        client_types=CLIENT_TYPES, sectors=SECTORS,
        routing_summary=_routing_summary(analysis), issues_summary=issues_summary,
        armar_preguntas=preguntas_texto if template == "preguntas" else "",
        email=email, email_body=email_body, recipient_error=recipient_error, blank_reply=blank_reply,
        sufficient=assessment.get("status") == "sufficient_for_internal_review",
        drive_pending=(analysis.get("drive") or {}).get("pending", ""),
        can_resolve=review["status"] == QuoteStatus.REVIEW,
        puede_volver=_puede_volver_atras(review, analysis),
        preguntas=preguntas, preguntas_enviadas=(analysis.get("preguntas_enviadas") or {}).get("preguntas"),
        compose_to=_compose_to(review, change, to_kind), compose_change_id=change_id if change else 0,
        compose_purpose=(template if change and template in ("ack", "ask_code", "notify_contact")
                         else "scope_update" if template == "scope_update" else ""),
        **_case_panels(thread_id, review, analysis, assessment, config, events, integrity_ok, event_count,
                       questions_in_decision=bool(preguntas) and review["status"] == QuoteStatus.REVIEW),
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
        code=code, contact=contact, stage_label=STAGE_LABELS.get(stage, ""),
        generated=datetime.now().strftime("%Y-%m-%d %H:%M"),
        integrity_ok=ok, event_count=count, events=events, changes=case_change_log(thread_id),
        people=case_people(thread_id, {contact}) if contact else case_people(thread_id, set()),
        versions=_acta_versions(thread_id, review),
        back=("/case?thread_id=" + urllib.parse.quote(thread_id)) if review else "/archive")
    return page("Acta de cambios", body)


def split_quoted(body: str) -> tuple[str, str]:
    """(mensaje nuevo, historial citado). El historial se muestra plegado para no repetir lo ya leído."""
    return split_quoted_reply(body)


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


SOURCE_LABELS = {"dashboard": "Dashboard", "gmail": "Gmail", "work": "ChatGPT Work",
                 "python": "Piloto"}
