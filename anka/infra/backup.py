"""Copias de seguridad de la base del dashboard: local y en Drive (sin credenciales ni token).

    python backup.py                  crea una copia ahora (local + Drive)
    python backup.py --no-drive       solo local
    python backup.py --restore ZIP    restaura quotes.sqlite3 desde una copia (guarda la actual)
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import shutil
import stat
from contextlib import closing
import tempfile
import zipfile
from datetime import datetime, timedelta
from pathlib import Path, PurePosixPath
from typing import Any

from anka.infra import settings
from anka.infra.logging_setup import configure_logging, get_logger

log = get_logger(__name__)

PREFIX = "anka-respaldo-"
KEEP = 14                      # copias que se conservan (local y Drive)
EVERY = timedelta(hours=24)
DRIVE_FOLDER = "Anka respaldos"
LAST_FILE = "last_backup.json"
# Solo estos archivos entran en la copia: nunca token.json ni credentials.json.
EXTRA_FILES = ("config.json", "work_dashboard_sync_state.json")


def backups_dir() -> Path:
    """Carpeta local donde se guardan las copias de seguridad."""
    return settings.DATA_DIR / "backups"


def create_backup() -> Path:
    """Copia consistente de la base (aunque el dashboard la esté usando) dentro de un .zip."""
    source = settings.DATA_DIR / "quotes.sqlite3"
    if not source.exists():
        raise FileNotFoundError(f"No existe {source}.")
    backups_dir().mkdir(parents=True, exist_ok=True)
    target = backups_dir() / f"{PREFIX}{datetime.now():%Y%m%d-%H%M%S}.zip"
    with tempfile.TemporaryDirectory() as temp:
        snapshot = Path(temp) / "quotes.sqlite3"
        origin, copy = sqlite3.connect(source), sqlite3.connect(snapshot)
        try:
            origin.backup(copy)
        finally:
            copy.close()
            origin.close()
        with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED) as archive:
            archive.write(snapshot, "quotes.sqlite3")
            for name in EXTRA_FILES:
                path = settings.DATA_DIR / name
                if path.exists():
                    archive.write(path, name)
            evidence = settings.DATA_DIR / 'evidence'
            for path in sorted(evidence.rglob('*.eml')):
                if path.is_file() and not path.is_symlink() and path.resolve().is_relative_to(evidence.resolve()):
                    archive.write(path, path.relative_to(settings.DATA_DIR).as_posix())
    return target


def prune_local(keep: int = KEEP) -> list[Path]:
    """Borra las copias locales más antiguas y deja las `keep` más recientes; devuelve las borradas."""
    files = sorted(backups_dir().glob(f"{PREFIX}*.zip"))
    old = files[:-keep] if keep else files
    for path in old:
        path.unlink(missing_ok=True)
    return old


def _drive_folder(drive: Any) -> str:
    query = (f"name = '{DRIVE_FOLDER}' and mimeType = 'application/vnd.google-apps.folder' "
             "and 'root' in parents and trashed = false")
    found = drive.files().list(q=query, fields="files(id)", pageSize=1).execute().get("files", [])
    if found:
        return found[0]["id"]
    created = drive.files().create(
        body={"name": DRIVE_FOLDER, "mimeType": "application/vnd.google-apps.folder"},
        fields="id").execute()
    return created["id"]


def upload_to_drive(drive: Any, path: Path, keep: int = KEEP) -> int:
    """Sube la copia y borra de Drive solo las copias propias (prefijo) más antiguas."""
    from googleapiclient.http import MediaIoBaseUpload

    folder = _drive_folder(drive)
    with path.open("rb") as handle:  # con `with` para no dejar el archivo bloqueado en Windows
        drive.files().create(
            body={"name": path.name, "parents": [folder]},
            media_body=MediaIoBaseUpload(handle, mimetype="application/zip"), fields="id").execute()
    listing = drive.files().list(
        q=f"'{folder}' in parents and name contains '{PREFIX}' and trashed = false",
        fields="files(id,name)", pageSize=200).execute().get("files", [])
    listing.sort(key=lambda item: item["name"])
    old = listing[:-keep] if keep else listing
    for item in old:
        drive.files().delete(fileId=item["id"]).execute()
    return len(old)


def run_backup(use_drive: bool = True, drive: Any = None) -> dict:
    """Crea la copia, la sube a Drive y deja constancia en last_backup.json."""
    result = {"at": datetime.now().astimezone().isoformat(timespec="seconds"),
              "file": "", "drive": False, "error": ""}
    try:
        path = create_backup()
        result["file"] = path.name
        prune_local()
        log.info("Copia de seguridad creada: %s", path.name)
    except Exception as exc:
        result["error"] = f"{type(exc).__name__}: {exc}"[:200]
        log.exception("No se pudo crear la copia de seguridad")
    else:
        if use_drive:
            try:
                if drive is None:
                    from anka.infra.google_services import services
                    _, drive = services()
                upload_to_drive(drive, path)
                result["drive"] = True
                log.info("Copia subida a Drive (%s)", DRIVE_FOLDER)
            except Exception as exc:
                result["error"] = f"Drive: {type(exc).__name__}: {exc}"[:200]
                log.warning("La copia local quedó, pero no se subió a Drive: %s", result["error"])
    try:
        (settings.DATA_DIR / LAST_FILE).write_text(json.dumps(result), encoding="utf-8")
    except OSError as exc:   # sin el registro la copia se repite en la siguiente vuelta: no se pierde nada
        log.warning("No se pudo anotar la última copia (%s); se volverá a intentar", type(exc).__name__)
    return result


def backup_due(now: datetime | None = None) -> bool:
    """True si la última copia exitosa tiene más de 24 h (o no hay ninguna)."""
    try:
        last = json.loads((settings.DATA_DIR / LAST_FILE).read_text(encoding="utf-8"))
        if not last.get("file"):
            return True
        return (now or datetime.now().astimezone()) - datetime.fromisoformat(last["at"]) >= EVERY
    except (OSError, ValueError, KeyError):
        return True


def restore(archive_path: Path) -> Path:
    """Valida y restaura base, ajustes y evidencia; conserva originales y revierte un fallo. Cierra Anka antes."""
    with tempfile.TemporaryDirectory() as temporary, zipfile.ZipFile(archive_path) as archive:
        stage = Path(temporary)
        names = _validate_archive(archive)
        for name in names:
            archive.extract(name, stage)
        with closing(sqlite3.connect(stage / 'quotes.sqlite3')) as db:
            if db.execute('PRAGMA integrity_check').fetchone()[0] != 'ok':
                raise ValueError('La base del respaldo no supera la comprobación de integridad.')
        _install_restore(stage, names)
    return settings.DATA_DIR / 'quotes.sqlite3'


def _validate_archive(archive: zipfile.ZipFile) -> list[str]:
    names = archive.namelist()
    if 'quotes.sqlite3' not in names or len(names) != len(set(names)):
        raise ValueError('Respaldo sin base de datos o con entradas repetidas.')
    if sum(info.file_size for info in archive.infolist()) > 2 * 1024**3:
        raise ValueError('El respaldo supera el límite de restauración de 2 GiB.')
    for info in archive.infolist():
        path = PurePosixPath(info.filename)
        allowed = info.filename in ('quotes.sqlite3', *EXTRA_FILES) or (
            len(path.parts) == 3 and path.parts[0] == 'evidence' and path.suffix == '.eml')
        if (not allowed or path.is_absolute() or '..' in path.parts or '\\' in info.filename
                or ':' in info.filename or stat.S_ISLNK(info.external_attr >> 16)):
            raise ValueError('El respaldo contiene una ruta o archivo no permitido.')
        target = (settings.DATA_DIR / info.filename).resolve()
        if not target.is_relative_to(settings.DATA_DIR.resolve()):
            raise ValueError('El destino del respaldo apunta fuera de la carpeta de datos.')
    return names


def _install_restore(stage: Path, names: list[str]) -> None:
    stamp = datetime.now().strftime('%Y%m%d-%H%M%S-%f')
    installed: list[tuple[Path, Path | None]] = []
    try:
        # La base se instala al final, cuando su evidencia ya está disponible.
        for name in sorted(names, key=lambda value: value == 'quotes.sqlite3'):
            target = settings.DATA_DIR / name
            target.parent.mkdir(parents=True, exist_ok=True)
            previous = target.with_name(target.name + '.before-restore-' + stamp) if target.exists() else None
            if previous:
                shutil.copy2(target, previous)
            installed.append((target, previous))
            shutil.copy2(stage / name, target)
    except Exception:
        for target, previous in reversed(installed):
            if previous:
                shutil.copy2(previous, target)
            elif target.exists():
                target.unlink()
        raise


def main() -> None:
    """Comando de copias: crea una copia (local y en Drive) o restaura una con `--restore`."""
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--no-drive", action="store_true")
    parser.add_argument("--restore", metavar="ZIP")
    args = parser.parse_args()
    configure_logging()
    if args.restore:
        print("Restaurada:", restore(Path(args.restore)))
        return
    result = run_backup(use_drive=not args.no_drive)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    raise SystemExit(1 if result["error"] and not result["file"] else 0)


if __name__ == "__main__":
    main()
