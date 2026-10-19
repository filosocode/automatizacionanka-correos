"""Tabla SQLite de los trabajos Python → Work (T-49)."""

from __future__ import annotations

import sqlite3


def migration_trabajos_work(db: sqlite3.Connection) -> None:
    """Crea `trabajos_work`; no toca `quotes`, `recepcion_*` ni datos previos."""
    db.execute("""CREATE TABLE IF NOT EXISTS trabajos_work (
        job_id TEXT PRIMARY KEY, account TEXT NOT NULL, thread_id TEXT NOT NULL, message_id TEXT NOT NULL,
        job_type TEXT NOT NULL, contract_version TEXT NOT NULL, state TEXT NOT NULL,
        message_ref_json TEXT NOT NULL DEFAULT '{}', ref_sha256 TEXT NOT NULL DEFAULT '',
        sheet_file_id TEXT NOT NULL DEFAULT '', folder_id TEXT NOT NULL DEFAULT '',
        claim_token TEXT NOT NULL DEFAULT '', claim_until REAL, attempts INTEGER NOT NULL DEFAULT 0,
        last_error TEXT NOT NULL DEFAULT '', created_at TEXT NOT NULL, published_at TEXT, conciliated_at TEXT,
        updated_at TEXT NOT NULL,
        UNIQUE(account, thread_id, message_id, job_type, contract_version))""")
    db.execute("CREATE INDEX IF NOT EXISTS idx_trabajos_work_estado ON trabajos_work(state, created_at)")
    db.execute("CREATE INDEX IF NOT EXISTS idx_trabajos_work_mensaje ON trabajos_work(account, thread_id, message_id)")
