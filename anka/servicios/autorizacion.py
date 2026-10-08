"""Estado de la autorización de Google y su renovación desde el dashboard.

El consentimiento de Google (iniciar sesión y aceptar permisos) lo hace una persona en el navegador de este equipo;
aquí solo se lanza sin bloquear el dashboard (un hilo con tope de tiempo y sin dobles ejecuciones) y se informa en qué
estado va. Nunca se muestra ni se registra el contenido del token.
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable
from dataclasses import dataclass

from anka.dominio.errores import ErrorDeAnka
from anka.infra.google_services import authorize_interactive, credentials
from anka.infra.logging_setup import get_logger

log = get_logger(__name__)

OK, VENCIDA, EN_CURSO, FALLIDA, DESCONOCIDO = "ok", "vencida", "en_curso", "fallida", "desconocido"
ESPERA_MAXIMA = 300           # segundos que se espera a que la persona complete el consentimiento
ESPERA_VERIFICACION = 5      # segundos máximos que una pantalla espera la verificación
VIGENCIA_OK = 300             # una verificación buena se reutiliza 5 minutos (evita una llamada a Google por pantalla)
VIGENCIA_VENCIDA = 60         # una mala se reverifica pronto: así se nota que ya reautorizaron desde otro lado


@dataclass(frozen=True)
class EstadoDeGoogle:
    """Cómo está la autorización de Google: `ok`, `vencida`, `en_curso`, `fallida` o `desconocido` (sin internet)."""

    estado: str
    texto: str


class ReautorizacionDeGoogle:
    """Verifica la autorización (con caché) y la renueva en segundo plano cuando una persona lo pide."""

    def __init__(self, autorizar: Callable[..., object] = authorize_interactive,
                 verificar: Callable[[], object] = credentials, espera_maxima: int = ESPERA_MAXIMA,
                 reloj: Callable[[], float] = time.monotonic) -> None:
        self._autorizar = autorizar
        self._verificar = verificar
        self._espera = espera_maxima
        self._reloj = reloj
        self._candado = threading.Lock()
        self._hilo: threading.Thread | None = None
        self._ultimo_error = ""
        self._cache: tuple[float, EstadoDeGoogle] | None = None

    def estado(self) -> EstadoDeGoogle:
        """El estado actual; una autorización en curso o fallida manda sobre la verificación guardada."""
        with self._candado:
            if self._hilo and self._hilo.is_alive():
                return EstadoDeGoogle(EN_CURSO, "Esperando que completes el consentimiento en la ventana de Google.")
            if self._ultimo_error:
                return EstadoDeGoogle(FALLIDA, self._ultimo_error)
            if self._cache and self._reloj() < self._cache[0]:
                return self._cache[1]
        estado = self._comprobar()
        with self._candado:
            vigencia = VIGENCIA_OK if estado.estado == OK else VIGENCIA_VENCIDA
            self._cache = (self._reloj() + vigencia, estado)
        return estado

    def _comprobar(self) -> EstadoDeGoogle:
        """Verifica en un hilo aparte: sin internet Google puede tardar mucho y no debe congelar la pantalla."""
        resultado: list[EstadoDeGoogle] = []

        def verificar() -> None:
            try:
                self._verificar()
            except ErrorDeAnka as exc:   # incluye AutorizacionDeGoogle: falta, venció o fue revocada
                resultado.append(EstadoDeGoogle(VENCIDA, str(exc)))
            except Exception as exc:     # p. ej. sin internet: no se sabe, y no se ofrece reautorizar por eso
                log.warning("No se pudo verificar la autorización de Google (%s)", type(exc).__name__)
            else:
                resultado.append(EstadoDeGoogle(OK, "La autorización de Google está vigente."))

        hilo = threading.Thread(target=verificar, name="verificar-google", daemon=True)
        hilo.start()
        hilo.join(ESPERA_VERIFICACION)
        return resultado[0] if resultado else EstadoDeGoogle(
            DESCONOCIDO, "No se pudo verificar la autorización de Google (¿sin conexión?).")

    def iniciar(self) -> str:
        """Abre el consentimiento de Google en segundo plano y devuelve el mensaje para la persona."""
        with self._candado:
            if self._hilo and self._hilo.is_alive():
                return "Ya hay una autorización en curso: completa la ventana de Google que se abrió."
            self._ultimo_error = ""
            self._hilo = threading.Thread(target=self._correr, name="reautorizar-google", daemon=True)
            self._hilo.start()
        return ("Se abrió la ventana de Google en este equipo. Inicia sesión, acepta los permisos y vuelve aquí; "
                f"tienes {self._espera // 60} minutos.")

    def _correr(self) -> None:
        try:
            self._autorizar(timeout_seconds=self._espera)
        except ErrorDeAnka as exc:
            self._fallo(str(exc))
        except Exception as exc:   # la persona cerró la ventana, venció la espera, Google rechazó…
            log.warning("La reautorización de Google no se completó (%s)", type(exc).__name__)
            self._fallo("No se completó la autorización (cerraste la ventana o venció la espera). Inténtalo de nuevo.")
        else:
            with self._candado:
                self._ultimo_error = ""
                self._cache = None          # la próxima consulta vuelve a verificar con el token nuevo
            log.info("Autorización de Google renovada desde el dashboard")

    def _fallo(self, texto: str) -> None:
        with self._candado:
            self._ultimo_error = texto


reautorizacion = ReautorizacionDeGoogle()


def estado_de_google() -> EstadoDeGoogle:
    """Estado de la autorización de Google (con caché breve)."""
    return reautorizacion.estado()


def reautorizar_google() -> str:
    """Lanza la reautorización de Google en segundo plano y devuelve el mensaje para la persona."""
    return reautorizacion.iniciar()
