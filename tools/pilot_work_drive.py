"""Publica y verifica expedientes de hilos explícitos en una carpeta de piloto, sin activar producción."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from anka.infra.drive_exchange import DriveExchange, preparation_lock  # noqa: E402
from anka.infra.google_services import services, FOLDER_MIME  # noqa: E402
from anka.infra.settings import load_config  # noqa: E402
from anka.servicios.work_drive import sync_work_drive  # noqa: E402


def main() -> None:
    """Prueba escritura, lectura íntegra e idempotencia usando las conexiones reales de Anka."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--parent-id", required=True)
    parser.add_argument("--account", required=True)
    parser.add_argument("--thread-id", action="append", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    gmail, drive = services()
    actual = gmail.users().getProfile(userId="me").execute()["emailAddress"]
    if actual.casefold() != args.account.casefold():
        raise ValueError("Cuenta de piloto incorrecta.")
    metadata = drive.files().get(fileId=args.parent_id, fields="id,name,mimeType,trashed").execute()
    if metadata.get("trashed") or metadata["mimeType"] != FOLDER_MIME:
        raise ValueError("Destino de piloto inválido.")
    exchange = DriveExchange(drive)
    with preparation_lock(args.output.parent / "pilot.lock"):
        root = exchange.folder(args.parent_id, "pilot-work-drive-20261009", "Recepcion Work - Piloto OCR")
        config = load_config()
        config.update({"active_mode": "chatgpt_work", "work_drive_enabled": True,
                       "work_drive_parent_id": root["id"], "work_sync_account": actual,
                       "work_drive_query": "in:inbox newer_than:1d"})
        reports = [sync_work_drive(gmail, drive, config, tid) for tid in args.thread_id]
        checked = []
        for tid in args.thread_id:
            folder = exchange.find(root["id"], "thread:" + tid)
            if folder is None:
                raise ValueError("No se publicó el expediente.")
            pointer = exchange.find(folder["id"], "current")
            if pointer is None:
                raise ValueError("La publicación está incompleta.")
            manifest = exchange.read_json(pointer)
            verified = []
            for record in manifest["attachments"]:
                original = record.get("original")
                if not original:
                    continue
                raw = drive.files().get_media(fileId=original["id"]).execute()
                if hashlib.sha256(raw).hexdigest() != record["sha256"]:
                    raise ValueError("No coincide el original publicado.")
                for segment in record["text_segments"]:
                    text = drive.files().get_media(fileId=segment["id"]).execute()
                    if hashlib.sha256(text).hexdigest() != segment["sha256"]:
                        raise ValueError("No coincide un segmento publicado.")
                verified.append({"filename": record["filename"], "coverage": record["coverage"],
                                 "original_verified": True, "segments": len(record["text_segments"])})
            checked.append({"thread_id": tid, "folder": folder, "manifest_file": pointer,
                            "attachments": verified, "manifest": manifest})
        repeated = [sync_work_drive(gmail, drive, config, tid) for tid in args.thread_id]
        result = {"root": root, "reports": reports, "verified": checked, "repeated": repeated}
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps({"root": root, "verified_files": sum(len(r["attachments"]) for r in checked),
                          "repeat_unchanged": sum(r["unchanged"] for r in repeated)}, ensure_ascii=True))
        if any(r["failures"] for r in reports + repeated):
            raise SystemExit(1)


if __name__ == "__main__":
    main()
