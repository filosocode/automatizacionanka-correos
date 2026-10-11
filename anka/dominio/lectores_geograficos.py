"""Lectura en memoria de geometrías, misiones y tablas geográficas."""

from __future__ import annotations
from typing import Any
from collections.abc import Iterable
import html
import csv
import json
import math
import re
import zipfile
from io import BytesIO
from xml.etree import ElementTree
from anka.dominio.attachments import (
    MAX_ARCHIVE_FILES,
    MAX_ARCHIVE_EXPANDED_BYTES,
    MAX_SPREADSHEET_CELLS,
    KML_GEOMETRIES,
)
from anka.dominio.lectores_texto import Unreadable


def local_name(tag: str) -> str:
    """Obtiene el nombre XML sin su espacio de nombres."""
    return tag.rsplit("}", 1)[-1]


def child_text(element: ElementTree.Element, name: str) -> str:
    """Lee el texto de un hijo XML por su nombre local."""
    for child in element:
        if local_name(child.tag) == name:
            return " ".join("".join(child.itertext()).split())
    return ""


def coordinate_rows(element: ElementTree.Element) -> list[tuple[float, float, float | None]]:
    """Extrae longitud, latitud y altitud de coordenadas KML válidas."""
    coordinates = []
    for node in element.iter():
        kind = local_name(node.tag)
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


def kml_coordinates(element: ElementTree.Element) -> list[tuple[float, float]]:
    """Obtiene las coordenadas horizontales de un elemento KML."""
    return [(longitude, latitude) for longitude, latitude, _ in coordinate_rows(element)]


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


def kmz_text(content: bytes) -> str:
    """Inspecciona archivos KML y misiones WPML dentro de un KMZ limitado."""
    with zipfile.ZipFile(BytesIO(content)) as archive:
        names = archive.namelist()
        infos = [item for item in archive.infolist() if not item.is_dir()]
        if len(infos) > MAX_ARCHIVE_FILES or sum(item.file_size for item in infos) > MAX_ARCHIVE_EXPANDED_BYTES:
            raise Unreadable("KMZ excede el límite de archivos o tamaño expandido; requiere revisión manual")
        wpml_files = [name for name in names if name.casefold().endswith(".wpml")]
        if wpml_files:
            mission_parts = [wpml_text(name, archive.read(name)) for name in wpml_files]
            mission_parts.extend(
                kml_text_file(name, archive.read(name)) for name in names if name.casefold().endswith(".kml")
            )
            return "\n\n".join(mission_parts)
        kml_files = [name for name in names if name.casefold().endswith(".kml")]
        if not kml_files:
            raise ValueError("el KMZ no contiene un archivo KML")
        return "\n\n".join(kml_text_file(name, archive.read(name)) for name in kml_files)


def kml_text_file(name: str, content: bytes) -> str:
    """Resume geometrías y atributos conservando el elemento KML como localizador."""
    root = ElementTree.fromstring(content)
    placemarks = [node for node in root.iter() if local_name(node.tag) == "Placemark"]
    lines = [f"Archivo KML: {name}", f"Elementos geográficos: {len(placemarks)}"]
    lines.extend(_kml_container_lines(root))
    lines.extend(_kml_overlay_lines(root))
    all_coordinates: list[tuple[float, float]] = []
    for number, placemark in enumerate(placemarks, 1):
        lines.extend(_kml_placemark_lines(placemark, number, all_coordinates))
    lines.extend(_kml_extent_lines(all_coordinates))
    times = [
        " ".join((node.text or "").split()) for node in root.iter() if local_name(node.tag) == "when" and node.text
    ]
    if times:
        lines.append(f"Marcas de tiempo KML: {len(times)}; rango {min(times)} a {max(times)}")
    return "\n".join(lines)


def _extent_text(coordinates: list[tuple[float, float]]) -> str:
    """Extensión mín X, mín Y, máx X, máx Y con la precisión con que se reporta en todos los formatos."""
    xs = [x for x, _ in coordinates]
    ys = [y for _, y in coordinates]
    return f"{min(xs):.8f}, {min(ys):.8f}, {max(xs):.8f}, {max(ys):.8f}"


def _kml_container_lines(root: ElementTree.Element) -> list[str]:
    """Nombre y descripción del `Document` y de cada `Folder`: ahí suelen ir el proyecto y las advertencias del archivo.

    Antes solo se leían los `Placemark` y una descripción general como «Archivo simulado, datos ficticios» se perdía.
    """
    lines = []
    for node in root.iter():
        if local_name(node.tag) not in {"Document", "Folder"}:
            continue
        label = child_text(node, "name")
        description = child_text(node, "description")
        if not label and not description:
            continue
        lines.append(f"Contenedor {local_name(node.tag)}: {label or 'sin nombre'}")
        if description:
            clean = html.unescape(re.sub(r"<[^>]+>", " ", description))
            lines.append(f"Descripción: {' '.join(clean.split())[:500]}")
    return lines


def _kml_overlay_lines(root: ElementTree.Element) -> list[str]:
    """Avisa de overlays y vínculos de red, que este lector no interpreta."""
    overlay_kinds = {"GroundOverlay", "ScreenOverlay", "NetworkLink"}
    overlays = [node for node in root.iter() if local_name(node.tag) in overlay_kinds]
    if not overlays:
        return []
    return [
        "COBERTURA PARCIAL: overlays o vínculos KML requieren análisis especializado: "
        + ", ".join(local_name(node.tag) for node in overlays)
    ]


def _kml_placemark_lines(
    placemark: ElementTree.Element, number: int, all_coordinates: list[tuple[float, float]]
) -> list[str]:
    """Etiqueta, descripción, datos asociados y geometrías de un Placemark; acumula sus coordenadas."""
    label = child_text(placemark, "name") or f"Elemento {number}"
    lines = [f"Elemento: {label}"]
    description = child_text(placemark, "description")
    if description:
        clean = html.unescape(re.sub(r"<[^>]+>", " ", description))
        lines.append(f"Descripción: {' '.join(clean.split())[:500]}")
    attributes = _kml_attributes(placemark)
    if attributes:
        lines.append("Datos asociados: " + "; ".join(attributes)[:1000])
    geometries = [
        node
        for node in placemark.iter()
        if local_name(node.tag) in KML_GEOMETRIES and not any(local_name(child.tag) in KML_GEOMETRIES for child in node)
    ]
    for geometry in geometries or [placemark]:
        lines.extend(_kml_geometry_lines(geometry, all_coordinates))
    return lines


def _kml_geometry_lines(geometry: ElementTree.Element, all_coordinates: list[tuple[float, float]]) -> list[str]:
    """Tipo, muestras, altitudes y longitud geodésica de una geometría KML."""
    kind = local_name(geometry.tag)
    geometry_rows = coordinate_rows(geometry)
    coordinates = [(lon, lat) for lon, lat, _ in geometry_rows]
    all_coordinates.extend(coordinates)
    lines = [f"Geometría: {kind}; coordenadas: {len(coordinates)}"]
    if not coordinates:
        return lines
    samples = "; ".join(f"{lon:.8f},{lat:.8f}" for lon, lat in coordinates[:8])
    lines.append(f"Muestras (longitud, latitud): {samples}")
    altitudes = [altitude for _, _, altitude in geometry_rows if altitude is not None]
    if altitudes:
        lines.append(
            "Altitud registrada en coordenadas (sin normalizar modo vertical): "
            f"mín. {min(altitudes):.3f}, máx. {max(altitudes):.3f}"
        )
    is_line = kind in {"LineString", "Track"} and len(coordinates) > 1
    if is_line and all(valid_lonlat(lon, lat) for lon, lat in coordinates):
        lines.append(
            "Longitud geodésica aproximada calculada desde coordenadas (sin elevación): "
            f"{geodesic_length_m(coordinates):.1f} m"
        )
    return lines


def _kml_extent_lines(all_coordinates: list[tuple[float, float]]) -> list[str]:
    """Extensión de todo el archivo, con advertencia si hay coordenadas fuera de lon/lat."""
    if not all_coordinates:
        return []
    lines = [
        "Extensión geográfica KML, oeste/sur/este/norte (longitud/latitud): " + _extent_text(all_coordinates)
    ]
    if any(abs(lon) > 180 or abs(lat) > 90 for lon, lat in all_coordinates):
        lines.append("Advertencia: hay coordenadas fuera de los rangos usuales de longitud/latitud.")
    return lines


def wpml_text(name: str, content: bytes) -> str:
    """Lee la estructura legible de una misión DJI WPML empaquetada en KMZ."""
    try:
        root = ElementTree.fromstring(content)
    except ElementTree.ParseError as exc:
        raise Unreadable(f"WPML XML inválido en {name}: {exc}") from None
    lines = [f"Archivo de misión DJI WPML: {name}"]
    for node in root.iter():
        local = local_name(node.tag)
        if local in {"missionConfig", "Folder"}:
            values = []
            for child in node:
                child_name = local_name(child.tag)
                value = " ".join("".join(child.itertext()).split())
                if child_name != "Placemark" and value and len(value) <= 500:
                    values.append(f"{child_name}={value}")
            if values:
                lines.append(f"Configuración {local}: " + "; ".join(values[:50]))
    placemarks = [node for node in root.iter() if local_name(node.tag) == "Placemark"]
    lines.append(f"Waypoints/rutas definidos: {len(placemarks)}")
    all_coordinates: list[tuple[float, float]] = []
    for index, placemark in enumerate(placemarks, 1):
        coordinates = coordinate_rows(placemark)
        all_coordinates.extend((lon, lat) for lon, lat, _ in coordinates)
        scalars = []
        for node in placemark.iter():
            value = (node.text or "").strip()
            if value and len(value) <= 180 and local_name(node.tag) not in {"coordinates", "coord"}:
                scalars.append(f"{local_name(node.tag)}={value}")
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
            f"{geodesic_length_m(all_coordinates):.1f} m"
        )
    return "\n".join(lines)


def gpx_text(content: bytes) -> str:
    """Resume GPX sin perder su distinción entre waypoints, rutas y tracks/segmentos."""
    try:
        root = ElementTree.fromstring(content)
    except ElementTree.ParseError as exc:
        raise Unreadable(f"GPX XML inválido: {exc}") from None
    points_all: list[tuple[float, float, float | None, str]] = []
    lines = [
        f"Formato GPX; versión: {root.attrib.get('version', 'no indicada')}; "
        f"creador: {root.attrib.get('creator', 'no indicado')}"
    ]

    waypoints = [node for node in root if local_name(node.tag) == "wpt"]
    waypoint_rows = _gpx_point_rows(lines, waypoints, "waypoint", "")
    lines.append(f"Waypoints independientes: {len(waypoint_rows)}")
    points_all.extend(waypoint_rows)
    route_count = track_count = 0
    for route in (node for node in root if local_name(node.tag) == "rte"):
        route_count += 1
        points_all.extend(_gpx_route_rows(route, route_count, lines))
    for track in (node for node in root if local_name(node.tag) == "trk"):
        track_count += 1
        points_all.extend(_gpx_track_rows(track, track_count, lines))
    lines.append(f"Rutas: {route_count}; tracks: {track_count}")
    coords_all = [(lon, lat) for lon, lat, _, _ in points_all]
    if coords_all:
        lines.append("Extensión calculada lon/lat: " + _extent_text(coords_all))
    return "\n".join(lines)


def _gpx_route_rows(
    route: ElementTree.Element, number: int, lines: list[str]
) -> list[tuple[float, float, float | None, str]]:
    """Puntos de una ruta y sus líneas de resumen (longitud y primeros puntos)."""
    route_name = child_text(route, "name") or f"Ruta {number}"
    rows = _gpx_point_rows(lines, (n for n in route if local_name(n.tag) == "rtept"), "route", route_name)
    coords = [(lon, lat) for lon, lat, _, _ in rows]
    lines.append(f"Ruta {route_name}: {len(rows)} puntos; longitud calculada {geodesic_length_m(coords):.1f} m")
    for index, (lon, lat, altitude, _) in enumerate(rows[:8], 1):
        suffix = f"; altitud={altitude:g} m" if altitude is not None else ""
        lines.append(f"  Punto ruta {index}: lat={lat:.8f}, lon={lon:.8f}{suffix}")
    return rows


def _gpx_track_rows(
    track: ElementTree.Element, number: int, lines: list[str]
) -> list[tuple[float, float, float | None, str]]:
    """Puntos de un track por segmentos y sus líneas de resumen (longitud, tiempo y elevación)."""
    track_name = child_text(track, "name") or f"Track {number}"
    segment_lengths = []
    track_rows = []
    for segment_number, segment in enumerate((n for n in track if local_name(n.tag) == "trkseg"), 1):
        rows = _gpx_point_rows(lines, (n for n in segment if local_name(n.tag) == "trkpt"), "track", track_name)
        track_rows.extend(rows)
        segment_lengths.append(geodesic_length_m([(lon, lat) for lon, lat, _, _ in rows]))
        lines.append(f"  Segmento {segment_number}: {len(rows)} puntos; {segment_lengths[-1]:.1f} m")
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
    return track_rows


def qgc_waypoints_text(content: bytes) -> str:
    """Lee el formato tabular QGroundControl WPL; conserva comandos por su índice numérico."""
    raw = content.decode("utf-8-sig", errors="replace")
    lines = [line.strip() for line in raw.splitlines() if line.strip()]
    if not lines or not lines[0].startswith("QGC WPL "):
        raise Unreadable("archivo .waypoints no coincide con el formato QGroundControl WPL")
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
        coordinate_label = f"lat={lat}; lon={lon}" if is_global_frame else f"x={x}; y={y} (marco local/no geográfico)"
        output.append(
            f"Elemento {seq}: current={current}; frame={frame}; command={command}; "
            f"{coordinate_label}; "
            f"altitud={altitude}; autocontinue={columns[11]}"
        )
        if math.isfinite(lat) and math.isfinite(lon) and -90 <= lat <= 90 and -180 <= lon <= 180:
            coordinates.append((lon, lat))
    output.append(f"Elementos WPL: {items}; puntos con coordenadas geográficas: {len(coordinates)}")
    if coordinates:
        output.append(f"Longitud calculada entre puntos consecutivos: {geodesic_length_m(coordinates):.1f} m")
        output.append(
            "Extensión calculada lon/lat: "
            f"{min(x for x, _ in coordinates):.8f}, {min(y for _, y in coordinates):.8f}, "
            f"{max(x for x, _ in coordinates):.8f}, {max(y for _, y in coordinates):.8f}"
        )
    return "\n".join(output)


def qgc_plan_text(content: bytes) -> str:
    """Lee los ítems de misión exportados por QGroundControl (.plan)."""
    try:
        document = json.loads(content.decode("utf-8-sig"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise Unreadable(f"plan QGroundControl inválido: {exc}") from None
    if not isinstance(document, dict):
        raise Unreadable("plan QGroundControl debe contener un objeto JSON")
    mission = document.get("mission", {})
    items = mission.get("items", []) if isinstance(mission, dict) else []
    if not isinstance(items, list):
        raise Unreadable("plan QGroundControl sin lista de elementos de misión")
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
        coordinate = _mission_coordinate(params)
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
        length_m = geodesic_length_m(coordinates)
        output.append(f"Puntos geográficos: {len(coordinates)}; longitud calculada {length_m:.1f} m")
        output.append(
            "Extensión calculada lon/lat: "
            f"{min(x for x, _ in coordinates):.8f}, {min(y for _, y in coordinates):.8f}, "
            f"{max(x for x, _ in coordinates):.8f}, {max(y for _, y in coordinates):.8f}"
        )
    return "\n".join(output)


def geojson_text(content: bytes) -> str:
    """Resume Feature/FeatureCollection GeoJSON sin perder propiedades ni tipos geométricos."""
    try:
        document = json.loads(content.decode("utf-8-sig"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise Unreadable(f"GeoJSON inválido: {exc}") from None
    if not isinstance(document, dict):
        raise Unreadable("GeoJSON debe contener un objeto JSON")
    features = document.get("features", [])
    if document.get("type") == "Feature":
        features = [document]
    if not isinstance(features, list):
        raise Unreadable("GeoJSON sin lista de features")
    output = [f"GeoJSON {document.get('type', 'objeto')}; features: {len(features)}"]
    all_coords: list[tuple[float, float]] = []
    for index, feature in enumerate(features, 1):
        if isinstance(feature, dict):
            output.extend(_geojson_feature_lines(index, feature, all_coords))
        else:
            output.append(f"Feature {index}: estructura no reconocida")
    if all_coords:
        output.append(f"Vértices con lon/lat interpretables: {len(all_coords)}")
        if any(not valid_lonlat(lon, lat) for lon, lat in all_coords):
            output.append("COBERTURA PARCIAL: coordenadas fuera de rangos lon/lat usuales; posible CRS proyectado")
        output.append(
            "Extensión numérica de coordenadas (se asumen lon/lat GeoJSON): " + _extent_text(all_coords)
        )
    return "\n".join(output)


def _geojson_feature_lines(index: int, feature: dict, all_coords: list[tuple[float, float]]) -> list[str]:
    """Geometría, propiedades y longitud de un Feature; acumula sus vértices en `all_coords`."""
    geometry = feature.get("geometry") or {}
    coordinates = geometry.get("coordinates") if isinstance(geometry, dict) else None
    before = len(all_coords)
    _visit_coordinates(all_coords, coordinates)
    if isinstance(geometry, dict) and geometry.get("type") == "GeometryCollection":
        for child_geometry in geometry.get("geometries", []):
            child_coords = child_geometry.get("coordinates") if isinstance(child_geometry, dict) else None
            _visit_coordinates(all_coords, child_coords)
    properties = feature.get("properties") or {}
    prop_text = (
        "; ".join(f"{key}={value}" for key, value in properties.items())
        if isinstance(properties, dict)
        else str(properties)
    )
    output = [
        f"Feature {index}: geometría={geometry.get('type', 'no indicada')}; "
        f"coordenadas={len(all_coords) - before}; propiedades={prop_text[:1000]}"
    ]
    if coordinates is not None and len(all_coords) - before == 0:
        output.append(f"LECTURA PARCIAL: coordenadas de feature {index} no reconocidas")
    lines = _geometry_lines(geometry)
    if lines and all(valid_lonlat(lon, lat) for line in lines for lon, lat in line):
        lengths = [geodesic_length_m(line) for line in lines]
        output.append(
            f"Longitud geodésica aproximada calculada de líneas en feature {index}: "
            f"{sum(lengths):.1f} m (no equivale a longitud declarada por el remitente)"
        )
    elif lines:
        output.append(
            f"COBERTURA PARCIAL: feature {index} sale del rango lon/lat; "
            "revisar el CRS antes de calcular longitudes"
        )
    return output


_CSV_ALIASES = {
    "lat": {"lat", "latitude", "latitud", "latituddeg", "latitudgrados"},
    "lon": {"lon", "lng", "long", "longitude", "longitud", "longituddeg"},
    "seq": {"seq", "sequence", "waypoint", "waypointnumber", "order", "orden"},
    "alt": {"alt", "altitude", "elevation", "elev", "height", "altura"},
}


def csv_text(content: bytes) -> str:
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
    indices = {
        key: next((i for i, name in enumerate(normalized) if name in accepted), None)
        for key, accepted in _CSV_ALIASES.items()
    }
    output = [f"CSV tabular; separador {delimiter!r}; columnas: {len(header)}"]
    if indices["lat"] is not None and indices["lon"] is not None:
        output.extend(_csv_waypoint_lines(header, rows[1:], indices))
    else:
        output.extend(_csv_table_lines(header, rows[1:]))
    return "\n".join(output)


def _csv_cell_labels(header: list[str], row: list[str]) -> list[str]:
    """`encabezado=valor` de las celdas con contenido (o `columna N` si la fila excede el encabezado)."""
    return [
        f"{header[index] if index < len(header) else f'columna {index + 1}'}={value}"
        for index, value in enumerate(row)
        if value
    ]


def _csv_waypoint_lines(header: list[str], data_rows: list[list[str]], indices: dict[str, int | None]) -> list[str]:
    """Tabla con latitud y longitud explícitas: waypoints ordenados, extensión, longitud y altitudes."""
    output = ["Tabla de coordenadas/waypoints con latitud y longitud explícitas."]
    points = _csv_coordinates(data_rows, indices, output)
    if indices["seq"] is not None:

        def order_key(item: tuple[str, float, float, float | None, list[str]]) -> tuple[int, Any]:
            try:
                return (0, float(item[0]))
            except ValueError:
                return (1, item[0].casefold())

        points.sort(key=order_key)
    output.append(f"Waypoints con coordenadas válidas: {len(points)}")
    for sequence, longitude, latitude, _altitude, row in points:
        values = "; ".join(_csv_cell_labels(header, row))
        output.append(f"Waypoint {sequence}: lat={latitude:.8f}; lon={longitude:.8f}; {values}")
    if points:
        output.append("Extensión calculada lon/lat: " + _extent_text([(point[1], point[2]) for point in points]))
        if indices["seq"] is not None:
            path = [(point[1], point[2]) for point in points]
            output.append(f"Longitud geodésica aproximada de secuencia: {geodesic_length_m(path):.1f} m")
        elevations = [point[3] for point in points if point[3] is not None]
        if elevations:
            output.append(
                f"Rango de valores de altitud/elevación en columna: {min(elevations):g} a {max(elevations):g}"
            )
    return output


def _csv_table_lines(header: list[str], data_rows: list[list[str]]) -> list[str]:
    """CSV sin coordenadas explícitas: se conservan encabezados y filas, con tope de celdas."""
    output = ["Fila 1 (encabezados): " + " | ".join(header)]
    cells_seen = len(header)
    for row_number, row in enumerate(data_rows, 2):
        cells_seen += len(row)
        if cells_seen > MAX_SPREADSHEET_CELLS:
            output.append(f"LECTURA PARCIAL: se superó el límite de {MAX_SPREADSHEET_CELLS} celdas CSV")
            break
        row_values = _csv_cell_labels(header, row)
        if row_values:
            output.append(f"Fila {row_number}: " + " | ".join(row_values))
    return output


def _kml_attributes(placemark: ElementTree.Element) -> list[str]:
    attributes = []
    for node in placemark.iter():
        if local_name(node.tag) in {"Data", "SimpleData"}:
            key = node.attrib.get("name", "")
            value = child_text(node, "value") or " ".join("".join(node.itertext()).split())
            if key and value:
                attributes.append(f"{key}={value}")
    return attributes


def _gpx_point_rows(
    lines: list[str], nodes: Iterable[ElementTree.Element], kind: str, label: str
) -> list[tuple[float, float, float | None, str]]:
    result = []
    for point in nodes:
        try:
            lon, lat = float(point.attrib["lon"]), float(point.attrib["lat"])
        except (KeyError, ValueError):
            lines.append(f"COBERTURA PARCIAL: coordenada GPX inválida en {label or kind}")
            continue
        if not valid_lonlat(lon, lat):
            lines.append(f"COBERTURA PARCIAL: coordenada GPX fuera de rango en {label or kind}")
            continue
        elevation_text = child_text(point, "ele")
        try:
            elevation = float(elevation_text) if elevation_text else None
        except ValueError:
            elevation = None
        name_value = child_text(point, "name") or label
        when = child_text(point, "time")
        result.append((lon, lat, elevation, when))
        if kind == "waypoint":
            detail = f"; altitud={elevation:g} m" if elevation is not None else ""
            if when:
                detail += f"; tiempo={when}"
            lines.append(f"Waypoint {name_value}: lat={lat:.8f}, lon={lon:.8f}{detail}")
    return result


def _geometry_lines(geometry: Any) -> list[list[tuple[float, float]]]:
    if not isinstance(geometry, dict):
        return []
    kind = geometry.get("type")
    coordinates = geometry.get("coordinates")
    if kind == "LineString" and isinstance(coordinates, list):
        return [
            [
                (float(point[0]), float(point[1]))
                for point in coordinates
                if isinstance(point, list)
                and len(point) >= 2
                and all(isinstance(number, (int, float)) for number in point[:2])
            ]
        ]
    if kind == "MultiLineString" and isinstance(coordinates, list):
        return [
            [
                (float(point[0]), float(point[1]))
                for point in line
                if isinstance(point, list)
                and len(point) >= 2
                and all(isinstance(number, (int, float)) for number in point[:2])
            ]
            for line in coordinates
            if isinstance(line, list)
        ]
    if kind == "GeometryCollection":
        return [line for child in geometry.get("geometries", []) for line in _geometry_lines(child)]
    return []


def _visit_coordinates(all_coords: list[tuple[float, float]], value: Any) -> None:
    if (
        isinstance(value, list)
        and len(value) >= 2
        and all(isinstance(coordinate, (int, float)) for coordinate in value[:2])
    ):
        lon, lat = float(value[0]), float(value[1])
        all_coords.append((lon, lat))
    elif isinstance(value, list):
        for child in value:
            _visit_coordinates(all_coords, child)


def _mission_coordinate(params: Any) -> tuple[float, float, float] | None:
    coordinate = None
    if isinstance(params, list) and len(params) >= 7:
        try:
            lat, lon, altitude = float(params[4]), float(params[5]), float(params[6])
            if -90 <= lat <= 90 and -180 <= lon <= 180:
                coordinate = (lon, lat, altitude)
        except (TypeError, ValueError):
            pass
    return coordinate


def _csv_coordinates(
    data_rows: list[list[str]], indices: dict[str, int | None], output: list[str]
) -> list[tuple[str, float, float, float | None, list[str]]]:
    points = []
    for row_number, row in enumerate(data_rows, 2):
        if row_number > MAX_SPREADSHEET_CELLS:
            output.append(f"LECTURA PARCIAL: se superó el límite de {MAX_SPREADSHEET_CELLS} filas CSV")
            break
        try:
            lat_index, lon_index = indices["lat"], indices["lon"]
            assert lat_index is not None and lon_index is not None
            latitude = float(row[lat_index])
            longitude = float(row[lon_index])
        except (IndexError, TypeError, ValueError):
            output.append(f"FILA {row_number}: coordenadas vacías o inválidas")
            continue
        if not valid_lonlat(longitude, latitude):
            output.append(
                f"COBERTURA PARCIAL: fila {row_number} fuera de rango lon/lat; revisar si son coordenadas proyectadas"
            )
            continue
        altitude = None
        if indices["alt"] is not None and indices["alt"] < len(row):
            try:
                altitude = float(row[indices["alt"]])
            except ValueError:
                pass
        sequence = (
            row[indices["seq"]] if indices["seq"] is not None and indices["seq"] < len(row) else str(row_number - 1)
        )
        points.append((sequence, longitude, latitude, altitude, row))
    return points
