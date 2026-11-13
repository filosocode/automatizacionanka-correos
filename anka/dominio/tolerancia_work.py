"""Tolerar y avisar: lo que Work escribe distinto de lo esperado se corrige solo, con aviso (T-28, D-106).

El esquema (`spec/contrato_work.schema.json`) dice la forma ideal del análisis. Aquí se recorre el análisis con esa
misma forma y, donde Work se desvió en algo secundario, se repara sin inventar nada: lo que no se entiende se trata como
desconocido (se omite o queda vacío), una fila cuyo significado cambió queda «pendiente» (sin confirmar) y todo se
cuenta en un solo aviso que el caso muestra. Solo se rechaza lo que compromete la identidad del caso (`thread_id`,
`message_id`, `client_email`), un estado desconocido o un JSON irrecuperable.

Capa de dominio: funciones puras, sin Gmail ni base de datos.
"""

from __future__ import annotations

import json
import re
import unicodedata
from collections.abc import Sequence
from typing import Any

from anka.dominio.clasificacion import CLASIFICACIONES, REVISION_MANUAL
from anka.dominio.status import reconocer_estado_de_work
from anka.dominio.work_contract import (
    corregir_descuidos_de_tipo, load_schema, tipo_cumple, validate, version_del_contrato,
)

PREFIJO_DEL_AVISO = "Work escribió algo distinto de lo esperado; se corrigió solo: "
SIN_NOMBRE = "Solicitud sin nombre"
SIN_FUENTE = "sin fuente"
MAX_DETALLES = 8          # grupos de correcciones que el aviso cuenta; el resto se resume
MAX_RUTAS_POR_GRUPO = 3
TEXTOS_DESCONOCIDOS = frozenset({
    "no_indicado", "no_indicada", "no_aplica", "n/a", "na", "desconocido", "desconocida", "sin_dato", "sin_datos",
    "null", "none", "nulo", "ninguno", "ninguna", "-", "",
})
IDENTIDAD = ("thread_id", "message_id", "client_email")     # sin ellos no se sabe de qué correo habla el análisis
# Los dos campos que se arreglan antes, a mano, en `reparar`: el recorrido general no los toca.
_PROPIOS = frozenset({"thread_id", "message_id", "client_email", "status", "engine", "project_name"})

# Valores permitidos que Work escribe en español; la clave está en la forma simple (sin tildes ni mayúsculas ni
# puntuación, con «_» entre palabras). Es el mapa explícito del contrato: lo que no está aquí ni coincide con el
# valor permitido se trata como desconocido, nunca se adivina.
SINONIMOS_DE_ENUMERADOS: dict[str, dict[str, str]] = {
    "evidence_register[].status": {
        "explicito": "explicit", "explicita": "explicit", "parcial": "partial", "ausente": "absent",
        "ilegible": "unreadable", "no_legible": "unreadable", "contradictorio": "contradictory",
        "contradictoria": "contradictory", "sin_confirmar": "unconfirmed", "no_confirmado": "unconfirmed",
        "no_confirmada": "unconfirmed",
    },
    "evidence_register[].owner": {
        "cliente": "client", "contratista": "contractor", "desconocido": "unknown", "desconocida": "unknown",
    },
    "question_audit[].disposition": {
        "preguntar": "ask_client", "preguntar_al_cliente": "ask_client", "revision_interna": "internal_review",
        "revisar_internamente": "internal_review", "no_preguntar": "do_not_ask",
    },
    "attachments[].read_via": {"adjunto": "attachment", "raw": "raw_mime", "mime_raw": "raw_mime"},
}
# Lo que falta de una fila secundaria y se puede dejar explícitamente vacío o «sin confirmar»; si falta algo que no
# está aquí, la fila se omite (con aviso): rellenarla sería inventar.
RELLENOS: dict[str, object] = {
    "fields[].value": "", "fields[].source": SIN_FUENTE,
    "evidence_register[].status": "unconfirmed", "evidence_register[].owner": "unknown",
    "evidence_register[].blocking": False, "evidence_register[].value": "", "evidence_register[].source": "",
    "evidence_register[].location": "",
    "question_audit[].reason_missing": "", "question_audit[].reviewed_sources": [],
    "attachments[].evidence[].value": "", "attachments[].evidence[].location": "",
    "attachments[].evidence[].excerpt": "",
}
_VERDADERO = frozenset({"true", "verdadero", "si", "yes", "1"})
_FALSO = frozenset({"false", "falso", "no", "0"})
_ESPERADO = {"string": "texto", "integer": "un número entero", "number": "un número", "boolean": "verdadero o falso",
             "array": "una lista", "object": "un objeto"}
_OMITIR = object()
_MAX_VALOR_EN_AVISO = 40


class Rechazo(ValueError):
    """Un análisis que no se puede usar. Guarda solo el motivo en lenguaje claro, la causa y los nombres de campo.

    Nunca lleva el contenido del cliente ni el JSON (T-36): `motivo` sale de frases fijas y de nombres de campo.
    """

    def __init__(self, motivo: str, causa: str, campos: Sequence[str] = ()) -> None:
        super().__init__(motivo)
        self.motivo, self.causa, self.campos = motivo, causa, tuple(campos)


def forma_simple(valor: object) -> str:
    """El texto sin tildes, en minúsculas, sin puntuación en los extremos y con «_» entre palabras."""
    texto = unicodedata.normalize("NFKD", str(valor if valor is not None else "").casefold())
    texto = "".join(c for c in texto if not unicodedata.combining(c) and unicodedata.category(c) != "Cf")
    texto = texto.strip(" \t\r\n.,;:!?¡¿«»\"'`()[]")
    return re.sub(r"[\s_\-]+", "_", texto)


def _describir_tipo(valor: object) -> str:
    if isinstance(valor, bool):
        return "verdadero o falso"
    if isinstance(valor, (int, float)):
        return "un número"
    if isinstance(valor, str):
        return "un texto"
    if isinstance(valor, list):
        return "una lista"
    return "un objeto" if isinstance(valor, dict) else "otra cosa"


def _generica(ruta: str) -> str:
    return re.sub(r"\[\d+\]", "[]", ruta)


class _Correcciones:
    """Las correcciones de una pasada, agrupadas para contarlas en un solo aviso."""

    def __init__(self) -> None:
        self.grupos: dict[tuple[str, str], list[str]] = {}
        self.total = 0

    def anotar(self, ruta: str, que: str) -> None:
        self.grupos.setdefault((_generica(ruta), que), []).append(ruta)
        self.total += 1

    def aviso(self) -> str:
        if not self.grupos:
            return ""
        detalles = []
        for (_, que), rutas in self.grupos.items():
            rutas = list(dict.fromkeys(rutas))
            nombres = ", ".join(rutas[:MAX_RUTAS_POR_GRUPO]) + (
                f" y {len(rutas) - MAX_RUTAS_POR_GRUPO} más" if len(rutas) > MAX_RUTAS_POR_GRUPO else "")
            detalles.append(f"{nombres}: {que}")
        extra = len(detalles) - MAX_DETALLES
        mas = f"; y {extra} correcciones más del mismo tipo" if extra > 0 else ""
        texto = "; ".join(detalles[:MAX_DETALLES]) + mas
        return PREFIJO_DEL_AVISO + texto + "."


def _bool_desde_texto(valor: str) -> bool | None:
    forma = forma_simple(valor)
    return True if forma in _VERDADERO else False if forma in _FALSO else None


def _a_texto(valor: object) -> str | object:
    """Un número, o una lista de números y textos, como texto; cualquier otra cosa es desconocida (no se adivina)."""
    if isinstance(valor, bool):
        return _OMITIR
    if isinstance(valor, (int, float)):
        return str(valor)
    if isinstance(valor, list) and valor and all(isinstance(x, (str, int, float)) and not isinstance(x, bool)
                                                 for x in valor):
        return "; ".join(str(x) for x in valor)
    return _OMITIR


def _a_entero(valor: object) -> int | object:
    if isinstance(valor, bool):
        return _OMITIR
    if isinstance(valor, float):
        return int(valor) if valor.is_integer() and valor >= 0 else _OMITIR
    if isinstance(valor, str) and re.fullmatch(r"\s*\d{1,15}\s*", valor, re.ASCII):
        return int(valor)
    if isinstance(valor, str):
        decimal = re.fullmatch(r"\s*(\d{1,15})[.,]0+\s*", valor, re.ASCII)
        if decimal:
            return int(decimal.group(1))
    return _OMITIR


def _a_numero(valor: object) -> int | float | object:
    if isinstance(valor, str):
        try:
            numero = float(valor.strip().replace(",", "."))
        except ValueError:
            return _OMITIR
        return int(numero) if numero.is_integer() else numero
    return _OMITIR


def _a_lista(valor: object, esq: dict) -> list | object:
    """Un texto suelto (o un objeto donde van objetos) es una lista de un elemento; vacío, una lista vacía."""
    if isinstance(valor, str):
        return [valor] if valor.strip() else []
    if isinstance(valor, dict) and "object" in _tipos(esq.get("items", {})):
        return [valor]
    return _OMITIR


def _tipos(esq: dict) -> list[str]:
    tipo = esq.get("type")
    return [] if tipo is None else [tipo] if isinstance(tipo, str) else list(tipo)


def _convertir(valor: object, tipos: list[str], esq: dict) -> object:
    """El valor llevado a uno de los tipos esperados, o `_OMITIR` si hacerlo sería adivinar."""
    for tipo in tipos:
        if tipo == "string":
            nuevo: object = _a_texto(valor)
        elif tipo == "integer":
            nuevo = _a_entero(valor)
        elif tipo == "number":
            nuevo = _a_numero(valor)
        elif tipo == "boolean":
            nuevo = _bool_desde_texto(valor) if isinstance(valor, str) else _OMITIR
            nuevo = _OMITIR if nuevo is None else nuevo
        elif tipo == "array":
            nuevo = _a_lista(valor, esq)
        else:
            nuevo = _OMITIR
        if nuevo is not _OMITIR:
            return nuevo
    return _OMITIR


def _resolver_enumerado(valor: object, ruta: str, permitidos: list) -> str | None:
    """El valor permitido que Work quiso escribir (mayúsculas, puntos, tildes, español), o `None` si no se sabe."""
    forma = forma_simple(valor)
    for permitido in permitidos:
        if forma_simple(permitido) == forma:
            return str(permitido)
    return SINONIMOS_DE_ENUMERADOS.get(_generica(ruta), {}).get(forma)


def _reparar_valor(valor: Any, esq: dict, ruta: str, cor: _Correcciones) -> Any:
    """El valor reparado según su esquema, o `_OMITIR` si hay que ignorarlo. Anota cada corrección."""
    tipos = _tipos(esq)
    if tipos and not tipo_cumple(valor, tipos):
        esperado = " o ".join(_ESPERADO.get(t, t) for t in tipos)
        nuevo = _convertir(valor, tipos, esq)
        if nuevo is _OMITIR:
            cor.anotar(ruta, f"llegó {_describir_tipo(valor)} y se esperaba {esperado}; se trató como desconocido "
                             "(la fila quedó sin confirmar)" if ruta.startswith("activities[")
                       else f"llegó {_describir_tipo(valor)} y se esperaba {esperado}; se trató como desconocido")
            return _OMITIR
        cor.anotar(ruta, f"llegó {_describir_tipo(valor)} y se esperaba {esperado}; se convirtió"
                         + (" (la fila quedó sin confirmar)" if ruta.startswith("activities[") else ""))
        valor = nuevo
    if "enum" in esq and valor not in esq["enum"]:
        elegido = _resolver_enumerado(valor, ruta, esq["enum"])
        if elegido is None:
            cor.anotar(ruta, "el valor no estaba entre los permitidos; se trató como desconocido")
            return _OMITIR
        escrito = " ".join(str(valor).split())[:_MAX_VALOR_EN_AVISO]
        cor.anotar(ruta, f"«{escrito}» se tomó como «{elegido}»")
        valor = elegido
    if isinstance(valor, str) and esq.get("minLength") and not valor.strip():
        return _OMITIR
    if isinstance(valor, str) and esq.get("pattern") and not re.search(esq["pattern"], valor):
        cor.anotar(ruta, "no tenía el formato permitido (p. ej. un enlace que no es de Gmail); se dejó vacío")
        return ""
    if isinstance(valor, dict):
        return _reparar_objeto(valor, esq, ruta, cor)
    if isinstance(valor, list) and "items" in esq:
        return _reparar_lista(valor, esq, ruta, cor)
    return valor


def _reparar_objeto(valor: dict, esq: dict, ruta: str, cor: _Correcciones) -> Any:
    salida = dict(valor)
    for clave, sub in esq.get("properties", {}).items():
        if salida.get(clave) is None:
            continue
        nuevo = _reparar_valor(salida[clave], sub, f"{ruta}.{clave}" if ruta else clave, cor)
        if nuevo is _OMITIR:
            del salida[clave]
        else:
            salida[clave] = nuevo
    if not ruta:
        return salida                       # la identidad del análisis la decide quien llama
    for clave in esq.get("required", []):
        if salida.get(clave) is None:
            relleno = RELLENOS.get(f"{_generica(ruta)}.{clave}", _OMITIR)
            if relleno is _OMITIR:
                cor.anotar(ruta, f"se omitió porque le faltaba «{clave}»")
                return _OMITIR
            salida[clave] = list(relleno) if isinstance(relleno, list) else relleno
            que = "faltaba; se marcó «sin fuente»" if relleno == SIN_FUENTE else (
                f"faltaba «{clave}»; se dejó " + ("vacío" if relleno in ("", []) else f"como «{relleno}»"))
            cor.anotar(f"{ruta}.{clave}", que)
    return salida


def _reparar_lista(valor: list, esq: dict, ruta: str, cor: _Correcciones) -> list:
    salida: list = []
    for indice, item in enumerate(valor):
        ruta_item = f"{ruta}[{indice}]"
        if item is None:
            cor.anotar(ruta_item, "estaba vacío; se omitió")
            continue
        antes = cor.total
        nuevo = _reparar_valor(item, esq["items"], ruta_item, cor)
        if nuevo is _OMITIR:
            continue
        if ruta == "activities" and cor.total > antes and isinstance(nuevo, dict):
            # un valor de la fila cambió de significado: nada queda confirmado (R12, «no se inventan datos»)
            if str(nuevo.get("estado") or "").strip().casefold() != "contradictorio":
                nuevo["estado"] = "pendiente"
        salida.append(nuevo)
    return salida


def reparar(payload: dict) -> str:
    """Repara en el sitio los descuidos secundarios del análisis y devuelve un aviso (o '' si no hubo nada).

    No toca la identidad (`thread_id`, `message_id`, `client_email`) ni `status`: esos los comprueba quien llama.
    """
    cor = _Correcciones()
    if str(payload.get("engine") or "") != "chatgpt_work":
        cor.anotar("engine", "no decía chatgpt_work; se tomó como chatgpt_work porque el análisis viene de Work")
        payload["engine"] = "chatgpt_work"
    nombre = payload.get("project_name")
    if isinstance(nombre, (int, float)) and not isinstance(nombre, bool):
        nombre = str(nombre)
    if not isinstance(nombre, str) or not nombre.strip():
        cor.anotar("project_name", f"faltaba; se usó «{SIN_NOMBRE}»")
        nombre = SIN_NOMBRE
    payload["project_name"] = nombre
    esquema = load_schema()
    propiedades = {k: v for k, v in esquema.get("properties", {}).items() if k not in _PROPIOS}
    reparado = _reparar_objeto(
        {k: v for k, v in payload.items() if k in propiedades}, {"properties": propiedades}, "", cor)
    for clave in propiedades:
        if clave in payload:
            if clave in reparado:
                payload[clave] = reparado[clave]
            else:
                del payload[clave]
    return cor.aviso()


def _quitar_comas_sobrantes(texto: str) -> str:
    """Quita las comas que preceden a «}» o «]» fuera de los textos."""
    salida: list[str] = []
    en_texto = escapado = False
    for indice, car in enumerate(texto):
        if en_texto:
            salida.append(car)
            escapado = (car == "\\") and not escapado
            if car == '"' and not escapado:
                en_texto = False
            continue
        if car == '"':
            en_texto = True
        elif car == ",":
            resto = texto[indice + 1:].lstrip()
            if resto[:1] in ("}", "]"):
                continue
        salida.append(car)
    return "".join(salida)


def leer_json(crudo: str) -> tuple[Any, str]:
    """(valor, aviso): el JSON del bloque, con reparaciones solo mecánicas (marcas ```, texto suelto, comas sobrantes).

    No repara comillas ni cierres que faltan: eso sería adivinar. Si no se puede leer lanza `Rechazo` con la posición
    del fallo (nunca el contenido ni el error de Python).
    """
    try:
        return json.loads(crudo), ""
    except json.JSONDecodeError as error:
        primero = error
    limpio = re.sub(r"^```[a-zA-Z]*\s*|\s*```$", "", crudo.strip())
    inicio, fin = limpio.find("{"), limpio.rfind("}")
    if inicio >= 0 and fin > inicio:
        limpio = limpio[inicio:fin + 1]
    limpio = _quitar_comas_sobrantes(limpio)
    try:
        valor = json.loads(limpio, strict=False)
    except json.JSONDecodeError:
        raise Rechazo(
            f"El análisis de Work no se puede leer: su formato está dañado cerca de la línea {primero.lineno}, "
            f"columna {primero.colno}.", "json_ilegible") from None
    return valor, ("Work escribió algo distinto de lo esperado; se corrigió solo: el bloque de datos traía marcas de "
                   "formato, texto suelto, comas sobrantes o saltos de línea dentro de un texto, y se limpió.")


_RUTA_EN_ERROR = re.compile(r"^(?:Falta el campo (\w+)|([\w.\[\]]+) (?:debe|tiene|no puede))")


def campos_de(errores: Sequence[str]) -> tuple[str, ...]:
    """Los nombres de campo que mencionan los errores de `validate`, sin su contenido."""
    nombres = []
    for error in errores:
        encontrado = _RUTA_EN_ERROR.match(error)
        if encontrado:
            nombres.append(encontrado.group(1) or encontrado.group(2))
    return tuple(dict.fromkeys(nombres))


def _version_declarada(valor: object) -> int:
    """La versión que Work declara en `schema_version` (opcional, fuera del esquema); 1 si no dice o no se lee."""
    if isinstance(valor, str) and valor.strip().isdigit():
        return int(valor)
    return valor if isinstance(valor, int) and not isinstance(valor, bool) else 1


def comprobar_identidad(payload: dict) -> None:
    """Sin `thread_id`, `message_id` y `client_email` no se sabe de qué correo habla el análisis: se rechaza."""
    faltan = [campo for campo in IDENTIDAD if not isinstance(payload.get(campo), str) or not payload[campo].strip()]
    if faltan:
        raise Rechazo("Al análisis de Work le falta " + ", ".join(f"«{campo}»" for campo in faltan)
                      + " o no es un texto: sin eso no se sabe de qué correo habla.", "falta_identidad", faltan)


def _normalizar_clasificacion(payload: dict) -> str:
    """T-52: una `classification` ilegible NO se borra (el caso avanzaría como si no existiera): va a revisión.

    Se toleran mayúsculas, espacios y guiones («solicitud nueva»); `null` equivale a no traerla.
    """
    if "classification" not in payload:
        return ""
    valor = payload["classification"]
    if valor is None:
        del payload["classification"]
        return ""
    texto = "_".join(str(valor).upper().replace("-", " ").split()) if isinstance(valor, str) else ""
    if texto in CLASIFICACIONES:
        payload["classification"] = texto
        return ""
    payload["classification"] = REVISION_MANUAL
    return "La clasificación que escribió Work no es una de las cuatro permitidas; el mensaje queda en revisión manual."


def normalizar_analisis(payload: dict) -> None:
    """Deja el análisis listo para importar: estado oficial, descuidos de tipo corregidos y avisos de lo corregido.

    Es la **única** política de validación del contrato con Work (U-51): la usan el puente (`work_sync`) y la
    importación (`work_import`). Solo rechaza (`Rechazo`) un estado desconocido o lo que, tras reparar, aún incumple
    el esquema; todo lo secundario se corrige solo y queda dicho en `_correcciones_de_work` (T-28, D-106).
    """
    avisos = corregir_descuidos_de_tipo(payload)  # p. ej. `unresolved: true`: queda vacío y el caso lo advierte
    if avisos:
        payload["_avisos_del_puente"] = avisos
    status, aviso = reconocer_estado_de_work(payload.get("status"))
    if status is None:
        raise Rechazo("El estado que escribió Work en «status» no es uno de los permitidos.",
                      "estado_desconocido", ["status"])
    payload["status"] = status  # la etiqueta oficial (aunque venga sin tildes, con punto final o con un sinónimo)
    if aviso:
        payload["_avisos_del_puente"] = [*payload.get("_avisos_del_puente", []), aviso]
    aviso_de_clase = _normalizar_clasificacion(payload)
    if aviso_de_clase:
        payload["_avisos_del_puente"] = [*payload.get("_avisos_del_puente", []), aviso_de_clase]
    reparado = reparar(payload)
    if reparado:
        payload["_correcciones_de_work"] = [*payload.get("_correcciones_de_work", []), reparado]
    errors = validate(payload)      # spec/contrato_work.schema.json es la fuente de verdad
    entendida, declarada = version_del_contrato(), _version_declarada(payload.get("schema_version"))
    if declarada > entendida and not errors:
        payload["_avisos_del_puente"] = [*payload.get("_avisos_del_puente", []), (
            f"Work declara la versión {declarada} del contrato y Anka entiende hasta la {entendida}: "
            "revisa este análisis con cuidado.")]
    if errors:   # tras reparar no debería quedar nada; si queda, se dice dónde y nunca cuál era el contenido
        campos = campos_de(errors)
        raise Rechazo("El análisis de Work no cumple el formato esperado en: " + (", ".join(campos) or "varios campos")
                      + ".", "formato", campos)
