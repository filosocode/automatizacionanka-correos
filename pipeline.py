"""Ciclo de revisión del buzón: decide qué hacer con cada hilo y lo aplica."""

from __future__ import annotations

import json
import sqlite3
import time
from dataclasses import dataclass, field
from datetime import datetime
from collections.abc import Sequence
from enum import Enum
from typing import Any
from status import (
    QuoteStatus,
)
import settings
from google_services import services, ensure_folder
from mail import (
    candidate_thread_ids, create_reply_draft, extract_message_attachments,
    forwarded_routing, inbound_messages, message_datetime, own_addresses, send_reply,
)
from reception import (
    attachment_report, blank_analysis, commercial_candidate, draft_body_with_review_note,
    question_email, reception_questions, safe_folder_name,
    unresolved_scope_issues,
)
from llm import analyze
from modes import (
    automation_interval_label, codex_automation_status, ensure_local_or_api_mode,
)
from settings import load_config
from store import save

from quotes import init_db, save_review_packet


LAST_CHECK_FILE = "last_check.json"


def _quote_snapshot() -> dict[str, tuple]:
    db = init_db()
    try:
        return {
            row["thread_id"]: (row["status"], row["updated_at"])
            for row in db.execute("SELECT thread_id, status, updated_at FROM quotes")
        }
    finally:
        db.close()


def read_last_check() -> dict | None:
    try:
        return json.loads((settings.DATA_DIR / LAST_CHECK_FILE).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def checked_run(apply: bool, review_only: bool, gmail_drafts: bool, source: str) -> dict:
    """Ejecuta una revision de Gmail y registra cuando, cuanto tardo y que cambio."""
    before = _quote_snapshot()
    started = time.time()
    error = ""
    try:
        run(apply, review_only, gmail_drafts)
    except (Exception, SystemExit) as exc:
        error = str(exc) or exc.__class__.__name__
    after = _quote_snapshot()
    result = {
        "at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "source": source,
        "seconds": round(time.time() - started, 1),
        "new": len([key for key in after if key not in before]),
        "updated": len([key for key in after if key in before and after[key] != before[key]]),
        "error": error,
    }
    settings.DATA_DIR.mkdir(parents=True, exist_ok=True)
    (settings.DATA_DIR / LAST_CHECK_FILE).write_text(
        json.dumps(result, ensure_ascii=False), encoding="utf-8")
    return result


def _ago(iso: str) -> str:
    try:
        seconds = (datetime.now().astimezone() - datetime.fromisoformat(iso)).total_seconds()
    except (TypeError, ValueError):
        return ""
    if seconds < 90:
        return "hace instantes"
    if seconds < 3600:
        return f"hace {int(seconds // 60)} min"
    if seconds < 86400:
        return f"hace {int(seconds // 3600)} h"
    return f"hace {int(seconds // 86400)} d"


def latest_case_activity() -> str:
    db = init_db()
    try:
        row = db.execute(
            "SELECT MAX(COALESCE(updated_at, first_seen_at)) AS ts FROM quotes").fetchone()
    finally:
        db.close()
    return row["ts"] or ""


def describe_check(result: dict) -> str:
    when = result["at"][11:19]
    if result.get("error"):
        return f"La revisión de las {when} falló: {result['error']}"
    return (
        f"Gmail revisado a las {when} en {result['seconds']} s: "
        f"{result['new']} solicitudes nuevas, {result['updated']} actualizadas."
    )


def sync_status_text() -> str:
    try:
        mode = load_config().get("active_mode", "local")
    except SystemExit:
        return "Sin configuración de motor."
    if mode == "chatgpt_work":
        status = codex_automation_status()
        state = {"ACTIVE": "activa", "PAUSED": "pausada"}.get(status or "", "no encontrada")
        last = latest_case_activity()
        tail = f" Último caso actualizado: {last[:16].replace('T', ' ')} ({_ago(last)})." if last else ""
        return (
            f"Motor ChatGPT Work: la automatización está {state} ({automation_interval_label()})."
            + tail
        )
    label = "Ollama" if mode == "local" else "API de OpenAI"
    last_check = read_last_check()
    if last_check:
        text = describe_check(last_check)
        return f"Motor {label}. {text} ({_ago(last_check['at'])}). El botón Actualizar correo revisa al instante."
    return (
        f"Motor {label}. Aún no se ha revisado Gmail desde este panel. La revisión periódica "
        "solo ocurre si el piloto (Piloto borradores Gmail.bat) está abierto, cada 5 min."
    )


class Action(str, Enum):
    """Efecto externo que exige una decisión, además de guardar el estado."""
    NONE = "none"
    SEND = "send"          # enviar las preguntas al cliente y esperar respuesta
    GMAIL_DRAFT = "draft"  # crear borrador en Gmail
    QUEUE = "queue"        # dejar la respuesta propuesta en la cola de revisión
    FOLDER = "folder"      # crear la carpeta del proyecto en Drive


@dataclass
class Decision:
    """Qué hacer con un hilo. Se calcula sin tocar Gmail, SQLite ni Drive."""
    status: QuoteStatus
    analysis: dict
    logs: list[str] = field(default_factory=list)
    action: Action = Action.NONE
    packet: bool = False               # guardar ficha de revisión
    questions: list[str] = field(default_factory=list)
    body: str = ""                     # borrador de preguntas al cliente
    folder_name: str = ""


def decide_prefilter(
    thread_id: str, analysis_base: dict, attachments: list[dict], is_candidate: bool,
) -> Decision | None:
    """Descarta el hilo antes de analizarlo; None si debe pasar al análisis."""
    if is_candidate:
        return None
    if any(item.get("status") == "bloqueado" for item in attachments):
        return Decision(
            QuoteStatus.REVIEW, analysis_base, packet=True,
            logs=[f"{thread_id}: adjuntos no legibles impiden clasificar el alcance"],
        )
    return Decision(
        QuoteStatus.IGNORED, {},
        logs=[f"{thread_id}: no pasó el prefiltro comercial"],
    )


def decide_analysis(
    thread_id: str, analysis: dict, blocked: list[dict], recipient: str, is_forwarded: bool,
    config: dict, *, review_only: bool, gmail_drafts: bool,
) -> Decision:
    """Decide el destino de un hilo ya analizado."""
    if not analysis["is_quote_request"]:
        return Decision(
            QuoteStatus.IGNORED, analysis,
            logs=[f"{thread_id}: no es solicitud de cotización"],
        )
    logs = [
        f"{thread_id}: {analysis['summary']}",
        json.dumps(analysis["activities"], ensure_ascii=False, indent=2),
    ]
    required = config["required_fields"]
    if blocked:
        names = ", ".join(item["filename"] for item in blocked)
        logs.append(f"{thread_id}: adjuntos no legibles ({names}); requiere revisión manual")
        return Decision(
            QuoteStatus.REVIEW, analysis, logs, packet=True,
            questions=reception_questions(analysis, required),
        )
    if not analysis["activities"]:
        logs.append(f"{thread_id}: sin actividades identificables; requiere revisión manual")
        return Decision(QuoteStatus.REVIEW, analysis, logs)
    if unresolved_scope_issues(analysis):
        logs.append(
            f"{thread_id}: dependencias o contradicciones pendientes; requiere revisión manual"
        )
        return Decision(
            QuoteStatus.REVIEW, analysis, logs, packet=True,
            questions=reception_questions(analysis, required),
        )
    questions = reception_questions(analysis, required)
    if questions:
        body = question_email(questions, is_forwarded)
        logs.append(f"{thread_id}: faltan datos. Borrador para {recipient}:\n{body}")
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
    name = safe_folder_name(
        analysis["project_name"], thread_id, config.get("folder_prefix", "Cotizacion")
    )
    logs.append(f"{thread_id}: alcance completo; carpeta: {name}")
    if review_only:
        return Decision(
            QuoteStatus.READY_REVIEW, analysis, logs, Action.QUEUE,
            packet=True, folder_name=name,
        )
    return Decision(QuoteStatus.READY, analysis, logs, Action.FOLDER, folder_name=name)


@dataclass
class RunContext:
    config: dict
    gmail: object
    drive: object
    db: sqlite3.Connection | None
    account_addresses: set[str]
    review_only: bool
    gmail_drafts: bool


def apply_decision(
    ctx: RunContext, thread: dict, thread_id: str, last: dict, recipient: str, d: Decision,
) -> None:
    """Ejecuta los efectos de una decisión. Sin base de datos (simulación) solo informa."""
    for line in d.logs:
        print(line)
    db = ctx.db
    if not db:
        return
    packet_body = d.body
    draft_id = None
    if d.action is Action.GMAIL_DRAFT:
        packet_body = draft_body_with_review_note(d.analysis, d.questions, d.body, last)
        draft_id = create_reply_draft(
            ctx.gmail, thread, recipient, packet_body, ctx.account_addresses
        )
    packet = None
    if d.packet:
        packet = save_review_packet(
            thread_id, recipient, d.analysis, d.questions, packet_body or None,
            d.folder_name or None,
        )
    if d.action is Action.SEND:
        save(db, thread_id, last["id"], QuoteStatus.SENDING, recipient, d.analysis)
        sent_id = send_reply(ctx.gmail, thread, recipient, d.body, ctx.account_addresses)
        save(db, thread_id, last["id"], d.status, recipient, d.analysis,
             sent_message_id=sent_id)
    elif d.action is Action.FOLDER:
        folder = ensure_folder(ctx.drive, ctx.config["drive_parent_id"], d.folder_name)
        save(db, thread_id, last["id"], d.status, recipient, d.analysis,
             folder_id=folder["id"])
        print(f"Carpeta creada/encontrada: {folder['id']}")
    elif d.action is Action.GMAIL_DRAFT:
        save(db, thread_id, last["id"], d.status, recipient, d.analysis,
             sent_message_id=draft_id)
        print(f"{thread_id}: borrador creado en Gmail para {recipient}: {draft_id}")
        print(f"{thread_id}: ficha de apoyo guardada: {packet}")
    else:
        save(db, thread_id, last["id"], d.status, recipient, d.analysis)
        if packet:
            verb = "guardado para revisión humana" if d.action is Action.QUEUE                 else "ficha de revisión guardada"
            print(f"{thread_id}: {verb}: {packet}")


def _skip_reason(row: Sequence[Any] | None, last_id: str, gmail_drafts: bool) -> str | None:
    """None si el hilo debe procesarse; si no, el motivo ("" = omitir sin avisar)."""
    if not row or row[0] != last_id:
        return None
    if row[1] == QuoteStatus.SENDING:
        return "envío interrumpido; revisar el hilo antes de reintentar"
    if gmail_drafts and row[1] == QuoteStatus.REVIEW_PENDING:
        return None
    return ""


def process_thread(ctx: RunContext, thread_id: str, thread: dict) -> None:
    config = ctx.config
    inbound = inbound_messages(thread, ctx.account_addresses)
    if not inbound:
        print(f"{thread_id}: solo contiene mensajes propios; se omite")
        return
    last = inbound[-1]
    routing = forwarded_routing(last, config)
    recipient = routing["selected_recipient"]
    if not recipient:
        print(f"{thread_id}: sin remitente; requiere revisión")
        return
    row = ctx.db.execute(
        "SELECT last_inbound_id, status FROM quotes WHERE thread_id=?", (thread_id,)
    ).fetchone() if ctx.db else None
    skip = _skip_reason(row, last["id"], ctx.gmail_drafts)
    if skip is not None:
        if skip:
            print(f"{thread_id}: {skip}")
        return
    attachments = []
    for message in inbound:
        attachments.extend(extract_message_attachments(ctx.gmail, message))
    blocked = [item for item in attachments if item.get("status") == "bloqueado"]
    required = config["required_fields"]

    unreadable = blank_analysis(
        "No fue posible clasificar el hilo porque hay adjuntos sin contenido legible."
    )
    unreadable["routing"] = routing
    unreadable["attachments"] = attachment_report(attachments, unreadable, required)
    decision = decide_prefilter(
        thread_id, unreadable, attachments,
        commercial_candidate(inbound, config.get("commercial_filter_terms", []), attachments),
    )
    if decision is None:
        analysis = analyze(inbound, required, config["llm"], attachments)
        analysis["received_at"] = message_datetime(last)
        analysis["routing"] = routing
        analysis["attachments"] = attachment_report(attachments, analysis, required)
        decision = decide_analysis(
            thread_id, analysis, blocked, recipient, routing["is_forwarded"], config,
            review_only=ctx.review_only, gmail_drafts=ctx.gmail_drafts,
        )
    apply_decision(ctx, thread, thread_id, last, recipient, decision)


def run(apply: bool, review_only: bool = False, gmail_drafts: bool = False) -> None:
    config = load_config()
    ensure_local_or_api_mode(config)
    if review_only and not apply:
        raise SystemExit("--review-only se usa junto con --apply.")
    if gmail_drafts and not apply:
        raise SystemExit("--gmail-drafts se usa junto con --apply.")
    if apply and not config["required_fields"]:
        raise SystemExit("Para --apply define required_fields en config.json.")
    if apply and not review_only and not gmail_drafts and not config.get("drive_parent_id"):
        raise SystemExit("Para --apply define drive_parent_id en config.json.")
    gmail, drive = services()
    db = init_db() if apply else None
    ctx = RunContext(
        config, gmail, drive, db, own_addresses(config, gmail), review_only, gmail_drafts,
    )
    try:
        thread_ids = set(candidate_thread_ids(
            gmail, config.get("gmail_query", "in:inbox newer_than:7d")
        ))
        if db:
            thread_ids.update(row[0] for row in db.execute(
                f"SELECT thread_id FROM quotes WHERE status='{QuoteStatus.WAITING}'"
            ))
            if gmail_drafts:
                thread_ids.update(row[0] for row in db.execute(
                    f"SELECT thread_id FROM quotes WHERE status='{QuoteStatus.REVIEW_PENDING}'"
                ))
        for thread_id in sorted(thread_ids):
            try:
                thread = gmail.users().threads().get(
                    userId="me", id=thread_id, format="full"
                ).execute()
            except Exception as exc:
                print(f"{thread_id}: no se pudo leer el hilo en Gmail: {exc}")
                if db:
                    db.execute(
                        f"UPDATE quotes SET status='{QuoteStatus.MISSING}' WHERE thread_id=?",
                        (thread_id,),
                    )
                    db.commit()
                continue
            process_thread(ctx, thread_id, thread)
    finally:
        if db:
            db.close()


def watch(apply: bool, review_only: bool, seconds: int, gmail_drafts: bool = False) -> None:
    if seconds < 30:
        raise SystemExit("--watch-seconds debe ser de al menos 30 segundos.")
    print(f"Vigilando Gmail cada {seconds} segundos. Presiona Ctrl+C para detener.")
    try:
        while True:
            print(time.strftime("\n[%Y-%m-%d %H:%M:%S] Revisando buzón..."))
            ensure_local_or_api_mode(load_config())  # en modo Work el bot se detiene
            try:
                checked_run(apply, review_only, gmail_drafts, "piloto")
            except Exception as exc:
                print(f"Error en ciclo de revisión: {exc}")
            time.sleep(seconds)
    except KeyboardInterrupt:
        print("\nVigilancia detenida por el usuario.")
