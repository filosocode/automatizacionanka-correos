"""Coordinación automática Python–Work (T-55, D-159 a D-162): un ciclo acotado que archiva, importa y despacha.

Compone piezas que ya existen y no tiene lógica comercial propia: el despacho y el archivado (T-53), la importación
aislada del borrador de Work (T-47, que concilia con T-48/T-49) y la cola de recepción (U-81). SQLite decide qué toca;
Drive y Gmail solo ejecutan. Nada aquí envía correos ni crea borradores.

Además vigila lo que se queda esperando: avisa una vez de un trabajo sin respuesta de Work y recupera, por la misma
ruta de publicación de T-49, la hoja de un trabajo publicado que desapareció de Drive. Con la coordinación activada este
ciclo es el único importador automático: si ve un puente general vivo, no importa.

El estado de los reintentos vive en el proceso (sin migración): tras reiniciar se vuelve a intentar una vez, y es seguro
porque cada paso es idempotente y las alertas dejan un evento por trabajo. Dos ciclos a la vez los impide un candado de
instancia única del sistema operativo.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from anka.dominio.coordinacion_work import MAXIMO_DE_FALLOS, Plan, instante, retroceso
from anka.dominio.trabajo_work import CONCILIADO, PENDIENTE_DE_PUBLICAR, PUBLICADO, PUBLICANDO
from anka.infra import settings
from anka.infra.clientes_google import ClienteDrive
from anka.infra.instancia_unica import InstanciaUnica, OtraInstanciaActiva
from anka.infra.logging_setup import get_logger
from anka.infra.work_heartbeat import other_bridge_running
from anka.servicios import despacho, trabajos_work
from anka.servicios.despacho import Alcance, CarpetaAmbigua

log = get_logger(__name__)

NOMBRE_DEL_CANDADO = "work_coordinacion.lock"
EVENTO_DE_IMPORTACION = "coordinacion_importacion"            # el primer fallo de un trabajo (un evento por tipo)
EVENTO_REQUIERE_PERSONA = "coordinacion_requiere_persona"     # el quinto fallo consecutivo, o demasiadas recuperaciones
EVENTO_SIN_RESPUESTA = "coordinacion_sin_respuesta"           # Work no respondió en el plazo (una sola vez)
EVENTO_HOJA_AUSENTE = "coordinacion_hoja_ausente"             # la hoja del trabajo publicado desapareció (una sola vez)
IMPORTACIONES_SIN_FALLO = ("importado", "sin_borrador")
MAXIMO_DE_RECUPERACIONES = 3                                  # por trabajo y por proceso

Importar = Callable[[str, str], dict[str, Any]]    # (thread_id, message_id) -> resultado de `importar_aislado`


def _ahora() -> datetime:
    return datetime.now().astimezone()


class Coordinador:
    """Ejecuta ciclos de coordinación. Conserva en memoria los fallos y esperas de cada trabajo entre ciclos."""

    def __init__(self, drive: ClienteDrive, importar: Importar, bloqueo: Path | None = None,
                 reloj: Callable[[], float] = time.time, fecha: Callable[[], datetime] = _ahora,
                 puente_activo: Callable[[], bool] = other_bridge_running, paquete_version: str = "") -> None:
        self.drive = drive
        self.paquete_version = paquete_version
        self.importar = importar
        self.reloj = reloj
        self.fecha = fecha
        self.puente_activo = puente_activo
        self.bloqueo = bloqueo or settings.DATA_DIR / NOMBRE_DEL_CANDADO
        self.fallos: dict[str, int] = {}
        self.proximo: dict[str, float] = {}
        self.visto: dict[str, float] = {}
        self.detenidos: set[str] = set()
        self.por_recuperar: set[str] = set()
        self.recuperaciones: dict[str, int] = {}
        self.avisado_puente = False

    def ciclo(self, plan: Plan) -> dict[str, Any]:
        """Un ciclo completo. Devuelve un resumen sin asuntos ni cuerpos; `concurrente` si ya hay otro en marcha."""
        try:
            with InstanciaUnica(self.bloqueo, "ciclo de coordinación Work"):
                informe = self._ciclo(plan)
        except OtraInstanciaActiva:
            return {"resultado": "concurrente"}
        if any(informe[fase] for fase in ("archivados", "importaciones", "publicaciones", "recuperadas", "alertas",
                                          "errores")):
            log.info("Coordinación Work: %s", resumen(informe))
        return informe

    def _ciclo(self, plan: Plan) -> dict[str, Any]:
        informe: dict[str, Any] = {"resultado": "ok", "archivados": [], "importaciones": [], "publicaciones": [],
                                   "recuperadas": [], "alertas": [], "sin_respuesta": [], "errores": [],
                                   "requieren_persona": sorted(self.detenidos)}
        alcance = (plan.thread_id, plan.message_id) if plan.de_laboratorio else None
        carpetas: dict[str, str] | None = None
        try:
            carpetas = despacho.asegurar_carpetas(self.drive, plan.raiz)
        except CarpetaAmbigua:
            informe["errores"].append("carpeta_ambigua")
        except Exception as exc:               # Drive o red: las fases de Drive esperan al ciclo siguiente
            informe["errores"].append(f"carpetas:{type(exc).__name__}")
        if carpetas is not None:
            self._archivar(plan, alcance, carpetas, informe)
        self._importar(plan, alcance, informe)
        self._alertar(plan, alcance, informe)
        if carpetas is not None:
            self._despachar(plan, alcance, carpetas, informe)
        if informe["errores"]:
            informe["resultado"] = "con_errores"
        return informe

    # --- fase a: archivar ----------------------------------------------------------------------------------------

    def _archivar(self, plan: Plan, alcance: tuple[str, str] | None, carpetas: dict[str, str],
                  informe: dict[str, Any]) -> None:
        cerrados = carpetas[despacho.NOMBRE_CERRADOS]
        pendientes = [t for t in trabajos_work.trabajos_por_estado([CONCILIADO], None, alcance)
                      if t["folder_id"] != cerrados]
        for trabajo in pendientes[:plan.max_archivar]:
            try:
                informe["archivados"].append({"job_id": trabajo["job_id"],
                                              "resultado": despacho.archivar_trabajo(self.drive, trabajo, cerrados)})
            except Exception as exc:           # se reintenta en el ciclo siguiente
                informe["archivados"].append({"job_id": trabajo["job_id"], "resultado": "error"})
                informe["errores"].append(f"archivar:{type(exc).__name__}")
                log.warning("No se pudo archivar el trabajo %s (%s)", trabajo["job_id"], type(exc).__name__)

    # --- fase b: importar ----------------------------------------------------------------------------------------

    def _importar(self, plan: Plan, alcance: tuple[str, str] | None, informe: dict[str, Any]) -> None:
        if self.puente_activo():                # un solo importador (D-161): con un puente general vivo no se importa
            informe["errores"].append("puente_general_activo")
            if not self.avisado_puente:
                self.avisado_puente = True
                log.warning("Coordinación Work: hay un puente general de borradores en marcha; no se importa hasta "
                            "que se detenga (es el único importador permitido con la coordinación activa)")
            return
        self.avisado_puente = False
        ahora = self.reloj()
        candidatos = [t for t in trabajos_work.trabajos_por_estado([PUBLICADO], None, alcance)
                      if t["job_id"] not in self.detenidos and self.proximo.get(t["job_id"], 0.0) <= ahora]
        candidatos.sort(key=lambda t: self.visto.get(t["job_id"], 0.0))       # el consultado hace más va primero
        for trabajo in candidatos[:plan.max_importar]:
            job_id = trabajo["job_id"]
            self.visto[job_id] = ahora
            try:
                resultado = self.importar(trabajo["thread_id"], trabajo["message_id"])
            except Exception as exc:           # Gmail o red; el importador ya cubre sus errores esperables
                resultado = {"resultado": "fallo_de_importacion", "error": type(exc).__name__}
            veredicto = str(resultado.get("resultado"))
            if veredicto == "importado" and (trabajos_work.obtener(job_id) or {}).get("state") != CONCILIADO:
                veredicto = "importado_sin_conciliar"                          # no se da por hecho sin comprobarlo
            informe["importaciones"].append({"job_id": job_id, "resultado": veredicto})
            if veredicto == "importado":
                self._sin_fallos(job_id)
            elif veredicto == "sin_borrador":
                self._comprobar_hoja(trabajo, informe)
            elif veredicto not in IMPORTACIONES_SIN_FALLO:
                self._fallo(job_id, veredicto, ahora, informe)

    def _sin_fallos(self, job_id: str) -> None:
        self.fallos.pop(job_id, None)
        self.proximo.pop(job_id, None)

    def _fallo(self, job_id: str, veredicto: str, ahora: float, informe: dict[str, Any]) -> None:
        """Suma un fallo consecutivo: espera con retroceso y, al quinto, deja el trabajo para una persona."""
        cuenta = self.fallos.get(job_id, 0) + 1
        self.fallos[job_id] = cuenta
        self.proximo[job_id] = ahora + retroceso(cuenta)
        requiere_persona = cuenta >= MAXIMO_DE_FALLOS
        if requiere_persona:
            self.detenidos.add(job_id)
            informe["requieren_persona"] = sorted(self.detenidos)
        if cuenta == 1:
            trabajos_work.anotar_evento(job_id, EVENTO_DE_IMPORTACION, {"resultado": veredicto})
        if requiere_persona:
            trabajos_work.anotar_evento(job_id, EVENTO_REQUIERE_PERSONA, {"resultado": veredicto, "fallos": cuenta})
        log.warning("Coordinación Work: la importación del trabajo %s terminó en %s (fallo %d%s)", job_id, veredicto,
                    cuenta, "; requiere a una persona" if requiere_persona else "")

    def _comprobar_hoja(self, trabajo: dict[str, Any], informe: dict[str, Any]) -> None:
        """Sin borrador, ¿sigue la hoja del trabajo? Si no, se marca para recuperarla al despachar (punto 13)."""
        job_id = trabajo["job_id"]
        try:
            presente = trabajos_work.hoja_guardada(self.drive, trabajo) is not None
        except Exception as exc:               # no se sabe: un error de Drive no es una ausencia
            informe["errores"].append(f"hoja:{type(exc).__name__}")
            return
        if presente:
            self.por_recuperar.discard(job_id)
            return
        self.por_recuperar.add(job_id)
        if trabajos_work.anotar_evento(job_id, EVENTO_HOJA_AUSENTE,
                                       {"sheet_file_id": trabajo.get("sheet_file_id", "")}):
            log.warning("Coordinación Work: la hoja del trabajo %s desapareció de Drive; se recuperará", job_id)

    # --- alertas -------------------------------------------------------------------------------------------------

    def _alertar(self, plan: Plan, alcance: tuple[str, str] | None, informe: dict[str, Any]) -> None:
        """Los publicados que llevan más del plazo sin respuesta de Work: estado actual y aviso de una sola vez."""
        limite = self.fecha() - timedelta(minutes=plan.alerta_minutos)
        for trabajo in trabajos_work.trabajos_por_estado([PUBLICADO], None, alcance):
            publicado = instante(trabajo.get("published_at"))
            if publicado is None or publicado > limite:
                continue
            job_id = trabajo["job_id"]
            informe["sin_respuesta"].append(job_id)
            if trabajos_work.anotar_evento(job_id, EVENTO_SIN_RESPUESTA, {"minutos": plan.alerta_minutos}):
                informe["alertas"].append(job_id)
                log.warning("Coordinación Work: el trabajo %s lleva más de %d min publicado sin respuesta de Work",
                            job_id, plan.alerta_minutos)

    # --- fase c: despachar y recuperar ---------------------------------------------------------------------------

    def _despachar(self, plan: Plan, alcance: tuple[str, str] | None, carpetas: dict[str, str],
                   informe: dict[str, Any]) -> None:
        ahora = self.reloj()
        recuperables = [t for t in trabajos_work.trabajos_por_estado([PUBLICADO], None, alcance)
                        if t["job_id"] in self.por_recuperar and t["job_id"] not in self.detenidos]
        a_reintentar = [t for t in trabajos_work.trabajos_por_estado([PENDIENTE_DE_PUBLICAR, PUBLICANDO], None, alcance)
                        if t["attempts"] < despacho.MAXIMO_DE_INTENTOS
                        and (t["state"] == PENDIENTE_DE_PUBLICAR or (t["claim_until"] or 0) <= ahora)]
        cola = [(t["thread_id"], t["message_id"], t["job_id"] if t["state"] == PUBLICADO else "")
                for t in recuperables + a_reintentar]
        cola += [(m["thread_id"], m["message_id"], "") for m in trabajos_work.mensajes_nuevos(plan.desde, alcance)]
        for hilo, mensaje, recuperando in cola[:plan.max_publicar]:
            try:
                publicacion = despacho.despachar_mensaje(self.drive, Alcance(hilo, mensaje), carpetas, self.reloj,
                                                      self.paquete_version)
            except Exception as exc:
                informe["errores"].append(f"despachar:{type(exc).__name__}")
                log.warning("No se pudo despachar un mensaje (%s)", type(exc).__name__)
                continue
            elemento = {"job_id": publicacion.get("job_id", ""), "resultado": publicacion.get("resultado", "")}
            informe["recuperadas" if recuperando else "publicaciones"].append(elemento)
            if recuperando:
                self._contar_recuperacion(recuperando, str(elemento["resultado"]), informe)
            if elemento["resultado"] == "error":
                informe["errores"].append("publicar:" + str(publicacion.get("error", "")))

    def _contar_recuperacion(self, job_id: str, resultado: str, informe: dict[str, Any]) -> None:
        """Cuenta la recuperación; tras 3 en este proceso el trabajo queda para una persona. Una hoja que reapareció, se
        recreó o quedó en manos de T-49 (ambigua) o de una persona deja de estar pendiente de recuperar."""
        if resultado in ("creado", "adoptado", "actualizado", "ya_publicado", "ambiguo", "no_elegible",
                         "clasificacion_pendiente"):
            self.por_recuperar.discard(job_id)
        cuenta = self.recuperaciones.get(job_id, 0) + 1
        self.recuperaciones[job_id] = cuenta
        if cuenta >= MAXIMO_DE_RECUPERACIONES and job_id not in self.detenidos:
            self.detenidos.add(job_id)
            self.por_recuperar.discard(job_id)
            informe["requieren_persona"] = sorted(self.detenidos)
            trabajos_work.anotar_evento(job_id, EVENTO_REQUIERE_PERSONA,
                                        {"resultado": "hoja_ausente_repetida", "recuperaciones": cuenta})


def resumen(informe: dict[str, Any]) -> str:
    """Una línea con contadores por fase; nunca asuntos, cuerpos ni direcciones."""
    def contar(fase: str) -> str:
        cuentas: dict[str, int] = {}
        for item in informe.get(fase, []):
            clave = item["resultado"] if isinstance(item, dict) else str(item)
            cuentas[clave] = cuentas.get(clave, 0) + 1
        return ",".join(f"{k}={v}" for k, v in sorted(cuentas.items())) or "-"
    return (f"archivados[{contar('archivados')}] importaciones[{contar('importaciones')}] "
            f"publicaciones[{contar('publicaciones')}] recuperadas[{contar('recuperadas')}] "
            f"alertas[{len(informe.get('alertas', []))}] errores[{contar('errores')}]")


__all__ = ["Coordinador", "EVENTO_DE_IMPORTACION", "EVENTO_HOJA_AUSENTE", "EVENTO_REQUIERE_PERSONA",
           "EVENTO_SIN_RESPUESTA", "NOMBRE_DEL_CANDADO", "resumen"]
