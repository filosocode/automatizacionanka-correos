"""Comprobación mecánica de que lo que Work puso en la tabla está en los textos del expediente (T-38).

Nada se cambia ni se bloquea: cada diferencia es un aviso con su ubicación. Solo se comprueba lo que se puede
decidir sin interpretar: números de las cantidades, archivos y páginas citados y extractos literales. Las fechas no
se comparan («7 de noviembre» y «2026-11-07» no se pueden igualar sin adivinar).
"""

from __future__ import annotations

import logging
import re
from typing import Any

from anka.dominio import cambios_de_magnitud, contexto_de_cantidad

log = logging.getLogger(__name__)
NUMERO = re.compile(r"\d(?:[\d.,]*\d)?")
ARCHIVO = re.compile(r"[\w\-. ()áéíóúñÁÉÍÓÚÑ]+?\.(?:pdf|docx?|xlsx?|xlsm|csv|tsv|txt|kml|kmz|gpx|geojson|zip|dwg|dxf|"
                     r"odt|ods|"
                     r"png|jpe?g|tiff?)", re.IGNORECASE)
PAGINA = re.compile(r"\b(?:p|pp|pág|págs|pagina|página|paginas|páginas)\.?\s*(\d+)(?:\s*[-–]\s*(\d+))?", re.IGNORECASE)
MARCA_PAGINA = re.compile(r"\[Página (\d+)\]")
FRAGMENTO_MIN = 12
MAX_AVISOS = 10


def valores(token: str) -> set[float]:
    """Los valores que puede significar un número con puntos y comas (`1.250` es 1,25 o 1250: se admiten ambos)."""
    puntos, comas = token.count("."), token.count(",")
    if puntos and comas:   # el último separador es el decimal
        decimal = "." if token.rfind(".") > token.rfind(",") else ","
        candidatos = [token.replace("," if decimal == "." else ".", "").replace(decimal, ".")]
    elif puntos or comas:
        separador = "." if puntos else ","
        partes = token.split(separador)
        if len(partes) > 2:   # varios separadores iguales: son miles
            candidatos = ["".join(partes)]
        else:
            candidatos = [partes[0] + "." + partes[1]]
            if len(partes[1]) == 3:   # `1.250` / `1,250`
                candidatos.append(partes[0] + partes[1])
    else:
        candidatos = [token]
    return {round(float(c), 6) for c in candidatos if re.fullmatch(r"\d+(?:\.\d+)?", c)}


def numeros_del_texto(texto: str) -> set[float]:
    """Todos los números que el texto puede estar diciendo."""
    encontrados: set[float] = set()
    for token in NUMERO.findall(texto):
        encontrados |= valores(token)
        if "," in token or token.count(".") > 1:
            # `-74.19,4.93; -74.18,4.93`: la coma puede separar una lista y no ser un decimal; vale también esa lectura
            encontrados |= {round(float(cifra), 6) for cifra in re.findall(r"\d+(?:\.\d+)?", token)}
    return encontrados


def _normalizado(texto: str) -> str:
    """Para comparar extractos: sin mayúsculas, comillas ni saltos, con los espacios colapsados."""
    texto = re.sub(r"[\"'«»“”‘’]", "", texto.casefold())
    return " ".join(texto.replace("-\n", "").split())


def _fragmentos(extracto: str) -> list[str]:
    """Las partes literales de un extracto con «…» o «...» (cada una debe aparecer por separado)."""
    partes = [_normalizado(p) for p in re.split(r"…|\.\.\.|\[\.\.\.\]", extracto)]
    return [p for p in partes if len(p) >= FRAGMENTO_MIN] or [p for p in partes if p]


class _Expediente:
    """Los textos del hilo ya preparados para consultar."""

    def __init__(self, textos: dict) -> None:
        self.correo = str(textos.get("correo") or "")
        self.adjuntos = [a for a in textos.get("adjuntos") or [] if isinstance(a, dict)]
        self.incompleto = bool(textos.get("incompleto"))
        self.sin_texto = self.incompleto or any(not str(a.get("text") or "").strip() for a in self.adjuntos)
        self._numeros: dict[int, set[float]] = {}
        self._global: set[float] | None = None

    def archivo(self, nombre: str) -> dict | None:
        """El adjunto cuyo nombre es el citado (sin distinguir mayúsculas ni espacios de más)."""
        buscado = " ".join(nombre.casefold().split())
        for adjunto in self.adjuntos:
            propio = " ".join(str(adjunto.get("filename") or "").casefold().split())
            if propio and (propio == buscado or propio.endswith(buscado) or buscado.endswith(propio)):
                return adjunto
        return None

    def numeros(self, adjunto: dict | None = None) -> set[float]:
        if adjunto is not None:
            clave = id(adjunto)
            if clave not in self._numeros:
                self._numeros[clave] = numeros_del_texto(str(adjunto.get("text") or ""))
            return self._numeros[clave]
        if self._global is None:
            self._global = numeros_del_texto(self.correo).union(*(self.numeros(a) for a in self.adjuntos))
        return self._global

    def todo(self) -> str:
        return "\n".join([self.correo, *(str(a.get("text") or "") for a in self.adjuntos)])


def _archivos_citados(fuente: str) -> list[str]:
    return [" ".join(m.group(0).split()).strip(" -.(") for m in ARCHIVO.finditer(fuente)]


def _comprobar_cantidad(etiqueta: str, cantidad: object, fuente: str, exp: _Expediente, res: dict) -> None:
    tokens = NUMERO.findall(str(cantidad if cantidad is not None else ""))
    citados = [a for a in (exp.archivo(n) for n in _archivos_citados(fuente)) if a is not None]
    for token in tokens:
        posibles = valores(token)
        if not posibles:
            continue
        if posibles & exp.numeros():
            if citados and not any(posibles & exp.numeros(a) for a in citados):
                nombres = ", ".join(str(a.get("filename")) for a in citados)
                res["diferencias"].append(f"{etiqueta}: la cantidad {token} no está en el archivo citado ({nombres}), "
                                          "sí en otro texto del expediente.")
            else:
                res["comprobadas"] += 1
        elif exp.sin_texto:
            res["no_comprobables"].append(f"{etiqueta}: la cantidad {token} no aparece en los textos leídos, pero hay "
                                          "adjuntos sin texto que podrían contenerla.")
        else:
            res["diferencias"].append(f"{etiqueta}: la cantidad {token} no aparece en ningún texto del expediente "
                                      "(si es un cálculo de Work, debe decirlo en las notas).")


def _comprobar_citas(etiqueta: str, fuente: str, exp: _Expediente, res: dict) -> None:
    for trozo in fuente.split(";"):
        for nombre in _archivos_citados(trozo):
            adjunto = exp.archivo(nombre)
            if adjunto is None:
                res["diferencias"].append(f"{etiqueta}: cita el archivo «{nombre}», que no está en el expediente.")
                continue
            res["comprobadas"] += 1
            marcas = [int(n) for n in MARCA_PAGINA.findall(str(adjunto.get("text") or ""))]
            paginas = [int(n) for m in PAGINA.finditer(trozo) for n in m.groups() if n]
            if marcas and paginas and max(paginas) > max(marcas):
                res["diferencias"].append(f"{etiqueta}: cita la página {max(paginas)} de «{adjunto.get('filename')}», "
                                          f"que se leyó con {max(marcas)} páginas.")


def _extracto_numerico_presente(extracto: str, contenido: str) -> bool:
    """Un extracto que son solo números (coordenadas de un KML, una fila) se comprueba por valor, no por texto.

    Python guarda `-74.30400000` donde el XML dice `-74.304`: son el mismo número y no deben dar una diferencia falsa.
    """
    if re.search(r"[^\W\d_]", NUMERO.sub("", extracto)):   # hay palabras: se comprueba literal
        return False
    existentes = numeros_del_texto(contenido)
    # la coma puede ser decimal (`126,51`) o separar una lista (`-74.3025,4.779`): vale cualquiera de las dos lecturas
    lecturas = (NUMERO.findall(extracto), re.findall(r"\d+(?:\.\d+)?", extracto))
    return any(cifras and all(valores(cifra) & existentes for cifra in cifras) for cifras in lecturas)


def _comprobar_extractos(payload: dict, exp: _Expediente, res: dict) -> None:
    for adjunto in payload.get("attachments") or []:
        if not isinstance(adjunto, dict):
            continue
        propio = exp.archivo(str(adjunto.get("filename") or ""))
        for n, evidencia in enumerate(adjunto.get("evidence") or [], 1):
            extracto = str(evidence_excerpt(evidencia))
            if not extracto.strip():
                continue
            etiqueta = f"adjunto «{adjunto.get('filename')}», evidencia {n}"
            contenido = str(propio.get("text") or "") if propio else exp.todo()
            texto = _normalizado(contenido)
            if all(f in texto for f in _fragmentos(extracto)) or _extracto_numerico_presente(extracto, contenido):
                res["comprobadas"] += 1
            elif propio is not None and not str(propio.get("text") or "").strip():
                res["no_comprobables"].append(f"{etiqueta}: el archivo no tiene texto para comprobar el extracto.")
            elif all(f in _normalizado(exp.todo()) for f in _fragmentos(extracto)):
                res["diferencias"].append(f"{etiqueta}: el extracto no está en ese archivo, sí en otro texto del "
                                          "expediente.")
            else:
                res["diferencias"].append(f"{etiqueta}: el extracto no aparece literal en el expediente.")


def evidence_excerpt(evidencia: Any) -> str:
    """El extracto literal de una evidencia de adjunto ('' si no es un objeto con extracto)."""
    return str(evidencia.get("excerpt") or "") if isinstance(evidencia, dict) else ""


def comprobar_tabla(payload: dict, textos: dict | None) -> dict | None:
    """Resultado de comprobar la tabla de Work contra los textos del expediente, o `None` si no hay textos (T-38).

    `estado`: «verificada» (hubo comprobaciones y ninguna diferencia), «con_diferencias» o «no_comprobable».
    """
    if not textos:
        return None
    exp = _Expediente(textos)
    res: dict[str, Any] = {"comprobadas": 0, "diferencias": [], "no_comprobables": []}
    for indice, fila in enumerate(payload.get("activities") or [], 1):
        if not isinstance(fila, dict):
            continue
        etiqueta = f"actividad {indice} («{str(fila.get('activity') or '')[:50]}»)"
        fuente = str(fila.get("fuente") or "")
        _comprobar_cantidad(etiqueta, fila.get("quantity"), fuente, exp, res)
        _comprobar_citas(etiqueta, fuente, exp, res)
    for indice, campo in enumerate(payload.get("fields") or [], 1):
        if isinstance(campo, dict):
            etiqueta = f"campo {indice} («{str(campo.get('name') or '')[:40]}»)"
            _comprobar_citas(etiqueta, str(campo.get("source") or ""), exp, res)
    _comprobar_extractos(payload, exp, res)
    res["observaciones"] = _observaciones_contextuales(payload, exp)
    res["cambios_de_magnitud"] = _cambios_de_magnitud(payload, textos)
    res["estado"] = ("con_diferencias" if res["diferencias"] else
                     "verificada" if res["comprobadas"] and not res["no_comprobables"] else "no_comprobable")
    return res


def _observaciones_contextuales(payload: dict, exp: _Expediente) -> list[dict]:
    """Verificación contextual de cantidades y unidades (T-66): modo sombra, solo observaciones.

    No altera `comprobadas`, `diferencias` ni `no_comprobables`, `avisos_de` no la muestra y un fallo aquí nunca frena
    la comprobación existente ni la importación.
    """
    try:
        return contexto_de_cantidad.observar(payload.get("activities") or [], exp.correo, exp.adjuntos, exp.incompleto)
    except Exception:
        log.warning("No se pudo calcular la verificación contextual de cantidades", exc_info=True)
        return []


def _cambios_de_magnitud(payload: dict, textos: dict) -> list[dict]:
    """Posibles cambios de cantidad entre los mensajes del cliente (T-69): modo sombra, solo observaciones.

    Igual que la verificación contextual: no toca los cubos de siempre, `avisos_de` no la muestra, no cambia la tabla ni
    elige un valor vigente, y un fallo aquí nunca frena la comprobación existente ni la importación.
    """
    try:
        return cambios_de_magnitud.detectar(payload.get("activities") or [], textos.get("mensajes"),
                                            str(textos.get("thread_id") or payload.get("thread_id") or ""))
    except Exception:
        log.warning("No se pudo calcular la detección de cambios de magnitud", exc_info=True)
        return []


def avisos_de(resultado: dict | None) -> list[str]:
    """Un aviso por diferencia (hasta 10) para el caso; vacío si no hay resultado o no hay diferencias."""
    if not resultado or not resultado["diferencias"]:
        return []
    mostrados = resultado["diferencias"][:MAX_AVISOS]
    resto = len(resultado["diferencias"]) - len(mostrados)
    return ["Comprobación de fuentes: " + texto for texto in mostrados] + (
        [f"Comprobación de fuentes: {resto} diferencia(s) más; ver el detalle del caso."] if resto else [])
