"""Cancelación de un mensaje de prueba: quién la autoriza, qué se rechaza y los errores que explican por qué (T-83).

Reglas puras, sin acceso a SQLite, Gmail ni Drive. Una cancelación deja una «lápida»: un trabajo en estado
`cancelado` que impide crear, publicar, importar o conciliar ese mensaje. Solo actúa sobre una identidad literal y un
mensaje cuyo asunto lleva la etiqueta de prueba autorizada.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from anka.dominio.errores import ErrorDeAnka

IDENTIFICADOR = re.compile(r"^[A-Za-z0-9_-]{1,128}$")
ETIQUETA = re.compile(r"^\[PI-[A-Z0-9-]{3,40}\]$")
EVENTO_DE_CANCELACION = "trabajo_cancelado"

CANCELADO_AHORA = "cancelado"
YA_CANCELADO = "ya_cancelado"

RECHAZOS = {
    "identidad_invalida": "el thread_id o el message_id no es un identificador literal válido",
    "falta_operador": "falta quien opera",
    "falta_motivo": "falta el motivo",
    "etiqueta_invalida": "la etiqueta de prueba no tiene la forma [PI-…]",
    "mensaje_no_registrado": "el mensaje no está en la cola de recepción (use la cancelación preventiva explícita)",
    "no_es_de_prueba": "el asunto del mensaje no lleva la etiqueta de prueba autorizada: podría ser una solicitud real",
    "mensaje_no_pendiente": "el mensaje ya no está pendiente de interpretación",
    "publicando_en_curso": "el trabajo se está publicando: espere o intervenga una persona",
    "ya_publicado_requiere_persona": "el trabajo ya está publicado (hay hoja en Drive y Work puede estar trabajando)",
    "conciliado_intocable": "el trabajo está conciliado y no se cambia",
    "carrera": "el trabajo cambió de estado durante la cancelación; vuelva a consultar",
}


class CancelacionRechazada(ErrorDeAnka):
    """La cancelación no se hizo; `codigo` es una clave de `RECHAZOS` y no se escribió nada."""

    def __init__(self, codigo: str, detalle: str = "") -> None:
        self.codigo = codigo
        super().__init__(f"{codigo}: {RECHAZOS[codigo]}" + (f" ({detalle})" if detalle else ""))


class MensajeCancelado(ErrorDeAnka):
    """El mensaje tiene una lápida: no se crea, publica, importa ni concilia nada que lo reactive."""


@dataclass(frozen=True)
class Autorizacion:
    """Lo que una persona autoriza, por escrito, para UN mensaje de prueba concreto."""

    thread_id: str
    message_id: str
    operador: str
    motivo: str
    etiqueta: str                 # p. ej. "[PI-T79-PRUEBA-01]": debe estar en el asunto del mensaje registrado
    preventiva: bool = False      # permite cancelar antes de que el receptor registre el mensaje

    def normalizada(self) -> Autorizacion:
        """La autorización con los textos recortados; rechaza identidades no literales y datos que faltan."""
        hilo, mensaje = self.thread_id.strip(), self.message_id.strip()
        if not (IDENTIFICADOR.match(hilo) and IDENTIFICADOR.match(mensaje)):
            raise CancelacionRechazada("identidad_invalida")
        if not self.operador.strip():
            raise CancelacionRechazada("falta_operador")
        if not self.motivo.strip():
            raise CancelacionRechazada("falta_motivo")
        if not ETIQUETA.match(self.etiqueta.strip()):
            raise CancelacionRechazada("etiqueta_invalida")
        return Autorizacion(hilo, mensaje, self.operador.strip(), self.motivo.strip(), self.etiqueta.strip(),
                            self.preventiva)
