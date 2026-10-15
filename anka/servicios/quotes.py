"""Los casos (solicitudes) guardados en SQLite, vistos y modificados desde el dashboard: la fachada de los servicios.

  1. lectura: `all_quotes` / `get_quote` arman cada `Caso` (delegan en `repositorio.py`);
  2. lo que se queda aquí: datos comerciales, descartar/rescatar, eliminar, novedades y el marcador de cambios.
Lo demás vive en su módulo y aquí solo se reexporta, para que quien ya importaba de `quotes` no cambie: los vínculos y
la unión de proyectos en `vinculos.py`, las decisiones de revisión técnica en `revision_tecnica.py` y la cola del piloto
local en `cola_del_piloto.py`. La edición de la tabla vive en `ediciones.py` y la de Work en `work_import.py`.
"""

from __future__ import annotations

from datetime import datetime

from anka.dominio.caso import CLIENT_TYPES, SECTORS, Caso, load_analysis, timestamp
from anka.dominio.errores import ErrorDeValidacion
from anka.dominio.novedades import REQUIEREN_ACCION
from anka.dominio.registros import Cambio, Evento
from anka.infra.logging_setup import get_logger
from anka.infra.store import connect, lectura, transaccion  # noqa: F401  (connect se reexporta: lo importan las pruebas)
from anka.servicios.cola_del_piloto import (  # noqa: F401  (se reexportan: quien ya los importaba de aquí no cambia)
    get_review, pending_reviews, review_action, review_packet_path, save_review_packet,
)
from anka.servicios.repositorio import RepositorioDeCasos
from anka.servicios.revision_tecnica import (  # noqa: F401  (se reexportan: quien ya los importaba de aquí no cambia)
    aceptar_lectura_revisada, recuperar_propuesta, resolve_model_proposal, resolve_technical_review, volver_a_decidir,
)
from anka.servicios.vinculos import (  # noqa: F401  (se reexportan: quien ya los importaba de aquí no cambia)
    dismiss_pending_links, dismiss_pending_links_many, merge_blocker, merge_project_thread, reason_label,
    resolve_link, separate_project_thread, separated_project_pairs,
)

log = get_logger(__name__)

# Fachada: las funciones de este módulo delegan en el repositorio de casos (ver repositorio.py).
repositorio = RepositorioDeCasos()


_TIPOS_QUE_CUENTAN = ", ".join(f"'{tipo}'" for tipo in sorted(REQUIEREN_ACCION))   # constantes propias, no entrada


def unread_thread_count() -> int:
    """Cuántos casos tienen novedades sin leer que piden acción: lo que muestra el contador (U-25)."""
    with lectura() as db:
        return int(db.execute(
            f"""SELECT COUNT(DISTINCT n.thread_id) FROM dashboard_notifications n
                JOIN quotes q ON q.thread_id = n.thread_id
                WHERE n.read_at IS NULL AND n.kind IN ({_TIPOS_QUE_CUENTAN})""").fetchone()[0])


def unread_notifications(limit: int = 20) -> list[dict]:
    """Novedades del dashboard sin leer, las más recientes primero."""
    with lectura() as db:
        rows = db.execute(f"""SELECT n.*, q.analysis_json
                             FROM dashboard_notifications n
                             JOIN quotes q ON q.thread_id = n.thread_id
                             WHERE n.read_at IS NULL AND n.kind IN ({_TIPOS_QUE_CUENTAN})
                             ORDER BY n.created_at DESC, n.id DESC
                             LIMIT ?""", (limit,)).fetchall()
    result = []
    for row in rows:
        analysis = load_analysis(row["analysis_json"])
        result.append({
            "id": row["id"], "thread_id": row["thread_id"],
            "kind": row["kind"], "title": row["title"],
            "message": row["message"], "created_at": row["created_at"],
            "project_name": analysis.get("project_name") or "Solicitud sin nombre",
        })
    return result


def mark_notifications_read(thread_id: str | None = None) -> None:
    """Marca como leídas las novedades de un caso o todas."""
    now = datetime.now().astimezone().isoformat(timespec="seconds")
    with transaccion(inmediata=False) as db:
        if thread_id:
            db.execute("""UPDATE dashboard_notifications SET read_at=?
                          WHERE read_at IS NULL AND thread_id=?""", (now, thread_id))
        else:
            db.execute(
                "UPDATE dashboard_notifications SET read_at=? WHERE read_at IS NULL", (now,)
            )


def minutes_since(value: str) -> int:
    """Minutos transcurridos desde una fecha."""
    return max(0, int((datetime.now().astimezone().timestamp() - timestamp(value)) // 60))


def ago_text(minutes: int) -> str:
    """«hace un momento», «hace 5 min», «hace 3 h»… según los minutos."""
    if minutes < 2:
        return "hace un momento"
    if minutes < 90:
        return f"hace {minutes} min"
    if minutes < 2880:
        return f"hace {minutes // 60} h"
    return f"hace {minutes // 1440} días"


def case_conversation(thread_id: str, limit: int = 12) -> list[dict]:
    """Correos del hilo, el más reciente primero."""
    return repositorio.conversacion(thread_id, limit)


def case_change_log(thread_id: str) -> list[Cambio]:
    """Todas las solicitudes de cambio del caso (cualquier estado), de la más antigua a la más nueva."""
    return repositorio.cambios(thread_id)


def chain_status(thread_id: str) -> tuple[bool, int]:
    """¿La cadena de huellas del historial del caso está íntegra? Devuelve (íntegra, cantidad de eventos)."""
    return repositorio.integridad(thread_id)


def archived_threads() -> list[dict]:
    """Casos eliminados del dashboard cuyo historial se conserva como evidencia."""
    return repositorio.archivados()


def case_has_links(thread_id: str) -> bool:
    """¿El caso avanzó más allá del primer análisis (correos vinculados, código de oferta o tabla editada)?"""
    return repositorio.tiene_vinculos(thread_id)


def case_people(thread_id: str, contact_emails: set[str]) -> list[dict]:
    """Quién ha escrito en el caso: persona, empresa, cuántos correos y el último."""
    return repositorio.personas(thread_id, contact_emails)


def all_quotes(include_ignored: bool = False) -> list[Caso]:
    """Todos los casos con sus cambios, vínculos, seguimiento y espera, el más reciente primero."""
    return repositorio.listar(include_ignored)


def get_quote(thread_id: str) -> Caso | None:
    """Un caso por su hilo, o `None` si no existe."""
    return repositorio.obtener(thread_id)


def discarded_quotes() -> list[dict]:
    """Hilos descartados, con el motivo, para que una persona pueda revisarlos y rescatarlos."""
    return repositorio.descartados()


def rescue_discarded(thread_id: str) -> str:
    """Marca un hilo descartado para que la próxima revisión de Gmail lo analice.

    No cambia su estado: lo reprocesa el ciclo normal (`pipeline.run`), que salta el
    filtro de ruido para los hilos con `force_analysis`.
    """
    repositorio.marcar_para_reanalisis(thread_id)
    log.info("%s: rescatado del descarte; se reanalizará en la próxima revisión", thread_id)
    return "Se volverá a mirar en la próxima búsqueda de correos."


def delete_quotes(thread_ids: list[str]) -> str:
    """Borra del dashboard los hilos indicados: solicitud, datos comerciales, novedades y paquete de revisión.

    Solo toca la base local y `review_queue`; no modifica Gmail, Drive ni Notion.
    """
    if not thread_ids:
        return "No marcaste ningún caso. Marca uno o más y vuelve a pulsar."
    found = repositorio.eliminar(thread_ids)
    for thread_id in found:
        review_packet_path(thread_id).unlink(missing_ok=True)
    log.info("Eliminadas %d solicitud(es) del dashboard: %s", len(found), ", ".join(found))
    if not found:
        return "Esos casos ya no estaban."
    return f"Se borraron {len(found)} caso(s)."


def save_quote_metadata(thread_id: str, client_name: str, client_type: str,
                        sector: str, notes: str) -> None:
    """Guarda cliente, tipo, sector y notas de un caso; rechaza valores fuera de las listas."""
    if client_type not in CLIENT_TYPES:
        raise ErrorDeValidacion("Tipo de cliente no válido.")
    if sector not in SECTORS:
        raise ErrorDeValidacion("Sector no válido.")
    if not repositorio.obtener(thread_id):
        raise ErrorDeValidacion("Este caso ya no existe. Vuelve a Mis casos.")
    repositorio.guardar_datos_comerciales(thread_id, client_name, client_type, sector, notes)


def set_client_name(thread_id: str, client_name: str) -> None:
    """Guarda el cliente confirmado de un caso sin tocar el resto de sus datos comerciales."""
    repositorio.fijar_cliente(thread_id, client_name)


def thread_events(thread_id: str) -> list[Evento]:
    """Seguimiento del caso, del más reciente al más antiguo."""
    return repositorio.eventos(thread_id)


def change_marker() -> str:
    """Cambia cuando se añade, edita o borra cualquier caso (para refrescar el dashboard)."""
    with lectura() as db:
        row = db.execute(
            "SELECT COUNT(*) AS n, MAX(COALESCE(updated_at, first_seen_at, '')) AS ts, "
            "(SELECT COUNT(*) FROM dashboard_notifications WHERE read_at IS NULL "
            f"AND kind IN ({_TIPOS_QUE_CUENTAN})) AS unread, "
            "(SELECT COUNT(*) || MAX(recorded_at) FROM case_events) AS ev FROM quotes"
        ).fetchone()
    return f"{row['n']}|{row['ts'] or ''}|{row['unread']}|{row['ev']}"


