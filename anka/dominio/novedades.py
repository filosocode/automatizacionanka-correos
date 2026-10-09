"""Novedades del dashboard (U-25): cuáles piden algo a una persona y por tanto cuentan.

Lógica pura. Lo que la propia persona hizo (guardar un borrador, enviar una respuesta) se anota en el historial del caso
pero no es una novedad que atender.
"""

from __future__ import annotations

# Tipos que piden una acción: el cliente respondió, posible actualización, caso nuevo y respuesta sugerida lista.
REQUIEREN_ACCION = frozenset({"client_reply", "possible_update", "request_received", "draft_suggested"})


def requiere_accion(tipo: object) -> bool:
    """¿Esta novedad pide algo a una persona (y por eso suma al contador)?"""
    return str(tipo) in REQUIEREN_ACCION
