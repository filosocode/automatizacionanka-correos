"""Persistencia SQLite del estado de solicitudes y novedades del dashboard."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from datetime import datetime
from pathlib import Path

from status import QuoteStatus, sql_in


QUOTES_EXTRA_COLUMNS = ("first_seen_at", "updated_at", "draft_id", "draft_body", "sent_at")


def _migration_1_quotes(db: sqlite3.Connection) -> None:
    """Tabla de solicitudes; completa las columnas que faltan en bases anteriores."""
    db.execute("""CREATE TABLE IF NOT EXISTS quotes (
        thread_id TEXT PRIMARY KEY, last_inbound_id TEXT NOT NULL,
        status TEXT NOT NULL, recipient TEXT NOT NULL,
        analysis_json TEXT NOT NULL, folder_id TEXT, sent_message_id TEXT,
        first_seen_at TEXT, updated_at TEXT, draft_id TEXT,
        draft_body TEXT, sent_at TEXT
    )""")
    existing = {row["name"] for row in db.execute("PRAGMA table_info(quotes)").fetchall()}
    for column in QUOTES_EXTRA_COLUMNS:
        if column not in existing:
            db.execute(f"ALTER TABLE quotes ADD COLUMN {column} TEXT")


def _migration_2_quote_metadata(db: sqlite3.Connection) -> None:
    db.execute("""CREATE TABLE IF NOT EXISTS quote_metadata (
        thread_id TEXT PRIMARY KEY,
        client_type TEXT NOT NULL DEFAULT 'No clasificado',
        sector TEXT NOT NULL DEFAULT 'No clasificado',
        client_name TEXT NOT NULL DEFAULT '',
        notes TEXT NOT NULL DEFAULT '',
        updated_at TEXT NOT NULL,
        FOREIGN KEY(thread_id) REFERENCES quotes(thread_id)
    )""")


def _migration_3_notifications(db: sqlite3.Connection) -> None:
    db.execute("""CREATE TABLE IF NOT EXISTS dashboard_notifications (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        thread_id TEXT NOT NULL,
        kind TEXT NOT NULL,
        source_key TEXT NOT NULL,
        title TEXT NOT NULL,
        message TEXT NOT NULL,
        created_at TEXT NOT NULL,
        read_at TEXT,
        UNIQUE(thread_id, kind, source_key)
    )""")


def _migration_4_case_events(db: sqlite3.Connection) -> None:
    """Seguimiento de cada caso: qué pasó, cuándo y desde dónde (dashboard, Gmail o Work)."""
    db.execute("""CREATE TABLE IF NOT EXISTS case_events (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        thread_id TEXT NOT NULL,
        kind TEXT NOT NULL,
        source TEXT NOT NULL,
        ref TEXT NOT NULL,
        summary TEXT NOT NULL,
        occurred_at TEXT NOT NULL,
        recorded_at TEXT NOT NULL,
        UNIQUE(thread_id, kind, ref)
    )""")


def _migration_5_traceability(db: sqlite3.Connection) -> None:
    """Quién hizo cada cosa (persona y empresa) y correos de otros hilos que podrían ser del caso."""
    existing = {row["name"] for row in db.execute("PRAGMA table_info(case_events)").fetchall()}
    for column in ("actor_email", "actor_name"):
        if column not in existing:
            db.execute(f"ALTER TABLE case_events ADD COLUMN {column} TEXT NOT NULL DEFAULT ''")
    db.execute("""CREATE TABLE IF NOT EXISTS case_links (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        thread_id TEXT NOT NULL,
        other_thread_id TEXT NOT NULL,
        message_id TEXT NOT NULL,
        sender_email TEXT NOT NULL,
        sender_name TEXT NOT NULL DEFAULT '',
        subject TEXT NOT NULL DEFAULT '',
        snippet TEXT NOT NULL DEFAULT '',
        reason TEXT NOT NULL,
        state TEXT NOT NULL DEFAULT 'pending',
        occurred_at TEXT NOT NULL,
        decided_at TEXT,
        UNIQUE(thread_id, message_id)
    )""")


def event_hash(prev: str, thread_id: str, kind: str, source: str, ref: str, summary: str,
               occurred_at: str, actor_email: str) -> str:
    """Huella de un hecho encadenada con la del anterior: alterar o quitar uno rompe la cadena."""
    text = "\x1f".join((prev, thread_id, kind, source, ref, summary, occurred_at, actor_email))
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _migration_6_codes_and_evidence(db: sqlite3.Connection) -> None:
    """Código de la oferta (T / T&E), contacto registrado, cambios solicitados y cadena de huellas."""
    existing = {row["name"] for row in db.execute("PRAGMA table_info(quotes)").fetchall()}
    for column in ("quote_code", "code_stage", "contact_email", "code_assigned_at"):
        if column not in existing:
            db.execute(f"ALTER TABLE quotes ADD COLUMN {column} TEXT")
    events = {row["name"] for row in db.execute("PRAGMA table_info(case_events)").fetchall()}
    for column in ("prev_hash", "hash"):
        if column not in events:
            db.execute(f"ALTER TABLE case_events ADD COLUMN {column} TEXT NOT NULL DEFAULT ''")
    links = {row["name"] for row in db.execute("PRAGMA table_info(case_links)").fetchall()}
    if "has_code" not in links:
        db.execute("ALTER TABLE case_links ADD COLUMN has_code INTEGER NOT NULL DEFAULT 0")
    db.execute("""CREATE TABLE IF NOT EXISTS case_changes (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        thread_id TEXT NOT NULL,
        message_id TEXT NOT NULL,
        sender_email TEXT NOT NULL,
        sender_name TEXT NOT NULL DEFAULT '',
        subject TEXT NOT NULL DEFAULT '',
        snippet TEXT NOT NULL DEFAULT '',
        occurred_at TEXT NOT NULL,
        has_code INTEGER NOT NULL DEFAULT 0,
        is_contact INTEGER NOT NULL DEFAULT 0,
        same_company INTEGER NOT NULL DEFAULT 0,
        state TEXT NOT NULL DEFAULT 'pending',
        reply_sent_at TEXT,
        contact_notified_at TEXT,
        decided_at TEXT,
        UNIQUE(thread_id, message_id)
    )""")
    # el contacto registrado es quien escribió primero
    for row in db.execute("SELECT thread_id, recipient, analysis_json FROM quotes "
                          "WHERE contact_email IS NULL").fetchall():
        try:
            routing = json.loads(row["analysis_json"]).get("routing", {})
        except (TypeError, ValueError):
            routing = {}
        contact = (routing.get("original_sender_email") or row["recipient"] or "").strip().casefold()
        db.execute("UPDATE quotes SET contact_email=? WHERE thread_id=?", (contact, row["thread_id"]))
    # cadena de huellas de los hechos ya guardados
    previous: dict[str, str] = {}
    for row in db.execute("SELECT id, thread_id, kind, source, ref, summary, occurred_at, "
                          "actor_email FROM case_events ORDER BY id").fetchall():
        prev = previous.get(row["thread_id"], "")
        digest = event_hash(prev, row["thread_id"], row["kind"], row["source"], row["ref"],
                            row["summary"], row["occurred_at"], row["actor_email"])
        db.execute("UPDATE case_events SET prev_hash=?, hash=? WHERE id=?", (prev, digest, row["id"]))
        previous[row["thread_id"]] = digest


def _migration_7_table_versions(db: sqlite3.Connection) -> None:
    """Versiones de la tabla de actividades: la del modelo y cada edición de una persona."""
    db.execute("""CREATE TABLE IF NOT EXISTS table_versions (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        thread_id TEXT NOT NULL,
        version INTEGER NOT NULL,
        source TEXT NOT NULL,
        activities_json TEXT NOT NULL,
        digest TEXT NOT NULL,
        summary TEXT NOT NULL DEFAULT '',
        reason TEXT NOT NULL DEFAULT '',
        created_at TEXT NOT NULL,
        UNIQUE(thread_id, version)
    )""")


def add_table_version(db: sqlite3.Connection, thread_id: str, activities: list, source: str,
                      summary: str, reason: str = "") -> int:
    """Guarda una versión de la tabla (source: 'previa', 'model' o 'dashboard'); devuelve su número."""
    text = json.dumps(activities, ensure_ascii=False, sort_keys=True)
    last = db.execute("SELECT version FROM table_versions WHERE thread_id=? ORDER BY version DESC LIMIT 1",
                      (thread_id,)).fetchone()
    version = (last["version"] if last else 0) + 1
    db.execute(
        """INSERT INTO table_versions
           (thread_id, version, source, activities_json, digest, summary, reason, created_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
        (thread_id, version, source, text, hashlib.sha256(text.encode("utf-8")).hexdigest(),
         summary, reason, datetime.now().astimezone().isoformat(timespec="seconds")))
    return version


def latest_table_version(db: sqlite3.Connection, thread_id: str) -> sqlite3.Row | None:
    return db.execute("SELECT * FROM table_versions WHERE thread_id=? ORDER BY version DESC LIMIT 1",
                      (thread_id,)).fetchone()


# Para cambiar el esquema: agrega una función al final de esta lista. Nunca edites ni
# reordenes las anteriores; las bases ya migradas no las volverán a ejecutar.
MIGRATIONS = (
    _migration_1_quotes,
    _migration_2_quote_metadata,
    _migration_3_notifications,
    _migration_4_case_events,
    _migration_5_traceability,
    _migration_6_codes_and_evidence,
    _migration_7_table_versions,
)
SCHEMA_VERSION = len(MIGRATIONS)


def migrate(db: sqlite3.Connection, db_path: Path | None = None) -> None:
    """Lleva la base a SCHEMA_VERSION, cada paso en su propia transacción.

    Antes de migrar una base con datos deja una copia `quotes.sqlite3.bak-vN`.
    Rechaza bases creadas por una versión más nueva del programa.
    """
    current = db.execute("PRAGMA user_version").fetchone()[0]
    if current > SCHEMA_VERSION:
        raise RuntimeError(
            f"La base es de una versión más nueva (esquema {current}) que este programa "
            f"({SCHEMA_VERSION}). Actualiza el programa; no se modificó nada."
        )
    if current == SCHEMA_VERSION:
        return
    has_data = db.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='quotes'"
    ).fetchone()
    if has_data and db_path is not None:
        backup = db_path.with_name(f"{db_path.name}.bak-v{current}")
        if not backup.exists():
            target = sqlite3.connect(backup)
            try:
                db.backup(target)
            finally:
                target.close()
    for version in range(current + 1, SCHEMA_VERSION + 1):
        db.execute("BEGIN")
        try:
            MIGRATIONS[version - 1](db)
            db.execute(f"PRAGMA user_version = {version}")
        except BaseException:
            db.rollback()
            raise
        db.commit()


def _derive_notifications(db: sqlite3.Connection) -> None:
    """Crea las novedades que corresponden a solicitudes ya guardadas.

    Es idempotente y corre en cada apertura a propósito: `save()` no genera novedades,
    esta derivación es lo que las produce (UNIQUE evita duplicados).
    """
    db.execute(f"""INSERT OR IGNORE INTO dashboard_notifications
                  (thread_id, kind, source_key, title, message, created_at)
                  SELECT thread_id, 'request_received', last_inbound_id,
                         'Correo nuevo recibido',
                         'La solicitud ya fue analizada y agregada al dashboard.',
                         first_seen_at
                  FROM quotes
                  WHERE first_seen_at IS NOT NULL AND status <> '{QuoteStatus.IGNORED}'""")
    db.execute(f"""INSERT OR IGNORE INTO dashboard_notifications
                  (thread_id, kind, source_key, title, message, created_at)
                  SELECT thread_id, 'draft_suggested', last_inbound_id,
                         'Respuesta sugerida lista para revisar',
                         'Revisa el destinatario y el texto antes de guardarlo en Gmail o enviarlo.',
                         COALESCE(updated_at, first_seen_at)
                  FROM quotes
                  WHERE first_seen_at IS NOT NULL
                    AND status IN {sql_in(QuoteStatus.REVIEW_PENDING, QuoteStatus.REVIEW,
                                          QuoteStatus.READY_REVIEW, QuoteStatus.DRAFT_CREATED)}""")


def open_db(data_dir: Path) -> sqlite3.Connection:
    data_dir.mkdir(parents=True, exist_ok=True)
    db_path = data_dir / "quotes.sqlite3"
    db = sqlite3.connect(db_path)
    db.row_factory = sqlite3.Row
    try:
        migrate(db, db_path)
        _derive_notifications(db)
        db.commit()
    except BaseException:
        db.close()
        raise
    return db


def add_dashboard_notification(db: sqlite3.Connection, thread_id: str, kind: str,
                               source_key: str, title: str, message: str) -> None:
    now = datetime.now().astimezone().isoformat(timespec="seconds")
    db.execute("""INSERT OR IGNORE INTO dashboard_notifications
                  (thread_id, kind, source_key, title, message, created_at)
                  VALUES (?, ?, ?, ?, ?, ?)""",
               (thread_id, kind, source_key, title, message, now))


def record_event(db: sqlite3.Connection, thread_id: str, kind: str, source: str, ref: str,
                 summary: str, occurred_at: str = "", actor_email: str = "",
                 actor_name: str = "") -> bool:
    """Anota un hecho del caso. Repetirlo (mismo hilo, tipo y referencia) no duplica; True si es nuevo.

    Cada hecho guarda la huella del anterior del mismo caso, así que no se puede editar ni quitar
    uno sin que `verify_chain` lo detecte.
    """
    if not db.in_transaction:
        db.execute("BEGIN IMMEDIATE")  # que dos procesos no encadenen sobre el mismo hecho
    now = datetime.now().astimezone().isoformat(timespec="seconds")
    when = occurred_at or now
    last = db.execute("SELECT hash FROM case_events WHERE thread_id=? ORDER BY id DESC LIMIT 1",
                      (thread_id,)).fetchone()
    prev = last["hash"] if last else ""
    digest = event_hash(prev, thread_id, kind, source, ref, summary, when, actor_email)
    cursor = db.execute("""INSERT OR IGNORE INTO case_events
                  (thread_id, kind, source, ref, summary, occurred_at, recorded_at,
                   actor_email, actor_name, prev_hash, hash)
                  VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                        (thread_id, kind, source, ref, summary, when, now,
                         actor_email, actor_name, prev, digest))
    return cursor.rowcount > 0


def verify_chain(db: sqlite3.Connection, thread_id: str) -> tuple[bool, int]:
    """(íntegro, cantidad de hechos). Falso si algún hecho fue alterado o quitado."""
    prev = ""
    rows = db.execute(
        "SELECT kind, source, ref, summary, occurred_at, actor_email, prev_hash, hash "
        "FROM case_events WHERE thread_id=? ORDER BY id", (thread_id,)).fetchall()
    for row in rows:
        expected = event_hash(prev, thread_id, row["kind"], row["source"], row["ref"],
                              row["summary"], row["occurred_at"], row["actor_email"])
        if row["prev_hash"] != prev or row["hash"] != expected:
            return False, len(rows)
        prev = row["hash"]
    return True, len(rows)


def register_change(db: sqlite3.Connection, thread_id: str, message_id: str, sender_email: str,
                     sender_name: str, subject: str, snippet: str, occurred_at: str,
                     has_code: bool, is_contact: bool, same_company: bool) -> bool:
    """Anota un mensaje recibido después de asignar la T, para que una persona decida si es un cambio."""
    cursor = db.execute(
        """INSERT OR IGNORE INTO case_changes
           (thread_id, message_id, sender_email, sender_name, subject, snippet, occurred_at,
            has_code, is_contact, same_company)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (thread_id, message_id, sender_email, sender_name, subject[:200], snippet, occurred_at,
         int(has_code), int(is_contact), int(same_company)))
    if cursor.rowcount:
        who = sender_name or sender_email
        record_event(db, thread_id, "change_candidate", "gmail", message_id,
                     f"{who} escribió después de asignar la T ({'cita' if has_code else 'NO cita'} el código"
                     f"{'' if is_contact else '; no es el contacto registrado'}): «{snippet}»",
                     occurred_at, sender_email, sender_name)
    return cursor.rowcount > 0


def case_events(db: sqlite3.Connection, thread_id: str) -> list[sqlite3.Row]:
    return db.execute(
        "SELECT kind, source, ref, summary, occurred_at, actor_email, actor_name, hash FROM case_events "
        "WHERE thread_id=? ORDER BY occurred_at, id", (thread_id,)).fetchall()


def save(db: sqlite3.Connection, thread_id: str, last_id: str, status: str, recipient: str,
         analysis: dict, folder_id: str | None = None,
         sent_message_id: str | None = None) -> None:
    now = datetime.now().astimezone().isoformat(timespec="seconds")
    received_at = analysis.get("received_at") or now
    previous = db.execute(
        "SELECT analysis_json FROM quotes WHERE thread_id=?", (thread_id,)
    ).fetchone()
    if previous:
        try:
            old = json.loads(previous["analysis_json"])
        except (TypeError, ValueError):
            old = {}
        if old.get("manual_edit"):
            # la edicion manual prevalece; lo nuevo del modelo queda para revisar
            if analysis.get("activities") != old.get("activities"):
                analysis["pending_model_activities"] = analysis.get("activities")
            analysis["activities"] = old.get("activities", [])
            analysis["manual_edit"] = old["manual_edit"]
    routing = analysis.get("routing") or {}
    contact = (routing.get("original_sender_email") or recipient or "").strip().casefold()
    db.execute("""INSERT INTO quotes (
            thread_id, last_inbound_id, status, recipient, analysis_json,
            folder_id, sent_message_id, first_seen_at, updated_at, contact_email
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(thread_id) DO UPDATE SET
        contact_email=COALESCE(NULLIF(quotes.contact_email, ''), excluded.contact_email),
        last_inbound_id=excluded.last_inbound_id, status=excluded.status,
        recipient=excluded.recipient, analysis_json=excluded.analysis_json,
        folder_id=COALESCE(excluded.folder_id, quotes.folder_id),
        sent_message_id=COALESCE(excluded.sent_message_id, quotes.sent_message_id),
        updated_at=excluded.updated_at""",
        (thread_id, last_id, status, recipient,
         json.dumps(analysis, ensure_ascii=False), folder_id, sent_message_id,
         received_at, now, contact))
    activities = analysis.get("activities")
    if isinstance(activities, list):
        last = latest_table_version(db, thread_id)
        if last is None or json.loads(last["activities_json"]) != activities:
            first = last is None
            add_table_version(
                db, thread_id, activities, "model",
                "Tabla original del modelo" if first else f"El modelo actualizó la tabla ({len(activities)} actividades)")
    db.commit()
