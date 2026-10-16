"""La forma del análisis de una solicitud: lo que el modelo (o Work) devuelve y el dashboard guarda como JSON.

Son `TypedDict`: en ejecución siguen siendo diccionarios (así se guardan en la base y los escribe Work), pero mypy
conoce sus claves y tipos, de modo que una clave mal escrita o un valor del tipo equivocado se detecta al verificar
y no cuando un cliente ya está mirando la pantalla. El contrato con Work está en `spec/03-contrato-work.md`.

Todos los campos son opcionales (`total=False`) porque un análisis se va completando: nace del modelo o de Work, y
luego el piloto añade el enrutamiento, los adjuntos y las decisiones de una persona.
"""

from __future__ import annotations

from typing import Any, TypedDict


class Actividad(TypedDict, total=False):
    """Una fila de la tabla de actividades."""

    activity: str
    servicio_tecnica: str
    ubicacion: str
    quantity: str
    unit: str
    entregable: str
    plazo_restriccion: str
    fuente: str
    estado: str          # confirmado | pendiente | contradictorio (R12)
    notes: str


class Campo(TypedDict, total=False):
    """Un dato de la solicitud (nombre y valor) que el modelo extrajo."""

    name: str
    value: str
    source: str


class EvidenciaAdjunto(TypedDict, total=False):
    """Hallazgo ligado a una ubicación concreta dentro de un adjunto."""

    topic: str
    value: str
    location: str
    excerpt: str


class Dependencia(TypedDict, total=False):
    """Algo de lo que depende el alcance (p. ej. un archivo por validar); pendiente manda el caso a revisión técnica."""

    description: str
    source: str
    status: str


class Contradiccion(TypedDict, total=False):
    """Un dato técnico que dos fuentes dicen distinto."""

    topic: str
    details: str
    sources: list[str]
    status: str


class Adjunto(TypedDict, total=False):
    """Un archivo adjunto y si se pudo leer."""

    filename: str
    status: str          # extraido | lectura_parcial | bloqueado | ignorado | registrado
    reason: str
    coverage: str
    extracted_chars: int
    reviewed_sections: list[str]
    blocked_fields: list[str]
    mime_type: str
    source_message_id: str
    text: str            # texto extraído; no se guarda en el análisis, solo viaja hasta el modelo
    evidence: list[EvidenciaAdjunto]


class Enrutamiento(TypedDict, total=False):
    """A quién se responde y si el correo llegó reenviado por alguien interno."""

    original_sender: str
    original_sender_email: str
    internal_forwarder: str
    selected_recipient: str
    is_forwarded: bool
    reply_policy: str


class RespuestaWork(TypedDict, total=False):
    """El borrador de respuesta al cliente que dejó ChatGPT Work."""

    text: str
    draft_id: str


class RevisionManual(TypedDict, total=False):
    """La decisión de una persona sobre un caso en revisión técnica."""

    decision: str        # sufficient | ask_client
    at: str
    questions: list[str]
    sin_respuesta: bool  # se siguió con la T sin esperar la respuesta del cliente (U-28)


class RevisionDeLectura(TypedDict, total=False):
    """Una persona revisó por su cuenta los archivos que Anka no pudo verificar (U-29)."""

    at: str
    files: list[str]     # los archivos que seguían sin verificar cuando la persona asumió la revisión


class EdicionManual(TypedDict, total=False):
    """Marca de que una persona editó la tabla: un reproceso del modelo no la pisa."""

    edited_at: str
    rows: int


class Analisis(TypedDict, total=False):
    """El análisis completo de una solicitud."""

    is_quote_request: bool
    project_name: str
    summary: str
    activities: list[Actividad]
    fields: list[Campo]
    other_questions: list[str]
    question_audit: list[dict[str, object]]
    evidence_register: list[dict[str, object]]
    question_quality_warnings: list[str]
    correcciones_de_work: list[str]      # T-28: lo que Work escribió distinto de lo esperado y se corrigió solo
    sin_analisis_de_work: dict[str, Any]   # T-36: {motivo, campos, fecha} del caso mínimo (sin análisis legible)
    scope_dependencies: list[Dependencia]
    contradictions: list[Contradiccion]
    attachments: list[Adjunto]
    routing: Enrutamiento
    work_reply: RespuestaWork
    work_model: str           # T-32: el modelo que declaró Work (solo interno)
    work_message_id: str      # el mensaje del cliente que analizó Work (puente, T-46)
    work_skill_version: str   # T-32: la versión del prompt que declaró Work (solo interno)
    manual_review: RevisionManual
    ultimas_preguntas: list[str]   # lo último que la persona dejó en el cuadro de preguntas para el cliente (U-31)
    manual_edit: EdicionManual
    pending_model_activities: list[Actividad]
    propuesta_descartada: list[Actividad]   # U-57: la propuesta de Work que se dejó de lado
    drive: dict[str, str]
    email: dict[str, str]
    received_at: str
    gmail_url: str
    engine: str
    DRIVE_PREFLIGHT: dict[str, Any]
    reading_audit: dict[str, Any]
    reading_review: RevisionDeLectura   # U-29: la lectura la revisó una persona; solo lo escribe el dashboard
    reading_summary: dict[str, Any]
    evidence_check: dict[str, Any]   # T-38: lo que se comprobó de la tabla de Work contra los textos del expediente
    related_threads: list[dict[str, Any]]
    applicability: dict[str, Any]
    analysis_complete: bool
    force_analysis: bool
    preguntas: list[Any]                 # preguntas del caso con la decisión de una persona (U-22)
    preguntas_enviadas: dict[str, Any]   # {en, preguntas}: las preguntas que llevaba el último correo enviado (U-22)
    analysis_failure: dict[str, Any]     # {motivo, intentos, proximo_intento}: el hilo no se pudo analizar (T-26)
    lectura_por_tramos: dict[str, Any]   # {adjuntos: [{adjunto, tramos, hallazgos}], niveles}: leídos por tramos
    ignored_reason: str
    merged_into: str            # hilo del caso al que se unió este (descartado como «unido»)
