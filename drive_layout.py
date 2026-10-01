"""Estructura de carpetas de Drive: Año / Cliente / Solicitud.

La carpeta padre configurada (`drive_parent_id`) es la del año vigente (por ejemplo `2026`).
Dentro van las carpetas de cada cliente y, dentro de cada una, una carpeta por solicitud.

Regla de seguridad: una carpeta de cliente NUNCA se crea por cuenta propia. Se reutiliza la
que coincida (sin importar mayúsculas, tildes ni sufijos como S.A.S. o Ltda.) y solo se crea
una nueva cuando una persona lo confirma. Un cliente mal identificado deja carpetas
duplicadas ("Acme", "ACME SAS") que luego son tediosas de limpiar.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from google_services import ensure_folder, list_folders
from reception import normalized_text

PATH_TEMPLATE = "{año}/{cliente}/{solicitud}"

# Sufijos societarios que se ignoran al comparar nombres (solo al final del nombre).
LEGAL_SUFFIXES = (
    "s a s", "s a", "s l", "e u", "sas", "ltda", "sa", "sl", "eu", "inc", "corp", "llc", "cia",
)
_SUFFIX_TOKENS = tuple(tuple(suffix.split()) for suffix in LEGAL_SUFFIXES)
_INVALID_CHARS = re.compile(r'[\\/:*?"<>|\x00-\x1f]')


class DriveLayoutError(Exception):
    """Error de estructura de Drive con un mensaje listo para mostrar a una persona."""


class ClientNotFound(DriveLayoutError):
    def __init__(self, client_name: str):
        super().__init__(
            f"No existe una carpeta del cliente '{client_name}'. Confirma que es un cliente "
            "nuevo para crearla, o escribe el nombre exacto de una carpeta existente."
        )
        self.client_name = client_name


class AmbiguousClient(DriveLayoutError):
    def __init__(self, client_name: str, folders: list[dict]):
        names = ", ".join(f"'{f['name']}'" for f in folders)
        super().__init__(
            f"Hay varias carpetas parecidas a '{client_name}' ({names}). "
            "Escribe el nombre exacto de la que corresponde."
        )
        self.client_name = client_name
        self.folders = folders


@dataclass
class ProjectFolder:
    client: dict
    client_created: bool
    folder: dict


def normalize_client_name(name: str) -> str:
    """Clave de comparación: sin tildes, mayúsculas, puntuación ni sufijo societario final."""
    tokens = re.findall(r"[a-z0-9]+", normalized_text(name))
    stripped = True
    while tokens and stripped:
        stripped = False
        for suffix in _SUFFIX_TOKENS:
            if len(tokens) >= len(suffix) and tuple(tokens[-len(suffix):]) == suffix:
                tokens = tokens[:-len(suffix)]
                stripped = True
                break
    return " ".join(tokens)


def clean_folder_name(name: str) -> str:
    clean = re.sub(r"\s+", " ", _INVALID_CHARS.sub(" ", name)).strip(" .")
    if not clean:
        raise ValueError("El nombre de la carpeta no puede estar vacío.")
    return clean


def find_client_folders(drive: Any, parent_id: str, client_name: str) -> list[dict]:
    """Carpetas de cliente bajo `parent_id` cuyo nombre equivale al dado."""
    key = normalize_client_name(client_name)
    if not key:
        return []
    return [f for f in list_folders(drive, parent_id) if normalize_client_name(f["name"]) == key]


def resolve_client_folder(
    drive: Any, parent_id: str, client_name: str, *, create: bool,
) -> tuple[dict, bool]:
    """Devuelve (carpeta del cliente, ¿recién creada?). Solo crea si `create` es True."""
    clean = clean_folder_name(client_name)
    matches = find_client_folders(drive, parent_id, clean)
    if len(matches) > 1:
        exact = [m for m in matches if m["name"].strip().casefold() == clean.casefold()]
        if len(exact) == 1:
            return exact[0], False
        raise AmbiguousClient(clean, matches)
    if matches:
        return matches[0], False
    if not create:
        raise ClientNotFound(clean)
    folder = drive.files().create(
        body={"name": clean, "mimeType": "application/vnd.google-apps.folder", "parents": [parent_id]},
        fields="id,name,webViewLink",
    ).execute()
    return folder, True


def create_project_folder(
    drive: Any, parent_id: str, client_name: str, request_name: str, *, create_client: bool = False,
) -> ProjectFolder:
    """Crea (o reutiliza) `<padre>/<cliente>/<solicitud>`. Idempotente."""
    client, created = resolve_client_folder(drive, parent_id, client_name, create=create_client)
    folder = ensure_folder(drive, client["id"], clean_folder_name(request_name))
    return ProjectFolder(client=client, client_created=created, folder=folder)


def year_mismatch(drive: Any, parent_id: str, received_at: str) -> str | None:
    """Aviso si la carpeta padre es de un año distinto al de la solicitud; None si todo bien."""
    year = (received_at or "")[:4]
    if not (len(year) == 4 and year.isdigit()):
        return None
    name = str(drive.files().get(fileId=parent_id, fields="id,name").execute().get("name", "")).strip()
    if re.fullmatch(r"\d{4}", name) and name != year:
        return (
            f"La carpeta padre es '{name}' pero la solicitud es de {year}. "
            f"Actualiza drive_parent_id a la carpeta de {year} en config.json."
        )
    return None
