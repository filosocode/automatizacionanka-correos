"""Reglas puras de la coordinación automática Python–Work (T-55, D-159, D-160): configuración, plan y retroceso.

Nada aquí toca Gmail, Drive ni SQLite. La coordinación nace apagada y su configuración se valida completa.
También guarda la regla del puente general de borradores, que solo corre con autorización expresa (T-56, D-163).
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from anka.dominio.errores import ErrorDeAnka

CLAVES = (
    "work_coordination_enabled", "work_coordination_folder_id", "work_coordination_since",
    "work_coordination_interval_seconds", "work_coordination_max_publish", "work_coordination_max_import",
    "work_coordination_max_archive", "work_coordination_thread_id", "work_coordination_message_id",
    "work_coordination_alert_minutes", "work_general_bridge_enabled",
)
PUENTE_GENERAL = "work_general_bridge_enabled"   # autorización expresa del puente general (T-56); ausente = apagado
ALERTA = (15, 1440, 120)                      # minutos sin respuesta de Work antes de alertar: mínimo, máximo y defecto
INTERVALO = (60, 3600, 300)                   # mínimo, máximo y valor por defecto, en segundos
LIMITES = {                                   # clave: (mínimo, máximo, por defecto) de trabajos por ciclo
    "work_coordination_max_publish": (1, 5, 1),
    "work_coordination_max_import": (1, 5, 1),
    "work_coordination_max_archive": (1, 10, 2),
}
MAXIMO_DE_FALLOS = 5                          # fallos consecutivos de un trabajo antes de pedir a una persona
RETROCESO_BASE = 60                           # segundos; el doble en cada fallo
RETROCESO_TOPE = 3600


@dataclass(frozen=True)
class Plan:
    """Lo que un ciclo puede hacer: dónde, desde cuándo y cuánto (límites por ciclo)."""

    raiz: str
    desde: datetime
    max_publicar: int
    max_importar: int
    max_archivar: int
    thread_id: str = ""
    message_id: str = ""
    intervalo: int = INTERVALO[2]
    alerta_minutos: int = ALERTA[2]

    @property
    def de_laboratorio(self) -> bool:
        """¿Se limita a un solo mensaje (laboratorio)?"""
        return bool(self.thread_id and self.message_id)


def retroceso(fallos: int) -> int:
    """Segundos de espera tras `fallos` fallos consecutivos de un mismo trabajo (60, 120, 240 … hasta una hora)."""
    return int(min(RETROCESO_TOPE, RETROCESO_BASE * 2 ** max(0, fallos - 1)))


def _entero(valor: Any) -> bool:
    return isinstance(valor, int) and not isinstance(valor, bool)


def instante(texto: Any) -> datetime | None:
    """La fecha y hora con zona de un texto ISO; `None` si no es una o no trae zona (sin zona sería ambiguo)."""
    try:
        valor = datetime.fromisoformat(str(texto).strip())
    except ValueError:
        return None
    return valor if valor.tzinfo is not None else None


def puente_general_habilitado(config: Mapping[str, Any]) -> bool:
    """¿Autorizó el dueño el puente general? Solo con `true` exacto: ausente, nulo o mal escrito cuenta como apagado."""
    return config.get(PUENTE_GENERAL) is True


def problemas_de_configuracion(config: Mapping[str, Any]) -> list[str]:
    """Qué está mal en las claves `work_coordination_*` y `work_general_bridge_enabled`; vacío si todo está bien."""
    problemas: list[str] = []
    if not isinstance(config.get(PUENTE_GENERAL, False), bool):
        problemas.append(f"{PUENTE_GENERAL} debe ser booleano.")
    if not isinstance(config.get("work_coordination_enabled", False), bool):
        problemas.append("work_coordination_enabled debe ser booleano.")
    for clave in ("work_coordination_folder_id", "work_coordination_since", "work_coordination_thread_id",
                  "work_coordination_message_id"):
        if not isinstance(config.get(clave, ""), str):
            problemas.append(f"{clave} debe ser texto.")
    intervalo = config.get("work_coordination_interval_seconds", INTERVALO[2])
    if not _entero(intervalo) or not INTERVALO[0] <= intervalo <= INTERVALO[1]:
        problemas.append(
            f"work_coordination_interval_seconds debe ser un entero entre {INTERVALO[0]} y {INTERVALO[1]}.")
    alerta = config.get("work_coordination_alert_minutes", ALERTA[2])
    if not _entero(alerta) or not ALERTA[0] <= alerta <= ALERTA[1]:
        problemas.append(f"work_coordination_alert_minutes debe ser un entero entre {ALERTA[0]} y {ALERTA[1]}.")
    for clave, (minimo, maximo, defecto) in LIMITES.items():
        valor = config.get(clave, defecto)
        if not _entero(valor) or not minimo <= valor <= maximo:
            problemas.append(f"{clave} debe ser un entero entre {minimo} y {maximo}.")
    hilo, mensaje = (str(config.get(c) or "").strip() for c in ("work_coordination_thread_id",
                                                                 "work_coordination_message_id"))
    if bool(hilo) != bool(mensaje):
        problemas.append("work_coordination_thread_id y work_coordination_message_id van juntos o ninguno.")
    if config.get("work_coordination_enabled") is True:
        if config.get("active_mode") != "chatgpt_work":
            problemas.append("work_coordination_enabled requiere active_mode 'chatgpt_work'.")
        if config.get("work_dispatch_enabled") is True:
            problemas.append("work_coordination_enabled y work_dispatch_enabled no pueden estar activos a la vez: "
                             "habría dos despachadores.")
        if not str(config.get("work_coordination_folder_id") or "").strip():
            problemas.append("work_coordination_folder_id es obligatorio al activar la coordinación.")
        if instante(config.get("work_coordination_since")) is None:
            problemas.append("work_coordination_since es obligatorio al activar la coordinación: fecha y hora ISO "
                             "con zona (por ejemplo 2026-10-18T20:00:00-05:00). Lo anterior no se procesa.")
    return problemas


def plan_desde_config(config: Mapping[str, Any]) -> Plan | None:
    """El plan de un ciclo, o `None` si la coordinación está apagada. Una configuración incompleta es un error."""
    if config.get("work_coordination_enabled", False) is not True:
        return None
    problemas = problemas_de_configuracion(config)
    if problemas:
        raise ErrorDeAnka("Coordinación Work mal configurada: " + " ".join(problemas))
    desde = instante(config.get("work_coordination_since"))
    assert desde is not None                       # lo garantiza problemas_de_configuracion
    return Plan(
        raiz=str(config["work_coordination_folder_id"]).strip(), desde=desde,
        max_publicar=int(config.get("work_coordination_max_publish", LIMITES["work_coordination_max_publish"][2])),
        max_importar=int(config.get("work_coordination_max_import", LIMITES["work_coordination_max_import"][2])),
        max_archivar=int(config.get("work_coordination_max_archive", LIMITES["work_coordination_max_archive"][2])),
        thread_id=str(config.get("work_coordination_thread_id") or "").strip(),
        message_id=str(config.get("work_coordination_message_id") or "").strip(),
        intervalo=int(config.get("work_coordination_interval_seconds", INTERVALO[2])),
        alerta_minutos=int(config.get("work_coordination_alert_minutes", ALERTA[2])))


__all__ = ["ALERTA", "CLAVES", "INTERVALO", "LIMITES", "MAXIMO_DE_FALLOS", "PUENTE_GENERAL", "Plan", "instante",
           "plan_desde_config", "problemas_de_configuracion", "puente_general_habilitado", "retroceso"]
