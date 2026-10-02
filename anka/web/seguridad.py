"""Seguridad del dashboard local: token CSRF persistente y comprobación de Host y Origen."""

from __future__ import annotations

import re
import secrets
import urllib.error
import urllib.parse
import urllib.request
from anka.infra import settings




def persistent_csrf_token() -> str:
    """Token de seguridad que sobrevive a los reinicios del dashboard.

    Antes se generaba en cada arranque, así que una página abierta antes de un reinicio (por
    ejemplo, al cambiar el código) fallaba al enviar con «Token de seguridad inválido».
    """
    path = settings.DATA_DIR / "csrf_token"
    try:
        value = path.read_text(encoding="utf-8").strip()
        if len(value) >= 32:
            return value
    except OSError:
        pass
    value = secrets.token_urlsafe(32)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(value, encoding="utf-8")
    except OSError:
        pass  # sin disco: el token vale solo para este arranque, como antes
    return value


CSRF_TOKEN = persistent_csrf_token()


POST_FORM_PATTERN = re.compile(r'(<form\b[^>]*\bmethod="post"[^>]*>)', re.IGNORECASE)


LOCAL_HOSTS = {"127.0.0.1", "localhost", "[::1]"}


def add_csrf_tokens(content: str) -> str:
    """Agrega el token de seguridad a cada formulario POST de la página."""
    field = f'<input type="hidden" name="csrf_token" value="{CSRF_TOKEN}">'
    return POST_FORM_PATTERN.sub(lambda m: m.group(1) + field, content)


def is_local_host_header(value: str) -> bool:
    """Acepta solo Host locales; bloquea DNS rebinding."""
    value = (value or "").lower()
    hostname = value.rsplit(":", 1)[0] if value.count(":") == 1 or value.startswith("[") and "]:" in value else value
    return hostname in LOCAL_HOSTS


def is_same_origin(origin: str, host: str) -> bool:
    """¿La petición viene de esta misma página (o no declara origen)?"""
    if not origin:
        return True  # navegadores omiten Origin en algunas navegaciones; el token cubre ese caso
    return urllib.parse.urlparse(origin).netloc.lower() == (host or "").lower()
