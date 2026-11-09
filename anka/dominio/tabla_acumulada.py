"""Tabla acumulada de actividades: cómo una tabla nueva (la de un seguimiento) se integra en la vigente (T-59, Fase B).

Lógica pura y determinista, sin base de datos ni Gmail. Un seguimiento no es un reemplazo: que no repita una actividad
no la elimina, y lo que contradice o sustituye algo ya informado no se aplica solo; queda como propuesta para revisar.

Reglas, en orden:
1. Dos filas son la misma actividad si su nombre coincide sin tildes, mayúsculas ni puntuación, o si, sin pareja por
   nombre, se parecen muchísimo (≥ 70 % de palabras en común, o un nombre contenido en el otro con la misma cantidad)
   y es la mejor pareja posible (U-92). Nada más se fusiona.
2. Una fila anterior que la nueva no menciona se conserva tal cual.
3. Una fila nueva sin pareja ni parecido se agrega.
4. De una pareja, la nueva **llena** un dato vacío o **reemplaza** uno que no estaba confirmado (si la fila nueva no es
   dudosa): eso se aplica y suma la fuente. Un dato ya informado y confirmado que cambia, una contradicción, una
   eliminación pedida en el texto, un posible duplicado o una sustitución ambigua **no** se aplican: la tabla vigente
   queda igual y la propuesta lleva el cambio con su motivo.
"""

from __future__ import annotations

import copy
import re
import unicodedata
from collections.abc import Mapping
from typing import Any, NamedTuple

from anka.dominio.activities import ACTIVITY_COLUMNS
from anka.dominio.reloj import instante


CAMPOS_DE_DATO = ("quantity", "unit", "ubicacion", "entregable", "plazo_restriccion", "servicio_tecnica")
_SIN_DATO = frozenset({"", "no indicado", "no indica", "sin definir", "por definir", "pendiente", "n/a", "ninguno"})
_ELIMINACION = re.compile(
    r"\bya no se requiere\b|\bno se requiere\b|\bse elimina\b|\beliminar\b|\bcancel(?:a|ar|ada|ado|an)\b|"
    r"\bse descarta\b|\bdescartar\b|\bexcluir\b|\bse excluye\b|\bsin este (?:servicio|alcance)\b")
_PARECIDO_MINIMO = 0.5
_MISMA_ACTIVIDAD = 0.7      # U-92: un nombre reformulado por el modelo con tanto en común es la misma actividad
_VENTAJA_MINIMA = 0.1      # U-92: para juntar por descarte, la pareja debe superar en esto a cualquier otra candidata
_MISMO_DATO = 0.6           # U-92: un texto reformulado (mismos números, casi las mismas palabras) no es otro dato
_CAMPOS_DE_TEXTO = ("ubicacion", "entregable", "plazo_restriccion", "servicio_tecnica")

Fila = dict[str, Any]       # una fila de la tabla (`Actividad`), sin exigir sus claves


class Integracion(NamedTuple):
    """El resultado de integrar: lo que se aplica ya, lo que se propone y por qué hay que revisar."""

    vigente: list[Any]          # la tabla que queda: la anterior con lo seguro aplicado
    propuesta: list[Any]        # la tabla con TODO aplicado (igual a `vigente` si no hay nada que revisar)
    revisar: list[dict[str, str]]
    aplicado: list[str]         # frases de lo que se aplicó solo (para el historial)


def _plano(texto: object) -> str:
    base = unicodedata.normalize("NFKD", str(texto if texto is not None else "").casefold())
    sin_tildes = "".join(c for c in base if not unicodedata.combining(c))
    return " ".join(re.sub(r"[^\w\s]", " ", sin_tildes).split())


def _vacio(valor: object) -> bool:
    return _plano(valor) in _SIN_DATO


def _clave(fila: Fila) -> str:
    return _plano(fila.get("activity", ""))


def _palabras(fila: Fila) -> set[str]:
    return {p for p in _clave(fila).split() if len(p) > 2}


def _parecidas(a: Fila, b: Fila) -> bool:
    """¿Podrían ser la misma actividad con otro nombre? Solo sirve para NO fusionar y pedir revisión."""
    servicio_a, servicio_b = _plano(a.get("servicio_tecnica")), _plano(b.get("servicio_tecnica"))
    mismo_servicio = bool(servicio_a) and servicio_a == servicio_b
    sitio_a, sitio_b = _plano(a.get("ubicacion")), _plano(b.get("ubicacion"))
    if mismo_servicio and (not sitio_a or not sitio_b or sitio_a == sitio_b):
        return True
    palabras_a, palabras_b = _palabras(a), _palabras(b)
    union = palabras_a | palabras_b
    return bool(union) and len(palabras_a & palabras_b) / len(union) >= _PARECIDO_MINIMO


def _jaccard(a: set[str], b: set[str]) -> float:
    union = a | b
    return len(a & b) / len(union) if union else 0.0


def _numeros(texto: object) -> set[str]:
    return set(re.findall(r"\d+(?:[.,]\d+)?", _plano(texto)))


def _palabras_de(texto: object) -> set[str]:
    return {p for p in _plano(texto).split() if len(p) > 2}


def _siglas(texto: object) -> set[str]:
    """Las siglas y formatos del texto (DWG, PDF, KMZ, IDU…): cambiar una es cambiar el dato, no reescribirlo."""
    return set(re.findall(r"(?<![A-Za-zÁÉÍÓÚÑáéíóúñ])[A-ZÁÉÍÓÚÑ]{2,6}(?![A-Za-zÁÉÍÓÚÑáéíóúñ])", str(texto or "")))


def _reformulado(antes: object, ahora: object) -> bool:
    """¿`ahora` dice lo mismo que `antes` con otras palabras? Mismos números y palabras en común (U-92)."""
    if _numeros(antes) != _numeros(ahora) or _siglas(antes) != _siglas(ahora):
        return False
    previas, nuevas = _palabras_de(antes), _palabras_de(ahora)
    if previas < nuevas:
        return False                    # lo nuevo solo agrega: es un dato más, no una reformulación
    return nuevas <= previas or _jaccard(previas, nuevas) >= _MISMO_DATO   # solo acorta, o casi las mismas palabras


def _detalle_del_cambio(antes: Fila, despues: Fila) -> str:
    """Qué campos cambiaron en una fila, con el valor de antes y el de ahora, para el historial (U-96)."""
    def corto(valor: object) -> str:
        texto = " ".join(str(valor or "").split())
        return texto if len(texto) <= 160 else texto[:157] + "..."
    campos = [c for c in (*CAMPOS_DE_DATO, "estado", "notes") if _plano(antes.get(c)) != _plano(despues.get(c))]
    etiqueta = dict(ACTIVITY_COLUMNS)
    return "; ".join(f"{etiqueta.get(c, c)}: «{corto(antes.get(c))}» → «{corto(despues.get(c))}»" for c in campos)


def _pide_eliminar(fila: Fila) -> bool:
    return bool(_ELIMINACION.search(_plano(fila.get("notes")) + " " + _plano(fila.get("estado"))))


def _fuente_sumada(anterior: str, nueva: str) -> str:
    if not nueva.strip() or _plano(nueva) in _plano(anterior):
        return anterior
    return f"{anterior}; {nueva}".strip("; ") if anterior.strip() else nueva


def _pareja(anterior: Fila, nueva: Fila, revisar: list[dict[str, str]],
            protegidos: frozenset[tuple[str, str]] = frozenset()) -> tuple[Fila, Fila, bool]:
    """(fila vigente, fila propuesta, ¿cambió algo seguro?) para una actividad que está en las dos tablas.

    Un dato con una propuesta pendiente de revisión humana (`protegidos`) nunca se reemplaza solo."""
    nombre = str(anterior.get("activity", ""))
    fuente = str(nueva.get("fuente") or "")
    confirma = _plano(nueva.get("estado")) == "confirmado" and _plano(anterior.get("estado")) != "confirmado"
    # un dato ya informado solo lo cambia una fila nueva no dudosa sobre una anterior que aún no estaba confirmada
    reemplaza = _plano(nueva.get("estado")) in ("confirmado", "") and _plano(anterior.get("estado")) != "confirmado"
    seguros: dict[str, str] = {}
    conflictos: dict[str, str] = {}
    for campo in CAMPOS_DE_DATO:
        antes, ahora = anterior.get(campo), nueva.get(campo)
        if _vacio(ahora) or _plano(antes) == _plano(ahora):
            continue
        if campo in _CAMPOS_DE_TEXTO and not _vacio(antes) and _reformulado(antes, ahora):
            continue                                  # U-92: el modelo lo escribió de otra forma; el dato es el mismo
        if (campo in _CAMPOS_DE_TEXTO and _plano(anterior.get("estado")) != "confirmado"
                and (_clave(anterior), campo) not in protegidos):
            seguros[campo] = str(ahora)               # U-96: en una fila sin confirmar, el texto nuevo se aplica solo
            continue
        if _vacio(antes) or (reemplaza and (_clave(anterior), campo) not in protegidos):
            seguros[campo] = str(ahora)
        else:
            conflictos[campo] = str(ahora)
    vigente = copy.deepcopy(anterior)
    vigente.update(seguros)
    propuesta = copy.deepcopy(vigente)
    propuesta.update(conflictos)
    notas = str(nueva.get("notes") or "").strip()
    if notas and notas != str(anterior.get("notes") or "").strip():
        sin_pendientes = not conflictos and (_clave(anterior), "notes") not in protegidos
        if _plano(anterior.get("estado")) != "confirmado" and sin_pendientes:
            vigente["notes"] = propuesta["notes"] = notas      # U-92: en una fila pendiente la nota es el comentario
        else:
            propuesta["notes"] = notas
            conflictos["notes"] = notas
    for campo, valor in conflictos.items():
        if campo == "notes":
            continue
        revisar.append({"tipo": "dato_distinto", "actividad": nombre, "campo": campo,
                        "anterior": str(anterior.get(campo) or ""), "propuesto": valor, "fuente": fuente})
    if _plano(nueva.get("estado")) == "contradictorio":
        vigente["estado"] = propuesta["estado"] = "contradictorio"
        revisar.append({"tipo": "contradiccion", "actividad": nombre, "campo": "estado", "anterior": "",
                        "propuesto": "contradictorio", "fuente": fuente})
    elif not conflictos and confirma and _plano(anterior.get("estado")) == "pendiente":
        vigente["estado"] = propuesta["estado"] = "confirmado"
    if "notes" in conflictos:
        revisar.append({"tipo": "dato_distinto", "actividad": nombre, "campo": "notes",
                        "anterior": str(anterior.get("notes") or ""), "propuesto": notas, "fuente": fuente})
    cambio = vigente != anterior
    if cambio and fuente:
        vigente["fuente"] = propuesta["fuente"] = _fuente_sumada(str(anterior.get("fuente") or ""), fuente)
    elif conflictos and fuente:
        propuesta["fuente"] = _fuente_sumada(str(anterior.get("fuente") or ""), fuente)
    return vigente, propuesta, cambio


def _mismo_alcance(a: Fila, b: Fila) -> bool:
    """¿Las dos filas dicen la misma cantidad, o alguna no dice ninguna? (U-92)"""
    cantidad_a, cantidad_b = a.get("quantity"), b.get("quantity")
    return _vacio(cantidad_a) or _vacio(cantidad_b) or _numeros(cantidad_a) == _numeros(cantidad_b)


def _puntaje_de_nombres(anterior: Fila, nueva: Fila) -> float:
    """Qué tanto se parecen los nombres de dos filas.

    Un nombre corto contenido en otro largo con la misma cantidad («Control Horizontal» y «Control horizontal GNSS»)
    cuenta como la misma actividad (U-92)."""
    a, b = _palabras(anterior), _palabras(nueva)
    puntaje = _jaccard(a, b)
    corto, largo = (a, b) if len(a) <= len(b) else (b, a)
    if len(corto) >= 2 and corto <= largo and _mismo_alcance(anterior, nueva):
        return max(puntaje, _MISMA_ACTIVIDAD)
    return puntaje


def anclar_filas(nuevas: Any, vigente: list[dict] | None) -> int:
    """Devuelve a cada fila nueva el nombre de la fila vigente que el modelo dijo actualizar (`fila_existente`) (U-94).

    La fila vigente se identifica por su posición (1, 2, 3…), que no cambia aunque el modelo reescriba los nombres.
    Un número inválido, repetido o sin fila vigente se ignora: esa fila sigue el camino de siempre. El campo
    `fila_existente` se quita de todas las filas porque no forma parte de la tabla. Devuelve cuántas se ancló.
    """
    if not isinstance(nuevas, list):
        return 0
    usadas: set[int] = set()
    ancladas = 0
    for fila in nuevas:
        if not isinstance(fila, dict):
            continue
        numero = fila.pop("fila_existente", 0)
        if isinstance(numero, bool) or not isinstance(numero, int) or numero in usadas:
            continue
        if not vigente or not 1 <= numero <= len(vigente) or not isinstance(vigente[numero - 1], dict):
            continue
        nombre = str(vigente[numero - 1].get("activity") or "")
        if nombre:
            usadas.add(numero)
            fila["activity"] = nombre
            ancladas += 1
    return ancladas


def _parejas_por_parecido(previas: list[tuple[int, Fila]], candidatas: list[Fila], emparejadas: dict[int, int],
                          sin_pareja: list[int]) -> list[tuple[int, int]]:
    """Parejas (índice anterior, índice nuevo) de nombres reformulados: las más parecidas primero (U-92)."""
    posibles = sorted(
        ((_puntaje_de_nombres(f, candidatas[n]), i, n)
         for i, f in previas if i not in emparejadas for n in sin_pareja
         if _clave(candidatas[n]) and not _pide_eliminar(candidatas[n])),
        key=lambda t: (-t[0], t[1], t[2]))
    usadas_i: set[int] = set()
    usadas_n: set[int] = set()
    parejas: list[tuple[int, int]] = []
    for puntaje, i, n in posibles:
        if puntaje < _MISMA_ACTIVIDAD:
            break
        if i not in usadas_i and n not in usadas_n:
            usadas_i.add(i)
            usadas_n.add(n)
            parejas.append((i, n))
    # por descarte: lo que sobra se junta si cada una es la mejor candidata de la otra y con ventaja clara
    quedan = [(p, i, n) for p, i, n in posibles if i not in usadas_i and n not in usadas_n and p >= _PARECIDO_MINIMO]
    for puntaje, i, n in quedan:
        if i in usadas_i or n in usadas_n:
            continue
        rivales = [p for p, a, b in quedan if (a == i) != (b == n)]
        if all(puntaje - otro >= _VENTAJA_MINIMA for otro in rivales):
            usadas_i.add(i)
            usadas_n.add(n)
            parejas.append((i, n))
    return parejas


def integrar(anterior: list[Any], nueva: list[Any],
             protegidos: frozenset[tuple[str, str]] = frozenset()) -> Integracion:
    """Integra la tabla `nueva` (de un seguimiento) en la `anterior` sin perder nada que nadie quitó a propósito."""
    previas = [(i, f) for i, f in enumerate(anterior) if isinstance(f, dict)]
    candidatas = [f for f in nueva if isinstance(f, dict)]
    revisar: list[dict[str, str]] = []
    aplicado: list[str] = []
    vigente = copy.deepcopy(anterior)
    propuesta = copy.deepcopy(anterior)
    emparejadas: dict[int, int] = {}               # índice anterior -> índice de la lista nueva
    sin_pareja: list[int] = []
    for n, fila in enumerate(candidatas):
        libre = next((i for i, f in previas if i not in emparejadas and _clave(f) == _clave(fila) and _clave(f)), None)
        if libre is not None:
            emparejadas[libre] = n
        else:
            sin_pareja.append(n)               # sin pareja por nombre, o el mismo nombre repetido
    for i, n in _parejas_por_parecido(previas, candidatas, emparejadas, sin_pareja):
        emparejadas[i] = n
        sin_pareja.remove(n)
    quitar: list[int] = []
    for i, n in emparejadas.items():
        fila = candidatas[n]
        if _pide_eliminar(fila):
            revisar.append({"tipo": "eliminacion_pedida", "actividad": str(anterior[i].get("activity", "")),
                            "campo": "", "anterior": "", "propuesto": "", "fuente": str(fila.get("fuente") or "")})
            quitar.append(i)
            continue
        nuevo_vigente, nuevo_propuesto, cambio = _pareja(anterior[i], fila, revisar, protegidos)
        vigente[i], propuesta[i] = nuevo_vigente, nuevo_propuesto
        if cambio:
            detalle = _detalle_del_cambio(anterior[i], nuevo_vigente)
            aplicado.append(f"se actualizó «{anterior[i].get('activity', '')}» ({detalle})")
    vistas = {_clave(f) for _, f in previas}
    for n in sin_pareja:
        fila = candidatas[n]
        nombre = str(fila.get("activity", ""))
        if not _clave(fila):
            revisar.append({"tipo": "sin_nombre", "actividad": nombre, "campo": "", "anterior": "", "propuesto": "",
                            "fuente": str(fila.get("fuente") or "")})
            propuesta.append(copy.deepcopy(fila))
            continue
        if _pide_eliminar(fila):
            revisar.append({"tipo": "eliminacion_sin_coincidencia", "actividad": nombre, "campo": "", "anterior": "",
                            "propuesto": "", "fuente": str(fila.get("fuente") or "")})
            continue
        if _clave(fila) in vistas:
            igual = next((f for _, f in previas if _clave(f) == _clave(fila)), None)
            if igual is not None and all(_plano(igual.get(c)) == _plano(fila.get(c)) for c in CAMPOS_DE_DATO):
                continue                                       # repetida tal cual: no es nada nuevo
            revisar.append({"tipo": "duplicada", "actividad": nombre, "campo": "", "anterior": "", "propuesto": "",
                            "fuente": str(fila.get("fuente") or "")})
            propuesta.append(copy.deepcopy(fila))
            continue
        parecida = next((f for i, f in previas if i not in emparejadas and _parecidas(f, fila)), None)
        if parecida is not None:
            revisar.append({"tipo": "sustitucion_ambigua", "actividad": nombre, "campo": "",
                            "anterior": str(parecida.get("activity", "")), "propuesto": nombre,
                            "fuente": str(fila.get("fuente") or "")})
            propuesta.append(copy.deepcopy(fila))
            continue
        vigente.append(copy.deepcopy(fila))
        propuesta.append(copy.deepcopy(fila))
        aplicado.append(f"se agregó «{nombre}»")
        vistas.add(_clave(fila))
    for i in sorted(quitar, reverse=True):
        del propuesta[i]
    if not revisar:
        propuesta = copy.deepcopy(vigente)
    return Integracion(vigente, propuesta, revisar, aplicado)


# --- Una propuesta pendiente sobrevive a los análisis posteriores (T-60) ----------------------------------------------
#
# Mientras el caso tenga `pending_model_activities`, la propuesta sigue sin resolver: quien la adopta o la deja de lado
# la quita de ahí (`resolve_model_proposal`). Que un seguimiento no la repita no es rechazarla.

_CAMBIA_FILAS = ("duplicada", "sustitucion_ambigua", "sin_nombre")


def _emparejar(base: list[Fila], otra: list[Fila]) -> list[tuple[Fila, int | None]]:
    """Cada fila de `base` con el índice de su pareja en `otra` (mismo nombre, una a una, en orden)."""
    usadas: set[int] = set()
    resultado: list[tuple[Fila, int | None]] = []
    for fila in base:
        indice = next((i for i, f in enumerate(otra) if i not in usadas and _clave(f) == _clave(fila) and _clave(f)),
                      None)
        if indice is not None:
            usadas.add(indice)
        resultado.append((fila, indice))
    return resultado


def _pendiente_de(anterior: Mapping[str, Any]) -> tuple[list[tuple[str, str, Fila, Any]], list[Fila], list[Fila]]:
    """Lo que la propuesta pendiente de un caso cambia respecto de su tabla: (datos, filas de más, filas que quita)."""
    pendiente = anterior.get("pending_model_activities")
    if not (isinstance(pendiente, list) and pendiente):
        return [], [], []
    base = [f for f in anterior.get("activities") or [] if isinstance(f, dict)]
    propuesta = [f for f in pendiente if isinstance(f, dict)]
    parejas = _emparejar(base, propuesta)
    datos = [(_clave(fila), campo, propuesta[i], propuesta[i].get(campo))
             for fila, i in parejas if i is not None for campo in (*CAMPOS_DE_DATO, "notes")
             if not _vacio(propuesta[i].get(campo)) and _plano(propuesta[i].get(campo)) != _plano(fila.get(campo))]
    emparejadas = {i for _, i in parejas if i is not None}
    sobrantes = [f for i, f in enumerate(propuesta) if i not in emparejadas]
    quitadas = [fila for fila, i in parejas if i is None]
    return datos, sobrantes, quitadas


def campos_protegidos(anterior: Mapping[str, Any]) -> frozenset[tuple[str, str]]:
    """Los datos `(actividad, campo)` con un cambio propuesto sin resolver: un seguimiento no los reemplaza solo."""
    return frozenset((clave, campo) for clave, campo, _, _ in _pendiente_de(anterior)[0])


def _clave_de_entrada(entrada: Mapping[str, Any]) -> tuple[str, str, str, str]:
    return (str(entrada.get("tipo")), _plano(entrada.get("actividad")), str(entrada.get("campo")),
            _plano(entrada.get("propuesto")))


def _mismos_datos(a: Fila, b: Fila) -> bool:
    return all(_plano(a.get(campo)) == _plano(b.get(campo)) for campo in CAMPOS_DE_DATO)


class _Suma:
    """La tabla propuesta y los motivos de revisión mientras se les suma lo que el caso ya tenía propuesto."""

    def __init__(self, res: Integracion, viejas: list[dict[str, Any]]) -> None:
        self.vigente = res.vigente
        self.propuesta: list[Any] = copy.deepcopy(res.propuesta)
        self.revisar: list[dict[str, str]] = [dict(r) for r in res.revisar]
        self.viejas = viejas
        self._conocidas = {_clave_de_entrada(e) for e in self.revisar}

    def anotar(self, entrada: dict[str, str]) -> None:
        if _clave_de_entrada(entrada) not in self._conocidas:
            self._conocidas.add(_clave_de_entrada(entrada))
            self.revisar.append(entrada)

    def anotar_viejas(self, tipos: tuple[str, ...], actividad: str | None = None) -> None:
        for entrada in self.viejas:
            if entrada.get("tipo") in tipos and (actividad is None or _plano(entrada.get("actividad")) == actividad):
                self.anotar(dict(entrada))

    def fila(self, tabla: list[Any], clave: str) -> Fila | None:
        return next((f for f in tabla if isinstance(f, dict) and _clave(f) == clave), None)

    def datos(self, datos: list[tuple[str, str, Fila, Any]]) -> None:
        """Los cambios de un dato que seguían pendientes: se vuelven a proponer salvo que la vigente ya los adoptara."""
        for clave, campo, fila_vieja, valor in datos:
            vigente, nueva = self.fila(self.vigente, clave), self.fila(self.propuesta, clave)
            if vigente is None or nueva is None or _plano(vigente.get(campo)) == _plano(valor):
                continue                                                # la actividad ya no está, o ya se adoptó
            entrada = next((e for e in self.viejas if e.get("tipo") == "dato_distinto" and e.get("campo") == campo
                            and _plano(e.get("actividad")) == clave and _plano(e.get("propuesto")) == _plano(valor)),
                           None)
            self.anotar({**(entrada or {"tipo": "dato_distinto", "actividad": str(vigente.get("activity", "")),
                                        "campo": campo, "propuesto": str(valor),
                                        "fuente": str(fila_vieja.get("fuente") or "")}),
                         "anterior": str(vigente.get(campo) or "")})
            if _plano(nueva.get(campo)) == _plano(vigente.get(campo)):  # el análisis nuevo no propone otro valor
                nueva[campo] = valor
                nueva["fuente"] = str(fila_vieja.get("fuente") or nueva.get("fuente") or "")

    def contradicciones(self) -> None:
        for entrada in self.viejas:
            fila = self.fila(self.vigente, _plano(entrada.get("actividad")))
            if entrada.get("tipo") == "contradiccion" and fila is not None:
                if _plano(fila.get("estado")) == "contradictorio":
                    self.anotar(dict(entrada))
        self.anotar_viejas(("eliminacion_sin_coincidencia",))

    def filas(self, sobrantes: list[Fila], quitadas: list[Fila]) -> None:
        """Las filas de más que se proponían y las que se pedía quitar."""
        for fila in sobrantes:
            if not any(_clave(f) == _clave(fila) and _mismos_datos(f, fila)
                       for f in self.propuesta if isinstance(f, dict)):
                self.propuesta.append(copy.deepcopy(fila))
            self.anotar_viejas(_CAMBIA_FILAS, _clave(fila))
        for fila in quitadas:
            if self.fila(self.vigente, _clave(fila)) is None:
                continue
            indice = next((i for i, f in enumerate(self.propuesta)
                           if isinstance(f, dict) and _clave(f) == _clave(fila)), None)
            if indice is not None:
                del self.propuesta[indice]
            self.anotar_viejas(("eliminacion_pedida",), _clave(fila))


def conservar_propuesta(anterior: Mapping[str, Any], res: Integracion) -> Integracion:
    """Suma a lo que propone este seguimiento lo que el caso ya tenía propuesto y nadie resolvió.

    No aplica nada a la tabla vigente y no duplica una propuesta que vuelve a aparecer. Si el nuevo análisis propone
    otro valor para el mismo dato, la tabla propuesta lleva el nuevo y la propuesta anterior sigue a la vista en
    `revision_de_tabla`. Lo que la tabla vigente ya adoptó se da por resuelto."""
    datos, sobrantes, quitadas = _pendiente_de(anterior)
    if not (datos or sobrantes or quitadas):
        return res
    suma = _Suma(res, [e for e in anterior.get("revision_de_tabla") or [] if isinstance(e, dict)])
    suma.datos(datos)
    suma.contradicciones()
    suma.filas(sobrantes, quitadas)
    if suma.revisar == res.revisar and suma.propuesta == res.propuesta:
        return res
    return Integracion(res.vigente, suma.propuesta, suma.revisar, res.aplicado)


def es_seguimiento(primera_vez: str, llegada: str, id_anterior: str, id_nuevo: str) -> bool:
    """¿El análisis es de un mensaje posterior al primero del caso (un seguimiento) y no un reanálisis del original?"""
    desde, hasta = instante(primera_vez), instante(llegada)
    if desde and hasta:
        return hasta > desde
    return id_nuevo != id_anterior


def integrar_seguimiento(anterior: Mapping[str, Any], analysis: Any, primera_vez: str, id_anterior: str,
                         id_nuevo: str) -> Integracion | None:
    """Suma la tabla de un seguimiento a la del caso, dentro de `analysis`; `None` si no corresponde (no es un
    seguimiento o el caso aún no tenía tabla). Con la tabla de una persona (`manual_edit`) o bloqueada por R11 nada se
    aplica solo: todo queda como propuesta pendiente."""
    anteriores = anterior.get("activities")
    bloqueada = isinstance(analysis.get("pending_model_activities"), list)
    nuevas = analysis["pending_model_activities"] if bloqueada else analysis.get("activities")
    if not (isinstance(anteriores, list) and anteriores and isinstance(nuevas, list)
            and es_seguimiento(primera_vez, str(analysis.get("received_at") or ""), id_anterior, id_nuevo)):
        return None
    resultado = conservar_propuesta(anterior, integrar(anteriores, nuevas, campos_protegidos(anterior)))
    if "propuesta_descartada" in anterior and "propuesta_descartada" not in analysis:
        analysis["propuesta_descartada"] = anterior["propuesta_descartada"]     # para poder recuperarla (U-57)
    if bloqueada or anterior.get("manual_edit"):
        analysis["activities"] = anteriores
        if resultado.propuesta != anteriores:
            analysis["pending_model_activities"] = resultado.propuesta
        else:
            analysis.pop("pending_model_activities", None)
    else:
        analysis["activities"] = resultado.vigente
        if resultado.revisar:
            analysis["pending_model_activities"] = resultado.propuesta
    if resultado.revisar:
        analysis["revision_de_tabla"] = resultado.revisar
    return resultado


def describir(res: Integracion) -> str:
    """Una frase para el historial con lo que se aplicó solo y lo que queda por revisar."""
    partes = []
    if res.aplicado:
        partes.append("; ".join(res.aplicado))
    if res.revisar:
        partes.append(f"{len(res.revisar)} cambio(s) por revisar: " + "; ".join(
            f"{r['tipo'].replace('_', ' ')} en «{r['actividad']}»" for r in res.revisar[:3]))
    return "Seguimiento: " + ("; ".join(partes) if partes else "sin cambios en la tabla") + "."
