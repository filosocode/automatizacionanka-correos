"""Catálogo y detección de cantidades con unidad (T-119). Puro: sin lectores, sin red, sin disco.

Regla de fondo: **una cifra con una palabra de medida que no está en el catálogo no se descarta**. Se conserva con su cantidad y su
unidad literales, marcada `unidad_no_reconocida` (o `unidad_ambigua`), con confianza baja y revisión humana. Nunca se convierte,
se normaliza ni se adivina la unidad.

Qué NO es una cantidad con unidad (se excluye para no generar falsos positivos):
- horas y fechas: `10:30 m`, `2026-10-23 ha`, `23/10/2026`, `25 de noviembre`, `10 am`;
- numeración y códigos: la cifra va tras una etiqueta («Nota 7», «Folio 12», «Cuadro 2», «Figura 1», «Lote 5», «No. 14»), pegada a
  letras o guiones (`P-12`, `A12`), o es una numeración de 1–2 cifras al inicio de línea («3 condiciones»);
- cifras seguidas de artículos, preposiciones, conjunciones, meses o días de la semana.
Lo excluido **no desaparece del informe**: cada página lista sus «cifras no registradas».

Límites declarados: una unidad escrita en mayúsculas y fuera del catálogo (`5 ML`) o pegada a la cifra (`5hitos`) no se detecta como
desconocida; una palabra común en minúscula tras una cifra («3 primeros») se registra como unidad desconocida y pide revisión.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

UNIDADES = (
    "kilómetros", "kilometros", "hectáreas", "hectareas", "construcciones", "fotografías", "fotografias", "quebradas",
    "secciones", "semanas", "unidades", "perfiles", "puntos", "metros", "tramos", "predios", "lotes", "meses", "horas",
    "días", "dias", "ejes", "vías", "vias", "vértices", "vertices", "hitos", "mojones", "estaciones", "kg", "ton", "litros",
    "minutos", "segundos", "grados", "años", "anos", "km", "ha", "m2", "m²", "m3", "m³", "cm", "mm", "m", "%",
)
UNIDADES_PEGADAS = ("km", "ha", "m2", "m²", "m3", "m³", "cm", "mm", "m", "kg")   # en OCR una cifra y su unidad pueden venir juntas
AMBIGUAS: dict[str, tuple[str, ...]] = {
    "gl": ("global", "galón"), "ml": ("metro lineal", "mililitro"), "pt": ("punto", "punto tipográfico"),
    "pts": ("puntos", "puntos tipográficos"), "h": ("hora", "altura"),
}
ETIQUETAS = frozenset({
    "nota", "notas", "folio", "fol", "pagina", "página", "pág", "pag", "figura", "fig", "cuadro", "tabla", "item", "ítem", "ref",
    "art", "artículo", "articulo", "numeral", "oficio", "radicado", "contrato", "código", "codigo", "sello", "no", "nº", "nro", "num",
    "número", "numero", "n", "anexo", "capítulo", "capitulo", "paso", "fase", "etapa", "versión", "version", "rev", "revisión",
    "revision", "apéndice", "apendice", "lote", "predio", "matrícula", "matricula", "cédula", "cedula", "nit", "tel", "teléfono",
    "telefono", "cel", "celular", "placa", "plano", "apartado", "punto",
})
_NO_ES_UNIDAD = frozenset({
    "de", "del", "la", "las", "el", "los", "lo", "un", "una", "unos", "unas", "y", "e", "o", "u", "a", "al", "en", "por", "para", "con",
    "sin", "sobre", "entre", "hasta", "desde", "que", "se", "es", "son", "será", "serán", "ser", "no", "ni", "si", "como", "cada",
    "más", "mas", "menos", "veces", "am", "pm", "enero", "febrero", "marzo", "abril", "mayo", "junio", "julio", "agosto",
    "septiembre", "setiembre", "octubre", "noviembre", "diciembre", "lunes", "martes", "miércoles", "miercoles", "jueves",
    "viernes", "sábado", "sabado", "domingo", "ya", "mi", "su", "sus", "tu", "fue", "han", "hay", "ha", "había",
})
_MINUSCULAS = "a-záéíóúñü"
_LETRAS = "A-Za-zÁÉÍÓÚÑáéíóúñü"
_NUMERO = r"(?P<c>\d{1,3}(?:[.,]\d{3})+(?:[.,]\d+)?|\d+(?:[.,]\d+)?)"
_ANTES = r"(?<![\w.,/-])(?<!\d:)"


def _alternativa(unidades: tuple[str, ...]) -> str:
    return "|".join(re.escape(u) for u in sorted(set(unidades), key=len, reverse=True))


_CATALOGO = frozenset(u.casefold() for u in UNIDADES)
_CON_ESPACIO = re.compile(
    _ANTES + _NUMERO + r"\s*(?P<u>(?:" + _alternativa(UNIDADES + tuple(AMBIGUAS)) + r")(?:/[" + _LETRAS + r"²³]+)?)(?![\w²³])", re.IGNORECASE)
_PEGADA = re.compile(_ANTES + _NUMERO + r"(?P<u>" + _alternativa(UNIDADES_PEGADAS) + r")(?![\d²³])", re.IGNORECASE)
_DESCONOCIDA = re.compile(
    _ANTES + _NUMERO + r"[ ]+(?P<u>[" + _MINUSCULAS + r"][" + _MINUSCULAS + r"²³°]+(?:/[" + _LETRAS + r"²³]+)?)(?![\w²³])")
_CUALQUIER_CIFRA = re.compile(r"\d[\d.,]*\d|\d")
_ETIQUETA_PREVIA = re.compile(r"([\wÁ-ú]+)\W{0,3}$")

RECONOCIDA = "reconocida"
NO_RECONOCIDA = "unidad_no_reconocida"
AMBIGUA = "unidad_ambigua"
SIN_UNIDAD = "sin_unidad"


@dataclass(frozen=True)
class Observacion:
    """Una cifra con la palabra de medida que la acompaña, tal como está escrita."""

    cantidad: str
    unidad: str
    estado: str
    compuesta: bool
    ventana: str
    posibles: tuple[str, ...]
    inicio: int


def clasificar(unidad: str) -> tuple[str, bool, tuple[str, ...]]:
    """(estado, es_compuesta, significados_posibles) de una unidad literal. No normaliza nada."""
    partes = [p for p in unidad.split("/") if p]
    estados = []
    posibles: list[str] = []
    for p in partes:
        k = p.casefold()
        if k in _CATALOGO:
            estados.append(RECONOCIDA)
        elif k in AMBIGUAS:
            estados.append(AMBIGUA)
            posibles += AMBIGUAS[k]
        else:
            estados.append(NO_RECONOCIDA)
    estado = NO_RECONOCIDA if NO_RECONOCIDA in estados else AMBIGUA if AMBIGUA in estados else RECONOCIDA
    return estado, len(partes) > 1, tuple(posibles)


def _ventana(texto: str, inicio: int, fin: int) -> str:
    """Un trozo del propio texto alrededor de la cifra (empieza y termina en límite de palabra)."""
    a, b = max(0, inicio - 32), min(len(texto), fin + 24)
    if a > 0:
        previo = texto.rfind(" ", 0, a + 1)
        a = previo + 1 if previo != -1 else 0
    if b < len(texto):
        corte = texto.find(" ", b)
        b = corte if corte != -1 else len(texto)
    return texto[a:b].strip()


def _aplanar(texto: str) -> tuple[str, set[int]]:
    """El texto en una sola línea y las posiciones donde empieza cada línea original."""
    partes: list[str] = []
    inicios: set[int] = set()
    posicion = 0
    for linea in texto.split("\n"):
        limpia = re.sub(r"\s+", " ", linea).strip()
        if not limpia:
            continue
        inicios.add(posicion)
        partes.append(limpia)
        posicion += len(limpia) + 1
    return " ".join(partes), inicios


def _es_numeracion(plano: str, m: re.Match[str], inicios: set[int]) -> bool:
    """¿La cifra es un código o una numeración y no una cantidad? (etiqueta previa o numeración de 1–2 cifras al inicio de línea)."""
    previo = _ETIQUETA_PREVIA.search(plano[:m.start("c")])
    if previo and previo.group(1).casefold() in ETIQUETAS:
        return True
    return m.start("c") in inicios and re.fullmatch(r"\d{1,2}", m.group("c")) is not None


def observar(texto: str, *, pegada: bool = False) -> tuple[list[Observacion], list[str]]:
    """(observaciones en orden de aparición, cifras del texto que no se registraron como cantidad con unidad)."""
    plano, inicios = _aplanar(texto)
    encontradas: dict[int, Observacion] = {}
    for patron in (_CON_ESPACIO, *([_PEGADA] if pegada else []), _DESCONOCIDA):
        for m in patron.finditer(plano):
            if m.start("c") in encontradas:
                continue
            unidad = m.group("u")
            if patron is _DESCONOCIDA and (unidad.casefold() in _NO_ES_UNIDAD or _es_numeracion(plano, m, inicios)):
                continue
            estado, compuesta, posibles = clasificar(unidad)
            encontradas[m.start("c")] = Observacion(
                m.group("c").rstrip(".,"), unidad, estado, compuesta, _ventana(plano, m.start(), m.end()), posibles, m.start("c"))
    ocupadas = set(encontradas)
    no_registradas = [m.group(0).rstrip(".,") for m in _CUALQUIER_CIFRA.finditer(plano) if m.start() not in ocupadas]
    return [encontradas[k] for k in sorted(encontradas)], no_registradas
