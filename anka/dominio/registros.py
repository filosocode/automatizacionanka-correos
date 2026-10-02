"""Los hechos que se guardan de un caso, como objetos con campos nombrados: `Evento`, `Cambio` y `Vinculo`.

Antes eran diccionarios sacados tal cual de la base: una clave mal escrita solo fallaba en ejecución y nadie
sabía qué traía cada uno. Son inmutables y, como `Caso`, se pueden leer también como mapa (`cambio["state"]`,
`dict(evento)`) para que las plantillas y el código que ya existía sigan funcionando.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator, Mapping
from dataclasses import dataclass, fields
from typing import Any, Self


class MapaDeSoloLectura(Mapping[str, Any]):
    """Un dato inmutable (dataclass) que además se lee como un mapa de sus campos."""

    def __getitem__(self, key: str) -> Any:
        if key not in self._names():
            raise KeyError(key)
        return getattr(self, key)

    def __iter__(self) -> Iterator[str]:
        return iter(self._names())

    def __len__(self) -> int:
        return len(self._names())

    @classmethod
    def _names(cls) -> tuple[str, ...]:
        return tuple(f.name for f in fields(cls))  # type: ignore[arg-type]

    # Campos que la base guarda como 0/1 y que aquí son verdaderos o falsos.
    _FLAGS: tuple[str, ...] = ()

    @classmethod
    def from_row(cls, row: Mapping[str, Any] | sqlite3.Row) -> Self:
        """Arma el objeto desde una fila de la base (ignora las columnas que no son campos)."""
        data = {name: row[name] for name in cls._names()}
        for flag in cls._FLAGS:
            data[flag] = bool(data[flag])
        return cls(**data)


@dataclass(frozen=True)
class Evento(MapaDeSoloLectura):
    """Un hecho del caso en su historial encadenado (quién, cuándo, qué y la huella que lo enlaza al anterior)."""

    kind: str
    source: str
    ref: str
    summary: str
    occurred_at: str
    actor_email: str
    actor_name: str
    hash: str


@dataclass(frozen=True)
class Cambio(MapaDeSoloLectura):
    """Un correo posterior a la T que podría pedir un cambio de alcance, y lo que se hizo con él (R11)."""

    id: int
    thread_id: str
    message_id: str
    sender_email: str
    sender_name: str
    subject: str
    snippet: str
    occurred_at: str
    has_code: bool
    is_contact: bool
    same_company: bool
    state: str
    reply_sent_at: str | None
    contact_notified_at: str | None
    decided_at: str | None
    _FLAGS = ("has_code", "is_contact", "same_company")


@dataclass(frozen=True)
class Vinculo(MapaDeSoloLectura):
    """Un correo de otro hilo que podría pertenecer al caso, a la espera de que una persona lo confirme."""

    id: int
    thread_id: str
    other_thread_id: str
    message_id: str
    sender_email: str
    sender_name: str
    subject: str
    snippet: str
    reason: str
    state: str
    occurred_at: str
    decided_at: str | None
    has_code: bool = False
    _FLAGS = ("has_code",)
