"""Estructura de carpetas de Drive: año / mes / cliente / solicitud / anexos (U-102).

La carpeta padre configurada (`drive_parent_id`) es la carpeta «Proyectos». Dentro, el año y el mes (por ejemplo
`2026/11-Noviembre`) se crean solos con la fecha en que se emite la T; después va la carpeta de cada cliente y, dentro,
la de la solicitud (`T&E26145 Mina Norte`) con una subcarpeta `anexos` para los archivos del cliente y la tabla.

Regla de seguridad: se reutiliza la carpeta de cliente que coincida (sin importar mayúsculas, tildes ni sufijos como
S.A.S. o Ltda.) en cualquier mes del año o del anterior. Una carpeta de cliente nueva se crea solo cuando una persona lo
confirma (`create=True`) o cuando el nombre del cliente lo escribió una persona y no existe ninguna carpeta igual ni
parecida (`auto_create=True`). Si hay una parecida ("Acme" / "Acme Colombia"), nunca se crea: decide una persona. Lo
mismo vale para la carpeta de la solicitud: si el cliente ya tiene una de nombre parecido o con el mismo archivo
adjunto, se detiene y avisa en vez de duplicar el proyecto.
"""

from __future__ import annotations
from anka.infra.clientes_google import ClienteDrive

import re
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime
from difflib import SequenceMatcher
from functools import lru_cache

from anka.dominio.errores import ErrorDeValidacion
from anka.dominio.reloj import ahora
from anka.infra.google_services import ensure_folder, list_file_names, list_folders
from anka.dominio.reception import normalized_text

PATH_TEMPLATE = "{año}/{mes}/{cliente}/{solicitud}/anexos"
ANEXOS = "anexos"
MAX_FOLDER_NAME = 150       # un asunto largo no debe producir un nombre ilegible ni rozar el tope de Drive
MESES = ("Enero", "Febrero", "Marzo", "Abril", "Mayo", "Junio", "Julio", "Agosto", "Septiembre", "Octubre",
         "Noviembre", "Diciembre")
ANIOS_QUE_SE_BUSCAN = 2     # el año de la solicitud y el anterior, para reconocer a un cliente que ya existe

# Sufijos societarios que se ignoran al comparar nombres (solo al final del nombre).
LEGAL_SUFFIXES = (
    "s a s", "s a", "s l", "e u", "sas", "ltda", "sa", "sl", "eu", "inc", "corp", "llc", "cia",
)
_SUFFIX_TOKENS = tuple(tuple(suffix.split()) for suffix in LEGAL_SUFFIXES)
_INVALID_CHARS = re.compile(r'[\\/:*?"<>|\x00-\x1f]')


class DriveLayoutError(Exception):
    """Error de estructura de Drive con un mensaje listo para mostrar a una persona."""


class ClientNotFound(DriveLayoutError):
    """No existe la carpeta del cliente y nadie confirmó que es nuevo."""
    def __init__(self, client_name: str):
        super().__init__(
            f"No existe una carpeta del cliente '{client_name}'. Confirma que es un cliente "
            "nuevo para crearla, o escribe el nombre exacto de una carpeta existente."
        )
        self.client_name = client_name


class AmbiguousClient(DriveLayoutError):
    """Hay varias carpetas de cliente que coinciden y una persona debe elegir."""
    def __init__(self, client_name: str, folders: list[dict]):
        names = ", ".join(f"'{f['name']}'" for f in folders)
        super().__init__(
            f"Hay varias carpetas parecidas a '{client_name}' ({names}). "
            "Escribe el nombre exacto de la que corresponde."
        )
        self.client_name = client_name
        self.folders = folders


class SimilarClient(DriveLayoutError):
    """Ya hay una carpeta de cliente parecida: no se crea otra sin confirmación."""
    def __init__(self, client_name: str, folders: list[dict]):
        names = ", ".join(f"'{f['name']}'" for f in folders)
        super().__init__(
            f"No creé la carpeta del cliente '{client_name}' porque ya hay una parecida: {names}. "
            "Si es el mismo cliente, escribe el nombre exacto de esa carpeta; si es otro, "
            "marca 'Es un cliente nuevo' para crearla."
        )
        self.client_name = client_name
        self.folders = folders


class SimilarProject(DriveLayoutError):
    """El cliente ya tiene una solicitud parecida: no se crea otra sin confirmación."""
    def __init__(self, client_name: str, matches: list[dict]):
        detail = "; ".join(f"'{m['name']}' ({m['reason']})" for m in matches)
        super().__init__(
            f"No creé la carpeta de la solicitud porque '{client_name}' ya tiene una parecida: "
            f"{detail}. Revísala en Drive; si es un proyecto distinto, marca "
            "'Es un proyecto distinto' para crearla de todos modos."
        )
        self.client_name = client_name
        self.matches = matches


@dataclass
class ProjectFolder:
    """Resultado de preparar la carpeta: la del cliente, si se creó y la de la solicitud."""
    client: dict
    client_created: bool
    folder: dict


def normalize_client_name(name: str) -> str:
    """Clave de comparación: sin tildes, mayúsculas, puntuación ni sufijo societario final."""
    tokens = re.findall(r"[a-z0-9]+", normalized_text(name))
    stripped = True
    while tokens and stripped:
        stripped = False
        for suffix in _SUFFIX_TOKENS:
            if len(tokens) >= len(suffix) and tuple(tokens[-len(suffix):]) == suffix:
                tokens = tokens[:-len(suffix)]
                stripped = True
                break
    return " ".join(tokens)


@lru_cache(maxsize=65536)
def _keys_similar(a: str, b: str) -> bool:
    """¿Dos claves normalizadas (ya sin tildes ni sufijos) parecen el mismo nombre?"""
    if not a or not b:
        return False
    if a == b:
        return True
    ta, tb = set(a.split()), set(b.split())
    small, big = (ta, tb) if len(ta) <= len(tb) else (tb, ta)
    if len(small) >= 2 and small <= big:
        return True
    comparador = SequenceMatcher(None, a, b)
    # Las dos cotas son techos de `ratio()`: si ya quedan por debajo, `ratio()` (lo caro) también (U-48).
    if comparador.real_quick_ratio() < 0.85 or comparador.quick_ratio() < 0.85:
        return False
    return comparador.ratio() >= 0.85


def clave_de_proyecto(nombre: str) -> str:
    """La clave con que se compara el nombre de un proyecto: sin tildes, sufijos ni el prefijo de la carpeta."""
    return normalize_client_name(project_title(nombre))


def claves_parecidas(a: str, b: str) -> bool:
    """¿Dos claves de proyecto o de cliente parecen el mismo nombre? (el criterio de las carpetas de Drive)."""
    return _keys_similar(a, b)


def project_title(folder_name: str) -> str:
    """Nombre del proyecto en `T&E<número> <proyecto>` o `<prefijo> - <proyecto> - <id>`; el entero si no encaja."""
    nombre = folder_name.strip()
    con_codigo = re.fullmatch(r"T&E\d{5}[ _-]+(.+)", nombre)
    if con_codigo:
        return con_codigo.group(1)
    match = re.fullmatch(r"[^-]+ - (.+) - [0-9A-Za-z_]{6,12}", nombre)
    return match.group(1) if match else folder_name


def nombre_del_mes(fecha: datetime) -> str:
    """El nombre de la carpeta del mes: `11-Noviembre`."""
    return f"{fecha.month:02d}-{MESES[fecha.month - 1]}"


def nombre_de_solicitud(numero: str, proyecto: str | None) -> str:
    """La carpeta de la solicitud desde la T: `T&E26145 Mina Norte` (no se renombra al aprobar, U-102)."""
    return clean_folder_name(f"T&E{numero} {proyecto or 'Sin nombre'}")


def carpeta_de_anexos(drive: ClienteDrive, folder_id: str) -> dict:
    """La subcarpeta `anexos` de la carpeta de una solicitud (se crea si no existe)."""
    return ensure_folder(drive, folder_id, ANEXOS)


def _nombres_de_archivos(drive: ClienteDrive, folder_id: str) -> list[str]:
    """Los archivos de una solicitud: los de `anexos` y los sueltos (carpetas hechas con la ruta anterior)."""
    nombres = list_file_names(drive, folder_id)
    anexos = next((f for f in list_folders(drive, folder_id) if f["name"] == ANEXOS), None)
    return nombres + (list_file_names(drive, anexos["id"]) if anexos else [])


def similar_project_folders(
    drive: ClienteDrive, client_id: str, request_name: str, attachment_names: Iterable[str] = (),
    *, max_scan: int = 30, incluir_mismo_nombre: bool = False,
) -> list[dict]:
    """Solicitudes del cliente que parecen ser el mismo proyecto: nombre parecido o mismo adjunto.

    Con `incluir_mismo_nombre` también señala la que se llama igual (otra carpeta del mismo cliente en otro mes).
    """
    key = normalize_client_name(project_title(request_name))
    wanted = {n.strip().casefold() for n in attachment_names if n and n.strip()}
    matches = []
    for folder in list_folders(drive, client_id)[:max_scan]:
        if folder["name"] == request_name:
            if incluir_mismo_nombre:
                matches.append({"name": folder["name"], "reason": "ya existe con ese nombre en otro mes"})
            continue
        if _keys_similar(key, normalize_client_name(project_title(folder["name"]))):
            matches.append({"name": folder["name"], "reason": "nombre parecido"})
            continue
        if wanted:
            shared = sorted(wanted & {n.casefold() for n in _nombres_de_archivos(drive, folder["id"])})
            if shared:
                matches.append({
                    "name": folder["name"], "reason": f"contiene el mismo archivo '{shared[0]}'",
                })
    return matches


def clean_folder_name(name: str) -> str:
    """Nombre de carpeta sin caracteres no válidos ni espacios sobrantes; falla si queda vacío."""
    clean = re.sub(r"\s+", " ", _INVALID_CHARS.sub(" ", name)).strip(" .")
    if not clean:
        raise ErrorDeValidacion("El nombre de la carpeta no puede estar vacío.")
    if len(clean) > MAX_FOLDER_NAME:
        corte = clean[:MAX_FOLDER_NAME]
        clean = (corte.rsplit(" ", 1)[0] if " " in corte else corte).strip(" .")   # sin cortar una palabra a la mitad
    return clean


def padre_invalido(drive: ClienteDrive, parent_id: str) -> str | None:
    """Aviso si la carpeta padre es la de un año (debe ser «Proyectos»); None si todo bien (R4)."""
    name = str(drive.files().get(fileId=parent_id, fields="id,name").execute().get("name", "")).strip()
    if re.fullmatch(r"\d{4}", name):
        return (
            f"La carpeta padre configurada se llama '{name}' y parece la de un año. drive_parent_id debe ser la "
            "carpeta "
            "«Proyectos»: el año y el mes se crean solos dentro. Apunta drive_parent_id a «Proyectos» en config.json."
        )
    return None


def _buscar(drive: ClienteDrive, parent_id: str, nombre: str) -> dict | None:
    """La subcarpeta con ese nombre exacto, sin crearla."""
    return next((f for f in list_folders(drive, parent_id) if f["name"] == nombre), None)


def clientes_del_archivo(drive: ClienteDrive, parent_id: str, anio: int) -> list[dict]:
    """Las carpetas de cliente de todos los meses del año y del anterior; cada una con el `mes_id` donde está."""
    por_anio = {f["name"]: f for f in list_folders(drive, parent_id)}
    encontrados: list[dict] = []
    for año in range(anio, anio - ANIOS_QUE_SE_BUSCAN, -1):
        carpeta = por_anio.get(str(año))
        if not carpeta:
            continue
        for mes in list_folders(drive, carpeta["id"]):
            encontrados.extend({**cliente, "mes_id": mes["id"]} for cliente in list_folders(drive, mes["id"]))
    return encontrados


def _decidir_cliente(
    conocidos: list[dict], mes_id: str, nombre: str, *, create: bool, auto_create: bool,
) -> tuple[dict | None, str, bool]:
    """(carpeta del cliente en este mes si ya existe, nombre con que se crea si falta, ¿es un cliente nuevo?).

    Un cliente que ya existe en otro mes no es nuevo: este mes crea su carpeta con el mismo nombre sin confirmación.
    Uno sin carpeta en ningún mes se crea solo con confirmación (`create`) o, con `auto_create`, si no hay parecido.
    """
    clave = normalize_client_name(nombre)
    iguales = [c for c in conocidos if normalize_client_name(c["name"]) == clave] if clave else []
    if iguales:
        distintos = {c["name"].strip().casefold(): c["name"] for c in iguales}
        elegido = next(iter(distintos.values()))
        if len(distintos) > 1:
            exactos = [n for k, n in distintos.items() if k == nombre.strip().casefold()]
            if len(exactos) != 1:
                raise AmbiguousClient(nombre, [{"name": n} for n in distintos.values()])
            elegido = exactos[0]
        aqui = [c for c in iguales if c["name"] == elegido and c["mes_id"] == mes_id]
        return (aqui[0] if aqui else None), elegido, False
    if not create:
        if not auto_create:
            raise ClientNotFound(nombre)
        parecidas = {c["name"]: c for c in conocidos if _keys_similar(clave, normalize_client_name(c["name"]))}
        if parecidas:
            raise SimilarClient(nombre, list(parecidas.values()))
    return None, nombre, True


def create_project_folder(
    drive: ClienteDrive, parent_id: str, client_name: str, request_name: str, *, emitida: datetime | None = None,
    create_client: bool = False, auto_create_client: bool = False, attachment_names: Iterable[str] = (),
    allow_similar_project: bool = False,
) -> ProjectFolder:
    """Crea (o reutiliza) `<padre>/<año>/<mes>/<cliente>/<solicitud>/anexos`. Idempotente.

    El año y el mes salen de `emitida` (la fecha en que se emitió la T; hoy si no hay T). Con `auto_create_client` un
    cliente sin carpeta ni parecidas en ningún mes se crea solo. Si el cliente ya tenía carpeta y hay una solicitud
    parecida (otro nombre o el mismo adjunto), lanza `SimilarProject` salvo que `allow_similar_project` lo permita.
    No crea nada hasta que todas las comprobaciones pasan.
    """
    fecha = emitida or ahora()
    if aviso := padre_invalido(drive, parent_id):
        raise DriveLayoutError(aviso)
    clean = clean_folder_name(client_name)
    request = clean_folder_name(request_name)
    anio = _buscar(drive, parent_id, str(fecha.year))
    mes = _buscar(drive, anio["id"], nombre_del_mes(fecha)) if anio else None
    conocidos = clientes_del_archivo(drive, parent_id, fecha.year)
    aqui, nombre, nuevo = _decidir_cliente(
        conocidos, mes["id"] if mes else "", clean, create=create_client, auto_create=auto_create_client)
    if not nuevo and not allow_similar_project:
        ya_esta = aqui is not None and any(f["name"] == request for f in list_folders(drive, aqui["id"]))
        if not ya_esta:
            clave = normalize_client_name(nombre)
            similares: list[dict] = []
            for carpeta in conocidos:
                if normalize_client_name(carpeta["name"]) == clave:
                    similares += similar_project_folders(
                        drive, carpeta["id"], request, attachment_names,
                        incluir_mismo_nombre=aqui is None or carpeta["id"] != aqui["id"])
            if similares:
                raise SimilarProject(nombre, similares)
    anio = anio or ensure_folder(drive, parent_id, str(fecha.year))
    mes = mes or ensure_folder(drive, anio["id"], nombre_del_mes(fecha))
    cliente = aqui or ensure_folder(drive, mes["id"], nombre)
    carpeta = ensure_folder(drive, cliente["id"], request)
    carpeta_de_anexos(drive, carpeta["id"])
    return ProjectFolder(client=cliente, client_created=nuevo, folder=carpeta)


def trash_project_folder(drive: ClienteDrive, parent_id: str, folder_id: str) -> bool:
    """Mueve a la papelera de Drive la carpeta de una solicitud creada por este sistema.

    Solo actúa sobre `<padre>/<año>/<mes>/<cliente>/<solicitud>` (o la ruta anterior `<padre>/<cliente>/<solicitud>`):
    si la carpeta no está exactamente a ese nivel bajo la carpeta padre configurada, no la toca. La papelera es
    recuperable. Devuelve False si la carpeta ya no existía o ya estaba en la papelera.
    """
    try:
        folder = drive.files().get(fileId=folder_id, fields="id,name,parents,trashed").execute()
    except Exception as exc:  # googleapiclient.errors.HttpError 404: ya no existe
        if getattr(getattr(exc, "resp", None), "status", None) == 404:
            return False
        raise
    if folder.get("trashed"):
        return False
    cadena = [folder_id]
    nombres = [str(folder.get("name") or "")]
    actual = folder
    for _ in range(4):
        padre = (actual.get("parents") or [""])[0]
        cadena.append(padre)
        if not padre or padre == parent_id:
            break
        actual = drive.files().get(fileId=padre, fields="id,name,parents").execute()
        nombres.append(str(actual.get("name") or ""))
    nivel = cadena.index(parent_id) if parent_id in cadena else -1
    # Dos niveles es la ruta anterior `cliente/solicitud`; si el de en medio es un año, es `año/mes`, no una solicitud.
    es_un_mes = nivel == 2 and re.fullmatch(r"\d{4}", nombres[1]) is not None
    if nivel not in (2, 4) or es_un_mes:
        raise DriveLayoutError(
            f"La carpeta '{folder.get('name', folder_id)}' no está en el nivel Año/Mes/Cliente/Solicitud "
            "de la carpeta configurada; no la moví a la papelera."
        )
    drive.files().update(fileId=folder_id, body={"trashed": True}).execute()
    return True
