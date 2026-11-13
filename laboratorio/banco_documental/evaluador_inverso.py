"""Evaluador inverso (T-77): del retorno de Work (contrato operativo) a una estructura comparable con el banco documental.

Es la dirección contraria al mapeador de T-76. Función pura de laboratorio: no consulta Gmail, Drive ni SQLite, no crea nada y
no altera el retorno (trabaja sobre copias). No es un análisis comercial ni una aprobación: solo hace comparable lo verificable.

Orden (cada paso puede detener la evaluación, y nada se evalúa si falla):
1. sintaxis estricta (T-75, sin reparar) y que sea un objeto;
2. el puente real la aceptaría (esquema y tolerancia de `anka.dominio`, sobre una copia);
3. identidad: `thread_id` y `message_id` iguales a los AUTORIZADOS para esa ejecución, que se aportan aparte;
4. clasificación que avanza (un mensaje de revisión manual o que no es solicitud no trae actividades evaluables).
Después: cantidad, unidad y fuente pasan literales (nada se convierte ni recalcula); lo histórico, retirado, de una hoja no
vigente o de otro archivo NO entra como actividad vigente; las contradicciones y la evidencia visual no certificada se
conservan como tales; lo que no es comparable y lo que se pierde se registra.
"""

from __future__ import annotations

import copy
import json
import re
import sys
from pathlib import Path
from typing import Any

from anka.dominio import work_contract
from anka.dominio.clasificacion import AVANZAN
from anka.dominio.tolerancia_work import Rechazo, comprobar_identidad, normalizar_analisis
from laboratorio.banco_documental import comparador_v2, validador

EVALUADO = "EVALUADO"
DETENIDO_RETORNO_INVALIDO = "DETENIDO_RETORNO_INVALIDO"
DETENIDO_IDENTIDAD = "DETENIDO_IDENTIDAD"
DETENIDO_CLASIFICACION = "DETENIDO_CLASIFICACION"
Dato = dict[str, Any]

_PAGINA = re.compile(r"^p(?:\.|ág\.?|agina)?\s*(\d+)$", re.IGNORECASE)
_CLAVE_VALOR = re.compile(r"^(tabla|fila|hoja|celda)\s+(.+)$", re.IGNORECASE)
_HOJA_Y_RANGO = re.compile(r"^(?P<hoja>[^!;]+)!(?P<rango>\$?[A-Za-z]{1,3}\$?\d+(?::\$?[A-Za-z]{1,3}\$?\d+)?)$")
_SIN_DATO = {"", "no indicado", "no especificado", "n/a", "-"}
_ESTADOS_VISUALES = {"no_certificada", "no certificada", "uncertified", "no_validada", "sin_validar", "certificada"}
# Campos del retorno sin equivalente en la estructura del banco (se cuentan, no se interpretan).
_NO_COMPARABLES = ("fields", "missing_data", "evidence_register", "related_threads", "cited_codes", "summary", "notes",
                   "draft_reply", "DRIVE_PREFLIGHT", "reading_audit", "applicability", "classification_reason")


def _texto(valor: Any) -> str:
    return valor if isinstance(valor, str) else ("" if valor is None else str(valor))


def parsear_fuente(fuente: str) -> tuple[str, Dato, list[str]]:
    """«archivo, p. 1, tabla 1, fila 2» -> (archivo, origen, trozos que no se pudieron estructurar). No inventa claves."""
    trozos = [t.strip() for t in fuente.split(",") if t.strip()]
    if not trozos:
        return "", {}, []
    origen: Dato = {}
    sobrantes: list[str] = []
    localizadores: list[list[str]] = []
    for trozo in trozos[1:]:
        pagina, par = _PAGINA.match(trozo), _CLAVE_VALOR.match(trozo)
        if pagina:
            origen["pagina"] = int(pagina.group(1))
        elif par:
            origen[par.group(1).lower()] = par.group(2).strip()
        else:
            citas = [_HOJA_Y_RANGO.match(parte.strip()) for parte in trozo.split(";") if parte.strip()]
            if citas and all(citas):
                localizadores.extend([c.group("hoja").strip(), c.group("rango")] for c in citas if c)
            else:
                sobrantes.append(trozo)
    if len(localizadores) == 1 and ":" not in localizadores[0][1]:
        origen["hoja"], origen["celda"] = localizadores[0]      # una sola celda exacta: es el localizador de siempre
    elif localizadores:
        origen["localizadores"] = localizadores                  # rangos o varias hojas: se comparan por contención
    return trozos[0], origen, sobrantes


def _separar_cantidad(cantidad: Any, unidad: str, transformaciones: list[str], indice: int) -> str:
    """La cantidad como texto literal; si repite la unidad al final («18,5 ha» + «ha») se quita SOLO esa repetición y se dice."""
    if isinstance(cantidad, bool) or cantidad is None:
        return ""
    texto = str(cantidad).strip()
    if not isinstance(cantidad, str):
        transformaciones.append(f"activities[{indice}].quantity llegó como número y se leyó como texto «{texto}»")
        return texto
    if unidad and texto.casefold().endswith(unidad.casefold()) and len(texto) > len(unidad):
        resto = texto[: -len(unidad)].strip()
        if resto and re.fullmatch(r"[\d.,\s]+", resto):
            transformaciones.append(f"activities[{indice}].quantity «{texto}» repetía la unidad «{unidad}»; se comparó «{resto}»")
            return resto
    return texto


def _restos(valor: Any) -> list[Any]:
    return valor if isinstance(valor, list) else ([valor] if isinstance(valor, str) and valor.strip() else [])


def _clave_de_duplicado(fila: Dato) -> tuple[str, ...]:
    return tuple(validador._normal(str(fila[c])) for c in ("nombre", "cantidad", "unidad", "fuente_literal"))


def _actividades(payload: Dato, nombre_documento: str, hojas_no_vigentes: frozenset[str], salida: Dato) -> list[Dato]:
    vigentes: list[Dato] = []
    vistas: set[tuple[str, ...]] = set()
    for indice, fila in enumerate(_restos(payload.get("activities"))):
        if not isinstance(fila, dict):
            salida["descartadas"].append({"indice": indice, "motivo": "fila escrita como texto, sin cantidad, unidad ni fuente", "contenido": fila})
            continue
        unidad = _texto(fila.get("unit")).strip()
        fuente = _texto(fila.get("fuente")).strip()
        archivo, origen, sobrantes = parsear_fuente(fuente)
        motivo = ""
        if "ya no se requiere" in _texto(fila.get("notes")).casefold():
            motivo = "el cliente retiró la actividad («ya no se requiere»): no es vigente"
        elif nombre_documento and archivo.casefold() != nombre_documento.casefold():
            motivo = f"su fuente «{archivo or 'sin fuente'}» no es el documento evaluado ({nombre_documento})"
        elif str(origen.get("hoja", "")) in hojas_no_vigentes:
            motivo = f"su fuente es la hoja «{origen['hoja']}», declarada no vigente: no es una cantidad vigente"
        elif origen.get("localizadores") and all(hoja in hojas_no_vigentes for hoja, _ in origen["localizadores"]):
            motivo = "todas las hojas que cita como fuente están declaradas no vigentes: no es una cantidad vigente"
        if motivo:
            salida["descartadas"].append({"indice": indice, "motivo": motivo, "contenido": fila})
            continue
        if sobrantes:
            salida["limitaciones"].append(f"activities[{indice}].fuente: no se pudo estructurar «{', '.join(sobrantes)}» (se conserva en fuente_literal)")
        if not origen:
            salida["limitaciones"].append(f"activities[{indice}].fuente sin localizador estructurable: «{fuente}»")
        comparable: Dato = {"nombre": _texto(fila.get("activity")), "cantidad": _separar_cantidad(fila.get("quantity"), unidad, salida["transformaciones"], indice),
                            "unidad": unidad, "entregable": _texto(fila.get("entregable")), "origen": origen, "fuente_literal": fuente,
                            "estado_work": _texto(fila.get("estado")), "notas_work": _texto(fila.get("notes"))}
        clave = _clave_de_duplicado(comparable)
        if clave in vistas:
            salida["duplicadas"].append({"indice": indice, "contenido": fila})
            continue
        vistas.add(clave)
        vigentes.append(comparable)
        for campo in ("servicio_tecnica", "ubicacion"):
            if _texto(fila.get(campo)).strip().casefold() not in _SIN_DATO:
                salida["perdidas"].append(f"activities[{indice}].{campo} «{_texto(fila.get(campo))}»: la estructura del banco no lo tiene")
    return vigentes


def _textos_de(valor: Any, claves: tuple[str, ...]) -> list[str]:
    """Texto literal de cada elemento: la cadena misma, o las claves del objeto unidas con « | » en el orden dado."""
    textos = []
    for elemento in _restos(valor):
        if isinstance(elemento, dict):
            partes = [_texto(elemento.get(c)).strip() for c in claves if _texto(elemento.get(c)).strip()]
            adicionales = [" ".join(_texto(s) for s in elemento[c]) for c in ("sources",) if isinstance(elemento.get(c), list) and elemento[c]]
            texto = " | ".join([*partes, *adicionales])
        else:
            texto = _texto(elemento).strip()
        if texto:
            textos.append(texto)
    return textos


def _adjuntos(payload: Dato, salida: Dato, autorizado: Dato) -> tuple[list[str], list[str]]:
    visuales, limitaciones = [], []
    for indice, adjunto in enumerate(_restos(payload.get("attachments"))):
        if not isinstance(adjunto, dict):
            continue
        nombre = _texto(adjunto.get("filename")) or f"adjunto {indice + 1}"
        otro = _texto(adjunto.get("message_id"))
        if otro and otro != autorizado["message_id"]:
            salida["limitaciones"].append(f"attachments[{indice}] ({nombre}) cita otro message_id; se conserva sin usarlo para identificar el correo")
        interpretacion = _texto(adjunto.get("visual_interpretation")).strip()
        if interpretacion:
            certificada = interpretacion.casefold() == "certificada"
            salida["visual"].append({"archivo": nombre, "estado": "certificada_segun_work" if certificada else "no_certificada",
                                     "texto": "" if certificada or interpretacion.casefold() in _ESTADOS_VISUALES else interpretacion})
            if interpretacion.casefold() not in _ESTADOS_VISUALES:
                visuales.append(interpretacion)
        for campo in ("reason", "coverage"):
            if _texto(adjunto.get(campo)).strip() and (campo == "reason" or _texto(adjunto.get(campo)).casefold() != "completa"):
                limitaciones.append(f"{nombre}: {campo} = {_texto(adjunto.get(campo)).strip()}")
        for bloqueado in _restos(adjunto.get("blocked_fields")):
            limitaciones.append(f"{nombre}: campo bloqueado «{_texto(bloqueado)}»")
        if adjunto.get("evidence"):
            salida["no_comparables"]["attachments[].evidence"] = salida["no_comparables"].get("attachments[].evidence", 0) + len(adjunto["evidence"])
    for dependencia in _restos(payload.get("scope_dependencies")):
        texto = _textos_de([dependencia], ("description",))
        if texto and "visual" in texto[0].casefold():
            visuales.append(texto[0])
    return visuales, limitaciones


def _restricciones_en_fields(payload: Dato, nombre_documento: str, salida: Dato) -> list[str]:
    """Un dato de `fields` con valor y con fuente que cita el documento evaluado también cuenta como restricción registrada.

    Sin fuente, o con una fuente de otro archivo, no cuenta (no se acepta un dato que Work no respalda). Solo sirve para
    reconocer una restricción de la referencia (mismos términos): no crea actividades ni oculta una omisión: si ningún campo
    trae el valor y el concepto de la restricción, sigue siendo `restriccion_omitida`.
    """
    textos = []
    for campo in _restos(payload.get("fields")):
        if not isinstance(campo, dict):
            continue
        nombre, valor = _texto(campo.get("name")).strip(), _texto(campo.get("value")).strip()
        archivo = parsear_fuente(_texto(campo.get("source")).strip())[0]
        if not (nombre and valor and archivo) or valor.casefold() in _SIN_DATO:
            continue
        if nombre_documento and archivo.casefold() != nombre_documento.casefold():
            continue
        textos.append(f"{nombre}: {valor}")
    if textos:
        salida["transformaciones"].append(f"{len(textos)} dato(s) de `fields` con fuente en el documento también se consultan como restricciones registradas")
    return textos


def convertir(payload: Dato, autorizado: Dato, nombre_documento: str = "", hojas_no_vigentes: frozenset[str] = frozenset(),
              caso: str = "") -> Dato:
    """El retorno YA aceptado -> {comparable, descartadas, duplicadas, perdidas, limitaciones, ...}. No modifica `payload`."""
    salida: Dato = {"descartadas": [], "duplicadas": [], "perdidas": [], "limitaciones": [], "transformaciones": [],
                    "no_comparables": {}, "visual": []}
    actividades = _actividades(payload, nombre_documento, hojas_no_vigentes, salida)
    visuales, limitaciones = _adjuntos(payload, salida, autorizado)
    auditoria = [a for a in _restos(payload.get("question_audit")) if isinstance(a, dict)]
    por_disposicion = {d: [a for a in auditoria if _texto(a.get("disposition")).casefold() == d] for d in ("ask_client", "internal_review", "do_not_ask")}
    sin_disposicion = [a for a in auditoria if _texto(a.get("disposition")).casefold() not in por_disposicion]
    if por_disposicion["do_not_ask"]:
        salida["no_comparables"]["question_audit[do_not_ask]"] = len(por_disposicion["do_not_ask"])
    if sin_disposicion:
        salida["limitaciones"].append(f"{len(sin_disposicion)} elementos de question_audit sin disposición reconocible: no se comparan")
    restricciones = [_texto(f.get("plazo_restriccion")).strip() for f in _restos(payload.get("activities")) if isinstance(f, dict)
                     and _texto(f.get("plazo_restriccion")).strip().casefold() not in _SIN_DATO]
    restricciones += _restricciones_en_fields(payload, nombre_documento, salida)
    entregables = sorted({a["entregable"] for a in actividades if a["entregable"].strip().casefold() not in _SIN_DATO})
    comparable: Dato = {
        "caso": caso, "actividades": actividades, "entregables": entregables, "restricciones": restricciones,
        "contradicciones": _textos_de(payload.get("contradictions"), ("topic", "details")),
        "preguntas_cliente": [_texto(a.get("question")) for a in por_disposicion["ask_client"] if _texto(a.get("question")).strip()],
        "revisiones_internas": [_texto(a.get("question")) for a in por_disposicion["internal_review"] if _texto(a.get("question")).strip()],
        "elementos_visuales": visuales, "limitaciones_de_lectura": limitaciones}
    for campo in _NO_COMPARABLES:
        if payload.get(campo):
            salida["no_comparables"][campo] = len(payload[campo]) if isinstance(payload[campo], (list, dict)) else 1
    salida["comparable"] = comparable
    return salida


def _detenido(estado: str, motivo: str, **extra: Any) -> Dato:
    return {"estado": estado, "motivo": motivo, "errores_criticos": 0, "sin_errores_criticos_automaticos": False,
            "aviso": "evaluación detenida: no se analizó ninguna actividad", **extra}


def _identidad(payload: Dato, autorizado: Dato) -> str:
    for campo in ("thread_id", "message_id"):
        esperado = autorizado.get(campo)
        if not isinstance(esperado, str) or not esperado.strip():
            return f"no se aportó el {campo} autorizado para esta ejecución"
        if payload.get(campo) != esperado:
            return f"el {campo} del retorno no es el autorizado"
    return ""


_OMISIONES = {"faltante_sin_reconocer": "faltantes", "restriccion_omitida": "restricciones", "contradiccion_omitida": "contradicciones"}


def _pistas_en_campos_no_comparables(informe: Dato, referencia: Dato, payload: Dato) -> list[Dato]:
    """Una omisión que sí se menciona en campos sin equivalente (missing_data, evidence_register, fields, notas): se señala a una persona."""
    textos = (_textos_de(payload.get("missing_data"), ("name", "type")) + _textos_de(payload.get("evidence_register"), ("topic", "value", "location"))
              + _textos_de(payload.get("fields"), ("name", "value"))
              + [_texto(f.get("notes")) for f in _restos(payload.get("activities")) if isinstance(f, dict) and _texto(f.get("notes")).strip()])
    grupos = {(seccion, e["id"]): e["grupos"] for seccion in _OMISIONES.values() for e in referencia.get(seccion, [])}
    return [{"ref": h["ref"], "detalle": f"«{h['codigo']}»: no está en las listas comparables, pero un texto de missing_data, evidence_register, fields o notas lo menciona",
             "verificacion": "humana"} for h in informe["hallazgos"]
            if h["codigo"] in _OMISIONES and comparador_v2.coincide(textos, grupos.get((_OMISIONES[h["codigo"]], h["ref"]), []))]


def evaluar_retorno(contenido: bytes, autorizado: Dato, referencia: Dato, documento: bytes | None = None, nombre_documento: str = "",
                    hojas_no_vigentes: frozenset[str] = frozenset(), origen_del_retorno: str = "no declarado") -> Dato:
    """Evalúa los BYTES de un retorno de Work contra la referencia v2 del caso; se detiene si es inválido o la identidad no coincide."""
    valor, error = validador.leer(contenido)
    base = {"sha256_del_retorno": validador.sha256(contenido), "origen_del_retorno": origen_del_retorno}
    if error is not None:
        return {**base, **_detenido(DETENIDO_RETORNO_INVALIDO, "sintaxis", error_de_sintaxis=error)}
    if not isinstance(valor, dict):
        return {**base, **_detenido(DETENIDO_RETORNO_INVALIDO, "la raíz del retorno no es un objeto")}
    prueba = copy.deepcopy(valor)
    estricto = work_contract.validate(valor)
    try:
        normalizar_analisis(prueba)
    except Rechazo as rechazo:
        return {**base, **_detenido(DETENIDO_RETORNO_INVALIDO, "esquema", detalle=str(rechazo), errores_de_esquema=estricto)}
    try:
        comprobar_identidad(prueba)
    except Rechazo as rechazo:
        return {**base, **_detenido(DETENIDO_IDENTIDAD, "identidad incompleta en el retorno", detalle=str(rechazo))}
    motivo = _identidad(valor, autorizado)
    if motivo:
        return {**base, **_detenido(DETENIDO_IDENTIDAD, motivo)}
    clasificacion = _texto(prueba.get("classification"))
    if clasificacion and clasificacion not in AVANZAN:
        return {**base, **_detenido(DETENIDO_CLASIFICACION, f"la clasificación {clasificacion} no lleva actividades evaluables")}
    conversion = convertir(valor, autorizado, nombre_documento, hojas_no_vigentes, referencia["caso"])
    informe = comparador_v2.comparar_v2(referencia, conversion["comparable"])
    fuentes = validador.verificar_fuentes(conversion["comparable"], documento, nombre_documento) if documento is not None else []
    informe["pistas_para_revision_humana"] = _pistas_en_campos_no_comparables(informe, referencia, valor)
    informe.update(base)
    informe.update({
        "estado": EVALUADO, "identidad": {"thread_id": valor["thread_id"], "message_id": valor["message_id"], "autorizada": True},
        "desviaciones_del_esquema": estricto, "correcciones_del_puente": list(prueba.get("_correcciones_de_work", [])),
        "avisos_del_puente": list(prueba.get("_avisos_del_puente", [])),
        "fuentes_inconsistentes": [h.como_dict() for h in fuentes],
        "actividades_descartadas": conversion["descartadas"], "actividades_duplicadas": conversion["duplicadas"],
        "evidencia_visual_declarada": conversion["visual"], "contradicciones_conservadas": conversion["comparable"]["contradicciones"],
        "transformaciones": conversion["transformaciones"], "perdidas": conversion["perdidas"], "limitaciones": conversion["limitaciones"],
        "no_comparables": conversion["no_comparables"], "extraccion_comparable": conversion["comparable"]})
    if fuentes:
        informe["sin_errores_criticos_automaticos"] = False
    if origen_del_retorno != "work_real":
        informe["aviso"] += f"; el retorno es «{origen_del_retorno}»: este resultado NO describe el desempeño de Work"
    return informe


def main(argumentos: list[str]) -> int:
    """Uso: python -m laboratorio.banco_documental.evaluador_inverso <retorno.json> <referencia_v2.json> <thread_id> <message_id> [documento]."""
    if len(argumentos) not in (4, 5):
        print(main.__doc__)
        return 2
    referencia = json.loads(Path(argumentos[1]).read_text(encoding="utf-8"))
    documento = Path(argumentos[4]) if len(argumentos) == 5 else None
    informe = evaluar_retorno(Path(argumentos[0]).read_bytes(), {"thread_id": argumentos[2], "message_id": argumentos[3]}, referencia,
                              documento.read_bytes() if documento else None, documento.name if documento else "")
    print(json.dumps(informe, ensure_ascii=False, indent=2))
    return 0 if informe["estado"] == EVALUADO else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
