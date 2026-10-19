"""Coordinación automática Python–Work con Gmail y Drive reales (T-55, D-159): un ciclo, o el hilo del lanzador.

Solo corre con `work_coordination_enabled` en `config.json` (apagado por defecto). Lee Gmail y escribe en la carpeta de
laboratorio de Drive y en SQLite; nunca envía correos ni crea borradores. Quien lo lanza es el lanzador (un hilo) o la
orden `work_jobs coordinar`.
"""

from __future__ import annotations

import json
import os
import sqlite3
import subprocess
import sys
from collections.abc import Mapping, Sequence
from datetime import datetime, timedelta
from functools import partial
from typing import Any

from anka.cli import paquete_work
from anka.cli.work_sync import importar_aislado
from anka.dominio.configuracion import es_modo_work
from anka.dominio.coordinacion_work import ALERTA, Plan, instante, plan_desde_config
from anka.dominio.errores import ErrorDeAnka
from anka.infra import settings
from anka.infra.google_services import services
from anka.infra.instancia_unica import leer_informacion
from anka.infra.logging_setup import get_logger
from anka.infra.work_heartbeat import other_bridge_running, pid_alive, read_heartbeat
from anka.servicios import trabajos_work
from anka.servicios.coordinacion import NOMBRE_DEL_CANDADO, Coordinador
from anka.servicios.recepcion import CUENTA, PENDIENTE
from anka.servicios.work_import import dashboard_upsert

log = get_logger(__name__)


def plan_de(config: Mapping[str, Any]) -> Plan | None:
    """El plan del ciclo si la coordinación está activada y el modo es Work; `None` si no toca hacer nada."""
    if not es_modo_work(config):
        return None
    return plan_desde_config(config)


def nuevo_coordinador() -> Coordinador:
    """Un coordinador con Gmail y Drive reales. La cuenta de Gmail debe ser la del puente (`work_sync_account`)."""
    config = settings.load_config()
    gmail, drive = services()
    cuenta = str(config.get("work_sync_account", "ankacomercialia@gmail.com")).lower()
    perfil = gmail.users().getProfile(userId="me").execute()
    if str(perfil.get("emailAddress", "")).lower() != cuenta:
        raise ErrorDeAnka(f"Cuenta incorrecta: la coordinación requiere {cuenta}.")
    instantaneas = settings.DATA_DIR / "manifiestos"
    subir = partial(dashboard_upsert, actualizar_hoja=False)

    def importar(thread_id: str, message_id: str) -> dict[str, Any]:
        return importar_aislado(gmail, subir, instantaneas, thread_id, message_id, {cuenta})

    return Coordinador(drive, importar, paquete_version=paquete_work.version_del_paquete())


def ejecutar_un_ciclo(config: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """Un ciclo con servicios reales; `{"resultado": "desactivado"}` si la coordinación está apagada."""
    config = settings.load_config() if config is None else config
    plan = plan_de(config)
    if plan is None:
        return {"resultado": "desactivado"}
    return nuevo_coordinador().ciclo(plan)


# --- Verificación previa a la activación (T-55, punto 15): solo lectura, sin Google ---------------------------------

CANDADO_DEL_RECEPTOR = "gmail_reception.lock"
TAREA_DE_WORK = ("La tarea programada de Work no se puede comprobar desde Python: confirma a mano que existe, que lee "
                 "PENDIENTES y REFERENCIA_ANKA_V1 y que crea un solo borrador por trabajo.")


def clasificar_procesos(filas: Sequence[Mapping[str, Any]], propio: int) -> dict[str, int]:
    """Cuenta los procesos de Anka por clase a partir de las filas `{ProcessId, ParentProcessId, CommandLine}`.

    Excluye el proceso propio y sus ascendentes (la orden que verifica no se cuenta a sí misma).
    """
    padre = {int(f["ProcessId"]): int(f.get("ParentProcessId") or 0) for f in filas}
    ascendentes, pid = set(), propio
    while pid and pid not in ascendentes:
        ascendentes.add(pid)
        pid = padre.get(pid, 0)
    cuentas: dict[str, int] = {}
    for fila in filas:
        linea = str(fila.get("CommandLine") or "")
        clase = _clase_de_proceso(linea.lower()) if int(fila["ProcessId"]) not in ascendentes else ""
        if clase:
            cuentas[clase] = cuentas.get(clase, 0) + 1
    return dict(sorted(cuentas.items()))


def _clase_de_proceso(linea: str) -> str:
    if "python" not in linea:
        return ""
    if "work_jobs" in linea:
        return {"coordinar": "ciclo_manual", "despachar": "despacho_manual"}.get(
            next((o for o in ("coordinar", "despachar") if o in linea), ""), "")
    if any(m in linea for m in ("work_dashboard_sync.py", "anka.cli.work_sync", "work_sync.py")):
        return "importacion_aislada" if "--only-thread-id" in linea else "puente_general"
    for marcador, clase in (("supervisor.py", "lanzador"), ("app.py review-ui", "dashboard"),
                            ("gmail_reception", "receptor_aislado"), ("work-drive-sync", "preparador")):
        if marcador in linea:
            return clase
    return ""


def listar_procesos() -> list[dict[str, Any]]:
    """Los procesos del sistema (Windows, PowerShell); vacío si no se puede listar."""
    if sys.platform != "win32":
        return []
    orden = ("Get-CimInstance Win32_Process | "
             "Select-Object ProcessId,ParentProcessId,CommandLine | ConvertTo-Json -Compress")
    try:
        salida = subprocess.run(["powershell", "-NoProfile", "-Command", orden],
                                capture_output=True, text=True, timeout=30).stdout
        filas = json.loads(salida or "[]")
    except (OSError, ValueError, subprocess.SubprocessError):
        return []
    return [filas] if isinstance(filas, dict) else list(filas)


def _candado_en_uso(nombre: str) -> bool:
    """¿Lo tiene un proceso vivo? La información del candado solo existe mientras alguien lo sostiene (sin tomarlo)."""
    info = leer_informacion(settings.DATA_DIR / nombre)
    return bool(info and pid_alive(int(info.get("pid") or 0)) and info.get("pid") != os.getpid())


def _revisar_configuracion(config: Mapping[str, Any], bloqueos: list[str], avisos: list[str]) -> None:
    bloqueos += [f"Configuración: {p}" for p in settings.validate_config(config)]
    if not es_modo_work(config):
        bloqueos.append("active_mode no es chatgpt_work.")
    if config.get("work_dispatch_enabled") is True:
        bloqueos.append("work_dispatch_enabled está activo: habría dos despachadores (apágalo).")
    if not str(config.get("work_coordination_folder_id") or "").strip():
        bloqueos.append("Falta work_coordination_folder_id (la raíz con PENDIENTES y CERRADOS).")
    if instante(config.get("work_coordination_since")) is None:
        bloqueos.append("Falta work_coordination_since válida (fecha y hora ISO con zona): sin corte no hay filtro "
                        "histórico.")
    if not (str(config.get("work_coordination_thread_id") or "").strip()
            and str(config.get("work_coordination_message_id") or "").strip()):
        bloqueos.append("El piloto exige un solo mensaje: faltan work_coordination_thread_id y "
                        "work_coordination_message_id.")
    if config.get("work_coordination_enabled") is not True:
        avisos.append("La coordinación está apagada (work_coordination_enabled = false).")
    if config.get("gmail_reception_enabled") is not True:
        avisos.append("La recepción de Gmail está apagada: no llegarán mensajes nuevos a la cola.")
    avisos.append(TAREA_DE_WORK)


def _revisar_procesos(config: Mapping[str, Any], procesos: Mapping[str, int], bloqueos: list[str],
                      avisos: list[str]) -> None:
    if procesos.get("puente_general"):
        bloqueos.append("Hay un puente general de borradores en marcha: debe haber un solo importador (T-55, D-161).")
    elif other_bridge_running():
        bloqueos.append("El latido del puente general sigue vivo: hay otro importador en marcha.")
    if procesos.get("ciclo_manual") or _candado_en_uso(NOMBRE_DEL_CANDADO):
        bloqueos.append("Hay un ciclo de coordinación en marcha (orden manual o candado en uso).")
    if procesos.get("despacho_manual"):
        bloqueos.append("Hay un despacho manual (work_jobs despachar) en marcha.")
    receptor_aislado = procesos.get("receptor_aislado") or _candado_en_uso(CANDADO_DEL_RECEPTOR)
    if receptor_aislado and procesos.get("lanzador") and config.get("gmail_reception_enabled") is True:
        bloqueos.append("Dos receptores de Gmail: el aislado y el del lanzador. Deja uno solo.")
    elif receptor_aislado:
        avisos.append("Hay un receptor de Gmail aislado en marcha: será el único receptor.")
    if procesos.get("lanzador"):
        avisos.append("El lanzador está corriendo: leerá la configuración en 30 s y arrancará la coordinación "
                      "si la activas.")
    if procesos.get("importacion_aislada"):
        avisos.append("Hay una importación aislada manual en marcha (es idempotente).")


def _conexion_de_solo_lectura() -> sqlite3.Connection | None:
    ruta = settings.DATA_DIR / "quotes.sqlite3"
    if not ruta.is_file():
        return None
    db = sqlite3.connect(f"file:{ruta.as_posix()}?mode=ro", uri=True)
    db.row_factory = sqlite3.Row
    return db


def _estado_del_mensaje(db: sqlite3.Connection, config: Mapping[str, Any], desde: datetime | None,
                        bloqueos: list[str], avisos: list[str]) -> dict[str, Any]:
    """Dónde está el mensaje de laboratorio y si el ciclo lo tomaría (sin asuntos ni cuerpos)."""
    hilo, mensaje = (str(config.get(c) or "").strip() for c in ("work_coordination_thread_id",
                                                                 "work_coordination_message_id"))
    if not (hilo and mensaje):
        return {}
    fila = db.execute("SELECT * FROM recepcion_mensajes WHERE account=? AND thread_id=? AND message_id=?",
                      (CUENTA, hilo, mensaje)).fetchone()
    trabajos = trabajos_work.trabajos_por_estado(["pendiente_de_publicar", "publicando", "publicado", "conciliado",
                                                  "ambiguo", "cancelado"], None, (hilo, mensaje), db)
    estado: dict[str, Any] = {"en_cola": fila is not None, "trabajos": [t["state"] for t in trabajos]}
    if trabajos:
        avisos.append(f"El mensaje de laboratorio ya tiene trabajo ({', '.join(estado['trabajos'])}).")
        return estado
    if fila is None:
        bloqueos.append("El mensaje de laboratorio no está en la cola de recepción: la recepción debe registrarlo "
                        "primero.")
        return estado
    estado["pendiente"] = (fila["state"], fila["disposition"]) == PENDIENTE
    estado["despues_del_corte"] = desde is not None and trabajos_work.detectado_desde(fila["detected_at"], desde)
    veredicto = trabajos_work.veredicto_del_mensaje(db, fila)
    estado["elegible"] = veredicto.elegible
    if not estado["pendiente"]:
        bloqueos.append("El mensaje de laboratorio ya no está pendiente de interpretación.")
    if not estado["despues_del_corte"]:
        bloqueos.append("El mensaje de laboratorio fue detectado antes del corte (work_coordination_since): "
                        "no se procesaría.")
    if not veredicto.elegible:
        bloqueos.append(f"El mensaje de laboratorio no es elegible (T-51: {veredicto.motivo_id}).")
    return estado


def _estado_de_cola(config: Mapping[str, Any], ahora: datetime, bloqueos: list[str],
                    avisos: list[str]) -> dict[str, Any]:
    desde = instante(config.get("work_coordination_since"))
    try:
        db = _conexion_de_solo_lectura()
    except sqlite3.Error as exc:
        avisos.append(f"No se pudo abrir la base en solo lectura ({type(exc).__name__}).")
        return {}
    if db is None:
        avisos.append("Aún no existe la base de datos local.")
        return {}
    try:
        pendientes = db.execute("SELECT detected_at FROM recepcion_mensajes WHERE account=? AND state=? "
                                "AND disposition=?", (CUENTA, *PENDIENTE)).fetchall()
        por_estado = {f["state"]: f["n"] for f in db.execute(
            "SELECT state, COUNT(*) AS n FROM trabajos_work GROUP BY state")}
        alerta = timedelta(minutes=int(config.get("work_coordination_alert_minutes", ALERTA[2])))
        publicados = trabajos_work.trabajos_por_estado(["publicado"], None, None, db)
        sin_respuesta = [t["job_id"] for t in publicados
                         if (instante(t.get("published_at")) or ahora) <= ahora - alerta]
        cola: dict[str, Any] = {
            "pendientes": len(pendientes),
            "fuera_del_corte": sum(1 for f in pendientes if desde is not None
                                   and not trabajos_work.detectado_desde(f["detected_at"], desde)),
            "elegibles_nuevos": len(trabajos_work.mensajes_nuevos(desde, None, db)) if desde else None,
            "trabajos_por_estado": por_estado, "sin_respuesta": sin_respuesta,
        }
        cola["mensaje_de_laboratorio"] = _estado_del_mensaje(db, config, desde, bloqueos, avisos)
        return cola
    except sqlite3.Error as exc:
        avisos.append(f"No se pudo leer la cola ({type(exc).__name__}).")
        return {}
    finally:
        db.close()


def _configuracion_efectiva(config: Mapping[str, Any]) -> dict[str, Any]:
    claves = ("active_mode", "gmail_reception_enabled", "gmail_reception_interval_seconds", "work_dispatch_enabled",
              "work_drive_enabled", "work_coordination_enabled", "work_coordination_since",
              "work_coordination_interval_seconds", "work_coordination_max_publish", "work_coordination_max_import",
              "work_coordination_max_archive", "work_coordination_alert_minutes")
    efectiva = {clave: config.get(clave) for clave in claves}
    efectiva["work_coordination_carpeta_definida"] = bool(str(config.get("work_coordination_folder_id") or "").strip())
    efectiva["work_coordination_mensaje_de_laboratorio_definido"] = bool(
        str(config.get("work_coordination_thread_id") or "").strip()
        and str(config.get("work_coordination_message_id") or "").strip())
    return efectiva


def verificar_piloto(config: Mapping[str, Any], procesos: Sequence[Mapping[str, Any]] | None = None,
                     ahora: datetime | None = None) -> dict[str, Any]:
    """Qué está habilitado y en marcha antes de activar la coordinación. Solo lectura: sin Google, sin escribir.

    `listo` es verdadero únicamente sin bloqueos. Los avisos son cosas que se confirman a mano.
    """
    ahora = ahora or datetime.now().astimezone()
    clases = clasificar_procesos(listar_procesos() if procesos is None else procesos, os.getpid())
    bloqueos: list[str] = []
    avisos: list[str] = []
    _revisar_configuracion(config, bloqueos, avisos)
    _revisar_procesos(config, clases, bloqueos, avisos)
    cola = _estado_de_cola(config, ahora, bloqueos, avisos)
    latido = read_heartbeat()
    return {"listo": not bloqueos, "activada": config.get("work_coordination_enabled") is True,
            "configuracion": _configuracion_efectiva(config), "procesos": clases,
            "candados": {"coordinacion": _candado_en_uso(NOMBRE_DEL_CANDADO),
                         "receptor_aislado": _candado_en_uso(CANDADO_DEL_RECEPTOR),
                         "latido_del_puente": (latido or {}).get("at", "")},
            "cola": cola, "bloqueos": bloqueos, "avisos": avisos}


__all__ = ["clasificar_procesos", "ejecutar_un_ciclo", "listar_procesos", "nuevo_coordinador", "plan_de",
           "verificar_piloto"]
