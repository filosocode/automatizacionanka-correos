"""Seguridad del dashboard local: token CSRF persistente, Host y Origen, cabeceras HTTP y límites (T-07, D-71)."""

from __future__ import annotations

import hmac
import re
import secrets
import urllib.parse

from anka.dominio.errores import ErrorDeAnka, codigo_de_error
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


def is_local_host_header(value: str, puerto: int | None = None) -> bool:
    """Acepta solo Host locales; bloquea DNS rebinding. Con `puerto` (el del servidor), el puerto del Host, si lo trae,
    debe ser ese (U-61, B2-06); sin puerto en el Host se acepta (HTTP/1.0 y clientes sencillos)."""
    value = (value or "").lower()
    tiene_puerto = value.count(":") == 1 or value.startswith("[") and "]:" in value
    hostname, _, escrito = value.rpartition(":") if tiene_puerto else (value, "", "")
    if hostname not in LOCAL_HOSTS:
        return False
    if puerto is not None and tiene_puerto:
        return escrito.isascii() and escrito.isdigit() and int(escrito) == puerto
    return True


# `Sec-Fetch-Site` lo escribe el navegador y la página no puede cambiarlo. Un POST legítimo del dashboard es siempre
# «same-origin»; «none» es lo que declara una acción de la propia persona (marcador, barra de direcciones).
FETCH_SITE_PERMITIDOS = frozenset({"same-origin", "none"})


def is_same_origin(origin: str, host: str, fetch_site: str = "") -> bool:
    """¿La petición viene de esta misma página? Política de un POST (U-61, B2-05):

    - `Sec-Fetch-Site` presente: debe ser `same-origin` o `none`; `cross-site` y `same-site` se rechazan.
    - `Origin` presente: debe ser el mismo sitio que el Host (`null` y los ajenos se rechazan).
    - Ninguno de los dos (un script, `curl`, las pruebas): se acepta, porque el token CSRF —que solo está en las
      páginas del propio dashboard— es lo que protege; así no se rompe ningún cliente legítimo.
    """
    if fetch_site and fetch_site.strip().lower() not in FETCH_SITE_PERMITIDOS:
        return False
    if not origin:
        return True  # navegadores omiten Origin en algunas navegaciones; el token cubre ese caso
    return urllib.parse.urlparse(origin).netloc.lower() == (host or "").lower()


# --- el aviso de la dirección (`?message=`)
MAX_MENSAJE = 300     # un enlace con un texto de otro no puede mostrar más que esto como «aviso» del dashboard


def firmar_mensaje(texto: str) -> str:
    """Firma corta del aviso que el propio servidor pone en una redirección (con el token, que no sale del equipo)."""
    return hmac.new(CSRF_TOKEN.encode(), texto.encode("utf-8"), "sha256").hexdigest()[:16]


def mensaje_de_la_direccion(texto: str, firma: str = "") -> str:
    """El aviso que se muestra: completo si lo firmó este servidor; si no, recortado a `MAX_MENSAJE` (un enlace
    manipulado no puede hacer pasar un texto largo por aviso del dashboard)."""
    if firma and hmac.compare_digest(firma, firmar_mensaje(texto)):
        return texto
    return texto if len(texto) <= MAX_MENSAJE else texto[:MAX_MENSAJE - 1].rstrip() + "…"


# --- cabeceras de seguridad (D-71)
# Los scripts van todos en archivos de `/static` (U-49, D-114): `script-src 'self'` sin 'unsafe-inline', así que una
# etiqueta <script> o un manejador `onclick` metido en un texto del cliente no se ejecuta. Los estilos siguen
# admitiendo 'unsafe-inline' (un <style> dentro de <noscript> y los anchos de las barras de las estadísticas son
# atributos `style`): un estilo no ejecuta código, y quitarlos obligaría a cambiar el diseño.
CSP_PAGINAS = ("default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; "
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
    return codigo_de_error()


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
