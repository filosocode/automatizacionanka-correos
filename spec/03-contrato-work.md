# Contrato con ChatGPT Work

La tarea de ChatGPT Work no ejecuta cÃ³digo de este repositorio: analiza los correos y deja en Gmail un **borrador**
cuyo texto interno lleva un bloque JSON. El puente (`work_dashboard_sync.py`) lo lee y lo importa al dashboard.
Requisito: **T-02**. La definiciÃ³n formal es [`contrato_work.schema.json`](contrato_work.schema.json): se valida en el
puente.

## El borrador
```
[REVISAR ANTES DE ENVIAR - BORRAR ESTA SECCION]
â€¦ correo original, resumen, actividades y preguntas (para la persona) â€¦
[ANKA_DASHBOARD_JSON_V1]
{ â€¦ el JSON del contrato â€¦ }
[/ANKA_DASHBOARD_JSON_V1]
Hola, â€¦ (el correo limpio para el cliente)
```
- El bloque debe estar **una sola vez** y dentro de la secciÃ³n interna inicial; nunca se copia desde correos citados.
- Lo que sigue al bloque es el **correo para el cliente**: el dashboard lo muestra en Â«Respuesta al clienteÂ» y, al
  enviar, **usa ese mismo borrador** (asÃ­ no queda uno huÃ©rfano con la secciÃ³n interna).
- Si ese texto trae el mensaje anterior citado, el dashboard muestra, guarda y envÃ­a solo la respuesta nueva. La
  conversaciÃ³n completa permanece en el historial del caso, y el correo conserva las cabeceras que lo unen al hilo
  original de Gmail.

## Campos
Obligatorios: `thread_id`, `message_id`, `client_email`, `project_name`, `status` y `engine` (= `chatgpt_work`).
Recomendados: `client_name`, `summary`, `received_at`, `gmail_url`, `activities`, `fields`, `missing_data`,
`attachments`, `contradictions`, `scope_dependencies`. `fields` contiene objetos `name`, `value` y `source`.
Cada `activities[].fuente` y `fields[].source` debe identificar el correo o archivo y, para adjuntos, el localizador
concreto. Cada adjunto puede incluir `evidence`: objetos `topic`, `value`, `location` y `excerpt` con hallazgos
explÃ­citos y citas breves. Work debe revisar cada archivo por separado y contrastarlo con el correo y los demÃ¡s
archivos. KML/KMZ, GeoJSON y GPX pueden registrar geometrías, coordenadas y extensiones. Las longitudes de rutas explícitas se pueden calcular como aproximaciones y deben rotularse como cálculos, nunca como cantidades declaradas o aprobadas para cotizar. No derivar áreas, precisión ni sistema de referencia. Una lectura parcial o no disponible debe indicar su lÃ­mite.
Control de cambios (R11): `requested_by_email`, `requested_by_name`,
`attachments[].coverage` indica `completa`, `parcial` o `no_legible`; `reviewed_sections` enumera las partes inspeccionadas.
Antes de preguntar, Work cruza cada faltante con el correo y todos los adjuntos, incluidos tablas, notas, figuras e
imágenes. `question_audit` registra la pregunta, por qué sigue abierta y las fuentes revisadas; si alguna fuente ya
la responde, la pregunta se elimina.
`cited_codes`. El puente agrega `draft_reply` y `draft_id`. Los valores `null` en campos opcionales se tratan como
ausentes; los campos desconocidos se toleran.

## Estado
`status` es una de las etiquetas oficiales del esquema; **se acepta sin tildes ni mayÃºsculas** y se normaliza. El estado
de cada actividad sigue R12.

## QuÃ© hace el dashboard con el JSON
- Mapea `status` al estado interno (`transiciones.json`, filas `work_import`).
- Aplica R11: con T asignada, la tabla solo se actualiza si lo escribiÃ³ el contacto registrado y cita el cÃ³digo;
  si no, lo propuesto queda como propuesta pendiente.
- Normaliza el estado de las filas (R12) y guarda el correo limpio como borrador propuesto.

## Cambiar el contrato
1. Edita el esquema (y este documento si cambia el sentido).
2. Actualiza `docs/INSTRUCCIONES.txt` (lo que se pega en la tarea de ChatGPT): `tests/test_contrato_work.py` falla si un campo del
   esquema no estÃ¡ documentado ahÃ­.
3. Actualiza la skill de Work (`SKILL.md`) y revisa el piloto manual (`docs/PILOTO_ACEPTACION.md`).
