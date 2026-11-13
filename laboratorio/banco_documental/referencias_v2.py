"""Propuesta v2 de las referencias del banco (T-75): se DERIVA de las referencias v1 congeladas, que no se tocan.

Cada cambio queda registrado dentro de la referencia v2 (`cambios`) con su motivo, su origen y su efecto esperado. Los
documentos originales y las respuestas originales de Work no cambian: solo cambia cómo se juzgan. La v2 se diseñó tras ver
las respuestas de C1, C2 y C3, de modo que reevaluarlas con la v2 NO es una evaluación ciega: sirve para medir el efecto de
los cambios, no para repetir la prueba.

Uso: python -m laboratorio.banco_documental.referencias_v2  (escribe `referencias_v2/`); `--verificar` comprueba que coincide.
"""

from __future__ import annotations

import copy
import hashlib
import json
import sys
from pathlib import Path
from typing import Any

RAIZ = Path(__file__).resolve().parent
REFERENCIAS_V1 = RAIZ / "referencias"
REFERENCIAS_V2 = RAIZ / "referencias_v2"
VERSION = "2"
Dato = dict[str, Any]


def _cambio(codigo: str, caso: str, que: str, por_que: str, origen: str, efecto: str) -> Dato:
    return {"id": codigo, "caso": caso, "que": que, "por_que": por_que, "origen_del_cambio": origen, "efecto": efecto}


def _a_grupos(claves: list[str]) -> list[list[str]]:
    """Cada clave de la v1 pasa a ser un grupo de una sola alternativa (todas deben aparecer)."""
    return [[clave] for clave in claves]


def _base(v1: Dato) -> Dato:
    v2 = copy.deepcopy(v1)
    v2["version"] = VERSION
    for actividad in v2["actividades"]:
        actividad["grupos_entregable"] = _a_grupos(actividad.pop("claves_entregable", []))
    for restriccion in v2["restricciones"]:
        restriccion["grupos"] = _a_grupos(restriccion.pop("claves"))
    for contradiccion in v2["contradicciones"]:
        contradiccion["grupos"] = _a_grupos(contradiccion.pop("literales_clave"))
        contradiccion["exigida"] = True
        contradiccion.pop("palabras", None)
    for faltante in v2["faltantes"]:
        faltante["grupos"] = [faltante.pop("palabras")]
        faltante["reconoce_en"] = ["preguntas_cliente"]
    for revision in v2["revisiones_internas"]:
        revision["grupos"] = [revision.pop("palabras")]
        revision["tipo"] = "verificable"
    for dato in v2["datos_presentes"]:
        dato["grupos"] = [dato.pop("palabras")]
    for elemento in v2["elementos_visuales"]:
        elemento["verificacion"] = "humana"
        elemento["grupos"] = [elemento.pop("claves")]
    v2["contradicciones_opcionales"] = []
    v2["ambiguedades"] = []
    v2["cambios"] = []
    return v2


def _aplicar_c1(v2: Dato) -> None:
    for faltante in v2["faltantes"]:
        if faltante["id"] == "C1-F1":
            faltante["grupos"] = [["poligono", "kmz", "kml", "limites", "delimit"], ["predio", "lote", "area", "alcance"]]
        if faltante["id"] == "C1-F2":
            faltante["grupos"] = [["sistema de coordenadas", "datum", "magna", "sistema de referencia", "coordenadas"]]
            faltante["reconoce_en"] = ["preguntas_cliente", "revisiones_internas"]
            faltante["destino"] = "cliente_o_revision_interna"
            faltante["motivo"] = ("La Nota 6 difiere la definición del sistema de coordenadas a la reunión de inicio: basta "
                                  "reconocerlo como pendiente, ya sea como pregunta al cliente o como revisión interna.")
        if faltante["id"] == "C1-F3":
            faltante["grupos"] = [["12"], ["10"]]
    for revision in v2["revisiones_internas"]:
        if revision["id"] == "C1-I1":
            revision["grupos"] = [["18,5", "intervencion"], ["21,0", "predio", "escritura"]]
    v2["revisiones_internas"] = [r for r in v2["revisiones_internas"] if r["id"] != "C1-I2"]
    v2["ambiguedades"] = [{
        "id": "C1-M1", "actividad": "C1-A2", "literal": "2,350", "unidad": "km", "gravedad": "no_critico",
        "motivo": "«2,350 km» admite 2,35 km o 2350 km según el separador; el prompt pide declarar la cifra ambigua.",
        "grupos": [["2,350", "2.350"], ["separador", "decimal", "miles", "ambig", "2,35"]],
        "reconoce_en": ["limitaciones_de_lectura", "revisiones_internas", "preguntas_cliente", "contradicciones"]}]
    v2["contradicciones_opcionales"] = [{
        "id": "C1-K2", "tema": "alcance de las construcciones del sector sur",
        "grupos": [["nota 7"], ["fachada"]], "palabras_de_contexto": ["construccion"],
        "motivo": "La Tabla 1 pide planta y fachada de 7 construcciones y la Nota 7 limita el sector sur a fachada principal."}]
    v2["cambios"] = [
        _cambio("V2-C1-1", "C1", "El sistema de coordenadas (C1-F2) pasa a destino «cliente_o_revision_interna»: se reconoce en "
                "preguntas o en revisiones internas.", "La Nota 6 del documento ya difiere su definición a la reunión de inicio; "
                "Work lo trató como revisión interna y no preguntó.", "respuesta de Work C1 (T-72)",
                "C1-F2 deja de contarse como omisión de Work (v1: no crítico)."),
        _cambio("V2-C1-2", "C1", "La ambigüedad de «2,350 km» sale de las revisiones internas y pasa a una lista de ambigüedades "
                "(C1-M1), reconocible en limitaciones, revisiones, preguntas o contradicciones.",
                "Era un juicio interno que el prompt ya pedía declarar como ambigüedad de lectura.", "T-72 (informe C1)",
                "Sigue siendo un hallazgo no crítico si Work no la declara (en C1 no la declaró)."),
        _cambio("V2-C1-3", "C1", "C1-F1, C1-F3 y C1-I1 exigen dos conceptos, no una palabra suelta.",
                "Evitar coincidencias por una sola palabra.", "T-73 (límites del comparador)",
                "C1-F3 exige «12» y «10»; C1-F1, un término de polígono y otro de predio o alcance."),
        _cambio("V2-C1-4", "C1", "Se añade C1-K2 (Nota 7 frente a planta y fachada) como contradicción opcional.",
                "Work la señaló y está fundada en el documento; la referencia v1 no la contemplaba.", "respuesta de Work C1 (T-72)",
                "Si aparece, se cuenta como hallazgo adicional correcto; si no, no se penaliza.")]


def _aplicar_c2(v2: Dato) -> None:
    for actividad in v2["actividades"]:
        actividad["entregable"] = ""
        actividad["grupos_entregable"] = []
        actividad["entregable_en_el_documento"] = "no especificado"
    for faltante in v2["faltantes"]:
        if faltante["id"] == "C2-F1":
            faltante["grupos"] = [["ubicacion", "anexo"], ["plano", "anexo", "delimit", "embalse", "municipio"]]
        if faltante["id"] == "C2-F3":
            faltante["grupos"] = [["45,0", "41,8", "area", "magnitud"], ["definitiv", "confirm", "rige", "vigente"]]
    for revision in v2["revisiones_internas"]:
        if revision["id"] == "C2-I1":
            revision["tipo"] = "juicio_tecnico"
        if revision["id"] == "C2-I2":
            revision["grupos"] = [["nivel de referencia", "vertical", "datum vertical", "cota"]]
            revision["reconoce_en"] = ["revisiones_internas", "preguntas_cliente"]
    v2["contradicciones_opcionales"] = [{
        "id": "C2-K2", "tema": "título «sin escala» frente a una barra gráfica de escala",
        "grupos": [["sin escala"], ["barra", "250", "500"]], "palabras_de_contexto": ["escala"],
        "motivo": "La Figura 1 se titula «sin escala» pero dibuja una barra con 0, 250 y 500 m (incoherencia real del documento)."}]
    v2["cambios"] = [
        _cambio("V2-C2-1", "C2", "Se retiran los entregables de las cuatro actividades.", "El PDF no tiene columna ni texto de "
                "entregables: la v1 los inventó. Work respondió correctamente «no se especifica».", "revisión de T-73",
                "Desaparecen los dos `entregable_omitido` de C2 (eran un defecto de la referencia)."),
        _cambio("V2-C2-2", "C2", "C2-I1 (ecosonda de doble frecuencia) pasa a juicio técnico: no se penaliza su ausencia y se "
                "reporta como revisión humana. C2-I2 (nivel de referencia vertical) se acepta en revisiones o en preguntas.",
                "Son criterios técnicos opinables; Work cubrió el datum vertical como pregunta al cliente.", "T-73",
                "Desaparecen las dos `revision_interna_omitida` de C2."),
        _cambio("V2-C2-3", "C2", "Se añade C2-K2 («sin escala» frente a la barra de escala) como contradicción opcional.",
                "Es una incoherencia real que mi generación del PDF dejó y Work detectó.", "respuesta de Work C2 (T-73)",
                "Hallazgo adicional correcto si aparece; no se penaliza si falta."),
        _cambio("V2-C2-4", "C2", "C2-F1 y C2-F3 exigen dos conceptos.", "Evitar coincidencias por una sola palabra.", "T-73",
                "C2-F3 exige hablar de área y de confirmación/vigencia.")]


def _aplicar_c3(v2: Dato) -> None:
    for restriccion in v2["restricciones"]:
        if restriccion["id"] == "C3-R1":
            restriccion["grupos"] = [["0,5", "0.5"], ["curva", "equidistancia"]]
        if restriccion["id"] == "C3-R2":
            restriccion["grupos"] = [["0,02", "0.02"], ["precision"]]
    for contradiccion in v2["contradicciones"]:
        if contradiccion["id"] == "C3-K1":
            contradiccion["grupos"] = [["7"], ["6", "l-06", "seis"], ["lote", "predio"]]
        if contradiccion["id"] == "C3-K2":
            contradiccion["grupos"] = [["31,2", "31.2"], ["33,7", "33.7"]]
    v2["contradicciones_opcionales"] = [
        {"id": "C3-K3", "tema": "ejes viales: 3.1 km (hoja oculta) frente a 3.625 km", "grupos": [["3,1", "3.1"], ["3,625", "3.625"]],
         "palabras_de_contexto": ["eje"], "motivo": "La hoja oculta también difiere en los ejes viales."},
        {"id": "C3-K4", "tema": "puntos de control: 20 (hoja oculta) frente a 24", "grupos": [["20"], ["24"]],
         "palabras_de_contexto": ["punto"], "motivo": "La hoja oculta también difiere en los puntos de control."}]
    v2["faltantes"] = [{
        "id": "C3-F1", "tema": "cantidad de lotes", "bloqueante": False, "afecta_cantidades": True, "destino": "cliente",
        "grupos": [["7"], ["lote", "predio"]], "reconoce_en": ["preguntas_cliente"],
        "motivo": "La discrepancia entre 7 lotes (Resumen!C9) y 6 listados (Detalle!A2:A7) afecta el área y los 24 puntos de "
                  "control: preguntarla al cliente es legítimo."}]
    for revision in v2["revisiones_internas"]:
        if revision["id"] == "C3-I1":
            revision["grupos"] = [["oculta", "borrador"], ["histor", "vigente", "anterior", "obsolet"]]
    v2["cambios"] = [
        _cambio("V2-C3-1", "C3", "Las restricciones numéricas aceptan coma o punto decimal («0,5» o «0.5», «0,02» o «0.02»).",
                "El libro guarda 0.5 y 0.02; exigir coma penalizaba a quien conservaba el literal.", "T-74 (límite conocido)",
                "Desaparecen las dos `restriccion_omitida` falsas."),
        _cambio("V2-C3-2", "C3", "Se declara C3-F1: preguntar la discrepancia 7/6 lotes es una pregunta esperada (no bloqueante, "
                "afecta cantidades).", "La v1 no tenía faltantes y toda pregunta salía «de más»; la discrepancia sí cambia "
                "el área y los 24 puntos.", "T-74", "La pregunta de los 7 lotes deja de ser «pregunta de más»."),
        _cambio("V2-C3-3", "C3", "Se añaden C3-K3 y C3-K4 (otras dos diferencias de la hoja oculta) como contradicciones opcionales.",
                "La hoja oculta difiere en las tres actividades; la v1 solo recogía una.", "T-74",
                "Hallazgos adicionales correctos si aparecen; no se penalizan si faltan."),
        _cambio("V2-C3-4", "C3", "C3-K1 exige «7», «6» y «lote/predio» juntos; C3-I1 exige hoja oculta y vigencia.",
                "Evitar coincidencias por una sola palabra.", "T-73", "Menos falsos aciertos.")]


_APLICAR = {"C1": _aplicar_c1, "C2": _aplicar_c2, "C3": _aplicar_c3}


def sha256(contenido: bytes) -> str:
    """La huella SHA-256 en hexadecimal."""
    return hashlib.sha256(contenido).hexdigest()


def a_json(valor: Any) -> bytes:
    """JSON estable para que los bytes sean reproducibles."""
    return (json.dumps(valor, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8")


def construir_v2(caso: str, v1_bytes: bytes) -> Dato:
    """La referencia v2 de un caso, derivada de los bytes de su referencia v1 (que no se modifica)."""
    v1 = json.loads(v1_bytes.decode("utf-8"))
    v2 = _base(v1)
    _APLICAR[caso](v2)
    v2["deriva_de"] = {"version": v1["version"], "sha256": sha256(v1_bytes)}
    v2["nota"] = ("Propuesta posterior a las respuestas de Work: reevaluar con ella no es una evaluación ciega. La referencia v1 "
                  "sigue siendo la línea base congelada.")
    return v2


def archivos_v2() -> dict[str, bytes]:
    """Todos los archivos de `referencias_v2/` como {nombre: bytes}."""
    archivos: dict[str, bytes] = {}
    for caso in _APLICAR:
        v1_bytes = (REFERENCIAS_V1 / f"{caso}.referencia.json").read_bytes()
        archivos[f"{caso}.referencia.v2.json"] = a_json(construir_v2(caso, v1_bytes))
    archivos["manifiesto_referencias_v2.json"] = a_json({
        "version": VERSION, "referencias": [{"id": nombre.split(".")[0], "archivo": nombre, "sha256": sha256(contenido)}
                                            for nombre, contenido in sorted(archivos.items())]})
    return archivos


def verificar() -> list[str]:
    """Diferencias entre `referencias_v2/` guardado y lo que se derivaría hoy de las referencias v1."""
    problemas = []
    for nombre, contenido in archivos_v2().items():
        ruta = REFERENCIAS_V2 / nombre
        if not ruta.is_file():
            problemas.append(f"falta {nombre}")
        elif ruta.read_bytes() != contenido:
            problemas.append(f"difiere {nombre}")
    return problemas


def main(argumentos: list[str]) -> int:
    """Escribe `referencias_v2/` o, con `--verificar`, comprueba que se reproduce."""
    if "--verificar" in argumentos:
        problemas = verificar()
        print("\n".join(problemas) if problemas else "Las referencias v2 se reproducen byte a byte.")
        return 1 if problemas else 0
    REFERENCIAS_V2.mkdir(exist_ok=True)
    for nombre, contenido in archivos_v2().items():
        (REFERENCIAS_V2 / nombre).write_bytes(contenido)
    print(f"Referencias v2 escritas en {REFERENCIAS_V2}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
