"""Qué hacer con un hilo ya leído: la decisión (estado, acción, avisos) sin ejecutar nada.

Son funciones puras: reciben el análisis y la configuración y devuelven una `Decision`. Ejecutarla (enviar, guardar,
crear carpetas) es cosa de `anka.servicios.pipeline.RevisorDeHilos`; por eso esto se prueba sin Gmail ni base.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from enum import StrEnum
from typing import NamedTuple
from anka.dominio.analisis import Adjunto, Analisis
from anka.dominio.status import (
    QuoteStatus,
)
from anka.dominio.reception import (
    question_email, reception_questions, safe_folder_name,
    unresolved_scope_issues,
)



class Action(StrEnum):
    """Efecto externo que exige una decisión, además de guardar el estado."""
    NONE = "none"
    SEND = "send"          # enviar las preguntas al cliente y esperar respuesta
    GMAIL_DRAFT = "draft"  # crear borrador en Gmail
    QUEUE = "queue"        # dejar la respuesta propuesta en la cola de revisión
    FOLDER = "folder"      # crear la carpeta del proyecto en Drive


class Note(NamedTuple):
    """Mensaje de registro de una decisión. `sensitive` = lleva datos de un cliente."""
    text: str
    sensitive: bool = False
    level: int = logging.INFO


@dataclass
class Decision:
    """Qué hacer con un hilo. Se calcula sin tocar Gmail, SQLite ni Drive."""
    status: QuoteStatus
    analysis: Analisis
    logs: list[Note] = field(default_factory=list)
    action: Action = Action.NONE
    packet: bool = False               # guardar ficha de revisión
    questions: list[str] = field(default_factory=list)
    body: str = ""                     # borrador de preguntas al cliente
    folder_name: str = ""


def decide_prefilter(thread_id: str, noise_reason: str | None, email: dict) -> Decision | None:
    """Descarta un hilo sin analizarlo solo si es ruido evidente; None si debe analizarse.

    El motivo y el asunto/remitente se guardan para que una persona pueda revisar lo
    descartado en el dashboard y rescatarlo.
    """
    if not noise_reason:
        return None
    return Decision(
        QuoteStatus.IGNORED, {"ignored_reason": noise_reason, "email": email},
        logs=[Note(f"{thread_id}: descartado sin analizar: {noise_reason}")],
    )


def _decide_not_a_request(thread_id: str, analysis: Analisis, blocked: list[Adjunto]) -> Decision:
    """El modelo concluyó que no es una solicitud: se descarta, salvo que haya adjuntos que no se pudieron leer."""
    if blocked:
        # La solicitud puede estar dentro del adjunto que no se pudo leer: no se descarta.
        return Decision(
            QuoteStatus.REVIEW, analysis, packet=True,
            logs=[Note(
                f"{thread_id}: el texto no parece una solicitud, pero hay "
                f"{len(blocked)} adjunto(s) no legible(s); requiere revisión manual",
                level=logging.WARNING,
            )],
        )
    analysis.setdefault("ignored_reason", "El modelo no la identificó como solicitud de cotización")
    return Decision(
        QuoteStatus.IGNORED, analysis,
        logs=[Note(f"{thread_id}: no es solicitud de cotización")],
    )


def _decide_ask_client(thread_id: str, analysis: Analisis, logs: list[Note], questions: list[str], recipient: str,
                       is_forwarded: bool, *, review_only: bool, gmail_drafts: bool) -> Decision:
    """Faltan datos: se propone pedirlos al cliente (borrador de Gmail, cola de revisión o envío directo)."""
    body = question_email(questions, is_forwarded)
    logs.append(Note(f"{thread_id}: faltan datos; se propone pedirlos al cliente"))
    logs.append(Note(f"{thread_id}: Borrador para {recipient}:\n{body}", sensitive=True))
    if gmail_drafts:
        return Decision(
            QuoteStatus.DRAFT_CREATED, analysis, logs, Action.GMAIL_DRAFT,
            packet=True, questions=questions, body=body,
        )
    if review_only:
        return Decision(
            QuoteStatus.REVIEW_PENDING, analysis, logs, Action.QUEUE,
            packet=True, questions=questions, body=body,
        )
    return Decision(QuoteStatus.WAITING, analysis, logs, Action.SEND, body=body)


def _decide_complete_scope(thread_id: str, analysis: Analisis, logs: list[Note], config: dict,
                           *, review_only: bool) -> Decision:
    """El alcance está completo: se propone la carpeta del proyecto (o se crea, si no es solo revisión)."""
    name = safe_folder_name(
        analysis["project_name"], thread_id, config.get("folder_prefix", "Cotizacion")
    )
    logs.append(Note(f"{thread_id}: alcance completo"))
    logs.append(Note(f"{thread_id}: carpeta propuesta: {name}", sensitive=True))
    if review_only:
        return Decision(
            QuoteStatus.READY_REVIEW, analysis, logs, Action.QUEUE,
            packet=True, folder_name=name,
        )
    return Decision(QuoteStatus.READY, analysis, logs, Action.FOLDER, folder_name=name)


def decide_analysis(
    thread_id: str, analysis: Analisis, blocked: list[Adjunto], recipient: str, is_forwarded: bool,
    config: dict, *, review_only: bool, gmail_drafts: bool,
) -> Decision:
    """Decide el destino de un hilo ya analizado."""
    if not analysis["is_quote_request"]:
        return _decide_not_a_request(thread_id, analysis, blocked)
    logs = [
        Note(f"{thread_id}: solicitud de cotización analizada; "
             f"{len(analysis['activities'])} actividad(es)"),
        Note(f"{thread_id}: {analysis['summary']}", sensitive=True),
        Note(json.dumps(analysis["activities"], ensure_ascii=False, indent=2), sensitive=True),
    ]
    required = config["required_fields"]
    if blocked:
        names = ", ".join(item["filename"] for item in blocked)
        logs.append(Note(
            f"{thread_id}: {len(blocked)} adjunto(s) no legible(s); requiere revisión manual",
            level=logging.WARNING,
        ))
        logs.append(Note(f"{thread_id}: adjuntos no legibles: {names}", sensitive=True,
                         level=logging.WARNING))
        return Decision(
            QuoteStatus.REVIEW, analysis, logs, packet=True,
            questions=reception_questions(analysis, required),
        )
    if not analysis["activities"]:
        logs.append(Note(
            f"{thread_id}: sin actividades identificables; requiere revisión manual",
            level=logging.WARNING,
        ))
        return Decision(QuoteStatus.REVIEW, analysis, logs)
    if unresolved_scope_issues(analysis):
        logs.append(Note(
            f"{thread_id}: dependencias o contradicciones pendientes; requiere revisión manual",
            level=logging.WARNING,
        ))
        return Decision(
            QuoteStatus.REVIEW, analysis, logs, packet=True,
            questions=reception_questions(analysis, required),
        )
    questions = reception_questions(analysis, required)
    if questions:
        return _decide_ask_client(thread_id, analysis, logs, questions, recipient, is_forwarded,
                                  review_only=review_only, gmail_drafts=gmail_drafts)
    return _decide_complete_scope(thread_id, analysis, logs, config, review_only=review_only)
