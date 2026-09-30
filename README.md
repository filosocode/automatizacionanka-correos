# Automatización de solicitudes de cotización

Primera etapa: leer Gmail, identificar solicitudes, preparar una tabla de actividades, pedir datos faltantes en el mismo hilo, incorporar la respuesta y crear una carpeta de proyecto en Drive bajo una carpeta existente.

## Datos locales y secretos

Las credenciales, el token, `config.json`, la base SQLite, `review_queue`, `logs` e `internal_context` viven **fuera del repositorio**, en `%LOCALAPPDATA%\AnkaCorreos` (o en la ruta de la variable de entorno `ANKA_DATA_DIR`). Nunca se versionan. Al configurar una máquina nueva, crea esa carpeta y coloca allí `credentials.json` y `config.json`.

## Preparación

1. Instala Python 3.10+ y las dependencias: `python -m pip install -r requirements.txt`.
2. Habilita Gmail API y Google Drive API en un proyecto de Google Cloud. Crea un cliente OAuth de tipo aplicación de escritorio y guarda su archivo como `credentials.json` en la carpeta de datos. La primera ejecución de `auth` abre el consentimiento de Google y guarda `token.json` localmente. Ninguno de esos archivos debe versionarse.
3. Copia `config.example.json` a `config.json` dentro de la carpeta de datos. En `required_fields`, agrega los nombres de los datos indispensables para cotizar; por ejemplo, `["ubicación del proyecto", "cantidades"]` **solo si aplican a tu negocio**. Registra en `own_addresses` la cuenta principal y todos los aliases desde los que Anka responde; esos remitentes se excluyen antes del análisis. `forwarded_reply_policy` queda en `internal_reviewer`: en solicitudes reenviadas identifica al cliente original, pero dirige el borrador al comercial de Anka que hizo el reenvío. En `drive_parent_id`, coloca el ID de la carpeta de arranque existente.
4. Elige un solo motor en `config.json`, dentro de `llm.provider`:
   - `"ollama"`: usa el modelo local indicado en `llm.model` y `llm.base_url`. No requiere clave de OpenAI ni genera cobro por tokens.
   - `"openai"`: usa la API de OpenAI y requiere `OPENAI_API_KEY` en el entorno. Configura el modelo en `llm.model`; el predeterminado es `gpt-4.1-mini`. La API se factura aparte de ChatGPT Plus.

El programa no cambia automáticamente de proveedor si falla el seleccionado. Así no activa la API ni incurre en consumo sin que OpenAI esté elegido explícitamente.

## Uso

`python app.py auth`: autoriza la cuenta de Google.

`python app.py drive-tree`: muestra las carpetas del nivel raíz y un nivel inferior, con sus IDs, para elegir la carpeta de arranque.

`python app.py analyze-text --text "..." --required-field "ubicación del proyecto"`: prueba el proveedor seleccionado con un correo de ejemplo, imprime el JSON y las preguntas faltantes, y no se conecta a Gmail ni Drive. También acepta `--file ruta\correo.txt` y varios `--required-field`. Sirve para comparar `ollama` y `openai` cambiando `llm.provider` en `config.json`.

`python app.py run`: hace una simulación; muestra clasificación, tabla y borrador de pregunta, sin enviar ni crear carpetas.

`python app.py run --apply --review-only`: procesa el buzón con estado persistente y guarda cada respuesta propuesta en `review_queue`, sin enviar correos ni crear carpetas. Este es el modo recomendado para un piloto semiasistido.

`python app.py run --apply --review-only --watch-seconds 300`: revisa Gmail cada cinco minutos y aplica el mismo flujo semiasistido. Usa `Ctrl+C` para detenerlo.

`python app.py run --apply`: procesa el buzón y realiza los envíos y la creación de carpetas. Ejecútalo periódicamente con el programador de tareas. `gmail_query` controla el periodo y los remitentes; la configuración actual revisa 30 días para Diego y el remitente de pruebas. El estado se guarda en `quotes.sqlite3` para no repetir correos.

La tabla se guarda en JSON en SQLite e incluye actividad, servicio o técnica candidata, ubicación, magnitud, unidad, entregable, plazo o restricción, fuente y estado de confirmación. El analizador recibe los mensajes externos del hilo y el texto extraído de TXT, CSV, JSON, XML, KML, KMZ, PDF con texto, DOCX y XLSX. Las imágenes incrustadas de firmas se registran como ignoradas y no bloquean el caso. DWG/DXF, formatos no soportados, archivos dañados y PDF escaneados quedan en revisión; la ficha indica el archivo, el motivo y los datos que no se pudieron confirmar.

Los reenvíos conservan por separado al remitente interno, el cliente original identificado dentro del mensaje y el destinatario seleccionado. Las referencias a alcances anteriores y las contradicciones entre correo y adjuntos fuerzan revisión manual. Los proyectos con componentes distintos, como parque, línea y bahía, deben generar actividades separadas.

Si un envío se interrumpe, el caso queda en estado `sending` para revisión humana y no se reenvía automáticamente. La búsqueda de hilos pendientes continúa aunque el mensaje original salga de la bandeja de entrada.

Fuentes técnicas: [Gmail Python quickstart](https://developers.google.com/workspace/gmail/api/quickstart/python), [hilos de Gmail](https://developers.google.com/workspace/gmail/api/guides/threads), [carpetas de Drive](https://developers.google.com/workspace/drive/api/guides/folder), [OpenAI Structured Outputs](https://developers.openai.com/api/docs/guides/structured-outputs).

## Arquitectura

`app.py` es solo el punto de entrada de la línea de comandos. Cada módulo tiene una responsabilidad y las dependencias van en un solo sentido (de abajo hacia arriba):

| Capa | Módulos | Responsabilidad |
|---|---|---|
| Base | `settings`, `status` | Carpeta de datos y configuración; estados de una solicitud (`QuoteStatus`). |
| Entrada/salida externa | `google_services`, `mail`, `llm`, `attachments` | Gmail y Drive, lectura de mensajes y adjuntos, motor de análisis. |
| Reglas | `reception`, `internal_context`, `modes` | Qué falta y qué preguntar, contexto comercial interno, modo de operación. |
| Datos | `store`, `quotes`, `activities` | SQLite, consultas del dashboard, tabla de actividades y Excel. |
| Flujo | `pipeline`, `case_actions` | `pipeline.decide_*` decide qué hacer con cada hilo sin efectos; `apply_decision` ejecuta. Acciones del dashboard sobre un caso. |
| Interfaz | `web`, `dashboard_views`, `templating`, `templates/` | Servidor local con protección CSRF; páginas y fragmentos HTML con plantillas Jinja2. |

## Pruebas y calidad

```
python -m pip install -r requirements-dev.txt
python -m unittest discover
python -m ruff check .
python -m mypy
```

`check.bat` corre lint, tipos, tests y cobertura en un solo paso, igual que el CI (`.github/workflows/ci.yml`). El piso de cobertura está en `pyproject.toml` (`fail_under`); súbelo cuando mejore y no lo bajes para hacer pasar un cambio. Las zonas con menos cobertura son `web.py` (handlers HTTP), `case_actions.py` y `quotes.py`.

En los tests, parchea cada nombre en el módulo que lo **usa** (por ejemplo `pipeline.services`), no en el que lo define. Los tests que tocan datos parchean `settings.DATA_DIR`.

## Cambios en la base de datos

El esquema de `quotes.sqlite3` está versionado con `PRAGMA user_version`. Para cambiarlo, agrega una función al final de `MIGRATIONS` en `store.py`; nunca edites las existentes. Al abrir una base con datos y una versión anterior, el programa deja primero una copia `quotes.sqlite3.bak-vN` y aplica cada paso en su propia transacción. Una base creada por una versión más nueva del programa se rechaza sin modificarla.

Los tests mandan los datos a una carpeta temporal (`_isolate.py`), así que nunca tocan `%LOCALAPPDATA%\AnkaCorreos`. Todo módulo de tests nuevo debe importar `_isolate` primero.

## HTML del dashboard

Todo el HTML sale de `templates/` con escape automático (`templating.render`). Reglas:

- Nunca armes HTML con f-strings ni `|safe` para datos que vienen de un correo. Pasa los valores a la plantilla; se escapan solos. Un fragmento ya renderizado (`Markup`) se puede anidar en otra plantilla.
- No uses manejadores en línea (`onclick=...`). Para confirmar una acción usa `data-confirm="..."`; un único listener en `layout.html` lo lee.
- Los formularios `POST` deben escribirse con `method="post"` (comillas dobles): el CSRF los detecta por ese texto.

`test_html_render.py` protege esto de dos maneras. Compara cada página con una instantánea en `golden/` (si cambias una plantilla a propósito, regenera con `UPDATE_GOLDEN=1 python -m unittest test_html_render` y revisa el diff de `golden/`), y renderiza datos hostiles en todos los campos que controla un cliente para comprobar que no aparecen etiquetas, atributos ni scripts nuevos.

## Tipos

`mypy` revisa todos los módulos (los tests quedan fuera) y exige que toda función esté anotada (`disallow_untyped_defs`). Los clientes de Gmail y Drive se tipan como `Any` porque `googleapiclient` construye sus métodos en ejecución. Si añades una función sin anotar, `mypy` falla en `check.bat` y en el CI.
