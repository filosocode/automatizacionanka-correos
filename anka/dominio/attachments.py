"""Extracción local y trazable de texto desde adjuntos (sin llamar al modelo)."""

from __future__ import annotations

import html
from html.parser import HTMLParser
from functools import lru_cache
import csv
import json
import math
import posixpath
import re
import shutil
import subprocess
import tempfile
import zipfile
from io import BytesIO
from pathlib import Path
from xml.etree import ElementTree

MAX_ATTACHMENT_BYTES = 10 * 1024 * 1024
MAX_ATTACHMENT_TEXT = 60000
MAX_EVIDENCE_ITEMS = 60
MAX_EVIDENCE_CHARS = 700
MAX_ARCHIVE_FILES = 200
MAX_ARCHIVE_EXPANDED_BYTES = 40 * 1024 * 1024
MAX_SPREADSHEET_CELLS = 50000
MAX_OCR_PAGES = 200
MAX_OCR_IMAGES = 100
MAX_OCR_PIXELS = 40_000_000
TEXT_ATTACHMENT_EXTENSIONS = {
    ".csv", ".geojson", ".gpx", ".json", ".kml", ".md", ".plan", ".tab", ".tsv",
    ".txt", ".waypoints", ".wpml", ".xml",
}
_KML_GEOMETRIES = {"Point", "LineString", "Polygon", "Track", "Model"}


def _local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _child_text(element: ElementTree.Element, name: str) -> str:
    for child in element:
        if _local_name(child.tag) == name:
            return " ".join("".join(child.itertext()).split())
    return ""


def _coordinate_rows(element: ElementTree.Element) -> list[tuple[float, float, float | None]]:
    coordinates = []
    for node in element.iter():
        kind = _local_name(node.tag)
        if kind == "coord" and node.text:
            parts = node.text.split()
            if len(parts) >= 2:
                try:
                    altitude = float(parts[2]) if len(parts) >= 3 else None
                    coordinates.append((float(parts[0]), float(parts[1]), altitude))
                except ValueError:
                    pass
            continue
        if kind != "coordinates" or not node.text:
            continue
        for item in node.text.split():
            parts = item.split(",")
            if len(parts) < 2:
                continue
            try:
                longitude, latitude = float(parts[0]), float(parts[1])
                altitude = float(parts[2]) if len(parts) >= 3 and parts[2] else None
            except ValueError:
                continue
            coordinates.append((longitude, latitude, altitude))
    return coordinates


def _kml_coordinates(element: ElementTree.Element) -> list[tuple[float, float]]:
    return [(longitude, latitude) for longitude, latitude, _ in _coordinate_rows(element)]


def _geodesic_length_m(coordinates: list[tuple[float, float]]) -> float:
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


def _valid_lonlat(longitude: float, latitude: float) -> bool:
    return (
        math.isfinite(longitude) and math.isfinite(latitude)
        and -180 <= longitude <= 180 and -90 <= latitude <= 90
    )


def _kmz_text(content: bytes) -> str:
    with zipfile.ZipFile(BytesIO(content)) as archive:
        names = archive.namelist()
        infos = [item for item in archive.infolist() if not item.is_dir()]
        if len(infos) > MAX_ARCHIVE_FILES or sum(item.file_size for item in infos) > MAX_ARCHIVE_EXPANDED_BYTES:
            raise _Unreadable("KMZ excede el límite de archivos o tamaño expandido; requiere revisión manual")
        wpml_files = [name for name in names if name.casefold().endswith(".wpml")]
        if wpml_files:
            mission_parts = [_wpml_text(name, archive.read(name)) for name in wpml_files]
            mission_parts.extend(
                _kml_text_file(name, archive.read(name)) for name in names
                if name.casefold().endswith(".kml")
            )
            return "\n\n".join(mission_parts)
        kml_files = [name for name in names if name.casefold().endswith(".kml")]
        if not kml_files:
            raise ValueError("el KMZ no contiene un archivo KML")
        return "\n\n".join(_kml_text_file(name, archive.read(name)) for name in kml_files)


def _kml_text_file(name: str, content: bytes) -> str:  # noqa: C901 - resume geometrías, atributos y cobertura KML
    root = ElementTree.fromstring(content)
    placemarks = [node for node in root.iter() if _local_name(node.tag) == "Placemark"]
    lines = [f"Archivo KML: {name}", f"Elementos geográficos: {len(placemarks)}"]
    all_coordinates: list[tuple[float, float]] = []
    overlay_kinds = {"GroundOverlay", "ScreenOverlay", "NetworkLink"}
    overlays = [node for node in root.iter() if _local_name(node.tag) in overlay_kinds]
    if overlays:
        lines.append(
            "COBERTURA PARCIAL: overlays o vínculos KML requieren análisis especializado: "
            + ", ".join(_local_name(node.tag) for node in overlays)
        )
    for number, placemark in enumerate(placemarks, 1):
        label = _child_text(placemark, "name") or f"Elemento {number}"
        lines.append(f"Elemento: {label}")
        description = _child_text(placemark, "description")
        if description:
            clean = html.unescape(re.sub(r"<[^>]+>", " ", description))
            lines.append(f"Descripción: {' '.join(clean.split())[:500]}")
        attributes = []
        for node in placemark.iter():
            if _local_name(node.tag) in {"Data", "SimpleData"}:
                key = node.attrib.get("name", "")
                value = _child_text(node, "value") or " ".join("".join(node.itertext()).split())
                if key and value:
                    attributes.append(f"{key}={value}")
        if attributes:
            lines.append("Datos asociados: " + "; ".join(attributes)[:1000])
        geometries = [
            node for node in placemark.iter()
            if _local_name(node.tag) in _KML_GEOMETRIES
            and not any(_local_name(child.tag) in _KML_GEOMETRIES for child in node)
        ]
        if not geometries:
            geometries = [placemark]
        for geometry in geometries:
            kind = _local_name(geometry.tag)
            coordinate_rows = _coordinate_rows(geometry)
            coordinates = [(lon, lat) for lon, lat, _ in coordinate_rows]
            all_coordinates.extend(coordinates)
            lines.append(f"Geometría: {kind}; coordenadas: {len(coordinates)}")
            if coordinates:
                samples = "; ".join(f"{lon:.8f},{lat:.8f}" for lon, lat in coordinates[:8])
                lines.append(f"Muestras (longitud, latitud): {samples}")
                altitudes = [altitude for _, _, altitude in coordinate_rows if altitude is not None]
                if altitudes:
                    lines.append(
                        "Altitud registrada en coordenadas (sin normalizar modo vertical): "
                        f"mín. {min(altitudes):.3f}, máx. {max(altitudes):.3f}"
                    )
                if kind in {"LineString", "Track"} and len(coordinates) > 1 and all(
                    _valid_lonlat(lon, lat) for lon, lat in coordinates
                ):
                    lines.append(
                        "Longitud geodésica aproximada calculada desde coordenadas (sin elevación): "
                        f"{_geodesic_length_m(coordinates):.1f} m"
                    )
    if all_coordinates:
        west = min(point[0] for point in all_coordinates)
        south = min(point[1] for point in all_coordinates)
        east = max(point[0] for point in all_coordinates)
        north = max(point[1] for point in all_coordinates)
        lines.append(
            "Extensión geográfica KML, oeste/sur/este/norte (longitud/latitud): "
            f"{west:.8f}, {south:.8f}, {east:.8f}, {north:.8f}"
        )
        if any(abs(lon) > 180 or abs(lat) > 90 for lon, lat in all_coordinates):
            lines.append("Advertencia: hay coordenadas fuera de los rangos usuales de longitud/latitud.")
    times = [
        " ".join((node.text or "").split()) for node in root.iter()
        if _local_name(node.tag) == "when" and node.text
    ]
    if times:
        lines.append(f"Marcas de tiempo KML: {len(times)}; rango {min(times)} a {max(times)}")
    return "\n".join(lines)


def _wpml_text(name: str, content: bytes) -> str:
    """Lee la estructura legible de una misión DJI WPML empaquetada en KMZ."""
    try:
        root = ElementTree.fromstring(content)
    except ElementTree.ParseError as exc:
        raise _Unreadable(f"WPML XML inválido en {name}: {exc}") from None
    lines = [f"Archivo de misión DJI WPML: {name}"]
    for node in root.iter():
        local = _local_name(node.tag)
        if local in {"missionConfig", "Folder"}:
            values = []
            for child in node:
                child_name = _local_name(child.tag)
                value = " ".join("".join(child.itertext()).split())
                if child_name != "Placemark" and value and len(value) <= 500:
                    values.append(f"{child_name}={value}")
            if values:
                lines.append(f"Configuración {local}: " + "; ".join(values[:50]))
    placemarks = [node for node in root.iter() if _local_name(node.tag) == "Placemark"]
    lines.append(f"Waypoints/rutas definidos: {len(placemarks)}")
    all_coordinates = []
    for index, placemark in enumerate(placemarks, 1):
        coordinates = _coordinate_rows(placemark)
        all_coordinates.extend((lon, lat) for lon, lat, _ in coordinates)
        scalars = []
        for node in placemark.iter():
            value = (node.text or "").strip()
            if value and len(value) <= 180 and _local_name(node.tag) not in {"coordinates", "coord"}:
                scalars.append(f"{_local_name(node.tag)}={value}")
        lines.append(f"Waypoint {index}: " + "; ".join(scalars[:40]))
        for lon, lat, altitude in coordinates[:3]:
            suffix = f", altitud={altitude:g}" if altitude is not None else ""
            lines.append(f"  Coordenada lon/lat={lon:.8f},{lat:.8f}{suffix}")
    if all_coordinates:
        lines.append(f"Puntos con coordenadas: {len(all_coordinates)}")
        lines.append(
            "Extensión calculada lon/lat: "
            f"{min(x for x, _ in all_coordinates):.8f}, {min(y for _, y in all_coordinates):.8f}, "
            f"{max(x for x, _ in all_coordinates):.8f}, {max(y for _, y in all_coordinates):.8f}"
        )
        lines.append(
            "Longitud aproximada de la secuencia WPML (sin elevación, puede incluir segmentos desconectados): "
            f"{_geodesic_length_m(all_coordinates):.1f} m"
        )
    return "\n".join(lines)


def _gpx_text(content: bytes) -> str:  # noqa: C901 - waypoints, rutas, segmentos y metadatos GPX
    """Resume GPX sin perder su distinción entre waypoints, rutas y tracks/segmentos."""
    try:
        root = ElementTree.fromstring(content)
    except ElementTree.ParseError as exc:
        raise _Unreadable(f"GPX XML inválido: {exc}") from None
    points_all: list[tuple[float, float, float | None, str]] = []
    lines = [
        f"Formato GPX; versión: {root.attrib.get('version', 'no indicada')}; "
        f"creador: {root.attrib.get('creator', 'no indicado')}"
    ]

    def point_rows(nodes, kind: str, label: str) -> list[tuple[float, float, float | None, str]]:
        result = []
        for point in nodes:
            try:
                lon, lat = float(point.attrib["lon"]), float(point.attrib["lat"])
            except (KeyError, ValueError):
                lines.append(f"COBERTURA PARCIAL: coordenada GPX inválida en {label or kind}")
                continue
            if not _valid_lonlat(lon, lat):
                lines.append(f"COBERTURA PARCIAL: coordenada GPX fuera de rango en {label or kind}")
                continue
            elevation_text = _child_text(point, "ele")
            try:
                elevation = float(elevation_text) if elevation_text else None
            except ValueError:
                elevation = None
            name_value = _child_text(point, "name") or label
            when = _child_text(point, "time")
            result.append((lon, lat, elevation, when))
            if kind == "waypoint":
                detail = f"; altitud={elevation:g} m" if elevation is not None else ""
                if when:
                    detail += f"; tiempo={when}"
                lines.append(f"Waypoint {name_value}: lat={lat:.8f}, lon={lon:.8f}{detail}")
        return result

    waypoints = [node for node in root if _local_name(node.tag) == "wpt"]
    waypoint_rows = point_rows(waypoints, "waypoint", "")
    lines.append(f"Waypoints independientes: {len(waypoint_rows)}")
    points_all.extend(waypoint_rows)
    route_count = track_count = 0
    for route in (node for node in root if _local_name(node.tag) == "rte"):
        route_count += 1
        route_name = _child_text(route, "name") or f"Ruta {route_count}"
        rows = point_rows((n for n in route if _local_name(n.tag) == "rtept"), "route", route_name)
        points_all.extend(rows)
        coords = [(lon, lat) for lon, lat, _, _ in rows]
        lines.append(f"Ruta {route_name}: {len(rows)} puntos; longitud calculada {_geodesic_length_m(coords):.1f} m")
        for index, (lon, lat, altitude, _) in enumerate(rows[:8], 1):
            suffix = f"; altitud={altitude:g} m" if altitude is not None else ""
            lines.append(f"  Punto ruta {index}: lat={lat:.8f}, lon={lon:.8f}{suffix}")
    for track in (node for node in root if _local_name(node.tag) == "trk"):
        track_count += 1
        track_name = _child_text(track, "name") or f"Track {track_count}"
        segment_lengths = []
        track_rows = []
        for segment_number, segment in enumerate(
            (n for n in track if _local_name(n.tag) == "trkseg"), 1
        ):
            rows = point_rows((n for n in segment if _local_name(n.tag) == "trkpt"), "track", track_name)
            track_rows.extend(rows)
            segment_coordinates = [(lon, lat) for lon, lat, _, _ in rows]
            segment_lengths.append(_geodesic_length_m(segment_coordinates))
            lines.append(
                f"  Segmento {segment_number}: {len(rows)} puntos; "
                f"{segment_lengths[-1]:.1f} m"
            )
        points_all.extend(track_rows)
        lines.append(
            f"Track {track_name}: {len(track_rows)} puntos; {len(segment_lengths)} segmentos; "
            f"longitud calculada {sum(segment_lengths):.1f} m"
        )
        timestamps = [when for _, _, _, when in track_rows if when]
        if timestamps:
            lines.append(f"  Rango temporal registrado: {min(timestamps)} a {max(timestamps)}")
        elevations = [elev for _, _, elev, _ in track_rows if elev is not None]
        if elevations:
            lines.append(f"  Rango de elevación registrado: {min(elevations):.2f} a {max(elevations):.2f} m")
    lines.append(f"Rutas: {route_count}; tracks: {track_count}")
    coords_all = [(lon, lat) for lon, lat, _, _ in points_all]
    if coords_all:
        lines.append(
            "Extensión calculada lon/lat: "
            f"{min(x for x, _ in coords_all):.8f}, {min(y for _, y in coords_all):.8f}, "
            f"{max(x for x, _ in coords_all):.8f}, {max(y for _, y in coords_all):.8f}"
        )
    return "\n".join(lines)


def _qgc_waypoints_text(content: bytes) -> str:
    """Lee el formato tabular QGroundControl WPL; conserva comandos por su índice numérico."""
    raw = content.decode("utf-8-sig", errors="replace")
    lines = [line.strip() for line in raw.splitlines() if line.strip()]
    if not lines or not lines[0].startswith("QGC WPL "):
        raise _Unreadable("archivo .waypoints no coincide con el formato QGroundControl WPL")
    output = [f"Plan QGroundControl: {lines[0]}"]
    coordinates = []
    items = 0
    for raw_line in lines[1:]:
        columns = raw_line.split("\t")
        if len(columns) < 12:
            columns = raw_line.split()
        if len(columns) < 12:
            output.append(f"FILA WPL NO INTERPRETADA: {raw_line[:300]}")
            continue
        items += 1
        seq, current, frame, command = columns[:4]
        try:
            x, y, altitude = float(columns[8]), float(columns[9]), float(columns[10])
        except ValueError:
            x = y = altitude = math.nan
        try:
            frame_code = int(frame)
        except ValueError:
            frame_code = -1
        is_global_frame = frame_code in {0, 3, 5, 6, 10, 11}
        lat, lon = (x, y) if is_global_frame else (math.nan, math.nan)
        coordinate_label = (
            f"lat={lat}; lon={lon}" if is_global_frame
            else f"x={x}; y={y} (marco local/no geográfico)"
        )
        output.append(
            f"Elemento {seq}: current={current}; frame={frame}; command={command}; "
            f"{coordinate_label}; "
            f"altitud={altitude}; autocontinue={columns[11]}"
        )
        if math.isfinite(lat) and math.isfinite(lon) and -90 <= lat <= 90 and -180 <= lon <= 180:
            coordinates.append((lon, lat))
    output.append(f"Elementos WPL: {items}; puntos con coordenadas geográficas: {len(coordinates)}")
    if coordinates:
        output.append(f"Longitud calculada entre puntos consecutivos: {_geodesic_length_m(coordinates):.1f} m")
        output.append(
            "Extensión calculada lon/lat: "
            f"{min(x for x, _ in coordinates):.8f}, {min(y for _, y in coordinates):.8f}, "
            f"{max(x for x, _ in coordinates):.8f}, {max(y for _, y in coordinates):.8f}"
        )
    return "\n".join(output)


def _qgc_plan_text(content: bytes) -> str:  # noqa: C901 - mission items, geofence y waypoints QGC
    """Lee los ítems de misión exportados por QGroundControl (.plan)."""
    try:
        document = json.loads(content.decode("utf-8-sig"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise _Unreadable(f"plan QGroundControl inválido: {exc}") from None
    if not isinstance(document, dict):
        raise _Unreadable("plan QGroundControl debe contener un objeto JSON")
    mission = document.get("mission", {})
    items = mission.get("items", []) if isinstance(mission, dict) else []
    if not isinstance(items, list):
        raise _Unreadable("plan QGroundControl sin lista de elementos de misión")
    output = [
        f"Plan QGroundControl: versión {document.get('version', 'no indicada')}; "
        f"vehículo {mission.get('vehicleType', 'no indicado')}; firmware {mission.get('firmwareType', 'no indicado')}; "
        f"elementos {len(items)}"
    ]
    home = mission.get("plannedHomePosition")
    if isinstance(home, list) and len(home) >= 2:
        output.append(f"Posición home declarada: {home[:3]}")
    for section in ("geoFence", "rallyPoints"):
        if document.get(section):
            serialized = json.dumps(document[section], ensure_ascii=False, separators=(",", ":"))
            output.append(f"{section} declarado en el plan: {serialized[:3000]}")
    coordinates = []
    for index, item in enumerate(items, 1):
        if not isinstance(item, dict):
            output.append(f"Elemento {index}: estructura no reconocida")
            continue
        params = item.get("params", [])
        coordinate = None
        if isinstance(params, list) and len(params) >= 7:
            try:
                lat, lon, altitude = float(params[4]), float(params[5]), float(params[6])
                if -90 <= lat <= 90 and -180 <= lon <= 180:
                    coordinate = (lon, lat, altitude)
            except (TypeError, ValueError):
                pass
        output.append(
            f"Elemento {index}: tipo={item.get('type', 'no indicado')}; "
            f"comando={item.get('command', 'no indicado')}; frame={item.get('frame', 'no indicado')}; "
            f"altitud_relativa={item.get('altitude', 'no indicada')}"
        )
        if coordinate:
            lon, lat, altitude = coordinate
            coordinates.append((lon, lat))
            output.append(f"  Coordenada lat/lon={lat:.8f},{lon:.8f}; altitud parámetro={altitude:g}")
        if item.get("type") == "ComplexItem":
            output.append(
                "COBERTURA PARCIAL: elemento complejo QGroundControl requiere interpretación de su subestructura"
            )
        elif item.get("type") not in {"ComplexItem", "SimpleItem"}:
            output.append("COBERTURA PARCIAL: tipo de elemento QGroundControl no reconocido")
    if coordinates:
        length_m = _geodesic_length_m(coordinates)
        output.append(f"Puntos geográficos: {len(coordinates)}; longitud calculada {length_m:.1f} m")
        output.append(
            "Extensión calculada lon/lat: "
            f"{min(x for x, _ in coordinates):.8f}, {min(y for _, y in coordinates):.8f}, "
            f"{max(x for x, _ in coordinates):.8f}, {max(y for _, y in coordinates):.8f}"
        )
    return "\n".join(output)


def _geojson_text(content: bytes) -> str:  # noqa: C901 - conserva features, geometrías y métricas de línea
    """Resume Feature/FeatureCollection GeoJSON sin perder propiedades ni tipos geométricos."""
    try:
        document = json.loads(content.decode("utf-8-sig"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise _Unreadable(f"GeoJSON inválido: {exc}") from None
    if not isinstance(document, dict):
        raise _Unreadable("GeoJSON debe contener un objeto JSON")
    features = document.get("features", [])
    if document.get("type") == "Feature":
        features = [document]
    if not isinstance(features, list):
        raise _Unreadable("GeoJSON sin lista de features")
    output = [f"GeoJSON {document.get('type', 'objeto')}; features: {len(features)}"]
    all_coords: list[tuple[float, float]] = []

    def visit_coordinates(value) -> None:
        if isinstance(value, list) and len(value) >= 2 and all(
            isinstance(coordinate, (int, float)) for coordinate in value[:2]
        ):
            lon, lat = float(value[0]), float(value[1])
            all_coords.append((lon, lat))
        elif isinstance(value, list):
            for child in value:
                visit_coordinates(child)

    def geometry_lines(geometry) -> list[list[tuple[float, float]]]:
        if not isinstance(geometry, dict):
            return []
        kind = geometry.get("type")
        coordinates = geometry.get("coordinates")
        if kind == "LineString" and isinstance(coordinates, list):
            return [[(float(point[0]), float(point[1])) for point in coordinates
                     if isinstance(point, list) and len(point) >= 2
                     and all(isinstance(number, (int, float)) for number in point[:2])]]
        if kind == "MultiLineString" and isinstance(coordinates, list):
            return [[(float(point[0]), float(point[1])) for point in line
                     if isinstance(point, list) and len(point) >= 2
                     and all(isinstance(number, (int, float)) for number in point[:2])]
                    for line in coordinates if isinstance(line, list)]
        if kind == "GeometryCollection":
            return [line for child in geometry.get("geometries", []) for line in geometry_lines(child)]
        return []

    for index, feature in enumerate(features, 1):
        if not isinstance(feature, dict):
            output.append(f"Feature {index}: estructura no reconocida")
            continue
        geometry = feature.get("geometry") or {}
        coordinates = geometry.get("coordinates") if isinstance(geometry, dict) else None
        before = len(all_coords)
        visit_coordinates(coordinates)
        if isinstance(geometry, dict) and geometry.get("type") == "GeometryCollection":
            for child_geometry in geometry.get("geometries", []):
                visit_coordinates(child_geometry.get("coordinates") if isinstance(child_geometry, dict) else None)
        properties = feature.get("properties") or {}
        prop_text = (
            "; ".join(f"{key}={value}" for key, value in properties.items())
            if isinstance(properties, dict) else str(properties)
        )
        output.append(
            f"Feature {index}: geometría={geometry.get('type', 'no indicada')}; "
            f"coordenadas={len(all_coords) - before}; propiedades={prop_text[:1000]}"
        )
        if coordinates is not None and len(all_coords) - before == 0:
            output.append(f"LECTURA PARCIAL: coordenadas de feature {index} no reconocidas")
        lines = geometry_lines(geometry)
        if lines and all(_valid_lonlat(lon, lat) for line in lines for lon, lat in line):
            lengths = [_geodesic_length_m(line) for line in lines]
            output.append(
                f"Longitud geodésica aproximada calculada de líneas en feature {index}: "
                f"{sum(lengths):.1f} m (no equivale a longitud declarada por el remitente)"
            )
        elif lines:
            output.append(
                f"COBERTURA PARCIAL: feature {index} sale del rango lon/lat; "
                "revisar el CRS antes de calcular longitudes"
            )
    if all_coords:
        output.append(f"Vértices con lon/lat interpretables: {len(all_coords)}")
        if any(not _valid_lonlat(lon, lat) for lon, lat in all_coords):
            output.append("COBERTURA PARCIAL: coordenadas fuera de rangos lon/lat usuales; posible CRS proyectado")
        output.append(
            "Extensión numérica de coordenadas (se asumen lon/lat GeoJSON): "
            f"{min(x for x, _ in all_coords):.8f}, {min(y for _, y in all_coords):.8f}, "
            f"{max(x for x, _ in all_coords):.8f}, {max(y for _, y in all_coords):.8f}"
        )
    return "\n".join(output)


def _csv_text(content: bytes) -> str:  # noqa: C901 - detecta tabla ordinaria o misión georreferenciada
    """Conserva tabla CSV; reconoce planes de waypoints solo si hay columnas lat/lon explícitas."""
    try:
        raw = content.decode("utf-8-sig")
    except UnicodeDecodeError:
        raw = content.decode("latin-1")
    sample = raw[:4096]
    try:
        delimiter = csv.Sniffer().sniff(sample, delimiters=",;\t|").delimiter
    except csv.Error:
        delimiter = ","
    rows = list(csv.reader(raw.splitlines(), delimiter=delimiter))
    if not rows:
        return ""
    header = [cell.strip() for cell in rows[0]]
    normalized = [re.sub(r"[^a-z0-9]+", "", cell.casefold()) for cell in header]
    aliases = {
        "lat": {"lat", "latitude", "latitud", "latituddeg", "latitudgrados"},
        "lon": {"lon", "lng", "long", "longitude", "longitud", "longituddeg"},
        "seq": {"seq", "sequence", "waypoint", "waypointnumber", "order", "orden"},
        "alt": {"alt", "altitude", "elevation", "elev", "height", "altura"},
    }
    indices = {
        key: next((i for i, name in enumerate(normalized) if name in accepted), None)
        for key, accepted in aliases.items()
    }
    is_coordinate_table = indices["lat"] is not None and indices["lon"] is not None
    output = [f"CSV tabular; separador {delimiter!r}; columnas: {len(header)}"]
    data_rows = rows[1:]
    if is_coordinate_table:
        output.append("Tabla de coordenadas/waypoints con latitud y longitud explícitas.")
        points = []
        for row_number, row in enumerate(data_rows, 2):
            if row_number > MAX_SPREADSHEET_CELLS:
                output.append(f"LECTURA PARCIAL: se superó el límite de {MAX_SPREADSHEET_CELLS} filas CSV")
                break
            try:
                latitude = float(row[indices["lat"]])
                longitude = float(row[indices["lon"]])
            except (IndexError, TypeError, ValueError):
                output.append(f"FILA {row_number}: coordenadas vacías o inválidas")
                continue
            if not _valid_lonlat(longitude, latitude):
                output.append(
                    f"COBERTURA PARCIAL: fila {row_number} fuera de rango lon/lat; "
                    "revisar si son coordenadas proyectadas"
                )
                continue
            altitude = None
            if indices["alt"] is not None and indices["alt"] < len(row):
                try:
                    altitude = float(row[indices["alt"]])
                except ValueError:
                    pass
            sequence = (
                row[indices["seq"]]
                if indices["seq"] is not None and indices["seq"] < len(row)
                else str(row_number - 1)
            )
            points.append((sequence, longitude, latitude, altitude, row))
        if indices["seq"] is not None:
            def order_key(item):
                try:
                    return (0, float(item[0]))
                except ValueError:
                    return (1, item[0].casefold())
            points.sort(key=order_key)
        output.append(f"Waypoints con coordenadas válidas: {len(points)}")
        for sequence, longitude, latitude, _altitude, row in points:
            values = "; ".join(
                f"{header[index] if index < len(header) else f'columna {index + 1}'}={value}"
                for index, value in enumerate(row) if value
            )
            output.append(f"Waypoint {sequence}: lat={latitude:.8f}; lon={longitude:.8f}; {values}")
        if points:
            output.append(
                "Extensión calculada lon/lat: "
                f"{min(point[1] for point in points):.8f}, {min(point[2] for point in points):.8f}, "
                f"{max(point[1] for point in points):.8f}, {max(point[2] for point in points):.8f}"
            )
            if indices["seq"] is not None:
                path = [(point[1], point[2]) for point in points]
                output.append(f"Longitud geodésica aproximada de secuencia: {_geodesic_length_m(path):.1f} m")
            elevations = [point[3] for point in points if point[3] is not None]
            if elevations:
                output.append(
                    "Rango de valores de altitud/elevación en columna: "
                    f"{min(elevations):g} a {max(elevations):g}"
                )
    else:
        output.append("Fila 1 (encabezados): " + " | ".join(header))
        cells_seen = len(header)
        for row_number, row in enumerate(data_rows, 2):
            cells_seen += len(row)
            if cells_seen > MAX_SPREADSHEET_CELLS:
                output.append(f"LECTURA PARCIAL: se superó el límite de {MAX_SPREADSHEET_CELLS} celdas CSV")
                break
            values = [
                f"{header[index] if index < len(header) else f'columna {index + 1}'}={value}"
                for index, value in enumerate(row) if value
            ]
            if values:
                output.append(f"Fila {row_number}: " + " | ".join(values))
    return "\n".join(output)


class _Unreadable(Exception):
    """El adjunto no se puede leer; el motivo se muestra en el dashboard."""


def _plain_text(content: bytes) -> str:
    try:
        text = content.decode("utf-8-sig")
    except UnicodeDecodeError:
        text = content.decode("latin-1")
    lines = text.splitlines()
    return "\n".join(f"Línea {number}: {line}" for number, line in enumerate(lines, 1))


class _VisibleHtml(HTMLParser):
    """Conserva texto y límites básicos de bloques; descarta etiquetas activas y estilos."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.hidden_depth = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in {"script", "style", "noscript", "svg"}:
            self.hidden_depth += 1
        elif tag in {"p", "div", "br", "li", "tr", "h1", "h2", "h3", "h4"} and not self.hidden_depth:
            self.parts.append("\n")
        if tag == "img":
            alt = dict(attrs).get("alt") or ""
            self.parts.append(f"\n[Imagen: {alt or 'sin texto alternativo; requiere inspección visual'}]\n")

    def handle_endtag(self, tag: str) -> None:
        if tag in {"script", "style", "noscript", "svg"} and self.hidden_depth:
            self.hidden_depth -= 1
        elif tag in {"p", "div", "li", "tr", "h1", "h2", "h3", "h4"} and not self.hidden_depth:
            self.parts.append("\n")

    def handle_data(self, data: str) -> None:
        if not self.hidden_depth and data.strip():
            self.parts.append(data.strip())

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == "img":
            alt = dict(attrs).get("alt") or ""
            self.parts.append(f"\n[Imagen: {alt or 'sin texto alternativo; requiere inspección visual'}]\n")


def _html_text(content: bytes) -> str:
    parser = _VisibleHtml()
    parser.feed(content.decode("utf-8-sig", errors="replace"))
    lines = [line.strip() for line in " ".join(parser.parts).splitlines() if line.strip()]
    return _plain_text("\n".join(lines).encode("utf-8"))


def _rtf_text(content: bytes) -> str:
    raw = content.decode("latin-1")
    has_images = r"\pict" in raw
    raw = re.sub(
        r"\\'[0-9a-fA-F]{2}",
        lambda m: bytes([int(m.group()[2:], 16)]).decode("cp1252", errors="replace"), raw,
    )
    raw = re.sub(r"\\u(-?\d+)\??", lambda m: chr(int(m.group(1)) % 65536), raw)
    raw = re.sub(r"\\(par|line|tab)\b", lambda m: "\n" if m.group(1) != "tab" else "\t", raw)
    raw = re.sub(r"\\[a-zA-Z]+-?\d* ?", "", raw)
    raw = raw.replace("{", "").replace("}", "")
    if has_images:
        raw += "\nCONTENIDO GRÁFICO RTF NO INSPECCIONADO"
    return _plain_text(raw.encode("utf-8"))


@lru_cache(maxsize=1)
def _ocr_engine():
    try:
        from rapidocr_onnxruntime import RapidOCR
    except ImportError:
        raise _Unreadable("falta instalar rapidocr-onnxruntime para leer imágenes y PDF escaneados") from None
    return RapidOCR()


def _ocr_image(content: bytes) -> str:
    try:
        from PIL import Image, ImageOps
        import numpy as np
    except ImportError:
        raise _Unreadable("falta instalar Pillow y numpy para leer imágenes") from None
    try:
        with Image.open(BytesIO(content)) as source:
            if source.width * source.height > MAX_OCR_PIXELS:
                raise _Unreadable("imagen excede el límite de píxeles para OCR; requiere revisión manual")
            image = ImageOps.exif_transpose(source).convert("RGB")
            return _ocr_pil_image(image, np)
    except _Unreadable:
        raise
    except Exception as exc:
        raise _Unreadable(f"no se pudo ejecutar OCR sobre la imagen: {exc}") from None


def _ocr_pil_image(image, numpy_module=None) -> str:
    """Reconoce una imagen PIL ya decodificada, como las páginas renderizadas de PDFium."""
    if image.width * image.height > MAX_OCR_PIXELS:
        raise _Unreadable("imagen excede el límite de píxeles para OCR; requiere revisión manual")
    if numpy_module is None:
        try:
            import numpy as numpy_module
        except ImportError:
            raise _Unreadable("falta instalar numpy para leer imágenes") from None
    try:
        result, _ = _ocr_engine()(numpy_module.asarray(image.convert("RGB")))
    except Exception as exc:
        raise _Unreadable(f"no se pudo ejecutar OCR sobre la imagen: {exc}") from None
    lines = []
    if result:
        for entry in result:
            if len(entry) >= 3 and float(entry[2]) >= 0.25 and str(entry[1]).strip():
                lines.append(str(entry[1]).strip())
    if not lines:
        raise _Unreadable("OCR no detectó texto legible en la imagen; requiere inspección visual")
    return "\n".join(lines)


def _image_text(filename: str, content: bytes) -> str:
    return f"[Imagen: {Path(filename).name}; texto OCR]\n{_ocr_image(content)}"


def _office_legacy_text(extension: str, content: bytes) -> str:
    converter = shutil.which("soffice") or shutil.which("libreoffice")
    if not converter and extension == ".doc":
        converter = shutil.which("antiword")
    if not converter:
        raise _Unreadable(
            f"formato Office antiguo ({extension}); instale LibreOffice para convertirlo a texto"
        )
    with tempfile.TemporaryDirectory(prefix="anka-adjunto-") as directory:
        source = Path(directory) / f"documento{extension}"
        source.write_bytes(content)
        if Path(converter).name.casefold().startswith("antiword"):
            result = subprocess.run([converter, str(source)], capture_output=True, timeout=60, check=False)
            if result.returncode:
                raise _Unreadable("antiword no pudo convertir el documento Word antiguo")
            return _plain_text(result.stdout)
        result = subprocess.run(
            [converter, "--headless", "--convert-to", "txt:Text", "--outdir", directory, str(source)],
            capture_output=True, timeout=120, check=False,
        )
        converted = source.with_suffix(".txt")
        if result.returncode or not converted.exists():
            raise _Unreadable(f"LibreOffice no pudo convertir {extension} a texto")
        return _plain_text(converted.read_bytes())


def _xls_text(content: bytes) -> str:
    try:
        import xlrd
    except ImportError:
        raise _Unreadable("falta instalar xlrd para leer libros .xls") from None
    try:
        workbook = xlrd.open_workbook(file_contents=content, on_demand=True)
    except Exception as exc:
        raise _Unreadable(f"no se pudo abrir el libro .xls: {exc}") from None
    lines = []
    cells_seen = 0
    for sheet in workbook.sheets():
        lines.append(f"Hoja: {sheet.name}; filas: {sheet.nrows}; columnas: {sheet.ncols}")
        for row_number in range(sheet.nrows):
            values = []
            for column in range(sheet.ncols):
                cells_seen += 1
                if cells_seen > MAX_SPREADSHEET_CELLS:
                    lines.append(f"LECTURA PARCIAL: se superó el límite de {MAX_SPREADSHEET_CELLS} celdas")
                    workbook.release_resources()
                    return "\n".join(lines)
                value = sheet.cell_value(row_number, column)
                if value != "":
                    values.append(f"fila {row_number + 1}, columna {column + 1}={value}")
            if values:
                lines.append(" | ".join(values))
    workbook.release_resources()
    return "\n".join(lines)


def _shapefile_zip_text(content: bytes) -> str:
    try:
        import shapefile
    except ImportError:
        raise _Unreadable("falta instalar pyshp para leer shapefiles") from None
    with zipfile.ZipFile(BytesIO(content)) as archive:
        groups: dict[str, dict[str, str]] = {}
        for name in archive.namelist():
            suffix = Path(name).suffix.casefold()
            if suffix in {".shp", ".shx", ".dbf", ".prj", ".cpg"}:
                groups.setdefault(str(Path(name).with_suffix("")), {})[suffix] = name
        fragments = []
        for layer, files in groups.items():
            if not {".shp", ".shx", ".dbf"}.issubset(files):
                continue
            reader = shapefile.Reader(
                shp=BytesIO(archive.read(files[".shp"])),
                shx=BytesIO(archive.read(files[".shx"])),
                dbf=BytesIO(archive.read(files[".dbf"])),
            )
            fields = [field[0] for field in reader.fields[1:]]
            fragments.append(
                f"Capa shapefile: {layer}; geometría: {reader.shapeTypeName}; "
                f"elementos: {len(reader)}; campos: {', '.join(fields)}"
            )
            if reader.bbox:
                fragments.append("Extensión XY (mín X, mín Y, máx X, máx Y): " + ", ".join(map(str, reader.bbox)))
            for index, record in enumerate(reader.iterRecords()):
                if index >= MAX_EVIDENCE_ITEMS:
                    fragments.append(f"LECTURA PARCIAL: se muestran {MAX_EVIDENCE_ITEMS} registros de atributos")
                    break
                fragments.append(f"Registro {index + 1}: " + "; ".join(
                    f"{field}={value}" for field, value in zip(fields, record, strict=True)
                ))
            if ".prj" in files:
                projection = archive.read(files[".prj"]).decode("utf-8", errors="replace")
                fragments.append("Sistema de coordenadas (PRJ): " + projection)
            reader.close()
        if not fragments:
            raise _Unreadable("ZIP sin shapefile completo; se requieren archivos .shp, .shx y .dbf")
        return "\n".join(fragments)


def _pptx_text(content: bytes) -> str:
    with zipfile.ZipFile(BytesIO(content)) as presentation:
        slide_names = sorted(
            (name for name in presentation.namelist()
             if re.fullmatch(r"ppt/slides/slide\d+\.xml", name)),
            key=lambda name: int(re.search(r"slide(\d+)", name).group(1)),
        )
        if not slide_names:
            raise _Unreadable("presentación sin diapositivas legibles")
        lines = []
        for slide_number, name in enumerate(slide_names, 1):
            root = ElementTree.fromstring(presentation.read(name))
            paragraphs = []
            for node in root.iter():
                if _local_name(node.tag) == "p":
                    text = "".join(
                        child.text or "" for child in node.iter() if _local_name(child.tag) == "t"
                    ).strip()
                    if text:
                        paragraphs.append(text)
            lines.append(f"[Diapositiva {slide_number}]\n" + "\n".join(paragraphs))
        images = [name for name in presentation.namelist() if name.startswith("ppt/media/")]
        _append_archive_image_ocr(presentation, images, lines)
        notes = [name for name in presentation.namelist()
                 if re.fullmatch(r"ppt/notesSlides/notesSlide\d+\.xml", name)]
        for note_name in notes:
            note_root = ElementTree.fromstring(presentation.read(note_name))
            note_text = ["".join(node.itertext()).strip() for node in note_root.iter()
                         if _local_name(node.tag) == "t" and "".join(node.itertext()).strip()]
            if note_text:
                lines.append(f"Notas del presentador ({note_name}): " + " | ".join(note_text))
        diagrams = [
            name for name in presentation.namelist()
            if name.startswith("ppt/diagrams/") and name.endswith(".xml")
        ]
        for diagram_name in diagrams:
            diagram_root = ElementTree.fromstring(presentation.read(diagram_name))
            diagram_text = ["".join(node.itertext()).strip() for node in diagram_root.iter()
                            if _local_name(node.tag) == "t" and "".join(node.itertext()).strip()]
            if diagram_text:
                lines.append(f"COBERTURA VISUAL PARCIAL: texto SmartArt ({diagram_name}): " + " | ".join(diagram_text))
        charts = [name for name in presentation.namelist() if name.startswith("ppt/charts/")]
        if charts:
            lines.append("CONTENIDO DE GRÁFICOS NO EXTRAÍDO: " + ", ".join(charts))
        return "\n".join(lines)


def _odt_text(content: bytes) -> str:
    with zipfile.ZipFile(BytesIO(content)) as document:
        root = ElementTree.fromstring(document.read("content.xml"))
    lines = []
    for node in root.iter():
        if _local_name(node.tag) in {"p", "h"}:
            text = " ".join("".join(node.itertext()).split())
            if text:
                lines.append(text)
    with zipfile.ZipFile(BytesIO(content)) as document:
        images = [name for name in document.namelist() if name.startswith("Pictures/")]
        _append_archive_image_ocr(document, images, lines)
    return _plain_text("\n".join(lines).encode("utf-8"))


def _ods_text(content: bytes) -> str:
    with zipfile.ZipFile(BytesIO(content)) as document:
        root = ElementTree.fromstring(document.read("content.xml"))
    lines = []
    for table_number, table in enumerate((n for n in root.iter() if _local_name(n.tag) == "table"), 1):
        name = next(
            (value for key, value in table.attrib.items() if _local_name(key) == "name"),
            f"Hoja {table_number}",
        )
        lines.append(f"Hoja: {name}")
        for row_number, row in enumerate((n for n in table.iter() if _local_name(n.tag) == "table-row"), 1):
            values = []
            column_number = 0
            for cell in (n for n in row if _local_name(n.tag) in {"table-cell", "covered-table-cell"}):
                repeat = next((int(v) for k, v in cell.attrib.items()
                               if _local_name(k) == "number-columns-repeated"), 1)
                value = " ".join("".join(p.itertext()).strip() for p in cell.iter()
                                 if _local_name(p.tag) in {"p", "h"}).strip()
                formula = next((v for k, v in cell.attrib.items() if _local_name(k) == "formula"), "")
                if formula:
                    if not value:
                        lines.append(
                            f"FÓRMULA SIN VALOR CALCULADO: hoja {name}, "
                            f"fila {row_number}, columna {column_number + 1}"
                        )
                    value = f"{value} [fórmula: {formula}]".strip()
                for _ in range(min(repeat, 1000)):
                    column_number += 1
                    if value:
                        values.append(f"fila {row_number}, columna {column_number}={value}")
            if values:
                lines.append(" | ".join(values))
    return "\n".join(lines)


def _odp_text(content: bytes) -> str:
    with zipfile.ZipFile(BytesIO(content)) as document:
        root = ElementTree.fromstring(document.read("content.xml"))
    lines = []
    pages = [node for node in root.iter() if _local_name(node.tag) == "page"]
    for number, page in enumerate(pages, 1):
        values = []
        for node in page.iter():
            if _local_name(node.tag) in {"p", "h"}:
                value = " ".join("".join(node.itertext()).split())
                if value:
                    values.append(value)
        lines.append(f"[Diapositiva {number}]\n" + "\n".join(values))
    with zipfile.ZipFile(BytesIO(content)) as document:
        images = [name for name in document.namelist() if name.startswith(("Pictures/", "ObjectReplacements/"))]
        _append_archive_image_ocr(document, images, lines)
    return "\n".join(lines)


def _append_archive_image_ocr(archive: zipfile.ZipFile, images: list[str], lines: list[str]) -> None:
    for image_name in images[:MAX_OCR_IMAGES]:
        try:
            lines.append(f"[Imagen incrustada {image_name}; texto OCR]\n{_ocr_image(archive.read(image_name))}")
            lines.append(
                f"COBERTURA VISUAL PARCIAL: el texto de {image_name} se reconoció por OCR; "
                "relaciones de tabla/figura no interpretadas automáticamente"
            )
        except _Unreadable as exc:
            lines.append(f"CONTENIDO GRÁFICO NO INSPECCIONADO ({image_name}): {exc}")
    if len(images) > MAX_OCR_IMAGES:
        lines.append(f"LECTURA PARCIAL: documento con más de {MAX_OCR_IMAGES} imágenes incrustadas")


def _zip_text(content: bytes) -> str:
    """Inspecciona paquetes ZIP sin extraerlos a disco; limita expansión y cantidad de entradas."""
    with zipfile.ZipFile(BytesIO(content)) as archive:
        entries = [item for item in archive.infolist() if not item.is_dir()]
        supported_extensions = TEXT_ATTACHMENT_EXTENSIONS | {
            ".pdf", ".doc", ".docx", ".xls", ".xlsx", ".xlsm", ".ppt", ".pptx", ".odt", ".ods", ".odp",
            ".rtf", ".html", ".htm", ".kmz", ".shp",
        }
        shapefile_sidecars = {".shp", ".shx", ".dbf", ".prj", ".cpg"}
        has_shapefiles = any(Path(item.filename).suffix.casefold() == ".shp" for item in entries)
        supported = [
            item for item in entries
            if Path(item.filename).suffix.casefold() in supported_extensions
            and Path(item.filename).suffix.casefold() != ".shp"
        ]
        if len(entries) > MAX_ARCHIVE_FILES or sum(item.file_size for item in entries) > MAX_ARCHIVE_EXPANDED_BYTES:
            raise _Unreadable("paquete ZIP excede el límite de archivos o tamaño expandido; requiere revisión manual")
        if not supported and not has_shapefiles:
            raise _Unreadable("el paquete ZIP no contiene documentos de formatos legibles")
        fragments = [_shapefile_zip_text(content)] if has_shapefiles else []
        unreadable = []
        unsupported = [
            item.filename for item in entries
            if Path(item.filename).suffix.casefold() not in supported_extensions
            and Path(item.filename).suffix.casefold() not in shapefile_sidecars
        ]
        for item in supported:
            safe_name = posixpath.normpath(item.filename)
            if safe_name.startswith("../") or safe_name.startswith("/"):
                continue
            nested = extract_attachment_text(safe_name, "", archive.read(item))
            text, reason = nested
            fragments.append(f"Archivo dentro de ZIP: {safe_name}\n{text or reason}")
            if reason:
                unreadable.append(f"{safe_name}: {reason}")
        if unreadable:
            fragments.append("LECTURA PARCIAL DEL ZIP; elementos que requieren revisión: " + " | ".join(unreadable))
        if unsupported:
            fragments.append(
                "LECTURA PARCIAL DEL ZIP; formatos internos no inspeccionados: "
                + ", ".join(unsupported[:100])
            )
        return "\n\n".join(fragments)


def _pdf_text(content: bytes) -> str:  # noqa: C901 - combina capa de texto nativa y cobertura visual OCR
    try:
        from pypdf import PdfReader
    except ImportError:
        raise _Unreadable("falta instalar pypdf para leer PDF") from None
    reader = PdfReader(BytesIO(content))
    page_texts = []
    if len(reader.pages) > MAX_OCR_PAGES:
        raise _Unreadable(f"PDF supera el límite de {MAX_OCR_PAGES} páginas; requiere revisión manual")
    try:
        import pypdfium2 as pdfium
        pdf_document = pdfium.PdfDocument(content)
    except ImportError:
        pdfium = None
        pdf_document = None
    for page_number, page in enumerate(reader.pages, 1):
        text = page.extract_text() or ""
        pdf_page = pdf_document[page_number - 1] if pdf_document is not None else None
        has_visual_content = False
        if pdf_page is not None:
            try:
                page_width, page_height = pdf_page.get_size()
                page_area = page_width * page_height
                image_objects = pdf_page.get_objects(filter=[pdfium.raw.FPDF_PAGEOBJ_IMAGE])
                for image_object in image_objects:
                    left, bottom, right, top = image_object.get_bounds()
                    image_width, image_height = max(0, right-left), max(0, top-bottom)
                    image_area = image_width * image_height
                    if image_area >= page_area * 0.025 or (image_width >= page_width * 0.35 and image_height >= 65):
                        has_visual_content = True
                        break
            except Exception:
                has_visual_content = True
        needs_ocr = not text.strip() or len(text.strip()) < 100 or has_visual_content
        if needs_ocr and pdf_page is not None:
            try:
                bitmap = pdf_page.render(scale=3)
                image = bitmap.to_pil()
                if image.width * image.height > MAX_OCR_PIXELS:
                    raise _Unreadable("página PDF excede el límite de píxeles para OCR")
                ocr = _ocr_pil_image(image)
                native_compact = re.sub(r"\W+", "", text.casefold())
                unique_ocr = [
                    line for line in ocr.splitlines()
                    if re.sub(r"\W+", "", line.casefold())
                    and re.sub(r"\W+", "", line.casefold()) not in native_compact
                ]
                if unique_ocr:
                    text = (text.rstrip() + "\n[OCR visual adicional]\n" + "\n".join(unique_ocr)).strip()
                if has_visual_content:
                    text += (
                        "\nCOBERTURA VISUAL PARCIAL: OCR de la página ejecutado; "
                        "relaciones de tabla/figura requieren interpretación visual"
                    )
                has_visual_content = False
            except _Unreadable as exc:
                text = (text.rstrip() + f"\nCONTENIDO VISUAL NO INSPECCIONADO: {exc}").strip()
                has_visual_content = True
        elif needs_ocr:
            message = "\nCONTENIDO VISUAL NO INSPECCIONADO: falta pypdfium2 para renderizar la página"
            text = (text.rstrip() + message).strip()
            has_visual_content = True
        if pdf_page is not None:
            pdf_page.close()
        page_texts.append((text, has_visual_content))
    if pdf_document is not None:
        pdf_document.close()
    if not any(page.strip() for page, _ in page_texts):
        raise _Unreadable("PDF sin texto extraíble ni texto legible por OCR")
    return "\n".join(
        f"[Página {number}{'; sin texto extraíble' if not page.strip() else ''}"
        f"{'; contiene imágenes no interpretadas por OCR' if has_images else ''}]\n{page}"
        for number, (page, has_images) in enumerate(page_texts, 1)
    )


def _docx_paragraph_text(paragraph: ElementTree.Element) -> str:
    return "".join(
        "\t" if _local_name(node.tag) == "tab" else "\n" if _local_name(node.tag) in {"br", "cr"}
        else node.text or ""
        for node in paragraph.iter()
        if _local_name(node.tag) in {"t", "tab", "br", "cr"}
    ).strip()


def _docx_text(content: bytes) -> str:  # noqa: C901 - tablas, revisiones, notas y recursos embebidos
    with zipfile.ZipFile(BytesIO(content)) as document:
        root = ElementTree.fromstring(document.read("word/document.xml"))
    body = next((node for node in root.iter() if _local_name(node.tag) == "body"), root)
    lines = []
    paragraph_number = table_number = 0
    for block in body:
        kind = _local_name(block.tag)
        if kind == "p":
            paragraph_number += 1
            text = _docx_paragraph_text(block)
            if text:
                lines.append(f"Párrafo {paragraph_number}: {text}")
        elif kind == "tbl":
            table_number += 1
            for row_number, row in enumerate(
                (node for node in block if _local_name(node.tag) == "tr"), 1
            ):
                cells = []
                for cell_number, cell in enumerate(
                    (node for node in row if _local_name(node.tag) == "tc"), 1
                ):
                    value = " ".join(
                        _docx_paragraph_text(node) for node in cell.iter()
                        if _local_name(node.tag) == "p"
                    ).strip()
                    cells.append(f"columna {cell_number}={value}")
                if any(value.split("=", 1)[-1] for value in cells):
                    lines.append(f"Tabla {table_number}, fila {row_number}: " + " | ".join(cells))
    with zipfile.ZipFile(BytesIO(content)) as document:
        names = document.namelist()
        for name in names:
            if re.fullmatch(r"word/(header|footer)\d+\.xml", name):
                root = ElementTree.fromstring(document.read(name))
                text = " ".join("".join(node.itertext()) for node in root.iter()
                               if _local_name(node.tag) in {"p", "t"}).strip()
                if text:
                    kind = "Encabezado" if "/header" in name else "Pie de página"
                    lines.append(f"{kind} {name}: {text}")
        for name in names:
            if name in {"word/footnotes.xml", "word/endnotes.xml", "word/comments.xml"}:
                sidecar_root = ElementTree.fromstring(document.read(name))
                note_text = [
                    " ".join("".join(node.itertext()).split()) for node in sidecar_root.iter()
                    if _local_name(node.tag) in {"footnote", "endnote", "comment"}
                ]
                if note_text:
                    lines.append(f"Contenido asociado ({name}): " + " | ".join(note_text))
        revisions = [node for node in root.iter() if _local_name(node.tag) in {"ins", "del"}]
        if revisions:
            lines.append(
                "CAMBIOS RASTREADOS: comparar inserciones y eliminaciones "
                "antes de considerar definitivo el texto"
            )
        for revision in revisions:
            value = " ".join("".join(revision.itertext()).split())
            if value:
                lines.append(f"Revisión rastreada ({_local_name(revision.tag)}): {value}")
        images = [name for name in names if name.startswith("word/media/")]
        _append_archive_image_ocr(document, images, lines)
    return "\n".join(lines)


def _xlsx_text(content: bytes) -> str:  # noqa: C901 - fórmulas, tablas, imágenes y objetos de hoja
    try:
        from openpyxl import load_workbook
    except ImportError:
        raise _Unreadable("falta instalar openpyxl para leer Excel") from None
    workbook = load_workbook(BytesIO(content), read_only=True, data_only=True)
    formulas = load_workbook(BytesIO(content), read_only=True, data_only=False)
    rows = []
    for sheet in workbook.worksheets:
        formula_sheet = formulas[sheet.title]
        rows.append(
            f"Hoja: {sheet.title}; visibilidad: {sheet.sheet_state}; "
            f"rango declarado: {sheet.calculate_dimension()}"
        )
        cell_count = 0
        formula_rows = formula_sheet.iter_rows()
        for row_number, row in enumerate(sheet.iter_rows(), 1):
            if row_number > MAX_SPREADSHEET_CELLS:
                rows.append(f"LECTURA PARCIAL: hoja {sheet.title} supera {MAX_SPREADSHEET_CELLS} filas inspeccionadas")
                break
            formula_row = next(formula_rows, ())
            values = []
            for cell, formula_cell in zip(row, formula_row, strict=True):
                cell_count += 1
                if cell_count > MAX_SPREADSHEET_CELLS:
                    rows.append(
                        f"LECTURA PARCIAL: hoja {sheet.title} supera "
                        f"{MAX_SPREADSHEET_CELLS} celdas inspeccionadas"
                    )
                    break
                if formula_cell.data_type == "f":
                    value = f"{formula_cell.value} [valor guardado: {cell.value}]"
                    if cell.value is None:
                        rows.append(
                            f"FÓRMULA SIN VALOR CALCULADO: hoja {sheet.title}, "
                            f"celda {cell.coordinate}"
                        )
                else:
                    value = cell.value
                if value is not None:
                    values.append(f"{cell.coordinate}={value}")
            if values:
                rows.append(" | ".join(values))
        with zipfile.ZipFile(BytesIO(content)) as archive:
            images = [name for name in archive.namelist() if name.startswith("xl/media/")]
            _append_archive_image_ocr(archive, images, rows)
            charts = [name for name in archive.namelist() if re.fullmatch(r"xl/charts/chart\d+\.xml", name)]
            for chart_name in charts:
                chart_root = ElementTree.fromstring(archive.read(chart_name))
                chart_text = [" ".join("".join(node.itertext()).split()) for node in chart_root.iter()
                              if _local_name(node.tag) in {"f", "v"} and "".join(node.itertext()).strip()]
                rows.append(f"Gráfico ({chart_name}); referencias y valores almacenados: " + "; ".join(chart_text[:80]))
            comments = [name for name in archive.namelist() if name.startswith("xl/comments")]
            for comment_name in comments:
                comment_root = ElementTree.fromstring(archive.read(comment_name))
                for comment in (node for node in comment_root.iter() if _local_name(node.tag) == "comment"):
                    reference = comment.attrib.get("ref", "celda sin referencia")
                    author_id = comment.attrib.get("authorId", "")
                    value = " ".join("".join(comment.itertext()).split())
                    rows.append(f"Comentario ({comment_name}, {reference}, autor {author_id}): {value}")
            pivots = [name for name in archive.namelist() if name.startswith("xl/pivotTables/")]
            if pivots:
                rows.append(
                    "COBERTURA PARCIAL: tablas dinámicas requieren interpretación especializada: "
                    + ", ".join(pivots)
                )
            drawings = [name for name in archive.namelist()
                        if name.startswith("xl/drawings/") and name.endswith(".xml")]
            if drawings:
                rows.append("DIBUJOS/FORMAS DE HOJA PENDIENTES DE LECTURA VISUAL: " + ", ".join(drawings))
    return "\n".join(rows)


def _read_other_format(filename: str, mime_type: str, content: bytes, extension: str) -> str:  # noqa: C901
    """Extrae contenido y conserva localizadores para poder citar la fuente."""
    if extension == ".kml":
        return _kml_text_file(Path(filename).name, content)
    if extension in {".html", ".htm"}:
        return _html_text(content)
    if extension == ".rtf":
        return _rtf_text(content)
    if extension == ".gpx":
        return _gpx_text(content)
    if extension == ".waypoints":
        return _qgc_waypoints_text(content)
    if extension == ".plan":
        return _qgc_plan_text(content)
    if extension == ".wpml":
        return _wpml_text(Path(filename).name, content)
    if extension == ".geojson":
        return _geojson_text(content)
    if extension == ".csv":
        return _csv_text(content)
    if extension in {".pptx", ".odt", ".ods", ".odp"}:
        return {
            ".pptx": lambda: _pptx_text(content),
            ".odt": lambda: _odt_text(content),
            ".ods": lambda: _ods_text(content),
            ".odp": lambda: _odp_text(content),
        }[extension]()
    if extension == ".zip":
        return _zip_text(content)
    if extension in TEXT_ATTACHMENT_EXTENSIONS or mime_type.startswith("text/"):
        return _plain_text(content)
    if extension == ".kmz" or mime_type == "application/vnd.google-earth.kmz":
        return _kmz_text(content)
    if extension == ".pdf" or mime_type == "application/pdf":
        return _pdf_text(content)
    if extension == ".docx":
        return _docx_text(content)
    if extension in {".xlsx", ".xlsm"}:
        return _xlsx_text(content)
    if extension == ".dwg":
        raise _Unreadable("DWG requiere exportación a PDF, DXF o imagen; revise el plano original")
    if extension in {".dwg", ".dxf"}:
        raise _Unreadable("plano CAD requiere revisión manual")
    return ""


def _read_by_format(filename: str, mime_type: str, content: bytes) -> str:
    extension = Path(filename).suffix.casefold()
    if extension in {".png", ".jpg", ".jpeg", ".tif", ".tiff", ".bmp", ".webp", ".gif"}:
        return _image_text(filename, content)
    if extension == ".xls":
        return _xls_text(content)
    if extension in {".doc", ".ppt"}:
        return _office_legacy_text(extension, content)
    if extension == ".shp":
        raise _Unreadable("shapefile incompleto; comprima .shp, .shx y .dbf juntos en un ZIP")
    return _read_other_format(filename, mime_type, content, extension)


def _evidence_location(line: str, current: str) -> tuple[str, str]:
    match = re.match(r"^\[(Página\s+\d+)(?:;([^\]]+))?\]\s*(.*)$", line)
    if match:
        detail = " ".join(part for part in (match.group(2), match.group(3)) if part)
        return match.group(1), detail.strip()
    if line.startswith("Hoja: "):
        return line.split(";", 1)[0], ""
    if line.startswith("Archivo KML:"):
        return line, ""
    if line.startswith("Archivo dentro de ZIP:"):
        return line, ""
    if line.startswith("LECTURA PARCIAL DEL ZIP"):
        return "Cobertura del ZIP", line
    if line.startswith("Elementos geográficos:"):
        return "Resumen KML", line
    if line.startswith(("Geometría:", "Muestras (longitud, latitud):", "Extensión geográfica KML")):
        return current, line
    if line.startswith("[OCR visual adicional]"):
        return current, line
    if line.startswith("[") and line.endswith("]"):
        return line.strip("[]"), ""
    if line.startswith("Párrafo ") or line.startswith("Tabla ") or line.startswith("Elemento: "):
        return line.split(":", 1)[0], line.partition(":")[2].strip()
    structured = re.match(
        r"^(Waypoint(?:\s+\d+)?|Ruta\s+[^:]+|Track\s+[^:]+|Segmento\s+\d+|"
        r"Punto ruta\s+\d+|Elemento\s+\d+|Feature\s+\d+|Capa shapefile|Registro\s+\d+):?\s*(.*)$",
        line,
    )
    if structured:
        return structured.group(1), structured.group(2)
    if re.match(r"^[A-Z]{1,3}\d+=", line):
        return current, line
    return current, line


def attachment_reviewed_sections(text: str) -> list[str]:
    """Enumera todos los localizadores del texto extraído, sin el límite de citas de la ficha."""
    sections = []
    current = "Contenido extraído"
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        location, _ = _evidence_location(line, current)
        if location != current:
            current = location
            if current not in sections:
                sections.append(current)
    return sections or ["Contenido sin paginación"]


def build_attachment_evidence(text: str) -> list[dict[str, str]]:
    """Resume fragmentos con localizadores estables para la ficha del caso."""
    evidence: list[dict[str, str]] = []
    current_location = "Contenido extraído"
    excerpt: list[str] = []

    def flush() -> None:
        value = " ".join(part.strip() for part in excerpt if part.strip()).strip()
        if value and len(evidence) < MAX_EVIDENCE_ITEMS:
            evidence.append({"topic": "extracto", "value": "", "location": current_location,
                             "excerpt": value[:MAX_EVIDENCE_CHARS]})

    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        location, content = _evidence_location(line, current_location)
        if location != current_location:
            flush()
            excerpt.clear()
            current_location = location
        if content:
            excerpt.append(content)
    flush()
    return evidence


def _readable(filename: str, mime_type: str) -> bool:
    extension = Path(filename).suffix.casefold()
    return (
        extension in TEXT_ATTACHMENT_EXTENSIONS
        or mime_type.startswith("text/")
        or extension in {
            ".kmz", ".pdf", ".docx", ".xlsx", ".xlsm", ".pptx", ".odt", ".ods", ".odp",
            ".rtf", ".html", ".htm", ".zip", ".xls", ".doc", ".ppt", ".shp",
            ".png", ".jpg", ".jpeg", ".tif", ".tiff", ".bmp", ".webp", ".gif",
        }
        or mime_type in {"application/vnd.google-earth.kmz", "application/pdf"}
    )


def extract_attachment_text(filename: str, mime_type: str, content: bytes) -> tuple[str, str]:
    """Devuelve texto y motivo; el texto mantiene referencias a páginas, filas o elementos."""
    if len(content) > MAX_ATTACHMENT_BYTES:
        return "", f"supera el límite de {MAX_ATTACHMENT_BYTES // (1024 * 1024)} MB"
    if not _readable(filename, mime_type):
        extension = Path(filename).suffix.casefold()
        if extension in {".dwg", ".dxf"}:
            return "", "plano CAD requiere revisión manual"
        return "", f"formato no soportado ({extension or mime_type or 'desconocido'})"
    reason = ""
    try:
        text = _read_by_format(filename, mime_type, content).strip()
    except _Unreadable as exc:
        return "", str(exc)
    except Exception as exc:
        return "", f"no se pudo leer: {exc}"
    if not text:
        return "", "el archivo no contiene texto extraíble"
    blank_pages = re.findall(r"\[Página (\d+); sin texto extraíble", text)
    if blank_pages:
        reason = (
            "lectura parcial: páginas sin texto extraíble (posible escaneo u hoja vacía): "
            + ", ".join(blank_pages)
        )
    visual_limit = (
        "contiene imágenes no interpretadas por OCR" in text
        or "CONTENIDO GRÁFICO" in text
        or "LECTURA PARCIAL" in text
        or "sin texto alternativo; requiere inspección visual" in text
        or "[Imagen:" in text
        or "CONTENIDO VISUAL NO INSPECCIONADO" in text
        or "CONTENIDO DE GRÁFICOS NO EXTRAÍDO" in text
        or "COBERTURA VISUAL PARCIAL" in text
        or "COBERTURA PARCIAL:" in text
        or "CAMBIOS RASTREADOS:" in text
    )
    if visual_limit:
        reason = (
            "lectura parcial: hay imágenes, gráficos, comentarios o secciones "
            "que requieren inspección visual/manual"
        )
    if len(text) > MAX_ATTACHMENT_TEXT:
        return text[:MAX_ATTACHMENT_TEXT], (
            f"lectura parcial: se recortó a {MAX_ATTACHMENT_TEXT:,} caracteres "
            f"de {len(text):,}; revise el archivo completo"
        )
    if "LECTURA PARCIAL DEL ZIP" in text:
        reason = "lectura parcial: uno o más archivos dentro del ZIP no se pudieron extraer por completo"
    return text, reason
