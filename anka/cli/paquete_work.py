"""Paquete de referencia del contrato para la tarea Work de la cola (T-54, D-158).

`construir` escribe en una carpeta local los archivos que Work lee desde Drive; `verificar` los comprueba y `publicar`
(con `--confirmar-drive`) los sube a una subcarpeta de la raíz de laboratorio. Nada corre solo.
"""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import re
import sys
import unicodedata
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import Any

from anka.dominio.clasificacion import CLASIFICACIONES
from anka.dominio.errores import ErrorDeAnka
from anka.infra.clientes_google import ClienteDrive

RAIZ_DEL_REPOSITORIO = Path(__file__).resolve().parents[2]
ESQUEMA = RAIZ_DEL_REPOSITORIO / "spec" / "contrato_work.schema.json"
PROMPT = RAIZ_DEL_REPOSITORIO / "docs" / "PROMPT_WORK_ANKA_V6.txt"
PLANTILLAS = RAIZ_DEL_REPOSITORIO / "docs" / "paquete_work"
NOMBRE_DEL_PAQUETE = "ANKA_REFERENCIA_WORK"
CARPETA_EN_DRIVE = "REFERENCIA_ANKA_V1"
CONTRATO = "ANKA_DASHBOARD_JSON_V1"
MARCA_DE_HUELLA = "anka_paquete_sha256"
DE_PLANTILLA = ("00_LEEME.md", "T-52_CLASIFICACION.md", "FORMATO_DE_RETORNO.md", "INSTRUCCIONES_UNA_EJECUCION.md")
# T-57: un ejemplo más, de SOLICITUD_NUEVA en revisión interna que conserva su tabla preliminar (no es una clase nueva)
EN_REVISION = "SOLICITUD_NUEVA_REVISION_INTERNA"
OBLIGATORIOS = ("manifest.json", "contrato_work.schema.json", "PRECISO_V6.1.1.txt", *DE_PLANTILLA,
                *(f"ejemplo_{clase}.json" for clase in (*CLASIFICACIONES, EN_REVISION)))
TIPOS = {".md": "text/markdown", ".json": "application/json", ".txt": "text/plain"}
# Lo que un paquete autónomo no puede mencionar: nada del equipo de Anka ni archivos comprimidos.
PROHIBIDO_EN_TEXTO = (".zip", "D:\\", "C:\\", "%LOCALAPPDATA%", "/Users/")


def _sha(datos: bytes) -> str:
    return hashlib.sha256(datos).hexdigest()


def _ejemplo_oficial(prompt: str) -> dict[str, Any]:
    """El ejemplo ficticio de PRECISO_V6.1.1: la base de los demás, para no inventar ni simplificar obligatorios."""
    bloque = re.search(r"\[ANKA_DASHBOARD_JSON_V1\]\s*(.*?)\s*\[/ANKA_DASHBOARD_JSON_V1\]", prompt, re.S)
    if bloque is None:
        raise ErrorDeAnka("El prompt PRECISO_V6.1.1 no trae su ejemplo del bloque JSON.")
    return dict(json.loads(bloque.group(1)))


def ejemplos(prompt: str) -> dict[str, dict[str, Any]]:
    """Un ejemplo ficticio válido por clasificación, derivado del ejemplo oficial de PRECISO_V6.1."""
    base = _ejemplo_oficial(prompt)
    nueva = {**copy.deepcopy(base), "classification": "SOLICITUD_NUEVA",
             "classification_reason": "El cliente pide un levantamiento topográfico de un predio, con TdR adjuntos."}
    seguimiento = {
        **copy.deepcopy(base), "message_id": "18f0000000000002", "classification": "SEGUIMIENTO",
        "classification_reason": "Responde en el hilo de la solicitud ya registrada y aporta el dato que faltaba.",
        "received_at": "2026-03-04T10:30:00-05:00", "status": "Información suficiente para revisión interna",
        "summary": ("El cliente responde con el sistema de referencia de entrega (MAGNA-SIRGAS, origen Bogotá). "
                    "Con ese dato no quedan faltantes críticos para revisar el alcance."),
        "fields": [{"name": "Ubicación", "value": "Madrid, Cundinamarca", "source": "correo del 2026-03-02, párrafo 1"},
                   {"name": "Sistema de referencia de entrega", "value": "MAGNA-SIRGAS, origen Bogotá",
                    "source": "correo del 2026-03-04, párrafo 1"}],
        "missing_data": [],
        "evidence_register": [
            base["evidence_register"][0],
            {"id": "E2", "topic": "Sistema de referencia", "status": "explicit", "value": "MAGNA-SIRGAS, origen Bogotá",
             "source": "correo del cliente del 2026-03-04", "location": "párrafo 1", "owner": "client",
             "blocking": False}],
        "question_audit": [
            {"question": "¿En qué sistema de referencia debe entregarse el levantamiento?",
             "reason_missing": "Ya lo indicó el cliente en su respuesta; no se vuelve a preguntar.",
             "reviewed_sources": ["correo del 2026-03-04, párrafo 1"], "question_id": "Q1",
             "target_finding_ids": ["E2"], "disposition": "do_not_ask", "unresolved": ""}],
        "analysis_complete": True}
    seguimiento["activities"][0].update(
        {"estado": "confirmado", "notes": "Sistema de referencia confirmado por el cliente en su respuesta.",
         "fuente": "TdR_El_Roble.pdf, p. 2, numeral 3; correo del 2026-03-04, párrafo 1"})
    vacio: dict[str, Any] = {"activities": [], "fields": [], "missing_data": [], "evidence_register": [],
                             "question_audit": [], "contradictions": [], "scope_dependencies": [],
                             "related_threads": [], "cited_codes": []}
    descartada = {**copy.deepcopy(base), **copy.deepcopy(vacio), "thread_id": "18f0000000000003",
                  "message_id": "18f0000000000003", "client_email": "persona@ejemplo.com",
                  "client_name": "Persona Ejemplo", "requested_by_email": "persona@ejemplo.com",
                  "requested_by_name": "Persona Ejemplo", "project_name": "Mensaje sin solicitud",
                  "status": "Descartada", "classification": "NO_SOLICITUD",
                  "classification_reason": "Es un saludo de cumpleaños: no pide ningún servicio de Anka.",
                  "summary": "Mensaje personal sin relación con topografía, geomática ni otros servicios de Anka.",
                  "attachments": [], "analysis_complete": True}
    revision = {**copy.deepcopy(base), **copy.deepcopy(vacio), "thread_id": "18f0000000000004",
                "message_id": "18f0000000000004", "client_email": "contacto@ejemplo.com",
                "client_name": "Contacto Ejemplo", "requested_by_email": "contacto@ejemplo.com",
                "requested_by_name": "Contacto Ejemplo", "project_name": "Mensaje dudoso",
                "status": "Requiere revisión técnica interna", "classification": "REVISION_MANUAL",
                "classification_reason": "Menciona un predio; no es claro si pide un servicio y no abrió el adjunto.",
                "summary": ("BORRADOR INTERNO — NO ENVIAR. Mensaje 18f0000000000004: el adjunto Plano.dwg no se "
                            "pudo abrir (error literal: formato no compatible). Una persona decide si es solicitud."),
                "question_audit": [{"question": "¿Es una solicitud de servicio y qué contiene el adjunto?",
                                    "reason_missing": "El adjunto no se pudo abrir.",
                                    "reviewed_sources": ["correo, párrafo 1"], "question_id": "Q1",
                                    "target_finding_ids": [], "disposition": "internal_review",
                                    "unresolved": "contenido del adjunto"}],
                "attachments": [{"filename": "Plano.dwg", "message_id": "18f0000000000004", "size_bytes": 90112,
                                 "read_via": "attachment", "status": "bloqueado",
                                 "reason": "formato no compatible", "blocked_fields": []}],
                "analysis_complete": False}
    return {"SOLICITUD_NUEVA": nueva, "SEGUIMIENTO": seguimiento, "NO_SOLICITUD": descartada,
            "REVISION_MANUAL": revision}


def ejemplo_en_revision(prompt: str) -> dict[str, Any]:
    """T-57: SOLICITUD_NUEVA ficticia en revisión interna que CONSERVA su tabla preliminar (no es un BLOQUEO).

    El caso es de prueba: sus datos quedan «unconfirmed» y «pendiente», sin precios ni método inventados, sin
    preguntas al cliente y sin texto para él. Es el contrapeso del retorno vacío del piloto 6C.
    """
    base = _ejemplo_oficial(prompt)
    fila = base["activities"][0]
    return {
        **copy.deepcopy(base), "thread_id": "18f0000000000005", "message_id": "18f0000000000005",
        "client_email": "laura@ejemplo-ingenieria.com", "client_name": "Laura Ejemplo",
        "requested_by_email": "laura@ejemplo-ingenieria.com", "requested_by_name": "Laura Ejemplo",
        "project_name": "Levantamiento lote Hatoviejo (caso de prueba)",
        "received_at": "2026-03-05T08:00:00-05:00", "status": "Requiere revisión técnica interna",
        "classification": "SOLICITUD_NUEVA",
        "classification_reason": "El mensaje pide un levantamiento; los datos son de prueba, no un bloqueo de lectura.",
        "summary": ("BORRADOR INTERNO — NO ENVIAR. Caso de prueba ficticio: se extrae el alcance, pero los datos "
                    "no están "
                    "validados. Pide un levantamiento planimétrico y altimétrico de unas 4,5 ha en Hatoviejo, con "
                    "curvas de nivel. El texto no menciona sistema de referencia."),
        "activities": [
            {**copy.deepcopy(fila), "activity": "Levantamiento planimétrico y altimétrico",
             "servicio_tecnica": "no indicado", "ubicacion": "Lote en Hatoviejo",
             "quantity": "aprox. 4,5 ha", "unit": "ha",
             "entregable": "Curvas de nivel", "plazo_restriccion": "",
             "fuente": "correo, párrafo 1", "estado": "pendiente",
             "notes": "Caso de prueba: datos sin validar. El texto no menciona el sistema de referencia."},
            {**copy.deepcopy(fila), "activity": "Localización de linderos, construcciones y drenajes",
             "servicio_tecnica": "no indicado", "ubicacion": "Lote en Hatoviejo", "quantity": "", "unit": "",
             "entregable": "", "plazo_restriccion": "",
             "fuente": "correo, párrafo 1", "estado": "pendiente",
             "notes": "Sin cantidad ni entregable en el texto."}],
        "fields": [{"name": "Ubicación", "value": "Hatoviejo", "source": "correo, párrafo 1"},
                   {"name": "Área", "value": "aprox. 4,5 ha", "source": "correo, párrafo 1"}],
        "missing_data": [{"name": "Sistema de referencia de entrega", "type": "critical"}],
        "evidence_register": [
            {"id": "E1", "topic": "Vigencia de los datos", "status": "unconfirmed", "value": "caso de prueba",
             "source": "correo, párrafo 1", "location": "mensaje completo", "owner": "anka", "blocking": True},
            {"id": "E2", "topic": "Sistema de referencia", "status": "absent", "value": "",
             "source": "correo", "location": "revisado completo", "owner": "client", "blocking": False}],
        "question_audit": [
            {"question": "¿Se validan los datos del caso de prueba antes de cotizar?",
             "reason_missing": "El caso es ficticio: los datos no tienen vigencia confirmada.",
             "reviewed_sources": ["correo, párrafo 1"], "question_id": "Q1", "target_finding_ids": ["E1"],
             "disposition": "internal_review", "unresolved": "vigencia de los datos"},
            {"question": "Sistema de referencia de entrega (se preguntaría si el caso fuera real).",
             "reason_missing": "No lo indica el correo; al ser un caso de prueba no se consulta al cliente.",
             "reviewed_sources": ["correo, párrafo 1"], "question_id": "Q2", "target_finding_ids": ["E2"],
             "disposition": "internal_review", "unresolved": "sistema de referencia de entrega"}],
        "attachments": [], "analysis_complete": False}


def _plano(texto: object) -> str:
    sin = unicodedata.normalize("NFKD", str(texto or "").casefold())
    return "".join(c for c in sin if not unicodedata.combining(c))


def _palabras(texto: object) -> set[str]:
    """Las palabras de 5 letras o más, sin tildes: sirven para comparar un resumen con lo que sigue pendiente."""
    return {p for p in re.findall(r"[a-z0-9]+", _plano(texto)) if len(p) >= 5}


VERBOS_DE_RECEPCION = ("responde con", "aporta", "envia", "adjunta", "indica", "confirma", "informa")
CERRADOS_PARA_PREGUNTAR = ("Información suficiente para revisión interna", "Alcance completo", "Borrador preparado",
                           "Descartada")


def incoherencias(ejemplo: Mapping[str, Any]) -> list[str]:
    """Contradicciones semánticas de un bloque (heurística): lo que el esquema no puede ver.

    Un dato declarado como recibido no puede seguir como faltante, pregunta pendiente o hallazgo ausente; el estado
    debe decir lo mismo que las preguntas; y la clasificación debe ser coherente con el estado y la tabla (T-52).
    """
    problemas: list[str] = []
    clase, estado = ejemplo.get("classification"), ejemplo.get("status")
    evidencias = [e for e in ejemplo.get("evidence_register") or [] if isinstance(e, dict)]
    preguntas = [q for q in ejemplo.get("question_audit") or [] if isinstance(q, dict)]
    a_cliente = [q for q in preguntas if q.get("disposition") == "ask_client"]
    faltantes = [m for m in ejemplo.get("missing_data") or []]
    nombres_faltantes = [str(m.get("name") if isinstance(m, dict) else m) for m in faltantes]
    for evidencia in evidencias:                                   # lo explícito no puede seguir como faltante
        if evidencia.get("status") != "explicit":
            continue
        tema = _plano(evidencia.get("topic")).strip()
        problemas += [f"«{n}» figura como faltante pero la evidencia {evidencia.get('id')} lo da por explícito."
                      for n in nombres_faltantes if tema and tema in _plano(n)]
    if a_cliente and estado in CERRADOS_PARA_PREGUNTAR:
        problemas.append(f"El estado «{estado}» no admite preguntas pendientes para el cliente.")
    if estado == "Requiere aclaraciones del cliente" and not a_cliente:
        problemas.append("El estado pide aclaraciones del cliente pero no hay ninguna pregunta ask_client.")
    if any(a.get("estado") == "pendiente" for a in ejemplo.get("activities") or [] if isinstance(a, dict)) and not (
            faltantes or a_cliente or any(e.get("blocking") for e in evidencias)):
        problemas.append("Hay actividades pendientes sin ningún faltante, pregunta ni hallazgo que lo explique.")
    resumen = _plano(ejemplo.get("summary"))
    if clase == "SEGUIMIENTO" and any(v in resumen for v in VERBOS_DE_RECEPCION):
        pendientes = [*nombres_faltantes, *(str(q.get("question")) for q in a_cliente),
                      *(str(e.get("topic")) for e in evidencias if e.get("status") == "absent")]
        for pendiente in pendientes:
            comunes = _palabras(pendiente) & _palabras(ejemplo.get("summary"))
            if len(comunes) >= 2:
                problemas.append(f"El resumen dice que el cliente aportó «{pendiente}» y el ejemplo sigue pidiéndolo.")
    if clase == "NO_SOLICITUD" and (estado != "Descartada" or ejemplo.get("activities")):
        problemas.append("NO_SOLICITUD exige estado Descartada y ninguna actividad.")
    if clase == "REVISION_MANUAL" and (estado != "Requiere revisión técnica interna" or ejemplo.get("activities")):
        problemas.append("REVISION_MANUAL exige revisión técnica interna y ninguna actividad.")
    if clase in ("SOLICITUD_NUEVA", "SEGUIMIENTO") and estado == "Descartada":
        problemas.append(f"{clase} no puede quedar Descartada.")
    problemas += _incoherencias_de_revision_interna(ejemplo, a_cliente)
    return problemas


def _incoherencias_de_revision_interna(ejemplo: Mapping[str, Any], a_cliente: Sequence[Mapping[str, Any]]) -> list[str]:
    """T-57: la revisión interna es un estado, no la forma de BLOQUEO; solo el bloqueo documentado vacía la tabla."""
    clase, estado = ejemplo.get("classification"), ejemplo.get("status")
    if clase not in ("SOLICITUD_NUEVA", "SEGUIMIENTO") or estado != "Requiere revisión técnica interna":
        return []
    problemas: list[str] = []
    adjuntos = [a for a in ejemplo.get("attachments") or [] if isinstance(a, dict)]
    bloqueado = any(a.get("status") == "bloqueado" for a in adjuntos)
    if not ejemplo.get("activities") and not bloqueado:
        problemas.append(f"{clase} en revisión interna sin adjunto bloqueado no puede vaciar la tabla de actividades.")
    if ejemplo.get("analysis_complete") is True:
        problemas.append("Un análisis en revisión interna no puede declararse completo.")
    if ejemplo.get("draft_reply"):
        problemas.append("Un análisis en revisión interna no lleva texto para el cliente.")
    if a_cliente and any(p in _plano(ejemplo.get("summary")) for p in ("ficticio", "de prueba", "laboratorio")):
        problemas.append("Un caso ficticio o de laboratorio no genera preguntas ask_client.")
    confirmadas = any(a.get("estado") == "confirmado" for a in ejemplo.get("activities") or [] if isinstance(a, dict))
    if confirmadas and any(e.get("status") == "unconfirmed" and e.get("blocking")
                           for e in ejemplo.get("evidence_register") or [] if isinstance(e, dict)):
        problemas.append("Una actividad no puede estar confirmada con un hallazgo bloqueante sin confirmar.")
    return problemas


def _contenidos(esquema: bytes, prompt: bytes) -> dict[str, bytes]:
    """Todos los archivos del paquete (menos el manifiesto), por ruta relativa, sin la versión aún."""
    archivos: dict[str, bytes] = {"contrato_work.schema.json": esquema, "PRECISO_V6.1.1.txt": prompt}
    for nombre in DE_PLANTILLA:
        archivos[nombre] = (PLANTILLAS / nombre).read_bytes()
    for clase, datos in ejemplos(prompt.decode("utf-8")).items():
        archivos[f"ejemplo_{clase}.json"] = (json.dumps(datos, ensure_ascii=False, indent=2) + "\n").encode()
    archivos[f"ejemplo_{EN_REVISION}.json"] = (
        json.dumps(ejemplo_en_revision(prompt.decode("utf-8")), ensure_ascii=False, indent=2) + "\n").encode()
    return archivos


def construir(destino: Path) -> dict[str, Any]:
    """Escribe el paquete en `destino` (una carpeta) y devuelve su manifiesto. Es determinista."""
    esquema, prompt = ESQUEMA.read_bytes(), PROMPT.read_bytes()
    schema = json.loads(esquema)
    archivos = _contenidos(esquema, prompt)
    version = _sha("".join(f"{n}:{_sha(archivos[n])}\n" for n in sorted(archivos)).encode())[:12]
    archivos["00_LEEME.md"] = (archivos["00_LEEME.md"].decode("utf-8")
                               .replace("{{PAQUETE_VERSION}}", version)
                               .replace("{{ESQUEMA_VERSION}}", str(schema.get("version", "?")))).encode("utf-8")
    ejemplo = _ejemplo_oficial(prompt.decode("utf-8"))
    manifiesto: dict[str, Any] = {
        "paquete": NOMBRE_DEL_PAQUETE, "paquete_version": version,
        "origen": {"repositorio": "Automatizacion_Correos",
                   "archivos": {"contrato_work.schema.json": "spec/contrato_work.schema.json",
                                "PRECISO_V6.1.1.txt": "docs/PROMPT_WORK_ANKA_V6.txt",
                                "documentos": "docs/paquete_work/", "requisitos": "T-52, T-53, T-54"}},
        "contract": {"name": CONTRATO, "schema_version": schema.get("version"),
                     "classification_enum": list(schema["properties"]["classification"]["enum"]),
                     "required": list(schema["required"])},
        "schema_sha256": _sha(esquema),
        "prompt": {"file": "PRECISO_V6.1.1.txt", "version": ejemplo.get("skill_version"), "sha256": _sha(prompt)},
        "files": [{"path": n, "sha256": _sha(archivos[n]), "size_bytes": len(archivos[n])} for n in sorted(archivos)],
    }
    archivos["manifest.json"] = (json.dumps(manifiesto, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
    for nombre, datos in archivos.items():
        ruta = destino / nombre
        ruta.parent.mkdir(parents=True, exist_ok=True)
        ruta.write_bytes(datos)
    return manifiesto


def _problemas_de_archivos(carpeta: Path, manifiesto: Mapping[str, Any]) -> list[str]:
    """Completo e íntegro: cada archivo listado existe con su tamaño y su huella; no falta ninguno obligatorio."""
    problemas: list[str] = []
    listados = {f["path"]: f for f in manifiesto.get("files", [])}
    for nombre in OBLIGATORIOS:
        if nombre != "manifest.json" and nombre not in listados:
            problemas.append(f"El manifiesto no lista {nombre}.")
    for ruta, ficha in listados.items():
        archivo = carpeta / ruta
        if not archivo.is_file():
            problemas.append(f"Falta el archivo {ruta}.")
            continue
        datos = archivo.read_bytes()
        if len(datos) != ficha.get("size_bytes"):
            problemas.append(f"El tamaño de {ruta} no coincide con el manifiesto.")
        elif _sha(datos) != ficha.get("sha256"):
            problemas.append(f"La huella SHA-256 de {ruta} no coincide con el manifiesto.")
        if ruta.endswith((".md", ".txt")) and ruta != "PRECISO_V6.1.1.txt":
            texto = datos.decode("utf-8", errors="replace")
            problemas += [f"{ruta} menciona «{x}», que Work no puede usar." for x in PROHIBIDO_EN_TEXTO if x in texto]
    return problemas


def _problemas_de_vigencia(carpeta: Path, manifiesto: Mapping[str, Any], version_del_contrato: str) -> list[str]:
    """Vigente y sin contradicciones: contrato, prompt, esquema y versión del paquete concuerdan con el manifiesto."""
    problemas: list[str] = []
    contrato = manifiesto.get("contract", {})
    if contrato.get("name") != version_del_contrato:
        problemas.append(f"El paquete es del contrato {contrato.get('name')}, no de {version_del_contrato}.")
    if manifiesto.get("prompt", {}).get("version") != "PRECISO_V6.1.1":
        problemas.append("El paquete no es de PRECISO_V6.1.")
    try:
        esquema = json.loads((carpeta / "contrato_work.schema.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return [*problemas, "El esquema no se puede leer."]
    if esquema.get("version") != contrato.get("schema_version"):
        problemas.append("La versión del esquema no es la del manifiesto.")
    if esquema.get("properties", {}).get("classification", {}).get("enum") != contrato.get("classification_enum"):
        problemas.append("El esquema no trae la clasificación T-52 declarada en el manifiesto.")
    leeme = carpeta / "00_LEEME.md"
    if leeme.is_file() and manifiesto.get("paquete_version", "") not in leeme.read_text(encoding="utf-8"):
        problemas.append("La versión de 00_LEEME.md no es la del manifiesto.")
    return problemas + _problemas_de_ejemplos(carpeta)


def _problemas_de_ejemplos(carpeta: Path) -> list[str]:
    """Los ejemplos no pueden contradecirse a sí mismos (coherencia semántica, no solo el esquema)."""
    problemas: list[str] = []
    for clase in (*CLASIFICACIONES, EN_REVISION):
        ruta = carpeta / f"ejemplo_{clase}.json"
        try:
            datos = json.loads(ruta.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue                                   # lo dice la comprobación de archivos
        problemas += [f"ejemplo_{clase}.json: {p}" for p in incoherencias(datos)]
    return problemas


def verificar_paquete(carpeta: Path, version_del_contrato: str = CONTRATO) -> list[str]:
    """Lo que Work debe comprobar antes de analizar: completo, íntegro, vigente y sin contradicciones. Vacío = bien."""
    try:
        manifiesto = json.loads((carpeta / "manifest.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return ["Falta manifest.json o no es JSON válido."]
    return _problemas_de_archivos(carpeta, manifiesto) + _problemas_de_vigencia(carpeta, manifiesto,
                                                                               version_del_contrato)


# --- Publicación en Drive ---------------------------------------------------------------------------------------------

def publicar(drive: ClienteDrive, raiz: str, origen: Path) -> dict[str, Any]:
    """Sube el paquete verificado a `raiz/REFERENCIA_ANKA_V1`. Idempotente; nunca borra ni elige entre duplicados."""
    from io import BytesIO

    from googleapiclient.http import MediaIoBaseUpload

    from anka.servicios.despacho import asegurar_carpeta

    problemas = verificar_paquete(origen)
    if problemas:
        raise ErrorDeAnka("El paquete local no pasa la verificación: " + " ".join(problemas))
    carpeta = asegurar_carpeta(drive, raiz, CARPETA_EN_DRIVE)
    informe: dict[str, Any] = {"carpeta": carpeta, "archivos": {}}
    for ruta in sorted(OBLIGATORIOS):
        datos = (origen / ruta).read_bytes()
        nombre, huella = ruta, _sha(datos)
        consulta = (f"'{carpeta}' in parents and trashed = false and name = '{nombre}'")
        existentes = drive.files().list(q=consulta, fields="files(id,name,appProperties,webViewLink)",
                                        pageSize=10).execute().get("files", [])
        if len(existentes) > 1:
            raise ErrorDeAnka(f"Hay {len(existentes)} archivos «{nombre}» en {CARPETA_EN_DRIVE}; no se elige uno.")
        medio = MediaIoBaseUpload(BytesIO(datos), mimetype=TIPOS[Path(ruta).suffix], resumable=False)
        if existentes and (existentes[0].get("appProperties") or {}).get(MARCA_DE_HUELLA) == huella:
            informe["archivos"][ruta] = {"resultado": "sin_cambios", "id": existentes[0]["id"]}
            continue
        propiedades = {MARCA_DE_HUELLA: huella}
        if existentes:
            hecho = drive.files().update(fileId=existentes[0]["id"], media_body=medio,
                                         body={"appProperties": propiedades}, fields="id,webViewLink").execute()
            informe["archivos"][ruta] = {"resultado": "actualizado", "id": hecho["id"]}
        else:
            hecho = drive.files().create(body={"name": nombre, "parents": [carpeta], "appProperties": propiedades},
                                         media_body=medio, fields="id,webViewLink").execute()
            informe["archivos"][ruta] = {"resultado": "creado", "id": hecho["id"]}
    for ruta in informe["archivos"]:               # verificar: uno solo por nombre
        nombre = ruta
        pagina = drive.files().list(q=f"'{carpeta}' in parents and trashed = false and name = '{nombre}'",
                                    fields="files(id)", pageSize=10).execute().get("files", [])
        if len(pagina) != 1:
            raise ErrorDeAnka(f"Tras publicar hay {len(pagina)} archivos «{nombre}»; revisar a mano.")
    return informe


def build_parser() -> argparse.ArgumentParser:
    """Órdenes: construir, verificar y publicar."""
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="orden", required=True)
    for nombre, ayuda in (("construir", "escribe el paquete en una carpeta local"),
                          ("verificar", "comprueba una carpeta local (solo lectura)"),
                          ("publicar", "sube el paquete a Drive (requiere --confirmar-drive)")):
        orden = sub.add_parser(nombre, help=ayuda)
        orden.add_argument("--carpeta", required=True, type=Path, help="carpeta local del paquete")
        if nombre == "publicar":
            orden.add_argument("--carpeta-id", required=True, help="raíz de laboratorio de Drive")
            orden.add_argument("--confirmar-drive", action="store_true", help="autoriza la escritura real en Drive")
    return parser


def main(argv: Sequence[str] | None = None, salida: Callable[[str], Any] = print) -> int:
    """Punto de entrada: 0 bien, 2 rechazado o paquete con problemas."""
    args = build_parser().parse_args(argv)
    try:
        if args.orden == "construir":
            resultado: Mapping[str, Any] = construir(args.carpeta)
        elif args.orden == "verificar":
            problemas = verificar_paquete(args.carpeta)
            resultado = {"correcto": not problemas, "problemas": problemas}
            if problemas:
                salida(json.dumps(resultado, ensure_ascii=False, indent=2))
                return 2
        else:
            if not args.confirmar_drive:
                raise ErrorDeAnka("Publicar escribe en Drive: agrega --confirmar-drive cuando esté autorizado.")
            from anka.infra.google_services import services

            _gmail, drive = services()
            resultado = publicar(drive, args.carpeta_id, args.carpeta)
    except ErrorDeAnka as exc:
        print(f"Rechazado: {exc}", file=sys.stderr)
        return 2
    salida(json.dumps(resultado, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
