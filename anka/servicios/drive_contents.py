"""Contenido de la carpeta de una solicitud en Drive.

Según la plantilla de Drive ("Leer" en `Año/Cliente/Solicitud`), la carpeta lleva:
  - el PDF de alcance y toda la documentación que envió el cliente (PDF, Word, Excel, KMZ, KML,
    DWG, shapefile, imágenes, comprimidos...),
  - una tabla en Sheet que resume lo que piden (la tabla de actividades).

Solo se omiten las imágenes incrustadas en la firma (logos), que no son documentación.

Nunca pisa nada: si ya hay un archivo con el mismo nombre, lo deja. Un fallo aquí no debe
deshacer la creación de la carpeta; por eso `populate_project_folder` informa en vez de lanzar.
"""

from __future__ import annotations

import mimetypes
from dataclasses import dataclass, field
from io import BytesIO
from typing import Any

from anka.dominio.activities import build_workbook
from anka.servicios.drive_layout import clean_folder_name
from anka.infra.google_services import list_file_names
from anka.infra.logging_setup import get_logger
from anka.dominio.correo import (
    decode_websafe,
    attachment_parts,
    is_inline_signature_image,
    normalized_address,
)

log = get_logger(__name__)

SHEET_MIME = "application/vnd.google-apps.spreadsheet"
XLSX_MIME = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


@dataclass
class ContentReport:
    copied: list[str] = field(default_factory=list)
    existing: list[str] = field(default_factory=list)
    failed: list[str] = field(default_factory=list)
    table: str = ""  # "creada", "ya existía" o "error"

    def summary(self) -> str:
        parts = []
        if self.copied:
            parts.append(f"copié {len(self.copied)} archivo(s) del cliente")
        if self.table == "creada":
            parts.append("creé la tabla de actividades (Sheet)")
        elif self.table == "ya existía":
            parts.append("la tabla de actividades ya estaba")
        if self.existing:
            parts.append(f"{len(self.existing)} ya estaban")
        text = "En la carpeta " + (", ".join(parts) if parts else "no había nada que copiar") + "."
        if self.failed or self.table == "error":
            what = ", ".join(self.failed + (["la tabla de actividades"] if self.table == "error" else []))
            text += f" No pude copiar: {what}. Súbelos a mano."
        return text


def _upload(drive: Any, folder_id: str, name: str, content: bytes, mime: str,
            target_mime: str | None = None) -> None:
    from googleapiclient.http import MediaIoBaseUpload

    body: dict[str, Any] = {"name": name, "parents": [folder_id]}
    if target_mime:
        body["mimeType"] = target_mime  # convierte el Excel en una hoja de Google
    media = MediaIoBaseUpload(BytesIO(content), mimetype=mime, resumable=False)
    drive.files().create(body=body, media_body=media, fields="id,name").execute()


def client_attachments(gmail: Any, thread: dict, own_addresses: set[str]) -> list[tuple[str, bytes]]:
    """(nombre, contenido) de todos los adjuntos de los mensajes del cliente, salvo logos de firma."""
    own = {normalized_address(a) for a in own_addresses}
    found: list[tuple[str, bytes]] = []
    for message in thread.get("messages", []):
        sender = next((h.get("value", "") for h in message.get("payload", {}).get("headers", [])
                       if h.get("name", "").casefold() == "from"), "")
        if normalized_address(sender) in own:
            continue
        for part in attachment_parts(message.get("payload", {})):
            if is_inline_signature_image(part):
                continue
            encoded = part.get("data")
            if not encoded and part.get("attachment_id"):
                encoded = gmail.users().messages().attachments().get(
                    userId="me", messageId=message["id"], id=part["attachment_id"]).execute().get("data")
            if encoded:
                found.append((part["filename"], decode_websafe(encoded)))
            else:
                found.append((part["filename"], b""))  # Gmail no devolvió contenido
    return found


def populate_project_folder(
    gmail: Any, drive: Any, thread: dict, folder_id: str, quote: dict | None,
    own_addresses: set[str],
) -> ContentReport:
    """Copia a `folder_id` todos los adjuntos del cliente y crea la tabla de actividades (Sheet)."""
    report = ContentReport()
    present = {name.casefold() for name in list_file_names(drive, folder_id)}
    try:
        attachments = client_attachments(gmail, thread, own_addresses)
    except Exception:
        log.exception("No se pudieron leer los adjuntos del hilo para copiarlos a Drive")
        attachments = []
        report.failed.append("los adjuntos del hilo")
    for name, content in attachments:
        if name.casefold() in present:
            report.existing.append(name)
            continue
        if not content:
            report.failed.append(name)
            continue
        try:
            _upload(drive, folder_id, name, content,
                    mimetypes.guess_type(name)[0] or "application/octet-stream")
            present.add(name.casefold())
            report.copied.append(name)
        except Exception:
            log.exception("No se pudo subir un adjunto a la carpeta de Drive")
            report.failed.append(name)
    if quote:
        project = (quote.get("analysis") or {}).get("project_name") or "Solicitud"
        table_name = clean_folder_name(f"Tabla de actividades - {project}")[:100]
        if table_name.casefold() in present:
            report.table = "ya existía"
        else:
            try:
                _upload(drive, folder_id, table_name, build_workbook([quote]), XLSX_MIME, SHEET_MIME)
                report.table = "creada"
            except Exception:
                log.exception("No se pudo crear la tabla de actividades en Drive")
                report.table = "error"
    log.info("Contenido de la carpeta: %d copiados, %d existentes, %d fallidos, tabla %s",
             len(report.copied), len(report.existing), len(report.failed), report.table or "omitida")
    return report
