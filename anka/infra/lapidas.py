"""Diario de lápidas de cancelación, fuera de SQLite, para que una copia de seguridad restaurada no las pierda (T-83).

`cancelaciones.jsonl` vive en la carpeta de datos, solo se agrega al final (con `fsync`) y NO forma parte de las
copias, así que restaurar una copia más vieja no lo pisa. Después de restaurar, `reaplicar` devuelve a la base las
lápidas que falten.
Nunca cambia un trabajo publicado, conciliado o ambiguo: eso lo decide una persona.
"""

from __future__ import annotations

import json
import os
import sqlite3
from pathlib import Path
from typing import Any

from anka.infra import settings

NOMBRE = "cancelaciones.jsonl"
CANCELADO = "cancelado"
PENDIENTE_DE_PUBLICAR = "pendiente_de_publicar"
EVENTO_DE_REAPLICACION = "trabajo_cancelado_reaplicado"


def ruta() -> Path:
    """El diario de la carpeta de datos vigente."""
    return settings.DATA_DIR / NOMBRE


def registrar(entrada: dict[str, Any]) -> None:
    """Agrega una línea al diario y la fuerza a disco; si falla, la cancelación entera se revierte."""
    linea = json.dumps(entrada, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n"
    ruta().parent.mkdir(parents=True, exist_ok=True)
    with ruta().open("a", encoding="utf-8", newline="\n") as archivo:
        archivo.write(linea)
        archivo.flush()
        os.fsync(archivo.fileno())


def leer() -> tuple[list[dict[str, Any]], int]:
    """Las entradas válidas del diario y cuántas líneas no se pudieron leer (una línea rota no tapa a las demás)."""
    entradas: list[dict[str, Any]] = []
    rotas = 0
    try:
        texto = ruta().read_text(encoding="utf-8")
    except FileNotFoundError:
        return entradas, rotas
    for linea in texto.splitlines():
        if not linea.strip():
            continue
        try:
            valor = json.loads(linea)
        except ValueError:
            rotas += 1
            continue
        if isinstance(valor, dict) and valor.get("job_id") and valor.get("thread_id") and valor.get("message_id"):
            entradas.append(valor)
        else:
            rotas += 1
    return entradas, rotas


def esta_registrada(job_id: str) -> bool:
    """¿El diario ya tiene la lápida de ese trabajo?"""
    return any(e["job_id"] == job_id for e in leer()[0])


def comparar(db: sqlite3.Connection) -> dict[str, Any]:
    """Solo lectura: qué lápidas del diario faltan en la base o chocan con un trabajo ya avanzado."""
    faltan, chocan, bien = [], [], 0
    entradas, rotas = leer()
    for entrada in entradas:
        fila = db.execute("SELECT state FROM trabajos_work WHERE job_id=?", (entrada["job_id"],)).fetchone()
        if fila is None or fila["state"] == PENDIENTE_DE_PUBLICAR:
            faltan.append(entrada["job_id"])
        elif fila["state"] == CANCELADO:
            bien += 1
        else:
            chocan.append({"job_id": entrada["job_id"], "estado": fila["state"]})
    return {"entradas": len(entradas), "lineas_rotas": rotas, "en_orden": bien, "faltan": faltan, "chocan": chocan}


def reaplicar_en_archivo(base: Path, ahora: str) -> dict[str, Any]:
    """Reaplica sobre un archivo SQLite SIN migrarlo; una base vieja sin `trabajos_work` se omite."""
    conexion = sqlite3.connect(base, timeout=15, isolation_level=None)
    conexion.row_factory = sqlite3.Row
    try:
        tabla = conexion.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='trabajos_work'").fetchone()
        if tabla is None:
            return {"omitido": "sin_tabla_de_trabajos", "entradas": len(leer()[0])}
        return reaplicar(conexion, ahora)
    finally:
        conexion.close()


def reaplicar(db: sqlite3.Connection, ahora: str) -> dict[str, Any]:
    """Devuelve a la base las lápidas del diario que falten. Idempotente; los choques se informan y no se tocan."""
    informe = comparar(db)
    reaplicadas: list[str] = []
    db.execute("BEGIN IMMEDIATE")
    try:
        for entrada in leer()[0]:
            if entrada["job_id"] not in informe["faltan"]:
                continue
            fila = db.execute("SELECT state FROM trabajos_work WHERE job_id=?", (entrada["job_id"],)).fetchone()
            if fila is None:
                db.execute("""INSERT INTO trabajos_work(job_id,account,thread_id,message_id,job_type,contract_version,
                    state,message_ref_json,ref_sha256,last_error,created_at,updated_at)
                    VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",
                           (entrada["job_id"], entrada["account"], entrada["thread_id"], entrada["message_id"],
                            entrada["job_type"], entrada["contract_version"], CANCELADO, "{}", "",
                            "cancelado:reaplicada", ahora, ahora))
            else:
                cambio = db.execute("UPDATE trabajos_work SET state=?,last_error=?,updated_at=? "
                                    "WHERE job_id=? AND state=?",
                                    (CANCELADO, "cancelado:reaplicada", ahora, entrada["job_id"],
                                     PENDIENTE_DE_PUBLICAR))
                if cambio.rowcount != 1:
                    continue
            db.execute("INSERT INTO recepcion_eventos(account,thread_id,message_id,event_kind,occurred_at,detail_json) "
                       "VALUES(?,?,?,?,?,?)",
                       (entrada["account"], entrada["thread_id"], entrada["message_id"], EVENTO_DE_REAPLICACION, ahora,
                        json.dumps({"job_id": entrada["job_id"], "operador": entrada.get("operador", ""),
                                    "motivo": entrada.get("motivo", ""),
                                    "cancelado_at": entrada.get("cancelado_at", "")},
                                   ensure_ascii=False, separators=(",", ":"), sort_keys=True)))
            reaplicadas.append(entrada["job_id"])
        db.execute("COMMIT")
    except BaseException:
        db.execute("ROLLBACK")
        raise
    return {**informe, "reaplicadas": reaplicadas}
