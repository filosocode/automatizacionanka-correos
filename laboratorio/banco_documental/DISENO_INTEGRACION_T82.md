# Integración segura de la cancelación (T-82) — PROPUESTA, NO DESPLEGADA

No autoriza nada. `anka/`, PRECISO_V6.1.3, el contrato Work, la tarea Work recurrente, la configuración y la base operativa
**no se tocaron**. Todo se probó con SQLite temporal y Gmail/Drive simulados. El parche se aplicó solo en copias fuera del repositorio.

- Evidencia del estado actual: `tests/test_integracion_cancelacion_t82.py` (corre contra el código productivo de hoy).
- Parche propuesto: `laboratorio/banco_documental/parche_t82/cancelacion_t83.diff` (se aplica con `git apply -p1`; verificado con `--check`).
- Requisitos: T-82 (evidencia) y T-83 (comportamiento nuevo) en `spec/requisitos.md`; decisión D-179 en el parche.

## 1. Inventario de rutas

| # | Ruta | Código | Reprocesaría un cancelado hoy |
|---|---|---|---|
| 1 | Recepción de Gmail | `recepcion.registrar_nuevos`, `trabajos_work.mensajes_nuevos` | No: ignora todo mensaje con alguna fila de trabajo |
| 2 | Creación de trabajo | `crear_trabajo`, `work_jobs crear` | No: el `UNIQUE` y la lectura previa devuelven la lápida (`creado=False`) |
| 3 | Coordinación (ciclo) | `coordinacion` (cola = recuperables + `mensajes_nuevos`) | No |
| 4 | Publicación en Drive | `publicar_trabajo` → `_reclamar` | No: lanza `TrabajoRechazado` |
| 5 | Despacho manual | `despacho.despachar` | No publica, pero **lanzaba error** (ruido) |
| 6 | Conciliación (cierre del trabajo) | `marcar_conciliado`, `CONCILIABLES` | No toca un cancelado |
| 7 | **Conciliar la fila de recepción** | `recepcion.conciliar_con_work` | **Sí**: cierra la fila como interpretada |
| 8 | **Importación directa de borrador** | `work_import.dashboard_upsert` (también `app.py dashboard-upsert`, `work_sync --only-*`) | **Sí**: guarda el análisis y deja el mensaje `interpretado` |
| 9 | **Importación aislada** | `work_sync.importar_aislado` | **Sí**: responde «importado» |
| 10 | **Restaurar copia** | `backup.restore` | **Sí**: una copia anterior pierde la lápida y el mensaje vuelve a ser publicable |
| 11 | Panel / consultas | `estado_operativo`, `trabajos_work.clasificar_pendientes` | Lo muestra como pendiente de atención |
| 12 | Orden de cancelar | `work_jobs` | **No existe**, tampoco el diario de lápidas |

## 2. Riesgos actuales demostrados (pruebas 10–16 de `CaminosAbiertosHoy`)

Rutas 7–10, 11 y 12 de la tabla. Las seguras (1–6) están demostradas en `CaminosSegurosHoy` (pruebas 01–06). Importante: la ruta 8 es
la más grave, porque Work puede devolver un borrador de un mensaje cancelado y el sistema lo daría por interpretado sin trabajo.

## 3. Diseño mínimo

1. **Lápida**: fila `trabajos_work` en `cancelado` con el `job_id` determinista; no se borra nada.
2. **Orden**: `work_jobs cancelar --thread-id --message-id --etiqueta --operador --motivo [--preventiva] --confirmar`; sin `--confirmar` no escribe.
3. **Identidad**: cuenta fija, hilo y mensaje literales, y el asunto del mensaje registrado debe llevar la etiqueta `[PI-…]` autorizada
   (marcador ficticio). Un mensaje real no se puede cancelar con la orden; exige una persona.
4. **Transacción**: una sola `BEGIN IMMEDIATE` con lápida + evento `trabajo_cancelado` (operador, motivo, fecha, identidad) y, antes de
   confirmar, la línea del diario. Idempotente: repetirla completa el diario si faltaba y no duplica eventos.
5. **Estados**: solo se cancela lo pendiente. Publicando, publicado, ambiguo o con hoja de Drive guardado pasan a una persona
   (`revisar_drive` si hubo intentos); conciliado no se toca.
6. **Cierre de caminos**: guardas en `_store_work_analysis` (lanza `MensajeCancelado`), `conciliar_con_work` (devuelve `cancelado`),
   `importar_aislado` (`mensaje_cancelado`, que el coordinador **no** cuenta como fallo ni reintenta), y el despacho (devuelve
   `cancelado` en lugar de lanzar, incluso si la lápida aparece entre la consulta y la creación o entre crear y publicar).
7. **Concurrencia**: todas las guardas leen dentro de la transacción que escribe; el `UNIQUE` y `BEGIN IMMEDIATE` serializan. Prueba de 20 carreras.
8. **Copias de seguridad**: el diario `cancelaciones.jsonl` vive fuera de la base y de las copias; `backup.restore` reaplica sin
   migrar y sin abrir el negocio; `work_jobs reaplicar-lapidas --confirmar` y `verificar-lapidas` (solo lectura) lo completan.
9. **Vistas**: «atención» excluye lápidas y `clasificar_pendientes` trae la clave `cancelado`.

## 4. Modificaciones archivo por archivo (ver el `.diff`)

| Archivo | Cambio |
|---|---|
| `anka/dominio/cancelacion_work.py` (nuevo) | Validación de identidad, `Autorizacion`, `CancelacionRechazada`, `MensajeCancelado` |
| `anka/infra/lapidas.py` (nuevo) | Diario append+fsync, `leer`, `comparar`, `reaplicar`, `reaplicar_en_archivo` |
| `anka/servicios/cancelaciones.py` (nuevo) | `cancelar_mensaje`, `verificar_lapida_preventiva`, `verificar_lapidas`, `reaplicar_lapidas`, `mensaje_cancelado` |
| `anka/servicios/work_import.py` | Guarda tras `begin_immediate` |
| `anka/servicios/recepcion.py` | `conciliar_con_work` devuelve `cancelado` |
| `anka/servicios/coordinacion.py` | `IMPORTACIONES_SIN_FALLO` incluye `mensaje_cancelado` |
| `anka/servicios/despacho.py` | Devuelve `cancelado` en tres puntos |
| `anka/servicios/trabajos_work.py`, `estado_operativo.py` | Vistas |
| `anka/cli/work_jobs.py` | Subcomandos `cancelar`, `verificar-lapidas`, `reaplicar-lapidas` |
| `anka/cli/work_sync.py` | `importar_aislado` y `sync_once` |
| `anka/infra/backup.py` | `restore` reaplica lápidas |
| `spec/05-decisiones.md`, `06-arquitectura.md`, `requisitos.md` | D-179, mapa de módulos, T-83 |

**Contratos y pruebas que cambian** (los únicos): `despachar` sobre una lápida devuelve `publicacion.resultado == "cancelado"` en vez de lanzar
(`test_cancelacion_t81` test 13); importar el análisis de un mensaje cancelado lanza `MensajeCancelado`
(`test_trabajos_work::test_un_trabajo_ambiguo_o_cancelado_no_cambia_al_importar`). Se retiran de la evidencia T-82 las pruebas que describían el
estado anterior (03, 06 y toda la clase `CaminosAbiertosHoy`) y la clase `HuecosDeProduccionTest` de T-81; las sustituye
`tests/test_cancelacion_work.py` (49 pruebas contra las funciones productivas). El contrato Work (`spec/contrato_work.schema.json`) **no cambia**.

## 5. Pruebas y simulación

Evidencia del estado actual (repo): 13 pruebas, 6 seguras y 7 que prueban un camino abierto. Tras aplicar el parche (copia): ver el
informe final en la respuesta (cifras de la suite completa). Casos clave: cancelar y luego importar por cada vía; cancelar entre
consulta y creación; cancelar con trabajo avanzado; mensaje real con etiqueta falsa; restauración con diario y sin diario; línea rota del
diario; diario con choque; 20 carreras cancelar/publicar; correos comerciales reales sin cambios.

## 6. Despliegue y reversión

**Antes**: copia de seguridad de la base y de `work_dashboard_sync_state.json`; `git apply --check`; `check.bat` verde en la copia aplicada;
`work_jobs verificar-lapidas` sin diario (debe dar «en orden total» con 0 entradas); el coordinador y el puente se reinician con el
lanzador (cambia el código). No pausar la tarea Work: las guardas no cambian el contrato.
**Orden**: aplicar el parche → reiniciar → `verificar-lapidas` → recién entonces `cancelar` con la etiqueta de la prueba.
**Reversión**: `git apply -R`. La lápida es una fila común (`cancelado` ya existía en el esquema), así que el código anterior la respeta
igual salvo en las rutas 7–10; no hay migración de esquema que deshacer. El diario `cancelaciones.jsonl` se conserva.
**Tras restaurar una copia**: `restore` reaplica solo; si la base copiada es anterior a `trabajos_work` se omite con aviso y se ejecuta
`reaplicar-lapidas --confirmar` después de migrar. Los choques con trabajos avanzados se informan y nunca se pisan.
**Correos comerciales**: sin etiqueta `[PI-…]` ninguna guarda cambia su camino; la prueba de regresión importa, concilia y despacha
mensajes reales con la base con y sin lápidas de otros mensajes.

## 7. Riesgos residuales

- Un trabajo ya publicado o en publicación no se cancela con la orden; exige una persona y revisar Drive.
- Si Work ya tiene un borrador en vuelo de un mensaje cancelado, se descarta al importar, pero ese borrador queda sin cierre en Work.
- El diario es un archivo local: si se pierde junto con la base, no hay lápida que reaplicar.
- La orden depende del marcador `[PI-…]`; un asunto sin él obliga a cancelar a mano.
- Aplicar el diff sobre archivos CRLF normaliza esas líneas a LF (revisar `.gitattributes`).
- La carrera «cancelar justo mientras `publicar` ya reclamó» queda cubierta por el estado `publicando` (requiere persona), no por la lápida.

## 8. Dictamen

Ver la respuesta final.
