# Diseño de aislamiento para la prueba integrada C1 (T-79) — ANÁLISIS Y SIMULACIÓN, NO EJECUTADO

Este documento **no autoriza** ninguna acción. Solo se leyó código y configuración; la simulación corre sobre SQLite temporal y Drive simulado
(`tests/test_aislamiento_t79.py`, 21 pruebas). No se envió ni creó nada, no se tocó Gmail, Drive, SQLite operativo, configuración, procesos ni tareas.

## 1. Mecánica real de la coordinación (Fase 2, con evidencia en código)

| Pregunta | Respuesta | Evidencia |
|---|---|---|
| ¿Qué inicia la coordinación? | El lanzador (`supervisor.servicio`) relee `config.json` cada **30 s** (`MODE_RECHECK`) y, si `work_coordination_enabled` es `true`, lanza un hilo de ciclo cada `work_coordination_interval_seconds` (300 s por defecto). Cada ciclo vuelve a leer la configuración (`load_config` no usa caché) | `supervisor.py`, `CoordinadorWork._ronda`, `settings.load_config` |
| ¿Qué pasa con `enabled=false`? | `plan_desde_config` devuelve `None`: no hay plan, no se lanza el hilo y no se toca Gmail ni Drive. **No** arranca el puente general (D-163; exige `work_general_bridge_enabled=true` aparte, hoy ausente) | pruebas 10 y 11 |
| ¿El receptor sigue? | Sí: depende solo de `gmail_reception_enabled`; la coordinación no lo consulta | prueba 12 |
| Una edición inválida de `config.json` | El lanzador captura el error y pone **a falso** receptor, coordinación y puente: un error de tipeo también pausa la **recepción** | `supervisor.py` (`except … reception_enabled = coordination_enabled = bridge_enabled = False`) |
| ¿Se pueden recuperar los ids reales? | Sí, en solo lectura, por la etiqueta única del asunto (`recepcion_mensajes.subject`), una vez que el receptor registre el mensaje | prueba 15 |
| ¿Crear y publicar solo ese trabajo? | Sí: `work_jobs despachar --carpeta-id RAIZ --thread-id X --message-id Y --confirmar-drive`. **No** requiere `work_dispatch_enabled` (solo `--automatico` lo lee), exige un mensaje concreto y repetirlo no duplica | pruebas 13 y 14 |
| ¿Impedir una publicación simultánea? | (a) Config en `false` (persistente). (b) El candado de instancia única `work_coordinacion.lock`: quien lo tiene hace que los ciclos devuelvan `concurrente` sin hacer nada; se libera solo si el proceso muere (**falla hacia lo abierto**) | prueba 20 y 21 |
| ¿Restablecer sin duplicados? | Sí: `UNIQUE(account, thread, message, tipo, versión)` y marca en Drive; lo que llegó durante la pausa se procesa después, una vez, con el mismo corte (`work_coordination_since` no cambia) | pruebas 40 y 42 |

Ahora bien, el corte y la elegibilidad no aíslan: un remitente humano **siempre** es elegible (T-51), de modo que ni la redacción del asunto ni una etiqueta lo
excluyen (prueba 02). Solo un remitente automático quedaría fuera, y desde Gmail no se puede enviar como tal.

## 2. Comparación de alternativas

### A. Pausar temporalmente la coordinación (`work_coordination_enabled=false`)
- **Cambios:** una edición de `config.json` antes y otra después (con copia); ningún código.
- **Riesgo para trabajos reales:** no se publican ni se importan ni se archivan trabajos reales mientras dura; las solicitudes reales **siguen registrándose** y se atienden al
  restaurar (prueba 42). Hoy no hay trabajos publicados ni pendientes de importar, así que la pausa no deja a nadie esperando una respuesta ya emitida.
- **Condiciones de carrera:** (1) un ciclo ya en curso al editar: termina con la configuración anterior; se resuelve esperando ≥ 30 s + la duración de un ciclo y comprobando
  que `work_coordinacion.lock` no está en uso y que su fecha no cambia en ≥ un intervalo (300 s); el correo ficticio **no se envía hasta tener esa evidencia**, de modo que no
  existe nada que un ciclo atrasado pueda tomar. (2) Un error de edición pausa también la recepción (ver arriba): se corrige antes de enviar.
- **Reversibilidad:** total (restaurar el archivo o la clave); el corte no se toca.
- **Evidencia de aislamiento:** `work_jobs verificar-piloto` (`activada=false`, `candados.coordinacion=false`), fecha del candado inmóvil, ausencia de `trabajo_creado`/`trabajo_publicado` en `recepcion_eventos`.
- **Autorizaciones:** editar la configuración operativa (y restaurarla); el resto, las de T-78.
- **Viabilidad con el código actual:** **demostrada** (pruebas 10 a 14, 40).

### B. Cuenta de Gmail y carpeta de Drive independientes
- **Cambios:** una segunda instancia con `ANKA_DATA_DIR` propio sería posible para la parte de Python (base y token separados), pero la recepción usa la cuenta **fija**
  `CUENTA = "ankacomercialia@gmail.com"` en sus consultas (`recepcion.py`, `trabajos_work.py`), de modo que otra cuenta exige **cambios de código**; además hace falta una autorización
  OAuth interactiva de la otra cuenta.
- **Work:** la tarea recurrente está atada al buzón y a la carpeta productivos; que Work lea otro buzón u otra carpeta **no se puede demostrar** (la tarea es NO_VERIFICABLE y crear o
  modificar tareas está prohibido). Sin esa pata no hay prueba de la cadena completa.
- **Riesgo / carreras:** ninguno sobre lo real, pero la prueba deja de ser representativa del sistema productivo.
- **Reversibilidad:** alta (instancia aparte), pero depende de código nuevo.
- **Autorizaciones:** nueva cuenta, nueva carpeta, OAuth, cambio de código y tarea Work nueva.
- **Viabilidad:** **no viable sin cambios de código; no verificable en la pata de Work.**

### C. Aislamiento por identificador o marcador ficticio
- **C1 marcador en el correo:** no aísla (prueba 02): el remitente humano es siempre elegible.
- **C2 alcance por ids en la configuración** (`work_coordination_thread_id` y `work_coordination_message_id`): existe en el código, limita archivado, importación, alertas y publicación a ese mensaje
  (prueba 30). Pero los ids solo se conocen **después** de recibir el correo: sin pausa previa hay una carrera entre la llegada y la edición (prueba 01 muestra que el ciclo lo publica solo).
  Solo es seguro **combinado con A**.
- **C3 filtro nuevo por marcador en el código:** exigiría modificar producción (`mensajes_nuevos`/elegibilidad): **fuera de alcance** y no verificable aquí.
- **Autorizaciones:** editar configuración (dos veces: poner ids y quitarlos).
- **Reversibilidad:** total. **Viabilidad:** C2 solo tras A; C1 inútil; C3 requiere cambios.

## 3. Opción recomendada

**A + despacho manual de un mensaje concreto + importación aislada**, sin cambios de código: pausar (A), enviar el correo recién después de comprobar la pausa, obtener los ids reales
en solo lectura, publicar con `despachar` solo ese mensaje, importar con `work_sync --once --only-thread-id --only-message-id`, archivar con `despachar` y restaurar.
Justificación: es la única combinación cuya viabilidad se demostró con el código actual, mantiene cada paso como acción separada, evita toda carrera (el ficticio no existe mientras el
coordinador puede actuar) y se revierte con una sola clave. C2 queda como alternativa de refuerzo para el tramo posterior a la publicación, no como base. B no se recomienda.

## 4. Riesgos y condiciones de carrera

1. Ciclo en vuelo al pausar → mitigado enviando el correo solo tras la evidencia de pausa.
2. Edición inválida de la configuración → pausa también la recepción; el correo ficticio no se registraría.
3. El candado como pausa falla hacia lo abierto (si el proceso que lo tiene muere, la coordinación vuelve sola): **no usarlo como mecanismo principal**; solo como comprobación en solo lectura (`_candado_en_uso`).
4. Duración de la pausa: Work tardó entre 1 y 3 h en responder en los trabajos anteriores; durante ese tiempo no se publican solicitudes reales (se registran y se atienden después).
   Mitigación: hacer la prueba fuera del horario de mayor llegada y fijar un tope; al vencerlo, restaurar (ver §6) y dejar que el flujo normal termine el trabajo del ficticio.
5. **Abortar antes de crear el trabajo** deja el ficticio pendiente y elegible: al restaurar la coordinación lo publicaría solo (prueba 41). No existe una orden documentada para descartar una fila de recepción;
   por eso la restauración exige antes que el ficticio tenga trabajo (cualquier estado) o una decisión humana explícita de dejarlo pasar como un trabajo más.
6. Work puede procesar otra hoja de PENDIENTES antes que la del ficticio; hoy no hay otras (verificar con listado de Drive).
7. La carpeta de Drive y el buzón siguen siendo los productivos (necesario para que Work vea la hoja).
8. Un caso real que llegue pidiendo lo mismo que el ficticio no se mezcla: las identidades son distintas, pero conviene que una persona vea la cola antes y después.
9. Pendientes heredados de T-78: dos mensajes posteriores al corte pendientes y no elegibles; revisarlos antes por si cambian de estado durante la pausa.

## 5. Protocolo paso a paso (NO EJECUTAR)

Notación: **[R]** lectura, **[W]** escritura externa que exige autorización. Antes y después de cada [W] se toma la **instantánea**: SHA-256 y copia de `config.json`; recuentos de
`recepcion_mensajes` por estado, de `trabajos_work` por estado y de `recepcion_eventos`; `mail_operations`; listado de PENDIENTES y CERRADOS en Drive; recuento de borradores y de la carpeta Enviados. Toda diferencia
no prevista **detiene** el procedimiento.

| Paso | Acción | Esperado | Parada si… |
|---|---|---|---|
| 0 [R] | `verificar-piloto`, instantánea base, hash del PDF (`f47ed4da…`, 4913 bytes) | `trabajos_por_estado` solo `conciliado`; PENDIENTES vacío | hay un trabajo no conciliado o archivos en PENDIENTES |
| 1 [W] A1 | Poner `work_coordination_enabled=false` (copia previa; JSON válido) | `config.json` válido y con una sola clave distinta | el JSON no valida; `verificar-piloto` informa errores |
| 2 [R] | Esperar ≥ 30 s + 300 s; 3 lecturas de `verificar-piloto` espaciadas | `activada=false`, candado libre, fecha del candado inmóvil, ningún evento nuevo | el candado cambia o hay un evento `trabajo_*` |
| 3 [R] | Comprobar que el receptor sigue (`recepcion_control.last_success_at` avanza) | avanza | no avanza (posible edición inválida) |
| 4 [W] A2 | Enviar **un** correo ficticio con asunto único `[PI-T79-…] C1` y el PDF, desde una dirección controlada distinta de la de Anka | un mensaje en Gmail | más de uno, o destinatarios no previstos |
| 5 [R] | Descargar el adjunto desde Gmail: SHA-256 y tamaño | `f47ed4da…` y 4913 | difieren |
| 6 [R] A3 | Esperar al receptor; leer en solo lectura `recepcion_mensajes` por el asunto | exactamente una fila; ids reales anotados; adjuntos con nombre, tamaño y estado `disponible_en_gmail` | 0 o más de 1 fila |
| 7 [R] | `work_jobs elegibilidad --thread-id X --message-id Y` | elegible; sin trabajo | no elegible (no forzar sin otra autorización) |
| 8 [W] A4+A5 | `work_jobs despachar --carpeta-id RAIZ --thread-id X --message-id Y --confirmar-drive` | un trabajo, una hoja en PENDIENTES con la marca; ningún otro trabajo | resultado `ambiguo` o `error`; más de un trabajo o archivo |
| 9 [R] | Instantánea: solo un `trabajo_creado` y un `trabajo_publicado`, de esos ids | igual | cualquier otro evento |
| 10 [W] A6+A7 | Work procesa ese trabajo (tarea existente, sin tocarla) y deja un borrador | un borrador; ningún envío | dos borradores o un envío |
| 11 [R] | Copiar el bloque `[ANKA_DASHBOARD_JSON_V1]` byte a byte y su SHA-256 | — | falta el bloque |
| 12 [R] | Evaluar con el evaluador T-77 (`origen_del_retorno="work_real"`) con los ids autorizados | `EVALUADO`, 0 críticos | cualquier `DETENIDO_*` |
| 13 [W] A8 | `work_sync --once --only-thread-id X --only-message-id Y` (importación aislada) | trabajo `conciliado`, un evento `trabajo_conciliado`; cantidades iguales a las del retorno | cambia una cantidad; segunda importación distinta de la primera |
| 14 [W] | `work_jobs despachar` del mismo mensaje (archiva en CERRADOS) | una hoja en CERRADOS | dos archivos |
| 15 [W] A9 | Restaurar `work_coordination_enabled=true` (misma copia) | `verificar-piloto` coherente con la línea base | cualquier clave distinta de la original |
| 16 [R] | 3 ciclos de observación (≈15 min): ningún trabajo duplicado, las solicitudes reales registradas durante la pausa pasan a `publicado` de una en una | igual a la base más lo previsto | duplicados, o el ficticio reprocesado |

## 6. Condiciones de detención, rollback y restauración

- **Detener** (no continuar y pasar al rollback correspondiente): cualquier diferencia de instantánea no prevista; hash o tamaño del adjunto distinto; más de una fila, trabajo, hoja o borrador del ficticio;
  un envío; un id distinto del anotado; `ambiguo`/`error` en Drive; retorno inválido; cantidad cambiada por el importador; edición inválida de la configuración; Work procesa algo que no es el ficticio.
- **Rollback según el punto:**
  - Antes del paso 4 (no se envió nada): restaurar la configuración (paso 15) y verificar.
  - Entre 4 y 8 (el ficticio existe pero sin trabajo): **no** restaurar hasta decidir; el ficticio quedaría elegible y se publicaría solo. Opciones: crear y publicar su trabajo con `despachar` (pasa a ser un trabajo ficticio más) o mantener la pausa mientras una persona decide.
  - Entre 8 y 14 (trabajo publicado): se puede restaurar la coordinación; importará y archivará el trabajo del ficticio por el flujo normal (idempotente).
- **Restauración:** volver a `true`, comparar `config.json` con la copia (misma huella salvo la clave), esperar un ciclo, comprobar sin duplicados (prueba 40).
- **Tope de pausa:** corregido en T-80 (`PLAN_PRUEBA_INTEGRADA.md` §8): la pausa solo cubre hasta publicar el trabajo del ficticio (objetivo 30 min, tope propuesto 60 min); el rollback entre los pasos 4 y 8 es «continuar hacia adelante», no restaurar.

## 7. Autorizaciones requeridas (escritas, independientes, con el identificador de la prueba)

A1 pausar la coordinación · A2 enviar el correo ficticio · A3 que el receptor lo registre (ya activo; confirmar) · A4 crear el trabajo en la base operativa · A5 publicar la hoja en Drive ·
A6 que Work ejecute su tarea sobre ese trabajo · A7 que deje el borrador interno · A8 importar y reconciliar · A9 restaurar la coordinación. Además: que una persona descargue el adjunto y calcule el hash,
y que otra revise lo que Work devuelva. Este informe no concede ninguna.

## 8. Dictamen

**AISLAMIENTO_VIABLE_SIN_CAMBIOS_DE_CODIGO** — para el tramo de Python (recepción, creación, publicación, importación, archivado y restauración), demostrado con el código actual en 21 pruebas de simulación,
**con estas salvedades que no se ocultan**: (1) la pausa en el proceso *vivo* solo puede confirmarse en la ejecución (pasos 1 a 3); (2) la lectura y el borrador de Work siguen siendo **NO_VERIFICABLE** desde el computador;
(3) la pausa detiene también las solicitudes reales durante la espera de Work; (4) abortar entre los pasos 4 y 8 no tiene una orden de descarte documentada.

## 9. Primera acción segura para una fase posterior

Sin escribir nada: que una persona de Anka decida por escrito el **tope de pausa**, la **hora** de la prueba (preferible fuera de la mayor llegada de solicitudes), la **dirección remitente** del correo
ficticio, y quién descargará el adjunto y revisará el retorno; y que se tome la instantánea base (paso 0), que es solo lectura.
