"""Auditoría de solo lectura de las cancelaciones (T-93A): distingue las autorizadas de las inesperadas.

Cruza tres fuentes que deben coincidir: los trabajos `cancelado` de SQLite, los eventos `trabajo_cancelado` y el diario de
lápidas (`cancelaciones.jsonl`), y las compara con el registro de cancelaciones AUTORIZADAS (`cancelaciones_autorizadas.json`).
Falla si hay una cancelación sin diario, un diario sin trabajo, una cancelación no registrada como autorizada, una
cancelación autorizada que falta o difiere, o un trabajo cancelado que después se publicó (hoja, evento o archivo en Drive).

Uso (desde la raíz del repositorio): python -m laboratorio.banco_documental.respaldo_t83.auditoria_cancelaciones [--sin-drive]
Nunca escribe: SQLite se abre en solo lectura y Drive solo se lista.
"""

from __future__ import annotations

import json
import os
import sqlite3
import sys
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any

REGISTRO = Path(__file__).resolve().parent / "cancelaciones_autorizadas.json"
EVENTOS_DE_PUBLICACION = ("trabajo_publicado", "trabajo_republicado", "trabajo_conciliado", "trabajo_archivado")


def _fuentes(db: sqlite3.Connection, diario: Sequence[dict[str, Any]]) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any], list[str]]:
    """Los trabajos cancelados, sus eventos y las entradas del diario, por job_id; y las repeticiones que ya son un problema."""
    problemas: list[str] = []
    trabajos = {r[0]: {"thread_id": r[1], "message_id": r[2], "hoja": r[3] or ""} for r in db.execute(
        "SELECT job_id,thread_id,message_id,sheet_file_id FROM trabajos_work WHERE state='cancelado'")}
    eventos: dict[str, dict[str, Any]] = {}
    for hilo, mensaje, detalle in db.execute(
            "SELECT thread_id,message_id,detail_json FROM recepcion_eventos WHERE event_kind='trabajo_cancelado'"):
        datos = json.loads(detalle or "{}")
        if datos.get("job_id") in eventos:
            problemas.append(f"{datos.get('job_id')}: más de un evento de cancelación")
        eventos[datos.get("job_id", "?")] = {"thread_id": hilo, "message_id": mensaje, **datos}
    en_diario: dict[str, dict[str, Any]] = {}
    for entrada in diario:
        if entrada.get("job_id") in en_diario:
            problemas.append(f"{entrada.get('job_id')}: repetida en el diario")
        en_diario[entrada.get("job_id", "?")] = entrada
    return trabajos, eventos, en_diario, problemas


def _contra_lo_autorizado(job_id: str, esperada: dict[str, Any], trabajo: dict[str, Any], evento: dict[str, Any] | None,
                          entrada: dict[str, Any] | None) -> list[str]:
    """Identificadores, etiqueta, operador y motivo de cada fuente frente a lo autorizado."""
    problemas = []
    for fuente, datos in (("SQLite", trabajo), ("evento", evento), ("diario", entrada)):
        if datos is None:
            continue
        for clave in ("thread_id", "message_id"):
            if datos.get(clave) != esperada[clave]:
                problemas.append(f"{job_id}: {clave} del {fuente} difiere del autorizado")
    for fuente, datos in (("evento", evento), ("diario", entrada)):
        if datos is None:
            continue
        for clave in ("etiqueta", "operador", "motivo"):
            if datos.get(clave) != esperada[clave]:
                problemas.append(f"{job_id}: {clave} del {fuente} difiere del autorizado")
    return problemas


def auditar(db: sqlite3.Connection, diario: Sequence[dict[str, Any]], lineas_rotas: int, autorizadas: Sequence[dict[str, Any]],
            hojas_de: Callable[[str], Sequence[Any]] | None = None, exigir_presentes: bool = True) -> dict[str, Any]:
    """Devuelve `{"ok", "cancelados", "problemas"}`; `problemas` vacío = todo explicado y consistente."""
    trabajos, eventos, en_diario, problemas = _fuentes(db, diario)
    if lineas_rotas:
        problemas.append(f"el diario tiene {lineas_rotas} línea(s) ilegible(s)")
    permitidas = {a["job_id"]: a for a in autorizadas}
    for job_id in sorted(set(trabajos) | set(eventos) | set(en_diario)):
        if job_id not in trabajos:
            problemas.append(f"{job_id}: figura en el diario o en eventos pero no hay trabajo cancelado en SQLite")
            continue
        if job_id not in en_diario:
            problemas.append(f"{job_id}: cancelado en SQLite sin entrada en el diario de lápidas")
        if job_id not in eventos:
            problemas.append(f"{job_id}: cancelado en SQLite sin evento trabajo_cancelado")
        if job_id not in permitidas:
            problemas.append(f"{job_id}: cancelación NO autorizada (no figura en el registro)")
            continue
        problemas += _contra_lo_autorizado(job_id, permitidas[job_id], trabajos[job_id], eventos.get(job_id), en_diario.get(job_id))
        problemas += _publicacion_posterior(db, job_id, trabajos[job_id], permitidas[job_id], hojas_de)
    if exigir_presentes:
        problemas += [f"{j}: falta la cancelación autorizada ({a.get('origen', '?')})" for j, a in permitidas.items() if j not in trabajos]
    return {"ok": not problemas, "cancelados": len(trabajos), "problemas": problemas}


def _publicacion_posterior(db: sqlite3.Connection, job_id: str, trabajo: dict[str, Any], esperada: dict[str, Any],
                           hojas_de: Callable[[str], Sequence[Any]] | None) -> list[str]:
    """Un trabajo cancelado no debe tener hoja, eventos de publicación ni archivos en Drive."""
    problemas = []
    if trabajo["hoja"]:
        problemas.append(f"{job_id}: cancelado pero SQLite guarda una hoja de Drive")
    marcas = ",".join("?" * len(EVENTOS_DE_PUBLICACION))
    publicados = db.execute(f"SELECT event_kind FROM recepcion_eventos WHERE thread_id=? AND message_id=? "
                            f"AND event_kind IN ({marcas})", (trabajo["thread_id"], trabajo["message_id"], *EVENTOS_DE_PUBLICACION)).fetchall()
    if publicados:
        problemas.append(f"{job_id}: cancelado pero tiene eventos de publicación: {sorted({p[0] for p in publicados})}")
    if esperada.get("estado_previo") == "sin_trabajo":
        creado = db.execute("SELECT COUNT(*) FROM recepcion_eventos WHERE thread_id=? AND message_id=? AND event_kind='trabajo_creado'",
                            (trabajo["thread_id"], trabajo["message_id"])).fetchone()[0]
        if creado:
            problemas.append(f"{job_id}: se canceló sin trabajo previo pero hay un evento trabajo_creado")
    if hojas_de is not None:
        try:
            hojas = list(hojas_de(job_id))
        except Exception as exc:
            problemas.append(f"{job_id}: no se pudo comprobar Drive ({type(exc).__name__})")
        else:
            if hojas:
                problemas.append(f"{job_id}: cancelado pero hay {len(hojas)} hoja(s) suya(s) en PENDIENTES/CERRADOS")
    return problemas


def main(argv: Sequence[str] | None = None) -> int:
    """Audita la base real en solo lectura e imprime el resultado como JSON (una línea); sale con 1 si hay problemas."""
    argumentos = list(sys.argv[1:] if argv is None else argv)
    sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
    from anka.infra import lapidas
    from laboratorio.banco_documental.ejecucion_c1 import c1
    hojas_de: Callable[[str], Sequence[Any]] | None = None
    if "--sin-drive" not in argumentos:
        _gmail, drive = c1._servicios()
        hojas_de = lambda job: c1.hojas_verificadas(drive, job)  # noqa: E731
    base = Path(os.environ.get("ANKA_DATA_DIR") or Path(os.environ["LOCALAPPDATA"]) / "AnkaCorreos") / "quotes.sqlite3"
    db = sqlite3.connect(f"file:{base.as_posix()}?mode=ro", uri=True, timeout=5)
    entradas, rotas = lapidas.leer()
    resultado = auditar(db, entradas, rotas, json.loads(REGISTRO.read_text(encoding="utf-8")), hojas_de)
    print(json.dumps(resultado, ensure_ascii=False))
    return 0 if resultado["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
