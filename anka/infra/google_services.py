"""Cliente de Gmail y Drive: credenciales, servicios y carpetas."""

from __future__ import annotations

import os
import shutil
import webbrowser
from typing import TYPE_CHECKING, Any

from pathlib import Path

from anka.dominio.errores import AutorizacionDeGoogle, ErrorDeAnka
from anka.infra import settings
from anka.infra.archivos_privados import escribir_privado
from anka.infra.logging_setup import get_logger

log = get_logger(__name__)

if TYPE_CHECKING:
    from google.oauth2.credentials import Credentials


SCOPES = [
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/auth/gmail.compose",
    "https://www.googleapis.com/auth/gmail.send",
    # Drive completo a propósito: se crean carpetas dentro de `drive_parent_id`, que Anka no creó; `drive.file` no la
    # vería. Riesgo aceptado y alternativa en spec/05-decisiones.md (D-72).
    "https://www.googleapis.com/auth/drive",
]


FOLDER_MIME = "application/vnd.google-apps.folder"

TIEMPO_LIMITE = 30        # segundos de espera por operación de red (U-43, D-108)
REINTENTOS = 3            # con espera creciente, solo en lecturas y escrituras idempotentes
METODOS_REINTENTABLES = frozenset({"GET", "PUT", "PATCH"})   # nunca POST: enviar, crear borrador o carpeta


def _oauth_browser() -> str | None:
    """Usa Chrome en Windows si el navegador predeterminado no abre desde el supervisor oculto."""
    candidates = [shutil.which("chrome.exe"), shutil.which("chrome")]
    for variable in ("PROGRAMFILES", "PROGRAMFILES(X86)", "LOCALAPPDATA"):
        base = os.getenv(variable)
        if base:
            candidates.append(str(Path(base) / "Google" / "Chrome" / "Application" / "chrome.exe"))
    for candidate in candidates:
        if candidate and Path(candidate).is_file():
            webbrowser.register("anka-chrome", None, webbrowser.BackgroundBrowser(candidate))
            return "anka-chrome"
    return None


def _token_path() -> Path:
    return settings.DATA_DIR / "token.json"


def _solicitud_con_reintentos() -> type:
    """Clase de solicitud de googleapiclient que reintenta solo GET/PUT/PATCH (nunca POST, ni el envío de correos)."""
    from googleapiclient.http import HttpRequest

    class SolicitudSegura(HttpRequest):
        def execute(self, http: Any = None, num_retries: int = 0) -> Any:
            if not num_retries and str(self.method).upper() in METODOS_REINTENTABLES:
                num_retries = REINTENTOS
            return super().execute(http=http, num_retries=num_retries)

    return SolicitudSegura


def credentials() -> Credentials:
    """Credenciales de Google ya autorizadas: reutiliza el token guardado y lo renueva si hace falta.

    Nunca abre el navegador: quien corre sin nadie delante (el puente, el dashboard, la revisión programada) no
    puede quedarse esperando un consentimiento. Si falta el token, no tiene los permisos o ya no sirve, lanza
    `AutorizacionDeGoogle`; reautorizar es `authorize_interactive()` (botón del dashboard o `python app.py auth`).
    """
    from google.auth.exceptions import RefreshError
    from google.auth.transport.requests import Request as _Request
    from google.oauth2.credentials import Credentials

    class Request(_Request):          # la renovación del token también espera como máximo 30 s
        def __call__(self, url: str, method: str = "GET", body: Any = None, headers: Any = None,
                     timeout: Any = TIEMPO_LIMITE, **kwargs: Any) -> Any:
            return super().__call__(url, method, body, headers, timeout=min(timeout or TIEMPO_LIMITE, TIEMPO_LIMITE),
                                    **kwargs)

    token_path = _token_path()
    if not token_path.exists():
        raise AutorizacionDeGoogle("Falta autorizar la cuenta de Google.")
    creds = Credentials.from_authorized_user_file(str(token_path), SCOPES)
    if not creds.has_scopes(SCOPES):
        raise AutorizacionDeGoogle("El token guardado no tiene todos los permisos necesarios.")
    renovado = False
    try:
        if creds.expired and creds.refresh_token:
            creds.refresh(Request())
            renovado = True
    except RefreshError as exc:
        log.warning("Google rechazó renovar el token (%s)", type(exc).__name__)
        raise AutorizacionDeGoogle("Google ya no acepta el token guardado.") from exc
    if not creds.valid:
        raise AutorizacionDeGoogle("El token guardado no es válido.")
    if renovado:   # solo se reescribe lo que cambió: esta función se llama a menudo y desde varios procesos
        escribir_privado(token_path, creds.to_json())
    return creds


def authorize_interactive(timeout_seconds: int | None = None) -> Credentials:
    """Abre el consentimiento de Google en el navegador de este equipo y guarda la autorización nueva.

    Espera a que la persona inicie sesión y acepte (`timeout_seconds` limita la espera; sin límite desde la terminal).
    No toca el token anterior hasta que la autorización nueva se completa.
    """
    from google_auth_oauthlib.flow import InstalledAppFlow

    secrets_path = settings.DATA_DIR / "credentials.json"
    if not secrets_path.exists():
        raise ErrorDeAnka(f"Falta {secrets_path} del cliente OAuth de escritorio.")
    creds = InstalledAppFlow.from_client_secrets_file(str(secrets_path), SCOPES).run_local_server(
        port=0, open_browser=os.getenv("ANKA_NO_BROWSER") != "1", timeout_seconds=timeout_seconds,
        browser=_oauth_browser())
    settings.DATA_DIR.mkdir(parents=True, exist_ok=True)
    escribir_privado(_token_path(), creds.to_json())
    log.info("Autorización de Google renovada")
    return creds


def services() -> tuple[Any, Any]:
    """Clientes de Gmail y de Drive ya autenticados: `(gmail, drive)`."""
    import google_auth_httplib2
    import httplib2
    from googleapiclient.discovery import build

    creds = credentials()
    solicitud = _solicitud_con_reintentos()

    def cliente(nombre: str, version: str) -> Any:
        http = google_auth_httplib2.AuthorizedHttp(creds, http=httplib2.Http(timeout=TIEMPO_LIMITE))
        return build(nombre, version, http=http, cache_discovery=False,
                     requestBuilder=lambda http, *args, **kwargs: solicitud(http, *args, **kwargs))

    return cliente("gmail", "v1"), cliente("drive", "v3")


def list_folders(drive: Any, parent_id: str = "root") -> list[dict]:
    """Carpetas (no borradas) que hay directamente dentro de una carpeta de Drive."""
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


def list_file_names(drive: Any, parent_id: str) -> list[str]:
    """Nombres de los archivos (no carpetas) directamente dentro de `parent_id`."""
    escaped = parent_id.replace("\\", "\\\\").replace("'", "\\'")
    query = f"'{escaped}' in parents and mimeType != '{FOLDER_MIME}' and trashed = false"
    result = drive.files().list(q=query, fields="files(name)", pageSize=200).execute()
    return [str(f["name"]) for f in result.get("files", [])]


def ensure_folder(drive: Any, parent_id: str, name: str) -> dict:
    """Devuelve la subcarpeta con ese nombre exacto; la crea si no existe."""
    for folder in list_folders(drive, parent_id):
        if folder["name"] == name:
            return folder
    return drive.files().create(
        body={"name": name, "mimeType": FOLDER_MIME, "parents": [parent_id]},
        fields="id,name,webViewLink",
    ).execute()
