"""Validador determinista e independiente de la evidencia documental (T-117).

No usa ningún modelo ni consulta nada externo: relee los originales y comprueba que cada cifra, unidad y cita de la
evidencia tiene respaldo localizable. Un análisis hecho por Work, por una API o por una persona se acepta o se rechaza
con las mismas reglas. No decide si un caso alcanza para cotizar (eso es de una persona) ni certifica interpretación
visual: solo impide que algo sin respaldo se presente como respaldado o como completo.

Cada hallazgo lleva un código estable (ver `CODIGOS`), una severidad («error» invalida la evidencia; «aviso» no) y la
ruta del dato dentro del documento.
"""

from __future__ import annotations

import re
from collections import defaultdict
from dataclasses import dataclass
from typing import Any

from openpyxl.utils import column_index_from_string

from laboratorio.evidencia_documental.esquema import (
    ESTADOS_QUE_BLOQUEAN_LA_COMPLETITUD,
    comprobar_forma,
)
from laboratorio.evidencia_documental.fuentes import (
    HojaDeCalculo,
    PaqueteDeOriginales,
    contiene_cifra,
    literales_de_valor,
    plano,
    sin_espacios,
    unidad_tras_cifra,
)

ERROR = "error"
AVISO = "aviso"

CODIGOS = {
    "forma_invalida": "la estructura no cumple el esquema",
    "id_duplicado": "dos elementos comparten id",
    "documento_no_entregado": "la evidencia cita un archivo que no está en el paquete",
    "huella_distinta": "el SHA-256 declarado no es el del archivo entregado",
    "documento_sin_cubrir": "un archivo del paquete no aparece en la evidencia",
    "documento_desconocido": "la evidencia cita un documento que no está en la lista de documentos",
    "tipo_de_documento_incorrecto": "el tipo declarado no corresponde al contenido",
    "documento_no_legible": "el archivo no se abre y la cobertura lo declara ilegible (aviso, no error)",
    "cobertura_inconsistente": "la cobertura no cuadra con el documento",
    "localizador_ausente": "falta el localizador",
    "localizador_mixto": "el localizador mezcla página con hoja/celda",
    "pagina_inexistente": "la página no existe",
    "hoja_inexistente": "la hoja no existe",
    "celda_invalida": "celda o rango mal escrito",
    "rango_fuera_de_la_hoja": "el rango se sale de la hoja",
    "hoja_no_vigente": "se cita como vigente una hoja oculta o histórica",
    "vigencia_sin_revision": "vigencia sin confirmar sin pedir revisión humana",
    "localizador_fino_no_verificable": "tabla, fila o región de un PDF no se puede comprobar automáticamente",
    "metodo_incompatible": "el método declarado no es posible para esa fuente",
    "texto_no_encontrado": "el texto literal no está en la fuente citada",
    "cantidad_ausente": "una evidencia de cantidad no trae cantidad literal",
    "cantidad_no_respaldada": "la cantidad literal no está en la fuente citada",
    "unidad_no_respaldada": "la unidad literal no está en la fuente citada",
    "unidad_alterada": "la cifra está en la fuente pero con otra unidad",
    "normalizado_sin_regla": "valor normalizado sin regla declarada",
    "normalizado_inconsistente": "el valor normalizado no se deduce del literal con la regla declarada",
    "normalizado_ambiguo": "la cifra tiene dos lecturas posibles y no se declaró la ambigüedad",
    "ocr_certifica_visual": "se pretende que el OCR o la visión certifique interpretación visual",
    "confianza_excesiva": "confianza alta con un método que no la permite",
    "vision_sin_revision": "lectura visual sin revisión humana requerida",
    "no_verificable_mecanicamente": "no hay texto contra el que contrastar este valor",
    "estado_con_cantidad": "un dato ilegible o no encontrado trae cantidad o texto",
    "estado_sin_motivo": "falta el motivo del estado",
    "estado_inconsistente": "el estado no es coherente con el resto de la evidencia",
    "parcial_sin_faltante": "lectura parcial sin declarar qué falta",
    "pendiente_visual_sin_revision": "pendiente de revisión visual sin pedir revisión humana",
    "elemento_visual_no_pendiente": "un elemento visual no puede darse por leído sin una persona",
    "contradictorio_sin_par": "evidencia marcada contradictoria que ninguna contradicción cita",
    "contradiccion_invalida": "la contradicción no cita al menos dos evidencias existentes y distintas",
    "contradiccion_sin_diferencia": "las evidencias de la contradicción dicen lo mismo",
    "contradiccion_resuelta_sin_persona": "contradicción resuelta sin una persona que la resuelva",
    "contradiccion_no_declarada": "dos cantidades vigentes distintas del mismo concepto sin contradicción declarada",
    "interpretacion_cita_inexistente": "la interpretación cita una evidencia que no existe",
    "interpretacion_sin_evidencia": "la interpretación no cita ninguna evidencia",
    "interpretacion_cifra_nueva": "la interpretación contiene una cifra que ninguna evidencia citada respalda",
    "completitud_falsa": "se declara lectura completa y la evidencia no lo permite",
    "incompleto_sin_motivo": "se declara incompleto sin decir por qué",
}


@dataclass(frozen=True)
class Hallazgo:
    """Un resultado del validador: código estable, severidad, ruta dentro del documento y explicación."""

    codigo: str
    severidad: str
    ruta: str
    mensaje: str

    def __str__(self) -> str:
        return f"[{self.severidad}] {self.codigo} {self.ruta}: {self.mensaje}"


class _Acumulador:
    def __init__(self) -> None:
        self.items: list[Hallazgo] = []

    def error(self, codigo: str, ruta: str, mensaje: str) -> None:
        self.items.append(Hallazgo(codigo, ERROR, ruta, mensaje))

    def aviso(self, codigo: str, ruta: str, mensaje: str) -> None:
        self.items.append(Hallazgo(codigo, AVISO, ruta, mensaje))


_METODOS_POR_TIPO = {"pdf_digital": ("texto_nativo", "vision", "humano"), "pdf_escaneado": ("ocr", "vision", "humano"),
                     "xlsx": ("celda", "humano")}
_REGLAS_DE_NORMALIZACION = ("identidad", "separador_decimal_coma", "separador_decimal_punto", "separador_de_miles_punto",
                            "conversion_declarada")
_AMBIGUA = re.compile(r"^\d{1,3}[.,]\d{3}$")
_NUMERO = re.compile(r"\d+(?:[.,]\d+)*")
_REFERENCIA_NUMERICA = re.compile(
    r"(?:tabla|fila|[ií]tem|item|p\.|p[aá]g\.?|p[aá]gina|hoja|nota|figura|cuadro|anexo|§|\b[a-f]\))\s*\d+", re.IGNORECASE)


def _numero(literal: str, regla: str) -> float | None:
    try:
        if regla == "separador_decimal_coma":
            return float(literal.replace(".", "").replace(",", "."))
        if regla == "separador_de_miles_punto":
            return float(literal.replace(".", "").replace(",", ""))
        return float(literal.replace(",", "")) if regla == "separador_decimal_punto" else float(literal.replace(",", "."))
    except ValueError:
        return None


def _revisar_normalizado(e: dict, ruta: str, salida: _Acumulador) -> None:
    n = e.get("valor_normalizado")
    if n is None:
        return
    regla = n.get("regla") if isinstance(n, dict) else None
    if regla not in _REGLAS_DE_NORMALIZACION:
        salida.error("normalizado_sin_regla", f"{ruta}.valor_normalizado", "debe declarar una regla de: " + ", ".join(_REGLAS_DE_NORMALIZACION))
        return
    literal = e.get("cantidad_literal") or ""
    esperado = _numero(literal, regla)
    factor = n.get("factor", 1.0) if regla == "conversion_declarada" else 1.0
    valor = n.get("valor")
    if esperado is None or not isinstance(valor, (int, float)) or abs(esperado * factor - valor) > 1e-9:
        salida.error("normalizado_inconsistente", f"{ruta}.valor_normalizado",
                     f"{literal!r} con la regla {regla} no da {valor!r}")
    if regla == "conversion_declarada":
        if not isinstance(n.get("factor"), (int, float)) or n.get("unidad") in (None, e.get("unidad_literal")):
            salida.error("normalizado_inconsistente", f"{ruta}.valor_normalizado",
                         "una conversión declara su factor y una unidad distinta de la literal")
    elif n.get("unidad") not in (None, e.get("unidad_literal")):
        salida.error("normalizado_inconsistente", f"{ruta}.valor_normalizado",
                     "sin conversión declarada la unidad normalizada es la literal")
    if e.get("metodo") != "celda" and _AMBIGUA.match(literal) and not e.get("ambiguedad"):
        salida.error("normalizado_ambiguo", f"{ruta}.valor_normalizado",
                     f"{literal!r} admite punto o coma como separador decimal o de miles; declara `ambiguedad` y pide revisión")


def _cifras(texto: str) -> set[str]:
    return set(_NUMERO.findall(_REFERENCIA_NUMERICA.sub(" ", texto or "")))


def _revisar_documentos(doc: dict, paquete: PaqueteDeOriginales, salida: _Acumulador) -> dict[str, dict]:
    por_archivo: dict[str, dict] = {}
    for i, d in enumerate(doc["documentos"]):
        ruta = f"$.documentos[{i}]"
        archivo = d["archivo"]
        if archivo in por_archivo:
            salida.error("id_duplicado", ruta, f"el documento {archivo} aparece dos veces")
        por_archivo[archivo] = d
        if not paquete.tiene(archivo):
            salida.error("documento_no_entregado", ruta, f"{archivo} no está en el paquete de originales")
            continue
        if paquete.sha256(archivo) != d["sha256"]:
            salida.error("huella_distinta", f"{ruta}.sha256", f"declarado {d['sha256'][:12]}…, real {paquete.sha256(archivo)[:12]}…")
        cob = d["cobertura"]
        _revisar_cobertura(d, cob, ruta, paquete, salida)
    return por_archivo


def _revisar_cobertura(d: dict, cob: dict, ruta: str, paquete: PaqueteDeOriginales, salida: _Acumulador) -> None:
    archivo, tipo = d["archivo"], d["tipo"]
    try:
        if tipo == "xlsx":
            real, unidad = len(paquete.libro(archivo)), "hojas"
        else:
            real, unidad = paquete.paginas(archivo), "paginas"
    except Exception as exc:   # archivo ilegible o de otro tipo: se informa, no se interrumpe  # noqa: BLE001
        if cob["ilegibles"] == cob["total"]:
            salida.aviso("documento_no_legible", ruta, f"{archivo} no se puede abrir como {tipo} ({type(exc).__name__}) y la cobertura lo declara ilegible")
        else:
            salida.error("tipo_de_documento_incorrecto", ruta, f"{archivo} no se puede abrir como {tipo}: {type(exc).__name__}")
        return
    if cob["unidad"] != unidad:
        salida.error("cobertura_inconsistente", f"{ruta}.cobertura.unidad", f"un {tipo} se cubre por {unidad}")
    if cob["total"] != real:
        salida.error("cobertura_inconsistente", f"{ruta}.cobertura.total", f"declara {cob['total']} y el archivo tiene {real}")
    if cob["leidas"] + cob["ilegibles"] > cob["total"]:
        salida.error("cobertura_inconsistente", f"{ruta}.cobertura", "leídas + ilegibles supera el total")
    detalle = cob["detalle"] if isinstance(cob["detalle"], list) else []
    leidas = sum(1 for x in detalle if isinstance(x, dict) and x.get("estado") == "leido")
    ilegibles = sum(1 for x in detalle if isinstance(x, dict) and x.get("estado") == "ilegible")
    if detalle and (leidas != cob["leidas"] or ilegibles != cob["ilegibles"]):
        salida.error("cobertura_inconsistente", f"{ruta}.cobertura.detalle",
                     f"el detalle suma {leidas} leídas y {ilegibles} ilegibles; el resumen dice {cob['leidas']} y {cob['ilegibles']}")
    if tipo.startswith("pdf"):
        paginas_con_texto = [p for p in range(1, real + 1) if paquete.texto_nativo(archivo, p).strip()]
        if tipo == "pdf_digital" and len(paginas_con_texto) < real:
            salida.error("tipo_de_documento_incorrecto", ruta, "se declara PDF digital pero hay páginas sin capa de texto")
        if tipo == "pdf_escaneado" and paginas_con_texto:
            salida.aviso("tipo_de_documento_incorrecto", ruta, "se declara PDF escaneado pero tiene capa de texto en algunas páginas")
        for x in detalle:
            if not isinstance(x, dict):
                continue
            m = re.fullmatch(r"p(\d+)", str(x.get("ref", "")))
            if m and x.get("metodo") == "texto_nativo" and int(m.group(1)) not in paginas_con_texto:
                salida.error("metodo_incompatible", f"{ruta}.cobertura.detalle", f"la página {m.group(1)} no tiene capa de texto")


def _fuente_pdf(e: dict, ruta: str, loc: dict, d: dict, paquete: PaqueteDeOriginales, salida: _Acumulador) -> tuple[str, bool] | None:
    """(texto de la página, pegada) para contrastar, o None si no se puede contrastar. Registra los errores de localizador."""
    archivo = e["documento"]
    if "hoja" in loc or "celda" in loc or "rango" in loc:
        salida.error("localizador_mixto", f"{ruta}.localizador", "un PDF se cita por página, no por hoja ni celda")
        return None
    pagina = loc.get("pagina")
    total = d["cobertura"]["total"]
    if not isinstance(pagina, int) or isinstance(pagina, bool):
        salida.error("localizador_ausente", f"{ruta}.localizador", "falta la página (entero desde 1)")
        return None
    if not 1 <= pagina <= total:
        salida.error("pagina_inexistente", f"{ruta}.localizador.pagina", f"la página {pagina} no existe; el documento tiene {total}")
        return None
    if any(k in loc for k in ("tabla", "fila", "region")):
        salida.aviso("localizador_fino_no_verificable", f"{ruta}.localizador",
                     "solo se comprueba la página; tabla, fila o región las confirma una persona")
    metodo = e["metodo"]
    if metodo == "celda":
        salida.error("metodo_incompatible", f"{ruta}.metodo", "«celda» solo existe en hojas de cálculo")
        return None
    if metodo == "texto_nativo":
        texto = paquete.texto_nativo(archivo, pagina)
        if not texto.strip():
            salida.error("metodo_incompatible", f"{ruta}.metodo", f"la página {pagina} no tiene capa de texto: solo cabe OCR o vista humana")
            return None
        return texto, False
    if metodo == "ocr":
        texto_ocr = paquete.texto_ocr(archivo, pagina)
        if texto_ocr is None:
            salida.error("metodo_incompatible", f"{ruta}.metodo", f"no hay texto OCR de la página {pagina} en el paquete")
            return None
        return texto_ocr, True
    return None


def _contrastar_texto(e: dict, ruta: str, texto: str, pegada: bool, salida: _Acumulador) -> None:
    literal, cifra, unidad = e.get("texto_literal"), e.get("cantidad_literal"), e.get("unidad_literal")
    if literal:
        encontrado = sin_espacios(literal) in sin_espacios(texto) if pegada else plano(literal) in plano(texto)
        if not encontrado:
            salida.error("texto_no_encontrado", f"{ruta}.texto_literal", f"«{literal[:60]}» no está en la fuente citada")
    if not cifra:
        return
    if not contiene_cifra(plano(texto), plano(cifra)):
        salida.error("cantidad_no_respaldada", f"{ruta}.cantidad_literal", f"«{cifra}» no aparece completa en la fuente citada")
        return
    if unidad:
        veredicto = unidad_tras_cifra(texto, cifra, unidad, pegada=pegada)
        if veredicto == "distinta":
            salida.error("unidad_alterada", f"{ruta}.unidad_literal", f"«{cifra}» está en la fuente pero no seguida de «{unidad}»")


def _fuente_hoja(e: dict, ruta: str, loc: dict, d: dict, paquete: PaqueteDeOriginales, salida: _Acumulador
                 ) -> tuple[HojaDeCalculo, list[str]] | None:
    if "pagina" in loc:
        salida.error("localizador_mixto", f"{ruta}.localizador", "una hoja de cálculo se cita por hoja y celda, no por página")
        return None
    libro = paquete.libro(e["documento"])
    nombre = loc.get("hoja")
    if not nombre:
        salida.error("localizador_ausente", f"{ruta}.localizador", "falta la hoja")
        return None
    if nombre not in libro:
        salida.error("hoja_inexistente", f"{ruta}.localizador.hoja", f"no hay una hoja «{nombre}»; hay: {', '.join(libro)}")
        return None
    hoja = libro[nombre]
    if hoja.estado != "visible" and e["vigencia"] == "vigente":
        salida.error("hoja_no_vigente", f"{ruta}.localizador.hoja", f"«{nombre}» está {hoja.estado}: sus datos no son vigentes salvo que una persona lo confirme")
    referencia = loc.get("celda") or loc.get("rango")
    if not referencia:
        salida.error("localizador_ausente", f"{ruta}.localizador", "falta la celda o el rango")
        return None
    try:
        return hoja, paquete.celdas_del_rango(hoja, referencia, con_comentarios=True)
    except ValueError as exc:
        codigo = "rango_fuera_de_la_hoja" if "se sale" in str(exc) else "celda_invalida"
        salida.error(codigo, f"{ruta}.localizador", str(exc))
        return None


def _contrastar_derivacion(e: dict, ruta: str, derivacion: Any, celdas: list[str], salida: _Acumulador) -> None:
    """Una cantidad que no está escrita en ninguna celda sino que se cuenta (p. ej. los lotes listados) se recalcula."""
    if not isinstance(derivacion, dict) or derivacion.get("operacion") != "contar_celdas" or not derivacion.get("unidad_inferida"):
        salida.error("normalizado_inconsistente", f"{ruta}.derivacion", "admite {operacion: contar_celdas, unidad_inferida: texto}")
        return
    if e.get("cantidad_literal") != str(len(celdas)):
        salida.error("cantidad_no_respaldada", f"{ruta}.cantidad_literal", f"el rango tiene {len(celdas)} celdas con dato, no {e.get('cantidad_literal')!r}")
    if e["confianza"] == "alta":
        salida.error("confianza_excesiva", f"{ruta}.confianza", "un conteo derivado no pasa de «media»: contar no prueba que cada fila sea un lote")


def _contrastar_celdas(e: dict, ruta: str, hoja: HojaDeCalculo, celdas: list[str], salida: _Acumulador) -> None:
    if e["metodo"] != "celda":
        if e["metodo"] != "humano":
            salida.error("metodo_incompatible", f"{ruta}.metodo", "en una hoja de cálculo el método es «celda» (o «humano»)")
        return
    derivacion = e.get("derivacion")
    if derivacion is not None:
        _contrastar_derivacion(e, ruta, derivacion, [c for c in celdas if c in hoja.valores], salida)
        return
    candidatos: list[str] = []
    textos: list[str] = []
    for c in celdas:
        valor = hoja.valores.get(c)
        candidatos += literales_de_valor(valor)
        if valor is not None:
            textos.append(plano(str(valor)))
        if c in hoja.comentarios:
            textos.append(plano(hoja.comentarios[c]))   # el comentario de una celda es parte de la celda
    literal, cifra, unidad = e.get("texto_literal"), e.get("cantidad_literal"), e.get("unidad_literal")
    if literal and not any(plano(literal) in t or t in plano(literal) for t in textos if t):
        salida.error("texto_no_encontrado", f"{ruta}.texto_literal", f"«{literal[:60]}» no está en {hoja.nombre}!{e['localizador'].get('celda') or e['localizador'].get('rango')}")
    if not cifra:
        return
    if cifra not in candidatos:
        salida.error("cantidad_no_respaldada", f"{ruta}.cantidad_literal", f"«{cifra}» no es el valor de ninguna celda del rango")
        return
    if unidad:
        unidades = set(textos)
        for c in celdas:
            m = re.search(r"\(([^)]+)\)", hoja.encabezados.get(_columna(c), ""))
            if m:
                unidades.add(plano(m.group(1)))
        if plano(unidad) not in unidades:
            salida.error("unidad_no_respaldada", f"{ruta}.unidad_literal", f"«{unidad}» no está en el rango ni en el encabezado de su columna")


def _columna(coord: str) -> int:
    letras = re.match(r"[A-Za-z]+", coord)
    return column_index_from_string(letras.group(0)) if letras else 0


def _revisar_evidencia(i: int, e: dict, por_archivo: dict[str, dict], paquete: PaqueteDeOriginales, salida: _Acumulador) -> None:  # noqa: C901  (una regla tras otra: partirla escondería el orden)
    ruta = f"$.evidencias[{i}]"
    estado, metodo, tipo_ev = e["estado_de_lectura"], e["metodo"], e["tipo"]
    d = por_archivo.get(e["documento"])
    if d is None:
        salida.error("documento_desconocido", f"{ruta}.documento", f"{e['documento']} no está en la lista de documentos")
        return
    if not paquete.tiene(e["documento"]):
        return   # ya se informó como documento_no_entregado
    tipo_doc = d["tipo"]
    if e.get("certifica_interpretacion_visual") is True:
        salida.error("ocr_certifica_visual", f"{ruta}.certifica_interpretacion_visual", "ni el OCR ni la visión certifican una interpretación visual")
    if e["vigencia"] == "sin_confirmar" and not e["revision_humana_requerida"]:
        salida.error("vigencia_sin_revision", f"{ruta}.revision_humana_requerida", "lo que no se sabe si es vigente lo confirma una persona")
    if metodo in ("ocr", "vision") and e["confianza"] == "alta":
        salida.error("confianza_excesiva", f"{ruta}.confianza", f"con {metodo} la confianza máxima es «media»")
    if metodo == "vision" and not e["revision_humana_requerida"]:
        salida.error("vision_sin_revision", f"{ruta}.revision_humana_requerida", "una lectura visual exige revisión humana")
    # estados que no traen valor
    if estado in ("ilegible", "no_encontrado"):
        if e.get("cantidad_literal") or e.get("texto_literal") or e.get("unidad_literal"):
            salida.error("estado_con_cantidad", ruta, f"un dato «{estado}» no puede traer cantidad, unidad ni texto")
        if not e.get("motivo"):
            salida.error("estado_sin_motivo", ruta, f"explica por qué «{estado}» (qué se buscó o por qué no se pudo leer)")
        if estado == "ilegible":
            if not e["revision_humana_requerida"]:
                salida.error("estado_inconsistente", f"{ruta}.revision_humana_requerida", "lo ilegible exige revisión humana")
            if not isinstance(e.get("localizador"), dict) or not e["localizador"]:
                salida.error("localizador_ausente", f"{ruta}.localizador", "lo ilegible dice dónde está")
        return
    if metodo not in _METODOS_POR_TIPO.get(tipo_doc, ()) and metodo != "humano":
        salida.error("metodo_incompatible", f"{ruta}.metodo", f"un {tipo_doc} no se lee con «{metodo}»")
    if estado == "parcialmente_leido" and not e.get("faltante"):
        salida.error("parcial_sin_faltante", ruta, "declara qué parte no se pudo leer")
    if estado == "pendiente_revision_visual":
        if not e["revision_humana_requerida"]:
            salida.error("pendiente_visual_sin_revision", f"{ruta}.revision_humana_requerida", "pendiente de revisión visual implica revisión humana")
        if e["confianza"] == "alta":
            salida.error("confianza_excesiva", f"{ruta}.confianza", "lo pendiente de revisión visual no tiene confianza alta")
    if tipo_ev == "elemento_visual":
        if estado != "pendiente_revision_visual" and not (metodo == "humano" and e.get("revisor")):
            salida.error("elemento_visual_no_pendiente", ruta, "tachaduras, anotaciones y figuras quedan pendientes hasta que una persona (con nombre) las revise")
        return
    if tipo_ev == "cantidad" and not e.get("cantidad_literal"):
        salida.error("cantidad_ausente", f"{ruta}.cantidad_literal", "una evidencia de cantidad lleva la cantidad tal como está escrita")
    _revisar_normalizado(e, ruta, salida)
    loc = e.get("localizador")
    if not isinstance(loc, dict) or not loc:
        salida.error("localizador_ausente", f"{ruta}.localizador", "toda evidencia dice dónde está")
        return
    if tipo_doc == "xlsx":
        fuente = _fuente_hoja(e, ruta, loc, d, paquete, salida)
        if fuente:
            _contrastar_celdas(e, ruta, fuente[0], fuente[1], salida)
        return
    fuente_pdf = _fuente_pdf(e, ruta, loc, d, paquete, salida)
    if fuente_pdf:
        _contrastar_texto(e, ruta, fuente_pdf[0], fuente_pdf[1], salida)
    elif metodo in ("vision", "humano") and e.get("cantidad_literal"):
        salida.aviso("no_verificable_mecanicamente", ruta, "sin texto del original: este valor depende de la revisión humana")


def _revisar_contradicciones(doc: dict, ids: dict[str, dict], salida: _Acumulador) -> None:
    citadas: set[str] = set()
    for i, c in enumerate(doc["contradicciones"]):
        ruta = f"$.contradicciones[{i}]"
        miembros = c["evidencias"]
        if len(set(miembros)) < 2 or any(m not in ids for m in miembros):
            salida.error("contradiccion_invalida", ruta, "cita al menos dos evidencias existentes y distintas")
            continue
        citadas.update(miembros)
        pares = {(ids[m].get("cantidad_literal"), ids[m].get("unidad_literal")) for m in miembros}
        if len(pares) < 2:
            salida.error("contradiccion_sin_diferencia", ruta, "las evidencias citadas dicen lo mismo")
        if c["estado"] == "resuelta_por_persona":
            if not c.get("revisor"):
                salida.error("contradiccion_resuelta_sin_persona", ruta, "una contradicción solo la resuelve una persona con nombre")
        else:
            for m in miembros:
                if ids[m]["estado_de_lectura"] not in ("contradictorio", "pendiente_revision_visual"):
                    salida.error("estado_inconsistente", f"{ruta}.evidencias", f"{m} es parte de una contradicción abierta y su estado es «{ids[m]['estado_de_lectura']}»")
    for i, e in enumerate(doc["evidencias"]):
        if e["estado_de_lectura"] == "contradictorio" and e["id"] not in citadas:
            salida.error("contradictorio_sin_par", f"$.evidencias[{i}]", "ninguna contradicción cita esta evidencia")
    por_concepto: dict[str, list[dict]] = defaultdict(list)
    for e in doc["evidencias"]:
        if (e["tipo"] == "cantidad" and e["vigencia"] == "vigente" and e.get("concepto")
                and e["estado_de_lectura"] in ("leido", "contradictorio", "parcialmente_leido")):
            por_concepto[plano(e["concepto"])].append(e)
    for concepto, lista in por_concepto.items():
        pares = {(x.get("cantidad_literal"), plano(x.get("unidad_literal") or "")) for x in lista}
        if len(pares) < 2:
            continue
        ids_concepto = {x["id"] for x in lista}
        if not any(len(ids_concepto & set(c["evidencias"])) >= 2 for c in doc["contradicciones"]):
            salida.error("contradiccion_no_declarada", "$.contradicciones",
                         f"«{concepto}» tiene {len(pares)} cantidades vigentes distintas ({', '.join(sorted(ids_concepto))}) y ninguna contradicción las junta")


def _revisar_interpretaciones(doc: dict, ids: dict[str, dict], salida: _Acumulador) -> None:
    for i, it in enumerate(doc["interpretaciones"]):
        ruta = f"$.interpretaciones[{i}]"
        citadas = it["evidencias"]
        faltan = [c for c in citadas if c not in ids]
        if faltan:
            salida.error("interpretacion_cita_inexistente", ruta, "cita evidencias que no existen: " + ", ".join(faltan))
        if not citadas and it["tipo"] not in ("faltante", "pregunta"):
            salida.error("interpretacion_sin_evidencia", ruta, "una interpretación cita la evidencia que la sostiene")
        respaldadas: set[str] = set()
        for c in citadas:
            e = ids.get(c)
            if not e:
                continue
            respaldadas |= _cifras(" ".join(str(e.get(k) or "") for k in ("texto_literal", "cantidad_literal")))
            n = e.get("valor_normalizado")
            if isinstance(n, dict) and "valor" in n:
                respaldadas |= {str(n["valor"]), str(n["valor"]).replace(".", ",")}
        nuevas = sorted(_cifras(it["texto"]) - respaldadas)
        if nuevas and citadas:
            salida.error("interpretacion_cifra_nueva", ruta, "cifras sin respaldo en las evidencias citadas: " + ", ".join(nuevas))


def _revisar_completitud(doc: dict, paquete: PaqueteDeOriginales, salida: _Acumulador) -> None:
    analisis = doc["analisis"]
    if not analisis["declarado_completo"]:
        if not analisis["motivos_de_incompletitud"]:
            salida.error("incompleto_sin_motivo", "$.analisis.motivos_de_incompletitud", "declara por qué la lectura es incompleta")
        return
    if analisis["motivos_de_incompletitud"]:
        salida.error("completitud_falsa", "$.analisis", "se declara completo y a la vez lista motivos de incompletitud")
    cubiertos = {d["archivo"] for d in doc["documentos"]}
    for archivo in paquete.archivos:
        if archivo not in cubiertos:
            salida.error("documento_sin_cubrir", "$.documentos", f"{archivo} se recibió y la evidencia no lo cubre")
    for i, d in enumerate(doc["documentos"]):
        cob = d["cobertura"]
        if cob["leidas"] != cob["total"] or cob["ilegibles"]:
            salida.error("completitud_falsa", f"$.documentos[{i}].cobertura",
                         f"{d['archivo']}: {cob['leidas']} de {cob['total']} {cob['unidad']} leídas y {cob['ilegibles']} ilegibles")
    for i, e in enumerate(doc["evidencias"]):
        if e["estado_de_lectura"] in ESTADOS_QUE_BLOQUEAN_LA_COMPLETITUD:
            salida.error("completitud_falsa", f"$.evidencias[{i}]", f"{e['id']} está «{e['estado_de_lectura']}»: la lectura no está completa")
        elif e["vigencia"] == "sin_confirmar":
            salida.error("completitud_falsa", f"$.evidencias[{i}]", f"{e['id']} tiene vigencia sin confirmar: una persona decide antes de dar la lectura por completa")


def validar(doc: Any, paquete: PaqueteDeOriginales) -> list[Hallazgo]:
    """Todos los hallazgos de una evidencia contra sus originales. Sin errores = evidencia válida (no = caso listo)."""
    salida = _Acumulador()
    errores_de_forma = comprobar_forma(doc)
    if errores_de_forma:
        for ruta, mensaje in errores_de_forma:
            salida.error("forma_invalida", ruta, mensaje)
        return salida.items
    por_archivo = _revisar_documentos(doc, paquete, salida)
    ids: dict[str, dict] = {}
    for coleccion, nombre in ((doc["evidencias"], "evidencias"), (doc["interpretaciones"], "interpretaciones"),
                              (doc["contradicciones"], "contradicciones")):
        vistos: set[str] = set()
        for i, x in enumerate(coleccion):
            if x["id"] in vistos:
                salida.error("id_duplicado", f"$.{nombre}[{i}]", f"el id {x['id']} se repite")
            vistos.add(x["id"])
            if nombre == "evidencias":
                ids.setdefault(x["id"], x)
    for i, e in enumerate(doc["evidencias"]):
        _revisar_evidencia(i, e, por_archivo, paquete, salida)
    _revisar_contradicciones(doc, ids, salida)
    _revisar_interpretaciones(doc, ids, salida)
    _revisar_completitud(doc, paquete, salida)
    return salida.items


def errores(hallazgos: list[Hallazgo]) -> list[Hallazgo]:
    """Solo los hallazgos que invalidan la evidencia."""
    return [h for h in hallazgos if h.severidad == ERROR]


def es_valida(hallazgos: list[Hallazgo]) -> bool:
    """Sin errores (los avisos no invalidan)."""
    return not errores(hallazgos)


def codigos(hallazgos: list[Hallazgo], severidad: str = ERROR) -> set[str]:
    """Los códigos presentes con esa severidad."""
    return {h.codigo for h in hallazgos if h.severidad == severidad}
