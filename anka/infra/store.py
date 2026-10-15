"""Persistencia SQLite del estado de solicitudes y novedades del dashboard."""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import time
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Any, cast
from datetime import datetime
from pathlib import Path

from anka.dominio.analisis import Analisis
from anka.dominio.caso import VERSION_DEL_RESUMEN, load_analysis, resumen_de_analisis
from anka.dominio.status import ESTADOS_CON_RESPUESTA_SUGERIDA, QuoteStatus, sql_in
from anka.infra import settings


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
    """Última versión guardada de la tabla de actividades del caso, o `None`."""
    return db.execute("SELECT * FROM table_versions WHERE thread_id=? ORDER BY version DESC LIMIT 1",
                      (thread_id,)).fetchone()


def _migration_8_case_messages(db: sqlite3.Connection) -> None:
    """Texto de los correos del hilo (cliente y Anka) para leerlos en el caso sin ir a Gmail."""
    db.execute("""CREATE TABLE IF NOT EXISTS case_messages (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        thread_id TEXT NOT NULL,
        message_id TEXT NOT NULL,
        direction TEXT NOT NULL,
        sender TEXT NOT NULL DEFAULT '',
        subject TEXT NOT NULL DEFAULT '',
        body TEXT NOT NULL DEFAULT '',
        occurred_at TEXT NOT NULL,
        UNIQUE(thread_id, message_id)
    )""")


def add_message(db: sqlite3.Connection, thread_id: str, message_id: str, direction: str, sender: str,
                subject: str, body: str, occurred_at: str) -> None:
    """Guarda un mensaje del hilo (entrante o saliente); no duplica uno ya guardado."""
    db.execute(
        """INSERT OR IGNORE INTO case_messages
           (thread_id, message_id, direction, sender, subject, body, occurred_at)
           VALUES (?, ?, ?, ?, ?, ?, ?)""",
        (thread_id, message_id, direction, sender, subject[:200], body[:8000], occurred_at))


def _migration_9_case_followup(db: sqlite3.Connection) -> None:
    """Seguimiento cuando el cliente tarda: recordatorios automáticos (opt-in), pausa y «ya hablé»."""
    db.execute("""CREATE TABLE IF NOT EXISTS case_followup (
        thread_id TEXT PRIMARY KEY,
        auto_enabled INTEGER NOT NULL DEFAULT 0,
        paused INTEGER NOT NULL DEFAULT 0,
        contacted_at TEXT,
        updated_at TEXT NOT NULL
    )""")


def _migration_10_sheet(db: sqlite3.Connection) -> None:
    """La hoja de Google Sheets de cada caso: su id, su última modificación en Drive (para notar ediciones hechas allí),
    cuándo se sincronizó y el último error."""
    existing = {row["name"] for row in db.execute("PRAGMA table_info(quotes)").fetchall()}
    for column in ("sheet_id", "sheet_modified", "sheet_synced_at", "sheet_error"):
        if column not in existing:
            db.execute(f"ALTER TABLE quotes ADD COLUMN {column} TEXT")


def _migration_11_one_case_per_message(db: sqlite3.Connection) -> None:
    """Un correo pertenece a un solo caso: si ya se confirmó en uno, el mismo aviso pendiente en los demás se cierra.

    Antes cada correo creaba un aviso por cada caso del mismo remitente y confirmar uno dejaba los otros pendientes.
    """
    now = datetime.now().astimezone().isoformat(timespec="seconds")
    db.execute("""UPDATE dashboard_notifications SET read_at=?
                  WHERE kind='possible_update' AND read_at IS NULL AND EXISTS (
                      SELECT 1 FROM case_links p WHERE p.state='pending'
                        AND p.thread_id=dashboard_notifications.thread_id
                        AND p.message_id=dashboard_notifications.source_key
                        AND EXISTS (SELECT 1 FROM case_links l WHERE l.message_id=p.message_id AND l.state='linked'
                                    AND l.thread_id<>p.thread_id))""", (now,))
    db.execute("""UPDATE case_links SET state='assigned_elsewhere', decided_at=?
                  WHERE state='pending' AND EXISTS (
                      SELECT 1 FROM case_links l WHERE l.message_id=case_links.message_id AND l.state='linked'
                        AND l.thread_id<>case_links.thread_id)""", (now,))


def _migration_12_mail_operations(db: sqlite3.Connection) -> None:
    db.execute('''CREATE TABLE IF NOT EXISTS mail_operations (
        operation_key TEXT PRIMARY KEY, thread_id TEXT NOT NULL, recipient TEXT NOT NULL,
        message_id TEXT NOT NULL UNIQUE, state TEXT NOT NULL CHECK(state IN ('pending', 'sent')),
        gmail_id TEXT NOT NULL, created_at TEXT NOT NULL, error TEXT NOT NULL)''')


def _migration_13_project_dismissals(db: sqlite3.Connection) -> None:
    """Pares de hilos que una persona dijo que NO son el mismo proyecto (siempre con el id menor primero)."""
    db.execute('''CREATE TABLE IF NOT EXISTS project_dismissals (
        thread_id TEXT NOT NULL, other_thread_id TEXT NOT NULL, decided_at TEXT NOT NULL,
        PRIMARY KEY (thread_id, other_thread_id), CHECK (thread_id < other_thread_id))''')


def _migration_14_analysis_failures(db: sqlite3.Connection) -> None:
    db.execute('''CREATE TABLE IF NOT EXISTS analysis_failures (
        thread_id TEXT PRIMARY KEY, failures INTEGER NOT NULL, last_error TEXT NOT NULL,
        first_at TEXT NOT NULL, last_at TEXT NOT NULL, next_retry_at TEXT NOT NULL)''')


def _migration_15_work_rejections(db: sqlite3.Connection) -> None:
    """Los análisis de Work que no se pudieron leer: solo motivo, campos y fecha, nunca contenido del cliente (T-28)."""
    db.execute('''CREATE TABLE IF NOT EXISTS work_rejections (
        draft_id TEXT NOT NULL, message_id TEXT NOT NULL, thread_id TEXT NOT NULL, day TEXT NOT NULL,
        at TEXT NOT NULL, cause TEXT NOT NULL, fields TEXT NOT NULL, reason TEXT NOT NULL, dismissed_at TEXT,
        PRIMARY KEY (draft_id, message_id))''')
    db.execute("CREATE INDEX IF NOT EXISTS idx_work_rejections_day ON work_rejections(day)")


def _migration_16_orphan_metadata(db: sqlite3.Connection) -> None:
    """Quita de `quote_metadata` las filas sin caso, para activar las claves foráneas (U-42); queda la copia previa."""
    db.execute("DELETE FROM quote_metadata WHERE thread_id NOT IN (SELECT thread_id FROM quotes)")


def _migration_17_indices(db: sqlite3.Connection) -> None:
    """Índices de lo que más se consulta: la lista de casos, el seguimiento y el marcador de cambios (U-48).

    Solo aceleran las lecturas; no cambian ningún dato. `IF NOT EXISTS` por si otro proceso llegó antes."""
    for sentencia in (
        # la lista ordena por «lo más reciente»; la expresión es la misma de `RepositorioDeCasos._filas` y del marcador
        "CREATE INDEX IF NOT EXISTS idx_quotes_recientes ON quotes(COALESCE(updated_at, first_seen_at, '') DESC, "
        "thread_id DESC)",
        "CREATE INDEX IF NOT EXISTS idx_quotes_estado ON quotes(status, updated_at)",
        "CREATE INDEX IF NOT EXISTS idx_quotes_codigo ON quotes(quote_code)",
        # «último correo recibido» y «recordatorios enviados» de todos los casos; el MAX del marcador
        "CREATE INDEX IF NOT EXISTS idx_case_events_tipo ON case_events(kind, occurred_at)",
        "CREATE INDEX IF NOT EXISTS idx_case_events_registrado ON case_events(recorded_at)",
        "CREATE INDEX IF NOT EXISTS idx_case_changes_estado ON case_changes(state, occurred_at)",
        "CREATE INDEX IF NOT EXISTS idx_case_links_estado ON case_links(state, occurred_at)",
        "CREATE INDEX IF NOT EXISTS idx_case_links_mensaje ON case_links(message_id)",
        "CREATE INDEX IF NOT EXISTS idx_notifications_sin_leer ON dashboard_notifications(read_at, kind, thread_id)",
    ):
        db.execute(sentencia)


def resumenes_pendientes(db: sqlite3.Connection) -> dict[str, tuple[str, dict[str, Any]]]:
    """Los casos sin resumen para las listas (o con uno de una versión anterior): `{hilo: (análisis, resumen)}` (U-49).

    Solo lee: devuelve también el análisis con que se calculó, para guardar el resumen solo si no cambió."""
    pendientes = {}
    for fila in db.execute("SELECT thread_id, analysis_json FROM quotes "
                           "WHERE resumen_json IS NULL OR resumen_v IS NOT ?", (VERSION_DEL_RESUMEN,)).fetchall():
        texto = fila["analysis_json"]
        pendientes[fila["thread_id"]] = (texto, resumen_de_analisis(load_analysis(texto)))
    return pendientes


def guardar_resumenes(db: sqlite3.Connection, pendientes: dict[str, tuple[str, dict[str, Any]]]) -> int:
    """Guarda los resúmenes calculados; un caso cuyo análisis cambió mientras tanto se deja para la próxima (U-49).

    Quien llama ya tiene el candado de escritura. Devuelve cuántos guardó."""
    guardados = 0
    for hilo, (texto, resumen) in pendientes.items():
        guardados += db.execute(
            "UPDATE quotes SET resumen_json=?, resumen_v=? WHERE thread_id=? AND analysis_json=?",
            (json.dumps(resumen, ensure_ascii=False, separators=(",", ":")), VERSION_DEL_RESUMEN, hilo, texto),
        ).rowcount
    return guardados


def _migration_18_resumen_de_casos(db: sqlite3.Connection) -> None:
    """Resumen de cada caso para las listas, sin tocar su análisis (U-49).

    Dos columnas nuevas (`resumen_json`, `resumen_v`), un disparador que borra el resumen cuando cambia
    `analysis_json` (así nunca queda uno viejo, lo escriba quien lo escriba) y el relleno de los casos que ya existen.
    Idempotente: las columnas y el disparador solo se crean si faltan, y el relleno solo toca casos sin resumen."""
    existentes = {fila["name"] for fila in db.execute("PRAGMA table_info(quotes)").fetchall()}
    if "resumen_json" not in existentes:
        db.execute("ALTER TABLE quotes ADD COLUMN resumen_json TEXT")
    if "resumen_v" not in existentes:
        db.execute("ALTER TABLE quotes ADD COLUMN resumen_v INTEGER")
    db.execute("""CREATE TRIGGER IF NOT EXISTS trg_quotes_resumen_vencido
                  AFTER UPDATE OF analysis_json ON quotes
                  WHEN NEW.analysis_json IS NOT OLD.analysis_json
                  BEGIN
                      UPDATE quotes SET resumen_json = NULL, resumen_v = NULL WHERE thread_id = NEW.thread_id;
                  END""")
    guardar_resumenes(db, resumenes_pendientes(db))


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
    _migration_8_case_messages,
    _migration_9_case_followup,
    _migration_10_sheet,
    _migration_11_one_case_per_message,
    _migration_12_mail_operations,
    _migration_13_project_dismissals,
    _migration_14_analysis_failures,
    _migration_15_work_rejections,
    _migration_16_orphan_metadata,
    _migration_17_indices,
    _migration_18_resumen_de_casos,
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
            temporal = backup.with_name(f"{backup.name}.{os.getpid()}.tmp")
            target = sqlite3.connect(temporal)
            try:
                db.backup(target)
            finally:
                target.close()
            try:
                if backup.exists():          # otro proceso llegó antes
                    temporal.unlink(missing_ok=True)
                else:
                    os.replace(temporal, backup)
            except OSError:
                temporal.unlink(missing_ok=True)
    for version in range(current + 1, SCHEMA_VERSION + 1):
        db.execute("BEGIN IMMEDIATE")      # un solo proceso migra a la vez (U-42)
        try:
            if db.execute("PRAGMA user_version").fetchone()[0] >= version:
                db.rollback()              # otro proceso ya hizo este paso mientras esperábamos
                continue
            MIGRATIONS[version - 1](db)
            db.execute(f"PRAGMA user_version = {version}")
        except BaseException:
            db.rollback()
            raise
        db.commit()


# Novedades que se derivan de los casos guardados: (tipo, título, mensaje, fecha, condición sobre el caso).
_DERIVED_NOTIFICATIONS = (
    ("request_received", "Correo nuevo recibido",
     "La solicitud ya fue analizada y agregada al dashboard.",
     "first_seen_at", f"status <> '{QuoteStatus.IGNORED}'"),
    ("draft_suggested", "Respuesta sugerida lista para revisar",
     "Revisa el destinatario y el texto antes de guardarlo en Gmail o enviarlo.",
     "COALESCE(updated_at, first_seen_at)",
     "status IN " + sql_in(*ESTADOS_CON_RESPUESTA_SUGERIDA)),
)


def _derive_notifications(db: sqlite3.Connection) -> None:
    """Crea las novedades que corresponden a solicitudes ya guardadas.

    `save()` no genera novedades; esta derivación es lo que las produce (UNIQUE evita duplicados).
    Corre en cada apertura, así que primero comprueba con una lectura si falta alguna y solo escribe
    (y toma el candado de escritura) cuando hace falta.
    """
    for kind, title, message, created, condition in _DERIVED_NOTIFICATIONS:
        base = (f"FROM quotes q WHERE first_seen_at IS NOT NULL AND last_inbound_id IS NOT NULL AND {condition}")
        missing = db.execute(
            f"""SELECT 1 {base} AND NOT EXISTS (
                    SELECT 1 FROM dashboard_notifications n
                    WHERE n.thread_id = q.thread_id AND n.kind = ? AND n.source_key = q.last_inbound_id)
                LIMIT 1""", (kind,)).fetchone()
        if missing:
            db.execute(f"""INSERT OR IGNORE INTO dashboard_notifications
                          (thread_id, kind, source_key, title, message, created_at)
                          SELECT thread_id, ?, last_inbound_id, ?, ?, {created} {base}
                          ORDER BY q.rowid""",             # el orden de siempre, aunque un índice cambie el recorrido
                       (kind, title, message))


_request_db: ContextVar[sqlite3.Connection | None] = ContextVar("anka_request_db", default=None)


class _RequestConnection:
    """La conexión de una petición vista desde cada función que la «abre» y la «cierra».

    Cada función de los servicios hace `db = connect()` ... `db.close()`. Dentro de una petición todas comparten la
    misma conexión, así que `close()` no la cierra: descarta lo que quedó sin confirmar, igual que haría cerrar una
    conexión propia, para que una función que falla a medias no deje cambios pendientes a la siguiente.
    """

    def __init__(self, db: sqlite3.Connection) -> None:
        self._db = db

    def __getattr__(self, name: str) -> Any:
        return getattr(self._db, name)

    def close(self) -> None:
        if self._db.in_transaction:
            self._db.rollback()


@contextmanager
def request_scope() -> Iterator[None]:
    """Una sola conexión (y una sola comprobación de esquema y novedades) para toda una petición web.

    Sin esto cada consulta abre la suya: una pantalla de caso llegaba a abrir ocho. Fuera de una petición
    (comandos, el piloto, el puente) `connect()` sigue abriendo una conexión nueva cada vez. Se pueden anidar.
    """
    if _request_db.get() is not None:
        yield
        return
    db = open_db(settings.DATA_DIR)
    token = _request_db.set(db)
    try:
        yield
    finally:
        _request_db.reset(token)
        db.close()


def connect() -> sqlite3.Connection:
    """Abre la base de la carpeta de datos vigente (la única forma de abrirla desde los servicios)."""
    shared = _request_db.get()
    if shared is not None:
        return cast(sqlite3.Connection, _RequestConnection(shared))
    return open_db(settings.DATA_DIR)


BUSY_TIMEOUT_MS = 15000


def begin_immediate(db: sqlite3.Connection) -> None:
    """Candado de escritura antes de leer lo que se va a modificar (U-42); si ya hay transacción, ya lo tiene."""
    if not db.in_transaction:
        db.execute("BEGIN IMMEDIATE")


def _activar_wal(db: sqlite3.Connection) -> None:
    """Pasa la base a WAL. Si otro proceso la abre a la vez el cambio de modo no espera solo: se reintenta."""
    limite = time.monotonic() + BUSY_TIMEOUT_MS / 1000
    while db.execute("PRAGMA journal_mode").fetchone()[0].lower() != "wal":
        try:
            db.execute("PRAGMA journal_mode=WAL")
        except sqlite3.OperationalError:
            if time.monotonic() > limite:
                raise
            time.sleep(0.05)


def open_db(data_dir: Path) -> sqlite3.Connection:
    """Abre (y migra si hace falta) la base SQLite de una carpeta de datos, en WAL y con espera de 15 s (U-42)."""
    data_dir.mkdir(parents=True, exist_ok=True)
    db_path = data_dir / "quotes.sqlite3"
    db = sqlite3.connect(db_path, timeout=BUSY_TIMEOUT_MS / 1000)
    db.row_factory = sqlite3.Row
    try:
        db.execute(f"PRAGMA busy_timeout={BUSY_TIMEOUT_MS}")
        db.execute("PRAGMA foreign_keys=ON")
        _activar_wal(db)
        migrate(db, db_path)
        _derive_notifications(db)
        db.commit()
    except BaseException:
        db.close()
        raise
    return db


def add_dashboard_notification(db: sqlite3.Connection, thread_id: str, kind: str,
                               source_key: str, title: str, message: str, leida: bool = False) -> None:
    """Crea una novedad para el dashboard; la misma `source_key` no se repite.

    `leida=True` es para lo que la propia persona hizo: queda en la tabla (constancia) pero no suma al contador (U-25).
    """
    now = datetime.now().astimezone().isoformat(timespec="seconds")
    db.execute("""INSERT OR IGNORE INTO dashboard_notifications
                  (thread_id, kind, source_key, title, message, created_at, read_at)
                  VALUES (?, ?, ?, ?, ?, ?, ?)""",
               (thread_id, kind, source_key, title, message, now, now if leida else None))


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
    """Seguimiento del caso en orden cronológico."""
    return db.execute(
        "SELECT kind, source, ref, summary, occurred_at, actor_email, actor_name, hash FROM case_events "
        "WHERE thread_id=? ORDER BY occurred_at, id", (thread_id,)).fetchall()


def save(db: sqlite3.Connection, thread_id: str, last_id: str, status: str, recipient: str,
         analysis: Analisis, folder_id: str | None = None,
         sent_message_id: str | None = None) -> None:
    """Crea o actualiza el caso (estado, destinatario, análisis) conservando lo que ya tenía, como la primera vista."""
    now = datetime.now().astimezone().isoformat(timespec="seconds")
    received_at = analysis.get("received_at") or now
    begin_immediate(db)      # leer y escribir bajo el mismo candado: el puente no pisa al dashboard (U-42)
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
                analysis["pending_model_activities"] = analysis.get("activities") or []
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
                "Tabla original del modelo" if first
                else f"El modelo actualizó la tabla ({len(activities)} actividades)")
    db.commit()
