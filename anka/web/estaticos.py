"""Archivos fijos del diseño (`/static/…`) y compresión de las respuestas (U-48).

Solo se sirven los nombres de la lista (nunca una ruta armada con lo que escribe la persona). El CSS compartido y los
dos guiones (`app.js`, el de cada página, y `tema.js`, el primer pintado) se publican con la huella de su contenido en
el nombre (`/static/style.<huella>.css`, `/static/app.<huella>.js`…): como cambia cuando cambia el archivo, el navegador
puede guardarlo para siempre y no se repite en cada página (U-49 sacó el JavaScript de las páginas). Los demás
archivos llevan `ETag`.
"""

from __future__ import annotations

import gzip
import hashlib
import threading
from dataclasses import dataclass
from pathlib import Path

STATIC_DIR = Path(__file__).with_name("static")
ESTILO = Path(__file__).with_name("templates") / "style.css"
# Guiones con huella: nombre corto -> archivo. `tema` va en el <head> sin `defer` (pinta el tema antes del 1.er cuadro).
GUIONES: dict[str, Path] = {"app": Path(__file__).with_name("templates") / "app.js",
                            "tema": Path(__file__).with_name("templates") / "tema.js"}
TIPO_DE_GUION = "text/javascript; charset=utf-8"
FIJOS: dict[str, str] = {"inter-latin-wght.woff2": "font/woff2", "logo-anka.png": "image/png"}
CACHE_DE_FIJOS = "public, max-age=86400"
CACHE_CON_HUELLA = "public, max-age=31536000, immutable"

# Se comprime solo texto y solo si pesa lo bastante para que valga la pena.
COMPRIMIBLES = ("text/html", "text/css", "text/javascript", "application/json", "application/javascript",
                "image/svg+xml")
MINIMO_PARA_COMPRIMIR = 1024


@dataclass(frozen=True)
class Archivo:
    """Un archivo fijo listo para enviar."""

    datos: bytes
    tipo: str
    etag: str
    cache: str


_lock = threading.Lock()
_leidos: dict[Path, tuple[tuple[int, int], bytes, str]] = {}


def _leer(ruta: Path) -> tuple[bytes, str]:
    """Contenido y huella del archivo; se vuelve a leer solo si cambió en disco (tamaño o fecha)."""
    estado = ruta.stat()
    clave = (estado.st_mtime_ns, estado.st_size)
    with _lock:
        guardado = _leidos.get(ruta)
        if guardado is not None and guardado[0] == clave:
            return guardado[1], guardado[2]
    datos = ruta.read_bytes()
    huella = hashlib.sha256(datos).hexdigest()[:12]
    with _lock:
        _leidos[ruta] = (clave, datos, huella)
    return datos, huella


def nombre_del_estilo() -> str:
    """`style.<huella>.css`: el nombre con que se publica el CSS compartido."""
    return f"style.{_leer(ESTILO)[1]}.css"


def url_del_estilo() -> str:
    """La dirección del CSS compartido, con la huella de su contenido (la usa el `<link>` de todas las páginas)."""
    return f"/static/{nombre_del_estilo()}"


def nombre_del_guion(guion: str) -> str:
    """`app.<huella>.js` (o `tema.<huella>.js`): el nombre con que se publica ese guion."""
    return f"{guion}.{_leer(GUIONES[guion])[1]}.js"


def url_del_guion(guion: str) -> str:
    """La dirección de un guion, con la huella de su contenido (la usa el `<script src>` de todas las páginas)."""
    return f"/static/{nombre_del_guion(guion)}"


def buscar(nombre: str) -> Archivo | None:
    """El archivo fijo con ese nombre exacto, o `None` si no está en la lista."""
    if nombre == nombre_del_estilo():
        datos, huella = _leer(ESTILO)
        return Archivo(datos, "text/css; charset=utf-8", f'"{huella}"', CACHE_CON_HUELLA)
    for guion, ruta in GUIONES.items():
        if nombre == nombre_del_guion(guion):
            datos, huella = _leer(ruta)
            return Archivo(datos, TIPO_DE_GUION, f'"{huella}"', CACHE_CON_HUELLA)
    tipo = FIJOS.get(nombre)
    if tipo is None:
        return None
    datos, huella = _leer(STATIC_DIR / nombre)
    return Archivo(datos, tipo, f'"{huella}"', CACHE_DE_FIJOS)


def coincide_etag(si_ninguna_coincide: str, etag: str) -> bool:
    """¿El navegador ya tiene esta versión? (`If-None-Match` puede traer varias, o `*`, o la marca débil `W/`)."""
    candidatas = [parte.strip().removeprefix("W/") for parte in si_ninguna_coincide.split(",")]
    return "*" in candidatas or etag in candidatas


def acepta_gzip(accept_encoding: str) -> bool:
    """¿El cliente acepta gzip? (`gzip;q=0` lo rechaza)."""
    for parte in accept_encoding.split(","):
        nombre, *parametros = (p.strip() for p in parte.split(";"))
        if nombre.lower() not in ("gzip", "x-gzip"):
            continue
        for parametro in parametros:
            clave, _, valor = parametro.partition("=")
            if clave.strip().lower() == "q":
                try:
                    return float(valor) > 0
                except ValueError:
                    return False
        return True
    return False


def comprimible(tipo: str) -> bool:
    """¿Es un tipo de texto que se comprime? (de eso depende `Vary: Accept-Encoding`)."""
    return tipo.split(";")[0].strip().lower() in COMPRIMIBLES


def comprimir(datos: bytes, tipo: str, accept_encoding: str) -> bytes | None:
    """Los datos en gzip si el cliente lo acepta, el tipo es de texto y vale la pena; si no, `None`."""
    if len(datos) < MINIMO_PARA_COMPRIMIR or not acepta_gzip(accept_encoding):
        return None
    if not comprimible(tipo):
        return None
    return gzip.compress(datos, compresslevel=5, mtime=0)
