"""Reintentos con espera creciente de los hilos que no se pueden analizar (T-26): reglas puras, sin base de datos."""

from __future__ import annotations

from datetime import datetime, timedelta

from anka.dominio.analisis import Analisis, Enrutamiento
from anka.dominio.errores import ErrorDeAnka

# Minutos que se espera tras el 1.º, 2.º... fallo. Pasado el último escalón se reintenta una vez al día: un correo
# nunca se abandona, pero tampoco se reintenta cada ciclo gastando modelo y Gmail.
ESPERAS_EN_MINUTOS = (5, 15, 60, 240, 720, 1440)
RETENCION_DIAS = 60  # los registros de fallos más viejos se borran


def espera_tras(fallos: int) -> timedelta:
    """Cuánto esperar antes del siguiente intento, dado el número de fallos acumulados (≥ 1)."""
    escalon = min(max(fallos, 1), len(ESPERAS_EN_MINUTOS)) - 1
    return timedelta(minutes=ESPERAS_EN_MINUTOS[escalon])


def motivo_legible(error: Exception) -> str:
    """Texto del fallo para la persona: el mensaje propio si lo hay; si no, el tipo de error (sin trazas ni rutas)."""
    texto = " ".join(str(error).split())
    if isinstance(error, ErrorDeAnka) or isinstance(error, RuntimeError):
        return texto[:300] or type(error).__name__
    return f"{type(error).__name__}: {texto[:200]}".rstrip(": ")


def resumen_del_fallo(motivo: str, fallos: int, proximo: datetime) -> str:
    """La frase que ve la persona en el caso mientras el hilo no se pueda analizar."""
    return (
        f"No se pudo analizar este correo automáticamente (intento {fallos}): {motivo} "
        f"Se reintentará solo a partir de las {proximo:%H:%M} del {proximo:%d/%m}; el botón «Actualizar correo» lo "
        "reintenta ya. Mientras tanto, revísalo a mano en Gmail."
    )


def analisis_de_fallo(
    asunto: str, remitente: str, recibido: str, enrutamiento: Enrutamiento, motivo: str, fallos: int, proximo: datetime,
) -> Analisis:
    """Análisis mínimo para mostrar el hilo como caso de revisión técnica con el motivo del fallo."""
    return {
        "is_quote_request": True,
        "project_name": asunto or "Correo sin asunto",
        "summary": resumen_del_fallo(motivo, fallos, proximo),
        "activities": [],
        "fields": [],
        "other_questions": [],
        "question_audit": [],
        "scope_dependencies": [],
        "contradictions": [],
        "attachments": [],
        "routing": enrutamiento,
        "email": {"subject": asunto, "from": remitente},
        "received_at": recibido,
        "analysis_failure": {
            "motivo": motivo, "intentos": fallos, "proximo_intento": proximo.isoformat(timespec="seconds"),
        },
    }
