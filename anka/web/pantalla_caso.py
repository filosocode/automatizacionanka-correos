"""La pantalla de un caso y su acta: etapa, siguiente paso, tabla, conversación, respuesta y seguimiento.

Las demás pantallas (resumen, solicitudes, descartadas) y la página común están en `paginas.py`;
este módulo depende de él, nunca al revés.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import replace
from typing import Any, NamedTuple
import json
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime
from markupsafe import Markup
from anka.dominio.configuracion import Configuracion
from anka.dominio.caso import Caso, timestamp
from anka.dominio.analisis import Analisis, RevisionManual
from anka.dominio.activities import huella_de_tabla, normalize_estado, parse_activities_form
from anka.web import formularios_pendientes as pendientes
from anka.dominio.work_contract import lectura_sin_verificar
from anka.web.views import (
    adjunto_no_se_pudo_leer, adjunto_requiere_atencion, attachments_panel, reception_assessment_panel,
    activities_editor, question_audit_panel, question_quality_panel, review_tasks_panel,
)
from anka.dominio.status import (
    QuoteStatus,
)
from anka.dominio.correo import (
    normalized_address, reply_body_without_quoted_history, split_quoted_reply, valid_email_address,
)
from anka.dominio.preguntas import candidatas, como_peticion, con_decisiones, elegida, en_el_correo, separar
from anka.dominio.reception import (
    correo_con_preguntas, reception_assessment, reception_questions, suggested_reply, unresolved_scope_issues,
)
from anka.servicios.internal_context import internal_commercial_context
from anka.infra.logging_setup import get_logger
from anka.infra.settings import load_config
from anka.web.templating import render

from anka.servicios.case_actions import con_codigo_final, resolve_case_recipient
from anka.servicios import followup_reminders as fup
from anka.servicios.change_control import (
    STAGE_LABELS, code_label, origen_de_destinatario, origen_en_texto, required_messages, suggest_number,
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


def _preguntas_sugeridas(analysis: Analisis, assessment: dict) -> list[str]:
    """Las preguntas que el análisis sugiere hacerle al cliente, mientras la persona no haya decidido nada (U-30)."""
    if analysis.get("manual_review") or analysis.get("preguntas"):
        return []
    return [p["texto"] for p in _preguntas_del_caso(analysis, assessment) if p["elegida"] == "ask_client"]


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


def _stepper(review: Caso, ronda: int = 1) -> Markup:
    """Recorrido del caso: dónde está y qué sigue, de un vistazo."""
    code = review.get("quote_code", "")
    stage = review.get("code_stage", "")
    validated = review.informacion_validada
    current = 4 if stage == "TE" else 3 if code else 2 if validated else 1
    current_notes = {
        1: review["status_label"] + ("" if review["status"] in (QuoteStatus.IGNORED, QuoteStatus.MISSING)
                                     else f" · ronda {ronda}"), 2: "Listo para emitir la T",
        3: code_label(code, "T") + " · cotizando" if code else "",
        4: code_label(code, "TE") + " · aprobada" if code else "",
    }
    titles = {1: "Revisar y aclarar", 2: "Información validada", 3: "T emitida", 4: "T&E aprobada"}
    steps = [{"title": titles[n],
              "note": "Completado" if n < current else current_notes[n] if n == current else "Pendiente",
              "state": "done" if n < current else "current" if n == current else ""}
             for n in range(1, 5)]
    return render("stepper.html", steps=steps, current=current)


def _datos_comerciales(review: Caso) -> Markup:
    """El formulario de cliente, tipo, sector y notas («Información comercial» o «Corregir el cliente»)."""
    return render("datos_comerciales.html", review=review, thread_id=review["thread_id"],
                  client_types=CLIENT_TYPES, sectors=SECTORS)


def _code_panel(review: Caso, escrito: Mapping[str, list[str]] | None = None,
                con_cliente: bool = False) -> Markup:
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
        suggestion=suggestion, contact=contact, anio_actual=datetime.now().strftime("%y"),
        numero_escrito=(escrito or {}).get("number", [""])[0],
        corregir_cliente=_datos_comerciales(review) if con_cliente and can_assign and not code else Markup(""),
        received_text=((escrito or {}).get("body", [""])[0]
                       or (received_email_text(project, code_label(suggestion, "T"), contact) if suggestion else "")))


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
        sent_questions = other["analysis"].get("preguntas_enviadas") or {}
        candidates.append({
            "thread_id": other["thread_id"], "project": other["analysis"].get("project_name") or "Solicitud sin nombre",
            "who": other["client_name"] or other["recipient"], "when": etiqueta_de_recepcion(other),
            "stage": case_stage(other)[0], "reason": reason, "verify": reason == "nombre parecido",
            "blocker": merge_blocker(other),
            "previous_questions": sent_questions.get("preguntas") or [],
            "questions_sent_at": sent_questions.get("en", "")})
    return render("project_suggestion.html", thread_id=thread_id, candidates=candidates)


_GMAIL_HILO = "https://mail.google.com/mail/u/0/#all/"


def _avisos(review: Caso, work_mode: bool, hay_correo: bool = True, oculto: bool = False) -> tuple[Markup, Markup]:
    """Los avisos de arriba del caso: una sola tarjeta principal y, plegado, «y además: N avisos» (U-40).

    Orden de prioridad (también en `spec/04-ux.md`): 1) cambio pendiente de clasificar, 2) correos que faltan por
    enviar de un cambio, 3) conversaciones vinculadas por verificar, 4) el aviso del estado del caso (respuesta del
    cliente, propuesta de Work, llamada, recordatorio, correo, decisión, T, carpeta, espera). Los tres primeros
    siempre se ven; el del estado se oculta cuando la tarjeta «Ahora» del caso ya dice lo mismo (`oculto`).
    `hay_correo` es si la pantalla dibuja el cuadro «Respuesta al cliente» (`#respuesta`): en revisión técnica
    interna no se dibuja y «Ir al correo» no llevaría a ninguna parte.
    """
    siempre: list[Markup] = []
    if any(c["state"] == "pending" for c in review.get("changes", [])):
        siempre.append(_change_banner(review))
    todo = _change_todo(review)
    if todo:
        siempre.append(todo)
    if review.get("links"):
        siempre.append(_link_banner(review))
    estado = _aviso_del_estado(review, work_mode, hay_correo, oculto)
    if not siempre:
        return estado, Markup("")
    mas = siempre[1:] + ([] if oculto else [estado])
    return siempre[0], _y_ademas(mas)


def _y_ademas(avisos: Sequence[Markup]) -> Markup:
    """«y además: 2 avisos»: los demás avisos del caso, plegados y con su texto completo."""
    if not avisos:
        return Markup("")
    return render("y_ademas.html", avisos=avisos, cuantos=len(avisos))


def _next_step_banner(review: Caso, work_mode: bool, hay_correo: bool = True, oculto: bool = False) -> Markup:
    """El aviso principal del caso junto con «y además» (lo que mostraría la pantalla arriba)."""
    principal, mas = _avisos(review, work_mode, hay_correo, oculto)
    return principal + mas


_SIN_ENLACE: dict[str, Any] = {"action_href": "", "action_label": "", "enlace_href": "", "enlace_label": "",
                               "formulario": None, "preguntas": []}


def _aviso_del_estado(review: Caso, work_mode: bool, hay_correo: bool, oculto: bool) -> Markup:
    """El aviso del estado del caso: lo más importante que sigue, con su botón si lo hay."""
    def aviso(**datos: Any) -> Markup:
        return render("next_step.html", oculto=oculto, **(_SIN_ENLACE | datos))

    if review.get("reply_pending", ""):
        minutes = minutes_since(review.get("reply_pending", ""))
        late = minutes >= REPLY_OVERDUE_MINUTES
        who = "ChatGPT Work" if work_mode else "el piloto de correos"
        return aviso(tone="warn" if late else "info",
            title=f"{review['client_name']} respondió {ago_text(minutes)}",
            text=(f"Todavía no pasamos esa respuesta a la tabla. Mira que {who} siga encendido."
                  if late else "La leeremos en la próxima ronda y la tabla se actualizará sola."))
    if review.get("proposal", []):
        return aviso(tone="warn",
            title=f"Work propone {len(review['proposal'])} cambio(s) a tu tabla",
            text="Llegó información nueva. Tu tabla no se cambió.",
            action_href="#proposal", action_label="Revisar")
    label, urgent = case_stage(review)
    hilo = str(review["thread_id"])
    if label == "Llamar al cliente":
        return aviso(tone="warn", title="Llamar al cliente",
            text=(f"Hace {fup.CALL_BUSINESS_DAYS} días de trabajo que no contesta. "
                  "Llámalo y, después, pulsa «Ya hablé con el cliente»."),
            enlace_href="#seguimiento", enlace_label="Ver seguimiento",
            formulario={"accion": "/followup", "thread_id": hilo, "campos": {"action": "contacted"},
                        "etiqueta": "Ya hablé con el cliente", "clase": "",
                        "confirmar": ("Se detienen los avisos y recordatorios de este caso. "
                                      "¿Ya hablaste con el cliente?")})
    if label == "Sin respuesta · recordar":
        return aviso(tone="warn", title="El cliente no ha respondido",
            text="Hace 3 días de trabajo que espera. Envíale un recordatorio o activa los recordatorios automáticos.",
            action_href="#seguimiento", action_label="Ver seguimiento")
    if hay_correo and review["status"] in (QuoteStatus.REVIEW_PENDING, QuoteStatus.DRAFT_CREATED):
        return aviso(tone="info", title="Revisa el correo y envíalo",
            text="Nada sale al cliente hasta que pulses «Enviar al cliente».",
            action_href="#respuesta", action_label="Ir al correo")
    if review["status"] == QuoteStatus.REVIEW:
        return aviso(tone="warn", title="Falta tu decisión",
            text="Decide si con esto alcanza o si hay que preguntarle al cliente.",
            action_href="#decision", action_label="Decidir")
    if review.informacion_validada and not review.get("quote_code") and review["status"] != QuoteStatus.IGNORED:
        return aviso(tone="info", title="Información validada: puedes emitir la T",
            text="Al emitir la T damos el proyecto por recibido.",
            action_href="#emitir-t", action_label="Emitir la T")
    code = review.get("quote_code", "")
    if code:
        stage = review.get("code_stage") or "T"
        title = f"{code_label(code, stage)} · " + ("oferta aprobada" if stage == "TE" else "cotizando")
        folder = review.get("folder_id")
        if folder:
            return aviso(tone="info", title=title,
                text=("La T ya está emitida y la carpeta del proyecto ya está en Drive, con la tabla y los archivos "
                      "del cliente. Siguiente paso: preparar la cotización con precios fuera de esta herramienta."),
                action_href="https://drive.google.com/drive/folders/" + urllib.parse.quote(folder),
                action_label="Abrir carpeta en Drive")
        return aviso(tone="warn", title=title,
            text="La T ya está emitida, pero falta la carpeta del proyecto en Drive.",
            action_href="#carpeta", action_label="Ir a la carpeta")
    if label.startswith("Esperando al cliente"):
        return aviso(tone="info", title="Esperando al cliente",
            text=(f"Nada que hacer: esperando su respuesta ({label}). Si no contesta, a los 3 días de trabajo te "
                  f"avisamos para enviar un recordatorio y a los {fup.CALL_BUSINESS_DAYS} para llamarlo."),
            enlace_href="#seguimiento", enlace_label="Ver el seguimiento",
            preguntas=list((review["analysis"].get("preguntas_enviadas") or {}).get("preguntas") or []))
    return _aviso_de_estado_final(review, aviso)

def _aviso_de_estado_final(review: Caso, aviso: Callable[..., Markup]) -> Markup:
    """El aviso de los estados que no piden nada o que se resuelven fuera del flujo: nunca queda vacío (U-31, D-88)."""
    hilo = urllib.parse.quote(str(review["thread_id"]))
    if review["status"] == QuoteStatus.IGNORED:
        return aviso(tone="info", title="Descartada",
            text=("No parece una solicitud de cotización, así que no hay nada que hacer. "
                  "Si fue un error, recupérala en «Descartadas»."),
            action_href="/discarded", action_label="Ver descartadas",
            formulario={"accion": "/rescue", "thread_id": str(review["thread_id"]), "campos": {},
                        "etiqueta": "Sí era una solicitud: analizarla", "clase": "secondary", "confirmar": ""})
    if review["status"] == QuoteStatus.MISSING:
        return aviso(tone="warn", title="Ya no está en Gmail",
            text=("Esta conversación ya no está en Gmail. Alguien la borró, por eso no se puede responder desde aquí. "
                  "Puedes volver a Mis casos."),
            action_href="/", action_label="Volver a Mis casos",
            formulario={"accion": "/delete", "thread_id": str(review["thread_id"]), "campos": {},
                        "etiqueta": "Borrar este caso", "clase": "secondary",
                        "confirmar": ("El caso se borrará de «Mis casos». Su historial se conserva "
                                      "en «Casos borrados». ¿Lo borramos?")})
    if review["status"] == QuoteStatus.SENDING:
        return aviso(tone="warn", title="El correo no salió, o no estamos seguros",
            text=("Gmail no nos confirmó el envío. Mira en Gmail si ya salió antes de enviarlo otra vez, "
                  "para no mandarlo dos veces."),
            action_href="#respuesta", action_label="Revisar si salió",
            enlace_href=_GMAIL_HILO + hilo, enlace_label="Abrir en Gmail (se abre en una pestaña nueva)")
    if review["status"] == QuoteStatus.RESPONSE_SENT:
        return aviso(tone="info", title="Respuesta enviada",
            text="No se espera nada del cliente. Si llega un mensaje nuevo, el caso vuelve a tu lista.",
            action_href=f"/case?thread_id={hilo}&template=libre#respuesta", action_label="Escribir otro correo")
    return aviso(tone="info", title=str(review.get("status_label") or "Sin novedades"),
        text="Este caso no necesita nada de ti ahora.", action_href="/", action_label="Volver a Mis casos")



def _manual_review_note(manual: RevisionManual | None) -> str:
    if not manual:
        return ""
    when = (manual.get("at") or "")[:16].replace("T", " ")
    action = ("decidió que con esto alcanza" if manual.get("decision") == "sufficient"
              else "decidió que hay que preguntarle al cliente")
    return f"Una persona revisó este caso y {action} ({when})."


def _internal_context_panel(client_name: str) -> Markup:
    context = internal_commercial_context(client_name)
    if not context:
        return Markup("")      # sin índice no hay nada que mostrar: la sección se oculta (U-40)

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


def _lo_que_falta(analysis: Analisis) -> str:
    """«Faltan: 3 actividades por completar y 2 archivos por revisar (…)»: lo que se mira antes de decidir."""
    actividades = [a for a in analysis.get("activities", []) if normalize_estado(a.get("estado")) != "confirmado"]
    archivos = [str(a.get("filename") or "sin nombre") for a in analysis.get("attachments", [])
                if isinstance(a, Mapping) and adjunto_requiere_atencion(a)]
    partes = []
    if actividades:
        partes.append(f"{len(actividades)} actividad{'' if len(actividades) == 1 else 'es'} por completar")
    if archivos:
        nombres = ", ".join(archivos[:3]) + ("…" if len(archivos) > 3 else "")
        partes.append(f"{len(archivos)} archivo{'' if len(archivos) == 1 else 's'} por revisar ({nombres})")
    if not partes:
        return ""
    ilegibles = [str(a.get("filename") or "sin nombre") for a in analysis.get("attachments", [])
                 if isinstance(a, Mapping) and adjunto_no_se_pudo_leer(a)]
    salida = (f" No pudimos leer {', '.join(ilegibles[:3])}{'…' if len(ilegibles) > 3 else ''}: ábrelo tú o pídele "
              "al cliente un PDF.") if ilegibles else ""
    return f"Faltan: {' y '.join(partes)}. Míralo en la tabla y en la pestaña «Archivos».{salida}"


def _campo(escrito: Mapping[str, list[str]], nombre: str) -> str:
    """El primer valor de un campo del formulario que se guardó, o vacío."""
    return (escrito.get(nombre) or [""])[0]


def _cuadro_de_preguntas(analysis: Analisis, preguntas: list[dict[str, Any]]) -> str:
    """El texto del cuadro único «Preguntas para el cliente» (U-31): lo último que escribió la persona o, si no hay,
    las inquietudes que el análisis sugiere para el cliente (las internas de Anka no van), una por línea."""
    escritas = analysis.get("ultimas_preguntas")
    if escritas:
        return "\n".join(str(p) for p in escritas)
    return "\n".join(como_peticion(p["texto"]) for p in preguntas if p["elegida"] == "ask_client")


def _ronda(review: Caso, analysis: Analisis, mensajes: Sequence[Mapping[str, Any]]) -> tuple[int, bool]:
    """(número de ronda, ¿el cliente ya contestó un correo con preguntas?) (U-40).

    La ronda 1 es la primera vez que se pregunta; cada correo enviado al cliente abre la siguiente. Sin estados nuevos:
    sale de lo que ya se guarda (los correos de la conversación, las preguntas enviadas y cuándo llegó la respuesta).
    """
    enviados = sum(1 for m in mensajes if m["direction"] == "sent")
    enviadas = analysis.get("preguntas_enviadas") or {}
    if review["status"] == QuoteStatus.WAITING:
        return max(1, enviados), False
    if not enviadas.get("preguntas") or review["status"] not in (
            QuoteStatus.REVIEW, QuoteStatus.REVIEW_PENDING, QuoteStatus.DRAFT_CREATED, QuoteStatus.READY_REVIEW):
        return 1, False
    salio = timestamp(str(enviadas.get("en") or ""))
    contesto = salio > 0 and any(
        m["direction"] == "received" and timestamp(str(m["occurred_at"] or "")) > salio for m in mensajes)
    return (max(2, enviados + 1), True) if contesto else (max(1, enviados), False)


def _puede_volver_atras(review: Caso, analysis: Analisis) -> bool:
    """¿Sigue sin salir el correo de la decisión de preguntar? Entonces se puede cambiar de idea (U-27)."""
    decision = analysis.get("manual_review") or {}
    if review["status"] not in (QuoteStatus.REVIEW_PENDING, QuoteStatus.DRAFT_CREATED):
        return False
    if decision.get("decision") != "ask_client":
        return False
    return not (review.get("sent_at") and str(review["sent_at"]) >= str(decision.get("at") or ""))


def _preguntas_para_armar(analysis: Analisis, template: str, escritas: str, puede_volver: bool) -> str:
    """El campo «Preguntas para el cliente» (U-24): lo escrito, o las elegidas mientras el correo no salió.

    Así las preguntas siguen un solo camino (U-28): se deciden en «¿Cómo seguimos?» y se ajustan en este mismo campo.
    """
    if template == "preguntas":
        return escritas
    if not puede_volver:
        return ""
    elegidas = (analysis.get("manual_review") or {}).get("questions") or []
    return "\n".join(str(p) for p in elegidas)


def _asunto_de_respuesta(thread_id: str) -> str:
    """El asunto con que saldría la respuesta («Re: …» del último correo del cliente), para confirmar el envío."""
    recibido = next((m for m in case_conversation(thread_id) if m.get("direction") == "received"), None)
    asunto = str((recibido or {}).get("subject") or "").strip()
    if not asunto:
        return ""
    return asunto if asunto.lower().startswith("re:") else f"Re: {asunto}"


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
            or reply_body_without_quoted_history(work_reply.get("text") or "") or review["draft_body"]
            or suggested_reply(analysis, config["required_fields"]))
    return "form", body, False


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
                "No encontramos a quién escribirle en Gmail. Por seguridad no se puede guardar ni enviar. "
                "Abre el correo en Gmail y confirma la dirección; luego vuelve a abrir este caso."
            )
    if review.get("reply_pending"):
        review = replace(review, status_label=REPLY_LABEL)
    elif review["status"] == QuoteStatus.READY and not review.get("quote_code"):
        review = replace(review, status_label=case_stage(review)[0])      # un solo nombre para «listo» (U-35.3)
    return review, recipient_error


def _compose_to(review: Mapping[str, Any], change: Cambio | None, to_kind: str) -> str:
    """Destinatario que se propone al responder por un cambio: quien lo escribió o el contacto registrado."""
    if change and to_kind == "sender":
        return change["sender_email"]
    if change and to_kind == "contact":
        return review.get("contact_email") or review["recipient"]
    return ""


def _compose_origen(thread_id: str, para: str) -> str:
    """De dónde sale el destinatario cuando no es el del caso (U-45); vacío si es el contacto o el destinatario."""
    if not para:
        return ""
    return origen_en_texto(origen_de_destinatario(thread_id, para)).strip(" ()")


def _code_text(review: Mapping[str, Any]) -> str:
    """«T26145 · En cotización» (o «T&E…· Aprobada»); vacío mientras no haya código."""
    if not review.get("quote_code"):
        return ""
    stage = review.get("code_stage") or "T"
    return code_label(review["quote_code"], stage) + " · " + STAGE_LABELS.get(stage, "")


def _reading_is_blocked(analysis: Analisis) -> bool:
    """Oculta resultados de Work solo cuando su lectura no se pudo verificar y nadie la revisó por su cuenta (U-29)."""
    return lectura_sin_verificar(analysis) and not analysis.get("reading_review")


def _nota_de_lectura_revisada(analysis: Analisis) -> str:
    """Aviso permanente de que la lectura la revisó una persona y no Anka (U-29); vacío si no aplica."""
    revision = analysis.get("reading_review")
    if not revision or not lectura_sin_verificar(analysis):
        return ""
    cuando = (revision.get("at") or "")[:16].replace("T", " ")
    archivos = revision.get("files") or []
    cuales = f" ({', '.join(archivos)})" if archivos else ""
    return (f"Una persona revisó estos archivos el {cuando} porque el programa no pudo{cuales}. "
            "Los resultados son los de Work: confirma lo que dependa de esos archivos.")


def _texto_de_lectura_pendiente(analysis: Analisis) -> str:
    """El aviso de lectura sin verificar: qué archivos faltan (si se sabe) y qué hacer para completarla."""
    audit = analysis.get("reading_audit") or {}
    archivos = audit.get("attachment_files_pending") or audit.get("attachment_files_unknown") or []
    falta = f" Falta leer: {', '.join(str(a) for a in archivos)}." if audit.get("attachment_files_pending") else (
        f" Work reportó adjuntos que Gmail no tiene: {', '.join(str(a) for a in archivos)}." if archivos else "")
    que = ("los archivos contra Gmail" if audit.get("verification") else "la lista de archivos y todas sus partes")
    return (f"No mostramos resultados ni se puede decidir hasta comprobar {que}." + falta
            + " Pide a Work que analice de nuevo (el nuevo análisis reemplaza a este)"
            " o, si ya revisaste tú los archivos, pulsa «Ya revisé estos archivos yo».")


def _case_panels(thread_id: str, review: Caso, analysis: Analisis, assessment: dict, config: Configuracion,
                 events: list[Evento], integrity_ok: bool, event_count: int,
                 questions_in_decision: bool = False, slot: str = "aviso",
                 pendiente: tuple[str, Mapping[str, list[str]]] | None = None,
                 actividades_escritas: list[Any] | None = None, ronda: int = 1) -> dict[str, Any]:
    """Los paneles de la pantalla del caso, cada uno ya renderizado."""
    proposal = review.get("proposal", [])
    reading_blocked = _reading_is_blocked(analysis)
    avisos = ((render("next_step.html", oculto=False, tone="warn", title="Lectura pendiente",
                      text=_texto_de_lectura_pendiente(analysis),
                      action_href="#lectura-revisada", action_label="Elegir cómo seguir"), Markup(""))
              if reading_blocked else _avisos(
                  review, config.get("active_mode") == "chatgpt_work",
                  hay_correo=assessment.get("status") != "technical_review",
                  oculto=slot in ("decision", "correo", "codigo")))
    return {
        "assessment_panel": Markup("") if reading_blocked else reception_assessment_panel(assessment),
        "internal_context": _internal_context_panel(review["client_name"]),
        "activities_editor": (Markup(
            "<div class='notice'><strong>Pendiente de lectura.</strong> No se muestran actividades hasta que Work "
            "confirme la lectura de los segmentos del mensaje y sus adjuntos.</div>"
        ) if reading_blocked else activities_editor(
            thread_id, actividades_escritas or analysis.get("activities", []), list(EDIT_REASONS.items()),
            abierto=bool(actividades_escritas), base_version=huella_de_tabla(analysis.get("activities", [])),
            hoja={"url": review.sheet_url, "error": review.sheet_error, "tiene_carpeta": bool(review.folder_id),
                  "actualizada": review.sheet_synced_at[:16].replace("T", " ")},
            ultima=last_manual_version(thread_id))),
        "attachments_panel": attachments_panel(analysis.get("attachments", [])),
        "reading_panel": render("reading_audit.html", reading=analysis.get("reading_summary") or {},
                                preflight=analysis.get("DRIVE_PREFLIGHT") or {},
                                audit=analysis.get("reading_audit") or {},
                                applicability=analysis.get("applicability") or {},
                                complete=analysis.get("analysis_complete"), blocked=reading_blocked),
        "review_tasks": (Markup(
            "<p class='meta'>Las preguntas y decisiones quedan ocultas hasta que se complete la lectura.</p>"
        ) if reading_blocked else review_tasks_panel(
            analysis.get("question_audit", []),
            other_questions=analysis.get("other_questions", []),
            attachments=analysis.get("attachments", []),
            dependencies=analysis.get("scope_dependencies", []),
            contradictions=analysis.get("contradictions", []),
            waiting_for_client=review["status"] == QuoteStatus.RESPONSE_SENT,
            questions_in_decision=questions_in_decision,
            decisiones=analysis.get("preguntas") or (),
        )),
        "question_audit": Markup("") if reading_blocked else question_audit_panel(analysis.get("question_audit", [])),
        "question_quality": (Markup("") if reading_blocked
                             else question_quality_panel(analysis.get("question_quality_warnings", []))),
        "manual_review": _manual_review_note(analysis.get("manual_review")),
        "reading_review_note": _nota_de_lectura_revisada(analysis),
        "conversation": _conversation_panel(thread_id),
        "stepper": _stepper(review, ronda),
        "followup": _followup_panel(review),
        "edit_note": _edit_note(thread_id),
        "code_panel": _code_panel(review, pendiente[1] if pendiente and pendiente[0] == "/code-issue" else None,
                                  con_cliente=slot == "codigo"),
        "datos_comerciales": _datos_comerciales(review),
        "code_text": _code_text(review),
        "next_step": avisos[0], "avisos_extra": avisos[1],
        "project_panel": Markup("") if reading_blocked else _project_panel(thread_id),
        "proposal_panel": (Markup("") if reading_blocked or not proposal
                           else render("proposal.html", changes=proposal, thread_id=thread_id)),
        "timeline": _timeline_panel(thread_id, review, analysis, events, integrity_ok, event_count),
    }


def render_case(thread_id: str, message: str = "", template: str = "", change_id: int = 0,
                to_kind: str = "", preguntas_texto: str = "") -> str:
    """Pantalla de un caso: etapa, siguiente paso, tabla, conversación, respuesta y seguimiento."""
    review, recipient_error = _load_review(thread_id)
    pendiente = pendientes.ver(thread_id)
    if not review:
        return page("Caso no encontrado", render("empty.html", text="La solicitud no existe.", salidas=True), message)
    analysis = review["analysis"]
    reading_blocked = _reading_is_blocked(analysis)
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
    espera_decision = review["status"] == QuoteStatus.REVIEW and not reading_blocked
    # U-30: las preguntas se ven siempre que el caso espera tu decisión, aunque la recepción no sea técnica
    preguntas = _preguntas_del_caso(analysis, assessment) if espera_decision else []
    sugeridas = _preguntas_sugeridas(analysis, assessment) if not reading_blocked else []
    puede_volver = _puede_volver_atras(review, analysis)
    ronda, segunda_ronda = _ronda(review, analysis, case_conversation(thread_id, 50))
    cuadro_preguntas = _cuadro_de_preguntas(analysis, preguntas)
    lo_que_falta = "" if reading_blocked else _lo_que_falta(analysis)
    plegar_respuesta = bool(review.get("quote_code")) or (
        review.informacion_validada and review["status"] not in {QuoteStatus.IGNORED, QuoteStatus.MISSING})
    puede_emitir = (review.informacion_validada and not review.get("quote_code")
                    and review["status"] not in {QuoteStatus.IGNORED, QuoteStatus.MISSING})
    # Una sola tarjeta «Ahora» (U-31): lectura pendiente, decisión, correo, emisión de la T o el aviso del estado.
    slot = ("lectura" if reading_blocked else "decision" if espera_decision
            else "correo" if email == "form" and not plegar_respuesta
            else "codigo" if puede_emitir else "aviso")
    # Lo que la persona escribió y no se pudo procesar vuelve a su sitio (U-31); si ya no aplica, se descarta.
    restaurado, esperar_marcada, actividades_escritas = False, False, None
    if pendiente:
        ruta_pendiente, escrito = pendiente
        if ruta_pendiente in ("/send", "/draft") and email == "form" and not reading_blocked:
            email_body, blank_reply, restaurado = _campo(escrito, "body"), False, True
            esperar_marcada = _campo(escrito, "esperar") == "1"
        elif ruta_pendiente == "/review-decision" and espera_decision and _campo(escrito, "questions"):
            cuadro_preguntas, restaurado = _campo(escrito, "questions"), True
        elif ruta_pendiente == "/code-issue" and puede_emitir:
            restaurado = True
        elif ruta_pendiente == "/code-issue" and review.get("quote_code") and email == "form":
            # La T se emitió pero el correo no salió (U-34.2): el texto que se revisó vuelve al cuadro de respuesta.
            email_body = con_codigo_final(_campo(escrito, "body"), _campo(escrito, "suggested_number"),
                                          _campo(escrito, "number"))
            blank_reply, restaurado = False, True
        elif ruta_pendiente == "/activities" and not reading_blocked:
            try:
                actividades_escritas = parse_activities_form(dict(escrito)) or None
            except ValueError:
                actividades_escritas = None
            restaurado = actividades_escritas is not None
        if not restaurado:
            pendientes.limpiar(thread_id)
    archivos = analysis.get("attachments", [])
    por_revisar = sum(1 for a in archivos if isinstance(a, Mapping) and adjunto_requiere_atencion(a))
    etiqueta_archivos = f"Archivos ({len(archivos)})" + (f" · {por_revisar} por revisar" if por_revisar else "")
    recibido = (review.get("first_seen_at") or "")[:10]
    body = render(
        "case.html",
        review=review, thread_id=thread_id,
        project_name=("Solicitud pendiente de lectura" if reading_blocked
                      else analysis.get("project_name") or "Solicitud sin nombre"),
        summary=("No se muestra el análisis hasta que Work confirme la lectura del mensaje y sus adjuntos."
                 if reading_blocked else analysis.get("summary") or "Sin resumen disponible"),
        analysis_blocked=reading_blocked,
        sin_analisis=analysis.get("sin_analisis_de_work"),
        correcciones_de_work=[] if reading_blocked else analysis.get("correcciones_de_work", []),
        sin_actividades=not reading_blocked and not analysis.get("activities"),
        service_family=assessment.get("service_family", "Por clasificar"),
        suggestion_complete=review["status"] not in {QuoteStatus.IGNORED, QuoteStatus.MISSING},
        draft_complete=bool(review["draft_id"] or sent_complete),
        plegar_respuesta=plegar_respuesta, slot=slot, recibido=recibido, etiqueta_archivos=etiqueta_archivos,
        restaurado=restaurado, esperar_marcada=esperar_marcada, ronda=ronda, segunda_ronda=segunda_ronda,
        cambiar_abierto=not email_body or template == "preguntas",
        correo_con_preguntas=bool(en_el_correo(email_body)),
        datos_en_panel=slot == "codigo",
        carpeta_en_mas_datos=(review["status"] == QuoteStatus.READY_REVIEW and not review.get("quote_code")
                              and not (analysis.get("drive") or {}).get("pending")),
        hay_revision_de_preguntas=bool(analysis.get("question_audit") or analysis.get("question_quality_warnings")),
        hay_pendientes=bool(unresolved_scope_issues(analysis)),
        tab_inicial="archivos" if por_revisar or reading_blocked else "conversacion",
        sent_complete=sent_complete, no_reply_needed=review["status"] in {QuoteStatus.READY, QuoteStatus.IGNORED},
        client_types=CLIENT_TYPES, sectors=SECTORS,
        routing_summary=("Pendiente de verificar la lectura del expediente." if reading_blocked
                         else _routing_summary(analysis)),
        issues_summary=("Lectura pendiente: todavía no se evalúan faltantes ni contradicciones." if reading_blocked
                        else issues_summary),
        armar_preguntas=_preguntas_para_armar(analysis, template, preguntas_texto, puede_volver),
        asunto_respuesta=_asunto_de_respuesta(thread_id),
        email=email, email_body=email_body, recipient_error=recipient_error, blank_reply=blank_reply,
        sufficient=not reading_blocked and assessment.get("status") == "sufficient_for_internal_review",
        drive_pending=(analysis.get("drive") or {}).get("pending", ""),
        can_resolve=espera_decision, preguntas_sugeridas=sugeridas,
        puede_volver=puede_volver,
        preguntas=preguntas, cuadro_preguntas=cuadro_preguntas, lo_que_falta=lo_que_falta,
        n_internas=sum(1 for p in preguntas if p["elegida"] == "internal"),
        preguntas_enviadas=(analysis.get("preguntas_enviadas") or {}).get("preguntas"),
        compose_to=_compose_to(review, change, to_kind), compose_change_id=change_id if change else 0,
        compose_origen=_compose_origen(thread_id, _compose_to(review, change, to_kind)),
        compose_purpose=(template if change and template in ("ack", "ask_code", "notify_contact")
                         else "scope_update" if template == "scope_update" else ""),
        **_case_panels(thread_id, review, analysis, assessment, config, events, integrity_ok, event_count,
                       questions_in_decision=bool(preguntas) and review["status"] == QuoteStatus.REVIEW, slot=slot,
                       pendiente=pendiente if restaurado else None, actividades_escritas=actividades_escritas,
                       ronda=ronda),
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
        return page("Caso no encontrado", render("empty.html", text="No hay historial para ese caso.", salidas=True))
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
    return page("Registro de cambios", body)


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
