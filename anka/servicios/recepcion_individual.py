"""Registro individual de un mensaje de prueba en la cola de recepción, sin sondeo general ni cursor (T-58, D-165)."""

from __future__ import annotations

import base64
import re
import time
from datetime import datetime
from typing import Any

from anka.dominio.configuracion import Configuracion
from anka.dominio.correo import contenido_nuevo, normalized_address, valid_email_address
from anka.dominio.errores import ErrorDeAnka
from anka.infra import settings
from anka.infra.clientes_google import ClienteGmail
from anka.infra.store import lectura, transaccion
from anka.servicios import recepcion
from anka.servicios.recepcion import CUENTA

MARCA_DE_PRUEBA = re.compile(r"PRUEBA-[A-Z0-9]+(?:-[A-Z0-9]+)*")
DECLARACION_FICTICIA = "prueba ficticia"


def _texto_plano(payload: dict) -> str:
    """El texto de las partes `text/plain` de un mensaje (solo para verificar la marca; nunca se guarda)."""
    partes = []
    if payload.get("mimeType") == "text/plain" and payload.get("body", {}).get("data"):
        partes.append(base64.urlsafe_b64decode(payload["body"]["data"]).decode("utf-8", "replace"))
    for hija in payload.get("parts", []) or []:
        partes.append(_texto_plano(hija))
    return "\n".join(partes)


def _problemas_del_texto(message: dict, marca: str, con_expediente: bool) -> list[str]:
    """Marca y declaración ficticia. Un hilo SIN expediente las exige como siempre (asunto y cuerpo completo); uno CON
    expediente admite la marca en una línea exacta «Identificador:» del contenido NUEVO, sin mirar el historial."""
    texto = _texto_plano(message.get("payload", {}))
    if not con_expediente:
        problemas = []
        if marca not in recepcion._headers(message).get("subject", ""):
            problemas.append("la marca no está en el asunto")
        cuerpo = " ".join(texto.casefold().split())
        if f"identificador: {marca}".casefold() not in cuerpo:
            problemas.append("el cuerpo no trae «Identificador: <marca>»")
        if DECLARACION_FICTICIA not in cuerpo:
            problemas.append("el cuerpo no se declara «prueba ficticia»")
        return problemas
    nuevo = contenido_nuevo(texto)
    if nuevo is None:
        return ["no se puede separar con fiabilidad el contenido nuevo del historial citado"]
    linea = re.compile(rf"identificador:\s*{re.escape(marca)}\.?", re.IGNORECASE)
    problemas = []
    if not any(linea.fullmatch(" ".join(renglon.split())) for renglon in nuevo.splitlines()):
        problemas.append("el contenido nuevo no trae la línea «Identificador: <marca>»")
    if DECLARACION_FICTICIA not in " ".join(nuevo.casefold().split()):
        problemas.append("el contenido nuevo no se declara «prueba ficticia»")
    return problemas


def _problemas_del_mensaje_individual(message: dict, thread_id: str, message_id: str, esperado: str,
                                      marca: str, con_expediente: bool = False) -> list[str]:
    """Todo lo que impide registrar este mensaje de prueba; vacío = verificado. No incluye contenido del correo."""
    headers = recepcion._headers(message)
    problemas = []
    if str(message.get("id", "")) != message_id:
        problemas.append("el id devuelto por Gmail no es el pedido")
    if str(message.get("threadId", "")) != thread_id:
        problemas.append("el hilo del mensaje no es el pedido")
    if recepcion._addresses(headers.get("from", "")) != [esperado]:
        problemas.append("el remitente no es el esperado")
    if CUENTA not in recepcion._addresses(headers.get("to", ""), headers.get("cc", ""), headers.get("bcc", "")):
        problemas.append(f"{CUENTA} no es destinataria")
    if {"DRAFT", "SENT"} & set(message.get("labelIds", [])):
        problemas.append("es un borrador o un mensaje enviado")
    return problemas + _problemas_del_texto(message, marca, con_expediente)


def _validar_parametros_individuales(thread_id: str, message_id: str, esperado: str, marca: str,
                                     own_addresses: set[str]) -> None:
    if not thread_id or not message_id:
        raise ErrorDeAnka("Hacen falta el thread_id y el message_id exactos.")
    if not valid_email_address(esperado):
        raise ErrorDeAnka("Hace falta la dirección exacta del remitente esperado.")
    if not MARCA_DE_PRUEBA.fullmatch(marca or ""):
        raise ErrorDeAnka("La marca debe ser de prueba (PRUEBA-…): este registro solo admite mensajes de laboratorio.")
    if esperado in own_addresses:
        raise ErrorDeAnka("El remitente esperado es una dirección propia: no es un cliente de prueba.")


def _leer_y_verificar_individual(gmail: ClienteGmail, thread_id: str, message_id: str, esperado: str,
                                 marca: str, con_expediente: bool = False) -> None:
    """Lee ese único mensaje y comprueba cuenta y contenido; cualquier fallo es un rechazo sin escrituras."""
    try:
        perfil = gmail.users().getProfile(userId="me").execute()
        if normalized_address(str(perfil.get("emailAddress", ""))) != CUENTA:
            raise ErrorDeAnka(f"Gmail no está autenticado como {CUENTA}.")
        message = gmail.users().messages().get(userId="me", id=message_id, format="full").execute()
    except ErrorDeAnka:
        raise
    except Exception as exc:
        tipo = recepcion._error_kind(exc)
        raise ErrorDeAnka(f"No se pudo leer el mensaje en Gmail ({tipo}); no se registra nada.") from exc
    problemas = _problemas_del_mensaje_individual(message, thread_id, message_id, esperado, marca, con_expediente)
    if problemas:
        raise ErrorDeAnka("El mensaje no se registra: " + "; ".join(problemas) + ".")


def registrar_mensaje_individual(gmail: ClienteGmail, thread_id: str, message_id: str, remitente_esperado: str,
                                 marca: str, config: Configuracion | None = None,
                                 now: datetime | None = None) -> dict[str, Any]:
    """Registra en la cola UN mensaje de prueba por su `message_id` exacto, sin sondeo general ni cursor (T-58, D-165).

    Verifica cuenta, hilo, remitente y marca antes de escribir; reutiliza `_register_message` de recepcion; no lee otras
    filas de la cola, no consulta History ni busca; es idempotente. El resultado no lleva asunto, cuerpo ni remitente.
    """
    thread_id, message_id = str(thread_id or "").strip(), str(message_id or "").strip()
    esperado = normalized_address(remitente_esperado or "")
    effective_config = config if config is not None else settings.load_config()
    own_addresses = {CUENTA, *(normalized_address(a) for a in effective_config.get("own_addresses", []) if a)}
    _validar_parametros_individuales(thread_id, message_id, esperado, marca, own_addresses)
    observed = (now or datetime.now().astimezone()).isoformat(timespec="seconds")
    if not recepcion._sondeo_lock.acquire(blocking=False):
        raise ErrorDeAnka("Hay un sondeo de recepción en curso en este proceso.")
    owner: str | None = None
    try:
        with lectura() as db:
            control = db.execute("SELECT history_id,initial_checkpoint_at FROM recepcion_control WHERE account=?",
                                 (CUENTA,)).fetchone()
            existente = db.execute("SELECT state,disposition FROM recepcion_mensajes WHERE account=? AND message_id=?",
                                   (CUENTA, message_id)).fetchone()
            con_expediente = db.execute("SELECT 1 FROM quotes WHERE thread_id=?", (thread_id,)).fetchone() is not None
        if existente is not None:
            return {"resultado": "ya_registrado", "thread_id": thread_id, "message_id": message_id,
                    "state": existente["state"], "disposition": existente["disposition"]}
        if control is None or not str(control["history_id"] or ""):
            raise ErrorDeAnka("La recepción de Gmail aún no está inicializada; este registro no la inicializa.")
        owner = recepcion._acquire(CUENTA, time.time(), observed, registrar_intento=False)
        if owner is None:
            raise ErrorDeAnka("Otro proceso tiene la reserva de la recepción; no se registra nada.")
        _leer_y_verificar_individual(gmail, thread_id, message_id, esperado, marca, con_expediente)
        with transaccion() as db:
            recepcion._require_owner(db, CUENTA, owner)
            creado = recepcion._enqueue_in_transaction(db, CUENTA, [{"id": message_id, "threadId": thread_id}],
                                             "registro_individual", observed)
            fila = db.execute("SELECT * FROM recepcion_mensajes WHERE account=? AND thread_id=? AND message_id=?",
                              (CUENTA, thread_id, message_id)).fetchone()
        if not creado:
            return {"resultado": "ya_registrado", "thread_id": thread_id, "message_id": message_id,
                    "state": fila["state"], "disposition": fila["disposition"]}
        try:
            state = recepcion._register_message(gmail, owner, dict(fila), own_addresses, observed, time.time(),
                                      str(control["initial_checkpoint_at"] or ""))
        except Exception as exc:
            with transaccion() as db:       # la cola queda como estaba: nada a medias que un sondeo procese después
                db.execute("DELETE FROM recepcion_mensajes WHERE account=? AND thread_id=? AND message_id=? "
                           "AND state='por_leer'", (CUENTA, thread_id, message_id))
                db.execute("DELETE FROM recepcion_eventos WHERE account=? AND thread_id=? AND message_id=? "
                           "AND event_kind='detectado' AND occurred_at=?", (CUENTA, thread_id, message_id, observed))
            tipo = recepcion._error_kind(exc)
            raise ErrorDeAnka(f"No se pudo registrar el mensaje ({tipo}); la cola no cambió.") from exc
        paquete = recepcion.event_packet(thread_id, message_id)
        return {"resultado": "registrado", "thread_id": thread_id, "message_id": message_id, "state": state,
                "disposition": paquete["disposition"] if paquete else ""}
    finally:
        if owner:
            recepcion._release(CUENTA, owner)
        recepcion._sondeo_lock.release()
