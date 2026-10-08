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
`missing_data` acepta textos por compatibilidad y objetos `{ "name": "dato pendiente", "type": "critical"|"recommended" }`;
el importador los normaliza a etiquetas legibles para el dashboard.
Cada `activities[].fuente` y `fields[].source` debe identificar el correo o archivo y, para adjuntos, el localizador
concreto. Cada adjunto puede incluir `evidence`: objetos `topic`, `value`, `location` y `excerpt` con hallazgos
explÃ­citos y citas breves. Work debe revisar cada archivo por separado y contrastarlo con el correo y los demÃ¡s
archivos. KML/KMZ, GeoJSON y GPX pueden registrar geometrías, coordenadas y extensiones. Las longitudes de rutas explícitas se pueden calcular como aproximaciones y deben rotularse como cálculos, nunca como cantidades declaradas o aprobadas para cotizar. No derivar áreas, precisión ni sistema de referencia. Una lectura parcial o no disponible debe indicar su lÃ­mite.
Control de cambios (R11): `requested_by_email`, `requested_by_name`,
`attachments[].coverage` indica `completa`, `parcial` o `no_legible`; `reviewed_sections` enumera las partes inspeccionadas.
`activities[].notes` debe ser accionable: para un faltante, indica qué dato falta, qué fuente se revisó y el siguiente paso; para una contradicción, enumera valores y fuentes con localizadores, aclara qué parámetro está en conflicto, qué decisión falta y qué rol debe resolverla. No inventa responsables nominales ni traslada al cliente una decisión técnica interna.
Antes de preguntar, Work cruza cada faltante con el correo y todos los adjuntos, incluidos tablas, notas, figuras e
imágenes. `question_audit` registra la pregunta, por qué sigue abierta y las fuentes revisadas; si alguna fuente ya
la responde, la pregunta se elimina.
Para los tipos, estados y reglas de advertencia del registro de evidencia, ver la sección «Control de preguntas
justificadas por evidencia» más abajo.
Además de los campos indicados, el contrato admite `evidence_register`: cada hallazgo tiene `id`, `topic`, `status`
(`explicit`, `partial`, `absent`, `unreadable`, `contradictory`, `unconfirmed`), `value`, `source`, `location`,
`owner` (`client`, `anka`, `contractor`, `unknown`) y `blocking`. `question_audit` conserva sus campos base
`question`, `reason_missing`, `reviewed_sources` y opcionalmente `question_id`, `target_finding_ids`, `disposition`
(`ask_client`, `internal_review`, `do_not_ask`) y `unresolved`. En el contrato v2, las preguntas a cliente deben
apuntar a hallazgos pendientes; el puente informa referencias inexistentes/duplicadas, falta de objetivo bloqueante,
preguntas sobre hallazgos explícitos y traslado de límites de lectura al cliente. Son advertencias para revisión
humana y no bloquean la importación. Payloads anteriores sin `evidence_register` siguen siendo válidos.
También avisa si dos preguntas al cliente son duplicadas por texto normalizado o apuntan al mismo hallazgo y
subdato pendiente; si una pregunta mezcla hallazgos explícitos y faltantes, pide separar el dato ya confirmado.
`unresolved` debe ser un texto con el subdato exacto que falta; si Work lo escribe como booleano el puente lo trata como
vacío (la pregunta queda con la advertencia correspondiente). Un borrador que incumple el contrato se rechaza una sola vez
mientras no cambie y se lista en el Resumen (T-28).
El puente agrega `draft_reply` y `draft_id`. Los valores `null` en campos opcionales se tratan como
ausentes; los campos desconocidos se toleran.

`activities[].quantity` admite texto o número JSON; el importador normaliza las cantidades numéricas a texto antes
de guardarlas en el dashboard.

La cobertura se declara según el contenido realmente accesible en la tarea de ChatGPT Work. Work no ejecuta los
extractores Python del modo local/API ni recibe automáticamente sus resultados; por eso no se presupone que pueda abrir o interpretar todos los formatos que
el extractor local admite. En local/API, los extractores sí se ejecutan al descargar el adjunto; en Work, solo se usan si una integración explícita comparte su informe. La tarea y la skill detallan una inspección por tipo de archivo y exigen revisión interna
para componentes que Gmail/conector no exponga, páginas u hojas no revisadas, OCR dudoso, diagramas sin interpretar,
KMZ cuyo KML interno no se pudo inspeccionar, planes de vuelo no decodificados y CAD. No se solicita al cliente
reenviar archivos solo para compensar una limitación del conector sin que una persona lo determine.

Como puente manual asistido, `python app.py prepare-work-packet` crea localmente `manifest.json`, `LEEME.md` y archivos
de texto segmentados por adjunto. Una persona adjunta esos archivos a la tarea de Work correspondiente; no hay
transferencia automática Gmail→Work ni subida automática del paquete. Work debe asociar `filename`/`source_id` con los
adjuntos del hilo, usar solo los segmentos del mismo caso, citar los localizadores conservados y respetar
`coverage`, `reason` y `reviewed_sections`. Cada texto es una extracción, no evidencia de que tablas/figuras se
interpretaron correctamente. El SHA-256 identifica los bytes procesados; Work no afirmará haberlo cotejado con el
original salvo que la herramienta disponible permita verificarlo. Los límites por tamaño, páginas, celdas y archivos comprimidos se registran; toda
lectura parcial/no legible mantiene revisión interna.

## Tipos de campos adicionales
`activities[].quantity` admite texto o número JSON; el importador normaliza cantidades numéricas a texto antes de
guardarlas en el dashboard.

## Control de preguntas justificadas por evidencia
`evidence_register` y los campos extendidos de `question_audit` son opcionales para conservar compatibilidad con
borradores ya existentes. Si `evidence_register` está presente, el puente revisa consistencia mecánica, sin pretender
determinar semánticamente si una pregunta es apropiada: los ids deben ser únicos, las referencias deben existir y
las preguntas con `disposition=ask_client` deben tener un hallazgo objetivo pendiente, bloqueante y atribuible al
cliente o contratista. Un hallazgo `unreadable` debe permanecer en revisión interna. Las advertencias aparecen en el
caso para que la persona revise el análisis y edite el borrador antes de enviarlo; la herramienta nunca lo envía por
esta validación.

`activities[].quantity` admite texto o número JSON; el importador normaliza cantidades numéricas a texto antes de
guardarlas en el dashboard.

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
