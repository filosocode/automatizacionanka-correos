"""Construye un indice comercial agregado y local desde el archivo historico T&E."""

from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from datetime import datetime
import json
import os
import re
import unicodedata
from pathlib import Path
import zipfile
from xml.etree import ElementTree


DEFAULT_SOURCE = Path(r"I:\T&E")
DATA_DIR = Path(os.getenv("ANKA_DATA_DIR") or Path(os.getenv("LOCALAPPDATA") or Path.home() / ".local" / "share") / "AnkaCorreos")
DEFAULT_OUTPUT = DATA_DIR / "internal_context" / "commercial_index.json"
MAX_SOURCE_REFERENCES = 12
MAX_DOCUMENTS_PER_CLIENT = 12
MAX_PDF_BYTES = 12 * 1024 * 1024
MAX_PDF_PAGES = 4

SENSITIVE_PARTS = (
    "afiliacion", "cedula", "documentos personal", "hoja de vida", "personal campo",
    "seguridad social", "hseq", "sgi", "planilla", "examen medico", "vacuna",
    "certificado laboral", "contrato laboral", "tratamiento de datos", "rut persona",
)
REQUEST_MARKERS = (
    "solicitud", "cotizacion", "rfq", "rfp", "terminos", "tdr", "pliego",
    "invitacion", "gmail", "alcance", "requerimiento",
)
COMMERCIAL_MARKERS = REQUEST_MARKERS + ("propuesta", "oferta", "tye", "t&e")

PROJECT_RULES = {
    "LiDAR": ("lidar", "laser aerotransportado", "nube de puntos"),
    "Topografia convencional": ("topograf", "replanteo", "levantamiento convencional", "comision topograf"),
    "Fotogrametria y ortofoto": ("fotogram", "ortofoto", "ortomosaico", "restitucion"),
    "Termografia e inspeccion solar": ("termograf", "panel solar", "modulos solares", "inspeccion solar"),
    "Batimetria": ("batimetr", "hidrograf"),
    "Georradar": ("georradar", "gpr"),
    "Escaner terrestre y tuneles": ("escaner terrestre", "laser terrestre", "tunel"),
    "Catastro y cartografia": ("catastro", "cartograf", "pot ", "plan de ordenamiento"),
    "Inspeccion con dron": ("inspeccion aerea", "inspeccion con dron", "drone", "dron "),
}

SECTOR_RULES = {
    "Energia": ("solar", "fotovolta", "subestacion", "transmision", "linea electr", "eolico", "energia"),
    "Infraestructura": ("carretera", "via ", "vial", "puente", "tunel", "ferrocarr", "aeropuerto"),
    "Mineria": ("mina", "minero", "cantera"),
    "Hidrocarburos": ("oleoduct", "gasoduct", "hidrocarb", "petrol"),
    "Ambiente y territorio": ("ambient", "catastro", "pot ", "ordenamiento", "forestal"),
    "Agua y saneamiento": ("acueduct", "alcantarill", "embalse", "rio ", "hidraul", "saneamiento"),
    "Agricultura": ("agric", "cultivo", "palma", "finca"),
    "Industrial": ("planta industrial", "bodega", "industria", "manufactura"),
}

COMPANY_RULES = {
    "Entidad publica": ("alcaldia", "gobernacion", "ministerio", "instituto", "municipio", "corporacion autonoma", "empresa publica"),
    "Empresa de energia": ("energia", "energy", "solar", "electrica", "electrico", "power", "renovable"),
    "Ingenieria o consultoria": ("ingenier", "consult", "estudios", "disenos", "diseños", "construccion", "constructora"),
    "Empresa minera o industrial": ("minera", "mineria", "cemento", "industrial", "manufactura"),
    "Operador de infraestructura": ("concesion", "vial", "infraestructura", "aeropuerto", "ferrocarr"),
}

REQUIREMENT_RULES = {
    "Ubicacion, poligono o coordenadas": ("ubicacion", "localizacion", "kmz", "kml", "poligono", "coordenad", "shapefile"),
    "Area, longitud o cantidades": ("hectarea", " hect", " km", "kilomet", "longitud", "area", "cantidad", "numero de"),
    "Servicio o metodo esperado": ("metodologia", "levantamiento", "lidar", "fotogram", "topograf", "batimetr", "georradar"),
    "Entregables y formatos": ("entregable", "formato", "dwg", "dxf", "las", "laz", "ortofoto", "informe", "plano"),
    "Precision, resolucion o escala": ("precision", "resolucion", "escala", "densidad", "gsd", "tolerancia"),
    "Sistema de referencia": ("sistema de referencia", "datum", "magna", "sirgas", "proyeccion"),
    "Plazo o cronograma": ("plazo", "cronograma", "fecha de entrega", "dias", "semanas", "duracion"),
    "Acceso, permisos o HSE": ("acceso", "permiso", "restriccion", "hse", "sst", "induccion", "seguridad"),
    "Normas o especificaciones": ("norma", "especificacion", "terminos de referencia", "tdr", "pliego"),
    "Campanas o frecuencia": ("campana", "frecuencia", "visita", "monitoreo", "periodicidad"),
    "Informacion previa o insumos": ("informacion previa", "cartografia base", "diseno", "diseño", "estudio previo", "archivo adjunto"),
}


def normalize(value: str) -> str:
    value = unicodedata.normalize("NFKD", value or "")
    return " ".join("".join(ch for ch in value if not unicodedata.combining(ch)).casefold().split())


def is_sensitive(path: Path) -> bool:
    text = normalize(" / ".join(path.parts))
    return any(marker in text for marker in SENSITIVE_PARTS)


def matched_labels(text: str, rules: dict[str, tuple[str, ...]]) -> list[str]:
    normalized = normalize(text)
    return [label for label, terms in rules.items() if any(normalize(term) in normalized for term in terms)]


def is_request_document(path: Path) -> bool:
    name = normalize(path.name)
    return path.suffix.casefold() in {".pdf", ".docx"} and any(marker in name for marker in REQUEST_MARKERS)


def is_commercial_source(path: Path) -> bool:
    name = normalize(path.name)
    return path.suffix.casefold() in {".pdf", ".docx", ".xlsx", ".xls"} and any(
        marker in name for marker in COMMERCIAL_MARKERS
    )


def extract_docx_text(path: Path) -> str:
    try:
        with zipfile.ZipFile(path) as archive:
            data = archive.read("word/document.xml")
        root = ElementTree.fromstring(data)
        return " ".join(node.text or "" for node in root.iter() if node.tag.endswith("}t"))[:120000]
    except (OSError, KeyError, zipfile.BadZipFile, ElementTree.ParseError):
        return ""


def extract_pdf_text(path: Path) -> str:
    if path.stat().st_size > MAX_PDF_BYTES:
        return ""
    try:
        from pypdf import PdfReader
        reader = PdfReader(str(path), strict=False)
        return " ".join((page.extract_text() or "") for page in reader.pages[:MAX_PDF_PAGES])[:120000]
    except Exception:
        return ""


def extract_request_text(path: Path) -> str:
    if path.suffix.casefold() == ".docx":
        return extract_docx_text(path)
    if path.suffix.casefold() == ".pdf":
        return extract_pdf_text(path)
    return ""


def inferred_company_type(client_name: str) -> tuple[str, str]:
    labels = matched_labels(client_name, COMPANY_RULES)
    if labels:
        return labels[0], "media"
    corporate_tokens = ("sas", "s a s", "ltda", "inc", "corp", "grupo", "company")
    if any(token in normalize(client_name) for token in corporate_tokens):
        return "Empresa privada", "baja"
    return "No clasificado", "baja"


def build_index(source: Path) -> dict:
    if not source.exists():
        raise FileNotFoundError(f"No existe la fuente: {source}")

    global_projects: Counter[str] = Counter()
    global_sectors: Counter[str] = Counter()
    global_requirements: Counter[str] = Counter()
    extension_counts: Counter[str] = Counter()
    profiles: dict[str, dict] = {}

    for client_dir in sorted((item for item in source.iterdir() if item.is_dir()), key=lambda p: p.name.casefold()):
        profile = {
            "client_name": client_dir.name,
            "company_type": inferred_company_type(client_dir.name)[0],
            "company_type_confidence": inferred_company_type(client_dir.name)[1],
            "file_count": 0,
            "commercial_document_count": 0,
            "request_document_count": 0,
            "project_types": Counter(),
            "sectors": Counter(),
            "usual_requests": Counter(),
            "source_references": [],
        }
        request_docs: list[Path] = []
        try:
            files = (item for item in client_dir.rglob("*") if item.is_file())
            for path in files:
                if is_sensitive(path):
                    continue
                profile["file_count"] += 1
                extension_counts[path.suffix.casefold() or "sin extension"] += 1
                relative = path.relative_to(source)
                context = str(relative.parent) + " " + path.stem
                for label in matched_labels(context, PROJECT_RULES):
                    profile["project_types"][label] += 1
                    global_projects[label] += 1
                for label in matched_labels(context, SECTOR_RULES):
                    profile["sectors"][label] += 1
                    global_sectors[label] += 1
                if is_commercial_source(path):
                    profile["commercial_document_count"] += 1
                    if len(profile["source_references"]) < MAX_SOURCE_REFERENCES:
                        profile["source_references"].append(str(relative))
                if is_request_document(path):
                    profile["request_document_count"] += 1
                    if len(request_docs) < MAX_DOCUMENTS_PER_CLIENT:
                        request_docs.append(path)
        except (OSError, PermissionError):
            continue

        for path in request_docs:
            text = extract_request_text(path)
            for label in matched_labels(text, REQUIREMENT_RULES):
                profile["usual_requests"][label] += 1
                global_requirements[label] += 1

        if profile["file_count"]:
            for key in ("project_types", "sectors", "usual_requests"):
                profile[key] = dict(profile[key].most_common())
            profiles[normalize(client_dir.name)] = profile

    return {
        "schema_version": 1,
        "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "source_label": "Archivo historico T&E (solo lectura)",
        "privacy": {
            "internal_only": True,
            "raw_text_stored": False,
            "prices_stored": False,
            "sensitive_personnel_documents_excluded": True,
            "outbound_use_allowed": False,
        },
        "summary": {
            "client_profiles": len(profiles),
            "project_types": dict(global_projects.most_common()),
            "sectors": dict(global_sectors.most_common()),
            "usual_requests": dict(global_requirements.most_common()),
            "file_extensions": dict(extension_counts.most_common(25)),
        },
        "clients": profiles,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=DEFAULT_SOURCE)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    result = build_index(args.source)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({
        "output": str(args.output),
        "client_profiles": result["summary"]["client_profiles"],
        "project_types": result["summary"]["project_types"],
        "sectors": result["summary"]["sectors"],
        "usual_requests": result["summary"]["usual_requests"],
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
