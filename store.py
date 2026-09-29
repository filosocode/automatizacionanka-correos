"""Persistencia SQLite del estado de solicitudes y novedades del dashboard."""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime
from pathlib import Path


def open_db(data_dir: Path) -> sqlite3.Connection:
    data_dir.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(data_dir / "quotes.sqlite3")
    db.row_factory = sqlite3.Row
    db.execute("""CREATE TABLE IF NOT EXISTS quotes (
        thread_id TEXT PRIMARY KEY, last_inbound_id TEXT NOT NULL,
        status TEXT NOT NULL, recipient TEXT NOT NULL,
        analysis_json TEXT NOT NULL, folder_id TEXT, sent_message_id TEXT,
        first_seen_at TEXT, updated_at TEXT, draft_id TEXT,
        draft_body TEXT, sent_at TEXT
    )""")
    existing_columns = {
        row["name"] for row in db.execute("PRAGMA table_info(quotes)").fetchall()
    }
    if "first_seen_at" not in existing_columns:
        db.execute("ALTER TABLE quotes ADD COLUMN first_seen_at TEXT")
    if "updated_at" not in existing_columns:
        db.execute("ALTER TABLE quotes ADD COLUMN updated_at TEXT")
    if "draft_id" not in existing_columns:
        db.execute("ALTER TABLE quotes ADD COLUMN draft_id TEXT")
    if "draft_body" not in existing_columns:
        db.execute("ALTER TABLE quotes ADD COLUMN draft_body TEXT")
    if "sent_at" not in existing_columns:
        db.execute("ALTER TABLE quotes ADD COLUMN sent_at TEXT")
    db.execute("""CREATE TABLE IF NOT EXISTS quote_metadata (
        thread_id TEXT PRIMARY KEY,
        client_type TEXT NOT NULL DEFAULT 'No clasificado',
        sector TEXT NOT NULL DEFAULT 'No clasificado',
        client_name TEXT NOT NULL DEFAULT '',
        notes TEXT NOT NULL DEFAULT '',
        updated_at TEXT NOT NULL,
        FOREIGN KEY(thread_id) REFERENCES quotes(thread_id)
    )""")
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
    db.execute("""INSERT OR IGNORE INTO dashboard_notifications
                  (thread_id, kind, source_key, title, message, created_at)
                  SELECT thread_id, 'request_received', last_inbound_id,
                         'Correo nuevo recibido',
                         'La solicitud ya fue analizada y agregada al dashboard.',
                         first_seen_at
                  FROM quotes
                  WHERE first_seen_at IS NOT NULL AND status <> 'ignored'""")
    db.execute("""INSERT OR IGNORE INTO dashboard_notifications
                  (thread_id, kind, source_key, title, message, created_at)
                  SELECT thread_id, 'draft_suggested', last_inbound_id,
                         'Respuesta sugerida lista para revisar',
                         'Revisa el destinatario y el texto antes de guardarlo en Gmail o enviarlo.',
                         COALESCE(updated_at, first_seen_at)
                  FROM quotes
                  WHERE first_seen_at IS NOT NULL
                    AND status IN ('review_pending', 'review', 'ready_review', 'draft_created')""")
    db.commit()
    return db


def add_dashboard_notification(db: sqlite3.Connection, thread_id: str, kind: str,
                               source_key: str, title: str, message: str) -> None:
    now = datetime.now().astimezone().isoformat(timespec="seconds")
    db.execute("""INSERT OR IGNORE INTO dashboard_notifications
                  (thread_id, kind, source_key, title, message, created_at)
                  VALUES (?, ?, ?, ?, ?, ?)""",
               (thread_id, kind, source_key, title, message, now))


def save(db, thread_id: str, last_id: str, status: str, recipient: str,
         analysis: dict, folder_id: str | None = None,
         sent_message_id: str | None = None) -> None:
    now = datetime.now().astimezone().isoformat(timespec="seconds")
    received_at = analysis.get("received_at") or now
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
