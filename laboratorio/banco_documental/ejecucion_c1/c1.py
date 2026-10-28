"""Controlador de la sesión supervisada C1 (T-89, corregido en T-90). Nada de esto se ha ejecutado contra la configuración real.

Casos: `--perfil C1|C2|C3` (por defecto C1; cada uno con su marcador, asunto, adjunto y carpeta de evidencia).

Órdenes (solo lectura salvo `pausar`, `restaurar` y `vigilar --confirmar`, que escriben únicamente `config.json`):
  estado            comprobación rápida de pausa y plazo (configuración, plan, candado, publicación en curso, receptor).
  ids               Gmail en solo lectura: halla el correo C1 por su marcador exacto, valida remitente, destinatario,
                    asunto, hilo y mensaje, y DESCARGA el adjunto (uno solo, solicitud_01.pdf, 4913 bytes, SHA-256);
                    guarda los ids en `evidencia_t92/ids.json`.
  confirmar-envio   el operador declara `enviado` o `no_enviado` (queda en `evidencia/operador.json`).
  localizar         Gmail y SQLite en solo lectura: repite la verificación del adjunto y comprueba registro, asunto,
                    adjunto registrado, ausencia de trabajo y elegibilidad; habilita `comandos publicar` (5 min).
  comandos          imprime, SIN ejecutarlo, el comando de `cancelar` (AUT-CANCELAR) o de `publicar` (AUT-PUBLICACION).
  pausar            AUT-CONFIG-1: work_coordination_enabled true -> false, cambio mínimo a nivel de bytes.
  restaurar         AUT-RESTAURAR: solo si el estado efectivo de C1 lo permite (ver `evaluar`).
  vigilar           controla el plazo de 15 min; al vencer restaura SOLO si `evaluar` lo permite y hay --confirmar.

La restauración exige UNA de estas tres condiciones comprobadas (ninguna se infiere de una simple ausencia en Gmail):
  1. ausencia comprobada: Gmail no muestra el marcador Y el operador confirmó `no_enviado`;
  2. cancelación verificada de ese mensaje (trabajo cancelado, diario y asunto reales);
  3. un único trabajo `publicado` o `conciliado` con exactamente una hoja verificada.

Uso: python -I laboratorio/banco_documental/ejecucion_c1/c1.py <orden> [--config RUTA] [--evidencia CARPETA] [--confirmar]
`--config` permite ensayar `pausar`/`restaurar` sobre una COPIA.
"""

from __future__ import annotations

import argparse
import base64
import binascii
import hashlib
import json
import os
import sqlite3
import sys
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from email.utils import getaddresses
from pathlib import Path
from typing import Any

RAIZ = Path(__file__).resolve().parents[3]
if str(RAIZ) not in sys.path:
    sys.path.insert(0, str(RAIZ))

MARCA = "[PI-C1-T92-001]"
ASUNTO = "[PI-C1-T92-001] Solicitud ficticia de laboratorio - levantamiento topográfico (SIN COTIZACIÓN REAL)"
MARCA_T91 = "[PI-C1-T89-001]"                       # el intento anterior (cancelado en T-91B): nunca se confunde con el actual
ADJUNTO = "solicitud_01.pdf"
SHA_PDF = "f47ed4da974e982b09954bfd1b71472b0fefa5859309d7ab1e24c924c92bd844"
VIGENCIA_VERIFICACION_S = 300                       # `localizar` caduca: el comando de publicar exige una verificación reciente
REMITENTE = "91andresmunoz@gmail.com"
DESTINATARIO = "ankacomercialia@gmail.com"
RAIZ_DRIVE = "1IvNxAoIQmPN8o7_HUB8oqqidtA3asK2w"           # --carpeta-id del despacho: la RAÍZ, no PENDIENTES
PENDIENTES_ID = "1cFvXLrilal2hWMc_JyU8Xa-5fKd5ZlNW"
CERRADOS_ID = "1s3R0Qr4VzYOCif-oaubEvvN4IPLW9VLF"
CLAVE_DE_MARCA = "anka_job_id"
BYTES_PDF = 4913
VIEJO, NUEVO = b'"work_coordination_enabled": true', b'"work_coordination_enabled": false'
TOPE_PAUSA_S = 15 * 60
LATIDO_MINIMO_S = 20                    # no se pausa con un ciclo recién iniciado (podría estar publicando)
CONSULTA = f'subject:"{MARCA}" in:anywhere'


@dataclass(frozen=True)
class Perfil:
    """Lo que distingue un caso del banco: el marcador, el asunto y el adjunto original (nombre, bytes y SHA-256)."""

    caso: str
    marca: str
    asunto: str
    adjunto: str
    sha256: str
    bytes_: int
    carpeta: str


PERFILES: dict[str, Perfil] = {
    "C1": Perfil("C1", MARCA, ASUNTO, ADJUNTO, SHA_PDF, BYTES_PDF, "evidencia_t92"),
    "C2": Perfil("C2", "[PI-C2-T98-001]",
                 "[PI-C2-T98-001] Solicitud ficticia de laboratorio - levantamiento batimétrico (SIN COTIZACIÓN REAL)",
                 "tdr_02.pdf", "0287414f91bb1c9b3ecfd05e98de713adaa81177261885f89e4fe8f23c66d732", 492471, "evidencia_c2_t98"),
    "C3": Perfil("C3", "[PI-C3-T98-001]",
                 "[PI-C3-T98-001] Solicitud ficticia de laboratorio - proyecto vial, resumen de cantidades (SIN COTIZACIÓN REAL)",
                 "cantidades_03.xlsx", "12c52e788feb76d4c7879726d7402cb0c51f8178101e5a7a9e1bfb10cb7c0c6a", 9087, "evidencia_c3_t98"),
}


def usar_perfil(caso: str) -> Perfil:
    """Fija el caso activo del proceso (un caso por ejecución). C1 es el valor de origen y no cambia nada."""
    global MARCA, ASUNTO, ADJUNTO, SHA_PDF, BYTES_PDF, CONSULTA
    perfil = PERFILES[caso]
    MARCA, ASUNTO, ADJUNTO, SHA_PDF, BYTES_PDF = perfil.marca, perfil.asunto, perfil.adjunto, perfil.sha256, perfil.bytes_
    CONSULTA = f'subject:"{MARCA}" in:anywhere'
    return perfil
PERMITIDO, NO_PERMITIDO = True, False


def datos() -> Path:
    """La carpeta de datos de ANKA (la de `ANKA_DATA_DIR` en las pruebas)."""
    return Path(os.environ.get("ANKA_DATA_DIR") or Path(os.environ["LOCALAPPDATA"]) / "AnkaCorreos")


def ahora() -> str:
    """La hora local con zona, en segundos."""
    return datetime.now().astimezone().isoformat(timespec="seconds")


# --- Gmail (solo lectura) ---------------------------------------------------------------------------------------------

@dataclass(frozen=True)
class Hallazgo:
    """Lo que Gmail muestra del correo C1: `ninguno`, `unico_valido`, `multiples`, `invalido` o `inaccesible`."""

    clase: str
    thread_id: str = ""
    message_id: str = ""
    detalle: str = ""
    adjunto_ok: bool = False      # solo con `unico_valido`: el adjunto descargado es el de C1
    adjunto: dict[str, Any] = field(default_factory=dict)


def _direcciones(valor: str) -> list[str]:
    return [d.lower() for _n, d in getaddresses([valor or ""]) if d]


def _partes(parte: dict[str, Any]) -> list[dict[str, Any]]:
    """Todas las partes del mensaje, en profundidad."""
    resultado: list[dict[str, Any]] = []
    for hija in parte.get("parts") or []:
        resultado.append(hija)
        resultado += _partes(hija)
    return resultado


def verificar_adjunto(gmail: Any, mensaje: dict[str, Any]) -> tuple[bool, str, dict[str, Any]]:
    """Descarga el adjunto (solo lectura) y exige: uno solo, `solicitud_01.pdf`, 4913 bytes y el SHA-256 esperado.

    Un error de Gmail al descargar se propaga (quien llama lo trata como `inaccesible`).
    """
    adjuntos = [p for p in _partes(mensaje.get("payload", {})) if p.get("filename")]
    if len(adjuntos) != 1:
        return False, f"se esperaba exactamente 1 adjunto y hay {len(adjuntos)}", {"adjuntos": len(adjuntos)}
    parte = adjuntos[0]
    cuerpo = parte.get("body") or {}
    info: dict[str, Any] = {"nombre": parte["filename"], "bytes_declarados": cuerpo.get("size")}
    codificado = cuerpo.get("data")
    if not codificado and cuerpo.get("attachmentId"):
        codificado = gmail.users().messages().attachments().get(
            userId="me", messageId=mensaje["id"], id=cuerpo["attachmentId"]).execute().get("data")
    if not codificado:
        return False, "el adjunto no tiene contenido accesible", info
    try:
        contenido = base64.urlsafe_b64decode(codificado + "=" * (-len(codificado) % 4))
    except (binascii.Error, ValueError):
        return False, "el contenido del adjunto es ilegible", info
    info.update(bytes=len(contenido), sha256=hashlib.sha256(contenido).hexdigest())
    fallos = []
    if parte["filename"] != ADJUNTO:
        fallos.append(f"nombre distinto de {ADJUNTO}")
    if info["bytes_declarados"] != BYTES_PDF or len(contenido) != BYTES_PDF:
        fallos.append(f"tamaño distinto de {BYTES_PDF} bytes")
    if info["sha256"] != SHA_PDF:
        fallos.append("SHA-256 distinto del esperado")
    return not fallos, "; ".join(fallos), info


def buscar_en_gmail(gmail: Any) -> Hallazgo:
    """Exige exactamente UN mensaje entrante con el marcador, del remitente y destinatario previstos y asunto exacto.

    `unico_valido` es la identidad (cabeceras, ids, hilo); además `adjunto_ok` dice si el archivo descargado es el de C1.
    Con un adjunto incorrecto se devuelven igualmente los ids: hacen falta para poder cancelar ese mensaje.
    """
    try:
        encontrados = gmail.users().messages().list(userId="me", q=CONSULTA, includeSpamTrash=True,
                                                    maxResults=10).execute().get("messages", [])
    except Exception as exc:
        return Hallazgo("inaccesible", detalle=f"Gmail no respondió ({type(exc).__name__})")
    if not encontrados:
        return Hallazgo("ninguno")
    if len(encontrados) > 1:
        return Hallazgo("multiples", detalle=f"{len(encontrados)} mensajes con el marcador")
    try:
        item = encontrados[0]
        mensaje = gmail.users().messages().get(userId="me", id=item["id"], format="full").execute()
        cabeceras = {h["name"].lower(): h["value"] for h in mensaje.get("payload", {}).get("headers", [])}
        hilo = gmail.users().threads().get(userId="me", id=str(mensaje.get("threadId") or ""),
                                           format="minimal").execute().get("messages", [])
        adjunto_ok, motivo_adjunto, adjunto = verificar_adjunto(gmail, mensaje)
    except Exception as exc:
        return Hallazgo("inaccesible", detalle=f"Gmail no respondió ({type(exc).__name__})")
    fallos = []
    if cabeceras.get("subject") != ASUNTO:
        fallos.append("el asunto no es el exacto (coincidencia en texto citado, respuesta o reenvío)")
    de, para = _direcciones(cabeceras.get("from", "")), _direcciones(cabeceras.get("to", ""))
    if DESTINATARIO in de or {"SENT", "DRAFT"} & set(mensaje.get("labelIds") or []):
        fallos.append("es un mensaje enviado por la cuenta comercial o un borrador")
    if de != [REMITENTE]:
        fallos.append("el remitente no es el previsto")
    if para != [DESTINATARIO] or cabeceras.get("cc") or cabeceras.get("bcc"):
        fallos.append("los destinatarios no son solo la cuenta comercial")
    if mensaje.get("id") != item["id"] or mensaje.get("threadId") != item.get("threadId"):
        fallos.append("los ids devueltos no coinciden")
    if len(hilo) != 1:
        fallos.append("el hilo no tiene exactamente un mensaje")
    if fallos:
        return Hallazgo("invalido", detalle="; ".join(fallos))
    detalle = "" if adjunto_ok else "ADJUNTO INCORRECTO: " + motivo_adjunto
    return Hallazgo("unico_valido", str(mensaje["threadId"]), str(mensaje["id"]), detalle, adjunto_ok, adjunto)


# --- estado efectivo de C1 y decisión de restaurar --------------------------------------------------------------------

@dataclass(frozen=True)
class Veredicto:
    """Si se puede restaurar la coordinación, por qué y qué hacer si no."""

    permitido: bool
    categoria: str
    motivo: str
    que_hacer: str = ""


@dataclass
class Hechos:
    """Lo que SQLite dice de ese mensaje (y solo de ese): la fila, sus trabajos y los trabajos ajenos sin cerrar."""

    fila: dict[str, Any] | None = None
    trabajos: list[dict[str, Any]] = field(default_factory=list)
    ajenos_sin_cerrar: list[dict[str, Any]] = field(default_factory=list)


def hechos_de_base(db: sqlite3.Connection, thread_id: str, message_id: str) -> Hechos:
    """Solo lectura: la fila, los trabajos de ese mensaje y los trabajos ajenos sin cerrar."""
    fila = db.execute("SELECT subject,state,disposition,attachments_json FROM recepcion_mensajes WHERE thread_id=? AND message_id=?",
                      (thread_id, message_id)).fetchone()
    trabajos = db.execute("SELECT job_id,state FROM trabajos_work WHERE thread_id=? AND message_id=?",
                          (thread_id, message_id)).fetchall()
    ajenos = db.execute("SELECT job_id,state FROM trabajos_work WHERE state NOT IN ('conciliado','cancelado') "
                        "AND NOT (thread_id=? AND message_id=?)", (thread_id, message_id)).fetchall()
    return Hechos(dict(fila) if fila else None, [dict(t) for t in trabajos], [dict(a) for a in ajenos])


def evaluar(hallazgo: Hallazgo, envio: str, hechos: Hechos | None,
            hojas_de: Callable[[str], Sequence[Any]], en_diario: Callable[[str], bool]) -> Veredicto:
    """¿Se puede devolver la coordinación? `envio` es lo que declaró el operador: `enviado`, `no_enviado` o `` (nada).

    Solo `permitido=True` en las tres condiciones de la cabecera; todo lo demás es NO, con lo que hay que hacer.
    """
    clase = hallazgo.clase
    if clase == "inaccesible":
        return Veredicto(NO_PERMITIDO, "incertidumbre", f"No se pudo consultar Gmail: {hallazgo.detalle}.",
                         "Reintentar; no restaurar mientras no se pueda comprobar.")
    if clase in ("multiples", "invalido"):
        return Veredicto(NO_PERMITIDO, "anomalia", f"Gmail muestra algo inesperado: {hallazgo.detalle}.",
                         "Detener y decidir una persona; no se actúa sobre ningún mensaje.")
    if clase == "ninguno":
        if envio == "no_enviado":
            return Veredicto(PERMITIDO, "ausencia_comprobada",
                             "Gmail no muestra el marcador y el operador confirmó que no se envió nada.")
        if envio == "enviado":
            return Veredicto(NO_PERMITIDO, "enviado_invisible",
                             "El operador envió el correo pero Gmail aún no lo muestra: puede llegar y registrarse "
                             "después, sin ids ni lápida posible.",
                             "Mantener la pausa y volver a consultar; extenderla exige una nueva autorización escrita.")
        return Veredicto(NO_PERMITIDO, "incertidumbre",
                         "Gmail no muestra el marcador, pero eso no prueba que no se haya enviado.",
                         "El operador debe declarar `confirmar-envio enviado` o `no_enviado`.")
    if envio == "no_enviado":
        return Veredicto(NO_PERMITIDO, "contradiccion",
                         "El operador declaró `no_enviado` pero el correo C1 existe en Gmail.",
                         "Corregir la declaración (`confirmar-envio enviado`) y resolver el mensaje.")
    assert hechos is not None
    return _evaluar_mensaje(hallazgo, envio, hechos, hojas_de, en_diario)


def _evaluar_mensaje(hallazgo: Hallazgo, envio: str, hechos: Hechos, hojas_de: Callable[[str], Sequence[Any]],
                     en_diario: Callable[[str], bool]) -> Veredicto:
    """El caso en que Gmail muestra exactamente el correo C1: manda lo que SQLite y Drive dicen de ese mensaje."""
    if hechos.ajenos_sin_cerrar:
        return Veredicto(NO_PERMITIDO, "otros_trabajos", f"Hay {len(hechos.ajenos_sin_cerrar)} trabajo(s) ajeno(s) sin "
                         "cerrar.", "Resolverlos antes de restaurar.")
    trabajos = hechos.trabajos
    if not trabajos:
        causa = "registrado_sin_trabajo" if hechos.fila else "enviado_no_registrado"
        return Veredicto(NO_PERMITIDO, causa, "El correo C1 existe y no tiene trabajo ni cancelación: la coordinación "
                         "lo publicaría sola al restaurar.",
                         "Elegir: AUT-PUBLICACION (despacho manual) o AUT-CANCELAR (cancelación, preventiva si aún no se registró)."
                         if hallazgo.adjunto_ok else
                         "El adjunto NO es el de C1 (" + hallazgo.detalle + "): no publicar. Solo AUT-CANCELAR (normal si ya está registrado).")
    if len(trabajos) > 1:
        return Veredicto(NO_PERMITIDO, "dos_trabajos", "El mensaje tiene más de un trabajo.", "Decisión humana.")
    trabajo = trabajos[0]
    estado = trabajo["state"]
    if estado == "cancelado":
        if not en_diario(trabajo["job_id"]):
            return Veredicto(NO_PERMITIDO, "cancelacion_incompatible", "La lápida no figura en el diario de cancelaciones.",
                             "Ejecutar `verificar-lapidas` y decidir.")
        if hechos.fila is not None and hechos.fila["subject"] != ASUNTO:
            return Veredicto(NO_PERMITIDO, "cancelacion_incompatible",
                             "El mensaje registrado no tiene el asunto de C1: la lápida no corresponde a esta prueba.",
                             "Decisión humana; no restaurar.")
        return Veredicto(PERMITIDO, "cancelacion_verificada",
                         "Existe lápida (SQLite y diario) del mensaje C1 cuyos ids y asunto fueron verificados en Gmail.")
    if estado in ("publicado", "conciliado"):
        if not hallazgo.adjunto_ok:
            return Veredicto(NO_PERMITIDO, "adjunto_incorrecto", "El trabajo existe pero el adjunto del correo no es el de C1.",
                             "Decisión humana; no restaurar.")
        try:
            hojas = list(hojas_de(trabajo["job_id"]))
        except Exception as exc:
            return Veredicto(NO_PERMITIDO, "incertidumbre", f"No se pudo verificar la hoja en Drive ({type(exc).__name__}).",
                             "Reintentar.")
        if len(hojas) != 1:
            return Veredicto(NO_PERMITIDO, "hojas_incorrectas", f"Se esperaba exactamente 1 hoja verificada y hay {len(hojas)}.",
                             "Decisión humana; no restaurar.")
        return Veredicto(PERMITIDO, "trabajo_" + estado, f"Un único trabajo {estado} con exactamente una hoja verificada.")
    return Veredicto(NO_PERMITIDO, "trabajo_" + estado, f"El trabajo del mensaje está `{estado}`.",
                     "Publicar (AUT-PUBLICACION) o cancelar (AUT-CANCELAR) según lo decidido; si es ambiguo o agotado, decisión humana.")


# --- plazo ------------------------------------------------------------------------------------------------------------

def segundos_restantes(pausa: dict[str, Any], t: float) -> float:
    """Segundos que faltan para el vencimiento de la pausa (negativo si ya venció)."""
    return float(pausa["vence_epoch"]) - t


def vigilar(pausa: dict[str, Any], evaluar_ahora: Callable[[], Veredicto], restaurar_ahora: Callable[[], Any], autorizado: bool,
            reloj: Callable[[], float] = time.time, dormir: Callable[[float], None] = time.sleep, paso: float = 15.0,
            salida: Callable[[str], None] = print) -> str:
    """Espera al vencimiento y NO restaura antes ni sin permiso. Al vencer: restaura solo si `evaluar` lo permite y el
    operador dio la autorización (`autorizado`); en cualquier otro caso se detiene en `bloqueado` sin tocar nada."""
    avisado = False
    while segundos_restantes(pausa, reloj()) > 0:
        resto = segundos_restantes(pausa, reloj())
        if resto <= 180 and not avisado:
            salida(f"AVISO: quedan {int(resto)} s de la pausa.")
            avisado = True
        dormir(min(paso, max(resto, 0.0)))
    veredicto = evaluar_ahora()
    salida(f"PLAZO VENCIDO. Estado de C1: {veredicto.categoria} - {veredicto.motivo}")
    if veredicto.permitido and autorizado:
        restaurar_ahora()
        return "restaurado"
    if veredicto.permitido:
        salida("La restauración es posible pero falta --confirmar (AUT-RESTAURAR): no se hace nada.")
        return "bloqueado_sin_autorizacion"
    salida("BLOQUEADO: la coordinación NO se restaura. " + veredicto.que_hacer)
    return "bloqueado"


# --- configuración ----------------------------------------------------------------------------------------------------

def impedimentos_de_trabajos(db: sqlite3.Connection) -> list[str]:
    """Trabajos que impiden pausar: todo salvo `conciliado` y `cancelado` con lápida consistente (diario y base de acuerdo)."""
    from anka.infra import lapidas
    cotejo = lapidas.comparar(db)
    rotas = [f"{cotejo['lineas_rotas']} línea(s) ilegible(s) en el diario de lápidas"] if cotejo.get("lineas_rotas") else []
    sin_lapida_valida = set(cotejo.get("faltan", [])) | {c["job_id"] for c in cotejo.get("chocan", [])}
    en_diario = {e["job_id"] for e in lapidas.leer()[0]}
    impide = []
    for job_id, estado in db.execute("SELECT job_id,state FROM trabajos_work WHERE state<>'conciliado'").fetchall():
        if estado == "cancelado" and job_id in en_diario and job_id not in sin_lapida_valida:
            continue
        impide.append(f"{job_id} ({estado}{'' if estado != 'cancelado' else ', sin lápida consistente'})")
    return impide + rotas


def pausar(cfg: Path, carpeta: Path, reloj: Callable[[], float] = time.time, comprobar_lock: bool = True) -> int:
    """AUT-CONFIG-1: work_coordination_enabled true -> false a nivel de bytes; guarda el original y el vencimiento."""
    from anka.infra import settings
    if (carpeta / "pausa.json").exists() and not json.loads((carpeta / "pausa.json").read_text()).get("restaurada"):
        print("NO SE PAUSA: ya hay una pausa sin restaurar en esta carpeta de evidencia.")
        return 6
    lock = datos() / "work_coordinacion.lock"
    if comprobar_lock:
        if lock.exists() and reloj() - lock.stat().st_mtime < LATIDO_MINIMO_S:
            print(f"NO SE PAUSA: hubo un ciclo hace menos de {LATIDO_MINIMO_S} s; reintentar en unos segundos.")
            return 2
        impedimentos = impedimentos_de_trabajos(_base())
        if impedimentos:
            print("NO SE PAUSA: hay trabajos que impiden la pausa (¿publicación en curso?): " + "; ".join(impedimentos))
            return 3
    original = cfg.read_bytes()
    if original.count(VIEJO) != 1:
        print("NO SE PAUSA: work_coordination_enabled no está en true una sola vez.")
        return 4
    nuevo = original.replace(VIEJO, NUEVO)
    if settings.validate_config(json.loads(nuevo.decode("utf-8-sig"))):
        print("NO SE PAUSA: la configuración resultante no es válida.")
        return 5
    carpeta.mkdir(parents=True, exist_ok=True)
    (carpeta / "config.json.original").write_bytes(original)
    tmp = cfg.with_name(cfg.name + ".c1tmp")
    tmp.write_bytes(nuevo)
    os.replace(tmp, cfg)
    inicio = reloj()
    (carpeta / "pausa.json").write_text(json.dumps({
        "inicio": ahora(), "inicio_epoch": inicio, "vence_epoch": inicio + TOPE_PAUSA_S,
        "vence": datetime.fromtimestamp(inicio + TOPE_PAUSA_S).astimezone().isoformat(timespec="seconds"),
        "sha_original": hashlib.sha256(original).hexdigest(), "sha_pausada": hashlib.sha256(nuevo).hexdigest()}, indent=1))
    print("PAUSA_APLICADA", ahora(), "vence como máximo a las", datetime.fromtimestamp(inicio + TOPE_PAUSA_S).strftime("%H:%M:%S"))
    return 0


def aplicar_restauracion(cfg: Path, carpeta: Path, reloj: Callable[[], float] = time.time) -> int:
    """Solo la operación de archivo: devuelve el original exacto. Quien llama ya comprobó que C1 lo permite."""
    meta = json.loads((carpeta / "pausa.json").read_text())
    original = (carpeta / "config.json.original").read_bytes()
    if hashlib.sha256(original).hexdigest() != meta["sha_original"]:
        print("NO SE RESTAURA: la copia guardada no coincide con su SHA-256.")
        return 2
    if cfg.read_bytes().replace(NUEVO, VIEJO) != original:
        print("NO SE RESTAURA: el archivo actual difiere del original en algo más que la clave; decide una persona.")
        return 3
    tmp = cfg.with_name(cfg.name + ".c1tmp")
    tmp.write_bytes(original)
    os.replace(tmp, cfg)
    meta["restaurada"] = ahora()
    (carpeta / "pausa.json").write_text(json.dumps(meta, indent=1))
    print("RESTAURADO", ahora(), "sha", hashlib.sha256(cfg.read_bytes()).hexdigest(),
          "minutos de pausa:", round((reloj() - meta["inicio_epoch"]) / 60, 1))
    return 0


# --- contexto real (Gmail, Drive y SQLite de solo lectura) --------------------------------------------------------------

def _base() -> sqlite3.Connection:
    db = sqlite3.connect(f"file:{(datos() / 'quotes.sqlite3').as_posix()}?mode=ro", uri=True, timeout=5)
    db.row_factory = sqlite3.Row
    return db


def _servicios() -> tuple[Any, Any]:
    from anka.infra.google_services import services
    return services()


def hojas_verificadas(drive: Any, job_id: str) -> list[dict[str, Any]]:
    """Archivos de PENDIENTES y CERRADOS con la marca del trabajo, fuera de la papelera (solo lectura)."""
    hojas = []
    for carpeta in (PENDIENTES_ID, CERRADOS_ID):
        pagina = drive.files().list(q=f"'{carpeta}' in parents and trashed = false",
                                    fields="files(id,name,appProperties)", pageSize=100).execute()
        hojas += [f for f in pagina.get("files", []) if (f.get("appProperties") or {}).get(CLAVE_DE_MARCA) == job_id]
    return hojas


def _leer(carpeta: Path, nombre: str) -> dict[str, Any]:
    ruta = carpeta / nombre
    return json.loads(ruta.read_text()) if ruta.exists() else {}


def evaluar_real(carpeta: Path) -> Veredicto:
    """`evaluar` con Gmail, Drive y SQLite reales, todos en solo lectura."""
    from anka.infra import lapidas
    gmail, drive = _servicios()
    hallazgo = buscar_en_gmail(gmail)
    hechos = None
    if hallazgo.clase == "unico_valido":
        with _base() as db:
            hechos = hechos_de_base(db, hallazgo.thread_id, hallazgo.message_id)
    envio = _leer(carpeta, "operador.json").get("envio", "")
    return evaluar(hallazgo, envio, hechos, lambda job: hojas_verificadas(drive, job), lapidas.esta_registrada)


# --- órdenes de consola -----------------------------------------------------------------------------------------------

def cmd_estado(cfg: Path, carpeta: Path) -> int:
    """Imprime la comprobación rápida de pausa y plazo (solo lectura)."""
    from anka.cli import work_coordinacion
    from anka.infra import acciones_en_curso, settings
    from anka.servicios import envios
    config = json.loads(cfg.read_text(encoding="utf-8-sig"))
    problemas = settings.validate_config(config)
    lock = datos() / "work_coordinacion.lock"
    edad = time.time() - lock.stat().st_mtime if lock.exists() else None
    db = _base()
    por_estado = dict(db.execute("SELECT state,COUNT(*) FROM trabajos_work GROUP BY 1").fetchall())
    control = db.execute("SELECT last_success_at,consecutive_errors FROM recepcion_control").fetchone()
    eventos = db.execute("SELECT COUNT(*),MAX(rowid) FROM recepcion_eventos").fetchone()
    pausa = _leer(carpeta, "pausa.json")
    plazo = "sin pausa registrada"
    if pausa and not pausa.get("restaurada"):
        resto = segundos_restantes(pausa, time.time())
        plazo = f"{int(resto)} s restantes" if resto > 0 else f"VENCIDA hace {int(-resto)} s"
    filas = [
        ("configuración válida", not problemas, problemas or "ok"),
        ("work_coordination_enabled", config.get("work_coordination_enabled"), "esperado False en pausa"),
        ("work_dispatch_enabled", config.get("work_dispatch_enabled"), "debe seguir False"),
        ("plan de coordinación vacío (=sin ciclos)", work_coordinacion.plan_de(config) is None, "True en pausa"),
        ("candado: segundos desde el último ciclo", None if edad is None else round(edad), "crece sin reiniciarse"),
        ("trabajos no conciliados", {k: v for k, v in por_estado.items() if k != "conciliado"} or "ninguno", "sin publicación en curso"),
        ("acciones del dashboard / envíos", (acciones_en_curso.acciones_activas(), envios.hay_envios_en_curso()), "vacío"),
        ("receptor (último éxito, errores)", (control["last_success_at"], control["consecutive_errors"]), "avanza, 0"),
        ("eventos (total, máximo id)", tuple(eventos), "comparar con la instantánea"),
        ("plazo de la pausa", plazo, "15 min como máximo"),
    ]
    for nombre, valor, nota in filas:
        print(f"{nombre:45} {valor!s:60} {nota}")
    return 0 if not problemas else 1


def cmd_ids(carpeta: Path) -> int:
    """Localiza el correo C1 en Gmail (solo lectura), descarga y verifica su adjunto y guarda los ids.

    Sale con 0 solo si el mensaje es único, válido Y el adjunto es el de C1. Con un adjunto incorrecto guarda igualmente
    los ids (marcados `adjunto_ok=false`) porque son necesarios para cancelar, pero sale con 1.
    """
    gmail, _drive = _servicios()
    h = buscar_en_gmail(gmail)
    print(json.dumps({"clase": h.clase, "thread_id": h.thread_id, "message_id": h.message_id, "adjunto_ok": h.adjunto_ok,
                      "adjunto": h.adjunto, "detalle": h.detalle}, ensure_ascii=False, indent=1))
    if h.clase != "unico_valido":
        return 1
    carpeta.mkdir(parents=True, exist_ok=True)
    (carpeta / "ids.json").write_text(json.dumps({"thread_id": h.thread_id, "message_id": h.message_id, "adjunto_ok": h.adjunto_ok,
                                                  "adjunto": h.adjunto, "obtenido": ahora()}, indent=1))
    print("Contrastar a mano: estos ids son los del buzón de", DESTINATARIO, "(los del buzón del remitente son otros).")
    if not h.adjunto_ok:
        print(f"RECHAZADO: el adjunto no es {ADJUNTO}. No se puede publicar; solo cancelar (AUT-CANCELAR).")
        return 1
    return 0


def cmd_confirmar_envio(carpeta: Path, valor: str, operador: str) -> int:
    """Registra lo que declara el operador: `enviado` o `no_enviado`."""
    carpeta.mkdir(parents=True, exist_ok=True)
    previo = _leer(carpeta, "operador.json")
    (carpeta / "operador.json").write_text(json.dumps({**previo, "envio": valor, "operador": operador, "hora": ahora()}, indent=1))
    print(f"Declaración registrada: {valor} por {operador}.")
    return 0


def verificar_para_publicar(carpeta: Path, gmail: Any, db: sqlite3.Connection, elegible: Callable[[str, str], bool],
                            reloj: Callable[[], float] = time.time) -> dict[str, Any]:
    """Todas las comprobaciones críticas antes de entregar el comando de publicar. Guarda `verificacion.json`."""
    ids = _leer(carpeta, "ids.json")
    faltas: list[str] = []
    if not ids:
        faltas.append("falta `ids`")
    h = buscar_en_gmail(gmail)
    hilo, mensaje = h.thread_id, h.message_id
    if h.clase != "unico_valido":
        faltas.append(f"Gmail: {h.clase} {h.detalle}".strip())
    else:
        if ids and (hilo, mensaje) != (ids.get("thread_id"), ids.get("message_id")):
            faltas.append("los ids de Gmail difieren de los guardados por `ids`")
        if not h.adjunto_ok:
            faltas.append(h.detalle)
    hechos = hechos_de_base(db, hilo, mensaje) if h.clase == "unico_valido" else Hechos()
    fila = hechos.fila
    registrado = fila is not None
    asunto_exacto = bool(fila and fila["subject"] == ASUNTO)
    adjuntos = json.loads((fila or {}).get("attachments_json") or "[]")
    adjunto_bd_ok = [(a.get("filename"), a.get("size_bytes")) for a in adjuntos] == [(ADJUNTO, BYTES_PDF)]
    pendiente = bool(fila and (fila["state"], fila["disposition"]) == ("pendiente_de_procesar", "pendiente_de_interpretacion_work"))
    sin_trabajo = not hechos.trabajos
    es_elegible = bool(h.clase == "unico_valido" and registrado and elegible(hilo, mensaje))
    for ok, falta in ((registrado, "el mensaje aún no está registrado en SQLite"), (asunto_exacto, "el asunto registrado no es el exacto"),
                      (adjunto_bd_ok, f"el adjunto registrado en SQLite no es {ADJUNTO} de {BYTES_PDF} bytes"),
                      (pendiente, "la fila no está pendiente de interpretación"), (sin_trabajo, "el mensaje ya tiene trabajo o lápida"),
                      (es_elegible, "el mensaje no es elegible")):
        if not ok and h.clase == "unico_valido":
            faltas.append(falta)
    resultado = {"thread_id": hilo, "message_id": mensaje, "hecho_en": reloj(), "registrado": registrado, "asunto_exacto": asunto_exacto,
                 "adjunto_gmail_ok": h.adjunto_ok, "adjunto_bd_ok": adjunto_bd_ok, "pendiente": pendiente, "sin_trabajo": sin_trabajo,
                 "elegible": es_elegible, "faltas": faltas, "listo_para_publicar": not faltas}
    carpeta.mkdir(parents=True, exist_ok=True)
    (carpeta / "verificacion.json").write_text(json.dumps(resultado, indent=1, ensure_ascii=False))
    return resultado


def _elegible_real(hilo: str, mensaje: str) -> bool:
    from anka.servicios import trabajos_work
    return any(f["thread_id"] == hilo and f["message_id"] == mensaje and f["veredicto"] == "elegible"
               for f in trabajos_work.clasificar_pendientes())


def cmd_localizar(carpeta: Path) -> int:
    """Verifica en Gmail (adjunto incluido) y en SQLite (solo lectura) que C1 está listo; habilita `comandos publicar`."""
    gmail, _drive = _servicios()
    with _base() as db:
        resultado = verificar_para_publicar(carpeta, gmail, db, _elegible_real)
    print(json.dumps(resultado, ensure_ascii=False, indent=1))
    if not resultado["listo_para_publicar"]:
        print("NO LISTO PARA PUBLICAR: " + "; ".join(resultado["faltas"]))
        return 1
    return 0


def faltas_para_publicar(carpeta: Path, reloj: Callable[[], float] = time.time) -> list[str]:
    """Por qué NO se puede entregar el comando de publicar (lista vacía = se puede)."""
    ids, v = _leer(carpeta, "ids.json"), _leer(carpeta, "verificacion.json")
    if not ids:
        return ["falta `ids`"]
    faltas = []
    if not ids.get("adjunto_ok"):
        faltas.append("`ids` no validó el adjunto")
    if not v:
        faltas.append("falta `localizar` (verificación crítica en Gmail y SQLite)")
    else:
        faltas += list(v.get("faltas") or [])
        if not v.get("listo_para_publicar"):
            faltas.append("la verificación no está completa")
        if (v.get("thread_id"), v.get("message_id")) != (ids["thread_id"], ids["message_id"]):
            faltas.append("la verificación es de otros ids")
        if reloj() - float(v.get("hecho_en", 0)) > VIGENCIA_VERIFICACION_S:
            faltas.append(f"la verificación caducó (más de {VIGENCIA_VERIFICACION_S} s): repetir `localizar`")
    return faltas


def cmd_comandos(carpeta: Path, cual: str) -> int:
    """Imprime, sin ejecutarlo, el comando de cancelar o de publicar, cada uno con su propia autorización.

    El de publicar solo se entrega si `ids` y `localizar` (recientes) verificaron adjunto, identidad, registro y elegibilidad.
    """
    ids = _leer(carpeta, "ids.json")
    if not ids:
        print("Falta `ids`.")
        return 1
    t, m = ids["thread_id"], ids["message_id"]
    py = r".venv\Scripts\python.exe -m anka.cli.work_jobs"
    if cual == "cancelar":
        print("AUT-CANCELAR (independiente de la publicación; --preventiva solo si el mensaje aún no está registrado):")
        print(f'{py} cancelar --thread-id {t} --message-id {m} --etiqueta "{MARCA}" --operador "<NOMBRE>" '
              f'--motivo "<MOTIVO>" [--preventiva] --confirmar')
        return 0
    faltas = faltas_para_publicar(carpeta)
    if faltas:
        print("NO SE ENTREGA EL COMANDO DE PUBLICACIÓN: " + "; ".join(faltas))
        return 1
    print("AUT-PUBLICACION (independiente de la cancelación; --carpeta-id es la RAÍZ, no PENDIENTES):")
    print(f"{py} despachar --carpeta-id {RAIZ_DRIVE} --thread-id {t} --message-id {m} --confirmar-drive")
    return 0


def cmd_restaurar(cfg: Path, carpeta: Path) -> int:
    """Restaura la coordinación solo si el estado efectivo de C1 lo permite."""
    veredicto = evaluar_real(carpeta)
    print(f"Estado de C1: {veredicto.categoria} - {veredicto.motivo}")
    if not veredicto.permitido:
        print("NO SE RESTAURA. " + veredicto.que_hacer)
        return 7
    return aplicar_restauracion(cfg, carpeta)


def cmd_vigilar(cfg: Path, carpeta: Path, confirmar: bool) -> int:
    """Controla el plazo de la pausa; al vencer restaura únicamente si es seguro y está autorizado."""
    pausa = _leer(carpeta, "pausa.json")
    if not pausa or pausa.get("restaurada"):
        print("No hay una pausa activa que vigilar.")
        return 1
    resultado = vigilar(pausa, lambda: evaluar_real(carpeta), lambda: aplicar_restauracion(cfg, carpeta), confirmar)
    print("RESULTADO:", resultado)
    return 0 if resultado == "restaurado" else 10


def main(argv: Sequence[str] | None = None) -> int:
    """Punto de entrada de consola."""
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("orden", choices=["estado", "ids", "confirmar-envio", "localizar", "comandos", "pausar", "restaurar", "vigilar"])
    p.add_argument("valor", nargs="?", help="confirmar-envio: enviado|no_enviado; comandos: cancelar|publicar")
    p.add_argument("--operador", default="")
    p.add_argument("--config", type=Path, default=None)
    p.add_argument("--perfil", choices=sorted(PERFILES), default="C1", help="caso del banco (por defecto C1)")
    p.add_argument("--evidencia", type=Path, default=None, help="carpeta de evidencia (por defecto, la del perfil)")
    p.add_argument("--confirmar", action="store_true", help="autoriza escribir config.json")
    a = p.parse_args(argv)
    perfil = usar_perfil(a.perfil)
    a.evidencia = a.evidencia or Path(__file__).resolve().parent / perfil.carpeta
    cfg = a.config or datos() / "config.json"
    if a.orden == "estado":
        return cmd_estado(cfg, a.evidencia)
    if a.orden == "ids":
        return cmd_ids(a.evidencia)
    if a.orden == "confirmar-envio":
        if a.valor not in ("enviado", "no_enviado") or not a.operador.strip():
            print("Uso: confirmar-envio enviado|no_enviado --operador NOMBRE")
            return 1
        return cmd_confirmar_envio(a.evidencia, a.valor, a.operador.strip())
    if a.orden == "localizar":
        return cmd_localizar(a.evidencia)
    if a.orden == "comandos":
        if a.valor not in ("cancelar", "publicar"):
            print("Uso: comandos cancelar|publicar")
            return 1
        return cmd_comandos(a.evidencia, a.valor)
    if a.orden == "vigilar":
        if not a.confirmar:
            print("Sin --confirmar solo se vigila el plazo; al vencer NO se restaurará.")
        return cmd_vigilar(cfg, a.evidencia, a.confirmar)
    if not a.confirmar:
        print("Esta orden escribe config.json: agrega --confirmar cuando esté autorizada (AUT-CONFIG-1 / AUT-RESTAURAR).")
        return 1
    return pausar(cfg, a.evidencia) if a.orden == "pausar" else cmd_restaurar(cfg, a.evidencia)


if __name__ == "__main__":
    sys.exit(main())
