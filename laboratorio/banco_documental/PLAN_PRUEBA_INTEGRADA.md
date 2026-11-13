# Plan de prueba integrada (T-75, actualizado en T-76, T-77, T-80, T-81 y T-86) — DISEÑADO, NO EJECUTADO

> **T-86:** el procedimiento vigente es §11. §0.3, §0.4 y §0.6 y las notas de §9 y §10 sobre «no desplegada» quedan como historia.

Cadena que se quiere probar, con un solo hilo ficticio por documento:
**Gmail → Python (recepción y cola) → Drive (hoja en `PENDIENTES`) → Work → borrador interno → Python (puente, concilia)
→ seguimiento.** Nada de esto se ha ejecutado ni se ejecuta sin autorización escrita por etapa.

> Estado de partida (verificado el 21/10/2026, al inicio de T-75): `work_dispatch_enabled=false`, `work_drive_enabled=false`.
> La prueba **no** los cambia por su cuenta: quien autorice decide si se activan, solo en la configuración de la prueba.

## 0. Condiciones previas (todas obligatorias)

1. Autorización escrita de una persona de Anka para esta prueba y para cada escritura externa (Gmail, Drive).
2. Datos 100 % ficticios: los tres documentos del banco (`solicitud_01.pdf`, `tdr_02.pdf`, `cantidades_03.xlsx`) y dos
   negativos (ver 2). Ningún dato de cliente, ningún correo de cliente real como destinatario.
3. Aislamiento de datos de Anka: `set ANKA_DATA_DIR=<carpeta temporal>` en cada orden de Python (base y evidencia aparte), más
   copia de seguridad de la base comercial antes de empezar. La base operativa no se abre.
4. Una carpeta raíz de laboratorio en Drive, distinta de las comerciales (su id lo aporta quien autoriza).
5. Una cuenta de Gmail de pruebas (la autorizada para el puente, `work_sync_account`) y un remitente ficticio controlado.
6. La tarea recurrente de Work de producción **no se toca**: la prueba usa una ejecución manual (un trabajo por ejecución).
7. `PRECISO_V6.1.3` sin cambios; se comprueba su SHA-256 contra `manifest.json` del paquete antes y después.

## 1. Identificadores de prueba (convención; ningún ID de Gmail ni trabajo se inventa)

| Dato | Convención | Cuándo se conoce |
|---|---|---|
| Identificador de la prueba | `PI-T75-AAAAMMDD-NN` (la fecha y el número los fija quien autoriza) | al autorizar |
| Etiqueta de cada hilo | asunto `[PI-T75-…] C1`, `[PI-T75-…] C2`, `[PI-T75-…] C3`, `[PI-T75-…] N1`, `[PI-T75-…] N2` | al preparar |
| `thread_id`, `message_id` | **los asigna Gmail** al recibir el mensaje; se anotan del resultado real, nunca antes | tras la etapa E1 |
| `job_id` | `ANKAJOB-` + 16 hex, **calculado por Python** (`work_jobs crear`); se anota de su salida | tras la etapa E3 |
| SHA-256 de cada adjunto | del manifiesto del banco (`documentos/manifiesto_documentos.json`) | ya conocido |

Casos: **C1** PDF digital, **C2** PDF escaneado, **C3** Excel (los del banco); **N1** adjunto ilegible (el PDF C1 truncado
al 40 % de sus bytes, generado por un script de laboratorio, con su SHA-256 anotado) y **N2** respuesta de Work inválida
(se simula entregando al puente un retorno con comillas sin escapar; no depende de que Work falle).

## 2. Etapas, evidencias y criterios (un caso por vez; detenerse al primer fallo de una condición de parada)

**E0 — Preparación local (sin red).** `construir --verificar` y `referencias_v2 --verificar` sin diferencias; SHA-256 de los
tres documentos igual al manifiesto; validador y comparador v2 con sus pruebas verdes; `check.bat` completo verde.
Evidencia: salida de esos comandos. Aprobado si todo coincide.

**E1 — Gmail (autorizado).** Un solo correo ficticio por caso, desde el remitente de prueba, con el adjunto del caso.
Evidencia: `thread_id`, `message_id`, nombre, tipo, tamaño y SHA-256 del adjunto tal como queda en Gmail (descarga de
verificación). Aprobado si el hash del adjunto en Gmail es igual al del banco.

**E2 — Recepción en Python (`ANKA_DATA_DIR` temporal).** El receptor registra el mensaje (`recepcion_mensajes`) y crea el
expediente preliminar. Evidencia: una fila por mensaje, estado pendiente, adjuntos solo con metadatos. Aprobado si hay
**exactamente una** fila por mensaje y ningún texto de adjunto leído por Python.

**E3 — Trabajo y hoja.** `work_jobs elegibilidad`, `crear`, `exportar` (hoja local) y revisión de la hoja sin Drive.
Evidencia: `job_id`, hoja con `thread_id`, `message_id`, `ref_sha256` y la lista de adjuntos esperados; **sin** cuerpo ni
contenido de adjuntos. Aprobado si crear dos veces devuelve el mismo `job_id`.

**E4 — Publicación en Drive (autorizada, `--confirmar-drive`).** `work_jobs despachar` o `publicar` hacia `PENDIENTES` de la
carpeta de laboratorio. Evidencia: resultado (`creado`/`adoptado`…), un único archivo con `appProperties.anka_job_id`.
Aprobado si hay un solo archivo por `job_id`. **Parada** si el resultado es `ambiguo` o `error` (no borrar ni elegir).

**E5 — Work (ejecución manual, una por trabajo).** Se da a Work el `job_id`; Work verifica la hoja contra Gmail, abre el
mensaje y **sus adjuntos**, y deja un único borrador `[ANKA_DASHBOARD_JSON_V1]`. Evidencia: captura del borrador sin
enviar, `draft_id`, y en el JSON los campos `attachments` y `reading_audit`. Aprobado si:
  - **acceso efectivo a los adjuntos:** cada adjunto del mensaje aparece en `attachments` con estado de lectura coherente
    con lo que Work realmente pudo abrir (no «leído» si no lo abrió);
  - **un solo borrador** con ese `message_id`; **ningún correo enviado** (carpeta Enviados y etiqueta `SENT` sin
    mensajes nuevos), y `DRIVE_PREFLIGHT` sin escrituras en Drive.

**E6 — Retorno válido y contrato.** El JSON del borrador pasa el validador estructural (sintaxis → esquema → contenido) y
`spec/contrato_work.schema.json` mediante el importador **existente** (solo lectura de lo importado en la base temporal).
Evidencia: informe del validador (`estado`, SHA-256) y salida del puente. Aprobado si `VALIDA` o
`VALIDA_CON_ADVERTENCIAS` **y** el puente lo acepta. Una respuesta inválida va a cuarentena (ver N2) y no se importa.

**E7 — Fidelidad al documento.** Se mapea lo importado a las actividades del caso y se evalúa con el comparador v2 y con
la revisión humana. Evidencia: informe v2 por caso y, para C2, `revision_visual_humana.md` rellenada por **otra persona**.
Aprobado si: 0 errores críticos automáticos; valor y unidad literales de todas las actividades; cada origen (`fuente`)
existe en el documento (`verificar_fuentes`); contradicciones y faltantes bloqueantes de la referencia reconocidos.
*Nota (T-77):* E7 se ejecuta con el **evaluador inverso** (`evaluador_inverso.evaluar_retorno`), que convierte el retorno de Work
(contrato) a la estructura del banco y la compara con la referencia v2; ver §7. El mapeador de T-76 va en la dirección contraria y solo
sirve para probar el esquema con datos del banco.

**E8 — Importación y duplicados.** Una sola importación (`work_sync --once --only-thread-id … --only-message-id …`) y una
segunda idéntica. Evidencia: el trabajo pasa a `conciliado`, un evento `trabajo_conciliado`, `PRAGMA integrity_check = ok`.
Aprobado si la segunda importación **no cambia nada** (sin duplicar actividades, borradores ni eventos).

**E9 — Seguimiento.** Se responde (desde el remitente de prueba) al mismo hilo con una aclaración ficticia (p. ej. la
cantidad definitiva). Evidencia: el seguimiento se asocia al hilo y se integra sin duplicar, y todo lo que salga al cliente
queda como borrador sin enviar (R8). Aprobado si no hay envíos automáticos y no se pierden actividades previas.

**N1 — Archivo ilegible.** Adjuntar el PDF truncado. Aprobado si Work lo **declara no legible** (`attachments.status` y
`reading_audit`), no inventa cantidades, y el borrador pide el archivo al cliente.

**N2 — Respuesta inválida.** Entregar al puente/validador el retorno con comillas sin escapar. Aprobado si queda en
`cuarentena/` con el error exacto (línea, columna), los bytes originales se conservan, **no** se importa nada y no hay
reparación automática.

## 3. Criterios globales de aprobación (todos)

1. E0 a E9 aprobadas para C1, C2 y C3, y N1 y N2 aprobadas.
2. 0 correos enviados, 0 borradores adicionales, 0 escrituras de Work en Drive, 0 duplicados.
3. 0 errores críticos automáticos en el comparador v2 y 0 errores críticos en la revisión humana.
4. Todas las evidencias archivadas con su SHA-256 y sin modificar.
5. Ninguna escritura fuera de `ANKA_DATA_DIR` temporal, la carpeta de laboratorio de Drive y la cuenta de pruebas.

## 4. Condiciones de detención (parar y dejar constancia; no reintentar a ciegas)

- Un correo sale hacia una dirección que no sea la de prueba, o aparece un envío no previsto.
- Dos borradores para el mismo `message_id`, o dos archivos para el mismo `job_id` (`ambiguo`).
- Work escribe, mueve o borra algo en Drive, o intenta usar un servicio externo para abrir un adjunto.
- Una respuesta inválida se importa, o algo se repara silenciosamente.
- El hash del adjunto en Gmail no coincide con el del banco.
- Cualquier dato que no sea ficticio aparece en la prueba.
- `PRAGMA integrity_check` distinto de `ok`, o el estado de la base comercial cambia.
- Falla la autorización de Google (no se abre el navegador solo: D-27) o la cuota de Gmail/Drive.

## 5. Riesgos y limitaciones de esta prueba

- Es **una** ejecución por caso: detecta fallos, no mide tasas de acierto.
- Work podría no abrir los adjuntos de Gmail (hay riesgo conocido en el piloto de la cola): E5 lo mide, no lo asume.
- El formato de extracción del banco difiere del contrato real. T-76 construyó el mapeador extracción → contrato y T-77 el
  evaluador inverso contrato → banco; ninguno ha visto aún un retorno real de Work (ver §7).
- La revisión visual de C2 la debe hacer una persona distinta de quien preparó el banco.
- Las referencias v2 se diseñaron tras ver las respuestas de la fase de laboratorio: no son una vara ciega.
- Una prueba con Gmail y Drive reales tiene coste y efectos externos (cuota, historial): por eso cada escritura se autoriza.

## 6. Decisión de integración (T-76): qué puede verificarse y qué no

El mapeador es de laboratorio, puro y sin red; **no** se conecta al importador ni sustituye el análisis de PRECISO_V6.1.3. Con él, y
con una carga de contexto ficticia (`thread_id` y `message_id` de PRUEBA, no de Gmail), ya se comprueba contra el esquema real sin
escribir en Gmail ni en Drive (`comprobar_contra_contrato`: esquema, identidad, `normalizar_analisis` y normalización de cantidades).

### Verificable automáticamente (sin autorización, sin red)
| Etapa | Qué se verifica solo | Con qué |
|---|---|---|
| E0 | Banco, referencias v2 y evidencias intactos (hashes) | `construir --verificar`, `referencias_v2 --verificar`, pruebas T-71/T-75/T-76 |
| E6 | Sintaxis, esquema y contenido de una respuesta; rechazo y cuarentena sin reparar | `validador` |
| E6 | El retorno cumple `contrato_work.schema.json`, la identidad y la tolerancia del puente (en seco, sobre una copia) | `mapeador.comprobar_contra_contrato` |
| E6/E7 | Cantidades, unidades y fuentes literales al pasar de extracción a contrato; el importador cambia la unidad (p. ej. `m2` → `m²`) | `diferencias_del_importador` |
| E7 | Contradicciones y faltantes nunca quedan `confirmado`; lo visual queda pendiente; las hojas ocultas no pasan a la tabla | pruebas de T-76 |
| N2 | Respuesta inválida: cuarentena con error exacto y sin importar | `validador.procesar` |

### Requiere revisión humana (el mapeador no la sustituye)
- Que lo leído coincida con el documento: interpretación visual de C2 (persona distinta del creador del banco), criterios técnicos
  opinables, preguntas no previstas y calidad de las preguntas al cliente.
- El `estado` de cada fila: el mapeador solo distingue «pendiente» de «confirmado» por la ausencia de incidencias del documento; que un
  dato esté de verdad confirmado lo decide una persona.
- Todo lo listado en `no_mapeado` (preguntas, revisiones internas, valores históricos, datos auxiliares, evidencia visual detallada).
- La sección de estado y la tabla del borrador real (E5), y el contenido de cualquier correo al cliente (R8).

### Campos del contrato que el mapeador NO puede producir (los debe aportar Work, nunca inventarse)
`servicio_tecnica`, `ubicacion`, `plazo_restriccion`; `question_audit` (motivo y fuentes revisadas); `missing_data`;
`evidence_register`; `attachments[].evidence[].excerpt` (cita literal); `attachments[].read_via`; `reading_audit`; `DRIVE_PREFLIGHT`;
`classification`; `analysis_complete=true`. Por eso su salida sirve para comprobar estructura y literalidad, **no** para generar un
retorno operativo.

### Evidencias que hay que recopilar en una prueba real (además de las de §2)
1. Retorno de Work **byte a byte** con su SHA-256 (nunca reconstruido ni copiado a mano).
2. Informe del validador y, si se rechaza, el archivo de cuarentena con el error exacto.
3. Salida de `comprobar_contra_contrato` (esquema, rechazo, correcciones, avisos de cobertura, diferencias del importador).
4. Lista de lo que Work **omitió** frente a lo que el documento trae (lo que el mapeador declararía `no_mapeado`).
5. `attachments[].filename` y `size_bytes` frente a Gmail (el puente los compara); fuente citada de cada fila frente al documento.
6. Captura del borrador sin enviar y recuento de borradores y de enviados antes y después.

### Autorización expresa antes de escribir (por escrito, una por acción, con el identificador de la prueba)
- Enviar el correo ficticio a la cuenta de pruebas (E1) y cualquier respuesta de seguimiento (E9).
- Publicar una hoja en Drive (E4, `--confirmar-drive`) y activar `work_drive_enabled` o `work_dispatch_enabled` aunque sea en la
  configuración de la prueba.
- Que Work ejecute su tarea (E5, una vez por trabajo) y que deje un borrador en Gmail.
- Ejecutar `work_sync` contra la cuenta real y conciliar (E8), aunque sea con `ANKA_DATA_DIR` temporal.
- Ninguna de ellas está concedida por haberse construido el mapeador.

### Condiciones que obligan a detener la ejecución (se suman a §4)
- El mapeador o el validador devuelven `MAPEO_NO_POSIBLE` o `RECHAZADO_ANTES_DEL_MAPEO` y alguien propone «repararlo a mano» para continuar.
- Una fila pasa a `confirmado` teniendo el documento contradicciones, lectura visual sin validar o limitaciones.
- Un valor de una hoja oculta, histórica o marcada no vigente aparece en la tabla de actividades.
- El importador cambia una cantidad (no solo la forma de la unidad) respecto de la fuente.
- `comprobar_contra_contrato` informa un `rechazo` sobre un retorno que Work declaró válido.
- El evaluador inverso devuelve un estado `DETENIDO_*` y alguien propone evaluar «a ojo» o corregir el retorno para continuar.

## 7. E7 con el evaluador inverso (T-77)

El evaluador es de laboratorio, puro y sin red (`evaluador_inverso.py`). Sus pruebas usan retornos **sintéticos** (`retorno_sintetico`
en `tests/test_evaluador_inverso.py`, armados desde la referencia v2): demuestran que el mecanismo funciona, **no** el desempeño de
Work. Con un retorno real se pasa `origen_del_retorno="work_real"`; con cualquier otro valor el informe aclara que no describe a Work.

### Cómo se usa en E7
1. Se guardan los bytes exactos del borrador (bloque entre `[ANKA_DASHBOARD_JSON_V1]` y `[/ANKA_DASHBOARD_JSON_V1]`, sin tocar una coma)
   y se calcula su SHA-256. Extraer el bloque es un paso manual y verificable, no una reparación.
2. `python -m laboratorio.banco_documental.evaluador_inverso <retorno> <referencia_v2.json> <thread_id> <message_id> <documento>` con los
   identificadores **autorizados** (los de E1), no los que diga el retorno.
3. Si el estado no es `EVALUADO` la prueba de ese caso se detiene (ver abajo); no se corrige el retorno para seguir.
4. Con `EVALUADO` se revisan, en este orden: `fuentes_inconsistentes`, `errores_criticos`, `actividades_descartadas` y
   `actividades_duplicadas`, `contradicciones_conservadas`, `evidencia_visual_declarada`, `pistas_para_revision_humana`, `perdidas`,
   `limitaciones` y `no_comparables`. Se archiva el informe completo con el SHA-256 del retorno.

### Qué se recupera de Gmail y de Drive (solo con autorización; el evaluador no lo consulta)
- **Gmail:** `thread_id` y `message_id` reales del correo de prueba; nombre y tamaño exacto de cada adjunto y su SHA-256 (descarga de
  verificación); el borrador y su contenido; recuento de borradores y de enviados antes y después.
- **Drive:** la hoja del trabajo y su `job_id`, y la ubicación en `PENDIENTES` / `CERRADOS`, para confirmar que se publicó una sola vez.
- **No se recupera de ningún sitio:** las respuestas de Work no se reconstruyen ni se completan; lo que falte se declara.

### Controles que siguen siendo humanos
- Que cada cantidad coincida con el documento (el evaluador solo compara contra la referencia y verifica que la fuente exista).
- La lectura visual de C2 (persona distinta del creador del banco) y la certeza de cada contradicción.
- Todo lo que cae en `pistas_para_revision_humana`, `perdidas`, `no_comparables` y los hallazgos de verificación `humana`.
- Restricciones y faltantes que Work puso en campos sin equivalente (`fields`, `missing_data`, `evidence_register`).
- El estado de cada fila (`estado_work`) y cualquier correo al cliente (R8).

### Acciones que requieren autorización expresa
Las mismas de §6 (Gmail, Drive, tarea de Work, interruptores, `work_sync`). Ejecutar el evaluador **no** la requiere, pero sí traer los
bytes del retorno desde Gmail. Tener el evaluador no autoriza ninguna prueba.

### Condiciones que detienen la prueba (se suman a §4 y §6)
- Estado `DETENIDO_RETORNO_INVALIDO`, `DETENIDO_IDENTIDAD` o `DETENIDO_CLASIFICACION` en cualquier caso.
- El `thread_id` o `message_id` del retorno no es el autorizado (posible análisis de otro correo).
- `fuentes_inconsistentes` no vacío (cita una hoja, celda o página que no existe, o un valor distinto del guardado).
- Un valor de una hoja histórica u oculta, de otro archivo o retirado aparece entre las actividades vigentes del retorno.
- El informe se presenta como desempeño de Work sin ser `work_real`, o se compara un retorno corregido a mano.

### Límites conocidos del evaluador
Las restricciones solo se recuperan de `plazo_restriccion`; los faltantes, solo de `question_audit`; `servicio_tecnica` y `ubicacion`
se pierden; una fuente con un localizador no estructurable (p. ej. «numeral 3») se conserva literal pero no se compara. Una hoja no vigente
solo se excluye si se declara al llamar (`hojas_no_vigentes`); el contrato no la identifica.


## 8. Recuperación y restauración (T-80; sustituye lo dicho sobre el tope de pausa y los abortos en `DISENO_AISLAMIENTO_T79.md`)

Fundamento y pruebas en `DISENO_RECUPERACION_T80.md` y `tests/test_recuperacion_t80.py`. **No concede autorizaciones.**

**Regla general: «cancelar» después de recibir el correo no existe; se continúa hacia adelante.** El código actual no tiene orden para
descartar un mensaje ni para cancelar un trabajo. Por eso, si la prueba se interrumpe con el ficticio ya registrado, se le crea y publica
su trabajo (`work_jobs despachar` de ese mensaje) y solo entonces se restaura la coordinación. El ficticio pasa a ser un trabajo más.

### 8.1 Duración de la pausa
- La pausa solo cubre del paso 1 al paso 8 (hasta que el trabajo del ficticio esté **publicado**); no depende de que Work termine. Objetivo: ≤ 30 min; tope duro propuesto: 60 min (lo fija quien autorice).
- Antes de enviar el correo (pasos 1 a 3): si se vence el tope, se restaura y se cancela la prueba sin residuos.
- Después de enviarlo: el tope **no** autoriza a restaurar mientras el ficticio no tenga trabajo. La pausa se mantiene hasta el paso 8; si el receptor no lo registra (Gmail caído), decide una persona (ver 8.6).
- Tras el paso 8 se restaura aunque Work no haya respondido: el importador lo concilia cuando llegue el borrador.

### 8.2 Reglas para restaurar `work_coordination_enabled` (todas)
1. El ficticio tiene trabajo en `publicado` o `conciliado`; ningún trabajo en `pendiente_de_publicar`/`publicando` ni con intentos agotados.
2. La fila del ficticio no está pendiente sin trabajo (revisar `work_jobs elegibilidad`).
3. El candado `work_coordinacion.lock` está libre y `config.json` es idéntico a la copia salvo la clave.
4. PENDIENTES contiene exactamente la hoja del ficticio (y las que ya estuvieran); sin carpetas ni archivos ambiguos.
5. Se anotó el conjunto N de reales pendientes y elegibles (id, hora de detección) y el recuento de trabajos por estado.
6. Quien restaura es quien autorizó A9; se restaura **una vez** y no se cambia `work_coordination_since`.

### 8.3 Mensaje ficticio pendiente (sin trabajo)
Preferido: continuar hacia adelante (8.0). Alternativa excepcional, solo con A10 explícita: adelantar `work_coordination_since` un segundo
después de la detección del ficticio. Excluye también a los reales detectados antes (siguen pendientes: se despachan uno a uno a mano, sin
mirar el corte) y no sirve si el ficticio ya tiene trabajo. Nunca se edita SQLite ni se mezclan ambas vías.

### 8.4 Continuidad de los reales
Los reales siguen registrándose durante la pausa. Tras restaurar se publican de uno en uno (`max_publicar`=1) por orden de detección, un
ciclo (≈5 min) cada uno: el tiempo de vaciado es N × intervalo. Comprobar cada ciclo que el recuento sube de uno en uno, sin duplicados.

### 8.5 Evidencias previas a restaurar y detención
Instantánea (hash de config, recuentos de mensajes/trabajos/eventos, PENDIENTES/CERRADOS) antes y después de cada acción. Detener ante:
trabajo del ficticio ausente o pendiente, más de una hoja o trabajo por mensaje, `ambiguo`, `error` o `agotado`, borradores repetidos, un
envío, candado ocupado, config distinta de la copia, un real que no avanza en dos ciclos.

### 8.6 Fallos de Gmail o Drive
- Drive al publicar: el trabajo queda `pendiente_de_publicar`, se reintenta; al quinto fallo queda **agotado** y ni el ciclo ni el despacho lo toman: intervención humana (no restaurar hasta resolverlo).
- Drive: hoja borrada de un trabajo publicado → Python la recrea (hasta 3 veces y luego avisa); borrarla no cancela.
- Gmail al importar: fallos con retroceso; al quinto el trabajo queda para una persona. No afecta a la publicación.
- Gmail antes de registrar el ficticio: no hay control posible; la pausa se mantiene y decide una persona (esperar, o abandonar sabiendo que el ficticio se publicará solo al restaurar). Esto es un riesgo residual.
- Cada recuperación se anota en un registro de laboratorio (fecha, paso, instantánea previa y posterior, resultado) hecho por la persona; Python ya deja `recepcion_eventos` y `last_error`.

### 8.7 Autorizaciones adicionales
A10 (solo excepcional): adelantar `work_coordination_since`. A11: despachar a mano reales anteriores al nuevo corte. Las A1 a A9 de T-79 siguen igual.

## 9. Cancelación con lápida (T-81; refina §8 si algún día existe la orden en producción)

> **Actualización T-86 (21/10/2026):** lo que sigue se conserva como estaba escrito en T-81. La orden `work_jobs cancelar` **ya está
> desplegada** (T-83, 19:01:29 del 21/10/2026). Lo vigente es §11.

Hoy la orden **no existe en producción**: `laboratorio/banco_documental/cancelacion.py` es una referencia probada solo con bases temporales
(`tests/test_cancelacion_t81.py`, 34 pruebas) y no se usa en la prueba real. Detalle en `DISENO_CANCELACION_T81.md`.

Lo que cambiaría en el protocolo **si** se integra la orden (futura, con su propia autorización y despliegue):
- Cancelar tras recibir el correo y antes de crear el trabajo deja de ser «continuar hacia adelante»: se cancela con la lápida y se restaura sin publicar el ficticio.
- La cancelación **preventiva** (antes de que el receptor registre el mensaje) cubre la ventana «enviado pero no registrado» de §8.6, con la verificación posterior del asunto.
- Un trabajo ya publicado **no** se cancela con la orden: sigue exigiendo a una persona (hoja en Drive, Work en curso).
- Un pendiente con intentos fallidos se cancela, pero hay que revisar PENDIENTES por si quedó una hoja marcada sin anotar.
- Nueva comprobación previa a restaurar: el mensaje ficticio tiene lápida **o** trabajo publicado/conciliado, y el evento `trabajo_cancelado` muestra operador y motivo.
- Condición de parada nueva: un borrador de Work para un mensaje con lápida (el importador actual lo aceptaría por una vía directa).

Mientras la orden no exista, rige §8 sin cambios.

## 10. Integración de la cancelación (T-82; no desplegada)

> **Actualización T-86 (21/10/2026):** el parche de T-82 se desplegó como T-83 (`cancelacion_t83_v2.diff`) y quedó verificado con
> `verificar_post_t83.ps1` (lápidas: 0 entradas, 0 faltan, 0 chocan). Las rutas de importación, conciliación y restauración ya rechazan un
> mensaje cancelado. El texto de abajo es el de T-82 y ya no describe el estado actual; rige §11.

Hasta que el parche de `parche_t82/` se implemente, **sigue vigente §9**: no existe la orden `cancelar` en producción y las rutas de
importación directa, importación aislada, conciliación de la fila y restauración de copias **aceptarían** un mensaje cancelado
(`tests/test_integracion_cancelacion_t82.py`). Por eso, mientras tanto, una prueba que cree un mensaje ficticio no debe confiar en una
lápida creada a mano. Detalle y orden de despliegue: `DISENO_INTEGRACION_T82.md`.

## 11. Procedimiento definitivo de C1 (T-86; vigente; sustituye en lo contrario a §0.3, §0.4, §0.6, §9 y §10)

**DISEÑADO, NO EJECUTADO. Este apartado no concede ninguna autorización.** Una sola prueba: un correo ficticio con `solicitud_01.pdf`
(SHA-256 `f47ed4da…d844`, 4913 bytes), asunto único `[PI-C1-…]`.

### 11.1 Decisiones y hechos comprobados en el código
- **Camino: coordinación pausada + despacho manual.** `work_jobs despachar --carpeta-id RAIZ --thread-id X --message-id Y --confirmar-drive`
  funciona con la coordinación apagada y **no** necesita `work_dispatch_enabled`: esa clave solo la lee `despachar --automatico`
  (`work_jobs.py::_despachar`, `despacho.alcance_automatico`). Por tanto no se toca y no hay nada que restaurar de ella. Además, activarla con la
  coordinación encendida es una configuración inválida (`coordinacion_work.py`), de modo que no debe usarse.
- **Lo único que se cambia en `config.json` es `work_coordination_enabled` (true → false → true).** Se restaura con la copia previa.
- **Carpeta de laboratorio separada: NO viable sin tocar la tarea de Work.** La tarea recurrente lista solo la carpeta `PENDIENTES` de su
  carpeta de referencia (`INSTRUCCIONES_UNA_EJECUCION.md`); está ligada a esa raíz y no se debe modificar. Una raíz distinta no sería leída por
  ella. Esto contradice §0.4 y §0.6 solo en lo que exigían raíz distinta con la tarea de producción. El mismo dato debe confirmarse a mano en
  Work (no es verificable desde el computador).
- **Alternativa supervisada (la que rige):** usar la raíz operativa ya configurada (`work_coordination_folder_id`), con la coordinación
  pausada para que el único trabajo publicado sea el del ficticio. **Riesgo operativo exacto:** la tarea recurrente de producción tomará la
  hoja del ficticio como cualquier otra (la más antigua sin borrador) y creará un borrador interno real en Gmail; si en ese momento hubiera
  otra hoja anterior en PENDIENTES, la tomaría antes. Mitigación: PENDIENTES debe estar vacía al empezar (paso 0) y no se publica ningún
  otro trabajo durante la pausa.
- **Datos aislados (§0.3):** `ANKA_DATA_DIR` temporal **no aplica** mientras el receptor vivo registra el correo: el ficticio entra en la
  base operativa. Compensación: copia previa de la base, instantáneas y etiqueta `[PI-C1-…]`.
- **ids reales:** `thread_id` y `message_id` salen de la fila que registra el receptor (solo lectura de `recepcion_mensajes`, por asunto) y se
  contrastan con el mensaje en Gmail; nunca se inventan. No existe forma de conocerlos antes del registro desde esta herramienta, por lo que
  la cancelación **preventiva** no se usa en C1.
- **La cancelación (T-83) se reserva a abortos.** Un trabajo destinado al análisis jamás se cancela: una lápida impide también publicarlo.

### 11.2 Qué hace `cancelar` en cada etapa (código vivo, `servicios/cancelaciones.py`)
| Etapa del ficticio | Resultado |
|---|---|
| Aún no registrado, sin `--preventiva` | rechaza (`mensaje_no_registrado`) |
| Aún no registrado, con `--preventiva` | escribe lápida sin trabajo previo; luego se verifica el asunto |
| Registrado, asunto sin la etiqueta | rechaza (`no_es_de_prueba`) |
| Registrado y pendiente, sin trabajo | crea lápida (`cancelado`), evento y diario |
| Registrado en otro estado (no pendiente) y sin trabajo | rechaza (`mensaje_no_pendiente`) |
| Trabajo `pendiente_de_publicar` sin hoja | lo cancela; si hubo intentos fallidos avisa de revisar PENDIENTES |
| Trabajo `pendiente_de_publicar` con hoja, `publicado` o `ambiguo` | rechaza (`ya_publicado_requiere_persona`) |
| Trabajo `publicando` | rechaza (`publicando_en_curso`) |
| Trabajo `conciliado` | rechaza (`conciliado_intocable`) |
| Ya cancelado | idempotente (`ya_cancelado`) |
Escribe en SQLite: exige `--confirmar` y autorización propia.

### 11.3 Pasos (todos con instantánea antes y después; cualquier diferencia no prevista detiene)
0. [R] `verificar_post_t83.ps1`; PENDIENTES y CERRADOS en Drive (PENDIENTES **vacía**); `verificar-piloto`; hash del PDF; hash y copia de `config.json`;
   copia puntual de la base; hash de `manifest.json`/`PRECISO_V6.1`. Parar si hay un trabajo no conciliado.
1. [W] **AUT-CONFIG-1** pausar: `work_coordination_enabled=false`, solo esa clave.
2. [R] Esperar ≥ 330 s y comprobar 3 veces: candado `work_coordinacion.lock` inmóvil, ningún evento `trabajo_*`, el receptor sigue avanzando.
3. [W] **AUT-ENVIO** enviar un único correo con el PDF, desde una dirección controlada distinta de la de Anka, a la cuenta operativa.
4. [R] Descargar el adjunto: SHA-256 y tamaño. Esperar al receptor (≤ 60 s) y leer una sola fila por asunto; anotar los ids; `work_jobs elegibilidad`.
   Si no es elegible, **parar** (no forzar sin autorización aparte).
5. [W] **AUT-PUBLICACION** `work_jobs despachar … --confirmar-drive` (crea el trabajo en la base operativa y publica una hoja). Esperado: un trabajo,
   una hoja con la marca, ningún otro.
6. [W] **AUT-RESTAURAR** (condición §8.2 cumplida) restaurar `work_coordination_enabled=true` con la copia. Aquí termina la pausa (≤ 60 min).
7. [W] **AUT-BORRADOR** la tarea de Work (la existente, sin tocarla) o una ejecución manual crea **un** borrador interno; nada se envía.
8. [R] Copiar el bloque `[ANKA_DASHBOARD_JSON_V1]` y su SHA-256; evaluador T-77 con `origen_del_retorno="work_real"`.
9. [W] **AUT-IMPORTACION** `work_sync --once --only-thread-id X --only-message-id Y` (si la coordinación ya restaurada no lo hizo sola: comprobar
   antes; nunca las dos vías). Esperado: `conciliado`, un evento `trabajo_conciliado`; segunda importación idéntica.
10. [R] Tres ciclos de observación: sin duplicados, reales de la pausa publicados de uno en uno, hoja del ficticio en CERRADOS.

### 11.4 Autorizaciones independientes (por escrito, una por acción, con el identificador de la prueba; ninguna implica otra)
AUT-ENVIO (enviar) · AUT-CONFIG-1 (pausar) · AUT-RESTAURAR (restaurar la coordinación) · AUT-PUBLICACION (crear el trabajo y publicar en Drive) ·
AUT-BORRADOR (que Work cree el borrador interno) · AUT-IMPORTACION (importar y conciliar en SQLite) · AUT-CANCELAR (solo si se aborta).
Aparte, sin escritura: quién descarga el adjunto, quién revisa el retorno, hora, tope de pausa y remitente.

### 11.5 Aborto y recuperación
- Antes de AUT-ENVIO: restaurar la coordinación y cerrar sin residuos.
- Enviado pero **no registrado**: mantener la pausa y esperar; no hay cancelación posible sin ids (preventiva descartada). Si el tope vence, decide una persona.
- Registrado, sin trabajo, y se decide abortar: `cancelar --confirmar` con la etiqueta, verificar evento y diario con `verificar-lapidas`, y solo
  entonces restaurar. Si en cambio se decide seguir: paso 5.
- Con trabajo en `pendiente_de_publicar` sin hoja: cancelar o publicar; si hubo intentos, revisar PENDIENTES.
- Publicado o más allá: **no se cancela**; seguir adelante (el flujo normal importa y archiva). Si hay que retirar la hoja, lo hace una persona en Drive.
- Cualquier `ambiguo`, `agotado`, segunda hoja, segundo borrador, envío o config distinta de la copia: detener y no restaurar hasta resolver.

### 11.6 Dónde quedan las evidencias
- Base operativa: `%LOCALAPPDATA%\AnkaCorreos\quotes.sqlite3` (filas `recepcion_mensajes`, `trabajos_work`, `recepcion_eventos`); lápidas en `cancelaciones.jsonl`.
- Instantáneas, copias de `config.json` y de la base, hashes y capturas: carpeta nueva `laboratorio/banco_documental/evidencias_c1/` (a crear con autorización de escritura local).
- Hoja en Drive (PENDIENTES → CERRADOS), borrador en Gmail y retorno de Work: referenciados por id; el bloque se copia byte a byte a la evidencia.

### 11.7 Riesgos residuales
Work tarda 1–3 h (la pausa no la espera: termina en el paso 6); la tarea de producción puede tomar la hoja antes de lo previsto; los reales
quedan sin publicar durante la pausa; abortar tras el registro con un trabajo publicado es manual; lo que hace Work es **no verificable** desde el
computador; el cambio de `config.json` lo toma el supervisor sin reiniciarse, pero un reinicio durante la pausa no la altera.
