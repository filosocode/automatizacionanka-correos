"""Inicio de sesión del dashboard: verifica usuario y contraseña contra `usuarios.json`, abre y cierra sesiones y
aplica la espera tras intentos fallidos (U-115, D-210)."""

from __future__ import annotations

from anka.dominio.errores import ErrorDeAnka
from anka.dominio.reloj import ahora
from anka.dominio.usuarios import verificar
from anka.infra import sesiones, usuarios_store


class CuentaBloqueada(ErrorDeAnka):
    """Demasiados intentos fallidos seguidos: hay que esperar antes de volver a intentarlo (D-210)."""


class CredencialesInvalidas(ErrorDeAnka):
    """Usuario o contraseña incorrectos; el mensaje no distingue cuál de los dos (E-115d)."""

    def __init__(self) -> None:
        super().__init__("El usuario o la contraseña no son correctos.")


def iniciar_sesion(usuario: str, contraseña: str, recordar: bool) -> str:
    """Verifica las credenciales y, si son correctas, abre una sesión y devuelve su id (el valor de la cookie).

    Levanta `CuentaBloqueada` si el usuario tiene una espera vigente (sin gastar el intento) y `CredencialesInvalidas`
    si el usuario no existe o la contraseña no corresponde (mismo mensaje en los dos casos, E-115d)."""
    usuario = usuario.strip().casefold()
    hasta = sesiones.bloqueada_hasta(usuario)
    if hasta and hasta > ahora():
        espera = int((hasta - ahora()).total_seconds())
        minutos = max(1, (espera + 59) // 60)
        raise CuentaBloqueada(
            f"Demasiados intentos. Espera {minutos} minuto{'s' if minutos != 1 else ''} e inténtalo de nuevo.")
    registro = usuarios_store.obtener(usuario)
    if not registro or not verificar(contraseña, registro):
        sesiones.registrar_fallo(usuario)
        raise CredencialesInvalidas()
    sesiones.registrar_exito(usuario)
    return sesiones.crear(usuario, recordar)


def usuario_de_la_sesion(session_id: str) -> str | None:
    """El usuario con sesión abierta, o `None` sin sesión válida."""
    return sesiones.usuario_de(session_id)


def cerrar_sesion(session_id: str) -> None:
    """Cierra la sesión (E-115h); no falla si ya no existía."""
    sesiones.cerrar(session_id)
