# Contrato con ChatGPT Work

La tarea de ChatGPT Work no ejecuta código de este repositorio: analiza los correos y deja en Gmail un **borrador**
cuyo texto interno lleva un bloque JSON. El puente (`work_dashboard_sync.py`) lo lee y lo importa al dashboard.
Requisito: **T-02**. La definición formal es [`contrato_work.schema.json`](contrato_work.schema.json): se valida en el
puente.

## El borrador

**Desde el prompt V6 (T-41) Work no usa Drive**: lee los adjuntos directamente en Gmail (si el lector del conector no admite el
formato, extrae el original del correo en formato `raw`) y reporta por cada adjunto su nombre, `size_bytes` y, opcional, `read_via`.
El puente compara esa lista con los adjuntos que Gmail tiene en el hilo (nombre y tamaño) y deja la lectura incompleta si falta alguno
o un tamaño difiere; `DRIVE_PREFLIGHT` y los campos `*_segment_*` ya no se exigen. Lo siguiente (T-34) rige solo para V5 y anteriores.

T-34 añade un canal explícito Gmail → extracción Python → Drive: el preparador publica `anka-work-drive-v1`
con originales, segmentos completos y manifiesto vigente por hilo. Work lo consulta usando su conexión Drive
y verifica la vigencia contra Gmail. La preparación es optativa; no cambia los estados comerciales ni el esquema
del borrador. Work registra revisión/segmentos leídos en reason/reviewed_sections de cada adjunto. La edición local
de la skill no configura por sí sola la tarea activa. Véase [guía](../docs/WORK_DRIVE_OCR.md).
```
[REVISAR ANTES DE ENVIAR - BORRAR ESTA SECCION]   ← el puente tolera tildes, mayúsculas y espacios (T-43)
… correo original, resumen, actividades y preguntas (para la persona) …
[ANKA_DASHBOARD_JSON_V1]
{ … el JSON del contrato … }
[/ANKA_DASHBOARD_JSON_V1]
Hola, … (el correo limpio para el cliente)
```
- El bloque debe estar **una sola vez** y dentro de la sección interna inicial; nunca se copia desde correos citados.
- Lo que sigue al bloque es el **correo para el cliente**: el dashboard lo muestra en «Respuesta al cliente» y, al
  enviar, **usa ese mismo borrador** (así no queda uno huérfano con la sección interna).
- Si ese texto trae el mensaje anterior citado, el dashboard lo quita **al importar el borrador de Work** (y al
  mostrar uno ya guardado) y propone solo la respuesta nueva. Desde que la persona lo edita, ese texto es suyo: se
  guarda y se envía tal cual, sin recortarlo (U-39, D-104). La
  conversación completa permanece en el historial del caso, y el correo conserva las cabeceras que lo unen al hilo
  original de Gmail.

## Campos

T-35: el puente conserva `DRIVE_PREFLIGHT`, `reading_audit`, `related_threads`,
`analysis_complete` y `applicability`. La lectura y la aplicabilidad comercial se muestran por separado.
Un documento simulado debe leerse; la simulación no prueba lectura incompleta ni autoriza su uso comercial.
Los conteos no certifican la interpretación visual. IDs repetidos o diferentes y segmentos pendientes
impiden declarar lectura completa. Un registro bloqueado permanece visible, sin considerarse análisis final.
Para PRECISO_V4.5_DRIVE y posteriores se verifica el mensaje origen en Gmail antes de importar;
el orden se basa en la recepción del mensaje origen, no en la edición del borrador. Para el mismo
mensaje, un registro incompleto no reemplaza una revisión final de la misma versión del prompt.
El preparador publica qué mensajes están listos; esto no ejecuta ni reintenta por sí solo la tarea remota.
Obligatorios: `thread_id`, `message_id`, `client_email`, `project_name`, `status` y `engine` (= `chatgpt_work`).
`status` es uno de los diez valores del esquema, tal cual. El puente acepta además unos pocos sinónimos evidentes que llevan a un
estado conservador (T-33), dejando un aviso en el caso; cualquier otro valor se rechaza.
Opcionales y solo internos (T-32): `model` y `skill_version`, el modelo que declara Work y la versión de su prompt; quedan
en el caso y en el historial, nunca en un correo al cliente.
Recomendados: `client_name`, `summary`, `received_at`, `gmail_url`, `activities`, `fields`, `missing_data`,
`attachments`, `contradictions`, `scope_dependencies`. `fields` contiene objetos `name`, `value` y `source`.
`missing_data` acepta textos por compatibilidad y objetos `{ "name": "dato pendiente", "type": "critical"|"recommended" }`;
el importador los normaliza a etiquetas legibles para el dashboard.
Cada `activities[].fuente` y `fields[].source` debe identificar el correo o archivo y, para adjuntos, el localizador
concreto. Cada adjunto puede incluir `evidence`: objetos `topic`, `value`, `location` y `excerpt` con hallazgos
explícitos y citas breves. Work debe revisar cada archivo por separado y contrastarlo con el correo y los demás
archivos. KML/KMZ, GeoJSON y GPX pueden registrar geometrías, coordenadas y extensiones. Las longitudes de rutas explícitas se pueden calcular como aproximaciones y deben rotularse como cálculos, nunca como cantidades declaradas o aprobadas para cotizar. No derivar áreas, precisión ni sistema de referencia. Una lectura parcial o no disponible debe indicar su límite.
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
vacío (la pregunta queda con la advertencia correspondiente). Qué se tolera y qué se rechaza está en «Tolerar y avisar»
(T-28, D-106).
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
`status` es una de las etiquetas oficiales del esquema; **se acepta sin tildes ni mayúsculas** y se normaliza. El estado
de cada actividad sigue R12.

## Qué hace el dashboard con el JSON
- Mapea `status` al estado interno (`transiciones.json`, filas `work_import`).
- Aplica R11: con T asignada, la tabla solo se actualiza si lo escribió el contacto registrado y cita el código;
  si no, lo propuesto queda como propuesta pendiente.
- Normaliza el estado de las filas (R12) y guarda el correo limpio como borrador propuesto.
- Con el prompt V6 verifica la lectura contra los adjuntos de Gmail por nombre y tamaño (T-41); con V5 y anteriores, contra el manifiesto
  del preparador (T-37): revisión, mensajes, conteos y IDs esperados y
  pendientes salen del manifiesto; de Work solo se toma qué segmentos dice haber leído. Lo que no se pueda verificar deja
  `analysis_complete` en falso y el caso en revisión técnica interna.
- Si el borrador no se puede usar (ver «Tolerar y avisar») y el hilo aún no tiene análisis, crea un caso mínimo con remitente, asunto
  y fecha del correo (T-36); un borrador válido posterior lo reemplaza y limpia el aviso. Un borrador sin bloque JSON se ignora si el hilo ya tiene análisis válido o si tiene
  menos de 10 minutos (T-36, D-69); un JSON roto se rechaza de inmediato.

## Tolerar y avisar (T-28, D-106)
Solo existe en modo `chatgpt_work`: en `local` y `api` no hay borrador de Work que rechazar (la salida del modelo se valida en
el piloto), así que este aviso no aparece allí. Los tres modos comparten el mismo editor de tabla.

**Se rechaza** (el análisis no se importa; el hilo sin análisis recibe un caso mínimo, T-36) solo cuando:
1. falta la identidad: `thread_id`, `message_id` o `client_email` ausentes, en blanco o que no son texto; o el `thread_id` no es el
   de la conversación del borrador, o el mensaje origen no coincide con el hilo o el remitente (verificación en Gmail);
2. `status` es desconocido después de normalizar tildes, mayúsculas, espacios y puntuación de los extremos (T-33);
3. el JSON es irrecuperable. Solo se reparan cosas mecánicas: cercas ```` ```json ````, texto suelto antes o después de las llaves,
   comas sobrantes y saltos de línea dentro de un texto. No se reparan comillas ni cierres que faltan;
4. el borrador no trae el bloque, el bloque está fuera de la sección interna inicial o hay bloques repetidos o incompletos.

**Todo lo demás se acepta con aviso.** El caso muestra «Work escribió algo distinto de lo esperado; se corrigió solo: …» con la ruta
del campo (p. ej. `activities[2].unit`) y qué se hizo. Nunca se inventa un dato:
- `no indicado` u otro texto donde va un número, un booleano o un enumerado se trata como desconocido: el campo se omite (o queda
  vacío) y el análisis sigue. Un número, booleano, lista u objeto donde va texto se convierte si es un número o se omite si hacerlo
  sería adivinar. Si el cambio altera una fila de `activities`, la fila queda `pendiente` (sin confirmar), nunca `confirmado`.
- Enumerados con mayúsculas, punto final o en español se llevan al valor permitido con un mapa explícito
  (`SINONIMOS_DE_ENUMERADOS` en `anka/dominio/tolerancia_work.py`): `explicito`→`explicit`, `parcial`→`partial`, `ausente`→`absent`,
  `ilegible`/`no legible`→`unreadable`, `contradictorio`→`contradictory`, `sin confirmar`→`unconfirmed`; `cliente`→`client`,
  `contratista`→`contractor`, `desconocido`→`unknown`; `preguntar`→`ask_client`, `revisión interna`→`internal_review`,
  `no preguntar`→`do_not_ask`; `adjunto`→`attachment`, `raw`→`raw_mime`. Lo que no coincide queda desconocido.
- Una lista escrita como texto es una lista de un elemento (`cited_codes`, `sources`, `reviewed_sections`, `reviewed_sources`…).
- `size_bytes` como texto (`"2048"`) o decimal entero (`2048.0`) es un entero; un decimal con fracción o un texto no numérico se
  omite y el adjunto queda sin verificar (T-41).
- `evidence[]` sin `excerpt` o `location` se conserva con esos campos vacíos; `fields[]` sin `source` queda con `sin fuente`;
  `evidence_register[]` sin `status`, `owner` o `blocking` queda `unconfirmed`, `unknown` y `false`. Una fila a la que le falta algo
  que no se puede dejar vacío (p. ej. `evidence_register[].id`, `question_audit[].question`) se omite y el aviso lo dice.
- `project_name` ausente es «Solicitud sin nombre»; `engine` distinto de `chatgpt_work` se toma como `chatgpt_work`.
- `quantity` y `unit` numéricos se guardan como texto.
- Un borrador HTML conserva las etiquetas que no son de formato (el `<Polygon>` de un KML citado) y convierte las entidades
  (`&lt;`); dentro del bloque solo se quitan las de formato simple (`br`, `p`, `div`, `span`, `pre`, `b`, `i`…).

**Qué se guarda de un rechazo:** el motivo en lenguaje claro, la causa, los nombres de campo y la fecha. Nunca el contenido del cliente
ni el JSON (T-36). Se cuenta por día (`work_rejections`, solo motivo, causa y campos); con más de 3 en un día la «Revisión de correos»
dice «Work está mandando análisis con errores: N hoy. Pide revisar el prompt.» (U-41).

## Cambiar el contrato
1. Edita el esquema (y este documento si cambia el sentido).
2. Actualiza `docs/INSTRUCCIONES.txt` (lo que se pega en la tarea de ChatGPT): `tests/test_contrato_work.py` falla si un campo del
   esquema no está documentado ahí.
3. Actualiza la skill de Work (`SKILL.md`) y revisa el piloto manual (`docs/PILOTO_ACEPTACION.md`).
