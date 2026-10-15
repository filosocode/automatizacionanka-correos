"""Seguridad del dashboard local: token CSRF persistente, Host y Origen, cabeceras HTTP y límites (T-07, D-71)."""

from __future__ import annotations

import re
import secrets
import urllib.parse

from anka.dominio.errores import ErrorDeAnka
from anka.infra import settings
from anka.infra.archivos_privados import escribir_privado


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
        escribir_privado(path, value)
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


# --- cabeceras de seguridad (D-71)
# Las plantillas llevan <script> y <style> en línea (y atributos `style`), pero ningún manejador de eventos en línea
# (T-07): por eso se permite 'unsafe-inline' solo para scripts y estilos y todo lo demás queda en el propio origen.
CSP_PAGINAS = ("default-src 'self'; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline'; "
               "img-src 'self' data:; connect-src 'self'; object-src 'none'; base-uri 'none'; form-action 'self'; "
               "frame-ancestors 'none'")


def csp_docs(nonce: str) -> str:
    """CSP de `/docs`: Swagger UI desde su CDN fijado y un solo script propio, autorizado por `nonce` (sin inline)."""
    cdn = "https://cdn.jsdelivr.net"
    return (f"default-src 'self'; script-src 'self' {cdn} 'nonce-{nonce}'; style-src 'self' {cdn} 'unsafe-inline'; "
            "img-src 'self' data:; connect-src 'self'; object-src 'none'; base-uri 'none'; form-action 'self'; "
            "frame-ancestors 'none'")


def cabeceras_de_seguridad(csp: str = CSP_PAGINAS) -> dict[str, str]:
    """Cabeceras que lleva toda respuesta del dashboard, también las redirecciones y el canal de avisos."""
    return {
        "X-Frame-Options": "DENY",
        "Content-Security-Policy": csp,
        "X-Content-Type-Options": "nosniff",
        # No `no-referrer`: con esa política los navegadores mandan `Origin: null` incluso en un formulario del
        # propio dashboard y `is_same_origin` lo rechazaría («Origen no permitido»).
        "Referrer-Policy": "same-origin",
        "Cache-Control": "no-store",
    }


# --- límites del cuerpo de un POST (D-71)
MAX_CUERPO = 2 * 1024 * 1024     # 2 MiB: la tabla más grande (200 filas) y un correo largo caben con holgura


class CuerpoNoValido(ErrorDeAnka):
    """El cuerpo de la petición no se puede aceptar; `status` es el código HTTP con que se responde."""

    def __init__(self, status: int, mensaje: str) -> None:
        super().__init__(mensaje)
        self.status = status


def longitud_del_cuerpo(valor: str | None) -> int:
    """El `Content-Length` como entero; no numérico o negativo → 400, mayor que `MAX_CUERPO` → 413."""
    texto = (valor or "0").strip()
    if not texto.isdigit() or not texto.isascii():
        raise CuerpoNoValido(400, "La petición no se pudo leer (longitud no válida).")
    longitud = int(texto)
    if longitud > MAX_CUERPO:
        raise CuerpoNoValido(413, "El formulario es demasiado grande para enviarlo.")
    return longitud


# --- nombres de archivo y mensajes de error (D-71)
def nombre_seguro(texto: str) -> str:
    """Solo letras ASCII, dígitos, «_» y «-»: lo que puede ir sin riesgo en `Content-Disposition`."""
    return re.sub(r"[^A-Za-z0-9_-]", "", texto)


def id_de_error() -> str:
    """Código corto que se muestra a la persona y se anota en el registro junto con el detalle del error."""
    return secrets.token_hex(4)


_FINAL_DE_FRASE = (".", "?", "!", "»", ")")


def es_mensaje_para_la_persona(exc: BaseException) -> bool:
    """¿El texto del error se escribió para la persona y se puede mostrar tal cual?

    Sí para `ErrorDeAnka` y para los `ValueError` que lanza Anka con una frase (empieza en mayúscula o «¿» y termina
    en puntuación). No para los de Python o de librerías («invalid literal for int()…», `UnicodeDecodeError`,
    `JSONDecodeError`): esos se reemplazan por un mensaje genérico y su detalle queda solo en el registro.
    """
    if isinstance(exc, ErrorDeAnka):
        return True
    if type(exc) is not ValueError:
        return False
    texto = str(exc).strip()
    return bool(texto) and (texto[0].isupper() or texto[0] == "¿") and texto.endswith(_FINAL_DE_FRASE)
