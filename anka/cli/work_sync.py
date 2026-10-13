"""Puente de solo lectura: importa al dashboard los borradores que Work deja en Gmail. Nunca envía ni modifica Gmail."""
from __future__ import annotations
import argparse
import base64
from datetime import datetime
import hashlib
import html
import json
import re
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, NoReturn

from anka.servicios.gmail_followup import sync_thread_activity
from anka.dominio.configuracion import es_modo_work
from anka.dominio.errores import ErrorDeAnka
from anka.dominio.caso_minimo import caso_minimo
from anka.dominio.correo import header, inbound_messages, normalized_address
from anka.infra.logging_setup import configure_logging, get_logger
from anka.dominio.status import reconocer_estado_de_work
from anka.dominio.verificacion_de_evidencia import avisos_de, comprobar_tabla
from anka.dominio.work_contract import (
    corregir_descuidos_de_tipo, empieza_con_el_encabezado_interno, reconciliar_lectura, usa_verificacion_por_adjuntos,
    validate,
)
from anka.infra import instantaneas_de_manifiesto
from anka.infra import settings
from anka.infra.google_services import services
from anka.infra.settings import load_config
from anka.infra.work_heartbeat import other_bridge_running, wait_for_next_cycle, write_heartbeat
from anka.servicios.lectura_de_adjuntos import verificar_lectura
from anka.servicios.work_import import dashboard_upsert

MOTIVO_MAX = 300
SIN_BLOQUE = "Borrador de Anka sin el bloque obligatorio"
GRACIA_SIN_BLOQUE_S = 600   # Work completa el borrador en 1-2 minutos; 10 dan margen sin ocultar un fallo real


@dataclass(frozen=True)
class Rechazado:
    """Un borrador cuyo contenido incumple el contrato: no se arregla reintentando, así que se recuerda por mensaje."""

    motivo: str


START = "[ANKA_DASHBOARD_JSON_V1]"
END = "[/ANKA_DASHBOARD_JSON_V1]"
log = get_logger(__name__)

EXIT_STOP = 2  # el .bat no reinicia con este código
DEFAULT_ACCOUNT = "ankacomercialia@gmail.com"

def body_text(part: dict) -> list[str]:
    """Textos (plano o HTML) de un mensaje de Gmail y de sus partes anidadas."""
    values: list[str] = []
    data = part.get("body", {}).get("data")
    if data and part.get("mimeType") in ("text/plain", "text/html"):
        raw = base64.urlsafe_b64decode(data + "=" * (-len(data) % 4)).decode("utf-8")
        if part.get("mimeType") == "text/html":
            raw = re.sub(r"<br\s*/?>|</(?:p|div|pre)>", "\n", raw, flags=re.I)
            raw = html.unescape(re.sub(r"<[^>]+>", "", raw))
        values.append(raw)
    for child in part.get("parts", []):
        values.extend(body_text(child))
    return values

def extract(message: dict, draft_id: str = "") -> tuple[dict, str] | None:
    """Saca el bloque JSON de Anka de un borrador de Work (con el borrador); `None` si no hay uno válido al inicio."""
    for text in body_text(message.get("payload", {})):
        if START not in text:
            # A Work-style draft without its dashboard JSON cannot be linked to a safe analysis.
            # Report it instead of silently ignoring the customer-facing text.
            if empieza_con_el_encabezado_interno(text):
                raise ValueError(f"{SIN_BLOQUE} ANKA_DASHBOARD_JSON_V1.")
            continue
        # Solo el bloque interno inicial, nunca un bloque citado en respuestas.
        if not empieza_con_el_encabezado_interno(text):
            raise ValueError("El bloque JSON no está en una sección interna inicial válida.")
        if text.count(START) != 1 or text.count(END) != 1:
            raise ValueError("Hay bloques JSON repetidos o incompletos.")
        raw = text.split(START, 1)[1].split(END, 1)[0].strip()
        payload = json.loads(raw)
        if not isinstance(payload, dict):
            raise ValueError("El JSON debe ser un objeto.")
        avisos = corregir_descuidos_de_tipo(payload)  # p. ej. `unresolved: true`: queda vacío y el caso lo advierte
        if avisos:
            payload["_avisos_del_puente"] = avisos
        status, aviso = reconocer_estado_de_work(payload.get("status"))
        if status is not None:
            payload["status"] = status  # la etiqueta oficial (aunque venga sin tildes o con un sinónimo evidente)
            if aviso:
                payload["_avisos_del_puente"] = [*payload.get("_avisos_del_puente", []), aviso]
        errors = validate(payload)      # spec/contrato_work.schema.json es la fuente de verdad
        if errors:
            raise ValueError("Contrato de Work no cumplido: " + " ".join(errors[:5]))
        if payload["thread_id"] != message.get("threadId"):
            raise ValueError("thread_id no coincide con el hilo del borrador.")
        # El correo limpio para el cliente (lo que sigue al bloque JSON) y el borrador de Gmail que
        # lo contiene: el dashboard los muestra en «Respuesta al cliente» y consume el borrador al enviar.
        payload["draft_reply"] = text.split(END, 1)[1].strip()
        payload["draft_id"] = draft_id
        canonical = json.dumps(payload, ensure_ascii=False, sort_keys=True)
        return payload, canonical
    return None

def _list_drafts(gmail: Any) -> list[dict]:
    drafts: list[dict] = []
    page = None
    while True:
        result = gmail.users().drafts().list(userId="me", maxResults=100, pageToken=page).execute()
        drafts.extend(result.get("drafts", []))
        page = result.get("nextPageToken")
        if not page:
            return drafts


def _hora_del_borrador(marca_ms: int) -> str:
    """La hora local (ISO) en que Gmail guardó el borrador, o '' si la marca no es una fecha utilizable."""
    try:
        return datetime.fromtimestamp(marca_ms / 1000).astimezone().isoformat(timespec="seconds") if marca_ms else ""
    except (OSError, OverflowError, ValueError):
        return ""


def _verify_origin(gmail: Any, payload: dict) -> dict:
    """Verifica el origen de contratos con preflight; un fallo de acceso se reintenta, nunca se inventa."""
    if (not payload.get("DRIVE_PREFLIGHT") and payload.get("skill_version") != "PRECISO_V4.5_DRIVE"
            and not usa_verificacion_por_adjuntos(payload)):
        return payload  # compatibilidad con borradores anteriores al contrato auditable
    source = gmail.users().messages().get(userId="me", id=payload["message_id"], format="metadata").execute()
    if source.get("id") != payload["message_id"] or source.get("threadId") != payload["thread_id"]:
        raise ValueError("El mensaje origen no coincide con el hilo del borrador.")
    if "DRAFT" in source.get("labelIds", []) or "SENT" in source.get("labelIds", []):
        raise ValueError("El mensaje origen es un borrador o un mensaje enviado, no una solicitud recibida.")
    sender = normalized_address(header(source, "From"))
    declared = normalized_address(str(payload.get("internal_sender") or payload["client_email"]))
    if not sender or sender != declared:
        raise ValueError("El remitente origen no coincide con el contacto o reenviador declarado.")
    stamp = int(source.get("internalDate") or 0)
    if stamp <= 0:
        raise ValueError("No se pudo verificar la recepción del mensaje origen.")
    return {**payload, "received_at": datetime.fromtimestamp(stamp / 1000).astimezone().isoformat(),
            "_source_internal_date": stamp, "received_at_source": "Gmail internalDate"}


def _origin_metadata(payload: dict, stamp: int) -> dict:
    """Orden y versión verificables del análisis; la hora de edición del borrador no es la recepción."""
    return {"source_stamp": payload.get("_source_internal_date", 0), "draft_stamp": stamp,
            "message_id": payload["message_id"], "complete": payload.get("analysis_complete") is True,
            "version": payload.get("skill_version", ""),
            "revision": (payload.get("DRIVE_PREFLIGHT") or {}).get("manifest_revision")}


def _should_replace(new: dict, old: dict) -> bool:
    """No retrocede al mensaje anterior ni a un bloqueo de la misma revisión ya finalizada."""
    if new["source_stamp"] and old.get("source_stamp"):
        if new["source_stamp"] != old["source_stamp"]:
            return bool(new["source_stamp"] > old["source_stamp"])
    same_revision = all(new.get(key) == old.get(key) for key in ("message_id", "version", "revision"))
    if same_revision and new["complete"] != old.get("complete", False):
        return bool(new["complete"])
    return bool(new["draft_stamp"] >= old.get("draft_stamp", 0))


def _verificar_con_el_expediente(payload: dict, instantaneas: Path) -> list[str]:
    """Verifica la lectura declarada (T-37) y la tabla (T-38) contra lo que el preparador guardó del hilo."""
    manifiesto = instantaneas_de_manifiesto.cargar(instantaneas, payload["thread_id"])
    avisos = reconciliar_lectura(payload, manifiesto)
    textos = instantaneas_de_manifiesto.cargar_textos(
        instantaneas, payload["thread_id"], (manifiesto or {}).get("revision"))
    resultado = comprobar_tabla(payload, textos if manifiesto else None)
    if resultado:
        payload["evidence_check"] = resultado
    return [*avisos, *avisos_de(resultado)]


def _extract_verified(gmail: Any, message: dict, draft_id: str,
                      instantaneas: Path | None = None, propias: set[str] | None = None) -> tuple[dict, str] | None:
    """Extrae el contrato y recupera metadatos del mensaje origen antes de admitirlo en la caché.

    Un borrador del prompt V6 se verifica contra los adjuntos que Gmail tiene en el hilo (T-41); uno anterior, con la
    instantánea del manifiesto del preparador (T-37). Si Gmail no responde, el borrador se marca
    `_lectura_sin_verificar` y no se recuerda en la caché, para reintentarlo en la siguiente revisión.
    """
    found = extract(message, draft_id)
    if not found:
        return None
    payload = _verify_origin(gmail, found[0])
    if instantaneas is not None:
        if usa_verificacion_por_adjuntos(payload):
            avisos, consultado = verificar_lectura(gmail, payload, propias or {DEFAULT_ACCOUNT}, instantaneas)
            if not consultado:
                payload["_lectura_sin_verificar"] = True
        else:
            avisos = _verificar_con_el_expediente(payload, instantaneas)
        if avisos:
            payload["_avisos_del_puente"] = [*payload.get("_avisos_del_puente", []), *avisos]
    return payload, json.dumps(payload, ensure_ascii=False, sort_keys=True)


def _ahora_ms() -> int:
    """La hora actual en milisegundos, como la marca de un borrador de Gmail (se sustituye en las pruebas)."""
    return int(time.time() * 1000)


def _rechazos_vigentes(rejected: list[dict], candidates: dict, state: dict) -> list[dict]:
    """Los borradores rechazados que hay que mostrar y convertir en caso mínimo (T-36).

    Un borrador sin bloque JSON no es un fallo cuando el hilo ya tiene un análisis válido (es el borrador auxiliar
    que Work deja junto al completo) ni mientras sea reciente (Work puede estar terminando). Los demás rechazos, como
    un JSON roto o campos inválidos, se muestran de inmediato: esperar no los arregla.
    """
    minimos = state.get("_minimos", {})
    ahora = _ahora_ms()
    vigentes = []
    for item in rejected:
        if item["reason"].startswith(SIN_BLOQUE):
            thread = item["thread"]
            if thread in candidates or (state.get(thread) and thread not in minimos):
                continue
            if ahora - int(item["stamp"] or 0) < GRACIA_SIN_BLOQUE_S * 1000:
                continue
        vigentes.append(item)
    return vigentes


def _se_puede_recordar(found: object) -> bool:
    """¿Se guarda este resultado en la caché? No si la lectura quedó sin verificar por un fallo de Gmail."""
    return not (isinstance(found, tuple) and found[0].get("_lectura_sin_verificar"))


def _minimo_desde_gmail(gmail: Any, thread_id: str, motivo: str, propias: set[str]) -> dict | None:
    """El caso mínimo de un hilo con un borrador de Work inválido (T-36); `None` si Gmail no da un mensaje recibido."""
    hilo = gmail.users().threads().get(userId="me", id=thread_id, format="metadata",
                                       metadataHeaders=["From", "Sender", "Subject", "Date"]).execute()
    recibidos = [m for m in inbound_messages(hilo, propias) if "DRAFT" not in m.get("labelIds", [])]
    return caso_minimo(thread_id, recibidos[-1], motivo) if recibidos else None


def _casos_minimos(gmail: Any, rejected: list[dict], candidates: dict, state: dict,
                   propias: set[str]) -> dict[str, str]:
    """Añade a `candidates` el caso mínimo de cada hilo con un borrador rechazado y sin análisis (T-36).

    Devuelve hilo → mensaje del borrador rechazado para anotarlo cuando el caso se importe. No reemplaza un análisis
    importado, no repite el mismo borrador y no consulta Gmail otra vez por un borrador que no cambió.
    """
    minimos = state.setdefault("_minimos", {})
    creados: dict[str, str] = {}
    for item in rejected:
        thread, mensaje = item["thread"], item["message"]
        analizado = bool(state.get(thread)) and thread not in minimos
        if not thread or thread in candidates or analizado or minimos.get(thread) == mensaje:
            continue
        try:
            payload = _minimo_desde_gmail(gmail, thread, item["reason"], propias)
        except Exception as exc:
            log.warning("Hilo %s: no se pudo crear el caso mínimo (%s)", thread, type(exc).__name__, exc_info=True)
            continue
        if payload:
            candidates[thread] = (item["stamp"], (payload, json.dumps(payload, ensure_ascii=False, sort_keys=True)))
            creados[thread] = mensaje
    return creados


def _anotar_minimo(minimos: dict, creados: dict[str, str], thread: str) -> None:
    """Recuerda el borrador rechazado del caso mínimo recién importado; un análisis válido lo borra."""
    if thread in creados:
        minimos[thread] = creados[thread]
    else:
        minimos.pop(thread, None)


def _cargar_estado(checkpoint: Path) -> dict:
    """El estado guardado de la última pasada (huellas, orígenes y casos mínimos por hilo), o vacío si no hay."""
    return json.loads(checkpoint.read_text(encoding="utf-8")) if checkpoint.exists() else {}


def _guardar_estado(checkpoint: Path, state: dict) -> None:
    """Guarda el estado de forma atómica (archivo temporal y reemplazo): un corte no deja un JSON a medias."""
    checkpoint.parent.mkdir(parents=True, exist_ok=True)
    temporary = checkpoint.with_suffix(".tmp")
    temporary.write_text(json.dumps(state), encoding="utf-8")
    temporary.replace(checkpoint)


def _olvidar_borradores_retirados(cache: dict, drafts: list[dict]) -> None:
    """Quita de la caché los borradores que ya no están en Gmail (enviados o borrados)."""
    for stale in set(cache) - {draft["id"] for draft in drafts}:
        del cache[stale]


def _leer_borrador(gmail: Any, draft: dict, cache: dict, instantaneas: Path,
                   propias: set[str]) -> tuple[str | None, int, object]:
    """(message_id, marca, hallazgo) de un borrador: de la caché si Gmail no lo cambió; si no, lo baja y lo verifica.

    Un contenido que incumple el contrato queda como `Rechazado`: reintentar no lo arregla, así que se recuerda por
    mensaje y se vuelve a revisar solo cuando Gmail da un mensaje nuevo (la persona o Work lo corrigieron).
    """
    message_id = draft.get("message", {}).get("id")
    cached = cache.get(draft["id"])
    if cached and message_id and cached[0] == message_id:
        return message_id, cached[1], cached[2]
    message = gmail.users().drafts().get(userId="me", id=draft["id"], format="full").execute()["message"]
    stamp = int(message.get("internalDate", "0"))
    found: object
    try:
        found = _extract_verified(gmail, message, draft["id"], instantaneas, propias)
    except ValueError as exc:
        found = Rechazado(str(exc)[:MOTIVO_MAX])
        log.warning("Borrador %s: rechazado (%s)", draft["id"], type(exc).__name__, exc_info=True)
        print(f"Borrador {draft['id']}: rechazado ({exc}).", flush=True)
    if message_id and _se_puede_recordar(found):
        cache[draft["id"]] = (message_id, stamp, found)
    return message_id, stamp, found


def _clasificar(draft: dict, message_id: str | None, stamp: int, found: object,
                candidates: dict[str, tuple[int, tuple[dict, str]]], rejected: list[dict]) -> None:
    """Anota el borrador como rechazado o como candidato de su hilo (gana el más nuevo según `_should_replace`)."""
    if isinstance(found, Rechazado):
        rejected.append({"draft": draft["id"], "thread": draft.get("message", {}).get("threadId", ""),
                         "reason": found.motivo, "message": message_id or "", "stamp": stamp})
    elif isinstance(found, tuple):
        thread = found[0]["thread_id"]
        previous = candidates.get(thread)
        if previous is None or _should_replace(_origin_metadata(found[0], stamp),
                                               _origin_metadata(previous[1][0], previous[0])):
            candidates[thread] = (stamp, found)


def _hace_falta_importar(state: dict, thread: str, origin: dict, digest: str) -> bool:
    """¿Es más nuevo que lo ya importado y con otro contenido? Si no, importarlo sería retroceder o repetir."""
    previous_origin = state.get("_origins", {}).get(thread)
    if previous_origin and not _should_replace(origin, previous_origin):
        return False
    return state.get(thread) != digest


def _codificar_para_importar(payload: dict, raw: str, stamp: int) -> str:
    """El contrato en base64, con la hora en que el borrador apareció en Gmail (`draft_at`).

    Esa hora viaja aparte del contenido (no entra en la huella): la importación la compara con su hora de llegada para
    medir la demora del puente (T-29).
    """
    hora = _hora_del_borrador(stamp)
    if hora:
        raw = json.dumps({**payload, "draft_at": hora}, ensure_ascii=False, sort_keys=True)
    return base64.urlsafe_b64encode(raw.encode("utf-8")).decode("ascii")


def sync_once(gmail: Any, upsert: Callable[[str], None], checkpoint: Path,
              cache: dict | None = None, propias: set[str] | None = None) -> dict:
    """Una pasada. `cache` (draft_id -> (message_id, stamp, hallazgo)) evita bajar de nuevo
    borradores sin cambios: Gmail da un message id nuevo cada vez que se edita un borrador.

    Un borrador rechazado no deja el hilo fuera del dashboard: si el hilo no tiene un análisis importado se crea un caso
    mínimo con los datos del correo (T-36). `propias` son las direcciones de la cuenta del puente.
    """
    cache = {} if cache is None else cache
    propias = {DEFAULT_ACCOUNT} if propias is None else propias
    instantaneas = checkpoint.parent / "manifiestos"
    state = _cargar_estado(checkpoint)
    drafts = _list_drafts(gmail)
    _olvidar_borradores_retirados(cache, drafts)
    candidates: dict[str, tuple[int, tuple[dict, str]]] = {}
    failures = 0
    rejected: list[dict] = []
    for draft in drafts:
        try:
            message_id, stamp, found = _leer_borrador(gmail, draft, cache, instantaneas, propias)
            _clasificar(draft, message_id, stamp, found, candidates, rejected)
        except Exception as exc:
            failures += 1
            log.warning("Borrador %s: no importado (%s)", draft["id"], type(exc).__name__, exc_info=True)
            print(f"Borrador {draft['id']}: no importado ({type(exc).__name__}: {exc}).", flush=True)
    rejected = _rechazos_vigentes(rejected, candidates, state)
    minimos = state.setdefault("_minimos", {})
    pendientes_minimos = _casos_minimos(gmail, rejected, candidates, state, propias)
    updated = 0
    for thread, (stamp, (payload, raw)) in candidates.items():
        origin = _origin_metadata(payload, stamp)
        digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()
        if not _hace_falta_importar(state, thread, origin, digest):
            continue
        try:
            upsert(_codificar_para_importar(payload, raw, stamp))
            state[thread] = digest
            state.setdefault("_origins", {})[thread] = origin
            _anotar_minimo(minimos, pendientes_minimos, thread)
            _guardar_estado(checkpoint, state)
            updated += 1
            print(f"Dashboard actualizado: {thread}", flush=True)
        except (Exception, SystemExit) as exc:
            failures += 1
            log.warning("Hilo %s: importación fallida (%s); se reintentará", thread, type(exc).__name__, exc_info=True)
            print(f"Hilo {thread}: importación fallida ({type(exc).__name__}). Se reintentará.", flush=True)
    print(f"Revisión: {len(drafts)} borradores; {updated} actualizados; {failures} errores; "
          f"{len(rejected)} rechazados.", flush=True)
    return {"drafts": len(drafts), "updated": updated, "failures": failures, "rejected": rejected}


def fatal(message: str) -> NoReturn:
    """Error que no se arregla reintentando: el .bat no debe reiniciar el puente."""
    print(message, flush=True)
    raise SystemExit(EXIT_STOP)


def main() -> None:
    """Importa los borradores de Work al dashboard una vez (`--once`) o cada N segundos, solo leyendo Gmail."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--once", action="store_true")
    parser.add_argument("--seconds", type=int, default=30)
    args = parser.parse_args()
    if args.seconds < 30:
        parser.error("--seconds debe ser al menos 30")
    # Sin consola (arranque con Windows) los errores quedaban solo en stdout y se perdían: ahora van al registro.
    configure_logging(console=False)
    if not es_modo_work(load_config()):
        fatal("Sincronización detenida: active_mode no es chatgpt_work.")
    if not args.once and other_bridge_running():
        fatal("Ya hay otro puente activo (ver work_sync_heartbeat.json). No se inicia un segundo.")
    account = load_config().get("work_sync_account", DEFAULT_ACCOUNT).lower()
    checkpoint = settings.DATA_DIR / "work_dashboard_sync_state.json"
    cache: dict = {}
    seen: set[str] = set()
    gmail = None
    print("Sincronización Work activa. Solo lectura de Gmail y escritura en dashboard local.", flush=True)
    while True:
        if not es_modo_work(load_config()):
            fatal("Sincronización detenida por cambio de modo.")
        result: dict[str, Any] = {"drafts": 0, "updated": 0, "failures": 0, "events": 0, "rejected": []}
        error = ""
        try:
            if gmail is None:
                gmail, _ = services()
                profile = gmail.users().getProfile(userId="me").execute()
                if profile.get("emailAddress", "").lower() != account:
                    fatal(f"Cuenta incorrecta: se requiere {account}.")
            result = sync_once(gmail, dashboard_upsert, checkpoint, cache, {account})
            try:
                follow = sync_thread_activity(gmail, seen)
                if follow["events"] or follow["moved"] or follow["related"]:
                    print(f"Seguimiento Gmail: {follow['events']} hechos nuevos; "
                          f"{follow['moved']} casos movidos.", flush=True)
                result["events"] = follow["events"] + follow["related"]
            except Exception as exc:  # el seguimiento no debe frenar la importación de borradores
                error = f"Seguimiento: {type(exc).__name__}: {exc}"[:200]
                log.warning("Error en el seguimiento de Gmail", exc_info=True)
                print(error, flush=True)
        except Exception as exc:
            gmail = None  # reconectar en el siguiente ciclo (token vencido, red caída)
            error = f"{type(exc).__name__}: {exc}"[:200]
            log.warning("Error de sincronización; se reconecta en el siguiente ciclo", exc_info=True)
            print(f"Error de sincronización: {error}", flush=True)
        write_heartbeat(args.seconds, error=error, **result)
        if args.once:
            raise SystemExit(1 if error or result["failures"] else 0)
        wait_for_next_cycle(args.seconds)   # o hasta que el dashboard pida revisar ahora

def cli() -> None:
    """Entrada de línea de comandos: Ctrl+C detiene el puente sin mostrar una traza."""
    try:
        main()
    except KeyboardInterrupt:
        print("Sincronización detenida.")
        raise SystemExit(EXIT_STOP) from None
    except ErrorDeAnka as exc:
        raise SystemExit(str(exc)) from exc   # el lanzador lo reinicia (código 1); el mensaje queda en el registro


if __name__ == "__main__":
    cli()
