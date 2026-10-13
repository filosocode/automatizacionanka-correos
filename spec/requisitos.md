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
Resume cada solicitud en una tabla de actividades que una persona puede editar y exportar a Excel. La unidad se escribe de una sola forma (ha, km, m, km², m²) aunque el correo diga «hectáreas», «Ha» o «Km». Se conserva la
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
(R8). En el dashboard, el cuerpo de respuesta contiene solo el texto nuevo: se quita el historial citado que venga en
un borrador, mientras la conversación completa sigue visible en el caso. La respuesta conserva el hilo original de
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

### T-24 · Lectura completa de los adjuntos legibles
La lectura exhaustiva de los adjuntos es lo que sostiene una cotización correcta, y vale igual en los tres modos. Todo adjunto
de un formato legible (hasta 50 MiB, más que el máximo de Gmail) se lee completo: ningún recorte por longitud al texto que
recibe el modelo ni al cuerpo del correo. `lectura_parcial` se reserva para contenido legible que de verdad no se pudo
extraer (páginas o imágenes sin texto, celdas o entradas más allá del tope de recursos, gráficos o dibujos no extraídos); un
OCR ejecutado conserva el texto extraído, pero su aviso de relaciones de tabla o figura pendientes marca cobertura parcial.
La extracción textual no certifica interpretación visual. Si el análisis completo no cabe no se recorta: Ollama amplía hasta
`llm.num_ctx_max` (32768 por defecto) y, si aun así no cabe, el hilo se informa para revisión manual con el motivo.
La cantidad de una actividad no repite su unidad (`544.5` + `ha`, no `544.5 ha` + `ha`).

### T-34 · Expedientes de recepción Gmail → OCR → Drive para Work
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
nada se envía sin que una persona pulse «Enviar ahora» (R8).

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

### U-12 · Decidir y preguntar en un solo paso
En revisión técnica, la pantalla ofrece una sola sección, «¿Cómo seguimos?», con las dos salidas y lo que ocurre después de
cada una. Las preguntas para el cliente se editan ahí y no se repiten como tareas: «Qué falta resolver» solo cuenta cuántas
hay. Mientras la respuesta está en pausa no aparece la sección «Respuesta al cliente». Al decidir, la persona llega directo
al correo (si pregunta) o a la emisión de la T (si la información basta), y el aviso de arriba le dice que revise y envíe.
Ningún correo sale sin «Enviar ahora» (R8).

### U-13 · Solicitudes: lista ordenada, con verbo y filtrada por etapa
La lista de Solicitudes va, por defecto, de la más reciente a la más antigua (U-16). Cada fila lleva el botón con el verbo de su
paso («Decidir», «Emitir la T», «Revisar borrador»…) y, si no espera nada de una persona, «Abrir». Se filtra por etapa (Por
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

### T-28 · Un borrador de Work inválido no deja el puente «con avisos» para siempre
El puente distingue dos cosas. Un **fallo** es transitorio (red, Gmail, importación) y se reintenta en el siguiente ciclo; deja el
puente «con avisos» mientras dure. Un **borrador rechazado** es un problema del contenido (el JSON no cumple el contrato de
Work): no se arregla reintentando, así que se descarga y se rechaza **una sola vez** mientras su contenido no cambie (se vuelve
a revisar solo cuando Gmail da un mensaje nuevo para ese borrador), se anota una vez en el registro y no cuenta como fallo.
Con borradores rechazados y sin fallos el puente está «activo» con un estado propio («con un borrador por revisar»), no en
error: sigue importando el resto. El Resumen lista cada borrador rechazado con su hilo (si existe), el motivo (campos del
contrato, nunca contenido del cliente) y qué hacer. En `question_audit`, un `unresolved` booleano (Work lo escribe como sí/no)
se trata como vacío y se importa: la advertencia de la pregunta («debe precisar qué subdato sigue sin resolver») lo hace visible.

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
dejó para Anka, las que descartó y las que agregó), para cambiar cuáles y cuántas. **No preguntar: la información es suficiente** da el caso
por suficiente, como en la decisión técnica, y habilita emitir la T. Ambas quedan en el historial y ninguna envía nada. Un borrador ya
guardado en Gmail no se borra. Con el correo ya enviado no se puede volver atrás: la regla lo rechaza con su motivo.

### U-24 · Armar un correo con las preguntas que la persona escribe, en cualquier etapa
En el cuadro de respuesta de cualquier etapa en que se pueda escribir al cliente hay un campo «Preguntas para el cliente» (una por
línea) y un botón «Armar correo con estas preguntas». El correo se arma con la misma plantilla fija de siempre (saludo, las preguntas
numeradas tal como se escribieron, «Puede responder a este mismo correo» y la firma), en los tres modos y sin usar ningún modelo: no
reescribe ni inventa nada. Cada línea es un punto de la lista tal como se escribió; se ignoran las líneas vacías, la numeración
y las viñetas que la persona haya puesto y las preguntas repetidas, con un máximo de 20. El texto queda en el cuadro para editarlo;
nada se envía hasta «Enviar ahora» (R8), y el aviso previo cuenta las preguntas y la casilla de espera sigue funcionando.
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

### U-28 · Pantalla del caso más clara: un camino para las preguntas, confirmaciones propias y accesibilidad
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

### T-35 · Trazabilidad de lectura y origen de Work
El puente conserva el preflight y la auditoría de lectura, distingue aplicabilidad, verifica el origen en Gmail para V4.5 y evita regresiones por borradores antiguos.

### T-36 · Un borrador de Work sin análisis válido no deja el caso fuera del dashboard
Cuando Work deja en Gmail un borrador que no cumple el contrato (sin el bloque `ANKA_DASHBOARD_JSON_V1`, con un JSON roto o con campos
inválidos) y el hilo no tiene todavía un análisis importado, el puente crea el caso con lo mínimo que es verificable en Gmail:
remitente, asunto y fecha de recepción del último mensaje recibido. El caso queda en «Requiere revisión técnica interna», sin
actividades, sin preguntas y sin texto para el cliente; sus pendientes dicen que Work no entregó un análisis válido, el motivo
(campos del contrato, nunca contenido del cliente) y qué hacer (pedir a Work que reanalice el hilo). No inventa ningún dato.
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
Cuando el preparador (T-34) publica un expediente, guarda una instantánea local mínima de su manifiesto (hilo, revisión, `message_ids`
e identificador y nombre de cada segmento del correo y de los adjuntos). El puente la usa para dejar de confiar en lo que Work copia a
mano: `DRIVE_PREFLIGHT.manifest_revision`, `message_ids`, los conteos de segmentos y las listas `*_segment_ids_expected` y
`*_segment_ids_pending` se calculan desde el manifiesto; `*_segment_ids_read` es lo que Work declaró haber leído, reducido a lo que
existe en el manifiesto (acepta el identificador o el nombre del segmento y lo normaliza al identificador). Cada diferencia se
anota como aviso del puente. Si Work leyó una revisión que no es la vigente, el mensaje analizado no está en el manifiesto o quedan
segmentos sin lectura verificada, `analysis_complete` pasa a falso y el caso a revisión técnica interna. Sin instantánea para el
hilo (preparador apagado o expediente anterior a este cambio) no se modifica nada: el comportamiento es el de T-35.

Cuando quedan segmentos de adjuntos sin lectura verificada, el puente dice **cuáles**: la instantánea guarda también el nombre del
archivo de cada segmento, el aviso del caso lo nombra («1 de 4 segmentos de adjuntos sin lectura verificada: plano.kml») y la
auditoría conserva la lista en `attachment_files_pending` (la calcula el puente, nunca Work). El aviso «Lectura pendiente» de la
pantalla del caso nombra esos archivos y dice qué hacer: volver a ejecutar la tarea de Work sobre el hilo, porque un bloqueo no es un
análisis final y un análisis completo posterior de la misma revisión reemplaza al incompleto. Los resultados siguen ocultos hasta
que la lectura se verifique; esto solo evita que la persona deba adivinar qué falló. Una instantánea anterior a este cambio no
trae los nombres: el aviso funciona igual, sin ellos, hasta que el preparador la vuelva a guardar.

### T-38 · Lo que Work pone en la tabla se comprueba contra los textos del expediente
Cuando el preparador (T-34) publica un expediente guarda también, solo en este equipo, el texto extraído del correo y de cada adjunto
(con un tope de tamaño; si lo supera, la comprobación queda como «no comprobable»). El puente comprueba, sin cambiar ni bloquear nada:
(a) que cada número de la cantidad de una actividad aparece en los textos del expediente (en el archivo citado si se puede
identificar; si solo está en otro archivo, lo dice); (b) que los archivos citados en las fuentes existen en el expediente y que la
página citada de un PDF no supera las páginas leídas; (c) que cada extracto literal de un adjunto aparece en el texto de ese adjunto.
Cada diferencia es un aviso del caso con su ubicación («actividad 3, cantidad 126»). Si algún adjunto no tiene texto (ilegible o
no extraído), un dato que no se encuentra se marca «no comprobable» y no como diferencia. El resultado queda en el caso
(`evidence_check`: estado, cuántas comprobaciones, diferencias, no comprobables). Sin textos del hilo no se comprueba nada.

### T-39 · El prompt de Work es corto y su ejemplo cumple el contrato
El prompt de la tarea de Work cabe en unos 12 000 caracteres (la V4.7 tiene 19 500; la V6.1, 11 800), no depende de la skill para el formato de salida e incluye un
JSON de ejemplo completo. Una prueba extrae ese ejemplo del archivo del prompt y comprueba que cumple el esquema
(`spec/contrato_work.schema.json`), de modo que el ejemplo no se desactualiza sin que falle una prueba. Work solo declara qué
segmentos leyó; la revisión, los conteos y los pendientes los calcula el puente (T-37).

### T-40 · El preparador gasta poca cuota de Gmail y no se queda sin preparar lo más nuevo
El preparador (T-34) no vuelve a bajar un hilo cuyo estado en Gmail (`historyId` de `threads.list`) no cambió desde la última vez que
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
   de siempre). No envía nada; el correo se revisa y se envía con «Enviar ahora» (R8). El botón desaparece cuando ya hay preguntas en el campo
   o ya decidiste.

### T-41 · Work lee los adjuntos directamente desde Gmail, sin expediente en Drive
**Estado: implementado** (pruebas en `tests/test_lectura_de_adjuntos.py` y `tests/test_prompt_work.py`); **prueba manual en Work parcial**: la
primera con el prompt V6.1 (2026-10-11, D-69) leyó el hilo y dejó el borrador completo; faltan el KMZ, el ZIP y el adjunto grande (ver «Prueba
manual»). Rige para los borradores del prompt V6 en adelante (`skill_version` `PRECISO_V6…`); los de V5 y anteriores siguen con T-34, T-37 y
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
   El origen del mensaje se sigue verificando contra Gmail (T-35) también para los borradores V6.
7. **Qué se retira.** La publicación de expedientes en Drive (T-34, T-40), el manifiesto con segmentos, las instantáneas de manifiesto,
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
1. **La versión declarada manda.** `PRECISO_V6` o posterior se verifica contra Gmail (T-41); `PRECISO_V5` o anterior sigue con el manifiesto (T-34,
   T-37). Una versión declarada de V5 no se reinterpreta aunque sus adjuntos traigan `size_bytes`.
2. **Sin versión declarada** (ausente, vacía o «no indicado»), la huella de V6 decide: si el borrador trae adjuntos y **todos** llevan su tamaño exacto
   en bytes (`size_bytes` entero, algo que V5 y anteriores no emiten), se verifica contra Gmail. Sin adjuntos o con alguno sin `size_bytes`, sigue el camino
   de siempre.
3. **El prompt lo exige.** El prompt de Work dice que `skill_version` es obligatorio y se copia tal cual (`PRECISO_V6.1`).
