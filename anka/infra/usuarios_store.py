"""Lectura y escritura de `usuarios.json` (carpeta de datos, fuera del repositorio): quién puede entrar al
dashboard y el hash de su contraseña (U-115, D-210). Nunca guarda una contraseña en claro."""

from __future__ import annotations

import json
from pathlib import Path

from anka.dominio.errores import ErrorDeAnka
from anka.dominio.usuarios import RegistroDeUsuario, generar_hash, nombre_valido
from anka.infra import settings
from anka.infra.archivos_privados import escribir_privado


def _ruta() -> Path:
    return settings.DATA_DIR / "usuarios.json"


def cargar() -> dict[str, RegistroDeUsuario]:
    """Los usuarios guardados; vacío si todavía no existe el archivo (antes de la primera alta)."""
    path = _ruta()
    if not path.exists():
        return {}
    try:
        datos = json.loads(path.read_text(encoding="utf-8-sig"))
    except json.JSONDecodeError as exc:
        raise ErrorDeAnka(f"{path} no es JSON válido (línea {exc.lineno}, columna {exc.colno}): {exc.msg}.") from exc
    if not isinstance(datos, dict):
        raise ErrorDeAnka(f"{path} debe contener un objeto JSON, no {type(datos).__name__}.")
    return datos


def _guardar(usuarios: dict[str, RegistroDeUsuario]) -> None:
    escribir_privado(_ruta(), json.dumps(usuarios, ensure_ascii=False, indent=2) + "\n")


def obtener(nombre: str) -> RegistroDeUsuario | None:
    """El registro guardado de `nombre` (hash, sal, iteraciones), o `None` si no existe."""
    return cargar().get(nombre)


def listar() -> list[str]:
    """Los nombres de usuario dados de alta, en orden alfabético."""
    return sorted(cargar())


def agregar_o_actualizar(nombre: str, contraseña: str) -> None:
    """Da de alta a `nombre` o le cambia la contraseña si ya existía. No hay pantalla pública para esto: es un
    comando de `anka/cli` (U-115.2)."""
    if not nombre_valido(nombre):
        raise ErrorDeAnka(
            "El usuario debe tener de 2 a 40 caracteres: minúsculas, dígitos, punto, guion o guion bajo, "
            "y no puede empezar por un símbolo.")
    if len(contraseña) < 8:
        raise ErrorDeAnka("La contraseña debe tener al menos 8 caracteres.")
    usuarios = cargar()
    usuarios[nombre] = generar_hash(contraseña)
    _guardar(usuarios)


def quitar(nombre: str) -> bool:
    """Quita a `nombre`; `False` si no existía."""
    usuarios = cargar()
    if nombre not in usuarios:
        return False
    del usuarios[nombre]
    _guardar(usuarios)
    return True
