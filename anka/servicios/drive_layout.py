"""Estructura de carpetas de Drive: Año / Cliente / Solicitud.

La carpeta padre configurada (`drive_parent_id`) es la del año vigente (por ejemplo `2026`).
Dentro van las carpetas de cada cliente y, dentro de cada una, una carpeta por solicitud.

Regla de seguridad: se reutiliza la carpeta de cliente que coincida (sin importar mayúsculas,
tildes ni sufijos como S.A.S. o Ltda.). Una carpeta de cliente nueva se crea solo cuando una
persona lo confirma (`create=True`) o cuando el nombre del cliente lo escribió una persona y no
existe ninguna carpeta igual ni parecida (`auto_create=True`). Si hay una parecida ("Acme" /
"Acme Colombia"), nunca se crea: decide una persona. Lo mismo vale para la carpeta de la
solicitud: si el cliente ya tiene una de nombre parecido o con el mismo archivo adjunto, se
detiene y avisa en vez de duplicar el proyecto.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass
from difflib import SequenceMatcher
from typing import Any

from anka.infra.google_services import ensure_folder, list_file_names, list_folders
from anka.dominio.reception import normalized_text

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
    """No existe la carpeta del cliente y nadie confirmó que es nuevo."""
    def __init__(self, client_name: str):
        super().__init__(
            f"No existe una carpeta del cliente '{client_name}'. Confirma que es un cliente "
            "nuevo para crearla, o escribe el nombre exacto de una carpeta existente."
        )
        self.client_name = client_name


class AmbiguousClient(DriveLayoutError):
    """Hay varias carpetas de cliente que coinciden y una persona debe elegir."""
    def __init__(self, client_name: str, folders: list[dict]):
        names = ", ".join(f"'{f['name']}'" for f in folders)
        super().__init__(
            f"Hay varias carpetas parecidas a '{client_name}' ({names}). "
            "Escribe el nombre exacto de la que corresponde."
        )
        self.client_name = client_name
        self.folders = folders


class SimilarClient(DriveLayoutError):
    """Ya hay una carpeta de cliente parecida: no se crea otra sin confirmación."""
    def __init__(self, client_name: str, folders: list[dict]):
        names = ", ".join(f"'{f['name']}'" for f in folders)
        super().__init__(
            f"No creé la carpeta del cliente '{client_name}' porque ya hay una parecida: {names}. "
            "Si es el mismo cliente, escribe el nombre exacto de esa carpeta; si es otro, "
            "marca 'Es un cliente nuevo' para crearla."
        )
        self.client_name = client_name
        self.folders = folders


class SimilarProject(DriveLayoutError):
    """El cliente ya tiene una solicitud parecida: no se crea otra sin confirmación."""
    def __init__(self, client_name: str, matches: list[dict]):
        detail = "; ".join(f"'{m['name']}' ({m['reason']})" for m in matches)
        super().__init__(
            f"No creé la carpeta de la solicitud porque '{client_name}' ya tiene una parecida: "
            f"{detail}. Revísala en Drive; si es un proyecto distinto, marca "
            "'Es un proyecto distinto' para crearla de todos modos."
        )
        self.client_name = client_name
        self.matches = matches


@dataclass
class ProjectFolder:
    """Resultado de preparar la carpeta: la del cliente, si se creó y la de la solicitud."""
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


def _keys_similar(a: str, b: str) -> bool:
    """¿Dos claves normalizadas (ya sin tildes ni sufijos) parecen el mismo nombre?"""
    if not a or not b:
        return False
    if a == b:
        return True
    ta, tb = set(a.split()), set(b.split())
    small, big = (ta, tb) if len(ta) <= len(tb) else (tb, ta)
    if len(small) >= 2 and small <= big:
        return True
    return SequenceMatcher(None, a, b).ratio() >= 0.85


def clave_de_proyecto(nombre: str) -> str:
    """La clave con que se compara el nombre de un proyecto: sin tildes, sufijos ni el prefijo de la carpeta."""
    return normalize_client_name(project_title(nombre))


def claves_parecidas(a: str, b: str) -> bool:
    """¿Dos claves de proyecto o de cliente parecen el mismo nombre? (el criterio de las carpetas de Drive)."""
    return _keys_similar(a, b)


def project_title(folder_name: str) -> str:
    """Nombre del proyecto dentro de `<prefijo> - <proyecto> - <id>`; el nombre entero si no encaja."""
    match = re.fullmatch(r"[^-]+ - (.+) - [0-9A-Za-z_]{6,12}", folder_name.strip())
    return match.group(1) if match else folder_name


def similar_client_folders(drive: Any, parent_id: str, client_name: str) -> list[dict]:
    """Carpetas de cliente de nombre parecido (pero no equivalente) al dado."""
    key = normalize_client_name(client_name)
    return [
        f for f in list_folders(drive, parent_id)
        if normalize_client_name(f["name"]) != key
        and _keys_similar(key, normalize_client_name(f["name"]))
    ]


def similar_project_folders(
    drive: Any, client_id: str, request_name: str, attachment_names: Iterable[str] = (),
    *, max_scan: int = 30,
) -> list[dict]:
    """Solicitudes del cliente que parecen ser el mismo proyecto: nombre parecido o mismo adjunto."""
    key = normalize_client_name(project_title(request_name))
    wanted = {n.strip().casefold() for n in attachment_names if n and n.strip()}
    matches = []
    for folder in list_folders(drive, client_id)[:max_scan]:
        if folder["name"] == request_name:
            continue
        if _keys_similar(key, normalize_client_name(project_title(folder["name"]))):
            matches.append({"name": folder["name"], "reason": "nombre parecido"})
            continue
        if wanted:
            shared = sorted(wanted & {n.casefold() for n in list_file_names(drive, folder["id"])})
            if shared:
                matches.append({
                    "name": folder["name"], "reason": f"contiene el mismo archivo '{shared[0]}'",
                })
    return matches


def clean_folder_name(name: str) -> str:
    """Nombre de carpeta sin caracteres no válidos ni espacios sobrantes; falla si queda vacío."""
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
    drive: Any, parent_id: str, client_name: str, *, create: bool, auto_create: bool = False,
) -> tuple[dict, bool]:
    """Devuelve (carpeta del cliente, ¿recién creada?).

    Crea si `create` es True (confirmación explícita) o, con `auto_create`, solo si no existe
    ninguna carpeta igual ni parecida.
    """
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
        if not auto_create:
            raise ClientNotFound(clean)
        similar = similar_client_folders(drive, parent_id, clean)
        if similar:
            raise SimilarClient(clean, similar)
    folder = drive.files().create(
        body={"name": clean, "mimeType": "application/vnd.google-apps.folder", "parents": [parent_id]},
        fields="id,name,webViewLink",
    ).execute()
    return folder, True


def create_project_folder(
    drive: Any, parent_id: str, client_name: str, request_name: str, *, create_client: bool = False,
    auto_create_client: bool = False, attachment_names: Iterable[str] = (),
    allow_similar_project: bool = False,
) -> ProjectFolder:
    """Crea (o reutiliza) `<padre>/<cliente>/<solicitud>`. Idempotente.

    Con `auto_create_client` un cliente sin carpeta ni parecidas se crea solo. Si el cliente ya
    tenía carpeta y hay una solicitud parecida (otro nombre o el mismo adjunto), lanza
    `SimilarProject` salvo que `allow_similar_project` lo permita.
    """
    client, created = resolve_client_folder(
        drive, parent_id, client_name, create=create_client, auto_create=auto_create_client)
    request = clean_folder_name(request_name)
    if not created and not allow_similar_project:
        if all(f["name"] != request for f in list_folders(drive, client["id"])):
            similar = similar_project_folders(drive, client["id"], request, attachment_names)
            if similar:
                raise SimilarProject(client["name"], similar)
    folder = ensure_folder(drive, client["id"], request)
    return ProjectFolder(client=client, client_created=created, folder=folder)


def trash_project_folder(drive: Any, parent_id: str, folder_id: str) -> bool:
    """Mueve a la papelera de Drive la carpeta de una solicitud creada por este sistema.

    Solo actúa sobre `<padre>/<cliente>/<solicitud>`: si la carpeta no está exactamente a ese
    nivel bajo la carpeta padre configurada, no la toca. La papelera es recuperable.
    Devuelve False si la carpeta ya no existía o ya estaba en la papelera.
    """
    try:
        folder = drive.files().get(fileId=folder_id, fields="id,name,parents,trashed").execute()
    except Exception as exc:  # googleapiclient.errors.HttpError 404: ya no existe
        if getattr(getattr(exc, "resp", None), "status", None) == 404:
            return False
        raise
    if folder.get("trashed"):
        return False
    client_id = (folder.get("parents") or [""])[0]
    client = drive.files().get(fileId=client_id, fields="id,name,parents").execute() if client_id else {}
    if parent_id not in (client.get("parents") or []):
        raise DriveLayoutError(
            f"La carpeta '{folder.get('name', folder_id)}' no está en el nivel Año/Cliente/Solicitud "
            "de la carpeta configurada; no la moví a la papelera."
        )
    drive.files().update(fileId=folder_id, body={"trashed": True}).execute()
    return True


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
