# Piloto 6C — Activación controlada del coordinador T-55 con un único mensaje ficticio nuevo

**Estado: preparado, nada activado.** Este documento solo prescribe; cada paso que escribe (SQLite, Gmail, Drive, configuración) necesita la
autorización expresa del dueño en el momento. Todo se ejecuta desde `D:\Automatizacion_Correos`; `PY` = `.venv\Scripts\python.exe`.
Scripts locales (en `archivo_local\`, fuera de git): `respaldo_consistente.py`, `localizar_mensaje.py`, `auditoria_piloto.py`,
`verificar_paquete_drive.py`.

## 0. Estado verificado (solo lectura)
- Coordinador T-55 y protecciones de la FASE 6B presentes: `tests.test_coordinacion` + `tests.test_coordinacion_operativa` = 68 pruebas OK.
- `PY -m anka.cli.work_jobs verificar-piloto` sobre la configuración real: `listo: false` solo por lo esperado (faltan carpeta, corte y
  mensaje de laboratorio); sin procesos de Anka en marcha, sin candados, latido del puente del 2026-10-16 (muerto).
- SQLite real: integridad `ok`, 2 trabajos (`conciliado`), 4 filas pendientes históricas (avisos de ChatGPT), recepción `normal`,
  último sondeo 2026-10-17 18:59.
- **No hay autoarranque de Anka** en Windows (ni tareas programadas ni clave `Run` de Anka): solo corre lo que se lance a mano.
- **Hallazgos a decidir antes de empezar:**
  1. `work_drive_enabled` está en **true** en la configuración real. Ese preparador (OCR/expedientes de Drive) arranca con el lanzador y
     actuaría sobre el correo ficticio. `docs/WORK_DRIVE_OCR.md` pide dejarlo apagado con V6. **Apágalo para el piloto.**
  2. `gmail_reception_enabled` no existe (apagado) y el receptor lleva ~1 día parado: el primer sondeo registrará **todo el correo
     llegado desde el 17/10 18:59** (recuperación automática de hasta 30 días) con `detected_at` = ahora, es decir, **posterior al corte**.
     Por eso el piloto exige alcance de laboratorio (un solo mensaje) y comprueba la hora de **llegada a Gmail**, no solo la de registro.
  3. La tarea de Work no se puede comprobar desde Python.

## 1. Cómo se garantiza que el mensaje ficticio es posterior al corte
1. Anota el instante `T0` (hora local con zona) **antes** de enviar: `Get-Date -Format "yyyy-MM-ddTHH:mm:sszzz"`. El corte
   `work_coordination_since` = `T0`.
2. Desde `91andresmunoz@gmail.com` envía a `ankacomercialia@gmail.com` un correo cuyo asunto lleve una marca única
   (`PRUEBA-6C-<4 letras al azar>`), con una solicitud de levantamiento declarada ficticia (datos ficticios, sin vigencia comercial).
3. Registra el correo con **una sola pasada** del receptor aislado (sin lanzador): `PY -m anka.cli.gmail_reception --once`
   (escribe en SQLite la cola de recepción; no escribe en Gmail).
4. Localiza el mensaje y prueba el corte: `PY -I archivo_local\localizar_mensaje.py --marca PRUEBA-6C-XXXX --desde <T0>`
   Debe dar **1 coincidencia**, `llegó DESPUÉS del corte: True` y código de salida 0. Copia de ahí `thread_id` y `message_id`.

## 2. Procedimiento con puntos de parada
Cada paso termina con una condición de **PARADA**: si no se cumple, no se avanza y se aplica la reversión indicada.

| Paso | Acción (autorización expresa) | Condición para continuar | Si falla → reversión |
|---|---|---|---|
| A | `PY -I archivo_local\respaldo_consistente.py antes-piloto-6c` y anota la línea base: 2 trabajos, 4 pendientes, nº de `quotes` | integridad `ok`, conteos idénticos | no continúes |
| B | Config: `work_drive_enabled=false`. Envía el correo (§1.2) y registra con `gmail_reception --once` (§1.3-1.4) | 1 coincidencia, posterior al corte, `pendiente_de_procesar`, elegible | no borres nada: no uses ese mensaje; envía otro con otra marca |
| C | Config: claves del §3 con `work_coordination_enabled=false`. `PY -m anka.cli.work_jobs verificar-piloto` | `"listo": true`, `"bloqueos": []`, `mensaje_de_laboratorio.elegible: true`, `despues_del_corte: true` | corrige la configuración; nada más se tocó |
| D | Confirma el paquete en Drive: `PY -I archivo_local\verificar_paquete_drive.py` | `TODO CORRECTO: True` (versión `e23a8366695b`, 11 archivos) | no crees la tarea Work; republica según `PILOTO_COLA_WORK.md` |
| E | `work_coordination_enabled=true` y **un ciclo manual**: `PY -m anka.cli.work_jobs coordinar --una-vez --confirmar` | informe `publicaciones: [{resultado: creado}]`; `PY -I archivo_local\auditoria_piloto.py --thread-id T --message-id M --esperado pendientes --trabajos-antes 2` → TODO SATISFACTORIO | `work_coordination_enabled=false`; la hoja queda en PENDIENTES (se deja, o se mueve a mano a CERRADOS) |
| F | **Arranca el lanzador** (`Iniciar Anka.bat`) | en ≤ 1 min el registro muestra ciclos y `verificar-piloto` no ve puente general (§4) | `work_coordination_enabled=false` y cierra el lanzador |
| G | **Crea la tarea Work recurrente** (§5) | la primera ejecución toma el único trabajo y deja **un** borrador | borra o pausa la tarea; ver §7 |
| H | Supervisión (§6) hasta ver `conciliado` + hoja en CERRADOS | `auditoria_piloto.py ... --esperado cerrados --trabajos-antes 2` → TODO SATISFACTORIO | ver §7 |
| I | Cierre, **en este orden**: (1) borra/pausa la tarea Work, (2) detén el lanzador y su árbol de procesos, (3) comprueba que no quedan procesos, (4) recién entonces `work_coordination_enabled=false` | `verificar-piloto` sin procesos; sin hojas en PENDIENTES | — |

## 3. Configuración exacta (`%LOCALAPPDATA%\AnkaCorreos\config.json`; se edita con el lanzador detenido)
```json
"active_mode": "chatgpt_work",
"work_drive_enabled": false,
"work_dispatch_enabled": false,
"gmail_reception_enabled": false,
"work_coordination_enabled": false,
"work_coordination_folder_id": "1IvNxAoIQmPN8o7_HUB8oqqidtA3asK2w",
"work_coordination_since": "<T0>",
"work_coordination_thread_id": "<thread_id>",
"work_coordination_message_id": "<message_id>",
"work_coordination_interval_seconds": 300,
"work_coordination_max_publish": 1,
"work_coordination_max_import": 1,
"work_coordination_max_archive": 2,
"work_coordination_alert_minutes": 30
```
- `gmail_reception_enabled` queda en **false**: la cola se alimentó con la pasada manual del §1. No habilites la recepción continua en el lanzador
  durante este piloto (evita dos receptores y un sondeo que traiga más correo).
- `work_coordination_enabled` pasa a `true` solo en el paso E.

## 4. Cómo se evita que el puente general compita
- Con `work_coordination_enabled=true` el lanzador **no arranca** `work_dashboard_sync` y detiene el que esté corriendo; `work_sync` general lanzado
  a mano sale con error; y el ciclo no importa si ve un latido vivo (`puente_general_activo`).
- Comprobaciones antes de F y durante el piloto:
  - `PY -m anka.cli.work_jobs verificar-piloto` → `procesos` sin `puente_general`, `candados.latido_del_puente` antiguo.
  - `Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -match 'work_dashboard_sync|anka.cli.work_sync|work_sync.py' } | Select ProcessId,CommandLine`
    debe estar vacío (una importación aislada con `--only-thread-id` sí está permitida).
- No existe autoarranque de Anka; no arranques `Iniciar Anka.bat` hasta el paso F.

## 5. Momento exacto de crear la tarea Work y de habilitar Python
La plataforma crea la tarea activa, así que **se crea al final de la preparación**:
1. Python se habilita primero, en dos tiempos: ciclo manual (paso E) y lanzador (paso F). Así la hoja del trabajo ya está en `PENDIENTES` y el coordinador
   ya está escuchando.
2. La tarea Work se crea **solo cuando** E y F pasaron (hoja única en PENDIENTES con su marca, paquete `e23a8366695b` verificado en Drive, `verificar-piloto` limpio y
   `work_coordination_alert_minutes=30`). Instrucciones de la tarea: `docs/TAREA_WORK_COLA.md` (un trabajo por ejecución; no mueve archivos; no envía).
3. Cadencia sugerida: cada 15–30 min; tras el piloto se borra/pausa. Antes de crearla Work no tiene nada que hacer y después de borrarla el coordinador sigue funcionando sin ella.

## 6. Supervisión y auditoría
- Registro: `Get-Content "$env:LOCALAPPDATA\AnkaCorreos\logs\anka.log" -Wait | Select-String "Coordinación Work"`.
  Normal: `publicaciones[creado=1]`, luego `importaciones[-]` (esperando), luego `importaciones[<job>=importado]`, luego `archivados[archivado=1]`.
- Estado: `PY -m anka.cli.work_jobs verificar-piloto` (`trabajos_por_estado`, `sin_respuesta`).
- Auditoría completa de solo lectura (SQLite, Drive y Gmail; sin asuntos ni cuerpos):
  `PY -I archivo_local\auditoria_piloto.py --thread-id T --message-id M --esperado pendientes|cerrados --trabajos-antes 2`
  Comprueba: integridad, un único trabajo, ningún evento duplicado, eventos esperados, un solo expediente, un único archivo con la marca en toda la cuenta de Drive y en
  la carpeta correcta, a lo sumo un borrador en el hilo y nada enviado.
- Señales de alarma: `coordinacion_sin_respuesta`, `coordinacion_importacion`, `coordinacion_requiere_persona`, `coordinacion_hoja_ausente`, `puente_general_activo`,
  `con_errores` en el informe.

## 7. Recuperación si Work no produce un borrador
1. **Esperar el plazo** (30 min tras publicar): llega el aviso `coordinacion_sin_respuesta` (una sola vez). No cambia el estado.
2. **Diagnóstico** (solo lectura): ¿corrió la tarea? (historial en Work). ¿La hoja sigue en PENDIENTES? (`auditoria_piloto.py --esperado pendientes`). ¿El paquete sigue en
   `e23a8366695b`? (`verificar_paquete_drive.py`). ¿Hay un borrador del hilo que no se importó (`importaciones` con `rechazado`/`ambiguo`)?
3. **Acciones**, de menor a mayor: (a) pide una ejecución manual de la tarea Work; (b) si Work se detuvo por el paquete, corrígelo y republica con autorización; (c) si hay dos
   borradores, borra el sobrante en Gmail a mano y deja que el ciclo importe; (d) si la hoja desapareció, el ciclo la recupera solo (máx. 3 veces); (e) si nada funciona,
   **desactiva** la coordinación y la tarea Work.
4. El trabajo queda `publicado` y no se cancela solo. Para repetir la prueba, usa un mensaje nuevo (otra marca, nuevo `T0`) y ajusta `thread_id`/`message_id`/`since`.

## 8. Reversión
- **Orden obligatorio (T-56, D-163):** primero detén el lanzador (`taskkill /PID <cmd del .bat> /T /F`, o Ctrl+C en su ventana) y comprueba que no quedan procesos de Anka; **solo después** cambia
  `work_coordination_enabled=false`. El puente general ya no arranca por apagar la coordinación (necesita `work_general_bridge_enabled=true`), pero no dependas de eso: el piloto 6C lo arrancó una vez.
- **Sin detener el lanzador (solo si `work_general_bridge_enabled` es `false` o no existe):** `work_coordination_enabled=false` → el lanzador deja de lanzar ciclos en 30 s; un ciclo en curso termina solo.
- **Work:** borrar/pausar la tarea (decisión del dueño; Python no la controla).
- **Datos:** todo lo que escribe el piloto es aditivo y de un solo mensaje (filas de cola/trabajo/expediente, una hoja de Drive y un borrador en Gmail). No hay nada
  que deshacer salvo borrar a mano el borrador y mover la hoja. Solo ante un daño real de la base: con todo detenido, restaurar `quotes.sqlite3.bak-antes-piloto-6c`
  (paso A) y reiniciar. Python no envía correos en ningún caso (R8).
- **Archivos que no se tocan:** ANKA 01, el contrato Work, el paquete de referencia, las reglas T-51/T-52.

## 9. Límites conocidos del piloto
- `work_coordination_since` filtra por la hora de **registro**; tras una pausa larga del receptor un correo antiguo recibe una hora de registro nueva. El alcance de un
  solo mensaje lo evita en este piloto; antes de operar sin alcance de laboratorio conviene filtrar también por la hora de llegada a Gmail (`internal_date`).
- Si el ciclo `coordinar --confirmar` manual se lanza mientras corre el lanzador, el candado lo detiene (`concurrente`).
- La auditoría `sin envíos registrados` revisa los eventos del caso; la comprobación decisiva de «nada enviado» es la de Gmail (etiqueta `SENT`).
