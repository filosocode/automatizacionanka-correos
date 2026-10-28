# Expedientes OCR de Gmail en Drive

> **Camino anterior (prompt V5 y antes).** Con el prompt V6 (T-41, `PROMPT_WORK_ANKA_V6.txt`) Work lee los adjuntos directamente
> desde Gmail y este preparador no hace falta: déjalo apagado (`work_drive_enabled: false`). Se retirará cuando V6 esté probado en Work.

La entrada sigue siendo Gmail. Un proceso Python descarga los adjuntos, conserva originales y publica la
extracción completa por segmentos en Drive. Work consulta esos archivos antes de interpretar el alcance.
La carpeta de recepción no confirma un cliente, no emite T y no reemplaza la carpeta comercial.

## Configuración

En `config.json` de la carpeta de datos (no en el repositorio), completar:

```json
{
  "active_mode": "chatgpt_work",
  "work_sync_account": "cuenta-autorizada@ejemplo.com",
  "work_drive_enabled": true,
  "work_drive_parent_id": "ID_DE_LA_CARPETA_DE_RECEPCION",
  "work_drive_query": "in:inbox newer_than:7d from:remitente-de-prueba@ejemplo.com"
}
```

Son claves adicionales: conservar las demás. La raíz de recepción debe existir y ser distinta de `drive_parent_id`.
Usar una búsqueda acotada durante el piloto. El preparador publica los hilos de esa búsqueda; no decide por palabras
clave qué es una cotización. No habilitar el preparador en varios equipos contra la misma raíz: el bloqueo es local.

Una pasada: `python app.py work-drive-sync`. Solo un hilo: agregar `--thread-id ID`.
El supervisor inicia automáticamente `work-drive-sync --watch-seconds 60` cuando está habilitado.
El equipo debe permanecer encendido. Los fallos quedan en `work_drive_status.json` y `logs/anka.log`.
Para detenerlo, poner `work_drive_enabled=false`; el supervisor termina el proceso en su siguiente revisión.

## Lectura en Work

La tarea persistente «Cotizaciones Grupo Anka» se comprobó con `PRECISO_V4.5_DRIVE` y la raíz de recepción
`https://drive.google.com/drive/folders/141Xp_F4ktXGhR4PW0s8fki9zGM65aFbs`. `PROMPT_WORK_ANKA_PRECISO_V4.txt`
conserva una copia histórica V4.2; no pegarla en la tarea activa. Verificar que Google Drive esté disponible.
Cambiar archivos del repositorio no actualiza una tarea remota ya creada.
Work debe leer `Hilo <thread_id>/MANIFIESTO_WORK.json`, comparar `message_ids` con Gmail y consultar todos los
segmentos por ID. Como control previo, debe dejar evidencia interna de la revisión del manifiesto, message_ids,
segmentos leídos y versión del prompt. No puede informar "conector no compatible" si la extracción local del
manifiesto es accesible. Si el conector solo ofrece una vista parcial, debe recuperar el archivo completo o declarar el límite.
Si la preparación aún no terminó, reintentar en la próxima ejecución; nunca inventar el contenido ni preguntar al cliente
por una demora del preparador. La tarea debe conservar los pendientes para reintentarlos aunque el mensaje ya esté leído.

Estructura: `Hilo <thread_id>/Originales`, `Lectura`, `Version <revision>` y manifiesto vigente.
Los originales y fichas se identifican por mensaje, posición del adjunto y SHA-256; dos nombres iguales no se pisan.
Una nueva respuesta genera otra versión; las anteriores quedan conservadas. `status=listo` confirma publicación,
no calidad: `coverage=parcial/no_legible` exige revisión interna. El OCR no certifica tablas, símbolos ni geometrías.

Los límites por expediente son 30 adjuntos y 250 MiB; por archivo 50 MiB. Los que exceden el límite se enumeran
con motivo y permanecen en Gmail. Se conservan límites internos de los lectores (PDF 200 páginas, OCR 40 millones
de píxeles, hojas y ZIP). Una descarga o subida interrumpida no actualiza el manifiesto vigente. Reintentar reutiliza
archivos publicados. Los IDs de mensajes, no etiquetas de leído/no leído, determinan cambios.

## Comparación reproducible

`tools/compare_work_drive.py` captura hilos indicados y el análisis histórico del dashboard en modo de solo lectura.
Guarda los datos en `archivo_local/` (ignorado por Git). El parámetro `--source-root` permite ejecutar los mismos bytes
con el código anterior y nuevo en procesos separados; conserva hashes, texto, cobertura, motivo y duración.
Con `--baseline`, `--updated` y `--output` produce una comparación contra la cobertura declarada en Work.
Eso compara extracción y acceso a evidencia; no calcula precisión de una nueva ejecución de Work que no haya ocurrido.

Para un A/B real, ejecutar ambos prompts sobre la misma captura, con el mismo modelo, sin guardar nuevos borradores
operativos ni modificar casos. Revisar cantidades, unidades, fuentes, omisiones y preguntas contra los originales.
Registrar versión del prompt/modelo y resultados. Repetir varias veces antes de estimar una mejora generalizable.

### Prueba piloto anterior (V4.2)

Esta prueba conserva el escenario de la iteración V4.2; no configura la tarea persistente actual.
En una ejecución aislada de Work, pide una sola vez:

> Reprocesa exclusivamente el hilo `1a1268517470e652` para comprobar el uso del expediente OCR. Primero abre la raíz
> configurada, el manifiesto vigente y todos sus segmentos. Devuelve en el bloque interno la revisión del manifiesto,
> message_ids, cuántos segmentos de correo/adjuntos abriste y `skill_version=PRECISO_V4.2_DRIVE`. Para
> `eje_variante_sur.kml`, informa los datos presentes en su segmento (geometría, coordenadas, longitud/extensión)
> sin inventar CRS. Para cada PDF, separa texto extraído de figuras no interpretadas e informa la advertencia de
> datos ficticios. Si no puedes leer el manifiesto o algún segmento, detente y nombra el archivo/segmento exacto;
> no lo declares ilegible si el manifiesto lo marca como extraído. No envíes correos ni emitas una T.

La prueba pasa solo si Work cita la revisión real, identifica el KML como extraído, informa las 12 coordenadas
registradas y la longitud aproximada de 13,110.6 m, y detecta la advertencia de datos ficticios. Si falla el
control de acceso, la lectura con Drive no está operativa aunque el preparador haya publicado el OCR.

Además, debe comparar la longitud aproximada derivada de 13,110.6 m con la progresiva solicitada de 12,5 km,
marcar ambas como medidas distintas y dejar la discrepancia para revisión técnica. Debe dejar `DRIVE_PREFLIGHT`
visible en la sección interna y registrar `skill_version=PRECISO_V4.2_DRIVE`; si alguno falta, la prueba falla.

### Aceptación observada para V4.5 (10 de octubre de 2026)

El caso simulado `1a127465796821cd` contiene un correo y cuatro adjuntos: KML, DOCX, PDF y XLSX. El resultado
importado declara 1/1 segmento de correo y 4/4 segmentos de adjuntos, y registra inspección de los cuatro originales.
Sin embargo, `reading_audit` no contiene `email_segment_ids_expected`, `email_segment_ids_read`,
`attachment_segment_ids_expected` ni `attachment_segment_ids_read`. El dashboard deja por ello la lectura en
`pendiente` y `analysis_complete=false`. Es el resultado conservador esperado del puente, pero la aceptación de
trazabilidad V4.5 **no pasa** hasta que un reanálisis dirigido incluya y verifique esas listas de IDs reales.

No uses «Ejecutar ahora» de la tarea persistente para esta prueba aislada: su búsqueda Gmail cubre varios remitentes
y podría procesar otros mensajes. El reintento dirigido de un solo hilo sigue pendiente de una vía que no dispare
la búsqueda general de la automatización.

## Auditoría del puente (T-46)

El resultado del preparador incluye `ready_message_ids` y `revision`, incluso sin cambios.
Sirve para comprobar publicación; no significa que Work haya leído el expediente ni dispara la tarea remota.
Un límite que impide consultar un archivo tiene `coverage=null`, no demuestra ilegibilidad.
La versión del extractor pasa a `anka-drive-ocr-v2` para regenerar las fichas anteriores de forma trazable.

El puente conserva `DRIVE_PREFLIGHT`, `reading_audit`, `related_threads`, `analysis_complete`
y `applicability`, y el dashboard separa lectura de aplicabilidad. Para comprobar IDs use
`reading_audit.email_segment_ids_expected`, `email_segment_ids_read`,
`attachment_segment_ids_expected` y `attachment_segment_ids_read`, como listas de IDs de Drive.
Los conteos solos no certifican lectura. `analysis_complete=false` crea revisión interna, sin respuesta propuesta al cliente.
El preparador y el webhook siguen siendo independientes: un expediente preparado después del evento
necesita reanálisis presentado a Work. Este parche no promete reintento remoto automático.
