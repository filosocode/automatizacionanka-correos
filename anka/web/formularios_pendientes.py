"""Lo que la persona escribió en un formulario que no se pudo procesar, para devolvérselo y que no lo pierda (U-31)."""

from __future__ import annotations

import threading
import time

VIGENCIA_S = 15 * 60            # pasado este tiempo se olvida
MAX_CASOS = 50                  # un solo proceso local: el tope evita que la memoria crezca sin límite
MAX_CARACTERES = 60_000         # por campo
# Las rutas donde la persona escribe texto que cuesta rehacer: el correo, la T, las preguntas y la tabla.
RUTAS_CON_TEXTO = frozenset({"/send", "/draft", "/code-issue", "/review-decision", "/activities"})

Form = dict[str, list[str]]

_guardados: dict[str, tuple[float, str, Form]] = {}      # solo en memoria: nunca se escribe a disco
_candado = threading.Lock()
_estado = threading.local()


def guardar(thread_id: str, ruta: str, form: Form) -> None:
    """Guarda lo escrito antes de procesarlo; si todo sale bien, la acción lo limpia."""
    campos = {nombre: [valor[:MAX_CARACTERES] for valor in valores]
              for nombre, valores in form.items() if nombre != "csrf_token"}
    ahora = time.time()
    with _candado:
        for hilo in [h for h, (cuando, _, _) in _guardados.items() if ahora - cuando > VIGENCIA_S]:
            del _guardados[hilo]
        _guardados[thread_id] = (ahora, ruta, campos)
        while len(_guardados) > MAX_CASOS:
            del _guardados[min(_guardados, key=lambda h: _guardados[h][0])]
    _estado.fallo = False


def marcar_fallo() -> None:
    """La acción terminó con un aviso para la persona (no con éxito): lo escrito se conserva."""
    _estado.fallo = True


def hubo_fallo() -> bool:
    """¿La última acción terminó con un aviso? Consulta y reinicia la marca."""
    fallo = bool(getattr(_estado, "fallo", False))
    _estado.fallo = False
    return fallo


def ver(thread_id: str) -> tuple[str, Form] | None:
    """La ruta y lo escrito de ese caso, si sigue vigente. No lo borra: recargar la página lo vuelve a mostrar."""
    with _candado:
        guardado = _guardados.get(thread_id)
        if guardado is None:
            return None
        cuando, ruta, campos = guardado
        if time.time() - cuando > VIGENCIA_S:
            del _guardados[thread_id]
            return None
        return ruta, campos


def limpiar(thread_id: str) -> None:
    """Olvida lo escrito de un caso (la acción salió bien o ya no aplica)."""
    with _candado:
        _guardados.pop(thread_id, None)


def limpiar_todo() -> None:
    """Olvida todo (para las pruebas)."""
    with _candado:
        _guardados.clear()


def cantidad() -> int:
    """Cuántos casos tienen algo guardado."""
    with _candado:
        return len(_guardados)
