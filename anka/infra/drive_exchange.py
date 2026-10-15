"""Archivos de intercambio en Drive, identificados por claves estables y con paginación."""

from __future__ import annotations
from anka.infra.clientes_google import ClienteDrive

import hashlib
import json
import sys
from contextlib import contextmanager
from collections.abc import Iterator
from io import BytesIO
from pathlib import Path

from anka.dominio.errores import ErrorDeAnka
from anka.infra.google_services import FOLDER_MIME


@contextmanager
def preparation_lock(path: Path) -> Iterator[None]:
    """Impide dos preparadores en el mismo equipo; el sistema libera el bloqueo si el proceso muere."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a+b") as handle:
        if path.stat().st_size == 0:
            handle.write(b"0")
            handle.flush()
        handle.seek(0)
        try:
            if sys.platform == "win32":
                import msvcrt
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            raise ErrorDeAnka("Ya hay un preparador de expedientes activo en este equipo.") from exc
        try:
            yield
        finally:
            handle.seek(0)
            if sys.platform == "win32":
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


class DriveExchange:
    """Acceso a expedientes propios sin alterar archivos ajenos ni sus permisos."""

    def __init__(self, drive: ClienteDrive):
        self.drive = drive

    def find(self, parent: str, key: str) -> dict | None:
        """Busca una clave propia bajo el padre y rechaza duplicados ambiguos."""
        escaped = parent.replace("\\", "\\\\").replace("'", "\\'")
        digest = hashlib.sha256(key.encode()).hexdigest()
        query = (f"'{escaped}' in parents and trashed = false and "
                 f"appProperties has {{ key='anka_work_key' and value='{digest}' }}")
        found, token = [], None
        while True:
            result = self.drive.files().list(
                q=query, fields="nextPageToken,files(id,name,mimeType,webViewLink)",
                pageSize=100, pageToken=token).execute()
            found.extend(result.get("files", []))
            token = result.get("nextPageToken")
            if not token:
                break
        if len(found) > 1:
            raise ErrorDeAnka("Expediente con claves duplicadas en Drive; requiere revisión interna.")
        return found[0] if found else None

    def folder(self, parent: str, key: str, name: str) -> dict:
        """Crea o reutiliza una carpeta identificada, independientemente de su nombre visible."""
        existing = self.find(parent, key)
        if existing:
            if existing["mimeType"] != FOLDER_MIME:
                raise ErrorDeAnka("La clave de carpeta apunta a un archivo en Drive.")
            return existing
        return self.drive.files().create(body={
            "name": name, "parents": [parent], "mimeType": FOLDER_MIME,
            "appProperties": {"anka_work_key": hashlib.sha256(key.encode()).hexdigest()},
        }, fields="id,name,mimeType,webViewLink").execute()

    def put(self, parent: str, key: str, name: str, content: bytes,
            mime: str = "text/plain", *, replace: bool = False) -> dict:
        """Publica un archivo; solo los índices mutables se reemplazan explícitamente."""
        from googleapiclient.http import MediaIoBaseUpload

        existing = self.find(parent, key)
        if existing and not replace:
            return existing
        media = MediaIoBaseUpload(BytesIO(content), mimetype=mime, resumable=True)
        fields = "id,name,mimeType,webViewLink"
        if existing:
            return self.drive.files().update(
                fileId=existing["id"], media_body=media, fields=fields).execute()
        return self.drive.files().create(body={
            "name": name, "parents": [parent], "mimeType": mime,
            "appProperties": {"anka_work_key": hashlib.sha256(key.encode()).hexdigest()},
        }, media_body=media, fields=fields).execute()

    def read_bytes(self, record: dict) -> bytes:
        """Contenido de un archivo propio (un segmento de texto), tal como se publicó."""
        return bytes(self.drive.files().get_media(fileId=record["id"]).execute())

    def read_json(self, record: dict) -> dict:
        """Lee un manifiesto propio como JSON, sin interpretar instrucciones de los documentos."""
        content = self.drive.files().get_media(fileId=record["id"]).execute()
        value = json.loads(content)
        if not isinstance(value, dict):
            raise ErrorDeAnka("Manifiesto de Drive inválido.")
        return value
