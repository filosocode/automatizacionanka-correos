"""Evidencia de referencia («dorada») de C1, C2 y C3 y el paquete de originales para validarla (T-117).

La evidencia dorada se escribió leyendo los originales del banco (T-71) y las referencias v2; sirve para probar que el
validador acepta lo correcto y rechaza cada alteración. No es la salida de ningún sistema evaluado.

El texto OCR de C2 (`datos/ocr_tdr_02.txt`) es el de la prueba aislada de laboratorio T-108 (motor local, sobre el
SHA-256 de `tdr_02.pdf`). No forma parte del procesamiento integrado de ANKA V1.0 ni certifica tablas, figuras,
tachaduras ni anotaciones.
"""

from __future__ import annotations

import copy
import re
from pathlib import Path
from typing import Any

from laboratorio.evidencia_documental.esquema import VERSION_MODELO
from laboratorio.evidencia_documental.fuentes import PaqueteDeOriginales

RAIZ = Path(__file__).resolve().parent
DOCUMENTOS = RAIZ.parent / "banco_documental" / "documentos"
OCR_TDR_02 = RAIZ / "datos" / "ocr_tdr_02.txt"
SHA_TDR_02 = "0287414f91bb1c9b3ecfd05e98de713adaa81177261885f89e4fe8f23c66d732"

ARCHIVOS = {"C1": "solicitud_01.pdf", "C2": "tdr_02.pdf", "C3": "cantidades_03.xlsx"}


def paginas_ocr(texto: str) -> dict[int, str]:
    """Separa el texto OCR por las marcas «[Página N]» (sin las marcas)."""
    partes = re.split(r"^\[P[áa]gina (\d+)\]\s*$", texto, flags=re.MULTILINE)
    return {int(partes[i]): partes[i + 1].strip() for i in range(1, len(partes) - 1, 2)}


def paquete(caso: str) -> PaqueteDeOriginales:
    """Los originales del caso (y el texto OCR de C2) para que el validador los relea."""
    archivo = ARCHIVOS[caso]
    textos = {archivo: paginas_ocr(OCR_TDR_02.read_text(encoding="utf-8"))} if caso == "C2" else None
    return PaqueteDeOriginales({archivo: (DOCUMENTOS / archivo).read_bytes()}, textos)


def _ev(id_: str, archivo: str, loc: dict | None, *, tipo: str = "cantidad", metodo: str, estado: str = "leido", concepto: str = "",
        texto: str | None = None, cantidad: str | None = None, unidad: str | None = None, vigencia: str = "vigente",
        confianza: str = "alta", revision: bool = False, **extra: Any) -> dict[str, Any]:
    e: dict[str, Any] = {
        "id": id_, "documento": archivo, "tipo": tipo, "metodo": metodo, "estado_de_lectura": estado, "vigencia": vigencia,
        "confianza": confianza, "revision_humana_requerida": revision, "certifica_interpretacion_visual": False,
    }
    if loc is not None:
        e["localizador"] = loc
    if concepto:
        e["concepto"] = concepto
    if texto is not None:
        e["texto_literal"] = texto
    if cantidad is not None:
        e["cantidad_literal"] = cantidad
    if unidad is not None:
        e["unidad_literal"] = unidad
    e.update(extra)
    return e


def _documento(archivo: str, tipo: str, unidad: str, total: int, detalle: list[dict], leidas: int | None = None,
               ilegibles: int = 0) -> dict[str, Any]:
    sha = (DOCUMENTOS / archivo).read_bytes()
    from laboratorio.evidencia_documental.fuentes import sha256
    return {"archivo": archivo, "sha256": sha256(sha), "tipo": tipo,
            "cobertura": {"unidad": unidad, "total": total, "leidas": total - ilegibles if leidas is None else leidas,
                          "ilegibles": ilegibles, "detalle": detalle}}


def _base(caso: str, documentos: list, evidencias: list, interpretaciones: list, contradicciones: list,
          completo: bool, motivos: list[str]) -> dict[str, Any]:
    return {"version_modelo": VERSION_MODELO, "caso": caso, "documentos": documentos, "evidencias": evidencias,
            "interpretaciones": interpretaciones, "contradicciones": contradicciones,
            "analisis": {"declarado_completo": completo, "motivos_de_incompletitud": motivos}}


def evidencia_c1() -> dict[str, Any]:
    """Evidencia de C1 (PDF digital) tal como debe quedar."""
    a = "solicitud_01.pdf"
    t: dict[str, Any] = {"metodo": "texto_nativo"}
    ev = [
        _ev("E1", a, {"pagina": 1, "tabla": 1, "fila": 1}, concepto="área de intervención", texto="del área de intervención 18,5 ha", cantidad="18,5", unidad="ha", **t),
        _ev("E2", a, {"pagina": 1}, concepto="área total del predio", texto="Área total del predio según escritura: 21,0 ha", cantidad="21,0", unidad="ha", **t),
        _ev("E3", a, {"pagina": 1, "tabla": 1, "fila": 2}, concepto="ejes de vías", texto="ejes de vías internas existentes 2,350 km", cantidad="2,350", unidad="km",
            valor_normalizado={"valor": 2.35, "unidad": "km", "regla": "separador_decimal_coma"},
            ambiguedad="«2,350» puede ser 2,35 km o 2350 km según el separador", revision_humana_requerida=True, confianza="media", metodo="texto_nativo"),
        _ev("E4", a, {"pagina": 1, "tabla": 1, "fila": 3}, concepto="puntos de control", estado="contradictorio", texto="monumentación de puntos de control 12 puntos", cantidad="12", unidad="puntos", **t),
        _ev("E5", a, {"pagina": 2}, concepto="puntos de control", estado="contradictorio", texto="Se estima un total de 10 puntos de control", cantidad="10", unidad="puntos", **t),
        _ev("E6", a, {"pagina": 2}, concepto="fotografías por punto", texto="12 fotografías por punto de control", cantidad="12", unidad="fotografías", **t),
        _ev("E7", a, {"pagina": 1, "tabla": 1, "fila": 4}, concepto="construcciones", texto="construcciones existentes 7 construcciones", cantidad="7", unidad="construcciones", **t),
        _ev("E8", a, {"pagina": 1, "tabla": 1, "fila": 5}, concepto="quebradas", texto="drenajes 3 quebradas", cantidad="3", unidad="quebradas", **t),
        _ev("E9", a, {"pagina": 2}, concepto="plazo", texto="Plazo de entrega: 25 días calendario", cantidad="25", unidad="días", **t),
        _ev("E10", a, {"pagina": 1}, tipo="texto", estado="no_encontrado", concepto="polígono del predio", metodo="texto_nativo",
            motivo="Se buscó el polígono: el documento dice que se enviará por separado en archivo KMZ y no viene.", revision=False),
    ]
    inter = [
        {"id": "I1", "tipo": "actividad", "texto": "Levantamiento planimétrico y altimétrico de 18,5 ha.", "evidencias": ["E1"], "confianza": "alta"},
        {"id": "I2", "tipo": "alcance", "texto": "La tabla pide 12 puntos de control y las condiciones estiman 10.", "evidencias": ["E4", "E5"], "confianza": "alta"},
        {"id": "I3", "tipo": "faltante", "texto": "Falta el polígono del predio.", "evidencias": ["E10"], "confianza": "alta"},
    ]
    contra = [{"id": "K1", "tema": "cantidad de puntos de control", "evidencias": ["E4", "E5"], "estado": "abierta"}]
    doc = _documento(a, "pdf_digital", "paginas", 2, [{"ref": "p1", "estado": "leido", "metodo": "texto_nativo"},
                                                    {"ref": "p2", "estado": "leido", "metodo": "texto_nativo"}])
    return _base("C1", [doc], ev, inter, contra, True, [])


def evidencia_c3() -> dict[str, Any]:
    """Evidencia de C3 (Excel con hoja oculta)."""
    a = "cantidades_03.xlsx"
    c: dict[str, Any] = {"metodo": "celda"}
    ev = [
        _ev("E1", a, {"hoja": "Resumen", "rango": "B4:E4"}, concepto="área de lotes", texto="Levantamiento topográfico de lotes", cantidad="33.7", unidad="ha", **c),
        _ev("E2", a, {"hoja": "Resumen", "rango": "B5:E5"}, concepto="ejes viales", texto="Levantamiento de ejes viales", cantidad="3.625", unidad="km", **c),
        _ev("E3", a, {"hoja": "Resumen", "rango": "B6:E6"}, concepto="puntos de control", texto="Puntos de control", cantidad="24", unidad="puntos", **c),
        _ev("E4", a, {"hoja": "Resumen", "rango": "B7:E7"}, concepto="perfiles", texto="Perfiles de eje", cantidad="3", unidad="perfiles", **c),
        _ev("E5", a, {"hoja": "Resumen", "rango": "B9:E9"}, concepto="predios intervenidos", estado="contradictorio", texto="Predios intervenidos", cantidad="7", unidad="lotes", **c),
        _ev("E6", a, {"hoja": "Detalle", "rango": "A2:A7"}, concepto="predios intervenidos", estado="contradictorio", cantidad="6", unidad="lotes", confianza="media",
            derivacion={"operacion": "contar_celdas", "unidad_inferida": "lotes listados"}, **c),
        _ev("E7", a, {"hoja": "Borrador_anterior", "rango": "B2:D2"}, concepto="área de lotes", vigencia="historica", texto="Levantamiento topográfico de lotes",
            cantidad="31.2", unidad="ha", **c),
        _ev("E8", a, {"hoja": "Supuestos", "rango": "A3:C3"}, concepto="equidistancia de curvas", texto="Equidistancia de curvas", cantidad="0.5", unidad="m", **c),
    ]
    inter = [
        {"id": "I1", "tipo": "actividad", "texto": "Levantamiento topográfico de lotes: 33.7 ha.", "evidencias": ["E1"], "confianza": "alta"},
        {"id": "I2", "tipo": "alcance", "texto": "El resumen declara 7 lotes y el detalle lista 6.", "evidencias": ["E5", "E6"], "confianza": "media"},
        {"id": "I3", "tipo": "restriccion", "texto": "Curvas de nivel cada 0.5 m.", "evidencias": ["E8"], "confianza": "alta"},
    ]
    contra = [{"id": "K1", "tema": "número de predios intervenidos", "evidencias": ["E5", "E6"], "estado": "abierta"}]
    doc = _documento(a, "xlsx", "hojas", 4, [{"ref": "hoja:Resumen", "estado": "leido", "metodo": "celda"},
                                             {"ref": "hoja:Detalle", "estado": "leido", "metodo": "celda"},
                                             {"ref": "hoja:Supuestos", "estado": "leido", "metodo": "celda"},
                                             {"ref": "hoja:Borrador_anterior", "estado": "leido", "metodo": "celda"}])
    return _base("C3", [doc], ev, inter, contra, True, [])


def evidencia_c2() -> dict[str, Any]:
    """Evidencia de C2 (PDF escaneado): lo visual queda pendiente y no se declara completo."""
    a = "tdr_02.pdf"
    o: dict[str, Any] = {"metodo": "ocr", "confianza": "media"}
    ev = [
        _ev("E1", a, {"pagina": 1, "tabla": "Cuadro 1", "fila": "A"}, concepto="área del espejo de agua", estado="pendiente_revision_visual", revision=True,
            texto="Batimetria monohaz del espejo de agua 45,0 ha", cantidad="45,0", unidad="ha", **{**o, "confianza": "baja"}),
        _ev("E2", a, {"pagina": 1, "tabla": "Cuadro 1", "fila": "A"}, concepto="área del espejo de agua", estado="pendiente_revision_visual", revision=True,
            texto="41,8ha", cantidad="41,8", unidad="ha", **{**o, "confianza": "baja"}),
        _ev("V1", a, {"pagina": 1, "region": "Cuadro 1, fila A, columna Magnitud"}, tipo="elemento_visual", estado="pendiente_revision_visual", revision=True,
            concepto="posible tachadura y anotación manuscrita", metodo="ocr", confianza="baja",
            motivo="El OCR leyó dos cifras en la misma fila (45,0 y 41,8ha); solo una persona viendo el original puede decir cuál rige y si hay tachadura."),
        _ev("V2", a, {"pagina": 2, "region": "Figura 1"}, tipo="elemento_visual", estado="pendiente_revision_visual", revision=True,
            concepto="zona del croquis y su exclusión", metodo="ocr", confianza="baja",
            motivo="El OCR leyó «3,2 ha» y «Excluida del levantamiento» dentro de la figura; qué zona excluye lo decide una persona."),
        _ev("E3", a, {"pagina": 1, "tabla": "Cuadro 1", "fila": "B"}, concepto="perfiles longitudinales", texto="Perfiles longitudinales de fondo 3 perfiles", cantidad="3", unidad="perfiles", **o),
        _ev("E4", a, {"pagina": 1, "tabla": "Cuadro 1", "fila": "C"}, concepto="espaciamiento de secciones", texto="Secciones transversales (espaciamiento) 50 m", cantidad="50", unidad="m", **o),
        _ev("E5", a, {"pagina": 1, "tabla": "Cuadro 1", "fila": "D"}, concepto="franja de ronda", texto="Topografia de la franja de ronda (25 m a cada lado 6,8 km", cantidad="6,8", unidad="km", **o),
        _ev("E6", a, {"pagina": 2}, concepto="ancho de la rampa", texto="La rampa de acceso para la embarcacion tiene 6,8 m de ancho", cantidad="6,8", unidad="m", **o),
        _ev("E7", a, {"pagina": 2}, concepto="profundidad máxima", texto="Profundidad maxima esperada: 14 m", cantidad="14", unidad="m", **o),
        _ev("E8", a, {"pagina": 2}, tipo="texto", estado="no_encontrado", concepto="ubicación del embalse", metodo="ocr", confianza="media",
            motivo="La ubicación se remite al «Anexo 3», que no viene; solo se nombra el embalse."),
    ]
    inter = [
        {"id": "I1", "tipo": "alcance", "texto": "Hay dos cifras para el espejo de agua; falta que una persona vea el original.", "evidencias": ["E1", "E2", "V1"], "confianza": "baja"},
        {"id": "I2", "tipo": "faltante", "texto": "Falta la ubicación del embalse (Anexo 3).", "evidencias": ["E8"], "confianza": "media"},
    ]
    contra = [{"id": "K1", "tema": "área del espejo de agua", "evidencias": ["E1", "E2"], "estado": "abierta"}]
    doc = _documento(a, "pdf_escaneado", "paginas", 2, [{"ref": "p1", "estado": "leido", "metodo": "ocr"}, {"ref": "p2", "estado": "leido", "metodo": "ocr"}])
    return _base("C2", [doc], ev, inter, contra, False,
                 ["Cuadro 1, fila A: cifra posiblemente tachada y anotación manuscrita sin revisión visual.",
                  "Figura 1: zona rayada y su exclusión sin revisión visual."])


EVIDENCIAS = {"C1": evidencia_c1, "C2": evidencia_c2, "C3": evidencia_c3}


def copia(caso: str) -> dict[str, Any]:
    """Copia independiente de la evidencia de un caso, para alterarla en las pruebas."""
    return copy.deepcopy(EVIDENCIAS[caso]())
