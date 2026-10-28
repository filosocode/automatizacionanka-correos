"""Tablas SQLite para el cursor, la cola y la auditoría del trigger Gmail (U-81)."""

from __future__ import annotations

import sqlite3


def migration_recepcion_gmail(db: sqlite3.Connection) -> None:
    """Crea el registro durable de recepción; no toca `quotes` ni datos previos."""
    db.execute("""CREATE TABLE IF NOT EXISTS recepcion_control (
        account TEXT PRIMARY KEY, history_id TEXT NOT NULL, history_page_token TEXT,
        last_success_at TEXT, last_attempt_at TEXT,
        last_error_kind TEXT NOT NULL DEFAULT '', last_error_type TEXT NOT NULL DEFAULT '',
        consecutive_errors INTEGER NOT NULL DEFAULT 0, lease_token TEXT, lease_until REAL,
        updated_at TEXT NOT NULL)""")
    db.execute("""CREATE TABLE IF NOT EXISTS recepcion_mensajes (
        account TEXT NOT NULL, thread_id TEXT NOT NULL, message_id TEXT NOT NULL,
        state TEXT NOT NULL, disposition TEXT NOT NULL DEFAULT '', sender TEXT NOT NULL DEFAULT '',
        recipients_json TEXT NOT NULL DEFAULT '[]', subject TEXT NOT NULL DEFAULT '',
        header_date TEXT NOT NULL DEFAULT '', internal_date TEXT NOT NULL DEFAULT '', snippet TEXT NOT NULL DEFAULT '',
        attachments_json TEXT NOT NULL DEFAULT '[]', thread_context_json TEXT NOT NULL DEFAULT '{}',
        evidence_json TEXT NOT NULL DEFAULT '{}', error_kind TEXT NOT NULL DEFAULT '',
        error_type TEXT NOT NULL DEFAULT '', attempts INTEGER NOT NULL DEFAULT 0, retry_at REAL,
        detected_at TEXT NOT NULL, processed_at TEXT, updated_at TEXT NOT NULL,
        PRIMARY KEY(account, thread_id, message_id))""")
    db.execute("CREATE INDEX IF NOT EXISTS idx_recepcion_cola "
               "ON recepcion_mensajes(account,state,retry_at,internal_date)")
    db.execute("""CREATE TABLE IF NOT EXISTS recepcion_eventos (
        id INTEGER PRIMARY KEY AUTOINCREMENT, account TEXT NOT NULL,
        thread_id TEXT NOT NULL DEFAULT '', message_id TEXT NOT NULL DEFAULT '',
        event_kind TEXT NOT NULL, occurred_at TEXT NOT NULL, detail_json TEXT NOT NULL DEFAULT '{}')""")
    db.execute("CREATE INDEX IF NOT EXISTS idx_recepcion_eventos "
               "ON recepcion_eventos(account,thread_id,message_id,id)")


def migration_recepcion_segura(db: sqlite3.Connection) -> None:
    """Metadatos de cobertura para recuperación acotada del historyId (U-81)."""
    columns = {row["name"] for row in db.execute("PRAGMA table_info(recepcion_control)")}
    for name, declaration in (
        ("initial_checkpoint_at", "TEXT"),
        ("recovery_state", "TEXT NOT NULL DEFAULT 'normal'"),
        ("recovery_gap_start_at", "TEXT"),
        ("recovery_gap_end_at", "TEXT"),
        ("history_batch_count", "INTEGER NOT NULL DEFAULT 0"),
        ("history_sync_confirmed", "INTEGER NOT NULL DEFAULT 0"),
    ):
        if name not in columns:
            db.execute(f"ALTER TABLE recepcion_control ADD COLUMN {name} {declaration}")


def migration_recepcion_auditoria(db: sqlite3.Connection) -> None:
    """Repara la tabla de auditoría si una base ya marcada como v22 quedó incompleta (U-81)."""
    db.execute("""CREATE TABLE IF NOT EXISTS recepcion_eventos (
        id INTEGER PRIMARY KEY AUTOINCREMENT, account TEXT NOT NULL,
        thread_id TEXT NOT NULL DEFAULT '', message_id TEXT NOT NULL DEFAULT '',
        event_kind TEXT NOT NULL, occurred_at TEXT NOT NULL, detail_json TEXT NOT NULL DEFAULT '{}')""")
    db.execute("CREATE INDEX IF NOT EXISTS idx_recepcion_eventos "
               "ON recepcion_eventos(account,thread_id,message_id,id)")
