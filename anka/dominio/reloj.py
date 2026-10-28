"""El reloj de Anka: una sola hora «de ahora» con zona horaria y una forma de ordenar fechas por instante (U-62).

Todo lo que Anka guarda lleva la hora local con su desfase (`2026-10-15T09:30:00-05:00`). Comparar esos textos como
cadenas ordena mal dos fechas con desfases distintos (un correo de Gmail en UTC contra un envío local), así que para
ordenar se usa `instante`/`clave_de_orden`, que comparan el momento real. Los textos ya guardados no cambian.
"""

from __future__ import annotations

from datetime import datetime, UTC


def ahora() -> datetime:
    """El momento actual, con la zona horaria local (nunca una hora «ingenua» sin zona)."""
    return datetime.now().astimezone()


def ahora_iso(timespec: str = "auto") -> str:
    """`ahora()` como texto ISO 8601, con su desfase."""
    return ahora().isoformat(timespec=timespec)


def instante(texto: str | None) -> datetime | None:
    """El texto ISO como momento con zona; sin zona se entiende hora local; vacío o ilegible, `None`."""
    if not texto:
        return None
    try:
        valor = datetime.fromisoformat(str(texto).strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    return valor if valor.tzinfo else valor.astimezone()


def clave_de_orden(texto: str | None) -> tuple[float, str]:
    """Clave para ordenar textos de fecha por instante; lo ilegible queda antes de todo y se ordena como texto."""
    momento = instante(texto)
    if momento is None:
        return (float("-inf"), str(texto or ""))
    return (momento.astimezone(UTC).timestamp(), str(texto or ""))
