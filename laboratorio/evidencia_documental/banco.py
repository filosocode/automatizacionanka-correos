"""Ejecuta el extractor sobre C1, C2 y C3, valida con T-117, compara con las referencias y guarda lo derivado (T-118).

    python -X utf8 -m laboratorio.evidencia_documental.banco [--sin-ocr]

Lo derivado (evidencias, hallazgos e informe) se escribe solo en `laboratorio/evidencia_documental/derivados/`, que Git ignora
y que no forma parte de ningún paquete de entrega. No modifica los originales, no consulta Gmail ni Drive, no ejecuta Work y no
envía ningún texto a servicios externos.
"""

from __future__ import annotations

from laboratorio.evidencia_documental import aislamiento

aislamiento.carpeta_temporal()

import json  # noqa: E402
import sys  # noqa: E402
import time  # noqa: E402
from collections import Counter  # noqa: E402
from pathlib import Path  # noqa: E402
from typing import Any  # noqa: E402

from laboratorio.evidencia_documental import casos, comparar_referencia, extractor  # noqa: E402
from laboratorio.evidencia_documental import validador as v  # noqa: E402
from laboratorio.evidencia_documental import versiones  # noqa: E402

DERIVADOS = Path(__file__).resolve().parent / "derivados"
VERSION_VALIDADOR = versiones.VERSION_VALIDADOR


def ejecutar_caso(caso: str, *, ocr: bool = True) -> dict[str, Any]:
    """Extrae, valida y compara un caso. Devuelve todo lo medido; no escribe en disco."""
    archivo = casos.ARCHIVOS[caso]
    contenido = (casos.DOCUMENTOS / archivo).read_bytes()
    inicio = time.perf_counter()
    doc, paquete, resultados = extractor.evidencia_del_caso(caso, {archivo: contenido}, ocr=ocr)
    extraccion_s = time.perf_counter() - inicio
    inicio = time.perf_counter()
    hallazgos = v.validar(doc, paquete)
    validacion_s = time.perf_counter() - inicio
    comparacion = comparar_referencia.comparar(caso, doc)
    metodos = Counter(e["metodo"] for e in doc["evidencias"])
    estados = Counter(e["estado_de_lectura"] for e in doc["evidencias"])
    cobertura = doc["documentos"][0]["cobertura"]
    return {
        "caso": caso, "archivo": archivo, "sha256": doc["documentos"][0]["sha256"], "version_extractor": extractor.VERSION_EXTRACTOR,
        "version_validador": VERSION_VALIDADOR, "ocr_solicitado": ocr,
        "tiempos_s": {"extraccion_total": round(extraccion_s, 3), "lectura": resultados[0].metricas.get("lectura_s"), "validacion": round(validacion_s, 3)},
        "cobertura": {"unidad": cobertura["unidad"], "total": cobertura["total"], "leidas": cobertura["leidas"], "ilegibles": cobertura["ilegibles"],
                      "detalle": cobertura["detalle"]},
        "evidencias": {"total": len(doc["evidencias"]), "por_metodo": dict(metodos), "por_estado": dict(estados),
                       "por_tipo": dict(Counter(e["tipo"] for e in doc["evidencias"]))},
        "declarado_completo": doc["analisis"]["declarado_completo"], "motivos_de_incompletitud": doc["analisis"]["motivos_de_incompletitud"],
        "validador": {"errores": [str(h) for h in v.errores(hallazgos)], "avisos": [str(h) for h in hallazgos if h.severidad == v.AVISO]},
        "comparacion": comparacion, "_doc": doc, "_hallazgos": hallazgos,
    }


def guardar(resultados: dict[str, dict[str, Any]]) -> Path:
    """Escribe lo derivado en `derivados/`. Devuelve la carpeta."""
    DERIVADOS.mkdir(exist_ok=True)
    (DERIVADOS / ".gitignore").write_text("*\n!.gitignore\n", encoding="utf-8")
    informe: dict[str, Any] = {"version_extractor": extractor.VERSION_EXTRACTOR, "version_validador": VERSION_VALIDADOR, "casos": {}}
    for caso, r in resultados.items():
        (DERIVADOS / f"{caso}.evidencia.json").write_text(json.dumps(r["_doc"], ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        (DERIVADOS / f"{caso}.hallazgos.json").write_text(
            json.dumps([{"codigo": h.codigo, "severidad": h.severidad, "ruta": h.ruta, "mensaje": h.mensaje} for h in r["_hallazgos"]],
                       ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        informe["casos"][caso] = {k: x for k, x in r.items() if not k.startswith("_")}
    (DERIVADOS / "informe_t118.json").write_text(json.dumps(informe, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return DERIVADOS


def main(argv: list[str]) -> int:
    """Corre el banco completo e imprime un resumen."""
    ocr = "--sin-ocr" not in argv
    resultados = {caso: ejecutar_caso(caso, ocr=ocr) for caso in ("C1", "C3", "C2")}
    carpeta = guardar(resultados)
    for caso in ("C1", "C2", "C3"):
        r = resultados[caso]
        print(f"{caso} {r['archivo']}: {r['evidencias']['total']} evidencias, cobertura {r['cobertura']['leidas']}/{r['cobertura']['total']} "
              f"{r['cobertura']['unidad']}, completo={r['declarado_completo']}, errores del validador={len(r['validador']['errores'])}, "
              f"{comparar_referencia.resumen_en_texto(r['comparacion'])}, extracción {r['tiempos_s']['extraccion_total']} s")
    print("Derivados en", carpeta)
    return 1 if any(r["validador"]["errores"] for r in resultados.values()) else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
