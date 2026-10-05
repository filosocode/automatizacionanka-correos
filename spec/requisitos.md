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
Resume cada solicitud en una tabla de actividades que una persona puede editar y exportar a Excel. Se conserva la
versión del modelo y cada edición queda como versión nueva, con sus diferencias y un motivo opcional.
Los hechos extraídos de adjuntos conservan el nombre del archivo y un localizador verificable (página, párrafo,
tabla/fila, hoja/celda o elemento KML/KMZ); los archivos o secciones no leídos se identifican como parciales o
pendientes de revisión. Se extraen formatos de texto, PDF, DOC/DOCX, XLS/XLSX/XLSM, PPT/PPTX, ODT/ODS/ODP, RTF, HTML, imágenes, KMZ/KML, GeoJSON, GPX, planes de vuelo QGroundControl (.plan/.waypoints), DJI WPML en KMZ y documentos compatibles dentro de ZIP. El OCR local cubre imágenes sueltas, páginas PDF escaneadas y recursos de imagen incrustados; baja confianza, gráficos estructurados omitidos, límites de cobertura y archivos no convertibles se marcan para revisión. Shapefiles se leen cuando se entregan con sus archivos laterales dentro de ZIP. DWG y DXF permanecen en revisión manual.
El modo Work sigue el mismo contrato de evidencia y registra, para cada pregunta, las fuentes ya verificadas;
una pregunta que ya esté contestada en cualquier archivo se elimina.

### F1-04 · Detectar datos faltantes
Determina qué datos faltan para la etapa de cotización, distinguiendo lo crítico de lo recomendable, y los convierte
en preguntas para el cliente. Con dependencias o contradicciones pendientes el caso pasa a revisión técnica.

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
`config.json` se valida y los errores dicen qué corregir.

### T-10 · Persistencia y migraciones
La base SQLite se migra por pasos con copia previa y rechaza versiones más nuevas que el programa.

### T-11 · Paridad de los tres modos
Ollama, API y ChatGPT Work siguen las mismas reglas, que viven en un solo documento; las pruebas fallan si se
separan.

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

### U-06 · Estados veraces
El estado que se ve en la lista, en el detalle y en los avisos no se contradice y refleja lo ocurrido.

### U-07 · Respuesta libre en casos avanzados
Un caso ya avanzado ofrece la caja de respuesta en blanco, sin textos sugeridos desactualizados.

### U-08 · Correos con el código de la oferta
Un caso con código T ofrece siempre, en la caja de respuesta, preparar el correo de «proyecto recibido» (con el
código) y, cuando la oferta pasa a T&E, el de «oferta aprobada». Al aprobar la oferta se prepara ese correo, pero
nada se envía sin que una persona pulse «Enviar ahora» (R8).
