# Anka · Automatización de solicitudes de cotización

Sistema local de apoyo comercial para Grupo Anka (topografía y geomática). **Fase 1:** leer Gmail, identificar las
solicitudes de cotización, resumirlas en una tabla de actividades, pedir al cliente los datos que falten (en el mismo
hilo), incorporar su respuesta y crear la carpeta del proyecto en Drive. **No cotiza**: no hay precios en esta fase.

Un comercial lo usa desde un **dashboard web local** que dice, caso por caso, en qué etapa está cada solicitud y qué
espera de él. Nada sale al cliente sin que una persona lo revise y lo envíe.

## Inicio rápido (Windows)

1. Prepara la máquina y los datos locales (secciones [Preparación](#preparación) y [Datos locales y secretos](#datos-locales-y-secretos)).
2. Doble clic en **`Iniciar Anka.bat`**: arranca el dashboard (http://127.0.0.1:8765/) y, en modo ChatGPT Work, el
   puente que trae los borradores de Work al dashboard. Se reinicia solo si se cae o si cambia el código y hace una
   copia de seguridad diaria. Para que arranque con Windows: `tools\Instalar autoarranque.bat`.
3. Mira la etiqueta **Puente activo** del encabezado y la pantalla **Qué hacer hoy**.

## Mapa del repositorio

```
anka/               el código, en capas (ver spec/06-arquitectura.md)
  dominio/            reglas puras: estados, actividades, recepción, correo, contrato con Work
  infra/              lo externo: configuración, base SQLite, Gmail/Drive, modelos, registro, copias
  servicios/          casos de uso: importar, enviar, controlar cambios, seguir al cliente, carpetas de Drive
  web/                servidor y formularios, páginas, seguridad CSRF y plantillas (templates/)
  cli/                comandos, lanzador único y puente con Work
tests/              pruebas (y tests/golden/ con las instantáneas de pantalla)
spec/               especificación: requisitos, reglas, contrato con Work, transiciones, decisiones
docs/               documentación para personas (guía comercial, flujo, modos, estándar de cambios)
skills/             la skill de ChatGPT Work y las reglas operativas que copia
tools/              scripts de operación de Windows (autoarranque, piloto local)
evaluacion/         muestra etiquetada para medir al modelo
.github/            integración continua y plantilla de pull request
app.py, supervisor.py, work_dashboard_sync.py    puntos de entrada de una línea hacia anka/cli/
check.bat, check_spec.py                         verificación completa (igual que el CI)
Iniciar Anka.bat                                 el lanzador de uso diario
```

## Documentación

| Para… | Lee |
|---|---|
| Usar el dashboard | [`docs/GUIA_COMERCIAL.md`](docs/GUIA_COMERCIAL.md) |
| Entender el flujo de la Fase 1 | [`docs/FLUJO_FASE1.md`](docs/FLUJO_FASE1.md) |
| Saber qué debe cumplir el sistema | [`spec/README.md`](spec/README.md) (requisitos, reglas, estados, contrato con Work, decisiones) |
| Cambiar el comportamiento | [`docs/ESTANDAR_CAMBIOS.md`](docs/ESTANDAR_CAMBIOS.md) |
| Operar los tres modos | [`docs/MODO_OPERACION.md`](docs/MODO_OPERACION.md) |
| Probar en ChatGPT Work | [`docs/PILOTO_ACEPTACION.md`](docs/PILOTO_ACEPTACION.md) |
| Trabajar con un agente de IA | [`CLAUDE.md`](CLAUDE.md) |
| El índice completo | [`docs/README.md`](docs/README.md) |

## Datos locales y secretos

Las credenciales, el token, `config.json`, la base SQLite, `review_queue`, `logs` e `internal_context` viven **fuera del repositorio**, en `%LOCALAPPDATA%\AnkaCorreos` (o en la ruta de la variable de entorno `ANKA_DATA_DIR`). Nunca se versionan. Al configurar una máquina nueva, crea esa carpeta y coloca allí `credentials.json` y `config.json`.

## Preparación

1. Instala Python 3.11+ y las dependencias exactas: `python -m pip install --require-hashes -r requirements.lock`.
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

El código vive en el paquete `anka/` en cinco capas con dependencias en un solo sentido
(`cli` y `web` → `servicios` → `infra` → `dominio`), sin ciclos. `tests/test_arquitectura.py` lo verifica.
El detalle, dónde poner código nuevo y las reglas están en [`spec/06-arquitectura.md`](spec/06-arquitectura.md).

## Pruebas y calidad

```
python -m pip install --require-hashes -r requirements-dev.lock
python -m unittest discover -s tests -t .
python -m ruff check .
python -m mypy
```

`check.bat` corre lint, tipos, tests y cobertura en un solo paso, igual que el CI (`.github/workflows/ci.yml`). El piso de cobertura está en `pyproject.toml` (`fail_under`); súbelo cuando mejore y no lo bajes para hacer pasar un cambio. Las zonas con menos cobertura son `anka/web/server.py` (manejadores HTTP) y `anka/cli/`.

En los tests, parchea cada nombre en el módulo que lo **usa** (por ejemplo `anka.servicios.pipeline.services`), no en el que lo define. Los tests que tocan datos parchean `settings.DATA_DIR` (`anka.infra.settings`).

## Cambios en la base de datos

El esquema de `quotes.sqlite3` está versionado con `PRAGMA user_version`. Para cambiarlo, agrega una función al final de `MIGRATIONS` en `anka/infra/store.py`; nunca edites las existentes. Al abrir una base con datos y una versión anterior, el programa deja primero una copia `quotes.sqlite3.bak-vN` y aplica cada paso en su propia transacción. Una base creada por una versión más nueva del programa se rechaza sin modificarla.

Los tests mandan los datos a una carpeta temporal (`tests/_isolate.py`), así que nunca tocan `%LOCALAPPDATA%\AnkaCorreos`. Todo módulo de tests nuevo debe empezar con `from tests import _isolate  # noqa: F401`.

## HTML del dashboard

Todo el HTML sale de `anka/web/templates/` con escape automático (`anka.web.templating.render`). Reglas:

- Nunca armes HTML con f-strings ni `|safe` para datos que vienen de un correo. Pasa los valores a la plantilla; se escapan solos. Un fragmento ya renderizado (`Markup`) se puede anidar en otra plantilla.
- No uses manejadores en línea (`onclick=...`). Para confirmar una acción usa `data-confirm="..."`; un único listener en `layout.html` lo lee.
- Los formularios `POST` deben escribirse con `method="post"` (comillas dobles): el CSRF los detecta por ese texto.

`tests/test_html_render.py` protege esto de dos maneras. Compara cada página con una instantánea en `tests/golden/` (si cambias una plantilla a propósito, regenera con `UPDATE_GOLDEN=1 python -m unittest tests.test_html_render` y revisa el diff de `tests/golden/`), y renderiza datos hostiles en todos los campos que controla un cliente para comprobar que no aparecen etiquetas, atributos ni scripts nuevos.

## Tipos

`mypy` revisa el paquete `anka/` y los puntos de entrada (los tests quedan fuera) y exige que toda función esté anotada (`disallow_untyped_defs`). Los clientes de Gmail y Drive se tipan como `Any` porque `googleapiclient` construye sus métodos en ejecución. Si añades una función sin anotar, `mypy` falla en `check.bat` y en el CI.

## Validación de `config.json`

`validate_config` (`anka/infra/settings.py`) revisa el archivo completo y lista **todos** los problemas juntos, no solo el primero:

```
config.json tiene 2 problema(s):
 - active_mode debe ser 'local', 'api' o 'chatgpt_work'.
 - gmail_query debe ser texto.
```

Además, un JSON mal escrito indica la línea y la columna del error; las claves que el programa no usa (por ejemplo `gmail_quary`) se avisan por `stderr` al iniciar cualquier comando; y `set-mode` no guarda una configuración que resultaría inválida. Al añadir una clave nueva, agrégala a `KNOWN_CONFIG_KEYS` y a `validate_config` en `anka/infra/settings.py`, y a `config.example.json` (una prueba comprueba que el ejemplo sea válido).

## Dependencias y versiones exactas

`requirements.txt` y `requirements-dev.txt` declaran los **rangos** permitidos; `requirements.lock` y `requirements-dev.lock` fijan las **versiones exactas con hashes** (incluidas las transitivas), así que una instalación hoy y otra en seis meses son idénticas y verificadas. El CI instala desde los `.lock` en Python 3.11 y 3.12. Python 3.10 no está soportado: las dependencias actuales (por ejemplo `cryptography`) ya no publican paquetes para esa versión.

Para actualizar (requiere [uv](https://docs.astral.sh/uv/)), cambia el rango si hace falta y regenera los dos archivos, luego corre `check.bat`:

```
uv pip compile requirements.txt --universal --python-version 3.11 --generate-hashes -o requirements.lock
uv pip compile requirements-dev.txt --universal --python-version 3.11 --generate-hashes -o requirements-dev.lock
```

## Registro (`logging`)

El piloto y el dashboard escriben en dos destinos (`anka/infra/logging_setup.py`, se configura al iniciar cualquier comando de `app.py`):

- **Consola** (`stdout`): el mismo texto de siempre, con todo el detalle (resumen, actividades, borrador).
- **Archivo** `logs/anka.log` en la carpeta de datos, rotativo (1 MB × 5): fecha, nivel, módulo y mensaje. **Sin datos de clientes**: solo identificadores de hilo y resultados (`t-123: borrador creado en Gmail: r-456`), rechazos de seguridad, advertencias y errores con su traceback. Cualquier dirección de correo que se cuele se reemplaza por `<correo>`.

`ANKA_LOG_LEVEL=DEBUG` (o `WARNING`, `ERROR`) cambia el nivel. Si el archivo no se puede abrir, se avisa y el programa sigue sin él.

Reglas para el código nuevo:

- Usa `log = get_logger(__name__)`, no `print`, para lo que sea registro. `print` queda solo para la salida de un comando (`mode`, `reviews`, `drive-tree`...).
- Todo mensaje que lleve contenido de un cliente (correo, destinatario, actividades, borrador, nombres de archivo) se marca con `extra=SENSITIVE`: va a la consola y no al archivo. Ejemplo: `log.info("%s: %s", thread_id, resumen, extra=SENSITIVE)`.
- En un `except` amplio, registra con `log.exception(...)`: el traceback es lo que permite diagnosticar después.

## Qué se descarta y cómo recuperarlo

El sistema **no usa palabras clave para decidir si un correo es una solicitud**: eso lo decide el modelo. Antes de llamarlo solo descarta ruido evidente (`automated_noise_reason` en `anka/dominio/reception.py`), y únicamente si todos los mensajes del cliente en el hilo tienen una señal inequívoca de automatización:

- remitente `no-reply`, `noreply`, `notifications`, `mailer-daemon`, `postmaster` o `bounce`;
- cabecera `List-Unsubscribe` (boletines y listas);
- `Auto-Submitted` distinto de `no`, o `Precedence` de tipo `bulk`, `list`, `junk` o `auto_reply`;
- marcado como spam por Gmail.

Un remitente humano nunca se descarta aquí, aunque Gmail lo ponga en Promociones. `commercial_filter_terms` en `config.json` solo sirve para rescatar un aviso automático que hable de cotizaciones (por ejemplo, el portal de un cliente). Si el modelo concluye que no es una solicitud pero hay adjuntos que no se pudieron leer, el caso va a revisión manual en vez de descartarse.

Todo lo descartado queda en la pestaña **Descartadas** del dashboard con su motivo, remitente y asunto. El botón **Rescatar** marca el hilo para que la próxima revisión de Gmail lo analice aunque parezca automático (solo funciona con el piloto de Python activo, no en modo `chatgpt_work`). Contrapartida: más correos llegan al modelo local (unos segundos o minutos cada uno, sin costo de API).

## Estructura de Drive

Las carpetas siguen la convención `Año / Cliente / Solicitud`. `drive_parent_id` en `config.json` es la carpeta **del año vigente** (por ejemplo `2026`); al cambiar de año hay que apuntarlo a la nueva (si la solicitud es de otro año que la carpeta padre, el sistema no crea nada y avisa). Dentro de ella:

- La carpeta del **cliente** se busca por nombre sin importar mayúsculas, tildes ni sufijos como S.A.S. o Ltda. Si hay una sola coincidencia se reutiliza; si hay varias parecidas, decide una persona. **Nunca se crea una carpeta de cliente por cuenta propia.**
- La carpeta de la **solicitud** (`Cotizacion - <proyecto> - <id>`) se crea sola solo cuando el cliente está confirmado: lo confirmó una persona en ese hilo, o en otro hilo del mismo remitente. Si no, el caso queda en "Información suficiente para revisión interna" con el motivo, y en la pantalla del caso aparece **Carpeta del proyecto en Drive**: se escribe la carpeta del cliente, se marca "cliente nuevo" solo si hay que crearla, y se pulsa **Crear carpeta del proyecto**. Así los siguientes correos de ese remitente se resuelven solos.
- Por línea de comandos: `python app.py approve --thread-id <id> --client-name "Minera Sur" [--new-client]`.

## Los tres modos de operación

Ollama (`local`), API (`api`) y ChatGPT Work (`chatgpt_work`) deben comportarse igual. Las reglas viven en un solo documento (`skills/anka-cotizaciones-correo/references/reglas_operativas.md`), `local` y `api` comparten el mismo código y Work usa la skill, y `tests/test_paridad_modos.py` falla si alguno se separa. El procedimiento para cambiar cualquier regla está en [docs/ESTANDAR_CAMBIOS.md](docs/ESTANDAR_CAMBIOS.md).
