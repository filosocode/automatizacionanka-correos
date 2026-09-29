# Diseño propuesto: automatización híbrida de solicitudes de cotización

**Estado:** propuesta para discusión. No autoriza envíos automáticos ni constituye una especificación final de cotización.

## Selección del motor de análisis

El prototipo permite elegir un único proveedor en `config.json`, mediante `llm.provider`:

- `ollama`: análisis en el modelo local definido en `llm.model` y `llm.base_url`.
- `openai`: análisis con la API, usando `OPENAI_API_KEY` y el modelo definido en `llm.model`.

No hay fallback entre proveedores. Si el proveedor elegido falla, el análisis se detiene para revisión/reintento; esto evita que una falla local active cobros de API o que una falla de API cambie el motor sin autorización. La elección no altera Gmail, Drive ni el estado persistente del flujo.

## Principio

Python gestiona el flujo, la extracción mecánica y las reglas aprobadas por Anka. El modelo interpreta lenguaje ambiguo, combina información dispersa y propone actividades o preguntas. Ninguna salida del modelo se considera un dato confirmado sin evidencia en el correo o sus adjuntos.

El objetivo no es minimizar llamadas a cualquier costo: una solicitud válida no debe descartarse solo por no contener la palabra «cotización». El ahorro se obtiene filtrando con reglas de alta certeza, eliminando contenido repetido y enviando al modelo únicamente los casos y fragmentos que requieren criterio.

## Flujo por niveles

| Nivel | Trabajo | ¿Usa tokens del modelo? |
|---|---|---|
| 0. Gmail + Python | Detectar mensajes nuevos, identificar hilo/remitente, excluir enviados por Anka, deduplicar, guardar estado. | No |
| 1. Preparación local | Limpiar firmas, avisos y citas repetidas; extraer texto de adjuntos compatibles; reconocer datos explícitos como correos, fechas, magnitudes y unidades conservando su fuente. | No |
| 2. Triaje | Reglas de alta certeza para separar ruido inequívoco y reconocer solicitudes explícitas. Los casos dudosos pasan al modelo. | Solo en casos dudosos |
| 3. Interpretación | En una consulta estructurada, clasificar lo dudoso, reconocer el servicio posible, sintetizar actividades y vincular cada dato con evidencia. | Sí, cuando hace falta |
| 4. Validación | Python comprueba esquema, evidencia, duplicados, contradicciones y campos obligatorios definidos por Anka para el servicio y la etapa. | No |
| 5. Respuesta | Una plantilla de Python redacta preguntas a partir de faltantes confirmados. El modelo interviene solo si la pregunta requiere una formulación técnica o resolver una ambigüedad. | Opcional |
| 6. Continuación | Al recibir respuesta en el mismo hilo, procesar solo el mensaje nuevo y actualizar los campos pendientes; conservar la tabla y fuentes anteriores. | Solo si la respuesta requiere interpretación |
| 7. Drive | Cuando se alcance el estado acordado, ubicar la carpeta padre real, buscar si ya existe la carpeta del proyecto y crearla una sola vez. | No |

## Qué debe decidir cada componente

**Reglas deterministas:** estado del hilo, fecha de última respuesta, deduplicación, lectura de formatos conocidos, validación de tipos y unidades, presencia de campos obligatorios, armado de plantillas, búsqueda y creación de carpetas. Las expresiones regulares pueden detectar magnitudes, pero no deben asignarlas a una actividad si el significado no es inequívoco.

**Modelo:** interpretar necesidades expresadas de forma libre, distinguir entre trabajo solicitado y entregable, resumir actividades de un pliego, relacionar una respuesta con una pregunta anterior, identificar contradicciones y sugerir aclaraciones. Debe devolver JSON estructurado con fragmento y fuente por cada afirmación; `no indicado` cuando falte evidencia.

**Equipo Anka:** definir qué datos son obligatorios para pasar de solicitud a alcance preliminar y de alcance preliminar a cotización; aprobar reglas de servicio, lenguaje comercial y excepciones. La lista pública de soluciones no equivale a esas reglas internas.

## Contrato mínimo de la tabla

Cada actividad debería incluir: descripción, servicio o técnica candidata, ubicación, magnitud, unidad, entregable, plazo o restricción si consta, fuente exacta, y estado (`confirmado`, `pendiente` o `contradictorio`). A nivel de solicitud: identificador del hilo, remitente, proyecto, versión de la tabla, preguntas abiertas y etapa del proceso.

Conviene distinguir **alcance documentado** de **listo para cotizar**. Puede existir una tabla completa respecto del correo y aun así faltar una decisión técnica o comercial de Anka.

## Control de costo y calidad

- Guardar un hash del contenido normalizado y la versión del análisis. Si nada relevante cambió, no repetir la consulta.
- En respuestas posteriores enviar al modelo la tabla vigente, los campos pendientes y el nuevo mensaje, en lugar de reenviar todo el hilo. Recuperar mensajes anteriores solo cuando haya una contradicción o referencia ambigua.
- Para adjuntos, extraer texto localmente antes de considerar visión u OCR. Un adjunto no legible pasa a revisión; no se trata como ausencia de datos.
- Limitar el contexto al material pertinente y registrar tamaño de entrada/salida, motivo de la llamada, costo y correcciones humanas.
- Medir sobre correos reales etiquetados: solicitudes perdidas, falsas solicitudes, exactitud de actividades, preguntas innecesarias, duplicados, casos a revisión y tokens por solicitud completada. No fijar porcentajes de ahorro sin esa muestra.

## Estados sugeridos

`nuevo → descartado | alcance_preliminar → esperando_cliente → alcance_actualizado → revisión_técnica | listo_para_cotizar → carpeta_creada`

Puede haber varias respuestas parciales y varias versiones de la tabla. Los envíos, cambios de estado y creación de carpeta requieren identificadores persistentes para reintentos seguros.

## Diferencias con el prototipo actual

El archivo `app.py` es exploratorio. Hoy llama al modelo para cada hilo nuevo, le envía todo el texto disponible y deja cualquier adjunto en revisión. Tampoco incorpora reglas internas por servicio ni evidencia por celda. No activar `--apply` hasta sustituir esas partes y validar el flujo con ejemplos de Anka.

## Decisiones pendientes de Anka

1. Qué datos son indispensables para cada familia de servicios y qué significa «listo para cotizar».
2. Si la interpretación se ejecutará mediante API automática, revisión humana en el chat, o ambas.
3. Qué tipos de adjuntos son habituales y cuáles deben leerse primero.
4. Cuándo puede salir una pregunta automáticamente y cuándo debe aprobarla una persona.
5. Qué carpeta padre y convención de nombres se usarán en Drive.
