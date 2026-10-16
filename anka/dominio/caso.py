"""`Caso`: la vista de una solicitud de cotización tal como la ven el dashboard y las acciones.

Es un dato inmutable con campos nombrados (antes era un diccionario con claves sueltas, sin dónde mirar qué
trae). Además se puede leer como un mapa (`caso["status"]`, `caso.get("x")`, `dict(caso)`), de modo que las
plantillas y el código que ya existía siguen funcionando mientras se migra a acceso por atributo.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator, Mapping
from dataclasses import dataclass, field
from typing import cast, Any
import json
from datetime import datetime

from anka.dominio.analisis import Actividad, Analisis
from anka.dominio.registros import Cambio, MapaDeSoloLectura, Vinculo
from anka.dominio.correo import valid_email_address
from anka.dominio.status import CLOSED_STATUSES, ESTADOS, QuoteStatus


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
    reception_assessment: Mapping[str, Any]
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
    sheet_id: str = ""            # hoja de Google Sheets del caso ("" si aún no existe)
    sheet_modified: str = ""      # su última modificación en Drive que conocemos (para notar ediciones hechas allí)
    sheet_synced_at: str = ""
    sheet_error: str = ""         # por qué falló la última actualización ("" si salió bien)

    def __hash__(self) -> int:
        return hash(self.thread_id)

    @property
    def sheet_url(self) -> str:
        """Enlace para abrir la hoja de Google Sheets del caso ("" si no existe)."""
        return f"https://docs.google.com/spreadsheets/d/{self.sheet_id}" if self.sheet_id else ""

    # --- reglas del caso que dependen solo de sus propios campos
    @property
    def marca_de_estado(self) -> str:
        """Identifica el estado del caso que la persona tenía a la vista (U-34.4): si cambia entre que abre la pantalla
        y envía, el envío se rechaza para que no salga un correo revisado sobre algo ya distinto."""
        return f"{self.updated_at}|{self.sent_at}"

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

STATUS_LABELS = {estado: info.etiqueta for estado, info in ESTADOS.items()}      # la tabla única: dominio/status.py


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


# --- El resumen de un caso para las listas (U-49): lo que pintan sin leer el análisis completo

VERSION_DEL_RESUMEN = 1
# Claves del análisis que leen las listas y las fichas de proyecto; el resumen guarda su valor exacto, así que leerlas
# del resumen da lo mismo que leerlas del análisis. Si una lista empieza a leer otra clave, no se rompe nada (se lee el
# análisis completo): solo se pierde la ventaja hasta añadirla aquí y subir `VERSION_DEL_RESUMEN`.
CLAVES_DE_LISTA = ("project_name", "received_at", "routing", "attachments", "preguntas_enviadas")


def resumen_de_analisis(analysis: Analisis) -> dict[str, Any]:
    """Lo que las listas necesitan de un análisis: sus claves de lista (valor exacto), si está vacío y lo derivado
    que cuesta recorrer actividades (servicios y propuesta del modelo). Es JSON puro, listo para guardarse."""
    return {
        "v": VERSION_DEL_RESUMEN,
        "vacio": not analysis,
        "claves": {k: v for k, v in cast(Mapping[str, Any], analysis).items() if k in CLAVES_DE_LISTA},
        "servicios": service_names(analysis),
        "propuesta": model_proposal(analysis),
    }


class AnalisisPerezoso(Mapping[str, Any]):
    """El análisis de un caso visto desde una lista: las claves de lista salen del resumen y, solo si alguien pide
    otra cosa, se lee y se interpreta el análisis completo (una vez). Se comporta como el diccionario de siempre."""

    __slots__ = ("_claves", "_vacio", "_cargar", "_completo")

    def __init__(self, claves: Mapping[str, Any], vacio: bool, cargar: Callable[[], Analisis]) -> None:
        self._claves = claves
        self._vacio = vacio
        self._cargar = cargar
        self._completo: Analisis | None = None

    @property
    def cargado(self) -> bool:
        """¿Ya se leyó el análisis completo? (las pruebas lo usan para comprobar que una lista no lo lee)"""
        return self._completo is not None

    def _todo(self) -> Analisis:
        if self._completo is None:
            self._completo = self._cargar()
        return self._completo

    def __getitem__(self, clave: str) -> Any:
        if self._completo is None and clave in CLAVES_DE_LISTA:
            return self._claves[clave]
        return cast(Mapping[str, Any], self._todo())[clave]

    def get(self, clave: str, default: Any = None) -> Any:
        """El valor de la clave, o `default`; las claves de lista salen del resumen sin leer el análisis."""
        if self._completo is None and clave in CLAVES_DE_LISTA:
            return self._claves.get(clave, default)
        return cast(Mapping[str, Any], self._todo()).get(clave, default)

    def __contains__(self, clave: object) -> bool:
        if self._completo is None and clave in CLAVES_DE_LISTA:
            return clave in self._claves
        return clave in self._todo()

    def __bool__(self) -> bool:
        return not self._vacio if self._completo is None else bool(self._completo)

    def __iter__(self) -> Iterator[str]:
        return iter(self._todo())

    def __len__(self) -> int:
        return len(self._todo())

    def __repr__(self) -> str:
        return repr(self._todo())


class EvaluacionPerezosa(Mapping[str, Any]):
    """La evaluación de recepción de un caso de una lista: se calcula la primera vez que alguien la mira."""

    __slots__ = ("_calcular", "_valor")

    def __init__(self, calcular: Callable[[], dict]) -> None:
        self._calcular = calcular
        self._valor: dict | None = None

    def _todo(self) -> dict:
        if self._valor is None:
            self._valor = self._calcular()
        return self._valor

    def __getitem__(self, clave: str) -> Any:
        return self._todo()[clave]

    def __iter__(self) -> Iterator[str]:
        return iter(self._todo())

    def __len__(self) -> int:
        return len(self._todo())

    def __repr__(self) -> str:
        return repr(self._todo())


def combine_activities(current: list[Actividad], incoming: list[Actividad]) -> list[Actividad]:
    """La tabla vigente más lo que trae otro análisis: actualiza las actividades de igual nombre y agrega las nuevas.

    Nunca quita filas: lo que el correo nuevo no menciona se queda como estaba.
    """
    def key(item: Actividad) -> str:
        return str(item.get("activity", "")).strip().casefold()

    news = {key(item): item for item in incoming if key(item)}
    result: list[Actividad] = []
    for item in current:
        new = news.pop(key(item), None)
        result.append(item if new is None else cast(Actividad, {**new, "activity": item.get("activity", "")}))
    return result + list(news.values())


def waiting_since(status: str, sent_at: str, latest: tuple[str, str] | None, sin_preguntas: bool = False) -> str:
    """Desde cuándo espera el caso la respuesta del cliente ('' si no está esperando).

    `sin_preguntas`: el último correo enviado era un acuse, sin preguntas (U-22): no se espera respuesta, así que no hay
    días de espera, ni «sin respuesta», ni «llamar al cliente».
    """
    if status not in (QuoteStatus.WAITING, QuoteStatus.RESPONSE_SENT) or not sent_at or sin_preguntas:
        return ""
    if latest and timestamp(latest[0]) >= timestamp(sent_at):
        return ""            # el cliente ya respondió después
    return sent_at


def status_label(status: Any, assessment: Mapping[str, Any]) -> str:
    """El nombre del estado, el mismo en la insignia, la lista y los pasos (U-31, D-86).

    Sale del estado del flujo y no de la evaluación de recepción: así nunca contradice a la tarjeta «Ahora».
    """
    return STATUS_LABELS.get(status) or str(status)
