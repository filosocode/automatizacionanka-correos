"""Ciclo de revisión del buzón: decide qué hacer con cada hilo y lo aplica."""

from __future__ import annotations

import json
import logging
import sqlite3
import time
from dataclasses import dataclass, field
from datetime import datetime
from collections.abc import Sequence
from enum import StrEnum
from typing import Any, NamedTuple
from anka.dominio.status import (
    QuoteStatus,
)
from anka.infra import settings
from anka.infra.work_heartbeat import bridge_state
from anka.infra.logging_setup import SENSITIVE, get_logger
from anka.servicios.drive_layout import (
    AmbiguousClient, ClientNotFound, create_project_folder, year_mismatch,
)
from anka.servicios.drive_contents import populate_project_folder
from anka.infra.google_services import services
from anka.dominio.correo import (
    message_text,
    forwarded_routing,
    header,
    inbound_messages,
    message_datetime,
)
from anka.infra.mail import (
    candidate_thread_ids,
    create_reply_draft,
    extract_message_attachments,
    own_addresses,
    send_reply,
)
from anka.dominio.reception import (
    attachment_report, automated_noise_reason, draft_body_with_review_note,
    mentions_commercial_terms, question_email, reception_questions, safe_folder_name,
    unresolved_scope_issues,
)
from anka.infra.llm import analyze
from anka.infra.modes import (
    ensure_local_or_api_mode,
)
from anka.infra.settings import load_config
from anka.dominio.activities import normalize_estado
from anka.servicios.change_control import apply_guard, find_codes
from email.utils import parseaddr
from anka.infra.store import connect, save

from anka.servicios.quotes import save_review_packet


log = get_logger(__name__)
LAST_CHECK_FILE = "last_check.json"


def _quote_snapshot() -> dict[str, tuple]:
    db = connect()
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
        log.exception("Error en la revisión de Gmail (%s)", source)
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
    db = connect()
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
        last = latest_case_activity()
        tail = f" Último caso actualizado: {last[:16].replace('T', ' ')} ({_ago(last)})." if last else ""
        return "Motor ChatGPT Work. " + bridge_state()[1] + tail
    label = "Ollama" if mode == "local" else "API de OpenAI"
    last_check = read_last_check()
    if last_check:
        text = describe_check(last_check)
        return f"Motor {label}. {text} ({_ago(last_check['at'])}). El botón Actualizar correo revisa al instante."
    return (
        f"Motor {label}. Aún no se ha revisado Gmail desde este panel. La revisión periódica "
        "solo ocurre si el piloto (Piloto borradores Gmail.bat) está abierto, cada 5 min."
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
    analysis: dict
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


def decide_analysis(
    thread_id: str, analysis: dict, blocked: list[dict], recipient: str, is_forwarded: bool,
    config: dict, *, review_only: bool, gmail_drafts: bool,
) -> Decision:
    """Decide el destino de un hilo ya analizado."""
    if not analysis["is_quote_request"]:
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
    for note in d.logs:
        log.log(note.level, "%s", note.text, extra=SENSITIVE if note.sensitive else None)
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
        _create_project_folder_or_hold(ctx, thread_id, last, recipient, d, thread)
    elif d.action is Action.GMAIL_DRAFT:
        save(db, thread_id, last["id"], d.status, recipient, d.analysis,
             sent_message_id=draft_id)
        log.info("%s: borrador creado en Gmail: %s", thread_id, draft_id)
        log.info("%s: destinatario del borrador: %s", thread_id, recipient, extra=SENSITIVE)
        log.info("%s: ficha de apoyo guardada: %s", thread_id, packet.name if packet else "")
    else:
        save(db, thread_id, last["id"], d.status, recipient, d.analysis)
        if packet:
            verb = (
                "guardado para revisión humana" if d.action is Action.QUEUE
                else "ficha de revisión guardada"
            )
            log.info("%s: %s: %s", thread_id, verb, packet.name)
    if d.action is Action.SEND:
        log.info("%s: preguntas enviadas al cliente; se espera respuesta", thread_id)


def confirmed_client(db: sqlite3.Connection, thread_id: str, analysis: dict, recipient: str) -> str:
    """Cliente que una persona ya confirmó: el de este hilo o el de otro del mismo remitente."""
    row = db.execute(
        "SELECT client_name FROM quote_metadata WHERE thread_id=? AND client_name <> ''",
        (thread_id,),
    ).fetchone()
    if row:
        return str(row[0])
    routing = analysis.get("routing", {})
    sender = routing.get("original_sender_email") or (
        None if routing.get("is_forwarded") else recipient
    )
    if not sender:
        return ""
    row = db.execute(
        """SELECT m.client_name FROM quote_metadata m JOIN quotes q ON q.thread_id = m.thread_id
           WHERE m.client_name <> '' AND m.thread_id <> ?
             AND (lower(q.recipient) = lower(?)
                  OR lower(json_extract(q.analysis_json, '$.routing.original_sender_email')) = lower(?))
           ORDER BY m.updated_at DESC LIMIT 1""",
        (thread_id, sender, sender),
    ).fetchone()
    return str(row[0]) if row else ""


def _create_project_folder_or_hold(
    ctx: RunContext, thread_id: str, last: dict, recipient: str, d: Decision, thread: dict,
) -> None:
    """Crea `Año/Cliente/Solicitud` solo si el cliente es seguro; si no, lo deja para una persona.

    Nunca crea una carpeta de cliente por cuenta propia (ver drive_layout).
    """
    db = ctx.db
    assert db is not None
    parent = ctx.config["drive_parent_id"]
    hold: tuple[str, str] | None = None  # (código genérico, mensaje completo)
    folder: dict | None = None
    mismatch = year_mismatch(ctx.drive, parent, d.analysis.get("received_at", ""))
    client = confirmed_client(db, thread_id, d.analysis, recipient)
    if mismatch:
        hold = ("año de la carpeta padre", mismatch)
    elif not client:
        hold = ("cliente sin confirmar",
                "No hay un cliente confirmado para este remitente. Indica la carpeta del cliente.")
    else:
        try:
            folder = create_project_folder(ctx.drive, parent, client, d.folder_name).folder
        except ClientNotFound as exc:
            hold = ("cliente sin carpeta", str(exc))
        except AmbiguousClient as exc:
            hold = ("carpetas de cliente ambiguas", str(exc))
    if hold or folder is None:
        code, message = hold or ("sin carpeta", "No se pudo crear la carpeta.")
        analysis = d.analysis | {"drive": {"pending": message}}
        save_review_packet(thread_id, recipient, analysis, [], None, d.folder_name)
        save(db, thread_id, last["id"], QuoteStatus.READY_REVIEW, recipient, analysis)
        log.warning("%s: carpeta del proyecto pendiente de confirmación (%s)", thread_id, code)
        log.info("%s: %s", thread_id, message, extra=SENSITIVE)
        return
    d.analysis.pop("drive", None)
    save(db, thread_id, last["id"], d.status, recipient, d.analysis, folder_id=folder["id"])
    log.info("%s: carpeta creada/encontrada: %s", thread_id, folder["id"])
    try:
        quote = {"thread_id": thread_id, "analysis": d.analysis, "status": d.status}
        report = populate_project_folder(
            ctx.gmail, ctx.drive, thread, folder["id"], quote, ctx.account_addresses)
        log.info("%s: %s", thread_id, report.summary())
    except Exception:
        log.exception("%s: no se pudo llenar la carpeta de Drive", thread_id)


def _skip_reason(row: Sequence[Any] | None, last_id: str, gmail_drafts: bool) -> str | None:
    """None si el hilo debe procesarse; si no, el motivo ("" = omitir sin avisar)."""
    if not row or row[0] != last_id:
        return None
    if row[1] == QuoteStatus.SENDING:
        return "envío interrumpido; revisar el hilo antes de reintentar"
    if gmail_drafts and row[1] == QuoteStatus.REVIEW_PENDING:
        return None
    return ""


def _rescued(row: Sequence[Any] | None) -> bool:
    """¿Una persona pidió reanalizar este hilo descartado desde el dashboard?"""
    if not row or len(row) < 3:
        return False
    try:
        return bool(json.loads(row[2] or "{}").get("force_analysis"))
    except (TypeError, ValueError):
        return False


def process_thread(ctx: RunContext, thread_id: str, thread: dict) -> None:
    config = ctx.config
    inbound = inbound_messages(thread, ctx.account_addresses)
    if not inbound:
        log.info("%s: solo contiene mensajes propios; se omite", thread_id)
        return
    last = inbound[-1]
    routing = forwarded_routing(last, config)
    recipient = routing["selected_recipient"]
    if not recipient:
        log.warning("%s: sin remitente; requiere revisión", thread_id)
        return
    row = ctx.db.execute(
        "SELECT last_inbound_id, status, analysis_json FROM quotes WHERE thread_id=?",
        (thread_id,),
    ).fetchone() if ctx.db else None
    skip = _skip_reason(row, last["id"], ctx.gmail_drafts)
    if skip is not None:
        if skip:
            log.warning("%s: %s", thread_id, skip)
        return
    email = {"subject": header(last, "Subject"), "from": header(last, "From")}
    noise = None if _rescued(row) else automated_noise_reason(inbound)
    if noise and mentions_commercial_terms(inbound, config.get("commercial_filter_terms", [])):
        noise = None  # un aviso automático que habla de cotizaciones sí se analiza
    decision = decide_prefilter(thread_id, noise, email)
    if decision is None:
        attachments = []
        for message in inbound:
            attachments.extend(extract_message_attachments(ctx.gmail, message))
        blocked = [item for item in attachments if item.get("status") == "bloqueado"]
        required = config["required_fields"]
        analysis = analyze(inbound, required, config["llm"], attachments)
        analysis["received_at"] = message_datetime(last)
        analysis["routing"] = routing
        analysis["email"] = email
        analysis["attachments"] = attachment_report(attachments, analysis, required)
        for row in analysis.get("activities", []):
            if isinstance(row, dict) and "estado" in row:
                row["estado"] = normalize_estado(row["estado"])  # R12
        if ctx.db:
            # R11: con T asignada, la tabla solo cambia por el contacto registrado y citando el código
            sender_name, sender_email = parseaddr(header(last, "From"))
            body_text = message_text(last.get("payload", {}))
            apply_guard(
                ctx.db, thread_id, analysis, routing.get("original_sender_email") or sender_email,
                sender_name, sorted(find_codes(email["subject"] + "\n" + body_text)), last["id"],
                email["subject"], " ".join(body_text.split())[:160], "python")
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
    db = connect() if apply else None
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
            thread_ids.update(row[0] for row in db.execute(
                f"SELECT thread_id FROM quotes WHERE status='{QuoteStatus.IGNORED}' "
                "AND json_extract(analysis_json, '$.force_analysis') = 1"
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
                log.warning("%s: no se pudo leer el hilo en Gmail: %s", thread_id, exc)
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
    log.info("Vigilando Gmail cada %s segundos. Presiona Ctrl+C para detener.", seconds)
    try:
        while True:
            log.info(time.strftime("\n[%Y-%m-%d %H:%M:%S] Revisando buzón..."))
            ensure_local_or_api_mode(load_config())  # en modo Work el bot se detiene
            try:
                checked_run(apply, review_only, gmail_drafts, "piloto")
            except Exception:
                log.exception("Error en ciclo de revisión")
            time.sleep(seconds)
    except KeyboardInterrupt:
        log.info("\nVigilancia detenida por el usuario.")
