"""Esquema de la evidencia documental (T-117) y comprobación de su forma.

La forma se comprueba a mano (el proyecto no depende de `jsonschema`). `ESQUEMA_JSON` es la misma definición en
JSON Schema, para documentarla y para que otra herramienta la pueda usar; `tests` comprueba que ambas coinciden en los
campos obligatorios y en los valores permitidos.
"""

from __future__ import annotations

from typing import Any

VERSION_MODELO = "evidencia-documental/1"

METODOS = ("texto_nativo", "celda", "ocr", "vision", "humano")
ESTADOS_DE_LECTURA = (
    "leido",                      # se leyó y el valor está respaldado en la fuente
    "no_encontrado",              # se buscó en el documento y el dato no está (no es lo mismo que no poder leerlo)
    "ilegible",                   # el lugar existe pero no se pudo leer
    "contradictorio",             # otra evidencia vigente dice algo distinto del mismo concepto
    "parcialmente_leido",         # se leyó una parte; lo que falta se declara
    "pendiente_revision_visual",  # solo una persona viendo el original puede confirmarlo
)
TIPOS_DE_EVIDENCIA = ("cantidad", "texto", "elemento_visual")
VIGENCIAS = ("vigente", "historica", "sin_confirmar")   # sin_confirmar: p. ej. una hoja oculta; una persona decide
CONFIANZAS = ("alta", "media", "baja")
TIPOS_DE_DOCUMENTO = ("pdf_digital", "pdf_escaneado", "xlsx")
TIPOS_DE_INTERPRETACION = ("actividad", "restriccion", "faltante", "pregunta", "alcance")
ESTADOS_DE_CONTRADICCION = ("abierta", "resuelta_por_persona")
UNIDADES_DE_COBERTURA = ("paginas", "hojas")

# Estados que impiden declarar que la lectura está completa.
ESTADOS_QUE_BLOQUEAN_LA_COMPLETITUD = ("ilegible", "parcialmente_leido", "pendiente_revision_visual")

_OBLIGATORIOS_RAIZ = ("version_modelo", "caso", "documentos", "evidencias", "interpretaciones", "contradicciones", "analisis")
_OBLIGATORIOS_DOCUMENTO = ("archivo", "sha256", "tipo", "cobertura")
_OBLIGATORIOS_COBERTURA = ("unidad", "total", "leidas", "ilegibles", "detalle")
_OBLIGATORIOS_EVIDENCIA = (
    "id", "documento", "tipo", "metodo", "estado_de_lectura", "vigencia", "confianza", "revision_humana_requerida",
)
_OBLIGATORIOS_INTERPRETACION = ("id", "tipo", "texto", "evidencias", "confianza")
_OBLIGATORIOS_CONTRADICCION = ("id", "tema", "evidencias", "estado")
_OBLIGATORIOS_ANALISIS = ("declarado_completo", "motivos_de_incompletitud")

ESQUEMA_JSON: dict[str, Any] = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "title": "Evidencia documental de ANKA (laboratorio T-117)",
    "type": "object",
    "required": list(_OBLIGATORIOS_RAIZ),
    "properties": {
        "version_modelo": {"const": VERSION_MODELO},
        "caso": {"type": "string"},
        "documentos": {
            "type": "array",
            "items": {
                "type": "object",
                "required": list(_OBLIGATORIOS_DOCUMENTO),
                "properties": {
                    "archivo": {"type": "string"},
                    "sha256": {"type": "string", "pattern": "^[0-9a-f]{64}$"},
                    "tipo": {"enum": list(TIPOS_DE_DOCUMENTO)},
                    "cobertura": {
                        "type": "object",
                        "required": list(_OBLIGATORIOS_COBERTURA),
                        "properties": {
                            "unidad": {"enum": list(UNIDADES_DE_COBERTURA)},
                            "total": {"type": "integer", "minimum": 1},
                            "leidas": {"type": "integer", "minimum": 0},
                            "ilegibles": {"type": "integer", "minimum": 0},
                            "detalle": {"type": "array"},
                        },
                    },
                },
            },
        },
        "evidencias": {
            "type": "array",
            "items": {
                "type": "object",
                "required": list(_OBLIGATORIOS_EVIDENCIA),
                "properties": {
                    "id": {"type": "string"},
                    "documento": {"type": "string"},
                    "localizador": {
                        "type": "object",
                        "description": "pagina (+ tabla, fila o region, descriptivos) o hoja + celda | rango",
                    },
                    "tipo": {"enum": list(TIPOS_DE_EVIDENCIA)},
                    "concepto": {"type": "string"},
                    "texto_literal": {"type": ["string", "null"]},
                    "cantidad_literal": {"type": ["string", "null"]},
                    "unidad_literal": {"type": ["string", "null"]},
                    "valor_normalizado": {
                        "type": ["object", "null"],
                        "description": "opcional: valor, unidad y regla; nunca sustituye al literal",
                    },
                    "metodo": {"enum": list(METODOS)},
                    "estado_de_lectura": {"enum": list(ESTADOS_DE_LECTURA)},
                    "vigencia": {"enum": list(VIGENCIAS)},
                    "confianza": {"enum": list(CONFIANZAS)},
                    "revision_humana_requerida": {"type": "boolean"},
                    "certifica_interpretacion_visual": {"const": False},
                    "motivo": {"type": "string"},
                    "faltante": {"type": "string"},
                    "revisor": {"type": "string"},
                },
            },
        },
        "interpretaciones": {
            "type": "array",
            "items": {
                "type": "object",
                "required": list(_OBLIGATORIOS_INTERPRETACION),
                "properties": {
                    "tipo": {"enum": list(TIPOS_DE_INTERPRETACION)},
                    "confianza": {"enum": list(CONFIANZAS)},
                    "evidencias": {"type": "array", "items": {"type": "string"}},
                },
            },
        },
        "contradicciones": {
            "type": "array",
            "items": {
                "type": "object",
                "required": list(_OBLIGATORIOS_CONTRADICCION),
                "properties": {"estado": {"enum": list(ESTADOS_DE_CONTRADICCION)}},
            },
        },
        "analisis": {
            "type": "object",
            "required": list(_OBLIGATORIOS_ANALISIS),
            "properties": {"declarado_completo": {"type": "boolean"}},
        },
    },
}


def _faltan(obj: Any, obligatorios: tuple[str, ...], ruta: str, salida: list[tuple[str, str]]) -> bool:
    if not isinstance(obj, dict):
        salida.append((ruta, "debe ser un objeto"))
        return False
    for clave in obligatorios:
        if clave not in obj:
            salida.append((f"{ruta}.{clave}", "campo obligatorio ausente"))
    return True


def _enum(obj: dict, clave: str, permitidos: tuple[str, ...], ruta: str, salida: list[tuple[str, str]]) -> None:
    if clave in obj and obj[clave] not in permitidos:
        salida.append((f"{ruta}.{clave}", f"valor no permitido {obj[clave]!r}; admitidos: {', '.join(permitidos)}"))


def comprobar_forma(doc: Any) -> list[tuple[str, str]]:  # noqa: C901  (una comprobación tras otra)
    """Errores de forma como pares (ruta, mensaje). Vacío si el documento tiene la estructura esperada."""
    errores: list[tuple[str, str]] = []
    if not _faltan(doc, _OBLIGATORIOS_RAIZ, "$", errores):
        return errores
    if doc.get("version_modelo") != VERSION_MODELO:
        errores.append(("$.version_modelo", f"debe ser {VERSION_MODELO!r}"))
    for nombre in ("documentos", "evidencias", "interpretaciones", "contradicciones"):
        if nombre in doc and not isinstance(doc[nombre], list):
            errores.append((f"$.{nombre}", "debe ser una lista"))
    for i, d in enumerate(doc.get("documentos") if isinstance(doc.get("documentos"), list) else []):
        ruta = f"$.documentos[{i}]"
        if _faltan(d, _OBLIGATORIOS_DOCUMENTO, ruta, errores):
            _enum(d, "tipo", TIPOS_DE_DOCUMENTO, ruta, errores)
            sha = d.get("sha256")
            if "sha256" in d and not (isinstance(sha, str) and len(sha) == 64 and all(c in "0123456789abcdef" for c in sha)):
                errores.append((f"{ruta}.sha256", "debe ser un SHA-256 en hexadecimal minúscula (64 caracteres)"))
            cob = d.get("cobertura")
            if "cobertura" in d and _faltan(cob, _OBLIGATORIOS_COBERTURA, f"{ruta}.cobertura", errores):
                _enum(cob, "unidad", UNIDADES_DE_COBERTURA, f"{ruta}.cobertura", errores)
                for n in ("total", "leidas", "ilegibles"):
                    v = cob.get(n)
                    if n in cob and (not isinstance(v, int) or isinstance(v, bool) or v < 0):
                        errores.append((f"{ruta}.cobertura.{n}", "debe ser un entero no negativo"))
    for i, e in enumerate(doc.get("evidencias") if isinstance(doc.get("evidencias"), list) else []):
        ruta = f"$.evidencias[{i}]"
        if _faltan(e, _OBLIGATORIOS_EVIDENCIA, ruta, errores):
            _enum(e, "tipo", TIPOS_DE_EVIDENCIA, ruta, errores)
            _enum(e, "metodo", METODOS, ruta, errores)
            _enum(e, "estado_de_lectura", ESTADOS_DE_LECTURA, ruta, errores)
            _enum(e, "vigencia", VIGENCIAS, ruta, errores)
            _enum(e, "confianza", CONFIANZAS, ruta, errores)
            if "revision_humana_requerida" in e and not isinstance(e["revision_humana_requerida"], bool):
                errores.append((f"{ruta}.revision_humana_requerida", "debe ser verdadero o falso"))
            for campo in ("texto_literal", "cantidad_literal", "unidad_literal"):
                if e.get(campo) is not None and not isinstance(e[campo], str):
                    errores.append((f"{ruta}.{campo}", "el literal debe ser texto, tal como está escrito en el original"))
            if "localizador" in e and not isinstance(e["localizador"], dict):
                errores.append((f"{ruta}.localizador", "debe ser un objeto"))
    for i, it in enumerate(doc.get("interpretaciones") if isinstance(doc.get("interpretaciones"), list) else []):
        ruta = f"$.interpretaciones[{i}]"
        if _faltan(it, _OBLIGATORIOS_INTERPRETACION, ruta, errores):
            _enum(it, "tipo", TIPOS_DE_INTERPRETACION, ruta, errores)
            _enum(it, "confianza", CONFIANZAS, ruta, errores)
            if not isinstance(it.get("evidencias"), list):
                errores.append((f"{ruta}.evidencias", "debe ser una lista de ids de evidencia"))
    for i, c in enumerate(doc.get("contradicciones") if isinstance(doc.get("contradicciones"), list) else []):
        ruta = f"$.contradicciones[{i}]"
        if _faltan(c, _OBLIGATORIOS_CONTRADICCION, ruta, errores):
            _enum(c, "estado", ESTADOS_DE_CONTRADICCION, ruta, errores)
            if not isinstance(c.get("evidencias"), list):
                errores.append((f"{ruta}.evidencias", "debe ser una lista de ids de evidencia"))
    if _faltan(doc.get("analisis"), _OBLIGATORIOS_ANALISIS, "$.analisis", errores):
        if not isinstance(doc["analisis"].get("declarado_completo"), bool):
            errores.append(("$.analisis.declarado_completo", "debe ser verdadero o falso"))
    return errores
