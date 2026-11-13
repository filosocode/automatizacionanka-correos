"""Lo registrado sobre la recepción de mensajes y los trabajos Work, para el panel «Recepción y trabajos» (T-61).

Solo lee las tablas de siempre (`recepcion_mensajes`, `recepcion_control`, `trabajos_work`, `quotes`) con consultas
acotadas: no escribe, no consulta Gmail ni Drive y no muestra cuerpos, remitentes, asuntos ni credenciales. Lo que dice
es lo que SQLite tiene registrado; que un servicio esté funcionando ahora no se deduce de ahí (solo el puente tiene una
señal de vida propia, su latido).
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from anka.dominio import trabajo_work as tw
from anka.infra.store import lectura
from anka.infra.work_heartbeat import bridge_state

LIMITE_DE_FILAS = 25          # filas por lista: es un vistazo, no un listado completo (los conteos sí son exactos)
LARGO_DE_ERROR = 200

# Cómo se ve cada estado registrado: (texto, gravedad). `pendiente` = falta que algo ocurra; `error` = ya falló.
ESTADOS_DE_MENSAJE: dict[str, tuple[str, str]] = {
    "por_leer": ("Por leer", "pendiente"),
    "requiere_reintento": ("Requiere reintento", "error"),
    "revision_manual": ("Revisión manual", "error"),
    "procesado": ("Registrado y clasificado", "ok"),
}
ESTADOS_DE_TRABAJO: dict[str, tuple[str, str]] = {
    tw.PENDIENTE_DE_PUBLICAR: ("Pendiente de publicar", "pendiente"),
    tw.PUBLICANDO: ("Publicándose", "pendiente"),
    tw.PUBLICADO: ("Publicado, a la espera del análisis de Work", "pendiente"),
    tw.CONCILIADO: ("Conciliado (el análisis ya llegó)", "ok"),
    tw.AMBIGUO: ("Ambiguo: hay que revisarlo", "error"),
    tw.CANCELADO: ("Cancelado", "neutro"),
}
_ORDEN_DE_TRABAJO = {estado: n for n, estado in enumerate(
    (tw.AMBIGUO, tw.PENDIENTE_DE_PUBLICAR, tw.PUBLICANDO, tw.PUBLICADO, tw.CONCILIADO, tw.CANCELADO))}


def _recortar(texto: object, largo: int = LARGO_DE_ERROR) -> str:
    limpio = " ".join(str(texto or "").split())
    return limpio if len(limpio) <= largo else limpio[:largo - 1] + "…"


def _fecha(valor: object) -> str:
    return str(valor or "").strip()[:16].replace("T", " ")


def _conteos(filas: Sequence[Any], conocidos: dict[str, tuple[str, str]]) -> list[dict[str, Any]]:
    """Un renglón por estado que existe, con su conteo; los estados no conocidos se muestran con su código tal cual."""
    total = {str(f["estado"]): int(f["n"]) for f in filas}
    ordenados = [e for e in conocidos if e in total] + sorted(e for e in total if e not in conocidos)
    return [{"estado": e, "texto": conocidos.get(e, (e or "(sin estado)", "desconocido"))[0],
             "gravedad": conocidos.get(e, ("", "desconocido"))[1], "n": total[e]} for e in ordenados]


def _expedientes(db: Any, hilos: Sequence[str]) -> set[str]:
    hilos = sorted(set(hilos))
    if not hilos:
        return set()
    marcas = ",".join("?" * len(hilos))
    return {r["thread_id"] for r in db.execute(f"SELECT thread_id FROM quotes WHERE thread_id IN ({marcas})", hilos)}


def _control(db: Any) -> list[dict[str, Any]]:
    return [{"ultimo_exito": _fecha(r["last_success_at"]), "ultimo_intento": _fecha(r["last_attempt_at"]),
             "errores_seguidos": int(r["consecutive_errors"] or 0),
             "tipo_de_error": _recortar(r["last_error_kind"], 80),
             "recuperacion": _recortar(r["recovery_state"], 40)}
            for r in db.execute("SELECT last_success_at, last_attempt_at, consecutive_errors, last_error_kind, "
                                "recovery_state FROM recepcion_control ORDER BY account LIMIT 5")]


def estado_operativo() -> dict[str, Any]:
    """El panel completo: conteos exactos por estado y listas cortas de lo que necesita atención."""
    with lectura() as db:
        mensajes = _conteos(db.execute("SELECT state AS estado, COUNT(*) AS n FROM recepcion_mensajes "
                                       "GROUP BY state").fetchall(), ESTADOS_DE_MENSAJE)
        clasificados = [{"texto": r["texto"] or "(sin clasificar)", "n": r["n"]} for r in db.execute(
            "SELECT disposition AS texto, COUNT(*) AS n FROM recepcion_mensajes WHERE state='procesado' "
            "GROUP BY disposition ORDER BY n DESC, disposition LIMIT 12")]
        fechas = db.execute("SELECT MAX(detected_at) AS detectado, MAX(processed_at) AS procesado "
                            "FROM recepcion_mensajes").fetchone()
        atencion = [dict(r) for r in db.execute(
            "SELECT thread_id, message_id, state, disposition, error_kind, attempts, detected_at, processed_at "
            "FROM recepcion_mensajes WHERE state != 'procesado' ORDER BY detected_at DESC LIMIT ?", (LIMITE_DE_FILAS,))]
        trabajos = _conteos(db.execute("SELECT state AS estado, COUNT(*) AS n FROM trabajos_work "
                                       "GROUP BY state").fetchall(), ESTADOS_DE_TRABAJO)
        con_error = db.execute("SELECT COUNT(*) FROM trabajos_work WHERE last_error != '' "
                               "AND state NOT IN (?, ?)", (tw.CONCILIADO, tw.CANCELADO)).fetchone()[0]
        filas = [dict(r) for r in db.execute(
            "SELECT job_id, thread_id, state, attempts, last_error, created_at, published_at, conciliated_at, "
            "updated_at "
            "FROM trabajos_work ORDER BY CASE state WHEN ? THEN 0 WHEN ? THEN 1 WHEN ? THEN 2 WHEN ? THEN 3 "
            "WHEN ? THEN 4 ELSE 5 END, updated_at DESC LIMIT ?",
            (tw.AMBIGUO, tw.PENDIENTE_DE_PUBLICAR, tw.PUBLICANDO, tw.PUBLICADO, tw.CONCILIADO, LIMITE_DE_FILAS))]
        existentes = _expedientes(db, [r["thread_id"] for r in atencion + filas])
        control = _control(db)
        ultimo_trabajo = db.execute("SELECT MAX(updated_at) FROM trabajos_work").fetchone()[0]
    for r in atencion:
        texto, gravedad = ESTADOS_DE_MENSAJE.get(r["state"], (r["state"] or "(sin estado)", "desconocido"))
        r.update(texto=texto, gravedad=gravedad, detalle=_recortar(r.pop("error_kind") or r.pop("disposition"), 80),
                 fecha=_fecha(r["detected_at"]), expediente=r["thread_id"] in existentes)
    for r in filas:
        texto, gravedad = ESTADOS_DE_TRABAJO.get(r["state"], (r["state"] or "(sin estado)", "desconocido"))
        clave = {tw.CONCILIADO: "conciliated_at", tw.PUBLICADO: "published_at"}.get(r["state"], "created_at")
        r.update(texto=texto, gravedad=gravedad, error=_recortar(r["last_error"]),
                 expediente=r["thread_id"] in existentes,
                 fecha=_fecha(r[clave] or r["updated_at"]), fecha_de=_ETIQUETA_DE_FECHA[clave])
    return {
        "mensajes": mensajes, "total_de_mensajes": sum(c["n"] for c in mensajes), "clasificados": clasificados,
        "ultimo_detectado": _fecha(fechas["detectado"]), "ultimo_procesado": _fecha(fechas["procesado"]),
        "atencion": atencion, "control": control,
        "trabajos": trabajos, "total_de_trabajos": sum(c["n"] for c in trabajos), "con_error": con_error,
        "lista_de_trabajos": filas, "ultimo_trabajo": _fecha(ultimo_trabajo), "limite": LIMITE_DE_FILAS,
        "puente": puente()}


_ETIQUETA_DE_FECHA = {"conciliated_at": "conciliado", "published_at": "publicado", "created_at": "creado"}


def puente() -> dict[str, str]:
    """Lo único con señal de vida propia: el latido del puente en disco. Nada más se da por «funcionando»."""
    estado, texto = bridge_state()
    return {"estado": estado, "texto": texto}
