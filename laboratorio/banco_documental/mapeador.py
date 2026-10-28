"""Mapeador de laboratorio (T-76): de la extracción documental del banco al formato estructural del contrato de Work.

NO es el análisis comercial de PRECISO_V6.1.3 ni una ruta hacia producción: solo prepara equivalencias estructurales
comprobables para poder medir la fidelidad de un retorno real. No toca Gmail, Drive ni SQLite, no crea casos ni borradores.

Reglas (todas con prueba):
- cantidades, unidades, nombres y fuentes pasan LITERALES; nada se convierte, recalcula ni completa;
- lo que el contrato necesita y la extracción no tiene (identidad del correo) lo aporta quien llama; si falta, el mapeo no es posible;
- nada queda `confirmado` si el documento declara contradicciones, lectura visual sin validar, limitaciones de lectura,
  valores históricos o actividades que se descartaron; las contradicciones se copian como `pendiente`, nunca se resuelven;
- una actividad de una hoja oculta o marcada no vigente NO es una actividad: queda fuera de la tabla y se informa;
- lo que no tiene equivalencia demostrable no se inventa: se devuelve en `no_mapeado` con el motivo;
- el resultado nunca declara `analysis_complete` verdadero ni un estado de «información suficiente».
"""

from __future__ import annotations

import copy
import json
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from anka.dominio import work_contract
from anka.dominio.activities import limpiar_cantidad_y_unidad
from anka.dominio.tolerancia_work import Rechazo, comprobar_identidad, normalizar_analisis
from laboratorio.banco_documental import validador

MAPEADO = "MAPEADO"
MAPEADO_CON_PENDIENTES = "MAPEADO_CON_PENDIENTES"
MAPEO_NO_POSIBLE = "MAPEO_NO_POSIBLE"
RECHAZADO_ANTES_DEL_MAPEO = "RECHAZADO_ANTES_DEL_MAPEO"
FALTA_IDENTIDAD = "FALTA_IDENTIDAD"

ESTADO_INTERNO = "Requiere revisión técnica interna"
IDENTIDAD_OBLIGATORIA = ("thread_id", "message_id", "client_email", "project_name")
OPCIONALES_DE_CONTEXTO = ("client_name", "received_at", "requested_by_email", "requested_by_name", "gmail_url")
Dato = dict[str, Any]


@dataclass(frozen=True)
class NoMapeado:
    """Algo de la extracción que no pasó al contrato, con el motivo; el contenido original se conserva literal."""

    campo: str
    motivo: str
    contenido: Any

    def como_dict(self) -> Dato:
        """El elemento como diccionario serializable."""
        return {"campo": self.campo, "motivo": self.motivo, "contenido": self.contenido}


@dataclass
class ResultadoMapeo:
    """Lo que salió del mapeo: la carga estructural (si fue posible), lo que quedó fuera y el motivo si no se pudo."""

    estado: str
    motivo: str = ""
    payload: Dato | None = None
    no_mapeado: list[NoMapeado] = field(default_factory=list)
    pendientes: list[str] = field(default_factory=list)
    validacion: Dato | None = None

    def como_dict(self) -> Dato:
        """El resultado como diccionario serializable."""
        return {"estado": self.estado, "motivo": self.motivo, "payload": self.payload,
                "no_mapeado": [n.como_dict() for n in self.no_mapeado], "pendientes": self.pendientes,
                "validacion": self.validacion}


def formatear_fuente(documento: str, origen: Dato) -> str:
    """El origen del banco como cita de fuente («archivo, p. 1, tabla 1, fila 2»); solo las claves que vienen, sin inventar."""
    etiquetas = (("pagina", "p. {}"), ("tabla", "tabla {}"), ("fila", "fila {}"), ("hoja", "hoja {}"), ("celda", "celda {}"))
    partes = [formato.format(origen[clave]) for clave, formato in etiquetas if origen.get(clave) not in (None, "")]
    return ", ".join([documento, *partes])


def _hojas_no_vigentes(datos: Dato) -> dict[str, str]:
    """Hojas que no son visibles (p. ej. ocultas): sus datos no son cantidades vigentes."""
    return {str(h["nombre"]): str(h.get("visibilidad", "")) for h in datos.get("hojas", [])
            if isinstance(h, dict) and "nombre" in h and str(h.get("visibilidad", "")).casefold() != "visible"}


def _excluida(actividad: Dato, no_visibles: dict[str, str], hojas_conocidas: set[str]) -> str:
    """Por qué una actividad NO puede pasar como cantidad vigente (cadena vacía si puede pasar)."""
    origen = actividad.get("origen")
    hoja = str(origen.get("hoja", "")) if isinstance(origen, dict) else ""
    if hoja and hoja in no_visibles:
        return f"su fuente es la hoja «{hoja}» ({no_visibles[hoja]}): los datos de hojas no visibles no son cantidades vigentes"
    if hoja and hojas_conocidas and hoja not in hojas_conocidas:
        return f"su fuente cita la hoja «{hoja}», que la extracción no declara entre las hojas del libro"
    if actividad.get("vigente") is False:
        return "la extracción la marca como no vigente"
    return ""


def _fila_de_actividad(actividad: Dato, documento: str, pendiente: bool, motivo: str) -> Dato:
    fila: Dato = {"activity": actividad["nombre"], "quantity": actividad["cantidad"], "unit": actividad["unidad"],
                  "entregable": actividad["entregable"], "fuente": formatear_fuente(documento, actividad["origen"]),
                  "estado": "pendiente" if pendiente else "confirmado"}
    notas = []
    if actividad.get("formula"):
        notas.append(f"Fórmula {actividad['formula']}; valor almacenado {actividad.get('valor_almacenado', 'no indicado')}.")
    if pendiente:
        notas.append(motivo)
    if notas:
        fila["notes"] = " ".join(notas)
    return fila


def _pendientes_del_documento(datos: Dato, descartadas: int) -> list[str]:
    """Razones por las que nada puede quedar confirmado; vacío solo si el documento no declara ninguna."""
    razones = []
    for campo, texto in (("contradicciones", "contradicciones declaradas"), ("evidencia_visual", "evidencia visual sin validación independiente"),
                         ("limitaciones_de_lectura", "limitaciones de lectura"),
                         ("valores_historicos_u_obsoletos", "valores históricos u obsoletos")):
        if datos.get(campo):
            razones.append(f"{len(datos[campo])} {texto}")
    if descartadas:
        razones.append(f"{descartadas} actividades descartadas por no ser vigentes")
    return razones


def _adjunto(datos: Dato, documento: str, tamano: int | None) -> Dato:
    adjunto: Dato = {"filename": documento, "status": "registrado"}
    if tamano is not None:
        adjunto["size_bytes"] = tamano
    if datos.get("limitaciones_de_lectura"):
        adjunto["reason"] = " | ".join(str(t) for t in datos["limitaciones_de_lectura"])
    if datos.get("evidencia_visual"):
        adjunto["visual_interpretation"] = "no_certificada"
        adjunto["coverage"] = "parcial"
        adjunto["reason"] = ((adjunto.get("reason", "") + " | ") if adjunto.get("reason") else "") + \
            "lectura visual sin validación independiente"
    return adjunto


def _dependencias_visuales(datos: Dato, documento: str) -> list[Dato]:
    """Cada elemento visual queda como dependencia pendiente: la extracción no lo valida y el mapeo tampoco."""
    filas = []
    for elemento in datos.get("evidencia_visual", []):
        if not isinstance(elemento, dict):
            continue
        etiqueta = str(elemento.get("elemento", "elemento visual"))
        pagina = elemento.get("pagina")
        filas.append({"description": f"Lectura visual no certificada: {etiqueta}", "status": "pendiente",
                      "source": formatear_fuente(documento, {"pagina": pagina} if pagina else {})})
    return filas


def _sin_equivalente(datos: Dato) -> list[NoMapeado]:
    motivos = {
        "preguntas_cliente": "el contrato exige para cada pregunta su motivo y las fuentes revisadas (question_audit) y la extracción no los trae",
        "revisiones_internas": "no tienen equivalente con los campos obligatorios de question_audit (motivo y fuentes revisadas)",
        "elementos_visuales": "descripciones sin localizador estructurado; la evidencia visual pasa como dependencia pendiente",
        "valores_historicos_u_obsoletos": "el contrato no distingue lo no vigente; mezclarlo con fields podría leerse como dato vigente",
        "otros_datos_numericos": "datos auxiliares sin equivalente contractual; no son actividades ni cantidades vigentes",
        "hojas": "inventario de lectura de la extracción; el contrato lo cubre con reading_audit, que la extracción no trae",
        "calidad_ocr": "calidad de lectura por página sin campo equivalente",
        "evidencia_visual": "el detalle (texto impreso y manuscrito) no cabe en evidence, que exige la cita literal (excerpt)",
    }
    return [NoMapeado(campo, motivo, datos[campo]) for campo, motivo in motivos.items() if datos.get(campo)]


def mapear(datos: Dato, contexto: Dato, documento: str, tamano_del_documento: int | None = None) -> ResultadoMapeo:
    """Convierte una extracción YA validada al formato del contrato; no valida la lectura ni decide nada comercial."""
    faltan = [c for c in IDENTIDAD_OBLIGATORIA if not isinstance(contexto.get(c), str) or not contexto[c].strip()]
    if faltan:
        return ResultadoMapeo(MAPEO_NO_POSIBLE, f"{FALTA_IDENTIDAD}: el contexto no trae {', '.join(faltan)}; no se inventan")
    if not documento.strip():
        return ResultadoMapeo(MAPEO_NO_POSIBLE, "falta el nombre del documento: sin él no se puede escribir ninguna fuente")
    no_visibles = _hojas_no_vigentes(datos)
    hojas_conocidas = {str(h["nombre"]) for h in datos.get("hojas", []) if isinstance(h, dict) and "nombre" in h}
    vigentes, no_mapeado = [], []
    for actividad in datos["actividades"]:
        razon = _excluida(actividad, no_visibles, hojas_conocidas)
        if razon:
            no_mapeado.append(NoMapeado("actividades", f"actividad descartada: {razon}", actividad))
        else:
            vigentes.append(actividad)
    pendientes = _pendientes_del_documento(datos, len(no_mapeado))
    motivo = ("El documento declara " + "; ".join(pendientes) + ": revisión interna antes de confirmar.") if pendientes else ""
    payload: Dato = {k: contexto[k] for k in (*IDENTIDAD_OBLIGATORIA, *OPCIONALES_DE_CONTEXTO) if contexto.get(k)}
    payload.update({
        "status": ESTADO_INTERNO, "engine": "chatgpt_work", "analysis_complete": False,
        "summary": f"Mapeo estructural de laboratorio (T-76) de la extracción del caso {datos['caso']}; no es un análisis comercial.",
        "activities": [_fila_de_actividad(a, documento, bool(pendientes), motivo) for a in vigentes],
        "fields": [{"name": nombre, "value": str(texto), "source": documento}
                   for campo, nombre in (("restricciones", "Restricción"), ("entregables", "Entregable"))
                   for texto in datos.get(campo, [])],
        "contradictions": [{"details": str(texto), "sources": [documento], "status": "pendiente"}
                           for texto in datos.get("contradicciones", [])],
        "attachments": [_adjunto(datos, documento, tamano_del_documento)],
        "scope_dependencies": _dependencias_visuales(datos, documento),
    })
    no_mapeado.extend(_sin_equivalente(datos))
    estado = MAPEADO_CON_PENDIENTES if pendientes or no_mapeado else MAPEADO
    return ResultadoMapeo(estado, "", payload, no_mapeado, pendientes)


def comprobar_contra_contrato(payload: Dato) -> Dato:
    """Prueba en seco, sin base ni red, qué haría el contrato real con la carga: esquema, tolerancia y normalización de cantidades.

    Usa solo funciones puras de `anka.dominio` sobre una COPIA. Devuelve `rechazo` si el puente la rechazaría, las
    correcciones que haría, los avisos de cobertura y las diferencias de cantidad o unidad que introduciría el importador.
    """
    copia = copy.deepcopy(payload)
    informe: Dato = {"errores_de_esquema": work_contract.validate(copia), "rechazo": "", "correcciones": [], "avisos_del_puente": [],
                     "avisos_de_cobertura": work_contract.attachment_coverage_warnings(copia), "diferencias_del_importador": []}
    try:
        comprobar_identidad(copia)
        normalizar_analisis(copia)
    except Rechazo as error:
        informe["rechazo"] = str(error)
        return informe
    informe["correcciones"] = list(copia.get("_correcciones_de_work", []))
    informe["avisos_del_puente"] = list(copia.get("_avisos_del_puente", []))
    for indice, original in enumerate(payload.get("activities", [])):
        fila = dict(original)
        limpiar_cantidad_y_unidad(fila)
        for clave in ("quantity", "unit", "estado"):
            if fila.get(clave) != original.get(clave):
                informe["diferencias_del_importador"].append(
                    {"fila": indice, "campo": clave, "antes": original.get(clave), "despues": fila.get(clave)})
    return informe


def mapear_respuesta(contenido: bytes, contexto: Dato, caso: str | None = None, documento: bytes | None = None,
                     nombre_documento: str = "", destino: Path | None = None) -> ResultadoMapeo:
    """Valida primero (T-75, sin reparar) y mapea solo lo estructuralmente aceptado; una respuesta inválida no se mapea."""
    veredicto = (validador.procesar(contenido, destino, caso, documento, nombre_documento) if destino is not None
                 else validador.validar(contenido, caso, documento, nombre_documento))
    resumen = veredicto.como_dict()
    if not veredicto.aceptada or veredicto.datos is None:
        return ResultadoMapeo(MAPEO_NO_POSIBLE, f"{RECHAZADO_ANTES_DEL_MAPEO}: {veredicto.estado}", None, [], [], resumen)
    resultado = mapear(veredicto.datos, contexto, nombre_documento, len(documento) if documento is not None else None)
    resultado.validacion = resumen
    return resultado


def main(argumentos: list[str]) -> int:
    """Uso: python -m laboratorio.banco_documental.mapeador <respuesta> <contexto.json> <documento> [--caso C1]."""
    if len(argumentos) not in (3, 5):
        print(main.__doc__)
        return 2
    caso = argumentos[4] if len(argumentos) == 5 and argumentos[3] == "--caso" else None
    documento = Path(argumentos[2])
    resultado = mapear_respuesta(Path(argumentos[0]).read_bytes(), json.loads(Path(argumentos[1]).read_text(encoding="utf-8")), caso,
                                 documento.read_bytes(), documento.name)
    salida = resultado.como_dict()
    if resultado.payload is not None:
        salida["comprobacion_contra_contrato"] = comprobar_contra_contrato(resultado.payload)
    print(json.dumps(salida, ensure_ascii=False, indent=2))
    return 0 if resultado.payload is not None else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
