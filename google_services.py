"""Cliente de Gmail y Drive: credenciales, servicios y carpetas."""

from __future__ import annotations

import os
from typing import TYPE_CHECKING, Any

import settings
from logging_setup import get_logger

log = get_logger(__name__)

if TYPE_CHECKING:
    from google.oauth2.credentials import Credentials


SCOPES = [
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/auth/gmail.compose",
    "https://www.googleapis.com/auth/gmail.send",
    "https://www.googleapis.com/auth/drive",
]


FOLDER_MIME = "application/vnd.google-apps.folder"


def credentials(force_auth: bool = False) -> Credentials:
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials
    from google_auth_oauthlib.flow import InstalledAppFlow

    token_path = settings.DATA_DIR / "token.json"
    secrets_path = settings.DATA_DIR / "credentials.json"
    creds = None
    if force_auth and token_path.exists():
        token_path.unlink()
    if token_path.exists():
        creds = Credentials.from_authorized_user_file(str(token_path), SCOPES)
        if not creds.has_scopes(SCOPES):
            log.warning("token.json necesita permisos nuevos; se abrira la autorizacion de Google.")
            creds = None
    if creds and creds.expired and creds.refresh_token:
        creds.refresh(Request())
    if not creds or not creds.valid:
        if not secrets_path.exists():
            raise SystemExit(f"Falta {secrets_path} del cliente OAuth de escritorio.")
        creds = InstalledAppFlow.from_client_secrets_file(
            str(secrets_path), SCOPES
        ).run_local_server(port=0, open_browser=os.getenv("ANKA_NO_BROWSER") != "1")
    settings.DATA_DIR.mkdir(parents=True, exist_ok=True)
    token_path.write_text(creds.to_json(), encoding="utf-8")
    return creds


def services() -> tuple[Any, Any]:
    from googleapiclient.discovery import build

    creds = credentials()
    return (
        build("gmail", "v1", credentials=creds, cache_discovery=False),
        build("drive", "v3", credentials=creds, cache_discovery=False),
    )


def list_folders(drive: Any, parent_id: str = "root") -> list[dict]:
    escaped = parent_id.replace("\\", "\\\\").replace("'", "\\'")
    query = f"'{escaped}' in parents and mimeType = '{FOLDER_MIME}' and trashed = false"
    folders = []
    page_token = None
    while True:
        result = drive.files().list(
            q=query, fields="nextPageToken,files(id,name,webViewLink)",
            pageSize=1000, pageToken=page_token,
        ).execute()
        folders.extend(result.get("files", []))
        page_token = result.get("nextPageToken")
        if not page_token:
            return sorted(folders, key=lambda f: f["name"].casefold())


def ensure_folder(drive: Any, parent_id: str, name: str) -> dict:
    for folder in list_folders(drive, parent_id):
        if folder["name"] == name:
            return folder
    return drive.files().create(
        body={"name": name, "mimeType": FOLDER_MIME, "parents": [parent_id]},
        fields="id,name,webViewLink",
    ).execute()
