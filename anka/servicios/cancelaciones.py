"""Cancela un mensaje de prueba con una lápida persistente que nada reactiva (T-83, D-179).

Una sola transacción `BEGIN IMMEDIATE`: verifica la identidad, escribe (o convierte) un trabajo en `cancelado`,
deja el evento y el diario fuera de SQLite, y confirma. Solo se cancela lo pendiente; un trabajo publicado, ambiguo o
conciliado exige a una persona. No toca Gmail ni Drive y no cambia la configuración. Nada de aquí corre solo: lo
invoca `work_jobs cancelar`.
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Callable
from typing import Any

from anka.dominio.cancelacion_work import (
    CANCELADO_AHORA,
    EVENTO_DE_CANCELACION,
    YA_CANCELADO,
    Autorizacion,
    CancelacionRechazada,
)
from anka.dominio.reloj import ahora_iso
from anka.dominio.trabajo_work import (
    AMBIGUO,
    CANCELADO,
    CONCILIADO,
    PENDIENTE_DE_PUBLICAR,
    PUBLICADO,
    PUBLICANDO,
    TIPO_ANALIZAR,
    VERSION_DEL_CONTRATO,
    huella_de_la_referencia,
    id_de_trabajo,
    referencia_del_mensaje,
)
from anka.infra import lapidas, settings
from anka.infra.store import lectura, transaccion
from anka.servicios.recepcion import CUENTA, PENDIENTE


def mensaje_cancelado(db: sqlite3.Connection, thread_id: str, message_id: object) -> bool:
    """¿Ese mensaje tiene una lápida? Un `message_id` vacío o que no es texto nunca coincide."""
    if not isinstance(message_id, str) or not message_id.strip():
        return False
    try:
        return db.execute("SELECT 1 FROM trabajos_work WHERE account=? AND thread_id=? AND message_id=? AND state=? "
                          "LIMIT 1", (CUENTA, str(thread_id), message_id.strip(), CANCELADO)).fetchone() is not None
    except sqlite3.OperationalError:          # una base sin la tabla de trabajos no tiene lápidas
        return False


def _verificar_mensaje(db: sqlite3.Connection, aut: Autorizacion) -> tuple[sqlite3.Row | None, bool]:
    fila = db.execute("SELECT * FROM recepcion_mensajes WHERE account=? AND thread_id=? AND message_id=?",
                      (CUENTA, aut.thread_id, aut.message_id)).fetchone()
    if fila is None:
        if not aut.preventiva:
            raise CancelacionRechazada("mensaje_no_registrado")
        return None, False
    if aut.etiqueta not in str(fila["subject"] or ""):
        raise CancelacionRechazada("no_es_de_prueba")
    return fila, True


def _juzgar(trabajos: list[sqlite3.Row]) -> None:
    """Rechaza si algún trabajo del mensaje no es cancelable (se comprueban todos antes de escribir nada)."""
    for trabajo in trabajos:
        estado = trabajo["state"]
        if estado == CONCILIADO:
            raise CancelacionRechazada("conciliado_intocable", trabajo["job_id"])
        if estado == PUBLICANDO:
            raise CancelacionRechazada("publicando_en_curso", trabajo["job_id"])
        if estado in (PUBLICADO, AMBIGUO) or (estado == PENDIENTE_DE_PUBLICAR and trabajo["sheet_file_id"]):
            raise CancelacionRechazada("ya_publicado_requiere_persona", trabajo["job_id"])


def _evento(db: sqlite3.Connection, aut: Autorizacion, job_id: str, when: str, detalle: dict[str, Any]) -> None:
    db.execute("INSERT INTO recepcion_eventos(account,thread_id,message_id,event_kind,occurred_at,detail_json) "
               "VALUES(?,?,?,?,?,?)",
               (CUENTA, aut.thread_id, aut.message_id, EVENTO_DE_CANCELACION, when,
                json.dumps({"job_id": job_id, "motivo": aut.motivo, "operador": aut.operador, "etiqueta": aut.etiqueta,
                            "cancelado_at": when, **detalle},
                           ensure_ascii=False, separators=(",", ":"), sort_keys=True)))


def _diario(aut: Autorizacion, job_id: str, when: str) -> None:
    lapidas.registrar({"job_id": job_id, "account": CUENTA, "thread_id": aut.thread_id, "message_id": aut.message_id,
                       "job_type": TIPO_ANALIZAR, "contract_version": VERSION_DEL_CONTRATO, "operador": aut.operador,
                       "motivo": aut.motivo, "etiqueta": aut.etiqueta, "cancelado_at": when})


def _cancelar(db: sqlite3.Connection, aut: Autorizacion, when: str) -> dict[str, Any]:
    fila, verificada = _verificar_mensaje(db, aut)
    job_id = id_de_trabajo(CUENTA, aut.thread_id, aut.message_id, TIPO_ANALIZAR, VERSION_DEL_CONTRATO)
    trabajos = db.execute("SELECT * FROM trabajos_work WHERE account=? AND thread_id=? AND message_id=? "
                          "ORDER BY job_id", (CUENTA, aut.thread_id, aut.message_id)).fetchall()
    if trabajos and all(t["state"] == CANCELADO for t in trabajos):
        if not lapidas.esta_registrada(trabajos[0]["job_id"]):
            _diario(aut, trabajos[0]["job_id"], when)       # una lápida sin diario se completa; nada más se escribe
        return {"resultado": YA_CANCELADO, "job_id": trabajos[0]["job_id"], "estado_previo": CANCELADO,
                "verificada": verificada}
    if fila is not None and (fila["state"], fila["disposition"]) != PENDIENTE and not trabajos:
        raise CancelacionRechazada("mensaje_no_pendiente", f"{fila['state']}/{fila['disposition']}")
    _juzgar(trabajos)
    if not trabajos:
        referencia = referencia_del_mensaje(dict(fila)) if fila is not None else {}
        db.execute("""INSERT INTO trabajos_work(job_id,account,thread_id,message_id,job_type,contract_version,state,
            message_ref_json,ref_sha256,last_error,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",
                   (job_id, CUENTA, aut.thread_id, aut.message_id, TIPO_ANALIZAR, VERSION_DEL_CONTRATO, CANCELADO,
                    json.dumps(referencia, ensure_ascii=False, sort_keys=True),
                    huella_de_la_referencia(referencia) if referencia else "", "cancelado:sin_trabajo_previo",
                    when, when))
        _evento(db, aut, job_id, when, {"estado_previo": "sin_trabajo", "verificada": verificada})
        _diario(aut, job_id, when)
        return {"resultado": CANCELADO_AHORA, "job_id": job_id, "estado_previo": "sin_trabajo",
                "verificada": verificada, "revisar_drive": False}
    for trabajo in trabajos:
        if trabajo["state"] == CANCELADO:
            continue
        cambio = db.execute("UPDATE trabajos_work SET state=?,claim_token='',claim_until=NULL,last_error=?,"
                            "updated_at=? WHERE job_id=? AND state=?",
                            (CANCELADO, "cancelado:" + aut.motivo[:150], when, trabajo["job_id"],
                             PENDIENTE_DE_PUBLICAR))
        if cambio.rowcount != 1:
            raise CancelacionRechazada("carrera", trabajo["job_id"])
        _evento(db, aut, trabajo["job_id"], when, {"estado_previo": trabajo["state"], "verificada": verificada,
                                                   "intentos_previos": trabajo["attempts"]})
        _diario(aut, trabajo["job_id"], when)
    # un intento de publicar que falló pudo dejar una hoja marcada en Drive sin anotar: lo revisa una persona
    return {"resultado": CANCELADO_AHORA, "job_id": trabajos[0]["job_id"], "estado_previo": trabajos[0]["state"],
            "verificada": verificada, "revisar_drive": any(t["attempts"] > 0 for t in trabajos)}


def cancelar_mensaje(autorizacion: Autorizacion, ahora: Callable[[], str] | None = None) -> dict[str, Any]:
    """Cancela, de forma persistente e idempotente, el mensaje de prueba autorizado.

    Devuelve `{"resultado": "cancelado" | "ya_cancelado", "job_id", "estado_previo", "verificada"}`; si no se puede,
    lanza `CancelacionRechazada` sin haber escrito nada. El diario se escribe antes de confirmar: si falla, se
    revierte todo.
    """
    aut = autorizacion.normalizada()
    when = (ahora or (lambda: ahora_iso("seconds")))()
    with transaccion() as db:
        return _cancelar(db, aut, when)


def verificar_lapida_preventiva(autorizacion: Autorizacion) -> dict[str, Any]:
    """Tras registrarse el mensaje de una cancelación preventiva: ¿su asunto lleva la etiqueta? Solo lectura."""
    aut = autorizacion.normalizada()
    with lectura() as db:
        fila = db.execute("SELECT subject FROM recepcion_mensajes WHERE account=? AND thread_id=? AND message_id=?",
                          (CUENTA, aut.thread_id, aut.message_id)).fetchone()
    if fila is None:
        return {"estado": "aun_no_registrado"}
    return {"estado": "registrado", "coincide": aut.etiqueta in str(fila["subject"] or "")}


def reaplicar_lapidas() -> dict[str, Any]:
    """Devuelve a la base las lápidas del diario que falten (idempotente); los choques se informan sin tocarlos."""
    return lapidas.reaplicar_en_archivo(settings.DATA_DIR / "quotes.sqlite3", ahora_iso("seconds"))


def verificar_lapidas() -> dict[str, Any]:
    """Solo lectura: ¿el diario y la base coinciden? Debe estar en orden antes de reactivar la coordinación."""
    with lectura() as db:
        informe = lapidas.comparar(db)
    informe["en_orden_total"] = not (informe["faltan"] or informe["chocan"] or informe["lineas_rotas"])
    return informe
