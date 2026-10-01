# Reglas operativas de la Fase 1 (fuente única)

Estas reglas valen **igual en los tres modos** de operación:

| Modo | Quién las aplica |
|---|---|
| `local` (Ollama) | El código Python (`reception.py`, `drive_layout.py`, `pipeline.py`) |
| `api` (OpenAI) | El mismo código Python; solo cambia el motor que analiza el correo |
| `chatgpt_work` | La skill `anka-cotizaciones-correo` (`SKILL.md`), que copia estas reglas |

Si una regla cambia, se cambia **aquí primero**, se sube la versión y se actualizan el código y la skill. Las pruebas `test_paridad_modos.py` fallan si alguno de los tres se separa. El procedimiento completo está en `ESTANDAR_CAMBIOS.md`.

**Versión de las reglas: 1**

## Reglas

| Id | Regla |
|---|---|
| R1 | Se descarta sin analizar **solo ruido evidente**: todos los mensajes del cliente en el hilo deben tener una señal inequívoca de automatización (remitente no-reply, `List-Unsubscribe`, `Auto-Submitted`, `Precedence` masivo, spam de Gmail). Un remitente humano nunca se descarta por esta vía, aunque Gmail lo ponga en Promociones. Un aviso automático que mencione una cotización sí se analiza. |
| R2 | Un adjunto ilegible nunca descarta un hilo: si el texto no parece una solicitud pero hay adjuntos que no se pudieron leer, el caso va a revisión manual. |
| R3 | Todo descarte guarda su motivo y puede revisarse y rescatarse por una persona. |
| R4 | La estructura de Drive es `{año}/{cliente}/{solicitud}`. La carpeta padre configurada es la del año vigente. No se crean carpetas si la solicitud es de un año distinto al de la carpeta padre. |
| R5 | Los clientes se comparan sin importar mayúsculas, tildes ni sufijos societarios al final (S.A.S., Ltda., S.A., Inc., Corp., etc.). Si exactamente una carpeta de cliente coincide, se reutiliza; si hay varias parecidas, decide una persona. |
| R6 | Nunca se crea una carpeta de cliente nueva sin confirmación de una persona. |
| R7 | La carpeta del proyecto solo se crea sola con un cliente confirmado (por una persona, en este hilo o en otro hilo del mismo remitente) cuya carpeta ya existe. Si no, el caso queda esperando aprobación. |
| R8 | Los correos al cliente siempre son borradores con revisión humana, hasta que la medición con correos reales justifique otra cosa. |

## Bloque legible por máquina

Las pruebas leen este bloque y lo comparan con el código y con `SKILL.md`. No lo edites sin actualizar ambos.

```json reglas
{
  "version": 1,
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
  "frases_en_skill": {
    "R1": "solo ruido evidente",
    "R2": "un adjunto ilegible nunca descarta",
    "R3": "todo descarte guarda su motivo",
    "R4": "{año}/{cliente}/{solicitud}",
    "R5": "sin importar mayúsculas, tildes ni sufijos societarios",
    "R6": "nunca crees una carpeta de cliente nueva sin confirmación",
    "R7": "la carpeta del proyecto solo se crea con un cliente confirmado",
    "R8": "los correos al cliente siempre son borradores"
  }
}
```
