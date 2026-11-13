"""El JavaScript de las páginas, que desde U-49 vive en archivos con huella y no dentro del HTML.

Las pruebas que buscan en él una pieza de código lo leen de aquí (`APP` el de cada página, `TEMA` el primer pintado).
"""

from pathlib import Path

PLANTILLAS = Path(__file__).resolve().parents[1] / "anka" / "web" / "templates"
APP = (PLANTILLAS / "app.js").read_text(encoding="utf-8")
TEMA = (PLANTILLAS / "tema.js").read_text(encoding="utf-8")
JS = APP + "\n" + TEMA
