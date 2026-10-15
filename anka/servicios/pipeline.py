"""Ciclo de revisión del buzón: decide qué hacer con cada hilo y lo aplica."""

from __future__ import annotations
from anka.infra.clientes_google import ClienteDrive, ClienteGmail
from anka.servicios.estados import cambiar_estado, verificar_transicion

import json
import sqlite3
from dataclasses import dataclass, replace
from collections.abc import Callable, Sequence
from typing import Any
from anka.dominio.configuracion import Configuracion
from anka.dominio.analisis import Analisis
from anka.dominio.errores import ContextoInsuficiente, ErrorDeAnka, ModoNoPermitido
from anka.dominio.status import (
    QuoteStatus, estado_tras_un_analisis,
)
from anka.dominio.decision import Action, Decision, decide_analysis, decide_prefilter
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
    mentions_commercial_terms,
)
from anka.dominio.reintentos import analisis_de_fallo, motivo_legible
from anka.servicios import reintentos
from anka.servicios.lectura_por_tramos import analizar_hilo
from anka.infra.modes import (
    ensure_local_or_api_mode,
)
from anka.infra.presupuesto_ocr import presupuesto_de_caso
from anka.infra.settings import load_config
from anka.dominio.activities import limpiar_cantidad_y_unidad, normalize_estado
from anka.servicios.change_control import apply_guard, find_codes
from email.utils import parseaddr
from anka.infra.store import connect, save
from anka.servicios.repositorio import hilos_descartados_por_reanalizar, hilos_en_estado

from anka.servicios.hoja_de_calculo import programar_actualizacion_de_hoja
from anka.servicios.cola_del_piloto import save_review_packet


log = get_logger(__name__)


@dataclass
class RevisorDeHilos:
    """Revisa los hilos de Gmail de una pasada: decide qué hacer con cada uno y lo aplica.

    Es una clase porque todo el procesamiento comparte el mismo contexto (configuración, Gmail, Drive, base de
    datos, direcciones propias y modo) que antes viajaba como un argumento por cada función. Sin base de datos
    (`db=None`) es una simulación: decide e informa, pero no guarda ni envía nada.
    """

    def __init__(self, config: Configuracion, gmail: ClienteGmail, drive: ClienteDrive, db: sqlite3.Connection | None,
                 account_addresses: set[str], review_only: bool, gmail_drafts: bool,
                 comprobar_modo: Callable[[], None] | None = None, respetar_espera: bool = True) -> None:
        self.config = config
        self.gmail = gmail
        self.drive = drive
        self.db = db
        self.account_addresses = account_addresses
        self.review_only = review_only
        self.gmail_drafts = gmail_drafts
        # Se llama antes de cada hilo: si el modo cambió a Work a mitad de una pasada, se detiene en el acto.
        self.comprobar_modo = comprobar_modo
        # False cuando una persona pide la revisión (botón «Actualizar correo»): se reintenta todo, sin esperas (T-26).
        self.respetar_espera = respetar_espera

    def pending_thread_ids(self) -> set[str]:
        """Hilos a revisar: los que coinciden con la búsqueda de Gmail y los que quedaron pendientes en la base."""
        thread_ids = set(candidate_thread_ids(
            self.gmail, self.config.get("gmail_query", "in:inbox newer_than:7d")
        ))
        if self.db:
            thread_ids.update(hilos_en_estado(self.db, QuoteStatus.WAITING))
            thread_ids.update(hilos_descartados_por_reanalizar(self.db))
            if self.gmail_drafts:
                thread_ids.update(hilos_en_estado(self.db, QuoteStatus.REVIEW_PENDING))
        return thread_ids

    def run(self) -> None:
        """Procesa cada hilo pendiente; uno que ya no se puede leer en Gmail queda marcado como no disponible."""
        for thread_id in sorted(self.pending_thread_ids()):
            if self.comprobar_modo:
                self.comprobar_modo()
            proximo = reintentos.proximo_intento(self.db, thread_id) if self.db and self.respetar_espera else None
            if proximo:  # falló hace poco: se espera su turno (backoff) para no gastar modelo y Gmail en cada ciclo
                log.debug("%s: en espera tras un fallo; se reintenta a partir de %s", thread_id, f"{proximo:%H:%M}")
                continue
            try:
                thread = self.gmail.users().threads().get(
                    userId="me", id=thread_id, format="full"
                ).execute()
            except Exception as exc:
                log.warning("%s: no se pudo leer el hilo en Gmail: %s", thread_id, exc)
                if self.db:
                    cambiar_estado(self.db, thread_id, QuoteStatus.MISSING, "hilo_no_disponible")
                    self.db.commit()
                continue
            try:
                self.process_thread(thread_id, thread)
                if self.db:
                    reintentos.limpiar(self.db, thread_id)
            except ModoNoPermitido:
                raise
            except ContextoInsuficiente as exc:
                # No se analiza con documentos recortados; el hilo queda sin procesar y se informa (T-24).
                log.warning("%s: %s", thread_id, exc)
                self._registrar_fallo(thread_id, thread, exc)
            except Exception as exc:
                # Un hilo que falla (modelo ocupado, respuesta inválida...) no debe frenar a los demás. Se reintenta
                # con espera creciente y, gracias a la caché de hallazgos, sin repetir los tramos ya leídos (T-26).
                log.exception("%s: no se pudo analizar el hilo", thread_id)
                self._registrar_fallo(thread_id, thread, exc)

    def _registrar_fallo(self, thread_id: str, thread: dict, error: Exception) -> None:
        """Anota el fallo con su espera y deja el hilo visible como caso de revisión técnica (T-26).

        Un hilo que ya tiene un análisis válido no se sobrescribe: el fallo queda solo en el registro de reintentos.
        """
        db = self.db
        if not db:
            return
        motivo = motivo_legible(error)
        fallos, proximo = reintentos.registrar_fallo(db, thread_id, motivo)
        log.warning("%s: intento %d fallido; se reintenta a partir de %s", thread_id, fallos, f"{proximo:%H:%M}")
        fila = db.execute("SELECT analysis_json FROM quotes WHERE thread_id=?", (thread_id,)).fetchone()
        if fila and "analysis_failure" not in (fila["analysis_json"] or ""):
            return
        inbound = inbound_messages(thread, self.account_addresses)
        if not inbound:
            return
        last = inbound[-1]
        routing = forwarded_routing(last, self.config)
        if not routing["selected_recipient"]:
            return
        analysis = analisis_de_fallo(
            header(last, "Subject"), header(last, "From"), message_datetime(last), routing, motivo, fallos, proximo,
        )
        # last_inbound_id vacío: al vencer la espera el hilo se vuelve a analizar aunque no haya mensajes nuevos
        save(db, thread_id, "", QuoteStatus.REVIEW, routing["selected_recipient"], analysis)
        db.commit()

    def process_thread(self, thread_id: str, thread: dict) -> None:
        """Decide y aplica qué hacer con un hilo: descartar, pedir datos, dejar para revisión o crear la carpeta."""
        config = self.config
        inbound = inbound_messages(thread, self.account_addresses)
        if not inbound:
            log.info("%s: solo contiene mensajes propios; se omite", thread_id)
            return
        last = inbound[-1]
        routing = forwarded_routing(last, config)
        recipient = routing["selected_recipient"]
        if not recipient:
            log.warning("%s: sin remitente; requiere revisión", thread_id)
            return
        row = self.db.execute(
            "SELECT last_inbound_id, status, analysis_json FROM quotes WHERE thread_id=?",
            (thread_id,),
        ).fetchone() if self.db else None
        skip = _skip_reason(row, last["id"], self.gmail_drafts)
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
            with presupuesto_de_caso(config):   # el tiempo de OCR se cuenta por caso (U-55)
                for message in inbound:
                    attachments.extend(extract_message_attachments(self.gmail, message))
            blocked = [item for item in attachments
                       if item.get("status") in {"bloqueado", "lectura_parcial"}]
            required = config["required_fields"]
            analysis = analizar_hilo(inbound, required, config["llm"], attachments)
            analysis["received_at"] = message_datetime(last)
            analysis["routing"] = routing
            analysis["email"] = email
            analysis["attachments"] = attachment_report(attachments, analysis, required)
            for row in analysis.get("activities", []):
                if isinstance(row, dict):
                    limpiar_cantidad_y_unidad(row)
                if isinstance(row, dict) and "estado" in row:
                    row["estado"] = normalize_estado(row["estado"])  # R12
            if self.db:
                # R11: con T asignada, la tabla solo cambia por el contacto registrado y citando el código
                sender_name, sender_email = parseaddr(header(last, "From"))
                body_text = message_text(last.get("payload", {}))
                apply_guard(
                    self.db, thread_id, analysis, routing.get("original_sender_email") or sender_email,
                    sender_name, sorted(find_codes(email["subject"] + "\n" + body_text)), last["id"],
                    email["subject"], " ".join(body_text.split())[:160], "python")
            decision = decide_analysis(
                thread_id, analysis, blocked, recipient, routing["is_forwarded"], config,
                review_only=self.review_only, gmail_drafts=self.gmail_drafts,
            )
        self.apply_decision(thread, thread_id, last, recipient, decision)

    def apply_decision(
        self, thread: dict, thread_id: str, last: dict, recipient: str, d: Decision,
    ) -> None:
        """Ejecuta los efectos de una decisión. Sin base de datos (simulación) solo informa."""
        for note in d.logs:
            log.log(note.level, "%s", note.text, extra=SENSITIVE if note.sensitive else None)
        db = self.db
        if not db:
            return
        d = self._respetar_estado_avanzado(db, thread_id, last, d)
        verificar_transicion(db, thread_id, d.status, "analisis_python")
        packet_body = d.body
        draft_id = None
        if d.action is Action.GMAIL_DRAFT:
            packet_body = draft_body_with_review_note(d.analysis, d.questions, d.body, last)
            draft_id = create_reply_draft(
                self.gmail, thread, recipient, packet_body, self.account_addresses
            )
        packet = None
        if d.packet:
            packet = save_review_packet(
                thread_id, recipient, d.analysis, d.questions, packet_body or None,
                d.folder_name or None,
            )
        if d.action is Action.SEND:
            save(db, thread_id, last["id"], QuoteStatus.SENDING, recipient, d.analysis)
            sent_id = send_reply(self.gmail, thread, recipient, d.body, self.account_addresses)
            save(db, thread_id, last["id"], d.status, recipient, d.analysis,
                 sent_message_id=sent_id)
        elif d.action is Action.FOLDER:
            self._create_project_folder_or_hold(thread_id, last, recipient, d, thread)
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
        programar_actualizacion_de_hoja(thread_id)   # si el análisis cambió la tabla, la hoja de Google lo refleja

    @staticmethod
    def _respetar_estado_avanzado(db: sqlite3.Connection, thread_id: str, last: dict, d: Decision) -> Decision:
        """Un caso `ready`, `sending` o `response_sent` no retrocede por un análisis (U-47, igual que Work).

        El análisis se guarda, pero el estado queda y no se envía ni se crea carpeta: el caso ya pasó por ahí.
        """
        row = db.execute("SELECT status, last_inbound_id FROM quotes WHERE thread_id=?", (thread_id,)).fetchone()
        if not row:
            return d
        mismo = row["last_inbound_id"] == last["id"]
        final = estado_tras_un_analisis(row["status"], d.status, mismo)
        if estado_tras_un_analisis(row["status"], "", mismo) == "":     # el caso no está protegido
            return d
        log.info("%s: el caso sigue en «%s»; el análisis nuevo no lo hace retroceder", thread_id, final)
        accion = Action.NONE if d.action in (Action.SEND, Action.FOLDER) else d.action
        return replace(d, status=QuoteStatus(final), action=accion)

    def _create_project_folder_or_hold(
        self, thread_id: str, last: dict, recipient: str, d: Decision, thread: dict,
    ) -> None:
        """Crea `Año/Cliente/Solicitud` solo si el cliente es seguro; si no, lo deja para una persona.

        Nunca crea una carpeta de cliente por cuenta propia (ver drive_layout).
        """
        db = self.db
        assert db is not None
        parent = self.config["drive_parent_id"]
        hold: tuple[str, str] | None = None  # (código genérico, mensaje completo)
        folder: dict | None = None
        mismatch = year_mismatch(self.drive, parent, d.analysis.get("received_at", ""))
        client = confirmed_client(db, thread_id, d.analysis, recipient)
        if mismatch:
            hold = ("año de la carpeta padre", mismatch)
        elif not client:
            hold = ("cliente sin confirmar",
                    "No hay un cliente confirmado para este remitente. Indica la carpeta del cliente.")
        else:
            try:
                folder = create_project_folder(self.drive, parent, client, d.folder_name).folder
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
                self.gmail, self.drive, thread, folder["id"], quote, self.account_addresses)
            log.info("%s: %s", thread_id, report.summary())
        except Exception:
            log.exception("%s: no se pudo llenar la carpeta de Drive", thread_id)


def confirmed_client(db: sqlite3.Connection, thread_id: str, analysis: Analisis, recipient: str) -> str:
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


def run(apply: bool, review_only: bool = False, gmail_drafts: bool = False, respetar_espera: bool = True) -> None:
    """Revisa Gmail una vez: simula o, con `apply`, guarda para revisión (y envía o crea carpetas sin `review_only`)."""
    config = load_config()
    ensure_local_or_api_mode(config)
    if review_only and not apply:
        raise ErrorDeAnka("--review-only se usa junto con --apply.")
    if gmail_drafts and not apply:
        raise ErrorDeAnka("--gmail-drafts se usa junto con --apply.")
    if apply and not config["required_fields"]:
        raise ErrorDeAnka("Para --apply define required_fields en config.json.")
    if apply and not review_only and not gmail_drafts and not config.get("drive_parent_id"):
        raise ErrorDeAnka("Para --apply define drive_parent_id en config.json.")
    gmail, drive = services()
    db = connect() if apply else None
    try:
        RevisorDeHilos(
            config, gmail, drive, db, own_addresses(config, gmail), review_only, gmail_drafts,
            comprobar_modo=lambda: ensure_local_or_api_mode(load_config()), respetar_espera=respetar_espera,
        ).run()
    finally:
        if db:
            db.close()
