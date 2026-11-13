"""Cola de trabajos Python → Work: un trabajo por mensaje y una hoja `.xlsx` de Drive por trabajo (T-49, D-153).

SQLite (`trabajos_work`) es la autoridad. La publicación en Drive no confía en buscar por nombre: reclama el trabajo,
marca el archivo con `appProperties.anka_job_id`, adopta antes de crear, verifica después de crear y, si hay más de un
archivo, marca el trabajo `ambiguo` sin borrar ni elegir. Nada de aquí corre solo: lo invoca una persona.
"""

from __future__ import annotations

import contextlib
import json
import secrets
import sqlite3
import time
from collections.abc import Callable, Iterator, Sequence
from datetime import datetime
from typing import Any

from anka.dominio.coordinacion_work import instante
from anka.dominio.errores import ErrorDeAnka
from anka.dominio.reloj import ahora_iso
from anka.dominio.caso import STATUS_LABELS
from anka.dominio.clasificacion import SEGUIMIENTO, Clasificacion, clasificar_preliminar
from anka.dominio.contexto_previo import Contexto, contexto_de_seguimiento
from anka.dominio.trabajo_work import (
    AMBIGUO, CLAVE_DE_MARCA, CONCILIABLES, CONCILIADO, EXCLUIDO, PENDIENTE_DE_PUBLICAR, PUBLICADO, PUBLICANDO,
    TIPO_ANALIZAR, TIPOS, VERSION_DEL_CONTRATO, Veredicto, construir_libro_del_trabajo, evaluar_elegibilidad,
    huella_de_la_referencia, id_de_trabajo, nombre_de_archivo, referencia_del_mensaje,
)
from anka.infra.clientes_google import ClienteDrive
from anka.infra import settings
from anka.infra.logging_setup import get_logger
from anka.infra.store import lectura, transaccion
from anka.servicios.recepcion import CUENTA, PENDIENTE

log = get_logger(__name__)

SHEET_MIME = "application/vnd.google-apps.spreadsheet"
XLSX_MIME = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
VIGENCIA_DEL_RECLAMO = 120.0      # segundos: un proceso caído suelta el trabajo pasado este tiempo


class TrabajoRechazado(ErrorDeAnka):
    """No se puede crear o publicar el trabajo pedido (sin fila de recepción, ya interpretado, estado no publicable)."""


class NoElegible(TrabajoRechazado):
    """El mensaje no es una solicitud para analizar (ruido) o no se sabe todavía (T-51); lleva el veredicto."""

    def __init__(self, veredicto: Veredicto) -> None:
        self.veredicto = veredicto
        etapa = "queda con la clasificación pendiente" if veredicto.estado != EXCLUIDO else "no es una solicitud"
        super().__init__(f"El mensaje {etapa}: {veredicto.motivo} ({veredicto.motivo_id}). "
                         "Una persona puede forzarlo con --forzar-elegibilidad.")


def _evento(db: sqlite3.Connection, trabajo: sqlite3.Row | dict, kind: str, when: str, detalle: dict[str, Any]) -> None:
    """Un evento por trabajo y tipo en `recepcion_eventos`: repetir la operación no lo duplica."""
    existe = db.execute("SELECT 1 FROM recepcion_eventos WHERE account=? AND thread_id=? AND message_id=? "
                        "AND event_kind=? AND json_extract(detail_json,'$.job_id')=? LIMIT 1",
                        (trabajo["account"], trabajo["thread_id"], trabajo["message_id"], kind,
                         trabajo["job_id"])).fetchone()
    if not existe:
        db.execute("INSERT INTO recepcion_eventos(account,thread_id,message_id,event_kind,occurred_at,detail_json) "
                   "VALUES(?,?,?,?,?,?)", (trabajo["account"], trabajo["thread_id"], trabajo["message_id"], kind, when,
                                           json.dumps({"job_id": trabajo["job_id"], **detalle},
                                                      separators=(",", ":"), sort_keys=True)))


def _terminos_comerciales() -> list[str] | None:
    """Los términos de R1 de la configuración; `None` si no se pueden leer (entonces nada automático se descarta)."""
    try:
        return list(settings.load_config().get("commercial_filter_terms", []))
    except ErrorDeAnka:
        return None


def _evento_de_mensaje(db: sqlite3.Connection, fila: sqlite3.Row, kind: str, when: str,
                      detalle: dict[str, Any]) -> None:
    """Un evento por mensaje y tipo (aún no hay trabajo): repetir el rechazo no lo duplica."""
    existe = db.execute("SELECT 1 FROM recepcion_eventos WHERE account=? AND thread_id=? AND message_id=? "
                        "AND event_kind=? LIMIT 1", (fila["account"], fila["thread_id"], fila["message_id"], kind)
                        ).fetchone()
    if not existe:
        db.execute("INSERT INTO recepcion_eventos(account,thread_id,message_id,event_kind,occurred_at,detail_json) "
                   "VALUES(?,?,?,?,?,?)", (fila["account"], fila["thread_id"], fila["message_id"], kind, when,
                                           json.dumps(detalle, separators=(",", ":"), sort_keys=True)))


def _forzada(db: sqlite3.Connection, thread_id: str, message_id: str) -> bool:
    return db.execute("SELECT 1 FROM recepcion_eventos WHERE account=? AND thread_id=? AND message_id=? "
                      "AND event_kind='trabajo_elegibilidad_forzada' LIMIT 1",
                      (CUENTA, thread_id, message_id)).fetchone() is not None


def veredicto_del_mensaje(db: sqlite3.Connection, fila: sqlite3.Row | dict[str, Any]) -> Veredicto:
    """La elegibilidad de la fila de recepción (T-51): una persona puede haberla forzado."""
    if _forzada(db, fila["thread_id"], fila["message_id"]):
        return Veredicto("elegible", "forzada", "una persona confirmó que es una solicitud")
    return evaluar_elegibilidad(dict(fila), _terminos_comerciales())


def clasificacion_del_mensaje(db: sqlite3.Connection, fila: sqlite3.Row | dict[str, Any]) -> Clasificacion:
    """La clasificación comercial preliminar del mensaje (T-52): T-51, expediente del hilo y servicios mencionados."""
    expediente = db.execute("SELECT 1 FROM quotes WHERE thread_id=? LIMIT 1", (fila["thread_id"],)).fetchone()
    return clasificar_preliminar(dict(fila), veredicto_del_mensaje(db, fila), expediente is not None,
                                 _terminos_comerciales() or [])


def _registrar_clasificacion(db: sqlite3.Connection, fila: sqlite3.Row, when: str) -> Clasificacion:
    """Anota la clasificación preliminar (sin asunto ni cuerpo); solo añade un evento si cambió la clase o el motivo."""
    clasificacion = clasificacion_del_mensaje(db, fila)
    detalle = json.dumps({"clase": clasificacion.clase, "motivo": clasificacion.motivo,
                          "enviar_a_work": clasificacion.enviar_a_work}, separators=(",", ":"), sort_keys=True)
    ultimo = db.execute("SELECT detail_json FROM recepcion_eventos WHERE account=? AND thread_id=? AND message_id=? "
                        "AND event_kind='clasificacion_preliminar' ORDER BY id DESC LIMIT 1",
                        (fila["account"], fila["thread_id"], fila["message_id"])).fetchone()
    if ultimo is None or ultimo["detail_json"] != detalle:
        db.execute("INSERT INTO recepcion_eventos(account,thread_id,message_id,event_kind,occurred_at,detail_json) "
                   "VALUES(?,?,?,?,?,?)", (fila["account"], fila["thread_id"], fila["message_id"],
                                           "clasificacion_preliminar", when, detalle))
    return clasificacion


def _rechazar_si_no_es_elegible(db: sqlite3.Connection, fila: sqlite3.Row, etapa: str, when: str) -> Veredicto | None:
    """Si el mensaje no es elegible anota el motivo una sola vez (R3) y devuelve el veredicto; si lo es, `None`."""
    veredicto = veredicto_del_mensaje(db, fila)
    if veredicto.elegible:
        return None
    _registrar_clasificacion(db, fila, when)
    kind = "trabajo_excluido" if veredicto.estado == EXCLUIDO else "trabajo_clasificacion_pendiente"
    _evento_de_mensaje(db, fila, kind, when, {"motivo": veredicto.motivo_id, "etapa": etapa})
    return veredicto


def _como_dict(fila: sqlite3.Row | None) -> dict[str, Any] | None:
    if fila is None:
        return None
    trabajo = dict(fila)
    trabajo["referencia"] = json.loads(trabajo.get("message_ref_json") or "{}")
    return trabajo


def obtener(job_id: str) -> dict[str, Any] | None:
    """El trabajo con ese `job_id`, o `None`."""
    with lectura() as db:
        return _como_dict(db.execute("SELECT * FROM trabajos_work WHERE job_id=?", (job_id,)).fetchone())


def trabajos_del_mensaje(thread_id: str, message_id: str) -> list[dict[str, Any]]:
    """Los trabajos de ese mensaje exacto (no los de otros mensajes del hilo)."""
    with lectura() as db:
        return [t for t in (_como_dict(f) for f in db.execute(
            "SELECT * FROM trabajos_work WHERE account=? AND thread_id=? AND message_id=? ORDER BY created_at,job_id",
            (CUENTA, thread_id, message_id))) if t]


def crear_trabajo(thread_id: str, message_id: str, job_type: str = TIPO_ANALIZAR,
                  contract_version: str = VERSION_DEL_CONTRATO, ahora: str | None = None,
                  paquete_version: str = "") -> dict[str, Any]:
    """Crea (o devuelve) el trabajo de un mensaje que sigue pendiente en la cola de recepción. No inventa filas.

    `paquete_version` es la del paquete contractual vigente (T-59): queda en el evento de creación y la hoja la muestra.
    """
    thread_id, message_id = str(thread_id or "").strip(), str(message_id or "").strip()
    if not thread_id or not message_id:
        raise TrabajoRechazado("Falta el thread_id o el message_id.")
    if job_type not in TIPOS:
        raise TrabajoRechazado(f"Tipo de trabajo desconocido: {job_type}.")
    when = ahora or ahora_iso("seconds")
    rechazo: Veredicto | None = None
    with transaccion() as db:
        fila = db.execute("SELECT * FROM recepcion_mensajes WHERE account=? AND thread_id=? AND message_id=?",
                          (CUENTA, thread_id, message_id)).fetchone()
        if fila is None:
            raise TrabajoRechazado("El mensaje no está en la cola de recepción; no se inventa una fila.")
        job_id = id_de_trabajo(CUENTA, thread_id, message_id, job_type, contract_version)
        existente = db.execute("SELECT * FROM trabajos_work WHERE job_id=?", (job_id,)).fetchone()
        if existente is not None:
            return {**(_como_dict(existente) or {}), "creado": False}
        if (fila["state"], fila["disposition"]) != PENDIENTE:
            raise TrabajoRechazado(f"El mensaje no está pendiente de interpretación (estado {fila['state']}).")
        rechazo = _rechazar_si_no_es_elegible(db, fila, "crear", when)
        if rechazo is not None:
            resultado: dict[str, Any] = {}
        else:
            resultado = _insertar_trabajo(db, fila, job_id, job_type, contract_version, when, paquete_version)
    if rechazo is not None:                # fuera de la transacción: el evento del rechazo ya quedó confirmado
        raise NoElegible(rechazo)
    return resultado


def _insertar_trabajo(db: sqlite3.Connection, fila: sqlite3.Row, job_id: str, job_type: str, contract_version: str,
                      when: str, paquete_version: str = "") -> dict[str, Any]:
    """Crea la fila del trabajo y su evento (el mensaje ya se comprobó pendiente y elegible)."""
    referencia = referencia_del_mensaje(dict(fila))
    clasificacion = _registrar_clasificacion(db, fila, when)
    db.execute("""INSERT INTO trabajos_work(job_id,account,thread_id,message_id,job_type,contract_version,state,
        message_ref_json,ref_sha256,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
               (job_id, CUENTA, fila["thread_id"], fila["message_id"], job_type, contract_version,
                PENDIENTE_DE_PUBLICAR, json.dumps(referencia, ensure_ascii=False, sort_keys=True),
                huella_de_la_referencia(referencia), when, when))
    creado = db.execute("SELECT * FROM trabajos_work WHERE job_id=?", (job_id,)).fetchone()
    _evento(db, creado, "trabajo_creado", when, {"job_type": job_type, "contract_version": contract_version,
                                                 **({"package_version": paquete_version} if paquete_version else {})})
    return {**(_como_dict(creado) or {}), "creado": True, "clasificacion": clasificacion.clase,
            "motivo_de_clasificacion": clasificacion.motivo}


def forzar_elegibilidad(thread_id: str, message_id: str, ahora: str | None = None) -> dict[str, Any]:
    """Una persona confirma que este mensaje sí es una solicitud (R3): deja su evento y desde entonces es elegible."""
    when = ahora or ahora_iso("seconds")
    with transaccion() as db:
        fila = db.execute("SELECT * FROM recepcion_mensajes WHERE account=? AND thread_id=? AND message_id=?",
                          (CUENTA, str(thread_id).strip(), str(message_id).strip())).fetchone()
        if fila is None:
            raise TrabajoRechazado("El mensaje no está en la cola de recepción; no se inventa una fila.")
        if (fila["state"], fila["disposition"]) != PENDIENTE:
            raise TrabajoRechazado(f"El mensaje no está pendiente de interpretación (estado {fila['state']}).")
        previo = veredicto_del_mensaje(db, fila)
        _evento_de_mensaje(db, fila, "trabajo_elegibilidad_forzada", when, {"veredicto_previo": previo.motivo_id})
    return {"thread_id": thread_id, "message_id": message_id, "forzada": True}


def clasificar_pendientes() -> list[dict[str, Any]]:
    """El veredicto de cada mensaje pendiente de interpretación, solo en lectura y sin asuntos ni cuerpos."""
    resultado = []
    with lectura() as db:
        for fila in db.execute("SELECT * FROM recepcion_mensajes WHERE account=? AND state=? AND disposition=? "
                               "ORDER BY detected_at,thread_id,message_id", (CUENTA, *PENDIENTE)).fetchall():
            veredicto = veredicto_del_mensaje(db, fila)
            tiene = db.execute("SELECT 1 FROM trabajos_work WHERE account=? AND thread_id=? AND message_id=? LIMIT 1",
                               (CUENTA, fila["thread_id"], fila["message_id"])).fetchone() is not None
            clasificacion = clasificacion_del_mensaje(db, fila)
            resultado.append({"thread_id": fila["thread_id"], "message_id": fila["message_id"],
                              "veredicto": veredicto.estado, "motivo": veredicto.motivo_id, "tiene_trabajo": tiene,
                              "clasificacion": clasificacion.clase, "motivo_de_clasificacion": clasificacion.motivo})
    return resultado


@contextlib.contextmanager
def _conexion(db: sqlite3.Connection | None) -> Iterator[sqlite3.Connection]:
    """La conexión dada (p. ej. de solo lectura, que no se cierra aquí) o una de lectura normal."""
    if db is not None:
        yield db
    else:
        with lectura() as propia:
            yield propia


def _desde(texto: object, limite: datetime | None) -> bool:
    """¿Es la hora `texto` igual o posterior al corte? Sin corte, sí; una hora ilegible o sin zona, no (conservador)."""
    if limite is None:
        return True
    valor = instante(texto)
    return valor is not None and valor >= limite


def detectado_desde(texto: object, limite: datetime) -> bool:
    """¿Es la hora `texto` igual o posterior al corte? Una hora ilegible o sin zona cuenta como anterior."""
    return _desde(texto, limite)


def trabajos_por_estado(estados: Sequence[str], desde: datetime | None = None,
                        alcance: tuple[str, str] | None = None,
                        db: sqlite3.Connection | None = None) -> list[dict[str, Any]]:
    """Los trabajos de análisis del contrato vigente en esos estados, creados desde el corte, del más viejo al nuevo.

    `alcance` (hilo, mensaje) limita a un mensaje. Solo lectura; la usa la coordinación (T-55) para saber qué le toca.
    """
    marcas = ",".join("?" * len(estados))
    with _conexion(db) as conexion:
        filas = conexion.execute(f"SELECT * FROM trabajos_work WHERE account=? AND job_type=? AND contract_version=? "
                                 f"AND state IN ({marcas}) ORDER BY created_at,job_id",
                                 (CUENTA, TIPO_ANALIZAR, VERSION_DEL_CONTRATO, *estados)).fetchall()
    trabajos = [t for t in (_como_dict(f) for f in filas) if t and _desde(t["created_at"], desde)]
    if alcance is not None:
        trabajos = [t for t in trabajos if (t["thread_id"], t["message_id"]) == alcance]
    return trabajos


def mensajes_nuevos(desde: datetime, alcance: tuple[str, str] | None = None,
                    db: sqlite3.Connection | None = None) -> list[dict[str, Any]]:
    """Los mensajes pendientes de interpretación detectados desde el corte, sin trabajo y elegibles (T-51), el más viejo
    primero. Solo lectura y sin asuntos ni cuerpos. Lo anterior al corte (la cola histórica) nunca aparece."""
    resultado = []
    with _conexion(db) as conexion:
        for fila in conexion.execute("SELECT * FROM recepcion_mensajes WHERE account=? AND state=? AND disposition=? "
                                     "ORDER BY detected_at,thread_id,message_id", (CUENTA, *PENDIENTE)).fetchall():
            if not _desde(fila["detected_at"], desde):
                continue
            if alcance is not None and (fila["thread_id"], fila["message_id"]) != alcance:
                continue
            if conexion.execute("SELECT 1 FROM trabajos_work WHERE account=? AND thread_id=? AND message_id=? LIMIT 1",
                                (CUENTA, fila["thread_id"], fila["message_id"])).fetchone() is not None:
                continue
            if not veredicto_del_mensaje(conexion, fila).elegible:
                continue
            resultado.append({"thread_id": fila["thread_id"], "message_id": fila["message_id"],
                              "detected_at": fila["detected_at"]})
    return resultado


def anotar_evento(job_id: str, kind: str, detalle: dict[str, Any], ahora: str | None = None) -> bool:
    """Deja un evento de auditoría sobre un trabajo (sin datos del correo), uno por trabajo y tipo.

    `True` solo si el evento es nuevo; `False` si el trabajo no existe o ya lo tenía (así los avisos no se repiten).
    """
    when = ahora or ahora_iso("seconds")
    with transaccion() as db:
        fila = db.execute("SELECT * FROM trabajos_work WHERE job_id=?", (job_id,)).fetchone()
        if fila is None or db.execute(
                "SELECT 1 FROM recepcion_eventos WHERE account=? AND thread_id=? AND message_id=? AND event_kind=? "
                "AND json_extract(detail_json,'$.job_id')=? LIMIT 1",
                (fila["account"], fila["thread_id"], fila["message_id"], kind, job_id)).fetchone():
            return False
        _evento(db, fila, kind, when, detalle)
    return True


def libro_del_trabajo(job_id: str, paquete_version: str = "") -> bytes:
    """El `.xlsx` que se publicaría para ese trabajo (no escribe nada); `paquete_version` es la efectiva (D-175)."""
    trabajo = obtener(job_id)
    if trabajo is None:
        raise TrabajoRechazado("No existe ese trabajo.")
    return construir_libro_del_trabajo(_con_clasificacion(trabajo, paquete_version))


def _con_clasificacion(trabajo: dict[str, Any], paquete_efectivo: str = "") -> dict[str, Any]:
    """El trabajo con la clasificación preliminar vigente del mensaje, para la hoja de Work (no cambia su huella).

    `paquete_version` de la hoja es la efectiva (la del paquete con el que se publica, si se indica); la de creación
    sigue en el evento `trabajo_creado` y se muestra aparte cuando difiere (D-175). Solo es lectura.
    """
    with lectura() as db:
        fila = db.execute("SELECT * FROM recepcion_mensajes WHERE account=? AND thread_id=? AND message_id=?",
                          (CUENTA, trabajo["thread_id"], trabajo["message_id"])).fetchone()
        if fila is None:
            return trabajo
        clasificacion = clasificacion_del_mensaje(db, fila)
        creacion = db.execute("SELECT detail_json FROM recepcion_eventos WHERE account=? AND thread_id=? "
                              "AND message_id=? AND event_kind='trabajo_creado' "
                              "AND json_extract(detail_json,'$.job_id')=? LIMIT 1",
                              (CUENTA, trabajo["thread_id"], trabajo["message_id"], trabajo["job_id"])).fetchone()
        contexto = _contexto_previo(db, trabajo) if clasificacion.clase == SEGUIMIENTO else None
    paquete = ""
    if creacion is not None:
        try:
            paquete = str(json.loads(creacion["detail_json"]).get("package_version") or "")
        except ValueError:
            paquete = ""
    efectiva = paquete_efectivo.strip() if paquete else ""        # un trabajo sin versión de creación no gana fila
    return {**trabajo, "clasificacion_preliminar": clasificacion.clase, "motivo_preliminar": clasificacion.motivo,
            "contexto_previo": contexto, "paquete_version": efectiva or paquete,
            "paquete_version_de_creacion": paquete}


def _contexto_previo(db: sqlite3.Connection, trabajo: dict[str, Any]) -> Contexto | None:
    """El contexto del expediente de ESE hilo (T-59, Fase C), o `None` si no hay: nunca se inventa."""
    caso = db.execute("SELECT status, analysis_json, quote_code, last_inbound_id FROM quotes WHERE thread_id=?",
                      (trabajo["thread_id"],)).fetchone()
    if caso is None:
        return None
    try:
        analisis = json.loads(caso["analysis_json"] or "{}")
    except ValueError:
        analisis = {}
    return contexto_de_seguimiento(
        analisis if isinstance(analisis, dict) else {}, thread_id=trabajo["thread_id"],
        estado=STATUS_LABELS.get(caso["status"], str(caso["status"] or "")), codigo_t=str(caso["quote_code"] or ""),
        ultimo_mensaje=str(caso["last_inbound_id"] or ""), mensaje_nuevo=trabajo["message_id"])


# --- Publicación en Drive -------------------------------------------------------------------------------------------

def _escapar(valor: str) -> str:
    return valor.replace("\\", "\\\\").replace("'", "\\'")


def _con_marca(drive: ClienteDrive, folder_id: str, job_id: str) -> list[dict[str, Any]]:
    """Los archivos de la carpeta con la marca de este trabajo (no depende del nombre)."""
    consulta = (f"'{_escapar(folder_id)}' in parents and mimeType = '{SHEET_MIME}' and trashed = false and "
                f"appProperties has {{ key='{CLAVE_DE_MARCA}' and value='{_escapar(job_id)}' }}")
    pagina = drive.files().list(q=consulta, fields="files(id,name,modifiedTime,appProperties)", pageSize=10).execute()
    return list(pagina.get("files", []))


def _media(contenido: bytes) -> Any:
    from io import BytesIO

    from googleapiclient.http import MediaIoBaseUpload

    return MediaIoBaseUpload(BytesIO(contenido), mimetype=XLSX_MIME, resumable=False)


def _no_existe(exc: Exception) -> bool:
    return getattr(getattr(exc, "resp", None), "status", None) == 404


def _elegibilidad_antes_de_publicar(job_id: str, when: str) -> Veredicto | None:
    """Vuelve a comprobar la elegibilidad antes de tocar Drive (T-51): cubre trabajos creados antes de la regla."""
    with transaccion() as db:
        trabajo = db.execute("SELECT * FROM trabajos_work WHERE job_id=?", (job_id,)).fetchone()
        if trabajo is None or trabajo["state"] not in (PENDIENTE_DE_PUBLICAR, PUBLICANDO, PUBLICADO):
            return None                    # `_reclamar` explica por qué no se publica
        fila = db.execute("SELECT * FROM recepcion_mensajes WHERE account=? AND thread_id=? AND message_id=?",
                          (CUENTA, trabajo["thread_id"], trabajo["message_id"])).fetchone()
        return None if fila is None else _rechazar_si_no_es_elegible(db, fila, "publicar", when)


def _reclamar(job_id: str, ahora: float, when: str) -> tuple[str, dict[str, Any] | None]:
    """Toma el trabajo para publicarlo: solo una de varias llamadas simultáneas lo consigue."""
    token = secrets.token_hex(8)
    with transaccion() as db:
        fila = db.execute("SELECT * FROM trabajos_work WHERE job_id=?", (job_id,)).fetchone()
        if fila is None:
            raise TrabajoRechazado("No existe ese trabajo.")
        if fila["state"] in (CONCILIADO, AMBIGUO, "cancelado"):
            raise TrabajoRechazado(f"El trabajo está {fila['state']}; no se publica.")
        if fila["state"] == PUBLICANDO and (fila["claim_until"] or 0) > ahora:
            return "", None
        cambio = db.execute(
            "UPDATE trabajos_work SET state=?,claim_token=?,claim_until=?,attempts=attempts+1,updated_at=? "
            "WHERE job_id=? AND state=? AND COALESCE(claim_token,'')=? AND COALESCE(claim_until,0)=?",
            (PUBLICANDO, token, ahora + VIGENCIA_DEL_RECLAMO, when, job_id, fila["state"], fila["claim_token"],
             fila["claim_until"] or 0))
        if cambio.rowcount != 1:
            return "", None
        return token, _como_dict(db.execute("SELECT * FROM trabajos_work WHERE job_id=?", (job_id,)).fetchone())


def _evento_de_republicacion(db: sqlite3.Connection, trabajo: sqlite3.Row, when: str, detalle: dict[str, Any]) -> None:
    """Deja huella de cada hoja actualizada con otra versión de paquete; repetir la misma versión no duplica (D-175)."""
    existe = db.execute("SELECT 1 FROM recepcion_eventos WHERE account=? AND thread_id=? AND message_id=? "
                        "AND event_kind='trabajo_republicado' AND json_extract(detail_json,'$.job_id')=? "
                        "AND json_extract(detail_json,'$.package_version')=? LIMIT 1",
                        (trabajo["account"], trabajo["thread_id"], trabajo["message_id"], trabajo["job_id"],
                         detalle["package_version"])).fetchone()
    if not existe:
        db.execute("INSERT INTO recepcion_eventos(account,thread_id,message_id,event_kind,occurred_at,detail_json) "
                   "VALUES(?,?,?,?,?,?)", (trabajo["account"], trabajo["thread_id"], trabajo["message_id"],
                                           "trabajo_republicado", when,
                                           json.dumps({"job_id": trabajo["job_id"], **detalle},
                                                      separators=(",", ":"), sort_keys=True)))


def _soltar(job_id: str, token: str, when: str, error: str, nuevo: str | None = None) -> None:
    """Devuelve el trabajo a su estado de antes de publicar (o a `ambiguo`) y anota el motivo, sin datos del correo."""
    with transaccion() as db:
        fila = db.execute("SELECT * FROM trabajos_work WHERE job_id=?", (job_id,)).fetchone()
        if fila is None or fila["state"] != PUBLICANDO or fila["claim_token"] != token:
            return
        destino = nuevo or (PUBLICADO if fila["sheet_file_id"] else PENDIENTE_DE_PUBLICAR)
        db.execute("UPDATE trabajos_work SET state=?,claim_token='',claim_until=NULL,last_error=?,updated_at=? "
                   "WHERE job_id=?", (destino, error[:200], when, job_id))
        if destino == AMBIGUO:
            _evento(db, fila, "trabajo_ambiguo", when, {"motivo": error[:200]})


def publicar_trabajo(drive: ClienteDrive, job_id: str, folder_id: str,
                     reloj: Callable[[], float] = time.time, paquete_version: str = "",
                     solo_su_hoja: bool = False) -> dict[str, Any]:
    """Publica (o actualiza) la hoja del trabajo en la carpeta de laboratorio. Nunca elige entre archivos ambiguos.

    `paquete_version`: versión efectiva con la que se publica (queda en el evento; la de creación no se toca).
    `solo_su_hoja`: republicación de un trabajo ya publicado; solo actualiza su hoja guardada si sigue en Drive, fuera
    de la papelera y con la marca de este trabajo. Si no, no escribe ni crea nada (D-175).
    """
    if not str(folder_id or "").strip():
        raise TrabajoRechazado("Falta la carpeta de laboratorio de Drive.")
    when = ahora_iso("seconds")
    rechazo = _elegibilidad_antes_de_publicar(job_id, when)
    if rechazo is not None:
        return {"resultado": "no_elegible" if rechazo.estado == EXCLUIDO else "clasificacion_pendiente",
                "job_id": job_id, "motivo": rechazo.motivo_id}
    token, trabajo = _reclamar(job_id, reloj(), when)
    if trabajo is None:
        return {"resultado": "no_reclamado", "job_id": job_id}
    try:
        if solo_su_hoja and _archivo_guardado(drive, trabajo) is None:
            _soltar(job_id, token, when, "republicacion_sin_su_hoja")
            return {"resultado": "identidad_divergente", "job_id": job_id}
        contenido = construir_libro_del_trabajo(_con_clasificacion(trabajo, paquete_version))
        resultado, archivo = _publicar_en_drive(drive, trabajo, folder_id, contenido)
    except Exception as exc:                       # Drive o red: no se sabe qué quedó hecho; se verifica al reintentar
        _soltar(job_id, token, when, f"error_de_drive:{type(exc).__name__}")
        log.warning("No se pudo publicar el trabajo %s (%s)", job_id, type(exc).__name__)
        return {"resultado": "error", "job_id": job_id, "error": type(exc).__name__}
    if resultado == "ambiguo":
        with transaccion() as db:
            if archivo.get("id") and not db.execute("SELECT sheet_file_id FROM trabajos_work WHERE job_id=?",
                                                    (job_id,)).fetchone()["sheet_file_id"]:
                db.execute("UPDATE trabajos_work SET sheet_file_id=? WHERE job_id=?", (archivo["id"], job_id))
        _soltar(job_id, token, when, f"archivos_ambiguos:{','.join(archivo.get('candidatos', []))}"[:200], AMBIGUO)
        return {"resultado": "ambiguo", "job_id": job_id, "candidatos": archivo.get("candidatos", [])}
    with transaccion() as db:
        fila = db.execute("SELECT * FROM trabajos_work WHERE job_id=?", (job_id,)).fetchone()
        cambio = db.execute(
            "UPDATE trabajos_work SET state=?,sheet_file_id=?,folder_id=?,published_at=COALESCE(published_at,?),"
            "claim_token='',claim_until=NULL,last_error='',updated_at=? WHERE job_id=? AND state=? AND claim_token=?",
            (PUBLICADO, archivo["id"], folder_id, when, when, job_id, PUBLICANDO, token))
        if cambio.rowcount != 1:
            return {"resultado": "reclamo_perdido", "job_id": job_id, "sheet_file_id": archivo["id"]}
        detalle = {"sheet_file_id": archivo["id"], "como": resultado}
        if paquete_version:
            detalle["package_version"] = paquete_version
        _evento(db, fila, "trabajo_publicado", when, detalle)
        if resultado == "actualizado" and paquete_version:
            _evento_de_republicacion(db, fila, when, detalle)
    return {"resultado": resultado, "job_id": job_id, "sheet_file_id": archivo["id"]}


def _publicar_en_drive(drive: ClienteDrive, trabajo: dict[str, Any], folder_id: str,
                       contenido: bytes) -> tuple[str, dict[str, Any]]:
    """Devuelve `(resultado, archivo)`: `creado`, `actualizado`, `adoptado` o `ambiguo` (con los candidatos)."""
    job_id = trabajo["job_id"]
    propio = _archivo_guardado(drive, trabajo)
    if propio is not None:
        hecho = drive.files().update(fileId=propio, media_body=_media(contenido), fields="id,modifiedTime").execute()
        return "actualizado", {"id": hecho.get("id", propio)}
    encontrados = _con_marca(drive, folder_id, job_id)
    if len(encontrados) > 1:
        return "ambiguo", {"candidatos": sorted(f["id"] for f in encontrados)}
    if len(encontrados) == 1:                       # lo creó una publicación anterior cuya respuesta se perdió
        hecho = drive.files().update(fileId=encontrados[0]["id"], media_body=_media(contenido),
                                     fields="id,modifiedTime").execute()
        return "adoptado", {"id": hecho.get("id", encontrados[0]["id"])}
    creado = drive.files().create(
        body={"name": nombre_de_archivo(job_id), "parents": [folder_id], "mimeType": SHEET_MIME,
              "appProperties": {CLAVE_DE_MARCA: job_id}},
        media_body=_media(contenido), fields="id,modifiedTime").execute()
    despues = _con_marca(drive, folder_id, job_id)   # verificar el resultado: dos procesos pudieron crear a la vez
    ids = sorted(f["id"] for f in despues)
    if ids == [creado["id"]]:
        return "creado", {"id": creado["id"]}
    return "ambiguo", {"id": creado["id"], "candidatos": sorted(set(ids) | {creado["id"]})}


def _archivo_guardado(drive: ClienteDrive, trabajo: dict[str, Any]) -> str | None:
    """El archivo que el trabajo ya tiene, si sigue en Drive, fuera de la papelera y con la marca de este trabajo."""
    guardado = trabajo.get("sheet_file_id") or ""
    if not guardado:
        return None
    try:
        meta = drive.files().get(fileId=guardado, fields="id,trashed,appProperties").execute()
    except Exception as exc:
        if _no_existe(exc):
            return None
        raise
    marca = (meta.get("appProperties") or {}).get(CLAVE_DE_MARCA)
    if meta.get("trashed") or marca != trabajo["job_id"]:
        return None
    return str(meta.get("id", guardado))


# --- Retorno --------------------------------------------------------------------------------------------------------

def marcar_conciliado(db: sqlite3.Connection, thread_id: str, message_id: str, analisis_ref: str,
                      ahora: str | None = None) -> list[str]:
    """Cierra los trabajos de ESTE mensaje cuando Work ya lo interpretó (T-49, punto 8). Dentro de la transacción
    del importador. No toca trabajos de otros mensajes del hilo."""
    when = ahora or ahora_iso("seconds")
    cerrados: list[str] = []
    marcas = ",".join("?" * len(CONCILIABLES))
    for fila in db.execute(f"SELECT * FROM trabajos_work WHERE account=? AND thread_id=? AND message_id=? "
                           f"AND state IN ({marcas})", (CUENTA, thread_id, message_id, *CONCILIABLES)).fetchall():
        cambio = db.execute("UPDATE trabajos_work SET state=?,conciliated_at=?,claim_token='',claim_until=NULL,"
                            "updated_at=? WHERE job_id=? AND state=?", (CONCILIADO, when, when, fila["job_id"],
                                                                         fila["state"]))
        if cambio.rowcount == 1:
            _evento(db, fila, "trabajo_conciliado", when, {"analysis_ref": analisis_ref, "desde": fila["state"]})
            cerrados.append(fila["job_id"])
    return cerrados


def registrar_archivado(job_id: str, folder_id: str, ahora: str | None = None) -> bool:
    """Anota que la hoja de un trabajo conciliado quedó en la carpeta CERRADOS (T-53). Un solo evento por trabajo."""
    when = ahora or ahora_iso("seconds")
    with transaccion() as db:
        fila = db.execute("SELECT * FROM trabajos_work WHERE job_id=?", (job_id,)).fetchone()
        if fila is None or fila["state"] != CONCILIADO:
            return False
        if fila["folder_id"] == folder_id:
            return True                                 # ya estaba anotado: no se repite ni el evento ni la escritura
        db.execute("UPDATE trabajos_work SET folder_id=?,updated_at=? WHERE job_id=?", (folder_id, when, job_id))
        _evento(db, fila, "trabajo_archivado", when, {"carpeta": folder_id})
    return True


def hoja_guardada(drive: ClienteDrive, trabajo: dict[str, Any]) -> str | None:
    """El archivo del trabajo si sigue en Drive, fuera de la papelera y con su marca (T-53); `None` si no."""
    return _archivo_guardado(drive, trabajo)


__all__ = ["NoElegible", "TrabajoRechazado", "clasificacion_del_mensaje", "clasificar_pendientes",
           "crear_trabajo", "forzar_elegibilidad",
           "hoja_guardada", "libro_del_trabajo", "marcar_conciliado", "mensajes_nuevos", "obtener", "publicar_trabajo",
           "registrar_archivado", "trabajos_del_mensaje", "trabajos_por_estado", "anotar_evento", "detectado_desde",
           "veredicto_del_mensaje"]
