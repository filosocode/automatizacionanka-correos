"""Puente de solo lectura: importa al dashboard los borradores que Work deja en Gmail. Nunca envía ni modifica Gmail."""
from __future__ import annotations
from anka.infra.clientes_google import ClienteGmail
import argparse
import base64
from datetime import datetime
import hashlib
import html
import json
import re
import tempfile
import time
from collections.abc import Callable, Mapping
from functools import partial
from dataclasses import dataclass
from pathlib import Path
from typing import Any, NoReturn

from anka.servicios.gmail_followup import sync_thread_activity
from anka.dominio import coordinacion_work
from anka.dominio.configuracion import es_modo_work
from anka.dominio.errores import ErrorDeAnka
from anka.dominio.caso_minimo import caso_minimo
from anka.dominio.correo import header, inbound_messages, normalized_address
from anka.dominio.areas_declaradas import pistas_de_area
from anka.dominio.filas_de_hoja import pistas_de_hoja
from anka.infra.logging_setup import configure_logging, get_logger
from anka.dominio.tolerancia_work import Rechazo, comprobar_identidad, leer_json, normalizar_analisis
from anka.dominio.verificacion_de_evidencia import avisos_de, comprobar_tabla
from anka.dominio.work_contract import (
    empieza_con_el_encabezado_interno, reconciliar_lectura, usa_verificacion_por_adjuntos,
)
from anka.infra import instantaneas_de_manifiesto, rechazos_de_work
from anka.infra import settings
from anka.infra.google_services import services
from anka.infra.settings import load_config
from anka.infra.work_heartbeat import other_bridge_running, wait_for_next_cycle, write_heartbeat
from anka.servicios.lectura_de_adjuntos import verificar_lectura
from anka.dominio.cancelacion_work import MensajeCancelado
from anka.servicios.work_import import dashboard_upsert

MOTIVO_MAX = 300
SIN_BLOQUE = "Borrador de Anka sin el bloque obligatorio"
GRACIA_SIN_BLOQUE_S = 600   # Work completa el borrador en 1-2 minutos; 10 dan margen sin ocultar un fallo real


@dataclass(frozen=True)
class Rechazado:
    """Un borrador cuyo contenido incumple el contrato: no se arregla reintentando, así que se recuerda por mensaje."""

    motivo: str
    causa: str = "otro"
    campos: tuple[str, ...] = ()


START = "[ANKA_DASHBOARD_JSON_V1]"
END = "[/ANKA_DASHBOARD_JSON_V1]"
log = get_logger(__name__)

EXIT_STOP = 2  # el .bat no reinicia con este código
DEFAULT_ACCOUNT = "ankacomercialia@gmail.com"

# Etiquetas que el HTML de un borrador usa para dar formato. Las demás (`<Polygon>`, `<coordinates>` de un KML citado)
# son texto del extracto y se conservan. Dentro del bloque JSON solo se quitan las de formato más simples, para que
# ninguna etiqueta del extracto se pierda por llamarse como una de HTML (T-28, D-106).
_ETIQUETAS_DE_HTML = frozenset(
    "a abbr b big blockquote body br caption center code col colgroup dd div dl dt em font h1 h2 h3 h4 h5 h6 head hr "
    "html i img li link meta ol p pre s small span strike strong sub sup table tbody td tfoot th thead title tr u ul"
    .split())
_ETIQUETAS_DE_FORMATO = frozenset("a b br code div em font i p pre span strong u".split())
_ETIQUETA = re.compile(r"</?([A-Za-z][A-Za-z0-9]*)\b[^>]*>")


def _sin_etiquetas(raw: str, quitar: frozenset[str]) -> str:
    """El HTML sin las etiquetas de `quitar`; cualquier otra (`<Polygon>`) queda como texto."""
    raw = re.sub(r"<!--.*?-->|<!doctype[^>]*>", "", raw, flags=re.I | re.S)
    return _ETIQUETA.sub(lambda m: "" if m.group(1).lower() in quitar else m.group(0), raw)


def _html_a_texto(raw: str) -> str:
    """El texto de un borrador HTML: saltos de línea, entidades convertidas y solo las etiquetas de formato fuera."""
    raw = re.sub(r"<br\s*/?>|</(?:p|div|pre)>", "\n", raw, flags=re.I)
    inicio, fin = raw.find(START), raw.rfind(END)
    if 0 <= inicio < fin:
        fin += len(END)
        partes = (_sin_etiquetas(raw[:inicio], _ETIQUETAS_DE_HTML),
                  _sin_etiquetas(raw[inicio:fin], _ETIQUETAS_DE_FORMATO),
                  _sin_etiquetas(raw[fin:], _ETIQUETAS_DE_HTML))
        return html.unescape("".join(partes))
    return html.unescape(_sin_etiquetas(raw, _ETIQUETAS_DE_HTML))


def body_text(part: dict) -> list[str]:
    """Textos (plano o HTML) de un mensaje de Gmail y de sus partes anidadas."""
    values: list[str] = []
    data = part.get("body", {}).get("data")
    if data and part.get("mimeType") in ("text/plain", "text/html"):
        raw = base64.urlsafe_b64decode(data + "=" * (-len(data) % 4)).decode("utf-8")
        values.append(_html_a_texto(raw) if part.get("mimeType") == "text/html" else raw)
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
                raise Rechazo(f"{SIN_BLOQUE} ANKA_DASHBOARD_JSON_V1.", "sin_bloque")
            continue
        # Solo el bloque interno inicial, nunca un bloque citado en respuestas.
        if not empieza_con_el_encabezado_interno(text):
            raise Rechazo("El bloque de datos de Work no está en una sección interna inicial válida del borrador.",
                          "bloque_fuera_de_lugar")
        if text.count(START) != 1 or text.count(END) != 1:
            raise Rechazo("Hay bloques de datos repetidos o incompletos en el borrador.", "bloques_repetidos")
        payload, aviso_json = leer_json(text.split(START, 1)[1].split(END, 1)[0].strip())
        if not isinstance(payload, dict):
            raise Rechazo("El análisis de Work debe ser un objeto, no una lista ni un texto suelto.", "no_es_objeto")
        comprobar_identidad(payload)
        if aviso_json:
            payload["_correcciones_de_work"] = [aviso_json]
        normalizar_analisis(payload)
        if payload["thread_id"] != message.get("threadId"):
            raise Rechazo("El «thread_id» del análisis no coincide con la conversación del borrador.",
                          "hilo_ajeno", ["thread_id"])
        # El correo limpio para el cliente (lo que sigue al bloque JSON) y el borrador de Gmail que
        # lo contiene: el dashboard los muestra en «Respuesta al cliente» y consume el borrador al enviar.
        payload["draft_reply"] = text.split(END, 1)[1].strip()
        payload["draft_id"] = draft_id
        canonical = json.dumps(payload, ensure_ascii=False, sort_keys=True)
        return payload, canonical
    return None


def _list_drafts(gmail: ClienteGmail) -> list[dict]:
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


def _verify_origin(gmail: ClienteGmail, payload: dict, forzar: bool = False) -> dict:
    """Verifica el origen de contratos con preflight; un fallo de acceso se reintenta, nunca se inventa.

    `forzar` (importación aislada, T-47) comprueba el origen aunque el borrador no declare versión ni preflight.
    """
    if (not forzar and not payload.get("DRIVE_PREFLIGHT") and payload.get("skill_version") != "PRECISO_V4.5_DRIVE"
            and not usa_verificacion_por_adjuntos(payload)):
        return payload  # compatibilidad con borradores anteriores al contrato auditable
    source = gmail.users().messages().get(userId="me", id=payload["message_id"], format="metadata").execute()
    if source.get("id") != payload["message_id"] or source.get("threadId") != payload["thread_id"]:
        raise Rechazo("El mensaje origen no coincide con la conversación del borrador.", "origen")
    if "DRAFT" in source.get("labelIds", []) or "SENT" in source.get("labelIds", []):
        raise Rechazo("El mensaje origen es un borrador o un mensaje enviado, no una solicitud recibida.", "origen")
    sender = normalized_address(header(source, "From"))
    declared = normalized_address(str(payload.get("internal_sender") or payload["client_email"]))
    if not sender or sender != declared:
        raise Rechazo("El remitente origen no coincide con el contacto o reenviador declarado.", "origen")
    stamp = int(source.get("internalDate") or 0)
    if stamp <= 0:
        raise Rechazo("No se pudo verificar la recepción del mensaje origen.", "origen")
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
    pistas = pistas_de_area((textos or {}).get("correo") or "", (textos or {}).get("adjuntos") or [])
    pistas.update(pistas_de_hoja((textos or {}).get("adjuntos") or [], payload.get("activities") or []))
    payload.update({clave: valor for clave, valor in pistas.items() if valor})   # T-125, T-126: no entran en la tabla
    if resultado:
        payload["evidence_check"] = resultado
    return [*avisos, *avisos_de(resultado)]


def _extract_verified(gmail: ClienteGmail, message: dict, draft_id: str,
                      instantaneas: Path | None = None, propias: set[str] | None = None,
                      aislado: tuple[str, str] | None = None) -> tuple[dict, str] | None:
    """Extrae el contrato y recupera metadatos del mensaje origen antes de admitirlo en la caché.

    Un borrador del prompt V6 se verifica contra los adjuntos que Gmail tiene en el hilo (T-41); uno anterior, con la
    instantánea del manifiesto del preparador (T-37). Si Gmail no responde, el borrador se marca
    `_lectura_sin_verificar` y no se recuerda en la caché, para reintentarlo en la siguiente revisión.

    `aislado` es el par (thread_id, message_id) de la importación aislada (T-47): el borrador debe declarar ese
    mensaje, su origen se verifica siempre y el texto que se baje de Gmail no se guarda en la carpeta del puente.
    """
    found = extract(message, draft_id)
    if not found:
        return None
    if aislado and (found[0]["thread_id"], found[0]["message_id"]) != aislado:
        raise Rechazo("El borrador declara otro mensaje que el de la importación aislada.", "mensaje_ajeno",
                      ["message_id"])
    payload = _verify_origin(gmail, found[0], forzar=aislado is not None)
    if instantaneas is not None:
        if usa_verificacion_por_adjuntos(payload):
            with tempfile.TemporaryDirectory() as temporal:
                avisos, consultado = verificar_lectura(
                    gmail, payload, propias or {DEFAULT_ACCOUNT}, Path(temporal) if aislado else instantaneas)
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
        if item.get("causa") == "sin_bloque":
            thread = item["thread"]
            if thread in candidates or (state.get(thread) and thread not in minimos):
                continue
            if ahora - int(item["stamp"] or 0) < GRACIA_SIN_BLOQUE_S * 1000:
                continue
        vigentes.append(item)
    return vigentes


def _sin_los_resueltos(rejected: list[dict], candidates: dict, state: dict) -> list[dict]:
    """Quita los rechazos de un hilo que ya tiene un análisis válido igual de nuevo o más (el aviso se limpia solo).

    Cuenta el análisis válido de esta pasada y el ya importado (no el caso mínimo, que nace del propio rechazo). Un
    rechazo más nuevo que el último análisis válido (un mensaje que Work no supo analizar) sigue a la vista.
    """
    minimos = state.get("_minimos", {})
    origenes = state.get("_origins", {})
    vigentes = []
    for item in rejected:
        thread = item["thread"]
        sellos = []
        if thread in candidates:
            sellos.append(int(candidates[thread][0]))
        if thread not in minimos and origenes.get(thread):
            sellos.append(int(origenes[thread].get("draft_stamp") or 0))
        if not thread or not sellos or max(sellos) < int(item["stamp"] or 0):
            vigentes.append(item)
    return vigentes


def _contar_y_filtrar(rejected: list[dict]) -> list[dict]:
    """Anota cada rechazo a la vista en la cuenta del día (una vez) y quita los que la persona dejó para después."""
    for item in rejected:
        rechazos_de_work.registrar(item["draft"], item["message"], item["thread"], item.get("causa", "otro"),
                                   item.get("campos") or (), item["reason"])
    omitidos = rechazos_de_work.pospuestos()
    return [item for item in rejected if (item["draft"], item["message"]) not in omitidos]


def _se_puede_recordar(found: object) -> bool:
    """¿Se guarda este resultado en la caché? No si la lectura quedó sin verificar por un fallo de Gmail."""
    return not (isinstance(found, tuple) and found[0].get("_lectura_sin_verificar"))


def _minimo_desde_gmail(gmail: ClienteGmail, thread_id: str, rechazo: dict, propias: set[str]) -> dict | None:
    """El caso mínimo de un hilo con un borrador de Work inválido (T-36); `None` si Gmail no da un mensaje recibido.

    Del rechazo solo pasan el motivo, los nombres de campo y la fecha.
    """
    hilo = gmail.users().threads().get(userId="me", id=thread_id, format="metadata",
                                       metadataHeaders=["From", "Sender", "Subject", "Date"]).execute()
    recibidos = [m for m in inbound_messages(hilo, propias) if "DRAFT" not in m.get("labelIds", [])]
    return (caso_minimo(thread_id, recibidos[-1], rechazo["reason"], tuple(rechazo.get("campos") or ()),
                        rechazo.get("fecha", "")) if recibidos else None)


def _casos_minimos(gmail: ClienteGmail, rejected: list[dict], candidates: dict, state: dict,
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
            payload = _minimo_desde_gmail(gmail, thread, item, propias)
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


def _leer_borrador(gmail: ClienteGmail, draft: dict, cache: dict, instantaneas: Path,
                   propias: set[str], aislado: tuple[str, str] | None = None) -> tuple[str | None, int, object]:
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
        found = _extract_verified(gmail, message, draft["id"], instantaneas, propias, aislado)
    except Rechazo as exc:
        found = Rechazado(exc.motivo[:MOTIVO_MAX], exc.causa, exc.campos)
        log.warning("Borrador %s: rechazado (%s: %s)", draft["id"], exc.causa, ", ".join(exc.campos) or "-")
        print(f"Borrador {draft['id']}: rechazado ({exc.motivo}).", flush=True)
    except ValueError as exc:     # un texto que no se pudo decodificar, etc.: nunca se muestra el error de Python
        found = Rechazado("El borrador de Work no se pudo leer como texto.", "ilegible")
        log.warning("Borrador %s: rechazado (%s)", draft["id"], type(exc).__name__, exc_info=True)
        print(f"Borrador {draft['id']}: rechazado ({type(exc).__name__}).", flush=True)
    if message_id and _se_puede_recordar(found):
        cache[draft["id"]] = (message_id, stamp, found)
    return message_id, stamp, found


def _clasificar(draft: dict, message_id: str | None, stamp: int, found: object,
                candidates: dict[str, tuple[int, tuple[dict, str]]], rejected: list[dict]) -> None:
    """Anota el borrador como rechazado o como candidato de su hilo (gana el más nuevo según `_should_replace`)."""
    if isinstance(found, Rechazado):
        rejected.append({"draft": draft["id"], "thread": draft.get("message", {}).get("threadId", ""),
                         "reason": found.motivo, "causa": found.causa, "campos": list(found.campos),
                         "message": message_id or "", "stamp": stamp, "fecha": _hora_del_borrador(stamp)[:10]})
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


def importar_aislado(gmail: ClienteGmail, upsert: Callable[[str], None], instantaneas: Path, thread_id: str,
                     message_id: str, propias: set[str] | None = None) -> dict[str, Any]:
    """Importa el borrador de Work de un solo mensaje y nada más (T-47, D-150).

    Solo se descargan los borradores del hilo; deben declarar ese hilo y ese mensaje y su origen se verifica siempre.
    No usa el estado ni el latido del puente, no crea casos mínimos, no sincroniza actividad ni registra rechazos. Con
    más de un borrador válido para el mensaje no importa ninguno. El resultado no lleva texto del correo.
    """
    propias = {DEFAULT_ACCOUNT} if propias is None else propias
    par = (thread_id, message_id)
    resultado: dict[str, Any] = {"thread_id": thread_id, "message_id": message_id, "borradores": [],
                                 "ignorados": [], "rechazados": []}
    candidatos: list[tuple[str, int, tuple[dict, str]]] = []
    for draft in _list_drafts(gmail):
        if draft.get("message", {}).get("threadId") != thread_id:
            continue                                  # otro hilo: ni se descarga
        try:
            _, stamp, found = _leer_borrador(gmail, draft, {}, instantaneas, propias, par)
        except Exception as exc:                      # Gmail no respondió: sin origen verificado no se importa
            return {**resultado, "resultado": "error_de_origen", "borrador": draft["id"],
                    "error": type(exc).__name__}
        if isinstance(found, Rechazado):
            lista = "ignorados" if found.causa in ("mensaje_ajeno", "sin_bloque") else "rechazados"
            resultado[lista].append({"borrador": draft["id"], "causa": found.causa})
        elif isinstance(found, tuple):
            candidatos.append((draft["id"], stamp, found))
        else:
            resultado["ignorados"].append({"borrador": draft["id"], "causa": "sin_bloque"})
    resultado["borradores"] = [item[0] for item in candidatos]
    if len(candidatos) > 1:
        return {**resultado, "resultado": "ambiguo"}
    if not candidatos:
        return {**resultado, "resultado": "rechazado" if resultado["rechazados"] else "sin_borrador"}
    draft_id, stamp, (payload, raw) = candidatos[0]
    try:
        upsert(_codificar_para_importar(payload, raw, stamp))
    except MensajeCancelado:
        return {**resultado, "resultado": "mensaje_cancelado"}      # T-83: la lápida del mensaje manda
    except (Exception, SystemExit) as exc:
        log.warning("Importación aislada fallida (%s)", type(exc).__name__, exc_info=True)
        return {**resultado, "resultado": "fallo_de_importacion", "error": type(exc).__name__}
    return {**resultado, "resultado": "importado", "avisos": len(payload.get("_avisos_del_puente", []))}


def sync_once(gmail: ClienteGmail, upsert: Callable[[str], None], checkpoint: Path,
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
    rejected = _contar_y_filtrar(_sin_los_resueltos(_rechazos_vigentes(rejected, candidates, state), candidates, state))
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
        except MensajeCancelado:
            log.info("Hilo %s: su mensaje está cancelado (T-83); no se importa", thread)
        except (Exception, SystemExit) as exc:
            failures += 1
            log.warning("Hilo %s: importación fallida (%s); se reintentará", thread, type(exc).__name__, exc_info=True)
            print(f"Hilo {thread}: importación fallida ({type(exc).__name__}). Se reintentará.", flush=True)
    print(f"Revisión: {len(drafts)} borradores; {updated} actualizados; {failures} errores; "
          f"{len(rejected)} rechazados.", flush=True)
    return {"drafts": len(drafts), "updated": updated, "failures": failures, "rejected": rejected}


MENSAJE_COORDINACION = ("Sincronización detenida: la coordinación Work (T-55) está activa y es el único importador "
                        "automático de borradores. Apágala (work_coordination_enabled) o usa la importación aislada.")


MENSAJE_SIN_AUTORIZACION = ("Sincronización detenida: el puente general de borradores no está autorizado. Para usarlo, "
                            "pon work_general_bridge_enabled en true (T-56); la importación aislada no lo necesita.")


def coordinacion_activa(config: Mapping[str, Any]) -> bool:
    """¿Está activada la coordinación Python–Work? Entonces este puente general no debe importar (D-161)."""
    return config.get("work_coordination_enabled") is True


def motivo_de_bloqueo(config: Mapping[str, Any]) -> str | None:
    """Por qué este puente general no debe correr con esa configuración, o `None` si puede (T-55, T-56)."""
    if coordinacion_activa(config):
        return MENSAJE_COORDINACION
    if not coordinacion_work.puente_general_habilitado(config):
        return MENSAJE_SIN_AUTORIZACION
    return None


def fatal(message: str) -> NoReturn:
    """Error que no se arregla reintentando: el .bat no debe reiniciar el puente."""
    print(message, flush=True)
    raise SystemExit(EXIT_STOP)


def _importacion_aislada(account: str, thread_id: str, message_id: str) -> NoReturn:
    """Importa un solo borrador y termina: sin estado, latido ni seguimiento del puente general (T-47)."""
    gmail, _ = services()
    profile = gmail.users().getProfile(userId="me").execute()
    if profile.get("emailAddress", "").lower() != account:
        fatal(f"Cuenta incorrecta: se requiere {account}.")
    result = importar_aislado(gmail, partial(dashboard_upsert, actualizar_hoja=False),
                              settings.DATA_DIR / "manifiestos", thread_id, message_id, {account})
    print(json.dumps(result, ensure_ascii=True, sort_keys=True), flush=True)
    raise SystemExit(0 if result["resultado"] == "importado" else 1)


def _pide_importacion_aislada(parser: argparse.ArgumentParser, args: argparse.Namespace) -> bool:
    """¿Se pidió la importación aislada? Si se pidió mal (sin `--once`, una sola opción o vacías), se niega."""
    if args.only_thread_id is None and args.only_message_id is None:
        return False
    if not args.once:
        parser.error("--only-thread-id y --only-message-id solo se usan con --once")
    if not (args.only_thread_id or "").strip() or not (args.only_message_id or "").strip():
        parser.error("--only-thread-id y --only-message-id son obligatorios juntos y no pueden estar vacíos")
    return True


def main() -> None:
    """Importa los borradores de Work al dashboard una vez (`--once`) o cada N segundos, solo leyendo Gmail."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--once", action="store_true")
    parser.add_argument("--seconds", type=int, default=30)
    parser.add_argument("--only-thread-id", help="importa solo el borrador de este hilo (T-47)")
    parser.add_argument("--only-message-id", help="y solo el que declara este mensaje (T-47)")
    args = parser.parse_args()
    if args.seconds < 30:
        parser.error("--seconds debe ser al menos 30")
    aislado = _pide_importacion_aislada(parser, args)
    # Sin consola (arranque con Windows) los errores quedaban solo en stdout y se perdían: ahora van al registro.
    configure_logging(console=False)
    config = load_config()
    if not es_modo_work(config):
        fatal("Sincronización detenida: active_mode no es chatgpt_work.")
    if not args.once and other_bridge_running():
        fatal("Ya hay otro puente activo (ver work_sync_heartbeat.json). No se inicia un segundo.")
    account = load_config().get("work_sync_account", DEFAULT_ACCOUNT).lower()
    if aislado:
        _importacion_aislada(account, args.only_thread_id.strip(), args.only_message_id.strip())
    if (motivo := motivo_de_bloqueo(config)) is not None:
        fatal(motivo)
    checkpoint = settings.DATA_DIR / "work_dashboard_sync_state.json"
    cache: dict = {}
    seen: set[str] = set()
    gmail = None
    print("Sincronización Work activa. Solo lectura de Gmail y escritura en dashboard local.", flush=True)
    while True:
        config = load_config()
        if not es_modo_work(config):
            fatal("Sincronización detenida por cambio de modo.")
        if (motivo := motivo_de_bloqueo(config)) is not None:
            fatal(motivo)
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
