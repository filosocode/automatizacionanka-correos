# Arquitectura

Requisito: **T-12**. Las reglas de esta página se verifican con `tests/test_arquitectura.py`; si una falla, el cambio
metió una dependencia que el diseño no permite, y se corrige moviendo el código, no relajando la regla.

## Capas
```
cli ─┐
web ─┴─▶ servicios ─▶ infra ─▶ dominio
```
Una capa solo importa de las que tiene **debajo**. `cli` puede iniciar el servidor web;
`web` no importa comandos. El dominio no inicia procesos ni importa los clientes externos.

| Capa (`anka/…`) | Qué contiene | Qué NO puede hacer |
|---|---|---|
| `dominio/` | Reglas del negocio y funciones puras: estados, tabla de actividades, recepción (qué falta y qué preguntar), contrato con Work, lectura de un mensaje de Gmail (`correo`), la decisión sobre un hilo (`decision`: qué hacer, sin ejecutarlo), los objetos del caso (`Caso`, `Evento`, `Cambio`, `Vinculo`: dataclasses inmutables que también se leen como mapa) y la forma del análisis y de `config.json` (`Analisis`, `Actividad`, `Configuracion`…: `TypedDict` que mypy verifica y que se guardan como JSON sin cambios), extracción de texto de adjuntos. | Hablar con Gmail, Drive, la base de datos ni el disco. |
| `infra/` | Todo lo que toca el mundo exterior: configuración (`settings`), base SQLite (`store`, `recepcion_store`), clientes de Google (`google_services`, `mail`), modelos de lenguaje (`llm`), registro (`logging_setup`), copias de seguridad, modo de operación. | Conocer casos de uso ni pantallas. |
| `servicios/` | Los casos de uso: importar un borrador de Work (`work_import`), ver y decidir casos (`quotes`), editar la tabla (`ediciones`), enviar y borrar casos (`case_actions`), el ciclo de revisión (`pipeline`), control de cambios y código de la oferta, seguimiento de hilos y recordatorios, carpetas de Drive. | Conocer el servidor web ni los comandos. |
| `web/` | El servidor HTTP local (`server`: valida y enruta), las acciones de los formularios (`acciones`), las páginas (`paginas`: resumen y listas; `pantalla_caso`: un caso y su acta), el token CSRF y la comprobación de Host/Origen (`seguridad`), los archivos fijos y la compresión (`estaticos`), el marcador compartido de los avisos (`marcador`), los fragmentos (`views`), la documentación OpenAPI/Swagger y el JSON de lectura (`openapi`, `api`) y las plantillas. | Importar de `cli`. |
| `cli/` | Los puntos de entrada: comandos (`app`), lanzador (`supervisor`) y puente con Work (`work_sync`). | — |

## Previsto (aún no implementado)

La recepción determinista completa (U-66 a U-76, D-131 a D-143) sigue prevista. La base de detección y registro local U-81 ya existe: sondeo Gmail, cursor y cola idempotente, sin materialización en `quotes`, alertas, panel ni integración automática con Work. Esta separación mantiene el detector fuera de `pipeline` y permite validar el mecanismo sin cambiar `[ANKA_DASHBOARD_JSON_V1]`.

| Módulo previsto | Capa | Para qué serviría | Incremento |
|---|---|---|---|
| `recepcion_minima` | `dominio/` | Funciones puras: clasificar un mensaje (entrante, ruido R1, propio, sin remitente), armar el expediente preliminar, `alerta(...)` (29:59 / 30:00 / 31:00) y la fecha de referencia del seguimiento. Sin I/O ni reloj propio | 2 |
| `listar_mensajes` | `infra/mail.py` (función nueva en un módulo existente) | Listar ids de mensajes de Gmail con paginación acotada; `candidate_thread_ids` pasaría a usarla sin cambiar su comportamiento | 3 |
| (bloque en `main`) | `cli/work_sync.py` | Llamar a `sondear` después de importar borradores y antes del seguimiento, aislado en su propio `try/except` | 3 |

**Datos previstos.** La migración 21 crea `recepcion_control` (cursor, estado del sondeo y lease) y `recepcion_mensajes` (identidad `(account, thread_id, message_id)`, fase, metadatos, reintento y paquete). La 22 añade el límite temporal del primer checkpoint, confirmación de una delta sincronizada, contador del lote paginado y estado/ventana de recuperación. La cola se conserva al eliminar casos; el control no tiene `thread_id`. Sigue prevista `caso_identidad` (`case_uid`, `thread_id`, `reemplaza_uid`, `unido_en_uid`, `eliminado_at`; sin clave foránea a `quotes`). **No se crea la tabla de conversaciones** (D-136, DT-24).

**Dependencias entre capas.** Se respetan las cinco capas: el dominio no importa Gmail ni SQLite; `servicios/recepcion` usa `infra/mail`, `infra/store` y el cliente de Gmail ya tipado; solo `infra/store.py` y el repositorio escriben en `quotes` (U-52); la recepción no es importada por `pipeline` ni por `estado_de_revision` (los modos `local` y `api` no la ejecutan). Sin dependencias externas nuevas.

## Mapa de módulos

Una fila por módulo (lo comprueba `tests/test_arquitectura.py`: si agregas un módulo y no lo nombras aquí, falla).
La descripción es la primera frase de su docstring.

### `anka/dominio/`

| Módulo | Para qué sirve |
|---|---|
| `activities` | Edicion y exportacion de la tabla de actividades (sin acceso a Gmail ni Drive). |
| `cancelacion_work` | Cancelación de un mensaje de prueba: quién la autoriza, qué se rechaza y los errores que explican por qué (T-83). |
| `reloj` | El reloj de Anka: una sola hora «de ahora» con zona horaria y una forma de ordenar fechas por instante (U-62). |
| `caso_minimo` | El caso mínimo para un borrador de Work sin análisis válido, con solo lo verificable en Gmail (T-36). |
| `cambios_de_magnitud` | Detección de posibles cambios de cantidad entre mensajes del cliente de un hilo, con la procedencia de cada mensaje; solo observaciones, en modo sombra (T-69). |
| `contexto_de_cantidad` | Verificación contextual de cantidades y unidades de la tabla de Work: ubicación citada, unidad, asociación con la actividad; solo observaciones, en modo sombra (T-66). |
| `verificacion_de_evidencia` | Comprobación mecánica de que lo que Work puso en la tabla está en los textos del expediente (T-38). |
| `analisis` | La forma del análisis de una solicitud: lo que el modelo (o Work) devuelve y el dashboard guarda como JSON. |
| `attachments` | Límites y evidencia de adjuntos en memoria. |
| `lectores_texto` | Lectura en memoria de texto, HTML y RTF. |
| `xml_tolerante` | Lectura de XML de adjuntos que tolera prefijos sin declarar y rechaza entidades (U-55). |
| `lectores_shapefile` | Shapefile y tabla dBase leídos con la biblioteca estándar, aunque falten sus archivos hermanos (U-55). |
| `lectores_cad` | DXF en texto: versión, capas, conteo de entidades y textos de un plano CAD sin biblioteca externa (U-55). |
| `tramos` | Dividir un texto largo sin perder nada y condensar los hallazgos de cada tramo (T-25). |
| `preguntas` | Preguntas candidatas de un caso, decisión de una persona por pregunta, preguntas que lleva un correo (U-22) y rondas de preguntas enviadas con su respuesta (T-59). |
| `contexto_previo` | Contexto de un expediente para el trabajo Work de un SEGUIMIENTO: tabla vigente, rondas de preguntas y revisión pendiente, recortado y sin secretos (T-59). |
| `tabla_acumulada` | Integra la tabla de un seguimiento en la vigente sin perder actividades: aplica lo seguro y deja lo ambiguo como propuesta (T-59). |
| `novedades` | Qué novedades piden acción a una persona y por tanto cuentan en el contador (U-25). |
| `desempeno` | Desempeño del análisis medido con las correcciones del uso real (T-35). |
| `evaluacion` | Medir el análisis contra lo esperado (T-34): comparar un caso, resumir varios y reconocer de qué habla una pregunta. |
| `reintentos` | Espera creciente entre intentos de un hilo que falla y el caso que lo muestra (T-26). |
| `areas_calculadas` | Las áreas calculadas de los polígonos de un KML/KMZ: cómo se escriben en el texto del archivo y cómo se leen de vuelta (T-125). |
| `geometria` | Longitudes, áreas y perímetros geodésicos sobre WGS84 a partir de coordenadas, sin leer ningún archivo (T-125). |
| `lectores_geograficos` | Geometrías, misiones y tablas geográficas en memoria. |
| `caso` | `Caso`: la vista de una solicitud de cotización tal como la ven el dashboard y las acciones. |
| `configuracion` | La forma de `config.json` (vive fuera del repositorio, en la carpeta de datos): sus claves y sus tipos. |
| `correo` | El correo como dato: leer cabeceras, texto y adjuntos de un mensaje de Gmail y armar una respuesta. |
| `decision` | Qué hacer con un hilo ya leído: la decisión (estado, acción, avisos) sin ejecutar nada. |
| `errores` | Errores propios de Anka. |
| `reception` | Reglas de recepción: qué falta, qué preguntar y cómo redactar la respuesta. |
| `hoja` | El libro de cálculo de un caso (Excel / Google Sheets): la tabla vigente y, aparte, lo que no debe perderse. |
| `registros` | Los hechos que se guardan de un caso, como objetos con campos nombrados: `Evento`, `Cambio` y `Vinculo`. |
| `status` | Estados del ciclo de vida de una solicitud, definidos en un solo lugar. |
| `tolerancia_work` | Tolerar y avisar: lo que Work escribe distinto de lo esperado se corrige solo, con aviso (T-28, D-106). |
| `work_contract` | Contrato entre ChatGPT Work y el puente: valida el JSON del borrador contra `spec/contrato_work.schema.json`. |
| `clasificacion` | Clasificación comercial preliminar (T-52): `SOLICITUD_NUEVA`, `SEGUIMIENTO`, `NO_SOLICITUD` o `REVISION_MANUAL`, sobre R1 y T-51. Reglas puras. |
| `coordinacion_work` | Reglas puras de la coordinación Python-Work (T-55): validación de `work_coordination_*`, plan del ciclo (límites, corte, laboratorio) y retroceso por fallo. |
| `trabajo_work` | Trabajo Python → Work (T-49): `job_id` determinista, referencia verificable al mensaje y hoja `.xlsx` del trabajo. Reglas puras. |

### `anka/infra/`

| Módulo | Para qué sirve |
|---|---|
| `attachments` | Coordinación de la extracción: elige el lector por formato, recorta y explica la cobertura. |
| `lectores_zip` | ZIP: apertura acotada (`zip_acotado`, base de todo Office/OpenDocument), OCR incrustado y shapefiles. |
| `lectores_office` | Word, Excel (`.xlsx`, `.xls`), PowerPoint y OpenDocument. |
| `lectores_pdf` | PDF: texto nativo y OCR por página. |
| `cache_hallazgos` | Caché en disco, con tope, de los hallazgos extraídos por tramo. |
| `lectores_imagen` | OCR y conversión externa de documentos antiguos. |
| `presupuesto_ocr` | Presupuesto de segundos de OCR por archivo y por caso y política de no hacer OCR a lo escaneado (U-55). |

| Módulo | Para qué sirve |
|---|---|
| `backup` | Copias de seguridad de la base del dashboard: local y en Drive, sin credenciales, token ni secretos: la configuración viaja redactada como `config.redactado.json` (U-78). |
| `acciones_en_curso` | Marcas de lo que el dashboard está haciendo, para que el lanzador no lo corte a la mitad al reiniciar (U-59). |
| `archivos_privados` | Escritura completa y privada de archivos sensibles (token de Google, token CSRF): temporal + `os.replace` y permiso solo del usuario. |
| `reinicio` | Reinicio de los datos locales para volver a probar desde cero: vista previa, copia de seguridad y vaciado. |
| `clientes_google` | La forma mínima de los clientes de Gmail y de Drive que usa Anka (`Protocol`), para tiparlos sin `Any` (U-65). |
| `google_services` | Cliente de Gmail y Drive: credenciales, servicios y carpetas. |
| `recepcion_store` | Migración SQLite del cursor, la cola y la auditoría del trigger Gmail (U-81). |
| `trabajos_work_store` | Migración SQLite de `trabajos_work`, la cola de trabajos Python → Work (T-49, D-153). |
| `gmail_reception_auth` | Autorización y cliente Gmail de solo lectura del receptor, con token propio y alcance verificado (U-81). |
| `lapidas` | Diario de lápidas de cancelación, fuera de SQLite, para que una copia de seguridad restaurada no las pierda (T-83). |
| `instancia_unica` | Candado de instancia única entre procesos, con el candado del sistema operativo y sin depender de un PID; lo usa el receptor aislado (U-81, D-151). |
| `drive_exchange` | Intercambio de archivos propios en Drive con claves estables, paginación y bloqueo local. |
| `llm` | Análisis del correo con el motor elegido (Ollama u OpenAI). |
| `logging_setup` | Registro de la aplicación: consola con todo el detalle, archivo sin datos de clientes. |
| `mail` | Correo en Gmail: lo que habla con la API (descargar adjuntos, enviar, crear borradores, listar hilos). |
| `modes` | Modo de operación (local, api, chatgpt_work) y su automatización asociada. |
| `settings` | Ajustes y carpeta de datos local (fuera del repositorio). |
| `store` | Persistencia SQLite del estado de solicitudes y novedades del dashboard. |
| `instantaneas_de_manifiesto` | Copia local mínima del manifiesto que publica el preparador, para verificar la lectura que declara Work (T-37). |
| `rechazos_de_work` | Cuenta por día los análisis de Work que no se pudieron leer y recuerda cuáles se dejaron para después (U-41). |
| `work_heartbeat` | Latido del puente Gmail → dashboard (modo chatgpt_work): permite saber si está vivo. |

### `anka/servicios/`

| Módulo | Para qué sirve |
|---|---|
| `envios` | Reserva persistente y reconciliación de envíos del dashboard. |
| `lectura_por_tramos` | Análisis de un hilo con lectura completa aunque no quepa en el modelo (T-25). |
| `lectura_de_adjuntos` | Verifica la lectura de Work contra los adjuntos que Gmail tiene en el hilo y deja su texto local para T-38 (T-41). |
| `reintentos` | Registro de los hilos que fallaron y de cuándo reintentarlos (T-26). |

| Módulo | Para qué sirve |
|---|---|
| `autorizacion` | Estado de la autorización de Google y su renovación desde el dashboard. |
| `case_actions` | Acciones sobre un caso desde el dashboard: destinatario, borrador, envío, aprobación. |
| `change_control` | Código de la oferta (T / T&E) y control de cambios: quién pide qué, con qué código y a quién se avisa. |
| `commercial_index` | Construye un indice comercial agregado y local desde el archivo historico T&E. |
| `drive_contents` | Contenido de la carpeta de una solicitud en Drive. |
| `desempeno` | Desempeño del análisis con el uso real: agrupa los casos por versión del prompt y modelo y mide las correcciones (T-35). |
| `evaluacion` | Evaluar el análisis con solicitudes reales del histórico: muestrear, guardar fichas, validarlas y medir (T-34). |
| `drive_layout` | Estructura de carpetas de Drive: Año / Cliente / Solicitud. |
| `ediciones` | Edición de la tabla de actividades por una persona: motivos, diferencias entre versiones y guardado. |
| `estado_operativo` | Lo registrado sobre la recepción de mensajes y los trabajos Work para el panel de solo lectura `/operacion` (T-61). |
| `estado_de_revision` | Estado de la revisión de Gmail: cuándo fue la última, cuánto tardó, qué cambió y cómo se cuenta a la persona. |
| `followup_reminders` | Seguimiento cuando el cliente tarda en responder (R13). |
| `gmail_followup` | Seguimiento del hilo en Gmail (solo lectura): registra lo enviado y lo recibido, venga de donde venga. |
| `recepcion` | Detecta mensajes nuevos con Gmail History y los registra idempotentemente en una cola local (U-81). |
| `hoja_de_calculo` | La hoja de Google Sheets de cada caso: se crea en la carpeta del proyecto y se mantiene al día sola (spec T-17). |
| `internal_context` | Contexto comercial interno: solo lectura, nunca sale en correos. |
| `pipeline` | Ciclo de revisión del buzón: decide qué hacer con cada hilo y lo aplica. |
| `proyectos_repetidos` | Agrupar las solicitudes que parecen el mismo proyecto, solo para mostrarlas juntas (U-19). |
| `quotes` | Los casos (solicitudes) guardados en SQLite, vistos y modificados desde el dashboard: la fachada de los servicios. |
| `vinculos` | Vínculos entre conversaciones: correos que parecen de otro caso y proyectos repetidos que se unen o se separan. |
| `revision_tecnica` | Las decisiones de una persona sobre la revisión técnica de un caso y sobre lo que propone el modelo. |
| `cola_del_piloto` | La cola de revisión del piloto local: el paquete de texto de cada caso y los casos que esperan a una persona. |
| `estados` | Cambios de estado de un caso comprobados contra `spec/transiciones.json`: una no prevista se permite y se registra (U-46). |
| `repositorio` | `RepositorioDeCasos`: el único lugar que sabe cómo se guardan y se leen los casos en SQLite. |
| `salud` | Comprobación de las conexiones del sistema: base, configuración, puente de Work, Gmail, Drive y motor de análisis. |
| `work_import` | Importación del borrador de ChatGPT Work al dashboard (`dashboard_upsert`). |
| `cancelaciones` | Cancela un mensaje de prueba con una lápida persistente que nada reactiva (T-83, D-179). |
| `trabajos_work` | Crea el trabajo de un mensaje pendiente, publica su hoja en Drive sin duplicar (reclamo en SQLite, marca `appProperties`, adopta antes de crear, verifica después) y cierra el trabajo al importar (T-49, D-153). |
| `despacho` | Despacho a Work (T-53): una pasada que asegura `PENDIENTES`/`CERRADOS`, crea y publica el trabajo elegible del mensaje del piloto, reintenta y archiva los conciliados. |
| `coordinacion` | Coordinación automática (T-55): un ciclo acotado que archiva lo conciliado, importa los borradores de Work con T-47 y despacha los mensajes elegibles nuevos, con límites, retroceso por trabajo, auditoría y candado de instancia única. |
| `work_attachment_packet` | Prepara localmente manifiesto de evidencia y textos de adjuntos segmentados para que una persona los aporte a la tarea de Work. |
| `work_drive` | Expedientes de recepción versionados de Gmail a Drive, con originales y extracción para Work. |

### `anka/web/`

Los archivos fijos del diseño (fuente Inter y logo) viven en `anka/web/static/` y se sirven por `GET /static/{archivo}` (U-36, D-101).
El JavaScript de las páginas vive en `templates/app.js` (el de cada página, con el fondo animado de nodos de U-38/D-103) y `templates/tema.js` (el primer pintado del tema), servidos con huella en `/static` (U-49, D-114); `layout.html` solo los enlaza.

| Módulo | Para qué sirve |
|---|---|
| `acciones` | Las acciones de los formularios del dashboard (POST): una función por ruta. |
| `api` | Representación JSON de los casos para las rutas de lectura `/api/...` (ver spec/07-api.md). |
| `estaticos` | Archivos fijos de `/static` (fuente, logo, el CSS compartido y los dos guiones con huella), `ETag` y compresión gzip de las respuestas (U-48, U-49). |
| `formularios_pendientes` | Lo que la persona escribió en un formulario que no se pudo procesar, para devolvérselo y que no lo pierda (U-31). |
| `marcador` | El marcador de cambios que comparten todas las pestañas con avisos en vivo: una consulta por segundo (U-48). |
| `openapi` | La documentación OpenAPI 3 del dashboard y la página de Swagger UI (spec/07-api.md, requisito T-13). |
| `paginas` | Las páginas comunes del dashboard: la página completa, el resumen, las solicitudes y las descartadas. |
| `deshacer` | El panel «Decisiones que puedes deshacer» del historial del caso: reabrir «No es un cambio» y recuperar la propuesta de Work que se dejó de lado (U-57). |
| `pantalla_caso` | La pantalla de un caso y su acta: etapa, siguiente paso, tabla, conversación, respuesta y seguimiento. |
| `seguridad` | Seguridad del dashboard local: token CSRF persistente y comprobación de Host y Origen. |
| `server` | Servidor HTTP local del dashboard: enruta las páginas (GET) y las acciones de los formularios (POST). |
| `templating` | Motor de plantillas del dashboard. |
| `views` | Fragmentos HTML del dashboard sin acceso a datos ni a Gmail/Drive. |

### `anka/cli/`

| Módulo | Para qué sirve |
|---|---|
| `app` | Línea de comandos de Anka: `python app.py <comando>`. |
| `supervisor` | Lanzador único: arranca dashboard y puente, los reinicia si se caen o si cambia el código (y se reinicia a sí mismo), y hace la copia de seguridad diaria. |
| `work_sync` | Puente de solo lectura: importa al dashboard los borradores que Work deja en Gmail. |
| `work_drive` | Proceso de preparación de expedientes, separado del puente de borradores. |
| `recepcion_individual` | Registro de UN mensaje de prueba en la cola de recepción por su `message_id`, sin sondeo ni cursor (T-58). |
| `gmail_reception` | Sondeo aislado de recepción Gmail (solo lectura, `python -m anka.cli.gmail_reception`); no arranca el supervisor (U-81). Con `--registrar-mensaje` registra un único mensaje de prueba por su `message_id`, sin tocar el cursor (T-58). |
| `work_jobs` | Órdenes `crear`, `ver`, `exportar` y `publicar` (esta con `--confirmar-drive`) de los trabajos Python → Work; no corre solo (T-49). |
| `paquete_work` | Paquete de referencia del contrato para la tarea Work de la cola (T-54): construye, verifica y publica en Drive (solo con confirmación) los archivos que Work lee. |
| `work_coordinacion` | Prepara y ejecuta el ciclo de T-55 con Gmail y Drive reales (lo usan el lanzador, en un hilo, y la orden `work_jobs coordinar`); solo corre con la coordinación activada en `config.json`. También la verificación previa de solo lectura (`work_jobs verificar-piloto`: configuración, procesos, candados, cola y mensaje de laboratorio). |
| `evaluacion` | Evaluación del análisis con solicitudes reales del histórico: `python -m anka.cli.evaluacion <comando>`. |

## Glosario

El código mezcla inglés y español por su historia; no se renombra (cientos de cambios sin valor para quien usa el programa). Este es el vocabulario: dónde veas uno de los de la izquierda, es lo de la derecha. Lo nuevo se escribe en español y se migra al tocar un módulo.

| En el código | En la pantalla y la especificación | Qué es |
|---|---|---|
| `quote`, `quotes` (tabla), `Caso` | **caso** / solicitud | Una solicitud de cotización de un cliente y todo lo que cuelga de ella. La tabla se llama `quotes` por su origen; el objeto de dominio, `Caso`. |
| `thread_id` | **hilo** | La conversación de Gmail; es la clave de un caso. |
| `review`, `REVIEW` | **revisión técnica** | Que una persona de Anka decida qué falta antes de pedirlo al cliente (el estado `review`); no confundir con «revisar el correo» antes de enviarlo (R8). |
| `draft`, `work_reply` | **borrador** | El texto del correo al cliente, antes de enviarse; puede estar en Gmail (borrador) o solo en el dashboard. |
| `T`, `quote_code`, `TE` | **código T** / **T&E** | El código de la solicitud aceptada y el de la oferta aprobada (T-xx). |
| `bridge`, `work_sync` | **puente** | El proceso que lee los borradores de Work en Gmail y los importa al dashboard. |
| `supervisor` | **lanzador** | `Iniciar Anka.bat`: arranca dashboard y puente, los reinicia y hace las copias. |
| `mode`, `active_mode` | **modo** | `local` (Ollama), `api` o `chatgpt_work`. |
| `status`, `QuoteStatus`, `ESTADOS` | **estado** | Dónde está el caso en su flujo (`spec/transiciones.json`); la **etapa** es lo que se ve en la lista. |
| `event`, `case_events`, `hash` | **historial** y **huella** | Lo que pasó en el caso, encadenado con huellas; el **ancla** es la última huella guardada aparte (U-60). |
| `mail_operations`, `reservar` | **reserva de envío** | El registro durable de «voy a enviar esto» antes de hablar con Gmail (U-59). |
| `notification` | **novedad** | Un aviso del dashboard sobre un caso. |
| `link`, `case_links` | **vínculo** | Un correo de otro hilo que parece del mismo proyecto. |
| `change`, `case_changes` | **cambio** | Un mensaje posterior a la T que pide algo distinto al alcance. |
| `marker`, `change_marker` | **marcador de cambios** | Un texto barato que cambia cuando algo cambia, para refrescar pantallas. |
| `recepcion_*` (U-81) | **recepción** / **evento interno** | Cursor y mensajes nuevos detectados, pendientes o con error; no crea casos ni modifica Gmail. |
| `case_uid` *(previsto, U-74)* | **identificador del expediente** | Identidad estable de un caso, distinta de `thread_id`: no se reutiliza si el caso se elimina y se recrea. |

## Clases y funciones: cuándo cada una
Se usa una clase donde hay **estado y operaciones que viajan juntas**, y funciones donde no:
- `RepositorioDeCasos` (`servicios/repositorio.py`): sabe cómo se guardan y leen los casos; recibe cómo abrir la base.
  Las funciones de `quotes.py` (`get_quote`, `all_quotes`…) son una fachada sobre una instancia por defecto.
- `Caso` y los demás objetos del dominio llevan las reglas que dependen solo de sus campos (`cerrado`, `reabierto`,
  `admite_correo`, `informacion_validada`, `espera_al_cliente`), para que no se repitan en cada pantalla.
- `RevisorDeHilos` (`servicios/pipeline.py`): una pasada de revisión de Gmail que **ejecuta** las decisiones que toma
  `dominio/decision.py`; el estado de la última revisión y la vigilancia viven en `servicios/estado_de_revision.py`; guarda el contexto (configuración,
  Gmail, Drive, base, modo) que antes viajaba como argumento por cada función.
- Las reglas puras (recepción, estados, correo) siguen siendo funciones: no tienen estado y así se prueban sin montar nada.

## Dónde poner código nuevo
1. ¿Es una regla o un cálculo que no necesita nada externo? → `dominio/`.
2. ¿Habla con Gmail, Drive, la base, el disco o la red? → `infra/`.
3. ¿Orquesta varias cosas para cumplir un caso de uso? → `servicios/`.
4. ¿Es lo que ve o hace la persona en el navegador? → `web/`.
5. ¿Es algo que se ejecuta desde la terminal o el lanzador? → `cli/`.

## Otras reglas
- **Una conexión por petición web**: `request_scope()` (`anka/infra/store.py`) hace que todas las consultas de una
  petición compartan una conexión; `close()` descarta lo no confirmado. Fuera de una petición cada `connect()` abre la suya.
- **Sin ciclos** de importación entre módulos.
- **Solo la línea de comandos termina el programa**: un servicio lanza `ErrorDeAnka` (`anka/dominio/errores.py`) con
  un mensaje para la persona; `anka/cli` lo convierte en salida con código 1 y el dashboard en un aviso.
- **Un `except Exception` nunca calla**: solo se usa en un límite (un bucle, una petición, una tarea opcional) y
  siempre registra el error, lo relanza o lo usa para explicarlo; una prueba lo comprueba. El puente de Work, que
  corre sin consola, registra sus fallos en el archivo de registro además de imprimirlos.
- **Presupuestos de tamaño (U-52)**: ningún módulo de `anka/` pasa de 800 líneas; el que ya pasaba (`web/pantalla_caso.py`) tiene un tope congelado
  que solo puede bajar. Solo `servicios/repositorio.py` y `infra/store.py` escriben en la tabla `quotes`, y solo el repositorio escribe
  un estado en una consulta (`hilos_en_estado`…); los estados salen de la tabla única `ESTADOS` de `dominio/status.py`.
- **Un error para la persona es `ErrorDeValidacion`** (U-53): un `ErrorDeAnka` que también es `ValueError`; un `ValueError` suelto es de Python.
- **Lo privado no se importa**: un nombre con `_` es de su módulo; si otro lo necesita, se hace público o se mueve.
- **Cada módulo empieza con un docstring** que dice para qué sirve; cada capa lo tiene en su `__init__.py`.
- **Toda función, clase y método público del paquete tiene docstring** (una frase que dice qué hace, no cómo) y
  **ninguna función pasa de complejidad 15**: ruff lo comprueba en `check.bat` y en el CI (`pyproject.toml`).
- **La raíz está ordenada**: solo los puntos de entrada (`app.py`, `supervisor.py`, `work_dashboard_sync.py`, cada uno de
  pocas líneas) y `check_spec.py`; el resto vive en `anka/`, `tests/`, `docs/`, `spec/`, `tools/` y `skills/`.

## Puntos de entrada
`app.py`, `supervisor.py` y `work_dashboard_sync.py` en la raíz son envoltorios de una línea que llaman a
`anka/cli/…`. Se conservan con su nombre porque los lanzadores `.bat`, la tarea programada de Windows y el lanzador
único los invocan así.

## Pruebas
Viven en `tests/` (con `tests/golden/` para las instantáneas de pantalla) y se ejecutan con
`python -m unittest discover -s tests -t .` (lo hace `check.bat`).
