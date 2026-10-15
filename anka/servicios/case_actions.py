"""Acciones sobre un caso desde el dashboard: destinatario, borrador, envío, aprobación."""

from __future__ import annotations
from anka.servicios.repositorio import actualizar_caso

import base64
import json
from datetime import datetime, timedelta
from pathlib import Path
import sqlite3
from collections.abc import Sequence
from typing import Any
from anka.dominio.configuracion import Configuracion
from anka.dominio.analisis import Analisis
from anka.dominio.errores import ErrorDeAnka
from anka.dominio.status import (
    QuoteStatus,
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
from anka.dominio.preguntas import en_el_correo, hay_pendientes_internos
from anka.dominio.reception import (
    question_email, reception_questions, safe_folder_name,
)
from anka.infra.settings import load_config
from anka.infra.logging_setup import SENSITIVE, get_logger
from anka.infra.store import (
    add_dashboard_notification, add_message, begin_immediate, connect, record_event, save,
)

from anka.servicios.drive_contents import populate_project_folder
from anka.servicios.pipeline import confirmed_client
from anka.servicios.change_control import (
    assign_code, code_label, comprobar_numero, known_addresses, mark_change_sent, normalize_number,
    origen_de_destinatario, origen_en_texto,
)
from anka.servicios.quotes import case_has_links, delete_quotes, get_quote, set_client_name
from anka.servicios import envios
from anka.servicios.estados import cambiar_estado, verificar_transicion

log = get_logger(__name__)


def resolve_case_recipient(thread_id: str) -> dict:
    """Confirma en Gmail el destinatario real de un caso cuyo correo no se pudo leer y lo guarda."""
    quote = get_quote(thread_id)
    if not quote:
        raise ValueError("Este caso ya no existe. Vuelve a Mis casos.")
    config = load_config()
    gmail, _ = services()
    account_addresses = own_addresses(config, gmail)
    thread = gmail.users().threads().get(
        userId="me", id=thread_id, format="full"
    ).execute()
    inbound = inbound_messages(thread, account_addresses)
    if not inbound:
        raise ValueError("No se encontró un mensaje entrante del cliente en esta conversación.")
    routing = forwarded_routing(inbound[-1], config)
    recipient = routing.get("selected_recipient", "")
    if not valid_email_address(recipient):
        raise ValueError("Gmail no nos dio una dirección para responder. Ábrelo en Gmail y revisa.")
    db = connect()
    try:
        begin_immediate(db)
        fila = db.execute("SELECT analysis_json FROM quotes WHERE thread_id=?", (thread_id,)).fetchone()
        if not fila:
            raise ValueError("Este caso ya no existe. Vuelve a Mis casos.")
        analysis = json.loads(fila["analysis_json"])      # el de ahora, no el de antes de hablar con Gmail (U-42)
        analysis["routing"] = routing
        actualizar_caso(db, thread_id,
            recipient=recipient,
            analysis_json=json.dumps(analysis, ensure_ascii=False),
            updated_at=datetime.now().astimezone().isoformat(timespec='seconds'))
        db.commit()
    finally:
        db.close()
    return {"quote": get_quote(thread_id), "gmail": gmail, "thread": thread,
            "account_addresses": account_addresses}


def case_email_context(thread_id: str) -> tuple[dict, Any, dict, set[str]]:
    """Reúne lo necesario para escribir al cliente (caso, Gmail, hilo, direcciones); falla si ya no admite correo."""
    quote = get_quote(thread_id)
    if not quote:
        raise ValueError("Este caso ya no existe. Vuelve a Mis casos.")
    if quote.cerrado and not quote.reabierto(case_has_links(thread_id)):
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
    body = body.strip()      # lo que la persona escribió sale tal cual: nunca se recorta en silencio (U-39)
    if not body:
        raise ValueError("Escribe algo antes de guardar.")
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
        cambiar_estado(db, thread_id, QuoteStatus.DRAFT_CREATED, "guardar_borrador",
            draft_id=draft_id,
            draft_body=body,
            updated_at=now)
        add_dashboard_notification(
            db, thread_id, "draft_saved", draft_id,
            "Borrador guardado en Gmail",
            f"El borrador quedó guardado para {recipient} y todavía no ha sido enviado.",
            leida=True,
        )
        db.commit()
    finally:
        db.close()
    log.info("%s: borrador guardado en Gmail desde el dashboard", thread_id)
    return f"Guardado en Gmail sin enviar, para {recipient}."


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
                "No lo enviamos otra vez; si quieres mandar otro, cambia el texto.")
    return ""


def _send_through_gmail(gmail: Any, thread_id: str, raw: str, draft_id: str) -> dict:
    """Envía el correo y devuelve el mensaje enviado.

    Se envía el borrador existente (el del dashboard o el que dejó Work) para no dejar uno huérfano con la sección
    interna; si ya no existe en Gmail, se envía como mensaje nuevo en el hilo.
    """
    if draft_id:
        try:
            gmail.users().drafts().update(
                userId="me", id=draft_id, body={"message": {"raw": raw, "threadId": thread_id}},
            ).execute()
        except Exception as exc:
            # solo un 404 de actualizar prueba que el borrador no existe: no se mandó nada (B3-08)
            if getattr(getattr(exc, "resp", None), "status", None) != 404:
                raise
            log.warning("%s: el borrador ya no existe en Gmail; se envía como mensaje nuevo", thread_id)
        else:
            # un 404 al enviar el borrador NO autoriza otro envío: el correo pudo salir; sube y queda la reserva
            try:
                return gmail.users().drafts().send(userId="me", body={"id": draft_id}).execute()
            except Exception as exc:
                if getattr(getattr(exc, "resp", None), "status", None) == 404:
                    raise ErrorDeAnka("Gmail no encontró el borrador al enviarlo y puede que el correo ya haya salido. "
                                      "Míralo en Gmail antes de intentar otra vez.") from exc
                raise
    return gmail.users().messages().send(userId="me", body={"raw": raw, "threadId": thread_id}).execute()


def _status_after_reply(
        status: str, preguntas: int = 0, esperar: bool = False, pendientes_internos: bool = False) -> str:
    """Estado del caso después de enviarle algo al cliente, según lo que el correo llevaba (U-22).

    Se espera respuesta si el correo hizo al menos una pregunta o la persona marcó «esperar respuesta». Un acuse
    queda como «respuesta enviada», salvo que aún haya preguntas decididas como internas: entonces el caso vuelve a
    la revisión técnica para que ese trabajo no desaparezca de la cola de la persona.
    """
    # Un caso con información suficiente sigue abierto para crear su carpeta en Drive:
    # enviar un acuse o una aclaración no debe cerrarlo.
    if status in (QuoteStatus.READY_REVIEW, QuoteStatus.READY):
        return status
    if preguntas or esperar:
        return QuoteStatus.WAITING
    return QuoteStatus.REVIEW if pendientes_internos else QuoteStatus.RESPONSE_SENT


def _record_sent_reply(quote: Any, thread_id: str, message_id: str, body: str, recipient: str,
                       new_status: str, now: str, preguntas: Sequence[str] = (), esperar: bool = False) -> None:
    """Guarda el estado nuevo, el hecho del envío (con cuántas preguntas llevaba) y la novedad para el dashboard."""
    db = connect()
    try:
        begin_immediate(db)
        fila = db.execute("SELECT analysis_json FROM quotes WHERE thread_id=?", (thread_id,)).fetchone()
        # el análisis de ahora: durante el envío el puente pudo guardar uno nuevo (U-42)
        analysis = json.loads(fila["analysis_json"]) if fila else dict(quote["analysis"])
        analysis.pop("work_reply", None)  # el borrador de Work ya se envió: no debe volver a proponerse
        analysis["preguntas_enviadas"] = {"en": now, "preguntas": list(preguntas), "esperada": bool(esperar)}
        if preguntas:
            con_preguntas = f"con {len(preguntas)} pregunta(s) para el cliente: se espera su respuesta"
        elif esperar:
            con_preguntas = "sin preguntas, pero marcaste esperar su respuesta"
        else:
            con_preguntas = "sin preguntas: no se espera respuesta del cliente"
            if new_status == QuoteStatus.REVIEW:
                con_preguntas += "; el caso vuelve a revisión técnica porque quedan tareas internas por resolver"
        cambiar_estado(db, thread_id, new_status, "enviar_desde_dashboard",
            sent_message_id=message_id,
            analysis_json=json.dumps(analysis, ensure_ascii=False),
            draft_id=None,
            draft_body=body,
            sent_at=now,
            updated_at=now)
        add_message(db, thread_id, message_id, "sent", "", "", body, now)
        origen = origen_de_destinatario(thread_id, recipient, db)
        record_event(db, thread_id, "sent", "dashboard", message_id,
                     f"Correo enviado desde el dashboard a {recipient}{origen_en_texto(origen)}, {con_preguntas}.", now)
        add_dashboard_notification(
            db, thread_id, QuoteStatus.RESPONSE_SENT, message_id,
            "Respuesta enviada",
            f"El correo fue enviado a {recipient}, {con_preguntas}.",
            leida=True,
        )
        db.commit()
    finally:
        db.close()


def send_dashboard_reply(thread_id: str, body: str, to: str = "", change_id: int = 0,
                         purpose: str = "", create_folder: bool = True, esperar: bool = False,
                         state_token: str = "") -> str:
    """Envía al cliente el correo escrito en el dashboard (ya revisado por una persona) y deja constancia.

    `esperar` fuerza «esperando cliente» aunque el correo no lleve preguntas (la persona sabe que espera respuesta).
    `state_token` es la `marca_de_estado` que la pantalla tenía al abrirse: si el caso cambió desde entonces se rechaza
    (U-34.4); vacío, no se comprueba. Rechaza un reenvío idéntico reciente. Un caso con información suficiente
    crea además su carpeta en Drive, salvo que quien llama ya se haya ocupado de ella (`create_folder=False`).
    """
    body = body.strip()      # lo revisado es lo enviado: no se recorta nada en silencio (U-39, R8)
    if not body:
        raise ValueError("Escribe el correo antes de enviar.")
    actual = get_quote(thread_id)
    if state_token and actual and actual.marca_de_estado != state_token:
        raise ValueError("Este caso cambió desde que abriste la pantalla, así que el correo no se envió. Lo que "
                         "escribiste sigue aquí: recarga, revisa lo nuevo y vuelve a enviar.")
    quote, gmail, thread, account_addresses = case_email_context(thread_id)
    recipient = _recipient(thread_id, quote, to)
    previous = envios.pendiente(thread_id, recipient, body)
    if previous:
        message_id = envios.reconciliar(gmail, previous)
        if message_id:
            if quote.get('sent_message_id') != message_id:
                hechas = en_el_correo(body)
                _record_sent_reply(quote, thread_id, message_id, body, recipient,
                                   _status_after_reply(quote['status'], len(hechas)), previous['created_at'], hechas)
            envios.confirmar(previous['operation_key'], message_id)
            raise ValueError('Este correo ya se envió; no lo enviamos otra vez.')
        raise ValueError('Gmail aún no confirma si el correo salió. No lo enviamos de nuevo; '
                         'mira en Gmail antes de intentar otra vez.')
    duplicate = _already_sent(quote, body, recipient)
    if duplicate:
        raise ValueError(duplicate)
    email = reply_email(thread, recipient, body, account_addresses)
    operation_key, stable_message_id = envios.reservar(thread_id, recipient, body)
    email['Message-ID'] = stable_message_id
    raw = base64.urlsafe_b64encode(email.as_bytes()).decode("ascii")
    draft_id = quote["draft_id"] or (quote["analysis"].get("work_reply") or {}).get("draft_id") or ""
    try:
        result = _send_through_gmail(gmail, thread_id, raw, draft_id)
    except Exception as exc:
        if envios.fallo_definitivo(exc):
            envios.liberar(operation_key)
            log.warning("%s: Gmail rechazó el envío; se libera la reserva para poder reintentar", thread_id)
        raise
    envios.confirmar(operation_key, result['id'])
    now = datetime.now().astimezone().isoformat(timespec="seconds")
    hechas = en_el_correo(body)
    new_status = _status_after_reply(quote["status"], len(hechas), esperar, hay_pendientes_internos(quote["analysis"]))
    _record_sent_reply(quote, thread_id, result["id"], body, recipient, new_status, now, hechas, esperar)
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
    message = f"Correo enviado a {recipient}."
    if new_status == QuoteStatus.READY_REVIEW and create_folder:
        message += " " + auto_create_project_folder(thread_id)
    return message


AVISO = "⚠"      # marca de lo que quedó pendiente en un mensaje compuesto (la pantalla conserva entonces lo escrito)


def con_codigo_final(body: str, suggested: str, number: str) -> str:
    """El texto se preparó con el número sugerido; si la persona lo corrigió, el correo debe llevar el final."""
    suggested, number = normalize_number(suggested), normalize_number(number)
    if suggested and number and suggested != number:
        return body.replace(code_label(suggested, "T"), code_label(number, "T"))
    return body


def issue_code_and_notify(thread_id: str, number: str, body: str, suggested_number: str = "") -> str:
    """Emite la T y, en el mismo paso: crea la carpeta en Drive (con los documentos del cliente y la tabla) y envía
    al cliente el correo con el código, las instrucciones y el proceso.

    El texto (`body`) es el que la persona vio y pudo editar en el panel de la T: pulsar el botón es la revisión y el
    envío (R8). Si algo después de emitir la T falla, la T queda emitida y se dice qué quedó pendiente.
    """
    if not body.strip():
        raise ValueError("Escribe el correo para el cliente (o usa «Solo emitir la T»).")
    final_body = con_codigo_final(body, suggested_number, number)
    # Antes de emitir (la T no se deshace, D-08) se comprueba todo lo que puede fallar sin dejar rastro (U-34.2):
    # el número, la autorización de Google, el hilo en Gmail y el destinatario.
    number = comprobar_numero(thread_id, number)
    quote = case_email_context(thread_id)[0]
    _recipient(thread_id, quote, "")
    parts = [assign_code(thread_id, number).split(" Revisa el correo")[0]]
    parts.append(auto_create_project_folder(thread_id))      # carpeta, documentos del cliente y tabla
    try:
        parts.append(send_dashboard_reply(thread_id, final_body, "", 0, "received", create_folder=False))
    except (ValueError, ErrorDeAnka) as exc:     # p. ej. el mismo texto ya salió hace poco, o venció Google
        motivo = str(exc).rstrip(".")
        parts.append(f"{AVISO} El correo no se envió: {motivo}. La T quedó emitida; el correo sigue en "
                     "«Respuesta al cliente» para enviarlo.")
    except Exception:
        log.exception("%s: no se pudo enviar el correo con la T", thread_id)
        parts.append(f"{AVISO} El correo NO se envió: Gmail no respondió. La T quedó emitida; reintenta desde "
                     "«Respuesta al cliente», donde sigue el texto.")
    return " ".join(parts)


def _fill_folder(gmail: Any, drive: Any, thread_id: str, folder_id: str,
                 account_addresses: set[str]) -> str:
    """Copia los adjuntos del cliente y la tabla a la carpeta; un fallo solo se informa."""
    try:
        thread = gmail.users().threads().get(userId="me", id=thread_id, format="full").execute()
        report = populate_project_folder(
            gmail, drive, thread, folder_id, get_quote(thread_id), account_addresses)
        return report.summary()
    except Exception:
        log.exception("%s: no se pudo llenar la carpeta de Drive", thread_id)
        return ("La carpeta quedó creada, pero no se pudo copiar su contenido. "
                "Súbelo tú a mano o prueba otra vez en un momento.")


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
        except Exception:
            log.exception("No se pudo conectar con Drive para borrar carpetas")
            return "No se borró nada: no hubo conexión con Drive. Prueba otra vez en un momento."
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
        message += (f" No se borró {len(failed)} caso(s) porque su carpeta no se pudo mover a la "
                    f"papelera: {failed[0][1]}")
    return message


def _note_drive_pending(thread_id: str, message: str) -> None:
    """Deja en el caso el motivo por el que la carpeta no se creó, para mostrarlo en pantalla."""
    db = connect()
    try:
        begin_immediate(db)
        row = db.execute("SELECT analysis_json FROM quotes WHERE thread_id=?", (thread_id,)).fetchone()
        if row:
            analysis = json.loads(row["analysis_json"])
            analysis["drive"] = {"pending": message}
            actualizar_caso(db, thread_id,
                analysis_json=json.dumps(analysis, ensure_ascii=False))
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
    except Exception:  # Drive o red: el correo ya salió y no se debe perder
        log.exception("%s: no se pudo crear la carpeta automáticamente", thread_id)
        message = ("No se pudo crear la carpeta sola. "
                   "Créala tú desde la sección de Drive.")
    _note_drive_pending(thread_id, message)
    log.warning("%s: carpeta del proyecto pendiente tras el envío", thread_id)
    return "Todavía no se creó la carpeta: " + message


def _saved_client_name(db: Any, thread_id: str) -> str:
    row = db.execute(
        "SELECT client_name FROM quote_metadata WHERE thread_id=? AND client_name <> ''", (thread_id,),
    ).fetchone()
    return str(row[0]) if row else ""


def _send_questions(db: sqlite3.Connection, gmail: Any, config: Configuracion, thread_id: str, last_id: str,
                    recipient: str, analysis: Analisis, body_file: str | None, body_text: str | None,
                    account_addresses: set[str]) -> str:
    """Envía al cliente las preguntas de un caso con datos pendientes y lo deja esperando su respuesta."""
    thread = gmail.users().threads().get(userId="me", id=thread_id, format="full").execute()
    if body_text is not None:
        body = body_text
    elif body_file:
        body = Path(body_file).read_text(encoding="utf-8")
    else:
        body = question_email(
            reception_questions(analysis, config["required_fields"]),
            analysis.get("routing", {}).get("is_forwarded", False),
        )
    verificar_transicion(db, thread_id, QuoteStatus.SENDING, "iniciar_envio")
    save(db, thread_id, last_id, QuoteStatus.SENDING, recipient, analysis)
    sent_id = send_reply(gmail, thread, recipient, body, account_addresses)
    verificar_transicion(db, thread_id, QuoteStatus.WAITING, "terminar_envio")
    save(db, thread_id, last_id, QuoteStatus.WAITING, recipient, analysis, sent_message_id=sent_id)
    record_event(db, thread_id, "sent", "dashboard", sent_id,
                 f"Preguntas enviadas desde el dashboard a {recipient}.")
    db.commit()
    message = f"{thread_id}: correo enviado a {recipient}."
    log.info("%s: correo enviado tras aprobación", thread_id)
    log.info("%s", message, extra=SENSITIVE)
    return f"Correo enviado a {recipient}."


def _resolve_client(db: sqlite3.Connection, thread_id: str, analysis: Analisis, recipient: str,
                    client_name: str | None, auto: bool) -> str:
    """Nombre de la carpeta del cliente: el escrito, el ya guardado o (en modo automático) el confirmado."""
    client = (client_name or "").strip() or _saved_client_name(db, thread_id)
    if not client and auto:
        client = confirmed_client(db, thread_id, analysis, recipient)
    if auto and "@" in client:
        client = ""  # un correo no es el nombre de una empresa: no crear una carpeta con él
    if not client:
        raise ErrorDeAnka("Indica la carpeta del cliente.")
    return client


def _create_case_folder(db: sqlite3.Connection, gmail: Any, drive: Any, config: Configuracion, thread_id: str,
                        last_id: str, recipient: str, analysis: Analisis, client_name: str | None,
                        new_client: bool, auto: bool, allow_similar_project: bool,
                        account_addresses: set[str]) -> str:
    """Crea la carpeta del proyecto en Drive para un caso con alcance completo y la llena."""
    parent = config.get("drive_parent_id")
    if not parent:
        raise ErrorDeAnka("Para aprobar alcance completo define drive_parent_id en config.json.")
    client = _resolve_client(db, thread_id, analysis, recipient, client_name, auto)
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
    verificar_transicion(db, thread_id, QuoteStatus.READY, "crear_carpeta")
    save(db, thread_id, last_id, QuoteStatus.READY, recipient, analysis, folder_id=result.folder["id"])
    set_client_name(thread_id, client)  # los próximos correos del remitente se resuelven solos
    contents = _fill_folder(gmail, drive, thread_id, result.folder["id"], account_addresses)
    log.info("%s: carpeta del proyecto creada tras aprobación: %s", thread_id, result.folder["id"])
    message = "Carpeta lista."
    if result.client_created:
        log.info("%s: carpeta de cliente nuevo creada tras confirmación", thread_id)
        message += " Se creó también la carpeta del cliente nuevo."
    return message + " " + contents


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
            "SELECT last_inbound_id, status, recipient, analysis_json FROM quotes WHERE thread_id=?",
            (thread_id,),
        ).fetchone()
        if not row:
            raise ErrorDeAnka("Este caso no tiene una revisión pendiente. Vuelve a Mis casos.")
        last_id, status, recipient, analysis_json = row
        analysis: Analisis = json.loads(analysis_json)
        if status == QuoteStatus.REVIEW_PENDING:
            return _send_questions(db, gmail, config, thread_id, last_id, recipient, analysis,
                                   body_file, body_text, account_addresses)
        if status == QuoteStatus.READY_REVIEW:
            return _create_case_folder(db, gmail, drive, config, thread_id, last_id, recipient, analysis,
                                       client_name, new_client, auto, allow_similar_project, account_addresses)
        raise ErrorDeAnka("Este caso ya no está pendiente de aprobación. "
                          "Ábrelo desde Mis casos para ver en qué paso va.")
    finally:
        db.close()
