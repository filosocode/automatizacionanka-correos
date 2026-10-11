"""Prepara extracción local, localizadores y cobertura para adjuntarlos a ChatGPT Work."""

from __future__ import annotations

import hashlib
import json
import mimetypes
import shutil
import tempfile
from pathlib import Path

from anka.infra.attachments import (
    MAX_WORK_PACKET_ATTACHMENT_BYTES,
    attachment_reviewed_sections,
    build_attachment_evidence,
    extract_attachment_text_for_work_packet,
)
from anka.dominio.errores import ErrorDeAnka

MAX_PACKET_FILES = 30
MAX_PACKET_TOTAL_BYTES = 250 * 1024 * 1024
MAX_PACKET_TEXT_BYTES = 300 * 1024 * 1024
PACKET_CHUNK_CHARS = 12_000


def chunk_text(text: str) -> list[str]:
    """Divide texto en fragmentos ordenados, procurando cortar en saltos de línea."""
    chunks = []
    remaining = text
    while remaining:
        if len(remaining) <= PACKET_CHUNK_CHARS:
            chunks.append(remaining)
            break
        boundary = remaining.rfind("\n", 0, PACKET_CHUNK_CHARS)
        if boundary < PACKET_CHUNK_CHARS // 2:
            boundary = PACKET_CHUNK_CHARS
        else:
            boundary += 1
        chunks.append(remaining[:boundary])
        remaining = remaining[boundary:]
    return chunks


def _safe_stem(path: Path, digest: str) -> str:
    """Genera un nombre de salida estable sin filtrar rutas locales."""
    stem = "".join(char if char.isalnum() or char in "-_" else "_" for char in path.stem)
    return f"{stem[:48] or 'adjunto'}-{digest[:10]}"


def _write_json(path: Path, value: object) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")


def prepare_work_attachment_packet(attachment_paths: list[str], output_dir: str) -> dict:
    """Crea un directorio con manifiesto y textos completos segmentados para Work.

    No sube ni comparte los archivos. La persona adjunta los artefactos generados
    a la tarea de Work que analiza el correo correspondiente.
    """
    if not attachment_paths:
        raise ErrorDeAnka("Indica al menos un archivo con --attachment.")
    if len(attachment_paths) > MAX_PACKET_FILES:
        raise ErrorDeAnka(f"El paquete admite hasta {MAX_PACKET_FILES} adjuntos por ejecución.")

    sources = [Path(value).expanduser().resolve() for value in attachment_paths]
    if len(set(sources)) != len(sources):
        raise ErrorDeAnka("Hay rutas repetidas en --attachment; cada adjunto debe aparecer una vez.")
    for source in sources:
        if not source.is_file():
            raise ErrorDeAnka(f"No se encuentra el archivo indicado: {source.name}")
    total_bytes = sum(source.stat().st_size for source in sources)
    if total_bytes > MAX_PACKET_TOTAL_BYTES:
        raise ErrorDeAnka("Los adjuntos superan el límite total del paquete local de 250 MiB.")

    destination = Path(output_dir).expanduser().resolve()
    if destination.exists():
        raise ErrorDeAnka(f"La carpeta de salida ya existe; elige una ruta nueva: {destination}")
    destination.parent.mkdir(parents=True, exist_ok=True)
    stage = Path(tempfile.mkdtemp(prefix=f".{destination.name}-", dir=destination.parent))
    try:
        result = _write_packet(sources, stage)
        stage.replace(destination)
    except Exception:
        shutil.rmtree(stage, ignore_errors=True)
        raise
    result["output_dir"] = str(destination)
    return result


def _write_packet(sources: list[Path], stage: Path) -> dict:
    """Extrae cada fuente y escribe fragmentos, evidencia y estados de cobertura."""
    records = []
    text_bytes_total = 0
    for index, source in enumerate(sources, 1):
        size_bytes = source.stat().st_size
        if size_bytes > MAX_WORK_PACKET_ATTACHMENT_BYTES:
            records.append(_blocked_record(source, size_bytes, "supera el límite local de 50 MiB"))
            continue
        try:
            content = source.read_bytes()
        except OSError as exc:
            records.append(_blocked_record(source, size_bytes, f"no se pudo leer ({type(exc).__name__})"))
            continue
        digest = hashlib.sha256(content).hexdigest()
        extracted, reason = extract_attachment_text_for_work_packet(
            source.name, mimetypes.guess_type(source.name)[0] or "", content,
        )
        encoded_text = extracted.encode("utf-8")
        text_bytes_total += len(encoded_text)
        if text_bytes_total > MAX_PACKET_TEXT_BYTES:
            raise ErrorDeAnka("La extracción supera 300 MiB de texto; divide los adjuntos en varios paquetes.")

        file_id = _safe_stem(source, digest)
        status = "bloqueado" if not extracted else "lectura_parcial" if reason else "extraido"
        text_files = []
        text_segments = []
        char_offset = 0
        chunks = chunk_text(extracted)
        for chunk_index, chunk in enumerate(chunks, 1):
            filename = f"{index:02d}-{file_id}-{chunk_index:03d}.txt"
            header = (
                f"[ANKA WORK PACKET | file={source.name} | source_id={file_id} | "
                f"sha256={digest} | segment={chunk_index}/{len(chunks)}]\n"
            )
            (stage / filename).write_text(header + chunk, encoding="utf-8")
            text_files.append(filename)
            text_segments.append({
                "filename": filename,
                "start_char": char_offset,
                "end_char": char_offset + len(chunk),
            })
            char_offset += len(chunk)
        records.append({
            "source_id": file_id,
            "filename": source.name,
            "mime_type": mimetypes.guess_type(source.name)[0] or "application/octet-stream",
            "size_bytes": len(content),
            "sha256": digest,
            "status": status,
            "coverage": "no_legible" if not extracted else "parcial" if reason else "completa",
            "reason": reason,
            "reviewed_sections": attachment_reviewed_sections(extracted) if extracted else [],
            "text_chars": len(extracted),
            "text_files": text_files,
            "text_segments": text_segments,
            "evidence": build_attachment_evidence(extracted) if extracted else [],
        })

    manifest = {
        "format": "anka-work-attachment-packet-v1",
        "source_files": len(sources),
        "source_bytes": sum(source.stat().st_size for source in sources),
        "text_bytes": text_bytes_total,
        "attachments": records,
    }
    _write_json(stage / "manifest.json", manifest)
    readme = (
        "# Paquete local de evidencia para ChatGPT Work\n\n"
        "Adjunta a la misma tarea el manifiesto y todos los archivos `.txt` generados. "
        "Cada adjunto conserva su nombre original, hash, cobertura, motivo, localizadores y "
        "fragmentos en orden. Evalúa el contenido junto al hilo correcto; no trates un archivo "
        "parcial o bloqueado como dato ausente del cliente. `no_legible` o `parcial` requiere "
        "revisión interna antes de preguntar. Este paquete no se envía automáticamente a Work.\n"
    )
    (stage / "LEEME.md").write_text(readme, encoding="utf-8")
    return {
        "source_files": len(sources),
        "extracted_files": sum(record["status"] == "extraido" for record in records),
        "partial_files": sum(record["status"] == "lectura_parcial" for record in records),
        "blocked_files": sum(record["status"] == "bloqueado" for record in records),
        "text_files": sum(len(record["text_files"]) for record in records),
        "manifest": "manifest.json",
    }


def _blocked_record(source: Path, size_bytes: int, reason: str) -> dict:
    """Describe un archivo que no se procesó sin confundirlo con un dato ausente."""
    file_id = _safe_stem(source, hashlib.sha256(f"{source.name}:{size_bytes}".encode()).hexdigest())
    return {
        "source_id": file_id,
        "filename": source.name,
        "mime_type": mimetypes.guess_type(source.name)[0] or "application/octet-stream",
        "size_bytes": size_bytes,
        "sha256": None,
        "status": "bloqueado",
        "coverage": "no_legible",
        "reason": reason,
        "reviewed_sections": [],
        "text_chars": 0,
        "text_files": [],
        "text_segments": [],
        "evidence": [],
    }
