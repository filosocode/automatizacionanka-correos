# Diagnóstico operativo previo a la prueba integrada C1 (T-78) — SOLO LECTURA

Fecha de la lectura: 21/10/2026, entre las 11:50 y las 12:10 (hora de Colombia). Este documento **no autoriza** ninguna acción de las
listadas en §6. No se envió ni se creó nada, no se tocó Gmail ni Drive, no se modificó SQLite ni la configuración, y no se reinició ningún
proceso. La base se abrió con `mode=ro` y `query_only`. Solo se escribió, fuera del repositorio, un paquete local de referencia en el
scratchpad para comparar versiones (sin Drive).

## 1. Estado real y evidencia

| # | Elemento | Estado | Evidencia |
|---|---|---|---|
| 1 | Receptor Gmail | **En marcha** | `recepcion_control`: último intento y éxito 21/10 11:50:36, 0 errores consecutivos, `recovery_state=normal`, `history_sync_confirmed=1`. Token del receptor con alcance **solo lectura** (`gmail.readonly`) |
| 1 | Coordinador Python | **En marcha y activado** | `work_coordination_enabled=true`; candado `work_coordinacion.lock` tocado hoy 11:46; el lanzador (PID 73272/98500) y el dashboard (PID 41188) corren desde el 20/10 |
| 1 | Puente general | Detenido por diseño | Latido del 19/10 07:42; con la coordinación activa el puente general no corre (`puente_general_habilitado`) |
| 2 | Procesamiento de adjuntos (preparador) | **Apagado** | `work_drive_enabled=false`; `work_drive_status.json` del 16/10 (10 omitidos, 0 preparados) |
| 3 | Publicación en Drive | **Activa por la coordinación** | `work_dispatch_enabled=false`, pero la coordinación publica sola en `work_coordination_folder_id` (raíz con PENDIENTES/CERRADOS) |
| 4 | Cola | 17 mensajes pendientes de interpretación (15 anteriores al corte, 2 posteriores pero no elegibles); 8 trabajos, **todos `conciliado`**; ninguno `pendiente_de_publicar`, `publicando`, `publicado` ni `ambiguo` | consultas de solo lectura; `integrity_check = ok` |
| 4 | Corte | `work_coordination_since = 2026-10-20T16:31:06-05:00`; `elegibles_nuevos = 0` | `work_jobs verificar-piloto` |
| 5 | Deduplicación | Ver §3 | esquema SQLite y código |
| 6 | Contrato | PRECISO_V6.1.3, `contrato_work.schema.json` v1. Paquete local construido en el scratchpad: versión `1797370a19dd`, **igual** a la del último trabajo publicado (20/10 17:06) | `paquete_work construir/verificar` (sin Drive): correcto, 0 problemas. SHA-256 del prompt `41f515b9…888ff`. Lo realmente publicado en Drive: **NO_VERIFICABLE** sin leer Drive |
| 7 | Envíos automáticos | Ver §5 | código y alcances |
| — | Tarea recurrente de Work | **NO_VERIFICABLE** desde el computador | Indicio: 8 trabajos publicados y conciliados entre el 17 y el 20/10 (el último, publicado 17:06 y conciliado 19:01 del 20/10); 2 eventos `coordinacion_sin_respuesta` |
| — | Registro `anka.log` | **Discrepancia sin explicar** | Su última línea es del 20/10 21:29, pero la recepción registró actividad hoy 11:50 y el candado de coordinación se tocó a las 11:46 |
| — | Copia de seguridad | La última a Drive falló (`TimeoutError`, 20/10 15:14); existe la copia local | `last_backup.json` |

Configuración relevante (sin credenciales): `active_mode=chatgpt_work`, `gmail_reception_enabled=true`, `work_sync_account=ankacomercialia@gmail.com`,
`work_coordination_thread_id` y `work_coordination_message_id` **vacíos**.

## 2. Acceso al PDF

1. **Identificación.** El receptor lee el mensaje por la API de Gmail (`format=full`) y registra por cada adjunto: `filename`, `mime_type`,
   `size_bytes`, `gmail_attachment_id`, `status = disponible_en_gmail` y `storage_path = null` (`recepcion.py`).
2. **Qué se publica.** La hoja `.xlsx` del trabajo (hojas TRABAJO, ADJUNTOS_ESPERADOS y VERIFICACION) lleva identidad del mensaje y, por adjunto,
   nombre, tipo, tamaño e id de Gmail. **No publica el contenido del adjunto, ni su hash.** `referencia_del_mensaje` lo declara: «nunca el
   cuerpo ni el texto de los adjuntos».
3. **Hash.** Python **no registra ningún SHA-256 del adjunto** en ningún punto. La igualdad con `f47ed4da…` solo puede comprobarse descargando el adjunto
   de Gmail y calculándolo aparte (acción humana).
4. **Cómo lo lee Work.** Directamente desde Gmail (prompt §2): abre el hilo, lista los adjuntos con nombre y tamaño exacto, los abre con el lector
   de adjuntos y, si no es compatible o viene truncado, extrae el original del MIME en formato raw. Tiene prohibido usar visores o servicios externos
   y escribir en Drive. La hoja le sirve para comprobar identidad y adjuntos esperados.
5. **Verificación de nombre, tamaño e identidad (puente).** Por cada adjunto de Gmail debe haber un reporte de Work con `filename` y `size_bytes` iguales
   **y con `coverage` declarada**; si falta alguno o sobra uno desconocido, `reading_audit.verification = incompleta` y `analysis_complete` pasa a falso.
   Si Gmail no responde: `sin_verificar` y se reintenta. Además `_verify_origin` comprueba `message_id`/`thread_id` contra Gmail, que el origen no sea
   borrador ni enviado, que el remitente coincida con `client_email` y fija `received_at` desde `internalDate`.
6. **Archivo ausente, ilegible o irrecuperable.** El prompt prescribe `no_legible` o BLOQUEO (`activities=[]`, `analysis_complete=false`, motivo exacto) y
   nunca sustituir un archivo por otro. En Python, un adjunto sin lectura verificada deja la lectura `incompleta`.
7. **¿Lectura completa o solo disponibilidad?** **Solo disponibilidad y coherencia declarada.** `verificada` significa que cada adjunto de Gmail fue reportado
   con el nombre y tamaño correctos y una cobertura *declarada por Work*. La comprobación mecánica de que las cantidades, archivos, páginas y extractos
   citados existen en el texto del adjunto (`evidence_check`, T-38) depende de las instantáneas del **preparador, hoy apagado**: sin él,
   `comprobar_tabla` recibe `None` y **no contrasta las citas con el contenido del PDF**. Esa verificación recae en el evaluador inverso T-77 y en una persona.

## 3. Deduplicación (controles disponibles)

- Recepción: clave primaria `(account, thread_id, message_id)`.
- Trabajo: `job_id` determinista (`ANKAJOB-` + 16 hex de SHA-256 de cuenta, hilo, mensaje, tipo y versión) y `UNIQUE(account, thread_id, message_id, job_type,
  contract_version)`; crear dos veces devuelve el mismo trabajo (prueba `test_crear_dos_veces_devuelve_el_mismo_trabajo_sin_duplicar`).
- Drive: marca `appProperties.anka_job_id`; más de un archivo con la marca o un fallo de publicación deja el trabajo `ambiguo` y no se elige ni se borra.
- Reclamo del trabajo con `claim_token` y vencimiento; importación idempotente y reconciliación por mensaje (`test_second_pass_does_not_duplicate_the_proposal`).
- Borradores: el prompt exige crearlos **una sola vez** y un `message_id` ya analizado no se repite; el puente no retrocede a un mensaje anterior
  (`_should_replace`). No existe un control de Python que *impida* que Work cree dos borradores: solo se detectaría después. **Humano.**

## 4. Riesgo de interferencia (el hallazgo principal)

La coordinación está **activa y sin alcance de laboratorio**: `verificar-piloto` informa `listo=false` con el bloqueo «El piloto exige un solo mensaje:
faltan work_coordination_thread_id y work_coordination_message_id». Consecuencias para la prueba C1:

1. Cualquier correo ficticio **elegible** (T-51) que llegue después del corte será tomado por el ciclo (cada 300 s por defecto, 1 trabajo por ciclo), se
   creará el trabajo y se publicará en Drive **sin una autorización aparte** por cada paso. Hoy no se pueden separar «permitir el procesamiento por Python»
   y «publicar en Drive».
2. Los identificadores reales solo se conocen **después** de que llega el correo; para acotar el ciclo a ese mensaje habría que editar la configuración
   cuando ya puede haber empezado (carrera).
3. El buzón es el productivo: los correos de clientes reales conviven con el ficticio. No hay trabajos pendientes de Drive que Work pueda tomar por error
   (los 8 están conciliados), pero cualquier solicitud real elegible que llegue durante la prueba será publicada por el mismo ciclo.
4. La carpeta de coordinación es la real (`work_coordination_folder_id`): no hay una carpeta de laboratorio aparte.
5. El token principal tiene alcances `gmail.send`, `gmail.compose` y `drive` completos: el límite de envíos es de código, no de permisos.

## 5. Envíos automáticos: controles existentes

- Automáticos: el receptor es de solo lectura; Work tiene prohibido enviar (prompt) y deja borradores; el único envío automático de Python son los
  recordatorios R13 (`run_due_reminders`), y solo sobre casos que **una persona ya envió** y esperan al cliente, solo al contacto registrado y en horario
  hábil. Un caso ficticio nuevo no cumple esas condiciones, pero **no es nulo el riesgo** sobre casos reales existentes (independiente de la prueba).
- Humanos: cualquier otro envío exige una acción en el dashboard (con reserva en `mail_operations`, 16 envíos confirmados hasta el 20/10 06:28);
  el borrador que deja Work lo revisa una persona (R8).
- No verificable: que la tarea de Work no envíe (se apoya solo en su prompt y en que la cuenta de Work no tenga permiso de envío).

## 6. Procedimiento exacto para una ejecución ficticia de C1 (NO EJECUTAR)

Datos: documento `laboratorio/banco_documental/documentos/solicitud_01.pdf`, **4913 bytes**, SHA-256
`f47ed4da974e982b09954bfd1b71472b0fefa5859309d7ab1e24c924c92bd844` (verificado hoy contra el archivo). Etiqueta de la prueba `PI-T78-AAAAMMDD-01`
(la fija quien autorice). Ningún identificador de Gmail, Drive ni trabajo existe todavía; se anotan de la salida real de cada paso.

**P0 — Preparación local (sin autorización, sin red).** `construir --verificar`, `referencias_v2 --verificar` y las pruebas del laboratorio; repetir
`work_jobs verificar-piloto` y guardar su salida. Respaldo local de `quotes.sqlite3` hecho por una persona. Decidir la carpeta de pruebas de Drive.

**P1 — Dejar la coordinación sin efecto sobre la prueba** *(requiere autorización A1)*. Opción recomendada: `work_coordination_enabled=false`
(el lanzador lo lee en ≤30 s; no se reinicia nada) durante la prueba, y anotar la hora. Alternativa: no hacerlo y aceptar que el ciclo publicará solo
(no cumple las autorizaciones separadas).

**P2 — Enviar un único correo ficticio** *(A2)* desde una dirección controlada distinta de `ankacomercialia@gmail.com` (si no, queda como
`mensaje_propio_excluido`), con asunto `[PI-T78-…] C1` y el PDF adjunto. Registrar `thread_id`, `message_id`, hora, y descargar el adjunto desde Gmail
para calcular su SHA-256 y tamaño (deben ser `f47ed4da…` y 4913). **Parada** si difieren.

**P3 — Recepción por Python** *(A3, solo lectura de Gmail por el receptor ya activo)*. Comprobar en `recepcion_mensajes` (solo lectura) una única fila con
ese par de ids, `attachments_json` con el nombre, 4913 bytes y `disponible_en_gmail`. `work_jobs elegibilidad` (solo lectura) debe decir elegible; si no,
**no** forzar sin una autorización específica.

**P4 — Crear y exportar el trabajo, sin Drive** *(A4)*. `ANKA_DATA_DIR` temporal **no sirve** aquí porque la fila de recepción está en la base real;
por eso `work_jobs crear` escribe en la base operativa: requiere autorización expresa y una copia previa. `work_jobs exportar` a un archivo local y revisar
la hoja: ids, `ref_sha256` y un único adjunto esperado. Crear dos veces debe devolver el mismo `job_id`.

**P5 — Publicar como máximo un trabajo en Drive** *(A5)*: `work_jobs publicar … --confirmar-drive` hacia PENDIENTES de la carpeta autorizada. Esperado: un archivo con
`anka_job_id`; `ambiguo` o `error` → parar.

**P6 — Work** *(A6)*: una ejecución autorizada (o esperar la programada, sin tocarla) que lea un solo trabajo y deje **un** borrador interno sin enviarlo.
Antes y después: recuento de borradores y de la carpeta Enviados.

**P7 — Conservar el retorno** *(A7)*: copiar byte a byte el bloque entre `[ANKA_DASHBOARD_JSON_V1]` y `[/ANKA_DASHBOARD_JSON_V1]`, SHA-256, sin editarlo.

**P8 — Evaluar** (sin autorización, solo local): `python -m laboratorio.banco_documental.evaluador_inverso <retorno> referencias_v2/C1.referencia.v2.json <thread_id> <message_id> solicitud_01.pdf`
con los ids **autorizados** de P2. Esperado: `EVALUADO`; las cinco actividades de la referencia v2 (C1-A1 18,5 ha; C1-A2 2,350 km; C1-A3 12 puntos; C1-A4 7 construcciones;
C1-A5 3 quebradas), contradicciones, faltantes, fuentes sin inconsistencias y 0 críticos; revisar a mano `pistas_para_revision_humana`, `perdidas` y `no_comparables`.

**P9 — Importar y conciliar** *(A8)*: la importación del único mensaje (`work_sync --once --only-thread-id --only-message-id`, o el ciclo de coordinación si
se reactiva con alcance de laboratorio). Comprobar que cantidades y unidades quedan iguales a las del retorno (el importador normaliza p. ej. `m2` → `m²`), que
el trabajo pasa a `conciliado` una sola vez y que una segunda importación no cambia nada.

**P10 — Cierre**: recuento final de envíos (debe ser 0 nuevos), de borradores (1), de archivos en Drive (1), restaurar `work_coordination_enabled=true` *(A9)* y archivar las
evidencias con su SHA-256.

## 7. Autorizaciones independientes (por escrito, una por acción, con el identificador de la prueba)

| Id | Acción | Efecto externo |
|---|---|---|
| A1 | Desactivar temporalmente `work_coordination_enabled` (y restaurarlo al final, A9) | Cambia la configuración operativa; detiene el procesamiento de solicitudes reales mientras tanto |
| A2 | Enviar el correo ficticio al buzón productivo | Gmail |
| A3 | Permitir que el receptor registre ese mensaje | SQLite operativo (ya activo; solo conviene confirmarlo) |
| A4 | Crear el trabajo (`work_jobs crear`) en la base operativa | SQLite operativo |
| A5 | Publicar la hoja en Drive (`--confirmar-drive`) | Drive |
| A6 | Ejecutar o esperar la tarea Work para ese trabajo | Gmail (lectura) y borrador |
| A7 | Que Work cree el borrador interno | Gmail |
| A8 | Importar y reconciliar el resultado | SQLite operativo |
| A9 | Restaurar la configuración | Configuración operativa |

Este diagnóstico no concede ninguna de ellas.

## 8. Condiciones de detención y tipo de control

| Condición | Control |
|---|---|
| Identidad del mensaje o del adjunto incorrecta (`thread_id`/`message_id`, nombre, tamaño) | Automático parcial (puente y evaluador T-77); confirmación humana en P2 |
| Hash descargado distinto de `f47ed4da…` | **Humano** (Python no guarda hash) |
| Acceso a un correo no autorizado | Humano: nadie debe abrir correos reales; el receptor ya los registra |
| Más de un trabajo para el mensaje, o `ambiguo` | Automático (UNIQUE, marca de Drive) + parada humana |
| Más de un borrador | **Humano** (no hay bloqueo de Python) |
| Retorno inválido o identidad distinta | Automático (validador T-75 y evaluador T-77) |
| Cantidad o unidad cambiada en la importación | Automático parcial (`comprobar_contra_contrato`); comparación final humana |
| Lectura documental no verificable | **Humano** (con el preparador apagado `evidence_check` no corre) |
| Cualquier envío automático | Humano: recuento de Enviados; Python no lo impide a nivel de permisos |
| Escritura en Drive fuera del alcance | Humano: inspección de la carpeta antes y después |

## 9. Bloqueos y riesgos pendientes

1. **Coordinación activa y sin alcance de laboratorio** (hallazgo principal, §4).
2. Estado de la tarea de Work y versión efectivamente publicada en Drive: **NO_VERIFICABLE**.
3. Python no registra ni verifica el hash del adjunto; la verificación de citas contra el contenido está apagada.
4. Buzón y carpeta de Drive productivos compartidos con trabajo real.
5. `anka.log` no refleja la actividad de hoy (causa desconocida).
6. La última copia de seguridad a Drive falló (existe la local).
7. Dos mensajes posteriores al corte siguen pendientes de interpretación: no son elegibles hoy, pero conviene que una persona confirme que no son solicitudes reales.
8. Un solo caso no mide tasas de acierto; C1 ya fue evaluado antes con otro canal.

## 10. Dictamen

**BLOQUEADO** para ejecutar la prueba tal como está configurado el sistema hoy, porque enviar el correo ficticio activaría sin autorizaciones separadas la
creación y la publicación en Drive. No hay un bloqueo técnico irresoluble: se levanta con las autorizaciones A1 (y A9) y con un acuerdo explícito sobre el
buzón y la carpeta.

## 11. Primer paso seguro recomendado

Sin tocar nada operativo: que una persona de Anka decida por escrito (a) si acepta pausar la coordinación durante la prueba (A1) o prefiere preparar una
carpeta de Drive y un buzón de pruebas aparte, y (b) quién descargará el adjunto para calcular el hash en P2. Mientras tanto, solo P0 (verificaciones locales).
