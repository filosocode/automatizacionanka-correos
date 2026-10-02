"""La documentación OpenAPI 3 del dashboard y la página de Swagger UI (spec/07-api.md, requisito T-13).

La especificación se arma aquí a partir de una tabla declarativa (`OPERACIONES`) y se compara, en las pruebas, con
las rutas reales de `server.py`: una ruta sin documentar o documentada de más hace fallar una prueba.
"""

from __future__ import annotations

from dataclasses import dataclass, fields
from typing import Any, get_type_hints

from anka.dominio.caso import CLIENT_TYPES, SECTORS, Caso
from anka.infra.settings import ACTIVE_MODES
from anka.servicios.ediciones import EDIT_REASONS
from anka.servicios.followup_reminders import ACTIONS as FOLLOWUP_ACTIONS

# Swagger UI: versión fijada y huella de integridad (SRI), para que el navegador rechace un archivo alterado.
SWAGGER_VERSION = "5.17.14"
SWAGGER_CSS = (f"https://cdn.jsdelivr.net/npm/swagger-ui-dist@{SWAGGER_VERSION}/swagger-ui.css",
               "sha384-wxLW6kwyHktdDGr6Pv1zgm/VGJh99lfUbzSn6HNHBENZlCN7W602k9VkGdxuFvPn")
SWAGGER_JS = (f"https://cdn.jsdelivr.net/npm/swagger-ui-dist@{SWAGGER_VERSION}/swagger-ui-bundle.js",
              "sha384-wmyclcVGX/WhUkdkATwhaK1X1JtiNrr2EoYJ+diV3vj4v6OC5yCeSu+yW13SYJep")

# Qué efecto tiene una operación; las que actúan fuera del dashboard llevan «⚠» en el resumen.
EFECTOS = {
    "lectura": "Solo lee: no cambia nada.",
    "guarda": "Guarda cambios en la base local del dashboard.",
    "correo": "⚠ Envía un correo real al cliente desde la cuenta de Gmail.",
    "drive": "⚠ Puede enviar un correo real o crear carpetas en Google Drive.",
    "gmail": "⚠ Consulta Gmail y actualiza los casos.",
    "elimina": "⚠ Elimina el caso del dashboard (su historial se conserva como evidencia).",
    "google": "⚠ Abre el consentimiento de Google en el navegador de este equipo y guarda una autorización nueva.",
}
EFECTOS_EXTERNOS = {"correo", "drive", "gmail", "elimina", "google"}

TAGS = {
    "Lectura (JSON)": "Rutas que devuelven JSON y no cambian nada. El mejor punto de partida para probar.",
    "Páginas": "Las pantallas HTML del dashboard y la descarga del Excel.",
    "Acciones del caso": "Formularios `POST` que actúan sobre un caso. Responden con una redirección (303).",
    "Acciones generales": "Formularios `POST` que no son de un caso: modo, revisión de Gmail, rescatar, eliminar.",
    "Documentación": "Esta documentación.",
}


@dataclass(frozen=True)
class Campo:
    """Un campo de una petición: parámetro de la URL (GET) o campo del formulario (POST)."""

    nombre: str
    descripcion: str
    tipo: str = "string"                     # string | integer | boolean | array
    requerido: bool = False
    ejemplo: Any = None
    opciones: tuple[str, ...] | None = None  # valores permitidos
    en: str = "query"                        # query | path (solo GET)


@dataclass(frozen=True)
class Operacion:
    """Una ruta del servidor con todo lo necesario para documentarla."""

    metodo: str                              # get | post
    ruta: str
    etiqueta: str
    resumen: str
    descripcion: str
    efecto: str = "lectura"
    campos: tuple[Campo, ...] = ()
    contenido: str = "html"                  # html | json | xlsx | redirect
    ejemplo_respuesta: Any = None


_ID = Campo("thread_id", "Id del hilo de Gmail del caso (aparece en la URL de `/case`).", requerido=True,
            ejemplo="18f3a9c2d4e5b601")
_MENSAJE = Campo("message", "Texto que se muestra como aviso arriba de la pantalla.")
_FILA = ("Filas de la tabla de actividades: `a0_activity`, `a0_servicio_tecnica`, `a0_ubicacion`, `a0_quantity`, "
         "`a0_unit`, `a0_entregable`, `a0_plazo_restriccion`, `a0_fuente`, `a0_estado`, `a0_notes`; "
         "la fila 1 usa `a1_…` y así. Se aceptan hasta 200 filas.")

OPERACIONES: tuple[Operacion, ...] = (
    # ------------------------------------------------------------------ lectura (JSON)
    Operacion("get", "/api/casos", "Lectura (JSON)", "Lista los casos",
              "Devuelve un resumen de cada caso, el más reciente primero. Con `descartados=1` incluye los descartados.",
              campos=(Campo("descartados", "Incluir los casos descartados.", "boolean", ejemplo=False),),
              contenido="json",
              ejemplo_respuesta=[{
                  "thread_id": "18f3a9c2d4e5b601", "project_name": "Línea de transmisión Llanos 8",
                  "client_name": "ACME S.A.S.", "recipient": "ana@acme.com", "status": "waiting",
                  "status_label": "Esperando cliente", "quote_code": "26002",
                  "waiting_since": "2026-10-01T10:15:00-05:00", "updated_at": "2026-10-01T10:15:00-05:00"}]),
    Operacion("get", "/api/casos/{thread_id}", "Lectura (JSON)", "Detalle de un caso",
              "Devuelve el caso completo: análisis, actividades, cambios y vínculos pendientes, seguimiento y "
              "estado. Responde 404 si el caso no existe.",
              campos=(Campo("thread_id", "Id del hilo de Gmail del caso.", requerido=True, en="path",
                            ejemplo="18f3a9c2d4e5b601"),),
              contenido="json"),
    Operacion("get", "/api/salud", "Lectura (JSON)", "¿Conecta todo? Salud de las conexiones",
              "Revisa la base local, la configuración, el puente de ChatGPT Work, la autorización de Google y el "
              "motor de análisis. Responde **200** si todo está bien (o no aplica) y **503** si algo falla; el "
              "cuerpo dice qué. Con `profundo=1` hace además una consulta real de solo lectura a **Gmail** y a "
              "**Drive** (más lento, pero confirma que conectan de verdad).",
              campos=(Campo("profundo", "Consultar de verdad a Gmail y a Drive (solo lectura).", "boolean",
                            ejemplo=False),),
              contenido="json",
              ejemplo_respuesta={"estado": "ok", "profundo": False, "comprobaciones": [
                  {"componente": "base_de_datos", "estado": "ok", "detalle": "Base local lista (esquema 9).", "ms": 3},
                  {"componente": "puente_work", "estado": "ok",
                   "detalle": "Puente Work activo: Gmail revisado hace 36 s.", "ms": 0},
                  {"componente": "motor_de_analisis", "estado": "no_aplica",
                   "detalle": "En modo ChatGPT Work el análisis lo hace Work, no Anka.", "ms": 0}]}),
    Operacion("get", "/api/autorizacion", "Lectura (JSON)", "Estado de la autorización de Google",
              "Dice si la autorización de Google (Gmail y Drive) está vigente: `ok`, `vencida` (hay que reautorizar), "
              "`en_curso` (esperando el consentimiento), `fallida` (no se completó) o `desconocido` (sin conexión). "
              "Se verifica como máximo cada 5 minutos.",
              contenido="json",
              ejemplo_respuesta={"estado": "vencida", "texto": "Google ya no acepta el token guardado. "
                                 "La autorización de Google venció o falta. Pulsa «Reautorizar Google» en el dashboard "
                                 "o ejecuta: python app.py auth"}),
    Operacion("get", "/state", "Lectura (JSON)", "Estado para refrescar la pantalla",
              "Marca de cambios (`v`) y estado del puente de ChatGPT Work. Las páginas abiertas la consultan cada "
              "20 segundos para saber si deben recargarse.",
              contenido="json",
              ejemplo_respuesta={"v": "3|2026-10-01T19:01:12-05:00|0|50", "bridge": "ok",
                                 "text": "Puente Work activo: Gmail revisado hace 36 s.",
                                 "google": {"estado": "ok", "texto": "La autorización de Google está vigente."}}),
    # ------------------------------------------------------------------ páginas
    Operacion("get", "/", "Páginas", "Resumen («qué hacer hoy»)",
              "Novedades, casos que esperan acción y métricas.", campos=(_MENSAJE,)),
    Operacion("get", "/requests", "Páginas", "Lista de solicitudes",
              "Todas las solicitudes con su etapa; las urgentes van primero.", campos=(_MENSAJE,)),
    Operacion("get", "/discarded", "Páginas", "Descartadas",
              "Hilos descartados con su motivo y el botón de rescatar.", campos=(_MENSAJE,)),
    Operacion("get", "/archive", "Páginas", "Archivo",
              "Casos eliminados cuyo historial se conserva como evidencia."),
    Operacion("get", "/case", "Páginas", "Pantalla de un caso",
              "Etapa, siguiente paso, tabla de actividades, conversación, respuesta y seguimiento.",
              campos=(_ID, _MENSAJE,
                      Campo("template", "Texto prepuesto en el cuadro de respuesta.",
                            opciones=("libre", "received", "ack", "ask_code", "notify_contact", "verify",
                                      "scope_update", "acuse", "te_aprobada")),
                      Campo("change_id", "Cambio al que responde la plantilla.", "integer", ejemplo=1),
                      Campo("to", "A quién proponer el correo del cambio.", opciones=("sender", "contact")))),
    Operacion("get", "/acta", "Páginas", "Acta de cambios (imprimible)",
              "Historial con fecha, autor y huella de cada hecho del caso, apto para imprimir.", campos=(_ID,)),
    Operacion("get", "/export.xlsx", "Páginas", "Exportar actividades a Excel",
              "Descarga un `.xlsx` con las actividades de un caso, o de todos si no se indica `thread_id`.",
              campos=(Campo("thread_id", "Si se omite, exporta todos los casos."),), contenido="xlsx"),
    # ------------------------------------------------------------------ documentación
    Operacion("get", "/docs", "Documentación", "Swagger UI", "Esta página."),
    Operacion("get", "/openapi.json", "Documentación", "Especificación OpenAPI",
              "La misma documentación en JSON, para importarla en Postman, Insomnia u otro cliente.",
              contenido="json"),
    # ------------------------------------------------------------------ acciones del caso
    Operacion("post", "/approve", "Acciones del caso", "⚠ Aprobar: enviar preguntas o crear la carpeta",
              "Según el estado del caso: con datos pendientes **envía las preguntas al cliente**; con alcance "
              "completo **crea la carpeta del proyecto en Drive**.",
              "drive", (_ID,
                        Campo("body", "Texto del correo; si se omite, se usa el sugerido."),
                        Campo("client_name", "Carpeta del cliente en Drive (para la carpeta del proyecto)."),
                        Campo("new_client", "`1` confirma que puede crearse la carpeta del cliente si no existe.",
                              opciones=("1",)),
                        Campo("allow_similar", "`1` crea la solicitud aunque haya una parecida.", opciones=("1",))),
              "redirect"),
    Operacion("post", "/metadata", "Acciones del caso", "Guardar datos comerciales",
              "Cliente, tipo, sector y notas del caso.", "guarda",
              (_ID, Campo("client_name", "Nombre del cliente.", ejemplo="ACME S.A.S."),
               Campo("client_type", "Tipo de cliente.", opciones=tuple(CLIENT_TYPES)),
               Campo("sector", "Sector.", opciones=tuple(SECTORS)),
               Campo("notes", "Notas internas.")), "redirect"),
    Operacion("post", "/draft", "Acciones del caso", "Guardar la respuesta como borrador de Gmail",
              "Crea el borrador en Gmail; **no lo envía**.", "guarda",
              (_ID, Campo("body", "Texto del correo.", requerido=True, ejemplo="Hola, ¿podría confirmar…?"),
               Campo("to", "Destinatario; si se omite, el del caso.")), "redirect"),
    Operacion("post", "/send", "Acciones del caso", "⚠ Enviar la respuesta al cliente",
              "**Envía un correo real** desde Gmail y deja el caso esperando. Rechaza repetir el mismo texto "
              "en 15 minutos.", "correo",
              (_ID, Campo("body", "Texto del correo.", requerido=True, ejemplo="Hola, ¿podría confirmar…?"),
               Campo("to", "Destinatario; si se omite, el del caso."),
               Campo("change_id", "Cambio al que responde este correo.", "integer", ejemplo=0),
               Campo("purpose", "Para qué es el correo, si responde a un cambio.",
                     opciones=("ack", "ask_code", "notify_contact", "scope_update"))), "redirect"),
    Operacion("post", "/activities", "Acciones del caso", "Guardar la tabla de actividades",
              "Reemplaza la tabla con las filas enviadas y guarda una versión nueva con lo que cambió.", "guarda",
              (_ID, Campo("reason", "Motivo de la edición (opcional).", opciones=tuple(k for k in EDIT_REASONS if k)),
               Campo("a0_activity", _FILA, ejemplo="Levantamiento topográfico"),
               Campo("a0_quantity", "Cantidad de la fila 0.", ejemplo="544,5"),
               Campo("a0_unit", "Unidad de la fila 0.", ejemplo="ha"),
               Campo("a0_estado", "Estado de la fila 0.", opciones=("confirmado", "pendiente", "contradictorio"))),
              "redirect"),
    Operacion("post", "/review-decision", "Acciones del caso", "Resolver la revisión técnica",
              "Una persona cierra la revisión técnica: información suficiente o preguntar al cliente. No envía nada.",
              "guarda",
              (_ID, Campo("decision", "Qué se decidió.", requerido=True, opciones=("sufficient", "ask_client")),
               Campo("questions", "Preguntas para el cliente, una por línea (si `decision=ask_client`).")),
              "redirect"),
    Operacion("post", "/followup", "Acciones del caso", "Seguimiento cuando el cliente tarda",
              "Activa, apaga, pausa o reanuda los recordatorios automáticos, o anota que ya se habló con el cliente.",
              "guarda",
              (_ID, Campo("action", "Acción de seguimiento.", requerido=True,
                          opciones=tuple(sorted(FOLLOWUP_ACTIONS)))),
              "redirect"),
    Operacion("post", "/code-issue", "Acciones del caso", "⚠ Emitir la T: carpeta en Drive y correo al cliente",
              "Un solo paso: **asigna el código T**, **crea la carpeta del proyecto en Drive** (con los documentos "
              "que envió el cliente y la tabla de actividades) y **envía al cliente el correo** con la T, las "
              "instrucciones y cómo sigue el proceso. El texto de `body` es el que la persona vio y pudo editar. Si la "
              "carpeta o el correo fallan, la T queda emitida y la respuesta dice qué quedó pendiente.", "drive",
              (_ID, Campo("number", "Número de la T (5 dígitos: año y consecutivo).", requerido=True, ejemplo="26145"),
               Campo("body", "Correo que se enviará al cliente.", requerido=True,
                     ejemplo="Hola, recibimos toda la información de su proyecto…"),
               Campo("suggested_number", "El número con el que se preparó el texto; si `number` es otro, el texto se "
                     "ajusta solo.", ejemplo="26145")), "redirect"),
    Operacion("post", "/code", "Acciones del caso", "Asignar el código T (sin enviar nada)",
              "Registra el código de la oferta (5 dígitos: año y consecutivo) y deja preparado el correo, sin "
              "enviarlo ni crear la carpeta. No se puede reutilizar.", "guarda",
              (_ID, Campo("number", "Código de 5 dígitos.", requerido=True, ejemplo="26145")), "redirect"),
    Operacion("post", "/code-approve", "Acciones del caso", "Promover la T a T&E",
              "Marca la oferta como aprobada.", "guarda", (_ID,), "redirect"),
    Operacion("post", "/change", "Acciones del caso", "Decidir si un mensaje pide un cambio",
              "Una persona decide si un mensaje posterior a la T solicita un cambio de alcance.", "guarda",
              (_ID, Campo("change_id", "Id del cambio pendiente.", "integer", True, 1),
               Campo("decision", "`yes`: es un cambio; `no`: no lo es.", requerido=True, opciones=("yes", "no"))),
              "redirect"),
    Operacion("post", "/link", "Acciones del caso", "Confirmar un correo de otro hilo",
              "Una persona confirma o descarta que un correo de otro hilo pertenece al caso.", "guarda",
              (_ID, Campo("link_id", "Id del vínculo pendiente.", "integer", True, 1),
               Campo("decision", "`yes`: es de este caso; `no`: no lo es.", requerido=True, opciones=("yes", "no"))),
              "redirect"),
    Operacion("post", "/proposal", "Acciones del caso", "Resolver la propuesta del modelo",
              "Adoptar la tabla que propone el modelo o conservar la editada por una persona.", "guarda",
              (_ID, Campo("decision", "`use`: adoptar la propuesta; `keep`: mantener la mía.", requerido=True,
                          opciones=("use", "keep"))), "redirect"),
    # ------------------------------------------------------------------ acciones generales
    Operacion("post", "/refresh", "Acciones generales", "⚠ Actualizar correo",
              "Revisa Gmail ahora. En modo ChatGPT Work no lee Gmail: solo informa el estado del puente.",
              "gmail", (), "redirect"),
    Operacion("post", "/google/reautorizar", "Acciones generales", "⚠ Reautorizar Google",
              "Abre en **este equipo** el consentimiento de Google (iniciar sesión y aceptar los permisos de Gmail y "
              "Drive) y espera hasta 5 minutos a que se complete. Responde enseguida; el avance se ve en "
              "`GET /api/autorizacion`. Solo hace falta cuando la autorización venció o fue revocada.",
              "google", (), "redirect"),
    Operacion("post", "/mode", "Acciones generales", "Cambiar el modo de operación",
              "Elige quién lee el correo: Ollama (local), API de OpenAI o ChatGPT Work.", "guarda",
              (Campo("mode", "Modo de operación.", requerido=True, opciones=tuple(sorted(ACTIVE_MODES))),),
              "redirect"),
    Operacion("post", "/rescue", "Acciones generales", "Rescatar un correo descartado",
              "Marca el hilo para que la próxima revisión de Gmail lo analice.", "guarda", (_ID,), "redirect"),
    Operacion("post", "/delete", "Acciones generales", "⚠ Eliminar casos",
              "Elimina del dashboard los casos indicados. El historial se conserva como evidencia.", "elimina",
              (Campo("thread_id", "Ids de los casos a eliminar (repite el campo para varios).", "array", True,
                     ["18f3a9c2d4e5b601"]),), "redirect"),
    Operacion("post", "/notifications/read", "Acciones generales", "Marcar las novedades como revisadas",
              "Marca todas las novedades sin leer.", "guarda", (), "redirect"),
)


def rutas_documentadas() -> tuple[set[str], set[str]]:
    """Las rutas GET y POST que esta documentación cubre (para compararlas con las del servidor)."""
    return ({o.ruta for o in OPERACIONES if o.metodo == "get"}, {o.ruta for o in OPERACIONES if o.metodo == "post"})


def _json_type(annotation: Any) -> dict[str, Any]:
    text = str(annotation)
    if text.startswith("list") or "list[" in text:
        return {"type": "array", "items": {}}
    if "dict" in text:
        return {"type": "object"}
    if "bool" in text:
        return {"type": "boolean"}
    if "int" in text:
        return {"type": "integer"}
    return {"type": "string", "nullable": "None" in text}


def _caso_schema() -> dict[str, Any]:
    """El esquema del caso, tomado de los campos reales de `Caso` para que no se desactualice."""
    hints = get_type_hints(Caso)
    return {"type": "object", "properties": {f.name: _json_type(hints[f.name]) for f in fields(Caso)}}


def _parameter(campo: Campo) -> dict[str, Any]:
    schema: dict[str, Any] = {"type": campo.tipo}
    if campo.opciones:
        schema["enum"] = list(campo.opciones)
    parameter: dict[str, Any] = {"name": campo.nombre, "in": campo.en, "description": campo.descripcion,
                                 "required": campo.requerido or campo.en == "path", "schema": schema}
    if campo.ejemplo is not None:
        parameter["example"] = campo.ejemplo
    return parameter


def _form_property(campo: Campo) -> dict[str, Any]:
    prop: dict[str, Any] = {"type": campo.tipo, "description": campo.descripcion}
    if campo.tipo == "array":
        prop["items"] = {"type": "string"}
    if campo.opciones:
        prop["enum"] = list(campo.opciones)
    if campo.ejemplo is not None:
        prop["example"] = campo.ejemplo
    return prop


_RESPUESTAS_POST = {
    "303": {"description": "Hecho (o explicado): redirige a la pantalla con el resultado en `message`.",
            "headers": {"Location": {"description": "Pantalla de destino con el mensaje.",
                                     "schema": {"type": "string"}}}},
    "403": {"description": "Rechazada: `Host` u `Origin` no permitidos, o `csrf_token` inválido.",
            "content": {"text/html": {"schema": {"type": "string"}}}},
    "404": {"description": "Ruta no existente.", "content": {"text/html": {"schema": {"type": "string"}}}},
    "500": {"description": "Error inesperado; queda registrado.",
            "content": {"text/html": {"schema": {"type": "string"}}}},
}


def _respuestas_get(operacion: Operacion) -> dict[str, Any]:
    if operacion.contenido == "json":
        schema: dict[str, Any] = {"$ref": "#/components/schemas/Caso"} if operacion.ruta.endswith("{thread_id}") \
            else {"type": "object" if operacion.ruta != "/api/casos" else "array"}
        if operacion.ruta == "/api/casos":
            schema = {"type": "array", "items": {"$ref": "#/components/schemas/ResumenDeCaso"}}
        elif operacion.ruta == "/api/salud":
            schema = {"$ref": "#/components/schemas/Salud"}
        media: dict[str, Any] = {"schema": schema}
        if operacion.ejemplo_respuesta is not None:
            media["example"] = operacion.ejemplo_respuesta
        responses: dict[str, Any] = {"200": {"description": "Correcto.", "content": {"application/json": media}}}
        if operacion.ruta == "/api/salud":
            responses["503"] = {"description": "Alguna conexión falla; el cuerpo dice cuál.",
                                "content": {"application/json": media}}
        if "{thread_id}" in operacion.ruta:
            responses["404"] = {"description": "El caso no existe.",
                                "content": {"application/json": {"example": {"error": "La solicitud no existe."}}}}
        return responses
    if operacion.contenido == "xlsx":
        return {"200": {"description": "Archivo Excel.", "content": {
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": {
                "schema": {"type": "string", "format": "binary"}}}},
                "404": {"description": "El caso no existe.", "content": {"text/html": {"schema": {"type": "string"}}}}}
    return {"200": {"description": "Página HTML.", "content": {"text/html": {"schema": {"type": "string"}}}}}


def _operacion(operacion: Operacion, csrf_token: str) -> dict[str, Any]:
    efecto = EFECTOS[operacion.efecto]
    resultado: dict[str, Any] = {
        "tags": [operacion.etiqueta], "summary": operacion.resumen,
        "description": f"{operacion.descripcion}\n\n**Efecto:** {efecto}", "x-efecto": operacion.efecto,
        "operationId": ("get" if operacion.metodo == "get" else "post") + operacion.ruta.replace("/", "_")
        .replace("{", "").replace("}", "").replace(".", "_"),
    }
    if operacion.metodo == "get":
        resultado["parameters"] = [_parameter(c) for c in operacion.campos]
        resultado["responses"] = _respuestas_get(operacion)
        return resultado
    properties = {"csrf_token": {"type": "string", "default": csrf_token, "description":
                                 "Token de seguridad vigente (ya viene relleno). Sin él la petición se rechaza (403)."}}
    properties.update({c.nombre: _form_property(c) for c in operacion.campos})
    required = ["csrf_token"] + [c.nombre for c in operacion.campos if c.requerido]
    media: dict[str, Any] = {"schema": {"type": "object", "properties": properties, "required": required}}
    arrays = {c.nombre: {"style": "form", "explode": True} for c in operacion.campos if c.tipo == "array"}
    if arrays:
        media["encoding"] = arrays
    resultado["requestBody"] = {"required": True, "content": {"application/x-www-form-urlencoded": media}}
    resultado["responses"] = _RESPUESTAS_POST
    return resultado


_DESCRIPCION = """
Dashboard **local** de Anka: páginas HTML y acciones por formulario. Esta página sirve para **probar cada petición**.

### Cómo probar
1. Empieza por **Lectura (JSON)**: `GET /api/salud` dice si todo conecta (200) o qué falla (503) y
   `GET /api/casos` lista los casos.
2. Copia un `thread_id` y pruébalo en `GET /api/casos/{thread_id}` o en `GET /case`.
3. Para una **acción** (`POST`), pulsa *Try it out*: el `csrf_token` ya viene relleno. Completa los campos y *Execute*.
   La respuesta real es `303` hacia la pantalla del caso; el navegador la sigue y Swagger muestra esa página.

### Cuidado
Las operaciones marcadas con **⚠** actúan de verdad: envían correos reales, crean carpetas en Drive o eliminan casos.
Úsalas con casos de prueba.

### Seguridad
Solo se atiende desde la propia máquina (`Host` local). Los `POST` exigen `Origin` del mismo servidor y el `csrf_token`.
"""


def build_spec(csrf_token: str) -> dict[str, Any]:
    """El documento OpenAPI 3 completo; el `csrf_token` vigente se incluye como valor por defecto de los `POST`."""
    paths: dict[str, dict[str, Any]] = {}
    for operacion in OPERACIONES:
        paths.setdefault(operacion.ruta, {})[operacion.metodo] = _operacion(operacion, csrf_token)
    return {
        "openapi": "3.0.3",
        "info": {"title": "Anka · Dashboard local", "version": "1.0.0", "description": _DESCRIPCION.strip()},
        "servers": [{"url": "/", "description": "Este servidor"}],
        "tags": [{"name": nombre, "description": texto} for nombre, texto in TAGS.items()],
        "paths": paths,
        "components": {"schemas": {
            "Caso": _caso_schema(),
            "Salud": {"type": "object", "properties": {
                "estado": {"type": "string", "enum": ["ok", "error"]}, "profundo": {"type": "boolean"},
                "comprobaciones": {"type": "array", "items": {"type": "object", "properties": {
                    "componente": {"type": "string"},
                    "estado": {"type": "string", "enum": ["ok", "error", "no_aplica"]},
                    "detalle": {"type": "string"}, "ms": {"type": "integer"}}}}}},
            "ResumenDeCaso": {"type": "object", "properties": {
                "thread_id": {"type": "string"}, "project_name": {"type": "string"},
                "client_name": {"type": "string"}, "recipient": {"type": "string"},
                "status": {"type": "string"}, "status_label": {"type": "string"},
                "quote_code": {"type": "string"}, "waiting_since": {"type": "string"},
                "updated_at": {"type": "string"}}},
        }},
    }


def docs_page() -> str:
    """La página de Swagger UI (carga el CSS y el JS fijados y con huella de integridad)."""
    css, css_sri = SWAGGER_CSS
    js, js_sri = SWAGGER_JS
    return f"""<!doctype html>
<html lang="es">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>API - Anka</title>
  <link rel="stylesheet" href="{css}" integrity="{css_sri}" crossorigin="anonymous">
  <style>
    body {{ margin: 0; }}
    .topbar-note {{ padding: 10px 20px; background: #126a62; color: #fff; font: 14px sans-serif; }}
    .topbar-note a {{ color: #fff; }}
  </style>
</head>
<body>
  <div class="topbar-note">Anka · API del dashboard local · <a href="/">Volver al dashboard</a>
    · <a href="/openapi.json">openapi.json</a></div>
  <div id="swagger-ui"></div>
  <script src="{js}" integrity="{js_sri}" crossorigin="anonymous"></script>
  <script>
    window.ui = SwaggerUIBundle({{
      url: "/openapi.json", dom_id: "#swagger-ui", deepLinking: true, filter: true,
      docExpansion: "list", defaultModelsExpandDepth: -1, tryItOutEnabled: true,
      displayRequestDuration: true
    }});
  </script>
</body>
</html>
"""
