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
from markupsafe import Markup
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
from anka.web.templating import render

from anka.servicios.estado_de_revision import sync_status_text
from anka.servicios import followup_reminders as fup
from anka.servicios.change_control import (
    code_label, required_messages,
)
from anka.dominio.registros import Cambio
from anka.servicios.quotes import discarded_quotes, all_quotes, unread_notifications
from anka.dominio.caso import CLIENT_TYPES, STATUS_LABELS

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
    received_at = quote.get("first_seen_at") or ""
    if len(received_at) < 16 or received_at[10] not in ("T", " "):
        received_at = quote["analysis"].get("received_at") or received_at
    date = _received_datetime_label(received_at)
    return render(
        "quote_row.html",
        project=project, services=services, is_new=is_new, selectable=selectable,
        search=(project + " " + quote["client_name"] + " " + services).casefold(),
        status=quote["status"], client_type=quote["client_type"],
        client_name=quote["client_name"], sector=quote["sector"],
        badge_class="warn" if case_stage(quote)[1] else "info",
        status_label=case_stage(quote)[0],
        date=date,
        thread_id=quote["thread_id"], reply_pending=bool(quote.get("reply_pending", "")),
        code=code_label(quote["quote_code"], quote.get("code_stage") or "T") if quote.get("quote_code") else "",
    )


def _received_datetime_label(value: str) -> str:
    """Muestra fecha y hora originales de recepción, nunca la fecha de última actualización."""
    if not value:
        return "Recibido: Sin fecha"
    parsed = None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        try:
            parsed = parsedate_to_datetime(value)
        except (TypeError, ValueError, OverflowError):
            pass
    if parsed is not None:
        return f"Recibido: {parsed.strftime('%Y-%m-%d %H:%M')}"
    # Compatibilidad con los registros históricos guardados como YYYY-MM-DD HH:MM.
    if len(value) >= 16 and value[10] in ("T", " "):
        return f"Recibido: {value[:16].replace('T', ' ')}"
    return f"Recibido: {value[:10]} (hora no disponible)"


def render_requests(message: str = "") -> str:
    """Lista de todas las solicitudes, las urgentes primero."""
    quotes = all_quotes()
    unread_threads = {item["thread_id"] for item in unread_notifications(100)}
    pending_links = [
        {"thread_id": quote["thread_id"],
         "project_name": quote["analysis"].get("project_name") or "Solicitud sin nombre",
         "link": link, "who": link["sender_name"] or link["sender_email"],
         "reason": link["reason"]}
        for quote in quotes for link in quote.get("links", [])
    ]
    body = render(
        "requests.html",
        rows=[quote_row(quote, quote["thread_id"] in unread_threads, selectable=True)
              for quote in sorted(quotes, key=lambda q: not case_stage(q)[1])],  # primero lo que pide acción
        pending_links=pending_links,
        status_options=list(STATUS_LABELS.items()),
        type_options=CLIENT_TYPES,
    )
    return page("Solicitudes", body, message)


def render_discarded(message: str = "") -> str:
    """Hilos descartados con su motivo y el botón de rescatarlos."""
    body = render("discarded.html", rows=discarded_quotes())
    return page("Descartadas", body, message)


REPLY_LABEL = "Cliente respondió · por analizar"


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
