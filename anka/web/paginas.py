"""Las páginas comunes del dashboard: la página completa, el resumen, las solicitudes y las descartadas.

La pantalla de un caso y su acta están en `pantalla_caso.py`.
El servidor HTTP, la protección CSRF y las acciones de los formularios están en `server.py` y `seguridad.py`.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Mapping, Sequence
from typing import Any
from datetime import datetime
from email.utils import parsedate_to_datetime
import os
from urllib.parse import quote as url_quote
from markupsafe import Markup
from anka.dominio.preguntas import era_un_acuse
from anka.dominio.errores import ErrorDeAnka
from anka.web.views import (
    metric_rows,
    render_page,
)
from anka.dominio.status import (
    QuoteStatus,
)
from anka.infra.logging_setup import get_logger
from anka.infra.settings import load_config
from anka.infra.work_heartbeat import rejected_drafts
from anka.web.templating import render

from anka.servicios.estado_de_revision import sync_status_text
from anka.servicios import followup_reminders as fup
from anka.servicios.change_control import (
    code_label, required_messages,
)
from anka.dominio.registros import Cambio
from anka.servicios.quotes import (
    all_quotes, discarded_quotes, get_quote, separated_project_pairs, unread_notifications, unread_thread_count,
)
from anka.dominio.caso import CLIENT_TYPES
from anka.servicios.proyectos_repetidos import Grupo, agrupar as agrupar_solicitudes

log = get_logger(__name__)


def page(title: str, body: Markup, message: str = "") -> str:
    """Página completa con el encabezado, el selector de modo y las novedades sin leer."""
    try:
        mode = load_config().get("active_mode")
    except (ErrorDeAnka, OSError, ValueError):
        mode = None
    return render_page(title, body, message, unread_thread_count(), mode,
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


def _notifications_panel(notifications: list[dict], plegar: bool = False) -> Markup:
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
    return render("notifications_panel.html", items=items, plegar=plegar)


REPLY_LABEL = "Cliente respondió · por analizar"
# Etiqueta de `case_stage` → (urgencia, verbo del botón, ancla del paso dentro del caso). Menor urgencia, primero.
_ACCION_POR_ETAPA: dict[str, tuple[int, str, str]] = {
    REPLY_LABEL: (0, "Revisar respuesta", ""),
    "Cambio por revisar": (1, "Revisar cambio", ""),
    "Faltan correos por enviar": (1, "Enviar correos", ""),
    "Posible actualización": (1, "Revisar", ""),
    "Propuesta del modelo por revisar": (1, "Revisar", "#proposal"),
    "Borrador por revisar": (2, "Revisar borrador", "#respuesta"),
    "Envío interrumpido": (2, "Revisar envío", "#respuesta"),
    "Información validada": (3, "Emitir la T", "#emitir-t"),
    "Por decidir": (4, "Decidir", "#decision"),
    "Por aclarar con el cliente": (4, "Aclarar", ""),
    "Hilo no disponible": (4, "Abrir", ""),
    "Llamar al cliente": (5, "Ver seguimiento", "#seguimiento"),
    "Sin respuesta · recordar": (5, "Ver seguimiento", "#seguimiento"),
}


def accion_de_caso(quote: Mapping[str, Any]) -> tuple[int, str, str]:
    """(urgencia, verbo, ancla) de un caso que espera una persona; lo desconocido va al final con «Abrir»."""
    return _ACCION_POR_ETAPA.get(case_stage(quote)[0], (9, "Abrir", ""))


def cola_de_hoy(quotes: Sequence[Mapping[str, Any]]) -> list[Mapping[str, Any]]:
    """Los casos que piden algo de una persona, del más urgente al menos y, a igual urgencia, el más reciente."""
    return sorted((q for q in quotes if case_stage(q)[1]),
                  key=lambda q: (accion_de_caso(q)[0], -fecha_de_recepcion(q)))


ETAPAS_FILTRO = (("atender", "Por atender"), ("esperando", "Esperando al cliente"),
                 ("listo", "Listas para la T"), ("cotizando", "En cotización"))


def grupo_de_etapa(quote: Mapping[str, Any]) -> str:
    """El grupo de la lista de Solicitudes: atender, esperando, listo, cotizando u otras."""
    label, urgent = case_stage(quote)
    if label == "Información validada":
        return "listo"
    if label.startswith("Esperando al cliente") or label in ("Llamar al cliente", "Sin respuesta · recordar"):
        return "esperando"
    if urgent:
        return "atender"
    return "cotizando" if quote.get("quote_code") else "otras"


def queue_row(quote: Mapping[str, Any], position: int, is_new: bool = False, mismos: int = 0) -> Markup:
    """Fila de la cola de hoy: posición, proyecto, qué espera y un solo botón con verbo."""
    _, verb, anchor = accion_de_caso(quote)
    code = quote.get("quote_code")
    return render(
        "queue_row.html", position=position, is_new=is_new, mismos=mismos, etapa=grupo_de_etapa(quote),
        primaria=position == 1 and bool(case_stage(quote)[1]),
        project=quote["analysis"].get("project_name") or "Solicitud sin nombre",
        client_name=quote["client_name"], reason=case_stage(quote)[0], verb=verb,
        code=code_label(code, quote.get("code_stage") or "T") if code else "",
        href=f"/case?thread_id={url_quote(quote['thread_id'])}{anchor}")


def _work_rejected_panel() -> Markup:
    """Los borradores de Work que el puente no pudo importar, con su motivo y el caso si ya existe."""
    items = []
    for item in rejected_drafts():
        thread = str(item.get("thread") or "")
        items.append({"draft": item.get("draft", ""), "thread": thread, "reason": item.get("reason", ""),
                      "case_href": ("/case?thread_id=" + url_quote(thread)) if thread and get_quote(thread) else ""})
    return render("work_rejected.html", items=items) if items else Markup("")


def render_index(message: str = "") -> str:
    """«Mis casos» (U-32): la lista de todos los casos, del más urgente al menos, con novedades y estadísticas."""
    quotes = all_quotes()
    notifications = unread_notifications(8)
    unread_threads = {item["thread_id"] for item in notifications}
    services = Counter(service for quote in quotes for service in quote["services"])
    clients = Counter(q["client_name"] for q in quotes if q["client_name"])
    stages = {q["thread_id"]: case_stage(q) for q in quotes}
    grupo_de = {q["thread_id"]: g for g in agrupar_solicitudes(quotes, separated_project_pairs()) for q in g.miembros}
    por_atender = cola_de_hoy(quotes)
    resto = sorted((q for q in quotes if not case_stage(q)[1]), key=lambda q: -fecha_de_recepcion(q))
    unidades: list[tuple[Mapping[str, Any], Grupo]] = []
    for q in por_atender + resto:                       # un proyecto ocupa una sola fila: la de su hilo más urgente
        if all(grupo_de[q["thread_id"]] is not g for _, g in unidades):
            unidades.append((q, grupo_de[q["thread_id"]]))
    en_lista = {m["thread_id"] for _, g in unidades for m in g.miembros}
    body = render(
        "index.html",
        sync_text=sync_status_text(),
        work_rejected=_work_rejected_panel(),
        notifications=_notifications_panel(notifications, plegar=bool(notifications) and all(
            item["thread_id"] in en_lista for item in notifications)),
        total=len(quotes),
        unique_clients=len({q["client_email"].casefold() for q in quotes if q["client_email"]}),
        action_count=sum(1 for q, _ in unidades if case_stage(q)[1]),
        etapas=[(key, label, sum(grupo_de_etapa(q) == key for q in quotes)) for key, label in ETAPAS_FILTRO],
        inicial="atender" if por_atender else "",
        waiting=sum(stages[q["thread_id"]][0] == "Esperando al cliente" for q in quotes),
        quoting=sum(bool(q.get("quote_code")) and q.get("code_stage") != "TE" for q in quotes),
        approved=sum(q.get("code_stage") == "TE" for q in quotes),
        services=metric_rows(services),
        statuses=metric_rows(Counter(stages[q["thread_id"]][0] for q in quotes), "blue"),
        client_types=metric_rows(Counter(q["client_type"] for q in quotes)),
        insights=_dashboard_insights(quotes, services, clients),
        todo=[queue_row(q, n, q["thread_id"] in unread_threads, mismos=len(g.miembros) - 1)
              for n, (q, g) in enumerate(unidades, 1)],
    )
    return page("Mis casos", body, message)


def quote_row(quote: Mapping[str, Any], is_new: bool = False, selectable: bool = False) -> Markup:
    """Fila de la lista de solicitudes: proyecto, cliente, etapa y siguiente paso."""
    project = quote["analysis"].get("project_name") or "Solicitud sin nombre"
    services = ", ".join(quote["services"]) or "Servicio por clasificar"
    date = _received_datetime_label(_received_value(quote))
    _, verb, anchor = accion_de_caso(quote) if case_stage(quote)[1] else (0, "Abrir", "")
    return render(
        "quote_row.html",
        project=project, services=services, is_new=is_new, selectable=selectable,
        search=(project + " " + quote["client_name"] + " " + services).casefold(),
        status=quote["status"], client_type=quote["client_type"],
        client_name=quote["client_name"], sector=quote["sector"],
        badge_class="warn" if case_stage(quote)[1] else "info",
        etapa=grupo_de_etapa(quote), verb=verb, href=f"/case?thread_id={url_quote(quote['thread_id'])}{anchor}",
        status_label=case_stage(quote)[0],
        date=date, fecha=int(fecha_de_recepcion(quote)), urgencia=urgencia_de_caso(quote),
        thread_id=quote["thread_id"], reply_pending=bool(quote.get("reply_pending", "")),
        code=code_label(quote["quote_code"], quote.get("code_stage") or "T") if quote.get("quote_code") else "",
    )


def etiqueta_de_recepcion(quote: Mapping[str, Any]) -> str:
    """«Recibido: 2026-10-08 06:10», la fecha de recepción tal como la muestra cada fila."""
    return _received_datetime_label(_received_value(quote))


def _received_value(quote: Mapping[str, Any]) -> str:
    """El texto de la fecha de recepción: la primera vez que se vio o, si no es una fecha completa, la del correo."""
    received_at = quote.get("first_seen_at") or ""
    if len(received_at) < 16 or received_at[10] not in ("T", " "):
        received_at = quote["analysis"].get("received_at") or received_at
    return str(received_at)


def _parse_received(value: str) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        try:
            return parsedate_to_datetime(value)
        except (TypeError, ValueError, OverflowError):
            return None


def fecha_de_recepcion(quote: Mapping[str, Any]) -> float:
    """Segundos desde 1970 de cuando se recibió la solicitud; 0 si no hay fecha, para que quede al final."""
    parsed = _parse_received(_received_value(quote))
    if parsed is None:
        return 0.0
    try:
        return parsed.timestamp()
    except (OverflowError, OSError, ValueError):
        return 0.0


def urgencia_de_caso(quote: Mapping[str, Any]) -> int:
    """El orden de la cola del Resumen para un caso; lo que no espera nada de una persona va después (10)."""
    return accion_de_caso(quote)[0] if case_stage(quote)[1] else 10


def _received_datetime_label(value: str) -> str:
    """Muestra fecha y hora originales de recepción, nunca la fecha de última actualización."""
    if not value:
        return "Recibido: Sin fecha"
    parsed = _parse_received(value)
    if parsed is not None:
        return f"Recibido: {parsed.strftime('%Y-%m-%d %H:%M')}"
    # Compatibilidad con los registros históricos guardados como YYYY-MM-DD HH:MM.
    if len(value) >= 16 and value[10] in ("T", " "):
        return f"Recibido: {value[:16].replace('T', ' ')}"
    return f"Recibido: {value[:10]} (hora no disponible)"


def _unidad_de_lista(grupo: Grupo, unread_threads: set[str]) -> Markup:
    """Una fila de la lista: un hilo solo, o un grupo de hilos del mismo proyecto con el más reciente al frente."""
    hilos = sorted(grupo.miembros, key=lambda q: -fecha_de_recepcion(q))      # estable: igual fecha, mismo orden
    filas = [quote_row(q, q["thread_id"] in unread_threads, selectable=True) for q in hilos]
    if len(hilos) == 1:
        return filas[0]
    return render(
        "request_group.html", n=len(hilos), motivo=grupo.motivo, verificar=grupo.verificar,
        principal=filas[0], resto=filas[1:], fecha=int(fecha_de_recepcion(hilos[0])),
        urgencia=min(urgencia_de_caso(q) for q in hilos), proyecto=hilos[0]["analysis"].get("project_name") or "")


def render_requests(message: str = "", agrupar_hilos: bool = True) -> str:
    """Lista de todas las solicitudes, la más reciente primero; en el navegador se puede reordenar."""
    quotes = all_quotes()
    unread_threads = {item["thread_id"] for item in unread_notifications(100)}
    pending_links = [
        {"thread_id": quote["thread_id"],
         "project_name": quote["analysis"].get("project_name") or "Solicitud sin nombre",
         "link": link, "who": link["sender_name"] or link["sender_email"],
         "reason": link["reason"]}
        for quote in quotes for link in quote.get("links", [])
    ]
    grupos = agrupar_solicitudes(quotes, separated_project_pairs())
    hay_grupos = any(len(g.miembros) > 1 for g in grupos)
    if not agrupar_hilos:
        grupos = [Grupo((q,)) for q in quotes]
    grupos = sorted(grupos, key=lambda g: -max(fecha_de_recepcion(q) for q in g.miembros))
    body = render(
        "requests.html",
        rows=[_unidad_de_lista(g, unread_threads) for g in grupos], hay_grupos=hay_grupos, agrupado=agrupar_hilos,
        pending_links=pending_links,
        etapas=[(key, label, sum(grupo_de_etapa(q) == key for q in quotes)) for key, label in ETAPAS_FILTRO],
        type_options=CLIENT_TYPES,
    )
    return page("Solicitudes", body, message)


def render_discarded(message: str = "") -> str:
    """Hilos descartados con su motivo y el botón de rescatarlos."""
    body = render("discarded.html", rows=discarded_quotes())
    return page("Descartadas", body, message)




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
        return "Por decidir", True
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
        QuoteStatus.RESPONSE_SENT: (
            ("Respuesta enviada · sin espera" if era_un_acuse(quote.get("analysis") or {}) else "Esperando al cliente"),
            False),
        QuoteStatus.READY_REVIEW: ("Información validada", True),
        QuoteStatus.READY: ("Información validada", True),
        QuoteStatus.IGNORED: ("Descartada", False),
    }
    return labels.get(status, (quote["status_label"], False))
