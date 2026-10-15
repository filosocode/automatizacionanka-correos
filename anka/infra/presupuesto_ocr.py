"""Presupuesto de tiempo de OCR por archivo y por caso, y política de no hacer OCR a lo escaneado (U-55)."""

from __future__ import annotations

from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from typing import Any

from anka.dominio.lectores_texto import Unreadable
from anka.infra import settings
from anka.infra.logging_setup import get_logger

log = get_logger(__name__)


class OcrOmitidoPorTiempo(Unreadable):
    """El presupuesto de segundos de OCR (por archivo o por caso) se agotó; se omite el OCR que falta."""


@dataclass
class Presupuesto:
    """Segundos de OCR permitidos (0 = sin tope, el comportamiento de siempre) y lo gastado hasta ahora."""

    por_archivo: float = 0.0
    por_caso: float = 0.0
    omitir_escaneados: bool = False
    gastado_archivo: float = 0.0
    gastado_caso: float = 0.0
    en_archivo: bool = False

    def agotado(self) -> bool:
        """Si ya no queda tiempo de OCR para el archivo en curso o para el caso."""
        return bool((self.por_archivo and self.gastado_archivo >= self.por_archivo)
                    or (self.por_caso and self.gastado_caso >= self.por_caso))

    def consumir(self, segundos: float) -> None:
        """Descuenta el tiempo de un OCR ya hecho."""
        self.gastado_archivo += segundos
        self.gastado_caso += segundos

    def descripcion(self) -> str:
        """Los topes vigentes, para decirlo en el motivo de la lectura parcial."""
        partes = []
        if self.por_archivo:
            partes.append(f"{self.por_archivo:g} s por archivo")
        if self.por_caso:
            partes.append(f"{self.por_caso:g} s por caso")
        return " y ".join(partes) or "sin tope"


_ACTUAL: ContextVar[Presupuesto | None] = ContextVar("presupuesto_ocr", default=None)


def _numero(valor: Any) -> float:
    return float(valor) if isinstance(valor, (int, float)) and not isinstance(valor, bool) and valor > 0 else 0.0


def presupuesto_desde_config(config: Mapping[str, Any]) -> Presupuesto:
    """Lee `ocr_segundos_por_archivo`, `ocr_segundos_por_caso` y `ocr_omitir_escaneados` (por defecto, sin límites)."""
    return Presupuesto(
        por_archivo=_numero(config.get("ocr_segundos_por_archivo")),
        por_caso=_numero(config.get("ocr_segundos_por_caso")),
        omitir_escaneados=config.get("ocr_omitir_escaneados") is True,
    )


def _desde_el_disco() -> Presupuesto:
    try:
        return presupuesto_desde_config(settings.load_config())
    except Exception as exc:    # sin config.json (pruebas, primer arranque) o inválida: el comportamiento de siempre
        log.debug("Sin presupuesto de OCR desde config.json (%s); se lee sin límites", type(exc).__name__)
        return Presupuesto()


def presupuesto_actual() -> Presupuesto:
    """El presupuesto del archivo que se lee; fuera de un contexto, uno sin gastar según `config.json`."""
    return _ACTUAL.get() or _desde_el_disco()


@contextmanager
def presupuesto_de_caso(config: Mapping[str, Any] | None = None) -> Iterator[Presupuesto]:
    """Agrupa la lectura de todos los adjuntos de un caso: el tiempo de OCR se descuenta del mismo presupuesto."""
    anterior = _ACTUAL.get()
    if anterior is not None:
        yield anterior          # un contexto dentro de otro comparte el presupuesto
        return
    presupuesto = presupuesto_desde_config(config) if config is not None else _desde_el_disco()
    token = _ACTUAL.set(presupuesto)
    try:
        yield presupuesto
    finally:
        _ACTUAL.reset(token)


@contextmanager
def presupuesto_de_archivo() -> Iterator[Presupuesto]:
    """Reinicia el tiempo gastado del archivo (no del caso); los archivos dentro de un ZIP comparten el del ZIP."""
    with presupuesto_de_caso() as presupuesto:
        if presupuesto.en_archivo:
            yield presupuesto
            return
        presupuesto.en_archivo, presupuesto.gastado_archivo = True, 0.0
        try:
            yield presupuesto
        finally:
            presupuesto.en_archivo = False
