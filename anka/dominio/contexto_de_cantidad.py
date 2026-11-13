"""Verificación contextual de las cantidades y unidades de la tabla de Work (T-66, modo sombra).

Para cada actividad con una cantidad numérica busca primero en la ubicación que Work cita (página, hoja, celda,
fila de tabla, diapositiva) y comprueba el valor, la unidad y que el dato pertenezca a esa actividad o a su concepto.
Solo calcula observaciones: nunca rechaza, nunca cambia el análisis y nunca convierte unidades. Una cifra ausente no
demuestra una contradicción, y una lectura parcial o por OCR nunca produce una contradicción.
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from typing import Any

VERIFICADA = "VERIFICADA"
VERIFICADA_SIN_UNIDAD = "VERIFICADA_SIN_UNIDAD"
ASOCIACION_DUDOSA = "ASOCIACION_DUDOSA"
NO_ENCONTRADA = "NO_ENCONTRADA"
NO_COMPROBABLE = "NO_COMPROBABLE"
NO_COMPROBABLE_OCR = "NO_COMPROBABLE_OCR"
CONTRADICTORIA_UNIDAD = "CONTRADICTORIA_UNIDAD"
CONTRADICTORIA_VALOR = "CONTRADICTORIA_VALOR"

SEVERIDAD = {
    VERIFICADA: "info", VERIFICADA_SIN_UNIDAD: "info", NO_COMPROBABLE: "info", NO_COMPROBABLE_OCR: "info",
    ASOCIACION_DUDOSA: "aviso", NO_ENCONTRADA: "aviso",
    CONTRADICTORIA_UNIDAD: "critico_futuro", CONTRADICTORIA_VALOR: "critico_futuro",
}
MAX_ACTIVIDADES = 50
MAX_FRAGMENTO = 160

_SINONIMOS: dict[str, tuple[str, ...]] = {
    "area_ha": ("ha", "hectarea", "hectareas"),
    "area_m2": ("m2", "m^2", "metro cuadrado", "metros cuadrados", "mt2", "mts2"),
    "area_km2": ("km2", "km^2", "kilometro cuadrado", "kilometros cuadrados"),
    "longitud_km": ("km", "kilometro", "kilometros"),
    "longitud_m": ("m", "mt", "mts", "metro", "metros", "ml"),
    "dias": ("dia", "dias"),
    "meses": ("mes", "meses"),
}
UNIDADES = {palabra: clase for clase, palabras in _SINONIMOS.items() for palabra in palabras}
_NO_ES_UNIDAD = frozenset(
    "de del en y a e o u con sin para por la el los las un una que se al mas como desde hasta entre aprox "
    "aproximadamente aproximada aproximado cerca unos unas casi son es fue".split())
_MODIFICADORES = ("aproximadamente", "aproximada", "aproximado", "aprox", "approx", "cerca de", "unos", "unas", "casi")
_STOP = frozenset("de del la el los las un una unos unas y o u e en con sin para por que se al como son es fue "
                  "entre sobre desde hasta este esta estos estas ese esa mas muy".split())
_GENERICAS = frozenset(r[:5] for r in (
    "levantamiento topografico topografia servicio servicios trabajo trabajos proyecto area actividad plano planos "
    "entregable entregables estudio informe cotizacion solicitud aprox total dato datos realizar").split())
_MESES = "enero|febrero|marzo|abril|mayo|junio|julio|agosto|septiembre|setiembre|octubre|noviembre|diciembre"
_FECHA = re.compile(
    rf"\b\d{{1,2}}[/-]\d{{1,2}}[/-]\d{{2,4}}\b|\b\d{{4}}-\d{{2}}-\d{{2}}\b|"
    rf"\b\d{{1,2}}\s+de\s+(?:{_MESES})(?:\s+de(?:l)?\s+\d{{4}})?|\b(?:{_MESES})\s+(?:de(?:l)?\s+)?\d{{4}}\b")
_NUM = re.compile(r"(?<![a-z0-9_])\d+(?:[.,]\d+)*(?!\d)")
_PREFIJO_DE_INDICE = re.compile(
    r"(?:\bp|\bpp|\bpag|\bpagina|\bpaginas|\bhoja|\btabla|\bfila|\bcolumna|\bdiapositiva|\bitem|\bitems|\bnumeral|"
    r"\bnumerales|\bpunto|\bnumero|\bnro|\bno|\bn|\bid|\bcodigo|\bcod|\bnit|\btel|\btelefono|\bcel|\bcelular|"
    r"\bparrafo|\bseccion|\bcapitulo|\bart|\barticulo|\bversion|\bv|\bviñeta)\.?\s*[°º#:-]?\s*$")
_PAGINA = re.compile(r"\b(?:p|pp|pag|pagina|paginas)\.?\s*(\d+)(?:\s*[-–]\s*(\d+))?")
_HOJA = re.compile(r"\bhoja\s+[«\"']?([^»\"',;:]+?)[»\"']?(?=\s*(?:,|;|:|$|\bcelda\b|\bfila\b|\bcolumna\b|\brango\b))")
_CELDA = re.compile(r"\b(?:celda|celdas|rango)\s+([a-z]{1,3})(\d{1,6})(?:\s*[:-]\s*([a-z]{1,3})(\d{1,6}))?")
_DIAPOSITIVA = re.compile(r"\bdiapositiva\s+(\d+)")
_TABLA = re.compile(r"\btabla\s+(\d+)(?:\s*,?\s*fila\s+(\d+))?")
_ARCHIVO = re.compile(r"[\w\-. ()]+?\.(?:pdf|docx?|xlsx?|xlsm|csv|tsv|txt|kml|kmz|gpx|geojson|zip|dwg|dxf|odt|ods|"
                      r"png|jpe?g|tiff?)", re.IGNORECASE)
_CELDA_DE_HOJA = re.compile(r"^([A-Z]{1,3})(\d{1,6})=")
_FILA_DE_WORD = re.compile(r"^Tabla (\d+), fila (\d+): (.*)$")
_CONFUNDIBLE = re.compile(r"(?<![A-Za-z])[0-9OoIlSBZ][0-9OoIlSBZ.,]*[0-9OoIlSBZ](?![A-Za-z])")
_CONFUSIONES = str.maketrans("OoIlSBZ", "0011582")


def normalizar(texto: str) -> str:
    """Minúsculas y sin tildes, carácter a carácter: la longitud se conserva y los índices valen para el original."""
    return "".join((unicodedata.normalize("NFKD", c) or c)[0].lower() for c in texto)


def valores(token: str) -> tuple[frozenset[Decimal], bool]:
    """Los valores que un número puede significar y si es ambiguo (`1.250` es 1250 o 1,25). Nunca separa cifras.

    Un solo separador seguido de tres cifras tras 1 a 3 cifras es ambiguo; con otra cantidad de cifras es decimal.
    Varios separadores iguales son miles; con ambos, el último es el decimal. Lo demás no es un número.
    """
    puntos, comas = token.count("."), token.count(",")
    try:
        if not puntos and not comas:
            return frozenset({Decimal(token)}), False
        if puntos and comas:
            decimal = "." if token.rfind(".") > token.rfind(",") else ","
            miles = "," if decimal == "." else "."
            entero, _, fraccion = token.rpartition(decimal)
            grupos = entero.split(miles)
            if miles in fraccion or decimal in entero or not _son_miles(grupos):
                return frozenset(), False
            return frozenset({Decimal("".join(grupos) + "." + fraccion)}), False
        separador = "." if puntos else ","
        partes = token.split(separador)
        if len(partes) > 2:
            return (frozenset({Decimal("".join(partes))}), False) if _son_miles(partes) else (frozenset(), False)
        izquierda, derecha = partes
        decimal_ = Decimal(f"{izquierda}.{derecha}")
        if len(derecha) == 3 and 1 <= len(izquierda) <= 3 and izquierda != "0":
            return frozenset({decimal_, Decimal(izquierda + derecha)}), True
        return frozenset({decimal_}), False
    except InvalidOperation:
        return frozenset(), False


def _son_miles(grupos: Sequence[str]) -> bool:
    return bool(grupos) and 1 <= len(grupos[0]) <= 3 and all(len(g) == 3 for g in grupos[1:])


def leer_unidad(resto: str) -> str | None:
    """La clase de unidad que sigue a un número (`area_ha`, `contable:casas`, …) o `None` si no hay unidad."""
    resto = re.sub(r"^\s*\(?\s*", "", resto)
    for modificador in _MODIFICADORES:
        if resto.startswith(modificador + " "):
            resto = resto[len(modificador):].lstrip()
            break
    encontrada = re.match(r"([a-z][a-z0-9^]*)(?:\s+([a-z][a-z0-9^]*))?", resto)
    if not encontrada:
        return None
    primera, segunda = encontrada.group(1), encontrada.group(2)
    if segunda and f"{primera} {segunda}" in UNIDADES:
        return UNIDADES[f"{primera} {segunda}"]
    if primera in UNIDADES:
        return UNIDADES[primera]
    if primera in _NO_ES_UNIDAD:
        return None
    return "contable:" + primera[:5]


def unidad_citada(unit: object, quantity: object) -> str | None:
    """La unidad que Work declara: el campo `unit` y, si falta o no se reconoce, la que sigue a la cifra."""
    declarada = normalizar(str(unit or "")).strip(" .")
    if declarada:
        clase = UNIDADES.get(declarada) or leer_unidad(declarada)
        if clase:
            return clase
    texto = normalizar(str(quantity or ""))
    primero = _NUM.search(texto)
    return leer_unidad(texto[primero.end():]) if primero else None


@dataclass
class Linea:
    """Una línea del texto leído y, si es fila de tabla, su celda, fila y celdas."""

    texto: str
    celda: str | None = None                 # `B3` en Excel, `columna 2` en Word
    fila: int | None = None
    tabla: int | None = None
    parrafo: int | None = None
    celdas: list[tuple[str, str]] = field(default_factory=list)   # (etiqueta de columna, valor) de una fila de tabla


@dataclass
class Segmento:
    """Un tramo del texto leído (página, hoja, diapositiva) con sus avisos de cobertura."""

    archivo: str
    ubicacion: str
    nivel: str
    pagina: int | None = None
    hoja: str | None = None
    diapositiva: int | None = None
    lineas: list[Linea] = field(default_factory=list)
    ocr: bool = False
    parcial: bool = False
    sin_texto: bool = False
    unidades_de_columna: dict[str, str] = field(default_factory=dict)

    @property
    def fiable(self) -> bool:
        """Solo una lectura completa, nativa y con texto permite concluir que algo falta o difiere."""
        return not (self.ocr or self.parcial or self.sin_texto)


@dataclass(frozen=True)
class Candidato:
    """Una cifra de la fuente con sus valores posibles, su unidad y la línea donde está."""

    valores: frozenset[Decimal]
    ambiguo: bool
    unidad: str | None
    token: str
    linea: Linea
    segmento: Segmento


def segmentar(archivo: str, texto: str, nivel_base: str) -> list[Segmento]:
    """Parte el texto de un lector por sus marcadores reales (página, hoja, diapositiva, imagen con OCR)."""
    segmentos: list[Segmento] = []
    actual = Segmento(archivo, archivo, nivel_base)
    segmentos.append(actual)
    for crudo in texto.splitlines():
        linea = crudo.rstrip()
        if not linea.strip():
            continue
        pagina = re.match(r"\[Página (\d+)([^\]]*)\]$", linea)
        diapositiva = re.match(r"\[Diapositiva (\d+)\]$", linea)
        hoja = re.match(r"Hoja: ([^;]+);", linea)
        imagen = re.match(r"\[Imagen(?: incrustada)?[^\]]*texto OCR\]$", linea)
        if pagina:
            actual = Segmento(archivo, f"{archivo}, página {pagina.group(1)}", "pagina", pagina=int(pagina.group(1)),
                              sin_texto="sin texto extraíble" in pagina.group(2),
                              parcial="no interpretadas por OCR" in pagina.group(2))
            segmentos.append(actual)
            continue
        if diapositiva:
            actual = Segmento(archivo, f"{archivo}, diapositiva {diapositiva.group(1)}", "pagina",
                              diapositiva=int(diapositiva.group(1)))
            segmentos.append(actual)
            continue
        if hoja:
            actual = Segmento(archivo, f"{archivo}, hoja {hoja.group(1).strip()}", "pagina", hoja=hoja.group(1).strip())
            segmentos.append(actual)
            continue
        if imagen:
            actual = Segmento(archivo, archivo, nivel_base, ocr=True)
            segmentos.append(actual)
            continue
        if linea.startswith("[OCR visual adicional]"):
            actual.ocr = True
            continue
        if re.match(r"(?:COBERTURA (?:VISUAL )?PARCIAL|LECTURA PARCIAL|CONTENIDO GRÁFICO NO INSPECCIONADO)", linea):
            actual.parcial = True
            continue
        actual.lineas.append(_linea(linea, actual))
    for segmento in segmentos:
        _unidades_de_encabezado(segmento)
    return [s for s in segmentos if s.lineas or s.sin_texto or s.parcial]


def _linea(texto: str, segmento: Segmento) -> Linea:
    word = _FILA_DE_WORD.match(texto)
    if word:
        celdas = [tuple(c.split("=", 1)) if "=" in c else ("", c) for c in word.group(3).split(" | ")]
        return Linea(texto, fila=int(word.group(2)), tabla=int(word.group(1)),
                     celdas=[(a, b) for a, b in celdas])
    if segmento.hoja is not None and _CELDA_DE_HOJA.match(texto):
        celdas = []
        for celda in texto.split(" | "):
            coordenada = _CELDA_DE_HOJA.match(celda)
            celdas.append((coordenada.group(1) + coordenada.group(2), celda.split("=", 1)[1]) if coordenada
                          else ("", celda))
        fila = _CELDA_DE_HOJA.match(texto)
        return Linea(texto, fila=int(fila.group(2)) if fila else None, celdas=celdas)
    parrafo = re.match(r"Párrafo (\d+): ", texto)
    return Linea(texto, parrafo=int(parrafo.group(1)) if parrafo else None)


def _unidades_de_encabezado(segmento: Segmento) -> None:
    """Una unidad definida en el encabezado de una columna (`Área (ha)`) vale para sus celdas numéricas."""
    for linea in segmento.lineas:
        if not linea.celdas or (linea.fila not in (1, None) and linea.tabla is None):
            continue
        if linea.tabla is not None and linea.fila != 1:
            continue
        if any(_NUM.search(normalizar(valor)) for _, valor in linea.celdas):
            continue
        for etiqueta, valor in linea.celdas:
            texto = normalizar(valor)
            interior = re.findall(r"[(\[]\s*([^)\]]+?)\s*[)\]]", texto)
            clase = next((UNIDADES[i.strip()] for i in interior if i.strip() in UNIDADES), None)
            if clase is None:
                palabras = re.findall(r"[a-z][a-z0-9^]*", texto)
                clase = next((UNIDADES[p] for p in reversed(palabras) if p in UNIDADES), None)
            if clase:
                segmento.unidades_de_columna[_columna(etiqueta)] = clase
        return


def _columna(etiqueta: str) -> str:
    """`B3` → `B`; `columna 2` → `columna 2`."""
    return re.sub(r"\d+$", "", etiqueta) if re.fullmatch(r"[A-Z]{1,3}\d+", etiqueta) else etiqueta


def _excluido(norm: str, inicio: int, fin: int, token: str, unidad: str | None, pegado: bool) -> bool:
    """¿El número es una fecha, página, índice, escala, coordenada, código, año o porcentaje y no una cantidad?"""
    if any(f.start() <= inicio < f.end() for f in _FECHA.finditer(norm)):
        return True
    antes = norm[max(0, inicio - 24):inicio]
    if _PREFIJO_DE_INDICE.search(antes) or re.search(r"[a-z]-$", antes) or antes.endswith(("$", "#")):
        return True
    if re.search(r"\d\s*:\s*$", antes) or re.match(r"\s*:\s*\d", norm[fin:fin + 4]):
        return True
    if re.fullmatch(r"\d{1,3}[.,]\d{4,}", token) or norm[fin:fin + 1] in ("°", "'", "′", "″", "%"):
        return True
    if unidad is None:
        cifras = re.sub(r"\D", "", token)
        if len(cifras) >= 6 and "." not in token and "," not in token:
            return True
        if re.fullmatch(r"\d{4}", token) and 1900 <= int(token) <= 2100:
            return True
    return pegado and unidad is None


def _numeros(linea: Linea, segmento: Segmento, unidad_de_columna: str | None = None,
             texto: str | None = None, columna: str = "") -> list[Candidato]:
    original = texto if texto is not None else linea.texto
    norm = normalizar(original)
    unidad_columna = unidad_de_columna or segmento.unidades_de_columna.get(columna)
    candidatos = []
    for hallado in _NUM.finditer(norm):
        token = hallado.group(0)
        resto = norm[hallado.end():hallado.end() + 40]
        pegado = bool(resto) and resto[0].isalpha()
        unidad = leer_unidad(resto) if (pegado or resto[:1] in (" ", "(")) else None
        if pegado and (unidad is None or unidad.startswith("contable:")):
            continue                                            # `12AB`, `1a15b…`: un código, no una cantidad
        if unidad is None:
            unidad = unidad_columna
        if _excluido(norm, hallado.start(), hallado.end(), token, unidad, pegado):
            continue
        valor, ambiguo = valores(token)
        if valor:
            candidatos.append(Candidato(valor, ambiguo, unidad, token, linea, segmento))
    return candidatos


def candidatos_de(segmento: Segmento) -> list[Candidato]:
    """Las cantidades de un segmento, con la unidad que las acompaña o la de su columna."""
    salida: list[Candidato] = []
    for linea in segmento.lineas:
        if linea.celdas:
            for indice, (etiqueta, valor) in enumerate(linea.celdas):
                siguiente = normalizar(linea.celdas[indice + 1][1]).strip() if indice + 1 < len(linea.celdas) else ""
                unidad_vecina = UNIDADES.get(siguiente)
                texto = valor + (f" {siguiente}" if unidad_vecina else "")
                salida.extend(_numeros(linea, segmento, None, texto, _columna(etiqueta)))
        else:
            salida.extend(_numeros(linea, segmento))
    return salida


def _raices(texto: str) -> set[str]:
    return {p[:5] for p in re.findall(r"[a-z]{3,}", normalizar(texto)) if p not in _STOP}


@dataclass(frozen=True)
class Anclas:
    """Las raíces que ligan una línea a la actividad y al concepto de su cantidad."""

    de_actividad: frozenset[str]
    de_concepto: frozenset[str]

    @property
    def suficientes(self) -> bool:
        """¿Hay palabras bastantes para asociar una cifra a la actividad?"""
        return bool(self.de_actividad) or self._concepto_usable

    @property
    def _concepto_usable(self) -> bool:
        return bool(self.de_concepto) and (len(self.de_concepto) >= 2 or not self.de_concepto <= _GENERICAS)

    def fuerte(self, raices_de_la_linea: set[str]) -> bool:
        """¿La línea nombra la actividad o todo el concepto de la cantidad?"""
        if self.de_actividad & raices_de_la_linea:
            return True
        return self._concepto_usable and self.de_concepto <= raices_de_la_linea


def anclas_de(actividad: Mapping[str, Any]) -> Anclas:
    """Las palabras que ligan una línea a la actividad (sin las genéricas) y al concepto de su cantidad."""
    unidad = normalizar(str(actividad.get("unit") or ""))
    descripcion = _NUM.sub(" ", normalizar(str(actividad.get("quantity") or "")))
    palabras_de_unidad = "|".join(sorted(map(re.escape, UNIDADES), key=len, reverse=True))
    descripcion = re.sub(rf"\b(?:{palabras_de_unidad})\b", " ", descripcion)
    concepto = _raices(descripcion) - {unidad[:5]}
    return Anclas(frozenset(_raices(str(actividad.get("activity") or "")) - _GENERICAS), frozenset(concepto))


def _raices_de(candidato: Candidato) -> set[str]:
    """Las raíces de la línea del candidato y, en texto corrido, las del encabezado anterior si no trae cifras."""
    linea, lineas = candidato.linea, candidato.segmento.lineas
    texto = linea.texto
    posicion = next((i for i, otra in enumerate(lineas) if otra is linea), 0)
    if not linea.celdas and posicion > 0:
        anterior = lineas[posicion - 1]
        if not anterior.celdas and not re.search(r"\d", anterior.texto):
            texto = anterior.texto + " " + texto
    return _raices(texto)


@dataclass(frozen=True)
class Cita:
    """Lo que `fuente` cita: un archivo (o el correo) y su localizador."""

    archivo: str | None
    paginas: tuple[int, int] | None = None
    hoja: str | None = None
    filas: tuple[int, int] | None = None
    diapositiva: int | None = None
    tabla: int | None = None
    fila_de_tabla: int | None = None

    @property
    def tiene_localizador(self) -> bool:
        """¿La cita trae página, hoja, celda, diapositiva o tabla?"""
        return any(v is not None for v in (self.paginas, self.hoja, self.filas, self.diapositiva, self.tabla))


def citas_de(fuente: str) -> list[Cita]:
    """Las citas de un campo `fuente` (se separan por «;»): archivo y, si lo trae, página, hoja, celda o tabla."""
    citas: list[Cita] = []
    for trozo in fuente.split(";"):
        norm = normalizar(trozo)
        nombres = [" ".join(m.group(0).split()).strip(" -.(") for m in _ARCHIVO.finditer(trozo)]
        pagina = _PAGINA.search(norm)
        hoja = _HOJA.search(norm)
        celda = _CELDA.search(norm)
        diapositiva = _DIAPOSITIVA.search(norm)
        tabla = _TABLA.search(norm)
        filas = None
        if celda:
            primera = int(celda.group(2))
            filas = (primera, int(celda.group(4)) if celda.group(4) else primera)
        comunes: dict[str, Any] = {
            "paginas": (int(pagina.group(1)), int(pagina.group(2) or pagina.group(1))) if pagina else None,
            "hoja": hoja.group(1).strip() if hoja else None, "filas": filas,
            "diapositiva": int(diapositiva.group(1)) if diapositiva else None,
            "tabla": int(tabla.group(1)) if tabla else None,
            "fila_de_tabla": int(tabla.group(2)) if tabla and tabla.group(2) else None}
        if nombres:
            citas.extend(Cita(nombre, **comunes) for nombre in nombres)
        elif trozo.strip():
            citas.append(Cita(None, **comunes))
    return citas


@dataclass
class Expediente:
    """El correo y los adjuntos leídos del hilo, partidos en segmentos bajo demanda."""

    correo: str
    adjuntos: list[dict[str, Any]]
    incompleto: bool
    _segmentos: dict[str, list[Segmento]] = field(default_factory=dict)

    def coincidencias(self, nombre: str) -> list[dict[str, Any]]:
        """Los adjuntos que puede nombrar la cita: el de nombre idéntico o, si no hay, los de nombre equivalente."""
        buscado = " ".join(nombre.casefold().split())
        propios = [(" ".join(str(a.get("filename") or "").casefold().split()), a) for a in self.adjuntos]
        exactos = [a for p, a in propios if p and p == buscado]
        return exactos or [a for p, a in propios if p and (p.endswith(buscado) or buscado.endswith(p))]

    def archivo(self, nombre: str) -> dict[str, Any] | None:
        """El adjunto cuyo nombre es el citado; `None` si no está o si la cita puede nombrar a más de uno."""
        hallados = self.coincidencias(nombre)
        return hallados[0] if len(hallados) == 1 else None

    def segmentos(self, nombre: str | None) -> list[Segmento]:
        """Los segmentos del correo (`None`) o de un adjunto, ya partidos por sus marcadores."""
        if nombre is None:
            if "\0correo" not in self._segmentos:
                self._segmentos["\0correo"] = segmentar("correo", self.correo, "correo")
            return self._segmentos["\0correo"]
        adjunto = self.archivo(nombre)
        return self._de_adjunto(adjunto) if adjunto is not None else segmentar(nombre, "", "archivo")

    def _de_adjunto(self, adjunto: dict[str, Any]) -> list[Segmento]:
        clave = str(id(adjunto))
        if clave not in self._segmentos:
            nombre = str(adjunto.get("filename") or "")
            texto = str(adjunto.get("text") or "")
            self._segmentos[clave] = segmentar(nombre, texto, "archivo")
            if not texto.strip():
                self._segmentos[clave].append(Segmento(nombre, nombre, "archivo", sin_texto=True))
        return self._segmentos[clave]

    def todos(self) -> list[Segmento]:
        """Todos los segmentos del expediente."""
        return [*self.segmentos(None), *(s for a in self.adjuntos for s in self._de_adjunto(a))]


def _ventana(cita: Cita, exp: Expediente) -> list[tuple[Segmento, list[Linea]]] | None:
    """Los (segmento, líneas) que corresponden a la cita; `None` si el archivo citado no está en el expediente."""
    if cita.archivo is not None and exp.archivo(cita.archivo) is None:
        return None
    segmentos = exp.segmentos(cita.archivo)
    elegidos = []
    for segmento in segmentos:
        if cita.paginas and (segmento.pagina is None or not cita.paginas[0] <= segmento.pagina <= cita.paginas[1]):
            continue
        if cita.diapositiva is not None and segmento.diapositiva != cita.diapositiva:
            continue
        if cita.hoja and normalizar(segmento.hoja or "") != normalizar(cita.hoja):
            continue
        lineas = segmento.lineas
        if cita.filas:
            lineas = [li for li in lineas if li.fila is not None and cita.filas[0] <= li.fila <= cita.filas[1]]
        if cita.tabla is not None:
            lineas = [li for li in lineas if li.tabla == cita.tabla
                      and (cita.fila_de_tabla is None or li.fila == cita.fila_de_tabla)]
        elegidos.append((segmento, lineas))
    return elegidos


def _nivel(cita: Cita) -> str:
    if cita.filas or (cita.tabla is not None and cita.fila_de_tabla is not None):
        return "celda" if cita.filas else "fila"
    if cita.tiene_localizador:
        return "pagina"
    return "correo" if cita.archivo is None else "archivo"


@dataclass
class Resolucion:
    """Dónde se busca: segmentos y líneas citados, nivel de precisión y si la cita se resolvió."""

    ventanas: list[tuple[Segmento, list[Linea]]]
    nivel: str
    ubicacion: str
    resuelta: bool
    # citas explícitas que no se pudieron resolver: fuente_inexistente, referencia_ambigua, ubicacion_no_resuelta
    pendientes: list[str] = field(default_factory=list)
    citada: str = ""


def resolver(fuente: str, exp: Expediente) -> Resolucion:
    """La ubicación citada, de la más precisa a la más amplia; sin cita utilizable, todo el expediente."""
    ventanas: list[tuple[Segmento, list[Linea]]] = []
    niveles: list[str] = []
    ubicaciones: list[str] = []
    pendientes: list[str] = []
    citadas: list[str] = []
    for cita in citas_de(fuente):
        if cita.archivo is not None:
            citadas.append(cita.archivo)
            hallados = exp.coincidencias(cita.archivo)
            if not hallados:
                pendientes.append("fuente_inexistente")
                continue
            if len(hallados) > 1:
                pendientes.append("referencia_ambigua")
                continue
        ventana = _ventana(cita, exp)
        if ventana is None:
            continue
        con_lineas = [(s, li) for s, li in ventana if li or s.sin_texto]
        if cita.tiene_localizador and not con_lineas:
            pendientes.append("ubicacion_no_resuelta")
            ventana = _ventana(Cita(cita.archivo), exp) or []
            con_lineas = [(s, li) for s, li in ventana if li or s.sin_texto]
            niveles.append("archivo" if cita.archivo else "correo")
        else:
            niveles.append(_nivel(cita))
        ventanas.extend(con_lineas)
        ubicaciones.extend(dict.fromkeys(s.ubicacion for s, _ in con_lineas))
    if ventanas:
        orden = ("celda", "fila", "pagina", "archivo", "correo")
        return Resolucion(ventanas, max(niveles, key=orden.index), "; ".join(dict.fromkeys(ubicaciones)), True,
                          pendientes, "; ".join(citadas))
    if pendientes:                      # se citó algo concreto y no se pudo leer: no se busca en otras fuentes
        return Resolucion([], "archivo", "; ".join(dict.fromkeys(citadas)), False, pendientes, "; ".join(citadas))
    todos = [(s, s.lineas) for s in exp.todos()]
    return Resolucion(todos, "expediente", "expediente", False)


def _cantidad(actividad: Mapping[str, Any]) -> tuple[frozenset[Decimal], bool, str] | None:
    texto = str(actividad.get("quantity") if actividad.get("quantity") is not None else "")
    norm = normalizar(texto)
    for hallado in _NUM.finditer(norm):
        valor, ambiguo = valores(hallado.group(0))
        if valor:
            return valor, ambiguo, hallado.group(0)
    return None


def _distintos(candidatos: Sequence[Candidato], buscado: frozenset[Decimal]) -> set[Decimal]:
    return {v for c in candidatos if not c.ambiguo for v in c.valores if v not in buscado}


def _con_confusion(segmentos: Sequence[Segmento], buscado: frozenset[Decimal]) -> bool:
    """¿El valor solo aparece si se corrigen confusiones típicas de OCR (S↔5, O↔0, l↔1)?"""
    for segmento in segmentos:
        for linea in segmento.lineas:
            for hallado in _CONFUNDIBLE.finditer(linea.texto):
                token = hallado.group(0)
                if not re.search(r"\d", token) or not re.search(r"[OoIlSBZ]", token):
                    continue
                if valores(token.translate(_CONFUSIONES))[0] & buscado:
                    return True
    return False


def _resultado(estado: str, motivo: str, res: Resolucion, *, candidato: Candidato | None = None,
               unidad: str | None = None, ocr: bool = False) -> dict[str, Any]:
    segmento = candidato.segmento if candidato else None
    return {"estado": estado, "severidad": SEVERIDAD[estado], "nivel": res.nivel,
            "ubicacion": segmento.ubicacion if segmento else res.ubicacion,
            "fragmento": candidato.linea.texto[:MAX_FRAGMENTO] if candidato else "",
            "unidad_fuente": (candidato.unidad if candidato else None) or "", "unidad_citada": unidad or "",
            "ocr": bool(ocr or (segmento and segmento.ocr)), "motivo": motivo}


def verificar_actividad(actividad: Mapping[str, Any], exp: Expediente) -> dict[str, Any] | None:
    """El resultado contextual de la cantidad de una actividad; `None` si la actividad no trae una cifra."""
    cantidad = _cantidad(actividad)
    if cantidad is None:
        return None
    buscado, _ambiguo, _token = cantidad
    unidad = unidad_citada(actividad.get("unit"), actividad.get("quantity"))
    res = resolver(str(actividad.get("fuente") or ""), exp)
    if res.pendientes and not res.ventanas:
        return _resultado(NO_COMPROBABLE, f"{res.pendientes[0]}: {_MOTIVOS_PENDIENTES[res.pendientes[0]]} "
                          f"({res.citada}); no se busca en otras fuentes", res, unidad=unidad)
    resultado = _decidir(buscado, unidad, actividad, exp, res)
    if res.pendientes and resultado["estado"] in (CONTRADICTORIA_VALOR, CONTRADICTORIA_UNIDAD):
        return _resultado(NO_COMPROBABLE, f"{res.pendientes[0]}: {_MOTIVOS_PENDIENTES[res.pendientes[0]]} "
                          f"({res.citada}); no se declara contradicción con evidencia de otra ubicación", res,
                          unidad=unidad)
    return resultado


_MOTIVOS_PENDIENTES = {
    "fuente_inexistente": "el archivo citado no está entre las fuentes leídas",
    "referencia_ambigua": "la cita puede nombrar a más de un archivo",
    "ubicacion_no_resuelta": "la página, hoja o celda citada no se puede localizar",
}


def _decidir(buscado: frozenset[Decimal], unidad: str | None, actividad: Mapping[str, Any], exp: Expediente,
             res: Resolucion) -> dict[str, Any]:
    anclas = anclas_de(actividad)
    candidatos = [c for s, li in res.ventanas for c in candidatos_de(Segmento(
        s.archivo, s.ubicacion, s.nivel, s.pagina, s.hoja, s.diapositiva, li, s.ocr, s.parcial, s.sin_texto,
        s.unidades_de_columna))]
    segmentos = [s for s, _ in res.ventanas]
    fiable = bool(segmentos) and all(s.fiable for s in segmentos) and not exp.incompleto
    coinciden = [c for c in candidatos if c.valores & buscado]
    propias = [c for c in coinciden if anclas.fuerte(_raices_de(c))]
    if propias:
        return _comparar_unidad(propias, unidad, res)
    if coinciden:
        alternativas = _alternativas(candidatos, buscado, unidad, anclas)
        if len(alternativas) == 1 and fiable:
            return _resultado(CONTRADICTORIA_VALOR, "el valor citado aparece solo en otro concepto; el de esta "
                              f"actividad es {_texto(alternativas)}", res, candidato=coinciden[0], unidad=unidad)
        motivo = ("el valor aparece, pero no hay palabras de la actividad o del concepto que lo liguen a ella"
                  if anclas.suficientes else "la actividad no trae palabras suficientes para asociar el valor")
        return _resultado(ASOCIACION_DUDOSA, motivo, res, candidato=coinciden[0], unidad=unidad)
    return _sin_coincidencia(buscado, unidad, anclas, candidatos, segmentos, fiable, res, exp)


def _texto(valores_: set[Decimal]) -> str:
    return ", ".join(format(v.normalize(), "f") for v in sorted(valores_))


def _alternativas(candidatos: Sequence[Candidato], buscado: frozenset[Decimal], unidad: str | None,
                  anclas: Anclas) -> set[Decimal]:
    """Los valores distintos que la fuente da al mismo concepto y en la misma unidad (solo con unidad conocida)."""
    if unidad is None:
        return set()
    propios = [c for c in candidatos if c.unidad == unidad and anclas.fuerte(_raices_de(c))]
    return _distintos(propios, buscado)


def _comparar_unidad(propias: list[Candidato], unidad: str | None, res: Resolucion) -> dict[str, Any]:
    if unidad is not None:
        iguales = [c for c in propias if c.unidad == unidad]
        if iguales:
            return _resultado(VERIFICADA, "valor, unidad y concepto coinciden", res, candidato=iguales[0],
                              unidad=unidad)
        medidas = [c for c in propias if c.unidad and not c.unidad.startswith("contable:")]
        if medidas and not unidad.startswith("contable:"):
            return _resultado(CONTRADICTORIA_UNIDAD, f"el mismo valor aparece en {medidas[0].unidad}, no en {unidad}",
                              res, candidato=medidas[0], unidad=unidad)
        return _resultado(VERIFICADA_SIN_UNIDAD, "el valor coincide; la unidad de la fuente no es comparable", res,
                          candidato=propias[0], unidad=unidad)
    return _resultado(VERIFICADA_SIN_UNIDAD, "el valor coincide; Work no indicó una unidad", res, candidato=propias[0])


def _sin_coincidencia(buscado: frozenset[Decimal], unidad: str | None, anclas: Anclas, candidatos: list[Candidato],
                      segmentos: list[Segmento], fiable: bool, res: Resolucion, exp: Expediente) -> dict[str, Any]:
    if not segmentos:
        return _resultado(NO_COMPROBABLE, "no hay texto leído donde buscar", res, unidad=unidad)
    if not fiable:
        ocr = any(s.ocr for s in segmentos)
        if _con_confusion(segmentos, buscado):
            return _resultado(NO_COMPROBABLE_OCR, "el valor solo aparece si se corrigen confusiones típicas de OCR",
                              res, unidad=unidad, ocr=True)
        motivo = ("lectura por OCR: la cifra no se encontró, pero no se concluye que falte" if ocr else
                  "lectura parcial o sin texto: no se concluye que la cifra falte")
        return _resultado(NO_COMPROBABLE_OCR if ocr else NO_COMPROBABLE, motivo, res, unidad=unidad, ocr=ocr)
    if res.resuelta:
        fuera = [c for s in exp.todos() if all(s is not t for t, _ in res.ventanas) for c in candidatos_de(s)
                 if c.valores & buscado and anclas.fuerte(_raices_de(c))]
        if fuera:
            return _resultado(ASOCIACION_DUDOSA, "el valor aparece fuera de la ubicación citada, en "
                              f"{fuera[0].segmento.ubicacion}", res, candidato=fuera[0], unidad=unidad)
    alternativas = _alternativas(candidatos, buscado, unidad, anclas)
    if len(alternativas) == 1:
        return _resultado(CONTRADICTORIA_VALOR, f"la fuente da para el mismo concepto y unidad el valor "
                          f"{_texto(alternativas)}", res, unidad=unidad)
    return _resultado(NO_ENCONTRADA, "la cifra no aparece en la ubicación citada; su ausencia no prueba un error",
                      res, unidad=unidad)


def observar(actividades: Sequence[Any], correo: str, adjuntos: Sequence[Mapping[str, Any]],
             incompleto: bool = False) -> list[dict[str, Any]]:
    """Las observaciones contextuales de la tabla de Work, una por actividad con cifra (modo sombra: solo informan)."""
    exp = Expediente(correo or "", [dict(a) for a in adjuntos if isinstance(a, Mapping)], bool(incompleto))
    observaciones = []
    for indice, actividad in enumerate(list(actividades)[:MAX_ACTIVIDADES], 1):
        if not isinstance(actividad, Mapping):
            continue
        resultado = verificar_actividad(actividad, exp)
        if resultado is None:
            continue
        observaciones.append({"actividad": indice, "nombre": str(actividad.get("activity") or "")[:60],
                              "cantidad": str(actividad.get("quantity") or "")[:40], **resultado})
    return observaciones
