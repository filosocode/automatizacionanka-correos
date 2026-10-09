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
OCR ejecutado cuenta como lectura completa, y su aviso sobre relaciones de tabla o figura queda en el texto sin degradar la
cobertura. Si el análisis completo no cabe en la ventana del modelo no se recorta en silencio: Ollama amplía su ventana hasta
`llm.num_ctx_max` (32768 por defecto) y, si aun así no cabe, el hilo se informa para revisión manual con el motivo.
La cantidad de una actividad no repite su unidad (`544.5` + `ha`, no `544.5 ha` + `ha`).

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
cambiarlo no lo reimporta. Si no se conoce la hora, el hecho queda como antes.

### T-31 · Las entradas inválidas se responden con claridad
Una petición que la aplicación rechaza con su motivo (un caso que ya no existe, un dato no válido) responde 400 con ese mensaje y se
anota como aviso, no como error del servidor (500); un fallo propio del código sigue siendo 500 y se anota con su traza. Un número
mal escrito en la dirección (`change_id=abc`) se toma como «ninguno» y no muestra el texto interno de Python. El nombre de una
carpeta de Drive tiene un tope de 150 caracteres y no se corta una palabra a la mitad. La importación de Work sigue aceptando un texto
donde se espera una lista (Work lo hace a veces); el contrato completo se exige en el puente, que es la entrada de producción.

### T-32 · Saber con qué modelo y qué versión del prompt analizó Work
El contrato admite dos campos opcionales: `model` (el modelo que declara la interfaz de ChatGPT Work para la tarea) y `skill_version`
(la versión del prompt). La importación los guarda en el caso y los anota en el hecho «Work analizó el caso» del historial. Son
**solo internos**: ningún correo al cliente los incluye. Lo escribe el propio modelo, así que sirve para detectar cambios entre
análisis, no como garantía. Se limpian (sin saltos de línea ni caracteres de control, con tope de largo) y se muestran escapados.
Un borrador sin ellos sigue siendo válido y el hecho queda como antes.

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

### U-24 · Armar un correo con las preguntas que la persona escribe, en cualquier etapa
En el cuadro de respuesta de cualquier etapa en que se pueda escribir al cliente hay un campo «Preguntas para el cliente» (una por
línea) y un botón «Armar correo con estas preguntas». El correo se arma con la misma plantilla fija de siempre (saludo, las preguntas
numeradas tal como se escribieron, «Puede responder a este mismo correo» y la firma), en los tres modos y sin usar ningún modelo: no
reescribe ni inventa nada. Una línea sin signos de interrogación se escribe entre ellos; se ignoran las líneas vacías, la numeración
y las viñetas que la persona haya puesto y las preguntas repetidas, con un máximo de 20. El texto queda en el cuadro para editarlo;
nada se envía hasta «Enviar ahora» (R8), y el aviso previo cuenta las preguntas y la casilla de espera sigue funcionando.

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
