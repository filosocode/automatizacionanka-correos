"""La cola de revisión del piloto local: el paquete de texto de cada caso y los casos que esperan a una persona.

Es lo que usan el modo local y el comando `reviews`; el dashboard y el modo Work trabajan con los casos directamente.
"""

from __future__ import annotations

import json
from pathlib import Path

from anka.dominio.analisis import Analisis
from anka.dominio.status import QuoteStatus
from anka.infra import settings
from anka.infra.store import connect
from anka.servicios.repositorio import filas_de_la_cola_de_revision


def save_review_packet(thread_id: str, recipient: str, analysis: Analisis,
                       questions: list[str], body: str | None,
                       folder_name: str | None = None) -> Path:
    """Guarda en `review_queue` el paquete de texto para revisar un caso y devuelve su ruta."""
    queue = settings.DATA_DIR / "review_queue"
    queue.mkdir(parents=True, exist_ok=True)
    path = queue / f"{thread_id}.txt"
    content = [
        f"Hilo: {thread_id}",
        f"Destinatario: {recipient}",
        f"Estado sugerido: {'preguntar al cliente' if body else 'información suficiente para revisión interna'}",
        "",
        "Resumen:",
        analysis.get("summary", ""),
        "",
        "Actividades:",
        json.dumps(analysis.get("activities", []), ensure_ascii=False, indent=2),
        "",
    ]
    if analysis.get("routing"):
        content.extend([
            "Enrutamiento del correo:",
            json.dumps(analysis["routing"], ensure_ascii=False, indent=2),
            "",
        ])
    if analysis.get("scope_dependencies"):
        content.extend([
            "Dependencias de alcance:",
            json.dumps(analysis["scope_dependencies"], ensure_ascii=False, indent=2),
            "",
        ])
    if analysis.get("contradictions"):
        content.extend([
            "Contradicciones detectadas:",
            json.dumps(analysis["contradictions"], ensure_ascii=False, indent=2),
            "",
        ])
    if analysis.get("attachments"):
        content.extend([
            "Adjuntos y bloqueos:",
            json.dumps(analysis["attachments"], ensure_ascii=False, indent=2),
            "",
        ])
    if questions:
        content.extend(["Preguntas faltantes:", *[f"- {q}" for q in questions], ""])
    if body:
        content.extend(["Borrador de correo:", body, ""])
    if folder_name:
        content.extend(["Carpeta propuesta:", folder_name, ""])
    content.extend([
        "JSON completo:",
        json.dumps(analysis, ensure_ascii=False, indent=2),
        "",
    ])
    path.write_text("\n".join(content), encoding="utf-8")
    return path


def review_packet_path(thread_id: str) -> Path:
    """Ruta del paquete de revisión de un caso."""
    return settings.DATA_DIR / "review_queue" / f"{thread_id}.txt"


def review_action(status: str) -> tuple[str, str]:
    """Etiqueta de estado y acción sugerida para la cola de revisión según el estado."""
    if status == QuoteStatus.REVIEW_PENDING:
        return "Correo por revisar", "Revisar y enviar el correo"
    if status == QuoteStatus.READY_REVIEW:
        return "Información validada", "Emitir la T"
    if status == QuoteStatus.SENDING:
        return "El correo no salió", "Revisar si salió"
    return "Revisión manual", "No disponible"


def pending_reviews() -> list[dict]:
    """Casos que esperan revisión humana, con su paquete de texto."""
    db = connect()
    try:
        rows = filas_de_la_cola_de_revision(db)
    finally:
        db.close()
    reviews = []
    for thread_id, status, recipient, analysis_json in rows:
        try:
            analysis = json.loads(analysis_json) if analysis_json else {}
        except json.JSONDecodeError:
            analysis = {}
        status_label, action_label = review_action(status)
        reviews.append({
            "thread_id": thread_id,
            "status": status,
            "status_label": status_label,
            "action_label": action_label,
            "recipient": recipient,
            "analysis": analysis,
            "packet": review_packet_path(thread_id),
        })
    return reviews


def get_review(thread_id: str) -> dict | None:
    """Una revisión pendiente por hilo, o `None`."""
    for review in pending_reviews():
        if review["thread_id"] == thread_id:
            return review
    return None
