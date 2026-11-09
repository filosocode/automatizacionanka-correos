"""Las áreas de los polígonos de un KML/KMZ: cómo se escriben en el texto y cómo se leen de vuelta (T-125)."""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Iterable, Mapping, Sequence
from typing import Any

from anka.dominio.analisis import Actividad, AreaCalculada

AVISO_DE_DATO = "Dato calculado por el programa, no declarado por el cliente"
_LINEA_DE_AREA = re.compile(
    r"^Polígono «(?P<nombre>.*)»: área geodésica aproximada calculada (?P<m2>[\d.]+) m² \((?P<ha>[\d.]+) ha\); "
    r"perímetro (?P<perimetro>[\d.]+) m; anillos interiores: (?P<interiores>\d+)\.", re.MULTILINE)


def linea_de_area(etiqueta: str, resultado: Mapping[str, float | int], aviso: str) -> str:
    """La línea de texto de un polígono con área; `aviso` es la advertencia de `area_del_poligono`, si la hay."""
    area = float(resultado["area_m2"])
    perimetro = float(resultado["perimetro_m"])
    return (f"Polígono «{etiqueta}»: área geodésica aproximada calculada {area:.1f} m² ({area / 10000:.4f} ha); "
            f"perímetro {perimetro:.1f} m; anillos interiores: {resultado['anillos_interiores']}. "
            f"{AVISO_DE_DATO}{f'; {aviso}' if aviso else ''}")


def linea_sin_area(etiqueta: str, motivo: str) -> str:
    """La línea de texto de un polígono que no tiene área, con la razón."""
    return f"Polígono «{etiqueta}»: sin área calculada ({motivo})"


def lineas_del_total(areas_m2: list[float]) -> list[str]:
    """El área total del archivo cuando tiene más de un polígono con área."""
    if len(areas_m2) < 2:
        return []
    total = sum(areas_m2)
    return [f"Área total calculada de {len(areas_m2)} polígonos: {total:.1f} m² ({total / 10000:.4f} ha). "
            f"{AVISO_DE_DATO}"]


def areas_calculadas_del_texto(archivo: str, texto: str) -> list[AreaCalculada]:
    """Las áreas que `kml_text_file` dejó escritas en el texto de un archivo, listas para la pantalla."""
    return [{"archivo": archivo, "poligono": m["nombre"], "area_m2": float(m["m2"]), "area_ha": float(m["ha"]),
             "perimetro_m": float(m["perimetro"]), "anillos_interiores": int(m["interiores"])}
            for m in _LINEA_DE_AREA.finditer(texto)]


def areas_de_los_adjuntos(adjuntos: Iterable[Mapping[str, Any]]) -> list[AreaCalculada]:
    """Las áreas calculadas de todos los adjuntos leídos (cada uno con `filename` y `text`)."""
    areas: list[AreaCalculada] = []
    for adjunto in adjuntos:
        if isinstance(adjunto, Mapping) and "Polígono «" in str(adjunto.get("text") or ""):
            areas.extend(areas_calculadas_del_texto(str(adjunto.get("filename") or ""), str(adjunto["text"])))
    return areas


_SIN_CANTIDAD = frozenset({"", "no indicado", "no indicada", "n/a", "na", "-", "—", "pendiente", "desconocido",
                           "desconocida", "por definir", "por completar"})


def sin_cantidad(valor: Any) -> bool:
    """Si una actividad no trae cantidad (vacía o «no indicado»): solo a esas se les puede poner un área calculada."""
    return str(valor or "").strip().casefold() in _SIN_CANTIDAD


def clave_de_area(indice: int, archivo: str, hectareas: float) -> str:
    """Identifica en un formulario un polígono (su posición en la lista) y comprueba que sigue siendo el mismo."""
    return f"{archivo}|{indice}|{hectareas:.4f}"


def clave_del_total(archivo: str, hectareas: float) -> str:
    """Identifica la suma de los polígonos de un archivo."""
    return f"{archivo}|*|{hectareas:.4f}"


def totales_por_archivo(areas: Sequence[Mapping[str, Any]]) -> list[tuple[str, int, float]]:
    """Por cada archivo con más de un polígono: (archivo, cuántos, hectáreas sumadas)."""
    cuentas: dict[str, list[float]] = {}
    for area in areas:
        if isinstance(area, Mapping):
            cuentas.setdefault(str(area.get("archivo") or ""), []).append(float(area.get("area_ha") or 0))
    return [(archivo, len(valores), sum(valores)) for archivo, valores in cuentas.items() if len(valores) > 1]


def area_elegida(areas: Sequence[Mapping[str, Any]], clave: str) -> tuple[str, float] | None:
    """El `(descripción, hectáreas)` que una clave del formulario señala hoy, o `None` si la lista ya cambió."""
    try:
        archivo, posicion, ha = clave.rsplit("|", 2)
        esperado = float(ha)
    except ValueError:
        return None
    if posicion == "*":
        for nombre, cuantos, total in totales_por_archivo(areas):
            if nombre == archivo and abs(total - esperado) < 0.00006:
                return f"suma de {cuantos} polígonos de {archivo}", total
        return None
    if not posicion.isdigit() or int(posicion) >= len(areas):
        return None
    area = areas[int(posicion)]
    if str(area.get("archivo") or "") != archivo or abs(float(area.get("area_ha") or 0) - esperado) >= 0.00006:
        return None
    return f"polígono «{area.get('poligono')}» de {archivo}", float(area["area_ha"])


def actividad_con_area(actividad: Mapping[str, Any], descripcion: str, hectareas: float) -> Actividad:
    """La actividad con el área calculada como cantidad (ha), `pendiente` y con una nota que dice de dónde viene."""
    texto = f"{hectareas:.4f}".rstrip("0").rstrip(".").replace(".", ",")
    nota = (f"Cantidad = área aproximada calculada por el programa del {descripcion}; no la declaró el cliente. "
            "Confírmala con el cliente.")
    previa = str(actividad.get("notes") or "").strip()
    return {**actividad, "quantity": texto, "unit": "ha", "estado": "pendiente",   # type: ignore[typeddict-item]
            "notes": f"{previa} {nota}".strip()}


# Solo lo que cubre TODA la franja del archivo: el vuelo. Un levantamiento a pie (estación total) cubre solo las
# zonas que el pliego nombra, no el polígono entero (U-101).
_SE_MIDE_POR_AREA = ("lidar", "fotogrametr", "aerofotogrametr", "ortofoto", "dron", "rpas")
_SE_MIDE_POR_LONGITUD_O_PUNTOS = ("nivelacion", "control", "inventario", "catastro", "red", "redes", "tuberia",
                                  "tuberias", "alcantarillado", "acueducto", "perfil", "estacion", "detalle",
                                  "detalles", "poligonal")


def _palabras(texto: Any) -> list[str]:
    plano = unicodedata.normalize("NFD", str(texto or "").casefold())
    return re.findall(r"[a-z0-9]+", "".join(c for c in plano if unicodedata.category(c) != "Mn"))


def se_mide_por_area(nombre: Any, servicio: Any = "") -> bool:
    """¿La actividad cubre toda la franja del archivo (vuelo LiDAR, fotogrametría, ortofoto, dron)? (U-101)

    Se mira solo el nombre y el servicio: «Levantamiento LiDAR y fotogrametría» sí; «Levantamiento de detalles con
    estación total», «Nivelación geométrica», «Control horizontal» o «Inventario de redes» no. Ante la duda, no.
    """
    palabras = _palabras(nombre) + _palabras(servicio)
    if any(p in _SE_MIDE_POR_LONGITUD_O_PUNTOS for p in palabras):
        return False
    return any(p.startswith(prefijo) for p in palabras for prefijo in _SE_MIDE_POR_AREA)


def poner_areas_solas(actividades: Any, areas: Any, diferencias: Any) -> list[str]:
    """Pone el área del único polígono del cliente como cantidad de las actividades de área que no la traen (U-101).

    Solo cuando el archivo es la ÚNICA fuente: un solo polígono y ninguna diferencia con lo que el correo o un documento
    escriben. Entra como cualquier área calculada (D-189): en ha, `pendiente` y con la nota de que es aproximada. Nunca
    cambia una cantidad escrita ni crea filas. Devuelve los nombres de las actividades que cambió.
    """
    if not isinstance(actividades, list) or not isinstance(areas, list) or len(areas) != 1 or diferencias:
        return []
    area = areas[0]
    hectareas = float(area.get("area_ha") or 0) if isinstance(area, Mapping) else 0.0
    if hectareas <= 0:
        return []
    descripcion = f"polígono «{area.get('poligono')}» de {area.get('archivo')} (la única fuente del área)"
    puestas: list[str] = []
    for posicion, fila in enumerate(actividades):
        if (isinstance(fila, Mapping) and sin_cantidad(fila.get("quantity"))
                and se_mide_por_area(fila.get("activity"), fila.get("servicio_tecnica"))):
            actividades[posicion] = actividad_con_area(fila, descripcion, hectareas)
            puestas.append(str(fila.get("activity") or ""))
    return puestas


def areas_solas_del_caso(analisis: Mapping[str, Any]) -> str:
    """Aplica `poner_areas_solas` a la tabla del caso y a su propuesta pendiente; devuelve la frase del historial.

    La propuesta se completa igual para que adoptarla no borre la cantidad. Sin cambios devuelve una cadena vacía.
    """
    areas, diferencias = analisis.get("areas_calculadas"), analisis.get("diferencias_de_area")
    puestas = poner_areas_solas(analisis.get("activities"), areas, diferencias)
    poner_areas_solas(analisis.get("pending_model_activities"), areas, diferencias)
    if not puestas or not isinstance(areas, list):
        return ""
    area = areas[0]
    return (f"Se puso el área de {area.get('archivo')} ({float(area.get('area_ha') or 0):.4f} ha) como cantidad de: "
            f"{'; '.join(puestas)}. Es aproximada y queda pendiente de confirmar.")
