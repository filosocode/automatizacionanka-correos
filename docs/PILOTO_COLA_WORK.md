# Piloto: cola de trabajos Python → Work (T-49, D-153)

Primera prueba **manual y supervisada** para que ChatGPT Work lea **una sola hoja de laboratorio** que Python preparó
para un mensaje ya registrado en `recepcion_mensajes`. Nada de esto corre solo: no hay tarea periódica, receptor ni
supervisor involucrados, y Sheets no activa a Work.

## Qué hace y qué no
- **Hace:** crea un trabajo en `trabajos_work` (SQLite, fuente principal), genera una hoja `.xlsx` por trabajo y, solo con
  `--confirmar-drive`, la publica en una carpeta de laboratorio de Drive con la marca `appProperties.anka_job_id`.
- **No hace:** no analiza, no cambia estados comerciales, no crea ni envía borradores, no escribe en Gmail ni en Sheets, no
  cambia el contrato `[ANKA_DASHBOARD_JSON_V1]` ni toca ANKA 01.

## Antes de empezar
1. Un mensaje **ficticio** (sin datos comerciales reales) ya registrado por el receptor y aún en
   `pendiente_de_procesar` / `pendiente_de_interpretacion_work`.
2. Una carpeta de Drive **solo de laboratorio** (su id). Anka usa el alcance `drive` que ya tiene (D-72); no se pide otro.
3. Una copia de la SQLite o una base temporal si se quiere ensayar sin tocar la comercial:
   `set ANKA_DATA_DIR=<carpeta temporal>` antes de cada orden.

## Procedimiento
1. **Crear** (solo SQLite): `.venv\Scripts\python.exe -m anka.cli.work_jobs crear --thread-id <hilo> --message-id <mensaje>`.
   Devuelve el `job_id` (`ANKAJOB-` + 16 hexadecimales). Repetir la orden devuelve el mismo trabajo.
2. **Revisar la hoja sin Drive**: `... work_jobs exportar --job-id <job_id> --salida hoja.xlsx`. Debe traer `job_id`,
   `thread_id`, `message_id`, `ref_sha256` y los adjuntos esperados, y **ningún** cuerpo ni texto de adjuntos.
3. **Publicar** (única escritura en Drive; pedir autorización antes):
   `... work_jobs publicar --job-id <job_id> --carpeta-id <id> --confirmar-drive`.
   Resultados: `creado`, `actualizado`, `adoptado` (había un archivo con la marca de una publicación anterior),
   `no_reclamado` (otra publicación está en curso), `ambiguo` o `error`.
4. **Work (manual):** dar a Work el `job_id` y el enlace de la hoja. Work la lee, abre en Gmail el hilo y el mensaje,
   compara remitente, asunto, fecha y adjuntos con la hoja `VERIFICACION` y deja el borrador `[ANKA_DASHBOARD_JSON_V1]`
   de siempre. Work **no escribe** en la hoja.
5. **Importar:** `... work_sync --once --only-thread-id <hilo> --only-message-id <mensaje>` (T-47). Concilia el mensaje
   (T-48) y cierra el trabajo (`conciliado`) en la misma transacción.
6. **Verificar** (solo lectura): fila de `recepcion_mensajes` en `procesado/interpretado_por_work`, trabajo `conciliado`,
   un evento `trabajo_conciliado`, `PRAGMA integrity_check` = `ok`. Repetir la importación no debe cambiar nada.

## Si algo sale ambiguo
- `ambiguo` significa que hay más de un archivo con la marca del trabajo (o que el listado no confirmó lo creado). **No se
  borra ni se elige ninguno** y no se republica solo: una persona decide cuál conservar en Drive y deja constancia.
- Un `error` de Drive deja el trabajo en `pendiente_de_publicar` con el tipo de error (nunca texto del correo). Antes de
  reintentar, `publicar` busca por la marca y adopta lo que ya exista: crear no se repite a ciegas.
- Un reclamo (`publicando`) vence a los 120 s; si el proceso murió, la siguiente publicación lo retoma.

## Riesgos pendientes
- No está verificado que Work pueda abrir la hoja desde su conector de Drive/Sheets; si no puede, se pega su contenido en
  el prompt, lo que prueba igual la verificación y el retorno.
- La hoja lleva remitente y asunto: es una salida nueva de datos del cliente hacia Drive (misma cuenta). Usar solo
  mensajes ficticios en la prueba.
- Gmail no expone en la cola el `Message-ID` RFC 822; Work compara por ids de Gmail, remitente, asunto y fecha.

## Despacho T-53 (PENDIENTES / CERRADOS) — prueba real de UN solo expediente

> **No ejecutar sin autorización.** Cada orden es una sola pasada; no hay proceso ni tarea periódica. Requiere la
> carpeta raíz de laboratorio de Drive (distinta de las carpetas comerciales) y un mensaje ficticio ya presente en la cola.

Preparación (solo lectura): `work_jobs elegibilidad --thread-id <hilo> --message-id <mensaje>` debe mostrar
`elegible` y la clasificación preliminar. Copia de seguridad de la base antes de empezar (API de copia de SQLite).

1. **Pasada de despacho** (crea `PENDIENTES` y `CERRADOS` si faltan y publica la hoja en `PENDIENTES`):
   `.venv\Scripts\python.exe -m anka.cli.work_jobs despachar (desde D:\Automatizacion_Correos) --carpeta-id <RAIZ> --thread-id <hilo> --message-id <mensaje> --confirmar-drive`
   Repetirla no duplica carpetas ni hojas (`ya_publicado`).
2. **Work** (ejecución manual o programada, con el texto de `docs/TAREA_WORK_COLA.md`): toma la hoja de `PENDIENTES` y deja el borrador.
3. **Importar** el borrador aislado: `work_sync --once --only-thread-id <hilo> --only-message-id <mensaje>`.
4. **Otra pasada de despacho** (mismos argumentos): mueve la hoja a `CERRADOS` y anota un solo evento `trabajo_archivado`.
5. **Verificar** (solo lectura): trabajo `conciliado` con `folder_id` = CERRADOS, un archivo con la marca `anka_job_id`
   en `CERRADOS` y ninguno en `PENDIENTES`, recepción `procesado/interpretado_por_work`, `PRAGMA integrity_check` = `ok`.

Despacho automático (opcional, apagado por defecto): en `config.json` ponga `work_dispatch_enabled` en `true`, con
`active_mode` `chatgpt_work`, `work_dispatch_folder_id`, `work_dispatch_thread_id` y `work_dispatch_message_id`; luego
`work_jobs despachar --automatico --confirmar-drive` hace una pasada con esos valores (sin ellos, no hace nada). El piloto
admite **un solo mensaje**; no existe un modo «toda la cola».

## Paquete de referencia para la tarea Work (T-54) — cómo entregarlo

Work no lee el repositorio ni ZIP: solo Drive y Gmail. El contrato (`ANKA_DASHBOARD_JSON_V1`), T-52 y `PRECISO_V6.1` se le entregan
como una carpeta de archivos de texto con manifiesto y huellas SHA-256. Todo se hace desde `D:\Automatizacion_Correos`.

1. **Construir en local** (no toca Drive; es determinista): `.venv\Scripts\python.exe -m anka.cli.paquete_work construir --carpeta archivo_local\paquete_work`
2. **Verificar en local** (solo lectura): `.venv\Scripts\python.exe -m anka.cli.paquete_work verificar --carpeta archivo_local\paquete_work` → `"correcto": true`.
3. **Publicar** (escribe en Drive; **solo con autorización**): `.venv\Scripts\python.exe -m anka.cli.paquete_work publicar --carpeta archivo_local\paquete_work --carpeta-id <RAIZ> --confirmar-drive`
   Crea (si falta) una sola subcarpeta `REFERENCIA_ANKA_V1` dentro de la raíz de laboratorio y sube 11 archivos. Repetirla no duplica
   (`sin_cambios`); si el paquete cambia solo actualiza lo cambiado. Un duplicado detiene la orden. Nunca borra.
4. **Comprobar** (solo lectura): la carpeta contiene 11 archivos, cada uno con `appProperties.anka_paquete_sha256` igual a la huella de `manifest.json`.
5. **Configurar la tarea programada de Work** (independiente de ANKA 01), con acceso de lectura a la raíz de laboratorio. Su prompt corto:
   > Antes de nada abre la carpeta de Drive `REFERENCIA_ANKA_V1` (dentro de la raíz de laboratorio), lee `INSTRUCCIONES_UNA_EJECUCION.md` y
   > sigue ese documento al pie de la letra: primero verifica el paquete con `manifest.json` y, si algo falta, no coincide, está
   > desactualizado o se contradice, detente sin crear borrador y dime qué falló. No envíes correos y no escribas en Drive.
6. **Si cambia el contrato o T-52:** reconstruir, verificar y volver a publicar. La versión (`paquete_version`) cambia sola y las
   instrucciones obligan a Work a detenerse si ve un paquete que no corresponde a la hoja del trabajo (`version_del_contrato`).

### Actualizar con seguridad un paquete ya publicado (REFERENCIA_ANKA_V1)

La publicación sube los archivos uno a uno y el `manifest.json` **al final**; no es atómica. Mientras dura, un paquete mezclado
no coincide con su manifiesto y las instrucciones obligan a Work a detenerse sin borrador (falla en seguro). Aun así:

1. **Evitar la ventana:** que no haya una ejecución de Work en curso ni próxima (pausar o esperar a que pase la programada; no se
   toca ANKA 01). La orden dura segundos.
2. **Reconstruir y verificar en local:** `construir` y `verificar` (arriba). `verificar` también rechaza ejemplos que se contradigan.
3. **Anotar la versión publicada actual** (`paquete_version` del `manifest.json` de Drive) por si hay que volver atrás.
4. **Publicar** con la misma orden (`publicar ... --confirmar-drive`, solo con autorización): solo se actualizan los archivos cuya
   huella cambió (`actualizado`); el resto queda `sin_cambios`, y no se crea nada nuevo ni se borra nada.
5. **Verificar directamente en Drive:** una sola carpeta `REFERENCIA_ANKA_V1`, 11 archivos con nombres únicos, cada uno con su
   SHA-256 igual al del manifiesto y a `anka_paquete_sha256`, y el manifiesto de Drive idéntico al local.
6. **Si algo falla o aparece un duplicado:** detenerse sin reparaciones destructivas. Para volver atrás, reconstruir el paquete desde el
   commit o la copia anterior y volver a publicar (la orden restaura los archivos cambiados).
7. Antes de la siguiente ejecución de Work, comprobar que el `paquete_version` del manifiesto de Drive es el nuevo.

## Coordinación automática Python–Work (T-55)

Convierte el ciclo del piloto 5B en un ciclo repetible: **archivar** lo conciliado, **importar** los borradores de Work (T-47) y
**despachar** a `PENDIENTES` los mensajes elegibles nuevos. Está **apagada por defecto**. SQLite decide; Drive y Gmail solo ejecutan.
Python solo lee Gmail y no envía ni crea borradores. Work sigue siendo una tarea programada **independiente** (se crea aparte, ver
`docs/TAREA_WORK_COLA.md`); esta función no crea ni modifica tareas.

### Configuración mínima de un piloto limitado (`config.json`, fuera del repositorio)
```json
"active_mode": "chatgpt_work",
"work_coordination_enabled": true,
"work_coordination_folder_id": "<raíz con PENDIENTES, CERRADOS y REFERENCIA_ANKA_V1>",
"work_coordination_since": "<fecha y hora ISO con zona, por ejemplo 2026-10-19T08:00:00-05:00>",
"work_coordination_interval_seconds": 300,
"work_coordination_max_publish": 1,
"work_coordination_max_import": 1,
"work_coordination_max_archive": 2,
"work_dispatch_enabled": false
```
- `work_coordination_since` es obligatoria: los mensajes detectados **antes** no se procesan jamás (la cola histórica queda quieta).
- Para el piloto más estrecho, agrega `work_coordination_thread_id` y `work_coordination_message_id`: todo el ciclo se limita a ese mensaje.
- No puede estar activo a la vez `work_dispatch_enabled` (habría dos despachadores). Si falta algo, la validación del arranque lo dice.
- La recepción de Gmail (`gmail_reception_enabled`) debe estar activa para que lleguen mensajes nuevos a la cola; es el único receptor.

### Cómo se ejecuta
- **Automático:** el lanzador (`supervisor.py`) corre un ciclo cada `work_coordination_interval_seconds` en un hilo, como el receptor de Gmail,
  solo si la coordinación está activada y el modo es `chatgpt_work`. Cambiar `config.json` se lee en 30 s.
- **Una pasada manual:** `.venv\Scripts\python.exe -m anka.cli.work_jobs coordinar --una-vez --confirmar [--thread-id T --message-id M]`
  (lee Gmail y escribe en Drive y SQLite; sin `--confirmar` se niega; con la función apagada responde `desactivado` sin tocar Google).
- Un candado (`work_coordinacion.lock` en la carpeta de datos) impide dos ciclos a la vez; el segundo responde `concurrente`.

### Qué mirar y cuándo intervenir una persona
- El registro (`Coordinación Work: archivados[...] importaciones[...] publicaciones[...] errores[...]`) resume cada ciclo, sin datos del correo.
- `requieren_persona` en el informe (y el evento `coordinacion_requiere_persona`) indica un trabajo cuya importación falló 5 veces seguidas
  (por ejemplo, **dos borradores válidos** del mismo mensaje, un borrador de otro mensaje o ilegible). Nada se resuelve solo: borra el borrador
  sobrante o corrige el bloque en Gmail y reinicia el lanzador; el trabajo sigue `publicado` y se importa en el ciclo siguiente.
- Un trabajo `ambiguo` (dos hojas con su marca en Drive) o `agotado` (5 intentos de publicar) tampoco se toca solo.

### Cómo apagarla
Pon `work_coordination_enabled` en `false` (o quítalo): el lanzador deja de lanzar ciclos en 30 s. No hay nada más que detener.

### Piloto automático limitado a un mensaje ficticio (FASE 6B)

**Configuración definitiva** (`config.json`, fuera del repositorio; los valores entre `<>` los pone una persona):
```json
"active_mode": "chatgpt_work",
"gmail_reception_enabled": true,
"gmail_reception_interval_seconds": 60,
"work_dispatch_enabled": false,
"work_coordination_enabled": true,
"work_coordination_folder_id": "<raíz con PENDIENTES, CERRADOS y REFERENCIA_ANKA_V1>",
"work_coordination_since": "<fecha y hora ISO con zona, posterior al último mensaje histórico>",
"work_coordination_thread_id": "<hilo del mensaje ficticio>",
"work_coordination_message_id": "<mensaje ficticio>",
"work_coordination_interval_seconds": 300,
"work_coordination_max_publish": 1,
"work_coordination_max_import": 1,
"work_coordination_max_archive": 2,
"work_coordination_alert_minutes": 120
```
Con la coordinación activa **el coordinador es el único importador de borradores**: el lanzador no arranca el puente general
(`work_dashboard_sync`) y detiene el que esté en marcha, el puente general lanzado a mano se niega, y el ciclo no importa si ve un
puente vivo. La importación aislada manual (`work_sync --once --only-thread-id --only-message-id`) sigue siendo posible.

**Antes de activar — verificación de solo lectura** (no usa Gmail ni Drive y no escribe):
```
.venv\Scripts\python.exe -m anka.cli.work_jobs verificar-piloto
```
Debe devolver `"listo": true` y `"bloqueos": []`. Los avisos se confirman a mano (sobre todo la tarea programada de Work, que Python no
puede comprobar: que lea `PENDIENTES` y `REFERENCIA_ANKA_V1`, cree un solo borrador por trabajo y no envíe nada).

**Activación** (solo con autorización):
1. Con el lanzador detenido (o corriendo: leerá la configuración en 30 s), edita `config.json` con la configuración de arriba. Mientras
   `work_coordination_enabled` sea `false` no ocurre nada.
2. Ejecuta `verificar-piloto`; resuelve cualquier bloqueo. No sigas con bloqueos.
3. Comprueba que el mensaje ficticio aparezca en la cola (recepción activa) y que sea posterior a `work_coordination_since`.
4. Opcional, una pasada manual antes de dejar el lanzador: `work_jobs coordinar --una-vez --confirmar` (publica, o no, según la cola).
5. Activa `work_coordination_enabled` en `true` y arranca el lanzador (`Iniciar Anka.bat`). Cada `work_coordination_interval_seconds`
   corre un ciclo; el registro muestra `Coordinación Work: archivados[...] importaciones[...] publicaciones[...] recuperadas[...] alertas[...]`.
6. Si el trabajo pasa de `work_coordination_alert_minutes` sin respuesta aparece un solo aviso y el evento `coordinacion_sin_respuesta`.

**Desactivación inmediata:** detén primero el lanzador y después pon `work_coordination_enabled` en `false` (T-56). Con el lanzador activo, deja
de lanzar ciclos en 30 s; el puente general **no** arranca por eso: solo corre con `work_general_bridge_enabled` en `true`. Un ciclo en curso termina solo (es corto y cada paso es idempotente). No hace falta tocar
Drive: la cola y las hojas se conservan. Para pausar también a Work, desactiva su tarea programada (decisión del dueño, fuera de Python).
