"""Reglas de recepción: qué falta, qué preguntar y cómo redactar la respuesta."""

from __future__ import annotations

import re
from collections.abc import Callable
import unicodedata
from functools import lru_cache
from email.utils import parseaddr

from anka.dominio.activities import normalize_estado, tiene_cifra
from anka.dominio.analisis import Actividad, Analisis, Adjunto
from anka.dominio.correo import attachment_names, header, message_datetime, message_text
from anka.dominio.preguntas import EXTENSIONES_GEOGRAFICAS, como_peticion, para_el_cliente, sin_nota_interna
from anka.dominio.work_contract import control_documental


@lru_cache(maxsize=4096)   # función pura que se llama con los mismos textos (nombres de campos) una y otra vez
def normalized_text(value: str) -> str:
    """Texto en minúsculas y sin tildes, para comparar sin que las mayúsculas o los acentos importen."""
    value = "".join(c for c in unicodedata.normalize("NFD", value.casefold())
                    if unicodedata.category(c) != "Mn")
    return value


_AUTOMATED_SENDER = re.compile(
    r"^(?:no[-_.]?reply|do[-_.]?not[-_.]?reply|mailer-daemon|postmaster|notifications?|bounces?)"
    r"(?:[+\-_.].*)?$"
)


# Señales de ruido evidente: identificador -> motivo (genérico, sin datos del remitente).
# Los identificadores son parte del estándar de reglas (skills/.../reglas_operativas.md).
NOISE_SIGNALS = {
    "remitente_no_reply": "remitente automático (no-reply)",
    "list_unsubscribe": "lista de correo o boletín (List-Unsubscribe)",
    "auto_submitted": "mensaje automático (Auto-Submitted)",
    "precedence_masivo": "envío masivo (Precedence)",
    "spam_gmail": "marcado como spam por Gmail",
}


def _noise_signal_id(message: dict) -> str | None:
    local = parseaddr(header(message, "From"))[1].split("@")[0].casefold()
    if _AUTOMATED_SENDER.match(local):
        return "remitente_no_reply"
    if header(message, "List-Unsubscribe"):
        return "list_unsubscribe"
    if header(message, "Auto-Submitted").strip().casefold() not in ("", "no"):
        return "auto_submitted"
    if header(message, "Precedence").strip().casefold() in {"bulk", "list", "junk", "auto_reply"}:
        return "precedence_masivo"
    if "SPAM" in message.get("labelIds", []):
        return "spam_gmail"
    return None


def noise_signal_id(message: dict) -> str | None:
    """El identificador de la señal de ruido (R1) de un mensaje de Gmail, o None.

    Es el que el receptor guarda en la evidencia del mensaje y el que usa la elegibilidad de los trabajos (T-51)."""
    return _noise_signal_id(message)


def _automated_signal(message: dict) -> str | None:
    """Motivo (genérico) por el que un mensaje es automático, o None."""
    signal = _noise_signal_id(message)
    return NOISE_SIGNALS[signal] if signal else None


def automated_noise_reason(messages: list[dict]) -> str | None:
    """Motivo para descartar un hilo sin analizarlo, o None si debe analizarse.

    Descarta solo ruido evidente: TODOS los mensajes del cliente deben tener una señal
    inequívoca de automatización. Un remitente humano nunca se descarta aquí, aunque
    Gmail lo clasifique como Promociones. Decidir si es una solicitud es trabajo del modelo.
    """
    if not messages:
        return None
    reasons = [_automated_signal(message) for message in messages]
    return reasons[0] if all(reasons) else None


def mentions_commercial_terms(
    messages: list[dict], terms: list[str], attachments: list[Adjunto] | None = None
) -> bool:
    """¿Menciona el hilo algún término comercial? Rescata correos automáticos del descarte."""
    if not terms:
        return False
    haystack = []
    for message in messages:
        haystack.extend([
            header(message, "From"),
            header(message, "Subject"),
            message_text(message.get("payload", {})),
        ])
    for attachment in attachments or []:
        haystack.extend([attachment.get("filename", ""), attachment.get("text", "")])
    text = normalized_text("\n".join(haystack))
    return any(normalized_text(term) in text for term in terms)


def missing_questions(analysis: Analisis, required_fields: list[str]) -> list[str]:
    """Preguntas para los datos obligatorios que faltan, más las otras dudas que detectó el análisis."""
    values = {item["name"].casefold(): item["value"].strip()
              for item in analysis["fields"]}
    missing = [name for name in required_fields if not values.get(name.casefold())]
    questions = [f"¿Podría indicar {name}?" for name in missing]
    for question in analysis.get("other_questions", []):
        if question.strip() and not any(_question_repeats_field(question, name) for name in missing):
            questions.append(question.strip())
    return list(dict.fromkeys(questions))


def _known_intake_value(value: object) -> bool:
    text = normalized_text(str(value or "")).strip()
    unknown = (
        "no indicado", "no especificado", "por confirmar", "pendiente",
        "sin definir", "no aplica", "n/a",
    )
    return bool(text) and not any(marker in text for marker in unknown)


def _known_magnitude(value: object) -> bool:
    """¿Es una medida? Debe traer una cantidad (en cifras o en palabras comunes): «Área» o «varios» no lo son.

    El modelo a veces escribe el nombre del dato («Área») donde va el valor; sin esta exigencia el caso salía como
    «información suficiente» sin que nadie preguntara el área (F1-04).
    """
    return _known_intake_value(value) and tiene_cifra(normalized_text(str(value or "")))


def _detected_service_family(analysis: Analisis) -> str:
    text = normalized_text(" ".join([
        str(analysis.get("project_name") or ""),
        str(analysis.get("summary") or ""),
        *[
            f"{item.get('activity', '')} {item.get('servicio_tecnica', '')}"
            for item in analysis.get("activities", []) if isinstance(item, dict)
        ],
    ]))
    rules = (
        ("LiDAR", ("lidar", "laser aerotransportado")),
        ("Batimetría", ("batimetr", "hidrograf")),
        ("Fotogrametría", ("fotogram", "ortofoto", "ortomosaico")),
        ("Termografía o inspección", ("termograf", "inspeccion", "panel solar")),
        ("Georradar", ("georradar", "gpr")),
        ("Escáner terrestre", ("escaner terrestre", "laser terrestre", "tunel")),
        ("Topografía", ("topograf", "levantamiento", "replanteo")),
        ("Catastro o cartografía", ("catastro", "cartograf")),
    )
    return next((label for label, terms in rules if any(term in text for term in terms)),
                "Servicio por clasificar")


# Pregunta por la magnitud del trabajo según el servicio; si no hay una específica se usa la genérica.
_MAGNITUDE_QUESTIONS = {
    "Batimetría": "¿Cuál es la extensión, longitud o número de secciones del cuerpo de agua que debe cubrirse?",
    "Termografía o inspección": (
        "¿Cuál es la cantidad de activos, módulos, estructuras o sitios que deben inspeccionarse?"),
    "Topografía": "¿Cuál es el área, longitud o cantidad aproximada de elementos que deben levantarse?",
}
_GENERIC_MAGNITUDE_QUESTION = "¿Cuál es el área, longitud, cantidad de elementos o magnitud aproximada del trabajo?"

# Lo que conviene precisar antes de cotizar, según el servicio.
_TECHNICAL_CHECKS = {
    "Topografía": ["Elementos y nivel de detalle", "Precisión y sistema de referencia",
                   "Entregables y formatos", "Accesos o restricciones"],
    "LiDAR": ["Densidad o resolución", "Precisión y sistema de referencia",
              "Productos derivados", "Restricciones de vuelo"],
    "Fotogrametría": ["GSD o resolución", "Control terrestre", "Entregables y formatos", "Restricciones de vuelo"],
    "Batimetría": ["Rango de profundidades", "Separación de líneas o secciones",
                   "Datum o referencia vertical", "Acceso y permisos"],
    "Termografía o inspección": ["Tipo de activo", "Condiciones operativas",
                                 "Frecuencia de inspección", "Formato del informe"],
    "Georradar": ["Objeto de búsqueda", "Profundidad esperada", "Condiciones del terreno", "Entregables"],
}
_GENERIC_TECHNICAL_CHECKS = ["Método técnico", "Precisión requerida", "Entregables", "Accesos o restricciones"]


def _intake_facts(analysis: Analisis) -> tuple[bool, bool, bool]:
    """¿Se conocen el objetivo, la ubicación y la magnitud? (en los datos extraídos o en las actividades)."""
    fields = {
        normalized_text(str(item.get("name") or "")): item.get("value")
        for item in analysis.get("fields", []) if isinstance(item, dict)
    }
    activities = [item for item in analysis.get("activities", []) if isinstance(item, dict)]

    def field_known(*terms: str, known: Callable[[object], bool] = _known_intake_value) -> bool:
        return any(
            any(normalized_text(term) in name for term in terms) and known(value)
            for name, value in fields.items()
        )

    location_known = field_known("ubicacion", "localizacion", "poligono", "coordenada") or any(
        _known_intake_value(item.get("ubicacion")) for item in activities
    )
    magnitude_known = field_known(
        "area", "longitud", "cantidad", "dimension", "numero de", "magnitud", known=_known_magnitude
    ) or any(_known_magnitude(item.get("quantity")) for item in activities)
    purpose_known = bool(activities) and any(
        _known_intake_value(item.get("activity")) for item in activities
    )
    return purpose_known, location_known, magnitude_known


def _intake_questions(service: str, purpose_known: bool, location_known: bool, magnitude_known: bool) -> list[str]:
    """Las preguntas básicas que faltan para poder iniciar la revisión interna."""
    questions = []
    if not purpose_known:
        questions.append("¿Cuál es el objetivo del trabajo y qué elementos o activos deben revisarse o levantarse?")
    if not location_known:
        if service in {"LiDAR", "Fotogrametría", "Catastro o cartografía"}:
            questions.append(
                "¿Puede compartir la ubicación y delimitación del área, preferiblemente mediante KMZ/KML, "
                "polígono o coordenadas?")
        else:
            questions.append("¿Puede indicar la ubicación del proyecto y el sector o tramo que debe cubrirse?")
    if not magnitude_known:
        questions.append(_MAGNITUDE_QUESTIONS.get(service, _GENERIC_MAGNITUDE_QUESTION))
    return questions


def reception_assessment(analysis: Analisis) -> dict:
    """Determina si el correo permite iniciar la revisión interna, no cotizar."""
    activities = [item for item in analysis.get("activities", []) if isinstance(item, dict)]
    purpose_known, location_known, magnitude_known = _intake_facts(analysis)
    service = _detected_service_family(analysis)
    questions = _intake_questions(service, purpose_known, location_known, magnitude_known)
    # copia: quien reciba la evaluación puede modificar la lista sin tocar la tabla común
    technical_checks = list(_TECHNICAL_CHECKS.get(service, _GENERIC_TECHNICAL_CHECKS))

    issues = unresolved_scope_issues(analysis)
    blocked = any(
        item.get("status") in {"bloqueado", "lectura_parcial", "no_legible", "parcial"}
        or item.get("coverage") in {"parcial", "no_legible"}
        for item in analysis.get("attachments", []) if isinstance(item, dict)
    )
    reviewed = bool(analysis.get("manual_review"))  # una persona ya resolvió la revisión técnica
    if (not reviewed and (issues or blocked)) or not activities:
        status = "technical_review"
        label = "Requiere revisión técnica interna"
        reason = ("Hay documentos no legibles, dependencias, contradicciones o una solicitud que necesita "
                  "interpretación humana.")
    elif questions:
        status = "needs_client_information"
        label = "Requiere aclaraciones del cliente"
        reason = "Faltan datos básicos para que Anka pueda iniciar la revisión interna con suficiente contexto."
    else:
        status = "sufficient_for_internal_review"
        label = "Información suficiente para revisión interna"
        reason = "La solicitud contiene objetivo, ubicación y magnitud suficientes para iniciar la revisión interna."
    return {
        "status": status,
        "label": label,
        "reason": reason,
        "service_family": service,
        "questions": questions,
        "technical_checks": technical_checks,
    }


def reception_questions(analysis: Analisis, required_fields: list[str] | None = None) -> list[str]:
    """Preguntas que se le hacen al cliente según la evaluación de recepción (ninguna si va a revisión técnica)."""
    assessment = reception_assessment(analysis)
    if assessment["status"] == "technical_review":
        return []
    return assessment["questions"]


def attachment_report(
    attachments: list[Adjunto], analysis: Analisis, required_fields: list[str]
) -> list[Adjunto]:
    """Estado de cada adjunto y qué datos obligatorios no se pudieron confirmar por no poder leerlo."""
    values = {
        item.get("name", "").casefold(): item.get("value", "").strip()
        for item in analysis.get("fields", [])
    }
    missing = [name for name in required_fields if not values.get(name.casefold())]
    report: list[Adjunto] = []
    for attachment in attachments:
        item: Adjunto = {**attachment}
        item.pop("text", None)   # el texto extraído viaja al modelo, no se guarda en el análisis
        if attachment.get("reason", "").startswith("lectura parcial"):
            item["status"] = "lectura_parcial"
            item["coverage"] = "parcial"
        item["blocked_fields"] = (
            missing or ["validación del alcance técnico"]
        ) if item.get("status") in {"bloqueado", "lectura_parcial"} else []
        report.append(item)
    return report


def blank_analysis(summary: str = "") -> Analisis:
    """Análisis vacío de una solicitud que no lo es (o que no se pudo analizar)."""
    return {
        "is_quote_request": False,
        "project_name": "",
        "summary": summary,
        "activities": [],
        "fields": [],
        "other_questions": [],
        "scope_dependencies": [],
        "contradictions": [],
    }


def actividades_sin_confirmar(analysis: Analisis) -> list[Actividad]:
    """Las actividades sin confirmar (pendientes o contradictorias): el alcance aún no está cerrado (U-87)."""
    return [item for item in analysis.get("activities", [])
            if isinstance(item, dict) and normalize_estado(item.get("estado")) != "confirmado"]


_FALTA_DE_UNA_ACTIVIDAD = re.compile(r"^falta(?:n)?\s+(?:por\s+)?(?:definir|confirmar|indicar|aclarar)?\s*(.+)$", re.I)


def preguntas_de_pendientes(analysis: Analisis) -> list[str]:
    """Preguntas para el cliente a partir de lo que el análisis dejó pendiente en cada actividad (U-87).

    Primero las dudas que el propio análisis formuló (`other_questions`); luego, por cada actividad sin confirmar, lo
    que su nota dice que falta. Son un borrador: una persona lo revisa antes de que salga nada (R8).
    """
    preguntas = [q.strip() for q in analysis.get("other_questions", []) if isinstance(q, str) and q.strip()]
    for item in actividades_sin_confirmar(analysis):
        nombre = str(item.get("activity") or "la actividad").strip()
        nota = sin_nota_interna(str(item.get("notes") or ""))
        faltante = _FALTA_DE_UNA_ACTIVIDAD.match(nota.strip().rstrip(".").strip())
        if faltante:
            preguntas.append(f"Sobre «{nombre}»: ¿puede indicar {faltante.group(1).strip()}?")
        else:
            preguntas.append(f"Sobre «{nombre}»: ¿puede confirmar los datos que faltan (cantidad, ubicación o plazo)?")
    return list(dict.fromkeys(preguntas))


def unresolved_scope_issues(analysis: Analisis) -> list[str]:
    """Dependencias de alcance y contradicciones que siguen pendientes (lo que manda un caso a revisión técnica)."""
    issues = []
    for dependency in analysis.get("scope_dependencies", []):
        if dependency.get("status") == "pendiente":
            issues.append(f"Dependencia: {dependency.get('description', 'sin descripción')}")
    for contradiction in analysis.get("contradictions", []):
        if contradiction.get("status", "pendiente") == "pendiente":
            issues.append(
                f"Contradicción en {contradiction.get('topic', 'dato técnico')}: "
                f"{contradiction.get('details', 'requiere revisión')}"
            )
    control = control_documental(analysis)
    issues.extend(m for m in control["pendientes_lectura"] if m not in issues)
    return issues


def _question_repeats_field(question: str, field: str) -> bool:
    def words(value: str) -> set[str]:
        value = "".join(c for c in unicodedata.normalize("NFD", value.casefold())
                        if unicodedata.category(c) != "Mn")
        return {word for word in re.findall(r"[a-z]{4,}", value)
                if word not in {"para", "proyecto", "dato", "datos"}}

    field_words = words(field)
    return bool(field_words) and field_words <= words(question)


def question_email(questions: list[str], forwarded: bool = False) -> str:
    """Correo al cliente con las preguntas numeradas, en el tono de un reenvío o de un mensaje directo."""
    textos = [str(q) for q in questions if q is not None and str(q).strip()]   # una pregunta vacía no se numera
    items = "\n".join(f"{i}. {question}" for i, question in enumerate(textos, 1))
    opening = (
        "Gracias por compartir la solicitud. Para completar la información inicial, "
        if forwarded else
        "Gracias por su solicitud. Para completar la información inicial, "
    )
    return (
        "Hola,\n\n" + opening + "necesitamos confirmar lo siguiente:\n\n" + items +
        "\n\nPuede responder a este mismo correo.\n\nSaludos"
    )


def review_note(analysis: Analisis, questions: list[str], source_message: dict | None = None) -> str:
    """Nota interna para quien revisa el borrador: resumen de actividades, preguntas y origen del correo."""
    activities = analysis.get("activities", [])
    activity_lines = [
        f"- {item.get('activity') or 'Actividad'}: "
        f"{item.get('quantity') or 'no indicado'} {item.get('unit') or ''}; "
        f"ubicación: {item.get('ubicacion') or 'no indicado'}; "
        f"fuente: {item.get('fuente') or 'no indicada'}; "
        f"estado: {item.get('estado') or 'pendiente'}".strip()
        for item in activities
    ] or ["- no indicado"]
    question_lines = [f"- {question}" for question in questions] or ["- no hay preguntas faltantes"]
    routing = analysis.get("routing", {})
    routing_block = ""
    if routing.get("is_forwarded"):
        routing_block = (
            "Enrutamiento del reenvío:\n"
            f"- Remitente interno: {routing.get('internal_forwarder') or 'no indicado'}\n"
            f"- Cliente original: {routing.get('original_sender') or 'no identificado'}\n"
            f"- Destinatario del borrador: {routing.get('selected_recipient') or 'no indicado'}\n"
            f"- Política: {routing.get('reply_policy') or 'internal_reviewer'}\n\n"
        )
    reading_block = ""
    por_tramos = analysis.get("lectura_por_tramos") or {}
    if por_tramos.get("adjuntos"):
        reading_block = "Lectura completa por tramos (no cabía en el modelo y se leyó entero):\n" + "".join(
            f"- {item['adjunto']}: {item['tramos']} tramos, {item['hallazgos']} hallazgos con su localizador\n"
            for item in por_tramos["adjuntos"]
        ) + "\n"
    source_block = ""
    if source_message:
        payload = source_message.get("payload", {})
        names = attachment_names(payload)
        attachment_lines = [f"- {name}" for name in names] or ["- no se detectaron adjuntos"]
        source_text = message_text(payload) or "no se pudo extraer texto del mensaje"
        source_block = (
            "Correo original del cliente:\n"
            f"De: {header(source_message, 'From') or 'no indicado'}\n"
            f"Fecha y hora: {message_datetime(source_message)}\n"
            f"Asunto: {header(source_message, 'Subject') or 'no indicado'}\n"
            "Documentos/adjuntos detectados:\n" + "\n".join(attachment_lines) + "\n\n"
            "Texto enviado por el cliente:\n"
            "----- INICIO CORREO CLIENTE -----\n"
            f"{source_text}\n"
            "----- FIN CORREO CLIENTE -----\n\n"
        )
    return (
        "[REVISAR ANTES DE ENVIAR - BORRAR ESTA SECCION]\n"
        "Validar contra el correo original del cliente en este mismo hilo.\n"
        "Esta seccion es interna y no debe enviarse al cliente.\n\n"
        + source_block +
        routing_block + reading_block +
        f"Resumen detectado: {analysis.get('summary') or 'no indicado'}\n\n"
        "Actividades detectadas:\n" + "\n".join(activity_lines) + "\n\n"
        "Datos/preguntas que motivan esta respuesta:\n" + "\n".join(question_lines) +
        "\n\n--- BORRAR HASTA ESTA LINEA ANTES DE ENVIAR ---\n\n"
    )


def draft_body_with_review_note(
    analysis: Analisis, questions: list[str], body: str, source_message: dict | None = None
) -> str:
    """Cuerpo del borrador de Gmail: la nota de revisión (a borrar antes de enviar) seguida del correo al cliente."""
    return review_note(analysis, questions, source_message) + body


def suggested_supporting_documents(analysis: Analisis) -> list[str]:
    """Documentos de soporte que conviene pedir según lo que el cliente ya adjuntó."""
    attachments = [
        item for item in analysis.get("attachments", [])
        if isinstance(item, dict) and item.get("status") != "ignorado"
    ]
    names = " ".join(str(item.get("filename") or "").casefold() for item in attachments)
    activity_text = " ".join(
        str(item.get("activity") or "") + " " + str(item.get("servicio_tecnica") or "")
        for item in analysis.get("activities", []) if isinstance(item, dict)
    )
    context = normalized_text(activity_text + " " + str(analysis.get("summary") or ""))
    geomatics_request = any(term in context for term in (
        "topograf", "lidar", "fotogram", "batimetr", "georradar", "catastro",
        "levantamiento", "inspeccion", "cartograf",
    ))
    if not geomatics_request:
        return []
    suggestions = []
    if not any(token in names for token in (
        ".kmz", ".kml", ".shp", ".geojson", "poligono", "coordenad",
    )):
        suggestions.append(
            "Archivo de ubicación y delimitación del área, preferiblemente KMZ/KML, "
            "polígono, coordenadas o formato GIS disponible."
        )
    if not any(token in names for token in (".dwg", ".dxf", "plano", "diseno", "diseño")):
        suggestions.append(
            "Planos, diseños preliminares, cartografía base o levantamientos previos "
            "que debamos considerar."
        )
    if not any(token in names for token in (
        "tdr", "terminos", "términos", "especific", "norma", "pliego",
    )):
        suggestions.append(
            "Términos de referencia, especificaciones técnicas o normas del "
            "contratante aplicables, si existen."
        )
    return suggestions


def _sin_repetir_lo_preguntado(documentos: list[str], preguntas: list[str]) -> list[str]:
    """Quita de la lista «también puede adjuntar» lo que ya es una pregunta del correo (el archivo de ubicación)."""
    textos = [normalized_text(pregunta) for pregunta in preguntas]
    if not any(palabra in t for t in textos for palabra in _PALABRAS_DE_UBICACION) and not any(
            "archivo" in t and "ubicacion" in t for t in textos):
        return documentos
    return [d for d in documentos if not d.startswith("Archivo de ubicación")]


def _nombre_del_proyecto(proyecto: str) -> str:
    """El nombre para el correo (U-108): sin espacios de más y a 90 caracteres como mucho; vacío si no hay."""
    limpio = " ".join(str(proyecto or "").split())
    return limpio if len(limpio) <= 90 else limpio[:89].rstrip() + "…"


def correo_con_preguntas(preguntas: list[str], reenviado: bool = False, documentos: list[str] | None = None,
                         proyecto: str = "", codigo: str = "", segunda_ronda: bool = False) -> str:
    """El correo al cliente con sus preguntas numeradas: la plantilla fija de todos los modos (no usa ningún modelo).

    Con `codigo` (la T del caso, para el contacto registrado) el correo nombra el proyecto y el código y pide citarlo al
    responder: desde la T la tabla solo se actualiza con mensajes que lo citen (R11). Nunca se pasa a quien no es el
    contacto registrado.
    """
    # Lo que llega al cliente son preguntas, no notas (U-99); lo que la persona escribió no se reescribe (U-33).
    preguntas = [como_peticion(p, quitar_verbo=False) for p in preguntas]
    nombre = _nombre_del_proyecto(proyecto)
    # Siempre numeradas, también una sola: el sistema reconoce lo preguntado por la lista numerada (U-26).
    question_lines = "\n".join(f"{number}. {question}" for number, question in enumerate(preguntas, 1))
    if codigo:
        intro = "Gracias por su respuesta. Sobre" if segunda_ronda else "Sobre"
        return (
            f"Hola,\n\n{intro} su proyecto «{nombre or 'su solicitud'}» ({codigo}), necesitamos confirmar lo siguiente:"
            f"\n\n{question_lines}\n\nAl responder, por favor cite el código {codigo} para que podamos asociar su "
            "respuesta al proyecto.\n\nPuede responder a este mismo correo.\n\nSaludos,\nGrupo Anka"
        )
    de = f" para el proyecto «{nombre}»" if nombre else ""
    if segunda_ronda:
        opening = f"Gracias por su respuesta{de}. Nos queda por confirmar lo siguiente:"
    else:
        base = "Gracias por compartir la solicitud" if reenviado else "Gracias por su solicitud"
        opening = f"{base}{de}. Para completar la información inicial, necesitamos confirmar lo siguiente:"
    documentos = _sin_repetir_lo_preguntado(documentos or [], preguntas)
    document_block = ""
    if documentos:
        document_lines = "\n".join(f"- {document}" for document in documentos)
        document_block = (
            "\n\nSi dispone de ellos, también puede adjuntar los siguientes "
            "documentos para complementar la revisión:\n\n" + document_lines
        )
    return (
        "Hola,\n\n" + opening + "\n\n"
        + question_lines + document_block
        + "\n\nPuede responder a este mismo correo.\n\nSaludos,\nGrupo Anka"
    )


def suggested_reply(analysis: Analisis, required_fields: list[str] | None = None) -> str:
    """Texto sugerido para responder al cliente: las preguntas pendientes o, si no hay, un acuse de recibo."""
    decididas = analysis.get("preguntas")
    if decididas:  # una persona decidió pregunta por pregunta (U-22): solo salen las que eligió para el cliente
        questions = para_el_cliente(decididas)
    else:
        questions = reception_questions(analysis, required_fields or [])
        questions += (analysis.get("manual_review") or {}).get("questions", [])
    questions = list(dict.fromkeys(questions))  # la misma pregunta puede venir de la recepción y de la revisión
    documents = suggested_supporting_documents(analysis)
    forwarded = analysis.get("routing", {}).get("is_forwarded", False)
    if questions:
        return correo_con_preguntas(questions, forwarded, documents)
    if analysis.get("routing", {}).get("is_forwarded"):
        opening = "Gracias por reenviar la solicitud."
    else:
        opening = "Gracias por su solicitud."
    document_block = ""
    if documents:
        document_block = (
            "\n\nSi dispone de información complementaria, puede adjuntar:\n"
            + "\n".join(f"- {document}" for document in documents)
        )
    return (
        "Hola,\n\n"
        f"{opening} Confirmamos que la hemos recibido y estamos revisando la "
        "información compartida para definir el alcance. Si necesitamos alguna "
        "aclaración adicional, nos comunicaremos por este mismo hilo."
        + document_block + "\n\n"
        "Saludos,\nGrupo Anka"
    )


def _sin_caracteres_de_control(texto: str) -> str:
    """El texto sin caracteres de control ni de formato invisibles (p. ej. «\\u202e», que invierte lo que se ve)."""
    return "".join(" " if unicodedata.category(c) in ("Cc", "Cf") else c for c in texto)


def safe_folder_name(project: str | None, thread_id: str | None, prefix: str) -> str:
    """Nombre de carpeta de la solicitud: prefijo, proyecto sin caracteres no válidos y final del id del hilo.

    El final del id también se limpia: un id de Gmail es alfanumérico, así que fuera de letras, cifras, «-» y «_» no
    queda nada que pueda partir la ruta (separadores, «..», controles) ni dejar un punto o espacio al final.
    """
    clean = re.sub(r'[\\/:*?"<>|]', " ", _sin_caracteres_de_control(str(project or ""))).strip(" .")
    clean = re.sub(r"\s+", " ", clean)[:90].strip(" .") or "Sin nombre"
    final = re.sub(r"[^0-9A-Za-z_-]", "", str(thread_id or ""))[-8:] or "sin-id"
    return f"{prefix} - {clean} - {final}"


# --- Siempre se pide el archivo de ubicación cuando falta (U-95) ----------------------------------------------

_EXTENSIONES_GEOGRAFICAS = EXTENSIONES_GEOGRAFICAS
_PALABRAS_DE_UBICACION = ("kmz", "kml", "shape", "delimit", "trazado", "coordenada", "archivo con la ubicacion",
                          "plano de ubicacion", "poligono")
PREGUNTA_DE_UBICACION = ("¿Podrían remitir el archivo con la ubicación y delimitación exacta del proyecto "
                         "(KML/KMZ o plano del trazado)?")


def agregar_pregunta_de_ubicacion(analysis: Analisis, adjuntos: list[Adjunto]) -> bool:
    """Agrega a las preguntas al cliente la del archivo de ubicación si hace falta y el modelo no la hizo (U-95).

    Hace falta cuando alguna actividad no trae cantidad (no se puede medir sin el polígono o el trazado) y el caso no
    tiene ningún archivo geográfico ni la pregunta ya está entre las del modelo. Devuelve si la agregó.
    """
    actividades = [a for a in analysis.get("activities") or [] if isinstance(a, dict)]
    if not analysis.get("is_quote_request") or not actividades:
        return False
    if all(tiene_cifra(a.get("quantity")) for a in actividades):
        return False
    if any(str(a.get("filename") or "").lower().endswith(_EXTENSIONES_GEOGRAFICAS) for a in adjuntos):
        return False
    preguntas = analysis.setdefault("other_questions", [])
    if any(palabra in normalized_text(str(p)) for p in preguntas for palabra in _PALABRAS_DE_UBICACION):
        return False
    preguntas.append(PREGUNTA_DE_UBICACION)
    return True
