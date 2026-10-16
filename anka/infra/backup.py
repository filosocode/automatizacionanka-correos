"""Copias de seguridad de la base del dashboard: local y en Drive (sin credenciales ni token).

    python backup.py                  crea una copia ahora (local + Drive)
    python backup.py --no-drive       solo local
    python backup.py --restore ZIP    restaura quotes.sqlite3 desde una copia (guarda la actual)
"""

from __future__ import annotations
from anka.infra.clientes_google import ClienteDrive

import argparse
import json
import os
import re
import sqlite3
import shutil
import socket
import stat
from contextlib import closing
import tempfile
import zipfile
from datetime import datetime, timedelta
from pathlib import Path, PurePosixPath

from anka.dominio.reloj import ahora, ahora_iso
from anka.infra import acciones_en_curso, settings
from anka.infra.logging_setup import configure_logging, get_logger

log = get_logger(__name__)

PREFIX = "anka-respaldo-"
KEEP = 14                      # copias que se conservan (local y Drive)
EVERY = timedelta(hours=24)
ESPERA_DE_LA_BASE = 15       # segundos que se espera si otro proceso escribe (igual que la base viva)
PUERTO_DEL_DASHBOARD = 8765  # puerto del dashboard del lanzador: restaurar por consola lo exige libre
DRIVE_FOLDER = "Anka respaldos"
LAST_FILE = "last_backup.json"
# Solo estos archivos entran en la copia: nunca token.json, credentials.json ni config.json (U-78).
EXTRA_FILES = ("work_dashboard_sync_state.json",)
CONFIG = "config.json"
CONFIG_REDACTADO = "config.redactado.json"   # config.json sin secretos: lo único de la configuración que viaja
# Copias anteriores a U-78 traen config.json: al restaurarlas se acepta (es un archivo de datos, no se ejecuta).
NOMBRES_QUE_SE_RESTAURAN = ("quotes.sqlite3", *EXTRA_FILES, CONFIG, CONFIG_REDACTADO)
MARCA_REDACTADA = "[REDACTADO]"
# Nombres de clave que se redactan (sin distinguir mayúsculas), a cualquier profundidad.
_CLAVE_SENSIBLE = re.compile(
    r"(api[_-]?key|apikey|secret|token(?!s)|passw|pwd|credential|private[_-]?key|authorization|bearer|cookie|session"
    r"|signature|(^|[_-])key$|[_-]auth$|^auth)", re.IGNORECASE)
# Valores que parecen un secreto aunque la clave tenga un nombre inocente.
_VALOR_SENSIBLE = re.compile(
    r"(sk-[A-Za-z0-9_-]{16,}|ya29\.[A-Za-z0-9_-]{10,}|AIza[0-9A-Za-z_-]{20,}|gh[pousr]_[A-Za-z0-9]{20,}"
    r"|xox[abprs]-[A-Za-z0-9-]{10,}|-----BEGIN [A-Z ]*PRIVATE KEY-----|\bBearer\s+[A-Za-z0-9._~+/-]{16,}"
    r"|\b1//[0-9A-Za-z_-]{20,})")
_USUARIO_EN_URL = re.compile(r"(?P<esquema>\b[a-z][a-z0-9+.-]*://)[^/\s@]+@", re.IGNORECASE)


def _redactar_valor(valor: object) -> object:
    if isinstance(valor, dict):
        return {clave: (MARCA_REDACTADA if _CLAVE_SENSIBLE.search(str(clave)) else _redactar_valor(contenido))
                for clave, contenido in valor.items()}
    if isinstance(valor, list):
        return [_redactar_valor(item) for item in valor]
    if isinstance(valor, str):
        if _VALOR_SENSIBLE.search(valor):
            return MARCA_REDACTADA
        return _USUARIO_EN_URL.sub(r"\g<esquema>", valor)     # https://usuario:clave@host -> https://host
    return valor


def redactar_config(config: dict) -> dict:
    """Copia de la configuración sin secretos: redacta por nombre de clave y por aspecto del valor (U-78)."""
    resultado = _redactar_valor(config)
    assert isinstance(resultado, dict)
    return resultado


def _config_redactado() -> str | None:
    """El `config.json` vivo ya redactado, o None si no existe o no se puede leer (nunca se copia en bruto)."""
    ruta = settings.DATA_DIR / CONFIG
    try:
        contenido = json.loads(ruta.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None
    except (OSError, ValueError):
        log.warning("config.json no se pudo leer; la copia sale sin configuración")
        return None
    if not isinstance(contenido, dict):
        log.warning("config.json no es un objeto JSON; la copia sale sin configuración")
        return None
    return json.dumps(redactar_config(contenido), ensure_ascii=False, indent=2)


def backups_dir() -> Path:
    """Carpeta local donde se guardan las copias de seguridad."""
    return settings.DATA_DIR / "backups"


def create_backup() -> Path:
    """Copia consistente de la base (aunque el dashboard la esté usando) dentro de un .zip."""
    source = settings.DATA_DIR / "quotes.sqlite3"
    if not source.exists():
        raise FileNotFoundError(f"No existe {source}.")
    backups_dir().mkdir(parents=True, exist_ok=True)
    target = backups_dir() / f"{PREFIX}{ahora():%Y%m%d-%H%M%S}.zip"
    with tempfile.TemporaryDirectory() as temp:
        snapshot = Path(temp) / "quotes.sqlite3"
        origin, copy = sqlite3.connect(source, timeout=ESPERA_DE_LA_BASE), sqlite3.connect(snapshot)
        try:
            origin.backup(copy)
            copy.execute("PRAGMA journal_mode=DELETE")     # la copia es un solo archivo, sin -wal ni -shm (U-42)
            if copy.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                raise ValueError("La copia recién hecha no supera la comprobación de integridad.")
        finally:
            copy.close()
            origin.close()
        with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED) as archive:
            archive.write(snapshot, "quotes.sqlite3")
            for name in EXTRA_FILES:
                path = settings.DATA_DIR / name
                if path.exists():
                    archive.write(path, name)
            redactado = _config_redactado()
            if redactado is not None:
                archive.writestr(CONFIG_REDACTADO, redactado)
            evidence = settings.DATA_DIR / 'evidence'
            for path in sorted(evidence.rglob('*.eml')):
                if path.is_file() and not path.is_symlink() and path.resolve().is_relative_to(evidence.resolve()):
                    archive.write(path, path.relative_to(settings.DATA_DIR).as_posix())
    try:
        _verificar_zip(target)
    except Exception:
        target.unlink(missing_ok=True)       # una copia que no se puede abrir no es una copia: no se deja ni se cuenta
        raise
    return target


def _verificar_zip(path: Path) -> None:
    """Abre la copia recién hecha en una carpeta temporal y comprueba que su base sirve (integridad y versión)."""
    with tempfile.TemporaryDirectory() as temporal, zipfile.ZipFile(path) as archive:
        malo = archive.testzip()                  # lee todo el zip: detecta una entrada dañada
        if malo is not None:
            raise ValueError(f"La copia recién hecha tiene dañada la entrada {malo}.")
        if CONFIG in archive.namelist():
            raise ValueError("La copia recién hecha trae config.json en bruto: podría llevar secretos (U-78).")
        archive.extract("quotes.sqlite3", temporal)
        _comprobar_base(Path(temporal) / "quotes.sqlite3")


def prune_local(keep: int = KEEP) -> list[Path]:
    """Borra las copias locales más antiguas y deja las `keep` más recientes; devuelve las borradas."""
    files = sorted(backups_dir().glob(f"{PREFIX}*.zip"))
    old = files[:-keep] if keep else files
    for path in old:
        path.unlink(missing_ok=True)
    return old


def _drive_folder(drive: ClienteDrive) -> str:
    query = (f"name = '{DRIVE_FOLDER}' and mimeType = 'application/vnd.google-apps.folder' "
             "and 'root' in parents and trashed = false")
    found = drive.files().list(q=query, fields="files(id)", pageSize=1).execute().get("files", [])
    if found:
        return found[0]["id"]
    created = drive.files().create(
        body={"name": DRIVE_FOLDER, "mimeType": "application/vnd.google-apps.folder"},
        fields="id").execute()
    return created["id"]


def upload_to_drive(drive: ClienteDrive, path: Path, keep: int = KEEP) -> int:
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


def run_backup(use_drive: bool = True, drive: ClienteDrive | None = None) -> dict:
    """Crea la copia, la sube a Drive y deja constancia en last_backup.json."""
    result = {"at": ahora_iso("seconds"),
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


def anotar_fallo(mensaje: str) -> None:
    """Deja en last_backup.json que la copia falló (p. ej. porque el lanzador la canceló por tardar demasiado)."""
    resultado = {"at": ahora_iso("seconds"), "file": "", "drive": False, "error": mensaje[:200]}
    try:
        (settings.DATA_DIR / LAST_FILE).write_text(json.dumps(resultado), encoding="utf-8")
    except OSError as exc:
        log.warning("No se pudo anotar el fallo de la copia (%s)", type(exc).__name__)


def backup_due(now: datetime | None = None) -> bool:
    """True si la última copia exitosa tiene más de 24 h (o no hay ninguna)."""
    try:
        last = json.loads((settings.DATA_DIR / LAST_FILE).read_text(encoding="utf-8"))
        if not last.get("file"):
            return True
        return (now or ahora()) - datetime.fromisoformat(last["at"]) >= EVERY
    except (OSError, ValueError, KeyError):
        return True


def restore(archive_path: Path) -> Path:
    """Valida y restaura base, ajustes y evidencia; conserva originales y revierte un fallo. Cierra Anka antes."""
    from anka.infra.work_heartbeat import other_bridge_running
    if other_bridge_running():
        raise ValueError('El puente está corriendo: cierra el lanzador («Iniciar Anka») antes de restaurar.')
    if acciones_en_curso.acciones_activas() or _base_en_uso():
        raise ValueError('La base está en uso: cierra el lanzador («Iniciar Anka») antes de restaurar.')
    with tempfile.TemporaryDirectory() as temporary, zipfile.ZipFile(archive_path) as archive:
        stage = Path(temporary)
        names = _validate_archive(archive)
        for name in names:
            archive.extract(name, stage)
        _comprobar_base(stage / 'quotes.sqlite3')
        names = _resolver_configuracion(stage, names)
        _install_restore(stage, names)
    return settings.DATA_DIR / 'quotes.sqlite3'


def _resolver_configuracion(stage: Path, names: list[str]) -> list[str]:
    """La configuración redactada nunca pisa la viva: solo se instala como `config.json` si no hay ninguna (U-78)."""
    if CONFIG_REDACTADO not in names:
        return names
    resto = [name for name in names if name != CONFIG_REDACTADO]
    if CONFIG not in names and not (settings.DATA_DIR / CONFIG).exists():
        os.replace(stage / CONFIG_REDACTADO, stage / CONFIG)
        resto.append(CONFIG)
        log.warning("Se restauró config.json SIN secretos: hay que volver a poner las claves y revisar el archivo")
    return resto


def _base_en_uso() -> bool:
    """¿Otro proceso tiene una escritura en curso en la base viva? (no puede cambiarse de debajo a quien la usa)."""
    base = settings.DATA_DIR / "quotes.sqlite3"
    if not base.exists():
        return False
    try:
        with closing(sqlite3.connect(base, timeout=1, isolation_level=None)) as db:
            db.execute("BEGIN IMMEDIATE")
            db.execute("ROLLBACK")
    except sqlite3.OperationalError:
        return True
    return False


def dashboard_en_marcha(puerto: int = PUERTO_DEL_DASHBOARD) -> bool:
    """¿Contesta algo en el puerto del dashboard? (la restauración desde la línea de comandos pide que no)."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as conexion:
        conexion.settimeout(0.5)
        return conexion.connect_ex(("127.0.0.1", puerto)) == 0


def _comprobar_base(path: Path) -> None:
    """Integridad y versión de esquema de una base de respaldo; no la modifica."""
    from anka.infra.store import SCHEMA_VERSION
    with closing(sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True)) as db:
        if db.execute('PRAGMA integrity_check').fetchone()[0] != 'ok':
            raise ValueError('La base del respaldo no supera la comprobación de integridad.')
        version = db.execute('PRAGMA user_version').fetchone()[0]
    if version > SCHEMA_VERSION:
        raise ValueError(f'El respaldo es de un esquema más nuevo ({version}) que este programa ({SCHEMA_VERSION}).')


def verificar_ultima_copia() -> dict:
    """Abre la última copia local en una carpeta temporal (nunca sobre la base viva) y comprueba que sirve (B3-10)."""
    copias = sorted(backups_dir().glob(f"{PREFIX}*.zip"))
    if not copias:
        return {"ok": False, "file": "", "error": "No hay copias locales."}
    try:
        with tempfile.TemporaryDirectory() as temporary, zipfile.ZipFile(copias[-1]) as archive:
            _validate_archive(archive)
            archive.extract('quotes.sqlite3', temporary)
            destino = Path(temporary) / 'restaurada.sqlite3'
            os.replace(Path(temporary) / 'quotes.sqlite3', destino)
            _comprobar_base(destino)
            with closing(sqlite3.connect(f"file:{destino.as_posix()}?mode=ro", uri=True)) as db:
                casos = db.execute("SELECT COUNT(*) FROM quotes").fetchone()[0]
        return {"ok": True, "file": copias[-1].name, "casos": casos, "error": ""}
    except (OSError, ValueError, sqlite3.Error, zipfile.BadZipFile) as exc:
        return {"ok": False, "file": copias[-1].name, "error": f"{type(exc).__name__}: {exc}"[:200]}


def _validate_archive(archive: zipfile.ZipFile) -> list[str]:
    names = archive.namelist()
    if 'quotes.sqlite3' not in names or len(names) != len(set(names)):
        raise ValueError('Respaldo sin base de datos o con entradas repetidas.')
    if sum(info.file_size for info in archive.infolist()) > 2 * 1024**3:
        raise ValueError('El respaldo supera el límite de restauración de 2 GiB.')
    for info in archive.infolist():
        path = PurePosixPath(info.filename)
        allowed = info.filename in NOMBRES_QUE_SE_RESTAURAN or (
            len(path.parts) == 3 and path.parts[0] == 'evidence' and path.suffix == '.eml')
        if (not allowed or path.is_absolute() or '..' in path.parts or '\\' in info.filename
                or ':' in info.filename or stat.S_ISLNK(info.external_attr >> 16)):
            raise ValueError('El respaldo contiene una ruta o archivo no permitido.')
        target = (settings.DATA_DIR / info.filename).resolve()
        if not target.is_relative_to(settings.DATA_DIR.resolve()):
            raise ValueError('El destino del respaldo apunta fuera de la carpeta de datos.')
    return names


def _instalar_base(origen: Path, destino: Path) -> None:
    """Instala la base con archivo temporal y `os.replace`, sin dejar -wal/-shm viejos que se aplicarían encima."""
    temporal = destino.with_name(destino.name + '.restaurando')
    shutil.copy2(origen, temporal)
    for sufijo in ('-wal', '-shm'):
        destino.with_name(destino.name + sufijo).unlink(missing_ok=True)
    os.replace(temporal, destino)      # en Windows falla si otro proceso la tiene abierta: se avisa y no se pierde nada


def _copia_previa(target: Path, previous: Path) -> None:
    """Guarda lo que había antes; una base viva se copia con la API de SQLite para incluir lo del -wal."""
    if target.name == 'quotes.sqlite3':
        origen, copia = sqlite3.connect(target, timeout=ESPERA_DE_LA_BASE), sqlite3.connect(previous)
        try:
            origen.backup(copia)
        finally:
            copia.close()
            origen.close()
    else:
        shutil.copy2(target, previous)


def _install_restore(stage: Path, names: list[str]) -> None:
    stamp = ahora().strftime('%Y%m%d-%H%M%S-%f')
    installed: list[tuple[Path, Path | None]] = []
    try:
        # La base se instala al final, cuando su evidencia ya está disponible.
        for name in sorted(names, key=lambda value: value == 'quotes.sqlite3'):
            target = settings.DATA_DIR / name
            target.parent.mkdir(parents=True, exist_ok=True)
            previous = target.with_name(target.name + '.before-restore-' + stamp) if target.exists() else None
            if previous:
                _copia_previa(target, previous)
            installed.append((target, previous))
            if name == 'quotes.sqlite3':
                _instalar_base(stage / name, target)
            else:
                shutil.copy2(stage / name, target)
    except Exception:
        for target, previous in reversed(installed):
            if previous and target.name == 'quotes.sqlite3':
                _instalar_base(previous, target)
            elif previous:
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
        if dashboard_en_marcha():
            raise SystemExit("El dashboard está abierto: cierra el lanzador («Iniciar Anka») antes de restaurar.")
        print("Restaurada:", restore(Path(args.restore)))
        print("La copia no lleva secretos: si config.json se restauró desde config.redactado.json, revisa los valores "
              f"{MARCA_REDACTADA}; vuelve a autorizar Google (python app.py auth) y a poner OPENAI_API_KEY "
              "si usas el modo api.")
        return
    result = run_backup(use_drive=not args.no_drive)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    raise SystemExit(1 if result["error"] and not result["file"] else 0)


if __name__ == "__main__":
    main()
