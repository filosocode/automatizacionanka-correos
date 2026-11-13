"""Construye el banco: documentos, referencias y manifiestos con SHA-256 (T-71). Uso: `python -m laboratorio.banco_documental.construir`.

Los documentos van a `documentos/` (lo único que puede ver el sistema evaluado) y las referencias a `referencias/`, en
carpetas hermanas y con manifiestos separados. `--verificar` regenera todo en memoria y compara contra lo guardado.
"""

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path
from typing import Any

from laboratorio.banco_documental import datos, generar

RAIZ = Path(__file__).resolve().parent
DOCUMENTOS = RAIZ / "documentos"
REFERENCIAS = RAIZ / "referencias"
MANIFIESTO_DOCUMENTOS = DOCUMENTOS / "manifiesto_documentos.json"
MANIFIESTO_REFERENCIAS = REFERENCIAS / "manifiesto_referencias.json"
VERSION = "1"
_UBICACION_T34 = {datos.CASO_1: ["esperanza", "villa ejemplo", "cundinamarca"], datos.CASO_2: [],
                  datos.CASO_3: ["esperanza", "mirador"]}


def sha256(contenido: bytes) -> str:
    """La huella SHA-256 en hexadecimal."""
    return hashlib.sha256(contenido).hexdigest()


def a_json(valor: Any) -> bytes:
    """JSON estable (claves ordenadas, UTF-8, salto de línea final) para que los bytes sean reproducibles."""
    return (json.dumps(valor, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8")


def _numero(texto: str) -> float:
    return float(texto)


def referencia_de(caso: str, huella_del_documento: str) -> dict[str, Any]:
    """La referencia correcta del caso: sale solo de `datos`, jamás de leer el documento generado."""
    c = datos.CASOS[caso]
    faltantes_t34 = sorted({f["tema"] for f in c["faltantes"] if f["tema"] in
                            ("ubicacion", "magnitud", "plazo", "entregables", "precision", "objetivo", "sistema", "acceso")})
    return {
        "version": VERSION, "caso": caso, "titulo": c["titulo"], "tipo": datos.TIPOS[caso], "ficticio": True,
        "documento": {"archivo": datos.ARCHIVOS[caso], "sha256": huella_del_documento},
        "esperado": {"es_solicitud": True, "ubicacion": _UBICACION_T34[caso], "faltante": faltantes_t34,
                     "actividades": [{"servicio": a["servicio"], "cantidad": _numero(a["valor"]), "unidad": a["unidad_literal"]}
                                     for a in c["actividades"]]},
        **{clave: c[clave] for clave in ("actividades", "restricciones", "contradicciones", "faltantes",
                                          "revisiones_internas", "datos_presentes", "trampas", "elementos_visuales",
                                          "ocr", "inventario")},
        "regla_de_valores": "cada valor y unidad se conservan tal como están escritos; no hay conversión implícita",
    }


def construir_en_memoria() -> tuple[dict[str, bytes], dict[str, bytes]]:
    """Todos los archivos del banco como {nombre: bytes}: (documentos, referencias)."""
    documentos: dict[str, bytes] = {}
    referencias: dict[str, bytes] = {}
    entradas_d, entradas_r = [], []
    for caso, generador in generar.GENERADORES.items():
        contenido = generador()
        archivo = datos.ARCHIVOS[caso]
        documentos[archivo] = contenido
        entradas_d.append({"id": caso, "archivo": archivo, "tipo": datos.TIPOS[caso], "bytes": len(contenido),
                           "sha256": sha256(contenido)})
        referencia = a_json(referencia_de(caso, sha256(contenido)))
        nombre = f"{caso}.referencia.json"
        referencias[nombre] = referencia
        entradas_r.append({"id": caso, "archivo": nombre, "sha256": sha256(referencia), "documento": archivo,
                           "documento_sha256": sha256(contenido)})
    documentos["manifiesto_documentos.json"] = a_json({"version": VERSION, "ficticio": True, "documentos": entradas_d,
                                                       "fuentes_del_escaneado": generar.huella_de_fuentes()})
    referencias["manifiesto_referencias.json"] = a_json({"version": VERSION, "referencias": entradas_r})
    return documentos, referencias


def escribir() -> None:
    """Escribe los archivos en `documentos/` y `referencias/`."""
    documentos, referencias = construir_en_memoria()
    for carpeta, archivos in ((DOCUMENTOS, documentos), (REFERENCIAS, referencias)):
        carpeta.mkdir(exist_ok=True)
        for nombre, contenido in archivos.items():
            (carpeta / nombre).write_bytes(contenido)


def verificar() -> list[str]:
    """Las diferencias entre lo guardado y lo que se generaría hoy (vacía si coincide)."""
    documentos, referencias = construir_en_memoria()
    problemas = []
    for carpeta, archivos in ((DOCUMENTOS, documentos), (REFERENCIAS, referencias)):
        for nombre, contenido in archivos.items():
            ruta = carpeta / nombre
            if not ruta.is_file():
                problemas.append(f"falta {ruta.relative_to(RAIZ)}")
            elif ruta.read_bytes() != contenido:
                problemas.append(f"difiere {ruta.relative_to(RAIZ)}")
    return problemas


def main(argumentos: list[str]) -> int:
    """Genera el banco, o con `--verificar` comprueba que lo guardado se reproduce byte a byte."""
    if "--verificar" in argumentos:
        problemas = verificar()
        print("\n".join(problemas) if problemas else "El banco se reproduce byte a byte.")
        return 1 if problemas else 0
    escribir()
    print(f"Banco escrito en {DOCUMENTOS} y {REFERENCIAS}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
