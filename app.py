"""Procesa solicitudes de cotización de Gmail y crea carpetas en Drive."""

from __future__ import annotations

import argparse
import base64
from collections import Counter
import html
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
import re
import secrets
import sqlite3
import time
import urllib.error
import urllib.parse
import urllib.request
import webbrowser
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from pathlib import Path

from activities import build_workbook, parse_activities_form
from dashboard_views import (
    _attachments_table, _metric_rows,
    _reception_assessment_panel, activities_editor, render_page,
)
from status import (
    ACTION_STATUSES, CLOSED_STATUSES, REVIEW_QUEUE_STATUSES, QuoteStatus, sql_in,
)
import settings
from google_services import credentials, services, list_folders, ensure_folder
from mail import (
    _decode_websafe, candidate_thread_ids, create_reply_draft, extract_message_attachments,
    forwarded_routing, inbound_messages, message_datetime, own_addresses, reply_email,
    send_reply, valid_email_address,
)
from reception import (
    attachment_report, blank_analysis, commercial_candidate, draft_body_with_review_note,
    question_email, reception_assessment, reception_questions, safe_folder_name,
    suggested_reply, unresolved_scope_issues,
)
from llm import analyze, analyze_text
from modes import (
    automation_interval_label, codex_automation_status, ensure_local_or_api_mode,
    print_mode, set_mode,
)
from internal_context import internal_commercial_context
from settings import ACTIVE_MODES, load_config
from store import add_dashboard_notification, open_db, save















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
    last = read_last_check()
    if last:
        text = describe_check(last)
        return f"Motor {label}. {text} ({_ago(last['at'])}). El botón Actualizar correo revisa al instante."
    return (
        f"Motor {label}. Aún no se ha revisado Gmail desde este panel. La revisión periódica "
        "solo ocurre si el piloto (Piloto borradores Gmail.bat) está abierto, cada 5 min."
    )


























































































def save_review_packet(thread_id: str, recipient: str, analysis: dict,
                       questions: list[str], body: str | None,
                       folder_name: str | None = None) -> Path:
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


















def resolve_case_recipient(thread_id: str) -> dict:
    quote = get_quote(thread_id)
    if not quote:
        raise ValueError("La solicitud ya no existe.")
    config = load_config()
    gmail, _ = services()
    account_addresses = own_addresses(config, gmail)
    thread = gmail.users().threads().get(
        userId="me", id=thread_id, format="full"
    ).execute()
    inbound = inbound_messages(thread, account_addresses)
    if not inbound:
        raise ValueError("No se encontró un mensaje entrante del cliente en el hilo.")
    routing = forwarded_routing(inbound[-1], config)
    recipient = routing.get("selected_recipient", "")
    if not valid_email_address(recipient):
        raise ValueError("Gmail no devolvió una dirección válida para responder.")
    analysis = quote["analysis"]
    analysis["routing"] = routing
    db = init_db()
    try:
        db.execute("""UPDATE quotes SET recipient=?, analysis_json=?, updated_at=?
                      WHERE thread_id=?""", (
            recipient, json.dumps(analysis, ensure_ascii=False),
            datetime.now().astimezone().isoformat(timespec="seconds"), thread_id,
        ))
        db.commit()
    finally:
        db.close()
    return {"quote": get_quote(thread_id), "gmail": gmail, "thread": thread,
            "account_addresses": account_addresses}


def _case_email_context(thread_id: str) -> tuple[dict, object, dict, set[str]]:
    quote = get_quote(thread_id)
    if not quote:
        raise ValueError("La solicitud ya no existe.")
    if quote["status"] in CLOSED_STATUSES:
        raise ValueError("Esta solicitud no admite una respuesta desde el dashboard.")
    context = resolve_case_recipient(thread_id)
    quote = context["quote"]
    gmail = context["gmail"]
    thread = context["thread"]
    account_addresses = context["account_addresses"]
    return quote, gmail, thread, account_addresses


def save_dashboard_draft(thread_id: str, body: str) -> str:
    body = body.strip()
    if not body:
        raise ValueError("El borrador no puede estar vacío.")
    quote, gmail, thread, account_addresses = _case_email_context(thread_id)
    email = reply_email(thread, quote["recipient"], body, account_addresses)
    raw = base64.urlsafe_b64encode(email.as_bytes()).decode("ascii")
    if quote["draft_id"]:
        result = gmail.users().drafts().update(
            userId="me", id=quote["draft_id"],
            body={"message": {"raw": raw, "threadId": thread_id}},
        ).execute()
    else:
        result = gmail.users().drafts().create(
            userId="me", body={"message": {"raw": raw, "threadId": thread_id}}
        ).execute()
    draft_id = result["id"]
    now = datetime.now().astimezone().isoformat(timespec="seconds")
    db = init_db()
    try:
        db.execute(
            f"""UPDATE quotes SET status='{QuoteStatus.DRAFT_CREATED}', draft_id=?, draft_body=?,
                                 updated_at=? WHERE thread_id=?""",
            (draft_id, body, now, thread_id),
        )
        add_dashboard_notification(
            db, thread_id, "draft_saved", draft_id,
            "Borrador guardado en Gmail",
            f"El borrador quedó guardado para {quote['recipient']} y todavía no ha sido enviado.",
        )
        db.commit()
    finally:
        db.close()
    return f"Borrador guardado en Gmail para {quote['recipient']}."


def send_dashboard_reply(thread_id: str, body: str) -> str:
    body = body.strip()
    if not body:
        raise ValueError("El correo no puede estar vacío.")
    quote, gmail, thread, account_addresses = _case_email_context(thread_id)
    email = reply_email(thread, quote["recipient"], body, account_addresses)
    raw = base64.urlsafe_b64encode(email.as_bytes()).decode("ascii")
    if quote["draft_id"]:
        gmail.users().drafts().update(
            userId="me", id=quote["draft_id"],
            body={"message": {"raw": raw, "threadId": thread_id}},
        ).execute()
        result = gmail.users().drafts().send(
            userId="me", body={"id": quote["draft_id"]}
        ).execute()
    else:
        result = gmail.users().messages().send(
            userId="me", body={"raw": raw, "threadId": thread_id}
        ).execute()
    now = datetime.now().astimezone().isoformat(timespec="seconds")
    db = init_db()
    try:
        db.execute(
            f"""UPDATE quotes SET status='{QuoteStatus.RESPONSE_SENT}', sent_message_id=?,
                                 draft_id=NULL, draft_body=?, sent_at=?, updated_at=?
               WHERE thread_id=?""",
            (result["id"], body, now, now, thread_id),
        )
        add_dashboard_notification(
            db, thread_id, QuoteStatus.RESPONSE_SENT, result["id"],
            "Respuesta enviada",
            f"El correo fue enviado a {quote['recipient']} en el mismo hilo.",
        )
        db.commit()
    finally:
        db.close()
    return f"Correo enviado a {quote['recipient']} en el mismo hilo."








def init_db() -> sqlite3.Connection:
    return open_db(settings.DATA_DIR)


def unread_notifications(limit: int = 20) -> list[dict]:
    db = init_db()
    try:
        rows = db.execute("""SELECT n.*, q.analysis_json
                             FROM dashboard_notifications n
                             JOIN quotes q ON q.thread_id = n.thread_id
                             WHERE n.read_at IS NULL
                             ORDER BY n.created_at DESC, n.id DESC
                             LIMIT ?""", (limit,)).fetchall()
    finally:
        db.close()
    result = []
    for row in rows:
        analysis = _load_analysis(row["analysis_json"])
        result.append({
            "id": row["id"], "thread_id": row["thread_id"],
            "kind": row["kind"], "title": row["title"],
            "message": row["message"], "created_at": row["created_at"],
            "project_name": analysis.get("project_name") or "Solicitud sin nombre",
        })
    return result


def mark_notifications_read(thread_id: str | None = None) -> None:
    now = datetime.now().astimezone().isoformat(timespec="seconds")
    db = init_db()
    try:
        if thread_id:
            db.execute("""UPDATE dashboard_notifications SET read_at=?
                          WHERE read_at IS NULL AND thread_id=?""", (now, thread_id))
        else:
            db.execute(
                "UPDATE dashboard_notifications SET read_at=? WHERE read_at IS NULL", (now,)
            )
        db.commit()
    finally:
        db.close()


def review_packet_path(thread_id: str) -> Path:
    return settings.DATA_DIR / "review_queue" / f"{thread_id}.txt"


def review_action(status: str) -> tuple[str, str]:
    if status == QuoteStatus.REVIEW_PENDING:
        return "Requiere aclaraciones", "Enviar preguntas al cliente"
    if status == QuoteStatus.READY_REVIEW:
        return "Información suficiente", "Revisión interna"
    if status == QuoteStatus.SENDING:
        return "Revisar envio interrumpido", "No disponible"
    return "Revision manual", "No disponible"


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


def _load_analysis(value: str) -> dict:
    try:
        return json.loads(value) if value else {}
    except (json.JSONDecodeError, TypeError):
        return {}


def _service_names(analysis: dict) -> list[str]:
    services = []
    for activity in analysis.get("activities", []):
        service = (activity.get("servicio_tecnica") or activity.get("activity") or "").strip()
        if service:
            services.append(service)
    return list(dict.fromkeys(services))


def all_quotes(include_ignored: bool = False) -> list[dict]:
    db = init_db()
    try:
        where = "" if include_ignored else f"WHERE q.status <> '{QuoteStatus.IGNORED}'"
        rows = db.execute(f"""SELECT q.*, m.client_type, m.sector,
                                      m.client_name, m.notes
                               FROM quotes q
                               LEFT JOIN quote_metadata m ON m.thread_id = q.thread_id
                               {where}
                               ORDER BY COALESCE(q.updated_at, q.first_seen_at, '') DESC,
                                        q.thread_id DESC""").fetchall()
    finally:
        db.close()
    quotes = []
    for row in rows:
        analysis = _load_analysis(row["analysis_json"])
        routing = analysis.get("routing", {})
        client_email = routing.get("original_sender_email") or row["recipient"]
        assessment = reception_assessment(analysis) if analysis else {}
        status_label = STATUS_LABELS.get(row["status"], row["status"])
        review_states = {QuoteStatus.REVIEW_PENDING, QuoteStatus.READY_REVIEW, QuoteStatus.REVIEW}
        if row["status"] in review_states and assessment:
            status_label = assessment["label"]
        quotes.append({
            "thread_id": row["thread_id"],
            "status": row["status"],
            "status_label": status_label,
            "recipient": row["recipient"],
            "client_email": client_email,
            "client_name": row["client_name"] or routing.get("original_sender") or client_email,
            "client_type": row["client_type"] or "No clasificado",
            "sector": row["sector"] or "No clasificado",
            "notes": row["notes"] or "",
            "analysis": analysis,
            "reception_assessment": assessment,
            "services": _service_names(analysis),
            "first_seen_at": row["first_seen_at"] or analysis.get("received_at") or "",
            "updated_at": row["updated_at"] or "",
            "folder_id": row["folder_id"],
            "draft_id": row["draft_id"],
            "draft_body": row["draft_body"] or "",
            "sent_at": row["sent_at"] or "",
        })
    return quotes


def get_quote(thread_id: str) -> dict | None:
    return next((item for item in all_quotes(True) if item["thread_id"] == thread_id), None)


def save_quote_metadata(thread_id: str, client_name: str, client_type: str,
                        sector: str, notes: str) -> None:
    if client_type not in CLIENT_TYPES:
        raise ValueError("Tipo de cliente no válido.")
    if sector not in SECTORS:
        raise ValueError("Sector no válido.")
    if not get_quote(thread_id):
        raise ValueError("La solicitud ya no existe.")
    now = datetime.now().astimezone().isoformat(timespec="seconds")
    db = init_db()
    try:
        db.execute("""INSERT INTO quote_metadata
                      (thread_id, client_type, sector, client_name, notes, updated_at)
                      VALUES (?, ?, ?, ?, ?, ?)
                      ON CONFLICT(thread_id) DO UPDATE SET
                      client_type=excluded.client_type, sector=excluded.sector,
                      client_name=excluded.client_name, notes=excluded.notes,
                      updated_at=excluded.updated_at""",
                   (thread_id, client_type, sector, client_name.strip(), notes.strip(), now))
        db.commit()
    finally:
        db.close()


def save_activities(thread_id: str, activities: list[dict]) -> str:
    """Guarda la tabla editada a mano y la marca para que un reproceso no la pise."""
    db = init_db()
    try:
        row = db.execute(
            "SELECT analysis_json FROM quotes WHERE thread_id=?", (thread_id,)
        ).fetchone()
        if not row:
            raise ValueError("La solicitud ya no existe.")
        analysis = _load_analysis(row["analysis_json"])
        now = datetime.now().astimezone().isoformat(timespec="seconds")
        analysis["activities"] = activities
        analysis["manual_edit"] = {"edited_at": now, "rows": len(activities)}
        db.execute(
            "UPDATE quotes SET analysis_json=?, updated_at=? WHERE thread_id=?",
            (json.dumps(analysis, ensure_ascii=False), now, thread_id),
        )
        db.commit()
    finally:
        db.close()
    return f"Tabla guardada ({len(activities)} actividades)."


def dashboard_upsert(payload_base64: str) -> None:
    try:
        raw = _decode_websafe(payload_base64).decode("utf-8")
        payload = json.loads(raw)
    except (ValueError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise SystemExit(f"Registro de dashboard no válido: {exc}") from exc
    required = {"thread_id", "status", "client_email", "project_name"}
    missing = sorted(field for field in required if not str(payload.get(field, "")).strip())
    if missing:
        raise SystemExit("Faltan campos del dashboard: " + ", ".join(missing))
    status_map = {
        "Nueva solicitud": QuoteStatus.REVIEW_PENDING,
        "En análisis": QuoteStatus.REVIEW,
        "Faltan datos": QuoteStatus.REVIEW_PENDING,
        "Requiere aclaraciones del cliente": QuoteStatus.REVIEW_PENDING,
        "Revisión técnica": QuoteStatus.REVIEW,
        "Requiere revisión técnica interna": QuoteStatus.REVIEW,
        "Borrador preparado": QuoteStatus.DRAFT_CREATED,
        "Alcance completo": QuoteStatus.READY_REVIEW,
        "Información suficiente para revisión interna": QuoteStatus.READY_REVIEW,
        "Descartada": QuoteStatus.IGNORED,
    }
    activities = payload.get("activities", [])
    if isinstance(activities, str):
        activities = [{
            "activity": activities,
            "servicio_tecnica": payload.get("service", ""),
            "ubicacion": payload.get("location", ""),
            "quantity": payload.get("magnitude", ""),
            "unit": "",
            "entregable": payload.get("deliverables", ""),
            "plazo_restriccion": payload.get("deadline", ""),
            "fuente": payload.get("source", ""),
            "estado": "confirmado",
            "notes": "",
        }]
    elif isinstance(activities, list):
        activities = [
            item if isinstance(item, dict) else {
                "activity": str(item), "servicio_tecnica": payload.get("service", ""),
                "ubicacion": payload.get("location", ""), "quantity": "", "unit": "",
                "entregable": "", "plazo_restriccion": "", "fuente": "",
                "estado": "pendiente", "notes": "",
            }
            for item in activities
        ]
    missing_data = payload.get("missing_data", [])
    if isinstance(missing_data, str):
        missing_data = [missing_data] if missing_data.strip() else []
    contradictions = payload.get("contradictions", [])
    if isinstance(contradictions, str):
        contradictions = ([{
            "topic": "dato técnico", "details": contradictions,
            "sources": [], "status": "pendiente",
        }] if contradictions.strip() else [])
    dependencies = payload.get("scope_dependencies", [])
    if isinstance(dependencies, str):
        dependencies = ([{
            "description": dependencies, "source": payload.get("source", ""),
            "status": "pendiente",
        }] if dependencies.strip() else [])
    attachments = payload.get("attachments", [])
    if isinstance(attachments, str):
        attachments = ([{
            "filename": attachments, "status": "registrado", "reason": "",
            "blocked_fields": [],
        }] if attachments.strip() else [])
    analysis = {
        "is_quote_request": payload["status"] != "Descartada",
        "project_name": payload["project_name"],
        "summary": payload.get("summary", ""),
        "activities": activities,
        "fields": payload.get("fields", []),
        "other_questions": missing_data,
        "scope_dependencies": dependencies,
        "contradictions": contradictions,
        "attachments": attachments,
        "received_at": payload.get("received_at", ""),
        "gmail_url": payload.get("gmail_url", ""),
        "engine": payload.get("engine", ""),
        "routing": {
            "original_sender": payload.get("client_name", ""),
            "original_sender_email": payload["client_email"],
            "internal_forwarder": payload.get("internal_sender", ""),
            "selected_recipient": payload.get("internal_sender") or payload["client_email"],
            "is_forwarded": bool(payload.get("internal_sender")),
        },
    }
    db = init_db()
    try:
        save(
            db, payload["thread_id"], payload.get("message_id") or payload["thread_id"],
            status_map.get(payload["status"], payload["status"]),
            payload.get("internal_sender") or payload["client_email"], analysis,
        )
    finally:
        db.close()
    client_type = payload.get("client_type", "No clasificado")
    sector = payload.get("sector", "No clasificado")
    if client_type not in CLIENT_TYPES:
        client_type = "No clasificado"
    if sector not in SECTORS:
        sector = "No clasificado"
    save_quote_metadata(
        payload["thread_id"], payload.get("client_name", ""),
        client_type, sector, payload.get("notes", ""),
    )


def pending_reviews() -> list[dict]:
    db = init_db()
    try:
        rows = db.execute(
            f"""SELECT thread_id, status, recipient, analysis_json
               FROM quotes
               WHERE status IN {sql_in(*REVIEW_QUEUE_STATUSES)}
               ORDER BY thread_id"""
        ).fetchall()
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
    for review in pending_reviews():
        if review["thread_id"] == thread_id:
            return review
    return None


def list_reviews() -> None:
    reviews = pending_reviews()
    if not reviews:
        print("No hay revisiones humanas pendientes.")
        return
    for review in reviews:
        packet = review["packet"]
        suffix = f" -> {packet}" if packet.exists() else " -> sin archivo en review_queue"
        print(f"{review['thread_id']} [{review['status']}] {review['recipient']}{suffix}")


def approve_review(
    thread_id: str, body_file: str | None = None, body_text: str | None = None
) -> str:
    config = load_config()
    gmail, drive = services()
    account_addresses = own_addresses(config, gmail)
    db = init_db()
    try:
        row = db.execute(
            """SELECT last_inbound_id, status, recipient, analysis_json
               FROM quotes WHERE thread_id=?""",
            (thread_id,),
        ).fetchone()
        if not row:
            raise SystemExit(f"No existe revisión registrada para el hilo {thread_id}.")
        last_id, status, recipient, analysis_json = row
        analysis = json.loads(analysis_json)
        if status == QuoteStatus.REVIEW_PENDING:
            thread = gmail.users().threads().get(
                userId="me", id=thread_id, format="full"
            ).execute()
            if body_text is not None:
                body = body_text
            elif body_file:
                body = Path(body_file).read_text(encoding="utf-8")
            else:
                body = question_email(
                    reception_questions(analysis, config["required_fields"]),
                    analysis.get("routing", {}).get("is_forwarded", False),
                )
            save(db, thread_id, last_id, QuoteStatus.SENDING, recipient, analysis)
            sent_id = send_reply(gmail, thread, recipient, body, account_addresses)
            save(db, thread_id, last_id, QuoteStatus.WAITING, recipient, analysis,
                 sent_message_id=sent_id)
            message = f"{thread_id}: correo enviado a {recipient}."
            print(message)
            return message
        elif status == QuoteStatus.READY_REVIEW:
            if not config.get("drive_parent_id"):
                raise SystemExit("Para aprobar alcance completo define drive_parent_id en config.json.")
            name = safe_folder_name(
                analysis["project_name"], thread_id,
                config.get("folder_prefix", "Cotizacion"),
            )
            folder = ensure_folder(drive, config["drive_parent_id"], name)
            save(db, thread_id, last_id, QuoteStatus.READY, recipient, analysis,
                 folder_id=folder["id"])
            message = f"{thread_id}: carpeta creada/encontrada: {folder['id']}"
            print(message)
            return message
        else:
            raise SystemExit(
                f"El hilo {thread_id} está en estado '{status}', no pendiente de aprobación."
            )
    finally:
        db.close()


def _page(title: str, body: str, message: str = "") -> str:
    try:
        mode = load_config().get("active_mode")
    except (SystemExit, OSError, ValueError):
        mode = None
    return render_page(title, body, message, len(unread_notifications(100)), mode,
                       bool(os.getenv("OPENAI_API_KEY")))


def _review_summary(review: dict) -> str:
    analysis = review["analysis"]
    summary = analysis.get("summary") or "Sin resumen disponible"
    project = analysis.get("project_name") or "Sin nombre de proyecto"
    return f"""
<div class="case">
  <span class="badge">{html.escape(review['status_label'])}</span>
  <h2>{html.escape(project)}</h2>
  <div>{html.escape(summary)}</div>
  <div class="meta">Cliente: {html.escape(review['recipient'])}</div>
  <div class="meta">Hilo: {html.escape(review['thread_id'])}</div>
  <a class="button" href="/case?thread_id={urllib.parse.quote(review['thread_id'])}">
    Revisar caso
  </a>
</div>"""


def _dashboard_insights(quotes: list[dict], services: Counter, clients: Counter) -> str:
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
    if not items:
        return "<p class='meta'>Las conclusiones aparecerán cuando ingresen solicitudes.</p>"
    return "".join(
        f"<div class='insight'><strong>{html.escape(title)}</strong>"
        f"<span class='meta'>{html.escape(detail)}</span></div>"
        for title, detail in items
    )


def _notifications_panel(notifications: list[dict]) -> str:
    if not notifications:
        return ""
    rows = []
    for item in notifications:
        timestamp = item["created_at"][:16].replace("T", " ") if item["created_at"] else ""
        rows.append(f"""
<article class="notification">
  <span class="notification-dot"></span>
  <div><strong>{html.escape(item['title'])}</strong>
    <div>{html.escape(item['project_name'])}</div>
    <div class="meta">{html.escape(item['message'])} {html.escape(timestamp)}</div>
  </div>
  <a class="button secondary" href="/case?thread_id={urllib.parse.quote(item['thread_id'])}">Revisar</a>
</article>""")
    return f"""
<section class="notifications">
  <div class="section-head"><div><h2>Novedades</h2><div class="meta">Correos y respuestas que requieren tu atención.</div></div>
    <form method="post" action="/notifications/read"><button class="secondary" type="submit">Marcar como revisadas</button></form>
  </div>
  {''.join(rows)}
</section>"""


def _render_index(message: str = "") -> str:
    quotes = all_quotes()
    notifications = unread_notifications(8)
    unread_threads = {item["thread_id"] for item in notifications}
    sync_text = sync_status_text()
    total = len(quotes)
    unique_clients = len({q["client_email"].casefold() for q in quotes if q["client_email"]})
    action_count = sum(q["status"] in ACTION_STATUSES for q in quotes)
    completed = sum(q["status"] in {QuoteStatus.READY, QuoteStatus.READY_REVIEW} for q in quotes)
    services = Counter(service for quote in quotes for service in quote["services"])
    statuses = Counter(q["status_label"] for q in quotes)
    clients = Counter(q["client_name"] for q in quotes if q["client_name"])
    client_types = Counter(q["client_type"] for q in quotes)
    recent_action = [q for q in quotes if q["status"] in ACTION_STATUSES][:5]
    recent_html = "".join(
        _quote_row(q, q["thread_id"] in unread_threads) for q in recent_action
    )
    if not recent_html:
        recent_html = "<div class='empty'>No hay solicitudes que requieran atención.</div>"
    body = f"""
<div class="page-head">
  <div><h1>Resumen comercial</h1><p>Qué está llegando, quién lo solicita y dónde hace falta actuar. {html.escape(sync_text)}</p></div>
  <a class="button secondary" href="/requests">Ver todas las solicitudes</a>
</div>
{_notifications_panel(notifications)}
<div class="grid kpis">
  <div class="kpi"><div class="kpi-label">Solicitudes registradas</div><div class="kpi-value">{total}</div><div class="kpi-note">Excluye correos descartados</div></div>
  <div class="kpi"><div class="kpi-label">Clientes identificados</div><div class="kpi-value">{unique_clients}</div><div class="kpi-note">Por correo de cliente</div></div>
  <div class="kpi"><div class="kpi-label">Requieren atención</div><div class="kpi-value">{action_count}</div><div class="kpi-note">Faltantes o revisión técnica</div></div>
  <div class="kpi"><div class="kpi-label">Información suficiente</div><div class="kpi-value">{completed}</div><div class="kpi-note">Listas para revisión interna</div></div>
</div>
<div class="grid dashboard-grid">
  <section class="section"><div class="section-head"><h2>Servicios solicitados</h2><span class="meta">Top 6</span></div>{_metric_rows(services)}</section>
  <section class="section"><div class="section-head"><h2>Estado de solicitudes</h2><span class="meta">Actual</span></div>{_metric_rows(statuses, 'blue')}</section>
  <section class="section"><div class="section-head"><h2>Tipos de cliente</h2><span class="meta">Clasificación comercial</span></div>{_metric_rows(client_types)}</section>
  <section class="section"><div class="section-head"><h2>Lectura rápida</h2></div>{_dashboard_insights(quotes, services, clients)}</section>
</div>
<section style="margin-top:24px">
  <div class="section-head" style="margin-bottom:12px"><h2>Requieren atención</h2><a href="/requests" class="meta">Ver todo</a></div>
  <div class="grid">{recent_html}</div>
</section>"""
    return _page("Resumen comercial", body, message)


def _quote_row(quote: dict, is_new: bool = False) -> str:
    analysis = quote["analysis"]
    project = analysis.get("project_name") or "Solicitud sin nombre"
    services = ", ".join(quote["services"]) or "Servicio por clasificar"
    badge_class = "warn" if quote["status"] in ACTION_STATUSES else "info"
    new_badge = " <span class='badge'>Nuevo</span>" if is_new else ""
    return f"""
<article class="case case-row" data-search="{html.escape((project + ' ' + quote['client_name'] + ' ' + services).casefold())}"
         data-status="{html.escape(quote['status'])}" data-type="{html.escape(quote['client_type'])}">
  <div><h3>{html.escape(project)}{new_badge}</h3><div class="meta">{html.escape(quote['client_name'])}</div></div>
  <div><strong>{html.escape(services)}</strong><div class="meta">{html.escape(quote['sector'])}</div></div>
  <span class="badge {badge_class}">{html.escape(quote['status_label'])}</span>
  <div class="meta">{html.escape((quote['updated_at'] or quote['first_seen_at'] or 'Sin fecha')[:10])}</div>
  <a class="button secondary" href="/case?thread_id={urllib.parse.quote(quote['thread_id'])}">Abrir</a>
</article>"""


def _render_requests(message: str = "") -> str:
    quotes = all_quotes()
    unread_threads = {item["thread_id"] for item in unread_notifications(100)}
    rows = "".join(
        _quote_row(quote, quote["thread_id"] in unread_threads) for quote in quotes
    )
    if not rows:
        rows = "<div class='empty'>Todavía no hay solicitudes registradas.</div>"
    status_options = "".join(
        f"<option value='{html.escape(key)}'>{html.escape(label)}</option>"
        for key, label in STATUS_LABELS.items()
    )
    type_options = "".join(
        f"<option value='{html.escape(value)}'>{html.escape(value)}</option>"
        for value in CLIENT_TYPES
    )
    body = f"""
<div class="page-head"><div><h1>Solicitudes</h1><p>Consulta y completa la información comercial de cada oportunidad.</p></div><a class="button secondary" href="/export.xlsx">Exportar todo a Excel</a></div>
<div class="filters">
  <label>Buscar<input id="search" type="search" placeholder="Proyecto, cliente o servicio"></label>
  <label>Estado<select id="status"><option value="">Todos</option>{status_options}</select></label>
  <label>Tipo de cliente<select id="client-type"><option value="">Todos</option>{type_options}</select></label>
</div>
<div id="requests" class="grid">{rows}</div>
<div id="no-results" class="empty" hidden>No hay solicitudes con esos filtros.</div>
<script>
const search = document.querySelector('#search');
const statusFilter = document.querySelector('#status');
const typeFilter = document.querySelector('#client-type');
const rows = [...document.querySelectorAll('.case-row')];
function filterRows() {{
  const term = search.value.trim().toLocaleLowerCase('es');
  let visible = 0;
  rows.forEach(row => {{
    const show = (!term || row.dataset.search.includes(term)) &&
      (!statusFilter.value || row.dataset.status === statusFilter.value) &&
      (!typeFilter.value || row.dataset.type === typeFilter.value);
    row.hidden = !show;
    if (show) visible += 1;
  }});
  document.querySelector('#no-results').hidden = visible !== 0;
}}
[search, statusFilter, typeFilter].forEach(input => input.addEventListener('input', filterRows));
</script>"""
    return _page("Solicitudes", body, message)


def _internal_context_panel(client_name: str) -> str:
    context = internal_commercial_context(client_name)
    if not context:
        return "<p class='meta'>El índice comercial interno todavía no está disponible.</p>"

    def compact_list(items: list[tuple[str, int]], limit: int = 5) -> str:
        if not items:
            return "<p class='meta'>Sin patrones suficientes.</p>"
        return "<ul class='context-list'>" + "".join(
            f"<li><span>{html.escape(str(label))}</span><strong>{int(count)}</strong></li>"
            for label, count in items[:limit]
        ) + "</ul>"

    profile = context.get("profile")
    if profile:
        project_types = list(profile.get("project_types", {}).items())[:5]
        sectors = list(profile.get("sectors", {}).items())[:4]
        requests = list(profile.get("usual_requests", {}).items())[:6]
        profile_html = f"""
<div class="context-profile">
  <div><span class="meta">Coincidencia histórica</span><strong>{html.escape(profile.get('client_name', 'Cliente'))}</strong></div>
  <div><span class="meta">Tipo de empresa inferido</span><strong>{html.escape(profile.get('company_type', 'No clasificado'))}</strong><small>Confianza {html.escape(profile.get('company_type_confidence', 'baja'))}</small></div>
  <div><span class="meta">Documentos comerciales</span><strong>{int(profile.get('commercial_document_count', 0))}</strong></div>
</div>
<div class="context-grid">
  <div><h3>Proyectos asociados</h3>{compact_list(project_types)}</div>
  <div><h3>Sectores asociados</h3>{compact_list(sectors)}</div>
  <div><h3>Datos solicitados habitualmente</h3>{compact_list(requests)}</div>
</div>"""
    else:
        profile_html = f"""
<div class="notice internal-note"><strong>Sin coincidencia de cliente.</strong> Escribe el nombre de la empresa en Información comercial y guarda para consultar su historial. Mientras tanto se muestran patrones generales de {int(context.get('client_profiles', 0))} perfiles.</div>
<div class="context-grid">
  <div><h3>Proyectos más frecuentes</h3>{compact_list(context.get('project_types', []))}</div>
  <div><h3>Sectores más frecuentes</h3>{compact_list(context.get('sectors', []))}</div>
  <div><h3>Datos solicitados habitualmente</h3>{compact_list(context.get('usual_requests', []))}</div>
</div>"""

    return f"""
<div class="notice internal-note"><strong>Uso interno confidencial.</strong> Este historial sirve para orientar la revisión. No se copia al borrador, no contiene precios y no debe compartirse con el cliente.</div>
{profile_html}
<p class="meta" style="margin-bottom:0">Fuente: archivo histórico T&amp;E, lectura agregada del {html.escape(context.get('generated_at', ''))}.</p>"""


def _render_case(thread_id: str, message: str = "") -> str:
    review = get_quote(thread_id)
    if not review:
        return _page("Caso no encontrado", "<div class='empty'>La solicitud no existe.</div>", message)
    recipient_error = ""
    if not valid_email_address(review["recipient"]):
        try:
            review = resolve_case_recipient(thread_id)["quote"]
        except Exception:
            recipient_error = (
                "No se pudo confirmar el destinatario en Gmail. "
                "El guardado y el envío están bloqueados para evitar un correo incorrecto."
            )
    analysis = review["analysis"]
    assessment = review.get("reception_assessment") or reception_assessment(analysis)
    config = load_config()
    routing = analysis.get("routing", {})
    routing_summary = "Correo directo"
    if routing.get("is_forwarded"):
        routing_summary = (
            f"Reenvío interno: {routing.get('internal_forwarder') or 'no indicado'}\n"
            f"Cliente original: {routing.get('original_sender') or 'no identificado'}\n"
            f"Destinatario seleccionado: {routing.get('selected_recipient') or 'no indicado'}"
        )
    scope_issues = unresolved_scope_issues(analysis)
    issues_summary = "\n".join(f"- {issue}" for issue in scope_issues) or "Sin asuntos pendientes."
    email_allowed = (
        valid_email_address(review["recipient"])
        and review["status"] not in CLOSED_STATUSES
        and assessment.get("status") != "technical_review"
    )
    if email_allowed:
        email_body = review["draft_body"] or suggested_reply(
            analysis, config["required_fields"]
        )
        draft_note = (
            "<div class='notice'>Ya existe un borrador en Gmail. Al guardarlo de nuevo se actualizará.</div>"
            if review["draft_id"] else
            "<div class='notice'><strong>Respuesta sugerida lista.</strong> Todavía no está guardada ni enviada en Gmail.</div>"
        )
        email_form = f"""
{draft_note}
<form method="post">
  <input type="hidden" name="thread_id" value="{html.escape(thread_id)}">
  <label>Respuesta para {html.escape(review['recipient'])}
    <textarea name="body" style="min-height:260px">{html.escape(email_body)}</textarea>
  </label>
  <div class="toolbar" style="margin-top:14px">
    <button type="submit" formaction="/draft">Guardar borrador en Gmail</button>
    <button type="submit" formaction="/send" class="danger"
      onclick="return confirm('Se enviará este correo a {html.escape(review['recipient'])} en el mismo hilo. ¿Confirmas el envío?');">Enviar ahora</button>
  </div>
</form>"""
    elif review["status"] == QuoteStatus.RESPONSE_SENT:
        email_form = (
            f"<div class='notice'>Respuesta enviada el {html.escape(review['sent_at'] or 'sin fecha registrada')}.</div>"
            f"<pre>{html.escape(review['draft_body'])}</pre>"
        )
    else:
        if recipient_error:
            email_form = f"<div class='notice' style='border-color:#e2b8b3;background:#faecea'>{html.escape(recipient_error)}</div>"
        elif assessment.get("status") == "technical_review":
            email_form = "<div class='notice'><strong>Respuesta en pausa.</strong> El equipo de Anka debe revisar el caso antes de solicitar información al cliente.</div>"
        else:
            email_form = "<p class='meta'>Esta solicitud no admite respuesta desde el dashboard en su estado actual.</p>"
    suggestion_complete = review["status"] not in {QuoteStatus.IGNORED, QuoteStatus.MISSING}
    draft_complete = bool(review["draft_id"] or review["status"] == QuoteStatus.RESPONSE_SENT)
    sent_complete = review["status"] == QuoteStatus.RESPONSE_SENT
    process_html = f"""
<div class="process">
  <div class="process-step done"><strong>1. Correo recibido</strong><span>Detectado y registrado</span></div>
  <div class="process-step done"><strong>2. Lectura inicial</strong><span>{html.escape(assessment.get('service_family', 'Por clasificar'))}</span></div>
  <div class="process-step {'done' if suggestion_complete else ''}"><strong>3. Aclaración sugerida</strong><span>{'Lista para revisión' if suggestion_complete else 'No disponible'}</span></div>
  <div class="process-step {'done' if draft_complete or sent_complete else 'current'}"><strong>4. Gestión de respuesta</strong><span>{'Enviada' if sent_complete else ('Borrador guardado' if draft_complete else 'Pendiente de aprobación')}</span></div>
</div>"""
    project_form = ""
    if assessment.get("status") == "sufficient_for_internal_review":
        project_form = (
            "<div class='notice'><strong>Recepción completa.</strong> La información puede "
            "pasar a revisión interna. Este dashboard todavía no prepara precios ni cotizaciones.</div>"
        )
    client_type_options = "".join(
        f"<option value='{html.escape(value)}'"
        f"{' selected' if value == review['client_type'] else ''}>{html.escape(value)}</option>"
        for value in CLIENT_TYPES
    )
    sector_options = "".join(
        f"<option value='{html.escape(value)}'"
        f"{' selected' if value == review['sector'] else ''}>{html.escape(value)}</option>"
        for value in SECTORS
    )
    metadata_form = f"""
<form method="post" action="/metadata">
  <input type="hidden" name="thread_id" value="{html.escape(thread_id)}">
  <div class="filters">
    <label>Cliente o empresa<input name="client_name" value="{html.escape(review['client_name'])}"></label>
    <label>Tipo de cliente<select name="client_type">{client_type_options}</select></label>
    <label>Sector<select name="sector">{sector_options}</select></label>
  </div>
  <label>Notas comerciales<textarea name="notes" style="min-height:90px">{html.escape(review['notes'])}</textarea></label>
  <div class="toolbar" style="margin-top:12px"><button type="submit">Guardar datos comerciales</button></div>
</form>"""
    body = f"""
<div class="page-head"><div><h1>{html.escape(analysis.get('project_name') or 'Solicitud sin nombre')}</h1><p>{html.escape(analysis.get('summary') or 'Sin resumen disponible')}</p></div><a class="button secondary" href="/requests">Volver</a></div>
<div class="layout">
  <section class="section">
    <span class="badge">{html.escape(review['status_label'])}</span>
    <div class="meta">Destinatario: {html.escape(review['recipient'])}</div>
    <div class="meta">Hilo: {html.escape(thread_id)}</div>
    {process_html}
  </section>
  <section class="section">
    <div class="section-head"><h2>Evaluación de recepción</h2><span class="badge info">Sin cotizar</span></div>
    {_reception_assessment_panel(assessment)}
  </section>
  <section class="section"><h2>Información comercial</h2>{metadata_form}</section>
  <section class="section">
    <div class="section-head"><h2>Contexto comercial interno</h2><span class="badge info">Solo Anka</span></div>
    {_internal_context_panel(review['client_name'])}
  </section>
  <section class="section">
    <h2>Actividades detectadas</h2>
    {activities_editor(thread_id, analysis.get('activities', []))}
  </section>
  <section class="section">
    <h2>Adjuntos</h2>
    {_attachments_table(analysis.get('attachments', []))}
  </section>
  <section class="section">
    <h2>Enrutamiento y validaciones</h2>
    <pre>{html.escape(routing_summary)}</pre>
    <h2 style="margin-top:16px;">Asuntos pendientes</h2>
    <pre>{html.escape(issues_summary)}</pre>
  </section>
  <section class="section">
    <h2>Respuesta al cliente</h2>
    <p class="meta">Revisa el destinatario y el contenido. Ningún correo se envía sin pulsar “Enviar ahora”.</p>
    {email_form}
    <div style="margin-top:14px">{project_form}</div>
  </section>
</div>"""
    return _page("Revisar caso", body, message)


CSRF_TOKEN = secrets.token_urlsafe(32)
POST_FORM_PATTERN = re.compile(r'(<form\b[^>]*\bmethod="post"[^>]*>)', re.IGNORECASE)
LOCAL_HOSTS = {"127.0.0.1", "localhost", "[::1]"}


def add_csrf_tokens(content: str) -> str:
    field = f'<input type="hidden" name="csrf_token" value="{CSRF_TOKEN}">'
    return POST_FORM_PATTERN.sub(lambda m: m.group(1) + field, content)


def is_local_host_header(value: str) -> bool:
    """Acepta solo Host locales; bloquea DNS rebinding."""
    value = (value or "").lower()
    hostname = value.rsplit(":", 1)[0] if value.count(":") == 1 or value.startswith("[") and "]:" in value else value
    return hostname in LOCAL_HOSTS


def is_same_origin(origin: str, host: str) -> bool:
    if not origin:
        return True  # navegadores omiten Origin en algunas navegaciones; el token cubre ese caso
    return urllib.parse.urlparse(origin).netloc.lower() == (host or "").lower()


class ReviewHandler(BaseHTTPRequestHandler):
    def _send_html(self, content: str, status: int = 200) -> None:
        data = add_csrf_tokens(content).encode("utf-8")
        self.send_response(status)
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Content-Security-Policy", "frame-ancestors 'none'")
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _read_form(self) -> dict[str, list[str]]:
        length = int(self.headers.get("Content-Length", "0"))
        data = self.rfile.read(length).decode("utf-8")
        return urllib.parse.parse_qs(data)

    def _reject(self, reason: str) -> None:
        self._send_html(_page("Solicitud rechazada", f"<div class='empty'>{html.escape(reason)}</div>"), 403)

    def do_GET(self) -> None:
        if not is_local_host_header(self.headers.get("Host", "")):
            return self._reject("Host no permitido.")
        parsed = urllib.parse.urlparse(self.path)
        query = urllib.parse.parse_qs(parsed.query)
        if parsed.path == "/":
            self._send_html(_render_index(query.get("message", [""])[0]))
        elif parsed.path == "/export.xlsx":
            thread_id = query.get("thread_id", [""])[0]
            quotes = [get_quote(thread_id)] if thread_id else all_quotes()
            quotes = [item for item in quotes if item]
            if thread_id and not quotes:
                return self._send_html(_page("No encontrado", "<div class='empty'>La solicitud no existe.</div>"), 404)
            data = build_workbook(quotes)
            stamp = datetime.now().strftime("%Y%m%d-%H%M")
            name = f"actividades-{thread_id[:8] or 'todas'}-{stamp}.xlsx"
            self.send_response(200)
            self.send_header("Content-Type", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
            self.send_header("Content-Disposition", f'attachment; filename="{name}"')
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
        elif parsed.path == "/requests":
            self._send_html(_render_requests(query.get("message", [""])[0]))
        elif parsed.path == "/case":
            self._send_html(_render_case(
                query.get("thread_id", [""])[0], query.get("message", [""])[0]
            ))
        else:
            self._send_html(_page("No encontrado", "<div class='empty'>Página no encontrada.</div>"), 404)

    def do_POST(self) -> None:
        host = self.headers.get("Host", "")
        if not is_local_host_header(host):
            return self._reject("Host no permitido.")
        if not is_same_origin(self.headers.get("Origin", ""), host):
            return self._reject("Origen no permitido.")
        parsed = urllib.parse.urlparse(self.path)
        form = self._read_form()
        token = form.get("csrf_token", [""])[0]
        if not secrets.compare_digest(token, CSRF_TOKEN):
            return self._reject("Token de seguridad inválido. Recarga la página e inténtalo de nuevo.")
        try:
            if parsed.path == "/refresh":
                if load_config().get("active_mode") == "chatgpt_work":
                    message = (
                        "Este botón no lee Gmail en modo ChatGPT Work. " + sync_status_text()
                    )
                else:
                    message = describe_check(checked_run(True, True, False, "dashboard"))
                self._redirect("/?message=" + urllib.parse.quote(message))
            elif parsed.path == "/approve":
                thread_id = form.get("thread_id", [""])[0]
                body = form.get("body", [None])[0]
                message = approve_review(thread_id, body_text=body)
                self._redirect("/?message=" + urllib.parse.quote(message))
            elif parsed.path == "/metadata":
                thread_id = form.get("thread_id", [""])[0]
                save_quote_metadata(
                    thread_id,
                    form.get("client_name", [""])[0],
                    form.get("client_type", ["No clasificado"])[0],
                    form.get("sector", ["No clasificado"])[0],
                    form.get("notes", [""])[0],
                )
                message = "Información comercial guardada."
                self._redirect(
                    "/case?thread_id=" + urllib.parse.quote(thread_id)
                    + "&message=" + urllib.parse.quote(message)
                )
            elif parsed.path == "/draft":
                thread_id = form.get("thread_id", [""])[0]
                body = form.get("body", [""])[0]
                message = save_dashboard_draft(thread_id, body)
                self._redirect(
                    "/case?thread_id=" + urllib.parse.quote(thread_id)
                    + "&message=" + urllib.parse.quote(message)
                )
            elif parsed.path == "/send":
                thread_id = form.get("thread_id", [""])[0]
                body = form.get("body", [""])[0]
                message = send_dashboard_reply(thread_id, body)
                self._redirect(
                    "/case?thread_id=" + urllib.parse.quote(thread_id)
                    + "&message=" + urllib.parse.quote(message)
                )
            elif parsed.path == "/activities":
                thread_id = form.get("thread_id", [""])[0]
                message = save_activities(thread_id, parse_activities_form(form))
                self._redirect(
                    "/case?thread_id=" + urllib.parse.quote(thread_id)
                    + "&message=" + urllib.parse.quote(message)
                )
            elif parsed.path == "/mode":
                message = set_mode(form.get("mode", [""])[0])
                self._redirect("/?message=" + urllib.parse.quote(message))
            elif parsed.path == "/notifications/read":
                mark_notifications_read()
                self._redirect("/?message=" + urllib.parse.quote("Novedades marcadas como revisadas."))
            else:
                self._send_html(_page("No encontrado", "<div class='empty'>Página no encontrada.</div>"), 404)
        except (Exception, SystemExit) as exc:
            self._send_html(_page("Error", f"<div class='empty'>{html.escape(str(exc))}</div>"), 500)

    def _redirect(self, location: str) -> None:
        self.send_response(303)
        self.send_header("Location", location)
        self.end_headers()

    def log_message(self, format: str, *args) -> None:
        return


def review_ui(host: str, port: int, open_browser: bool = True) -> None:
    url = f"http://{host}:{port}"
    print(f"Bandeja de revisión comercial: {url}")
    print("Deja esta ventana abierta. Presiona Ctrl+C para cerrar.")
    if open_browser:
        webbrowser.open(url)
    server = ThreadingHTTPServer((host, port), ReviewHandler)
    server.daemon_threads = True
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nBandeja cerrada.")
    finally:
        server.server_close()




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


def _skip_reason(row, last_id: str, gmail_drafts: bool) -> str | None:
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


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "command",
        choices=[
            "auth", "drive-tree", "mode", "run", "reviews", "approve", "review-ui",
            "set-mode", "analyze-text", "dashboard-upsert",
        ],
    )
    parser.add_argument("--apply", action="store_true")
    parser.add_argument(
        "--review-only", action="store_true",
        help="Procesa y guarda borradores para revisión humana, sin enviar ni crear carpetas.",
    )
    parser.add_argument(
        "--gmail-drafts", action="store_true",
        help="Crea borradores en Gmail con nota de revision; no envia correos.",
    )
    parser.add_argument(
        "--watch-seconds", type=int, default=0,
        help="Revisa Gmail en bucle cada N segundos. Usa 0 para una sola ejecución.",
    )
    text_source = parser.add_mutually_exclusive_group()
    text_source.add_argument("--text", help="Texto de correo para probar el analizador local/API.")
    text_source.add_argument("--file", help="Archivo UTF-8 con el texto del correo de prueba.")
    parser.add_argument(
        "--required-field", action="append", default=[],
        help="Dato que se desea verificar; puede repetirse.",
    )
    parser.add_argument("--thread-id", help="ID del hilo a aprobar.")
    parser.add_argument(
        "--payload-base64", help="Registro JSON codificado para actualizar el dashboard."
    )
    parser.add_argument(
        "--body-file",
        help="Archivo UTF-8 con el correo revisado que se enviará al aprobar una pregunta.",
    )
    parser.add_argument("--host", default="127.0.0.1", help=argparse.SUPPRESS)
    parser.add_argument("--port", type=int, default=8765, help=argparse.SUPPRESS)
    parser.add_argument("--no-browser", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument(
        "--force-auth", action="store_true",
        help="Fuerza una nueva autorizacion de Google para actualizar permisos.",
    )
    parser.add_argument(
        "--active-mode",
        choices=sorted(ACTIVE_MODES),
        help="Modo a activar con set-mode.",
    )
    args = parser.parse_args()
    if args.command == "dashboard-upsert":
        if not args.payload_base64:
            parser.error("dashboard-upsert requiere --payload-base64.")
        dashboard_upsert(args.payload_base64)
        print("Registro del dashboard actualizado.")
    elif args.command == "analyze-text":
        if not args.text and not args.file:
            parser.error("analyze-text requiere --text o --file.")
        config = load_config()
        text = args.text if args.text is not None else Path(args.file).read_text(encoding="utf-8")
        result = analyze_text(
            text, args.required_field or config["required_fields"], config["llm"]
        )
        print(json.dumps({
            "provider": config["llm"]["provider"],
            "model": config["llm"].get("model"),
            "analysis": result,
            "questions": reception_questions(
                result, args.required_field or config["required_fields"]
            ),
        }, ensure_ascii=False, indent=2))
    elif args.command == "auth":
        credentials(args.force_auth)
        print("Autorización lista.")
    elif args.command == "drive-tree":
        _, drive = services()
        for folder in list_folders(drive):
            print(f"{folder['name']} [{folder['id']}]")
            for child in list_folders(drive, folder["id"]):
                print(f"  {child['name']} [{child['id']}]")
    elif args.command == "mode":
        print_mode()
    elif args.command == "set-mode":
        if not args.active_mode:
            parser.error("set-mode requiere --active-mode.")
        print(set_mode(args.active_mode))
    elif args.command == "reviews":
        list_reviews()
    elif args.command == "approve":
        if not args.thread_id:
            parser.error("approve requiere --thread-id.")
        approve_review(args.thread_id, args.body_file)
    elif args.command == "review-ui":
        review_ui(args.host, args.port, not args.no_browser)
    else:
        if args.watch_seconds:
            watch(args.apply, args.review_only, args.watch_seconds, args.gmail_drafts)
        else:
            run(args.apply, args.review_only, args.gmail_drafts)


if __name__ == "__main__":
    main()
