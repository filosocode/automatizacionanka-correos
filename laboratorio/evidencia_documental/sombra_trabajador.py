"""Proceso hijo del modo sombra (T-119): extrae y valida UN archivo y escribe el resultado en un archivo JSON.

    python -X utf8 -m laboratorio.evidencia_documental.sombra_trabajador <job_id> <nombre> <contenido> <salida> <ocr:0|1>

Corre aparte para que un cuelgue, una caída o un consumo excesivo (el OCR es lento) no alcancen a quien lo lanza: el padre lo mata al
vencer el plazo. Solo lee `contenido` y solo escribe `salida`; no abre red, Gmail, Drive, Work ni ninguna base de datos.
"""

from __future__ import annotations

from laboratorio.evidencia_documental import aislamiento

aislamiento.carpeta_temporal()

import json  # noqa: E402
import sys  # noqa: E402
import time  # noqa: E402
from pathlib import Path  # noqa: E402

from laboratorio.evidencia_documental import extractor  # noqa: E402
from laboratorio.evidencia_documental import validador as v  # noqa: E402


def trabajar(job_id: str, nombre: str, contenido: Path, salida: Path, ocr: bool) -> int:
    """Extrae, valida y escribe `salida`. Devuelve el código de salida del proceso."""
    inicio = time.perf_counter()
    try:
        doc, paquete, _ = extractor.evidencia_del_caso(job_id, {nombre: contenido.read_bytes()}, ocr=ocr)
        hallazgos = v.validar(doc, paquete)
        resultado = {
            "doc": doc, "duracion_s": round(time.perf_counter() - inicio, 3),
            "hallazgos": [{"codigo": h.codigo, "severidad": h.severidad, "ruta": h.ruta, "mensaje": h.mensaje} for h in hallazgos],
        }
    except Exception as exc:   # el padre decide qué hacer; aquí solo se informa el tipo, nunca el contenido del documento  # noqa: BLE001
        salida.write_text(json.dumps({"error": type(exc).__name__}), encoding="utf-8")
        return 2
    salida.write_text(json.dumps(resultado, ensure_ascii=False), encoding="utf-8")
    return 0


if __name__ == "__main__":
    if len(sys.argv) != 6:
        sys.exit(64)
    sys.exit(trabajar(sys.argv[1], sys.argv[2], Path(sys.argv[3]), Path(sys.argv[4]), sys.argv[5] == "1"))
