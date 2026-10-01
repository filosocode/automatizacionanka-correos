"""Persistencia SQLite del estado de solicitudes y novedades del dashboard."""

from __future__ import annotations

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


# Para cambiar el esquema: agrega una función al final de esta lista. Nunca edites ni
# reordenes las anteriores; las bases ya migradas no las volverán a ejecutar.
MIGRATIONS = (
    _migration_1_quotes,
    _migration_2_quote_metadata,
    _migration_3_notifications,
    _migration_4_case_events,
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
                 summary: str, occurred_at: str = "") -> bool:
    """Anota un hecho del caso. Repetirlo (mismo hilo, tipo y referencia) no duplica; True si es nuevo."""
    now = datetime.now().astimezone().isoformat(timespec="seconds")
    cursor = db.execute("""INSERT OR IGNORE INTO case_events
                  (thread_id, kind, source, ref, summary, occurred_at, recorded_at)
                  VALUES (?, ?, ?, ?, ?, ?, ?)""",
                        (thread_id, kind, source, ref, summary, occurred_at or now, now))
    return cursor.rowcount > 0


def case_events(db: sqlite3.Connection, thread_id: str) -> list[sqlite3.Row]:
    return db.execute(
        "SELECT kind, source, summary, occurred_at FROM case_events "
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
    db.execute("""INSERT INTO quotes (
            thread_id, last_inbound_id, status, recipient, analysis_json,
            folder_id, sent_message_id, first_seen_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(thread_id) DO UPDATE SET
        last_inbound_id=excluded.last_inbound_id, status=excluded.status,
        recipient=excluded.recipient, analysis_json=excluded.analysis_json,
        folder_id=COALESCE(excluded.folder_id, quotes.folder_id),
        sent_message_id=COALESCE(excluded.sent_message_id, quotes.sent_message_id),
        updated_at=excluded.updated_at""",
        (thread_id, last_id, status, recipient,
         json.dumps(analysis, ensure_ascii=False), folder_id, sent_message_id,
         received_at, now))
    db.commit()
