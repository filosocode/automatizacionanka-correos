# Reglas operativas de la Fase 1 (fuente única)

Estas reglas valen **igual en los tres modos** de operación:

| Modo | Quién las aplica |
|---|---|
| `local` (Ollama) | El código Python (`reception.py`, `drive_layout.py`, `pipeline.py`) |
| `api` (OpenAI) | El mismo código Python; solo cambia el motor que analiza el correo |
| `chatgpt_work` | La skill `anka-cotizaciones-correo` (`SKILL.md`), que copia estas reglas |

Si una regla cambia, se cambia **aquí primero**, se sube la versión y se actualizan el código y la skill. Las pruebas `test_paridad_modos.py` fallan si alguno de los tres se separa. El procedimiento completo está en `ESTANDAR_CAMBIOS.md`.

**Versión de las reglas: 6**

## Reglas

| Id | Regla |
|---|---|
| R1 | Se descarta sin analizar **solo ruido evidente**: todos los mensajes del cliente en el hilo deben tener una señal inequívoca de automatización (remitente no-reply, `List-Unsubscribe`, `Auto-Submitted`, `Precedence` masivo, spam de Gmail). Un remitente humano nunca se descarta por esta vía, aunque Gmail lo ponga en Promociones. Un aviso automático que mencione una cotización sí se analiza. |
| R2 | Un adjunto ilegible nunca descarta un hilo: si el texto no parece una solicitud pero hay adjuntos que no se pudieron leer, el caso va a revisión manual. |
| R3 | Todo descarte guarda su motivo y puede revisarse y rescatarse por una persona. |
| R4 | La estructura de Drive es `{año}/{cliente}/{solicitud}`. La carpeta padre configurada es la del año vigente. No se crean carpetas si la solicitud es de un año distinto al de la carpeta padre. |
| R5 | Los clientes se comparan sin importar mayúsculas, tildes ni sufijos societarios al final (S.A.S., Ltda., S.A., Inc., Corp., etc.). Si exactamente una carpeta de cliente coincide, se reutiliza; si hay varias parecidas, decide una persona. |
| R6 | Nunca se crea una carpeta de cliente nueva sin confirmación de una persona (la única excepción es R9). |
| R7 | La carpeta del proyecto solo se crea sola con un cliente confirmado (por una persona, en este hilo o en otro hilo del mismo remitente) cuya carpeta ya existe. Si no, el caso queda esperando aprobación. |
| R8 | Los correos al cliente siempre son borradores con revisión humana, hasta que la medición con correos reales justifique otra cosa. |
| R9 | Cuando una persona envía desde el dashboard la respuesta de un caso con información suficiente, la carpeta del proyecto se crea sola con el cliente que ya tiene el caso (el confirmado por una persona o el del mismo remitente). Si ese cliente no tiene carpeta y no existe ninguna igual ni parecida, se crea también la carpeta del cliente. Si hay una carpeta de cliente parecida, o una solicitud del cliente con nombre parecido o con el mismo archivo adjunto, no se crea nada y decide una persona. Esto lo hace el dashboard (Python) igual en los tres modos; ChatGPT Work nunca crea carpetas. |
| R10 | Al crear la carpeta del proyecto se llena con el contenido de la plantilla de Drive: toda la documentación que envió el cliente (PDF, Word, Excel, KMZ, KML, DWG, shapefile, imágenes y demás adjuntos; solo se omiten los logos incrustados en firmas) y una hoja de Google con la tabla de actividades. No se pisa ningún archivo existente y un fallo al copiar no deshace la carpeta. Lo hace el código Python (dashboard y piloto); ChatGPT Work no crea ni llena carpetas. El PDF de alcance generado con el resumen queda pendiente. |
| R11 | Antes de la T el cliente puede escribir sin código. Cuando una persona acepta el proyecto en el dashboard se asigna la **T** (`T` + año de 2 cifras + consecutivo de 3, p. ej. `T26145`; al aprobarse pasa a `T&E26145`, mismo número); ni Work ni Python generan ni inventan el código. Desde la T, la tabla de actividades **solo se actualiza con un mensaje posterior a la T** que venga del **contacto registrado** (quien escribió primero) **y** cite el código del caso. Un mensaje de otra persona, de otro dominio o sin el código **no modifica la tabla**: lo que propone queda como propuesta pendiente y el dashboard avisa. Quien analiza informa `requested_by_email`, `requested_by_name` y `cited_codes`; el dashboard valida. Una edición del comercial en el dashboard no pide validación ni envía correos: solo deja historial. Los correos a quien no es el contacto registrado nunca incluyen el código. |
| R12 | El estado de cada actividad es exactamente `confirmado`, `pendiente` o `contradictorio`, sin texto añadido. Solo es `confirmado` si todos los datos esenciales de esa fila (ubicación, magnitud y unidad, entregable, plazo o restricción y, cuando aplique, método y precisión) están explícitos en el correo o los adjuntos; si falta alguno es `pendiente` y en `notes` se anota qué falta. `contradictorio` cuando las fuentes discrepan. El dashboard normaliza cualquier otro texto a uno de los tres valores. |

## Bloque legible por máquina

Las pruebas leen este bloque y lo comparan con el código y con `SKILL.md`. No lo edites sin actualizar ambos.

```json reglas
{
  "version": 6,
  "descarte": {
    "senales": ["remitente_no_reply", "list_unsubscribe", "auto_submitted", "precedence_masivo", "spam_gmail"],
    "requiere_todos_los_mensajes": true,
    "remitente_humano_nunca": true
  },
  "drive": {
    "ruta": "{año}/{cliente}/{solicitud}",
    "sufijos_societarios": ["s a s", "s a", "s l", "e u", "sas", "ltda", "sa", "sl", "eu", "inc", "corp", "llc", "cia"],
    "crear_cliente_automatico": false,
    "bloquear_si_anio_distinto": true,
    "proyecto_requiere_cliente_confirmado": true
  },
  "correo_al_cliente": {"envio_automatico": false},
  "actividades": {"estados": ["confirmado", "pendiente", "contradictorio"]},
  "cambios": {
    "codigo": "T + año de 2 cifras + consecutivo de 3",
    "se_exige_desde": "asignacion_de_la_t",
    "tabla_solo_contacto_con_codigo": true,
    "correo_a_no_contacto_revela_codigo": false
  },
  "frases_en_skill": {
    "R1": "solo ruido evidente",
    "R2": "un adjunto ilegible nunca descarta",
    "R3": "todo descarte guarda su motivo",
    "R4": "{año}/{cliente}/{solicitud}",
    "R5": "sin importar mayúsculas, tildes ni sufijos societarios",
    "R6": "nunca crees una carpeta de cliente nueva sin confirmación",
    "R7": "la carpeta del proyecto solo se crea con un cliente confirmado",
    "R8": "los correos al cliente siempre son borradores",
    "R9": "al enviar la respuesta desde el dashboard",
    "R10": "se llena con el contenido de la plantilla de Drive",
    "R11": "solo se actualiza con un mensaje posterior a la T",
    "R12": "solo es confirmado si todos los datos esenciales"
  }
}
```
