"""Lectura de casos y actualizaciones de su fila centralizadas para los servicios.

Una clase porque tiene estado (cómo abrir la base) y un conjunto de operaciones que viajan juntas. Quien la usa
pide casos y datos del caso («obtener», «listar», «eventos»…) sin abrir ni cerrar conexiones.
Los servicios aún gestionan transacciones y tablas auxiliares; sus cambios a la fila del caso pasan por
`actualizar_caso`, que valida campos y parametriza valores. `infra.store` conserva migraciones y altas iniciales.

Por defecto abre la base con `store.connect()`, así que dentro de una petición web comparte su conexión
(`request_scope`); para probar basta pasarle otra forma de abrirla. Las funciones de `quotes.py` (`get_quote`,
`all_quotes`…) son una fachada sobre una instancia por defecto, para no tocar a quienes ya las usan.
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Callable, Iterator, Mapping
from contextlib import contextmanager
from datetime import datetime
from typing import NamedTuple, Any, cast
from uuid import uuid4

from anka.dominio.analisis import Analisis
from anka.dominio.preguntas import era_un_acuse
from anka.dominio.caso import (
    VERSION_DEL_RESUMEN, AnalisisPerezoso, Caso, EvaluacionPerezosa, load_analysis, model_proposal, service_names,
    status_label, unanalyzed_reply, waiting_since,
)
from anka.dominio.reception import reception_assessment
from anka.dominio.registros import Cambio, Evento, Vinculo
from anka.dominio.status import QuoteStatus
from anka.infra.logging_setup import get_logger
from anka.infra.store import (
    BUSY_TIMEOUT_MS, begin_immediate, case_events, connect, guardar_resumenes, record_event, resumenes_pendientes,
    verify_chain,
)

log = get_logger(__name__)


CASE_FIELDS = frozenset({
    'recipient', 'analysis_json', 'updated_at', 'status', 'draft_id', 'draft_body',
    'sent_message_id', 'sent_at', 'quote_code', 'code_stage', 'code_assigned_at',
    'last_inbound_id', 'folder_id', 'sheet_id', 'sheet_modified', 'sheet_synced_at', 'sheet_error',
})


def actualizar_caso(db: sqlite3.Connection, thread_id: str, **campos: Any) -> None:
    """Actualiza campos permitidos de un caso dentro de la transacción del caso de uso."""
    if not campos or not campos.keys() <= CASE_FIELDS:
        raise ValueError('Campos de caso no permitidos.')
    assignments = ', '.join(f'{name}=?' for name in campos)
    db.execute(f'UPDATE quotes SET {assignments} WHERE thread_id=?', (*campos.values(), thread_id))


# Las columnas de `quotes` que usa una lista: todas menos `analysis_json` (U-49).
COLUMNAS_DE_LISTA = (
    "thread_id", "last_inbound_id", "status", "recipient", "folder_id", "sent_message_id", "first_seen_at",
    "updated_at", "draft_id", "draft_body", "sent_at", "quote_code", "code_stage", "code_assigned_at",
    "contact_email", "sheet_id", "sheet_modified", "sheet_synced_at", "sheet_error", "resumen_json", "resumen_v",
)


def _orden_de_lista(fila: sqlite3.Row) -> tuple[str, str]:
    """La clave del orden «más reciente primero»: la fecha de actualización (o de primera vista, o vacía) y el hilo."""
    cuando = fila["updated_at"]
    if cuando is None:
        cuando = fila["first_seen_at"]
    return ("" if cuando is None else cuando, fila["thread_id"])


class _CaseRelations(NamedTuple):
    """Lo que se guarda aparte de cada caso y que `Caso` necesita (todo indexado por hilo)."""

    latest_reply: dict[str, tuple[str, str]]
    followups: dict[str, dict]
    reminders: dict[str, list[str]]
    changes: dict[str, list[Cambio]]
    pending_links: dict[str, list[Vinculo]]


def _build_caso(row: sqlite3.Row, rel: _CaseRelations, resumen: dict | None = None,
                cargar: Callable[[str], Analisis] | None = None) -> Caso:
    """Arma el `Caso` de una fila con lo que se guardó aparte.

    Con `resumen` (y `cargar`) arma el de una lista (U-49): el análisis es perezoso y lo derivado sale del resumen, sin
    leer el análisis completo; sin `resumen` lee el análisis completo, como siempre."""
    thread = row["thread_id"]
    if resumen is None:
        analysis = load_analysis(row["analysis_json"])
        claves: Mapping[str, Any] = analysis
        assessment: Mapping[str, Any] = reception_assessment(analysis) if analysis else {}
        services, proposal = service_names(analysis), model_proposal(analysis)
    else:
        assert cargar is not None
        claves = resumen["claves"]
        analysis = cast(Analisis, AnalisisPerezoso(claves, resumen["vacio"], lambda: cargar(thread)))
        assessment = EvaluacionPerezosa(lambda: reception_assessment(analysis) if analysis else {})
        services, proposal = list(resumen["servicios"]), list(resumen["propuesta"])
    routing = claves.get("routing", {})
    client_email = routing.get("original_sender_email") or row["recipient"]
    followup = rel.followups.get(thread, {})
    latest = rel.latest_reply.get(thread)
    return Caso(
        thread_id=thread,
        status=row["status"],
        status_label=status_label(row["status"], assessment),
        recipient=row["recipient"],
        client_email=client_email,
        client_name=row["client_name"] or routing.get("original_sender") or client_email,
        client_name_saved=row["client_name"] or "",
        client_type=row["client_type"] or "No clasificado",
        sector=row["sector"] or "No clasificado",
        notes=row["notes"] or "",
        analysis=analysis,
        reception_assessment=assessment,
        services=services,
        first_seen_at=row["first_seen_at"] or claves.get("received_at") or "",
        updated_at=row["updated_at"] or "",
        folder_id=row["folder_id"],
        draft_id=row["draft_id"],
        draft_body=row["draft_body"] or "",
        sent_at=row["sent_at"] or "",
        reply_pending=unanalyzed_reply(
            latest, row["last_inbound_id"], cast(Analisis, claves), row["first_seen_at"] or "", row["status"]),
        proposal=proposal,
        links=rel.pending_links.get(thread, []),
        changes=rel.changes.get(thread, []),
        waiting_since=waiting_since(row["status"], row["sent_at"] or "", latest, sin_preguntas=era_un_acuse(claves)),
        followup={
            "auto_enabled": bool(followup.get("auto_enabled")),
            "paused": bool(followup.get("paused")),
            "contacted_at": followup.get("contacted_at") or "",
        },
        reminders=[ref for ref in rel.reminders.get(thread, []) if ref.startswith((row["sent_at"] or "") + "|")],
        quote_code=row["quote_code"] or "",
        code_stage=row["code_stage"] or "",
        code_assigned_at=row["code_assigned_at"] or "",
        contact_email=row["contact_email"] or "",
        sheet_id=row["sheet_id"] or "",
        sheet_modified=row["sheet_modified"] or "",
        sheet_synced_at=row["sheet_synced_at"] or "",
        sheet_error=row["sheet_error"] or "",
    )


# Lo que se borra al eliminar un caso (T-42). Se conservan como evidencia el historial, las solicitudes de cambio
# (alimentan el acta) y el registro de lo enviado.
TABLAS_DEL_CASO = ("dashboard_notifications", "quote_metadata", "case_messages", "case_followup", "table_versions",
                   "analysis_failures", "quotes")
TABLAS_CON_DOS_HILOS = ("case_links", "project_dismissals")
TABLAS_QUE_SE_CONSERVAN = ("case_events", "case_changes", "mail_operations", "work_rejections")


class RepositorioDeCasos:
    """Lee y escribe casos y sus datos propios (conversación, historial, personas, datos comerciales)."""

    def __init__(self, abrir: Callable[[], sqlite3.Connection] = connect) -> None:
        self._abrir = abrir

    @contextmanager
    def _conexion(self) -> Iterator[sqlite3.Connection]:
        db = self._abrir()
        try:
            yield db
        finally:
            db.close()

    # ------------------------------------------------------------------ casos
    def listar(self, incluir_ignorados: bool = False, completo: bool = False) -> list[Caso]:
        """Todos los casos con sus cambios, vínculos, seguimiento y espera, el más reciente primero.

        Sin `completo`, el análisis de cada caso es perezoso (U-49): la lista sale del resumen guardado y el análisis
        completo solo se lee del caso que lo pida. Con `completo=True` se lee el de todos (p. ej. para exportar)."""
        return self._armar(incluir_ignorados, None, completo)

    def obtener(self, thread_id: str) -> Caso | None:
        """Un caso por su hilo, o `None` si no existe (solo sus filas; lee su análisis completo)."""
        return next(iter(self._armar(True, thread_id, True)), None)

    def _armar(self, incluir_ignorados: bool, thread_id: str | None, completo: bool) -> list[Caso]:
        with self._conexion() as db:
            rows = self._filas(db, incluir_ignorados, thread_id, completo)
            relations = self._relaciones(db, thread_id)
            resumenes = {} if completo else self._resumenes(db, rows)
        if completo:
            return [_build_caso(row, relations) for row in rows]
        return [_build_caso(row, relations, resumenes[row["thread_id"]], self._analisis_de) for row in rows]

    def _analisis_de(self, thread_id: str) -> Analisis:
        """El análisis completo de un caso (lo pide `AnalisisPerezoso` solo cuando alguien lo necesita)."""
        with self._conexion() as db:
            fila = db.execute("SELECT analysis_json FROM quotes WHERE thread_id=?", (thread_id,)).fetchone()
        return load_analysis(fila["analysis_json"]) if fila else {}

    @staticmethod
    def _resumenes(db: sqlite3.Connection, rows: list[sqlite3.Row]) -> dict[str, dict]:
        """El resumen de cada fila; los que faltan o son de otra versión se calculan y se guardan (U-49).

        Guardar es un favor para la próxima lista: si la base está ocupada se sigue con lo calculado, sin esperar."""
        resumenes: dict[str, dict] = {}
        faltan = []
        for row in rows:
            if row["resumen_json"] is not None and row["resumen_v"] == VERSION_DEL_RESUMEN:
                resumenes[row["thread_id"]] = json.loads(row["resumen_json"])
            else:
                faltan.append(row["thread_id"])
        if faltan:
            pendientes = resumenes_pendientes(db)
            for hilo in faltan:       # si el caso desapareció mientras tanto, se muestra vacío
                resumenes[hilo] = pendientes[hilo][1] if hilo in pendientes else {
                    "v": VERSION_DEL_RESUMEN, "vacio": True, "claves": {}, "servicios": [], "propuesta": []}
            db.execute("PRAGMA busy_timeout = 0")
            try:
                begin_immediate(db)
                guardar_resumenes(db, pendientes)
                db.commit()
            except sqlite3.OperationalError:
                db.rollback()
            finally:
                db.execute(f"PRAGMA busy_timeout = {BUSY_TIMEOUT_MS}")
        return resumenes

    @staticmethod
    def _filas(db: sqlite3.Connection, incluir_ignorados: bool, thread_id: str | None,
               completo: bool = True) -> list[sqlite3.Row]:
        """Filas de los casos con datos comerciales, el más reciente primero (sin `analysis_json` salvo `completo`)."""
        conditions = [] if incluir_ignorados else [f"q.status <> '{QuoteStatus.IGNORED}'"]
        if thread_id is not None:
            conditions.append("q.thread_id = ?")
        where = ("WHERE " + " AND ".join(conditions)) if conditions else ""
        columnas = "q.*" if completo else ", ".join(f"q.{c}" for c in COLUMNAS_DE_LISTA)
        filas = db.execute(f"""SELECT {columnas}, m.client_type, m.sector, m.client_name, m.notes
                               FROM quotes q
                               LEFT JOIN quote_metadata m ON m.thread_id = q.thread_id
                               {where}""", (thread_id,) if thread_id is not None else ()).fetchall()
        # El orden de siempre (`COALESCE(updated_at, first_seen_at, '') DESC, thread_id DESC`), hecho aquí: recorrer la
        # tabla en su orden y ordenar en memoria cuesta menos que saltar por el índice entre filas de varios KB.
        filas.sort(key=_orden_de_lista, reverse=True)
        return filas

    @staticmethod
    def _relaciones(db: sqlite3.Connection, thread_id: str | None) -> _CaseRelations:
        """Respuestas, seguimiento, recordatorios, cambios y vínculos de todos los casos (o de uno solo)."""
        scope = (thread_id,) if thread_id is not None else ()
        only = " AND thread_id = ?" if thread_id is not None else ""
        only_where = " WHERE thread_id = ?" if thread_id is not None else ""
        latest_reply = {r["thread_id"]: (r["occurred_at"], r["ref"]) for r in db.execute(
            "SELECT thread_id, ref, occurred_at FROM case_events "
            f"WHERE kind='received'{only} ORDER BY occurred_at, id", scope)}
        followups = {r["thread_id"]: dict(r) for r in db.execute(f"SELECT * FROM case_followup{only_where}", scope)}
        reminders: dict[str, list[str]] = {}
        for r in db.execute(
                f"SELECT thread_id, ref FROM case_events WHERE kind='reminder_sent'{only} ORDER BY id", scope):
            reminders.setdefault(r["thread_id"], []).append(r["ref"])
        changes: dict[str, list[Cambio]] = {}
        for change in db.execute(
                f"SELECT * FROM case_changes WHERE state IN ('pending', 'change'){only} ORDER BY occurred_at", scope):
            changes.setdefault(change["thread_id"], []).append(Cambio.from_row(change))
        pending_links: dict[str, list[Vinculo]] = {}
        for link in db.execute(
                f"SELECT * FROM case_links WHERE state='pending'{only} ORDER BY occurred_at", scope):
            pending_links.setdefault(link["thread_id"], []).append(Vinculo.from_row(link))
        return _CaseRelations(latest_reply, followups, reminders, changes, pending_links)

    def descartados(self) -> list[dict]:
        """Hilos descartados, con el motivo, para que una persona pueda revisarlos y rescatarlos."""
        result = []
        for caso in self.listar(True):
            if caso["status"] != QuoteStatus.IGNORED:
                continue
            analysis = caso["analysis"]
            email = analysis.get("email") or {}
            result.append({
                "thread_id": caso["thread_id"],
                "reason": analysis.get("ignored_reason") or "Descartado por una versión anterior (sin motivo guardado)",
                "subject": email.get("subject") or "",
                "sender": email.get("from") or caso["recipient"],
                "date": (caso["updated_at"] or caso["first_seen_at"] or "")[:10],
                "rescued": bool(analysis.get("force_analysis")),
            })
        return result

    def archivados(self) -> list[dict]:
        """Casos eliminados del dashboard cuyo historial se conserva como evidencia."""
        with self._conexion() as db:
            rows = db.execute(
                """SELECT thread_id, COUNT(*) AS n, MAX(occurred_at) AS last, MIN(occurred_at) AS first
                   FROM case_events WHERE thread_id NOT IN (SELECT thread_id FROM quotes)
                   GROUP BY thread_id ORDER BY last DESC""").fetchall()
        return [dict(row) for row in rows]

    # ------------------------------------------------------- lo que cuelga de un caso
    def conversacion(self, thread_id: str, limite: int = 12) -> list[dict]:
        """Correos del hilo, el más reciente primero."""
        with self._conexion() as db:
            rows = db.execute(
                "SELECT direction, sender, subject, body, occurred_at FROM case_messages WHERE thread_id=? "
                "ORDER BY occurred_at DESC, id DESC LIMIT ?", (thread_id, limite)).fetchall()
        return [dict(row) for row in rows]

    def versiones_de_tabla(self, thread_id: str) -> list[dict]:
        """Versiones guardadas de la tabla de actividades (la del modelo, cada edición, cada propuesta), de la primera
        a la última."""
        with self._conexion() as db:
            rows = db.execute("SELECT * FROM table_versions WHERE thread_id=? ORDER BY version",
                              (thread_id,)).fetchall()
        return [dict(row) for row in rows]

    def primer_mensaje(self, thread_id: str) -> dict | None:
        """El primer correo que escribió el cliente en el hilo: la solicitud inicial, tal como llegó."""
        with self._conexion() as db:
            row = db.execute(
                "SELECT sender, subject, body, occurred_at FROM case_messages WHERE thread_id=? "
                "AND direction='received' ORDER BY occurred_at, id LIMIT 1", (thread_id,)).fetchone()
        return dict(row) if row else None

    def cambios(self, thread_id: str) -> list[Cambio]:
        """Todas las solicitudes de cambio del caso (cualquier estado), de la más antigua a la más nueva."""
        with self._conexion() as db:
            return [Cambio.from_row(row) for row in db.execute(
                "SELECT * FROM case_changes WHERE thread_id=? ORDER BY occurred_at", (thread_id,))]

    def eventos(self, thread_id: str) -> list[Evento]:
        """Seguimiento del caso, del más reciente al más antiguo."""
        with self._conexion() as db:
            rows = case_events(db, thread_id)
        return [Evento.from_row(row) for row in reversed(rows)]

    def integridad(self, thread_id: str) -> tuple[bool, int]:
        """¿La cadena de huellas del historial del caso está íntegra? Devuelve (íntegra, cantidad de eventos)."""
        with self._conexion() as db:
            return verify_chain(db, thread_id)

    def tiene_vinculos(self, thread_id: str) -> bool:
        """¿El caso avanzó más allá del primer análisis (correos vinculados, código de oferta o tabla editada)?

        Un aviso descartado o asignado a otro caso no cuenta: no hubo nada nuevo en este.
        """
        with self._conexion() as db:
            return db.execute(
                "SELECT 1 FROM case_links WHERE thread_id=? AND state IN ('pending', 'linked') "
                "UNION SELECT 1 FROM quotes WHERE thread_id=? AND quote_code IS NOT NULL AND quote_code <> '' "
                "UNION SELECT 1 FROM table_versions WHERE thread_id=? AND source='dashboard' "
                "LIMIT 1", (thread_id, thread_id, thread_id)).fetchone() is not None

    def personas(self, thread_id: str, correos_de_contacto: set[str]) -> list[dict]:
        """Quién ha escrito en el caso: persona, empresa, cuántos correos y el último."""
        with self._conexion() as db:
            rows = db.execute(
                """SELECT actor_email, MAX(actor_name) AS name, COUNT(*) AS n, MAX(occurred_at) AS last
                   FROM case_events WHERE thread_id=? AND actor_email <> ''
                     AND kind IN ('received', 'linked') GROUP BY actor_email ORDER BY MIN(occurred_at)""",
                (thread_id,)).fetchall()
        people = []
        for row in rows:
            email = row["actor_email"]
            people.append({
                "name": row["name"] or email, "email": email, "domain": email.rsplit("@", 1)[-1],
                "count": row["n"], "last": row["last"][:16].replace("T", " "),
                "role": "Contacto del caso" if email in correos_de_contacto else "Otra persona",
            })
        return people

    # ---------------------------------------------------------------- escrituras
    def guardar_datos_comerciales(self, thread_id: str, cliente: str, tipo: str, sector: str, notas: str) -> None:
        """Guarda cliente, tipo, sector y notas del caso (la validación de las listas es de quien llama)."""
        now = datetime.now().astimezone().isoformat(timespec="seconds")
        with self._conexion() as db:
            db.execute("""INSERT INTO quote_metadata
                          (thread_id, client_type, sector, client_name, notes, updated_at)
                          VALUES (?, ?, ?, ?, ?, ?)
                          ON CONFLICT(thread_id) DO UPDATE SET
                          client_type=excluded.client_type, sector=excluded.sector,
                          client_name=excluded.client_name, notes=excluded.notes,
                          updated_at=excluded.updated_at""",
                       (thread_id, tipo, sector, cliente.strip(), notas.strip(), now))
            db.commit()

    def fijar_cliente(self, thread_id: str, cliente: str) -> None:
        """Guarda el cliente confirmado de un caso sin tocar el resto de sus datos comerciales."""
        now = datetime.now().astimezone().isoformat(timespec="seconds")
        with self._conexion() as db:
            db.execute("""INSERT INTO quote_metadata
                          (thread_id, client_type, sector, client_name, notes, updated_at)
                          VALUES (?, 'No clasificado', 'No clasificado', ?, '', ?)
                          ON CONFLICT(thread_id) DO UPDATE SET
                          client_name=excluded.client_name, updated_at=excluded.updated_at""",
                       (thread_id, cliente.strip(), now))
            db.commit()

    def guardar_hoja(self, thread_id: str, sheet_id: str, modificada: str) -> None:
        """Recuerda la hoja del caso y cuándo la dejamos: así se nota si alguien la editó directamente en Drive."""
        now = datetime.now().astimezone().isoformat(timespec="seconds")
        with self._conexion() as db:
            db.execute("UPDATE quotes SET sheet_id=?, sheet_modified=?, sheet_synced_at=?, sheet_error='' "
                       "WHERE thread_id=?", (sheet_id, modificada, now, thread_id))
            db.commit()

    def registrar_error_de_hoja(self, thread_id: str, texto: str) -> None:
        """Deja constancia de por qué no se pudo actualizar la hoja (se ve en el caso)."""
        with self._conexion() as db:
            db.execute("UPDATE quotes SET sheet_error=? WHERE thread_id=?", (texto[:300], thread_id))
            db.commit()

    def eliminar(self, thread_ids: list[str]) -> list[str]:
        """Borra todo lo del caso (solicitud, datos, mensajes, versiones, vínculos…) y devuelve qué hilos existían.

        Se conservan como evidencia el historial (`case_events`, con la anotación de que se eliminó), las solicitudes de
        cambio (`case_changes`, alimentan el acta) y el registro de lo que se envió (`mail_operations`) (T-42).
        """
        ids = list(dict.fromkeys(thread_ids))
        if not ids:
            return []
        marks = ",".join("?" * len(ids))
        with self._conexion() as db:
            found = [row["thread_id"] for row in db.execute(
                f"SELECT thread_id FROM quotes WHERE thread_id IN ({marks})", ids)]
            if found:
                found_marks = ",".join("?" * len(found))
                for thread_id in found:
                    record_event(db, thread_id, "deleted", "dashboard",
                                 f"{datetime.now().astimezone().isoformat(timespec='seconds')}-{uuid4().hex[:6]}",
                                 "El caso se eliminó del dashboard; su historial se conserva en el archivo.")
                for table in TABLAS_DEL_CASO:
                    db.execute(f"DELETE FROM {table} WHERE thread_id IN ({found_marks})", found)
                for table in TABLAS_CON_DOS_HILOS:     # el hilo puede estar en cualquiera de los dos lados
                    db.execute(f"DELETE FROM {table} WHERE thread_id IN ({found_marks}) "
                               f"OR other_thread_id IN ({found_marks})", found + found)
            db.commit()
        return found

    def marcar_para_reanalisis(self, thread_id: str) -> None:
        """Marca un hilo descartado para que la próxima revisión de Gmail lo analice (no cambia su estado)."""
        with self._conexion() as db:
            begin_immediate(db)
            row = db.execute(
                "SELECT status, analysis_json FROM quotes WHERE thread_id=?", (thread_id,)
            ).fetchone()
            if not row:
                raise ValueError("La solicitud no existe.")
            if row["status"] != QuoteStatus.IGNORED:
                raise ValueError("Solo se pueden rescatar los correos descartados.")
            analysis = load_analysis(row["analysis_json"])
            analysis["force_analysis"] = True
            db.execute(
                "UPDATE quotes SET last_inbound_id='', analysis_json=?, updated_at=? WHERE thread_id=?",
                (json.dumps(analysis, ensure_ascii=False),
                 datetime.now().astimezone().isoformat(timespec="seconds"), thread_id),
            )
            db.commit()
