"""La hoja de Google Sheets de cada caso: se crea en la carpeta del proyecto y se mantiene al día sola (spec T-17).

El dashboard es la fuente de verdad. La hoja es una vista que se reescribe completa cada vez que cambia la tabla,
actualizando **el mismo archivo** (así Google conserva el historial de versiones). El libro lleva, además de la tabla
vigente, la solicitud inicial y el historial (ver `anka.dominio.hoja`), de modo que nada del origen del caso se pierde.

Si alguien editó la hoja directamente en Drive, se nota (por su fecha de modificación): el cambio queda en el
historial de versiones de Google, se avisa en el dashboard y la hoja vuelve a reflejar el dashboard.
"""

from __future__ import annotations
from anka.infra.clientes_google import ClienteDrive

import os
import threading
import time
from collections.abc import Callable
from datetime import datetime
from io import BytesIO
from typing import Any, NamedTuple

from anka.dominio.errores import ErrorDeAnka
from anka.dominio.hoja import build_case_workbook
from anka.infra.google_services import services
from anka.infra.logging_setup import get_logger
from anka.servicios.drive_layout import clean_folder_name
from anka.servicios.repositorio import RepositorioDeCasos

log = get_logger(__name__)

SHEET_MIME = "application/vnd.google-apps.spreadsheet"
XLSX_MIME = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
ESPERA_ANTES_DE_ACTUALIZAR = 2.0      # segundos: junta varias ediciones seguidas en una sola actualización
TOLERANCIA_DE_EDICION = 60.0          # segundos: Drive termina de convertir la hoja un par de segundos después de
                                      # responder y mueve su fecha; un cambio mayor sí es una edición hecha en Drive


class ResultadoHoja(NamedTuple):
    """Qué pasó al sincronizar: `estado` es creada, actualizada, sin_carpeta o sin_caso."""

    estado: str
    mensaje: str
    url: str


class Publicacion(NamedTuple):
    """Lo que dejó la publicación en Drive."""

    id: str
    modificada: str
    creada: bool
    aviso: str


def nombre_de_hoja(proyecto: str) -> str:
    """Nombre del archivo en Drive («Tabla de actividades - Proyecto»)."""
    return clean_folder_name(f"Tabla de actividades - {proyecto or 'Solicitud'}")[:100]


def _editada_por_fuera(actual: str, guardada: str) -> bool:
    """¿Cambió la hoja después de que la dejamos? (con tolerancia: ver `TOLERANCIA_DE_EDICION`)."""
    if not guardada or not actual:
        return False
    try:
        diferencia = (datetime.fromisoformat(actual) - datetime.fromisoformat(guardada)).total_seconds()
    except ValueError:
        return actual != guardada
    return diferencia > TOLERANCIA_DE_EDICION


def _no_existe(exc: Exception) -> bool:
    return getattr(getattr(exc, "resp", None), "status", None) == 404


def _media(contenido: bytes) -> Any:
    from googleapiclient.http import MediaIoBaseUpload

    return MediaIoBaseUpload(BytesIO(contenido), mimetype=XLSX_MIME, resumable=False)


def _buscar_hoja(drive: ClienteDrive, folder_id: str, nombre: str) -> dict | None:
    """La hoja con ese nombre que ya esté en la carpeta (por si la creó otra versión del sistema o una persona)."""
    escapado = nombre.replace("\\", "\\\\").replace("'", "\\'")
    consulta = (f"'{folder_id}' in parents and name = '{escapado}' and mimeType = '{SHEET_MIME}' "
                "and trashed = false")
    archivos = drive.files().list(q=consulta, fields="files(id,name,modifiedTime)", pageSize=5).execute()
    encontrados = archivos.get("files", [])
    return encontrados[0] if encontrados else None


def publicar(drive: ClienteDrive, folder_id: str, nombre: str, contenido: bytes, sheet_id: str,
             modificada_guardada: str) -> Publicacion:
    """Crea la hoja o reescribe la existente (el mismo archivo). Avisa si alguien la editó directamente en Drive."""
    aviso = ""
    if sheet_id:
        try:
            meta = drive.files().get(fileId=sheet_id, fields="id,modifiedTime,trashed").execute()
        except Exception as exc:
            if not _no_existe(exc):
                raise
            meta = None
        if meta and not meta.get("trashed"):
            if _editada_por_fuera(meta.get("modifiedTime", ""), modificada_guardada):
                aviso = ("La hoja tenía cambios hechos directamente en Drive; quedaron en su historial de versiones "
                         "(Archivo > Historial de versiones) y ahora refleja el dashboard.")
            hecho = drive.files().update(fileId=sheet_id, media_body=_media(contenido),
                                         fields="id,modifiedTime").execute()
            return Publicacion(hecho["id"], hecho.get("modifiedTime", ""), False, aviso)
        aviso = "La hoja anterior ya no estaba en Drive (se borró); se creó una nueva."
    existente = _buscar_hoja(drive, folder_id, nombre)
    if existente:
        hecho = drive.files().update(fileId=existente["id"], media_body=_media(contenido),
                                     fields="id,modifiedTime").execute()
        return Publicacion(hecho["id"], hecho.get("modifiedTime", ""), False, aviso)
    hecho = drive.files().create(
        body={"name": nombre, "parents": [folder_id], "mimeType": SHEET_MIME}, media_body=_media(contenido),
        fields="id,modifiedTime").execute()
    return Publicacion(hecho["id"], hecho.get("modifiedTime", ""), True, aviso)


def sincronizar_hoja(
    thread_id: str, drive: ClienteDrive | None = None, repositorio: RepositorioDeCasos | None = None,
) -> ResultadoHoja:
    """Deja la hoja de Google del caso igual al dashboard (la crea si no existe). Lanza el error de Google si falla."""
    repo = repositorio or RepositorioDeCasos()
    caso = repo.obtener(thread_id)
    if caso is None:
        return ResultadoHoja("sin_caso", "Este caso ya no existe. Vuelve a Mis casos.", "")
    if not caso.folder_id:
        return ResultadoHoja("sin_carpeta", "La hoja de Google se crea en la carpeta del proyecto, que aún no existe "
                             "(se crea al emitir la T). Mientras tanto puedes descargar el Excel.", "")
    drive = drive or services()[1]
    contenido = build_case_workbook(
        caso, repo.versiones_de_tabla(thread_id), repo.primer_mensaje(thread_id),
        datetime.now().astimezone().strftime("%Y-%m-%d %H:%M"))
    proyecto = (caso.analysis or {}).get("project_name") or "Solicitud"
    resultado = publicar(drive, caso.folder_id, nombre_de_hoja(proyecto), contenido, caso.sheet_id, caso.sheet_modified)
    repo.guardar_hoja(thread_id, resultado.id, resultado.modificada)
    url = f"https://docs.google.com/spreadsheets/d/{resultado.id}"
    mensaje = ("Hoja de Google creada en la carpeta del proyecto." if resultado.creada
               else "Hoja de Google actualizada.")
    log.info("%s: %s", thread_id, mensaje)
    return ResultadoHoja("creada" if resultado.creada else "actualizada",
                         (mensaje + " " + resultado.aviso).strip(), url)


def texto_del_error(exc: Exception) -> str:
    """Explicación corta para la persona (sin el texto técnico de Google, que puede traer datos)."""
    if isinstance(exc, ErrorDeAnka):
        return str(exc)
    return f"No se pudo actualizar la hoja de Google ({type(exc).__name__}). Pulsa «Actualizar ahora» para reintentar."


class SincronizadorDeHojas:
    """Actualiza la hoja de un caso en segundo plano cada vez que cambia su tabla, sin frenar al dashboard.

    Junta ediciones seguidas (espera un momento y actualiza una sola vez) y nunca corre dos actualizaciones del mismo
    caso a la vez: si llega otro cambio mientras trabaja, repite al terminar. Un fallo no interrumpe a nadie: queda
    registrado en el caso para que se vea y se pueda reintentar.
    """

    def __init__(self, sincronizar: Callable[[str], ResultadoHoja] = sincronizar_hoja,
                 repositorio: RepositorioDeCasos | None = None, espera: float = ESPERA_ANTES_DE_ACTUALIZAR,
                 en_hilo: bool = True) -> None:
        self._sincronizar = sincronizar
        self._repo = repositorio or RepositorioDeCasos()
        self._espera = espera
        self._en_hilo = en_hilo
        self._candado = threading.Lock()
        self._activos: set[str] = set()
        self._pendientes: set[str] = set()

    def programar(self, thread_id: str) -> None:
        """Pide actualizar la hoja del caso (no hace nada si el caso aún no tiene carpeta ni hoja)."""
        if os.getenv("ANKA_HOJA_AUTOMATICA", "1") == "0":
            return
        caso = self._repo.obtener(thread_id)
        if caso is None or not caso.folder_id:
            return
        with self._candado:
            if thread_id in self._activos:
                self._pendientes.add(thread_id)
                return
            self._activos.add(thread_id)
        if self._en_hilo:
            threading.Thread(target=self._correr, args=(thread_id,), name="hoja-de-google", daemon=True).start()
        else:
            self._correr(thread_id)

    def _correr(self, thread_id: str) -> None:
        while True:
            if self._espera:
                time.sleep(self._espera)
            with self._candado:
                self._pendientes.discard(thread_id)
            try:
                self._sincronizar(thread_id)
            except Exception as exc:
                log.warning("%s: no se pudo actualizar la hoja de Google (%s)", thread_id, type(exc).__name__,
                            exc_info=True)
                try:
                    self._repo.registrar_error_de_hoja(thread_id, texto_del_error(exc))
                except Exception:
                    log.exception("%s: no se pudo registrar el error de la hoja", thread_id)
            with self._candado:
                if thread_id in self._pendientes:
                    continue
                self._activos.discard(thread_id)
                return


sincronizador = SincronizadorDeHojas()


def programar_actualizacion_de_hoja(thread_id: str) -> None:
    """Llamar después de cualquier cambio en la tabla de un caso: la hoja de Google se actualiza sola."""
    sincronizador.programar(thread_id)
