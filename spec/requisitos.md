# Requisitos

Cada requisito tiene un identificador estable. Las reglas operativas **R1 a R13** viven en
`skills/anka-cotizaciones-correo/references/reglas_operativas.md` y cuentan como requisitos con su propio id.
`check_spec.py` comprueba que **todo requisito tenga al menos una prueba** (etiqueta `Cubre:` en los
`test_*.py`) y que **toda etiqueta apunte a un requisito que existe**.

Para añadir un requisito: agrega aquí un encabezado `### ID · Título` con su enunciado, escribe la prueba y
etiquétala con `# Cubre: ID`. Para quitarlo: bórralo aquí y de las etiquetas, y anótalo en `05-decisiones.md`.

## Fase 1 (los 7 pasos)

### F1-01 · Leer correos
El sistema lee los correos de la cuenta comercial y trae cada solicitud al dashboard. En modo Work los lee
ChatGPT y el puente importa sus borradores (solo lectura de Gmail); en local y api los lee el piloto de Python.

### F1-02 · Identificar solicitudes de cotización
Distingue una solicitud de cotización de lo que no lo es. Descarta sin analizar solo ruido evidente y guarda el
motivo para poder rescatarlo (R1 a R3); ante la duda, analiza.

### F1-03 · Tabla de actividades
Resume cada solicitud en una tabla de actividades que una persona puede editar y exportar a Excel. La unidad se escribe de una sola forma (ha, km, m, km², m²) aunque el correo diga «hectáreas», «Ha» o «Km».
La cantidad de una actividad es una cifra y su unidad va aparte: si el análisis escribe en la cantidad una unidad sin cifra («Ha»), una etiqueta o
un valor en dinero (la Fase 1 no cotiza), la cantidad queda «no indicado», la fila queda pendiente y se anota por qué; la unidad que
venía en su lugar se conserva y un valor en dinero no se guarda en ninguna parte. Se conserva la
versión del modelo y cada edición queda como versión nueva, con sus diferencias y un motivo opcional.
Los hechos extraídos de adjuntos conservan el nombre del archivo y un localizador verificable (página, párrafo,
tabla/fila, hoja/celda o elemento KML/KMZ); los archivos o secciones no leídos se identifican como parciales o
pendientes de revisión; el dashboard muestra los adjuntos y sus revisiones/tareas pendientes antes de la tabla de actividades, sin esconderlos dentro de trazabilidad. Las notas de filas `pendiente` o `contradictorio` explican el dato faltante o los parámetros discrepantes, citan fuentes/localizadores y dan el siguiente paso y rol responsable sin inventar nombres. Se extraen formatos de texto, PDF, DOC/DOCX, XLS/XLSX/XLSM, PPT/PPTX, ODT/ODS/ODP, RTF, HTML, imágenes, KMZ/KML, GeoJSON, GPX, planes de vuelo QGroundControl (.plan/.waypoints), DJI WPML en KMZ y documentos compatibles dentro de ZIP. El OCR local cubre imágenes sueltas, páginas PDF escaneadas y recursos de imagen incrustados; baja confianza, gráficos estructurados omitidos, límites de cobertura y archivos no convertibles se marcan para revisión. Shapefiles se leen cuando se entregan con sus archivos laterales dentro de ZIP; si falta `.prj`, se declara la referencia espacial no indicada y la lectura queda parcial. DWG y DXF permanecen en revisión manual.
Al modelo (local o API) le llega el texto de todo adjunto con lectura completa **o parcial**, con su estado y cobertura en el encabezado
de la fuente; solo se omite el de un adjunto bloqueado o ilegible. Ollama recibe además una ventana de contexto explícita
(`llm.num_ctx`, 16384 por defecto) porque su valor por defecto (4096) recorta el prompt sin avisar. Una pregunta nunca aparece repetida
en el correo sugerido.
El modo Work sigue el mismo contrato de evidencia y registra, para cada pregunta, las fuentes ya verificadas;
una pregunta que ya esté contestada en cualquier archivo se elimina. El inventario de I:\\T&E y los límites actuales
del lector por formato/tamaño se documentan en [INVENTARIO_FORMATOS_HISTORICO_TE.md](../docs/INVENTARIO_FORMATOS_HISTORICO_TE.md).
El modo local/API puede preparar un paquete local con manifiesto, cobertura y texto extraído segmentado por adjunto para
que una persona lo agregue a la tarea de Work. El paquete conserva hashes/localizadores, no se sube automáticamente y
no convierte una extracción parcial o no legible en un dato ausente. Los documentos Office/OpenDocument y los ZIP se rechazan para revisión manual si su tamaño
expandido o su número de entradas superan el tope (protección contra bombas de descompresión), antes de descomprimirlos. Work sigue siendo el responsable de interpretar
el alcance y proponer preguntas; el validador Python comprueba la estructura y referencias mecánicas del resultado.

### F1-04 · Detectar datos faltantes
Determina qué datos faltan para la etapa de cotización, distinguiendo lo crítico de lo recomendable, y los convierte
en preguntas para el cliente. Con dependencias o contradicciones pendientes el caso pasa a revisión técnica. Una magnitud (área, longitud, cantidad) solo cuenta como conocida si trae un número, en cifras o en palabras
comunes: una etiqueta como «Área» o una palabra como «varios» en el campo de cantidad no lo es, y el caso pregunta la magnitud.

### F1-05 · Pedir los datos al cliente
Prepara el correo de respuesta con las preguntas; una persona lo revisa y lo envía desde el dashboard o desde Gmail
(R8). En el dashboard, el borrador que propone Work se muestra sin el historial citado (se quita al importarlo);
lo que la persona escribe, guarda o envía sale tal cual, sin recortes en silencio (U-39). La conversación completa
sigue visible en el caso. La respuesta conserva el hilo original de
Gmail. Al enviar desde el dashboard, el mensaje aparece de inmediato en «Conversación con el cliente». Un clic produce
un correo: el mismo texto no se reenvía y el caso muestra que ya se envió.

### F1-06 · Esperar la respuesta y cerrar el alcance
Detecta la respuesta del cliente (en el mismo hilo o en otro), avisa, reconcilia la tabla sin pisar lo que una
persona editó, y permite dar la información por suficiente.

### F1-07 · Carpeta en Drive
Crea la carpeta del proyecto con la estructura `{año}/{cliente}/{solicitud}` y su contenido (R4 a R10), sin crear
carpetas de cliente por error.

## Transversales

### T-01 · Estados y transiciones
El ciclo de vida de un caso sigue `spec/transiciones.json`; cada fila se ejecuta contra el código real.

### T-02 · Contrato con Work
El JSON que ChatGPT Work deja en el borrador cumple `spec/contrato_work.schema.json`; el puente lo valida y acepta el
estado sin importar tildes ni mayúsculas.
Un descuido de tipo evidente en un campo opcional (p. ej. `unresolved: true` donde va un texto) no hace perder el borrador:
el puente lo corrige (queda vacío), lo importa y deja el motivo como advertencia visible en el caso. Un borrador que de
verdad incumple el contrato se rechaza y se muestra en el Resumen según T-28.

### T-03 · Código de la oferta y control de cambios
Cada caso aceptado recibe su T (que pasa a T&E al aprobarse). Desde la T, la tabla solo cambia por el contacto
registrado citando el código (R11); el resto queda como solicitud por revisar, con avisos al contacto.

### T-04 · Evidencia e historial
Cada hecho queda con fecha, autor y enlace al correo, encadenado con huellas; los mensajes posteriores a la T se
guardan completos; eliminar un caso no borra su historial; hay un acta de cambios imprimible.

### T-05 · Seguimiento cuando el cliente tarda
Muestra el tiempo de espera en días hábiles, alerta a los 3 días y pide llamar a los 7; los recordatorios
automáticos son opcionales por caso (R13).

### T-06 · Operación
Un lanzador único mantiene dashboard y puente, los reinicia si se caen o si cambia el código, y hace una copia de
seguridad diaria (local y en Drive, sin credenciales). El estado del puente es visible.

### T-07 · Seguridad del dashboard
Solo atiende en local; los formularios llevan un token que sobrevive a los reinicios; no hay manejadores de eventos
en línea; los datos de clientes se escapan y no pueden crear etiquetas ni scripts.
Además (D-71): `/openapi.json` no incluye el token (lo pone `/docs`); toda respuesta, incluidas las redirecciones y
`/eventos`, lleva `X-Content-Type-Options: nosniff`, `Referrer-Policy: same-origin`, `Cache-Control: no-store` y una
CSP `default-src 'self'`; un `POST` de más de 2 MiB se rechaza con 413 y uno con longitud no válida o mal codificado
con 400; un error inesperado muestra un mensaje genérico con un código y nunca el detalle interno; el nombre del
archivo descargado solo lleva `[A-Za-z0-9_-]`.

### T-08 · Privacidad del registro
Los registros no guardan contenido de correos ni datos de clientes; las direcciones de correo se enmascaran.

### T-09 · Configuración validada
`config.json` se valida y los errores dicen qué corregir. Las claves opcionales de `llm` y sus valores por defecto son:
`num_ctx` 16384 y `num_ctx_max` 32768 (Ollama: ventana de contexto pedida y máxima del modelo), `context_tokens` 100000 (API:
tokens de entrada por llamada), `chunk_chars` 12000 con Ollama y 30000 con API (tamaño de un tramo de lectura) y
`timeout_seconds` 180 (por cada 8192 tokens del prompt). `config.example.json` las muestra todas.

### T-10 · Persistencia y migraciones
La base SQLite se migra por pasos con copia previa y rechaza versiones más nuevas que el programa.

### T-11 · Paridad de los tres modos
Ollama, API y ChatGPT Work siguen las mismas reglas, que viven en un solo documento; las pruebas fallan si se
separan.
Solo un orquestador lee Gmail: si el modo activo pasa a `chatgpt_work`, el bot local se detiene solo, de inmediato —
entre un hilo y el siguiente de una pasada, o en las pausas de la vigilancia (cada 5 s)—, sin errores ni trazas.

### T-12 · Arquitectura en capas
El código vive en `anka/` en cinco capas (dominio, infra, servicios, web, cli) con dependencias en una sola dirección,
sin ciclos y sin importar nombres privados de otro módulo; la raíz solo tiene puntos de entrada delgados.
Ver `06-arquitectura.md`.

### T-13 · API documentada con Swagger
`/docs` abre Swagger UI y `/openapi.json` entrega la especificación OpenAPI 3 de todas las rutas del dashboard
(lectura JSON, páginas y acciones), con sus campos, respuestas y el efecto de cada una. La documentación se genera de
las rutas reales y falla una prueba si una ruta queda sin documentar. Ver `07-api.md`.

### T-14 · Salud de las conexiones
`GET /api/salud` responde 200 si la base, la configuración, el puente de ChatGPT Work, la autorización de Google y el
motor de análisis están bien, y 503 con la causa si alguno falla; con `profundo=1` consulta además Gmail y Drive. Un
fallo nunca se lanza como excepción ni revela datos de clientes o claves. Ver `07-api.md`.

### T-15 · Reautorizar Google desde el dashboard
Cuando la autorización de Google falta, venció o fue revocada, el dashboard lo avisa («Google desconectado») y ofrece
el botón «Reautorizar Google», que abre el consentimiento en el navegador de este equipo. La autorización nunca se
abre sola: ni el puente, ni el dashboard, ni la revisión programada esperan un consentimiento; la espera tiene un
tope, no se abre más de una ventana a la vez y el token no se muestra ni se registra. Ver `07-api.md`.

### T-16 · Emitir la T en un solo paso
Cuando la información está validada, emitir la T hace, en un solo paso: asignar el código, crear la carpeta del
proyecto en Drive con los documentos del cliente y la tabla de actividades, y enviar al cliente el correo con la T,
las instrucciones y cómo sigue el proceso. El texto se muestra y se puede editar antes de pulsar el botón (que dice
que envía), así que sigue siendo una persona quien revisa y envía (R8). Si la carpeta o el correo fallan, la T queda
emitida y se dice qué quedó pendiente. Existe además «Solo emitir la T», que no envía ni crea nada.

### T-17 · Hoja de Google Sheets del caso
Desde el caso se puede abrir y crear/actualizar una hoja de Google Sheets (y descargar el Excel). La hoja vive en la
carpeta del proyecto en Drive y **se actualiza sola cada vez que cambia la tabla** (edición, propuesta del modelo
adoptada, nuevo análisis), siempre sobre el mismo archivo. El dashboard sigue siendo la fuente de verdad: el libro
lleva la tabla vigente, la **solicitud inicial** (primer correo y primera tabla), el **historial** de versiones y una
nota de uso, de modo que nada del origen del caso se pierde. Si alguien edita la hoja directamente en Drive se avisa
(sus cambios quedan en el historial de versiones de Google). Un fallo no interrumpe nada: se muestra en el caso y se
puede reintentar.

### T-18 · Un correo, un solo caso
Cuando una persona confirma que un correo de otro hilo pertenece a un caso, ese mismo correo deja de aparecer como
«posible actualización» en los demás casos del mismo remitente: sus avisos se cierran y sus novedades se marcan leídas.
Cada caso afectado conserva una anotación de a qué caso se asignó. Lo que una persona ya descartó se respeta, descartar
en un caso no cierra los otros, y un aviso cerrado no cuenta como avance del caso. Si el correo ya había creado su
propio caso (un hilo nuevo), ese caso se **une** al elegido: queda en Descartadas como «unido», con su contenido e
historial, rescatable; lo que ese correo traía para la tabla queda como **propuesta pendiente** (la tabla vigente más
las actividades nuevas o cambiadas, sin quitar filas) que se adopta o se descarta; solo si no avanzó (sin código de oferta, carpeta ni respuesta enviada). Los avisos que ya
habían quedado repetidos se limpian al actualizar la base.

### T-19 · Preguntas justificadas por evidencia
El contrato de Work admite hallazgos atómicos (`evidence_register`) y enlaces de cada pregunta a sus hallazgos
(`question_audit`). El puente advierte referencias duplicadas o inexistentes, preguntas repetidas por texto o por
el mismo hallazgo/dato pendiente, preguntas sin objetivo bloqueante atribuible al cliente/contratista, preguntas que
incluyen hallazgos explícitos y límites de lectura trasladados al cliente. La advertencia es informativa: conserva el
análisis y el borrador para revisión humana; payloads anteriores sin esos campos siguen siendo válidos.

### T-20 · Descarte por lote de posibles vínculos
Una persona puede seleccionar varias sugerencias de vínculo en un caso o en la lista global y descartarlas en una
acción. Solo se resuelven los avisos seleccionados que sigan pendientes; correos, casos, tablas e historiales se
conservan y cada decisión deja un evento.

### T-21 · Aislamiento de correos de prueba en sugerencias de vínculo
`test_sender_addresses` configura remitentes controlados para pruebas. Solo esos remitentes, con un asunto marcado
explícitamente `prueba`, `test` o `nuevo`, quedan fuera de las sugerencias de vínculo con casos previos. No se
excluyen direcciones de Anka por defecto ni correos de clientes externos por usar esas palabras; el filtro no
elimina ni altera mensajes o casos.

## Experiencia de usuario

### U-01 · Siguiente paso claro
Cada caso muestra un solo aviso con su acción principal, un recorrido de etapas y las secciones en el orden de la
tarea, con lo secundario plegado.

### U-02 · Conversación visible
El caso muestra lo que se han escrito cliente y Anka, con el historial citado plegado.

### U-03 · La T solo con la información validada
La opción de emitir la T aparece cuando el alcance está completo o una persona dio la información por suficiente.

### U-04 · Etapa y urgencia en cada caso
Cada caso dice su etapa o lo que espera de una persona; lo urgente va primero; el Resumen es una lista de «qué
hacer hoy».

### U-05 · Un clic, un correo
El botón de enviar se bloquea tras el primer clic y el servidor rechaza repetir el mismo texto.
La reserva de envío es persistente y atómica por hilo, destinatario real y texto. Un envío en curso o
de resultado incierto bloquea el reintento, incluso después de reiniciar. Antes de reintentarlo se
reconcilia con Gmail mediante un identificador de mensaje estable; no se presupone que un timeout sea un fallo de envío.
Si Gmail rechaza la petición con certeza (error 4xx, salvo 408), la reserva se libera para poder corregir y reintentar;
ante un timeout, un corte de red o un 5xx la reserva se conserva, porque el mensaje pudo haber salido.
Las reservas confirmadas se conservan 90 días (basta para rechazar repeticiones); las inciertas nunca se purgan.

### T-22 · Recuperación de evidencia
El respaldo incluye los originales de `evidence/` junto con la base y los ajustes permitidos, nunca credenciales.
La restauración valida el ZIP y la integridad SQLite antes de reemplazar datos, conserva una copia previa y
rechaza rutas externas, enlaces y archivos ajenos a la lista permitida. Se prueba una recuperación con evidencia.

### T-23 · Evaluación reproducible
La evaluación distingue pruebas determinísticas de resultados observados con modelos reales. Registra versión
del prompt, modelo, fecha, corpus anonimizado y resultados por caso. Una muestra sin etiquetar no produce una
calificación de precisión. La trazabilidad identifica métodos de prueba ejecutables y no solo archivos etiquetados.

### T-35 · Desempeño del análisis medido con el uso real
Cada vez que una persona corrige lo que el análisis propuso, esa corrección es una medida de su acierto: sin etiquetar nada, el uso diario dice
qué tan bien trabaja Work (o el modelo local). La pantalla «Desempeño» agrupa los casos por la versión del prompt y el modelo que declaró el
análisis (T-32) y, para cada grupo, muestra cuántos casos hay, cuántos no necesitaron ninguna corrección, qué parte de las filas de la tabla se
conservó, qué parte de las cantidades quedó intacta, y cuánto coincidió la persona con lo que se le sugirió para las preguntas (cuántas
sacó del cliente, cuántas añadió o pasó al cliente). Compara la tabla que dejó el análisis con la que dejó la persona, solo hasta el primer
reanálisis (después los cambios pueden venir de información nueva). Con menos de 10 casos en un grupo lo dice: son pocos para concluir. Un caso
sin correcciones puede ser un acierto o un caso que nadie revisó: la pantalla lo advierte.

### T-34 · Evaluar el análisis con solicitudes reales del histórico
Para saber cuánto acierta el análisis (T-23) se arma un conjunto de casos a partir del histórico de propuestas (`I:\T&E`, solo
lectura): la solicitud que hizo el cliente frente a lo que Anka terminó cotizando. Cada caso es una ficha con lo esperado: si es
una solicitud, la ubicación, las actividades con su cantidad y unidad, y los datos que la solicitud no traía y debían preguntarse.
Una ficha nace como **borrador** (la propone el modelo a partir de la propuesta aprobada) y solo cuenta cuando una persona la
**valida**. Las fichas y los informes viven en la carpeta de datos del equipo, nunca en el repositorio, y los archivos del cliente
no se copian: la ficha apunta a su ubicación original. `evaluar` corre la cadena real (lectura de adjuntos, análisis,
preguntas) sobre las fichas validadas y mide: si identifica la solicitud, qué actividades encuentra y con qué cantidad y unidad,
si encuentra la ubicación, y si pregunta lo que faltaba sin preguntar lo que ya venía. El informe guarda el modelo, la versión
del prompt, la fecha y el resultado por caso, para comparar versiones. Sin fichas validadas no se calcula ninguna precisión.
Cuando la solicitud trae una hoja de Excel con su tabla de cantidades (columnas de descripción, unidad y cantidad), lo esperado se
**extrae de esa tabla de forma determinista**, sin modelo y sin que nadie tenga que recordar el caso: la ficha queda «extraída» y
cuenta en la evaluación, marcada como tal en el informe, y una persona puede revisarla por muestreo. Una tabla de más de 25 filas
no se usa. Un borrador propuesto por el modelo sigue sin contar mientras nadie lo valide.

### T-24 · Lectura completa de los adjuntos legibles
La lectura exhaustiva de los adjuntos es lo que sostiene una cotización correcta, y vale igual en los tres modos. Todo adjunto
de un formato legible (hasta 50 MiB, más que el máximo de Gmail) se lee completo: ningún recorte por longitud al texto que
recibe el modelo ni al cuerpo del correo. `lectura_parcial` se reserva para contenido legible que de verdad no se pudo
extraer (páginas o imágenes sin texto, celdas o entradas más allá del tope de recursos, gráficos o dibujos no extraídos); un
OCR ejecutado conserva el texto extraído, pero su aviso de relaciones de tabla o figura pendientes marca cobertura parcial.
La extracción textual no certifica interpretación visual. Si el análisis completo no cabe no se recorta: Ollama amplía hasta
`llm.num_ctx_max` (32768 por defecto) y, si aun así no cabe, el hilo se informa para revisión manual con el motivo.
La cantidad de una actividad no repite su unidad (`544.5` + `ha`, no `544.5 ha` + `ha`).

### T-45 · Expedientes de recepción Gmail → OCR → Drive para Work
*Sustituido para el prompt V6 por T-41; se retira cuando T-41 esté probado en Work.*
Un proceso optativo y separado del puente de borradores prepara expedientes bajo una carpeta de recepción configurada,
con una búsqueda Gmail explícita y cuenta verificada. No envía correos, no clasifica solicitudes ni crea carpetas comerciales.
Cada hilo tiene un expediente y cada versión conserva mensaje, adjunto, SHA-256, originales, texto completo segmentado,
localizadores, cobertura y motivo. Los adjuntos homónimos y las respuestas no sobrescriben versiones anteriores.
El manifiesto se publica al final; una descarga/subida fallida no publica una versión lista y se reintenta sin duplicar.
Archivos no soportados o fuera de límites se enumeran con su motivo; listo para analizar no equivale a lectura completa.
Work consulta el manifiesto vigente del hilo y todos sus segmentos, verifica message_ids contra Gmail y deja revisión interna
si el expediente falta, está desactualizado o es parcial. Los originales siguen disponibles para inspección visual.
La comparación distingue extracción local reproducible de interpretación real de Work; nunca atribuye a Work un resultado simulado.

### T-25 · Lectura por tramos de los documentos que no caben en el modelo
Un documento largo se lee **entero**, nunca recortado, en el modo local y en el de API. Si el correo y sus adjuntos caben en la
ventana del modelo (`num_ctx_max` en Ollama, `context_tokens` en API) se analizan de una vez, como siempre. Si no caben:
1. cada adjunto largo se divide en tramos de a lo sumo `llm.chunk_chars` caracteres (12 000 con Ollama y 30 000 con API por defecto), cortando en saltos
   estructurales (página, hoja, párrafo, línea) y con un pequeño solapamiento, de modo que todo el texto pasa por el modelo;
2. de cada tramo el modelo extrae **hallazgos** (tema, actividad, dato, valor, localizador y cita textual corta), sin inventar;
3. los hallazgos se unen sin duplicados en un compendio con sus localizadores; si el compendio aún no cabe, se consolida
   (hasta 3 niveles); si ni así cabe, `ContextoInsuficiente` y el hilo se informa para revisión manual;
4. el análisis final (el de siempre: reglas, preguntas justificadas, contradicciones) se hace sobre el correo y los compendios;
   los adjuntos pequeños se conservan tal cual, y la evidencia por adjunto se calcula sobre el texto completo;
5. los hallazgos de un tramo se guardan en una caché con tope (clave: texto del tramo, modelo y versión del prompt) para no
   repetir llamadas al reanalizar un hilo;
6. un tramo que el modelo no responde se divide en dos y se reintenta (hasta 2 veces); si ya no se puede dividir se hace
   un último intento que castiga la repetición; si tampoco, se detiene el análisis del hilo con un aviso: jamás se completa
   con tramos faltantes;
   antes de dividir se colapsan las líneas largas repetidas (encabezados y pies de página): se leen una vez; cada tramo
   recibe el último marcador de página u hoja visto, para que el localizador no se invente; y los hallazgos vacíos con que
   un modelo pequeño rellena el esquema se descartan;
7. el análisis registra `lectura_por_tramos` (adjunto, tramos, hallazgos, niveles) y la nota de revisión lo muestra.
Con ChatGPT Work el paquete local entrega todo el texto en segmentos ordenados (`segment=i/n`) y el contrato advierte cuando
Work declara, por adjunto, una cobertura incoherente (completa sin secciones revisadas, parcial o ilegible sin motivo).

### T-26 · Un hilo que falla se reintenta con espera y se ve
Un hilo que no se puede analizar (modelo ocupado, respuesta inválida, documentos que no caben) no se reintenta en cada
ciclo: se registra el fallo y se espera 5 min, 15 min, 1 h, 4 h, 12 h y luego 24 h entre intentos; un hilo que falla no frena
a los demás. Mientras no se logre, el hilo aparece en la lista de casos como «Requiere revisión técnica» con el motivo y
la hora del próximo intento, para que una persona lo revise a mano sin esperar; si el análisis sale bien, ese caso se
reemplaza por el real. El botón «Actualizar correo» ignora la espera (es una orden de una persona). Un hilo que ya tenía
un análisis válido nunca se sobrescribe con el aviso de fallo. Los registros de fallos de más de 60 días se borran.

### U-06 · Estados veraces
El estado que se ve en la lista, en el detalle y en los avisos no se contradice y refleja lo ocurrido.

### U-07 · Respuesta libre en casos avanzados
Un caso ya avanzado ofrece la caja de respuesta en blanco, sin textos sugeridos desactualizados.

### U-08 · Correos con el código de la oferta
Un caso con código T ofrece siempre, en la caja de respuesta, preparar el correo de «proyecto recibido» (con el
código) y, cuando la oferta pasa a T&E, el de «oferta aprobada». Al aprobar la oferta se prepara ese correo, pero
nada se envía sin que una persona pulse «Enviar al cliente» (R8).

### U-09 · El siguiente paso no se pierde al desplazarse
El aviso de «siguiente paso» del caso queda a la vista mientras se recorre la pantalla (fijo arriba), con su botón que lleva
al paso. En un caso listo para cotizar, la acción principal es emitir la T con el correo y la carpeta; la variante «solo
emitir la T» queda plegada en «Otras opciones», de modo que a la vez solo hay una acción primaria de emisión visible.

### U-10 · Fuentes compactas
Las páginas citadas como fuente de una revisión pendiente se muestran como rangos («págs. 3, 5–7, 10–15») y no repetidas
una por una, sin perder ninguna página. Con el mismo contenido el caso es más corto y legible.

### U-11 · Resumen como cola priorizada
El Resumen muestra los casos que esperan una acción de una persona como una cola ordenada por urgencia, de a lo más cinco,
y cada uno con un solo botón con verbo que lleva a su paso. El orden es: el cliente respondió; cambios, propuestas o
actualizaciones por revisar; borrador o envío por revisar; listo para emitir la T; decisión técnica o aclaración; seguimiento sin
respuesta. A igual urgencia va primero la más reciente. Si hay más de cinco, el Resumen lo dice y ofrece un botón a Solicitudes,
donde siguen todos. Las novedades que repiten a casos ya mostrados en la cola quedan plegadas, con su botón de marcarlas
como revisadas.

### U-25 · Novedades: qué cuenta, cuándo se leen y cómo avisan
Una novedad cuenta solo si pide algo a una persona: el cliente respondió, posible actualización por revisar, caso nuevo y respuesta
sugerida lista. Lo que la propia persona hizo (guardar un borrador, enviar una respuesta) queda en el historial del caso pero nace ya
leído: no suma al contador ni aparece en «Novedades». El contador del menú cuenta casos con novedades, no filas: un caso nuevo
cuenta uno aunque tenga dos novedades. Al abrir un caso con la página a la vista se marcan como leídas las novedades de ese caso
(y solo las suyas); «Marcar como revisadas» sigue marcando todas. Si la página estaba en segundo plano no se marcan hasta que se vea.
Para avisar sin mirar el Resumen, el título de la pestaña lleva el número de casos con novedades («(2) Resumen comercial - Anka») y,
si la persona lo activa, el navegador muestra un aviso cuando entran novedades nuevas con la pestaña oculta. Es solo el navegador de
este equipo: no se envía nada fuera.
La novedad «respuesta sugerida lista» solo se deriva si hay una respuesta preparada o el análisis no se declara incompleto (T-50, D-154).

### U-12 · Decidir y preguntar en un solo paso
En revisión técnica, la pantalla ofrece una sola sección, «¿Cómo seguimos?», con las dos salidas y lo que ocurre después de
cada una. Las preguntas para el cliente se editan ahí y no se repiten como tareas: «Qué falta resolver» solo cuenta cuántas
hay. Mientras la respuesta está en pausa no aparece la sección «Respuesta al cliente». Al decidir, la persona llega directo
al correo (si pregunta) o a la emisión de la T (si la información basta), y el aviso de arriba le dice que revise y envíe.
Ningún correo sale sin «Enviar al cliente» (R8).

### U-13 · Solicitudes: lista ordenada, con verbo y filtrada por etapa
La lista de Solicitudes va, por defecto, de la más reciente a la más antigua (U-16). Cada fila lleva el botón con el verbo de su
paso («Decidir», «Emitir la T», «Revisar correo»…) y, si no espera nada de una persona, «Abrir». Se filtra por etapa (Por
atender, Esperando al cliente, Listas para la T, En cotización) con la cantidad de cada una, y por tipo de cliente. «Eliminar
seleccionadas» queda dentro de un menú «Acciones» y conserva su confirmación: no está a la vista junto a las acciones normales.

### U-14 · Tabla legible y lenguaje claro
La tabla de actividades muestra cinco columnas (Actividad, Servicio/técnica, Cantidad con su unidad, Estado y Notas); ubicación,
entregable, plazo y fuente van en «Más datos» de cada fila y se guardan igual. Las cinco columnas miden lo mismo y todas las cajas de una
fila tienen la misma altura (la que pida el texto más largo), de modo que por defecto no hace falta desplazarse; cantidad y
unidad van juntas en una caja. Se puede cambiar la altura arrastrando la esquina de una caja (toda su fila toma esa altura y
la caja se desplaza por dentro si el texto no cabe) y un botón «Ajustar cajas al texto» vuelve al tamaño automático. La guía de la tabla habla al comercial (qué
significa cada estado y qué escribir en Notas). La tabla de adjuntos usa palabras de persona («No se pudo leer», «Falta
revisar», «Dónde se leyó») y no códigos internos. El foco del teclado es siempre visible y los botones y campos miden al
menos 44 px de alto.

### U-15 · Tema claro y oscuro
El dashboard tiene tema claro y oscuro con la misma paleta verde azulada. Sigue el tema del sistema por defecto y un botón
«Tema» de la barra superior alterna sistema, claro y oscuro; la elección se recuerda en el navegador y se aplica antes de
pintar la página, sin parpadeo. Todos los colores salen de variables definidas para ambos temas: no quedan colores literales
fuera de ellas (salvo la marca y el texto blanco de los botones de peligro). El foco y el contraste del texto se mantienen
legibles en los dos.

### U-16 · Solicitudes ordenadas por fecha
La lista de Solicitudes se puede ordenar por «Más recientes primero», «Más antiguas primero» y «Más urgentes primero». Al abrir
la página sale por defecto la más reciente primero. La fecha es la de recepción del correo (la que muestra cada fila), no la
de la última actualización; las solicitudes sin fecha quedan al final en cualquier orden por fecha. A igual fecha se conserva
el orden de la lista. «Más urgentes primero» usa el orden de la cola del Resumen y, a igual urgencia, la más reciente primero.
El orden se aplica en el servidor (la página ya llega ordenada) y se puede cambiar sin recargar.

### U-17 · Siguiente paso en todas las etapas, bajo el encabezado
El aviso de «siguiente paso» está en las cuatro etapas: además de la decisión y la emisión de la T, un caso que espera al cliente dice
cuánto lleva esperando y lleva al seguimiento, y un caso con T dice su código y lleva a la carpeta del proyecto (o a crearla). El
aviso se queda fijo justo debajo del encabezado, sin taparlo ni cubrir la navegación, en cualquier alto de encabezado.

### U-18 · La respuesta no compite con la emisión de la T
Cuando la información está validada o el caso ya tiene T, «Respuesta al cliente» queda plegada («opcional») y no aparece junto a la
emisión; un enlace a ella (`#respuesta`) la abre. Sigue estando disponible para el acuse y los correos con el código (U-08).
Con la información sin validar, la respuesta sigue a la vista como siempre.

### U-19 · Hilos del mismo proyecto, agrupados a la vista
Solicitudes y el Resumen muestran juntos los hilos que parecen el mismo proyecto, sin unir ni cambiar nada. Dos hilos parecen
el mismo proyecto si comparten un documento adjunto (mismo nombre y la misma cantidad de texto leído; las
imágenes no cuentan, por las firmas de correo, ni un archivo que no se pudo leer) o si el nombre del
proyecto es parecido, con el mismo criterio que las carpetas de Drive (sin tildes ni sufijos; uno contenido en el otro o 85 %
iguales) y los números iguales («Calle 170» no es «Calle 171»), siempre que el cliente sea el mismo o no esté identificado. El
mismo remitente no basta, dos clientes distintos no se agrupan (ni por el nombre ni por un archivo) y dos casos con T distinta
nunca se agrupan.
En Solicitudes el grupo es una fila: el hilo más reciente, con «N hilos del mismo proyecto», el motivo (y «verifica» si solo el
nombre se parece) y un botón para desplegar los demás; el orden por fecha usa el hilo más reciente del grupo, y el orden por
urgencia el más urgente. Se puede ver cada hilo por separado (`agrupar=0`). En el Resumen un proyecto ocupa una sola entrada
de la cola, que abre su hilo más urgente y reciente y dice cuántos más hay. Unir de verdad los hilos es una
acción aparte con confirmación de una persona (U-20).

### U-20 · Unir o separar un hilo del mismo proyecto, con confirmación
La pantalla de un caso avisa «Parece el mismo proyecto» cuando otros hilos se parecen a él (U-19) y, de cada uno, dice quién lo
mandó, cuándo, en qué etapa está y por qué se parece (con «verifica» si solo es el nombre). De cada hilo se ofrece
«Unir a este caso» y «No es el mismo proyecto». Unir pide confirmación y usa la unión que ya existe: el hilo pasa a Descartadas
como «unido» (se puede rescatar), su historial se conserva y lo que traía para la tabla llega como propuesta para adoptar o
descartar; queda anotado quién lo decidió y cuándo en los dos casos. Solo se unen hilos que no avanzaron: uno con código de
oferta, con carpeta, con respuesta enviada o ya descartado no se puede unir, y el aviso lo explica (sigue pudiendo separarse).
«No es el mismo proyecto» se guarda y esos dos hilos no vuelven a agruparse ni a sugerirse, en la lista ni en el caso.
Nada se une solo (D-09).

### T-28 · Un borrador de Work con descuidos se importa con aviso; solo se rechaza lo que compromete la identidad
El puente distingue dos cosas. Un **fallo** es transitorio (red, Gmail, importación) y se reintenta en el siguiente ciclo; deja el
puente «con avisos» mientras dure. Un **borrador rechazado** es un problema del contenido que no se arregla reintentando: se
descarga y se rechaza **una sola vez** mientras su contenido no cambie (se vuelve a revisar solo cuando Gmail da un mensaje nuevo
para ese borrador), se anota una vez en el registro y no cuenta como fallo. Con borradores rechazados y sin fallos el puente está
«activo» con un estado propio («con un borrador por revisar»), no en error: sigue importando el resto.

**Política «tolerar y avisar» (D-106, reemplaza el criterio estricto anterior y el texto de D-87).** Solo se rechaza (a) si falta la
identidad (`thread_id`, `message_id` o `client_email`, o el `thread_id` no es el de la conversación), (b) si el estado es desconocido
después de normalizar tildes, mayúsculas, espacios y puntuación de los extremos, o (c) si el JSON es irrecuperable (solo se reparan
cercas de código, texto suelto, comas sobrantes y saltos de línea dentro de un texto). Todo lo demás se acepta y el caso lo dice:
«Work escribió algo distinto de lo esperado; se corrigió solo: …» con la ruta de cada campo (p. ej. `activities[2].unit`).
Se tolera: `no indicado` o un texto donde va un número, un booleano o un enumerado (se trata como desconocido: el campo se omite o
queda vacío; nunca se inventa), los enumerados en español o con mayúsculas o punto final (mapa explícito en
`spec/03-contrato-work.md`), una lista escrita como texto, `evidence` sin `excerpt` o `location`, `fields` sin `source`,
`size_bytes` como texto o decimal entero y `quantity`/`unit` numéricos. Si el valor tolerado cambia el significado de una fila de
la tabla, esa fila queda `pendiente` (sin confirmar), nunca `confirmado` (R12). El aviso es **propio del modo `chatgpt_work`**: en
`local` y `api` no hay borrador de Work, y la salida del modelo se valida en el piloto; el editor de tabla es el mismo en los tres.

**Qué se muestra y qué se guarda de un rechazo.** Solo el motivo en lenguaje claro y en español (sin errores de Python como
«Expecting property name…»), la causa, los nombres de campo y la ruta, y la fecha; nunca el contenido del cliente ni el JSON (T-36).
El aviso se limpia solo cuando llega un análisis válido del mismo hilo igual de nuevo o más. Se cuenta por día (motivo y campo, sin
contenido): con más de 3 en un día, la «Revisión de correos» dice «Work está mandando análisis con errores: N hoy. Pide revisar
el prompt.» (U-41). En `question_audit`, un `unresolved` booleano se trata como vacío y se importa.

### T-29 · Medir cuánto tarda un borrador de Work en llegar al dashboard
Para saber si una demora es de Work (que tarda en dejar el borrador en Gmail) o del puente (que tarda en importarlo), el puente
anota la hora en que el borrador apareció en Gmail y la importación la compara con su hora de llegada. El historial del caso
lo dice en el hecho «Work analizó el caso»: «El borrador estaba en Gmail desde las HH:MM:SS; llegó al dashboard N s después»,
y el registro técnico guarda el mismo dato. Esa hora no forma parte de la huella del contenido: reguardar un borrador sin
cambiarlo no lo reimporta. Si no se conoce la hora (o llega sin zona horaria, y no se puede saber cuánto pasó), el hecho
queda como antes y el borrador se importa igual: el dato es solo informativo.

### T-31 · Las entradas inválidas se responden con claridad
Una petición que la aplicación rechaza con su motivo (un caso que ya no existe, un dato no válido) responde 400 con ese mensaje y se
anota como aviso, no como error del servidor (500); un fallo propio del código sigue siendo 500 y se anota con su traza. Un número
mal escrito en la dirección (`change_id=abc`) se toma como «ninguno» y no muestra el texto interno de Python. El nombre de una
carpeta de Drive tiene un tope de 150 caracteres y no se corta una palabra a la mitad. La importación de Work sigue aceptando un texto
donde se espera una lista (Work lo hace a veces); el contrato completo se exige en el puente, que es la entrada de producción.
Aun así, la importación nunca termina en un error de Python: un contrato que no es un objeto JSON, un campo obligatorio que no es
texto (`status: true`, `client_email: [...]`), un texto con sustitutos sueltos (`\ud800`) o un campo de lista con un número
(`activities: 5`, `cited_codes: 5`) se rechazan con su motivo (`ErrorDeAnka`); `null` en un campo opcional es lo mismo que no
escribirlo. Un estado fuera de la lista cerrada se rechaza también aquí (T-33), nunca se guarda tal cual. Los datos de Gmail
incompletos (cabecera sin nombre, cuerpo ausente, base64 mal formado, tamaño o fecha que no son números, adjunto sin nombre)
se leen como vacíos; el texto de un correo HTML no incluye lo que va en `<script>`, `<style>` ni comentarios, y un `<` suelto
no lo recorta. El final del id del hilo en el nombre de una carpeta solo conserva letras, cifras, `-` y `_`.

### T-32 · Saber con qué modelo y qué versión del prompt analizó Work
El contrato admite dos campos opcionales: `model` (el modelo que declara la interfaz de ChatGPT Work para la tarea) y `skill_version`
(la versión del prompt). La importación los guarda en el caso y los anota en el hecho «Work analizó el caso» del historial. Son
**solo internos**: ningún correo al cliente los incluye. Lo escribe el propio modelo, así que sirve para detectar cambios entre
análisis, no como garantía. Se limpian (sin saltos de línea ni caracteres de control, con tope de largo) y se muestran escapados.
Un borrador sin ellos sigue siendo válido y el hecho queda como antes.

### T-33 · El estado que escribe Work: lista cerrada y sinónimos conservadores
El prompt de Work lista los diez estados permitidos y exige uno de ellos tal cual, sin variantes. Si aun así llega una variante, el
puente solo la acepta cuando es un sinónimo evidente de un estado **conservador**, es decir, uno en el que una persona decide o se
piden datos: «Requiere revisión técnica interna», «Faltan datos» o «Requiere aclaraciones del cliente». Nunca se interpreta una
variante como un estado que avanza el caso («Alcance completo», «Información suficiente», «Borrador preparado») ni como «Descartada»:
adivinar mal ahí adelantaría o escondería una solicitud. Cuando se acepta un sinónimo, el caso lo dice en sus advertencias
(«Work escribió el estado «X»; se tomó como «Y»»). Cualquier otro valor se rechaza como hasta ahora y se ve en el Resumen.
Al comparar, además de tildes, mayúsculas y espacios, se ignoran las formas de ancho completo (NFKC) y los caracteres invisibles
de ancho cero; un valor que no es texto no se reconoce.

### T-30 · Revisar Gmail ahora y un ciclo más corto del puente
El puente revisa Gmail cada 30 segundos (antes 60; es el mínimo permitido). En modo ChatGPT Work el botón «Revisar ahora» del
encabezado le pide revisar ya: el dashboard no lee Gmail, solo deja una señal que el puente atiende en cuanto la ve (en menos
de un segundo) y consume. Cada revisión sigue siendo de solo lectura sobre Gmail.

### T-27 · El lanzador se reinicia solo cuando cambia el código
Cuando cambia el código (incluida una migración de la base), el lanzador no solo reinicia el dashboard y el puente: **se reinicia
él mismo**, para no seguir corriendo con código anterior contra una base ya migrada (los recordatorios y la copia de seguridad
fallaban con «la base es de una versión más nueva»). Un guardián mínimo lanza el servicio del lanzador y lo vuelve a lanzar
cada vez que este lo pide con el código de salida 75; con cualquier otra salida termina igual que él (Ctrl+C o parada normal).
Al arrancar, el servicio cierra los lanzadores, dashboards y puentes anteriores **sin cerrar a su propio guardián** (sus
procesos ascendentes). El guardián no ejecuta lógica del lanzador, así que no se queda desactualizado.

### U-21 · Guardar la tabla con control: cambios visibles, motivo solo si hay cambios y exportar aparte
Debajo de la tabla de actividades hay una barra de guardado. Dice si hay cambios sin guardar y cuáles («2 filas editadas, 1 por
quitar, 1 nueva») o, si no hay, la última versión guardada; «Guardar tabla» solo se puede pulsar cuando hay algo que guardar. El
motivo (opcional) aparece solo cuando se edita, se quita o se añade algo, junto a «Guardar tabla», y no se muestra cerca de la
exportación. Quitar una fila la marca a la vista (atenuada y tachada, «se quitará al guardar», con opción de deshacer) y guardar
con filas quitadas pide confirmar cuántas. Un enlace «Ver historial de cambios» lleva al acta del caso. La exportación (Excel y
hoja de Google) es una zona aparte, «Exportar la tabla», con una sola explicación corta y el detalle plegado. Nada de esto
cambia lo que se guarda ni cómo.

### U-23 · El dashboard se actualiza solo, al instante
Las páginas abiertas reciben un aviso del servidor en cuanto cambia un caso (nuevo borrador de Work, correo enviado, cambio de
estado) y se actualizan sin esperar la consulta periódica (que se conserva como respaldo si el navegador no admite el canal o se
corta). Una página con algo escrito no se recarga sola: muestra el aviso de siempre. El canal es de solo lectura, se renueva solo
cada 10 minutos, admite pocas pestañas a la vez (las demás siguen con la consulta periódica) y se cierra sin dejar nada colgado
cuando la pestaña se cierra.

### U-26 · Los puntos que se piden al cliente son los de su lista, y lo que es de Anka no va
En el correo, la frase «¿podría confirmar lo siguiente?» es la pregunta y los puntos numerados que la siguen son lo que se pide
confirmar: no necesitan llevar cada uno su propio signo de interrogación. Para saber si un correo espera respuesta se cuentan esos
puntos (y cualquier pregunta con «?» fuera de la lista), de modo que el aviso previo («lleva N preguntas») cuente lo mismo que el cliente
va a leer. Lo que es de Anka no se sugiere para el cliente: las tareas internas y los límites de lectura («KML no inspeccionable»,
«PDFs extraídos parcialmente», «no verificado»), tengan o no «?».

### U-27 · Cambiar de idea antes de enviar las preguntas
Mientras el correo con las preguntas no haya salido, la persona puede volver atrás desde la pantalla del caso, con dos salidas. **Volver a
elegir las preguntas** devuelve el caso a la decisión técnica con todo lo que había elegido conservado (las que pasó al cliente, las que
dejó para Anka, las que descartó y las que agregó), para cambiar cuáles y cuántas. **No preguntar: con esto alcanza** da el caso
por suficiente, como en la decisión técnica, y habilita emitir la T. Ambas quedan en el historial y ninguna envía nada. Un borrador ya
guardado en Gmail no se borra. Con el correo ya enviado no se puede volver atrás: la regla lo rechaza con su motivo.

### U-28 · Seguir con la T sin esperar la respuesta del cliente
Un caso al que ya se le escribió y el cliente no responde (esperando su respuesta, o con una respuesta enviada) puede seguir adelante si la persona lo
decide: «Seguir con la T sin su respuesta» da la información por suficiente bajo su criterio y habilita emitir la T y crear la carpeta,
con una confirmación y una nota en el historial («sin esperar la respuesta del cliente»). Deja de contar días de espera y de recibir
recordatorios, porque ya no está esperando. No envía nada. Si el cliente responde después, la respuesta se atiende como cualquier mensaje
nuevo. No aplica a un caso que ya tiene código ni a uno que no está esperando.

### U-24 · Armar un correo con las preguntas que la persona escribe, en cualquier etapa
En el cuadro de respuesta de cualquier etapa en que se pueda escribir al cliente hay un campo «Preguntas para el cliente» (una por
línea) y un botón «Preparar el correo de nuevo». El correo se arma con la misma plantilla fija de siempre (saludo, las preguntas
numeradas tal como se escribieron, «Puede responder a este mismo correo» y la firma), en los tres modos y sin usar ningún modelo: no
reescribe ni inventa nada. Cada línea es un punto de la lista tal como se escribió; se ignoran las líneas vacías, la numeración
y las viñetas que la persona haya puesto y las preguntas repetidas, con un máximo de 20. El texto queda en el cuadro para editarlo;
nada se envía hasta «Enviar al cliente» (R8), y el aviso previo cuenta las preguntas y la casilla de espera sigue funcionando.
Si el caso ya tiene código (T o T&E) y el correo va al contacto registrado, la plantilla es otra: nombra el proyecto y el código
(«Sobre su proyecto «X» (T26145), necesitamos confirmar lo siguiente») y pide citar el código al responder, porque desde la T la
tabla solo se actualiza con mensajes que lo citen (R11). A quien no es el contacto registrado nunca se le incluye el código.

### U-22 · Decidir cada pregunta y saber de qué correos se espera respuesta
En un caso en revisión técnica, cada pregunta candidata (las de la auditoría de preguntas, las pendientes del análisis y las de
recepción) aparece con un selector: «Preguntar al cliente», «Resolver internamente» o «Descartar», ya puesto según lo que
sugirió el análisis; la persona puede editar el texto, cambiar el selector de una, de varias o de todas, y agregar preguntas
propias. Una pregunta que el análisis marcó como revisión interna o «no preguntar», o cuyo encabezado dice «interno»,
«interna» o «internamente», nunca se sugiere para el cliente.
«Preparar el correo con las preguntas elegidas» arma el correo solo con esas preguntas, numeradas, y no lo reemplaza el
borrador de Work (que sigue siendo el punto de partida cuando no se eligió ninguna). «Guardar mis decisiones» las conserva sin
pasar el caso a preguntas; las marcadas «Resolver internamente» aparecen en los pendientes de Anka. Cada decisión deja huella
(quién, cuándo, qué). Si llega un mensaje nuevo en el hilo, el reanálisis las descarta, como la decisión de revisión técnica.
El estado después de enviar depende de **lo que salió en el correo**, no de un clic anterior: con al menos una pregunta el caso
queda en «Esperando cliente» (y el aviso dice cuántas); sin preguntas (un acuse) queda en «Respuesta enviada · sin espera»:
no cuenta días de espera, ni avisa «sin respuesta» ni «llamar al cliente», ni recibe recordatorios por silencio. El evento del
envío y el aviso del caso lo dicen con esas palabras. Un caso anterior a este cambio, sin el dato de las preguntas enviadas,
conserva su comportamiento de antes. Antes de enviar, la pantalla dice cuántas preguntas lleva el
correo y si el caso quedará esperando; una casilla «Esperar su respuesta aunque el correo no tenga preguntas» fuerza la espera
(TR-26). Un acuse mientras queden preguntas decididas como internas devuelve el caso a «Falta tu decisión técnica» (TR-25),
para que ese trabajo no salga de la cola de la persona.

### U-80 · Pantalla del caso más clara: un camino para las preguntas, confirmaciones propias y accesibilidad
Ajustes de usabilidad que no cambian qué se guarda ni qué se envía:
1. **Un solo camino para las preguntas del correo.** En revisión técnica las preguntas se deciden en «¿Cómo seguimos?» (U-22),
   que va justo debajo del aviso de siguiente paso. Después, en «Respuesta al cliente», el campo «Preguntas para el cliente»
   (U-24) llega con las preguntas que se eligieron mientras el correo no haya salido (U-27), para cambiarlas ahí mismo. Si el
   cuadro del correo ya tiene texto, ese campo queda plegado («Cambiar las preguntas del correo»), y si la persona ya editó el
   correo, rearmarlo pide confirmar que se reemplaza lo escrito.
2. **Guardar el borrador es la acción principal; enviar es aparte.** «Guardar borrador en Gmail» es el botón primario y «Enviar
   ahora» va en una zona propia que dice que el envío es definitivo. El envío sigue pidiendo confirmación (R8), que muestra el
   destinatario, el asunto, cuántas preguntas lleva (lo mismo que el aviso previo) y que no lleva adjuntos.
3. **Confirmaciones propias y accesibles.** Las acciones con confirmación (`data-confirm`) abren un diálogo propio (`<dialog>`
   modal, con foco en «Cancelar», Escape para cancelar y el foco de vuelta en el botón). Si el navegador no lo admite se usa la
   confirmación nativa; sin JavaScript el formulario funciona como antes. No se agregan scripts ni manejadores en línea.
4. **Un solo botón «Reautorizar Google»**, en Ajustes; el aviso «Google desconectado» del encabezado lleva a él.
5. **Colores con un solo significado:** el rojo (`danger`) es solo para lo destructivo (eliminar); el ámbar, para avisos.
6. **Accesibilidad:** la navegación marca la página actual (`aria-current`), hay un enlace «Saltar al contenido», la tabla de
   actividades conserva sus `role` de tabla (no son redundantes: la cuadrícula cambia su `display` y algunos navegadores
   pierden entonces la semántica nativa), los contadores y avisos pequeños miden al menos 12 px y
   el botón de tema dice el tema actual y a cuál cambia.
7. **Ayuda breve** para los términos propios («T», «T&E», «aplicabilidad», «lectura realizada»): un glosario plegable en el caso.
8. **Menos estilos en línea:** la pantalla del caso no usa atributos `style` y la tabla de actividades no trae su propio bloque de
   estilos (todo está en la hoja de estilos de siempre).
9. La carpeta del proyecto en Drive se pliega mientras es solo una alternativa manual (sin código y sin un aviso de Drive pendiente).

### T-46 · Trazabilidad de lectura y origen de Work
El puente conserva el preflight y la auditoría de lectura, distingue aplicabilidad, verifica el origen en Gmail para V4.5 y evita regresiones por borradores antiguos.

### T-36 · Un borrador de Work sin análisis válido no deja el caso fuera del dashboard
Cuando Work deja en Gmail un borrador que no se puede usar (sin el bloque `ANKA_DASHBOARD_JSON_V1`, con un JSON irrecuperable, sin
identidad o con un estado desconocido; ver T-28) y el hilo no tiene todavía un análisis importado, el puente crea el caso con lo mínimo que es verificable en Gmail:
remitente, asunto y fecha de recepción del último mensaje recibido. El caso queda en «Requiere revisión técnica interna», sin
actividades, sin preguntas y sin texto para el cliente; sus pendientes dicen que Work dejó un análisis que Anka no pudo leer, el
motivo (en lenguaje claro: nombres de campo, nunca contenido del cliente ni el JSON ni errores de Python) y qué pasará: la persona
puede completar la tabla y, si Work vuelve a mandar el análisis, se revisa solo (D-106, U-41). El historial dice que el análisis
no se pudo leer y que el caso se creó con los datos del correo, no «Work analizó el caso». No inventa ningún dato.
Nunca reemplaza un análisis ya importado de ese hilo, ni vuelve a crear un caso que una persona eliminó mientras el borrador no
cambie. En cuanto Work deja un borrador válido, este reemplaza al caso mínimo. El Resumen sigue listando el borrador rechazado.

Dos excepciones para un borrador **sin el bloque JSON** (no para uno con JSON roto o con campos inválidos, que sigue rechazándose de
inmediato), porque Work a veces deja un borrador auxiliar o uno a medias que no es un fallo:
1. **Si el hilo ya tiene un análisis válido** (en esta pasada o importado antes), ese borrador se ignora: no se lista como rechazado ni
   crea un caso mínimo. Es el segundo borrador que Work deja a los pocos segundos del completo, con un resumen en prosa y sin JSON.
2. **Mientras tenga menos de 10 minutos**, tampoco se rechaza ni crea el caso mínimo: Work puede estar terminando. En cuanto pasa ese
   tiempo sin un borrador válido, se trata como siempre (se lista y crea el caso mínimo). Con ello el dashboard no dice «Work no pudo»
   cuando Work solo está a medias.
Un borrador rechazado de un mensaje nuevo en un hilo ya analizado queda oculto por la excepción 1 hasta que Work deje su borrador
válido; el mensaje nuevo se sigue viendo como novedad del hilo (U-25). Motivo: D-69.

### T-37 · La lectura que declara Work se verifica contra el manifiesto del preparador
Cuando el preparador (T-45) publica un expediente, guarda una instantánea local mínima de su manifiesto (hilo, revisión, `message_ids`
e identificador y nombre de cada segmento del correo y de los adjuntos). El puente la usa para dejar de confiar en lo que Work copia a
mano: `DRIVE_PREFLIGHT.manifest_revision`, `message_ids`, los conteos de segmentos y las listas `*_segment_ids_expected` y
`*_segment_ids_pending` se calculan desde el manifiesto; `*_segment_ids_read` es lo que Work declaró haber leído, reducido a lo que
existe en el manifiesto (acepta el identificador o el nombre del segmento y lo normaliza al identificador). Cada diferencia se
anota como aviso del puente. Si Work leyó una revisión que no es la vigente, el mensaje analizado no está en el manifiesto o quedan
segmentos sin lectura verificada, `analysis_complete` pasa a falso y el caso a revisión técnica interna. Sin instantánea para el
hilo (preparador apagado o expediente anterior a este cambio) no se modifica nada: el comportamiento es el de T-46.

Cuando quedan segmentos de adjuntos sin lectura verificada, el puente dice **cuáles**: la instantánea guarda también el nombre del
archivo de cada segmento, el aviso del caso lo nombra («1 de 4 segmentos de adjuntos sin lectura verificada: plano.kml») y la
auditoría conserva la lista en `attachment_files_pending` (la calcula el puente, nunca Work). El aviso «Lectura pendiente» de la
pantalla del caso nombra esos archivos y dice qué hacer: volver a ejecutar la tarea de Work sobre el hilo, porque un bloqueo no es un
análisis final y un análisis completo posterior de la misma revisión reemplaza al incompleto. Los resultados siguen ocultos hasta
que la lectura se verifique; esto solo evita que la persona deba adivinar qué falló. Una instantánea anterior a este cambio no
trae los nombres: el aviso funciona igual, sin ellos, hasta que el preparador la vuelva a guardar.

### T-38 · Lo que Work pone en la tabla se comprueba contra los textos del expediente
Cuando el preparador (T-45) publica un expediente guarda también, solo en este equipo, el texto extraído del correo y de cada adjunto
(con un tope de tamaño; si lo supera, la comprobación queda como «no comprobable»). El puente comprueba, sin cambiar ni bloquear nada:
(a) que cada número de la cantidad de una actividad aparece en los textos del expediente (en el archivo citado si se puede
identificar; si solo está en otro archivo, lo dice); (b) que los archivos citados en las fuentes existen en el expediente y que la
página citada de un PDF no supera las páginas leídas; (c) que cada extracto literal de un adjunto aparece en el texto de ese adjunto.
Cada diferencia es un aviso del caso con su ubicación («actividad 3, cantidad 126»). Si algún adjunto no tiene texto (ilegible o
no extraído), un dato que no se encuentra se marca «no comprobable» y no como diferencia. El resultado queda en el caso
(`evidence_check`: estado, cuántas comprobaciones, diferencias, no comprobables). Sin textos del hilo no se comprueba nada.

### T-39 · El prompt de Work es corto y su ejemplo cumple el contrato
El prompt de la tarea de Work cabe en unos 12 000 caracteres (la V4.7 tiene 19 500; la V6.1, 11 800; la V6.1.1, 12 800 por la regla de revisión interna de T-57; la V6.1.2, 14 000 por las reglas de seguimiento de T-59, D-168; el límite de las pruebas es 14 500), no depende de la skill para el formato de salida e incluye un
JSON de ejemplo completo. Una prueba extrae ese ejemplo del archivo del prompt y comprueba que cumple el esquema
(`spec/contrato_work.schema.json`), de modo que el ejemplo no se desactualiza sin que falle una prueba. Work solo declara qué
segmentos leyó; la revisión, los conteos y los pendientes los calcula el puente (T-37).

### T-40 · El preparador gasta poca cuota de Gmail y no se queda sin preparar lo más nuevo
El preparador (T-45) no vuelve a bajar un hilo cuyo estado en Gmail (`historyId` de `threads.list`) no cambió desde la última vez que
lo preparó con éxito, salvo que falten sus copias locales (T-37, T-38). En cada pasada baja como máximo seis hilos completos y
empieza por los que nunca ha preparado; los demás esperan a la siguiente pasada. Si Gmail rechaza una petición por cuota, termina la
pasada sin insistir ni contar cada hilo restante como fallo, y lo deja anotado en su estado. Pedir un hilo concreto lo prepara siempre.

### U-29 · «Ya revisé estos archivos yo»: la lectura sin verificar no detiene el trabajo
**Estado: implementado** (pruebas en `tests/test_lectura_revisada.py`). Decisión: D-73. Acota T-41.

**Motivo.** T-41 oculta los resultados y las decisiones mientras la lectura de Work no se verifica contra Gmail. Hay archivos que ningún
lector abre (un plano CAD: `read_attachment_supported: false`) y otros que Anka debe revisar por dentro aunque el motor los lea. Repetir la
tarea de Work no arregla eso, así que el caso quedaba sin salida.

**Qué debe cumplirse**
1. **Siempre hay una salida.** Mientras la lectura esté sin verificar, la pantalla del caso muestra, bajo el aviso, «¿Ya revisaste estos
   archivos tú?» con el botón «Ya revisé estos archivos yo» (con confirmación) y recuerda que también se puede volver a ejecutar la tarea de
   Work. Aplica a cualquier causa: archivos sin leer, archivos que Work nombró y Gmail no tiene, o un expediente de Drive incompleto (V5).
2. **Qué hace.** Levanta el bloqueo: se ven los resultados de Work y se habilitan las decisiones. No cambia el estado del caso, no envía
   nada al cliente (R8) y no marca la lectura como verificada.
3. **Deja huella.** Guarda en el análisis `reading_review` (`at`, `files`: los archivos que seguían sin verificar) y un evento
   `reading_reviewed` en el historial (quién, cuándo y sobre qué archivos).
4. **No se esconde.** Mientras la marca exista, el caso muestra el aviso permanente «Lectura revisada por una persona…», con los
   archivos, y dice que los resultados son los que declaró Work.
5. **Solo si hace falta.** Si la lectura no está pendiente, o ya se asumió, la acción responde con un mensaje claro y no cambia nada.
6. **Un análisis nuevo de Work la reemplaza.** La marca vive en el análisis; un borrador nuevo se verifica de nuevo y, si sigue sin
   verificarse, vuelve a ofrecer el botón.

### U-30 · Mientras el caso espera tu decisión, siempre se ven las preguntas del análisis
**Estado: implementado** (pruebas en `tests/test_preguntas_visibles.py`). Decisión: D-75. Acota U-22 y U-24.

**Motivo.** Un caso quedó en «revisión» (Work lo dejó en «Requiere revisión técnica interna») pero la evaluación de recepción del dashboard
decía «suficiente para revisión interna». Los selectores de pregunta solo se pintaban cuando esa evaluación era técnica, así que
«¿Cómo seguimos?» decía «El análisis no dejó preguntas candidatas» aunque había 7 (4 sugeridas para el cliente), y el cuadro «Respuesta
al cliente» quedaba en blanco sin forma de generar las preguntas.

**Qué debe cumplirse**
1. **Los selectores salen siempre que el caso espera tu decisión** (estado «revisión» y lectura no bloqueada), sea cual sea la
   evaluación de recepción. Cada pregunta trae lo que sugiere el análisis ya elegido, y «Preparar el correo con las preguntas elegidas»
   arma el correo con las que elijas para el cliente.
2. **El aviso «El análisis no dejó preguntas candidatas» solo sale si de verdad no hay ninguna** (ni del análisis, ni de la auditoría de
   preguntas, ni de la recepción).
3. **«Usar las preguntas sugeridas».** En «Respuesta al cliente», mientras no hayas decidido nada y el análisis sugiera preguntas para el
   cliente, un botón carga esas preguntas en el campo «Preguntas para el cliente» y arma el correo (el mismo camino de U-24, con la plantilla
   de siempre). No envía nada; el correo se revisa y se envía con «Enviar al cliente» (R8). El botón desaparece cuando ya hay preguntas en el campo
   o ya decidiste.

### U-31 · Pantalla del caso por pasos: una tarjeta «Ahora», la tabla siempre a la vista y el resto a un clic
**Estado: en implementación, etapas 1 a 5** (pruebas en `tests/test_pantalla_por_pasos.py`). Decisiones: D-78 (reemplazada por D-82), D-79, D-80, D-81, D-82, D-83, D-84, D-85, D-86, D-87, D-88, D-89, D-90, D-95, D-98. Acota y, donde se indica, reemplaza partes de U-12, U-17, U-18 y U-80.
Prototipo de referencia (datos de ejemplo): https://claude.ai/artifact/6EdjHMPpWHfj3VdkQedGs8

**Motivo.** La revisión de UX (2026-10-14) puntuó la pantalla del caso con 73/100 y el ingeniero la calificó de compleja, poco intuitiva y sin
fluidez: 11 secciones, 43 campos de texto, ayuda en prosa en casi cada sección, dos caminos para las mismas preguntas y mensajes distintos sobre
la carpeta de Drive.

**Qué debe cumplirse (acordado hasta ahora)**
1. **Una tarjeta «Ahora»** bajo el encabezado, con lo que toca hacer en este paso y como máximo dos salidas. Sin envíos al cliente en la decisión (R8).
2. **Los cuatro pasos reales arriba**, los de siempre: «Revisar y aclarar», «Información validada», «T emitida» y «T&E aprobada», con «Paso N de 4». No cambia
   ningún estado ni transición (`transiciones.json`, `STATUS_LABELS` y `_case_stage` quedan como están). La carpeta de Drive no es un paso: se crea al emitir la T
   (ver el requisito de emisión) y se anota en «T emitida» («carpeta creada»).
3. **«Actividades detectadas» siempre visible**, con la tabla del prototipo: una vista compacta de tres columnas (Actividad, Cantidad, Estado) y, en el estado, una etiqueta («Completa»,
   «Por completar», «Contradictoria») y el motivo en letra pequeña cuando falta algo. Encima, la línea «N actividades · M por completar» (o «todas completas»). Con más de 8 filas se ven 8
   y «Ver todas (N)»; las incompletas siempre se ven. La cabecera de la tarjeta lleva el título, el resumen y, a la derecha, «+ Agregar» y «Editar tabla»; las filas incompletas llevan una franja de color. «Editar tabla» abre la tabla completa de siempre (las 10 columnas, con «Más datos»); allí siguen el guardado con motivo,
   «Quitar», la exportación y el historial de cambios de U-21. Lo que cambia es qué se ve primero; qué se guarda, no.
4. **Archivos y tareas de revisión en la pestaña «Archivos»**, como en el prototipo (D-82, que reemplaza a D-36 y D-78). La pestaña lleva en su nombre cuántos archivos hay y cuántos están por
   revisar («Archivos (3) · 1 por revisar»), se ve como una tabla simple de dos columnas (Archivo y Qué pasó, con etiqueta «Leído» o «No se pudo leer: ábrelo tú» y la razón), con lo que hay que revisar primero y una franja de color; el detalle técnico de cada archivo queda a un clic («Ver el detalle técnico de los archivos»); «Lectura y aplicabilidad» va plegada salvo que la lectura esté pendiente. Antes: muestra primero lo que no se pudo leer y deja lo leído plegado, y trae también «Qué falta resolver» y «Lectura y aplicabilidad». Abre sola
   (es la pestaña activa al cargar) cuando hay archivos por revisar o la lectura está pendiente; si no, abre «Conversación». Un enlace a «#attachments-review» abre su pestaña.
5. **Un solo cuadro de texto con las inquietudes del análisis.** Al elegir «Hay que preguntarle al cliente», la tarjeta muestra un único cuadro «Preguntas para el cliente (una por línea)»,
   ya escrito con todas las inquietudes que el análisis sugiere para el cliente (las de Anka, que son internas, no van). La persona borra las que no hagan falta, corrige las que quiera
   y escribe las suyas; «Continuar» arma el correo con lo que quede, sin más pasos (el mismo camino de U-24). Debajo, una línea dice cuántos puntos resuelve Anka por dentro («Qué falta
   resolver»). Ya no hay una tarjeta por pregunta ni selectores por pregunta en la pantalla. Si el cuadro queda vacío, se pide escribir al menos una o elegir «Con esto alcanza». Al
   volver a decidir antes de enviar (U-27), el cuadro trae lo que la persona había escrito. El contrato del formulario con las decisiones por pregunta (U-22: `q_id`, `q_decision_<id>`)
   sigue aceptándose en el servidor y en la API, pero la pantalla ya no lo usa; reemplaza lo que U-22, U-26 y U-30 decían de los selectores en pantalla.

**Valores por omisión propuestos (se dan por buenos si nadie los objeta antes de programar)**
6. **Glosario.** El glosario de U-80.7 deja de ser el primer bloque del caso: un «?» junto a «Paso N de 4» lo abre (mismos términos: T, T&E, lectura realizada, aplicabilidad).
7. **Un solo nombre por estado (D-86).** La insignia del caso, la nota del primer paso, la lista de Solicitudes, el Resumen y los botones usan los mismos nombres, según el estado del flujo y no
   según la evaluación de recepción: «Por decidir» (revisión técnica: falta tu decisión), «Correo por revisar» (el correo para el cliente está redactado y falta que lo revises y lo envíes; D-96),
   «Esperando al cliente» (ya se le escribió), «Información validada» (lista para emitir la T) y, con T, «T… · cotizando» o «T&E… · aprobada». Se distingue «Correo por revisar» de «Esperando» porque en el
   primero la persona todavía debe actuar y el correo aún no salió. Desaparecen «Información suficiente para revisión interna», «Requiere revisión técnica interna», «Falta tu decisión técnica», «Listo para emitir la T» y
   «Recepción completa» como nombre del estado de un caso. Los textos que Work envía (su etiqueta de estado) no cambian y se siguen aceptando tal cual; el panel «Evaluación de recepción» del
   detalle técnico conserva su propia etiqueta.
8. **Lectura sin verificar (U-29).** Si hay archivos que Anka no pudo verificar, la tarjeta «Ahora» lo dice primero y ofrece «Ya revisé estos archivos yo» (con su confirmación)
   como acción principal; la decisión de abajo queda deshabilitada hasta entonces, igual que hoy.
10. **Detalle técnico.** Datos comerciales, contexto interno, enrutamiento, asuntos pendientes y revisión de preguntas pasan a una sección plegada «Detalle técnico» al final del caso; la
    trazabilidad (historial) queda en su propia pestaña. Nada se elimina ni cambia de contenido.
11. **Orden de lectura.** Encabezado → pasos → tarjeta «Ahora» → «Actividades detectadas» → seguimiento (si espera al cliente) → pestañas «Conversación», «Archivos», «Historial» y «Detalle
    técnico» → lo demás que aún aplica (respuesta opcional, carpeta). Reemplaza el orden de la regla 2 de `04-ux.md`.
12. **Resumen de la tabla.** Sobre la tabla, una línea «N actividades · M por completar» (o «todas completas»); `M` cuenta las que no están «confirmado» (R12). La explicación larga de los
    estados queda plegada en «Cómo leer los estados».
13. **Aspecto de «Ahora» (idéntico al prototipo, D-83).** La decisión de revisión técnica es una tarjeta destacada con la etiqueta «Ahora», borde de color y el título «Revisa y decide cómo seguir», «Qué pide:» en un recuadro, una frase de lo que falta («Faltan: 3 actividades por completar y 2 archivos por revisar…») y, al final, «Elige una opción para continuar. Nada se envía al cliente en este paso.» Las salidas son
    opciones grandes elegibles (no botones sueltos): «Con esto alcanza» y «Hay que preguntarle al cliente» (no hay una tercera: guardar decisiones por pregunta ya no existe en la pantalla). Las opciones son tarjetas sin círculo de selección, con una línea de explicación, y **no viene ninguna marcada**: el cuadro de preguntas aparece al elegir «Hay que preguntarle al cliente» (reemplaza lo que U-30 decía de marcarla de entrada). Hay un botón por opción, que aparece al elegirla: «Confirmar que alcanza» (pide confirmación, U-80.3) y «Preparar el correo». Sin JavaScript el formulario funciona igual. Los pasos se dibujan como una barra de avance: hechos y actual en el color de la
    marca, pendientes en gris, y el actual con el texto en negrita.

14. **Encabezado del prototipo.** «← Mis casos» (U-32), el nombre del proyecto, una línea «cliente · correo · recibido el …» y la insignia del estado. El párrafo largo de resumen sale del
    encabezado: en la decisión pasa a la tarjeta como «Qué pide:» (máximo unas 320 letras).
15. **La tarjeta «Ahora» existe en todos los pasos.** Es una sola, la primera tras los pasos: la decisión (revisar), el correo por enviar, la emisión de la T o el aviso del estado
    (esperando al cliente, T emitida, carpeta lista). Lleva la etiqueta «Ahora», no se pega al desplazarse (reemplaza esa parte de U-09) y, si otra tarjeta ya dice lo mismo, el aviso de
    siguiente paso no se dibuja.
16. **El correo, simple y amigable (D-84).** La tarjeta muestra «Para» en una línea, el «Mensaje (puedes editarlo)» en un cuadro grande, una línea que dice cuántas preguntas lleva y qué pasará con
    el caso, y dos botones: «Enviar al cliente» (principal; sigue pidiendo confirmación con destinatario y asunto, R8) y «Guardar borrador en Gmail». Debajo, «Cambiar las preguntas»
    abre un cuadro con las preguntas actuales (una por línea) para cambiarlas, y «Preparar el correo de nuevo» escribe otra vez el mensaje con ellas (reemplaza el texto; si la persona ya lo editó, pide
    confirmar, U-80). La casilla «Esperar su respuesta aunque el correo no tenga preguntas» solo aparece cuando el mensaje no lleva preguntas. «¿Cambiaste de idea?» es una línea discreta con
    «Volver a decidir» y «No preguntar: con esto alcanza». Dentro de esta tarjeta ya no salen «Respuesta sugerida lista…», «Recepción completa…» ni los párrafos largos
    de ayuda. Reemplaza el punto 2 de U-80 y los textos del punto 1 de U-80.
17. **Pestañas.** «Conversación», «Archivos», «Historial» y «Detalle técnico» (datos comerciales, contexto interno, evaluación de recepción, revisión de preguntas, enrutamiento y asuntos
    pendientes). Funcionan con teclado (flechas) y, sin JavaScript, todos los paneles se ven uno tras otro.
18. **Seguridad del usuario (evaluación de 4 agentes, 2026-10-14; D-85).** a) El aviso de siguiente paso solo se oculta cuando la tarjeta «Ahora» dice lo mismo, y se oculta desde el servidor
    (atributo `hidden`), no por una regla CSS: los avisos que piden una decisión (cambio detectado, enlaces, respuesta por actualizar) nunca se esconden y se rotulan «Antes de seguir».
    b) Ningún formulario de escritura se envía dos veces: tras el primer envío se ignoran los siguientes y el botón queda bloqueado (antes solo «Enviar»). c) Si el mensaje del correo está
    editado y la persona intenta salir de la página, el navegador avisa. d) «+ Agregar» abre el editor de la tabla aunque ya se hubiera abierto y cerrado. e) El mensaje de éxito o de error
    se queda a la vista tras saltar a un ancla y no reaparece al recargar. f) Ningún texto apunta a un título que ya no existe.
19. **El Resumen no empieza con jerga (D-87; el texto y las acciones del aviso los reemplaza U-41).** El aviso de borradores de Work que el puente no pudo leer va debajo de la cola «Qué hacer hoy», no encima, con un título en lenguaje llano («Un borrador de Work
    no se pudo leer»), una frase de qué pasó y qué hacer, el botón «Ver el caso» cuando el caso existe, y el identificador del borrador, el hilo y el motivo técnico plegados en «Ver el detalle técnico».
20. **Ningún estado se queda sin tarjeta «Ahora» (D-88).** Todo caso muestra una tarjeta o un aviso de siguiente paso. Los que antes no tenían: «Descartada» (cómo rescatarla), «Hilo no
    disponible», «Respuesta enviada» sin espera (cómo escribir otro correo), «Envío interrumpido» (revisar antes de reenviar) y, para cualquier otro, el nombre del estado, «Este caso no necesita nada
    de ti ahora» y «Volver a Mis casos».

21. **Lo escrito no se pierde si la acción falla (D-89).** En enviar, guardar borrador, emitir la T, decidir con preguntas y guardar la tabla, lo que la persona escribió se guarda en memoria
    (nunca en disco, 15 minutos, hasta 50 casos, sin el token de seguridad) antes de procesarlo. Si la acción falla con un aviso o con un error del servidor, al volver al caso el mensaje, el número de
    la T, el cuadro de preguntas o la tabla vuelven a su sitio, con el aviso «Lo que escribiste sigue aquí», y la página de error dice que lo escrito se conserva. Si la acción sale bien, o el caso ya no
    admite ese formulario, se olvida.
22. **CSS ordenado y legible en pantalla angosta (D-90).** Cada selector de la pantalla del caso se define una sola vez (sin reglas apendadas que pisen a otras). El borde de input, select, textarea, las opciones y los
    botones de contorno tiene al menos 3:1 de contraste con su fondo en los dos temas (token `--control-line`). A 640 px o menos, los botones de las pestañas, los enlaces, las opciones y los botones de la
    cabecera de la tabla no ocupan todo el ancho, y cada fila de la tabla de edición se apila con la etiqueta de cada campo. Con `prefers-reduced-motion` no hay animaciones ni desplazamientos suaves.
23. **La acción principal del correo siempre se alcanza (D-95).** En la tarjeta del correo, la línea que dice cuántas preguntas lleva y los botones «Enviar al cliente» y «Guardar borrador en Gmail» forman una barra
    que se queda pegada al borde inferior de la pantalla mientras se lee el mensaje, así que no hay que bajar hasta el final para enviar. El mensaje se muestra completo (sin cuadro con desplazamiento propio) para que la
    persona pueda revisarlo entero antes de enviar (R8); no se esconde parte del texto. La barra respeta el área segura de los celulares y no tapa el último renglón del mensaje.
24. **Un solo texto por decisión y por acción (D-97).** Cada decisión y cada acción se llama igual en toda la interfaz: «Con esto alcanza» (la opción) y «Confirmar que alcanza» (su botón); «Hay que preguntarle al cliente» y
    «Preparar el correo»; «Cambiar las preguntas» (abre el cuadro) y «Preparar el correo de nuevo»; «Volver a decidir»; «No preguntar: con esto alcanza»; y siempre «Enviar al cliente» para enviar (el diálogo dice «Sí, enviar al cliente»).
    No quedan «Marcar como suficiente», «Redactar de nuevo», «Volver a redactar», «Volver a elegir», «Armar correo» ni «Enviar ahora». Una prueba de vocabulario lee las plantillas y los textos de la pantalla y falla si aparece alguno.
25. **Tabla y formularios accesibles (D-98, WCAG 2.2 AA).** a) Cada campo de la tabla de edición dice de qué actividad es («Notas de Planimetría», «Estado de Altimetría»; la fila nueva, «de la fila nueva»), y la casilla de quitar
    dice «Quitar» más la actividad; no hay dos campos con el mismo nombre en la página. b) La tabla conserva su semántica aunque se dibuje con cuadrícula: grupos de filas, encabezados de columna y celdas con su rol.
    c) Los errores se anuncian como alerta. d) Al saltar a un ancla, el encabezado fijo no tapa el elemento (`scroll-padding-top`). e) Tras una acción con mensaje, el foco se mueve al mensaje para que el teclado y el lector de
    pantalla no queden al inicio de la página.

**Casos de aceptación** (los que se vuelven pruebas; etapa 1 = primera entrega)
- E1 · El caso en revisión muestra «Paso 1 de 4» y los cuatro pasos antes del aviso de siguiente paso; con la información validada, «Paso 2 de 4»; con T emitida, «Paso 3 de 4»; con T&E, «Paso 4 de 4».
- E1 · El «?» del glosario está junto a los pasos, abre T, T&E, Lectura realizada y Aplicabilidad, y ya no hay un glosario antes del encabezado de pasos.
- E1 · Orden: pasos, tarjeta «Ahora», actividades y, después, las pestañas (conversación, archivos, historial y detalle técnico); la conversación ya no va antes de la tabla.
- E1 · Con 4 actividades de las que 2 no están confirmadas, la tabla dice «4 actividades · 2 por completar»; con todas confirmadas, «todas completas»; sin actividades, no hay línea.
- E1 · La explicación de los estados está plegada («Cómo leer los estados») y las acciones de guardar, exportar y el historial de cambios no cambian (U-21).
- E2 · Los archivos y «Qué falta resolver» están en la pestaña «Archivos», que dice «N por revisar» en su nombre y es la activa si hay algo por revisar; lo no leído se ve abierto y lo leído, plegado.
- E2 · Con lectura sin verificar, el aviso «Ahora» dice «Lectura pendiente» y su botón «Elegir cómo seguir» lleva a «¿Ya revisaste estos archivos tú?», justo debajo, con «Ya revisé estos archivos yo»; no hay decisión ni envío (U-29).
- E2 · Con más de 8 actividades se ven 8 y «Ver todas (N)»; las incompletas siempre se ven y las filas escondidas se guardan igual. «Detalle técnico» ya es la sección plegada «Detalles y trazabilidad del caso»: conserva su nombre y su contenido.
- E3a · La decisión es una tarjeta «Ahora» con dos tarjetas de opción de una sola elección (`name="decision"`: `sufficient`, `ask_client`), sin círculo visible, y un botón por opción que aparece al elegirla; no hay botones de envío sueltos ni tercera opción.
- E3a · No viene ninguna opción marcada; el cuadro único trae las preguntas sugeridas una por línea y se muestra al elegir «Hay que preguntarle al cliente». Sin las reglas CSS modernas todo sigue visible y enviable.
- E3a · «Confirmar que alcanza» pide confirmación; «Preparar el correo» no.
- E3a · Enviar el formulario con «Con esto alcanza» o con «Hay que preguntarle al cliente» deja el mismo estado e historial que antes; con preguntas, el caso pasa a «Revisa el correo» con ellas.
- E3b · No hay una tarjeta ni un selector por pregunta: hay un cuadro `questions` con las sugeridas una por línea, que solo incluye las que el análisis sugiere para el cliente; las internas se cuentan en una línea.
- E3b · Con el cuadro vacío, «Continuar» pide escribir al menos una pregunta. Lo escrito se conserva al volver a decidir antes de enviar (U-27).
- E3b · La vista compacta de actividades tiene tres columnas con etiqueta y motivo; «Editar tabla» abre la tabla completa y guardar funciona como antes.
- E3b · Mientras la tarjeta «Ahora» con la decisión está en la página, el aviso «Falta tu decisión de revisión técnica» no se dibuja (lo dice la tarjeta) y no se queda pegado arriba.
- E4 · El encabezado muestra «← Mis casos», el proyecto, «cliente · correo · recibido el …» y la insignia del estado; no trae el párrafo de resumen.
- E4 · Cada paso tiene su tarjeta «Ahora»: revisar (decisión con «Qué pide»), correo (formulario), información validada (emisión de la T), y aviso para esperando, T emitida y carpeta; nunca dos.
- E4 · El aviso de siguiente paso no se pega al desplazarse y no se dibuja junto a una tarjeta que ya dice lo mismo.
- E4 · La tarjeta de correo trae «Para», «Mensaje (puedes editarlo)», la línea de cuántas preguntas lleva, «Enviar al cliente» (principal, con confirmación y resumen) y «Guardar borrador en Gmail»; enviar y guardar hacen lo mismo que antes.
- E4 · «Cambiar las preguntas» trae las preguntas actuales y «Preparar el correo de nuevo» rearma el mensaje con ellas; la casilla de esperar solo aparece si el mensaje no lleva preguntas; no hay avisos «Respuesta sugerida lista» ni «Recepción completa» en la tarjeta.
- E4 · Hay cuatro pestañas con sus paneles, y la activa al cargar es «Archivos» si hay algo por revisar y «Conversación» si no.
- E5 · Los pasos se ven con «Paso N de 4» y el «?» encima de la tarjeta de pasos, sin numerarlos dentro; el contenido del caso tiene un ancho máximo de lectura de 880 px.
- E5 · «Editar tabla» y «+ Agregar» se dibujan como botones de contorno con texto del color de la marca.
- E6 · Con una tarjeta «Ahora» el aviso estándar lleva `hidden`; sin ella se ve; el aviso de cambio, de enlaces y de respuesta por actualizar nunca lleva `hidden`.
- E6 · Un formulario POST no se envía dos veces; con el correo editado el navegador pide confirmar al salir; «+ Agregar» abre el editor por script; el mensaje queda a la vista y su parámetro sale de la URL.
- E8 · En el Resumen los casos por atender salen antes que el aviso de borradores de Work; el aviso dice «Un borrador de Work no se pudo leer» (o «N borradores… no se pudieron leer»), trae «Ver el caso» y deja el identificador y el motivo dentro de un detalle plegado.
- E9 · Para cada estado del caso la pantalla trae una tarjeta «Ahora» o un aviso de siguiente paso con título, texto y un botón que lleva a algún sitio; descartada, hilo no disponible, respuesta enviada sin espera y envío interrumpido dicen qué pasó y qué hacer.
- E10 · Si Gmail falla al enviar, el mensaje editado y la casilla de esperar vuelven a la pantalla; un aviso de la acción (por ejemplo un borrador que no se pudo guardar) también conserva lo escrito; un envío exitoso no deja nada; si el caso ya no admite responder, no se avisa en falso.
- E10 · Lo mismo para el cuadro de preguntas de la decisión, el número y el correo de la T, y las actividades de la tabla (con el editor abierto); lo guardado caduca, tiene tope y no incluye el token.
- E11 · `--control-line` pasa 3:1 contra el panel y el fondo de los campos en claro y oscuro; los selectores `.next-step`, `.paso-cabecera`, `.paso-n`, `.pregunta`, `.armar-correo`, `.process-step strong` y `.toolbar` se definen una vez; hay reglas de 640 px para las pestañas y la tabla apilada con `data-etiqueta`, y de `prefers-reduced-motion`.
- E12 · «Enviar al cliente», «Guardar borrador en Gmail» y la línea de preguntas están dentro de una barra `.barra-accion` pegada abajo (`position: sticky; bottom: 0`); el mensaje crece con su contenido (`field-sizing: content`) y no tiene tope de altura.
- E12 · En el navegador, con el caso largo (CLL 170) y la pantalla en el arranque, la barra queda dentro de la pantalla visible.
- E13 · Ninguna plantilla ni texto de la pantalla del caso contiene los sinónimos retirados; cada decisión y cada acción usa el texto único de U-31.24.
- E14 · Con tres actividades, todos los campos de la tabla de edición tienen nombre accesible distinto y que incluye el nombre de su actividad; la casilla «Quitar» también; la fila nueva dice «de la fila nueva».
- E14 · `thead` y `tbody` llevan `role="rowgroup"`, los `th` `role="columnheader"` y los `td` `role="cell"`; el aviso de error de destinatario lleva `role="alert"`; hay `scroll-padding-top` y el script enfoca el mensaje.
- E7 · En revisión técnica la insignia, la nota del paso y la lista dicen «Por decidir»; con el correo redactado sin enviar, «Correo por revisar»; con la información validada, «Información validada»; con el correo ya enviado, «Esperando al cliente»; ninguna dice «Información suficiente para revisión interna» ni «Requiere revisión técnica interna».

**Vacíos abiertos:** ninguno que bloquee la etapa 1. Prueba manual en Work: no aplica (Work no cambia).

### U-32 · Una sola entrada, «Mis casos», sin barra de pestañas
**Estado: en implementación, pasos 1 y 2** (pruebas en `tests/test_pantalla_por_pasos.py`). Decisiones: D-91, D-92, D-93. Reemplaza lo que U-80.6 decía de la navegación; no quita ninguna función de U-11, U-13, U-16, U-19 ni U-25.

**Motivo.** La persona responsable del producto no quiere la barra «Resumen / Solicitudes / Descartadas / Archivo»: pide un flujo amigable, práctico, sin tantas variaciones ni desvíos. Tres agentes de
evaluación (UX, técnico y gestión, 2026-10-14) encontraron que Resumen y Solicitudes responden lo mismo, que solo una o dos de las cuatro pestañas sirven al trabajo diario y que Archivo (casos eliminados,
con identificadores ilegibles) es de auditoría. La persona respondió que rescata descartados «casi nunca» y que Archivo y estadísticas los consulta solo ella, casi nunca.

**Qué debe cumplirse (paso 1)**
1. **Sin barra de pestañas.** El encabezado trae la marca, que es el enlace a «Mis casos» (la página de inicio, antes «Resumen») con el contador de casos con novedades, y a la derecha el tema, el estado
   del puente, «Ajustes» y «Revisar ahora». La página de inicio se titula «Mis casos» y sigue mostrando «Qué hacer hoy», las novedades, el aviso de borradores y los contadores.
2. **Lo raro, fuera del camino.** «Descartadas» es un enlace discreto al pie de toda página («Correos descartados»); «Casos eliminados (archivo)» vive dentro de «Ajustes». Las direcciones `/requests`, `/discarded` y
   `/archive` no cambian, y desde «Mis casos» se sigue llegando a la lista completa.
3. **Un solo «volver».** Desde un caso, «← Mis casos» lleva a la entrada; los avisos de los casos sin acción dicen «Volver a Mis casos».
4. **Accesibilidad.** La marca lleva `aria-current="page"` en «Mis casos»; su nombre accesible dice cuántos casos tienen novedades.

**Paso 2 (pedido por la persona el mismo día, D-92): la página principal es la lista de casos.** La página de inicio («Mis casos», con el título grande) lista todos los casos, uno por fila, del más urgente al menos y,
a igual urgencia, el más reciente; un proyecto con varios hilos ocupa una sola fila. Cada fila dice el proyecto, el cliente, en qué paso está (con los nombres de U-31.7) y trae un botón con verbo («Aclarar», «Decidir», «Emitir la T»,
«Abrir») que lleva al caso; desde el caso, «← Mis casos» regresa a la lista. Encima de la lista, chips de etapa que son los contadores de antes («Por atender», «Esperando al cliente», «Listas para la T», «En cotización») y «Todos»;
al entrar está elegido «Por atender» si hay casos que esperan a la persona y «Todos» si no. Las novedades y el aviso de borradores de Work van en la misma página; las estadísticas siguen plegadas. Un cuadro «Buscar» (proyecto, cliente o servicio) filtra la lista, junto con los chips. Eliminar casos en lote y exportar a Excel, que se usan poco, viven en «Administrar casos» (`/requests`), al que se llega
desde «Ajustes»: ya no hay un botón «Buscar o exportar» ni dos listas en el camino. Reemplaza lo de U-11 sobre la cola de cinco casos y el botón «Ver los N en Solicitudes».

**Casos de aceptación**
- E1 · El encabezado no tiene `<nav>` ni los enlaces «Resumen», «Solicitudes» y «Archivo»; la marca enlaza a `/` y marca la página actual; el contador de novedades está en la marca.
- E1 · La página de inicio se titula «Mis casos»; toda página trae «Correos descartados» al pie; «Ajustes» trae «Casos eliminados (archivo)»; las tres direcciones antiguas siguen existiendo.
- E1 · «← Mis casos» en el caso y «Volver a Mis casos» en los avisos llevan a `/`.
- E2 · «Mis casos» lista todos los casos (no solo cinco), ordenados por urgencia y luego por fecha, con un solo botón con verbo por fila; un proyecto con varios hilos ocupa una fila; el título es un `h1` grande.
- E2 · Los chips de etapa traen el número de casos de cada una y «Todos»; al entrar está elegido «Por atender» si hay casos por atender y «Todos» si no; elegir un chip muestra solo esas filas y, si no hay, dice que no hay casos en esa etapa.
- E2 · El cuadro «Buscar» filtra las filas por proyecto, cliente o servicio, junto con el chip elegido; si nada coincide dice «No hay casos con esos filtros». No hay botón «Buscar o exportar».
- E2 · «Ajustes» trae «Administrar casos (eliminar, exportar)» que lleva a `/requests`, que se titula «Administrar casos» y trae «← Mis casos»; las novedades y el aviso de borradores de Work siguen en la página principal.

### U-33 · Lo que se le pide al cliente se lee como una petición, sin etiquetas internas
**Estado: dashboard implementado; ajuste de la skill de Work pendiente de prueba manual en Work** (pruebas en `tests/test_preguntas_al_cliente.py`). Decisión: D-94. Acota U-26 y U-31.5.

**Motivo.** La evaluación de calidad (2026-10-14) y la revisión de los datos reales de CLL 170 y LLANOS 8 mostraron que los puntos que el análisis de Work deja para el cliente llegan al correo escritos como
órdenes de trabajo interno («Resolver la correspondencia del polígono…», «Resolver la contradicción entre miércoles y 30-10-2026») y, en un caso, con la etiqueta «(bloqueante)». Los puntos sí son del cliente
(U-26 los clasifica bien); el defecto es la redacción y la etiqueta, que no deben llegarle.

**Qué debe cumplirse**
1. **Sin etiquetas internas.** Al limpiar un punto del análisis se quitan también «(bloqueante)», «(bloqueantes)» y «(blocking)» al final, además de «(crítico)», «(recomendable)» y sus equivalentes en inglés.
2. **Sin verbos de trabajo interno.** Al escribir el cuadro «Preguntas para el cliente» (U-31.5) con lo que sugiere el análisis, un punto que empieza por «Resolver» se escribe como lo que se le pide:
   «Resolver la contradicción entre miércoles y 30-10-2026» → «La contradicción entre miércoles y 30-10-2026». Un punto que ya es una pregunta (lleva «?») o que no empieza por ese verbo no se toca, y lo que la
   persona escribe o edita en el cuadro nunca se reescribe.
3. **Work escribe bien desde el origen.** La skill pide que cada punto para el cliente se redacte como dato o pregunta cortés, sin verbos de trabajo interno («Resolver», «Definir», «Reconciliar») ni etiquetas
   como «(bloqueante)»; lo que es tarea de Anka se marca como interno. **Prueba manual en Work pendiente** antes de darlo por aceptado.

**Casos de aceptación**
- E1 · «Confirmar relación de Andrés Muñoz con OTACC (bloqueante)» se limpia a «Confirmar relación de Andrés Muñoz con OTACC»; «Crítico: confirmar el datum.» sigue limpiándose como antes.
- E1 · Con los cinco puntos de CLL 170, el cuadro de la decisión trae «La correspondencia del polígono LevantamientoRedes con el proyecto Calle 170» y «La contradicción entre miércoles y 30-10-2026», sin «Resolver».
- E1 · El correo que se arma con ese cuadro no contiene «Resolver» ni «bloqueante».
- E1 · Un punto con «?» y un punto que no empieza por «Resolver» quedan igual; lo que la persona ya escribió (`ultimas_preguntas`) no se reescribe.
- E1 · La skill de Work trae la regla de redacción para el cliente.

### U-34 · Errores con salida y protección de lo irreversible (Ola 0 del plan de experiencia)
**Estado: implementado** (pruebas en `tests/test_errores_y_proteccion.py`). Decisión: D-99. Acota U-31 (conservar lo escrito), D-08 (la T), D-12 y R8 (un correo, una vez, revisado por una persona) y D-27 (la autorización de Google solo con acción explícita).

**Motivo.** La evaluación de la experiencia (2026-10-14) encontró cinco puntos donde un fallo o una pantalla vieja podían perder trabajo o romper una garantía: un `ErrorDeAnka` (Google vencido, modo inválido) terminaba en una página 500 que pedía «avisar con el código»; emitir la T consumía el consecutivo antes de comprobar que el correo podía salir;
la pantalla se recargaba sola borrando lo que se estaba leyendo o escribiendo; un correo se podía enviar desde una pantalla anterior a un cambio del caso; y la tabla de actividades se guardaba aunque otra persona la hubiera cambiado, llegara incompleta o dejara filas sin nombre.

**Qué debe cumplirse**
1. **Errores con salida.** Una acción del dashboard que falla por un `ErrorDeAnka` o un `ValueError` de Anka (no solo las que ya lo atrapaban) vuelve al caso con el mensaje y conserva lo escrito (U-31); `/mode` con un modo inválido y `/link` con un número ilegible no dan 500. Un `ErrorDeAnka` que llega
   al servidor se muestra sin la frase «avisa con el código de error» (su texto ya dice qué hacer); un fallo inesperado la conserva. Las páginas de aviso (400, 403, 404, 500) ofrecen «Volver al caso» cuando se sabe cuál es y siempre «← Mis casos»; si la causa fue la autorización de Google ofrecen además el botón «Volver a conectar Google»,
   que pide confirmar y solo entonces abre la ventana de Google (D-27). Un caso inexistente en `GET /case` responde 404 con esa salida; un `config.json` incompleto al abrir un caso dice qué falta, en vez de un error genérico.
2. **La T no se consume en falso.** Antes de asignar la T (D-08) «Emitir la T» comprueba el número (formato, caso sin código, número no repetido), la autorización de Google y el destinatario. Si algo falla ahí, no se asigna nada. Si después de emitir el correo no sale, la T queda, el mensaje dice la causa real
   (sin culpar a Gmail si fue la autorización) y el texto revisado vuelve al cuadro de «Respuesta al cliente». El diálogo de confirmación muestra el número tecleado y el destinatario, y avisa si el número no es el sugerido o si su año no es el actual.
3. **La pantalla no se recarga sobre el trabajo.** La recarga automática por cambios solo ocurre si no hay un diálogo abierto, la página está arriba del todo, ningún campo tiene el cursor y ninguno tiene texto distinto del original; en cualquier otro caso se muestra el aviso «Hay novedades en esta página», que es un estado anunciado (`role="status"`).
4. **Un correo, una vez, desde un estado conocido (R8).** El formulario del correo lleva la marca del estado del caso al abrirse; si el caso cambió antes de enviar, el envío se rechaza («Este caso cambió desde que abriste la pantalla…»), lo escrito se conserva y no se contacta a Gmail. Sin marca (llamadas que no vienen de esa pantalla) no se comprueba.
   El diálogo de envío dice cuándo ya se envió un correo en ese caso. Es una protección añadida: ninguna existente se relaja.
5. **La tabla de actividades no pierde datos en silencio.** El formulario lleva la huella de la tabla que se abrió (`base_version`): si cambió (otra edición o un análisis nuevo) no se guarda y lo escrito vuelve al editor. Se rechaza un formulario sin `row_count`, o con 0 filas cuando la tabla tiene filas, un `row_count` fuera de 0 a 200,
   una celda de más de 2000 caracteres (las cajas llevan `maxlength`) y una fila con datos pero sin nombre de actividad. Guardar sin cambios no crea una versión («No hay cambios que guardar»). Una fila existente con todas las celdas vaciadas se cuenta y se confirma como «por quitar», porque así se guarda.

**Casos de aceptación**
- E1 · Con Google sin autorización, `/send`, `/draft` y `/approve` redirigen al caso con el mensaje (303) y el texto escrito sigue disponible; `/mode` con «inventado» y `/link` con «abc» no devuelven 500.
- E1 · `GET /case?thread_id=no-existe` es 404 y trae «← Mis casos»; un `ErrorDeAnka` en una acción es 500 sin «código de error» y con «Volver al caso»; un `RuntimeError` conserva el código y no muestra su detalle; un `AutorizacionDeGoogle` trae el botón «Volver a conectar Google» con confirmación.
- E2 · Sin Google, sin destinatario o con un número inválido, `issue_code_and_notify` falla y el caso sigue sin T; si el envío falla después, la T queda, el aviso lleva «⚠» y la causa, y el texto vuelve a la respuesta. El botón de emitir lleva los datos para el resumen (`data-resumen="emision"`, `data-para`, `data-sugerido`, `data-anio`).
- E3 · El script de la página contiene `puedeRecargar()` con las cuatro condiciones y el aviso lleva `role="status"`.
- E4 · Con una marca vieja `send_dashboard_reply` lanza «Este caso cambió desde que abriste la pantalla» sin llamar a Gmail; con la vigente o sin marca sigue su camino; la pantalla trae la marca en un campo oculto.
- E5 · Sin `row_count`, con 0 filas sobre una tabla con filas, con una celda de 2001 caracteres o con una fila sin nombre, nada se guarda; con la huella vieja, tampoco, y el editor muestra lo escrito con la huella nueva; sin cambios no hay versión nueva; con un cambio real sí.

**Vacíos abiertos:** el rechazo de una tabla cambiada devuelve lo escrito junto a la tabla vigente, pero no la compara campo a campo; queda para la ola de mejoras de la tabla. Prueba manual en Work: no aplica.

### U-36 · Sistema visual de Anka: color, fuente, radios, foco y animaciones
**Estado: implementado** (pruebas en `tests/test_estilo_visual.py`; instantáneas regeneradas). Decisión: D-101. Solo estilo: no cambia textos ni estructura (U-35, D-97) y respeta D-27.

**Qué debe cumplirse**
1. **Color.** `style.css` define la paleta en `:root` (claro) y la repite igual para oscuro en `prefers-color-scheme` y en `data-theme="dark"`. La acción es `#bd5209` y el azul de marca `#2f4d73`; el naranja de marca `#f47c27` y el ámbar `#f59e0b` no se usan como color de texto ni de relleno bajo texto.
2. **Contraste.** Cada par texto/fondo principal llega a 4,5:1 y cada borde de campo y color de foco a 3:1, en claro y en oscuro (lo calcula la prueba con las variables del CSS).
3. **Fuente.** Inter Variable (subconjunto latino) se sirve desde `GET /static/inter-latin-wght.woff2` con `font-display: swap` y alternativa `system-ui`, «Segoe UI»; el logo, desde `GET /static/logo-anka.png`. Solo esos dos nombres: otro responde 404 (sin leer rutas del disco). Sin fuentes ni imágenes de terceros.
4. **Forma.** Radios de .5 rem (controles) y .75 rem (tarjetas); sombras suaves; controles de al menos 44 px; foco visible de 2 px en el color de acción con desfase de 2 px; campos de 16 px en pantalla angosta.
5. **Movimiento.** Tarjetas y paneles entran con «fade-in-up» (.2 s, 6 px), los avisos con «fade-in» (.15 s) y los controles cambian de color en .15 s; todo se anula con `prefers-reduced-motion` (también `::before` y `::after`). Sin partículas ni librerías.
6. **Logo.** La cabecera muestra el logo (`alt="Anka"`, 38 px de alto) en vez de la «A»; el enlace conserva su nombre accesible.

**Casos de aceptación**
- E1 · Los pares de la lista cumplen 4,5:1 / 3:1 en claro y oscuro, y las dos variantes oscuras tienen las mismas variables.
- E2 · `GET /static/logo-anka.png` y `/static/inter-latin-wght.woff2` responden 200 con su tipo; `/static/otro` y `/static/..%2Fstyle.css` responden 404.
- E3 · El CSS declara `@font-face` con `font-display: swap`, no menciona Google Fonts y la regla de «reducir movimiento» cubre `*`, `::before` y `::after`.
- E4 · La cabecera trae `<img … alt="Anka">` hacia `/static/logo-anka.png`.

### U-39 · Lo que la persona escribe sale tal cual: solo el borrador de Work pierde el historial citado
**Estado: implementado** (pruebas en `tests/test_texto_de_la_persona.py`). Decisión: D-104. Acota F1-05 y el contrato con Work (`spec/03-contrato-work.md`), y aplica R8 (lo revisado es lo enviado). Corrige que enviar, guardar el borrador y emitir la T recortaban en silencio líneas con «>» o «El … escribió:» que la persona había tecleado.

**Qué debe cumplirse**
1. **Dónde se recorta.** El historial citado se quita solo del borrador que viene de Work: al importarlo (`work_import`) y al mostrar uno ya guardado antes de este cambio. Nada más pasa por ese recorte.
2. **Lo tecleado no se toca.** Enviar al cliente, guardar el borrador en Gmail y emitir la T envían exactamente el texto del cuadro (solo se quitan los espacios y saltos de los extremos). Una línea con «>» o una frase como «El cliente nos escribió: …» se conserva.
3. **El detector no confunde frases.** Una cabecera de cita es «El/On … escribió/wrote:» con los dos puntos al final de la línea y una fecha o un correo; «>» solo cuenta con dos o más líneas seguidas. Una frase suelta de la persona no es una cita.
4. **Aviso, no recorte.** Si el texto que se va a enviar todavía trae un mensaje citado de verdad, el diálogo de envío y el de emitir la T añaden una fila «Ojo»: «Tu respuesta incluye un mensaje citado. ¿Enviar así?». La persona decide; el texto no cambia.

**Casos de aceptación**
- A1 · Enviar con «> …» y con «El jue, 1 oct 2026, Ana <ana@acme.com> escribió:» tecleados: el correo sale con ese texto completo.
- A2 · Guardar el borrador y emitir la T: lo mismo (la T pasa el texto al envío sin recortarlo).
- A3 · «El cliente nos escribió: necesitamos 50 ha.» no se recorta ni se avisa; una sola línea con «>» tampoco.
- A4 · Un borrador de Work con cita se importa sin ella y la pantalla no la muestra.
- A5 · Los diálogos de envío y de emisión llevan el aviso de cita sin quitar nada.

### U-38 · Nodos animados de fondo, sin librerías y sin molestar
**Estado: implementado** (pruebas en `tests/test_particulas.py`; instantáneas regeneradas). Decisión: D-103. Solo estilo: no cambia textos ni estructura (U-35, D-97) y respeta D-27 y la CSP de D-71.

**Qué debe cumplirse**
1. **Qué es.** Un `<canvas id="particulas">` en el layout común (todas las pantallas), fijo detrás de todo (`position: fixed; inset: 0; z-index: -1; pointer-events: none`) y con `aria-hidden="true"`. Dibuja la animación del login de ANKA Inventario en JavaScript puro, en el `<script>` en línea del layout (sin librería, sin archivo externo, sin red). Su física reproduce la de tsparticles 3.9.1 (D-103): nodos azul `#2f4d73` y naranja `#f47c27` (al azar) de 1,5 a 3,5 px y opacidad 0,6; dirección libre con vector unitario; movimiento `velocidad · factor / 2` px por cuadro, con `factor = 60 · ms / 1000` (18 px/s con `velocidad` 0,6, con cualquier frecuencia de cuadros); rebote total en los bordes; líneas de hasta 140 px con opacidad `0,35 · (1 − d/140)`; repulsión del ratón a 100 px que suma directamente a la posición `min(easeOutQuad(1 − d/100) · 100, 50)` px por cuadro, sin suavizar. La cantidad sigue la densidad de tsparticles (40 nodos por 1920×1080, proporcional al área) multiplicada por 2 porque las tarjetas opacas tapan buena parte de la pantalla, con un mínimo de 16.
2. **No estorba.** La intensidad (opacidad de nodos y líneas) se multiplica por 0,7 (0,84 en oscuro): el texto y las tablas siempre van sobre superficies opacas y el contraste que mide `test_estilo_visual` no cambia; la repulsión es de posición, así que su efecto no depende de la intensidad. El ratón se escucha en `window` (el canvas no recibe eventos) y se suelta al salir de la ventana, perder el foco o salir del documento. En modo oscuro el azul se aclara (`#8fb0dc`) y el naranja es `#f9a668`.
3. **Movimiento reducido.** Con `prefers-reduced-motion: reduce` no se monta (el canvas queda `hidden`); si la preferencia cambia en vivo, se detiene o arranca. En alto contraste (`forced-colors`) y al imprimir el canvas no se muestra.
4. **Gasto y fluidez.** Se pausa cuando la pestaña está oculta (`visibilitychange`) o la ventana pierde el foco (`blur`; `focus` la reanuda, U-65) y al volver reinicia el reloj; se limita a ~60 cuadros por segundo con `requestAnimationFrame`; el movimiento sigue al reloj (no a pasos fijos por cuadro) y un cuadro tardío mueve como mucho lo de 100 ms; sigue el tamaño de la ventana (reajusta la cantidad) y la densidad de pantalla (`devicePixelRatio`, tope 2).

**Casos de aceptación**
- A1 · Toda página trae un solo `<canvas id="particulas" aria-hidden="true">` y el CSS lo deja fijo, detrás y sin eventos.
- A2 · El script consulta `prefers-reduced-motion`, `visibilitychange` y `devicePixelRatio`, y no carga nada externo (ni `src=`, ni `import`, ni `fetch`).
- A3 · Los parámetros clave (40, 140, 0,35, 0,6, 100, 100, 50, 60, colores) están en el script.
- A4 · La física, ejecutada en Node (`tests/fisica_nodos_prueba.js`), da 18 px/s con 30, 60 y 144 cuadros por segundo, mueve 50 px por cuadro a un nodo a menos de ~70 px del ratón y 0 a 100 px o más, y deja fuera del radio a todo nodo con el ratón quieto.

### U-37 · Accesibilidad de la pantalla: lo que se elige y se avisa no depende del color ni del ratón (Ola 2 del plan de experiencia)
**Estado: implementado** (pruebas en `tests/test_accesibilidad.py`; instantáneas regeneradas). Decisión: D-102. Es de forma, no de contenido: no cambia textos únicos (D-97), «Sí, enviar al cliente» ni el vocabulario de `test_vocabulario`; el texto añadido solo para lectores de pantalla (`solo-lectores`) usa ese mismo vocabulario. Complementa U-31 punto 25 (D-98) y U-36.

**Qué debe cumplirse**
1. **Avance del caso.** Los pasos son una lista ordenada (`<ol>`); el actual lleva `aria-current="step"`; cada uno dice «Hecho», «Paso actual» o «Pendiente» para lectores y lleva marca ✓, ▶ u ○ (no solo color).
2. **Lo elegido se ve sin color.** Las tarjetas de decisión muestran ○ / ●, las píldoras y los chips marcados muestran ✓; en alto contraste (`forced-colors`) el borde pasa a `Highlight`. «Cómo seguir con este caso» es un `fieldset` con `legend`.
3. **Avisos.** Un aviso que cuenta un fallo se anuncia como `role="alert"` con «Error:» para lectores (los demás, `role="status"`); lo escrito por el servidor lo marca con `aviso=error` en la dirección, no con el texto. Los cambios de estado de la revisión de correos y de Google se anuncian en una región `status`; el contador de casos visibles tras filtrar («3 casos a la vista») también; el texto de un aviso en vivo solo se reescribe si cambió. «Hay cambios nuevos» ya era `status` (U-34).
4. **Pantalla angosta y zoom.** A 360 px no hay desplazamiento horizontal (el `overflow-x: hidden` del `body` se quitó y se arreglaron los desbordes); Ajustes se despliega en el flujo en ≤ 720 px, el selector de motor envuelve y Esc cierra Ajustes. Con `max-height: 600px` el encabezado, la barra de envío y el aviso dejan de ser pegajosos; hay `scroll-padding-bottom` para que la barra no tape el foco.
5. **Tamaños.** El texto no baja de 14 px (rem, con `max(.875rem, 14px)`); los campos son de 16 px en ≤ 720 px; casillas de 24 px y enlaces/botones de acción de 44 px de alto; los botones secundarios y los chips tienen borde visible.
6. **Campos.** El buscador y el mensaje del correo tienen etiqueta visible; los campos obligatorios de la T y de la carpeta dicen «(obligatorio)»; el número de la T usa `inputmode="numeric"`.
7. **Tabla de actividades.** Tiene nombre accesible; todo `th` lleva `scope="col"` y ninguno está vacío (los de acción llevan texto para lectores); «Editar tabla» y «Ver todas» son botones con `aria-expanded` (no casillas ocultas) y sin JavaScript el editor se ve siempre; «+ Agregar» es un botón y lleva el cursor a la fila nueva; el editor se apila hasta 900 px.
8. **Botón de tema.** Su nombre accesible empieza por el texto visible («Tema: sistema. Pulsa para usar el claro»).
9. **Acciones largas.** Toda acción que escribe cambia el texto del botón («Enviando…», «Guardando…», «Creando la carpeta…»), lo dice en una región `status` y marca el formulario con `aria-busy`; a los 30 s libera el botón y avisa «No hubo respuesta…». Un envío detenido por otro script no bloquea el formulario.
10. **Limpieza.** Los casos de `/requests` usan `h2`; los enlaces que abren pestaña nueva lo dicen; sin `alert()` (el aviso es en línea con `role="alert"`); las pestañas responden a Inicio y Fin; el punto de novedades dice «Nuevo:».

**Casos de aceptación**
- A1 · La pantalla de un caso trae `<ol class="process">` con un solo `aria-current="step"` y el texto «Paso actual».
- A2 · Un `?message=…&aviso=error` se dibuja con `role="alert"`; sin `aviso=error`, con `role="status"`.
- A3 · Una acción que termina con aviso añade `aviso=error` a la dirección a la que redirige.
- A4 · El CSS no pone `overflow-x: hidden` en `body`, declara `forced-colors`, marcas ○/●/✓ y `scroll-padding-bottom`.
- A5 · Ningún `th` de las plantillas queda sin `scope` ni vacío.
- A6 · El código de la T pide «(obligatorio)» y `inputmode="numeric"`.

### U-40 · Flujo simple: una acción principal por estado, lo secundario plegado (Ola 3 del plan de experiencia)
**Estado: implementado** (pruebas en `tests/test_flujo_simple.py`; instantáneas regeneradas). Decisión: D-105. Es de forma y orden, no de comportamiento: no cambia `spec/transiciones.json`, ningún nombre de estado (D-86, D-96), los textos únicos de D-97, «Sí, enviar al cliente», el contrato con Work ni el momento en que se crea la carpeta. Respeta R8 (el envío sigue explícito y con su confirmación), R11, R12, R13 (no se automatiza ningún envío) y D-27.

**Motivo.** La evaluación de flujo (2026-10-15) contó seis vías de salida en «Correo por revisar», un botón que no era tarea en «Esperando al cliente», la segunda ronda con los mismos rótulos que la primera, un panel de la T con tres campos y el correo abierto, dos «Ahora» compitiendo arriba del caso, secciones vacías con el mismo peso que las llenas y callejones sin salida.

**Qué debe cumplirse**
1. **Correo por revisar.** Visibles: el texto, «Enviar al cliente» (principal) y «Guardar borrador en Gmail». «Cambiar las preguntas», «Volver a decidir» y «No preguntar: con esto alcanza» están en un solo menú plegado «Cambiar de idea» (abierto solo si el correo está en blanco o se acaba de pedir otro con preguntas). La casilla «Esperar su respuesta» solo se ofrece si el correo no hace preguntas y explica qué hace; el servidor ya la entrega oculta cuando el texto trae preguntas. Rearmar el correo editado sigue pidiendo confirmación.
2. **Esperando al cliente.** Sin botón principal: «Nada que hacer: esperando su respuesta…», con lo que se hará a los 3 y 7 días de trabajo (R13), las preguntas enviadas (plegadas) y el seguimiento como enlace. Si toca llamar, «Ya hablé con el cliente» está en la misma tarjeta (registra la llamada; no envía nada).
3. **Segunda ronda.** Cuando el cliente contestó un correo con preguntas (hay un correo suyo posterior al envío), el paso 1 dice «· ronda N» y la tarjeta «Ahora» de la decisión o del correo abre con «El cliente respondió (ronda N): revisa lo que faltaba.», lo que se le preguntó y lo que sigue pendiente. No hay estados ni eventos nuevos y no se afirma qué respondió: la respuesta está en «Conversación». La tabla va justo debajo de la tarjeta.
4. **Emitir la T.** Antes del botón, una lista de las 3 cosas que pasan (se crea el código, se crea la carpeta, se envía el correo a quien corresponda); el número dice de dónde sale; el correo va plegado en «Ver o editar el correo»; «Solo emitir la T» es un botón secundario visible; «Corregir el cliente» (el formulario de cliente, tipo y sector) está en el mismo panel. El bloque «Carpeta del proyecto en Drive» sale de este estado y queda en «Más datos» como «Si la carpeta falló: crearla a mano»; si hay un aviso de carpeta pendiente o la T ya está emitida sin carpeta, sigue a la vista. Un campo obligatorio vacío dentro de algo plegado lo abre.
5. **Avisos con una prioridad.** Una tarjeta principal arriba y, plegada, «y además: N avisos» con el resto (orden en `spec/04-ux.md`). Novedades: una fila por caso. Tema, «Avisarme de novedades» y el selector de quién analiza los correos están dentro de Ajustes; el selector, en «Ajustes avanzados (administración)» con su advertencia y el modo activo dicho en llano.
6. **Tabla.** El resumen marca lo que falta («Falta ubicación, plazo» según los datos esenciales de R12); «Corregir la tabla» abre la edición; el estado se llama igual en el resumen y en el editor (Completa / Por completar / Contradictoria) aunque el valor enviado es el de siempre; no hay botón «Ajustar cajas al texto» (las cajas se ajustan solas); «¿Por qué cambias?» solo aparece tras cambiar algo.
7. **Secciones vacías.** «Lo que sabemos del cliente», «Revisión de las preguntas» y «Pendientes» no se dibujan si no tienen contenido. «Información comercial» pasa a «Corregir el cliente» en el panel de la T y, sin ese panel, sigue en «Más datos».
8. **Callejones.** «Descartada» ofrece «Sí era una solicitud: analizarla»; «El correo no salió» enlaza a Gmail (pestaña nueva); «Ya no está en Gmail» ofrece «Borrar este caso» (con confirmación; el historial se conserva); un archivo que no se pudo leer dice «No pudimos leer plano.dwg: ábrelo tú o pídele al cliente un PDF»; el caso con carpeta creada dice cuál es el siguiente paso.

**Casos de aceptación**
- F1 · Un caso con el correo armado trae «Enviar al cliente» y «Guardar borrador en Gmail» fuera de `<details id="cambiar-idea">` y las tres salidas dentro.
- F2 · Un caso en espera no tiene botón en la tarjeta y lista las preguntas enviadas; con 7 días trae el formulario «Ya hablé con el cliente».
- F3 · Un caso con preguntas enviadas y un correo del cliente posterior dice «ronda 2»; sin correo posterior, no.
- F4 · El panel de la T lista 3 cosas antes del botón, el correo está plegado y «Solo emitir la T» no está dentro de ningún `<details>`.
- F5 · Con un cambio pendiente y otro por enviar, hay una tarjeta y «y además: 2 avisos»; con la tarjeta «Ahora» del paso, el aviso del estado no se repite.
- F6 · Un plano `.dwg` no leído trae su salida en la decisión y en la lista de archivos.

**Pendiente (Ola 4 o necesita decisión del dueño, no se hizo):** un solo disparador de la carpeta (R9 frente a D-29), deshacer tras «Con esto alcanza», anular la T, un evento «Confirmar alcance», pre-redactar la petición con las celdas por completar, pasar de 4 a 5 pasos, renombrar estados, «Guardar borrador» y «Administrar casos», la decisión con dos tarjetas-botón, «Abrir en Gmail» para el caso que ya no está en Gmail (no tiene sentido), agrupar el encabezado en una sola barra en pantallas angostas y poner el verbo primero en cada fila de la lista.

### U-41 · Cuando Work manda un análisis que no se puede leer, la persona sabe qué pasó y qué hacer
**Estado: implementado** (pruebas en `tests/test_tolerancia_work.py`, `tests/test_rechazo_de_work.py` y `tests/test_scripts_validos.py`). Decisión: D-106. Reemplaza el texto y las acciones de D-87 y acota T-28 y T-36. No toca R8 (nada sale al cliente), R11, R12 ni D-27. El aviso es propio de `chatgpt_work`; el editor de tabla es el de los tres modos.

**Motivo.** El diagnóstico (2026-10-15) mostró que el aviso «Work no pudo entender un análisis» salía por descuidos secundarios de Work (un `no indicado` en un número, un enumerado en español, una cita sin extracto), decía el error de Python en inglés, no se limpiaba cuando llegaba un análisis válido, ofrecía «Confirmar que alcanza» sobre cero actividades, mentía en el historial («Work analizó el caso») y pedía algo que nadie puede hacer («arréglalo en Gmail»).

**Qué debe cumplirse**
1. **Tolerar y avisar** (T-28): un análisis con descuidos secundarios se importa y el caso muestra «Work escribió algo distinto de lo esperado; se corrigió solo: …». Solo se rechaza la identidad, un estado desconocido o un JSON irrecuperable.
2. **Panel del Resumen.** Título «Falta el análisis de un caso» (o «de N casos»), qué pasó en lenguaje claro y en español, qué pasará («Si Work vuelve a mandar el análisis, se revisa solo») y dos acciones: «Completar la tabla yo» (principal: abre la tabla editable del caso, «Corregir la tabla» y «+ Agregar») y «Dejarlo para después» (secundaria: oculta el aviso y deja constancia en el historial). No dice «Pide a Work que lo haga de nuevo» ni «arréglalo en Gmail». El aviso se limpia solo cuando llega un análisis válido del mismo hilo.
3. **Pantalla del caso mínimo.** Una franja «Este caso aún no tiene análisis de Work» con el motivo, «Completar la tabla yo» y «Ver el correo del cliente». «Confirmar que alcanza» (y la opción «Con esto alcanza») quedan desactivados, con la razón visible, mientras no haya ninguna actividad; el servidor también lo rechaza.
4. **Historial.** Un caso mínimo no dice «Work analizó el caso»: dice que el análisis no se pudo leer y que el caso se creó con los datos del correo.
5. **Qué se guarda.** Del rechazo solo el motivo, los nombres de campo, la ruta y la fecha (T-36).
6. **Métrica y alerta.** Los rechazos se cuentan por día (causa y campos, sin contenido). Con más de 3 en un día la «Revisión de correos» dice «Work está mandando análisis con errores: N hoy. Pide revisar el prompt.».
7. **Borrador HTML.** Las etiquetas que no son de formato (un `<Polygon>` citado de un KML) no se borran del bloque; las entidades se convierten.
8. **Los scripts de las páginas se comprueban.** Una prueba pasa `node --check` a todo el JavaScript de las páginas (desde U-49, los archivos `app.js` y `tema.js`, y cualquier `<script>` en línea que reaparezca; se salta si no hay Node), para que un error de sintaxis como el de `layout.html` no pase desapercibido.

**Casos de aceptación**
- N1 · `size_bytes: "no indicado"`, `owner: "cliente"`, `status` con punto final, `sources` como texto y `evidence` sin `excerpt` se importan con aviso; un `thread_id` ajeno, un estado inventado y un JSON roto siguen rechazándose.
- N2 · Un rechazo guarda motivo, causa y campos, sin ningún texto del cliente.
- N3 · Un análisis válido posterior del mismo hilo limpia el aviso; uno anterior al rechazo no.
- N4 · El caso mínimo no dice «Work analizó el caso»; «Confirmar que alcanza» sale desactivado y el servidor lo rechaza.
- N5 · Con 4 rechazos en el día aparece la alerta; con 3 no.
- N6 · Un KML citado en un borrador HTML llega entero al bloque JSON.
- N7 · Todos los `<script>` de la lista y del caso pasan `node --check`.

**Pendiente (no se hizo; necesita prueba manual del dueño en Work):** la versión V6.2 del prompt (núcleo y extras, valores permitidos junto a cada campo, una lista de comprobación y menos contexto); el diff propuesto está en `docs/PROPUESTA_PROMPT_V6_2.md`.

### U-42 · La base aguanta tres procesos a la vez (Ola B0 del plan de backend)
**Estado: implementado** (pruebas en `tests/test_base_concurrente.py`). Decisión: D-107. No toca R8, D-27 ni la reserva de envío.

**Qué debe cumplirse**
1. Toda conexión a la base usa `journal_mode=WAL`, `busy_timeout=15000` (15 s) y `foreign_keys=ON`. `synchronous` se queda en FULL.
2. Las migraciones toman `BEGIN IMMEDIATE`, releen `user_version` dentro de la transacción y las tablas se crean con `IF NOT EXISTS`: dashboard, puente y preparador pueden arrancar a la vez sin error.
3. La migración 16 retira de `quote_metadata` las filas sin caso (huérfanas); antes queda la copia `.bak-v15`.
4. Un escritor que guarda un análisis (importación de Work, `save`) toma el candado de escritura antes de leer el estado: el puente no pisa lo que se hizo en el dashboard entre la lectura y la escritura. El envío y la resolución del destinatario releen el análisis dentro del candado en vez de guardar una copia vieja.
5. Las copias de seguridad tratan `-wal`/`-shm`: la copia es un solo archivo, la restauración se instala con archivo temporal y `os.replace`, borra los `-wal`/`-shm` viejos, guarda lo anterior con la API de copia de SQLite y rechaza un respaldo de un esquema más nuevo. `verificar_ultima_copia` abre la última copia en una carpeta temporal y comprueba integridad y versión.

**Casos de aceptación**
- B0-1 · Una base nueva queda en WAL, con `foreign_keys=1` y `busy_timeout=15000`.
- B0-2 · Seis hilos abren la misma base vacía a la vez: ninguno falla y `user_version` es la última.
- B0-3 · Con otro proceso escribiendo, `record_event` espera en vez de fallar a los 5 s.
- B0-4 · Un guardado de Work con el estado leído tarde no sobrescribe un cambio hecho dentro del candado.
- B0-5 · Restaurar no deja `-wal`/`-shm` viejos y la base queda íntegra.

### U-43 · Red con tiempo límite y reintentos solo donde es seguro
**Estado: implementado** (pruebas en `tests/test_red_segura.py`). Decisión: D-108.
1. Todas las llamadas a Gmail y Drive esperan como máximo 30 s por operación (también la renovación del token).
2. Las lecturas (GET) y las escrituras idempotentes (PUT/PATCH) se reintentan hasta 3 veces con espera creciente ante 429, 5xx o corte de red. **Nunca** se reintenta un POST: enviar, crear borrador o carpeta.
3. Un 404 al enviar un borrador solo cae a «mensaje nuevo» si vino de actualizar el borrador, no de enviarlo (B3-08): si el envío del borrador da 404 no se manda un segundo correo.
4. El servidor del dashboard corta una conexión que no habla en 30 s. Ollama y la API ya tenían tiempo límite propio, calculado según el documento.

### U-44 · El token de Google y el de CSRF se escriben completos y privados
**Estado: implementado** (pruebas en `tests/test_archivos_privados.py`). Decisión: D-109.
Se escriben en un archivo temporal de la misma carpeta y se mueven con `os.replace` (nunca queda a medias); el permiso queda solo para el usuario (`0600` en POSIX; en Windows `icacls` quitando la herencia). Si no se puede restringir, se avisa en el registro sin contenido y se guarda igual.

### U-45 · Quien revisa ve de dónde sale cada destinatario
**Estado: implementado** (pruebas en `tests/test_destinatarios.py`). Decisión: D-110. R8 intacto: sigue decidiendo una persona.
Los destinatarios externos al contacto se mantienen, pero el correo a una dirección distinta del destinatario del caso muestra su origen (contacto registrado, destinatario del caso, participante del hilo, remitente de un cambio, vinculado de otro hilo) antes de enviar, y el hecho «Correo enviado…» lo repite en el historial.

### U-46 · Las transiciones de estado se comprueban contra la tabla (solo registrar)
**Estado: implementado** (pruebas en `tests/test_estados.py`). Decisión: D-111.
`spec/transiciones.json` incluye ahora crear carpeta, enviar y terminar envío, guardar borrador, unir/descartar, rescatar, hilo no disponible, salida de `sending` y el análisis del modo Python. `cambiar_estado` / `verificar_transicion` (`anka/servicios/estados.py`) consultan la tabla: una transición que no está **se permite igual** y deja un hecho `transicion_no_prevista` (caso, origen, destino, evento) y una línea de registro. Nunca bloquea.

### U-47 · Los tres modos protegen igual un caso avanzado
**Estado: implementado** (pruebas en `tests/test_paridad_estados.py`). Decisión: D-112. Cumple «los tres modos se comportan igual».
Un análisis (de Work o del modo local/api) nunca hace retroceder un caso `ready` (carpeta creada) ni `sending` (envío en curso); un caso `response_sent` solo se mueve si llega un mensaje **nuevo** del cliente, no por volver a analizar el mismo (TR-07, TR-08, TR-58). El análisis sí se guarda (tabla, resumen, control de cambios). En el modo Python, además, un caso protegido no recibe un envío automático de preguntas ni otra carpeta. La regla vive en un solo lugar (`estado_tras_un_analisis`, `anka/dominio/status.py`) y la usan `work_import` y `RevisorDeHilos`.
Fuera de alcance: guardar un borrador desde el dashboard sobre un caso `ready` o `response_sent` reabierto sigue pasándolo a `draft_created` (es igual en los tres modos, TR-43 lo registra como no previsto); las transiciones no previstas siguen solo registrándose (D-111).

### U-48 · El dashboard sigue ágil con cientos y miles de casos (Ola B1 del plan de backend)
**Estado: implementado** (pruebas en `tests/test_rendimiento.py`); lo que quedó pendiente se cerró en U-49. Decisión: D-113 (ajustada por D-114). No cambia ningún texto ni estado, salvo el pie «Ver más» de las listas largas. No toca R8, D-27 ni la paridad de los tres modos.

**Motivo.** Con 500 casos sintéticos, abrir «Mis casos» o un caso tardaba 2,3 a 5 s (la agrupación de proyectos comparaba todos contra todos con `difflib`), ocho pestañas a la vez 44 s, exportar todo a Excel 63 s, y cada página repetía ~48 KB de CSS. Cada pestaña con avisos en vivo consultaba la base una vez por segundo.

**Qué debe cumplirse**
1. **Agrupar proyectos sin comparar todos contra todos.** `agrupar` solo compara los pares que comparten un documento (mismo nombre y texto leído) o una palabra del nombre del proyecto con los mismos números; los demás nunca podían parecerse. Los grupos, su orden y su motivo son **idénticos** a los de comparar todos los pares (`agrupar_lento`, que las pruebas conservan como referencia). La comparación de nombres descarta antes lo que no puede llegar a 0,85 y recuerda sus respuestas.
2. **El resultado se recuerda mientras nada cambie.** Las listas y el panel «Parece el mismo proyecto» usan `agrupar_en_cache`: la clave es el marcador de cambios (`change_marker`), los pares «No es el mismo proyecto» y los datos que leen las fichas (cliente confirmado, código, nombre y adjuntos de cada caso). Si cualquiera cambia, se recalcula. Se recuerdan posiciones, no casos: lo que se devuelve siempre contiene los casos recién leídos.
3. **Excel lineal.** `build_workbook` escribe en modo de solo escritura: el tiempo crece en línea recta con las filas. Conserva el contenido celda por celda y el formato esencial: encabezado en negrita blanca sobre verde, color por estado de actividad, texto ajustado y arriba, filtro, primera fila y primeras cuatro columnas fijas y anchos de columna.
4. **Índices en la base.** La migración 17 crea (con `IF NOT EXISTS`) los índices de la lista de casos (orden por «más reciente»), del estado, del código T, de los eventos por tipo y por fecha de registro, de los cambios y vínculos por estado y de las novedades sin leer. Solo aceleran lecturas: ningún dato cambia (la derivación de novedades conserva su orden de siempre con `ORDER BY rowid`).
5. **Listas de a 200.** «Mis casos» y «Administrar casos» muestran las primeras 200 filas (un grupo de proyecto cuenta como una). Si hay más, debajo de la lista aparece «Se muestran N de M casos.» y el enlace «Ver K más» (`?mostrar=N+200`, que vuelve a mostrar todo lo anterior y, desde U-49, conserva la búsqueda y los filtros: ya no miran solo lo mostrado). Con 200 o menos casos la página es la de siempre. Los números de las etapas y el resumen cuentan todos los casos. El pie usa solo palabras ya permitidas (sin sinónimos retirados de D-97).
6. **Respuestas más livianas.** Si el navegador acepta `gzip`, el HTML, el JSON y el CSS de 1 KB o más se envían comprimidos (`Content-Encoding: gzip`, `Vary: Accept-Encoding`); los demás tipos (Excel, imágenes, fuente, canal de avisos) no. `/api/casos`, `/api/casos/{id}` y `/openapi.json` salen en JSON compacto. El servidor habla HTTP/1.1 (la conexión se reutiliza; cada respuesta lleva su longitud, el canal de avisos `Connection: close` y un `POST` rechazado antes de leer su cuerpo cierra la conexión).
7. **Archivos fijos con caché.** `/static/…` lleva `ETag`; con `If-None-Match` igual responde `304` sin cuerpo. El CSS compartido ya no va dentro de cada página: es `/static/style.<huella>.css`, donde la huella es la del contenido, con `Cache-Control: public, max-age=31536000, immutable`. La fuente y el logo siguen con `max-age=86400`. Lo demás (páginas, JSON, `303`, `/eventos`) sigue con `no-store`. La lista de nombres es fija: cualquier otro nombre es `404`.
8. **Un solo cálculo por segundo para los avisos en vivo.** Todas las pestañas conectadas a `/eventos` leen el marcador de `MarcadorCompartido`, que consulta la base como máximo una vez por segundo sin importar cuántas pestañas haya.

**Casos de aceptación**
- E1 · Sobre datos sintéticos (800 casos con clientes, códigos, números y adjuntos repetidos, y pares separados), `agrupar` y `agrupar_lento` devuelven los mismos grupos, en el mismo orden y con el mismo motivo; con 600 casos `agrupar` tarda menos de 3 s (umbral holgado).
- E2 · Cambiar el nombre, el cliente confirmado, el código o un par separado de un caso recalcula los grupos; sin cambios, la segunda llamada no vuelve a comparar.
- E3 · El Excel nuevo y el anterior tienen las mismas celdas, el mismo filtro y los mismos estilos esenciales; 3 000 filas se escriben en menos de 8 s.
- E4 · Una base en el esquema 16 pasa al 17 sin perder datos y las consultas de la lista, de los eventos recibidos y del marcador usan los índices (`EXPLAIN QUERY PLAN`).
- E5 · Con 450 casos, «Mis casos» y «Administrar casos» muestran 200 filas y «Ver 200 más»; con `?mostrar=400`, 400 y «Ver 50 más»; con 150, ni pie ni enlace.
- E6 · Con `Accept-Encoding: gzip`, la página y `/api/casos` llegan comprimidas y descomprimen al mismo contenido; sin esa cabecera, sin comprimir. `/static/<css con huella>` responde `304` con su `ETag`.
- E7 · Veinte lecturas seguidas del marcador compartido consultan la base una vez; pasado el segundo, otra.

**Pendiente (no se hizo, queda registrado):** el evento de avisos con el contenido de `/state` incluido; devolver los Excel por partes o en segundo plano. Lo demás que quedó pendiente aquí (JavaScript con huella y CSP sin `'unsafe-inline'` en scripts, búsqueda y filtros en el servidor, listar sin leer `analysis_json`) se hizo en **U-49**.

### U-49 · Listar sin leer el análisis, buscar en el servidor y JavaScript en archivos con huella (cierre de la Ola B1)
**Estado: implementado** (pruebas en `tests/test_lista_resumen.py`, `tests/test_busqueda_en_servidor.py` y `tests/test_scripts_validos.py`). Decisión: D-114 (ajusta D-113). No cambia ningún texto ni estado de la persona salvo el pie de «Ver más» (ya no dice que la búsqueda mira solo lo mostrado) y el botón «Buscar» junto a los campos. No toca R8, D-27 ni la paridad de los tres modos.

**Motivo.** Con 5 000 casos sintéticos, armar la lista tardaba ~0,8 s porque cada caso leía e interpretaba su análisis completo y recalculaba la evaluación de recepción; la búsqueda y los filtros solo veían las 200 filas mostradas; y ~37 KB de JavaScript se repetían en cada página y obligaban a `'unsafe-inline'` en la CSP.

**Qué debe cumplirse**
1. **La lista no interpreta el análisis completo.** `RepositorioDeCasos.listar()` lee de `quotes` todas las columnas menos `analysis_json` y un **resumen** por caso (`resumen_json`, versión `resumen_v`): el valor exacto de las claves que pintan las listas (`project_name`, `received_at`, `routing`, `attachments`, `preguntas_enviadas`), si el análisis está vacío y lo derivado que cuesta recorrer actividades (servicios y propuesta del modelo). El `Caso` de una lista lleva un análisis **perezoso** (`AnalisisPerezoso`): esas claves salen del resumen y cualquier otra (o recorrerlo) lee y guarda el análisis completo de ese caso, una sola vez; la evaluación de recepción (`EvaluacionPerezosa`) se calcula solo si alguien la mira. `listar(completo=True)` y `obtener()` (el detalle y todo lo que escribe) leen el análisis completo como siempre.
2. **Equivalencia.** Para cualquier base, `listar()` y `listar(completo=True)` devuelven los mismos casos, en el mismo orden (`COALESCE(updated_at, first_seen_at, '') DESC, thread_id DESC`, ahora ordenado en memoria) y con los mismos campos y análisis; la prueba lo compara campo por campo sobre datos variados (análisis vacío, dañado, sin `routing`, con adjuntos, con propuesta, descartados).
3. **El resumen nunca queda viejo.** La migración 18 (con `BEGIN IMMEDIATE`, como todas) añade `resumen_json` y `resumen_v` solo si faltan, crea un disparador (`IF NOT EXISTS`) que borra el resumen cuando cambia `analysis_json` —lo escriba quien lo escriba— y rellena el de los casos existentes (solo los que no lo tienen: correrla otra vez no cambia nada). Un resumen ausente o de otra `VERSION_DEL_RESUMEN` se calcula al listar y se guarda **solo si el análisis no cambió mientras tanto** y sin esperar el candado (con la base ocupada se sigue con lo calculado). Guardar el resumen no cambia `updated_at` ni el marcador de cambios.
4. **Búsqueda y filtros en el servidor.** `GET /` acepta `q` (texto, hasta 200 caracteres, sin mayúsculas, sobre proyecto, cliente, código y servicios), `etapa` (`atender`, `esperando`, `listo`, `cotizando` o `todos`; sin el campo entra en `atender` si hay algo que atender) y `mostrar`. `GET /requests` acepta `q`, `etapa`, `tipo` (tipo de cliente), `orden` (`reciente`, `antigua`, `urgencia`), `agrupar` y `mostrar`. Se filtra y ordena **todo** antes de contar y de mostrar las primeras 200 filas; «Se muestran N de M casos» y el enlace «Ver K más» (que conserva `q`, `etapa`, `tipo`, `orden` y `agrupar`) hablan del total que cumple. Los números de los chips y del resumen siguen contando todos los casos. En «Administrar casos» de cada grupo de proyecto quedan los correos que cumplen y, si queda uno solo, se ve como fila suelta.
5. **Funciona sin JavaScript.** El campo de búsqueda, el orden y el tipo son un formulario `GET` con botón «Buscar», y las etapas son botones del mismo formulario (`aria-pressed` marca la elegida, además de la marca visual de siempre). El contador (`#n-visibles`, `role="status"`) y el aviso de «sin resultados» vienen del servidor. Con JavaScript solo se añade una mejora: escribir afina al instante las filas ya cargadas (diciendo que «Pulsa Buscar para mirar todos» si hay más) y cambiar el orden o el tipo envía el formulario.
6. **JavaScript en archivos con huella.** Los scripts salen de las páginas a `templates/app.js` (cargado con `<script src defer>` al final) y `templates/tema.js` (en el `<head>`, sin `defer`, para pintar el tema guardado antes del primer cuadro). Se publican como `/static/app.<huella>.js` y `/static/tema.<huella>.js` con `Cache-Control: public, max-age=31536000, immutable`, `ETag` y gzip, igual que el CSS. Ninguna página lleva JavaScript en línea ni manejadores `onclick…`. Lo que hacían se conserva: recarga segura, diálogos de confirmación y resumen de la T, aviso de cita, tema, editor de tabla, «Enviando…», partículas (U-38, con el bloque `// <física>` en `app.js`) y «reducir movimiento».
7. **CSP.** `script-src 'self'` (sin `'unsafe-inline'`). `style-src` conserva `'unsafe-inline'`: el `<noscript><style>` del layout y los anchos de las barras de estadísticas son estilos en línea; un estilo no ejecuta código y quitarlos exigiría cambiar el diseño. `/docs` no cambia (su único script en línea sigue autorizado por `nonce`).
8. **Los scripts se comprueban.** `node --check` pasa por `app.js` y `tema.js` y, por si reaparece uno, por todo `<script>` en línea de las páginas; otra prueba exige que ninguna página lo tenga.

**Casos de aceptación**
- F1 · Con 5 000 casos sintéticos, `listar()` tarda menos de 0,4 s en una máquina de desarrollo (meta ~0,25 s; antes ~0,8 s) y listar una página de «Mis casos» no interpreta ningún análisis completo (`AnalisisPerezoso.cargado` sigue en falso).
- F2 · `listar()` y `listar(completo=True)` son iguales campo por campo; leer `activities` de un caso de la lista da lo mismo que en el detalle.
- F3 · Una base en el esquema 17 pasa al 18 sin perder datos, con todos los resúmenes puestos; correr la migración dos veces deja lo mismo; cambiar el análisis de un caso borra su resumen y la lista siguiente lo recalcula.
- F4 · Con 450 casos, buscar `q=obra 449` encuentra un caso que no estaba entre las 200 primeras; la etapa filtra antes de paginar; el enlace «Ver más» conserva los filtros; el contador dice el total real.
- F5 · La página de «Mis casos» no lleva `<script>` en línea ni `onclick`; carga `tema` y `app` con huella; la CSP de las páginas no tiene `'unsafe-inline'` en `script-src`; `/static/app.<huella>.js` responde `304` con su `ETag`.

### U-50 · Errores de la API con una sola forma (Ola B2 del plan de backend)
**Estado: implementado** (pruebas en `tests/test_errores_de_api.py`). Decisión: D-115. No cambia ninguna página HTML ni ningún texto de la persona. No toca R8, D-27 ni la paridad de los tres modos.

**Motivo.** Un cliente que lee `/api/...` recibía a veces JSON (`{"error": "..."}`), a veces una página HTML (un 500, un 404, un `Host` no permitido) y nunca un `405`: un `POST` a una ruta de lectura era «404» y `HEAD`/`PUT`/`OPTIONS` caían en el `501` HTML de `http.server`. No había un código que un programa pudiera leer.

**Qué debe cumplirse**
1. **Una sola forma.** Todo error bajo `/api/...` y en `/state`, `/eventos` y `/openapi.json` responde `application/json` con `{"error": {"codigo": "...", "mensaje": "..."}}`: `codigo` estable y sin tildes (`solicitud_no_valida` 400, `solicitud_rechazada` 403, `no_encontrado` 404, `metodo_no_permitido` 405, `cuerpo_demasiado_grande` 413, `error_interno` 500, `metodo_no_implementado` 501, `no_disponible` 503) y `mensaje` en español claro. Un `500` añade `id` (el código de 8 caracteres que ya queda en el registro) y nunca el detalle técnico.
2. **Coherencia HTTP.** Una ruta que no existe es `404`; existe pero con otro método, `405` con `Allow`; `Host`/`Origin`/token inválidos, `403`; un valor que la aplicación rechaza, `400`; un fallo propio, `500`; un método que el servidor no conoce, `501`. `PUT`, `DELETE`, `PATCH` y `OPTIONS` no se aceptan en ninguna ruta. Pedir una acción con `GET` o una página con `POST` también es `405` (en HTML, con la misma página de aviso de siempre). Un token inválido sigue siendo `403` antes que `405`.
3. **`HEAD`.** Funciona en todo `GET`: mismo estado, mismas cabeceras y `Content-Length`, sin cuerpo; en `/eventos` solo confirma las cabeceras y no abre la conexión larga.
4. **Excepción deliberada.** `GET /api/salud` con una conexión caída sigue siendo `503` con el informe de salud: es el resultado de la consulta, no un fallo de ella.
5. **Documentado.** El esquema `Error` está en `components.schemas` de `openapi.py` y lo referencian las respuestas `403`, `404`, `405`, `500` y `503` de las rutas JSON; `spec/07-api.md` explica la tabla de códigos. Las páginas HTML siguen mostrando su aviso con salida (y el código de error en un 500).

**Casos de aceptación**
- G1 · Para cada ruta JSON: `Host` ajeno es `403` JSON, `POST` es `405` JSON con `Allow: GET, HEAD`, `PUT`/`DELETE`/`PATCH`/`OPTIONS` es `405` JSON, `HEAD` no tiene cuerpo; `/api/xyz` y `/api/casos/no-existe` son `404` JSON.
- G2 · Una excepción dentro de `/api/casos` es `500` JSON con `id`, el `id` está en el registro y el texto de la excepción no sale; un `ValueError` es `400` JSON.
- G3 · `GET /no-existe` y `GET /mode` siguen siendo páginas HTML (`404` y `405`); un `POST` a cada página es `405` HTML.

### U-51 · Una sola política de validación del contrato con Work (Ola B2 del plan de backend)
**Estado: implementado** (pruebas en `tests/test_politica_de_contrato.py`; ajusta `tests/test_entradas_adversas.py`). Decisión: D-116. No cambia el prompt de Work ni el esquema salvo una anotación `"version": 1`. No toca R8 ni D-27.

**Motivo.** El puente (`work_sync`) validaba con el esquema y la tolerancia «tolerar y avisar»; la importación (`dashboard-upsert`) usaba su propia lista de campos y tipos. Un mismo JSON podía entrar por un camino y rechazarse por el otro (sin `message_id` o con actividades como texto), y la tolerancia a un `size_bytes` como texto nunca se ejercía en el puente. Además el validador del esquema ignoraba en silencio cualquier palabra clave que no conocía y lanzaba `KeyError` con un `type` desconocido.

**Qué debe cumplirse**
1. **Un solo camino.** `work_sync` y `work_import._decode_work_payload` llaman a las mismas funciones de `anka/dominio/tolerancia_work.py` (`comprobar_identidad` y `normalizar_analisis`: identidad, estado, reparación con aviso y esquema). Se rechaza lo mismo (identidad ausente, estado desconocido, lo que tras reparar aún incumple el esquema) y se acepta con aviso lo mismo (tipos, enumerados, listas escritas como texto, nulos, `engine` o nombre de proyecto ausentes). Un mismo JSON deja el mismo análisis por los dos caminos. La importación a mano sigue diciendo qué estado llegó cuando es desconocido.
2. **Cambio visible.** Lo que la importación a mano rechazaba por tipo (`activities` o `cited_codes` como número) ahora se acepta con aviso (el campo queda vacío y el caso lo cuenta en «Work escribió algo distinto de lo esperado»), como ya hacía el puente; y ahora exige `message_id`, como el puente.
3. **El validador no calla.** `validate` devuelve como error —con el nombre de la palabra— toda palabra clave del esquema que no comprueba (`pattern`, `oneOf`, `additionalProperties`, `minItems`, `$ref`…) y todo `type` desconocido (ya no `KeyError`). Solo se ignoran las anotaciones (`$comment`, `description`, `version`…). Una prueba exige que el esquema real use solo lo que se comprueba.
4. **Versión.** El esquema declara `"version": 1`. Work puede escribir `schema_version` (opcional, fuera del esquema, así que el prompt no cambia): si es mayor que la que Anka entiende, el análisis se acepta y el caso avisa «Work declara la versión N del contrato y Anka entiende hasta la 1». Ausente, ilegible o igual: sin aviso.

**Casos de aceptación**
- H1 · Para catorce variaciones de un mismo JSON (completo, sin `message_id`, sin `engine`, estado sin tildes, estado desconocido, tamaño como texto, actividades como texto o número, nulos…), el puente y la importación aceptan o rechazan igual y dejan el mismo análisis.
- H2 · Un esquema con `pattern` o con `type: date` produce errores nombrados, no se ignora ni lanza.
- H3 · `schema_version: 2` se acepta con un aviso; `"3"` también; `"x"`, `null` o `true` no avisan.

### U-52 · Una tabla única de estados y consultas por estado solo en el repositorio (Ola B2 del plan de backend)
**Estado: implementado** (pruebas en `tests/test_tabla_de_estados.py` y `tests/test_arquitectura.py`). Decisión: D-117. No cambia ninguna etiqueta, etapa ni estado. No toca R8, D-27 ni la paridad de los tres modos.

**Motivo.** Añadir un estado obligaba a tocar a mano `STATUS_LABELS`, `CLOSED_STATUSES`, `ACTION_STATUSES`, `REVIEW_QUEUE_STATUSES`, los estados que un análisis no mueve, la etapa de la lista (`case_stage`) y las consultas con estados escritos en el SQL (`status='waiting'` en los recordatorios, f-strings en el ciclo de revisión), y solo una prueba avisaba si se olvidaba uno.

**Qué debe cumplirse**
1. **Una fila por estado.** `ESTADOS` (`anka/dominio/status.py`) dice, por cada `QuoteStatus`, su etiqueta, su etapa visible y si pide acción, si está cerrado, si espera acción humana, si está en la cola de revisión, si un análisis nunca lo mueve, si solo lo mueve un mensaje nuevo del cliente, si genera la novedad «Respuesta sugerida» y su etapa cuando el último correo fue un acuse. `STATUS_LABELS`, `CLOSED_STATUSES`, `ACTION_STATUSES`, `REVIEW_QUEUE_STATUSES`, `ESTADOS_QUE_UN_ANALISIS_NO_MUEVE`, `ESTADOS_CON_RESPUESTA_SUGERIDA`, `estado_tras_un_analisis` y el recurso por estado de `case_stage` salen de esa tabla. Añadir un estado = una fila en `ESTADOS` + `spec/transiciones.json` (+ el aviso de la pantalla del caso y las pruebas que ya exigen cada uno).
2. **Exhaustividad.** Una prueba exige una fila por cada `QuoteStatus`, los estados de `transiciones.json` iguales a los del enum, y congela los grupos de siempre para que añadir un estado no cambie a los demás.
3. **Consultas por estado en el repositorio.** Ninguna consulta fuera de `servicios/repositorio.py` escribe un estado en el SQL: `hilos_en_estado`, `hilos_descartados_por_reanalizar`, `casos_esperando_con_recordatorios` y `filas_de_la_cola_de_revision` los consultan con parámetros. Una prueba recorre el paquete y falla si aparece un literal de estado en una consulta.
4. **Presupuestos de arquitectura.** Ningún módulo de `anka/` pasa de 800 líneas; los que ya pasaban (hoy `web/pantalla_caso.py`, tope 1100) tienen un tope congelado que solo puede bajar y una prueba avisa si ya sobra. Solo `servicios/repositorio.py` y `infra/store.py` escriben en la tabla `quotes`.

### U-53 · Errores de validación tipados y fallos de red distintos de los propios (Ola B2 del plan de backend)
**Estado: implementado** (pruebas en `tests/test_errores_internos.py`). Decisión: D-118. No cambia ningún texto de validación. No toca R8, D-27 ni la reserva de envío.

**Motivo.** Los mensajes para la persona salían de `ValueError`, el mismo tipo que lanzan Python y las librerías: un bug se confundía con un dato mal escrito. Y al emitir la T, cualquier excepción decía «Gmail no respondió», aunque fuera un `KeyError` propio.

**Qué debe cumplirse**
1. **`ErrorDeValidacion`.** `anka/dominio/errores.py` define `ErrorDeValidacion(ErrorDeAnka, ValueError)` para lo que se le explica a la persona («Escribe algo antes de guardar.»). Es un `ErrorDeAnka` (su texto se muestra) y, por compatibilidad, un `ValueError` (quien lo capturaba sigue capturándolo; el servidor lo sigue respondiendo con `400`). Los módulos principales (tabla de actividades, preguntas, acciones del caso, control de cambios, carpetas, ediciones, recordatorios, `quotes`, `vinculos`, `revision_tecnica`, repositorio e importación de Work) lo lanzan en vez de `ValueError`; un `ValueError` suelto vuelve a significar un fallo de Python o de una librería, cuyo texto no se muestra. Quedan fuera, sin cambio: la reserva de envíos (`envios.py`), las copias de seguridad, el lector geográfico y el modelo.
2. **Red o programa.** `es_fallo_externo` (`infra/google_services.py`) dice si un fallo viene de la red o de Google (`OSError`, tiempos agotados, errores de `httplib2`/`googleapiclient`/`google.auth`, `AutorizacionDeGoogle`). Al emitir la T, al llenar la carpeta y al crear la carpeta sola, un fallo externo se registra como aviso y se explica como antes («Gmail no respondió»); cualquier otro se registra como error con su traza y un código de 8 caracteres, y la persona lee «ocurrió un error del programa, no de Gmail; si se repite, avisa con el código …». La T emitida y el texto del correo se conservan igual.

**Casos de aceptación**
- I1 · `ErrorDeValidacion` es `ErrorDeAnka` y `ValueError`; una acción que lo lanza responde `400` con su texto; un `ValueError("invalid literal…")` responde `400` sin ese texto.
- I2 · Emitir la T con `ConnectionError` en el envío dice «Gmail no respondió» y registra un aviso; con `KeyError` no culpa a Gmail, registra un error con traza y da un código.

### U-54 · `quotes.py` deja de ser un módulo-cajón (Ola B2 del plan de backend)
**Estado: implementado** (pruebas en `tests/test_arquitectura.py`, `tests/test_vinculos.py`, `tests/test_union_de_proyectos.py` y `tests/test_review_decision.py`, que no cambian). Decisión: D-119. No cambia ningún comportamiento.

**Motivo.** `quotes.py` se presentaba como fachada pero alojaba, en ~800 líneas, la lógica de vínculos y unión de casos, las decisiones de revisión técnica, la cola del piloto local y un comando que imprime por consola.

**Qué debe cumplirse**
1. **Tres módulos propios.** `servicios/vinculos.py` (confirmar o descartar un correo de otro hilo, unir o separar proyectos), `servicios/revision_tecnica.py` (decidir la revisión técnica, volver a decidir, asumir una lectura sin verificar, adoptar o descartar la tabla del modelo) y `servicios/cola_del_piloto.py` (paquete de texto y casos que esperan a una persona en el modo local). `list_reviews`, que imprime, pasa a `cli/app.py`.
2. **Fachada fina.** `quotes.py` conserva lo de lectura, datos comerciales, descartar/rescatar, eliminar, novedades y el marcador de cambios, y reexporta los nombres movidos: ningún importador, de código o de pruebas, cambia. Los módulos nuevos se nombran en el «Mapa de módulos» de `spec/06-arquitectura.md`.

### U-55 · El lector de adjuntos no se rinde ante lo que sí se puede leer (experimento de lectura 2026-10)
**Estado: implementado** (pruebas en `tests/test_lector_de_adjuntos.py`). Decisión: D-120. No cambia R8, D-27, los tres modos ni el contrato con Work (la cobertura sigue siendo `completa`, `parcial` o `no_legible`; los motivos nuevos viajan en `reason`).

**Motivo.** Un experimento de solo lectura sobre 424 archivos reales (20 proyectos) midió dónde el lector de Python fallaba o gastaba tiempo sin necesidad: 3 de 53 KML/KMZ no abrían por «unbound prefix»; los `.shp` sueltos se rechazaban; un PDF de 265 páginas se rechazaba entero; el OCR de PDF escaneados tardaba de media 17 s por página y 22 archivos (5 %) agotaron 420 s; RAR, 7z, DWG y DXF (10 % de los archivos) devolvían «formato no soportado» sin decir qué hacer.

**Qué debe cumplirse**
1. **KML/KMZ/GPX/WPML con prefijos XML sin declarar** (`gx:`, `atom:`, otros): se declaran en la raíz y se vuelve a leer; no se pierde ninguna geometría ni atributo y el texto lo dice («Lectura tolerante»). Un XML con `<!ENTITY>` se rechaza (nunca se resuelven entidades externas ni se expanden internas).
2. **Shapefile.** Un `.shp` suelto se lee con la biblioteca estándar: tipo de geometría, extensión XY y número de registros; con `.dbf` también campos y primeros registros; lo que falte (`.dbf`, `.shx`, `.prj`) se marca `LECTURA PARCIAL` / `SISTEMA DE COORDENADAS NO DECLARADO`. Un `.dbf` suelto se lee (campos y registros) y queda parcial. Un shapefile con sus hermanos (`.shx`, `.dbf`, `.prj`, `.cpg` del mismo nombre y mensaje) cuenta como **una sola entrada**: los hermanos se leen con el `.shp` y aparecen como «ignorado: parte del shapefile …». Un ZIP con un `.shp` incompleto lo lee igual y marca lo que falta.
3. **PDF largo.** Un PDF de más de 600 páginas (`MAX_PDF_PAGINAS_LEIDAS`) ya no se rechaza: se leen las primeras 600 y el texto dice «COBERTURA PARCIAL: PDF de N páginas; se leyeron las páginas 1–600; faltan …». El OCR se limita a 200 páginas por archivo (`MAX_OCR_PAGES`); las demás se listan como «sin OCR».
4. **Escaneados y tiempo de OCR.** Tres claves de `config.json`, todas con un valor por defecto que conserva el comportamiento de siempre: `ocr_segundos_por_archivo` (0 = sin tope), `ocr_segundos_por_caso` (0 = sin tope) y `ocr_omitir_escaneados` (`false`). Al agotarse un presupuesto, el OCR que falta se omite y el texto dice «COBERTURA PARCIAL: OCR omitido por tiempo» con las páginas sin OCR (el motivo: «OCR omitido por tiempo»). Con `ocr_omitir_escaneados: true` y al menos la mitad de las páginas de un PDF con menos de 100 caracteres de texto nativo, **no se hace OCR** a esas páginas: «REQUIERE VISIÓN» con sus páginas (si no queda texto alguno, el archivo es `no_legible` con motivo «requiere visión»). Una imagen suelta con la política activa también es `no_legible` «requiere visión». El presupuesto se mide entre páginas (un OCR en curso no se interrumpe).
5. **Excel.** Las hojas ocultas (`hidden`, `veryHidden`) se leen y se marcan «HOJA OCULTA» con su visibilidad; las fórmulas no se recalculan: se muestra la fórmula y el valor guardado, y «FÓRMULA SIN VALOR CALCULADO» si no hay.
6. **Sin lector.** RAR, 7z y DWG devuelven `no_legible` con un motivo en español y la marca «pedir al cliente un formato abierto» (el DWG dice su versión si la cabecera la trae). Un DXF de texto se lee lo mínimo (versión, capas, conteo de entidades por tipo y textos) y queda `parcial`; un DXF binario es `no_legible` con la misma marca. Dentro de un ZIP, los RAR/7z/DWG se listan con la marca.

**Casos de aceptación**
- J1 · Un KML con `gx:` y `atom:` sin declarar abre con todos sus `Placemark`, `gx:Track` y atributos; un KML con `<!ENTITY>` se rechaza; una entidad externa no lee ningún archivo.
- J2 · Un `.shp` solo da tipo, extensión y registros y es parcial; con `.dbf` da campos; con todos sus hermanos es una sola entrada en el correo, en el expediente de Drive y en el paquete de Work.
- J3 · Un PDF de más páginas que el tope se lee parcialmente con las páginas leídas dichas.
- J4 · Con presupuesto de 0,05 s y un motor de OCR lento, solo se hace OCR a la primera página y el resto queda «sin OCR por tiempo»; con la política activa un PDF escaneado no llama al motor de OCR.
- J5 · Un libro con una hoja `veryHidden` la entrega marcada y con una fórmula sin recalcular.
- J6 · `.rar`, `.7z` y `.dwg` dan `no_legible` con la marca; un `.dxf` sencillo da capas y textos.

### U-58 · Encabezado compacto, verbo primero y avisos accesibles (cierre de las Olas 2 y 3)
**Estado: implementado** (pruebas en `tests/test_cabecera_y_filas.py`). Decisión: D-123. Respeta los textos únicos de D-97, el estilo ANKA y la accesibilidad de U-37; no cambia R8, D-27 ni los tres modos.

**Qué debe cumplirse**
1. **Encabezado en una barra (≤ 600 px).** El logo, «Buscar correos nuevos» y «Ajustes» van en una sola fila (el botón puede partir su texto en dos líneas); «Mis casos» se queda solo para lectores (el enlace del logo ya dice «Anka, mis casos»); el menú de Ajustes se despliega a todo el ancho encima del contenido y sigue plegado hasta abrirlo. Cada control mide al menos 44 px de alto y ningún texto baja de 14 px. Si un chip de estado está visible, baja a una segunda fila.
2. **Verbo primero.** En las filas de «Mis casos» y de «Administrar casos», la primera línea empieza por lo que hay que hacer: «Revisar el correo · Cliente · Proyecto». El botón de la derecha conserva exactamente el mismo texto (D-97); el verbo repetido en la primera línea es solo visual (`aria-hidden`) para que un lector de pantalla no lo lea dos veces. La segunda línea lleva el código de oferta cuando existe.
3. **La marca «Ahora» / «Antes de seguir» es texto.** Es un elemento (`.marca-paso`) en los cuatro avisos de arriba del caso, no contenido generado por CSS, así que un lector de pantalla la lee.
4. **Todo fallo es alerta.** Las acciones que terminan en fallo sin pasar por la guía (buscar correos nuevos que falla, actualizar la hoja de Google que falla, borrar casos que pide ELIMINAR) se anuncian con `role="alert"` y «Error:» como las demás.

**Casos de aceptación**
- E1 · A 360 px de ancho el encabezado cabe en una fila sin desbordar y Ajustes abre una caja que ocupa el ancho de la pantalla.
- E2 · La lista muestra «Revisar el correo · Acme · Llanos 8» y el botón dice «Revisar el correo».
- E3 · Un «Buscar correos nuevos» sin conexión muestra una alerta con el motivo y qué hacer.

### U-57 · Lo que se decide por error se puede deshacer y borrar lo importante pide más (cierre de la Ola 1)
**Estado: implementado** (pruebas en `tests/test_deshacer.py`). Decisión: D-122. Completa U-35 (ítems 1.11 y 1.12 del plan de experiencia). No cambia R8, D-27 ni los tres modos; ninguna acción envía nada.

**Motivo.** «No es un cambio» y «Dejar mi tabla» eran de un solo clic y sin vuelta atrás; «Borrar» un caso con código T o con un correo ya enviado pedía el mismo «¿Borrar?» que uno recién llegado; y se podía vaciar un dato comercial por accidente.

**Qué debe cumplirse**
1. **Reabrir «No es un cambio».** La pestaña «Historial» del caso muestra «Decisiones que puedes deshacer» con los mensajes marcados «No es un cambio» (los 5 más recientes) y un botón «Reabrir este mensaje». `POST /change` con `decision=reopen` vuelve el mensaje a «por revisar», deja el hecho `change_reopened` (quién y cuándo) en el historial y no hace nada si el mensaje ya estaba por revisar o ya se confirmó como cambio. El mensaje de éxito de «No es un cambio» dice dónde se deshace.
2. **Recuperar la propuesta de Work.** «Dejar mi tabla» guarda la propuesta descartada (`propuesta_descartada` en el análisis); el mismo panel ofrece «Recuperar la propuesta» (`POST /proposal` con `decision=restore`): la propuesta vuelve a estar pendiente, la tabla de la persona no se toca y queda anotado. No se ofrece si ya hay otra propuesta pendiente; aceptar una propuesta borra la guardada.
3. **Borrar con código T o correo enviado.** Un caso con `quote_code` o `sent_at` solo se borra si `POST /delete` trae `confirmacion` igual a `ELIMINAR` (sin distinguir mayúsculas ni espacios en los bordes); si falta, no se borra **nada** (ni los demás casos marcados ni las carpetas de Drive) y el mensaje explica qué escribir. La lista muestra el cuadro «escribe ELIMINAR» solo cuando alguno de los casos marcados está protegido; la pantalla del caso (borrar un caso que ya no está en Gmail) lo pide con su propio cuadro y, si falla, vuelve al caso.
4. **Vaciar datos comerciales.** El formulario de cliente y notas pide confirmar («Vas a dejar vacío «Notas comerciales» (ahora dice «…»). ¿Seguimos?») cuando la persona deja vacío un campo que ya tenía texto. Es solo del formulario: `POST /metadata` directo (API) no lo exige.
5. **Pendiente, con su razón.** El mensaje de éxito por cookie de un solo uso en lugar de `?message=` no se hizo: 46 referencias en 23 archivos de pruebas leen el `Location` de las redirecciones y el beneficio (no repetir el mensaje al recargar) no compensa el riesgo. Se retoma si se reescriben esas pruebas por otra razón.

**Casos de aceptación**
- D1 · Se marca «No es un cambio», se abre el Historial, se pulsa «Reabrir este mensaje» y el aviso de cambio vuelve a aparecer arriba; la cadena del historial sigue íntegra.
- D2 · Se deja la tabla propia, se recupera la propuesta y vuelve el aviso «Work propone…» con la tabla de la persona intacta.
- D3 · Con un caso con T y otro sin ella marcados, borrar sin escribir nada no borra ninguno; con «eliminar» borra los dos.

### U-56 · Las pruebas no dependen de la máquina ni del azar (pendientes pequeños 2026-10)
**Estado: implementado** (pruebas en `tests/test_entorno_aislado.py`, `tests/test_base_concurrente.py` y `tests/test_busqueda_en_servidor.py`). Decisión: D-121. No cambia lo que ve la persona ni R8, D-27 ni los tres modos.

**Motivo.** Con `OPENAI_API_KEY` definida en la máquina de quien corre las pruebas, 21 instantáneas fallaban («OpenAI» en vez de «OpenAI (sin clave)») y dos pruebas fallaban de vez en cuando por defectos reales del código, no por mala suerte.

**Qué debe cumplirse**
1. `tests/_isolate.py` (lo primero que importa cada módulo de pruebas) quita del entorno `OPENAI_API_KEY`, `ANTHROPIC_API_KEY`, `ANKA_HOJA_AUTOMATICA`, `ANKA_LOG_LEVEL`, `ANKA_NO_BROWSER` y `CODEX_HOME`. Una prueba que necesita una de ellas la fija con `mock.patch.dict(os.environ, ...)`. Toda la suite pasa igual con o sin esas variables definidas.
2. **Copia previa a migrar.** El archivo temporal de `quotes.sqlite3.bak-vN` lleva el identificador del hilo además del del proceso: dos hilos que abrían la base a la vez compartían nombre y en Windows uno recibía `PermissionError` (el servidor del dashboard es multihilo).
3. **Huella de los archivos fijos.** La caché de `anka/web/estaticos.py` no da por igual un archivo cuya fecha de modificación es de hace menos de 2 s solo porque coincidan fecha y tamaño: dos escrituras seguidas del mismo largo dejaban la huella vieja y el navegador seguiría con el JavaScript o CSS anterior.

**Casos de aceptación**
- N1 · Con `OPENAI_API_KEY=x` definida al lanzar las pruebas, ninguna instantánea ni prueba cambia de resultado.
- N2 · Seis hilos abren a la vez ocho bases con datos de un esquema anterior sin ningún error ni archivo `.tmp` sobrante.
- N3 · Un guion reescrito con el mismo tamaño y la misma fecha cambia de huella.

### U-35 · Lenguaje claro: mensajes que dicen qué pasó y qué hacer (Ola 1 del plan de experiencia)
**Estado: implementado** (pruebas en `tests/test_lenguaje_claro.py` y las de texto exacto de cada pantalla). Decisión: D-100. Acota U-31 puntos 24 y 25 (un solo texto por decisión y por acción), D-96/D-97 (nombres de estado y de decisión, que no cambian) y D-27 (la autorización de Google solo con acción explícita, que no cambia).

**Motivo.** La auditoría de textos (2026-10-14) encontró que una persona nueva se frena por la jerga («hilo», «puente», «motor», «enrutamiento», «integridad», «huella», «aplicabilidad»), por mensajes que dicen qué falló pero no qué hacer («Decisión no válida.», «No seleccionaste ninguna solicitud.») y por dos nombres para lo mismo («Actualizar correo» y «Revisar ahora»; «Eliminar», «Borrar» y «Quitar»).

**Qué debe cumplirse**
1. **Palabras de la pantalla.** Lo que la persona lee dice «conversación» o «correo», nunca «hilo» (el identificador `thread_id`, los parámetros de URL y los correos al cliente de R11 no cambian); «Revisión de correos» en vez de «puente» y «Programa que lee los correos» en vez de «motor»; «Volver a conectar Google» en vez de «Reautorizar Google»
   (el botón sigue pidiendo confirmar y la ruta `/google/reautorizar` no cambia); «Más datos» en vez de «Detalle técnico»; «Primera revisión del correo», «De dónde vino el correo» y «Lo que sabemos del cliente» en vez de «Evaluación de recepción», «Enrutamiento» y «Contexto comercial interno»; «Registro de cambios» y «anotaciones» en vez de «Acta», «hechos», «huella» e «integridad».
2. **Una acción, un nombre.** Los dos botones de revisar Gmail se llaman «Buscar correos nuevos» (el de Work solo despierta a la revisión de correos; el local la hace). Se borra, no se «elimina» ni se «quita» una fila o un caso (el botón de quitar una actividad antes de guardar sigue marcando la fila «Se borrará al guardar. Deshacer»).
3. **Mensajes con salida.** Todo mensaje de error o de éxito de las acciones dice qué pasó y, si hace falta, qué hacer; no lleva identificadores internos (`thread_id`) ni el nombre de una excepción de Python. Un `ValueError` que no es una frase escrita por Anka (`es_mensaje_para_la_persona`) no se muestra: la persona ve «No pudimos terminar lo que pediste. Lo que escribiste sigue aquí…».
4. **Un solo nombre para «listo».** Un caso en `ready` sin código se llama «Información validada» en la insignia, en la lista y en el paso (sin renombrar ningún estado: `STATUS_LABELS` no cambia).
5. **Las etiquetas visibles no son valores del contrato.** Los estados de actividad siguen siendo `confirmado`, `pendiente` y `contradictorio` (R12); el glosario conserva T, T&E, Lectura realizada y Aplicabilidad, explicados con palabras simples.

**Casos de aceptación**
- E1 · Ninguna plantilla muestra la palabra «hilo» ni «puente», «Reautorizar», «Detalle técnico», «Enrutamiento», «Evaluación de recepción», «Contexto comercial interno», «Hilo no disponible» ni «Top 6»; los textos únicos de D-97 y el «Sí, enviar al cliente» siguen en `case.html`.
- E2 · En modo local y en modo Work el encabezado ofrece «Buscar correos nuevos»; los avisos de la revisión de correos dicen «Revisión de correos: activa / con avisos / detenida / sin iniciar».
- E3 · `_guidance` con un `ValueError("invalid literal for int()")` devuelve el mensaje genérico con «Lo que escribiste sigue aquí» y no el texto de Python; con un `ValueError` escrito por Anka devuelve su texto.
- E4 · Un caso en `ready` sin código muestra «Información validada» en la insignia y no «Proyecto creado».
- E5 · El glosario trae los cuatro términos y la pregunta «¿Qué significan T y T&E?».

**Pendiente (necesita decisión del dueño, no se hizo):** renombrar «Guardar borrador» (D-95), «Administrar casos» (D-93; la página y el enlace de Ajustes conservan su nombre), los correos que van al cliente y la skill de Work (R11), el aviso de un solo uso por cookie en lugar de `?message=` (Ola 1.12).

### T-41 · Work lee los adjuntos directamente desde Gmail, sin expediente en Drive
**Estado: implementado** (pruebas en `tests/test_lectura_de_adjuntos.py` y `tests/test_prompt_work.py`); **prueba manual en Work parcial**: la
primera con el prompt V6.1 (2026-10-11, D-69) leyó el hilo y dejó el borrador completo; faltan el KMZ, el ZIP y el adjunto grande (ver «Prueba
manual»). Rige para los borradores del prompt V6 en adelante (`skill_version` `PRECISO_V6…`); los de V5 y anteriores siguen con T-45, T-37 y
T-40 hasta que se retiren. Decisión: D-68.

**Motivo.** El 2026-10-11 un caso real quedó oculto («Lectura pendiente») porque Work no abrió 1 de los 4 segmentos que el preparador
había publicado en Drive («ID esperado no existe»), aunque el archivo existía, era del dueño y se podía descargar. Una prueba directa
con el mismo hilo, sin Drive, abrió los 4 adjuntos: nombres y tamaños idénticos a los de Gmail, 10 de 12 extractos literales iguales en
el texto que extrae Python y los otros 2 (coordenadas del KML) iguales en valor, solo escritos de otra forma.

**Qué debe cumplirse**
1. **Lectura.** Work abre los originales en Gmail: los del mensaje que analiza y los de los demás mensajes recibidos del hilo. Si el
   lector de adjuntos del conector no admite el formato (p. ej. KML: `read_attachment_supported: false`) o devuelve el contenido
   truncado, abre el mensaje en formato `raw` y extrae el original de su MIME. Nunca sustituye un archivo por otro ni da por leído lo
   truncado. No usa Drive para leer.
2. **Informe por archivo.** Cada elemento de `attachments[]` lleva, además de lo que ya lleva, `size_bytes` (entero: el tamaño que muestra
   Gmail) y, opcional, `read_via` (`attachment` o `raw_mime`). Sin `size_bytes` el archivo no se puede verificar (ver 3).
3. **Completitud verificada por el puente (sustituye a los segmentos de T-37).** Al importar un borrador de Work nuevo o cambiado, el
   puente obtiene de Gmail la lista de adjuntos de los mensajes recibidos del hilo (nombre y tamaño; no descarga su contenido) y no cuenta
   las imágenes inline de firma. Un adjunto queda **verificado** si Work lo reporta con el mismo nombre y el mismo tamaño y una cobertura
   distinta de `null` (un adjunto sin `coverage` cuenta como no leído); dos adjuntos con el mismo nombre y tamaño son el mismo
   archivo reenviado y cuentan como uno. «El mismo nombre» se compara en forma Unicode NFC y sin mayúsculas (una tilde compuesta
   o descompuesta es la misma letra); «el mismo tamaño» acepta `10`, `10.0` o `"10"`. Un adjunto de Gmail sin nombre se nombra
   «(adjunto sin nombre)» y queda pendiente. Una imagen con `Content-Disposition: attachment` es un adjunto del cliente aunque
   traiga `Content-ID` (Outlook lo pone a todas); solo es imagen de firma si es `inline` o, sin disposición, trae `Content-ID`.
   Una imagen incrustada que Work reporta no cuenta como «adjunto que Gmail no tiene». Lo que Work marca con `status: ignorado` no cuenta como adjunto desconocido. Si falta alguno, difiere un tamaño o Work reporta uno que Gmail no
   tiene, la lectura queda incompleta: `analysis_complete` pasa a falso, el caso a revisión técnica interna, el aviso del caso nombra
   los archivos y los resultados siguen ocultos hasta que un análisis posterior de Work los verifique (el mismo mecanismo de
   reemplazo de T-37). La lista se guarda con el caso: se consulta una vez por borrador importado, no en cada pasada.
4. **Si Gmail no responde** al verificar (cuota, red), el borrador no se descarta ni se da por verificado: la lectura queda «sin
   verificar», se dice así en el caso y se reintenta en la siguiente pasada, sin insistir dentro de la misma (como T-40).
5. **Texto local para T-38.** El puente descarga y extrae con los lectores de Python solo los adjuntos de los hilos con un borrador
   importado, una vez por adjunto (se reconoce por su huella), y guarda el texto solo en este equipo. Ya no hay un proceso que recorra el
   buzón cada minuto bajando hilos completos. T-38 sigue siendo solo avisos, con un cambio: **los números se comparan por valor**, no
   por texto (`-74.304` = `-74.30400000`; `126,51` = `126.51`), y un número con separador ambiguo (`1.234`, `1,234`) se acepta si
   coincide con cualquiera de sus lecturas. Un extracto que son solo cifras (`-74.3025,4.779,2584.3`) se comprueba por valor, leyendo la
   coma como decimal o como separador de lista, y el texto del archivo se lee igual (los pares `lon,lat; lon,lat` que escribe el lector
   de KML se toman número a número). Sin ese cambio las coordenadas de un KML darían diferencias falsas. El lector de KML incluye además
   el nombre y la descripción del `Document` y de cada `Folder` (ahí suelen ir el proyecto y avisos como «datos ficticios»), que antes se
   perdían porque solo se leían los `Placemark`.
6. **Sin `DRIVE_PREFLIGHT` no se oculta el caso.** Un caso de Work que no trae `DRIVE_PREFLIGHT` ni verificación (un borrador anterior sin
   expediente, o un caso viejo) ya no desaparece de la pantalla por eso; un borrador V6 se verifica por el punto 3. Solo se oculta un caso
   con verificación `incompleta` (o, mientras existan, uno de V5 cuyo `DRIVE_PREFLIGHT` o cuyos segmentos no cuadran, como en T-37); uno
   «sin verificar» se muestra con el aviso. En un borrador V6, `DRIVE_PREFLIGHT`, `reading_audit` y los campos `*_segment_*` se aceptan si
   llegan, pero no se exigen ni deciden nada: `reading_audit` lo escribe el puente.
   El origen del mensaje se sigue verificando contra Gmail (T-46) también para los borradores V6.
7. **Qué se retira.** La publicación de expedientes en Drive (T-45, T-40), el manifiesto con segmentos, las instantáneas de manifiesto,
   `work_drive_enabled` (queda en falso por defecto) y el paquete manual de adjuntos. El código se retira cuando T-41 esté
   implementado **y probado en Work**, no antes. Los modos `local` y `api` no cambian: leen con Python.

**Casos de aceptación** (los que se vuelven pruebas)
- Cuatro adjuntos reportados con su nombre y tamaño: verificados, y `analysis_complete` se respeta.
- Falta el KML en el informe de Work: aviso «Falta leer: poligono.kml», `analysis_complete` falso, caso en revisión técnica interna.
- Un tamaño distinto al de Gmail, y un adjunto reportado que Gmail no tiene: ambos quedan pendientes y nombrados.
- La imagen inline de firma no cuenta como adjunto pendiente.
- Gmail falla al verificar: «sin verificar», el borrador se conserva y se reintenta.
- Las coordenadas del KML con distinto formato no generan diferencia en T-38.
- Un borrador sin `DRIVE_PREFLIGHT` se importa y no se oculta por eso; uno antiguo con `DRIVE_PREFLIGHT` se acepta sin exigirlo.

**Cambios que acompañan (al implementar).** `spec/contrato_work.schema.json` (`attachments[].size_bytes` y `read_via`, opcionales);
el prompt de Work pasa de la V5 a la V6 (la sección 2 se reescribe sin Drive, con la regla del formato `raw`, y el ejemplo JSON lleva
`size_bytes` sin `DRIVE_PREFLIGHT`); `SKILL.md`; `tests/test_prompt_work.py` valida el ejemplo contra el esquema.
**Prueba manual en Work** antes de activarlo: repetir la del 2026-10-11 con el hilo de ese caso y añadir un KMZ, un ZIP y un adjunto
grande, para conocer el límite del conector con el correo `raw`.

**Preguntas abiertas** (no se deciden aquí)
- Qué significa `coverage` cuando falta la revisión visual: Work marcó «parcial» el Excel y el KML por no revisarlos visualmente, y
  Python los da por «completa»; a la inversa, Python marcó «parcial» un PDF de una página que Work leyó y renderizó completo.
- DWG/DXF: ni Python ni el conector los leen. Se seguirían registrando como no legibles, con la sugerencia de pedir al cliente un DXF y
  un PDF de las presentaciones; leer DXF con una librería es una dependencia nueva que requiere su propia decisión.
- Si Work puede hacer cálculos de comprobación (áreas, perímetros) marcados como derivados; la V5 dice «no calcules ni conviertas».
- El límite de tamaño del correo `raw` en el conector.

### T-42 · Eliminar un caso no deja restos, y se puede reiniciar todo para probar desde cero
**Estado: implementado** (pruebas en `tests/test_reinicio.py`). Decisión: D-74.

**Motivo.** «Eliminar seleccionadas» borraba la solicitud, los datos comerciales y las novedades, pero dejaba mensajes, versiones de la
tabla, vínculos entre hilos, cambios pendientes, seguimiento y fallos de análisis. Si el mismo hilo volvía a entrar, esos restos
reaparecían mezclados con el caso nuevo. Y no había forma de volver a probar desde cero sin tocar la base a mano.

**Qué debe cumplirse**
1. **Eliminar un caso borra todo lo suyo** salvo la evidencia: la solicitud, los datos comerciales, las novedades, los mensajes, las
   versiones de la tabla, el seguimiento, los fallos de análisis y los vínculos y descartes de proyecto donde el hilo sea cualquiera de
   los dos lados. Se conservan `case_events` (el historial, con la anotación de que se eliminó), `case_changes` (las solicitudes de
   cambio, que alimentan el acta) y `mail_operations` (el registro de lo que se envió). Una prueba recorre las tablas de la base y falla si aparece una tabla nueva con
   `thread_id` que no esté en una de las dos listas.
2. **Reiniciar para probar desde cero** (`python -m anka.infra.reinicio`):
   - Sin opciones solo **muestra** qué se borraría (filas por tabla, archivos, casos con carpeta en Drive) y no cambia nada.
   - Con `--confirmar` primero hace una **copia de seguridad** (la de `backup`, restaurable con `--restore`); si la copia falla, no
     borra nada. Después vacía todas las tablas de la base, borra el estado del puente (`work_dashboard_sync_state.json`) y las
     carpetas derivadas (`review_queue`, `manifiestos`, `cache_hallazgos`).
   - Se niega si el puente está corriendo (hay que cerrar el lanzador).
   - **No toca** Gmail, Drive, `config.json`, el token ni las credenciales, ni `evidence/` ni `backups/`. Avisa de que los correos de
     prueba, los borradores de Work y las carpetas de Drive siguen ahí y Work los verá como historial relacionado.

### T-43 · El puente acepta el encabezado del borrador aunque Work le cambie tildes, mayúsculas o espacios
**Estado: implementado** (pruebas en `tests/test_encabezado_de_work.py`). Decisión: D-76.

**Motivo.** El 2026-10-13 un borrador completo, con su bloque JSON válido, se rechazó («El bloque JSON no está en una sección interna
inicial válida») porque Work escribió `[REVISAR ANTES DE ENVIAR - BORRAR ESTA SECCIÓN]` con tilde. El puente comparaba el encabezado
letra por letra y el caso quedó como un caso mínimo. Es el mismo descuido que ya se tolera con las etiquetas de estado.

**Qué debe cumplirse**
1. **Se tolera la forma, no el fondo.** El borrador sigue debiendo empezar con la línea del encabezado, pero se comparan sin tildes, sin
   distinguir mayúsculas, con los espacios colapsados y con cualquier guion tipográfico como «-». Aplica tanto a decidir si el borrador
   es de Anka como a exigir que el bloque JSON esté en la sección inicial.
2. **Sigue sin aceptarse lo inseguro.** Un bloque JSON dentro de un texto que no empieza con el encabezado (por ejemplo, uno citado en una
   respuesta) se rechaza igual que antes; un borrador con el encabezado y sin bloque se sigue reportando; los bloques repetidos o
   incompletos también.
3. **El prompt lo pide sin tilde.** El prompt de Work dice que la línea se copia tal cual, sin tilde en «SECCION». Con 1 ya no es
   imprescindible, pero evita que se repita.

### T-44 · Un borrador de V6 que omite `skill_version` se verifica igual contra Gmail
**Estado: implementado** (pruebas en `tests/test_version_de_work.py`). Decisión: D-77. Acota T-41.

**Motivo.** El 2026-10-13 un borrador de V6.1, con los adjuntos en formato V6 (`size_bytes`, `read_via`), omitió `skill_version` porque el prompt
dice «omite lo desconocido». El puente lo tomó por un borrador anterior, lo verificó contra el manifiesto de Drive (que V6 ya no usa) y el caso quedó
en «Lectura pendiente: falta leer el PDF», aunque Work había leído el original desde Gmail.

**Qué debe cumplirse**
1. **La versión declarada manda.** `PRECISO_V6` o posterior se verifica contra Gmail (T-41); `PRECISO_V5` o anterior sigue con el manifiesto (T-45,
   T-37). Una versión declarada de V5 no se reinterpreta aunque sus adjuntos traigan `size_bytes`.
2. **Sin versión declarada** (ausente, vacía o «no indicado»), la huella de V6 decide: si el borrador trae adjuntos y **todos** llevan su tamaño exacto
   en bytes (`size_bytes` entero, algo que V5 y anteriores no emiten), se verifica contra Gmail. Sin adjuntos o con alguno sin `size_bytes`, sigue el camino
   de siempre.
3. **El prompt lo exige.** El prompt de Work dice que `skill_version` es obligatorio y se copia tal cual (`PRECISO_V6.1`).


### U-59 · Un envío incierto no se duplica ni se queda atascado (Ola B3 del plan de backend)
**Estado: implementado** (pruebas en `tests/test_envios_inciertos.py`; ajusta `tests/test_fiabilidad.py`). Decisión: D-124. Toca R8 **solo para añadir protección**: nada se envía ni se reenvía solo; la reserva durable (`BEGIN IMMEDIATE`) y el `Message-ID` estable no cambian.

**Motivo.** La reserva de un envío se identificaba por el hash del texto: dos pestañas con textos distintos (o una coma editada) reservaban dos envíos del mismo caso y salían dos correos. Y un envío que quedaba incierto (el proceso murió entre «Gmail lo aceptó» y «el caso se actualizó») solo se aclaraba si la persona volvía a pulsar «Enviar» con el mismo texto. El lanzador, además, reiniciaba por un cambio de código sin esperar a que una acción en curso terminara.

**Qué debe cumplirse**
1. **Un solo envío en curso por caso.** `envios.reservar` rechaza —dentro de la misma transacción que reserva— cualquier envío si el mismo hilo tiene otra reserva `pending` de menos de 2 minutos (`MINUTOS_EN_CURSO`), aunque el texto o el destinatario difieran. Mensaje: «Ya hay un envío en curso para este caso. Espera un momento y recarga la página, o revisa Gmail (carpeta Enviados) para ver si el correo salió. No se envió otro.» Con el mismo texto sigue valiendo el mensaje de siempre.
2. **Se guarda lo revisado.** La reserva guarda el texto que la persona revisó (`mail_operations.body`, migración 19; las filas anteriores quedan con el texto vacío) para poder completar el caso después sin pedirlo otra vez.
3. **Se aclara solo, nunca se reenvía.** Una reserva `pending` de más de 2 minutos se aclara consultando Gmail por su `Message-ID`: si está en Enviados se da por enviada y el caso avanza como un envío normal (estado, mensaje, evento y novedad); si no está, se libera la reserva con un evento en el historial y un aviso, **sin enviar nada**. Lo hace (a) el lanzador cada 5 minutos, en un hilo aparte, solo si hay reservas viejas, y (b) el propio botón «Enviar»: antes de enviar aclara las viejas del caso y, si hubo alguna, **no envía esa vez** (la persona ve qué pasó y decide). Si Gmail no responde, no se libera nada.
4. **«Ya salió: marcar como enviado».** En el caso con un envío sin confirmar, la persona puede declarar que el correo sí salió (`POST /mark-sent`). Cierra la reserva, completa el caso con el texto guardado y deja en el historial, con huella, que fue una declaración de la persona. No consulta ni envía nada.
5. **Cierre ordenado.** Antes de reiniciar por un cambio de código, el lanzador espera hasta 20 s a que no haya acciones del dashboard en curso (`en_curso/` en la carpeta de datos, una marca por acción POST; las de más de 5 minutos se ignoran) ni una reserva de envío de menos de 2 minutos. Pasado el tope reinicia igual y lo anota. Las acciones se escriben para que, si aun así se cortan, la base quede consistente (transacciones) y el envío incierto se aclare como en 3.

**Casos de aceptación**
- Q1 · Con una reserva `pending` reciente del hilo, reservar otro texto (u otro destinatario) lanza el aviso; con una de otro hilo, no; con una de más de 2 minutos, no bloquea la reserva (pero `send_dashboard_reply` la aclara antes).
- Q2 · Dos pestañas con el mismo `state_token` y textos distintos: sale un solo correo.
- Q3 · Reserva vieja con el correo en Enviados: queda `sent`, el caso avanza y hay un evento `send_reconciled`; sin el correo: la reserva desaparece, hay un evento `send_released` y no se llamó a `messages.send` ni a `drafts.send`; con Gmail caído: nada cambia.
- Q4 · «Ya salió» cierra la reserva y avanza el caso con el texto guardado; sin envíos pendientes se rechaza; la acción está en `POST_ROUTES` y en `openapi.py`.
- Q5 · `cierre_ordenado` espera mientras haya una marca reciente o una reserva reciente, y se rinde al pasar el tope; una marca vieja no cuenta.

### U-60 · La copia de seguridad no frena al lanzador y la cadena de huellas no se puede acortar (Ola B3 del plan de backend)
**Estado: implementado** (pruebas en `tests/test_respaldo_robusto.py`). Decisión: D-125. No cambia qué contiene una copia ni cómo se restaura (B0).

**Motivo.** La copia diaria corría dentro del bucle del lanzador, sin tope de tiempo: un Drive colgado congelaba la vigilancia del dashboard y los recordatorios. La copia no se comprobaba una vez hecha el zip. Borrar los últimos hechos del historial de un caso pasaba por «íntegro». Y la copia previa a migrar (`.bak-vN`) no se renovaba si ya existía de un intento fallido.

**Qué debe cumplirse**
1. **Fuera del bucle.** El lanzador lanza la copia como un proceso aparte (`python -m anka.infra.backup`) y solo lo vigila: tope de 15 minutos (`COPIA_TOPE`), pasado el cual lo cancela, lo anota en `last_backup.json` y espera una hora antes de reintentar; una copia que falla tampoco se repite en cada vuelta sino pasada una hora.
2. **Verificada.** Tras escribir el zip, `create_backup` lo abre, comprueba cada entrada y revisa la base (`integrity_check` y versión de esquema); si no pasa, borra la copia y falla. La copia de la base ya se comprobaba antes de comprimirla.
3. **Restauración con todo parado.** Además de exigir el puente parado, `restore` se niega si hay acciones del dashboard en curso o si otro proceso tiene una escritura abierta en la base; el comando `--restore` se niega también si algo contesta en el puerto 8765. La instalación atómica (`os.replace`, borrado de `-wal/-shm`) no cambia.
4. **Ancla de la cadena.** Cada caso guarda el último hecho anotado (`event_anchors`: id, huella y cuántos hechos hay; migración 20, que parte de lo que ya existe). `verify_chain` devuelve «no íntegra» si la huella del último hecho o la cantidad ya no coinciden con la ancla, así que borrar la cola se detecta. Es una ancla dentro de la misma base: detecta pérdidas y borrados parciales, no a quien reescriba la base entera (eso lo cubre la copia en Drive). Eliminar un caso conserva sus hechos y su ancla; reiniciar los datos vacía todo.
5. **Copia previa a migrar.** Si `.bak-vN` ya existe y tiene más de 5 minutos, se hace una nueva y la vieja pasa a `.bak-vN.anterior`; una de menos de 5 minutos (de otro proceso que migra a la vez) se respeta.

**Casos de aceptación**
- R1 · `CopiaDeSeguridad.check` lanza un proceso, no espera, lo cancela al pasar el tope y no reintenta antes de una hora; `servicio()` ya no llama a `run_backup`.
- R2 · Un zip dañado o con una base corrupta hace fallar `create_backup` y no deja archivo.
- R3 · `restore` se niega con una escritura abierta o una acción en curso, sin tocar la base.
- R4 · Borrar el último hecho de un caso (o todos) hace que `verify_chain` dé falso; añadir hechos sigue dando verdadero; una base del esquema 19 llega al 20 con las anclas puestas.
- R5 · Con `.bak-vN` vieja se crea una nueva y queda `.anterior`; con una reciente no se toca.

### U-61 · Seguridad menor del dashboard y de la lectura de archivos (Ola B3 del plan de backend)
**Estado: implementado** (pruebas en `tests/test_seguridad_menor.py`). Decisión: D-126. No cambia R8, D-27 ni la paridad de los tres modos.

**Motivo.** Ocho hallazgos de severidad baja de la auditoría de seguridad (B2-04 a B2-15): avisos de la dirección sin límite, política de `Origin` laxa, `Host` sin puerto, `gmail_url` de Work sin formato, tope de píxeles implícito, ZIP leídos sin tope, `llm.base_url` sin advertencia, dependencias sin auditar y sin corpus de instrucciones inyectadas.

**Qué debe cumplirse**
1. **Avisos de la dirección.** `?message=` se recorta a 300 caracteres salvo que lo haya firmado este servidor: toda redirección propia añade `firma` (HMAC del texto con el token del dashboard), de modo que los avisos largos legítimos se ven completos y un enlace manipulado no puede mostrar un texto largo como si fuera del dashboard.
2. **Origen de un POST.** Si llega `Sec-Fetch-Site`, debe ser `same-origin` o `none` (`cross-site` y `same-site` se rechazan con 403); si llega `Origin`, debe coincidir con el `Host` (`null` se rechaza); si no llega ninguno de los dos (un script, `curl`, las pruebas) se acepta, porque el token CSRF —que solo está en las páginas del propio dashboard— es la protección. Así no se rompe ningún cliente legítimo.
3. **Puerto del `Host`.** Si el `Host` trae puerto, debe ser el del servidor; sin puerto se acepta.
4. **`gmail_url`.** El esquema declara `pattern` `^(https://mail\.google\.com/.*)?$`; el validador del contrato ahora comprueba `pattern`; un enlace de otro tipo no rechaza el análisis: se deja vacío con el aviso de siempre («Work escribió algo distinto de lo esperado»).
5. **Imágenes y ZIP.** `Image.MAX_IMAGE_PIXELS` se fija en `MAX_OCR_PIXELS` (el mismo tope del lector). Todo ZIP que se lee de un adjunto pasa por `zip_acotado` (`_zip_text` ya no lo abre a mano); las excepciones, con su propio tope equivalente, están nombradas en una prueba: el KMZ del dominio (que no puede importar `infra`), las copias propias de `backup` y la lectura de un `.docx` local del índice comercial (tope de 50 MB al XML).
6. **`llm.base_url` fuera del equipo.** `settings.config_warnings` avisa (no rechaza) si el host no es `127.0.0.1`, `localhost` ni `::1`; el comando lo imprime al arrancar.
7. **Dependencias.** El workflow de CI corre `pip-audit` sobre `requirements.lock` y `requirements-dev.lock` (solo ese paso; no se instala nada en el equipo del desarrollador; `docs/ESTANDAR_CAMBIOS.md` explica cómo correrlo a mano).
8. **Instrucciones dentro de un correo.** Un corpus de pruebas (`tests/test_inyeccion_de_instrucciones.py`) comprueba que el texto del cliente y el del OCR viajan en el prompt como datos delimitados, que ni la salida del modelo ni un borrador de Work con instrucciones inyectadas cambian un destinatario, un estado o envían algo (R8: el envío solo sale de la acción de una persona), y que el contrato rechaza lo que no cumple el esquema.

**Casos de aceptación**
- S1 · `?message=` de 1 000 caracteres sin firma se muestra de 300; con la firma de una redirección propia se muestra completo; una firma de otro texto no sirve.
- S2 · POST con `Sec-Fetch-Site: cross-site` o `Origin` ajeno → 403; con `same-origin`, con `none` o sin ninguno → pasa; sigue valiendo el token.
- S3 · `Host: 127.0.0.1:9999` contra un servidor en 8765 → 403; `127.0.0.1:8765` y `127.0.0.1` pasan; `[::1]:8765` pasa.
- S4 · `gmail_url: "javascript:alert(1)"` queda vacío con aviso; `https://mail.google.com/mail/u/0/#all/abc` se conserva.
- S5 · Una prueba recorre `anka/` y falla si aparece `zipfile.ZipFile(` fuera de las excepciones nombradas.
- S6 · `config_warnings` avisa con `http://10.0.0.5:11434` y calla con `http://127.0.0.1:11434`.
- S7 · El workflow de CI contiene el paso `pip-audit`.

### U-62 · Un solo reloj y las fechas se ordenan por instante (Ola B3 del plan de backend)
**Estado: implementado** (pruebas en `tests/test_reloj.py`). Decisión: D-127. No cambia ninguna fecha ya guardada.

**Motivo.** Varios módulos tomaban la hora con `datetime.now()` sin zona (el año de la T, el nombre de una copia, el sello de una exportación) y otros con `astimezone()`. Y las listas se ordenaban comparando el **texto** de la fecha: dos fechas con desfases distintos (un correo recibido en UTC y un envío local) se desordenaban.

**Qué debe cumplirse**
1. **Un reloj.** `anka/dominio/reloj.py`: `ahora()` (la hora local **con** zona, la que Anka ya guardaba), `ahora_iso()`, `instante()` y `clave_de_orden()`. `backup.py`, `change_control.py`, `pantalla_caso.py`, `server.py`, `repositorio.py`, `store.py`, `envios.py` y `case_actions.py` lo usan; no queda `datetime.now()` sin zona en esos módulos. La zona sigue siendo la del equipo (en la oficina, Bogotá, -05:00) para coincidir con lo guardado; el horario hábil de los recordatorios conserva su propia zona fija.
2. **Orden por instante.** La lista de casos, la última respuesta del cliente, el seguimiento del caso, la conversación, el primer mensaje y los cambios se ordenan por instante, no por texto; a igual instante manda el orden de guardado. Una fecha sin zona se entiende en hora local y una ilegible va primero.
3. **Sin tocar lo guardado.** Los textos guardados no se reescriben ni se migran.

**Casos de aceptación**
- T1 · `ahora()` trae zona; el año de la T sale de `ahora()`.
- T2 · Dos fechas con desfases distintos se ordenan por instante en la lista de casos, en `case_events` y en la última respuesta; el orden por texto habría dado el contrario.
- T3 · Una fecha sin zona, una vacía y una ilegible no rompen el orden.

### U-63 · Consistencia entre las tablas del caso y lo que el lector descarta (Ola B3 del plan de backend)
**Estado: implementado** (pruebas en `tests/test_consistencia_de_tablas.py`; la comprobación original sigue en `tests/test_reinicio.py`). Decisión: D-128. No cambia ningún texto que el lector entrega.

**Qué debe cumplirse**
1. **Toda tabla con `thread_id` está clasificada.** Una prueba compara las tablas de la base que tienen `thread_id` con `TABLAS_DEL_CASO`, `TABLAS_CON_DOS_HILOS` y `TABLAS_QUE_SE_CONSERVAN`, en los dos sentidos: una tabla nueva sin clasificar falla y una lista que nombra una tabla inexistente también; ninguna tabla está en dos listas. `event_anchors` se conserva con el historial.
2. **El lector geográfico deja constancia.** Las coordenadas y datos que descarta sin avisar en el texto (coordenadas KML ilegibles, coordenadas de misión fuera de rango o ilegibles, altitudes CSV y elevaciones GPX ilegibles) se anotan en el registro a nivel depuración con su cantidad; el texto que se entrega no cambia.

**Casos de aceptación**
- U1 · Un KML con coordenadas rotas deja el mismo texto y una línea de registro con el número de descartes.

### U-64 · Una sola forma de escribir en la base: `transaccion()` (Ola B3 del plan de backend)
**Estado: parcial** (migración incremental; pruebas en `tests/test_transaccion.py`). Decisión: D-129. No cambia ningún comportamiento ni texto.

**Motivo.** Cada servicio abría la base con `connect()`, hacía `commit()` donde le parecía y cerraba en un `finally`: unas cincuenta veces el mismo patrón de seis líneas, con candados de escritura tomados (o no) a mano y sin una unidad de trabajo explícita.

**Qué debe cumplirse**
1. **Dos contextos en `infra/store.py`.** `transaccion(inmediata=True)` abre la base (o comparte la de la petición web, `request_scope`), toma el candado de escritura al entrar (o con la primera escritura si `inmediata=False`), **confirma al salir bien y revierte si algo falla** (también con una interrupción), y cierra. `lectura()` abre para leer y descarta lo que quedara sin confirmar.
2. **Migrados** (sin cambiar su comportamiento; las pruebas que ya existían pasan sin tocarse, salvo la que llamaba a `_record_sent_reply` con 9 parámetros): `envios`, `case_actions` (salvo `approve_review`), `change_control` (salvo la conexión opcional de `origen_de_destinatario`), `ediciones`, `work_import`, `vinculos`, `revision_tecnica`, `quotes`, `estado_de_revision`, `salud`, `cola_del_piloto` y `infra/rechazos_de_work`.
3. **Pendientes** (listados en `PENDIENTES` de la prueba; la lista solo puede bajar): `case_actions.approve_review` (pasa su conexión a `_send_questions` y `_create_case_folder`), `gmail_followup.sync_thread_activity` (lee Gmail dentro de la transacción y confirma por tandas, hay que separarlo antes), `pipeline` (el ciclo de revisión comparte una conexión durante toda la pasada) y la conexión opcional de `change_control.origen_de_destinatario`. Tampoco se tocaron `repositorio.py` (ya es el repositorio del agregado «caso» y administra sus conexiones), `reintentos`, `followup_reminders` y `reinicio`, que reciben la conexión de quien los llama.
4. **Repositorios por agregado.** Queda como propuesta: `RepositorioDeCasos` ya cubre el caso; `envios` (reserva de correos) es el siguiente candidato natural.

**Casos de aceptación**
- V1 · Dos escrituras dentro de `transaccion()` quedan juntas; si la segunda falla, no queda ninguna (también con `KeyboardInterrupt`).
- V2 · Con `inmediata=True` otro proceso no puede tomar el candado mientras dura el bloque; al salir queda libre.
- V3 · Dentro de `request_scope` comparte la conexión, confirma y revierte igual.
- V4 · `lectura()` descarta lo que se escriba por error; una prueba cuenta los `connect()` que quedan fuera de `store.py` y falla si suben.

### U-65 · Integración continua, desarrollo y tipos en las fronteras (Ola B3 del plan de backend)
**Estado: parcial** (pruebas en `tests/test_ci_y_desarrollo.py`). Decisión: D-130. No cambia ningún comportamiento visible.

**Qué debe cumplirse**
1. **CI.** Cada trabajo de `.github/workflows/ci.yml` lleva `timeout-minutes` y el flujo `concurrency` con `cancel-in-progress` (una corrida nueva de la misma rama cancela la vieja).
2. **`check.bat quick`.** Corre ruff, mypy, `check_spec` y las pruebas rápidas (`tools/pruebas_rapidas.py --rapido`, sin los módulos lentos de `LENTOS` y sin cobertura); `check.bat` sin argumentos sigue siendo lo mismo que el CI y lo único que cierra un cambio. Equivalente sin `.bat` documentado en `docs/ESTANDAR_CAMBIOS.md`.
3. **Sin deriva de `spec/07-api.md`.** Una prueba exige que el documento nombre cada ruta `GET` que documenta `openapi.py` y las etiquetas de las acciones; las cabeceras y la política de `Origin` del documento son las vigentes.
4. **Animación.** Los nodos de fondo se pausan también cuando la ventana pierde el foco (`blur`) y se reanudan con `focus`; la física y sus parámetros (60 cuadros por segundo como tope, `tests/fisica_nodos_prueba.js`) no cambian (U-38).
5. **Parámetros largos.** Lo que se anota tras enviar un correo se agrupa en `RespuestaEnviada` (frozen dataclass, antes 9 parámetros). Queda para después `record_event` (9) y `register_change` (11), porque tienen decenas de llamadores y pruebas.
6. **Glosario.** `spec/06-arquitectura.md` fija el vocabulario (caso/solicitud/hilo/«quote»/revisión…); no se renombra nada.
7. **Protocolos de Gmail y Drive.** `anka/infra/clientes_google.py` define `ClienteGmail` y `ClienteDrive` (`Protocol`); `services()` y los parámetros `gmail`/`drive` de los módulos de `anka/` los usan en vez de `Any` (ninguno queda como `Any`). Los resultados de cada llamada siguen siendo `Any`: son JSON de Google.

**Casos de aceptación**
- W1 · El workflow tiene `timeout-minutes` en todos sus trabajos y `cancel-in-progress: true`.
- W2 · `check.bat` reconoce `quick`, la herramienta existe y todo módulo de `LENTOS` es un módulo de pruebas real.
- W3 · `spec/07-api.md` nombra cada ruta `GET` documentada; el script de la animación se detiene en `blur` y arranca en `focus`.
- W4 · No queda `gmail: Any` ni `drive: Any` en `anka/`; los dobles de prueba cumplen los protocolos.

### U-77 · La línea de comandos nunca envía correos al cliente (R8; auditoría de seguridad)
**Estado: implementado** (pruebas en `tests/test_sin_envio_directo.py`). Decisión: D-144. Supersede la vía de «envío directo» de `run --apply` sin `--review-only` que describían el README y `docs/DISENO_HIBRIDO.md`.

**Motivo.** R8 y los principios no negociables dicen que nada sale al cliente sin que una persona lo revise (única excepción: los recordatorios opcionales de R13). `python app.py run --apply` sin `--review-only` ni `--gmail-drafts` enviaba las preguntas al cliente desde `pipeline` sin revisión, en los modos `local` y `api`.

**Qué debe cumplirse**
1. **Ningún camino de `anka/cli/`, `pipeline` ni `decision` envía.** `Action.SEND` ya no existe; ni `RevisorDeHilos` ni `run`/`watch` llaman a `send_reply` ni a `messages().send` / `drafts().send`. El único envío al cliente sigue siendo el confirmado por una persona en el dashboard (`case_actions`), y el recordatorio de R13.
2. **Faltan datos → revisión humana.** Con datos faltantes, la decisión es siempre `REVIEW_PENDING` («Correo por revisar») con la ficha y el texto propuesto (`Action.QUEUE`), o el borrador de Gmail con `--gmail-drafts`; `review_only` ya no cambia ese resultado. No hay clave de `config.json` ni bandera que lo active: una configuración antigua no puede pedir el envío.
3. **`--apply` sin `--review-only` ni `--gmail-drafts` se ignora con aviso.** Imprime (stderr) y registra un aviso en español que dice que Anka ya no envía desde la línea de comandos y que el envío es solo desde el dashboard tras la revisión. Lo demás no cambia: la carpeta del proyecto (alcance completo) se sigue creando como antes y sigue exigiendo `drive_parent_id`; el contrato con Work y el modo `chatgpt_work` no se tocan.
4. **Paridad (U-47).** Un caso `ready`, `sending` o `response_sent` sigue sin retroceder; una fila `sending` heredada se sigue omitiendo con su motivo.

**Casos de aceptación**
- S1 · Con datos faltantes, `run(True)` (sin banderas) deja el caso en `review_pending` con la ficha, escribe el aviso, y el doble de Gmail registra **cero** llamadas de envío.
- S2 · `run(True, review_only=True)` y `run(True, gmail_drafts=True)` no envían y no escriben el aviso.
- S3 · `Action` no tiene miembro de envío; `decide_analysis` con cualquier combinación de banderas nunca devuelve un envío.
- S4 · Un recorrido (AST) de `anka/cli/`, `pipeline.py`, `decision.py` y `estado_de_revision.py` no encuentra `send_reply` ni llamadas `.send(`.

### U-78 · La copia de seguridad no sube secretos a Drive (auditoría de seguridad)
**Estado: implementado** (pruebas en `tests/test_respaldo_sin_secretos.py`). Decisión: D-145. Precisa U-42 y U-60; no cambia cómo se verifica la base.

**Motivo.** El respaldo diario incluía `config.json` tal cual y se subía a Drive. El archivo es de uso libre (las claves desconocidas solo se avisan) y alguien puede haber puesto allí una clave, un token o un endpoint con usuario y contraseña. Las claves que Anka reconoce hoy (`anka/dominio/configuracion.py`) no son secretos (modo, consultas, direcciones, ids de carpeta, prefijos, parámetros del motor) y la clave de OpenAI se lee de la variable de entorno `OPENAI_API_KEY`, nunca del archivo; aun así, un archivo editado a mano no es una garantía.

**Qué debe cumplirse**
1. **`config.json` no entra en la copia.** En su lugar entra `config.redactado.json`: la misma estructura, con el valor de toda clave sensible cambiado por `[REDACTADO]`. Se redacta, a cualquier profundidad, por **nombre** (api_key, apikey, secret, token (no `*_tokens`), password/passwd, pwd, credential, private_key, authorization, bearer, cookie, session, signature, `*_key`, `auth`) y por **aspecto del valor** (claves `sk-…`, tokens `ya29.`/`AIza`/`ghp_`/`xox*`, `Bearer …`, claves privadas PEM, tokens de refresco `1//…`), y se quita el usuario:clave de las URL (`https://u:c@host` pasa a `https://host`). Ninguna clave que Anka reconoce se redacta.
2. **Un archivo ilegible no se copia en bruto.** Si `config.json` no es JSON válido o no es un objeto, la copia sale sin configuración y queda un aviso en el registro.
3. **La verificación lo comprueba.** `_verificar_zip` rechaza (y borra) una copia recién hecha que contenga `config.json`.
4. **Restaurar sigue funcionando.** La configuración redactada **nunca pisa** la viva: solo se instala como `config.json` si no existe ninguna (y avisa que hay que volver a poner los valores `[REDACTADO]`). Una copia **antigua** que traiga `config.json` se restaura como antes (es un archivo de datos que no se ejecuta). `token.json`, `credentials.json` y la variable `OPENAI_API_KEY` nunca viajaron ni viajan: tras restaurar en un equipo nuevo hay que volver a autorizar Google (`python app.py auth`) y definir la variable.
5. **Copias ya subidas.** Las copias anteriores en Drive pueden contener `config.json` con secretos y este cambio no las toca (el código solo borra las propias, con prefijo, por antigüedad). Quien administra la cuenta debe revisarlas, borrar las que tengan secretos y rotar las credenciales que hayan estado en el archivo (`docs/MODO_OPERACION.md`).

**Casos de aceptación**
- C1 · Con un `config.json` sintético con claves falsas (nombres y valores sensibles, anidados y en listas) la copia no contiene ninguna, ni en el zip local ni en lo que recibe el doble de Drive; sí contiene `config.redactado.json` con lo no sensible intacto.
- C2 · Todas las claves de `Configuracion`/`ConfigLlm` sobreviven a la redacción.
- C3 · La copia se verifica y se restaura; una copia vieja con `config.json` se restaura sin error y sin ejecutar nada; la redactada no pisa la viva y sí crea `config.json` si no había.
- C4 · Un `config.json` roto no rompe la copia ni se copia en bruto.

### T-47 · Importación aislada de un borrador de Work (piloto 03)
**Estado: implementado** (pruebas en `tests/test_importacion_aislada.py`). Decisión: D-150. Acota T-41, T-44 y T-46; no cambia el contrato de Work.

**Motivo.** El piloto del retorno Work → Python necesita importar **un** borrador sin que `--once` toque nada más: `--once` revisa todos los borradores
de la cuenta, crea casos mínimos para los rechazados (T-36), sincroniza la actividad de los hilos, programa la hoja de Google y escribe el estado y el
latido del puente general.

**Qué debe cumplirse**
1. **Opciones.** `work_dashboard_sync.py --once --only-thread-id T --only-message-id M`. Las dos son obligatorias juntas y solo valen con `--once`;
   sin `--once`, con una sola o con un valor vacío el comando se niega antes de abrir Gmail. Sin ellas, el modo general no cambia.
2. **Filtro previo.** Solo se descargan e interpretan los borradores cuyo `threadId` en Gmail es `T`. Los demás ni se leen.
3. **Identidad contra el JSON.** El bloque debe declarar `thread_id = T` y `message_id = M`. Un borrador del hilo con otro `message_id` se informa como
   ignorado y no se verifica ni se importa.
4. **Origen obligatorio.** El mensaje `M` se comprueba en Gmail (mismo `id` y `threadId`, no es borrador ni enviado, el remitente coincide con
   `internal_sender` o `client_email`, tiene `internalDate`) **aunque el borrador no declare `skill_version`** ni preflight. No se escribe ni se
   finge ninguna versión. Si Gmail no responde, no se importa.
5. **Ambigüedad.** Si hay más de un borrador válido para `(T, M)` no se importa ninguno: se informa la lista de borradores y el comando termina con error.
6. **Validaciones de lectura que siguen aplicando.** Con `skill_version` V6 o posterior (o adjuntos con `size_bytes`) la lectura se verifica contra los
   adjuntos que Gmail tiene en el hilo (T-41). En cualquier otro caso se verifica contra la instantánea del manifiesto de Drive del hilo (T-37/T-38):
   sin instantánea (mensaje sin adjuntos, preparador sin publicar) no hay nada que reconciliar y no se inventa una advertencia; **con** instantánea
   que no incluya a `M`, el análisis queda incompleto y con aviso, igual que en el modo general.
7. **Escritura mínima.** Solo se escribe el análisis del caso `T` (tabla del caso, su auditoría y los datos comerciales de ese caso). No se crean casos
   mínimos, no se sincroniza la actividad de Gmail, no se programa la hoja de Google, no se escribe el estado (`work_dashboard_sync_state.json`) ni el
   latido del puente general y no se anotan rechazos en `work_rejections`. De `recepcion_mensajes` solo se concilia la fila del mensaje elegido, con la misma
   regla de T-48 (ningún otro mensaje del hilo ni de otros hilos).
8. **Salida.** Una línea JSON con el resultado (`importado`, `sin_borrador`, `rechazado`, `ambiguo`, `error_de_origen`, `fallo_de_importacion`), los ids de borrador
   implicados y los borradores ignorados o rechazados con su causa. Código de salida 0 solo si se importó; 1 en los demás casos. No imprime el contenido del correo.
9. **Repetir es seguro.** Importar dos veces el mismo borrador no duplica el hecho de auditoría (misma referencia).

**Casos de aceptación**
- C1 · Con tres borradores de hilos distintos, solo se descarga el del hilo elegido; los otros dos no se leen (`gets` = 1) ni se crean casos mínimos.
- C2 · Un borrador del hilo con otro `message_id` se ignora y se informa; sin válido, resultado `sin_borrador`.
- C3 · Sin `skill_version`, el origen se verifica igual; un remitente distinto o un mensaje que es un borrador rechaza.
- C4 · Dos borradores válidos para `(T, M)` → `ambiguo`, nada importado.
- C5 · Tras importar, no cambian el archivo de estado ni el latido del puente general, no se programa la hoja, no hay filas en `work_rejections` y de `recepcion_mensajes` solo cambia la fila del mensaje elegido (T-48).
- C6 · Con instantánea del manifiesto que no incluye `M`, el análisis queda incompleto con aviso; sin instantánea, sin aviso.
- C7 · Argumentos incompletos o sin `--once` terminan en error de uso sin llamar a Gmail.
- C8 · El modo general sigue igual (las pruebas del puente no cambian).

### T-48 · Una importación de Work confirma el mensaje que la originó en la cola de recepción
**Estado: implementado** (pruebas en `tests/test_conciliacion_work.py`). Decisión: D-152. Acota U-81 y T-47; no cambia `[ANKA_DASHBOARD_JSON_V1]` ni el receptor.

**Motivo.** El receptor Gmail (U-81) deja cada mensaje entrante en `pendiente_de_procesar` / `pendiente_de_interpretacion_work` y nada lo cierra: aunque Work ya
lo analizó y el puente lo importó, la fila sigue pendiente (piloto 03: `1a149ae2f36b4147`).

**Cuándo queda confirmada una importación.** `work_import._store_work_analysis` guarda el caso (`store.save` confirma `quotes` y la versión de la tabla), anota el hecho
`analysis` de la auditoría y confirma. La conciliación se escribe en esa **última transacción, después del hecho de auditoría**: se confirma junto con él y nunca antes de que
el caso esté guardado. Si algo falla antes, la fila no cambia; si el proceso se interrumpe tras guardar el caso y antes de confirmar, la fila sigue pendiente y reimportar (el
puente reintenta lo que falló) la concilia.

**Qué debe cumplirse**
1. **Identidad exacta.** Se concilia la fila `(cuenta, thread_id, message_id)` con el `thread_id` y el `message_id` que el borrador declara. Nunca otro mensaje del mismo hilo: un
   seguimiento del cliente (otro `message_id`) concilia el suyo, y los anteriores siguen como estaban. Un borrador sin `message_id` propio (el importador lo sustituye por el
   `thread_id`) no concilia nada.
2. **Cambio de estado.** Solo `pendiente_de_procesar` / `pendiente_de_interpretacion_work` pasa a `procesado` / `interpretado_por_work`. Se anota en la evidencia de la fila (`work`:
   referencia del análisis, `analysis_complete`, estado declarado por Work, hora) y se audita con un evento `interpretado_por_work`.
3. **Estados independientes.** La recepción solo dice «Work ya interpretó este mensaje y el dashboard lo guardó». No dice si el análisis está completo, si hay que revisarlo
   técnicamente ni en qué etapa comercial va el caso: eso sigue en `quotes` (`analysis_complete`, estado del caso). Un análisis incompleto se concilia igual.
4. **Idempotente.** Reimportar el mismo borrador, o uno nuevo del mismo mensaje, no cambia la fila ni duplica el evento (`ya_conciliado`).
5. **Sin registro.** Si la fila no existe no se crea, no se toca el cursor ni `recepcion_control`, y se deja **un** evento `conciliacion_sin_registro` (por mensaje) para poder investigarlo.
6. **Otros estados.** Si el mensaje aún no está leído por el receptor (`por_leer`, `requiere_reintento`) la conciliación se difiere: la fila no cambia y queda **un** evento
   `conciliacion_diferida`. Si está en `revision_manual` o excluido (propio o anterior al cursor) queda **un** evento `conciliacion_omitida` y la fila no cambia.
7. **Un fallo de la conciliación no pierde el análisis.** Se ejecuta en un punto de guardado: si falla (p. ej. tabla ausente) se revierte solo ella, se avisa en el registro (sin
   datos del correo) y el análisis se confirma. La fila queda pendiente y se puede conciliar después (punto 9).
8. **Solo análisis de Work.** Un caso mínimo creado por el puente porque el borrador era inválido (T-36, `_caso_minimo`) no es una interpretación de Work y no concilia.
9. **Conciliación de lo ya importado.** `recepcion.conciliar_pendientes()` aplica la misma regla a las filas pendientes cuyo mensaje ya tiene un análisis de Work guardado
   (`work_message_id` igual al `message_id`, no un caso mínimo): cubre lo importado antes de este cambio y lo diferido. Es idempotente y no se ejecuta sola.
10. **Sin efectos fuera de la cola.** No escribe Gmail, Drive ni Sheets, no toca el cursor, no cambia el código del receptor ni el contrato con Work.

**Casos de aceptación**
- C1 · Importación válida: la fila pasa a `procesado/interpretado_por_work` con evento y evidencia; el caso queda en `quotes`.
- C2 · Reimportar (mismo borrador y uno nuevo) no duplica el evento ni cambia la fila.
- C3 · Seguimiento: el análisis del mensaje 2 concilia solo el mensaje 2; el mensaje 1 del hilo y los de otros hilos no cambian.
- C4 · Análisis incompleto (`analysis_complete=false`, «Requiere revisión técnica interna»): la fila se concilia y el caso sigue en revisión.
- C5 · Sin fila: no se crea fila, el cursor no cambia y queda un solo evento `conciliacion_sin_registro`, aunque se reimporte.
- C6 · Mensaje `por_leer`, `requiere_reintento`, `revision_manual` o excluido: la fila no cambia y queda un evento.
- C7 · Fallo al anotar el hecho de auditoría: la fila no cambia y reimportar la concilia. Fallo de la conciliación: el análisis queda, la fila no cambia y no queda un evento a medias.
- C8 · Caso mínimo y borrador sin `message_id`: no concilian.
- C9 · `conciliar_pendientes()` concilia lo ya importado una sola vez y respeta C3 y C8.
- C10 · La importación aislada (T-47) concilia solo el mensaje elegido.

### T-49 · Cola de trabajos Python → Work: un trabajo por mensaje y una hoja `.xlsx` de Drive por trabajo
**Estado: implementado en local, sin publicar** (pruebas en `tests/test_trabajos_work.py`; ninguna prueba toca Drive). Decisión: D-153. Acota D-131 y U-66 a U-76 (que siguen previstos) y no cambia `[ANKA_DASHBOARD_JSON_V1]`, T-47, T-48 ni el receptor.

**Motivo.** Para que Work lea un expediente que Python eligió, el mensaje registrado en `recepcion_mensajes` necesita un trabajo con identidad estable y una hoja que Work pueda abrir. La primera prueba es manual y supervisada: nada lo activa solo.

**Qué debe cumplirse**
1. **Tabla `trabajos_work` (migración 24), SQLite como autoridad.** Campos: `job_id` (clave), `account`, `thread_id`, `message_id`, `job_type`, `contract_version`, `state`, `message_ref_json`, `ref_sha256`, `sheet_file_id`, `folder_id`, `claim_token`, `claim_until`, `attempts`, `last_error`, fechas. Clave única `(account, thread_id, message_id, job_type, contract_version)`.
2. **`job_id` determinista.** `ANKAJOB-` + los 16 primeros hexadecimales del SHA-256 de `account|thread_id|message_id|job_type|contract_version`. El mismo mensaje con el mismo tipo y la misma versión da siempre el mismo trabajo; cambiar el tipo o la versión del contrato da otro.
3. **Crear un trabajo.** Solo para una fila que exista en `recepcion_mensajes` y siga en `pendiente_de_procesar` / `pendiente_de_interpretacion_work`. Si no existe o ya está interpretada, se rechaza y no se inventa nada. Crear dos veces devuelve el mismo trabajo, sin duplicar fila ni evento. Hoy el único tipo es `analizar_solicitud` y la versión es `ANKA_DASHBOARD_JSON_V1`.
4. **Referencia verificable al mensaje.** `message_ref_json` guarda solo metadatos que ya tiene la cola: `gmail_thread_id`, `gmail_message_id`, remitente, asunto, `internal_date`, `header_date` y la lista de adjuntos (nombre, tipo, tamaño e id de adjunto). Nunca el cuerpo ni el texto de los adjuntos. `ref_sha256` es el SHA-256 de esa referencia en JSON canónico y viaja en la hoja.
5. **Estados.** `pendiente_de_publicar` → `publicando` → `publicado` → `conciliado`; además `ambiguo` (requiere una persona) y `cancelado`. Cada transición es un `UPDATE ... WHERE state=<esperado>`: solo una de dos transiciones simultáneas lo logra. Los eventos de auditoría van a `recepcion_eventos` (`trabajo_creado`, `trabajo_publicado`, `trabajo_ambiguo`, `trabajo_conciliado`), uno por transición.
6. **Hoja `.xlsx` por trabajo** (Drive existente, conversión a hoja de Google). Archivo `ANKA_JOB_<job_id>`. Hojas: `TRABAJO` (campo/valor: `job_id`, `thread_id`, `message_id`, tipo, estado, versión del contrato, fecha de creación, `ref_sha256`, referencia al mensaje, instrucción para Work), `ADJUNTOS_ESPERADOS` y `VERIFICACION` (qué debe comparar Work con Gmail antes de analizar). Work solo lee la hoja; nunca la escribe.
7. **Publicar sin duplicar bajo concurrencia.** Buscar por nombre no basta. Controles:
   a. **Reclamo en SQLite:** antes de tocar Drive se reclama el trabajo (`pendiente_de_publicar` → `publicando` con `claim_token` y `claim_until`). Quien no obtiene el reclamo no escribe en Drive. Un reclamo vencido (proceso caído) se puede retomar.
   b. **Marca verificable en Drive:** el archivo se crea con `appProperties.anka_job_id = job_id` y se busca por esa marca (el nombre es solo una pista), nunca solo por nombre.
   c. **Adoptar antes de crear:** si el trabajo ya tiene `sheet_file_id`, se verifica que exista, no esté en la papelera y lleve la marca de ese trabajo. Si falta el id pero hay un archivo con la marca (respuesta perdida tras crear), se adopta.
   d. **Verificar después de crear:** se vuelve a listar por la marca. Con exactamente un archivo, es el resultado. Con más de uno (carrera entre procesos) el trabajo pasa a `ambiguo`, se anota cuáles son y **no se borra ni se elige ninguno**.
   e. **Resultado ambiguo o error:** un error de Drive deja el trabajo en `pendiente_de_publicar` (con `last_error` sin datos del correo) si no hay efecto comprobado, o en `ambiguo` si hay más de un candidato. Un trabajo `ambiguo` no se vuelve a publicar solo.
   f. **Crear no se reintenta a ciegas** (un POST puede haber tenido efecto): se verifica primero por la marca. Actualizar el mismo archivo sí es idempotente.
8. **Retorno sin cambiar el contrato.** Work devuelve el borrador `[ANKA_DASHBOARD_JSON_V1]` de siempre; el trabajo se une al retorno por `(thread_id, message_id)`. La importación (general o T-47) concilia el mensaje (T-48) y, en la misma transacción y punto de guardado, pasa el trabajo (`pendiente_de_publicar`, `publicando` o `publicado`) a `conciliado` con un evento único. Reimportar no cambia nada. `ambiguo` y `cancelado` no cambian.
9. **Solo local por ahora.** Existe `python -m anka.cli.work_jobs` con `crear`, `exportar` (escribe el `.xlsx` en un archivo local, sin Drive), `ver` y `publicar`. `publicar` exige `--carpeta-id` y `--confirmar-drive` y no se ejecuta sin autorización expresa. Nada corre de forma periódica, no hay hilo ni tarea programada.
10. **Sin efectos fuera de lo dicho.** No crea ni envía borradores, no escribe en Gmail ni Sheets, no toca el cursor, `quotes` ni ANKA 01.

**Casos de aceptación**
- C1 · `job_id` estable, distinto por mensaje, tipo y versión; formato `ANKAJOB-` + 16 hexadecimales.
- C2 · Crear dos veces: una fila y un evento. Sin fila de recepción, ya interpretada o sin identidad: se rechaza.
- C3 · La referencia solo lleva metadatos; `ref_sha256` cambia si cambia la referencia.
- C4 · La hoja `.xlsx` trae el `job_id`, ambos ids, el hash y los adjuntos, y no trae cuerpo ni texto.
- C5 · Publicar: crea con la marca, verifica, guarda `sheet_file_id` y deja `publicado` con un evento. Republicar actualiza el mismo archivo.
- C6 · Dos publicaciones simultáneas: un solo archivo en Drive.
- C7 · Respuesta perdida tras crear: la siguiente publicación adopta el archivo con la marca, sin crear otro.
- C8 · Dos archivos con la misma marca: `ambiguo`, sin borrar y sin republicar.
- C9 · Error de Drive: vuelve a `pendiente_de_publicar` con `last_error`; el reclamo vencido se retoma; el reclamo vigente no.
- C10 · Importar el borrador concilia el mensaje y el trabajo; reimportar y la importación aislada no duplican nada.
- C11 · Un trabajo no se concilia por otro mensaje del mismo hilo.
- C12 · La CLI `publicar` se niega sin `--confirmar-drive`.

### T-50 · «Respuesta sugerida lista para revisar» solo anuncia una respuesta que existe
**Estado: implementado** (pruebas en `tests/test_novedad_respuesta_sugerida.py`). Decisión: D-154. Acota U-25 y U-73.4; no cambia estados, `ESTADOS`, `transiciones.json`, la recepción, la cola T-49 ni los contratos de Work.

**Defecto (piloto 3B).** `_derive_notifications` (`anka/infra/store.py`) derivaba la novedad `draft_suggested` solo del estado del caso (`sugiere_respuesta`). Un análisis de Work **incompleto** queda en `review` («Por decidir») y `_work_analysis` le quita el texto al cliente (`work_reply` vacío): la persona veía «Respuesta sugerida lista para revisar» sin que hubiera ninguna respuesta. El importador no crea novedades; las produce la derivación al abrir la base.

**Cuatro cosas distintas** (no se mezclan):
- **Análisis importado:** el caso existe en `quotes` con el hecho `analysis` en la auditoría. Lo anuncia `request_received` («La solicitud ya fue analizada y agregada al dashboard»).
- **Análisis incompleto:** `analysis_complete = false`. Work no pudo cerrar el alcance; el caso necesita revisión técnica y **no** propone correo al cliente.
- **Revisión técnica pendiente:** es el estado del caso (`review`, «Por decidir»). Es lo que ya dice la etapa en la lista; no genera una novedad propia.
- **Respuesta comercial preparada:** hay texto utilizable para el cliente: `quotes.draft_body` no vacío o `analysis.work_reply.text` no vacío.

**Qué debe cumplirse**
1. **Regla.** `draft_suggested` no se deriva cuando el análisis declara `analysis_complete = false` **y** no hay respuesta preparada (`draft_body` vacío y `work_reply.text` vacío). Con una respuesta preparada por una persona (`draft_body`) sí se deriva, aunque el análisis siga incompleto: ahí la respuesta existe y es suya.
2. **Sin respuesta, la novedad es la que ya existe.** El caso conserva `request_received` («analizada y agregada»). No se crea un tipo nuevo ni se cambia `REQUIEREN_ACCION`; la revisión técnica pendiente se ve en la etapa de la lista (U-13).
3. **Se decide en la derivación, sin tocar el caso.** La condición va en la derivación SQL (con `json_valid`, para que un `analysis_json` dañado no la rompa y siga el comportamiento de siempre). No cambia estados, análisis ni `quotes`.
4. **Reimportar.** Reimportar un análisis incompleto no crea la novedad ni duplica `request_received`. Si el mismo mensaje se reanaliza después de forma completa y con respuesta, `draft_suggested` aparece una sola vez (la clave `UNIQUE` y el `INSERT OR IGNORE` no cambian). Una novedad ya creada **no se borra ni se modifica** (historial intacto).
5. **No cambia lo demás.** Un análisis que no declara `analysis_complete` (modos `local` y `api`, o Work completo) se deriva como siempre. No se toca ninguna otra novedad.
6. **Límite conocido.** Un análisis completo de Work con `draft_reply` vacío sigue derivando `draft_suggested` por el estado: la pantalla aún puede proponer un texto armado con las preguntas (`suggested_reply`), y decidir eso exige las preguntas, fuera de esta corrección.

**Casos de aceptación**
- C1 · Incompleto sin respuesta: solo `request_received`; el estado del caso es el mismo que antes del cambio.
- C2 · Incompleto aunque Work mande `draft_reply`: no hay respuesta (el importador la descarta); solo `request_received`.
- C3 · Completo con respuesta: `request_received` y `draft_suggested`.
- C4 · Incompleto con un borrador de la persona (`draft_body`): se deriva `draft_suggested`.
- C5 · Reimportar el incompleto: sin duplicados ni `draft_suggested`.
- C6 · Incompleto y luego completo con respuesta para el mismo mensaje: `draft_suggested` una sola vez; completo y luego incompleto: la novedad anterior se conserva.
- C7 · `analysis_json` dañado: la apertura de la base no falla y se deriva como siempre.
- C8 · Una novedad `draft_suggested` histórica de un caso incompleto no se borra ni se marca.

### T-51 · Un trabajo Work solo se crea y se publica para un mensaje elegible
**Estado: implementado** (pruebas en `tests/test_elegibilidad_de_trabajos.py`). Decisión: D-155. Acota T-49 y aplica R1/R3; no cambia `[ANKA_DASHBOARD_JSON_V1]`, T-47, T-48 ni el estado de recepción de ningún mensaje.

**Defecto (diagnóstico 4A).** `crear_trabajo` aceptaba cualquier fila `pendiente_de_procesar`, incluidas las notificaciones de ChatGPT («[Task Update] ANKA 01…», de `noreply@tm.openai.com`). El receptor (U-81) solo excluye los mensajes propios y los anteriores al primer checkpoint: nunca aplicó R1.

**Una sola clasificación.** No hay un segundo clasificador: se reutiliza el de R1 (`anka/dominio/reception.py`: remitente no-reply, `List-Unsubscribe`, `Auto-Submitted`, `Precedence` masivo, spam de Gmail) y su rescate por términos comerciales (`commercial_filter_terms`, el mismo de `pipeline`). Se añade una única señal propia, inequívoca: la notificación de las tareas de ChatGPT (dominio `tm.openai.com`).

**Qué debe cumplirse**
1. **Tres veredictos**, calculados por una función pura (`evaluar_elegibilidad`, `anka/dominio/trabajo_work.py`) a partir de la fila de `recepcion_mensajes`:
   - `elegible`: remitente humano o sin ninguna señal de automatización registrada. Las solicitudes y los seguimientos de personas son siempre elegibles, aunque Gmail los ponga en Promociones (R1).
   - `excluido` (con motivo): TODOS los remitentes del mensaje son automáticos de forma inequívoca. Motivos: los de R1 (`remitente_no_reply`, `list_unsubscribe`, `auto_submitted`, `precedence_masivo`, `spam_gmail`) y `notificacion_de_chatgpt`.
   - `revision` (clasificación pendiente, con motivo): hay duda y no se descarta en silencio. Motivos: `aviso_automatico_con_terminos_comerciales` (R1: un aviso automático que habla de cotizaciones sí se analiza; no aplica a la notificación de ChatGPT) y `remitente_desconocido` (fila sin remitente legible).
2. **Qué señales usa.** El receptor guarda en la evidencia del mensaje la señal de R1 que detectó al leerlo (`evidence.noise_signal`; solo cuando hay una). Las filas registradas antes de este cambio no la tienen: para ellas solo se evalúa el remitente guardado (no-reply y ChatGPT). La falta de señal registrada nunca cuenta como ruido.
3. **Antes de crear.** `crear_trabajo` evalúa la elegibilidad y rechaza (`TrabajoRechazado`) lo `excluido` y lo `revision`. Un trabajo ya existente se devuelve igual (idempotencia T-49).
4. **Antes de publicar.** `publicar_trabajo` vuelve a evaluar antes de reclamar el trabajo: si no es elegible no toca Drive y devuelve `no_elegible` o `clasificacion_pendiente`. Cubre trabajos creados antes de esta regla o cuando cambió la clasificación.
5. **Nada desaparece en silencio (R3).** Cada rechazo deja un solo evento por mensaje y veredicto en `recepcion_eventos` (`trabajo_excluido` o `trabajo_clasificacion_pendiente`, con motivo y etapa). La fila de recepción y su estado no cambian.
6. **Rescate por una persona.** `work_jobs crear --forzar-elegibilidad` (y `elegibilidad --forzar`) deja el evento `trabajo_elegibilidad_forzada` y desde entonces el mensaje cuenta como elegible para crear y publicar. No se puede forzar un mensaje que no está en la cola ni uno ya interpretado.
7. **Ver sin escribir.** `work_jobs elegibilidad` (sin argumentos) lista, solo en lectura, el veredicto de cada mensaje pendiente (ids, veredicto, motivo; nunca asunto ni cuerpo).
8. **Sin efectos fuera de lo dicho.** No cambia la recepción, el cursor, los estados de `recepcion_mensajes`, `quotes`, Gmail, Drive, Sheets ni ANKA 01.

**Casos de aceptación**
- C1 · Notificación de ChatGPT (`noreply@tm.openai.com`, y otra dirección de `tm.openai.com`): excluida, sin trabajo y con un evento.
- C2 · Cada señal de R1 registrada por el receptor (`list_unsubscribe`, `auto_submitted`, `precedence_masivo`, `spam_gmail`) y el remitente no-reply excluyen.
- C3 · Solicitud de una persona, y seguimiento en un hilo existente: elegibles, aunque el hilo tenga mensajes automáticos.
- C4 · Aviso automático cuyo asunto o adjunto menciona un término comercial: `revision`; sin término: excluido. La notificación de ChatGPT no se rescata.
- C5 · Fila sin remitente: `revision`. Fila antigua sin señal registrada y con remitente humano: elegible.
- C6 · Varios remitentes: solo se excluye si todos son automáticos.
- C7 · Un trabajo creado antes de la regla y no elegible no se publica ni toca Drive.
- C8 · Repetir el rechazo no duplica el evento; el estado de la fila de recepción no cambia.
- C9 · Forzar la elegibilidad permite crear y publicar y deja su evento; no sirve para un mensaje inexistente o ya interpretado.
- C10 · El receptor guarda `noise_signal` solo cuando hay señal y no cambia nada más de lo que ya guardaba.
- C11 · La identidad (`job_id`) y la idempotencia de T-49 siguen iguales para los elegibles.

### T-52 · Clasificación comercial de cada mensaje: preliminar en Python, confirmada por Work
**Estado: implementado en Python** (pruebas en `tests/test_clasificacion_comercial.py`); **pendiente de prueba real en Work** (que ANKA 01 devuelva `classification`). Decisión: D-156. Se apoya en R1 y T-51; no cambia T-49 (`job_id`, idempotencia), `[ANKA_DASHBOARD_JSON_V1]` (solo se le añade un campo opcional), el receptor ni ANKA 01.

**Una sola cadena, sin sistema paralelo.** El receptor (U-81) guarda el mensaje; T-51 excluye el ruido inequívoco; T-52 clasifica lo que queda y Work confirma o corrige. Un remitente humano NO prueba por sí solo que haya una solicitud.

**Las cuatro clasificaciones** (`SOLICITUD_NUEVA`, `SEGUIMIENTO`, `NO_SOLICITUD`, `REVISION_MANUAL`)
1. **Preliminar de Python** (función pura `clasificar_preliminar`, `anka/dominio/clasificacion.py`), con el veredicto de T-51, si el hilo tiene expediente, el asunto, el fragmento (`snippet`) de Gmail que guarda el receptor y los nombres de adjuntos:
   - T-51 `excluido` → `NO_SOLICITUD` (motivo de T-51). No se crea trabajo.
   - T-51 `revision` → `REVISION_MANUAL` (motivo de T-51). No se crea trabajo hasta que una persona lo fuerce (T-51).
   - el `thread_id` ya tiene un expediente (`quotes`) → `SEGUIMIENTO`.
   - menciona (en el asunto, el fragmento o un adjunto) un servicio de Grupo Anka (topografía, geomática, LiDAR, fotogrametría, batimetría, inspección, georadar, catastro, escáner terrestre y afines) o un término comercial de R1, sea o no una petición explícita → `SOLICITUD_NUEVA`.
   - cualquier otro mensaje humano → `REVISION_MANUAL` con motivo `ambiguo_para_work`: **se envía a Work** para la clasificación semántica; las solicitudes indirectas, que no usan esas palabras, no se pierden.
   Las palabras clave solo elevan la confianza; **nunca descartan**. Solo T-51 descarta, y solo ruido inequívoco.
2. **Trazabilidad.** Cada clasificación preliminar queda como evento `clasificacion_preliminar` en `recepcion_eventos` (por `thread_id` y `message_id`; clase y motivo, sin asunto ni cuerpo). Repetir la evaluación no duplica el evento; solo se añade otro si cambia la clase o el motivo (p. ej. el hilo ya tiene expediente).
3. **Hacia Work.** `crear_trabajo` informa la clasificación preliminar y la hoja TRABAJO la lleva (`clasificacion_preliminar`, `motivo_preliminar`) junto con la orden de confirmarla o corregirla. La hoja es solo de lectura; la identidad del trabajo y su huella no cambian.
4. **De vuelta de Work.** El borrador puede traer, además, `classification` (uno de los cuatro valores) y `classification_reason` (texto breve). Es opcional: si falta, el análisis se importa como siempre (compatibilidad). Se toleran mayúsculas y espacios («solicitud nueva»); `null` equivale a no traerla. Un valor ilegible NO se borra (el caso avanzaría como si Work no hubiera dicho nada): se toma como `REVISION_MANUAL` y se avisa.
5. **Solo lo confirmado avanza.** Si `classification` es `SOLICITUD_NUEVA` o `SEGUIMIENTO` (o falta) el análisis sigue su camino: tabla de actividades, faltantes y respuesta preparada. Si es `NO_SOLICITUD` el caso queda descartado (como el estado `Descartada`) sin actividades ni respuesta. Si es `REVISION_MANUAL` el caso queda en revisión técnica interna sin actividades, preguntas ni respuesta preparada, para que decida una persona. En los dos últimos casos el mensaje igualmente se concilia (T-48): la cola no se queda esperándolo.
6. **Auditoría de Work.** La clasificación de Work se guarda en el análisis (`classification`) y queda como evento `clasificacion_work` por mensaje; el evento `interpretado_por_work` la incluye. Si Work corrige la preliminar, ambas quedan visibles.
7. **Nada sale al cliente** (R8) ni se escribe en Gmail, Drive o Sheets por esta clasificación.

**Casos de aceptación**
- C1 · Notificación de ChatGPT / aviso automático: `NO_SOLICITUD`, sin trabajo.
- C2 · Aviso automático con término comercial o remitente sin dirección: `REVISION_MANUAL`, sin trabajo.
- C3 · Mensaje de una persona que menciona un servicio en el asunto o en un adjunto, con o sin la palabra «cotización»: `SOLICITUD_NUEVA`.
- C4 · Mensaje de una persona en un hilo con expediente: `SEGUIMIENTO`, aunque no mencione servicios.
- C5 · Mensaje humano sin señales (p. ej. «Hola, ¿podemos hablar?»): `REVISION_MANUAL` / `ambiguo_para_work`, con trabajo y sin descartarlo.
- C6 · El `job_id`, la huella y la idempotencia de T-49 son los mismos con o sin clasificación.
- C7 · Evento preliminar único por clase y motivo; cambia de clase → evento nuevo.
- C8 · Work devuelve `NO_SOLICITUD`: el caso se descarta, sin actividades; la cola queda `interpretado_por_work`.
- C9 · Work devuelve `REVISION_MANUAL`: revisión técnica interna, sin actividades ni respuesta.
- C10 · Work devuelve `SOLICITUD_NUEVA` o `SEGUIMIENTO`, o no devuelve el campo: se importa como siempre.
- C11 · Un valor de `classification` ilegible se toma como `REVISION_MANUAL` con aviso, y nunca avanza.
- C12 · La hoja del trabajo lleva la clasificación preliminar y sigue sin incluir cuerpo del correo.

### T-53 · Despacho de trabajos a Work: cola documental PENDIENTES / CERRADOS administrada por Python
**Estado: implementado en Python** (pruebas en `tests/test_despacho.py`); **pendiente de prueba real** (un solo expediente, con autorización). Decisión: D-157. Une U-81 (recepción), T-51/T-52 (elegibilidad y clasificación), T-49 (cola de trabajos) y T-48 (conciliación); no crea otro receptor ni otro clasificador y no cambia `[ANKA_DASHBOARD_JSON_V1]`, `job_id` ni la huella de T-49.

**La cola documental.** En la carpeta de laboratorio de Drive (la «raíz») Python administra dos subcarpetas, `PENDIENTES` y `CERRADOS`. Solo es una vista para Work: **SQLite es la autoridad de los estados** (`trabajos_work`); si Drive y SQLite discrepan, manda SQLite y Drive se corrige. Work solo lee `PENDIENTES`; no mueve, no borra ni escribe archivos.

**Qué debe cumplirse**
1. **Carpetas sin duplicados.** `asegurar_carpetas` busca cada subcarpeta por nombre, tipo y padre (fuera de la papelera), la crea si falta y verifica después de crear. Si hay más de una con el mismo nombre se detiene (`carpeta_ambigua`) y no elige.
2. **Qué se publica.** Solo un mensaje (a) pendiente de interpretación en `recepcion_mensajes`, (b) elegible por T-51 (el ruido y la duda nunca generan trabajo) y (c) dentro del alcance del despacho. Un mensaje de T-52 `REVISION_MANUAL` con motivo `ambiguo_para_work` sí se publica: Work lo clasifica. El trabajo se crea con `crear_trabajo` y se publica en `PENDIENTES` con `publicar_trabajo`; ambos conservan sus controles anti-duplicado.
3. **Qué se mueve a CERRADOS.** Un trabajo `conciliado` (Work ya devolvió el análisis y el importador lo guardó, T-48) cuya hoja sigue en `PENDIENTES`. Antes de mover se comprueba la marca `anka_job_id` del archivo; un archivo sin la marca, en la papelera o ausente no se mueve y se informa. Mover es idempotente: si la hoja ya está en `CERRADOS` no se hace nada. El movimiento anota un solo evento `trabajo_archivado` y guarda `folder_id` = `CERRADOS`; el estado del trabajo no cambia.
4. **Recuperación e idempotencia.** Cada pasada es repetible: un trabajo `pendiente_de_publicar` o `publicado` sin su hoja se reintenta; un fallo de Drive o de red se anota en `last_error` y se reintenta en la pasada siguiente, hasta `MAXIMO_DE_INTENTOS` (5); luego el trabajo queda `agotado` en el informe y requiere a una persona. Un trabajo `ambiguo` nunca se toca. Dos pasadas simultáneas no duplican: lo impiden el reclamo y la marca de T-49.
5. **Límite del piloto.** Cada pasada publica **como máximo un trabajo nuevo** y solo del mensaje nombrado en el alcance. Sin mensaje en el alcance no hay despacho (no existe un modo «toda la cola»; la cola histórica no se procesa).
6. **Desactivado por defecto.** `config.json`: `work_dispatch_enabled` (falso), `work_dispatch_folder_id` (la raíz), `work_dispatch_thread_id` y `work_dispatch_message_id` (el único mensaje del piloto). Activarlo exige modo `chatgpt_work` y los tres textos. `work_jobs despachar --automatico` lee esa configuración y no hace nada si está desactivada; sin `--automatico` se indican la carpeta y el mensaje en la orden. En ambos casos escribir en Drive exige `--confirmar-drive`. **No hay proceso persistente ni tarea periódica**: cada orden es una sola pasada.
7. **Informe.** Cada pasada devuelve un resumen sin asuntos ni cuerpos: carpetas, trabajo creado o reutilizado, publicación, archivados, rechazados por T-51, agotados, ambiguos y errores.
8. **Instrucciones para la futura tarea Work.** `docs/TAREA_WORK_COLA.md` define qué hace una ejecución: toma **un** trabajo de `PENDIENTES`, verifica la hoja contra Gmail, evita repetir si ya existe el borrador de ese mensaje, devuelve el borrador con `[ANKA_DASHBOARD_JSON_V1]` (y `classification`) y no mueve archivos. Es un texto para revisar; **no se crea ninguna tarea** ni se modifica ANKA 01.
9. **Nada sale al cliente** (R8) ni se crean borradores desde Python.

**Casos de aceptación**
- C1 · Primera pasada: se crean `PENDIENTES` y `CERRADOS`, se publica un trabajo en `PENDIENTES`; repetirla no duplica carpetas ni archivos.
- C2 · Dos subcarpetas con el mismo nombre: `carpeta_ambigua` y no se escribe nada más.
- C3 · Ruido (ChatGPT, no-reply) y duda de T-51: no se crea trabajo ni se publica nada.
- C4 · Un mensaje fuera del alcance (otro hilo u otro mensaje) no se despacha, aunque esté pendiente.
- C5 · Con varios mensajes elegibles pero alcance de uno, solo se publica ese.
- C6 · Un trabajo conciliado se mueve de `PENDIENTES` a `CERRADOS` una sola vez; sin marca o en la papelera no se mueve.
- C7 · Fallo de Drive al publicar: queda anotado, la pasada siguiente lo recupera sin duplicar; tras 5 intentos queda `agotado`.
- C8 · Respuesta perdida al crear la hoja: la pasada siguiente adopta la hoja existente (T-49).
- C9 · Despacho automático desactivado, incompleto o sin mensaje: no hace nada ni toca Drive.
- C10 · SQLite manda: el estado del trabajo no depende del contenido de las carpetas.
- C11 · `job_id`, huella y clasificación preliminar de T-49/T-52 no cambian.

### T-54 · Paquete de referencia del contrato para la tarea Work de la cola, legible desde Drive
**Estado: implementado en Python** (pruebas en `tests/test_paquete_work.py`); **pendiente de publicar y de prueba real en Work**. Decisión: D-158. No cambia `[ANKA_DASHBOARD_JSON_V1]`, el esquema, `PRECISO_V6.1`, T-52, T-53 ni ANKA 01.

**Defecto.** La primera ejecución programada de la cola (piloto 5B) descubrió el trabajo y verificó Gmail, pero se detuvo: no pudo acceder a una versión verificable del contrato T-52 ni de `PRECISO_V6.1`. Work solo lee lo que está en Drive o en Gmail; el repositorio y sus ZIP no le sirven.

**Qué debe cumplirse**
1. **Fuentes canónicas, sin copias a mano.** El paquete se construye con `python -m anka.cli.paquete_work construir`: el esquema es `spec/contrato_work.schema.json` y el prompt es `docs/PROMPT_WORK_ANKA_V6.txt` (`PRECISO_V6.1`), copiados byte a byte; las reglas T-52, el formato de retorno y las instrucciones de una ejecución viven en `docs/paquete_work/`. Los ejemplos se derivan del ejemplo oficial de `PRECISO_V6.1`, de modo que no se inventan ni se simplifican campos obligatorios.
2. **Autónomo.** Una carpeta de archivos de texto (`.md`, `.json`, `.txt`), sin ZIP, sin rutas locales y sin depender de otro archivo del repositorio.
3. **Verificable.** `manifest.json` declara la versión del paquete (derivada de las huellas, por lo que es reproducible), el origen (rutas relativas del repositorio), el contrato (nombre, versión del esquema y las cuatro clases) y el prompt, y la huella SHA-256 y el tamaño de cada archivo.
4. **Cuatro ejemplos ficticios válidos**, uno por clasificación (`SOLICITUD_NUEVA`, `SEGUIMIENTO`, `NO_SOLICITUD`, `REVISION_MANUAL`). Cada uno cumple el esquema, se importa sin avisos de formato y produce en Python el efecto que T-52 define. Además son **coherentes en el sentido**: un dato que el resumen da por recibido no figura como faltante, pregunta pendiente ni hallazgo ausente; el estado dice lo mismo que las preguntas; y la clasificación concuerda con el estado y la tabla. `incoherencias` (heurística) lo comprueba y `verificar_paquete` rechaza un paquete cuyos ejemplos se contradigan (defecto hallado por Work en `ejemplo_SEGUIMIENTO.json`, piloto 5B).
5. **Detenerse ante un paquete dudoso.** Las instrucciones obligan a Work a verificar el paquete (completo, íntegro, vigente, sin contradicciones) antes de analizar y a detenerse, sin borrador, si algo falla. Python ofrece la misma verificación (`verificar_paquete`) y la prueba con paquetes alterados, incompletos y desactualizados.
6. **Excepción declarada.** La tarea de la cola lee Drive solo para leer (`PENDIENTES` y la referencia); `PRECISO_V6.1` dice que ANKA 01 no usa Drive y sigue siendo así.
7. **Publicación explícita e idempotente.** `paquete_work publicar --carpeta-id <raíz> --confirmar-drive` crea (si falta) una sola subcarpeta `REFERENCIA_ANKA_V1` en la raíz de laboratorio y sube cada archivo; repetirla no duplica nada y solo actualiza lo que cambió (marca `anka_paquete_sha256`). Más de un archivo o carpeta con el mismo nombre detiene la publicación. Nunca borra. Sin `--confirmar-drive` no toca Drive. No hay proceso ni tarea periódica.

**Casos de aceptación**
- C1 · Construir dos veces produce los mismos bytes y la misma versión.
- C2 · El esquema y el prompt del paquete son idénticos a los del repositorio y las huellas del manifiesto coinciden.
- C3 · Los cuatro ejemplos cumplen el esquema y se importan sin avisos; `NO_SOLICITUD` descarta el caso y `REVISION_MANUAL` lo deja en revisión sin actividades; los otros avanzan.
- C4 · Un paquete con un archivo alterado, faltante, un esquema sin la clasificación o de otra versión no pasa `verificar_paquete`.
- C5 · Ningún archivo menciona ZIP ni rutas locales.
- C7 · El ejemplo de `SEGUIMIENTO` no vuelve a pedir un dato que su resumen declara recibido; un ejemplo que se contradiga (en la forma original defectuosa o en las equivalentes) no pasa la verificación.
- C6 · Publicar crea la subcarpeta y los archivos una sola vez; repetir no duplica; un duplicado detiene la publicación.

### T-55 · Coordinación automática Python–Work: un ciclo acotado que despacha, importa y archiva
**Estado: implementado en Python** (pruebas en `tests/test_coordinacion.py`); **desactivado por defecto y sin prueba real automática** (requiere autorización). Decisiones: D-159, D-160, D-161, D-162. Compone T-53 (despacho), T-47 (importación aislada), T-48/T-49 (conciliación) y U-81 (recepción); no crea otro receptor, otro importador ni otro clasificador y no cambia `[ANKA_DASHBOARD_JSON_V1]`, `job_id`, la huella de T-49 ni ANKA 01.

**Qué resuelve.** El piloto 5B probó a mano el ciclo completo (recepción → trabajo → PENDIENTES → Work → borrador → importación → CERRADOS). T-55 lo ejecuta solo, en pasos pequeños y repetibles, sin que Python decida nada comercial y sin enviar nada al cliente.

**Qué debe cumplirse**
1. **Un ciclo, tres fases, siempre en este orden:** (a) *archivar* los trabajos conciliados cuya hoja sigue en `PENDIENTES`; (b) *importar* el borrador estructurado de los trabajos `publicado` con T-47 (`importar_aislado`, un mensaje cada vez, origen verificado siempre); (c) *despachar* a `PENDIENTES` los mensajes elegibles nuevos y reintentar los trabajos `pendiente_de_publicar`. El orden cierra lo terminado antes de abrir lo nuevo. Un fallo en una fase se anota y no impide las otras.
2. **Solo lo que SQLite dice.** Qué se archiva, importa o publica sale de `trabajos_work` y `recepcion_mensajes`; Drive solo se toca para ejecutar y nunca decide por el contenido de las hojas ni por qué hay en las carpetas. La conciliación sigue siendo la de T-48 dentro de la importación: el ciclo no cambia estados de trabajo por su cuenta, salvo lo que ya hacen `publicar_trabajo` y el archivado de T-53.
3. **Solo mensajes elegibles (T-51) y nuevos.** Un mensaje entra únicamente si está pendiente de interpretación, es elegible, **no tiene trabajo** y fue detectado en o después de `work_coordination_since`. Ruido, duda de T-51 y la cola histórica anterior a esa fecha nunca se procesan. Los mensajes con clasificación preliminar ambigua (`ambiguo_para_work`) se publican para que Work los clasifique; Python no los trata como solicitud comercial (T-52).
4. **Límites conservadores por ciclo.** `work_coordination_max_publish` (1 por defecto, máximo 5), `work_coordination_max_import` (1, máx. 5) y `work_coordination_max_archive` (2, máx. 10). Las importaciones rotan: el trabajo consultado hace más tiempo (o nunca) va primero, para que uno sin borrador no deje sin turno a los demás.
5. **Desactivado por defecto.** `work_coordination_enabled` (falso). `work_coordination_alert_minutes` (120) fija la alerta del punto 12. Activarlo exige modo `chatgpt_work`, `work_coordination_folder_id` (la raíz con PENDIENTES y CERRADOS), `work_coordination_since` (fecha y hora con zona), que `work_dispatch_enabled` esté apagado (un solo despachador) y los límites dentro de rango. `work_coordination_interval_seconds` (60 a 3600; 300 por defecto) fija cada cuánto corre. Sin esa configuración completa, no hay ciclo ni se toca Drive o Gmail.
6. **Mensaje de laboratorio.** Con `work_coordination_thread_id` y `work_coordination_message_id` (ambos o ninguno) todas las fases se limitan a ese mensaje, como en el piloto. También se acepta por la orden `work_jobs coordinar --thread-id --message-id`.
7. **Concurrencia.** Un candado de instancia única del sistema operativo (`work_coordinacion.lock`) impide dos ciclos a la vez, aunque sean del supervisor y de una orden manual; el segundo responde `concurrente` sin hacer nada. Debajo siguen el reclamo y la marca de T-49. **No se supone exclusión entre ejecuciones de Work:** si Work deja más de un borrador válido para un mensaje, la importación no importa ninguno (`ambiguo`) y el trabajo queda `publicado` para una persona.
8. **Reintentos y recuperación.** Una importación sin borrador es normal (Work aún no responde) y se vuelve a mirar en un ciclo posterior. Una importación `ambiguo`, `rechazado`, `error_de_origen` o `fallo_de_importacion` suma un fallo al trabajo y espera con retroceso exponencial (el doble cada vez, hasta una hora); al quinto fallo consecutivo el ciclo deja de mirarlo en ese proceso (`requiere_persona`) y lo anota. Un fallo de Drive al publicar o archivar se anota (`last_error`) y se reintenta; la publicación se rinde tras `MAXIMO_DE_INTENTOS` (T-53). Tras reiniciar, todo se retoma desde SQLite sin repetir efectos: crear trabajo, publicar, importar y archivar son idempotentes.
9. **Auditoría.** El primer fallo de importación de un trabajo (todo salvo `importado` y `sin_borrador`) deja el evento `coordinacion_importacion` con el resultado, y el quinto fallo consecutivo deja `coordinacion_requiere_persona` con el número de fallos (T-49 guarda un evento por trabajo y tipo); ninguno lleva asuntos ni cuerpos; lo demás ya deja los eventos de T-49/T-53/U-81. Cada ciclo devuelve y registra un resumen con contadores y sin datos del correo.
10. **Sin envíos ni borradores desde Python.** Python solo lee Gmail (borradores y mensajes) y escribe en la carpeta de laboratorio de Drive y en SQLite; no envía, no crea ni modifica borradores (R8). Crear la tarea programada de Work y activar esta función son decisiones del dueño.
11. **Dónde corre.** Como hilo del lanzador, con el mismo patrón que el receptor Gmail (un hilo, nunca dos solapados), y como orden manual de una pasada: `work_jobs coordinar --una-vez --confirmar` (escribe en Drive, lee Gmail). Cambiar el código reinicia el lanzador y no deja ciclos a medias gracias a las reglas de arriba.

12. **Alerta por falta de respuesta de Work.** Un trabajo `publicado` cuyo `published_at` supera `work_coordination_alert_minutes` (15 a 1440; 120 por defecto) sin conciliarse se declara **sin respuesta**: el ciclo lo incluye en `sin_respuesta` del informe cada vez (estado actual), deja **una sola vez** el evento `coordinacion_sin_respuesta` y **una sola vez** un aviso en el registro. Repetir ciclos o reiniciar no duplica el evento ni el aviso (el evento existente manda). La alerta no cambia el estado del trabajo ni lo detiene: sigue importándose si Work responde tarde.
13. **Hoja desaparecida y recuperación segura.** Cuando la importación de un trabajo `publicado` no encuentra borrador, el ciclo comprueba su hoja (`hoja_guardada`: existe, fuera de la papelera y con la marca `anka_job_id` del trabajo). Si falta, deja una sola vez el evento `coordinacion_hoja_ausente` y la recupera en la fase de despacho con la misma ruta de T-49/T-53 (reclamo, adopción de una hoja marcada ya existente, creación con marca y verificación posterior), sin duplicar: si aparecieran dos hojas con la marca el trabajo pasa a `ambiguo` y nada se elige ni se borra. Cada recuperación cuenta contra `work_coordination_max_publish` y un mismo trabajo se recupera como máximo 3 veces por proceso; después queda `requiere_persona`. Un error de Drive al comprobar no se interpreta como ausencia.
14. **Un único responsable de importar borradores.** Con la coordinación activada el coordinador es el **único importador automático**: (a) el lanzador no arranca el puente general (`work_dashboard_sync`) y detiene el que esté en marcha; (b) el puente general, lanzado a mano, se niega a ejecutarse (código de salida de parada, no se reinicia); (c) antes de importar, el ciclo comprueba el latido del puente (`other_bridge_running`): si hay un puente vivo no importa, informa `puente_general_activo` y lo anota en el registro una vez. Se conservan el candado de ciclo (punto 7) y la importación aislada por mensaje (T-47), que una persona puede seguir usando a mano porque es idempotente.
15. **Verificación previa a la activación.** `work_jobs verificar-piloto` (solo lectura: no usa Gmail ni Drive, no escribe en SQLite ni en `config.json`) informa de la configuración efectiva (modo, recepción, despacho, coordinación y límites), los procesos de Anka en marcha (lanzador, dashboard, puente general, receptor aislado, preparador, ciclos manuales), los candados y el latido del puente, el estado de la cola (pendientes y trabajos por estado, cuántos mensajes quedan fuera por el corte histórico, trabajos sin respuesta) y el estado del mensaje de laboratorio. Devuelve `listo` (verdadero solo sin bloqueos), `bloqueos` y `avisos`. Son **bloqueos**: configuración inválida o incompleta para el piloto (carpeta, corte, un solo mensaje de laboratorio), `work_dispatch_enabled` activo, un puente general o un ciclo manual en marcha, dos receptores, y un mensaje de laboratorio que no está en la cola, no es elegible o fue detectado antes del corte. Son **avisos**: que la tarea programada de Work no se puede comprobar desde Python, que la recepción esté apagada, o que el lanzador esté corriendo (leerá la configuración en 30 s).
16. **Apagada por defecto y sin cambios de contrato.** Nada de esto activa la coordinación, la recepción ni el lanzador; no cambia T-51, T-52, `[ANKA_DASHBOARD_JSON_V1]`, `job_id` ni ANKA 01.

**Casos de aceptación**
- C1 · Ciclo vacío o desactivado: no toca Drive ni Gmail.
- C2 · Un mensaje elegible nuevo se convierte en un trabajo y se publica en `PENDIENTES`; repetir el ciclo no duplica trabajo ni hoja.
- C3 · Mensajes anteriores a `work_coordination_since`, ruido y duda de T-51 no generan trabajo.
- C4 · Con varios mensajes elegibles y `max_publish = 1`, un ciclo publica uno; los siguientes ciclos publican los demás.
- C5 · Un trabajo `publicado` con un borrador válido se importa, queda `conciliado` y su hoja pasa a `CERRADOS` en el mismo ciclo o en el siguiente; repetir no duplica.
- C6 · Sin borrador: no cambia nada y no cuenta como fallo.
- C7 · Dos borradores válidos del mismo mensaje: no se importa ninguno, el trabajo sigue `publicado`, queda un evento y se reintenta con retroceso; al quinto fallo `requiere_persona`.
- C8 · Borrador inválido (otro mensaje, sin bloque, JSON ilegible): se rechaza o ignora según T-47 y el trabajo sigue `publicado`.
- C9 · Fallo de Drive al publicar o archivar: se anota y el ciclo siguiente lo recupera sin duplicar.
- C10 · Dos ciclos simultáneos: uno responde `concurrente`.
- C11 · Reinicio (un coordinador nuevo) a mitad de camino: retoma desde SQLite sin duplicar.
- C12 · Un trabajo ya `conciliado` no se vuelve a importar ni a publicar.
- C13 · Alcance de laboratorio: solo se toca el mensaje nombrado.
- C14 · El módulo no importa envío de correo ni crea borradores.
- C15 · La configuración incompleta, fuera de rango o junto con `work_dispatch_enabled` se rechaza al validarla.
- C16 · Un trabajo publicado más allá del umbral sin respuesta queda en `sin_respuesta`, con un solo evento y un solo aviso aunque pasen muchos ciclos o se reinicie; uno dentro del umbral no.
- C17 · Si Work responde tarde, el trabajo se importa y se concilia aunque ya estuviera alertado.
- C18 · Hoja borrada, en la papelera o sin la marca de su trabajo: se recupera una sola hoja marcada; repetir no duplica; con una hoja marcada ya existente la adopta; con dos marcadas queda `ambiguo`; un error de Drive al comprobar no recupera nada.
- C19 · Con la coordinación activada, el lanzador no arranca el puente general y detiene el que esté en marcha; el puente general manual se niega; con un puente vivo el ciclo no importa.
- C20 · Con la coordinación apagada, el lanzador y el puente general se comportan como antes.
- C21 · `verificar-piloto` no usa Google ni escribe, y señala cada bloqueo y aviso del punto 15.
- C22 · Un reinicio del coordinador no repite alertas, recuperaciones ni importaciones.
- C23 · El mensaje de laboratorio anterior al corte, no elegible o ausente de la cola bloquea la verificación.


### T-56 · El puente general de borradores solo corre con autorización expresa
**Estado: implementado** (pruebas en `tests/test_puente_autorizado.py`). Decisión: D-163. Corrige el incidente del piloto 6C (19/10/2026): al poner `work_coordination_enabled=false` con el lanzador activo, el supervisor arrancó `work_dashboard_sync` y procesó borradores históricos de laboratorio. No cambia T-47, T-55, `[ANKA_DASHBOARD_JSON_V1]`, `job_id` ni ANKA 01.

**Qué resuelve.** Hasta ahora el puente general corría por omisión en modo `chatgpt_work` siempre que la coordinación no estuviera activa; apagarla era, sin querer, una orden de arrancar el puente. El puente es el único componente que importa en bloque todos los borradores de la cuenta, así que arrancarlo debe ser una decisión expresa.

**Qué debe cumplirse**
1. **Clave de autorización.** `work_general_bridge_enabled` (booleano, **falso por defecto**). El puente general solo corre si vale exactamente `true`: ausente, nulo, texto, número u otro valor cuenta como apagado, y `validate_config` rechaza un valor que no sea booleano.
2. **Tabla de decisión** (`supervisor.puente_general_corre`): corre solo con modo `chatgpt_work` **y** autorización **y** coordinación inactiva. Cualquier otra combinación no lo arranca, y si ya estaba en marcha el lanzador lo detiene en la siguiente relectura de la configuración (30 s).
3. **Apagar la coordinación nunca arranca el puente.** Es el caso del incidente: coordinación `true` → `false` sin autorización deja el puente apagado.
4. **La coordinación activa lo excluye siempre** (D-161), aunque la autorización esté en `true`.
5. **El puente lanzado a mano respeta el mismo control**: `work_dashboard_sync.py` (con `--once` o continuo) sale con el código de parada y un mensaje claro si falta la autorización o si la coordinación está activa; se comprueba antes de abrir Gmail y en cada ciclo.
6. **La importación aislada (T-47) no depende de la clave**: `--once --only-thread-id --only-message-id` sigue permitida, como la usa el coordinador.
7. **Configuración conservadora.** Una configuración ilegible en la relectura del lanzador deja el puente apagado.
8. **Procedimiento de cierre.** Detener primero los servicios (lanzador y su árbol de procesos) y después cambiar la coordinación; `docs/PILOTO_6C_ACTIVACION.md` lo dice y `docs/MODO_OPERACION.md` explica cuándo volver a autorizar el puente.

**Casos de aceptación**
- C1 · Tabla completa (ocho combinaciones de modo, coordinación y autorización): solo (Work, sin coordinación, autorizado) corre.
- C2 · Incidente: coordinación `true` → `false` sin autorización, durante tres relecturas, no vigila ni arranca el puente.
- C3 · Clave ausente o con valores inválidos (`"true"`, `1`, `null`, lista…): el puente no corre.
- C4 · Autorizado y sin coordinación: corre en cada vuelta. Autorizado con coordinación: no corre; si estaba en marcha, se detiene. Retirar la autorización lo detiene; una configuración ilegible lo detiene.
- C5 · El puente manual se niega sin autorización (con y sin `--once`) y con coordinación, sin abrir Gmail; autorizado, llega a Gmail; la importación aislada funciona sin autorización.
- C6 · La clave es conocida, booleana, y `config.example.json` la trae en `false`; autorizar no activa la coordinación.
- C7 · La documentación de cierre detiene primero los servicios y la de operación explica cómo volver a autorizar el puente.

### T-57 · Una revisión interna no vacía el análisis técnico de una solicitud legible
**Estado: implementado en Python y en el paquete; pendiente de publicar el paquete y de prueba real en Work** (pruebas en `tests/test_analisis_conservado.py`). Decisión: D-164. Corrige la calidad del análisis del piloto 6C (19/10/2026): Work clasificó bien `SOLICITUD_NUEVA` pero devolvió `activities`, `fields`, `missing_data` y `evidence_register` vacíos porque el caso era de laboratorio, aplicando la forma de «BLOQUEO» a un estado de revisión interna.

**Qué resuelve.** `PRECISO_V6.1` y T-52 mezclaban dos cosas: el *estado* «Requiere revisión técnica interna» (que agrupa bloqueo, datos ficticios, contradicción…) y la *forma de salida* BLOQUEO (que vacía la tabla). Nada decía que «datos ficticios» no vacía la tabla, y no había ejemplo de una `SOLICITUD_NUEVA` en revisión interna con su tabla. El esquema y el importador no cambian: ya admiten ambas formas.

**Qué debe cumplirse**
1. **Cuatro dimensiones independientes:** `classification`, estado del expediente, completitud del análisis (`analysis_complete`) y autorización para contactar al cliente. Ninguna se deduce de otra.
2. **`SOLICITUD_NUEVA` / `SEGUIMIENTO` con mensaje legible conservan la tabla** aunque el caso sea ficticio, de laboratorio, sin vigencia, contradictorio o con lectura parcial documentada: una actividad por servicio pedido con su `fuente` y `estado` «pendiente»; `quantity`, `unit` y `entregable` solo si el texto los dice; `fields`, `missing_data` y `evidence_register` (`unconfirmed` o el que corresponda). `analysis_complete` sigue en `false`.
3. **La forma BLOQUEO se reserva** a: mensaje o adjunto esencial ilegible, identidad o hilo que no coinciden con la hoja, o `REVISION_MANUAL`. Nunca por usar el estado de revisión interna. `NO_SOLICITUD` sigue vacía y `Descartada`.
4. **Preguntas.** Un caso ficticio solo genera `internal_review` (también para lo que se preguntaría al cliente); `ask_client` solo si el caso es real y falta algo indispensable. Nunca texto para el cliente en un caso interno.
5. **No se inventa** método, equipo, precisión, sistema de referencia, cantidad, fecha ni precio; un dato sin fuente no se confirma.
6. **`estado_del_trabajo` = `publicando`** en la hoja `TRABAJO` es la instantánea de su creación; el estado operativo definitivo está en SQLite. No es motivo para vaciar el análisis.
7. **Paquete.** Versión del prompt `PRECISO_V6.1.1` (archivo `PRECISO_V6.1.1.txt`), T-52 actualizada y un ejemplo más, `ejemplo_SOLICITUD_NUEVA_REVISION_INTERNA.json`; `incoherencias` rechaza una `SOLICITUD_NUEVA`/`SEGUIMIENTO` en revisión interna con la tabla vacía sin adjunto bloqueado, un análisis en revisión interna declarado completo o con texto para el cliente, preguntas `ask_client` en un caso ficticio y actividades confirmadas con un hallazgo bloqueante sin confirmar.
8. **Compatibilidad.** Sin cambios en `contrato_work.schema.json` ni en el importador; los retornos históricos (`PRECISO_V6.1`, incluido el del 6C con la tabla vacía) siguen validando e importándose igual, y `PRECISO_V6.1.1` se trata como V6 (verificación por adjuntos).

**Casos de aceptación**
- C1 · `SOLICITUD_NUEVA` normal: sin incoherencias.
- C2 · `SOLICITUD_NUEVA` ficticia en revisión interna: el ejemplo conserva actividades con fuente y `pendiente`, `analysis_complete=false`, solo `internal_review`, sin texto para el cliente, sin precios ni métodos inventados.
- C3 · Retorno vacío del 6C: `incoherencias` lo señala; con un adjunto `bloqueado` se admite vacío.
- C4 · Lectura parcial documentada: conserva la tabla; declararla completa se rechaza.
- C5 · `SEGUIMIENTO` con información adicional y en revisión interna: conserva la tabla.
- C6 · `REVISION_MANUAL` y `NO_SOLICITUD` siguen vacías.
- C7 · Los retornos históricos 5B y 6C validan e importan; la versión nueva se importa sin avisos de formato.
- C8 · El paquete se construye, se verifica y lista el ejemplo nuevo (manifiesto con `PRECISO_V6.1.1`).

### T-58 · Registro individual de un mensaje en la cola de recepción
**Estado: implementado en Python; sin ejecutar contra Gmail real** (pruebas en `tests/test_recepcion_individual.py`). Decisión: D-165. Corrige el hueco que apareció preparando la prueba integrada de T-57: un mensaje de laboratorio llegado después del último sondeo solo podía entrar en la cola con una pasada general de `gmail_reception --once`, que registra todo lo nuevo desde el cursor (aquí, cinco mensajes, tres de ellos ajenos: avisos de ChatGPT y mensajes del hilo del piloto 6C) y avanza el cursor.

**Qué debe cumplirse**
1. **Orden explícita y aislada.** `python -m anka.cli.gmail_reception --registrar-mensaje --thread-id T --message-id M --remitente-esperado D --marca PRUEBA-… --confirmar`. Los cinco parámetros son obligatorios; sin `--confirmar` se rechaza sin tocar Gmail ni SQLite. No se combina con `--once`, `--authorize` ni `--interval`. No hay búsqueda: se exige el `message_id` exacto.
2. **Verificación previa a cualquier escritura**, leyendo ese único mensaje en Gmail: la cuenta del perfil es `ankacomercialia@gmail.com`; el `id` y el `threadId` devueltos son los pedidos; el remitente es exactamente el esperado; la cuenta es destinataria; no es borrador ni enviado; la marca (`PRUEBA-` más letras, números o guiones) está en el asunto y en el cuerpo como «Identificador: <marca>»; y el cuerpo se declara a sí mismo «prueba ficticia». Si algo falla, no se escribe nada.
3. **Reutiliza el procesamiento normal** (`_register_message`): mismos estados, clasificaciones (propio, anterior al checkpoint, pendiente), metadatos de adjuntos, evidencia y eventos. La fuente de la detección queda como `registro_individual`.
4. **Solo ese mensaje.** No se leen ni procesan otras filas de la cola (ni las pendientes ni las que esperan reintento), no se consulta History ni se hace búsqueda, y no se tocan `quotes`, trabajos, borradores ni Drive.
5. **El cursor general no se altera**: `history_id`, `history_page_token`, `last_success_at`, `last_attempt_at`, `initial_checkpoint_at`, el estado de recuperación y los contadores de error quedan como estaban. Solo se toma y se suelta la reserva de la cuenta (evita competir con un sondeo) y se renueva `updated_at`. Sin fila de control inicializada, se rechaza.
6. **Idempotencia.** Si el mensaje ya está en la cola (en cualquier estado) no se escribe nada y se informa `ya_registrado`. Un fallo al leerlo tras insertar deja la cola como estaba (se retira la fila y el evento de esa orden).
7. **Salida sin contenido**: ids, estado y disposición; nunca asunto, cuerpo ni remitente.
8. **Compatibilidad.** La fila registrada es la que `work_jobs elegibilidad` y `work_jobs crear` esperan. La orden no crea trabajos ni publica en Drive.
9. **Uso.** Es una herramienta de laboratorio y de recuperación supervisada: la marca `PRUEBA-` limita hoy su uso a mensajes de prueba declarados.
10. **Respuesta a un hilo con expediente (seguimiento de prueba).** Si el hilo ya tiene un expediente registrado, la marca y
    la declaración «prueba ficticia» se buscan en el **contenido nuevo** del mensaje y no hace falta que la marca esté en el
    asunto: basta una línea exacta «Identificador: <marca>» (sin distinguir mayúsculas, con punto final opcional). El contenido
    nuevo se separa del historial con `correo.contenido_nuevo`, que solo reconoce la respuesta de Gmail (texto nuevo, línea
    de atribución «El … escribió:» y el historial con «>»). Una marca o una declaración presentes solo en la cita no valen, y
    se rechaza sin escribir todo lo que no se pueda separar con fiabilidad (cita sin atribución, atribución sin cita,
    historial sin «>», reenvíos, respuestas intercaladas o texto debajo de la cita). Un hilo **sin** expediente conserva las
    reglas de siempre (marca en el asunto y en el cuerpo). Cuenta, hilo, mensaje, remitente, destinatario, borrador o enviado,
    idempotencia, aislamiento y cursor sin cambios.

**Casos de aceptación**
- C1 · Registro correcto: una fila pendiente de interpretación, eventos `detectado` y `pendiente_work`.
- C2 · Segunda ejecución: `ya_registrado`, sin filas ni eventos nuevos.
- C3 · `message_id` inexistente, hilo, remitente o cuenta incorrectos, mensaje no ficticio, sin marca, borrador o enviado: rechazo sin escrituras.
- C4 · El cursor y el resto de la cola quedan idénticos; no se llama a History ni a búsquedas.
- C5 · Sin `--confirmar` o con parámetros faltantes o combinados: rechazo sin Gmail.
- C6 · `work_jobs crear` acepta la fila registrada.
- C7 · Seguimiento válido (hilo con expediente, asunto con la marca anterior, marca nueva en el contenido nuevo): se registra
  sin tocar cursor, expedientes ni trabajos; repetirlo no duplica.
- C8 · Marca o declaración ficticia solo en la cita, línea que no es exacta, hilo sin expediente o remitente incorrecto: rechazo
  sin escrituras.
- C9 · Separación ambigua entre lo nuevo y lo citado: rechazo sin escrituras.

### T-59 · Ningún texto interno de Work llega al cliente (Fase A: protección de respuestas comerciales)
El contenido interno de Work (el bloque `[ANKA_DASHBOARD_JSON_V1]`…`[/ANKA_DASHBOARD_JSON_V1]`, la sección «REVISAR ANTES
DE ENVIAR - BORRAR ESTA SECCION» y el rótulo «BORRADOR INTERNO — NO ENVIAR») nunca se guarda como borrador de Gmail ni se
envía desde Anka.
1. **Dónde.** Se comprueba el texto final, el que de verdad iría a Gmail, en `reply_email`, por donde pasa todo correo que
   Anka guarda o envía (guardar borrador, enviar desde el dashboard, preguntas del flujo de Python, recordatorios). Además
   `save_dashboard_draft`, `send_dashboard_reply` y el envío de preguntas lo comprueban antes de reservar, cambiar de estado
   o tocar Gmail.
2. **Qué.** Los marcadores del contrato JSON se reconocen sin distinguir mayúsculas. Las frases de la sección y del rótulo
   internos se reconocen **en mayúsculas** (o como encabezado inicial con la forma de T-43), para no bloquear «revisar antes
   de enviar» escrito en un correo normal. Palabras sueltas como «JSON», «borrador» o «dashboard» no se bloquean.
3. **Ante contenido prohibido** se rechaza la operación con el motivo («El correo contiene texto interno de Work…»): no se
   envía nada, no se crea ni actualiza un borrador de Gmail, no se reserva ningún envío y el estado del caso no cambia.
4. **Excepción única.** El borrador de revisión que los modos `local` y `api` dejan en Gmail lleva a propósito la sección
   interna (es el aviso para quien lo revisa y Anka no lo envía): `create_reply_draft` permite la sección pero **nunca** los
   marcadores del contrato.
5. **Lo aprobado es lo entregado.** Antes de reservar el envío o de guardar el borrador se comprueba que el texto del
   mensaje que se va a entregar a Gmail es el aprobado; si no coincide no se envía nada. El evento `sent` del caso incluye
   la **huella SHA-256** del texto aprobado (con saltos de línea normalizados), sin guardar nada más.
6. **No cambia** la aprobación humana (R8), la `marca_de_estado`, las reservas de `mail_operations` ni el importador del
   JSON de Work, que sigue aceptando sus marcadores.
Casos: C1 guardar o enviar con cada marcador → rechazo sin efectos; C2 el mismo texto pegado a mitad de un correo; C3 un
correo legítimo con frases comunes se guarda y se envía, con huella; C4 un cambio de texto después de la aprobación es otro
envío con otra huella; C5 un texto entregado distinto del aprobado no se envía; C6 el importador sigue aceptando el
borrador de Work y su `draft_reply` queda limpio; C7 el borrador de revisión de Python conserva su encabezado.

**Fase B · Continuidad de preguntas y actividades.** Una respuesta del cliente actualiza el mismo expediente sin perder
lo preguntado ni las actividades anteriores.
7. **Rondas de preguntas.** Cada correo enviado desde Anka deja una *ronda* en `rondas_de_preguntas`: cuándo salió, su
   mensaje de Gmail (`mensaje`) y las preguntas que llevaba. `preguntas_enviadas` sigue siendo la última ronda (ahora con
   `mensaje`) y **no se pierde** al importar un análisis posterior, en ningún modo (`store.save`). Una pregunta que Work solo
   propuso, o que la persona aprobó pero no salió en un correo, no es una pregunta enviada. Un expediente anterior, con solo
   `preguntas_enviadas`, se convierte en una ronda al primer análisis.
8. **Respuesta, sin inventar correspondencias.** Un análisis de un mensaje posterior al envío y distinto de él liga su
   `respuesta` (mensaje y hora) a la ronda, una sola vez. De cada pregunta solo se afirma lo visible: `sigue_abierta` si el
   análisis nuevo la vuelve a pedir y `sin_confirmar` si no (que ya no aparezca no prueba que la hayan respondido). Las
   decisiones de `preguntas` se renuevan con cada mensaje (U-22) y el historial queda en las rondas.
9. **Tabla acumulada (`tabla_acumulada.integrar`).** Un seguimiento (análisis de un mensaje posterior al primero del caso)
   se suma a la tabla vigente en vez de reemplazarla. Una actividad es la misma si su nombre coincide sin tildes,
   mayúsculas ni puntuación; nada más se fusiona. Que el seguimiento no repita una actividad **no** la elimina. Se aplica
   solo: agregar una actividad sin parecido, llenar un dato vacío y reemplazar uno que aún no estaba confirmado (si la fila
   nueva no es dudosa), sumando la fuente. **No** se aplica solo, y queda como propuesta (`pending_model_activities`, la misma que
   ya revisa la persona) con su motivo en `revision_de_tabla`: un dato ya confirmado que cambia, una contradicción
   (la fila se marca `contradictorio`), una eliminación pedida en el texto, dos filas con el mismo nombre y datos distintos
   y una actividad nueva parecida a una anterior (mismo servicio y lugar, o nombre con la mitad de las palabras en común).
   La propuesta es la tabla vigente con todo aplicado, así que adoptarla no borra lo que el seguimiento no repitió.
10. **Edición humana y R11.** Con una tabla editada por una persona (`manual_edit`) o bloqueada por la T nada se aplica solo:
    todo queda como propuesta. La tabla vigente solo cambia por lo seguro; si nada se puede aplicar con seguridad, queda
    igual. Importar dos veces el mismo seguimiento da el mismo resultado y no crea versiones ni eventos de más. El primer
    análisis y el reanálisis del mensaje original reemplazan como siempre.
Casos: C8 la ronda sobrevive a un seguimiento; C9 respuesta ligada a la ronda y preguntas abiertas o sin confirmar; C10
propuestas de Work nunca enviadas; C11 expediente anterior a las rondas; C12 seguimiento que aclara un dato, que no repite
el alcance, con actividad nueva, con cantidad corregida, con eliminación pedida, con sustitución ambigua, duplicadas y
contradictorias; C13 tabla de una persona y tabla bloqueada por la T; C14 importación repetida.

**Fase C · Contexto acumulado para Work.** Work interpreta un `SEGUIMIENTO` con el estado vigente del expediente a la vista,
por el mismo circuito Python → Drive → Work → Python y sin cambiar el esquema `ANKA_DASHBOARD_JSON_V1`.
11. **Contexto previo en la hoja.** Cuando la clasificación preliminar del mensaje es `SEGUIMIENTO` (el hilo ya tiene
    expediente), el libro del trabajo agrega tres hojas de solo lectura, construidas al publicar desde el expediente de ESE
    `thread_id` y de ningún otro: `CONTEXTO_PREVIO` (campo/valor: expediente, estado, último mensaje analizado, mensaje por
    analizar, si ese mensaje ya se analizó, si una persona editó la tabla, si hay una propuesta pendiente y sus motivos,
    contradicciones abiertas), `ACTIVIDADES_VIGENTES` (las diez columnas de la tabla vigente, con su fuente) y
    `PREGUNTAS` (cada pregunta con su `tipo`: `enviada`, `propuesta_no_enviada` o `interna`; para las enviadas, la ronda, la
    fecha, el mensaje de Gmail, su estado `enviada`/`sigue_abierta`/`sin_confirmar` y el mensaje que la siguió). Una
    `SOLICITUD_NUEVA` conserva el libro de siempre: no lleva hojas de contexto, ni vacías ni inventadas.
12. **Mínimo y sin secretos.** El contexto no trae correos de otros expedientes, direcciones de correo, tokens, rutas ni
    configuración; los textos largos se recortan (con aviso en `CONTEXTO_PREVIO`) y las listas tienen tope. Una pregunta que
    Work solo propuso o que la persona dejó como interna nunca figura como enviada. El contexto es un estado provisional de
    Python: la hoja lo dice y Work lo contrasta con el hilo.
13. **Reglas de interpretación (`PRECISO_V6.1.2`).** El prompt añade, solo para `SEGUIMIENTO`: leer el mensaje nuevo y el
    hilo; tratar el contexto como provisional; devolver la tabla **acumulada** reutilizando el `activity` exacto de las
    filas vigentes; no quitar una fila por no mencionarla; para una eliminación pedida por el cliente conservar la fila con
    «ya no se requiere» en `notes`; marcar lo modificado en `notes` y los desacuerdos como `contradictorio` con
    `status` de revisión; no volver a pedir datos ya dados ni preguntas enviadas; no dar una pregunta por respondida sin
    evidencia; conservar la revisión humana ante contradicciones. Es compatible con `tabla_acumulada.integrar`, que decide
    qué se aplica solo y qué queda para revisar (los casos se prueban con retornos de ejemplo de Work).
14. **Versión del paquete en los trabajos nuevos.** Al crear un trabajo se anota en su evento `trabajo_creado` la versión del
    paquete contractual vigente (`paquete_version`, la del manifiesto) y la hoja la muestra en `version_del_paquete`. Los
    trabajos creados antes no la tienen y su hoja no cambia; el `job_id` no cambia.
15. **Nada se toca de lo ya hecho.** Construir la hoja no escribe en el expediente; los expedientes conciliados y las hojas
    históricas no se modifican; el esquema, el importador y las importaciones históricas siguen igual.
16. **Paquete reproducible.** La versión, las huellas y el manifiesto del paquete se calculan sobre el texto de sus fuentes
    (esquema, prompt y documentos) con final de línea LF, venga el árbol de trabajo en LF o en CRLF (`core.autocrlf`):
    `paquete_work.leer` normaliza al construir y `.gitattributes` fija esas fuentes en `eol=lf`. Construir desde el árbol
    actual, desde una copia en LF y desde un checkout limpio con CRLF da el mismo paquete byte a byte. Un paquete ya
    publicado se verifica con los bytes con que se publicó (cada archivo contra su propio manifiesto).
Casos: C15 seguimiento con tabla existente (tres hojas); C16 solicitud nueva sin contexto; C17 pregunta enviada, propuesta
no enviada e interna; C18 pregunta respondida o sin respuesta demostrada; C19 tabla editada por una persona y propuesta
pendiente; C20 contexto recortado y sin datos de otro expediente; C21 retornos de Work que cambian una magnitud, agregan una
actividad, eliminan, contradicen o conservan nombres; C22 trabajo nuevo con versión del paquete y trabajo antiguo sin ella; C23 el paquete sale igual desde un árbol en LF,
en CRLF y desde un checkout limpio.

## Recepción determinista de correos en modo `chatgpt_work` (previsto)

**Estado de toda esta sección: previsto. Es solo especificación (incremento 1): no hay código, tablas, migraciones ni pruebas.** La base es la especificación técnica v2, que el dueño aprobó conceptualmente; **eso no autoriza implementar**: cada incremento (2 a 7) necesita la autorización expresa del dueño. Decisiones: D-131 a D-143 y las decisiones técnicas DT en `05-decisiones.md`.

**Cómo se trazan mientras no existen.** Estos requisitos usan encabezado de **cuarto nivel** (`#### U-xx`) a propósito: `check_spec.py` solo exige prueba a los encabezados `### ID · ` y a las reglas R, así que no cuenta estos como requisitos sin prueba ni falla por ellos (no se modificó `check_spec.py`). Al implementar un requisito, el incremento que lo haga lo **promueve a `###`**, escribe sus pruebas y las etiqueta con `# Cubre: U-xx`; hasta entonces ninguno figura como implementado ni como probado (Constitución, Gobernanza).

**Qué problema resuelven.** En modo `chatgpt_work`, un correo nuevo para el que Work aún no dejó un borrador no deja ni caso ni alerta: nadie sabe que llegó. La única vía hoy que crea un caso sin análisis válido parte de un borrador rechazado (T-36). La recepción determinista registra cada correo entrante apenas llega, crea un expediente preliminar con solo lo verificable, y avisa si Work no lo analiza en 30 minutos medidos desde la recepción real en Gmail. **No analiza, no escribe en Gmail, no cotiza y no envía nada al cliente.**

### Vocabulario de esta sección
- **Expediente preliminar**: la fila del caso (`quotes`) creada por la recepción en estado `review`, con remitente, asunto, fecha de recepción y nombres de archivos; sin actividades ni datos inferidos.
- **Registro de recepción**: una fila por mensaje de Gmail visto (`recepcion_mensajes`), con su fase.
- **Cubierto / pendiente de análisis**: un mensaje está cubierto cuando existe **evidencia verificable** de que un análisis de Work lo recogió (U-70). Sin esa evidencia, queda pendiente.
- **Pendiente de comprobación**: mensaje de un hilo que sí tiene un análisis válido, pero sin evidencia de que ese análisis lo recoja. Nunca se da por cubierto por su fecha.

#### U-66 · Todo correo entrante en modo Work deja un expediente, aunque Work no lo haya analizado
**Estado: previsto** (el incremento 3 lo implementa). Decisiones: D-131, D-138, D-140, D-142.

**Qué debe cumplirse**
1. **Solo lectura y solo en modo `chatgpt_work`.** El sondeo corre dentro del proceso del puente (no es un segundo orquestador), después de importar los borradores y antes del seguimiento, en un bloque aislado: un error de recepción se guarda en su propio control y no pone en rojo al puente. Las únicas llamadas a Gmail son `getProfile`, `messages.list` y `messages.get`, más la lectura del hilo (`threads.get`) estrictamente para la comprobación de U-68 (DT-25). Nunca `modify`, etiquetas, `trash`, `send`, borradores, `import` ni `insert`; no marca leído. Los alcances (SCOPES) no cambian y `credentials()` sigue sin abrir el navegador (D-27).
2. **Cuenta verificada.** Antes de cada pasada `getProfile` debe coincidir con la cuenta configurada del puente (`work_sync_account`); si no, el sondeo se detiene con un error de configuración visible y no mezcla cursores.
3. **Expediente preliminar con solo lo verificable.** Se crea en `quotes` con estado `review` (creación, no transición), `first_seen_at` = recepción real (`internalDate`) del entrante más antiguo del hilo, `contact_email` vacío (para que Work pueda fijarlo), remitente = encabezado `From` tal cual (si no hay dirección válida no hay caso: se registra `sin_remitente` y se avisa), asunto recortado sin «Re:/RV:/Fwd:». **No se interpreta el asunto**: ni servicio, ni actividad, ni ubicación, ni magnitud, ni plazo, ni entregables; cliente comercial, tipo y sector quedan «No clasificado». Actividades, campos, faltantes y auditoría de preguntas, vacíos; `analysis_complete` falso.
4. **Adjuntos: solo metadatos.** Nombre, tipo y tamaño, con la leyenda «Anka solo registró el nombre, tipo y tamaño; nadie ha leído este archivo». No se descarga ni se guarda contenido ni el `attachment_id`. La cobertura de lectura de la recepción es la constante `metadatos+estructura_de_adjuntos` y se dice (Constitución I).
5. **Clasificación.** Cada mensaje es `entrante`, `ruido_r1`, `propio` (etiqueta `SENT`/`DRAFT` o dirección propia) o `sin_remitente`. El ruido sigue R1: solo señales de encabezado; las palabras comerciales solo **rescatan** un aviso automático del descarte; **un humano nunca se descarta por palabras**. La procedencia es `externo` o `prueba_interna` (remitente de pruebas configurado y asunto con marca de prueba); las pruebas **sí crean caso**, con la etiqueta «Prueba».
6. **Un hilo, un expediente.** La inserción es `INSERT … ON CONFLICT(thread_id) DO NOTHING`; la recepción **nunca** usa `store.save()`, nunca escribe `analysis_json`, `status` ni `last_inbound_id` de un caso existente y no cambia estados. El segundo mensaje de un hilo solo se registra y, si el caso es todavía preliminar, sube `updated_at`.
7. **Reenvíos.** Un asunto con prefijo de reenvío se registra (`asunto_con_reenvio`) y el texto dice que quien escribe puede ser alguien de Anka y que el cliente original no está identificado; nunca se asigna un remitente original distinto del `From`.
8. **Los otros modos no cambian.** `local` y `api` no importan ni ejecutan la recepción; el indicador de U-73 no aparece si no hay filas de recepción. Work gana una recepción propia: es una mejora de la paridad (local y api ya crean caso por cada hilo candidato).
9. **Nada sale al cliente (R8).** El aviso es una novedad interna; no hay envío, borrador ni recordatorio.
10. **Configuración** (claves nuevas de `config.json`, fuera del repo; validadas en `validate_config`): `recepcion_activa` (falso por defecto, también es el rollback), `recepcion_simulacion`, `recepcion_materializar_desde_horas`, `recepcion_consulta` (`in:inbox`), `recepcion_ventana_inicial_horas` (48, máx. 168) y `recepcion_minutos_alerta` (30, entre 5 y 1440).

**Casos de aceptación**: E1, E2, E3, E9, E10, E12 y E13 (tabla de aceptación, al final de esta sección).

#### U-67 · Registro por fases y cola persistente: los topes limitan la velocidad, no el resultado
**Estado: previsto** (incremento 3). Decisiones: D-132, D-140.

**Qué debe cumplirse**
1. **Fases.** Cada mensaje listado es una fila con fase `por_leer` (id listado, aún sin leer) → `registrado` (leído y clasificado como entrante) → `materializado` (tiene expediente o se reconoció el expediente existente); o `omitido` (ruido, propio, sin remitente, anterior a la activación o dado por no disponible con evidencia, U-68) o `revision_manual` (U-68). Toda fila `omitido` o `revision_manual` conserva su motivo (R3).
2. **Clave.** El registro se identifica por el par `(thread_id, message_id)` (D-140). Un `message_id` que reaparece con otro `thread_id` no sobrescribe nada: se anota como anomalía y queda en `revision_manual`.
3. **Registro inmediato.** Cada id que `messages.list` devuelve y no se conocía se inserta en el acto, antes de cualquier lectura. Desde ese momento el trabajo pendiente vive en la tabla, no en el cursor ni en el tamaño de la pasada.
4. **Drenaje antes de listar, más antiguo primero.** Cada pasada procesa primero la cola: hasta 40 lecturas (`messages.get`) y hasta 10 materializaciones, siempre por `recibido_ms` ascendente (y por orden de inserción las no leídas). Los topes son ≥ 1 y solo limitan la velocidad: lo excedente queda en la cola y se procesa en las pasadas siguientes.
5. **Garantía.** Para todo mensaje `entrante` elegible que Anka llega a listar existe un número finito de pasadas tras el cual tiene expediente o una fila terminal o de revisión con motivo. Ni un tope ni el avance del cursor lo impiden.
6. **Cursor propio, no guardián del trabajo.** Fecha `internalDate` con solape de 10 minutos; avanza solo cuando el listado fue completo (todas las páginas, máx. 10) y todos los ids listados quedaron al menos registrados. No se guarda ningún `pageToken`: reanudar es repetir la consulta desde el cursor. Cada 6 horas se lista además `newer_than:2d` solo para restar ids (mensajes con fecha interna anterior al cursor). Ventana inicial de 48 h (máx. 168 h); tras una parada mayor que 168 h se recorta la consulta y se anota un **hueco visible**: lo ya registrado nunca caduca y el hueco y el backlog son cosas distintas, ambas visibles.
7. **Escrituras cortas.** Todo con `transaccion()`/`lectura()` (D-129): una transacción por mensaje o lote de ≤ 10, y **ninguna llamada a Gmail dentro de una transacción**. Sin dependencias nuevas y sin `connect()` suelto.
8. **Se conserva (T-42).** `recepcion_mensajes` y `caso_identidad` son tablas «se conservan» (evidencia de que el mensaje ya se vio; no se recrea un caso por un mensaje ya registrado). `recepcion_control` no tiene `thread_id`. La migración llega sola en su incremento y solo hacia adelante; el rollback funcional es `recepcion_activa=false`.

**Casos de aceptación**: E2, E3, E8, E14.

#### U-68 · Lecturas fallidas y mensajes «no encontrados»: se registran con motivo y un 404 nunca se toma por borrado
**Estado: previsto** (incremento 3). Decisiones: D-141, D-132.

**Principio.** Un error de Gmail no es una conclusión comercial ni una prueba de eliminación. Un `404` de `messages.get` puede ser un mensaje eliminado, un id inválido, una cuenta o credencial equivocada o un fallo transitorio: **un 404 aislado no es prueba de eliminación y no borra, oculta ni da por terminado nada**. Cada intento deja `intentos`, `ultimo_error` (con su clase) y la fecha; ninguna fila de registro, caso, evento ni estado se borra o cambia por un error.

| Situación | Qué hace Anka |
|---|---|
| Cuota agotada (`es_error_de_cuota`) | Aplaza la pasada con espera creciente; no cuenta como fallo de acceso; las filas quedan como estaban |
| Error de red o 5xx | La fila sigue `por_leer`, suma un intento, registra la clase `red`; reintenta en la pasada siguiente con espera creciente |
| Autorización vencida o revocada | Detiene el sondeo con clase `autorizacion`, aviso en el resumen y la salud; **no abre el navegador** (D-27); las filas quedan intactas |
| Cuenta distinta a la configurada (`getProfile`) | Detiene el sondeo con error de configuración; no mezcla cursores ni toca filas |
| `404` aislado en un id recién listado (o con pocos intentos) | **Reintentar con espera creciente.** La fila sigue `por_leer`, registra «404» con la fecha de cada intento. No es terminal |
| `404` en varios ids seguidos de la misma pasada | Se sospecha de cuenta o credencial, no de borrado: se repite `getProfile`, la clase de error es `acceso`, **ninguno** se marca no disponible y todas las filas siguen `por_leer` |
| `404` que persiste tras varios intentos (mínimo 3, repartidos en al menos 30 min) | La fila pasa a `revision_manual` con el motivo «Gmail respondió 404 en N intentos entre HH:MM y HH:MM». Cuenta en el backlog y en la salud. Una persona decide: reintentar o dar por omitido (con nota, quién y cuándo, y evento) |
| `404` con **evidencia adicional**: tras al menos 2 intentos, `getProfile` correcto de la misma cuenta y la lectura del hilo (`threads.get`) funciona y **no lista** el mensaje | `omitido` con clase `no_disponible` y el motivo «ausente del hilo según la lectura del <fecha>»; se guardan los ids que el hilo sí listó como evidencia. Es reversible: una persona puede devolverlo a `por_leer` |
| Id con formato inválido (`400`) | `revision_manual` con motivo; sin reintentos automáticos |
| Un mensaje ya `materializado` que después da 404 | No se toca el caso, el historial ni el registro; solo una anotación informativa. Un 404 posterior nunca deshace un expediente |
| Listado incompleto o inesperadamente vacío | No se deduce que algo se borró: el cursor no avanza si el listado fue incompleto |

Los números exactos de intentos y esperas son una decisión técnica abierta (DT-27); la regla que no cambia es que **solo la evidencia adicional, nunca el 404 solo, permite dar un mensaje por omitido**.

**Casos de aceptación**: E8 (incluye el comportamiento de cada fila de la tabla) y E11.

#### U-69 · Simulación primero: registrar sin crear nada, y activar sin perder lo registrado
**Estado: previsto** (incremento 3). Decisión: D-133. Propuestas pendientes de confirmación explícita del dueño: DT-1 y DT-20.

**Qué debe cumplirse**
1. **Qué escribe la simulación (`recepcion_simulacion=true`).** Solo filas de `recepcion_mensajes` (`por_leer` → `registrado`/`omitido`) y la fila de control (cursor y salud). **No** escribe en `quotes`, `case_events`, `case_messages`, `dashboard_notifications` ni `event_anchors`; no materializa, no marca `analizado_at`. Su resultado es «registrado, pendiente de materializar»: **la simulación no puede dar nada por terminado**. Gmail sigue recibiendo solo lecturas.
2. **Informe de la simulación.** Mientras dura, la banda de recepción y `/api/salud` dicen «Simulación: N correos habrían creado expediente (M hilos), K descartados como ruido, J propios», calculado sobre las filas.
3. **Activación** (`recepcion_simulacion=false` con `recepcion_activa=true`). La primera pasada anota `activada_at` una sola vez, marca `omitido` («anterior a la activación») lo registrado con recepción anterior a `ahora − recepcion_materializar_desde_horas` y drena el resto con la misma cola y los mismos topes. No hay cursor que «saltar» ni duplicados (clave del registro, `DO NOTHING` y `UNIQUE (thread_id, kind, ref)` de los eventos). Lo omitido por antigüedad queda **visible con su motivo**; `omitido` no significa «cubierto» ni «analizado» (U-70).
4. **Activar exige antes** que el ruido R1 registrado pueda revisarse y rescatarse (R3, D-142): hasta que exista ese panel (incremento 6) la recepción solo se puede usar en simulación.
5. **Primera activación sobre la cuenta real** (propuesta DT-1, pendiente de confirmación explícita): primero simulación con ventana de 48 h, informe al dueño y solo después activación. Omitir lo más viejo que `recepcion_materializar_desde_horas` al activar (propuesta DT-20, pendiente) también necesita confirmación.

**Casos de aceptación**: E15, E16.

#### U-70 · Un análisis de Work posterior (o anterior) al registro cubre solo lo que su evidencia demuestra
**Estado: previsto** (incremento 4). Decisiones: D-134, D-143. No cambia `sync_once`, el parser, el contrato V6.1 ni el prompt.

**Regla central.** **La fecha nunca basta para dar un mensaje por cubierto.** Un mensaje anterior en fecha al mensaje analizado no queda «analizado» por eso: se exige **evidencia verificable** o el mensaje conserva la condición «pendiente de comprobación». `received_at`, `first_seen_at` y el `historyId` de hilo solo son **pistas** (sirven para ordenar y para decirle a quien revisa «este mensaje es anterior al analizado»); no cubren nada. (Corrige la v2, que trataba `received_at` como prueba suficiente.)

**Evidencia aceptada, en orden de confianza** (un mensaje queda cubierto solo por la coincidencia exacta de su `message_id`):

| # | Evidencia | Qué cubre |
|---|---|---|
| 1 | El `message_id` del análisis que se está importando (campo del contrato, ya verificado contra Gmail por `_verify_origin`) es el del mensaje registrado | Ese mensaje, al importar (gancho en `work_import`) |
| 2 | `quotes.last_inbound_id` del caso es el del mensaje | Ese mensaje (casos con análisis ya importado antes del registro) |
| 3 | `analysis.work_message_id` guardado del caso es el del mensaje | Ese mensaje (casos importados antes de que existiera `last_inbound_id`, o cuando difiere) |
| — | Ninguna de las anteriores, aunque la fecha sea anterior (`received_at`/`first_seen_at`) | **Pendiente de comprobación**: `analizado_at` queda vacío y `cobertura_origen` en blanco |

**Qué debe cumplirse**
1. **Orden de llegada indiferente.** Si el análisis llega después del registro, un gancho dentro de la misma transacción de `_store_work_analysis` (después de `save`, antes del evento `analysis`) marca los mensajes cubiertos según la tabla. Si llegó antes, la recepción **no inserta ni actualiza** `quotes` (ni lo intenta) y aplica la misma tabla al materializar. Ambos órdenes convergen al mismo estado.
2. **Sin preliminar sobre un caso existente, sin sobrescribir `analysis_json`**, sin alerta falsa, sin perder historial: no se borra ningún registro, evento ni mensaje; `first_seen_at` no cambia. El evento `analysis_late` («El análisis de Work llegó N minutos después de la recepción…») se añade al evento `analysis` que ya existe.
3. **Qué se ve cuando hay pendientes de comprobación.** En un hilo con análisis válido, los mensajes sin evidencia se presentan como **novedad posterior al análisis** («Revisa si el análisis recoge este mensaje; es anterior al que Work analizó»), nunca como «Pendiente de análisis de Work» (esa etapa exige un hilo sin análisis válido). Es el error seguro: puede pedir revisar un mensaje ya recogido, nunca esconder uno que no lo está.
4. **Cobertura confirmada por una persona.** Una persona puede confirmar que el análisis recoge un mensaje (acción verificada con CSRF): se guarda quién y cuándo, `cobertura_origen = 'persona'` y un evento; no cambia el estado del caso. Cada cobertura guarda su origen (`message_id` del análisis, `last_inbound_id`, `work_message_id` o `persona`) para auditarla.
5. **Tabla de decisión.** Análisis de un mensaje anterior con otros ya registrados (E5): solo el analizado queda cubierto, el resto pendiente de comprobación y el aviso «Revisa si el análisis sigue vigente: llegaron N mensajes después». Payload con `_caso_minimo: True` (rechazo T-36) y caso con `sin_analisis_de_work`: **no cubren nada**. `analysis_complete: False` válido para el contrato, o estado `Descartada`: sí cubren su mensaje. Edición manual de la tabla sobre un preliminar: no cubre. Hilo sin filas de recepción (caso anterior a la función): el gancho no hace nada y no falla. Discrepancia entre el remitente registrado y el declarado por Work: se tolera y se avisa (D-106); no rechaza.
6. **Atomicidad.** Un fallo del gancho revierte toda la importación y el puente reintenta, como cualquier fallo de importación.
7. **Mejora futura, fuera de este requisito.** Que Work declare en el contrato los ids de los mensajes que leyó (cobertura demostrable de los mensajes anteriores) exigiría cambiar el contrato, el importador, la skill y el prompt (DT-26); no se asume.

**Casos de aceptación**: E4, E5, E9, E17 y E18.

#### U-71 · El seguimiento no omite los mensajes del hilo anteriores al mensaje analizado
**Estado: previsto** (incremento 5). Decisión: D-143.

**Hoy.** `gmail_followup.sync_thread_activity` descarta todo mensaje con fecha anterior a `first_seen_at`, que para un caso de Work es la recepción del mensaje **analizado**; los mensajes anteriores del hilo quedan fuera del historial y de `case_messages` (y se dan por vistos en el proceso).

**Qué debe cumplirse**
1. **Fecha de referencia del seguimiento** = el menor entre `first_seen_at` del caso y la recepción del entrante más antiguo registrado del hilo. Para un preliminar coincide con `first_seen_at`. Fuera de la ventana de la recepción se conserva el comportamiento actual (no se reescribe historia que Anka nunca vio).
2. Un mensaje anterior a la referencia sigue omitiéndose, pero ya **no** se da por visto. Los mensajes de otro hilo nunca se vinculan por asunto (solo se proponen, como hoy).
3. Todo mensaje del mismo hilo con fecha ≥ referencia deja su evento `sent`/`received` y su fila en `case_messages`; el aviso «El cliente respondió» solo aplica si es posterior al análisis. El evento de recepción es de otro tipo (`reception`) y no choca con `received`; la recepción no escribe `received`, `case_messages` ni avisos de respuesta: eso lo sigue haciendo el seguimiento, que guarda el texto.
4. **Esto no marca cobertura**: registrar un mensaje anterior en el historial no significa que Work lo haya analizado (U-70).
5. Se mantiene la guarda posterior a la T (`when > code_assigned_at`) y que los `sent` anteriores al análisis no mueven estado. La deuda de transacciones del seguimiento (U-64) no crece.

**Casos de aceptación**: E6 (y el caso heredado sin registro, que se comporta como hoy).

#### U-72 · La alerta de 30 minutos se mide desde la recepción real y se calcula al leer
**Estado: previsto** (incrementos 2 y 6). Decisión: D-139.

**Qué debe cumplirse**
1. **Función pura** `alerta(pendiente_desde_ms, ahora, umbral_min)` que devuelve `ninguna`, `pendiente` o `vencida`, con enteros y reloj inyectable: **29 min 59 s → `pendiente`; 30 min 00 s → `vencida`; 31 min → `vencida`**. No hay temporizadores ni estado guardado: se evalúa al leer, con `reloj.ahora()` en el borde.
2. **Referencia.** `pendiente_desde_ms` es la recepción real en Gmail (`internalDate`) del mensaje entrante pendiente **más antiguo** del hilo, no el momento en que Anka lo descubrió. Un hilo con `analizado_at`/`silenciado_at` en todos sus entrantes no alerta.
3. **Umbral configurable** (`recepcion_minutos_alerta`, 30 por defecto, entre 5 y 1440).
4. **A quién aplica.** Al hilo sin análisis válido (situación 1). Para los mensajes pendientes de comprobación en un hilo con análisis (situación 5) el umbral y el texto son los de la decisión técnica DT-6 (abierta; recomendación: mismo umbral, otro texto).
5. **Las cinco situaciones se distinguen** y no se mezclan: (1) pendiente de análisis, (2) fallo del sondeo o del acceso a Gmail, (3) resultado de Work rechazado (T-36, panel existente; un hilo en ese panel no se repite en la banda 1), (4) análisis importado (el historial muestra `analysis_late`), (5) nueva información posterior al análisis. Textos en español sin términos técnicos (U-41).
6. **Sistema apagado.** Tras una parada ≤ 168 h, los correos aparecen ya vencidos con el texto «Recibido hace 5 h. Anka estaba detenido y lo detectó hasta ahora» (`deteccion_tardia`); no se acusa a Work de haber tardado. Tras una parada mayor se anota el hueco y no se finge cobertura.
7. **Una sola novedad** `reception_overdue` por mensaje y umbral (`source_key = "<message_id>:<umbral>"`).

**Casos de aceptación**: E7 (el mismo resultado en lista, banda y `/api/salud`), E14 y E8 (parada y recuperación).

#### U-73 · «Pendiente de análisis de Work» es un indicador derivado en la lista principal, no un estado
**Estado: previsto** (incremento 6). Decisión: D-138. No cambia `ESTADOS`, `STATUS_LABELS`, `transiciones.json` ni las reglas operativas.

**Qué debe cumplirse**
1. **La etapa se llama exactamente «Pendiente de análisis de Work»** (nombre fijado por el dueño; sin sinónimos). Se muestra en la lista principal (y en el caso) mientras el hilo tenga mensajes entrantes registrados sin análisis válido que los cubra; desaparece sola al llegar el análisis. Va en el grupo «Por atender» con urgencia 1 y el verbo «Ver el caso», antes de «El cliente respondió» y después de los avisos de cambios y vínculos.
2. **Derivado de dos hechos que ya existen** (mensaje entrante registrado y sin análisis válido que lo cubra); se lee de una relación agregada y no del `analysis_json` (la lista perezosa U-49 no lo lee y no sube `VERSION_DEL_RESUMEN`). La fila muestra «hace N min» con el tono de aviso hasta el umbral y de error desde el umbral, y la etiqueta «Prueba» si aplica.
3. **No es un estado**: no hay transiciones nuevas, no cambia lo que cada TR hace con un análisis y el caso preliminar vive en `review`. «Pendiente» dice quién debe actuar (Work); los estados dicen dónde está el caso para la persona.
4. **Novedades derivadas correctas.** Las novedades «La solicitud ya fue analizada y agregada al dashboard» y «Respuesta sugerida lista para revisar» no se derivan para un hilo con mensajes entrantes sin análisis (hoy un caso sin análisis las produce con texto falso: defecto previo, que también afecta al caso mínimo de T-36). Al llegar el análisis se derivan solas con el texto correcto.
5. **Pantalla del caso.** Un bloque propio «Este correo aún no tiene análisis de Work» con quién escribió, asunto, hora de recepción, nombres de archivos «sin leer» y las acciones existentes («Completar la tabla yo», «Ver el correo»); distinto del bloque de T-36. Una persona puede silenciar la alerta de un mensaje que Work legítimamente no analiza («Ya lo atendí»: no cambia el estado; DT-4, abierta).
6. Las rutas nuevas se registran en `GET_ROUTES`/`POST_ROUTES` y en `openapi.py` (con `spec/07-api.md`); los goldens se regeneran y revisan línea a línea; la comparación visual con el prototipo se hace con capturas lado a lado.

**Casos de aceptación**: E1, E4, E7.

#### U-74 · Identidad estable del expediente (`case_uid`) y referencia opcional a un chat
**Estado: previsto** (`case_uid`: incrementos 3 y 4; la referencia al chat: **no se implementa**, posterior al incremento 7). Decisiones: D-135, D-136. DT-24 (fijada por el dueño): se **difiere** la tabla de conversaciones.

**Qué debe cumplirse**
1. **`case_uid`, identificador estable del expediente.** Hoy el caso solo tiene `thread_id`, que se reutilizaría si un caso eliminado (T-42) se recreara. Cada expediente recibe un `case_uid` (ULID) en la tabla `caso_identidad`, generado una sola vez en la misma transacción que crea la fila del caso. Es **inmutable** (un disparador prohíbe cambiar `case_uid` y `thread_id`), **no reutilizable** (nunca se borra su fila: es evidencia; al eliminar el caso se marca `eliminado_at`) y se conserva al eliminar (T-42): la tabla va a «se conservan». Un `thread_id` que se recrea después recibe un `case_uid` **nuevo** con `reemplaza_uid` apuntando al anterior. **Fusionar** dos casos no reasigna ni destruye ningún `case_uid`: el caso unido conserva el suyo con `unido_en_uid` = el del destino. El relleno de los casos existentes es idempotente (misma migración que la recepción; `origen` ∈ `recepcion`, `work`, `python`, `migracion`).
2. **Referencias de Gmail.** `thread_id` y `message_id` siguen siendo referencias (no la identidad del expediente) y no cambian.
3. **Cliente confirmado.** Hoy no existe un identificador de cliente (solo `contact_email` y datos libres); no se inventa uno. Se reserva una columna opcional `cliente_ref` en `caso_identidad` que solo se llenaría cuando una persona confirme el cliente, con evento de quién y cuándo (DT-23, abierta); no se crea hasta que exista ese flujo.
4. **Referencia opcional a un chat de ChatGPT (requisito de identidad y trazabilidad, no implementado).** Cuando exista evidencia de que hay un chat verificable, un `case_uid` podrá llevar una referencia **opcional** (URL o id) a un chat del Proyecto «ANKA | Inteligencia Comercial Work», escrita **solo por una persona** mediante una acción verificada, con quién y cuándo, y con un evento en la cadena de huellas. **Ninguna lógica de estado, alerta, envío o análisis puede depender de esa referencia** y el caso funciona igual sin ella.
5. **Límites explícitos.** **No se implementa la tabla de conversaciones (`caso_conversacion`) ni ninguna creación automática de chats.** **No está comprobado que Python o Work puedan crear chats dentro de un Proyecto**; por eso no se diseña ni se asume esa integración, y no se añade ninguna llamada a ChatGPT, Work ni OpenAI.

**Casos de aceptación**: E18 (identidad: único, inmutable, conservado al eliminar y recrear; relleno idempotente). La referencia al chat no tiene prueba porque no se implementa.

#### U-75 · El backlog de la recepción siempre se ve
**Estado: previsto** (incrementos 3 y 6). Decisión: D-137.

**Qué debe cumplirse**
1. **Contador.** La banda de recepción y `/api/salud` muestran «N correos registrados esperan su expediente (el más antiguo, de HH:MM)», calculado sobre las filas pendientes (`por_leer`, `registrado` y `revision_manual`); cuesta proporcional a los pendientes. Un pendiente sin materializar **nunca** cuenta como «todo al día».
2. **Alerta del backlog.** La alerta de 30 minutos (U-72) también se aplica a los `registrado` mediante ese contador y la novedad `reception_backlog`, medida desde la recepción real.
3. **Señales de error.** La comprobación de salud es `error` si hay `registrado` con más de 10 minutos de antigüedad, `por_leer` con 3 o más intentos, o cualquier `revision_manual`.
4. **Hueco y backlog** se muestran por separado.

**Casos de aceptación**: E14, E8.

#### U-76 · La salud de la recepción se informa aparte del puente
**Estado: previsto** (incremento 6). Decisión: D-137.

**Qué debe cumplirse**
1. `/api/salud` suma la comprobación `recepcion` junto a `puente_work` (`no_aplica` fuera del modo Work). Es `error` si el último éxito tiene más de `max(3 × intervalo, 180 s)`, si hay un hueco sin atender, si hay mensajes vencidos o si el backlog cumple U-75.3; `ok` con el detalle del último sondeo en caso contrario.
2. El estado `ok` del puente significa solo «el puente corrió»; el estado de la recepción se presenta aparte y el resumen **nunca dice «todo al día»** con pendientes vencidos, huecos de cobertura o `errores_seguidos > 0`.
3. `sync_status_text` suma una frase de cobertura de la recepción («Anka no pudo revisar Gmail desde las 14:20 (autorización vencida). Los correos nuevos pueden no aparecer.»). La verdad vive en la tabla de control, no en el latido (el formato del latido no cambia).
4. Un fallo de acceso se clasifica (`autorizacion`, `cuota`, `red`, `acceso`, `interno`) y no abre el navegador (D-27).

**Casos de aceptación**: E11, E8.

### Aceptación de la recepción determinista: los 18 escenarios
Los 18 escenarios (los 13 primeros de la primera versión más los escenarios 14 a 18 añadidos en la v2) son los casos de aceptación de U-66 a U-76. Se implementarán en `tests/test_recepcion_determinista.py` con un doble de Gmail que registra cada llamada y falla ante cualquier escritura, aislados del entorno (`tests/_isolate.py`), con reloj inyectado y base temporal. **Ninguna prueba se escribe en el incremento 1.**

| Id | Escenario | Qué verifica | Requisitos |
|---|---|---|---|
| E1 | Correo nuevo sin resultado de Work | Una fila en `quotes` en `review`, `first_seen_at` = `internalDate`, `contact_email` vacío, actividades vacías, nada inferido del asunto, evento `reception`, novedad `reception_pending` y **ninguna** novedad «ya fue analizada» | U-66, U-73 |
| E2 | Dos mensajes del mismo hilo (pasadas distintas y misma pasada) | Un solo caso, dos filas de registro, `first_seen_at` = el más antiguo, `updated_at` sube | U-66, U-67 |
| E3 | Repetición de sondeos | Mismos conteos de filas, sin eventos duplicados, cadena de huellas íntegra | U-66, U-67 |
| E4 | Análisis tardío | Un caso; el mensaje cuyo `message_id` coincide queda cubierto; eventos `analysis` y `analysis_late`; indicador apagado; novedades con el texto correcto; `first_seen_at` y el historial intactos | U-70, U-73 |
| E5 | Análisis antiguo frente a seguimiento reciente | Solo el analizado queda cubierto; el posterior queda pendiente (novedad «El cliente respondió»); aviso de vigencia; el estado no retrocede; `contact_email` lo fija Work | U-70 |
| E6 | Respuesta anterior a `first_seen_at` | Con la nueva referencia el seguimiento registra el mensaje anterior; un caso heredado sin registro se comporta como antes; otro hilo con asunto parecido no se vincula | U-71 |
| E7 | Alertas 29:59 / 30:00 / 31:00 | `pendiente`, `vencida`, `vencida`; una sola novedad `reception_overdue`; mismo resultado en lista, banda y `/api/salud` | U-72, U-73 |
| E8 | Reinicio, paginación, recuperación y lecturas fallidas | 1200 ids en 3 páginas; corte tras el primer lote; error 5xx y cuota; el cursor no avanza con listado incompleto; parada > ventana → hueco; **cada fila de la tabla de U-68** (404 aislado reintenta, 404 persistente a revisión manual, 404 con evidencia omitido, ráfaga de 404 sin marcar nada, 404 posterior a la materialización sin efecto; nada se borra) | U-67, U-68, U-72, U-75, U-76 |
| E9 | Concurrencia recepción contra Work | Dos conexiones, ambos órdenes: resultado único y determinista; sin «database is locked»; el `DO NOTHING` nunca pisa un análisis | U-66, U-70 |
| E10 | Propios, publicidad y pruebas | `SENT`, dirección propia, `List-Unsubscribe`, `no-reply` → registrados con motivo y sin caso; humano en Promociones **sí** crea caso; aviso automático con término comercial se rescata; prueba interna crea caso con etiqueta; ningún descarte por palabras | U-66 |
| E11 | Error de Gmail y salud | `getProfile`/`list` fallan, autorización vencida: la clase de error queda en el control, salud en `error` con texto claro, el latido no se contamina, no abre el navegador | U-68, U-76 |
| E12 | Cero escrituras en Gmail | Solo `getProfile`, `messages.list`, `messages.get` (y la lectura del hilo de U-68); el doble levanta ante cualquier escritura; recorrido AST sin `.send(`; SCOPES sin cambios | U-66 |
| E13 | Paridad y no regresión | `local` y `api` no ejecutan ni importan la recepción; suites de paridad, transiciones, arquitectura, tablas, transacciones y goldens de local/api sin diff | U-66 |
| E14 | Ráfagas por encima de los topes | 15, 55 y 130 hilos + 20 mensajes de un hilo + ruido + propio: tras N pasadas, un caso por hilo entrante, `entrantes_sin_expediente = 0`; el cursor ya avanzó y aun así el pendiente persiste y se ve; orden por recepción ascendente; con reinicio a mitad el mismo resultado; la lectura fallida queda `por_leer` visible; convergencia con tope de pasadas | U-67, U-72, U-75 |
| E15 | Simulación → activación | En simulación 0 filas en `quotes`, 0 eventos, 0 novedades; al activar, un caso por hilo sin duplicados y cadena íntegra; lo anterior a `recepcion_materializar_desde_horas` queda `omitido` con motivo; `activada_at` una vez | U-69 |
| E16 | Simulación, reinicio del supervisor, activación | Igual que E15 con cierre y reapertura de la base y un mensaje nuevo entre medias; el cursor no saltó nada de la cola; el nuevo se materializa después de los antiguos | U-69 |
| E17 | Work antes que la recepción, y su inversa | (a) análisis de m2 sin filas de recepción, Gmail con m1, m2, m3: 0 preliminares, **m2 cubierto por evidencia (`last_inbound_id`/`work_message_id`); m1 y m3 pendientes de comprobación aunque m1 sea anterior en fecha**; `analysis_json` intacto; sin `reception_pending`. (b) recepción primero y luego análisis: mismo estado final. Caso T-36: nada cubierto. `received_at` vacío o presente: nunca basta | U-70 |
| E18 | Concurrencia ampliada e identidad | 200 corridas en ambos órdenes con dos conexiones; dos materializaciones del mismo mensaje; `case_uid` único e inmutable, conservado al eliminar y recreado con `reemplaza_uid`, `unido_en_uid` al fusionar; migración idempotente con relleno | U-70, U-74 |

### U-81 · Trigger Gmail determinístico, acotado y de solo lectura
**Estado: implementado parcialmente** (detección y registro local; no materializa casos ni activa Work). Decisión: D-148.

1. En modo `chatgpt_work`, una opción apagada por defecto activa un sondeo configurable (60 s por defecto) de `ankacomercialia@gmail.com`, ejecutado por el supervisor. En otros modos no corre.
2. La primera ejecución guarda el `historyId` actual y no lista ni importa mensajes previos. Después usa Gmail History API y guarda mensajes por identidad `(thread_id, message_id)`; los reintentos y reinicios no duplican eventos.
3. El cursor solo avanza después de persistir todos los IDs de las páginas. Un lote de History mayor a 1000 candidatos se pausa en `revision_manual` sin avanzar el cursor. IDs pendientes sobreviven a reinicios; fallos se reintentan con espera creciente y, tras cinco fallos, pasan a `revision_manual`. Cada escritura del sondeo comprueba un token propietario y que el lease SQLite no haya vencido; se renueva antes y después de llamadas Gmail.
4. Si Gmail devuelve cursor vencido, una búsqueda `in:anywhere` con `includeSpamTrash` cubre desde el último sondeo correcto menos el solapamiento; el `after:` se expresa como fecha PST con un día adicional de margen. La recuperación automática se limita a 30 días y 1000 candidatos. Si el cursor vence antes de la primera sincronización delta correcta, no se intenta buscar: se requiere revisión para preservar la política de línea base. Fuera de esos límites, sin timestamps verificables o ante un fallo de paginación, se conserva el cursor y se detiene en `revision_manual`; no se supone continuidad por una búsqueda parcial o vacía.
5. Se registran tanto mensajes entrantes como propios; los propios quedan excluidos de interpretación por `SENT` y remitente. No hay análisis, alta en `quotes`, cambios de lectura/etiquetas, envío, borradores ni escritura en Drive/Sheets.
6. El paquete interno expone IDs Gmail, remitente, destinatarios, asunto, fechas, referencia al hilo, metadatos de adjuntos (sin descargarlos, con `storage_path` vacío), estado, evidencia y errores clasificados. El cuerpo/snippet no se persiste. No cambia `[ANKA_DASHBOARD_JSON_V1]`.
7. Logs contienen solo conteos y clases/tipos de error; nunca cabeceras, contenido, IDs, credenciales ni respuesta textual de Gmail. La opción permanece apagada hasta habilitarla explícitamente.
8. `initial_checkpoint_at` preserva el límite de la primera ejecución: en una búsqueda de recuperación, los mensajes con `internalDate` anterior se auditan como `anterior_al_checkpoint_inicial` y no quedan pendientes de interpretación. La recuperación solo puede comprobar mensajes que aún existen en el buzón; no reconstruye mensajes eliminados permanentemente. Un hueco que requiere revisión se incluye en la salida interna y pausa el sondeo sin mover el cursor.
9. **Destinatarios.** `recipients_json` guarda las direcciones de `To`, `Cc` y `Bcc` aunque alguno de esos encabezados falte o venga vacío. Cada encabezado se interpreta por separado: uno vacío o malformado se descarta sin perder los demás (Python 3.12 devuelve una lista vacía para todos si un valor de `getaddresses` está en blanco). Piloto 01: los 9 mensajes registrados antes de la corrección quedaron con `[]`; no se reescriben.
10. **Instancia única del receptor aislado.** `python -m anka.cli.gmail_reception` (con o sin `--once`) toma un candado del sistema operativo sobre `gmail_reception.lock` en la carpeta de datos. Funciona entre procesos independientes y lo libera el sistema si el proceso muere (un cierre inesperado no deja un bloqueo obsoleto). Un segundo receptor se niega a arrancar con el PID y la hora del que lo tiene. El archivo guarda como información el PID, un identificador de instancia y la hora de inicio: nunca decide por sí solo (no basta un PID) y una instancia solo borra su propia información, nunca la de otra. Al tomar un candado libre con información de una instancia anterior, informa que recuperó un bloqueo de una instancia que ya no existe. El hilo de recepción del supervisor no usa este candado (el lease de SQLite sigue serializando los sondeos).
11. **Contadores.** Cada pasada con estado `ok` informa, además de `discovered`, `processed`, `retried`, `manual_review`, `own_excluded` y `baseline_excluded` (sin cambios de significado): `history_seen` (mensajes que Gmail History reportó, nuevos o no) y `already_registered` (los que ya estaban en la cola). Siempre `history_seen = discovered + already_registered`. `discovered` sigue contando solo lo insertado por primera vez; un cero ya no se puede confundir con «no llegó nada».
12. **Auditoría del cursor.** `cursor_actualizado` se registra solo cuando el `historyId` cambia (con el valor anterior y el nuevo); un sondeo sin novedades solo actualiza `last_success_at` del control. No cambian los eventos de detección, error, `revision_manual` ni recuperación (`hueco_reconstruido_desde_buzon`, `recuperacion_requiere_revision`).

**Casos cubiertos**: primera ejecución sin historial, mensaje nuevo, seguimiento, repetición, reinicio, recuperación de conexión, error, concurrencia entre procesos, cola durable, mensaje propio, cursor vencido, hueco >24 h, período >30 días, overflow >1000, falla de paginación y fencing del propietario vencido (mocks de Gmail y SQLite temporal); destinatarios con `Cc`/`Bcc` vacíos, múltiples y malformados; dos procesos que compiten por el candado, recuperación tras un cierre brusco y liberación solo de lo propio; contadores `history_seen` y `already_registered` en una pasada repetida; el cursor sin cambio no genera evento.

### Plan por incrementos (previsto)
El plan original por incrementos se mantiene para U-66 a U-76. La solicitud explícita del 2026-10-16 autoriza U-81 (código, migración y pruebas con mocks); no autoriza ejecutar sondeos contra la cuenta real ni activar el ajuste de producción.

| # | Incremento | Alcance |
|---|---|---|
| 1 | Especificación y decisiones | Solo documentos oficiales: `spec/` y `CLAUDE.md` |
| 2 | Dominio puro | `recepcion_minima` (clasificar, expediente preliminar, alerta, referencia del seguimiento) y claves de configuración; pruebas unitarias |
| 3 | Recepción y persistencia (apagada por defecto) | Migración, `servicios/recepcion`, listado de mensajes, bloque en el puente, fases y cola, simulación, `case_uid` |
| 4 | Análisis tardío | Gancho en `work_import`, cobertura por evidencia, `analysis_late` |
| 5 | Seguimiento | Referencia de seguimiento en `gmail_followup` |
| 6 | Alertas y dashboard | Indicador, banda, salud, panel de rescate del ruido, «Ya lo atendí», revisión manual |
| 7 | Validación integral | `check.bat` completo y pruebas reales autorizadas |

**Pruebas reales que exigen autorización aparte** (ninguna se hace en el incremento 1): aplicar la migración a la base real, activar la simulación sobre la cuenta real, medir el borde de `after:` frente a `internalDate`, un correo de prueba real con y sin respuesta de Work, parada y recuperación del puente, autorización vencida durante el sondeo, cuota bajo carga, y la prueba manual en Work si algún día se cambia el prompt o la skill (hoy no se cambia).
