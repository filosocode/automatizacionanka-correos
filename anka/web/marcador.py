"""El marcador de cambios, calculado una vez por segundo para todas las pestañas (U-48).

Cada pestaña con el canal de avisos (`/eventos`) preguntaba por su cuenta si algo había cambiado cada segundo, y cada
pregunta abre una conexión a la base: con varias pestañas el costo se multiplicaba. Ahora todas leen el mismo valor y
solo una consulta por segundo llega a la base, sin importar cuántas pestañas haya.
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable


class MarcadorCompartido:
    """Recuerda el último valor de `calcular()` durante `vigencia` segundos y lo comparte entre hilos."""

    def __init__(self, calcular: Callable[[], str], vigencia: float = 1.0,
                 reloj: Callable[[], float] = time.monotonic) -> None:
        self._calcular = calcular
        self._vigencia = vigencia
        self._reloj = reloj
        self._lock = threading.Lock()
        self._valor = ""
        self._calculado_en: float | None = None
        self.calculos = 0                  # cuántas veces se consultó de verdad (para las pruebas y el diagnóstico)

    def valor(self) -> str:
        """El marcador; si el recordado es más viejo que la vigencia se calcula de nuevo (un solo hilo a la vez)."""
        with self._lock:
            ahora = self._reloj()
            if self._calculado_en is None or ahora - self._calculado_en >= self._vigencia:
                self._valor = self._calcular()          # si falla, no se guarda nada y el error llega a quien preguntó
                self._calculado_en = ahora
                self.calculos += 1
            return self._valor
