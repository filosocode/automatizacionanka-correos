"""Usuarios del dashboard: validación del nombre, hash de la contraseña y la espera tras varios fallos (U-115).

Lógica pura: no toca disco ni el reloj del sistema (recibe «ahora» como argumento). El hash no es reversible y no
guarda la contraseña en claro; la espera tras fallos (D-210) crece si la persona sigue fallando, para frenar la
adivinanza por fuerza bruta sin estorbar un error normal de tecleo.
"""

from __future__ import annotations

import hashlib
import hmac
import re
import secrets
from collections.abc import Mapping
from datetime import datetime, timedelta
from typing import TypedDict

NOMBRE_VALIDO = re.compile(r"^[a-z0-9][a-z0-9._-]{1,39}$")

ALGORITMO = "pbkdf2_sha256"
ITERACIONES = 310_000       # recomendación vigente de OWASP para PBKDF2-HMAC-SHA256


class RegistroDeUsuario(TypedDict):
    """Lo que se guarda de un usuario en `usuarios.json`: nunca la contraseña en claro."""

    algoritmo: str
    hash: str
    sal: str
    iteraciones: int


def nombre_valido(nombre: str) -> bool:
    """Minúsculas, dígitos, punto, guion o guion bajo; de 2 a 40 caracteres, sin empezar por un símbolo."""
    return bool(NOMBRE_VALIDO.match(nombre))


def generar_hash(contraseña: str, sal: bytes | None = None, iteraciones: int = ITERACIONES) -> RegistroDeUsuario:
    """El registro que se guarda para una contraseña nueva (o al cambiarla): nunca la contraseña en claro."""
    sal = sal if sal is not None else secrets.token_bytes(16)
    derivado = hashlib.pbkdf2_hmac("sha256", contraseña.encode("utf-8"), sal, iteraciones)
    return {"algoritmo": ALGORITMO, "hash": derivado.hex(), "sal": sal.hex(), "iteraciones": iteraciones}


def verificar(contraseña: str, registro: Mapping[str, object]) -> bool:
    """¿La contraseña corresponde al registro guardado? Compara en tiempo constante (sin revelar cuánto coincide)."""
    if registro.get("algoritmo") != ALGORITMO:
        return False
    try:
        sal = bytes.fromhex(str(registro["sal"]))
        iteraciones = int(str(registro["iteraciones"]))
        esperado = bytes.fromhex(str(registro["hash"]))
    except (KeyError, ValueError, TypeError):
        return False
    calculado = hashlib.pbkdf2_hmac("sha256", contraseña.encode("utf-8"), sal, iteraciones)
    return hmac.compare_digest(calculado, esperado)


# --- espera tras intentos fallidos (D-210): 5 fallos seguidos esperan 1 minuto; cada fallo de más dobla la espera.
UMBRAL_DE_FALLOS = 5
ESPERA_BASE = timedelta(minutes=1)
ESPERA_MAXIMA = timedelta(minutes=30)


def espera_tras(fallos: int) -> timedelta:
    """Cuánto hay que esperar tras `fallos` intentos seguidos fallidos (cero si aún no llega al umbral)."""
    if fallos < UMBRAL_DE_FALLOS:
        return timedelta(0)
    exponente = min(fallos - UMBRAL_DE_FALLOS, 20)      # de sobra para llegar al tope sin desbordar el cálculo
    return min(ESPERA_BASE * (2 ** exponente), ESPERA_MAXIMA)


def bloqueada_hasta(fallos: int, ultimo_fallo: datetime | None) -> datetime | None:
    """¿Hasta cuándo está bloqueado el siguiente intento? `None` si no hay bloqueo vigente."""
    if not ultimo_fallo:
        return None
    espera = espera_tras(fallos)
    if espera <= timedelta(0):
        return None
    hasta = ultimo_fallo + espera
    return hasta
