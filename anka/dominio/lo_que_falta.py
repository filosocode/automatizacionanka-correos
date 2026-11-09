"""Lo que falta de una actividad, dicho en una frase corta para el comercial y no para quien desarrolla (U-103.2).

Las notas del análisis cuentan de todo: qué archivo llegó, qué fuentes se revisaron, cuál es el siguiente paso interno.
Al comercial solo le sirve lo que sigue pendiente. Aquí se queda con eso y nada más; nunca inventa un dato.
"""

from __future__ import annotations

import re

from anka.dominio.preguntas import sin_nota_interna

LARGO_MAXIMO = 140
_ORACIONES = re.compile(r"(?<=[.;])\s+(?=[A-ZÁÉÍÓÚÑ¿])")
_YA_RECIBIDO = re.compile(
    r"^(?:se\s+(?:aport|recib|adjunt|revis|ley|identific|calcul|incluy|envi|encontr|verific)\w*"
    r"|el\s+cliente\s+(?:aport|adjunt|envi|ley)\w*|(?:ya\s+)?(?:hay|existe|se\s+cuenta\s+con)\b)", re.IGNORECASE)
_PENDIENTE = re.compile(
    r"\b(?:falta|faltan|pendiente|por\s+(?:definir|confirmar|aclarar)|sin\s+(?:definir|confirmar)|"
    r"no\s+(?:se\s+)?(?:indica|indic[oó]|especifica|defini[oó]|hay|cuenta|dice))\b", re.IGNORECASE)
_QUEDA_PENDIENTE = re.compile(
    r"^(?:se\s+mantiene|queda|sigue|permanece)\s+(?:como\s+)?pendiente\s+"
    r"(?:(?:la|el)\s+(?:definici[oó]n|confirmaci[oó]n|precisi[oó]n|aclaraci[oó]n)\s+(?:de\s+)?"
    r"|(?P<verbo>definir|confirmar|aclarar|indicar|precisar)\s+)(?P<resto>.+)$", re.IGNORECASE)
_VERBO_DE = {"definici": "definir", "confirmaci": "confirmar", "precisi": "precisar", "aclaraci": "aclarar"}
_COLA = re.compile(r",\s*(?:la|el|lo)\s+cual\b.*$|,\s*(?:que|cuya|cuyo)\s+el\s+cliente\b.*$", re.IGNORECASE)


def _termina(frase: str) -> str:
    frase = frase.strip().rstrip(" ;:,")
    if len(frase) > LARGO_MAXIMO:
        frase = frase[:LARGO_MAXIMO].rstrip() + "…"
        return frase
    return frase if frase.endswith((".", "…", "?")) else frase + "."


def _como_falta(oracion: str) -> str:
    """«Se mantiene como pendiente la definición de X, la cual…» -> «Falta definir X.»; lo demás queda igual."""
    oracion = _COLA.sub("", oracion.strip())
    if encaja := _QUEDA_PENDIENTE.match(oracion.rstrip(" .;")):
        verbo = encaja.group("verbo")
        if not verbo:
            clave = re.search(r"definici|confirmaci|precisi|aclaraci", oracion, re.IGNORECASE)
            verbo = _VERBO_DE[clave.group(0).lower()] if clave else "definir"
        return f"Falta {verbo.lower()} {encaja.group('resto').strip()}"
    return oracion.rstrip(" .;")


def _oraciones_utiles(nota: str) -> list[str]:
    limpia = " ".join(sin_nota_interna(nota).split())
    return [o for o in _ORACIONES.split(limpia) if o.strip() and not _YA_RECIBIDO.match(o.strip())]


def lo_pendiente(nota: str) -> str:
    """La primera frase de la nota que dice qué sigue pendiente, como «Falta …»; vacío si la nota no dice ninguna."""
    for oracion in _oraciones_utiles(nota):
        if _PENDIENTE.search(oracion):
            return _termina(_como_falta(oracion))
    return ""


def lo_que_dice_la_nota(nota: str) -> str:
    """La nota sin lo ya recibido, las fuentes ni los pasos internos, en una frase corta; vacío si no queda nada."""
    utiles = [_como_falta(o) for o in _oraciones_utiles(nota)]
    return _termina(". ".join(utiles[:2])) if utiles else ""


# Lo que la nota dice como pendiente de verdad: se descartan antes las negaciones («no falta nada», «sin pendientes»).
_NEGADO = re.compile(
    r"\b(?:no|nada|ning[uú]n[ao]?|ya\s+no)\s+(?:\w+\s+){0,2}?(?:falta|faltan|faltante|faltantes|pendiente|pendientes)\b"
    r"|\bsin\s+(?:\w+\s+)?(?:faltantes?|pendientes?)\b", re.IGNORECASE)
_PENDIENTE_ESTRICTO = re.compile(
    r"\b(?:falta|faltan|faltante|faltantes|pendiente|pendientes|por\s+(?:definir|confirmar|aclarar|precisar|validar|verificar)"
    r"|sin\s+(?:definir|confirmar|precisar)|no\s+(?:se\s+)?(?:ha\s+)?(?:indica|indicado|indic[oó]|especifica|especificado|"
    r"defini[oó]|definido|aclarado))\b", re.IGNORECASE)
_DUDA = re.compile(
    r"\bconf[ií]rma(?:r|rla|rlo|rnos|n)?\b|\bconfirme(?:n)?\b|aproximad|\bverificar\b|\brevisar\b|\baclarar\b|"
    r"\bsupuest|\basumid|\bestimad|contradic|\bpor\s+validar\b|\bno\s+coincide", re.IGNORECASE)


def dice_pendiente(nota: str) -> bool:
    """¿La nota dice que algo sigue pendiente? «No falta nada» o «sin pendientes» no lo dicen."""
    sin_negar = _NEGADO.sub(" ", " ".join(sin_nota_interna(nota).split()))
    return bool(_PENDIENTE_ESTRICTO.search(sin_negar))


def deja_dudas(nota: str) -> bool:
    """¿La nota deja algo por confirmar, aproximado o por revisar? Con dudas, una actividad no se da por completa."""
    sin_negar = _NEGADO.sub(" ", " ".join(sin_nota_interna(nota).split()))
    return bool(_PENDIENTE_ESTRICTO.search(sin_negar) or _DUDA.search(sin_negar))
