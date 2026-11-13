"""Adaptador de laboratorio: del JSON vigente de Work (`ANKA_DASHBOARD_JSON_V1`) a la evidencia documental (T-117).

Sirve para medir la compatibilidad: ¿qué parte de lo que Work ya devuelve alcanza para el validador? No modifica el
contrato ni el retorno (trabaja sobre una copia). Lo que no puede mapear con certeza lo devuelve en `sin_mapear`, nunca
lo inventa. Las citas de Work son texto libre («solicitud_01.pdf, p. 1, Tabla 1, ítem 1»); aquí se interpretan con
expresiones regulares y lo que no encaja queda sin localizador.
"""

from __future__ import annotations

import re
from typing import Any

from laboratorio.evidencia_documental.esquema import VERSION_MODELO
from laboratorio.evidencia_documental.fuentes import PaqueteDeOriginales

_VALOR = re.compile(r"^\s*(?P<cifra>\d[\d.,]*)\s*(?P<unidad>\S.*?)?\s*$")
_PAGINA = re.compile(r"\bp\.?\s*(?P<n>\d+)\b|\bp[aá]g(?:ina)?\.?\s*(?P<m>\d+)\b", re.IGNORECASE)
_TABLA = re.compile(r"tabla\s*(\d+)", re.IGNORECASE)
_FILA = re.compile(r"[ií]tem\s*(\d+)|fila\s*(\d+)", re.IGNORECASE)
_CELDA = re.compile(r"(?P<hoja>[A-Za-zÁÉÍÓÚÑáéíóúñ_][\wÁÉÍÓÚÑáéíóúñ ]*)!(?P<ref>\$?[A-Z]{1,3}\$?\d+(?::\$?[A-Z]{1,3}\$?\d+)?)")


def _tipo_de_documento(archivo: str, paquete: PaqueteDeOriginales) -> str:
    if archivo.lower().endswith((".xlsx", ".xlsm")):
        return "xlsx"
    paginas = paquete.paginas(archivo)
    con_texto = sum(1 for p in range(1, paginas + 1) if paquete.texto_nativo(archivo, p).strip())
    return "pdf_digital" if con_texto == paginas else "pdf_escaneado"


def interpretar_cita(cita: str, tipo: str) -> dict[str, Any]:
    """Localizador a partir de una cita libre; vacío si no se puede interpretar."""
    if tipo == "xlsx":
        m = _CELDA.search(cita or "")
        if not m:
            return {}
        ref = m.group("ref").replace("$", "")
        return {"hoja": m.group("hoja").strip(), ("rango" if ":" in ref else "celda"): ref}
    m = _PAGINA.search(cita or "")
    if not m:
        return {}
    loc: dict[str, Any] = {"pagina": int(m.group("n") or m.group("m"))}
    t, f = _TABLA.search(cita), _FILA.search(cita)
    if t:
        loc["tabla"] = int(t.group(1))
    if f:
        loc["fila"] = int(f.group(1) or f.group(2))
    return loc


def _cantidad_y_unidad(valor: str, unidad: str | None = None) -> tuple[str | None, str | None]:
    m = _VALOR.match(valor or "")
    if not m:
        return None, None
    return m.group("cifra").rstrip(".,"), (unidad or m.group("unidad") or "")


def adaptar(retorno: dict[str, Any], paquete: PaqueteDeOriginales, caso: str) -> tuple[dict[str, Any], list[str]]:
    """(evidencia, sin_mapear). `retorno` es el JSON de Work; no se modifica."""
    sin_mapear: list[str] = []
    conocidos = {a["filename"]: a for a in retorno.get("attachments", []) if isinstance(a, dict) and a.get("filename") in paquete.archivos}
    documentos: list[dict[str, Any]] = []
    evidencias: list[dict[str, Any]] = []
    tipos: dict[str, str] = {}
    for archivo, adj in conocidos.items():
        tipo = _tipo_de_documento(archivo, paquete)
        tipos[archivo] = tipo
        total = len(paquete.libro(archivo)) if tipo == "xlsx" else paquete.paginas(archivo)
        ilegible = adj.get("status") in ("bloqueado", "no_legible", "ilegible") or adj.get("coverage") in ("no_legible", "ninguna")
        documentos.append({
            "archivo": archivo, "sha256": paquete.sha256(archivo), "tipo": tipo,
            "cobertura": {"unidad": "hojas" if tipo == "xlsx" else "paginas", "total": total, "leidas": 0 if ilegible else total,
                          "ilegibles": total if ilegible else 0, "detalle": []},
        })
        if ilegible:
            evidencias.append({
                "id": f"W-{len(evidencias) + 1}", "documento": archivo, "tipo": "texto", "metodo": "ocr" if tipo == "pdf_escaneado" else "texto_nativo",
                "estado_de_lectura": "ilegible", "vigencia": "vigente", "confianza": "baja", "revision_humana_requerida": True,
                "certifica_interpretacion_visual": False, "localizador": {"pagina": 1},
                "motivo": str(adj.get("reason") or "Work declaró el adjunto sin leer.")[:300],
            })
            continue
        for item in adj.get("evidence", []) or []:
            if not isinstance(item, dict):
                continue
            cifra, unidad = _cantidad_y_unidad(str(item.get("value") or ""))
            loc = interpretar_cita(str(item.get("location") or ""), tipo)
            e: dict[str, Any] = {
                "id": f"W-{len(evidencias) + 1}", "documento": archivo, "tipo": "cantidad" if cifra else "texto",
                "concepto": str(item.get("topic") or ""), "metodo": "celda" if tipo == "xlsx" else "texto_nativo",
                "estado_de_lectura": "leido", "vigencia": "vigente", "confianza": "media", "revision_humana_requerida": False,
                "certifica_interpretacion_visual": False,
            }
            if loc:
                e["localizador"] = loc
            else:
                sin_mapear.append(f"evidencia «{item.get('topic')}»: localizador no interpretable ({item.get('location')!r})")
            if item.get("excerpt"):
                e["texto_literal"] = str(item["excerpt"])
            if cifra:
                e["cantidad_literal"], e["unidad_literal"] = cifra, unidad
            evidencias.append(e)
    interpretaciones: list[dict[str, Any]] = []
    for a in retorno.get("activities", []) or []:
        if not isinstance(a, dict):
            continue
        fuente = str(a.get("fuente") or "")
        archivo = next((k for k in conocidos if fuente.startswith(k)), None)
        if archivo is None:
            sin_mapear.append(f"actividad «{str(a.get('activity'))[:50]}»: la fuente no empieza por un adjunto conocido ({fuente[:50]!r})")
            continue
        cifra, unidad = _cantidad_y_unidad(str(a.get("quantity") or ""), str(a.get("unit") or "") or None)
        loc = interpretar_cita(fuente.split(";")[0], tipos[archivo])
        if not cifra:
            sin_mapear.append(f"actividad «{str(a.get('activity'))[:50]}»: sin cantidad numérica ({a.get('quantity')!r})")
            continue
        id_ev = f"W-{len(evidencias) + 1}"
        e = {"id": id_ev, "documento": archivo, "tipo": "cantidad", "concepto": str(a.get("activity") or ""),
             "metodo": "celda" if tipos[archivo] == "xlsx" else "texto_nativo", "estado_de_lectura": "leido", "vigencia": "vigente",
             "confianza": "media", "revision_humana_requerida": False, "certifica_interpretacion_visual": False,
             "cantidad_literal": cifra, "unidad_literal": unidad or ""}
        if loc:
            e["localizador"] = loc
        else:
            sin_mapear.append(f"actividad «{str(a.get('activity'))[:50]}»: localizador no interpretable ({fuente[:60]!r})")
        evidencias.append(e)
        interpretaciones.append({"id": f"I{len(interpretaciones) + 1}", "tipo": "actividad", "texto": f"{a.get('quantity')} {a.get('unit') or ''}".strip(),
                                 "evidencias": [id_ev], "confianza": "media"})
    sin_mapear += [f"contradicción «{c.get('topic')}»: el contrato actual la cita con fuentes en texto libre; no se enlaza a evidencias"
                   for c in retorno.get("contradictions", []) or [] if isinstance(c, dict)]
    completo = retorno.get("analysis_complete") is True and all(d["cobertura"]["ilegibles"] == 0 for d in documentos)
    motivos = [] if completo else ["Work no declaró análisis completo o hay adjuntos sin leer."]
    return ({"version_modelo": VERSION_MODELO, "caso": caso, "documentos": documentos, "evidencias": evidencias,
             "interpretaciones": interpretaciones, "contradicciones": [],
             "analisis": {"declarado_completo": completo, "motivos_de_incompletitud": motivos}}, sin_mapear)
