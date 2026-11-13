"""Longitudes, áreas y perímetros geodésicos sobre WGS84 a partir de coordenadas, sin leer ningún archivo (T-125)."""

from __future__ import annotations

import math

_WGS84_A = 6_378_137.0
_WGS84_E2 = 0.00669437999014
_MAX_VERTICES_PARA_COMPROBAR_CRUCES = 5000   # en el peor caso la comprobación es cuadrática: más se calcula sin ella
_AVISO_SIN_CRUCES = f"no se comprobó si el contorno se cruza (más de {_MAX_VERTICES_PARA_COMPROBAR_CRUCES} vértices)"


def geodesic_length_m(coordinates: list[tuple[float, float]]) -> float:
    """Calcula longitud aproximada sobre WGS84 a partir de lon/lat, sin elevación."""
    radius_m = 6_371_008.8
    total = 0.0
    for (lon1, lat1), (lon2, lat2) in zip(coordinates, coordinates[1:], strict=False):
        phi1, phi2 = math.radians(lat1), math.radians(lat2)
        delta_phi = math.radians(lat2 - lat1)
        delta_lambda = math.radians(lon2 - lon1)
        haversine = math.sin(delta_phi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(delta_lambda / 2) ** 2
        total += 2 * radius_m * math.asin(min(1.0, math.sqrt(haversine)))
    return total


def valid_lonlat(longitude: float, latitude: float) -> bool:
    """Comprueba rangos y valores finitos de longitud y latitud."""
    return math.isfinite(longitude) and math.isfinite(latitude) and -180 <= longitude <= 180 and -90 <= latitude <= 90


def _q_autalica(latitud_rad: float) -> float:
    """La función q de la proyección cilíndrica de áreas iguales sobre el elipsoide (Snyder, ec. 3-12)."""
    e = math.sqrt(_WGS84_E2)
    seno = math.sin(latitud_rad)
    return (1 - _WGS84_E2) * (seno / (1 - _WGS84_E2 * seno * seno)
                              - math.log((1 - e * seno) / (1 + e * seno)) / (2 * e))


def _area_del_anillo_m2(anillo: list[tuple[float, float]]) -> float:
    """Área geodésica del anillo sobre WGS84: regla del trapecio en la proyección de áreas iguales, sin elevación."""
    qs = [_q_autalica(math.radians(lat)) for _, lat in anillo]
    total = 0.0
    for i, (lon1, _) in enumerate(anillo):
        j = (i + 1) % len(anillo)
        delta = math.radians(anillo[j][0] - lon1)
        delta = (delta + math.pi) % (2 * math.pi) - math.pi   # un anillo que cruza el antimeridiano no suma de más
        total += delta * (qs[i] + qs[j])
    return abs(total) * _WGS84_A ** 2 / 4


def _giro(a: tuple[float, float], b: tuple[float, float], c: tuple[float, float]) -> float:
    return (b[0] - a[0]) * (c[1] - a[1]) - (b[1] - a[1]) * (c[0] - a[0])


def _contorno_se_cruza(anillo: list[tuple[float, float]]) -> bool:
    """Si dos lados del anillo se cruzan de verdad (un roce en un vértice, como el de dos lados contiguos, no cuenta).

    Barre los lados ordenados por su x mínima y solo compara los que se solapan en x e y: en un polígono real son
    pocos, así que el costo es casi lineal (comparar todos con todos tardaba 1 s con 2000 vértices).
    """
    n = len(anillo)
    lados = sorted(
        (min(a[0], b[0]), max(a[0], b[0]), min(a[1], b[1]), max(a[1], b[1]), a, b)
        for a, b in ((anillo[i], anillo[(i + 1) % n]) for i in range(n))
    )
    for k, (_, max_x, min_y, max_y, a, b) in enumerate(lados):
        for m in range(k + 1, n):
            min_x2, _, min_y2, max_y2, c, d = lados[m]
            if min_x2 > max_x:
                break
            if max_y2 < min_y or min_y2 > max_y:
                continue
            if _giro(a, b, c) * _giro(a, b, d) < 0 and _giro(c, d, a) * _giro(c, d, b) < 0:
                return True
    return False


def _anillo_sin_cierre(anillo: list[tuple[float, float]]) -> list[tuple[float, float]]:
    return anillo[:-1] if len(anillo) > 1 and anillo[0] == anillo[-1] else anillo


def _motivo_de_anillo_invalido(anillo: list[tuple[float, float]], nombre: str) -> str:
    """Por qué un anillo no sirve para calcular un área; vacío si sirve."""
    if not all(valid_lonlat(lon, lat) for lon, lat in anillo):
        return f"{nombre} tiene coordenadas fuera de longitud/latitud"
    if len(set(anillo)) < 3:
        return f"{nombre} tiene menos de tres vértices distintos"
    if len(anillo) <= _MAX_VERTICES_PARA_COMPROBAR_CRUCES and _contorno_se_cruza(anillo):
        return f"{nombre} se cruza a sí mismo"
    return ""


def area_del_poligono(
    exterior: list[tuple[float, float]], interiores: list[list[tuple[float, float]]],
) -> tuple[dict[str, float | int] | None, str]:
    """Área (m²) y perímetro (m) del anillo exterior menos los interiores, o None si el polígono no tiene área (T-125).

    El texto es el motivo cuando no hay área, y una advertencia (casi siempre vacía) cuando la hay.
    """
    exterior = _anillo_sin_cierre(exterior)
    interiores = [_anillo_sin_cierre(anillo) for anillo in interiores]
    for nombre, anillo in [("el contorno", exterior), *[("un anillo interior", a) for a in interiores]]:
        if motivo := _motivo_de_anillo_invalido(anillo, nombre):
            return None, motivo
    area = _area_del_anillo_m2(exterior) - sum(_area_del_anillo_m2(anillo) for anillo in interiores)
    if area <= 0:
        return None, "el área resultó nula"
    resultado = {"area_m2": area, "perimetro_m": geodesic_length_m([*exterior, exterior[0]]),
                 "anillos_interiores": len(interiores)}
    sin_cruces = len(exterior) > _MAX_VERTICES_PARA_COMPROBAR_CRUCES
    return resultado, _AVISO_SIN_CRUCES if sin_cruces else ""
