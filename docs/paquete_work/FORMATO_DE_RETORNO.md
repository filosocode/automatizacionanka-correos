# Formato del borrador de retorno

Crea **un solo** borrador en el hilo del mensaje del trabajo, sin enviarlo y una sola vez, ya completo. Estructura exacta:

1. Primera línea, tal cual (sin tilde en SECCION):
   `[REVISAR ANTES DE ENVIAR - BORRAR ESTA SECCION]`
2. La sección interna que describe `PRECISO_V6.1.2` (resumen, tabla, críticos, cobertura, contradicciones…). Para `NO_SOLICITUD` y
   `REVISION_MANUAL` basta con un resumen corto del motivo y, si aplica, la línea `BORRADOR INTERNO — NO ENVIAR`.
3. El bloque, con los marcadores en líneas propias y **un solo JSON válido** entre ellos (sin Markdown ni comentarios):

```
[ANKA_DASHBOARD_JSON_V1]
{ ...JSON... }
[/ANKA_DASHBOARD_JSON_V1]
```

4. Después del bloque, solo el correo limpio para el cliente cuando haya preguntas aprobadas. En `NO_SOLICITUD` y `REVISION_MANUAL`:
   nada para el cliente.

## Reglas que Python comprueba (no las rompas)
- `thread_id` y `message_id` son **exactamente** los de la hoja del trabajo (el `message_id` es el del mensaje del cliente, nunca el del borrador).
- `engine` = `chatgpt_work` y `skill_version` = `PRECISO_V6.1.2`.
- `status` es una de las diez etiquetas oficiales del esquema.
- `classification` (opcional pero pedido en esta cola) es una de las cuatro clases; `classification_reason` es texto.
- No escribas `draft_id` ni `draft_reply`: los agrega el puente.
- Un solo bloque JSON por borrador y un solo borrador por mensaje. Antes de crear uno, busca si ya existe un borrador tuyo con ese
  `message_id`; si existe, no crees otro.

## Ejemplos
`ejemplo_SOLICITUD_NUEVA.json`, `ejemplo_SEGUIMIENTO.json`, `ejemplo_NO_SOLICITUD.json`, `ejemplo_REVISION_MANUAL.json` y
`ejemplo_SOLICITUD_NUEVA_REVISION_INTERNA.json` (caso de prueba que conserva su tabla) son bloques válidos con datos ficticios. Copia la **forma**, nunca los datos ni los ids.
