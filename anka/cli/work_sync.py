"""Importa JSON de revisión de borradores Gmail. Nunca envía ni modifica Gmail."""
from __future__ import annotations
import argparse
import base64
import hashlib
import html
import json
import re
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any, NoReturn

from anka.servicios.gmail_followup import sync_thread_activity
from anka.dominio.status import canonical_work_status
from anka.dominio.work_contract import validate
from anka.infra.work_heartbeat import other_bridge_running, write_heartbeat

START = "[ANKA_DASHBOARD_JSON_V1]"
END = "[/ANKA_DASHBOARD_JSON_V1]"
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
    """Saca el bloque JSON de Anka del borrador de Work y el borrador que lo contiene; `None` si no hay uno válido al inicio."""
    for text in body_text(message.get("payload", {})):
        if START not in text:
            continue
        # Solo el bloque interno inicial, nunca un bloque citado en respuestas.
        if not text.lstrip().startswith("[REVISAR ANTES DE ENVIAR - BORRAR ESTA SECCION]"):
            raise ValueError("El bloque JSON no está en una sección interna inicial válida.")
        if text.count(START) != 1 or text.count(END) != 1:
            raise ValueError("Hay bloques JSON repetidos o incompletos.")
        raw = text.split(START, 1)[1].split(END, 1)[0].strip()
        payload = json.loads(raw)
        if not isinstance(payload, dict):
            raise ValueError("El JSON debe ser un objeto.")
        status = canonical_work_status(payload.get("status"))
        if status is not None:
            payload["status"] = status  # la etiqueta oficial, aunque Work la escriba sin tildes
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


def sync_once(gmail: Any, upsert: Callable[[str], None], checkpoint: Path,
              cache: dict | None = None) -> dict:
    """Una pasada. `cache` (draft_id -> (message_id, stamp, hallazgo)) evita bajar de nuevo
    borradores sin cambios: Gmail da un message id nuevo cada vez que se edita un borrador."""
    cache = {} if cache is None else cache
    state = json.loads(checkpoint.read_text(encoding="utf-8")) if checkpoint.exists() else {}
    drafts = _list_drafts(gmail)
    for stale in set(cache) - {draft["id"] for draft in drafts}:
        del cache[stale]
    candidates: dict[str, tuple[int, tuple[dict, str]]] = {}
    failures = 0
    for draft in drafts:
        try:
            message_id = draft.get("message", {}).get("id")
            cached = cache.get(draft["id"])
            if cached and message_id and cached[0] == message_id:
                _, stamp, found = cached
            else:
                message = gmail.users().drafts().get(userId="me", id=draft["id"], format="full").execute()["message"]
                found = extract(message, draft["id"])
                stamp = int(message.get("internalDate", "0"))
                if message_id:
                    cache[draft["id"]] = (message_id, stamp, found)
            if found:
                thread = found[0]["thread_id"]
                if thread not in candidates or stamp > candidates[thread][0]:
                    candidates[thread] = (stamp, found)
        except Exception as exc:
            failures += 1
            print(f"Borrador {draft['id']}: no importado ({type(exc).__name__}: {exc}).", flush=True)
    updated = 0
    for thread, (_, (_payload, raw)) in candidates.items():
        digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()
        if state.get(thread) == digest:
            continue
        try:
            encoded = base64.urlsafe_b64encode(raw.encode("utf-8")).decode("ascii")
            upsert(encoded)
            state[thread] = digest
            checkpoint.parent.mkdir(parents=True, exist_ok=True)
            temporary = checkpoint.with_suffix(".tmp")
            temporary.write_text(json.dumps(state), encoding="utf-8")
            temporary.replace(checkpoint)
            updated += 1
            print(f"Dashboard actualizado: {thread}", flush=True)
        except (Exception, SystemExit) as exc:
            failures += 1
            print(f"Hilo {thread}: importación fallida ({type(exc).__name__}). Se reintentará.", flush=True)
    print(f"Revisión: {len(drafts)} borradores; {updated} actualizados; {failures} errores.", flush=True)
    return {"drafts": len(drafts), "updated": updated, "failures": failures}


def fatal(message: str) -> NoReturn:
    """Error que no se arregla reintentando: el .bat no debe reiniciar el puente."""
    print(message, flush=True)
    raise SystemExit(EXIT_STOP)


def main() -> None:
    """Importa los borradores de Work al dashboard una vez (`--once`) o cada N segundos, solo leyendo Gmail."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--once", action="store_true")
    parser.add_argument("--seconds", type=int, default=60)
    args = parser.parse_args()
    if args.seconds < 30:
        parser.error("--seconds debe ser al menos 30")
    from anka.infra.settings import DATA_DIR, load_config
    from anka.infra.google_services import services
    from anka.servicios.quotes import dashboard_upsert
    if load_config().get("active_mode") != "chatgpt_work":
        fatal("Sincronización detenida: active_mode no es chatgpt_work.")
    if not args.once and other_bridge_running():
        fatal("Ya hay otro puente activo (ver work_sync_heartbeat.json). No se inicia un segundo.")
    account = load_config().get("work_sync_account", DEFAULT_ACCOUNT).lower()
    checkpoint = DATA_DIR / "work_dashboard_sync_state.json"
    cache: dict = {}
    seen: set[str] = set()
    gmail = None
    print("Sincronización Work activa. Solo lectura de Gmail y escritura en dashboard local.", flush=True)
    while True:
        if load_config().get("active_mode") != "chatgpt_work":
            fatal("Sincronización detenida por cambio de modo.")
        result = {"drafts": 0, "updated": 0, "failures": 0, "events": 0}
        error = ""
        try:
            if gmail is None:
                gmail, _ = services()
                profile = gmail.users().getProfile(userId="me").execute()
                if profile.get("emailAddress", "").lower() != account:
                    fatal(f"Cuenta incorrecta: se requiere {account}.")
            result = sync_once(gmail, dashboard_upsert, checkpoint, cache)
            try:
                follow = sync_thread_activity(gmail, seen)
                if follow["events"] or follow["moved"] or follow["related"]:
                    print(f"Seguimiento Gmail: {follow['events']} hechos nuevos; "
                          f"{follow['moved']} casos movidos.", flush=True)
                result["events"] = follow["events"] + follow["related"]
            except Exception as exc:  # el seguimiento no debe frenar la importación de borradores
                error = f"Seguimiento: {type(exc).__name__}: {exc}"[:200]
                print(error, flush=True)
        except Exception as exc:
            gmail = None  # reconectar en el siguiente ciclo (token vencido, red caída)
            error = f"{type(exc).__name__}: {exc}"[:200]
            print(f"Error de sincronización: {error}", flush=True)
        write_heartbeat(args.seconds, error=error, **result)
        if args.once:
            raise SystemExit(1 if error or result["failures"] else 0)
        time.sleep(args.seconds)

def cli() -> None:
    """Entrada de línea de comandos: Ctrl+C detiene el puente sin mostrar una traza."""
    try:
        main()
    except KeyboardInterrupt:
        print("Sincronización detenida.")
        raise SystemExit(EXIT_STOP) from None


if __name__ == "__main__":
    cli()
