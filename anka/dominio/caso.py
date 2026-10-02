"""`Caso`: la vista de una solicitud de cotización tal como la ven el dashboard y las acciones.

Es un dato inmutable con campos nombrados (antes era un diccionario con claves sueltas, sin dónde mirar qué
trae). Además se puede leer como un mapa (`caso["status"]`, `caso.get("x")`, `dict(caso)`), de modo que las
plantillas y el código que ya existía siguen funcionando mientras se migra a acceso por atributo.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from anka.dominio.registros import Cambio, MapaDeSoloLectura, Vinculo


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
    analysis: dict
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
