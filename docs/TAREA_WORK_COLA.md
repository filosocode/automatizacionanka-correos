# Tarea de Work que recoge un trabajo por ejecución (borrador para revisar · T-53)

> **Es un texto para revisar, no una tarea creada.** No se ha creado ninguna tarea de Work ni se ha modificado ANKA 01.
> Antes de crearla hace falta autorización y una prueba con un solo expediente (ver `docs/PILOTO_COLA_WORK.md`).

## Qué hace Python (no lo hace Work)
- Crea y publica la hoja de cada trabajo elegible en la carpeta **PENDIENTES** del laboratorio.
- Mueve a **CERRADOS** las hojas cuyo análisis ya importó el dashboard.
- Guarda en SQLite el estado de cada trabajo. Las carpetas son solo una vista para Work.

## Instrucciones propuestas para la tarea de Work (una ejecución = un trabajo)
1. Abre la carpeta de laboratorio de Drive y lista **solo PENDIENTES**. Si está vacía, termina sin hacer nada.
2. Toma **un solo trabajo**: la hoja más antigua (por fecha de creación) que no tenga ya un borrador tuyo. No tomes más de uno por ejecución.
3. Lee la hoja `TRABAJO` (solo lectura): `job_id`, `thread_id`, `message_id`, remitente, asunto, fecha y la `clasificacion_preliminar`. La hoja `VERIFICACION` dice qué comprobar.
4. Abre en Gmail el hilo `thread_id` y el mensaje `message_id`. Compara remitente, asunto, fecha y adjuntos con la hoja. Si algo no coincide, **no analices**: termina y avisa.
5. **Evita repetir:** antes de trabajar busca en Gmail un borrador tuyo que ya contenga ese `message_id` dentro de `[ANKA_DASHBOARD_JSON_V1]`. Si existe, no crees otro; termina.
6. Analiza solo ese mensaje (los demás del hilo son contexto) con las reglas de siempre de ANKA 01.
7. Confirma o corrige la `clasificacion_preliminar` con el campo opcional `classification` (`SOLICITUD_NUEVA`, `SEGUIMIENTO`, `NO_SOLICITUD` o `REVISION_MANUAL`) y una frase en `classification_reason`. No decidas solo por palabras clave; ante la duda, `REVISION_MANUAL`.
8. Devuelve el borrador de siempre con `[ANKA_DASHBOARD_JSON_V1]`, el mismo `thread_id` y `message_id` de la hoja. **No envíes** ningún correo al cliente.
9. **No muevas, renombres, borres ni edites** archivos de Drive. Python mueve las hojas.
10. Termina. Una ejecución no procesa un segundo trabajo.

## Por qué así
- *Un trabajo por ejecución* y *busca un borrador previo* evitan que dos ejecuciones analicen el mismo mensaje.
- Si Work falla a medias, la hoja sigue en PENDIENTES y la ejecución siguiente la retoma; Python no la cierra hasta importar el análisis.
- Si Python se cae, SQLite conserva los estados y la siguiente pasada del despacho repara las carpetas.

## Qué probar antes de crear la tarea real
Un solo expediente ficticio de punta a punta (comandos en `docs/PILOTO_COLA_WORK.md`, sección «Despacho T-53»). Quedan por comprobar en Work: que liste PENDIENTES desde una ejecución programada, que respete «un trabajo por ejecución» y que devuelva `classification`.

## Referencia del contrato (T-54)
Work no puede leer el repositorio: las instrucciones definitivas y el contrato viven en la carpeta de Drive `REFERENCIA_ANKA_V1` (paquete con manifiesto y SHA-256). Ver «Paquete de referencia» en `docs/PILOTO_COLA_WORK.md` y `docs/paquete_work/`.
