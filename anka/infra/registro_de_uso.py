"""Registro de los tokens que gasta cada análisis del modelo y libro `uso_del_modelo.jsonl` (U-91).

Cada llamada al proveedor suma sus tokens a la medición abierta; al terminar el análisis se deja una línea en el
registro y otra en el libro. Los precios no se inventan: el costo solo se estima si `llm.precios` los trae.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from datetime import datetime

from anka.dominio.configuracion import ConfigLlm
from anka.infra import settings
from anka.infra.logging_setup import get_logger

log = get_logger(__name__)

LIBRO = "uso_del_modelo.jsonl"


@dataclass
class Llamada:
    """Una llamada al proveedor: qué modelo respondió y cuántos tokens contó."""

    proveedor: str
    modelo: str
    entrada: int
    salida: int
    cacheados: int = 0


@dataclass
class Medicion:
    """Las llamadas al modelo hechas mientras está abierta (un análisis puede hacer varias, con modelos distintos)."""

    llm: ConfigLlm
    llamadas: list[Llamada] = field(default_factory=list)


_actual: ContextVar[Medicion | None] = ContextVar("medicion_de_uso", default=None)


@contextmanager
def medir(llm: ConfigLlm) -> Iterator[Medicion]:
    """Abre una medición: las llamadas al modelo que se hagan dentro se suman en ella."""
    medicion = Medicion(llm)
    marca = _actual.set(medicion)
    try:
        yield medicion
    finally:
        _actual.reset(marca)


def _entero(valor: object) -> int:
    """Un conteo de tokens como entero; lo que no sea un número (dato ausente o raro) cuenta cero, no se inventa."""
    return valor if isinstance(valor, int) and not isinstance(valor, bool) and valor > 0 else 0


def registrar(proveedor: str, modelo: str, entrada: object, salida: object, cacheados: object = 0) -> None:
    """Suma una llamada a la medición abierta; sin medición abierta no hace nada."""
    medicion = _actual.get()
    if medicion is not None:
        medicion.llamadas.append(Llamada(proveedor, modelo, _entero(entrada), _entero(salida), _entero(cacheados)))


def _costo(medicion: Medicion) -> float | None:
    """Costo estimado en dólares si config trae `llm.precios` (por millón de tokens); si no, `None`."""
    precios = medicion.llm.get("precios")
    if not isinstance(precios, dict):
        return None
    entrada, salida = precios.get("entrada"), precios.get("salida")
    if isinstance(entrada, bool) or isinstance(salida, bool):
        return None
    if not isinstance(entrada, (int, float)) or not isinstance(salida, (int, float)):
        return None
    total = sum(item.entrada * float(entrada) + item.salida * float(salida) for item in medicion.llamadas)
    return round(total / 1_000_000, 4)


def texto_del_uso(medicion: Medicion) -> str:
    """Una línea para el registro: modelo, llamadas, tokens y, si se conocen los precios, el costo estimado."""
    modelos = ", ".join(dict.fromkeys(item.modelo for item in medicion.llamadas))
    entrada = sum(item.entrada for item in medicion.llamadas)
    salida = sum(item.salida for item in medicion.llamadas)
    llamadas = len(medicion.llamadas)
    texto = (f"Análisis con {modelos}: {llamadas} llamada{'' if llamadas == 1 else 's'}, "
             f"{entrada:,} tokens de entrada y {salida:,} de salida")
    costo = _costo(medicion)
    return texto + (f"; costo estimado US$ {costo:.4f}." if costo is not None else ".")


def anotar(thread_id: str, medicion: Medicion) -> None:
    """Deja constancia de lo que gastó un análisis. Un fallo al escribir se avisa y nunca frena el análisis."""
    if not medicion.llamadas:
        return
    log.info("%s: %s", thread_id, texto_del_uso(medicion))
    linea = {
        "at": datetime.now().astimezone().isoformat(timespec="seconds"), "thread_id": thread_id,
        "proveedor": medicion.llamadas[0].proveedor, "modelo": medicion.llamadas[-1].modelo,
        "llamadas": len(medicion.llamadas), "tokens_entrada": sum(i.entrada for i in medicion.llamadas),
        "tokens_cacheados": sum(i.cacheados for i in medicion.llamadas),
        "tokens_salida": sum(i.salida for i in medicion.llamadas), "costo_usd": _costo(medicion),
    }
    try:
        settings.DATA_DIR.mkdir(parents=True, exist_ok=True)
        with (settings.DATA_DIR / LIBRO).open("a", encoding="utf-8") as libro:
            libro.write(json.dumps(linea, ensure_ascii=False) + "\n")
    except OSError as exc:
        log.warning("No se pudo anotar el uso del modelo en el libro (%s)", type(exc).__name__)
