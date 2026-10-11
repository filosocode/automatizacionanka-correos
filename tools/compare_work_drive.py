"""Captura hilos de prueba y compara extracción reproducible con los análisis existentes de Work.

Los datos se guardan fuera del control de versiones. Nunca envía, crea borradores ni modifica casos.
Ejecutar con --capture y --thread-id para descargar; con --source-root para extraer con una versión del código.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
import sys
import time
from pathlib import Path
from typing import Any


def write_json(path: Path, value: object) -> None:
    """Guarda un artefacto de comparación UTF-8."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")


def capture(destination: Path, ids: list[str]) -> None:
    """Captura los mensajes, adjuntos exactos y análisis de la base en modo de solo lectura."""
    from anka.dominio.correo import attachment_parts, decode_websafe, inbound_messages
    from anka.infra.google_services import services
    from anka.infra.settings import DATA_DIR, load_config

    if destination.exists():
        raise ValueError("Use una carpeta nueva para conservar la referencia anterior.")
    config = load_config()
    gmail, _ = services()
    account = gmail.users().getProfile(userId="me").execute()["emailAddress"]
    own = {account, *config.get("own_addresses", [])}
    cases: list[dict] = []
    db = sqlite3.connect((DATA_DIR / "quotes.sqlite3").as_uri() + "?mode=ro", uri=True)
    try:
        for tid in ids:
            row = db.execute("SELECT status, last_inbound_id, analysis_json FROM quotes WHERE thread_id=?",
                             (tid,)).fetchone()
            thread = gmail.users().threads().get(userId="me", id=tid, format="full").execute()
            records = []
            for message in inbound_messages(thread, own):
                if "DRAFT" in message.get("labelIds", []):
                    continue
                for number, part in enumerate(attachment_parts(message.get("payload", {}))):
                    encoded = part.get("data")
                    if not encoded and part.get("attachment_id"):
                        encoded = gmail.users().messages().attachments().get(
                            userId="me", messageId=message["id"], id=part["attachment_id"]).execute()["data"]
                    if not encoded:
                        raise ValueError("Gmail no devolvió el adjunto de prueba.")
                    content = decode_websafe(encoded)
                    name = Path(part["filename"]).name
                    relative = Path(tid) / message["id"] / f"{number:02d}-{name}"
                    target = destination / relative
                    target.parent.mkdir(parents=True, exist_ok=True)
                    target.write_bytes(content)
                    records.append({"filename": name, "message_id": message["id"],
                                    "mime_type": part["mime_type"], "path": relative.as_posix(),
                                    "sha256": hashlib.sha256(content).hexdigest()})
            write_json(destination / tid / "thread.json", thread)
            cases.append({"thread_id": tid, "attachments": records, "baseline_status": row[0] if row else None,
                          "baseline_message_id": row[1] if row else None,
                          "baseline_work": json.loads(row[2]) if row else None})
    finally:
        db.close()
    write_json(destination / "cases.json", {"account": account, "cases": cases})
    print(json.dumps({"cases": len(cases), "attachments": sum(len(c["attachments"]) for c in cases)}))


def extract(fixtures: Path, output: Path) -> None:
    """Ejecuta el extractor real de la versión seleccionada, sin sustituir el motor OCR."""
    from anka.infra.attachments import extract_attachment_text_for_work_packet

    captured = json.loads((fixtures / "cases.json").read_text(encoding="utf-8"))
    rows = []
    for case in captured["cases"]:
        for item in case["attachments"]:
            content = (fixtures / item["path"]).read_bytes()
            if hashlib.sha256(content).hexdigest() != item["sha256"]:
                raise ValueError("Cambió un archivo de prueba.")
            started = time.monotonic()
            text, reason = extract_attachment_text_for_work_packet(item["filename"], item["mime_type"], content)
            rows.append({**item, "thread_id": case["thread_id"], "text": text, "reason": reason,
                         "coverage": "no_legible" if not text else "parcial" if reason else "completa",
                         "seconds": round(time.monotonic() - started, 3)})
    write_json(output, {"source_root": sys.path[0], "attachments": rows})
    print(json.dumps({"files": len(rows), "readable": sum(bool(r["text"]) for r in rows),
                      "partial": sum(r["coverage"] == "parcial" for r in rows)}))


def compare(fixtures: Path, baseline: Path, updated: Path, output: Path) -> None:
    """Compara texto local y cobertura declarada por Work; no simula una nueva ejecución del modelo."""
    captured = json.loads((fixtures / "cases.json").read_text(encoding="utf-8"))
    old = json.loads(baseline.read_text(encoding="utf-8"))["attachments"]
    new = json.loads(updated.read_text(encoding="utf-8"))["attachments"]
    old_by_key = {(r["thread_id"], r["path"]): r for r in old}
    rows = []
    for row in new:
        case = next(c for c in captured["cases"] if c["thread_id"] == row["thread_id"])
        work: dict = next((a for a in (case["baseline_work"] or {}).get("attachments", [])
                     if a.get("filename") == row["filename"]), {})
        previous = old_by_key[(row["thread_id"], row["path"])]
        rows.append({"thread_id": row["thread_id"], "filename": row["filename"],
                     "baseline_work_coverage": work.get("coverage", "no_informada"),
                     "baseline_work_reason": work.get("reason", ""),
                     "old_local_coverage": previous["coverage"], "new_local_coverage": row["coverage"],
                     "old_chars": len(previous["text"]), "new_chars": len(row["text"]),
                     "text_identical": previous["text"] == row["text"], "new_reason": row["reason"]})
    write_json(output, {"comparison": rows, "new_work_run": None,
                        "interpretation_accuracy_gain": None,
                        "note": "Extracción local real y análisis Work histórico; no es un A/B de modelos."})
    print(json.dumps({"files": len(rows), "historical_work_unreadable": sum(
        r["baseline_work_coverage"] == "no_legible" for r in rows), "new_extraction_readable": sum(
        r["new_chars"] > 0 for r in rows), "interpretation_accuracy_gain": None}))


def main() -> None:
    """Selecciona captura, extracción o comparación sin activar el flujo comercial."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--capture", action="store_true")
    parser.add_argument("--thread-id", action="append", default=[])
    parser.add_argument("--source-root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--fixtures", type=Path, required=True)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--baseline", type=Path)
    parser.add_argument("--updated", type=Path)
    args: Any = parser.parse_args()
    sys.path.insert(0, str(args.source_root.resolve()))
    if args.capture:
        if not args.thread_id:
            parser.error("Indique --thread-id para capturar.")
        capture(args.fixtures, args.thread_id)
    elif args.baseline and args.updated and args.output:
        compare(args.fixtures, args.baseline, args.updated, args.output)
    elif args.output:
        extract(args.fixtures, args.output)
    else:
        parser.error("Indique --output.")


if __name__ == "__main__":
    main()
