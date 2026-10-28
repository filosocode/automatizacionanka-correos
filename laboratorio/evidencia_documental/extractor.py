"""Extractor local de evidencias documentales (T-118). Solo laboratorio.

Reutiliza, sin modificarlos, los lectores de `anka/infra/` (`pdf_text` con su OCR local y `xlsx_text`) y convierte su texto,
que ya trae la página o la coordenada de cada dato, en evidencia del modelo de T-117. No interpreta: una cifra con su unidad
es una *observación*, no una actividad cotizable. No envía nada a ningún servicio, no consulta Gmail ni Drive y no escribe
junto a los originales (devuelve estructuras; quien lo llama decide dónde guardarlas, dentro del laboratorio).

Identificadores: `ev-` + 12 hexadecimales de un SHA-256 sobre (huella del documento, localizador, método, cantidad, unidad,
nº de ocurrencia). Mientras el documento y su lectura no cambien, los identificadores son los mismos.

Límites que se declaran, no se esconden:
- el OCR no ve tachaduras, anotaciones a mano, zonas rayadas, norte ni escala: esos elementos quedan «pendientes de revisión
  visual» (con las pistas de texto que el OCR sí leyó), nunca leídos;
- una cifra seguida de una palabra de medida fuera del catálogo se conserva como `unidad_no_reconocida` (ver `unidades.py`); las cifras
  sin palabra de medida no son cantidades y se listan, por página, como «cifras no registradas»;
- el extractor localiza evidencia; no puede saber si la lectura fue exhaustiva ni cuántas actividades hay (`exhaustividad: no_determinable`);
- el texto que entrega el lector de Excel separa las celdas con « | »: un valor que contenga ese separador se lee mal.
"""

from __future__ import annotations

from laboratorio.evidencia_documental import aislamiento

aislamiento.carpeta_temporal()   # primero: fija ANKA_DATA_DIR antes de importar los lectores

import hashlib  # noqa: E402
import re  # noqa: E402
import time  # noqa: E402
from collections import Counter  # noqa: E402
from dataclasses import dataclass, field  # noqa: E402
from typing import Any  # noqa: E402

from anka.dominio.lectores_texto import Unreadable  # noqa: E402
from anka.infra import lectores_office, lectores_pdf  # noqa: E402
from anka.infra.presupuesto_ocr import presupuesto_de_caso  # noqa: E402
from laboratorio.evidencia_documental.esquema import VERSION_MODELO  # noqa: E402
from laboratorio.evidencia_documental import unidades  # noqa: E402
from laboratorio.evidencia_documental.versiones import VERSION_EXTRACTOR  # noqa: E402,F401
from laboratorio.evidencia_documental.fuentes import PaqueteDeOriginales, plano, sha256  # noqa: E402


_PISTAS_VISUALES = re.compile(r"\b(figura|croquis|plano|mapa|escala|norte|leyenda|excluid\w*|tachad\w*|manuscrit\w*|rayad\w*)\b", re.IGNORECASE)
_PAGINA = re.compile(r"^\[Página (\d+)([^\]]*)\]\s*$", re.MULTILINE)
_MARCA_OCR = "[OCR visual adicional]"
_RANGO = re.compile(r"páginas sin OCR: ([\d,\s–-]+)")
_NUMERICO = re.compile(r"^-?\d+(?:\.\d+)?(?:[eE][-+]?\d+)?$")
_HOJA = re.compile(r"^Hoja: (?P<n>.*); visibilidad: (?P<v>\w+); (?:HOJA OCULTA; )?rango declarado: (?P<d>.*)$")
_CELDA = re.compile(r"^(?P<ref>[A-Z]{1,3}\d+)=(?P<valor>.*)$", re.DOTALL)
_FORMULA = re.compile(r"^(?P<f>=.*) \[valor guardado: (?P<v>.*)\]$", re.DOTALL)
_ENCABEZADO_UNIDAD = re.compile(r"\(([^)]+)\)")


@dataclass
class Resultado:
    """Lo que el extractor obtuvo de un archivo: su entrada de documento, sus evidencias y lo necesario para validarlo."""

    archivo: str
    documento: dict[str, Any]
    evidencias: list[dict[str, Any]]
    textos_ocr: dict[int, str] = field(default_factory=dict)
    motivos_de_incompletitud: list[str] = field(default_factory=list)
    avisos_del_lector: list[str] = field(default_factory=list)
    metricas: dict[str, Any] = field(default_factory=dict)


def identificador(prefijo: str, huella: str, localizador: str, metodo: str, cantidad: str, unidad: str, ocurrencia: int) -> str:
    """Identificador estable: depende solo del documento, de dónde está el dato, de cómo se leyó y de lo que dice."""
    base = "|".join([huella, localizador, metodo, cantidad, unidad, str(ocurrencia)])
    return f"{prefijo}-{hashlib.sha256(base.encode('utf-8')).hexdigest()[:12]}"


def _rangos_de_paginas(texto: str) -> list[int]:
    paginas: list[int] = []
    for parte in texto.split(","):
        parte = parte.strip()
        m = re.fullmatch(r"(\d+)\s*[–-]\s*(\d+)", parte)
        if m:
            paginas += list(range(int(m.group(1)), int(m.group(2)) + 1))
        elif parte.isdigit():
            paginas.append(int(parte))
    return paginas


def _evidencia(id_: str, archivo: str, localizador: dict[str, Any], *, tipo: str, metodo: str, estado: str = "leido",
               vigencia: str = "vigente", confianza: str, revision: bool, texto: str | None = None, cantidad: str | None = None,
               unidad: str | None = None, **extra: Any) -> dict[str, Any]:
    e: dict[str, Any] = {
        "id": id_, "documento": archivo, "tipo": tipo, "metodo": metodo, "estado_de_lectura": estado, "vigencia": vigencia,
        "confianza": confianza, "revision_humana_requerida": revision, "certifica_interpretacion_visual": False,
        "localizador": localizador,
    }
    if texto is not None:
        e["texto_literal"] = texto
    if cantidad is not None:
        e["cantidad_literal"] = cantidad
    if unidad is not None:
        e["unidad_literal"] = unidad
    e.update(extra)
    return e


def _cantidad(id_: str, archivo: str, localizador: dict[str, Any], *, metodo: str, confianza: str, revision: bool, texto: str | None,
              cantidad: str, unidad: str, vigencia: str = "vigente", **extra: Any) -> dict[str, Any]:
    """Evidencia de cantidad; una unidad no reconocida o ambigua baja la confianza y pide revisión, sin tocar lo literal."""
    estado, compuesta, posibles = unidades.clasificar(unidad) if unidad else (unidades.SIN_UNIDAD, False, ())
    if estado not in (unidades.RECONOCIDA, unidades.SIN_UNIDAD):
        confianza, revision = "baja", True
        if posibles:
            extra["unidades_posibles"] = list(posibles)
    if compuesta:
        extra["unidad_compuesta"] = True
    return _evidencia(id_, archivo, localizador, tipo="cantidad", metodo=metodo, vigencia=vigencia, confianza=confianza, revision=revision,
                      texto=texto, cantidad=cantidad, unidad=unidad, unidad_estado=estado, **extra)


def _exhaustividad_de_pagina(estado: str, ocr: bool, visual: bool, sin_ocr: bool) -> tuple[str, str]:
    """El extractor no puede probar que leyó todo; solo distingue las páginas donde además hay motivos concretos para dudar."""
    if estado != "leido":
        return "no_determinable", "la página no se leyó completa"
    if sin_ocr:
        return "no_determinable", "OCR omitido en esta página"
    if visual:
        return "no_determinable", "hay contenido visual que el texto no describe"
    if ocr:
        return "no_determinable", "OCR: no hay garantía de que todo el texto se haya reconocido"
    return "sin_indicios_de_omision", "texto nativo extraído; sin imágenes ni avisos del lector (no prueba que esté todo)"


# ---------------------------------------------------------------- PDF
def _paginas_del_lector(salida: str) -> tuple[dict[int, tuple[str, str]], list[str]]:
    """Del texto de `pdf_text`: {página: (encabezado, cuerpo)} y las líneas de aviso de cobertura del final."""
    partes = _PAGINA.split(salida)
    paginas: dict[int, tuple[str, str]] = {}
    for i in range(1, len(partes) - 1, 3):
        paginas[int(partes[i])] = (partes[i + 1], partes[i + 2])
    avisos: list[str] = []
    if paginas:
        ultima = max(paginas)
        encabezado, cuerpo = paginas[ultima]
        lineas = cuerpo.split("\n")
        cola = [i for i, linea in enumerate(lineas) if linea.startswith(("COBERTURA PARCIAL:", "REQUIERE VISIÓN:"))]
        if cola:
            avisos = lineas[cola[0]:]
            paginas[ultima] = (encabezado, "\n".join(lineas[:cola[0]]))
    return paginas, avisos


def _no_se_abre(archivo: str, huella: str, tipo: str, unidad: str, error: Exception) -> Resultado:
    """Un archivo dañado o de otro formato: se registra como ilegible, sin inventar páginas, hojas ni cifras."""
    motivo = f"El archivo no se pudo abrir ({type(error).__name__})."
    localizador = {"hoja": "?", "celda": "A1"} if unidad == "hojas" else {"pagina": 1}
    ev = _evidencia(identificador("ev", huella, "archivo", "ilegible", "", "", 0), archivo, localizador, tipo="texto",
                    metodo="celda" if unidad == "hojas" else "texto_nativo", estado="ilegible", confianza="baja", revision=True, motivo=motivo)
    documento = {"archivo": archivo, "sha256": huella, "tipo": tipo,
                 "cobertura": {"unidad": unidad, "total": 1, "leidas": 0, "ilegibles": 1, "detalle": [{"ref": "archivo", "estado": "ilegible", "lectura": "no_determinable", "motivo_lectura": "el archivo no se abre"}]}}
    return Resultado(archivo, documento, [ev], {}, [motivo], [], {"lectura_s": 0.0, "no_se_abre": True})


def _extraer_pdf(archivo: str, contenido: bytes, huella: str, *, ocr: bool) -> Resultado:
    inicio = time.perf_counter()
    paquete = PaqueteDeOriginales({archivo: contenido})
    try:
        total = paquete.paginas(archivo)
        nativos = {p: paquete.texto_nativo(archivo, p) for p in range(1, total + 1)}
    except Exception as exc:   # PDF dañado o cifrado: el lector de pypdf lanza varios tipos de error  # noqa: BLE001
        return _no_se_abre(archivo, huella, "pdf_escaneado", "paginas", exc)
    if total == 0:
        return _no_se_abre(archivo, huella, "pdf_escaneado", "paginas", ValueError("sin páginas"))
    tipo = "pdf_digital" if all(t.strip() for t in nativos.values()) else "pdf_escaneado"
    config = {"ocr_omitir_escaneados": not ocr}
    ilegible_total = ""
    salida = ""
    try:
        with presupuesto_de_caso(config):
            salida = lectores_pdf.pdf_text(contenido)
    except Unreadable as exc:
        ilegible_total = str(exc)
    lectura = time.perf_counter() - inicio
    paginas, avisos = _paginas_del_lector(salida)
    sin_ocr = {p for aviso in avisos if "sin OCR" in aviso for m in _RANGO.finditer(aviso) for p in _rangos_de_paginas(m.group(1))}
    evidencias: list[dict[str, Any]] = []
    textos_ocr: dict[int, str] = {}
    detalle: list[dict[str, Any]] = []
    motivos: list[str] = []
    ocurrencias: Counter[tuple[str, ...]] = Counter()
    for pagina in range(1, total + 1):
        encabezado, cuerpo = paginas.get(pagina, ("", ""))
        ref = f"p{pagina}"
        if ilegible_total or pagina not in paginas:
            detalle.append({"ref": ref, "estado": "ilegible", "metodo": "ocr" if tipo == "pdf_escaneado" else "texto_nativo",
                            "lectura": "no_determinable", "motivo_lectura": "la página no se leyó"})
            evidencias.append(_evidencia(
                identificador("ev", huella, ref, "ilegible", "", "", 0), archivo, {"pagina": pagina}, tipo="texto",
                metodo="ocr" if tipo == "pdf_escaneado" else "texto_nativo", estado="ilegible", confianza="baja", revision=True,
                motivo=ilegible_total or "El lector no devolvió esta página."))
            motivos.append(f"Página {pagina} ilegible.")
            continue
        visual_parcial = "COBERTURA VISUAL PARCIAL" in cuerpo or "contiene imágenes no interpretadas" in encabezado
        no_inspeccionado = "CONTENIDO VISUAL NO INSPECCIONADO" in cuerpo or pagina in sin_ocr
        lineas = [x for x in cuerpo.split("\n") if not x.startswith(("COBERTURA VISUAL PARCIAL", "CONTENIDO VISUAL NO INSPECCIONADO"))]
        cuerpo_limpio = "\n".join(lineas)
        if _MARCA_OCR in cuerpo_limpio:
            antes, _, despues = cuerpo_limpio.partition(_MARCA_OCR)
            nativo, ocr_texto = antes.strip(), despues.strip()
        else:
            nativo, ocr_texto = cuerpo_limpio.strip(), ""
        if ocr_texto:
            textos_ocr[pagina] = ocr_texto
        usa_ocr = bool(ocr_texto)
        no_registradas: list[str] = []
        for metodo, texto, confianza in (("texto_nativo", nativo, "alta"), ("ocr", ocr_texto, "media")):
            if not texto:
                continue
            observadas, sobrantes = unidades.observar(texto, pegada=metodo == "ocr")
            no_registradas += sobrantes
            for o in observadas:
                n = ocurrencias[(ref, metodo, o.cantidad, o.unidad)]
                ocurrencias[(ref, metodo, o.cantidad, o.unidad)] += 1
                evidencias.append(_cantidad(
                    identificador("ev", huella, ref, metodo, o.cantidad, o.unidad, n), archivo, {"pagina": pagina}, metodo=metodo,
                    confianza=confianza, revision=visual_parcial, texto=o.ventana, cantidad=o.cantidad, unidad=o.unidad))
        if visual_parcial or no_inspeccionado:
            evidencias += _pendientes_visuales(archivo, huella, pagina, ocr_texto or nativo, ocurrencias, usa_ocr)
        estado_pagina = "leido"
        if no_inspeccionado or (not nativo and not ocr_texto):
            estado_pagina = "parcialmente_leido" if (nativo or ocr_texto) else "ilegible"
            motivos.append(f"Página {pagina}: " + ("sin OCR (política, tiempo o tope)." if pagina in sin_ocr else "contenido visual no inspeccionado."))
            if estado_pagina == "ilegible":
                evidencias.append(_evidencia(
                    identificador("ev", huella, ref, "ilegible", "", "", 0), archivo, {"pagina": pagina}, tipo="texto",
                    metodo="ocr" if tipo == "pdf_escaneado" else "texto_nativo", estado="ilegible", confianza="baja", revision=True,
                    motivo="Página sin texto nativo y sin texto OCR (OCR omitido o sin resultado)."))
        elif visual_parcial:
            motivos.append(f"Página {pagina}: tachaduras, anotaciones, figuras, escala y norte dependen de revisión visual.")
        lectura_p, motivo_p = _exhaustividad_de_pagina(estado_pagina, usa_ocr, visual_parcial, pagina in sin_ocr)
        detalle.append({"ref": ref, "estado": estado_pagina, "metodo": "ocr" if usa_ocr and not nativo else "texto_nativo",
                        "visual_pendiente": visual_parcial, "lectura": lectura_p, "motivo_lectura": motivo_p,
                        "cifras_no_registradas": len(no_registradas), "muestra_cifras_no_registradas": no_registradas[:20]})
    leidas = sum(1 for d in detalle if d["estado"] == "leido")
    ilegibles = sum(1 for d in detalle if d["estado"] == "ilegible")
    documento = {"archivo": archivo, "sha256": huella, "tipo": tipo,
                 "cobertura": {"unidad": "paginas", "total": total, "leidas": leidas, "ilegibles": ilegibles, "detalle": detalle}}
    return Resultado(archivo, documento, evidencias, textos_ocr, motivos, avisos,
                     {"lectura_s": round(lectura, 3), "ocr_solicitado": ocr, "paginas": total, "paginas_con_ocr": len(textos_ocr)})


def _raiz_de_pista(palabra: str) -> str:
    """La pista sin las letras que el OCR pega de la palabra siguiente («excluidadel» → «excluid»)."""
    minuscula = palabra.lower()
    return next((r for r in ("excluid", "tachad", "manuscrit", "rayad") if minuscula.startswith(r)), minuscula)


def _pendientes_visuales(archivo: str, huella: str, pagina: int, texto: str, ocurrencias: Counter, usa_ocr: bool) -> list[dict[str, Any]]:
    """Lo visual que el OCR no interpreta: una evidencia por pista de texto y una general de la página, siempre pendientes."""
    ref = f"p{pagina}"
    salida: list[dict[str, Any]] = []
    lineas = [re.sub(r"\s+", " ", x).strip() for x in texto.split("\n") if x.strip()]
    vistas: set[str] = set()
    for linea in lineas:
        pista = _PISTAS_VISUALES.search(linea)
        if pista and linea not in vistas:
            vistas.add(linea)
            n = ocurrencias[(ref, "pista", linea)]
            ocurrencias[(ref, "pista", linea)] += 1
            salida.append(_evidencia(
                identificador("vis", huella, ref, "pista", linea, "", n), archivo, {"pagina": pagina, "region": f"texto junto a «{_raiz_de_pista(pista.group(1))}»"},
                tipo="elemento_visual", metodo="ocr" if usa_ocr else "texto_nativo", estado="pendiente_revision_visual", confianza="baja", revision=True,
                texto=linea, motivo="El texto sugiere un elemento visual (figura, escala, norte, exclusión o tachadura); el texto no dice qué muestra."))
    salida.append(_evidencia(
        identificador("vis", huella, ref, "pagina", "", "", 0), archivo, {"pagina": pagina, "region": "página completa"},
        tipo="elemento_visual", metodo="ocr" if usa_ocr else "texto_nativo", estado="pendiente_revision_visual", confianza="baja", revision=True,
        motivo="El OCR no detecta tachaduras, anotaciones a mano, zonas rayadas, norte ni escala: una persona debe ver el original de esta página."))
    return salida


# ---------------------------------------------------------------- Excel
def _columna(ref: str) -> int:
    from openpyxl.utils import column_index_from_string
    return column_index_from_string(re.match(r"[A-Z]+", ref).group(0))   # type: ignore[union-attr]


def _fila(ref: str) -> int:
    return int(re.search(r"\d+", ref).group(0))   # type: ignore[union-attr]


def _extraer_xlsx(archivo: str, contenido: bytes, huella: str) -> Resultado:   # noqa: C901
    inicio = time.perf_counter()
    paquete = PaqueteDeOriginales({archivo: contenido})
    try:
        libro = paquete.libro(archivo)       # solo para ubicar comentarios (el lector de texto no dice a qué hoja pertenecen)
    except Exception as exc:   # libro dañado, cifrado o que no es un .xlsx  # noqa: BLE001
        return _no_se_abre(archivo, huella, "xlsx", "hojas", exc)
    try:
        salida = lectores_office.xlsx_text(contenido)
        ilegible = ""
    except Unreadable as exc:
        salida, ilegible = "", str(exc)
    lectura = time.perf_counter() - inicio
    hojas: list[dict[str, Any]] = []
    sueltas: list[str] = []
    actual: dict[str, Any] | None = None
    for linea in salida.split("\n"):
        m = _HOJA.match(linea)
        if m:
            actual = {"nombre": m.group("n"), "visibilidad": m.group("v"), "celdas": {}, "filas": {}, "parcial": []}
            hojas.append(actual)
            continue
        if actual is None:
            sueltas.append(linea)
            continue
        if linea.startswith(("LECTURA PARCIAL", "FÓRMULA SIN VALOR CALCULADO")):
            actual["parcial"].append(linea)
            continue
        if linea.startswith(("Comentario (", "Gráfico (", "COBERTURA PARCIAL", "DIBUJOS/FORMAS")) or not _CELDA.match(linea.split(" | ")[0]):
            sueltas.append(linea)
            continue
        for segmento in linea.split(" | "):
            c = _CELDA.match(segmento)
            if c:
                ref = c.group("ref")
                actual["celdas"][ref] = c.group("valor")
                actual["filas"].setdefault(_fila(ref), []).append(ref)
            else:
                sueltas.append(f"segmento sin coordenada: {segmento[:60]}")
    evidencias: list[dict[str, Any]] = []
    detalle: list[dict[str, Any]] = []
    motivos: list[str] = []
    ocurrencias: Counter[tuple[str, ...]] = Counter()
    for hoja in hojas:
        nombre, visible = hoja["nombre"], hoja["visibilidad"] == "visible"
        vigencia = "vigente" if visible else "sin_confirmar"
        celdas: dict[str, str] = hoja["celdas"]
        numericas = sin_unidad = 0
        encabezados = {_columna(r): v for r, v in celdas.items() if _fila(r) == 1}
        for _fila_n, refs_de_fila in sorted(hoja["filas"].items()):
            refs = sorted(refs_de_fila, key=_columna)
            for ref in refs:
                bruto = celdas[ref]
                formula, almacenado = None, bruto
                f = _FORMULA.match(bruto)
                if f:
                    formula, almacenado = f.group("f"), f.group("v")
                if not _NUMERICO.match(almacenado):
                    continue
                col = _columna(ref)
                izquierda = [r for r in refs if _columna(r) < col and not _NUMERICO.match(_valor_almacenado(celdas[r]))]
                etiqueta_ref = izquierda[-1] if izquierda else None
                derecha = next((r for r in refs if _columna(r) == col + 1), None)
                unidad, unidad_ref = "", None
                if derecha and _es_celda_de_unidad(celdas[derecha], encabezados.get(col + 1, "")):
                    unidad, unidad_ref = celdas[derecha], derecha
                else:
                    h = _ENCABEZADO_UNIDAD.search(encabezados.get(col, ""))
                    if h:
                        unidad = h.group(1)
                extremos = [_columna(r) for r in (etiqueta_ref, ref, unidad_ref) if r]
                desde = next(r for r in refs if _columna(r) == min(extremos)) if etiqueta_ref else ref
                hasta = next(r for r in refs if _columna(r) == max(extremos))
                rango = ref if desde == hasta else f"{desde}:{hasta}"
                loc = {"hoja": nombre, ("celda" if ":" not in rango else "rango"): rango}
                n = ocurrencias[(nombre, ref)]
                ocurrencias[(nombre, ref)] += 1
                extra: dict[str, Any] = {"hoja_visibilidad": hoja["visibilidad"]}
                if formula:
                    extra.update(formula=formula, valor_almacenado=almacenado)
                evidencias.append(_cantidad(
                    identificador("ev", huella, f"{nombre}!{ref}", "celda", almacenado, unidad, n), archivo, loc,
                    metodo="celda", vigencia=vigencia, confianza="alta" if unidad else "baja", revision=not visible,
                    texto=celdas[etiqueta_ref] if etiqueta_ref else None, cantidad=almacenado, unidad=unidad, **extra))
                numericas += 1
                sin_unidad += 0 if unidad else 1
        for ref, comentario in libro[nombre].comentarios.items():
            n = ocurrencias[(nombre, "comentario", ref)]
            ocurrencias[(nombre, "comentario", ref)] += 1
            evidencias.append(_evidencia(
                identificador("ev", huella, f"{nombre}!{ref}", "celda", comentario, "", n), archivo, {"hoja": nombre, "celda": ref}, tipo="texto",
                metodo="celda", vigencia=vigencia, confianza="alta", revision=not visible, texto=comentario, elemento="comentario"))
        estado = "parcialmente_leido" if hoja["parcial"] else "leido"
        if hoja["parcial"]:
            motivos.append(f"Hoja «{nombre}»: " + "; ".join(hoja["parcial"])[:200])
        if not visible:
            motivos.append(f"Hoja oculta «{nombre}»: su vigencia no está confirmada; una persona decide si es histórica o vigente.")
        detalle.append({"ref": f"hoja:{nombre}", "estado": estado, "metodo": "celda", "visibilidad": hoja["visibilidad"],
                        "lectura": "no_determinable" if hoja["parcial"] else "sin_indicios_de_omision",
                        "motivo_lectura": "el lector avisó lectura parcial o fórmulas sin valor" if hoja["parcial"]
                        else "todas las celdas con valor se registraron; no prueba que no haya objetos no leídos",
                        "celdas_numericas": numericas, "celdas_numericas_sin_unidad": sin_unidad})
    for linea in sueltas:
        if linea.startswith("DIBUJOS/FORMAS") or linea.startswith("Gráfico (") or linea.startswith("COBERTURA PARCIAL"):
            nombre = hojas[0]["nombre"] if hojas else ""
            evidencias.append(_evidencia(
                identificador("vis", huella, nombre, "dibujo", linea[:80], "", 0), archivo, {"hoja": nombre, "celda": "A1"},
                tipo="elemento_visual", metodo="celda", estado="pendiente_revision_visual", confianza="baja", revision=True,
                motivo=linea[:200]))
            motivos.append("Hay dibujos, gráficos u objetos que el lector no interpreta.")
    if ilegible or not hojas:
        total = len(libro)
        detalle = [{"ref": f"hoja:{nh}", "estado": "ilegible", "metodo": "celda", "lectura": "no_determinable", "motivo_lectura": "la hoja no se leyó"}
                   for nh in libro]
        motivos.append(ilegible or "El lector no devolvió hojas.")
        for nh in libro:
            evidencias.append(_evidencia(identificador("ev", huella, nh, "ilegible", "", "", 0), archivo, {"hoja": nh, "celda": "A1"}, tipo="texto",
                                         metodo="celda", estado="ilegible", confianza="baja", revision=True, motivo=ilegible or "sin lectura"))
    else:
        total = len(hojas)
    leidas = sum(1 for d in detalle if d["estado"] == "leido")
    ilegibles = sum(1 for d in detalle if d["estado"] == "ilegible")
    documento = {"archivo": archivo, "sha256": huella, "tipo": "xlsx",
                 "cobertura": {"unidad": "hojas", "total": total, "leidas": leidas, "ilegibles": ilegibles, "detalle": detalle}}
    return Resultado(archivo, documento, evidencias, {}, motivos, sueltas,
                     {"lectura_s": round(lectura, 3), "hojas": total, "hojas_ocultas": sum(1 for h in hojas if h["visibilidad"] != "visible")})


def _es_celda_de_unidad(texto: str, encabezado: str) -> bool:
    """¿La celda a la derecha de una cifra es su unidad? Sí si está en el catálogo (o es ambigua) o si su columna se titula «unidad»."""
    if _NUMERICO.match(texto) or len(texto) > 30:
        return False
    return unidades.clasificar(texto)[0] != unidades.NO_RECONOCIDA or "unidad" in plano(encabezado)


def _valor_almacenado(bruto: str) -> str:
    f = _FORMULA.match(bruto)
    return f.group("v") if f else bruto


# ---------------------------------------------------------------- caso
def extraer_archivo(archivo: str, contenido: bytes, *, ocr: bool = True) -> Resultado:
    """Extrae un archivo; el tipo se decide por la extensión (.pdf, .xlsx). Otro tipo no se interpreta: se informa."""
    aislamiento.comprobar()
    huella = sha256(contenido)
    nombre = archivo.lower()
    if nombre.endswith(".pdf"):
        return _extraer_pdf(archivo, contenido, huella, ocr=ocr)
    if nombre.endswith((".xlsx", ".xlsm")):
        return _extraer_xlsx(archivo, contenido, huella)
    raise ValueError(f"El prototipo solo extrae PDF y XLSX; {archivo!r} no se interpreta.")


def _limitaciones(resultados: list[Resultado]) -> list[str]:
    """Lo que esta extracción no puede afirmar, en palabras llanas. `declarado_completo` solo habla de cobertura de lectura."""
    salida = [
        "Es evidencia localizada, no actividades: ninguna cifra es todavía una actividad cotizable.",
        "El extractor no puede determinar si la lectura fue exhaustiva; un validador sin errores no prueba que se recuperó todo.",
        "`declarado_completo` significa cobertura de lectura sin pendientes, no que estén todas las actividades.",
    ]
    for r in resultados:
        detalle = r.documento["cobertura"]["detalle"]
        sin_registrar = sum(d.get("cifras_no_registradas", 0) for d in detalle)
        if sin_registrar:
            salida.append(f"{r.archivo}: {sin_registrar} cifra(s) del texto no se registraron como cantidad (sin unidad, hora, fecha o numeración).")
        dudosas = [d["ref"] for d in detalle if d.get("lectura") == "no_determinable"]
        if dudosas:
            salida.append(f"{r.archivo}: lectura no determinable en {', '.join(dudosas)}.")
    return salida


def evidencia_del_caso(caso: str, archivos: dict[str, bytes], *, ocr: bool = True) -> tuple[dict[str, Any], PaqueteDeOriginales, list[Resultado]]:
    """(documento de evidencia T-117, paquete de originales con el OCR, resultados por archivo). No interpreta ni declara actividades."""
    resultados = [extraer_archivo(nombre, contenido, ocr=ocr) for nombre, contenido in sorted(archivos.items())]
    motivos = [m for r in resultados for m in r.motivos_de_incompletitud]
    evidencias_previas = [e for r in resultados for e in r.evidencias]
    dudosas = [e for e in evidencias_previas if e.get("unidad_estado") in (unidades.NO_RECONOCIDA, unidades.AMBIGUA)]
    if dudosas:
        motivos.append(f"{len(dudosas)} cifra(s) con unidad no reconocida o ambigua: una persona debe confirmar qué miden.")
    completo = not motivos
    evidencias = [e for r in resultados for e in r.evidencias]
    doc = {"version_modelo": VERSION_MODELO, "caso": caso, "documentos": [r.documento for r in resultados], "evidencias": evidencias,
           "interpretaciones": [], "contradicciones": [],
           "analisis": {"declarado_completo": completo, "motivos_de_incompletitud": motivos, "alcance": "evidencia_localizada",
                        "exhaustividad": "no_determinable", "actividades_interpretadas": 0, "limitaciones": _limitaciones(resultados)}}
    paquete = PaqueteDeOriginales(archivos, {r.archivo: r.textos_ocr for r in resultados if r.textos_ocr})
    return doc, paquete, resultados
