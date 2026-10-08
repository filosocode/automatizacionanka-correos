"""Tramos de lectura (T-25): dividir un texto largo sin perder nada y condensar los hallazgos de cada tramo.

Es lógica pura, sin modelo ni disco: el orquestador (`servicios/lectura_por_tramos`) decide cuándo hace falta y
llama al modelo; aquí solo se corta, se une y se da formato.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from typing import TypedDict

SOLAPE_CARACTERES = 600  # lo que se repite entre tramos vecinos para no partir un dato que cae en la frontera
MIN_TRAMO_CARACTERES = 1000
# Fronteras preferidas al cortar, de más a menos fuerte. Un encabezado de página u hoja es mejor corte que un párrafo.
_FRONTERAS = ("\n[Página", "\nHoja:", "\n[Diapositiva", "\n\n", "\n", " ")


class Hallazgo(TypedDict):
    """Un dato explícito de un tramo, con dónde aparece y una cita corta que permite comprobarlo."""

    tema: str          # alcance | ubicacion | magnitud | entregable | plazo | metodo | precision | norma | ...
    actividad: str     # componente o actividad a la que pertenece, o vacío
    dato: str
    valor: str
    localizador: str   # archivo y página, párrafo, tabla/fila, hoja/celda...
    cita: str


def estimar_tokens(texto: str, caracteres_por_token: float = 2.5) -> int:
    """Estimación prudente (por exceso) de los tokens de un texto en español técnico."""
    return int(len(texto) / caracteres_por_token) + 1


_ENCABEZADO = re.compile(r"^(?:[[]\s*(?:Página|Diapositiva|Hoja)[^]]*[]]|Hoja:.*)$", re.IGNORECASE)


def ultimo_encabezado(texto: str) -> str:
    """El último marcador de página, diapositiva u hoja de `texto` (vacío si no hay).

    Un tramo que no empieza en un marcador no sabe en qué página está; sin esto el modelo inventaba el localizador.
    """
    for linea in reversed(texto.split("\n")):
        if _ENCABEZADO.match(linea.strip()):
            return linea.strip()
    return ""


def quitar_lineas_repetidas(texto: str, minimo: int = 30) -> tuple[str, int]:
    """Quita las líneas largas que ya aparecieron antes (encabezados y pies de página, avisos repetidos).

    Una línea igual a otra anterior no aporta ningún dato nuevo, pero un modelo pequeño extrae sus hechos una vez por
    cada repetición hasta desbordar su salida. Las líneas cortas se conservan: pueden ser filas de tabla o totales
    que sí significan algo cada vez. Devuelve el texto y cuántas líneas quitó.
    """
    vistas: set[str] = set()
    conservadas = []
    quitadas = 0
    for linea in texto.split("\n"):
        clave = re.sub(r"\s+", " ", linea).strip().casefold()
        if len(clave) >= minimo:
            if clave in vistas:
                quitadas += 1
                continue
            vistas.add(clave)
        conservadas.append(linea)
    return "\n".join(conservadas), quitadas


def _punto_de_corte(texto: str, inicio: int, fin: int) -> int:
    """Mejor lugar donde cortar entre `inicio` y `fin`: la frontera más fuerte en la segunda mitad de la ventana."""
    piso = inicio + (fin - inicio) // 2
    for frontera in _FRONTERAS:
        posicion = texto.rfind(frontera, piso, fin)
        if posicion != -1:
            return posicion + 1 if frontera.startswith("\n") else posicion + len(frontera)
    return fin  # una línea sin ningún espacio: corte duro


def dividir_texto(texto: str, max_caracteres: int, solape: int = SOLAPE_CARACTERES) -> list[str]:
    """Divide `texto` en tramos de a lo sumo `max_caracteres`; la unión de los tramos cubre todo el texto.

    Corta en saltos estructurales y repite hasta `solape` caracteres (desde un inicio de línea) entre tramos vecinos.
    """
    if max_caracteres < MIN_TRAMO_CARACTERES:
        raise ValueError(f"El tramo debe tener al menos {MIN_TRAMO_CARACTERES} caracteres.")
    if len(texto) <= max_caracteres:
        return [texto]
    solape = min(solape, max_caracteres // 4)
    tramos = []
    inicio = 0
    while True:
        fin = min(inicio + max_caracteres, len(texto))
        corte = len(texto) if fin == len(texto) else _punto_de_corte(texto, inicio, fin)
        tramos.append(texto[inicio:corte])
        if corte >= len(texto):
            return tramos
        reinicio = max(corte - solape, inicio + 1)
        salto = texto.find("\n", reinicio, corte)
        inicio = salto + 1 if salto != -1 and salto + 1 < corte else reinicio


def _clave(hallazgo: Hallazgo) -> tuple[str, ...]:
    return tuple(re.sub(r"\s+", " ", str(hallazgo.get(campo, ""))).strip().casefold()
                 for campo in ("tema", "actividad", "dato", "valor", "localizador"))


def unir_hallazgos(listas: Iterable[Iterable[Hallazgo]]) -> list[Hallazgo]:
    """Une los hallazgos de varios tramos conservando el orden y quitando los repetidos por el solapamiento."""
    vistos: set[tuple[str, ...]] = set()
    unidos: list[Hallazgo] = []
    for lista in listas:
        for hallazgo in lista:
            clave = _clave(hallazgo)
            if clave not in vistos:
                vistos.add(clave)
                unidos.append(hallazgo)
    return unidos


def compendio(hallazgos: Iterable[Hallazgo]) -> str:
    """Texto del compendio de hallazgos: una línea por dato con su localizador y su cita."""
    lineas = []
    for item in hallazgos:
        actividad = f"{item['actividad']}: " if item.get("actividad") else ""
        cita = f" | «{item['cita']}»" if item.get("cita") else ""
        lineas.append(
            f"- [{item.get('tema', 'otro')}] {actividad}{item.get('dato', '')}: {item.get('valor', '')}"
            f" | {item.get('localizador', 'sin localizador')}{cita}"
        )
    return "\n".join(lineas)
