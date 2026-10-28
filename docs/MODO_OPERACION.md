# Modo de operacion

El sistema puede trabajar en tres escenarios. Solo uno debe estar activo procesando Gmail.

## Regla principal

No correr dos orquestadores al mismo tiempo sobre la misma cuenta de Gmail.

Ejemplos de conflicto:

- bot local activo y tarea de ChatGPT Work activa;
- bot en modo API activo y bot en modo local activo;
- una ventana vieja con `--review-only` y otra con `--gmail-drafts`.

## Ver modo activo

```powershell
.\.venv\Scripts\python.exe app.py mode
```

## Activar modo local

Usa Ollama local. No consume API de OpenAI.

```powershell
.\.venv\Scripts\python.exe app.py set-mode --active-mode local
```

Para correr el piloto de borradores:

```powershell
.\.venv\Scripts\python.exe app.py run --apply --gmail-drafts --watch-seconds 300
```

El botón `Piloto borradores Gmail.bat` ejecuta exactamente este flujo y primero muestra el modo activo. El modo vigente se define en `active_mode` dentro de `config.json` (carpeta de datos, no versionada); confírmalo siempre con `app.py mode`. Si vale `chatgpt_work`, el mismo botón se detiene antes de tocar Gmail para evitar dos orquestadores; cambia a `local` con `set-mode` solo si el orquestador de ChatGPT está desactivado.

En `config.json`, `own_addresses` debe incluir la cuenta principal y todos los aliases usados por Anka. El programa también consulta la dirección principal autenticada en Gmail y excluye esos mensajes antes de escoger remitente o analizar el hilo.

La política predeterminada para reenvíos es `forwarded_reply_policy: internal_reviewer`. El sistema intenta identificar al cliente original dentro del mensaje, pero prepara el borrador para el comercial de Anka que lo reenvió. Cambiarla a `original_sender` solo debe hacerse después de validar el enrutamiento con suficientes casos reales.

### Trigger Python de recepción (opt-in, U-81)

La recepción determinista es un sondeo de solo lectura que corre dentro del supervisor **solo en modo `chatgpt_work`**. Convive con ANKA 01; no analiza, no modifica el JSON del puente, no cambia el correo y no crea casos. Está apagada por defecto. Para habilitarla en Windows, edita `%LOCALAPPDATA%\AnkaCorreos\config.json` y agrega:

```json
"gmail_reception_enabled": true,
"gmail_reception_interval_seconds": 60
```

Reinicia `Iniciar Anka.bat`. La primera consulta fija el `historyId` y la hora de inicio; no importa mensajes previos. A partir de ahí registra IDs detectados y metadatos en `quotes.sqlite3`. El intervalo admite 30 a 3600 segundos. La migración 22 añade columnas al control de recepción. La migración 23 asegura la tabla de auditoría `recepcion_eventos`, incluso si una base ya marcada como v22 estuviera incompleta.

Cada escritura comprueba el token propietario de la reserva y que no haya vencido; se renueva alrededor de cada llamada Gmail. Si la operación supera el lease y otra ejecución lo toma, la anterior aborta antes de modificar cola o cursor. Un lote acumulado de History también tiene límite de 1000 candidatos: si se supera, se conserva el cursor y el receptor se pausa en revisión manual.

Ante un `historyId` vencido, una búsqueda desde el último sondeo correcto menos 24 horas solo se permite después de que al menos una sincronización delta terminara correctamente. Usa fecha PST y un día adicional de margen; recorre todas las páginas antes de guardar candidatos y mover el cursor. Se limita a 30 días y 1000 candidatos. Una ventana más larga, desconocida, más de 1000 candidatos o un cursor vencido antes de la primera delta quedan en `revision_manual`, conservan el cursor y pausan el sondeo hasta una revisión. No se deduce que no hubo mensajes cuando la consulta no terminó. Gmail solo permite reconstruir mensajes que sigan presentes en el buzón; los borrados permanentemente no son recuperables. El filtro de respaldo usa la fecha interna de Gmail, así que mensajes importados con una fecha interna antigua no tienen garantía de aparecer aunque se hayan añadido durante el hueco.

La guía de piloto —que aún no se ejecuta— está en [`docs/PILOTO_TRIGGER_GMAIL.md`](PILOTO_TRIGGER_GMAIL.md). Antes de habilitarlo, tenga presente que el token Google del proyecto también conserva alcances Gmail de composición/envío y Drive para las funciones ya existentes, aunque el receptor solo usa operaciones de lectura.

Para apagarlo, cambia `gmail_reception_enabled` a `false` y reinicia el supervisor. Las filas de recepción y el cursor se conservan; el trigger no borra ni cambia nada en Gmail.

Gmail Work no tiene una API de activación de tareas expuesta al programa: el puente existente lee borradores de Gmail depositados por Work. El paquete U-81 deja `event_packet(thread_id, message_id)` como salida Python interna; un despacho automático a ANKA 01 o a otro chat queda pendiente de comprobar y no se implementa aquí.

### Importación aislada de un borrador (piloto 03, T-47)

Para probar el retorno Work → Python con **un** mensaje, sin que el puente toque el resto de la cuenta:

```powershell
.\.venv\Scripts\python.exe work_dashboard_sync.py --once --only-thread-id 1a149ae2f36b4147 --only-message-id 1a149ae2f36b4147
```

Las dos opciones son obligatorias juntas y solo valen con `--once`. Solo se leen los borradores de ese hilo; el JSON debe declarar ese hilo y ese mensaje; el mensaje origen se verifica en Gmail siempre (existe, no es borrador ni enviado, el remitente coincide con `internal_sender` o `client_email`), aunque el borrador no declare `skill_version`. Con dos borradores válidos para el mismo mensaje no importa ninguno. No escribe el estado ni el latido del puente, no crea casos mínimos, no sincroniza la actividad ni la hoja de Google, no anota rechazos y no actualiza `recepcion_mensajes`. Imprime una línea JSON con el resultado (`importado`, `sin_borrador`, `rechazado`, `ambiguo`, `error_de_origen`, `fallo_de_importacion`) y sale con 0 solo si importó. Importar dos veces no duplica la auditoría. Como no guarda estado, el puente general volverá a ver ese borrador cuando se active (es idempotente). Requisito: T-47; decisión: D-150.

## Activar modo API

Usa proveedor API. Actualmente el bot soporta OpenAI API.

```powershell
.\.venv\Scripts\python.exe app.py set-mode --active-mode api
```

Antes de correrlo, verifica que exista `OPENAI_API_KEY` en el entorno.

## Activar modo ChatGPT Work / Plus

Usa ChatGPT Work/Plus con conectores y la skill `anka-cotizaciones-correo`.

```powershell
.\.venv\Scripts\python.exe app.py set-mode --active-mode chatgpt_work
```

En este modo el bot local bloquea `run/watch` para evitar duplicados. La tarea de ChatGPT debe ser el unico orquestador activo.

### Puente general de borradores (opt-in, T-56)

El puente general (`work_dashboard_sync`, que importa todos los borradores de la cuenta) **no arranca por omisión**: el lanzador solo lo inicia y el puente manual solo corre si `config.json` trae

```json
"work_general_bridge_enabled": true
```

La clave viene en `false` en `config.example.json`; un valor que no sea el booleano `true` (o su ausencia) cuenta como apagado. Condiciones para volver a habilitarlo en operación normal:
1. El modo activo es `chatgpt_work` y la coordinación (`work_coordination_enabled`) está apagada: con la coordinación activa el puente queda excluido aunque esté autorizado (D-161).
2. Antes de autorizarlo, `PY -m anka.cli.work_jobs verificar-piloto` no muestra bloqueos ni procesos concurrentes, y sabes qué borradores históricos hay en la cuenta: el puente los importa todos (los inválidos quedan como rechazos y crean expedientes mínimos).
3. Se autoriza con el lanzador detenido y se arranca después; para retirarlo basta poner la clave en `false` (el lanzador lo detiene en 30 s).

La importación aislada de un borrador (`--once --only-thread-id --only-message-id`) no necesita esta clave. Al apagar la coordinación, detén antes el lanzador. Requisito: T-56; decisión: D-163.

## Skill de decision

La skill `skills/anka-cotizaciones-correo` contiene las reglas comunes para:

- detectar solicitudes de cotizacion;
- extraer datos del correo;
- armar tabla de actividades;
- detectar faltantes;
- redactar borradores seguros;
- decidir si el caso requiere revision, preguntas al cliente o carpeta de Drive.

La misma logica debe guiar el bot local, el modo API y el modo ChatGPT Work.

## Adjuntos

**Con el prompt V6 (T-41) Work lee los adjuntos directamente desde Gmail y no se necesita el preparador de abajo:** el puente compara
el nombre y el tamaño de cada adjunto que Work reporta con los que Gmail tiene en el hilo y nombra los que falten. Pega
`docs/PROMPT_WORK_ANKA_V6.txt` en la tarea de Work y deja `work_drive_enabled` en `false`. Lo que sigue describe el camino anterior
(V5 y antes), que se retirará cuando V6 esté probado en Work.

En `chatgpt_work` se puede activar el preparador Gmail → OCR → Drive (T-45). Es un proceso de documentos,
no otro analizador comercial: Work sigue decidiendo el alcance y el borrador. El preparador requiere una raíz
de recepción y una búsqueda explícitas; publica originales, versiones, segmentos y manifiesto por hilo.
Véase [configuración y prueba](WORK_DRIVE_OCR.md). La tarea de Work debe actualizarse para consultar ese manifiesto.

El bot extrae texto y cobertura verificable de TXT, CSV, JSON, XML, KML/KMZ, GeoJSON, GPX, planes QGroundControl (.plan/.waypoints), DJI WPML en KMZ, PDF, DOC/DOCX, XLS/XLSX, PPT/PPTX, ODT/ODS/ODP, RTF, HTML, imágenes y documentos compatibles dentro de ZIP. El OCR local inspecciona imágenes, páginas PDF escaneadas y figuras incrustadas; formatos Office antiguos se convierten con LibreOffice cuando está instalado. Shapefiles se leen desde ZIP cuando incluyen .shp, .shx y .dbf. Las capturas inline de firma no bloquean el análisis. DWG/DXF, OCR ilegible, gráficos estructurados y todo contenido que no pueda convertirse quedan identificados para revisión manual.

Una referencia a trabajos anteriores sin su alcance adjunto se registra como dependencia pendiente. Las cifras o requisitos incompatibles entre fuentes se registran como contradicciones. Ambos casos impiden crear automáticamente la carpeta del proyecto hasta que comercial o técnica los resuelva.

## Copias de seguridad y secretos (U-78)

La copia diaria (local y en la carpeta «Anka respaldos» de Drive) lleva la base, la evidencia y `config.redactado.json`: la
configuración con los valores sensibles cambiados por `[REDACTADO]`. **No** lleva `config.json` en bruto, `token.json` ni
`credentials.json`.

- **Restaurar** (`python -m anka.infra.backup --restore ZIP`, con el lanzador cerrado) no pisa el `config.json` que ya existe. Si no hay
  ninguno, instala el redactado como `config.json`: hay que volver a poner a mano los valores `[REDACTADO]`, autorizar Google otra vez
  (`python app.py auth`) y definir `OPENAI_API_KEY` si se usa el modo `api`. Una copia hecha antes de este cambio trae `config.json`
  y se restaura como siempre.
- **Revisión pendiente en Drive (la hace una persona, no el programa).** Las copias subidas antes de U-78 pueden contener `config.json`
  con lo que alguien haya escrito allí. Abre la carpeta «Anka respaldos», revisa si alguna clave, token o contraseña estuvo en ese
  archivo, **rota esas credenciales** y borra (también de la papelera) las copias antiguas que los contengan.
