"""Despacho de trabajos a Work (T-53, D-157): una pasada única y repetible, sin procesos ni tareas periódicas.

Une lo que ya existe: la cola de recepción (U-81), la elegibilidad y la clasificación (T-51, T-52) y la cola de
trabajos (T-49). SQLite manda; las carpetas `PENDIENTES` y `CERRADOS` de Drive son una vista para Work.
"""

from __future__ import annotations

import time
from collections.abc import Callable, Mapping
from typing import Any, NamedTuple

from anka.dominio.errores import ErrorDeAnka
from anka.dominio.trabajo_work import (
    AMBIGUO, CLAVE_DE_MARCA, CONCILIADO, EXCLUIDO, PENDIENTE_DE_PUBLICAR, TIPO_ANALIZAR, VERSION_DEL_CONTRATO,
)
from anka.infra.clientes_google import ClienteDrive
from anka.infra.logging_setup import get_logger
from anka.servicios import trabajos_work
from anka.servicios.trabajos_work import NoElegible, TrabajoRechazado

log = get_logger(__name__)

NOMBRE_PENDIENTES = "PENDIENTES"
NOMBRE_CERRADOS = "CERRADOS"
CARPETA_MIME = "application/vnd.google-apps.folder"
MAXIMO_DE_INTENTOS = 5


class CarpetaAmbigua(ErrorDeAnka):
    """Hay más de una carpeta con el mismo nombre: no se elige, una persona decide."""


class Alcance(NamedTuple):
    """El único mensaje que puede despachar una pasada (primer piloto)."""

    thread_id: str
    message_id: str


def _escapar(valor: str) -> str:
    return valor.replace("\\", "\\\\").replace("'", "\\'")


def _buscar_carpetas(drive: ClienteDrive, raiz: str, nombre: str) -> list[dict[str, Any]]:
    consulta = (f"'{_escapar(raiz)}' in parents and mimeType = '{CARPETA_MIME}' and trashed = false and "
                f"name = '{_escapar(nombre)}'")
    pagina = drive.files().list(q=consulta, fields="files(id,name)", pageSize=10).execute()
    return list(pagina.get("files", []))


def _carpeta(drive: ClienteDrive, raiz: str, nombre: str) -> str:
    """El id de la subcarpeta `nombre` de la raíz: la busca, la crea si falta y verifica que quede una sola."""
    existentes = _buscar_carpetas(drive, raiz, nombre)
    if not existentes:
        drive.files().create(body={"name": nombre, "parents": [raiz], "mimeType": CARPETA_MIME},
                             fields="id").execute()
        existentes = _buscar_carpetas(drive, raiz, nombre)      # dos procesos pudieron crearla a la vez
    if len(existentes) != 1:
        raise CarpetaAmbigua(f"Hay {len(existentes)} carpetas «{nombre}» en la raíz del despacho; no se elige una. "
                             "Una persona debe dejar solo una.")
    return str(existentes[0]["id"])


def asegurar_carpeta(drive: ClienteDrive, raiz: str, nombre: str) -> str:
    """El id de la subcarpeta `nombre` de la raíz (la crea si falta; si hay más de una, se detiene)."""
    if not str(raiz or "").strip():
        raise ErrorDeAnka("Falta la carpeta raíz.")
    return _carpeta(drive, raiz, nombre)


def asegurar_carpetas(drive: ClienteDrive, raiz: str) -> dict[str, str]:
    """`{PENDIENTES: id, CERRADOS: id}` dentro de la raíz del laboratorio, sin duplicados."""
    if not str(raiz or "").strip():
        raise ErrorDeAnka("Falta la carpeta raíz del despacho.")
    return {nombre: _carpeta(drive, raiz, nombre) for nombre in (NOMBRE_PENDIENTES, NOMBRE_CERRADOS)}


def _archivar(drive: ClienteDrive, trabajo: dict[str, Any], cerrados: str) -> str:
    """Mueve la hoja de un trabajo conciliado a CERRADOS. Devuelve `archivado`, `ya_archivado` o por qué no."""
    hoja = trabajo.get("sheet_file_id") or ""
    if not hoja:
        return "sin_hoja"
    try:
        meta = drive.files().get(fileId=hoja, fields="id,trashed,appProperties,parents").execute()
    except Exception as exc:
        if getattr(getattr(exc, "resp", None), "status", None) == 404:
            return "hoja_ausente"
        raise
    if meta.get("trashed") or (meta.get("appProperties") or {}).get(CLAVE_DE_MARCA) != trabajo["job_id"]:
        return "hoja_sin_marca_o_en_papelera"
    padres = [str(p) for p in meta.get("parents") or []]
    if padres != [cerrados]:
        quitar = ",".join(p for p in padres if p != cerrados)
        pedido: dict[str, Any] = {"fileId": hoja, "fields": "id,parents"}
        if cerrados not in padres:
            pedido["addParents"] = cerrados
        if quitar:
            pedido["removeParents"] = quitar
        drive.files().update(**pedido).execute()
        resultado = "archivado"
    else:
        resultado = "ya_archivado"
    trabajos_work.registrar_archivado(trabajo["job_id"], cerrados)
    return resultado


def _despachar_mensaje(drive: ClienteDrive, alcance: Alcance, carpetas: dict[str, str],
                       reloj: Callable[[], float], paquete: str = "") -> dict[str, Any]:
    """Crea (si hace falta) y publica en PENDIENTES el trabajo del mensaje del alcance."""
    existentes = [t for t in trabajos_work.trabajos_del_mensaje(alcance.thread_id, alcance.message_id)
                  if t["job_type"] == TIPO_ANALIZAR and t["contract_version"] == VERSION_DEL_CONTRATO]
    trabajo = existentes[0] if existentes else None
    if trabajo is not None:
        if trabajo["state"] == CONCILIADO:
            return {"resultado": "ya_conciliado", "job_id": trabajo["job_id"]}
        if trabajo["state"] == AMBIGUO:
            return {"resultado": "ambiguo", "job_id": trabajo["job_id"]}
        if trabajo["state"] == PENDIENTE_DE_PUBLICAR and trabajo["attempts"] >= MAXIMO_DE_INTENTOS:
            return {"resultado": "agotado", "job_id": trabajo["job_id"], "intentos": trabajo["attempts"],
                    "ultimo_error": trabajo["last_error"]}
        if trabajo["state"] == "publicado" and trabajos_work.hoja_guardada(drive, trabajo) is not None:
            return {"resultado": "ya_publicado", "job_id": trabajo["job_id"]}
    else:
        try:
            trabajo = trabajos_work.crear_trabajo(alcance.thread_id, alcance.message_id, paquete_version=paquete)
        except NoElegible as rechazo:
            return {"resultado": "no_elegible" if rechazo.veredicto.estado == EXCLUIDO else "clasificacion_pendiente",
                    "motivo": rechazo.veredicto.motivo_id}
        except TrabajoRechazado:
            return {"resultado": "mensaje_no_pendiente"}
    publicacion = trabajos_work.publicar_trabajo(drive, trabajo["job_id"], carpetas[NOMBRE_PENDIENTES], reloj,
                                                 paquete_version=paquete)
    return {**publicacion, "clasificacion": trabajo.get("clasificacion", "")}


def despachar_mensaje(drive: ClienteDrive, alcance: Alcance, carpetas: dict[str, str],
                      reloj: Callable[[], float] = time.time, paquete: str = "") -> dict[str, Any]:
    """Crea (si hace falta) y publica en PENDIENTES el trabajo de un mensaje (lo usa la coordinación, T-55)."""
    return _despachar_mensaje(drive, alcance, carpetas, reloj, paquete)


def archivar_trabajo(drive: ClienteDrive, trabajo: dict[str, Any], cerrados: str) -> str:
    """Mueve la hoja de un trabajo conciliado a CERRADOS (idempotente); ver `_archivar`."""
    return _archivar(drive, trabajo, cerrados)


def despachar(drive: ClienteDrive, raiz: str, alcance: Alcance,
              reloj: Callable[[], float] = time.time, paquete: str = "") -> dict[str, Any]:
    """Una pasada del despacho para un solo mensaje: carpetas, archivado de lo conciliado y publicación pendiente.

    Repetirla no duplica nada. Un fallo de Drive queda anotado en el trabajo y se reintenta en la pasada siguiente.
    """
    if not (str(alcance.thread_id).strip() and str(alcance.message_id).strip()):
        raise ErrorDeAnka("El despacho exige un mensaje concreto (thread_id y message_id): "
                          "no existe un modo «toda la cola».")
    informe: dict[str, Any] = {"thread_id": alcance.thread_id, "message_id": alcance.message_id}
    carpetas = asegurar_carpetas(drive, raiz)
    informe["carpetas"] = carpetas
    archivados = []
    for trabajo in trabajos_work.trabajos_del_mensaje(alcance.thread_id, alcance.message_id):
        if trabajo["state"] == CONCILIADO:
            try:
                destino = carpetas[NOMBRE_CERRADOS]
                archivados.append({"job_id": trabajo["job_id"], "resultado": _archivar(drive, trabajo, destino)})
            except Exception as exc:               # Drive o red: se reintenta en la pasada siguiente
                log.warning("No se pudo archivar el trabajo %s (%s)", trabajo["job_id"], type(exc).__name__)
                archivados.append({"job_id": trabajo["job_id"], "resultado": "error", "error": type(exc).__name__})
    informe["archivados"] = archivados
    informe["publicacion"] = _despachar_mensaje(drive, alcance, carpetas, reloj, paquete)
    return informe


def alcance_automatico(config: Mapping[str, Any]) -> tuple[str, Alcance] | None:
    """`(raíz, alcance)` si el despacho automático está activado y completo; `None` si está apagado (por defecto)."""
    if not config.get("work_dispatch_enabled", False):
        return None
    return (str(config.get("work_dispatch_folder_id") or ""),
            Alcance(str(config.get("work_dispatch_thread_id") or ""),
                    str(config.get("work_dispatch_message_id") or "")))


__all__ = ["Alcance", "CarpetaAmbigua", "MAXIMO_DE_INTENTOS", "NOMBRE_CERRADOS", "NOMBRE_PENDIENTES",
           "alcance_automatico", "archivar_trabajo", "asegurar_carpeta", "asegurar_carpetas", "despachar",
           "despachar_mensaje"]
