"""Retira borradores internos importados, conservando evidencia recuperable antes de tocar Gmail."""

from __future__ import annotations

import base64
import hashlib
import json
from email.message import EmailMessage
from email.utils import getaddresses

from anka.dominio.correo import header, normalized_address
from anka.infra import settings
from anka.infra.archivos_privados import escribir_privado
from anka.infra.clientes_google import ClienteGmail
from anka.infra.logging_setup import get_logger
from anka.infra.store import lectura, record_event, transaccion
from anka.servicios.repositorio import actualizar_caso

log = get_logger(__name__)


def retirar_importado(gmail: ClienteGmail, contrato_base64: str, propias: set[str], mensaje_borrador: str) -> bool:
    """Borra solo el borrador interno exacto de un análisis confirmado; cualquier fallo conserva la recuperación."""
    try:
        return _retirar(gmail, contrato_base64, propias, mensaje_borrador)
    except Exception:
        log.warning("No se pudo confirmar la limpieza del borrador interno", exc_info=True)
        return False


def _importacion_confirmada(hilo: str, mensaje: str, version: str) -> bool:
    with lectura() as db:
        evento = db.execute(
            "SELECT 1 FROM case_events WHERE thread_id=? AND kind='analysis' AND source='work' AND ref=?",
            (hilo, version)).fetchone()
        caso = db.execute("SELECT 1 FROM quotes WHERE thread_id=?", (hilo,)).fetchone()
        pendiente = db.execute(
            "SELECT 1 FROM trabajos_work WHERE thread_id=? AND message_id=? AND state<>'conciliado' LIMIT 1",
            (hilo, mensaje)).fetchone()
    return bool(evento and caso and not pendiente)


def _interno(mensaje: dict, propias: set[str]) -> bool:
    etiquetas = mensaje.get("labelIds", [])
    cabeceras = [header(mensaje, campo) for campo in ("To", "Cc", "Bcc")]
    destinatarios = {normalized_address(direccion) for _, direccion in getaddresses([
        valor for valor in cabeceras if valor]) if direccion}
    return ("DRAFT" in etiquetas and "SENT" not in etiquetas and bool(destinatarios)
            and destinatarios <= propias and normalized_address(header(mensaje, "From")) in propias)


def _retirar(gmail: ClienteGmail, contrato_base64: str, propias: set[str], mensaje_borrador: str) -> bool:
    raw = base64.urlsafe_b64decode(contrato_base64).decode("utf-8")
    contrato = json.loads(raw)
    hilo, mensaje, borrador = (str(contrato.get(k) or "") for k in ("thread_id", "message_id", "draft_id"))
    version = hashlib.sha1(raw.encode("utf-8")).hexdigest()[:12]
    if not all((hilo, mensaje, borrador, mensaje_borrador)) or not _importacion_confirmada(hilo, mensaje, version):
        return False
    original = gmail.users().drafts().get(userId="me", id=borrador, format="full").execute()
    contenido = original["message"]
    if (contenido.get("id") != mensaje_borrador or contenido.get("threadId") != hilo
            or not _interno(contenido, propias)):
        return False
    copia = EmailMessage()
    copia["Subject"] = "Evidencia interna de Work importada en Anka"
    copia.set_content("Copia de recuperación del borrador y del contrato importado. No enviar.")
    copia.add_attachment(json.dumps({"borrador": original, "contrato_importado": contrato},
                                   ensure_ascii=False, sort_keys=True).encode("utf-8"),
                         maintype="application", subtype="json", filename="evidencia.json")
    evidencia = copia.as_string()
    clave = hashlib.sha256((borrador + "\0" + str(contenido["id"])).encode()).hexdigest()
    ruta = settings.DATA_DIR / "evidence" / "work_importado" / f"{clave}.eml"
    escribir_privado(ruta, evidencia)
    if ruta.read_text(encoding="utf-8") != evidencia:
        return False
    # Gmail cambia el message.id al editar un borrador: una edición posterior queda intacta.
    actual = gmail.users().drafts().get(userId="me", id=borrador, format="full").execute()["message"]
    if actual.get("id") != contenido["id"] or actual.get("threadId") != hilo or not _interno(actual, propias):
        return False
    if not _importacion_confirmada(hilo, mensaje, version):
        return False
    ref = "work-draft-" + clave
    with transaccion() as db:
        record_event(db, hilo, "work_draft_archived", "python", ref,
                     f"Borrador interno respaldado; evidencia {clave}.eml; análisis {version}.")
    gmail.users().drafts().delete(userId="me", id=borrador).execute()
    with transaccion() as db:
        fila = db.execute("SELECT draft_id FROM quotes WHERE thread_id=?", (hilo,)).fetchone()
        if fila and fila["draft_id"] == borrador:
            actualizar_caso(db, hilo, draft_id=None)
        record_event(db, hilo, "work_draft_cleaned", "python", ref,
                     "Borrador interno retirado de Gmail tras verificar importación y evidencia local.")
    return True
