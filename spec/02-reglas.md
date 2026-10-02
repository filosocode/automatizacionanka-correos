# Reglas operativas (índice)

El texto vigente de cada regla, con su versión y su bloque legible por máquina, está en
[`skills/anka-cotizaciones-correo/references/reglas_operativas.md`](../skills/anka-cotizaciones-correo/references/reglas_operativas.md).
**Allí se edita primero**; este documento solo las lista. Cada regla es un requisito (`R1`… `R13`) con pruebas.

| Id | Tema | En una línea |
|---|---|---|
| R1 | Descarte | Solo se descarta sin analizar el ruido evidente (señales inequívocas de automatización en todos los mensajes). |
| R2 | Adjuntos | Un adjunto ilegible nunca descarta un hilo: va a revisión manual. |
| R3 | Trazabilidad del descarte | Todo descarte guarda su motivo y puede rescatarse. |
| R4 | Estructura de Drive | `{año}/{cliente}/{solicitud}`; no se crea nada si el año no coincide con la carpeta padre. |
| R5 | Cliente | Comparación sin mayúsculas, tildes ni sufijos societarios; varias parecidas ⇒ decide una persona. |
| R6 | Cliente nuevo | Nunca se crea la carpeta de un cliente nuevo sin confirmación de una persona. |
| R7 | Carpeta del proyecto | Solo con un cliente confirmado cuya carpeta ya existe. |
| R8 | Correos | Los correos al cliente son borradores que una persona revisa y envía (única excepción: R13). |
| R9 | Carpeta al enviar | El dashboard crea la carpeta al enviar la respuesta de un caso con información suficiente. |
| R10 | Contenido de la carpeta | Se llena con los documentos del cliente y la hoja de actividades. |
| R11 | Código y cambios | Con T, la tabla solo cambia por el contacto registrado citando el código. |
| R12 | Estado de las actividades | `confirmado`, `pendiente` o `contradictorio`; confirmado solo con todos los datos esenciales. |
| R13 | Recordatorios | Tiempo de espera, llamada a los 7 días hábiles y hasta 2 recordatorios automáticos opcionales. |

## Procedimiento de cambio
Ver [`../docs/ESTANDAR_CAMBIOS.md`](../docs/ESTANDAR_CAMBIOS.md): **requisito/regla ⇒ casos ⇒ código ⇒ skill de Work ⇒
verificación (`check.bat`) ⇒ prueba manual en Work ⇒ pull request**. Si la regla cambia de contenido, se sube la
versión de las reglas (hoy **7**).
