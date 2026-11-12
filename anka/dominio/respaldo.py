"""El respaldo de un cambio de estado en la tabla de actividades (U-112, parte C).

Pasar una actividad a «confirmado», reabrirla, agregarla o quitarla exige dejar dicho de dónde sale: lo que el cliente
dijo (medio y día), lo que dice un documento o el criterio técnico de Anka. Así el cliente no puede decir después
«yo no pedí eso» y la persona no tiene que defender un cambio de memoria. Corregir un error de escritura no es un
cambio de estado y no lo pide.

Todo es puro: recibe datos y devuelve datos, sin base ni reloj propio (el día de hoy llega de fuera).
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import date
from typing import TypedDict

from anka.dominio.analisis import Actividad
from anka.dominio.errores import ErrorDeValidacion

ORIGENES = {"cliente": "El cliente", "documento": "Un documento", "tecnico": "Criterio técnico de Anka"}
MEDIOS = ("Llamada", "WhatsApp", "Correo", "Reunión")
NOTA_MINIMA = 10                  # letras: una frase corta basta, pero no un «ok»
MESES = ("ene", "feb", "mar", "abr", "may", "jun", "jul", "ago", "sep", "oct", "nov", "dic")


class Respaldo(TypedDict, total=False):
    """De dónde sale un cambio: quién lo respalda y con qué palabras."""

    origen: str       # cliente | documento | tecnico
    medio: str        # solo con origen «cliente»
    fecha: str        # AAAA-MM-DD, solo con origen «cliente»
    documento: str    # solo con origen «documento»
    nota: str         # lo que dijo, lo que dice el documento o el fundamento técnico
    quien: str        # la persona que lo registró


class CambiosDeEstado(TypedDict):
    """Lo que una edición hizo con el estado de las actividades (por nombre, sin mayúsculas)."""

    confirmadas: list[str]
    reabiertas: list[str]
    agregadas: list[str]
    quitadas: list[str]


def _clave(fila: Actividad) -> str:
    return str(fila.get("activity", "")).strip().casefold()


def _nombre(fila: Actividad) -> str:
    return str(fila.get("activity", "")).strip()


def cambios_de_estado(antes: Sequence[Actividad], despues: Sequence[Actividad]) -> CambiosDeEstado:
    """Qué actividades pasaron a «confirmado», volvieron a abrirse, se agregaron o se quitaron."""
    previas = {_clave(f): f for f in antes if isinstance(f, dict)}
    nuevas = {_clave(f): f for f in despues if isinstance(f, dict)}
    cambios: CambiosDeEstado = {"confirmadas": [], "reabiertas": [], "agregadas": [], "quitadas": []}
    for clave, fila in nuevas.items():
        if clave not in previas:
            cambios["agregadas"].append(_nombre(fila))
            continue
        antes_ok = previas[clave].get("estado") == "confirmado"
        ahora_ok = fila.get("estado") == "confirmado"
        if ahora_ok and not antes_ok:
            cambios["confirmadas"].append(_nombre(fila))
        elif antes_ok and not ahora_ok:
            cambios["reabiertas"].append(_nombre(fila))
    cambios["quitadas"] = [_nombre(f) for c, f in previas.items() if c not in nuevas]
    return cambios


def exige_respaldo(cambios: CambiosDeEstado) -> bool:
    """Hay algo que respaldar. Pasar entre «pendiente» y «contradictorio» o corregir un dato no lo exige."""
    return any(cambios.values())


def normalizar_respaldo(datos: Mapping[str, str] | None, hoy: date, quien: str = "") -> Respaldo:
    """Valida lo que la persona escribió y lo deja en su forma guardada; si falta algo dice qué en una frase."""
    datos = datos or {}
    origen = str(datos.get("origen") or "").strip().casefold()
    if origen not in ORIGENES:
        raise ErrorDeValidacion("Elige de dónde viene: el cliente, un documento o tu criterio técnico.")
    respaldo: Respaldo = {"origen": origen}
    if origen == "cliente":
        medio = next((m for m in MEDIOS if m.casefold() == str(datos.get("medio") or "").strip().casefold()), "")
        if not medio:
            raise ErrorDeValidacion("Elige por qué medio te lo dijo el cliente.")
        try:
            dia = date.fromisoformat(str(datos.get("fecha") or "").strip())
        except ValueError:
            raise ErrorDeValidacion("Escribe el día en que te lo dijo el cliente.") from None
        if dia > hoy:
            raise ErrorDeValidacion("El día en que te lo dijo no puede ser futuro.")
        respaldo["medio"] = medio
        respaldo["fecha"] = dia.isoformat()
    if origen == "documento":
        documento = str(datos.get("documento") or "").strip()
        if not documento:
            raise ErrorDeValidacion("Elige en cuál documento está.")
        respaldo["documento"] = documento
    nota = " ".join(str(datos.get("nota") or "").split())
    if len(nota) < NOTA_MINIMA:
        raise ErrorDeValidacion("Cuéntalo en una frase (mínimo 10 letras), para que quede claro de dónde sale.")
    respaldo["nota"] = nota
    if quien.strip():
        respaldo["quien"] = quien.strip()
    return respaldo


def _dia_corto(iso: str) -> str:
    dia = date.fromisoformat(iso)
    return f"{dia.day} {MESES[dia.month - 1]}"


def describir_respaldo(respaldo: Mapping[str, object]) -> str:
    """La línea que se ve bajo la actividad y en el historial."""
    origen = respaldo.get("origen", "")
    if origen == "cliente":
        medio = str(respaldo.get("medio", "")).lower()
        return f"Lo confirmó el cliente por {medio}, el {_dia_corto(str(respaldo['fecha']))}"
    if origen == "documento":
        return f"Según {respaldo.get('documento') or 'un documento'}"
    quien = respaldo.get("quien") or "Anka"
    return f"Por la experiencia de {quien} · se lo presentamos como recomendación"
