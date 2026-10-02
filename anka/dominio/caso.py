"""`Caso`: la vista de una solicitud de cotización tal como la ven el dashboard y las acciones.

Es un dato inmutable con campos nombrados (antes era un diccionario con claves sueltas, sin dónde mirar qué
trae). Además se puede leer como un mapa (`caso["status"]`, `caso.get("x")`, `dict(caso)`), de modo que las
plantillas y el código que ya existía siguen funcionando mientras se migra a acceso por atributo.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any
import json
from datetime import datetime

from anka.dominio.analisis import Analisis
from anka.dominio.registros import Cambio, MapaDeSoloLectura, Vinculo
from anka.dominio.correo import valid_email_address
from anka.dominio.status import CLOSED_STATUSES, QuoteStatus


@dataclass(frozen=True)
class Caso(MapaDeSoloLectura):
    """Un hilo de Gmail convertido en solicitud, con lo que se necesita para mostrarlo y decidir."""

    thread_id: str
    status: str
    status_label: str
    recipient: str
    client_email: str
    client_name: str
    client_name_saved: str
    client_type: str
    sector: str
    notes: str
    analysis: Analisis
    reception_assessment: dict
    services: list[str]
    first_seen_at: str
    updated_at: str
    folder_id: str | None
    draft_id: str | None
    draft_body: str
    sent_at: str
    reply_pending: str
    proposal: list[dict]
    links: list[Vinculo] = field(default_factory=list)
    changes: list[Cambio] = field(default_factory=list)
    waiting_since: str = ""
    followup: dict = field(default_factory=dict)
    reminders: list[str] = field(default_factory=list)
    quote_code: str = ""
    code_stage: str = ""
    code_assigned_at: str = ""
    contact_email: str = ""

    def __hash__(self) -> int:
        return hash(self.thread_id)

    # --- reglas del caso que dependen solo de sus propios campos
    @property
    def cerrado(self) -> bool:
        """¿El caso ya terminó o se descartó (no espera más gestión por correo)?"""
        return self.status in CLOSED_STATUSES

    @property
    def informacion_validada(self) -> bool:
        """¿La información ya está validada para empezar a cotizar?

        Sí cuando el alcance está completo, una persona dio la información por suficiente, o ya hay T.
        """
        return (bool(self.quote_code)
                or self.status in (QuoteStatus.READY_REVIEW, QuoteStatus.READY)
                or (self.analysis.get("manual_review") or {}).get("decision") == "sufficient")

    @property
    def espera_al_cliente(self) -> bool:
        """¿Ya se le escribió al cliente y se espera su respuesta?"""
        return self.status == QuoteStatus.WAITING and bool(self.sent_at)

    def reabierto(self, tiene_vinculos: bool) -> bool:
        """Un caso cerrado vuelve a admitir correo si el cliente escribió de nuevo (hay vínculos o código)."""
        return self.status in (QuoteStatus.READY, QuoteStatus.RESPONSE_SENT) and tiene_vinculos

    def admite_correo(self, *, reabierto: bool, revision_tecnica: bool) -> bool:
        """¿Se le puede escribir al cliente? Destinatario válido, caso abierto y sin bloqueo técnico."""
        return valid_email_address(self.recipient) and (not self.cerrado or reabierto) and not revision_tecnica


# --- Datos que se derivan de lo guardado de un caso (funciones puras: no tocan la base)

STATUS_LABELS = {
    QuoteStatus.REVIEW_PENDING: "Requiere aclaraciones",
    QuoteStatus.READY_REVIEW: "Información suficiente para revisión interna",
    QuoteStatus.SENDING: "Enviando",
    QuoteStatus.REVIEW: "Revisión técnica interna",
    QuoteStatus.WAITING: "Esperando cliente",
    QuoteStatus.DRAFT_CREATED: "Borrador preparado",
    QuoteStatus.RESPONSE_SENT: "Respuesta enviada",
    QuoteStatus.READY: "Proyecto creado",
    QuoteStatus.MISSING: "Hilo no disponible",
    QuoteStatus.IGNORED: "Descartada",
}


CLIENT_TYPES = [
    "No clasificado", "Empresa privada", "Entidad pública",
    "Consultor o aliado", "Particular",
]


SECTORS = [
    "No clasificado", "Energía", "Infraestructura", "Hidrocarburos",
    "Ambiente", "Catastro", "Otro",
]


def load_analysis(value: str) -> Analisis:
    """El análisis guardado de un caso como diccionario ({} si falta o está dañado)."""
    try:
        return json.loads(value) if value else {}
    except (json.JSONDecodeError, TypeError):
        return {}


def service_names(analysis: Analisis) -> list[str]:
    """Servicios o técnicas de las actividades del caso, sin repetir y en orden."""
    services = []
    for activity in analysis.get("activities", []):
        service = (activity.get("servicio_tecnica") or activity.get("activity") or "").strip()
        if service:
            services.append(service)
    return list(dict.fromkeys(services))


REPLY_OVERDUE_MINUTES = 30      # una respuesta sin analizar más de este tiempo se resalta


REPLY_TOLERANCE_SECONDS = 120   # la fecha del correo y la de llegada pueden diferir unos segundos


def timestamp(value: str) -> float:
    """Fecha ISO como segundos (0.0 si no se puede leer): permite compararlas sin que un dato malo rompa nada."""
    try:
        return datetime.fromisoformat(value).timestamp()
    except (TypeError, ValueError):
        return 0.0


def unanalyzed_reply(latest: tuple[str, str] | None, last_inbound_id: str, analysis: Analisis,
                      first_seen_at: str, status: str) -> str:
    """Hora de la respuesta del cliente que el análisis todavía no recoge ('' si no hay)."""
    if not latest or status in (QuoteStatus.IGNORED, QuoteStatus.MISSING):
        return ""
    occurred_at, ref = latest
    if ref == last_inbound_id:
        return ""
    analyzed = timestamp(analysis.get("received_at") or first_seen_at)
    return occurred_at if timestamp(occurred_at) > analyzed + REPLY_TOLERANCE_SECONDS else ""


def model_proposal(analysis: Analisis) -> list[dict]:
    """Filas que el modelo propone y que difieren de la tabla que editó una persona."""
    proposed = analysis.get("pending_model_activities")
    if not isinstance(proposed, list):
        return []
    mine = {str(item.get("activity", "")).strip().casefold(): item
            for item in analysis.get("activities", []) if isinstance(item, dict)}
    changes = []
    for item in proposed:
        if not isinstance(item, dict):
            continue
        name = str(item.get("activity", "")).strip()
        current = mine.get(name.casefold())
        detail = " ".join(str(item.get(key) or "") for key in ("quantity", "unit")).strip()
        if current is None:
            changes.append({"kind": "nueva", "activity": name, "detail": detail})
        elif any(str(current.get(key) or "") != str(item.get(key) or "")
                 for key in ("quantity", "unit", "ubicacion", "entregable", "plazo_restriccion")):
            changes.append({"kind": "distinta", "activity": name, "detail": detail})
    return changes


def waiting_since(status: str, sent_at: str, latest: tuple[str, str] | None) -> str:
    """Desde cuándo espera el caso la respuesta del cliente ('' si no está esperando)."""
    if status not in (QuoteStatus.WAITING, QuoteStatus.RESPONSE_SENT) or not sent_at:
        return ""
    if latest and timestamp(latest[0]) >= timestamp(sent_at):
        return ""            # el cliente ya respondió después
    return sent_at


def status_label(status: Any, assessment: dict) -> str:
    """Etiqueta del estado; en revisión la da la evaluación de recepción (qué le falta al caso)."""
    review_states = {QuoteStatus.REVIEW_PENDING, QuoteStatus.READY_REVIEW, QuoteStatus.REVIEW}
    if status in review_states and assessment:
        return str(assessment["label"])
    return STATUS_LABELS.get(status) or str(status)
