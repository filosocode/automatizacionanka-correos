# Cancelación segura de trabajos ficticios (T-81) — REFERENCIA DE LABORATORIO, NO DESPLEGADA

No autoriza nada ni se usa en la prueba real. Código: `laboratorio/banco_documental/cancelacion.py`. Pruebas: `tests/test_cancelacion_t81.py` (34, con SQLite temporal, Gmail y Drive simulados).
`anka/`, la configuración, el importador, el contrato y PRECISO_V6.1.3 no se tocaron (ver §8).

## 1. Ciclo de vida (Fase 1)

| Pieza | Identidad | Quién la escribe | Qué hace falta saber |
|---|---|---|---|
| Mensaje en Gmail | `thread_id`, `message_id` (los asigna Gmail) | el remitente | solo se conocen al recibirlo |
| Fila de recepción `recepcion_mensajes` | `(account, thread_id, message_id)` | el receptor (solo lectura de Gmail) | `pendiente_de_procesar` hasta que Work lo interpreta |
| Trabajo `trabajos_work` | `job_id = ANKAJOB-` + SHA-256(cuenta, hilo, mensaje, tipo, versión)[:16], `UNIQUE` por mensaje/tipo/versión | coordinador o `work_jobs crear` | estados pendiente_de_publicar → publicando → publicado → conciliado; `ambiguo` y `cancelado` aparte |
| Hoja en Drive | marca `appProperties.anka_job_id` | publicación | existe desde `publicado` |
| Eventos `recepcion_eventos` | `(thread, message, kind)` | todos | la huella de quién y cuándo |

Qué debe cumplirse para que **nadie** vuelva a crear o publicar un mensaje cancelado (comprobado en el código actual):
1. `mensajes_nuevos` descarta cualquier mensaje que ya tenga **alguna** fila en `trabajos_work`, sea cual sea su estado → el coordinador no lo crea.
2. `crear_trabajo` calcula el mismo `job_id`; si existe, lo devuelve sin crear (`creado=False`) → ni el despacho manual ni `work_jobs crear` lo duplican.
3. `_reclamar` rechaza publicar un trabajo `cancelado`; `trabajos_por_estado` no lo incluye en los estados vivos → no se reintenta ni se importa.
4. `marcar_conciliado` solo cierra pendiente/publicando/publicado → un cancelado no pasa a conciliado.
5. La fila debe existir **antes** de que el coordinador mire el mensaje, y escribirse en una transacción con candado de escritura (el código actual ya usa `BEGIN IMMEDIATE`).

Conclusión: la lápida es compatible con el código actual **en el lado consumidor**; solo falta el productor.

## 2. Operación de cancelación (Fase 2) — `cancelar_mensaje(db, Autorizacion)`

| # | Requisito de la solicitud | Cómo se cumple | Prueba |
|---|---|---|---|
| 1 | Identidad verificada | `thread_id` y `message_id` literales (`^[A-Za-z0-9_-]{1,128}$`); si la fila está registrada, su asunto debe llevar la etiqueta `[PI-…]` autorizada | 03, 05 |
| 2 | Excluir aunque no tenga trabajo | inserta la lápida con el `job_id` determinista; y cancelación **preventiva** explícita antes del registro, con verificación posterior | 10 a 13, 30 a 33 |
| 3 | Transacción y carreras | `BEGIN IMMEDIATE`, comparar-y-cambiar (`WHERE state='pendiente_de_publicar'`), todo o nada, rechazo si la conexión ya está en transacción | 22, 23, 40 a 42 |
| 4 | Coordinador no crea ni publica | puntos 1 a 3 de §1 | 12, 13, 14, 24, 41 |
| 5 | No elimina nada | solo inserta o cambia un estado; mensajes y eventos previos intactos | 10, 61 |
| 6 | No toca conciliados ni reales | conciliado → `conciliado_intocable`; real → `no_es_de_prueba`; otros mensajes y trabajos idénticos | 05, 18, 51 |
| 7 | Registra motivo, fecha, identidad, operador | evento `trabajo_cancelado` con `job_id`, `motivo`, `operador`, `etiqueta`, `cancelado_at`, `estado_previo`, `verificada`, `intentos_previos` | 10 |
| 8 | Idempotente | si todo está cancelado devuelve `ya_cancelado` sin escribir ni duplicar el evento | 20, 21, 33 |
| 9 | Sin ambigüedad | sin comodines, sin asunto/remitente/parcial como criterio de búsqueda | 03, 06 |
| 10 | No cambia el supervisor | la operación solo recibe una conexión; no lee ni escribe configuración, procesos ni Drive/Gmail | 02, 61 |

Qué hace en cada estado del trabajo:

| Estado | Resultado | Motivo |
|---|---|---|
| Sin trabajo, mensaje registrado y de prueba | lápida | — |
| Sin trabajo, mensaje no registrado | rechazo (`mensaje_no_registrado`) salvo `preventiva=True` | no se verifica una identidad que no se ve |
| `pendiente_de_publicar` | `cancelado` (con aviso `revisar_drive` si hubo intentos fallidos) | nada visible fuera de SQLite; un intento fallido pudo dejar una hoja huérfana |
| Pendiente con hoja anotada | rechazo (`ya_publicado_requiere_persona`) | hay hoja |
| `publicando` | rechazo (`publicando_en_curso`) | otro proceso la está creando |
| `publicado`, `ambiguo` | rechazo (`ya_publicado_requiere_persona`) | hoja en Drive y Work puede estar trabajando: **exige persona** |
| `conciliado` | rechazo (`conciliado_intocable`) | — |
| `cancelado` | `ya_cancelado` | idempotencia |

Decisión sobre «cancelación después de publicación»: **se rechaza y exige intervención humana**. Cancelar la fila no retira la hoja de PENDIENTES ni detiene a Work; sería una cancelación falsa.

## 3. Resultados de las simulaciones (Fase 3) — 34 pruebas, todas OK

Cancelación antes y después de registrar (10, 30 a 33); antes de crear el trabajo (10 a 13); con trabajo pendiente (14 a 16); carrera con un ciclo (40 a 42); reintentos tras error (22, 24);
restauración y reales acumulados (12, 50); mensaje no autorizado (03 a 07); repetida (20, 21, 33); tras publicación (17, 40); conciliado (18); reclamado (19).
Hallazgos: con una lápida el ciclo no publica nada y los 3 reales salen en su orden de llegada (4 trabajos, 3 hojas, sin duplicados tras 5 ciclos más);
un error a mitad revierte todo y el reintento funciona; un fallo de Drive posterior no resucita el trabajo; el despacho manual de una lápida responde `TrabajoRechazado`.

## 4. Carreras y atomicidad

- **Cancelación vs. ciclo, mismo mensaje.** 20 carreras con hilos reales y retardos escalonados: resultados `cancelado` (6 a 8 veces), `publicando_en_curso` y `ya_publicado_requiere_persona` en el resto; **0 incoherentes**
  (nunca «cancelado con hoja» ni «publicado y cancelado»). El orden lo decide el candado de escritura de SQLite.
- **Por qué es seguro.** (a) `BEGIN IMMEDIATE` serializa contra `crear_trabajo`/`_reclamar`, que usan la misma transacción. (b) La lápida y el evento van en la misma transacción. (c) El cambio de estado compara el estado esperado; si falla, `carrera` y nada se escribe.
  (d) Si el ciclo ya reclamó el trabajo, la cancelación ve `publicando` y se niega.
- **Límite.** La ventana de un ciclo que ya leyó `mensajes_nuevos` antes de la lápida y llama a `crear_trabajo` después se cierra en `crear_trabajo`: encuentra la fila cancelada y no crea nada. No se vio ninguna fuga en las pruebas; no es una prueba formal.
- **Atomicidad del evento.** Si el evento falla, la lápida se revierte (prueba 22).

## 5. Requisitos para una integración productiva (NO implementada)

1. Nuevo requisito en `spec/requisitos.md` y decisión en `spec/05-decisiones.md`; la orden `work_jobs cancelar --thread-id --message-id --etiqueta --operador --motivo [--preventiva]` con confirmación explícita.
2. Función en `anka/servicios/trabajos_work.py` usando `store.transaccion()`, las constantes reales de cuenta y estados, y el `_evento` existente; módulo/ruta descritos en el «Mapa de módulos» (`spec/06`).
3. **Importador:** hoy un borrador para un mensaje con lápida se importa por una vía directa (prueba 60) y deja el mensaje `interpretado` y el análisis guardado. La integración debe decidir si el importador lo rechaza; es un cambio al importador y exige su propia autorización.
4. Vistas: el mensaje cancelado sigue como «pendiente / elegible» en la clasificación de recepción; mostrar `cancelado`.
5. Pruebas equivalentes a las 34 de laboratorio, contra la función productiva y SQLite temporal, con `# Cubre:` y cobertura ≥ 82 %.
6. Copias de seguridad: restaurar una copia anterior a la lápida la pierde; el procedimiento de restauración debe re-comprobar las lápidas (evento `trabajo_cancelado`).
7. Ninguna ruta del dashboard (solo CLI); si hubiera, `openapi.py` y `GET_ROUTES`/`POST_ROUTES`.
8. Despliegue por el lanzador (reinicia al cambiar el código) fuera de la prueba, con autorización propia.

## 6. Protocolo de recuperación actualizado
`PLAN_PRUEBA_INTEGRADA.md` §9. Mientras la orden no exista en producción rige §8; si se integra, cambian: la cancelación tras recibir sin trabajo (lápida en lugar de «continuar hacia adelante»), la cobertura de «enviado pero no registrado» (preventiva + verificación),
la comprobación previa a restaurar (lápida o trabajo publicado/conciliado) y una condición de parada nueva (borrador de Work para un mensaje con lápida).

## 7. Riesgos que permanecen sin resolver
1. La orden no existe en producción: hoy la prueba real **sigue sin poder descartar** un ficticio (rige §8, «continuar hacia adelante»).
2. El importador no respeta la lápida por una vía directa (prueba 60).
3. Un trabajo ya publicado solo se resuelve con una persona; el ficticio publicado ocupa PENDIENTES y la atención de Work.
4. La cancelación preventiva se basa en ids que una persona leyó; si fueran de un mensaje real, se cancelaría una solicitud real. Mitigación: verificar el asunto en cuanto se registre (prueba 32) y revertir a mano.
5. `revisar_drive`: un pendiente con intentos fallidos pudo dejar una hoja huérfana que la orden no ve.
6. Sin vistas, el mensaje cancelado parece pendiente.
7. Las copias de seguridad pueden perder la lápida.
8. Sigue sin explicar el silencio de `anka.log` desde el 20/10 y el estado real de la tarea Work (NO_VERIFICABLE).
9. La referencia usa constantes replicadas (prueba 01 detecta deriva); una verificación formal de concurrencia con varios procesos no se hizo.

## 8. Compatibilidad y no alteración
Comprobado: ninguna de las rutas siguientes cambió (`git status` limpio salvo `laboratorio/`, `tests/` y `spec/requisitos.md`): `anka/`, `skills/` (PRECISO_V6.1.3), `spec/contrato_work.schema.json`, `anka/servicios/work_import.py` (importador), la lógica de creación de borradores y la ausencia de envío automático (R8).
El funcionamiento normal sin cancelar no cambia: las pruebas 12, 41 y 50 muestran que los mensajes no cancelados siguen su curso con orden y límites iguales.

## 9. Dictamen
**CANCELACION_SEGURA_EN_LABORATORIO.** La operación cumple los diez requisitos con pruebas y resiste las carreras simuladas. No es una cancelación disponible: lo seguro hoy es la referencia, no el sistema. Los riesgos 2 a 7 deben resolverse en la integración productiva, que requiere su propia autorización.
