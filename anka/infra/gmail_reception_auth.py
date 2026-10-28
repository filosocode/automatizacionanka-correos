"""Autorización Gmail solo lectura, separada de las credenciales comerciales."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from anka.dominio.errores import AutorizacionDeGoogle
from anka.infra import settings
from anka.infra.archivos_privados import escribir_privado

READONLY_SCOPE = "https://www.googleapis.com/auth/gmail.readonly"
READONLY_SCOPES = (READONLY_SCOPE,)
TOKEN_FILENAME = "gmail_reception_token.json"


def token_path() -> Path:
    """Token dedicado del receptor; nunca apunta al token comercial `token.json`."""
    return settings.DATA_DIR / TOKEN_FILENAME


def _assert_dedicated_file(path: Path) -> None:
    """Rechaza enlaces que hagan apuntar el token dedicado al archivo comercial."""
    commercial = settings.DATA_DIR / "token.json"
    if path.is_symlink() or path.resolve() == commercial.resolve():
        raise AutorizacionDeGoogle("El token del receptor debe ser un archivo independiente del token comercial.")
    try:
        if commercial.exists() and path.exists() and path.samefile(commercial):
            raise AutorizacionDeGoogle("El token del receptor debe ser un archivo independiente del token comercial.")
    except OSError as exc:
        raise AutorizacionDeGoogle("No se pudo verificar el aislamiento del token Gmail.") from exc


def _check_scope_list(scopes: object) -> None:
    if not isinstance(scopes, list) or set(scopes) != set(READONLY_SCOPES) or len(scopes) != 1:
        raise AutorizacionDeGoogle("La credencial dedicada no declara exclusivamente Gmail readonly.")


def credentials() -> Any:
    """Carga solo el token dedicado y falla cerrado; no existe fallback al token comercial."""
    path = token_path()
    _assert_dedicated_file(path)
    if not path.is_file():
        raise AutorizacionDeGoogle(f"Falta la autorización Gmail solo lectura: {path.name}.")
    try:
        info = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(info, dict):
            raise AutorizacionDeGoogle("El archivo de autorización dedicado no tiene formato válido.")
        _check_scope_list(info.get("scopes"))
    except (OSError, ValueError, TypeError) as exc:
        raise AutorizacionDeGoogle("No se pudo validar la credencial dedicada de Gmail.") from exc

    from google.auth.exceptions import RefreshError
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials

    creds = Credentials.from_authorized_user_file(str(path), list(READONLY_SCOPES))
    _check_scope_list(list(creds.scopes or []))
    granted = getattr(creds, "granted_scopes", None)
    if granted is not None:
        _check_scope_list(list(granted))
    try:
        if creds.expired and creds.refresh_token:
            creds.refresh(Request())
    except RefreshError as exc:
        raise AutorizacionDeGoogle("Google rechazó renovar la autorización Gmail solo lectura.") from exc
    if not creds.valid:
        raise AutorizacionDeGoogle("La autorización Gmail solo lectura no es válida.")
    granted = getattr(creds, "granted_scopes", None)
    if granted is not None:
        _check_scope_list(list(granted))
    # Solo se reescribe el archivo dedicado; nunca `token.json`.
    if creds.token != info.get("token"):
        _check_scope_list(list(creds.scopes or []))
        escribir_privado(path, creds.to_json())
    return creds


def authorize_interactive() -> Path:
    """Obtiene consentimiento readonly y lo guarda en un archivo independiente."""
    from google_auth_oauthlib.flow import InstalledAppFlow

    client_secrets = settings.DATA_DIR / "credentials.json"
    if not client_secrets.is_file():
        raise AutorizacionDeGoogle("Falta el archivo de cliente OAuth de escritorio.")
    flow = InstalledAppFlow.from_client_secrets_file(str(client_secrets), list(READONLY_SCOPES))
    creds = flow.run_local_server(port=0)
    _check_scope_list(list(creds.scopes or []))
    granted = getattr(creds, "granted_scopes", None)
    if granted is not None:
        _check_scope_list(list(granted))
    if not creds.valid:
        raise AutorizacionDeGoogle("Google no devolvió una autorización válida Gmail solo lectura.")
    path = token_path()
    _assert_dedicated_file(path)
    escribir_privado(path, creds.to_json())
    return path


def gmail_service() -> Any:
    """Construye únicamente el cliente Gmail con la credencial dedicada."""
    import google_auth_httplib2
    import httplib2
    from googleapiclient.discovery import build

    creds = credentials()
    http = google_auth_httplib2.AuthorizedHttp(creds, http=httplib2.Http(timeout=30))
    return build("gmail", "v1", http=http, cache_discovery=False)
