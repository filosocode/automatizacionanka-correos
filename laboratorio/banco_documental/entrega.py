"""Entrega de los documentos al sistema evaluado SIN las referencias (T-71).

`preparar_entrega` copia solo `documentos/` a una carpeta de fuera del banco; `verificar_entrega` comprueba que allí no hay
nada de las referencias. Uso: `python -m laboratorio.banco_documental.entrega <carpeta_destino>`.
"""

from __future__ import annotations

import json
import re
import shutil
import sys
from pathlib import Path

from laboratorio.banco_documental import construir

_ID_DE_REFERENCIA = re.compile(rb"\bC[123]-(?:A|R|K|F|I|T|V)\d\b")
_PISTAS = (b"referencia.json", b"manifiesto_referencias", b"banco_documental", b"alternativas_aceptadas")


class EntregaInsegura(ValueError):
    """El destino o su contenido dejaría al sistema evaluado cerca de la respuesta."""


def _dentro_de(ruta: Path, carpeta: Path) -> bool:
    return ruta == carpeta or carpeta in ruta.parents


def preparar_entrega(destino: Path) -> list[Path]:
    """Copia los documentos y su manifiesto a `destino`, que debe estar fuera del banco y vacío o inexistente."""
    destino = destino.resolve()
    if _dentro_de(destino, construir.RAIZ) or _dentro_de(construir.RAIZ, destino):
        raise EntregaInsegura("El destino debe estar fuera de la carpeta del banco (y no la contiene).")
    if destino.exists() and any(destino.iterdir()):
        raise EntregaInsegura("El destino ya tiene archivos; use una carpeta vacía.")
    destino.mkdir(parents=True, exist_ok=True)
    copiados = []
    for origen in sorted(construir.DOCUMENTOS.iterdir()):
        if origen.is_file():
            copiados.append(Path(shutil.copy2(origen, destino / origen.name)))
    verificar_entrega(destino)
    return copiados


def verificar_entrega(destino: Path) -> None:
    """Falla si en `destino` hay referencias, ids de referencia, pistas del banco o archivos fuera del manifiesto."""
    manifiesto = json.loads((destino / "manifiesto_documentos.json").read_text(encoding="utf-8"))
    permitidos = {"manifiesto_documentos.json", *(d["archivo"] for d in manifiesto["documentos"])}
    for ruta in destino.rglob("*"):
        if ruta.is_dir():
            raise EntregaInsegura(f"Hay una subcarpeta inesperada: {ruta.name}")
        if ruta.name not in permitidos:
            raise EntregaInsegura(f"Archivo no previsto en la entrega: {ruta.name}")
        contenido = ruta.read_bytes()
        if ruta.suffix == ".json" and (_ID_DE_REFERENCIA.search(contenido) or any(p in contenido for p in _PISTAS)):
            raise EntregaInsegura(f"{ruta.name} menciona referencias o ids internos")
        if ruta.suffix in {".pdf", ".xlsx"} and _ID_DE_REFERENCIA.search(contenido):
            raise EntregaInsegura(f"{ruta.name} contiene ids de referencia")


def main(argumentos: list[str]) -> int:
    """Prepara la entrega en la carpeta indicada."""
    if len(argumentos) != 1:
        print("Uso: python -m laboratorio.banco_documental.entrega <carpeta_destino>")
        return 2
    for ruta in preparar_entrega(Path(argumentos[0])):
        print(ruta)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
