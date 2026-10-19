"""Detecta novedades de Gmail y las registra en una cola local sin ejecutar acciones comerciales (U-81)."""

from __future__ import annotations

import json
import sqlite3
import time
import uuid
from datetime import UTC, datetime, timedelta, timezone
from email.utils import getaddresses
from threading import Lock
from typing import Any

from anka.dominio.configuracion import Configuracion
from anka.dominio.correo import attachment_parts, normalized_address, valid_email_address
from anka.dominio.reloj import ahora_iso
from anka.dominio.reception import noise_signal_id
from anka.infra import settings
from anka.infra.clientes_google import ClienteGmail
from anka.infra.store import lectura, transaccion
from anka.infra.logging_setup import get_logger

log = get_logger(__name__)

CUENTA = "ankacomercialia@gmail.com"
INTERVALO_PREDETERMINADO = 60
LEASE_SEGUNDOS = 180
MAX_INTENTOS = 5
SOLAPAMIENTO_RECUPERACION_SEGUNDOS = 86400
MAX_RECUPERACION_DIAS = 30
MAX_RECUPERACION_MENSAJES = 1000
ZONA_BUSQUEDA_GMAIL = timezone(timedelta(hours=-8))
_sondeo_lock = Lock()


class PropiedadReservaPerdida(RuntimeError):
    """La reserva caducó o pertenece a otra ejecución; el propietario antiguo queda cercado."""


def _require_owner(db: sqlite3.Connection, account: str, owner: str, now_epoch: float | None = None) -> None:
    row = db.execute("SELECT lease_token,lease_until FROM recepcion_control WHERE account=?", (account,)).fetchone()
    instant = time.time() if now_epoch is None else now_epoch
    if not row or row["lease_token"] != owner or not row["lease_until"] or row["lease_until"] <= instant:
        raise PropiedadReservaPerdida("reserva_perdida")


def _renew(account: str, owner: str) -> None:
    with transaccion() as db:
        now_epoch = time.time()
        _require_owner(db, account, owner, now_epoch)
        db.execute("UPDATE recepcion_control SET lease_until=?,updated_at=? WHERE account=? AND lease_token=?",
                   (now_epoch + LEASE_SEGUNDOS, ahora_iso("seconds"), account, owner))


def _iso_millis(value: object) -> str:
    try:
        return datetime.fromtimestamp(int(str(value)) / 1000, UTC).isoformat(timespec="seconds")
    except (TypeError, ValueError, OSError, OverflowError):
        return ""


def _headers(message: dict) -> dict[str, str]:
    return {str(item.get("name", "")).casefold(): str(item.get("value", ""))
            for item in message.get("payload", {}).get("headers", []) if item.get("name")}


def _direcciones_de(value: str) -> set[str]:
    """Las direcciones válidas de un solo encabezado; uno vacío o malformado no aporta nada ni afecta a los demás."""
    if not value or not value.strip():
        return set()
    pares = getaddresses([value])
    if not any(valid_email_address(address) for _, address in pares):
        try:     # `getaddresses` estricto descarta el valor entero si una parte está mal; se rescata lo que sea válido
            pares = getaddresses([value], strict=False)
        except TypeError:        # versiones de Python sin el parámetro `strict`
            pares = []
    return {normalized_address(address) for _, address in pares if valid_email_address(address)}


def _addresses(*values: str) -> list[str]:
    """Direcciones normalizadas de varios encabezados, cada uno interpretado por separado (D-151)."""
    return sorted(set().union(*(_direcciones_de(value) for value in values)))


def _error_kind(exc: BaseException) -> str:
    status = getattr(getattr(exc, "resp", None), "status", None) or getattr(exc, "status_code", None)
    name = type(exc).__name__.casefold()
    if status == 429 or "quota" in str(exc).casefold():
        return "cuota"
    if isinstance(exc, PermissionError):
        return "acceso"
    if status in (401, 403) or "auth" in name or "credential" in name or "autoriz" in name:
        return "autorizacion" if status == 401 or "auth" in name or "credential" in name or "autoriz" in name \
            else "acceso"
    if status == 404:
        return "no_disponible"
    if status and status >= 500 or isinstance(exc, (OSError, TimeoutError, ConnectionError)):
        return "red"
    return "interno"


def _record_failure(account: str, exc: BaseException, now: str, owner: str | None = None) -> None:
    kind = _error_kind(exc)
    with transaccion() as db:
        if owner:
            _require_owner(db, account, owner)
        else:
            active = db.execute("SELECT lease_until FROM recepcion_control WHERE account=?", (account,)).fetchone()
            if active and active["lease_until"] and active["lease_until"] > time.time():
                db.execute("INSERT INTO recepcion_eventos(account,event_kind,occurred_at,detail_json) "
                           "VALUES(?,?,?,?)", (account, "fallo_fuera_de_sondeo", now,
                           json.dumps({"error_kind": kind, "error_type": type(exc).__name__}, separators=(",", ":"))))
                return
        db.execute("""INSERT INTO recepcion_control
            (account, history_id, last_attempt_at, last_error_kind, last_error_type,
             consecutive_errors, updated_at)
            VALUES (?, '', ?, ?, ?, 1, ?)
            ON CONFLICT(account) DO UPDATE SET last_attempt_at=excluded.last_attempt_at,
              last_error_kind=excluded.last_error_kind, last_error_type=excluded.last_error_type,
              consecutive_errors=recepcion_control.consecutive_errors+1, updated_at=excluded.updated_at""",
            (account, now, kind, type(exc).__name__, now))
        db.execute("INSERT INTO recepcion_eventos(account,event_kind,occurred_at,detail_json) VALUES(?,?,?,?)",
                   (account, "fallo_sondeo", now,
                    json.dumps({"error_kind": kind, "error_type": type(exc).__name__}, separators=(",", ":"))))
    # Nunca se registran el texto de Gmail, la dirección observada ni los detalles de OAuth/API.
    log.warning("Recepción Gmail falló (%s; %s)", kind, type(exc).__name__)


def registrar_fallo(exc: BaseException) -> None:
    """Persiste fallos de autenticación/conexión al construir el cliente Google (U-81)."""
    _record_failure(CUENTA, exc, ahora_iso("seconds"))


def _acquire(account: str, now_epoch: float, now: str) -> str | None:
    token = uuid.uuid4().hex
    with transaccion() as db:
        now_epoch = max(now_epoch, time.time())
        row = db.execute("SELECT lease_until FROM recepcion_control WHERE account=?", (account,)).fetchone()
        if row and row["lease_until"] and row["lease_until"] > now_epoch:
            return None
        if row:
            db.execute("UPDATE recepcion_control SET lease_token=?, lease_until=?, last_attempt_at=?, updated_at=? "
                       "WHERE account=?", (token, now_epoch + LEASE_SEGUNDOS, now, now, account))
        else:
            # history_id vacío impide que otra ejecución inicialice la cuenta mientras esta consulta su perfil.
            db.execute("INSERT INTO recepcion_control"
                       "(account,history_id,last_attempt_at,lease_token,lease_until,updated_at) "
                       "VALUES(?,'',?,?,?,?)", (account, now, token, now_epoch + LEASE_SEGUNDOS, now))
    return token


def _release(account: str, token: str) -> None:
    with transaccion() as db:
        db.execute("UPDATE recepcion_control SET lease_token=NULL, lease_until=NULL WHERE account=? AND lease_token=?",
                   (account, token))


def _enqueue(account: str, owner: str, messages: list[dict], source: str, observed_at: str,
             visto: dict[str, int] | None = None) -> int:
    with transaccion() as db:
        _require_owner(db, account, owner)
        return _enqueue_in_transaction(db, account, messages, source, observed_at, visto)


def _enqueue_in_transaction(db: sqlite3.Connection, account: str, messages: list[dict], source: str,
                            observed_at: str, visto: dict[str, int] | None = None) -> int:
    """Registra los mensajes nuevos; `visto` acumula cuántos reportó Gmail y cuántos ya estaban en la cola (D-151)."""
    created = 0
    for item in messages:
        message_id, thread_id = str(item.get("id", "")), str(item.get("threadId", ""))
        if not message_id or not thread_id:
            continue
        cursor = db.execute("""INSERT OR IGNORE INTO recepcion_mensajes
            (account,thread_id,message_id,state,detected_at,updated_at,evidence_json)
            VALUES(?,?,?,'por_leer',?,?,?)""",
                            (account, thread_id, message_id, observed_at, observed_at,
                             json.dumps({"source": source}, separators=(",", ":"))))
        created += max(0, cursor.rowcount)
        if visto is not None:
            visto["history_seen"] += 1
            visto["already_registered"] += 0 if cursor.rowcount > 0 else 1
        if cursor.rowcount > 0:
            db.execute("INSERT INTO recepcion_eventos"
                       "(account,thread_id,message_id,event_kind,occurred_at,detail_json) "
                       "VALUES(?,?,?,?,?,?)",
                       (account, thread_id, message_id, "detectado", observed_at,
                        json.dumps({"source": source}, separators=(",", ":"))))
        other = db.execute("SELECT thread_id FROM recepcion_mensajes WHERE account=? AND message_id=? "
                           "AND thread_id<>? LIMIT 1", (account, message_id, thread_id)).fetchone()
        if other:
            db.execute("UPDATE recepcion_mensajes SET state='revision_manual',disposition='id_en_otro_hilo', "
                       "error_kind='integridad',updated_at=? WHERE account=? AND thread_id=? AND message_id=?",
                       (observed_at, account, thread_id, message_id))
    return created


def _history_changes(
    gmail: ClienteGmail, account: str, owner: str, cursor: str, page_token: str | None, now: str,
    visto: dict[str, int] | None = None,
) -> tuple[int | None, str]:
    token, created = page_token, 0
    while True:
        _renew(account, owner)
        kwargs: dict[str, Any] = {"userId": "me", "startHistoryId": cursor,
                                  "historyTypes": ["messageAdded"], "maxResults": 500}
        if token:
            kwargs["pageToken"] = token
        result = gmail.users().history().list(**kwargs).execute()
        _renew(account, owner)
        messages = [entry.get("message", {}) for event in result.get("history", [])
                    for entry in event.get("messagesAdded", [])]
        next_token = result.get("nextPageToken")
        new_cursor = str(result.get("historyId", "")) if not next_token else ""
        if not next_token and (not new_cursor.isdigit() or (cursor.isdigit() and int(new_cursor) < int(cursor))):
            raise ValueError("history_id_ausente_o_regresivo")
        with transaccion() as db:
            _require_owner(db, account, owner)
            control = db.execute("SELECT history_batch_count,last_success_at FROM recepcion_control WHERE account=?",
                                 (account,)).fetchone()
            batch_count = int(control["history_batch_count"] or 0) + len(messages)
            if batch_count > MAX_RECUPERACION_MENSAJES:
                gap_start = str(control["last_success_at"] or "")
                db.execute("""UPDATE recepcion_control SET recovery_state='revision_manual',
                    recovery_gap_start_at=?,recovery_gap_end_at=?,last_error_kind='cobertura',
                    last_error_type='history_batch_supera_limite',history_batch_count=?,updated_at=?
                    WHERE account=?""", (gap_start, now, batch_count, now, account))
                db.execute("INSERT INTO recepcion_eventos(account,event_kind,occurred_at,detail_json) "
                           "VALUES(?,?,?,?)", (account, "recuperacion_requiere_revision", now,
                           json.dumps({"reason": "history_batch_supera_limite", "gap_start_at": gap_start,
                                       "gap_end_at": now, "candidates_observed": batch_count,
                                       "cursor_advanced": False}, separators=(",", ":"))))
                return None, cursor
            created += _enqueue_in_transaction(db, account, messages, "gmail_history", now, visto)
            if next_token:
                token = str(next_token)
                db.execute("UPDATE recepcion_control SET history_page_token=?,history_batch_count=?,updated_at=? "
                           "WHERE account=?", (token, batch_count, now, account))
                continue
            db.execute("""UPDATE recepcion_control SET history_id=?,history_page_token=NULL,last_success_at=?,
                last_error_kind='',last_error_type='',consecutive_errors=0,recovery_state='normal',
                recovery_gap_start_at=NULL,recovery_gap_end_at=NULL,history_batch_count=0,
                history_sync_confirmed=1,updated_at=? WHERE account=?""",
                       (new_cursor, now, now, account))
            if new_cursor != cursor:       # un sondeo sin novedades solo mueve `last_success_at` (U-81, punto 12)
                db.execute("INSERT INTO recepcion_eventos(account,event_kind,occurred_at,detail_json) VALUES(?,?,?,?)",
                           (account, "cursor_actualizado", now,
                            json.dumps({"history_id": new_cursor, "previous_history_id": cursor})))
        return created, new_cursor


def _mark_recovery_review(account: str, owner: str, now: str, gap_start: str, gap_end: str,
                          reason: str, candidates: int = 0) -> None:
    with transaccion() as db:
        _require_owner(db, account, owner)
        db.execute("""UPDATE recepcion_control SET recovery_state='revision_manual',
            recovery_gap_start_at=?,recovery_gap_end_at=?,last_error_kind='cobertura',
            last_error_type=?,updated_at=? WHERE account=?""",
                   (gap_start, gap_end, reason, now, account))
        db.execute("INSERT INTO recepcion_eventos(account,event_kind,occurred_at,detail_json) VALUES(?,?,?,?)",
                   (account, "recuperacion_requiere_revision", now,
                    json.dumps({"reason": reason, "gap_start_at": gap_start, "gap_end_at": gap_end,
                                "candidates_observed": candidates, "cursor_advanced": False}, separators=(",", ":"))))


def _parse_iso_epoch(value: str) -> float | None:
    try:
        parsed = datetime.fromisoformat(value)
        return parsed.timestamp() if parsed.tzinfo is not None else None
    except (TypeError, ValueError, OverflowError, OSError):
        return None


def _recovery_query_date(floor_epoch: float) -> str:
    # Gmail interprets dates at midnight PST; include one additional day around the lower boundary.
    day = datetime.fromtimestamp(floor_epoch, ZONA_BUSQUEDA_GMAIL).date() - timedelta(days=1)
    return day.strftime("%Y/%m/%d")


def _recover_expired_history(gmail: ClienteGmail, account: str, owner: str, now: str, now_epoch: float,
                             profile_history_id: str, last_success_at: str,
                             initial_checkpoint_at: str, history_sync_confirmed: bool,
                             visto: dict[str, int] | None = None) -> int | None:
    if not history_sync_confirmed:
        _mark_recovery_review(account, owner, now, initial_checkpoint_at, now,
                              "history_expirado_antes_de_primera_sincronizacion")
        return None
    last = _parse_iso_epoch(last_success_at)
    initial = _parse_iso_epoch(initial_checkpoint_at)
    if last is None or initial is None:
        _mark_recovery_review(account, owner, now, initial_checkpoint_at, now,
                              "checkpoint_temporal_ausente_o_invalido")
        return None
    if initial > last:
        _mark_recovery_review(account, owner, now, initial_checkpoint_at, last_success_at,
                              "checkpoint_inicial_posterior_al_ultimo_sondeo")
        return None
    if now_epoch < last or now_epoch - last > MAX_RECUPERACION_DIAS * 86400:
        _mark_recovery_review(account, owner, now, last_success_at, now, "intervalo_supera_limite_automatico")
        return None

    floor_epoch = max(initial, last - SOLAPAMIENTO_RECUPERACION_SEGUNDOS)
    query_after = _recovery_query_date(floor_epoch)
    page_token: str | None = None
    candidates: list[dict] = []
    while True:
        _renew(account, owner)
        kwargs: dict[str, Any] = {"userId": "me", "q": f"in:anywhere after:{query_after}",
                                  "includeSpamTrash": True, "maxResults": 500}
        if page_token:
            kwargs["pageToken"] = page_token
        result = gmail.users().messages().list(**kwargs).execute()
        _renew(account, owner)
        items = result.get("messages", [])
        if len(candidates) + len(items) > MAX_RECUPERACION_MENSAJES:
            _mark_recovery_review(account, owner, now, last_success_at, now,
                                  "candidatos_superan_limite_automatico", len(candidates) + len(items))
            return None
        candidates.extend(items)
        page_token = result.get("nextPageToken")
        if not page_token:
            break

    # The checkpoint moves only after every bounded page was read by the current lease owner.
    created = _enqueue(account, owner, candidates, "history_gap_recovery", now, visto)
    with transaccion() as db:
        _require_owner(db, account, owner)
        db.execute("""UPDATE recepcion_control SET history_id=?,history_page_token=NULL,last_success_at=?,
            last_error_kind='',last_error_type='',consecutive_errors=0,recovery_state='normal',
            recovery_gap_start_at=NULL,recovery_gap_end_at=NULL,history_batch_count=0,
            history_sync_confirmed=1,updated_at=? WHERE account=?""",
                   (profile_history_id, now, now, account))
        db.execute("INSERT INTO recepcion_eventos(account,event_kind,occurred_at,detail_json) VALUES(?,?,?,?)",
                   (account, "hueco_reconstruido_desde_buzon", now,
                    json.dumps({"history_id": profile_history_id, "candidates": created,
                                "coverage_start_epoch": floor_epoch, "coverage_end_epoch": now_epoch,
                                "query_after_pst": query_after, "all_pages_read": True,
                                "current_mailbox_only": True}, separators=(",", ":"))))
    log.warning("Recuperación Gmail acotada completada (%d candidatos)", created)
    return created


def _due_queue(account: str, now_epoch: float) -> list[dict]:
    with lectura() as db:
        return [dict(row) for row in db.execute("""SELECT * FROM recepcion_mensajes WHERE account=?
            AND (state='por_leer' OR (state='requiere_reintento' AND COALESCE(retry_at,0)<=?))
            ORDER BY detected_at,thread_id,message_id LIMIT 100""", (account, now_epoch))]


def _register_message(gmail: ClienteGmail, owner: str, row: dict, own_addresses: set[str], observed_at: str,
                      now_epoch: float, initial_checkpoint_at: str) -> str:
    _renew(row["account"], owner)
    message = gmail.users().messages().get(userId="me", id=row["message_id"], format="full").execute()
    _renew(row["account"], owner)
    if str(message.get("threadId", "")) != row["thread_id"]:
        raise ValueError("thread_id_inconsistente")
    headers = _headers(message)
    senders = _addresses(headers.get("from", ""))
    recipients = _addresses(headers.get("to", ""), headers.get("cc", ""), headers.get("bcc", ""))
    own = bool(set(senders) & own_addresses) or "SENT" in message.get("labelIds", [])
    baseline_epoch = _parse_iso_epoch(initial_checkpoint_at)
    message_epoch = _parse_iso_epoch(_iso_millis(message.get("internalDate")))
    before_baseline = baseline_epoch is not None and message_epoch is not None and message_epoch < baseline_epoch
    parts = attachment_parts(message.get("payload", {}))
    attachments = [{"filename": p["filename"], "mime_type": p["mime_type"], "size_bytes": p["size"],
                    "gmail_attachment_id": p.get("attachment_id") or "", "storage_path": None,
                    "status": "disponible_en_gmail"} for p in parts]
    internal_date = _iso_millis(message.get("internalDate"))
    context = {"thread_id": row["thread_id"], "source": "Gmail API", "mensajes_detectados": []}
    with lectura() as db:
        context["mensajes_detectados"] = [dict(item) for item in db.execute(
            "SELECT message_id,internal_date,sender,subject,state FROM recepcion_mensajes "
            "WHERE account=? AND thread_id=? ORDER BY detected_at,message_id",
            (row["account"], row["thread_id"]))]
    evidence = {"source": json.loads(row["evidence_json"]).get("source", "gmail_history"),
                "detected_at": row["detected_at"],
                "registered_at": observed_at, "gmail_message_id": row["message_id"],
                "gmail_thread_id": row["thread_id"], "internal_date": internal_date}
    noise_signal = noise_signal_id(message)      # R1 (T-51): la elegibilidad de los trabajos la usa después
    if noise_signal:
        evidence["noise_signal"] = noise_signal
    state, disposition = (("procesado", "mensaje_propio_excluido") if own else
                          (("procesado", "anterior_al_checkpoint_inicial") if before_baseline else
                           ("pendiente_de_procesar", "pendiente_de_interpretacion_work")))
    with transaccion() as db:
        _require_owner(db, row["account"], owner)
        db.execute("""UPDATE recepcion_mensajes SET state=?,disposition=?,sender=?,recipients_json=?,subject=?,
            header_date=?,internal_date=?,snippet=?,attachments_json=?,thread_context_json=?,evidence_json=?,
            error_kind='',error_type='',attempts=attempts+1,retry_at=NULL,processed_at=?,updated_at=?
            WHERE account=? AND thread_id=? AND message_id=?""",
                   (state, disposition, ", ".join(senders), json.dumps(recipients, ensure_ascii=False),
                    headers.get("subject", "")[:500], headers.get("date", "")[:200], internal_date,
                    "", json.dumps(attachments, ensure_ascii=False),
                    json.dumps(context, ensure_ascii=False), json.dumps(evidence, ensure_ascii=False),
                    observed_at, observed_at, row["account"], row["thread_id"], row["message_id"]))
        db.execute("INSERT INTO recepcion_eventos(account,thread_id,message_id,event_kind,occurred_at,detail_json) "
                   "VALUES(?,?,?,?,?,?)",
            (row["account"], row["thread_id"], row["message_id"],
             "propio_excluido" if own else ("anterior_checkpoint_excluido" if before_baseline else "pendiente_work"),
             observed_at, json.dumps({"disposition": disposition}, separators=(",", ":"))))
    return state


def _process_queue(
    gmail: ClienteGmail, account: str, owner: str, own_addresses: set[str], now: str, now_epoch: float,
) -> dict[str, int]:
    counts = {"processed": 0, "retried": 0, "manual_review": 0, "own_excluded": 0, "baseline_excluded": 0}
    with lectura() as db:
        control = db.execute("SELECT initial_checkpoint_at FROM recepcion_control WHERE account=?",
                             (account,)).fetchone()
    baseline = str(control["initial_checkpoint_at"] or "") if control else ""
    for row in _due_queue(account, now_epoch):
        try:
            state = _register_message(gmail, owner, row, own_addresses, now, now_epoch, baseline)
            disposition = event_packet(row["thread_id"], row["message_id"], account)
            if state == "procesado" and disposition and disposition["disposition"] == "anterior_al_checkpoint_inicial":
                counts["baseline_excluded"] += 1
            else:
                counts["own_excluded" if state == "procesado" else "processed"] += 1
        except Exception as exc:
            kind = _error_kind(exc)
            attempts = int(row["attempts"]) + 1
            manual = attempts >= MAX_INTENTOS or kind == "interno" and str(exc) == "thread_id_inconsistente"
            state = "revision_manual" if manual else "requiere_reintento"
            retry_at = None if manual else now_epoch + min(3600, 30 * (2 ** min(attempts - 1, 7)))
            with transaccion() as db:
                _require_owner(db, account, owner)
                db.execute("""UPDATE recepcion_mensajes SET state=?,error_kind=?,error_type=?,attempts=?,
                    retry_at=?,updated_at=? WHERE account=? AND thread_id=? AND message_id=?""",
                           (state, kind, type(exc).__name__, attempts, retry_at, now,
                            account, row["thread_id"], row["message_id"]))
                db.execute("INSERT INTO recepcion_eventos"
                           "(account,thread_id,message_id,event_kind,occurred_at,detail_json) "
                           "VALUES(?,?,?,?,?,?)",
                           (account, row["thread_id"], row["message_id"], state, now,
                            json.dumps({"error_kind": kind, "error_type": type(exc).__name__,
                                        "attempt": attempts}, separators=(",", ":"))))
            counts["manual_review" if manual else "retried"] += 1
            log.warning("No se pudo registrar un mensaje detectado (%s; intento %d; %s)",
                        kind, attempts, type(exc).__name__)
    return counts


def _profile_cursor(gmail: ClienteGmail, owner: str) -> str:
    _renew(CUENTA, owner)
    profile = gmail.users().getProfile(userId="me").execute()
    _renew(CUENTA, owner)
    if normalized_address(str(profile.get("emailAddress", ""))) != CUENTA:
        raise PermissionError("cuenta_gmail_no_coincide")
    history_id = str(profile.get("historyId", ""))
    if not history_id.isdigit():
        raise ValueError("gmail_sin_history_id")
    return history_id


def _save_initial_cursor(account: str, owner: str, history_id: str, observed: str) -> None:
    with transaccion() as db:
        _require_owner(db, account, owner)
        db.execute("""UPDATE recepcion_control SET history_id=?,last_success_at=?,last_error_kind='',
            last_error_type='',consecutive_errors=0,initial_checkpoint_at=?,recovery_state='normal',updated_at=?
            WHERE account=?""", (history_id, observed, observed, observed, account))
        db.execute("INSERT INTO recepcion_eventos(account,event_kind,occurred_at,detail_json) VALUES(?,?,?,?)",
                   (account, "cursor_inicializado", observed,
                    json.dumps({"history_id": history_id, "first_checkpoint_at": observed})))


def _discover_changes(gmail: ClienteGmail, owner: str, cursor: str, page_token: str | None, observed: str,
                      profile_history_id: str, last_success: str, initial_checkpoint: str,
                      history_sync_confirmed: bool, visto: dict[str, int] | None = None) -> int | None:
    try:
        discovered, _ = _history_changes(gmail, CUENTA, owner, cursor, page_token, observed, visto)
        return discovered
    except PropiedadReservaPerdida:
        raise
    except Exception as exc:
        if _error_kind(exc) != "no_disponible":
            raise
        return _recover_expired_history(gmail, CUENTA, owner, observed, time.time(),
                                        profile_history_id, last_success, initial_checkpoint,
                                        history_sync_confirmed, visto)


def sondear(gmail: ClienteGmail, config: Configuracion | None = None, now: datetime | None = None) -> dict[str, Any]:
    """Registra mensajes desde el cursor Gmail; el primer sondeo solo fija el punto de inicio (U-81)."""
    effective_config = config if config is not None else settings.load_config()
    observed = (now or datetime.now().astimezone()).isoformat(timespec="seconds")
    epoch = (now.timestamp() if now else time.time())
    lease_epoch = time.time()
    if not _sondeo_lock.acquire(blocking=False):
        return {"status": "concurrente", "discovered": 0}
    lease = None
    try:
        lease = _acquire(CUENTA, lease_epoch, observed)
        if not lease:
            return {"status": "concurrente", "discovered": 0}
        with lectura() as db:
            control_before_profile = db.execute("SELECT recovery_state,recovery_gap_start_at,recovery_gap_end_at "
                                                "FROM recepcion_control WHERE account=?", (CUENTA,)).fetchone()
        if control_before_profile and control_before_profile["recovery_state"] == "revision_manual":
            return {"status": "recuperacion_requiere_revision", "discovered": 0,
                    "gap_start_at": control_before_profile["recovery_gap_start_at"],
                    "gap_end_at": control_before_profile["recovery_gap_end_at"]}
        profile_history_id = _profile_cursor(gmail, lease)
        with lectura() as db:
            control = db.execute("SELECT * FROM recepcion_control WHERE account=?", (CUENTA,)).fetchone()
            cursor = str(control["history_id"] or "") if control else ""
            page_token = control["history_page_token"] if control else None
            last_success = str(control["last_success_at"] or "") if control else ""
            initial_checkpoint = str(control["initial_checkpoint_at"] or "") if control else ""
            history_sync_confirmed = bool(control["history_sync_confirmed"]) if control else False
        if cursor.isdigit() and int(profile_history_id) < int(cursor):
            raise ValueError("profile_history_id_regresivo")
        if not cursor:
            checkpoint_at = (now or datetime.now().astimezone()).isoformat(timespec="seconds")
            _save_initial_cursor(CUENTA, lease, profile_history_id, checkpoint_at)
            return {"status": "inicializado_sin_historial", "discovered": 0}
        own_addresses = {CUENTA, *(normalized_address(a) for a in effective_config.get("own_addresses", []) if a)}
        prior = _process_queue(gmail, CUENTA, lease, own_addresses, observed, epoch)
        visto = {"history_seen": 0, "already_registered": 0}
        discovered = _discover_changes(gmail, lease, cursor, page_token, observed,
                                       profile_history_id, last_success, initial_checkpoint,
                                       history_sync_confirmed, visto)
        if discovered is None:
            return {"status": "recuperacion_requiere_revision", "discovered": 0, **prior}
        counts = _process_queue(gmail, CUENTA, lease, own_addresses, observed, epoch)
        result: dict[str, Any] = {key: prior[key] + counts[key] for key in counts}
        result.update({"status": "ok", "discovered": discovered, **visto})
        return result
    except Exception as exc:
        if isinstance(exc, PropiedadReservaPerdida):
            log.warning("Recepción Gmail abortada: la reserva expiró o cambió de propietario")
            return {"status": "reserva_perdida", "discovered": 0}
        try:
            _record_failure(CUENTA, exc, observed, lease)
        except PropiedadReservaPerdida:
            log.warning("Recepción Gmail no registra el fallo: esta ejecución ya perdió la reserva")
            return {"status": "reserva_perdida", "discovered": 0}
        return {"status": "error", "error_kind": _error_kind(exc), "error_type": type(exc).__name__}
    finally:
        if lease:
            _release(CUENTA, lease)
        _sondeo_lock.release()


def event_packet(thread_id: str, message_id: str, account: str = CUENTA) -> dict[str, Any] | None:
    """Devuelve el evento registrado con referencias verificables para una integración futura con Work."""
    with lectura() as db:
        row = db.execute("SELECT * FROM recepcion_mensajes WHERE account=? AND thread_id=? AND message_id=?",
                         (account, thread_id, message_id)).fetchone()
        control = db.execute("SELECT recovery_state,recovery_gap_start_at,recovery_gap_end_at "
                             "FROM recepcion_control WHERE account=?", (account,)).fetchone()
    if not row:
        return None
    return {"message_id": row["message_id"], "thread_id": row["thread_id"], "account": row["account"],
            "sender": row["sender"], "recipients": json.loads(row["recipients_json"]),
            "subject": row["subject"], "header_date": row["header_date"],
            "internal_date": row["internal_date"],
            "thread_context": json.loads(row["thread_context_json"]),
            "attachments": json.loads(row["attachments_json"]), "state": row["state"],
            "disposition": row["disposition"], "evidence": json.loads(row["evidence_json"]),
            "mailbox_recovery": ({"state": control["recovery_state"],
                                  "gap_start_at": control["recovery_gap_start_at"],
                                  "gap_end_at": control["recovery_gap_end_at"]} if control else None),
            "error": {"kind": row["error_kind"], "type": row["error_type"]} if row["error_kind"] else None,
            "audit": _message_events(account, thread_id, message_id)}


def _message_events(account: str, thread_id: str, message_id: str) -> list[dict[str, Any]]:
    with lectura() as db:
        return [{"event": row["event_kind"], "occurred_at": row["occurred_at"],
                 "detail": json.loads(row["detail_json"])}
                for row in db.execute("""SELECT event_kind,occurred_at,detail_json FROM recepcion_eventos
                    WHERE account=? AND thread_id=? AND message_id=? ORDER BY id""",
                                      (account, thread_id, message_id))]


PENDIENTE = ("pendiente_de_procesar", "pendiente_de_interpretacion_work")
INTERPRETADO = ("procesado", "interpretado_por_work")


def _evento_unico(db: sqlite3.Connection, thread_id: str, message_id: str, kind: str, when: str,
                  detail: dict[str, Any]) -> None:
    """Anota el evento una sola vez por mensaje: reimportar no lo repite."""
    existe = db.execute("SELECT 1 FROM recepcion_eventos WHERE account=? AND thread_id=? AND message_id=? "
                        "AND event_kind=? LIMIT 1", (CUENTA, thread_id, message_id, kind)).fetchone()
    if not existe:
        db.execute("INSERT INTO recepcion_eventos(account,thread_id,message_id,event_kind,occurred_at,detail_json) "
                   "VALUES(?,?,?,?,?,?)", (CUENTA, thread_id, message_id, kind, when,
                                           json.dumps(detail, separators=(",", ":"), sort_keys=True)))


def conciliar_con_work(db: sqlite3.Connection, thread_id: str, message_id: str, analisis_ref: str,
                       analisis_completo: object = None, estado_work: str = "", ahora: str | None = None,
                       clasificacion: str = "") -> str:
    """Cierra en la cola el mensaje que Work interpretó y el dashboard guardó (T-48, D-152).

    Se ejecuta con la conexión y dentro de la transacción del importador: el cambio y el análisis se confirman juntos.
    Concilia solo la fila `(thread_id, message_id)` exacta y solo desde `pendiente_de_procesar`; no inventa filas ni
    toca el cursor. No juzga el análisis: uno incompleto también se concilia. Devuelve qué pasó: `conciliado`,
    `ya_conciliado`, `sin_registro`, `diferida`, `omitida` o `sin_identidad`.
    """
    thread_id, message_id = str(thread_id or "").strip(), str(message_id or "").strip()
    if not thread_id or not message_id:
        return "sin_identidad"
    when = ahora or ahora_iso("seconds")
    fila = db.execute("SELECT state,disposition,evidence_json FROM recepcion_mensajes "
                      "WHERE account=? AND thread_id=? AND message_id=?", (CUENTA, thread_id, message_id)).fetchone()
    if fila is None:
        _evento_unico(db, thread_id, message_id, "conciliacion_sin_registro", when, {"analysis_ref": analisis_ref})
        return "sin_registro"
    actual = (fila["state"], fila["disposition"])
    if actual == INTERPRETADO:
        return "ya_conciliado"
    if actual != PENDIENTE:
        diferida = fila["state"] in ("por_leer", "requiere_reintento")
        _evento_unico(db, thread_id, message_id, "conciliacion_diferida" if diferida else "conciliacion_omitida",
                      when, {"state": fila["state"], "disposition": fila["disposition"], "analysis_ref": analisis_ref})
        return "diferida" if diferida else "omitida"
    try:
        evidencia = json.loads(fila["evidence_json"] or "{}")
    except ValueError:
        evidencia = {}
    work: dict[str, Any] = {"analysis_ref": analisis_ref, "reconciled_at": when}
    if isinstance(analisis_completo, bool):
        work["analysis_complete"] = analisis_completo
    if estado_work:
        work["work_status"] = estado_work
    if clasificacion:
        work["classification"] = clasificacion      # T-52: lo que Work confirmó o corrigió
    evidencia["work"] = work
    cursor = db.execute("""UPDATE recepcion_mensajes SET state=?,disposition=?,evidence_json=?,updated_at=?
        WHERE account=? AND thread_id=? AND message_id=? AND state=? AND disposition=?""",
                        (*INTERPRETADO, json.dumps(evidencia, ensure_ascii=False), when, CUENTA, thread_id,
                         message_id, *PENDIENTE))
    if cursor.rowcount != 1:
        return "ya_conciliado"       # otro proceso lo cerró entre la lectura y la escritura
    db.execute("INSERT INTO recepcion_eventos(account,thread_id,message_id,event_kind,occurred_at,detail_json) "
               "VALUES(?,?,?,?,?,?)", (CUENTA, thread_id, message_id, "interpretado_por_work", when,
                                       json.dumps(work, separators=(",", ":"), sort_keys=True)))
    if clasificacion:
        _evento_unico(db, thread_id, message_id, "clasificacion_work", when, {"clase": clasificacion})
    return "conciliado"


def conciliar_pendientes(ahora: str | None = None) -> dict[str, int]:
    """Concilia las filas pendientes cuyo mensaje ya tiene un análisis de Work guardado (T-48, punto 9).

    Cubre lo importado antes de la conciliación automática y lo que se difirió. Es idempotente y no se ejecuta sola.
    Un caso mínimo (T-36) no cuenta, y el análisis de otro mensaje del hilo tampoco.
    """
    cuentas = {"revisadas": 0, "conciliado": 0, "otros": 0}
    with transaccion() as db:
        filas = db.execute("""SELECT r.thread_id,r.message_id,q.analysis_json FROM recepcion_mensajes r
            JOIN quotes q ON q.thread_id=r.thread_id
            WHERE r.account=? AND r.state=? AND r.disposition=?
              AND json_extract(q.analysis_json,'$.work_message_id')=r.message_id
            ORDER BY r.detected_at,r.thread_id,r.message_id""", (CUENTA, *PENDIENTE)).fetchall()
        for fila in filas:
            analisis = json.loads(fila["analysis_json"])
            if analisis.get("sin_analisis_de_work"):
                continue
            cuentas["revisadas"] += 1
            hecho = db.execute("SELECT ref FROM case_events WHERE thread_id=? AND kind='analysis' AND source='work' "
                               "ORDER BY id DESC LIMIT 1", (fila["thread_id"],)).fetchone()
            resultado = conciliar_con_work(db, fila["thread_id"], fila["message_id"], hecho["ref"] if hecho else "",
                                           analisis.get("analysis_complete"), ahora=ahora,
                                           clasificacion=analisis.get("classification", ""))
            cuentas["conciliado" if resultado == "conciliado" else "otros"] += 1
    return cuentas
