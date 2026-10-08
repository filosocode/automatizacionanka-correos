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
| `infra/` | Todo lo que toca el mundo exterior: configuración (`settings`), base SQLite (`store`), clientes de Google (`google_services`, `mail`), modelos de lenguaje (`llm`), registro (`logging_setup`), copias de seguridad, modo de operación. | Conocer casos de uso ni pantallas. |
| `servicios/` | Los casos de uso: importar un borrador de Work (`work_import`), ver y decidir casos (`quotes`), editar la tabla (`ediciones`), enviar y borrar casos (`case_actions`), el ciclo de revisión (`pipeline`), control de cambios y código de la oferta, seguimiento de hilos y recordatorios, carpetas de Drive. | Conocer el servidor web ni los comandos. |
| `web/` | El servidor HTTP local (`server`: valida y enruta), las acciones de los formularios (`acciones`), las páginas (`paginas`: resumen y listas; `pantalla_caso`: un caso y su acta), el token CSRF y la comprobación de Host/Origen (`seguridad`), los fragmentos (`views`), la documentación OpenAPI/Swagger y el JSON de lectura (`openapi`, `api`) y las plantillas. | Importar de `cli`. |
| `cli/` | Los puntos de entrada: comandos (`app`), lanzador (`supervisor`) y puente con Work (`work_sync`). | — |

## Mapa de módulos

Una fila por módulo (lo comprueba `tests/test_arquitectura.py`: si agregas un módulo y no lo nombras aquí, falla).
La descripción es la primera frase de su docstring.

### `anka/dominio/`

| Módulo | Para qué sirve |
|---|---|
| `activities` | Edicion y exportacion de la tabla de actividades (sin acceso a Gmail ni Drive). |
| `analisis` | La forma del análisis de una solicitud: lo que el modelo (o Work) devuelve y el dashboard guarda como JSON. |
| `attachments` | Límites y evidencia de adjuntos en memoria. |
| `lectores_texto` | Lectura en memoria de texto, HTML y RTF. |
| `tramos` | Dividir un texto largo sin perder nada y condensar los hallazgos de cada tramo (T-25). |
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
| `work_contract` | Contrato entre ChatGPT Work y el puente: valida el JSON del borrador contra `spec/contrato_work.schema.json`. |

### `anka/infra/`

| Módulo | Para qué sirve |
|---|---|
| `attachments` | Coordinación de la extracción: elige el lector por formato, recorta y explica la cobertura. |
| `lectores_zip` | ZIP: apertura acotada (`zip_acotado`, base de todo Office/OpenDocument), OCR incrustado y shapefiles. |
| `lectores_office` | Word, Excel (`.xlsx`, `.xls`), PowerPoint y OpenDocument. |
| `lectores_pdf` | PDF: texto nativo y OCR por página. |
| `cache_hallazgos` | Caché en disco, con tope, de los hallazgos extraídos por tramo. |
| `lectores_imagen` | OCR y conversión externa de documentos antiguos. |

| Módulo | Para qué sirve |
|---|---|
| `backup` | Copias de seguridad de la base del dashboard: local y en Drive (sin credenciales ni token). |
| `google_services` | Cliente de Gmail y Drive: credenciales, servicios y carpetas. |
| `llm` | Análisis del correo con el motor elegido (Ollama u OpenAI). |
| `logging_setup` | Registro de la aplicación: consola con todo el detalle, archivo sin datos de clientes. |
| `mail` | Correo en Gmail: lo que habla con la API (descargar adjuntos, enviar, crear borradores, listar hilos). |
| `modes` | Modo de operación (local, api, chatgpt_work) y su automatización asociada. |
| `settings` | Ajustes y carpeta de datos local (fuera del repositorio). |
| `store` | Persistencia SQLite del estado de solicitudes y novedades del dashboard. |
| `work_heartbeat` | Latido del puente Gmail → dashboard (modo chatgpt_work): permite saber si está vivo. |

### `anka/servicios/`

| Módulo | Para qué sirve |
|---|---|
| `envios` | Reserva persistente y reconciliación de envíos del dashboard. |
| `lectura_por_tramos` | Análisis de un hilo con lectura completa aunque no quepa en el modelo (T-25). |

| Módulo | Para qué sirve |
|---|---|
| `autorizacion` | Estado de la autorización de Google y su renovación desde el dashboard. |
| `case_actions` | Acciones sobre un caso desde el dashboard: destinatario, borrador, envío, aprobación. |
| `change_control` | Código de la oferta (T / T&E) y control de cambios: quién pide qué, con qué código y a quién se avisa. |
| `commercial_index` | Construye un indice comercial agregado y local desde el archivo historico T&E. |
| `drive_contents` | Contenido de la carpeta de una solicitud en Drive. |
| `drive_layout` | Estructura de carpetas de Drive: Año / Cliente / Solicitud. |
| `ediciones` | Edición de la tabla de actividades por una persona: motivos, diferencias entre versiones y guardado. |
| `estado_de_revision` | Estado de la revisión de Gmail: cuándo fue la última, cuánto tardó, qué cambió y cómo se cuenta a la persona. |
| `followup_reminders` | Seguimiento cuando el cliente tarda en responder (R13). |
| `gmail_followup` | Seguimiento del hilo en Gmail (solo lectura): registra lo enviado y lo recibido, venga de donde venga. |
| `hoja_de_calculo` | La hoja de Google Sheets de cada caso: se crea en la carpeta del proyecto y se mantiene al día sola (spec T-17). |
| `internal_context` | Contexto comercial interno: solo lectura, nunca sale en correos. |
| `pipeline` | Ciclo de revisión del buzón: decide qué hacer con cada hilo y lo aplica. |
| `proyectos_repetidos` | Agrupar las solicitudes que parecen el mismo proyecto, solo para mostrarlas juntas (U-19). |
| `quotes` | Los casos (solicitudes) guardados en SQLite, vistos y modificados desde el dashboard. |
| `repositorio` | `RepositorioDeCasos`: el único lugar que sabe cómo se guardan y se leen los casos en SQLite. |
| `salud` | Comprobación de las conexiones del sistema: base, configuración, puente de Work, Gmail, Drive y motor de análisis. |
| `work_import` | Importación del borrador de ChatGPT Work al dashboard (`dashboard_upsert`). |
| `work_attachment_packet` | Prepara localmente manifiesto de evidencia y textos de adjuntos segmentados para que una persona los aporte a la tarea de Work. |

### `anka/web/`

| Módulo | Para qué sirve |
|---|---|
| `acciones` | Las acciones de los formularios del dashboard (POST): una función por ruta. |
| `api` | Representación JSON de los casos para las rutas de lectura `/api/...` (ver spec/07-api.md). |
| `openapi` | La documentación OpenAPI 3 del dashboard y la página de Swagger UI (spec/07-api.md, requisito T-13). |
| `paginas` | Las páginas comunes del dashboard: la página completa, el resumen, las solicitudes y las descartadas. |
| `pantalla_caso` | La pantalla de un caso y su acta: etapa, siguiente paso, tabla, conversación, respuesta y seguimiento. |
| `seguridad` | Seguridad del dashboard local: token CSRF persistente y comprobación de Host y Origen. |
| `server` | Servidor HTTP local del dashboard: enruta las páginas (GET) y las acciones de los formularios (POST). |
| `templating` | Motor de plantillas del dashboard. |
| `views` | Fragmentos HTML del dashboard sin acceso a datos ni a Gmail/Drive. |

### `anka/cli/`

| Módulo | Para qué sirve |
|---|---|
| `app` | Línea de comandos de Anka: `python app.py <comando>`. |
| `supervisor` | Lanzador único: arranca dashboard y puente, los reinicia si se caen o si cambia el código, y hace la copia de seguridad diaria. |
| `work_sync` | Puente de solo lectura: importa al dashboard los borradores que Work deja en Gmail. |

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
