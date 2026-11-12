"""Cambios a UNA actividad hechos desde el panel del caso, con su respaldo (U-112, partes B y C).

El panel no reemplaza la tabla entera: cada acción (resolver, reabrir, quitar, corregir, agregar) arma la tabla nueva a
partir de la que tiene el caso y la guarda con `save_activities`, que exige el respaldo y deja el rastro. Nada aquí
escribe al cliente (R8) ni usa un modelo.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import date

from anka.dominio.activities import MAX_CELL, huella_de_tabla, normalize_estado
from anka.dominio.analisis import Actividad
from anka.dominio.caso import load_analysis
from anka.dominio.errores import ErrorDeValidacion
from anka.dominio.estado_coherente import DATOS_ESENCIALES, datos_que_faltan
from anka.dominio.respaldo import describir_respaldo, normalizar_respaldo
from anka.infra.store import lectura
from anka.servicios.ediciones import save_activities

ACCIONES = ("resolver", "reabrir", "quitar", "corregir", "agregar")
ARTICULO = {"ubicación": "la ubicación", "cantidad": "la cantidad", "unidad": "la unidad",
            "entregable": "el entregable", "plazo": "el plazo"}
CAMPOS_CORREGIBLES = ("servicio_tecnica", "ubicacion", "quantity", "unit", "entregable", "plazo_restriccion", "notes")


def _limpio(valor: object) -> str:
    return " ".join(str(valor or "").split())[:MAX_CELL]


def _tabla_actual(thread_id: str) -> list[Actividad]:
    with lectura() as db:
        row = db.execute("SELECT analysis_json FROM quotes WHERE thread_id=?", (thread_id,)).fetchone()
    if not row:
        raise ErrorDeValidacion("Este caso ya no existe. Vuelve a Mis casos.")
    return [dict(f) for f in (load_analysis(row["analysis_json"]).get("activities") or [])]    # type: ignore[misc]


def _anotar(fila: Actividad, texto: str) -> None:
    fila["notes"] = f"{str(fila.get('notes') or '').strip()} {texto}".strip()


def _fila(tabla: list[Actividad], indice: int) -> Actividad:
    if not 0 <= indice < len(tabla):
        raise ErrorDeValidacion("Esa actividad ya no está en la tabla. Recarga el caso y vuelve a intentarlo.")
    return tabla[indice]


def _datos_escritos(fila: Actividad, datos: Mapping[str, str], obligatorios: bool) -> None:
    """Pone en la fila los datos esenciales que la persona escribió. Los que faltaban son obligatorios al resolver."""
    for clave, _nombre in DATOS_ESENCIALES:
        valor = _limpio(datos.get(f"dato_{clave}"))
        if valor:
            fila[clave] = valor              # type: ignore[literal-required]
    if obligatorios:
        faltan = datos_que_faltan(fila)
        if faltan:
            raise ErrorDeValidacion(f"Escribe {ARTICULO[faltan[0]]} para dejar esta actividad lista.")


def cambiar_actividad(thread_id: str, accion: str, indice: int, datos: Mapping[str, str],
                      base_version: str = "", quien: str = "") -> str:
    """Aplica una acción a una actividad del caso y devuelve el aviso para la persona.

    `datos` trae lo que escribió el panel: los datos esenciales (`dato_ubicacion`, `dato_quantity`…), `decision`,
    `nombre`, `campo_*` y el respaldo (`origen`, `medio`, `fecha`, `documento`, `nota`). Si la tabla cambió desde que
    la persona abrió el caso (`base_version`), se rechaza sin guardar nada (U-34.5).
    """
    if accion not in ACCIONES:
        raise ErrorDeValidacion("No entendimos qué querías hacer con la actividad. Recarga el caso.")
    if datos.get("origen") == "tecnico" and not quien.strip():
        raise ErrorDeValidacion("Para registrar un criterio técnico escribe tu nombre en Ajustes: queda junto a la "
                                "recomendación.")
    tabla = _tabla_actual(thread_id)
    base = base_version or huella_de_tabla(tabla)
    nueva: list[Actividad] = [Actividad(**f) for f in tabla]       # copia: la original no se toca
    if accion == "agregar":
        nombre = _limpio(datos.get("nombre"))
        if len(nombre) < 2:
            raise ErrorDeValidacion("Escribe cómo se llama la actividad.")
        if any(_limpio(f.get("activity")).casefold() == nombre.casefold() for f in tabla):
            raise ErrorDeValidacion("Ya hay una actividad con ese nombre: ábrela en la lista y cámbiala ahí.")
        fila: Actividad = {"activity": nombre, "servicio_tecnica": _limpio(datos.get("campo_servicio_tecnica")),
                           "estado": "pendiente"}
        _datos_escritos(fila, datos, obligatorios=False)
        nuevo_respaldo = normalizar_respaldo(datos, date.today(), quien)
        fila["estado"] = "pendiente" if datos_que_faltan(fila) else "confirmado"
        _anotar(fila, f"Agregada: {describir_respaldo(nuevo_respaldo)} — «{nuevo_respaldo['nota']}».")
        nueva.append(fila)
        mensaje = f"Listo: agregaste «{nombre}»."
    else:
        fila = _fila(nueva, indice)
        nombre = _limpio(fila.get("activity"))
        estado = normalize_estado(fila.get("estado"))
        if accion == "resolver":
            if estado == "confirmado":
                raise ErrorDeValidacion("Esa actividad ya está lista.")
            decision = _limpio(datos.get("decision"))
            if (estado == "contradictorio" or not datos_que_faltan(fila)) and len(decision) < 3:
                raise ErrorDeValidacion("Escribe qué quedó decidido para dejar esta actividad lista.")
            _datos_escritos(fila, datos, obligatorios=True)
            respaldo = normalizar_respaldo(datos, date.today(), quien)
            if decision:
                _anotar(fila, f"Decisión: {decision}.")
            _anotar(fila, f"Respaldo: {describir_respaldo(respaldo)} — «{respaldo['nota']}».")
            fila["estado"] = "confirmado"
            mensaje = f"Listo: «{nombre}» quedó resuelta."
        elif accion == "reabrir":
            if estado != "confirmado":
                raise ErrorDeValidacion("Esa actividad ya falta algo.")
            respaldo = normalizar_respaldo(datos, date.today(), quien)
            _anotar(fila, f"Falta: {respaldo['nota']}. Reabierta: {describir_respaldo(respaldo)}.")
            fila["estado"] = "pendiente"
            mensaje = f"Listo: «{nombre}» vuelve a faltar un dato."
        elif accion == "quitar":
            del nueva[indice]
            mensaje = f"Listo: quitaste «{nombre}». Queda en el historial con su motivo."
        else:       # corregir: un error de escritura; no cambia el estado ni pide respaldo
            for clave in CAMPOS_CORREGIBLES:
                if f"campo_{clave}" in datos:
                    fila[clave] = _limpio(datos[f"campo_{clave}"])      # type: ignore[literal-required]
            mensaje = f"Listo: corregiste «{nombre}»."
    resultado = save_activities(thread_id, nueva, "", base, None, datos, exigir_respaldo=True, quien=quien)
    return resultado if resultado.startswith("No hay cambios") else mensaje
