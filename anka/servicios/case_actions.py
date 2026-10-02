"""Acciones sobre un caso desde el dashboard: destinatario, borrador, envío, aprobación."""

from __future__ import annotations

import base64
import json
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any
from anka.dominio.errores import ErrorDeAnka
from anka.dominio.status import (
    CLOSED_STATUSES, QuoteStatus,
)
from anka.servicios.drive_layout import DriveLayoutError, create_project_folder, trash_project_folder, year_mismatch
from anka.infra.google_services import services
from anka.dominio.correo import (
    forwarded_routing,
    inbound_messages,
    reply_email,
    valid_email_address,
)
from anka.infra.mail import own_addresses, send_reply
from anka.dominio.reception import (
    question_email, reception_questions, safe_folder_name,
)
from anka.infra.settings import load_config
from anka.infra.logging_setup import SENSITIVE, get_logger
from anka.infra.store import add_dashboard_notification, connect, record_event, save

from anka.servicios.drive_contents import populate_project_folder
from anka.servicios.pipeline import confirmed_client
from anka.servicios.change_control import known_addresses, mark_change_sent
from anka.servicios.quotes import case_has_links, delete_quotes, get_quote, set_client_name

log = get_logger(__name__)


def resolve_case_recipient(thread_id: str) -> dict:
    """Confirma en Gmail el destinatario real de un caso cuyo correo no se pudo leer y lo guarda."""
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
    db = connect()
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


def case_email_context(thread_id: str) -> tuple[dict, Any, dict, set[str]]:
    """Reúne lo necesario para escribir al cliente (caso, Gmail, hilo, direcciones); falla si ya no admite correo."""
    quote = get_quote(thread_id)
    if not quote:
        raise ValueError("La solicitud ya no existe.")
    reopened = quote["status"] in (QuoteStatus.READY, QuoteStatus.RESPONSE_SENT) and case_has_links(thread_id)
    if quote["status"] in CLOSED_STATUSES and not reopened:
        raise ValueError("Esta solicitud no admite una respuesta desde el dashboard.")
    context = resolve_case_recipient(thread_id)
    quote = context["quote"]
    gmail = context["gmail"]
    thread = context["thread"]
    account_addresses = context["account_addresses"]
    return quote, gmail, thread, account_addresses


def _recipient(thread_id: str, quote: dict, to: str) -> str:
    """Destinatario: el del caso, o una persona que ya participa en él (nunca una dirección cualquiera)."""
    to = to.strip()
    if not to or to.casefold() == quote["recipient"].casefold():
        return str(quote["recipient"])
    if to.casefold() not in known_addresses(thread_id):
        raise ValueError("Solo puedes escribir a personas que ya participan en este caso.")
    return to


def save_dashboard_draft(thread_id: str, body: str, to: str = "") -> str:
    """Guarda como borrador de Gmail el texto escrito en el dashboard, sin enviarlo."""
    body = body.strip()
    if not body:
        raise ValueError("El borrador no puede estar vacío.")
    quote, gmail, thread, account_addresses = case_email_context(thread_id)
    recipient = _recipient(thread_id, quote, to)
    email = reply_email(thread, recipient, body, account_addresses)
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
    db = connect()
    try:
        db.execute(
            f"""UPDATE quotes SET status='{QuoteStatus.DRAFT_CREATED}', draft_id=?, draft_body=?,
                                 updated_at=? WHERE thread_id=?""",
            (draft_id, body, now, thread_id),
        )
        add_dashboard_notification(
            db, thread_id, "draft_saved", draft_id,
            "Borrador guardado en Gmail",
            f"El borrador quedó guardado para {recipient} y todavía no ha sido enviado.",
        )
        db.commit()
    finally:
        db.close()
    log.info("%s: borrador guardado en Gmail desde el dashboard", thread_id)
    return f"Borrador guardado en Gmail para {recipient}."


DUPLICATE_WINDOW = timedelta(minutes=15)


def _already_sent(quote: dict, body: str, recipient: str) -> str:
    """Aviso si este mismo texto ya salió hace poco a la misma persona (doble clic, página que no cambió)."""
    sent_at = quote.get("sent_at") or ""
    if not sent_at or (quote.get("draft_body") or "").strip() != body:
        return ""
    try:
        age = datetime.now().astimezone() - datetime.fromisoformat(sent_at)
    except ValueError:
        return ""
    if age <= DUPLICATE_WINDOW and recipient.casefold() == str(quote["recipient"]).casefold():
        return (f"Este mismo correo ya se envió a {recipient} hace {max(1, int(age.total_seconds() // 60))} min. "
                "No se envió de nuevo; si quieres mandar otro, cambia el texto.")
    return ""


def send_dashboard_reply(thread_id: str, body: str, to: str = "", change_id: int = 0,
                         purpose: str = "") -> str:
    """Envía al cliente el correo escrito en el dashboard (ya revisado por una persona) y deja constancia.

    Rechaza un reenvío idéntico reciente.
    """
    body = body.strip()
    if not body:
        raise ValueError("El correo no puede estar vacío.")
    quote, gmail, thread, account_addresses = case_email_context(thread_id)
    recipient = _recipient(thread_id, quote, to)
    duplicate = _already_sent(quote, body, recipient)
    if duplicate:
        raise ValueError(duplicate)
    email = reply_email(thread, recipient, body, account_addresses)
    raw = base64.urlsafe_b64encode(email.as_bytes()).decode("ascii")
    # Se envía el borrador existente (el del dashboard o el que dejó Work) para no dejar uno huérfano
    # con la sección interna; si ya no existe en Gmail, se envía como mensaje nuevo en el hilo.
    draft_id = quote["draft_id"] or (quote["analysis"].get("work_reply") or {}).get("draft_id") or ""
    result = None
    if draft_id:
        try:
            gmail.users().drafts().update(
                userId="me", id=draft_id, body={"message": {"raw": raw, "threadId": thread_id}},
            ).execute()
            result = gmail.users().drafts().send(userId="me", body={"id": draft_id}).execute()
        except Exception as exc:
            if getattr(getattr(exc, "resp", None), "status", None) != 404:
                raise
            log.warning("%s: el borrador ya no existe en Gmail; se envía como mensaje nuevo", thread_id)
    if result is None:
        result = gmail.users().messages().send(
            userId="me", body={"raw": raw, "threadId": thread_id}
        ).execute()
    now = datetime.now().astimezone().isoformat(timespec="seconds")
    # Un caso con información suficiente sigue abierto para crear su carpeta en Drive:
    # enviar un acuse o una aclaración no debe cerrarlo.
    if quote["status"] in (QuoteStatus.READY_REVIEW, QuoteStatus.READY):
        new_status = quote["status"]
    elif quote["status"] == QuoteStatus.REVIEW_PENDING:
        new_status = QuoteStatus.WAITING  # se preguntó al cliente: espera su respuesta (igual que approve_review)
    else:
        new_status = QuoteStatus.RESPONSE_SENT
    db = connect()
    try:
        analysis = dict(quote["analysis"])
        analysis.pop("work_reply", None)  # el borrador de Work ya se envió: no debe volver a proponerse
        db.execute(
            """UPDATE quotes SET status=?, sent_message_id=?, analysis_json=?,
                                 draft_id=NULL, draft_body=?, sent_at=?, updated_at=?
               WHERE thread_id=?""",
            (new_status, result["id"], json.dumps(analysis, ensure_ascii=False), body, now, now, thread_id),
        )
        record_event(db, thread_id, "sent", "dashboard", result["id"],
                     f"Correo enviado desde el dashboard a {recipient}.", now)
        add_dashboard_notification(
            db, thread_id, QuoteStatus.RESPONSE_SENT, result["id"],
            "Respuesta enviada",
            f"El correo fue enviado a {recipient} en el mismo hilo.",
        )
        db.commit()
    finally:
        db.close()
    log.info("%s: correo enviado desde el dashboard", thread_id)
    if change_id and purpose:
        mark_change_sent(thread_id, change_id, purpose, recipient)
    if purpose == "scope_update":
        db = connect()
        try:
            record_event(db, thread_id, "scope_update_sent", "dashboard", now,
                         f"Avisaste al cliente ({recipient}) de la actualización de la tabla.", now)
            db.commit()
        finally:
            db.close()
    message = f"Correo enviado a {recipient} en el mismo hilo."
    if new_status == QuoteStatus.READY_REVIEW:
        message += " " + auto_create_project_folder(thread_id)
    return message


def _fill_folder(gmail: Any, drive: Any, thread_id: str, folder_id: str,
                 account_addresses: set[str]) -> str:
    """Copia los adjuntos del cliente y la tabla a la carpeta; un fallo solo se informa."""
    try:
        thread = gmail.users().threads().get(userId="me", id=thread_id, format="full").execute()
        report = populate_project_folder(
            gmail, drive, thread, folder_id, get_quote(thread_id), account_addresses)
        return report.summary()
    except Exception as exc:
        log.exception("%s: no se pudo llenar la carpeta de Drive", thread_id)
        return (f"La carpeta quedó creada pero no pude copiar su contenido ({type(exc).__name__}). "
                "Súbelo a mano.")


def delete_cases(thread_ids: list[str]) -> str:
    """Elimina hilos del dashboard y mueve a la papelera de Drive la carpeta que se les creó.

    Si una carpeta no se puede mover a la papelera, ese hilo NO se borra (así no se pierde el
    vínculo con su carpeta) y se informa. Las carpetas de cliente nunca se tocan.
    """
    ids = list(dict.fromkeys(thread_ids))
    db = connect()
    try:
        marks = ",".join("?" * len(ids))
        folders = {
            row["thread_id"]: row["folder_id"] for row in db.execute(
                f"SELECT thread_id, folder_id FROM quotes WHERE thread_id IN ({marks}) "
                "AND folder_id IS NOT NULL AND folder_id <> ''", ids)
        } if ids else {}
    finally:
        db.close()
    trashed, failed = 0, []
    if folders:
        try:
            _, drive = services()
            parent = load_config().get("drive_parent_id", "")
        except Exception as exc:
            log.exception("No se pudo conectar con Drive para borrar carpetas")
            return f"No borré nada: no pude conectar con Drive ({type(exc).__name__}). Inténtalo de nuevo."
        for thread_id, folder_id in folders.items():
            try:
                trashed += trash_project_folder(drive, parent, folder_id)
            except Exception as exc:
                log.exception("%s: no se pudo mover la carpeta a la papelera", thread_id)
                failed.append((thread_id, str(exc) if isinstance(exc, DriveLayoutError) else type(exc).__name__))
    kept = {thread_id for thread_id, _ in failed}
    message = delete_quotes([thread_id for thread_id in ids if thread_id not in kept])
    if trashed:
        message += f" {trashed} carpeta(s) de proyecto movida(s) a la papelera de Drive."
    if failed:
        message += (f" No borré {len(failed)} solicitud(es) porque su carpeta no se pudo mover a la "
                    f"papelera: {failed[0][1]}")
    return message


def _note_drive_pending(thread_id: str, message: str) -> None:
    """Deja en el caso el motivo por el que la carpeta no se creó, para mostrarlo en pantalla."""
    db = connect()
    try:
        row = db.execute("SELECT analysis_json FROM quotes WHERE thread_id=?", (thread_id,)).fetchone()
        if row:
            analysis = json.loads(row["analysis_json"])
            analysis["drive"] = {"pending": message}
            db.execute("UPDATE quotes SET analysis_json=? WHERE thread_id=?",
                       (json.dumps(analysis, ensure_ascii=False), thread_id))
            db.commit()
    finally:
        db.close()


def auto_create_project_folder(thread_id: str) -> str:
    """Crea la carpeta del proyecto tras enviar la respuesta; nunca hace fallar el envío.

    Si falta el cliente, hay una carpeta parecida o Drive falla, no crea nada y deja el
    motivo en el caso para que una persona decida desde la sección de Drive.
    """
    try:
        return "Carpeta en Drive: " + approve_review(thread_id, auto=True)
    except ErrorDeAnka as exc:
        message = str(exc)
    except Exception as exc:  # Drive o red: el correo ya salió y no se debe perder
        log.exception("%s: no se pudo crear la carpeta automáticamente", thread_id)
        message = (f"No se pudo crear la carpeta automáticamente ({type(exc).__name__}). "
                   "Inténtalo desde la sección de Drive.")
    _note_drive_pending(thread_id, message)
    log.warning("%s: carpeta del proyecto pendiente tras el envío", thread_id)
    return "No creé la carpeta todavía: " + message


def _saved_client_name(db: Any, thread_id: str) -> str:
    row = db.execute(
        "SELECT client_name FROM quote_metadata WHERE thread_id=? AND client_name <> ''", (thread_id,),
    ).fetchone()
    return str(row[0]) if row else ""


def approve_review(
    thread_id: str, body_file: str | None = None, body_text: str | None = None,
    client_name: str | None = None, new_client: bool = False,
    auto: bool = False, allow_similar_project: bool = False,
) -> str:
    """Aprueba un caso: envía las preguntas (review_pending) o crea la carpeta (ready_review).

    Para la carpeta, `client_name` es la carpeta del cliente; `new_client=True` es la
    confirmación explícita de que puede crearse si no existe. Con `auto=True` (al enviar la
    respuesta de un caso con información suficiente) el cliente sale del que ya conoce el
    caso o su remitente, y una carpeta de cliente nueva se crea sola solo si no hay ninguna
    igual ni parecida. `allow_similar_project=True` crea la solicitud aunque haya una parecida.
    """
    config = load_config()
    gmail, drive = services()
    account_addresses = own_addresses(config, gmail)
    db = connect()
    try:
        row = db.execute(
            """SELECT last_inbound_id, status, recipient, analysis_json
               FROM quotes WHERE thread_id=?""",
            (thread_id,),
        ).fetchone()
        if not row:
            raise ErrorDeAnka(f"No existe revisión registrada para el hilo {thread_id}.")
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
            record_event(db, thread_id, "sent", "dashboard", sent_id,
                         f"Preguntas enviadas desde el dashboard a {recipient}.")
            db.commit()
            message = f"{thread_id}: correo enviado a {recipient}."
            log.info("%s: correo enviado tras aprobación", thread_id)
            log.info("%s", message, extra=SENSITIVE)
            return message
        elif status == QuoteStatus.READY_REVIEW:
            parent = config.get("drive_parent_id")
            if not parent:
                raise ErrorDeAnka("Para aprobar alcance completo define drive_parent_id en config.json.")
            client = (client_name or "").strip() or _saved_client_name(db, thread_id)
            if not client and auto:
                client = confirmed_client(db, thread_id, analysis, recipient)
            if auto and "@" in client:
                client = ""  # un correo no es el nombre de una empresa: no crear una carpeta con él
            if not client:
                raise ErrorDeAnka("Indica la carpeta del cliente.")
            mismatch = year_mismatch(drive, parent, analysis.get("received_at", ""))
            if mismatch:
                raise ErrorDeAnka(mismatch)
            request_name = safe_folder_name(
                analysis["project_name"], thread_id, config.get("folder_prefix", "Cotizacion"),
            )
            try:
                result = create_project_folder(
                    drive, parent, client, request_name, create_client=new_client,
                    auto_create_client=auto,
                    attachment_names=[
                        str(item.get("filename") or "") for item in analysis.get("attachments", [])
                        if isinstance(item, dict)
                    ],
                    allow_similar_project=allow_similar_project,
                )
            except DriveLayoutError as exc:
                raise ErrorDeAnka(str(exc)) from exc
            analysis.pop("drive", None)
            save(db, thread_id, last_id, QuoteStatus.READY, recipient, analysis,
                 folder_id=result.folder["id"])
            set_client_name(thread_id, client)  # los próximos correos del remitente se resuelven solos
            contents = _fill_folder(gmail, drive, thread_id, result.folder["id"], account_addresses)
            log.info("%s: carpeta del proyecto creada tras aprobación: %s", thread_id, result.folder["id"])
            message = f"{thread_id}: carpeta creada/encontrada: {result.folder['id']}"
            if result.client_created:
                log.info("%s: carpeta de cliente nuevo creada tras confirmación", thread_id)
                message += " Se creó también la carpeta del cliente nuevo."
            return message + " " + contents
        else:
            raise ErrorDeAnka(
                f"El hilo {thread_id} está en estado '{status}', no pendiente de aprobación."
            )
    finally:
        db.close()
