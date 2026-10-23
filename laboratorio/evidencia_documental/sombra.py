"""Modo sombra de la evidencia documental (T-119). Prototipo de laboratorio: NO está conectado a ANKA.

Idea: cuando llegue un adjunto, *además* del flujo normal, se podría extraer y validar su evidencia en segundo plano y guardarla aparte,
sin que nadie la use todavía. Este módulo es ese «aparte», probado solo con documentos ficticios y los casos del banco.

Garantías (cada una tiene prueba en `test_sombra.py`):
- **Apagado por defecto**: sin habilitarlo explícitamente (`habilitada=True` o `ANKA_EVIDENCIA_SOMBRA=1`) no hace nada ni crea la base.
  El archivo interruptor (`archivo_apagado`) lo detiene aunque esté habilitado.
- **Base propia**: SQLite separada; se rechaza cualquier ruta dentro de la carpeta de datos de ANKA o con el nombre de la base operativa.
- **Identidad**: (job_id, SHA-256 del archivo, versión del extractor). Repetir la misma operación no duplica filas; cambiar la versión del
  extractor crea una fila nueva y conserva la anterior. Una fila fallida se reintenta (se reemplaza); una fila buena nunca se sobrescribe.
- **Plazo**: la extracción corre en un proceso hijo que se mata al vencer el plazo del trabajo (todos sus archivos juntos).
- **Aislamiento**: `procesar` nunca lanza; cualquier fallo (hijo caído, plazo, base bloqueada, ruta prohibida) vuelve como estado.
  `lanzar` lo ejecuta en un hilo aparte para que el receptor no espere.
- **Sin efectos externos**: no importa Gmail, Drive, Work, red ni la SQLite operativa; solo lee el contenido que recibe y escribe en su base.
- **Sin veredicto de exhaustividad**: guarda `exhaustividad = no_determinable`; que el validador no dé errores no significa que se haya
  recuperado todo.
"""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import subprocess
import sys
import tempfile
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from laboratorio.evidencia_documental.esquema import VERSION_MODELO
from laboratorio.evidencia_documental.versiones import VERSION_EXTRACTOR, VERSION_VALIDADOR

INTERRUPTOR_ENTORNO = "ANKA_EVIDENCIA_SOMBRA"
VERSION_ESQUEMA_SOMBRA = 1
RAIZ = Path(__file__).resolve().parent.parent.parent
NOMBRES_PROHIBIDOS = ("anka.db", "anka.sqlite", "store.db")

ESTADOS = ("ok", "ok_con_errores_de_validacion", "fallo", "plazo_excedido")

_ESQUEMA = """
CREATE TABLE IF NOT EXISTS meta (clave TEXT PRIMARY KEY, valor TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS extraccion (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    job_id TEXT NOT NULL, sha256 TEXT NOT NULL, archivo TEXT NOT NULL,
    version_extractor TEXT NOT NULL, version_validador TEXT NOT NULL, version_modelo TEXT NOT NULL,
    estado TEXT NOT NULL, error TEXT, creado REAL NOT NULL, duracion_s REAL,
    n_evidencias INTEGER, n_errores INTEGER, n_avisos INTEGER, exhaustividad TEXT NOT NULL DEFAULT 'no_determinable',
    UNIQUE (job_id, sha256, version_extractor)
);
CREATE TABLE IF NOT EXISTS evidencia (
    extraccion_id INTEGER NOT NULL REFERENCES extraccion(id) ON DELETE CASCADE, evidencia_id TEXT NOT NULL, json TEXT NOT NULL,
    PRIMARY KEY (extraccion_id, evidencia_id)
);
CREATE TABLE IF NOT EXISTS hallazgo (
    extraccion_id INTEGER NOT NULL REFERENCES extraccion(id) ON DELETE CASCADE,
    codigo TEXT NOT NULL, severidad TEXT NOT NULL, ruta TEXT NOT NULL, mensaje TEXT NOT NULL
);
"""


@dataclass(frozen=True)
class Configuracion:
    """Cómo corre el modo sombra. `habilitada=False` es el valor por defecto y significa «no hacer nada»."""

    ruta_db: Path
    habilitada: bool = False
    plazo_s: float = 60.0
    archivo_apagado: Path | None = None
    ocr: bool = True
    comando: tuple[str, ...] | None = None   # solo para pruebas: reemplaza al proceso hijo real


def desde_entorno(ruta_db: Path, **kw: Any) -> Configuracion:
    """Configuración cuyo interruptor se lee de `ANKA_EVIDENCIA_SOMBRA` (solo «1» lo enciende)."""
    return Configuracion(ruta_db=ruta_db, habilitada=os.environ.get(INTERRUPTOR_ENTORNO) == "1", **kw)


def _carpeta_operativa() -> Path:
    base = os.environ.get("LOCALAPPDATA") or str(Path.home() / ".local" / "share")
    return (Path(base) / "AnkaCorreos").resolve()


def _comprobar_ruta(ruta: Path) -> None:
    destino = ruta.resolve()
    if destino.name.lower() in NOMBRES_PROHIBIDOS:
        raise ValueError("el nombre coincide con una base operativa de ANKA")
    operativa = _carpeta_operativa()
    if destino == operativa or operativa in destino.parents:
        raise ValueError("la base de la sombra no puede estar en la carpeta de datos de ANKA")


def _sha256(contenido: bytes) -> str:
    return hashlib.sha256(contenido).hexdigest()


class Sombra:
    """Extracción y validación en paralelo, almacenadas aparte. Ver el docstring del módulo."""

    def __init__(self, config: Configuracion) -> None:
        self.config = config

    # ------------------------------------------------------------ interruptor
    def activa(self) -> bool:
        """¿Hay que trabajar? Falso si no está habilitada o si existe el archivo interruptor."""
        c = self.config
        return c.habilitada and not (c.archivo_apagado is not None and c.archivo_apagado.exists())

    # ------------------------------------------------------------ almacenamiento
    def _conectar(self) -> sqlite3.Connection:
        _comprobar_ruta(self.config.ruta_db)
        self.config.ruta_db.parent.mkdir(parents=True, exist_ok=True)
        con = sqlite3.connect(self.config.ruta_db, timeout=2.0)
        con.execute("PRAGMA foreign_keys = ON")
        con.executescript(_ESQUEMA)
        con.execute("INSERT OR IGNORE INTO meta VALUES ('version_esquema', ?)", (str(VERSION_ESQUEMA_SOMBRA),))
        return con

    def _guardar(self, con: sqlite3.Connection, job_id: str, sha: str, archivo: str, estado: str, error: str | None,
                 resultado: dict[str, Any] | None, creado: float) -> str:
        """Inserta la fila de este (job, huella, versión). Devuelve «nueva», «reemplazada» o «existente»."""
        previa = con.execute("SELECT id, estado FROM extraccion WHERE job_id=? AND sha256=? AND version_extractor=?",
                             (job_id, sha, VERSION_EXTRACTOR)).fetchone()
        if previa and previa[1] in ("ok", "ok_con_errores_de_validacion"):
            return "existente"
        with con:   # una sola transacción: o queda todo o no queda nada
            if previa:
                con.execute("DELETE FROM extraccion WHERE id=?", (previa[0],))
            hallazgos = (resultado or {}).get("hallazgos", [])
            n_err = sum(1 for h in hallazgos if h["severidad"] == "error")
            doc = (resultado or {}).get("doc", {"evidencias": []})
            cur = con.execute(
                "INSERT INTO extraccion (job_id, sha256, archivo, version_extractor, version_validador, version_modelo, estado, error, creado,"
                " duracion_s, n_evidencias, n_errores, n_avisos) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (job_id, sha, archivo, VERSION_EXTRACTOR, VERSION_VALIDADOR, VERSION_MODELO, estado, error, creado,
                 (resultado or {}).get("duracion_s"), len(doc["evidencias"]), n_err, len(hallazgos) - n_err))
            for e in doc["evidencias"]:
                con.execute("INSERT INTO evidencia VALUES (?,?,?)", (cur.lastrowid, e["id"], json.dumps(e, ensure_ascii=False, sort_keys=True)))
            for h in hallazgos:
                con.execute("INSERT INTO hallazgo VALUES (?,?,?,?,?)", (cur.lastrowid, h["codigo"], h["severidad"], h["ruta"], h["mensaje"]))
        return "reemplazada" if previa else "nueva"

    # ------------------------------------------------------------ ejecución del hijo
    def _extraer(self, job_id: str, nombre: str, contenido: bytes, restante_s: float) -> tuple[str, str | None, dict[str, Any] | None]:
        """(estado, error, resultado) de extraer un archivo en un proceso hijo con plazo."""
        if restante_s <= 0:
            return "plazo_excedido", "el plazo del trabajo se agotó antes de empezar este archivo", None
        with tempfile.TemporaryDirectory(prefix="anka-sombra-") as tmp:
            entrada, salida = Path(tmp) / "contenido.bin", Path(tmp) / "salida.json"
            entrada.write_bytes(contenido)
            comando = list(self.config.comando or (sys.executable, "-X", "utf8", "-m", "laboratorio.evidencia_documental.sombra_trabajador"))
            comando += [job_id, nombre, str(entrada), str(salida), "1" if self.config.ocr else "0"]
            entorno = {**os.environ, "PYTHONPATH": str(RAIZ), "PYTHONIOENCODING": "utf-8"}
            try:
                hijo = subprocess.run(comando, cwd=RAIZ, env=entorno, timeout=restante_s, capture_output=True, check=False)   # noqa: S603
            except subprocess.TimeoutExpired:
                return "plazo_excedido", f"el proceso de extracción superó el plazo ({self.config.plazo_s:g} s) y se detuvo", None
            if hijo.returncode != 0:
                tipo = ""
                try:
                    tipo = json.loads(salida.read_text(encoding="utf-8")).get("error", "")
                except (OSError, ValueError):
                    pass
                return "fallo", f"el proceso terminó con código {hijo.returncode}" + (f" ({tipo})" if tipo else ""), None
            try:
                resultado = json.loads(salida.read_text(encoding="utf-8"))
                resultado["doc"]["evidencias"]
            except (OSError, ValueError, KeyError, TypeError):
                return "fallo", "el proceso no dejó un resultado legible", None
        errores = sum(1 for h in resultado["hallazgos"] if h["severidad"] == "error")
        return ("ok_con_errores_de_validacion" if errores else "ok"), None, resultado

    # ------------------------------------------------------------ API
    def procesar(self, job_id: str, archivos: dict[str, bytes]) -> dict[str, Any]:
        """Extrae y guarda cada archivo del trabajo. **Nunca lanza**: devuelve un estado por archivo y uno global."""
        try:
            if not self.activa():
                return {"estado": "desactivado", "archivos": {}}
            inicio = time.monotonic()
            salida: dict[str, Any] = {}
            con = self._conectar()
            try:
                for nombre in sorted(archivos):
                    contenido = archivos[nombre]
                    sha = _sha256(contenido)
                    previa = con.execute(
                        "SELECT estado FROM extraccion WHERE job_id=? AND sha256=? AND version_extractor=?",
                        (job_id, sha, VERSION_EXTRACTOR)).fetchone()
                    if previa and previa[0] in ("ok", "ok_con_errores_de_validacion"):
                        salida[nombre] = {"estado": "existente", "sha256": sha}
                        continue
                    restante = self.config.plazo_s - (time.monotonic() - inicio)
                    estado, error, resultado = self._extraer(job_id, nombre, contenido, restante)
                    escrito = self._guardar(con, job_id, sha, nombre, estado, error, resultado, time.time())
                    salida[nombre] = {"estado": estado, "sha256": sha, "fila": escrito, **({"error": error} if error else {})}
            finally:
                con.close()
            return {"estado": "procesado", "archivos": salida}
        except Exception as exc:   # el receptor jamás debe enterarse de un fallo de la sombra  # noqa: BLE001
            return {"estado": "error_aislado", "error": f"{type(exc).__name__}: {str(exc)[:200]}", "archivos": {}}

    def lanzar(self, job_id: str, archivos: dict[str, bytes]) -> threading.Thread:
        """Ejecuta `procesar` en un hilo aparte y devuelve de inmediato. El hilo es demonio: no retiene el cierre del programa."""
        hilo = threading.Thread(target=self.procesar, args=(job_id, dict(archivos)), name=f"sombra-{job_id}", daemon=True)
        hilo.start()
        return hilo

    def consultar(self, job_id: str) -> list[dict[str, Any]]:
        """Filas guardadas para un trabajo (solo lectura). Lista vacía si no hay base."""
        if not self.config.ruta_db.exists():
            return []
        con = sqlite3.connect(f"file:{self.config.ruta_db.as_posix()}?mode=ro", uri=True, timeout=2.0)
        try:
            con.row_factory = sqlite3.Row
            return [dict(r) for r in con.execute("SELECT * FROM extraccion WHERE job_id=? ORDER BY id", (job_id,))]
        finally:
            con.close()
