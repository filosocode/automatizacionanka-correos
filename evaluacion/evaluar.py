"""Compara etiquetas humanas y resultados observados; nunca llama a modelos ni a Gmail."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
from datetime import datetime, UTC
from pathlib import Path
from typing import Any

FIELDS = {
    "es_solicitud (si/no)": "es_solicitud",
    "servicio_esperado": "servicio",
    "ubicacion_esperada": "ubicacion",
    "magnitud_esperada": "magnitud",
    "unidad": "unidad",
    "entregables_esperados": "entregables",
    "plazo_esperado": "plazo",
    "datos_faltantes_esperados": "datos_faltantes",
    "estado_esperado (descartar/revision_manual/borrador_preguntas/alcance_completo)": "estado",
}


def evaluar(corpus: Path, resultados: Path) -> dict[str, Any]:
    """Calcula acuerdos exactos normalizados y errores de clasificación para un corpus completo."""
    rows = list(csv.DictReader(corpus.read_text(encoding="utf-8-sig").splitlines()))
    observed = json.loads(resultados.read_text(encoding="utf-8"))
    required = ("modelo", "prompt", "fecha", "tipo_corpus", "casos")
    if any(not observed.get(key) for key in required):
        raise ValueError("Faltan modelo, prompt, fecha, tipo_corpus o casos observados.")
    datetime.fromisoformat(observed["fecha"])
    if observed["tipo_corpus"] not in {"real_anonimizado", "sintetico"}:
        raise ValueError("tipo_corpus debe ser real_anonimizado o sintetico.")
    ids = [row["id_caso"] for row in rows]
    if not rows or len(set(ids)) != len(ids) or any(not identifier for identifier in ids):
        raise ValueError("El corpus debe contener identificadores únicos y no vacíos.")
    if any(not row.get(field, "").strip() for row in rows for field in FIELDS):
        raise ValueError("Corpus sin etiquetar por completo: no se puede calcular precisión.")
    cases = observed["casos"]
    if len(cases) != len(ids) or {case.get("id_caso") for case in cases} != set(ids):
        raise ValueError("Los resultados deben cubrir cada caso del corpus exactamente una vez.")
    indexed = {case["id_caso"]: case for case in cases}
    hits = dict.fromkeys(FIELDS.values(), 0)
    lost = false_positive = 0
    for row in rows:
        actual = indexed[row["id_caso"]]
        for field, target in FIELDS.items():
            if target not in actual:
                raise ValueError(f"Falta el resultado {target} del caso {row['id_caso']}.")
            hits[target] += _normal(row[field]) == _normal(actual[target])
        expected_quote = _normal(row["es_solicitud (si/no)"])
        actual_quote = _normal(actual["es_solicitud"])
        if expected_quote not in {"si", "no"} or actual_quote not in {"si", "no"}:
            raise ValueError("es_solicitud debe ser si o no.")
        lost += expected_quote == "si" and actual_quote == "no"
        false_positive += expected_quote == "no" and actual_quote == "si"
    return {
        "fecha_evaluacion": datetime.now(UTC).isoformat(),
        "modelo": observed["modelo"],
        "prompt": observed["prompt"],
        "fecha_ejecucion": observed["fecha"],
        "tipo_corpus": observed["tipo_corpus"],
        "casos": len(rows),
        "corpus_sha256": hashlib.sha256(corpus.read_bytes()).hexdigest(),
        "resultados_sha256": hashlib.sha256(resultados.read_bytes()).hexdigest(),
        "acuerdo_exacto_por_campo": {key: value / len(rows) for key, value in hits.items()},
        "solicitudes_perdidas": lost,
        "falsas_solicitudes": false_positive,
        "limite": "El acuerdo textual no mide equivalencia semántica ni sustituye la aceptación humana.",
    }


def _normal(value: Any) -> str:
    return " ".join(str(value).casefold().split()).replace("sí", "si")


def main() -> int:
    """Escribe un informe solo cuando etiquetas y observaciones están completas."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("corpus", type=Path)
    parser.add_argument("resultados", type=Path)
    parser.add_argument("informe", type=Path)
    args = parser.parse_args()
    try:
        report = evaluar(args.corpus, args.resultados)
    except (ValueError, KeyError, TypeError, OSError) as exc:
        parser.exit(2, f"Evaluación pendiente: {exc}\n")
    args.informe.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
