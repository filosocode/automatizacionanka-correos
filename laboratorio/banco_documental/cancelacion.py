"""Cancelación segura de un mensaje o trabajo ficticio de prueba (T-81). IMPLEMENTACIÓN DE REFERENCIA DE LABORATORIO.

NO es producción: no se importa desde `anka/`, no se conecta a ninguna base por sí misma y nunca debe recibir la conexión de
la base operativa. Quien la llama (las pruebas) le da una conexión a una base TEMPORAL. Demuestra qué debería hacer una
futura orden `work_jobs cancelar` y cuánto del código actual ya la respeta.

Idea: una «lápida» persistente. Cancelar escribe (o convierte) UNA fila de `trabajos_work` en estado `cancelado`, con el mismo
`job_id` determinista que tendría el trabajo real. Con esa fila:
- `mensajes_nuevos` ignora el mensaje (ya tiene fila de trabajo), así que el coordinador nunca lo crea;
- `crear_trabajo` devuelve la fila existente sin crear otra (mismo `job_id`);
- `_reclamar` rechaza publicar un trabajo cancelado y `trabajos_por_estado` no lo lista en ningún estado vivo.

Reglas (todas con prueba):
- identidad exacta: `thread_id` y `message_id` literales; sin comodines, sin buscar por asunto, remitente ni coincidencias parciales;
- si el mensaje ya está en la cola de recepción, su asunto debe llevar la etiqueta de prueba autorizada (evita cancelar uno real);
- solo se cancela lo pendiente: un trabajo publicando/publicado/ambiguo exige a una persona (hay hoja en Drive y Work puede estar
  trabajando) y uno conciliado no se toca nunca; un mensaje que ya no está pendiente se rechaza;
- todo en una transacción `BEGIN IMMEDIATE`; el cambio de estado es comparar-y-cambiar; nada se borra;
- idempotente: repetir devuelve `ya_cancelado` sin escribir; deja motivo, fecha, identidad y operador en `recepcion_eventos`;
- no toca configuración, procesos, Gmail ni Drive.
"""

from __future__ import annotations

import json
import re
import sqlite3
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

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

# Réplicas de constantes de `anka.servicios.recepcion` (el laboratorio solo importa el dominio puro); una prueba las compara.
CUENTA = "ankacomercialia@gmail.com"
PENDIENTE = ("pendiente_de_procesar", "pendiente_de_interpretacion_work")
EVENTO = "trabajo_cancelado"

CREADO = "cancelado"
YA_CANCELADO = "ya_cancelado"

IDENTIFICADOR = re.compile(r"^[A-Za-z0-9_-]{1,128}$")
ETIQUETA = re.compile(r"^\[PI-[A-Z0-9-]{3,40}\]$")

RECHAZOS = {
    "identidad_invalida": "el thread_id o el message_id no es un identificador literal válido",
    "falta_operador": "falta quien opera",
    "falta_motivo": "falta el motivo",
    "etiqueta_invalida": "la etiqueta de prueba no tiene la forma [PI-…]",
    "mensaje_no_registrado": "el mensaje no está en la cola de recepción (use la cancelación preventiva explícita)",
    "no_es_de_prueba": "el asunto del mensaje no lleva la etiqueta de prueba autorizada: podría ser una solicitud real",
    "mensaje_no_pendiente": "el mensaje ya no está pendiente de interpretación",
    "publicando_en_curso": "el trabajo se está publicando: espere o intervenga una persona",
    "ya_publicado_requiere_persona": "el trabajo ya está publicado (hay hoja en Drive y Work puede estar trabajando)",
    "conciliado_intocable": "el trabajo está conciliado y no se cambia",
    "carrera": "el trabajo cambió de estado durante la cancelación; vuelva a consultar",
    "cuenta_distinta": "la cuenta de la fila no es la del buzón de Anka",
}


class CancelacionRechazada(Exception):
    """La cancelación no se hizo; `codigo` es una clave de `RECHAZOS` y no se escribió nada."""

    def __init__(self, codigo: str, detalle: str = "") -> None:
        self.codigo = codigo
        super().__init__(f"{codigo}: {RECHAZOS[codigo]}" + (f" ({detalle})" if detalle else ""))


@dataclass(frozen=True)
class Autorizacion:
    """Lo que una persona autoriza, por escrito, para UN mensaje de prueba concreto."""

    thread_id: str
    message_id: str
    operador: str
    motivo: str
    etiqueta: str                 # p. ej. "[PI-T79-PRUEBA-01]": debe estar en el asunto del mensaje registrado
    preventiva: bool = False      # permite cancelar antes de que el receptor registre el mensaje

    def normalizada(self) -> Autorizacion:
        """La autorización con los textos recortados; rechaza identidades no literales y datos que faltan."""
        hilo, mensaje = self.thread_id.strip(), self.message_id.strip()
        if not (IDENTIFICADOR.match(hilo) and IDENTIFICADOR.match(mensaje)):
            raise CancelacionRechazada("identidad_invalida")
        if not self.operador.strip():
            raise CancelacionRechazada("falta_operador")
        if not self.motivo.strip():
            raise CancelacionRechazada("falta_motivo")
        if not ETIQUETA.match(self.etiqueta.strip()):
            raise CancelacionRechazada("etiqueta_invalida")
        return Autorizacion(hilo, mensaje, self.operador.strip(), self.motivo.strip(), self.etiqueta.strip(),
                            self.preventiva)


def _ahora() -> str:
    return datetime.now(UTC).astimezone().isoformat(timespec="seconds")


def _evento(db: sqlite3.Connection, aut: Autorizacion, job_id: str, when: str, detalle: dict[str, Any]) -> None:
    db.execute("INSERT INTO recepcion_eventos(account,thread_id,message_id,event_kind,occurred_at,detail_json) "
               "VALUES(?,?,?,?,?,?)",
               (CUENTA, aut.thread_id, aut.message_id, EVENTO, when,
                json.dumps({"job_id": job_id, "motivo": aut.motivo, "operador": aut.operador, "etiqueta": aut.etiqueta,
                            "cancelado_at": when, **detalle}, ensure_ascii=False, separators=(",", ":"), sort_keys=True)))


def _verificar_mensaje(db: sqlite3.Connection, aut: Autorizacion) -> tuple[sqlite3.Row | None, bool]:
    """La fila de recepción y si la identidad quedó verificada contra ella. Rechaza lo que podría ser una solicitud real."""
    fila = db.execute("SELECT * FROM recepcion_mensajes WHERE account=? AND thread_id=? AND message_id=?",
                      (CUENTA, aut.thread_id, aut.message_id)).fetchone()
    if fila is None:
        if not aut.preventiva:
            raise CancelacionRechazada("mensaje_no_registrado")
        return None, False
    if aut.etiqueta not in str(fila["subject"] or ""):
        raise CancelacionRechazada("no_es_de_prueba")
    return fila, True


def _trabajos_del_mensaje(db: sqlite3.Connection, aut: Autorizacion) -> list[sqlite3.Row]:
    return db.execute("SELECT * FROM trabajos_work WHERE account=? AND thread_id=? AND message_id=? ORDER BY job_id",
                      (CUENTA, aut.thread_id, aut.message_id)).fetchall()


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


def cancelar_mensaje(db: sqlite3.Connection, aut: Autorizacion, ahora: Callable[[], str] = _ahora) -> dict[str, Any]:
    """Cancela, de forma persistente e idempotente, el mensaje de prueba autorizado. Una sola transacción.

    Devuelve `{"resultado": "cancelado" | "ya_cancelado", "job_id", "estado_previo", "verificada"}`; si no se puede,
    lanza `CancelacionRechazada` sin haber escrito nada.
    """
    aut = aut.normalizada()
    if db.in_transaction:
        raise RuntimeError("La conexión ya tiene una transacción abierta; la cancelación necesita la suya.")
    db.execute("BEGIN IMMEDIATE")
    try:
        resultado = _cancelar(db, aut, ahora())
        db.execute("COMMIT")
        return resultado
    except BaseException:
        db.execute("ROLLBACK")
        raise


def _cancelar(db: sqlite3.Connection, aut: Autorizacion, when: str) -> dict[str, Any]:
    fila, verificada = _verificar_mensaje(db, aut)
    job_id = id_de_trabajo(CUENTA, aut.thread_id, aut.message_id, TIPO_ANALIZAR, VERSION_DEL_CONTRATO)
    trabajos = _trabajos_del_mensaje(db, aut)
    if trabajos and all(t["state"] == CANCELADO for t in trabajos):
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
                    huella_de_la_referencia(referencia) if referencia else "", "cancelado:sin_trabajo_previo", when, when))
        _evento(db, aut, job_id, when, {"estado_previo": "sin_trabajo", "verificada": verificada})
        return {"resultado": CREADO, "job_id": job_id, "estado_previo": "sin_trabajo", "verificada": verificada}
    for trabajo in trabajos:
        if trabajo["state"] == CANCELADO:
            continue
        cambio = db.execute("UPDATE trabajos_work SET state=?,claim_token='',claim_until=NULL,last_error=?,updated_at=? "
                            "WHERE job_id=? AND state=?",
                            (CANCELADO, "cancelado:" + aut.motivo[:150], when, trabajo["job_id"], PENDIENTE_DE_PUBLICAR))
        if cambio.rowcount != 1:
            raise CancelacionRechazada("carrera", trabajo["job_id"])
        _evento(db, aut, trabajo["job_id"], when, {"estado_previo": trabajo["state"], "verificada": verificada,
                                                   "intentos_previos": trabajo["attempts"]})
    # un intento de publicar que falló pudo dejar una hoja marcada en Drive sin que el trabajo la anotara: lo revisa una persona
    revisar = any(t["attempts"] > 0 for t in trabajos)
    return {"resultado": CREADO, "job_id": trabajos[0]["job_id"], "estado_previo": trabajos[0]["state"],
            "verificada": verificada, "revisar_drive": revisar}


def verificar_lapida_preventiva(db: sqlite3.Connection, aut: Autorizacion) -> dict[str, Any]:
    """Tras registrarse el mensaje de una cancelación preventiva: ¿su asunto lleva la etiqueta? Solo lectura.

    `coincide=False` significa que se canceló algo que podría ser real: una persona debe revisarlo ya.
    """
    aut = aut.normalizada()
    fila = db.execute("SELECT subject FROM recepcion_mensajes WHERE account=? AND thread_id=? AND message_id=?",
                      (CUENTA, aut.thread_id, aut.message_id)).fetchone()
    if fila is None:
        return {"estado": "aun_no_registrado"}
    return {"estado": "registrado", "coincide": aut.etiqueta in str(fila["subject"] or "")}
