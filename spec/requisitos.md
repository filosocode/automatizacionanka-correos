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

### F1-04 · Detectar datos faltantes
Determina qué datos faltan para la etapa de cotización, distinguiendo lo crítico de lo recomendable, y los convierte
en preguntas para el cliente. Con dependencias o contradicciones pendientes el caso pasa a revisión técnica.

### F1-05 · Pedir los datos al cliente
Prepara el correo de respuesta con las preguntas; una persona lo revisa y lo envía desde el dashboard o desde Gmail
(R8). Un clic produce un correo: el mismo texto no se reenvía y el caso muestra que ya se envió.

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
