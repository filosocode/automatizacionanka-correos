"""La barra lateral, el inicio guiado y el «siguiente caso» (U-112, parte A).

Solo arma datos para las plantillas: qué casos esperan a la persona, con qué punto y con qué frase. Nada aquí lee ni
escribe fuera de lo que recibe.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any
from urllib.parse import quote as url_quote

from anka.dominio.activities import normalize_estado
from anka.web.paginas import accion_de_caso, case_stage, cola_de_hoy, grupo_de_etapa

_PUNTO = {"atender": "ambar", "esperando": "gris", "listo": "verde"}
_PUNTO_DICE = {"ambar": "te espera", "gris": "espera al cliente", "verde": "lista para la T", "": ""}
MAX_EN_BARRA = 60       # la barra no crece sin límite: el resto se ve en «Inicio»


def _nombre(caso: Mapping[str, Any]) -> str:
    return str(caso["analysis"].get("project_name") or "Solicitud sin nombre")


def _enlace(caso: Mapping[str, Any]) -> str:
    return f"/case?thread_id={url_quote(caso['thread_id'])}"


def frase_de_caso(caso: Mapping[str, Any]) -> str:
    """Una frase corta y sin jerga de lo que pasa en el caso: «Le faltan 2 datos» o la etapa."""
    faltan = sum(1 for a in caso["analysis"].get("activities") or []
                 if isinstance(a, Mapping) and normalize_estado(a.get("estado")) != "confirmado")
    etapa, urgente = case_stage(caso)
    if urgente and faltan and etapa in ("Por decidir", "Correo por revisar"):
        return f"Le falta{'' if faltan == 1 else 'n'} {faltan} dato{'' if faltan == 1 else 's'}"
    return etapa


def barra_de_casos(casos: Sequence[Mapping[str, Any]], actual: str = "") -> dict[str, Any]:
    """Los casos de la barra (primero los que esperan a la persona), el actual marcado y el siguiente por atender."""
    urgentes = cola_de_hoy(casos)
    ids = {c["thread_id"] for c in urgentes}
    resto = sorted((c for c in casos if c["thread_id"] not in ids), key=lambda c: _nombre(c).casefold())
    items = []
    for caso in [*urgentes, *resto][:MAX_EN_BARRA]:
        punto = _PUNTO.get(grupo_de_etapa(caso), "")
        items.append({"nombre": _nombre(caso), "cliente": str(caso.get("client_name") or ""), "punto": punto,
                      "punto_dice": _PUNTO_DICE[punto], "href": _enlace(caso), "actual": caso["thread_id"] == actual})
    siguiente = next((c for c in urgentes if c["thread_id"] != actual), None) if actual else None
    ir_a = ({"cliente": str(siguiente.get("client_name") or _nombre(siguiente)),
             "href": _enlace(siguiente) + accion_de_caso(siguiente)[2]} if siguiente else None)
    return {"items": items, "total": len(casos), "mas": max(0, len(casos) - MAX_EN_BARRA), "siguiente": ir_a}


def inicio_guiado(casos: Sequence[Mapping[str, Any]], nombre: str) -> dict[str, Any]:
    """Lo que dice Inicio: el saludo, cuántos casos esperan, el botón al más urgente y las dos listas de tarjetas."""
    urgentes = cola_de_hoy(casos)

    def tarjeta(caso: Mapping[str, Any]) -> dict[str, str]:
        return {"nombre": _nombre(caso), "cliente": str(caso.get("client_name") or ""), "frase": frase_de_caso(caso),
                "href": _enlace(caso) + (accion_de_caso(caso)[2] if case_stage(caso)[1] else "")}

    esperando = [c for c in casos if grupo_de_etapa(c) == "esperando"]
    primero = urgentes[0] if urgentes else None
    return {
        "nombre": nombre, "cuantos": len(urgentes),
        "boton": ({"cliente": str(primero.get("client_name") or _nombre(primero)), "href": tarjeta(primero)["href"]}
                  if primero else None),
        "te_esperan": [tarjeta(c) for c in urgentes[:6]], "mas_te_esperan": max(0, len(urgentes) - 6),
        "esperando": [tarjeta(c) for c in esperando[:6]], "mas_esperando": max(0, len(esperando) - 6),
    }
