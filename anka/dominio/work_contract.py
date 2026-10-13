"""Contrato entre ChatGPT Work y el puente: valida el JSON del borrador contra `spec/contrato_work.schema.json`.

El esquema es la fuente de verdad (campos, tipos y estados permitidos). Se valida un subconjunto de
JSON Schema —type, required, enum, properties, items, minLength— sin dependencias externas. Un valor
`null` en un campo opcional se trata como ausente: Work a veces lo escribe así y no debe rechazar el caso.
"""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any
import re
import unicodedata

from anka.dominio.analisis import Analisis

SCHEMA_PATH = Path(__file__).resolve().parents[2] / "spec" / "contrato_work.schema.json"

_TYPES: dict[str, type | tuple[type, ...]] = {
    "string": str, "array": list, "object": dict, "boolean": bool,
    "number": (int, float), "integer": int,
}


@lru_cache(maxsize=1)
def load_schema() -> dict:
    """Carga el esquema JSON del contrato con Work (`spec/contrato_work.schema.json`)."""
    return json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))


def _type_ok(value: Any, expected: str | list) -> bool:
    names = expected if isinstance(expected, list) else [expected]
    for name in names:
        python_type = _TYPES[name]
        if isinstance(value, bool) and name in ("number", "integer"):
            continue
        if isinstance(value, python_type):
            return True
    return False


def validate(instance: Any, schema: dict | None = None, path: str = "") -> list[str]:
    """Lista de errores (vacía si el valor cumple el esquema). No lanza."""
    schema = schema if schema is not None else load_schema()
    where = path or "el JSON"
    errors: list[str] = []
    if "type" in schema and not _type_ok(instance, schema["type"]):
        expected = schema["type"] if isinstance(schema["type"], str) else " o ".join(schema["type"])
        return [f"{where} debe ser de tipo {expected}."]
    if "enum" in schema and instance not in schema["enum"]:
        errors.append(f"{where} tiene un valor no permitido: {instance!r}.")
    if isinstance(instance, str) and "minLength" in schema and len(instance.strip()) < schema["minLength"]:
        errors.append(f"{where} no puede estar vacío.")
    if isinstance(instance, dict):
        for key in schema.get("required", []):
            if instance.get(key) is None:
                errors.append(f"Falta el campo {key}.")
        for key, sub in schema.get("properties", {}).items():
            if instance.get(key) is not None:
                errors.extend(validate(instance[key], sub, f"{path + '.' if path else ''}{key}"))
    if isinstance(instance, list) and "items" in schema:
        for index, item in enumerate(instance):
            errors.extend(validate(item, schema["items"], f"{where}[{index}]"))
    return errors


def _validate_question_evidence(payload: dict) -> list[str]:
    """Calcula advertencias mecánicas sobre el soporte de preguntas del contrato v2."""
    errors: list[str] = []
    by_id = _evidence_findings(payload.get("evidence_register") or [], errors)
    seen_questions: dict[str, int] = {}
    seen_targets: dict[tuple[tuple[str, ...], str], tuple[int, str]] = {}
    for index, audit in enumerate(payload.get("question_audit") or []):
        if isinstance(audit, dict):
            errors.extend(_audit_question(index, audit, by_id, payload))
            if audit.get("disposition") == "ask_client":
                question_key = _question_key(audit.get("question"))
                if question_key:
                    if question_key in seen_questions:
                        errors.append(
                            f"question_audit[{index}] repite la pregunta de question_audit["
                            f"{seen_questions[question_key]}] (ignorando mayúsculas, tildes y puntuación)."
                        )
                    else:
                        seen_questions[question_key] = index
                refs = audit.get("target_finding_ids") or []
                unresolved = _question_key(audit.get("unresolved"))
                target_key = (tuple(sorted(set(refs))), unresolved) if isinstance(refs, list) else ((), "")
                if target_key[0] and unresolved:
                    if target_key in seen_targets and question_key != seen_targets[target_key][1]:
                        errors.append(
                            f"question_audit[{index}] vuelve a preguntar por los mismos hallazgos y dato pendiente "
                            f"de question_audit[{seen_targets[target_key][0]}]."
                        )
                    else:
                        seen_targets.setdefault(target_key, (index, question_key))
    return errors


def _question_key(value: Any) -> str:
    """Normaliza una pregunta para detectar duplicados textuales sin comparación semántica."""
    text = str(value or "").casefold()
    text = "".join(char for char in unicodedata.normalize("NFD", text)
                   if unicodedata.category(char) != "Mn")
    return re.sub(r"[^a-z0-9]+", " ", text).strip()


def _evidence_findings(findings: list, errors: list[str]) -> dict[str, dict]:
    """Indexa hallazgos y registra ids repetidos."""
    by_id: dict[str, dict] = {}
    for index, finding in enumerate(findings):
        if not isinstance(finding, dict):
            continue
        finding_id = str(finding.get("id") or "").strip()
        if finding_id in by_id:
            errors.append(f"evidence_register[{index}].id está duplicado: {finding_id}.")
        elif finding_id:
            by_id[finding_id] = finding
    return by_id


def _audit_question(index: int, audit: dict, by_id: dict[str, dict], payload: dict) -> list[str]:
    """Valida referencias y soporte verificable de una pregunta."""
    errors: list[str] = []
    disposition = audit.get("disposition")
    if disposition not in {"ask_client", "internal_review", "do_not_ask"}:
        return [f"question_audit[{index}] debe indicar disposition para el contrato v2."]
    refs = audit.get("target_finding_ids") or []
    if len(refs) != len(set(refs)):
        errors.append(f"question_audit[{index}] repite un target_finding_id.")
    targets = []
    for ref in refs:
        finding = by_id.get(ref)
        if finding is None:
            errors.append(f"question_audit[{index}] referencia un hallazgo inexistente: {ref}.")
        else:
            targets.append(finding)
    if disposition == "ask_client":
        errors.extend(_ask_client_warnings(index, audit, targets, payload))
    return errors


def _ask_client_warnings(index: int, audit: dict, targets: list[dict], payload: dict) -> list[str]:
    """Comprueba que una pregunta al cliente tenga evidencia de un faltante suyo."""
    if not targets:
        return [f"question_audit[{index}] pregunta al cliente sin hallazgo objetivo."]
    errors: list[str] = []
    if not str(audit.get("unresolved") or "").strip():
        errors.append(f"question_audit[{index}] debe precisar qué subdato sigue sin resolver.")
    eligible_status = {"partial", "absent", "contradictory", "unconfirmed"}
    eligible = [item for item in targets if item.get("status") in eligible_status
                and item.get("owner") in {"client", "contractor"} and item.get("blocking") is True]
    if not eligible:
        errors.append(
            f"question_audit[{index}] pregunta al cliente sin evidencia bloqueante pendiente atribuible "
            "al cliente/contratista."
        )
    if any(item.get("status") == "explicit" for item in targets):
        errors.append(
            f"question_audit[{index}] incluye un hallazgo explícito; separa ese dato de lo que realmente falta."
        )
    if not audit.get("reviewed_sources"):
        errors.append(f"question_audit[{index}] debe registrar las fuentes revisadas antes de preguntar.")
    if any(item.get("status") == "unreadable" for item in targets):
        errors.append(
            f"question_audit[{index}] no debe pedir al cliente resolver una limitación de lectura; "
            "use internal_review."
        )
    errors.extend(_unreadable_attachment_warning(index, audit, payload))
    return errors


def _unreadable_attachment_warning(index: int, audit: dict, payload: dict) -> list[str]:
    """Advierte si la pregunta traslada al cliente un límite de lectura de archivo."""
    question = str(audit.get("question") or "").casefold()
    unreadable_types = {
        str(item.get("filename") or "").rsplit(".", 1)[-1].casefold()
        for item in (payload.get("attachments") or [])
        if isinstance(item, dict)
        and (item.get("coverage") == "no_legible"
             or str(item.get("status") or "").casefold() in {"no_legible", "unreadable", "ilegible"})
        and "." in str(item.get("filename") or "")
    }
    mentioned = sorted(ext for ext in unreadable_types
                       if re.search(rf"(?<![a-z0-9]){re.escape(ext)}(?![a-z0-9])", question))
    if not mentioned:
        return []
    extensions = ", .".join(mentioned)
    return [f"question_audit[{index}] pregunta sobre contenido de un adjunto .{extensions} no legible; "
            "primero requiere revisión interna."]


def question_evidence_warnings(payload: dict) -> list[str]:
    """Advertencias accionables; no rechaza análisis ni hace desaparecer borradores."""
    if not isinstance(payload, dict) or "evidence_register" not in payload:
        return []
    return _validate_question_evidence(payload)


def _plain(value: object) -> str:
    """Texto en minúsculas y sin tildes: Work escribe a veces los estados sin ellas."""
    text = unicodedata.normalize("NFKD", str(value or "").casefold())
    return "_".join("".join(char for char in text if not unicodedata.combining(char)).split())


_COMPLETE = {"completa", "completo", "complete", "extraido"}
_INCOMPLETE = {"parcial", "partial", "lectura_parcial", "no_legible", "ilegible", "unreadable", "bloqueado"}
_VISUAL_EXTENSIONS = {"pdf", "png", "jpg", "jpeg", "tif", "tiff", "bmp", "dwg", "dxf", "doc", "docx", "ppt", "pptx"}


def attachment_coverage_warnings(payload: dict) -> list[str]:
    """Advertencias cuando la cobertura que Work declara por adjunto no es coherente (T-25).

    Una lectura «completa» debe dejar rastro de lo revisado (secciones o evidencia); una parcial o ilegible debe decir
    por qué. Así una lectura superficial no pasa por exhaustiva. No rechaza el análisis: solo avisa.
    """
    warnings = []
    for index, item in enumerate((payload.get("attachments") if isinstance(payload, dict) else None) or []):
        if not isinstance(item, dict):
            continue
        name = item.get("filename") or f"adjunto {index + 1}"
        declared = {_plain(item.get("coverage")), _plain(item.get("status"))}
        extension = str(name).rsplit(".", 1)[-1].casefold() if "." in str(name) else ""
        visual_state = _plain(item.get("visual_interpretation"))
        visual_reason = _plain(item.get("reason"))
        visual_relevant = extension in _VISUAL_EXTENSIONS or any(
            word in visual_reason for word in ("visual", "imagen", "grafico", "plano", "lamina", "pagina escaneada")
        )
        if visual_state in {"no_certificada", "uncertified", "no_validada", "sin_validar"} and visual_relevant:
            warnings.append(
                f"attachments[{index}] ({name}) tiene lectura visual no certificada; requiere revisión humana "
                "de páginas, imágenes o planos antes de considerar completo su alcance."
            )
        if declared & _INCOMPLETE:
            if not str(item.get("reason") or "").strip():
                warnings.append(
                    f"attachments[{index}] ({name}) declara lectura parcial o ilegible sin indicar el motivo."
                )
        elif declared & _COMPLETE and not item.get("reviewed_sections") and not item.get("evidence"):
            warnings.append(
                f"attachments[{index}] ({name}) declara lectura completa sin secciones revisadas ni evidencia."
            )
    return warnings


def corregir_descuidos_de_tipo(payload: dict) -> list[str]:
    """Corrige en el sitio los descuidos de tipo evidentes de Work y devuelve un aviso por cada uno (T-02).

    Hoy: `question_audit[n].unresolved` escrito como booleano (`true`) donde va un texto con el subdato pendiente. No
    hay forma de recuperar ese texto, pero perder el borrador entero por eso es peor: queda vacío y el aviso lo cuenta.
    """
    avisos = []
    for indice, item in enumerate((payload.get("question_audit") if isinstance(payload, dict) else None) or []):
        if isinstance(item, dict) and isinstance(item.get("unresolved"), bool):
            valor = "true" if item["unresolved"] else "false"
            avisos.append(
                f"question_audit[{indice}].unresolved llegó como {valor} y no como texto; se trató como vacío."
            )
            item["unresolved"] = ""
    return avisos


def _avisos_de_segmentos(payload: dict, preflight: dict, audit: dict) -> list[str]:
    """Los avisos de la lectura declarada con el expediente en Drive (V5 y anteriores; T-35)."""
    warnings = []
    if preflight.get("thread_id") != payload.get("thread_id"):
        warnings.append("El preflight no corresponde al hilo analizado.")
    if payload.get("message_id") not in (preflight.get("message_ids") or []):
        warnings.append("El preflight no incluye el mensaje analizado.")
    if not preflight.get("manifest_revision"):
        warnings.append("No se verificó la revisión del manifiesto.")
    for kind in ("email", "attachment"):
        read = preflight.get(kind + "_segments_read")
        available = preflight.get(kind + "_segments_available")
        if type(read) is not int or type(available) is not int or read < 0 or read != available:
            warnings.append(f"Segmentos {kind}: lectura pendiente o conteos no verificables.")
    for kind in ("email", "attachment"):
        expected = audit.get(kind + "_segment_ids_expected")
        read_ids = audit.get(kind + "_segment_ids_read")
        if expected is None and read_ids is None and preflight.get(kind + "_segments_available"):
            warnings.append(f"Segmentos {kind}: faltan IDs para verificar la lectura declarada.")
        if expected is not None or read_ids is not None:
            if not _same_unique_ids(expected, read_ids):
                warnings.append(f"Segmentos {kind}: IDs omitidos, repetidos o no verificables.")
            elif isinstance(read_ids, list) and len(read_ids) != preflight.get(kind + "_segments_read"):
                warnings.append(f"Segmentos {kind}: los IDs no coinciden con el conteo.")
    return warnings


def usa_verificacion_por_adjuntos(payload: dict) -> bool:
    """¿El borrador es de un prompt que lee desde Gmail (V6 en adelante) y se verifica por nombre y tamaño (T-41)?

    Los borradores anteriores (V5 y antes, con expediente en Drive) siguen su camino de siempre hasta que se retire.
    """
    if not isinstance(payload, dict):
        return False
    # hasta tres cifras: una versión absurda («PRECISO_V999…9») no debe romper la conversión a entero
    version = re.match(r"PRECISO_V(\d{1,3})(?!\d)", str(payload.get("skill_version") or ""), re.ASCII)
    return version is not None and int(version.group(1)) >= 6


SIN_NOMBRE = "(adjunto sin nombre)"


def _nombre_visible(nombre: object) -> str:
    """El nombre de un adjunto tal como se muestra en un aviso: un texto en una línea, nunca la forma de una lista."""
    texto = " ".join(nombre.split()) if isinstance(nombre, str) else ""
    return texto or SIN_NOMBRE


def _nombre_de_adjunto(nombre: object) -> str:
    """El nombre de un adjunto para compararlo: en forma Unicode NFC, sin mayúsculas ni espacios de más.

    Gmail y Work pueden escribir la misma tilde compuesta («á») o descompuesta («a» + acento): es el mismo archivo.
    """
    if not isinstance(nombre, str):
        return ""
    return " ".join(unicodedata.normalize("NFC", nombre).casefold().split())


def _tamano_en_bytes(tamano: object) -> int | None:
    """El tamaño de un adjunto como entero: acepta `10`, `10.0` o `"10"`; `None` si no es un entero no negativo."""
    if isinstance(tamano, bool):
        return None
    if isinstance(tamano, float):
        return int(tamano) if tamano.is_integer() and tamano >= 0 else None
    if isinstance(tamano, str) and re.fullmatch(r"\s*\d{1,15}\s*", tamano, re.ASCII):
        return int(tamano)
    if isinstance(tamano, int) and tamano >= 0:
        return tamano
    return None


def _clave_de_adjunto(nombre: object, tamano: object) -> tuple[str, int | None]:
    """Cómo se reconoce un adjunto: su nombre normalizado y su tamaño en bytes (`None` si no es un entero)."""
    return _nombre_de_adjunto(nombre), _tamano_en_bytes(tamano)


def verificar_adjuntos(payload: dict, adjuntos_gmail: list[dict] | None,
                       incrustadas: list[dict] | None = None) -> list[str]:
    """Comprueba que Work reportó cada adjunto que Gmail tiene en el hilo, por nombre y tamaño (T-41).

    `adjuntos_gmail` son los adjuntos reales del hilo (`filename`, `size`); `None` si no se pudo consultar Gmail.
    `incrustadas` son las imágenes del cuerpo (`imagenes_incrustadas`): no se exigen, pero tampoco son desconocidas.
    Deja el resultado en `reading_audit` (`verification`: verificada / incompleta / sin_verificar, y los archivos que
    faltan o que Gmail no tiene) y, si la lectura no está verificada, `analysis_complete` en falso. Devuelve un
    aviso por diferencia.
    """
    audit = _diccionario(payload.get("reading_audit"))
    avisos: list[str] = []
    pendientes: list[str] = []
    desconocidos: list[str] = []
    if adjuntos_gmail is None:
        verificacion = "sin_verificar"
        avisos.append("Lectura sin verificar: no se pudo consultar Gmail; se reintentará en la siguiente revisión.")
    else:
        # dos adjuntos con el mismo nombre y tamaño son el mismo archivo reenviado: cuentan como uno
        esperados = {_clave_de_adjunto(a.get("filename"), a.get("size")): a
                     for a in adjuntos_gmail if isinstance(a, dict)}
        atendidos = payload.get("attachments")
        reportados = [item for item in (atendidos if isinstance(atendidos, list) else [])
                      if isinstance(item, dict) and item.get("status") != "ignorado"]
        # sin un tamaño entero el archivo no se puede verificar (T-41, punto 2), aunque Gmail tampoco lo diera
        leidos = {clave for clave in (_clave_de_adjunto(item.get("filename"), item.get("size_bytes"))
                                      for item in reportados if item.get("coverage")) if clave[1] is not None}
        pendientes = [_nombre_visible(a.get("filename")) for clave, a in esperados.items() if clave not in leidos]
        # una imagen incrustada (logo, foto pegada en el cuerpo) existe en Gmail aunque no sea un adjunto por leer
        nombres = {clave[0] for clave in esperados} | {_nombre_de_adjunto(i.get("filename"))
                                                      for i in incrustadas or [] if isinstance(i, dict)}
        desconocidos = [_nombre_visible(item.get("filename")) for item in reportados
                        if _nombre_de_adjunto(item.get("filename")) not in nombres]
        audit["attachments_expected"] = [{"filename": _nombre_visible(a.get("filename")),
                                          "size_bytes": _tamano_en_bytes(a.get("size"))}
                                         for a in esperados.values()]
        verificacion = "incompleta" if pendientes or desconocidos else "verificada"
        if pendientes:
            avisos.append(f"{len(pendientes)} de {len(esperados)} adjuntos sin lectura verificada: "
                          f"{', '.join(pendientes)}.")
        if desconocidos:
            avisos.append(f"Work reportó {len(desconocidos)} adjunto(s) que no están en Gmail: "
                          f"{', '.join(desconocidos)}.")
    audit.update({"verification": verificacion, "attachment_files_pending": pendientes,
                  "attachment_files_unknown": desconocidos})
    payload["reading_audit"] = audit
    if verificacion != "verificada" and payload.get("analysis_complete") is not False:
        payload["analysis_complete"] = False
        avisos.append("El análisis no se da por completo: la lectura de los adjuntos no está verificada contra Gmail.")
    return avisos


def _diccionario(valor: object) -> dict:
    """Copia de un objeto del borrador; `{}` si Work escribió otra cosa (un texto, una lista)."""
    return dict(valor) if isinstance(valor, dict) else {}


def _avisos_de_verificacion(audit: dict) -> list[str]:
    """Los avisos de la lectura verificada contra Gmail por nombre y tamaño (T-41)."""
    avisos = []
    if audit.get("verification") == "sin_verificar":
        avisos.append("Lectura sin verificar: Gmail no respondió; se reintentará.")
    if audit.get("attachment_files_pending"):
        faltan = ", ".join(map(str, audit["attachment_files_pending"]))
        avisos.append(f"Adjuntos sin lectura verificada: {faltan}.")
    if audit.get("attachment_files_unknown"):
        avisos.append("Adjuntos que Work reportó y Gmail no tiene: "
                      + ", ".join(map(str, audit["attachment_files_unknown"])) + ".")
    return avisos


def reading_summary(payload: dict) -> dict[str, Any]:
    """Resume lectura declarada y coherencia del preflight sin certificar comprensión visual (T-35)."""
    audit = _diccionario(payload.get("reading_audit"))
    preflight = _diccionario(payload.get("DRIVE_PREFLIGHT"))
    if audit.get("verification"):
        warnings = _avisos_de_verificacion(audit)
    elif not preflight:
        return {"status": "no indicado", "warnings": [], "visual": "no indicado"}
    else:
        warnings = _avisos_de_segmentos(payload, preflight, audit)
    declarados = payload.get("attachments")
    attachments = [item for item in (declarados if isinstance(declarados, list) else []) if isinstance(item, dict)]
    visual_pending = any(item.get("visual_interpretation") == "no_certificada" for item in attachments)
    if visual_pending:
        warnings.append("Interpretación visual pendiente; la extracción no la certifica.")
    if any(item.get("coverage") != "completa" for item in attachments):
        warnings.append("Hay adjuntos con cobertura pendiente o parcial.")
    warnings.extend(attachment_coverage_warnings(payload))
    return {"status": "pendiente" if warnings else "completa declarada",
            "warnings": list(dict.fromkeys(warnings)),
            "visual": "pendiente" if visual_pending else "consultar revisión por archivo"}


def _same_unique_ids(expected: object, read: object) -> bool:
    """Compara IDs sin aceptar duplicados, conteos equivalentes ni datos que no sean listas de texto."""
    if not isinstance(expected, list) or not isinstance(read, list):
        return False
    if any(not isinstance(value, str) or not value for value in [*expected, *read]):
        return False
    return len(set(expected)) == len(expected) and len(set(read)) == len(read) and set(expected) == set(read)


def _normalizar_ids(declarados: object, segmentos: list[dict]) -> tuple[list[str], list[str]]:
    """(identificadores del manifiesto que Work dice haber leído, citas que no corresponden a ningún segmento).

    Acepta el identificador o el nombre del segmento y lo normaliza al identificador, sin repetidos y en el orden del
    manifiesto.
    """
    alias = {valor: segmento["id"] for segmento in segmentos for valor in (segmento["id"], segmento.get("name"))
             if valor}
    declarados = declarados if isinstance(declarados, list) else []
    citados = [valor for valor in declarados if isinstance(valor, str) and valor]
    leidos = {alias[valor] for valor in citados if valor in alias}
    return ([segmento["id"] for segmento in segmentos if segmento["id"] in leidos],
            [valor for valor in citados if valor not in alias])


def reconciliar_lectura(payload: dict, instantanea: dict | None) -> list[str]:
    """Reemplaza lo comprobable de la auditoría de lectura por lo que dice el manifiesto del preparador (T-37).

    Revisión, mensajes, conteos y listas de IDs esperados y pendientes salen del manifiesto; de Work solo se toma qué
    segmentos dice haber leído, reducidos a los que existen. Devuelve un aviso por cada diferencia y, si la lectura no
    está verificada, deja `analysis_complete` en falso. Sin instantánea del mismo hilo no toca nada.
    """
    if (not isinstance(payload, dict) or not isinstance(instantanea, dict) or not instantanea.get("thread_id")
            or instantanea.get("thread_id") != payload.get("thread_id")):
        return []
    avisos: list[str] = []
    preflight = _diccionario(payload.get("DRIVE_PREFLIGHT"))
    audit = _diccionario(payload.get("reading_audit"))
    revision = instantanea.get("revision")
    declarada = preflight.get("manifest_revision")
    mensajes = list(instantanea.get("message_ids") or [])
    incompleta = False
    if declarada and declarada != revision:
        avisos.append("Work leyó una revisión del manifiesto que no es la vigente; la lectura puede estar "
                      "desactualizada.")
        incompleta = True
    if payload.get("message_id") not in mensajes:
        avisos.append("El manifiesto vigente no incluye el mensaje analizado.")
        incompleta = True
    for tipo, clave in (("email", "email_segments"), ("attachment", "attachment_segments")):
        segmentos = [s for s in instantanea.get(clave) or [] if isinstance(s, dict) and s.get("id")]
        esperados = [segmento["id"] for segmento in segmentos]
        leidos, desconocidos = _normalizar_ids(audit.get(tipo + "_segment_ids_read"), segmentos)
        pendientes = [valor for valor in esperados if valor not in leidos]
        etiqueta = "del correo" if tipo == "email" else "de adjuntos"
        if isinstance(preflight.get(tipo + "_segments_available"), int) and \
                preflight[tipo + "_segments_available"] != len(esperados):
            avisos.append(f"Work declaró {preflight[tipo + '_segments_available']} segmentos {etiqueta}; "
                          f"el manifiesto tiene {len(esperados)}.")
        if desconocidos:
            avisos.append(f"Work citó {len(desconocidos)} segmento(s) {etiqueta} que no existen en el manifiesto.")
        archivos = list(dict.fromkeys(s["archivo"] for s in segmentos
                                      if s["id"] in pendientes and s.get("archivo")))
        if pendientes:
            nombres = f": {', '.join(archivos)}" if archivos else ""
            avisos.append(
                f"{len(pendientes)} de {len(esperados)} segmentos {etiqueta} sin lectura verificada{nombres}.")
            incompleta = True
        audit.update({tipo + "_segment_ids_expected": esperados, tipo + "_segment_ids_read": leidos,
                      tipo + "_segment_ids_pending": pendientes})
        if tipo == "attachment":
            audit["attachment_files_pending"] = archivos
        preflight.update({tipo + "_segments_available": len(esperados), tipo + "_segments_read": len(leidos)})
    preflight.update({"thread_id": instantanea["thread_id"], "manifest_revision": revision, "message_ids": mensajes})
    payload["DRIVE_PREFLIGHT"], payload["reading_audit"] = preflight, audit
    if incompleta and payload.get("analysis_complete") is not False:
        payload["analysis_complete"] = False
        avisos.append("El análisis no se da por completo: la lectura no está verificada contra el manifiesto.")
    return avisos


def lectura_sin_verificar(analysis: Analisis) -> bool:
    """¿La lectura que Work declaró no se pudo verificar? (T-41; en V5 y antes, el preflight del expediente en Drive).

    Con la verificación contra Gmail es `incompleta`; un borrador del expediente en Drive se da por sin verificar si
    falla su preflight o la auditoría de segmentos. Sin ninguna de las dos no hay nada que verificar. No mira si una
    persona ya la revisó (U-29): eso lo decide quien muestra la pantalla.
    """
    if analysis.get("engine") != "chatgpt_work":
        return False
    audit = analysis.get("reading_audit") or {}
    if audit.get("verification"):
        return bool(audit["verification"] == "incompleta")
    preflight = analysis.get("DRIVE_PREFLIGHT") or {}
    if not preflight:
        return False
    if not preflight.get("manifest_revision"):
        return True
    message_id = analysis.get("work_message_id")
    if message_id and message_id not in (preflight.get("message_ids") or []):
        return True
    for kind in ("email", "attachment"):
        available = preflight.get(kind + "_segments_available")
        read_count = preflight.get(kind + "_segments_read")
        expected = audit.get(kind + "_segment_ids_expected")
        read_ids = audit.get(kind + "_segment_ids_read")
        if not isinstance(available, int) or not isinstance(read_count, int) or read_count != available:
            return True
        if not isinstance(expected, list) or not isinstance(read_ids, list):
            return available > 0
        if len(set(expected)) != len(expected) or len(set(read_ids)) != len(read_ids):
            return True
        if set(expected) != set(read_ids) or len(read_ids) != read_count:
            return True
    return False


def archivos_sin_verificar(analysis: Analisis) -> list[str]:
    """Los archivos que quedaron sin leer o que Work nombró y Gmail no tiene (vacío si no se sabe cuáles)."""
    audit = analysis.get("reading_audit") or {}
    archivos = audit.get("attachment_files_pending") or audit.get("attachment_files_unknown") or []
    return [str(a) for a in archivos] if isinstance(archivos, list) else []
