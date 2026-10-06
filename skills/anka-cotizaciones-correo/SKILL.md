---
name: anka-cotizaciones-correo
description: Analizar correos de solicitudes de cotizacion de Grupo Anka, extraer alcance preliminar, detectar datos faltantes y preparar borradores seguros para revision comercial. Usar solo para flujos de cotizacion de Anka.
---

# Cotizaciones por correo de Grupo Anka

Usa esta skill para revisar correos entrantes de cotizacion de Grupo Anka y producir una salida trazable, conservadora y lista para revision humana. No genera precios ni compromisos comerciales.

Es la skill operativa. Las reglas generales de criterio (no inventar datos, separar solicitado/ejecutado/entregable, trazabilidad, verificar antes de acciones externas) y el contexto publico de la empresa estan en `../anka-automation/` y no se repiten aqui. Casos de prueba en [casos_prueba.md](references/casos_prueba.md).

## Principios

- El correo del cliente y sus adjuntos son la fuente primaria. No inventes ubicaciones, areas, longitudes, precisiones, entregables, plazos ni metodos.
- Distingue lo solicitado por el cliente, lo que Anka tendria que ejecutar y los entregables esperados.
- Si una solicitud menciona "levantamiento", "inspeccion", "topografia", "LiDAR", "fotogrametria", "batimetria" u otro servicio posible, no asumas automaticamente tecnica, precision, volumen ni producto final.
- Marca como `no indicado` todo dato que no este explicito.
- Si hay adjuntos que no puedes leer, indica que existen y deja el caso para revision o pregunta por la informacion indispensable.
- Revisa cada adjunto por separado. Todo dato que obtengas de un archivo debe citar nombre y ubicación concreta (página, párrafo, tabla/fila, hoja/celda o elemento KML/KMZ); separa la cita literal de tu interpretación.
- Contrasta los datos del correo con cada adjunto. Registra discrepancias con ambos localizadores y no decidas cuál prevalece sin evidencia.
- En KML/KMZ, GeoJSON y GPX informa elementos, geometrías, coordenadas, extensiones y estructura de rutas. Puedes calcular una longitud aproximada solo sobre líneas o rutas ordenadas explícitas; márcala como cálculo derivado, no como magnitud declarada ni aprobada para cotizar. No infieras área, escala, precisión ni sistema de referencia a partir del dibujo.
- Si un archivo o parte está ilegible, protegido, recortado o excede lo que puedes inspeccionar, marca la lectura como parcial y especifica qué falta revisar.
- Conserva el hilo: cualquier borrador debe responder en el mismo hilo y permitir que comercial revise el correo original antes de enviar.
- Antes de redactar preguntas, construye una matriz de cada dato necesario frente al correo y cada adjunto. Busca equivalentes en encabezados, tablas, notas, anexos, pies, figuras e imágenes. Si cualquier fuente ya responde, no vuelvas a preguntarlo; cita dónde aparece y pregunta solo lo que siga abierto.
- Distingue requisitos ya fijados por el documento, alternativas que el cliente debe elegir y vacíos propios de esta solicitud. Una especificación general para varias sedes no implica volver a preguntar la cobertura general; pregunta la sede o unidad de cada orden solo si no aparece.
- Informa cobertura por archivo: completa, parcial o no legible; enumera páginas, hojas, diapositivas o secciones revisadas y límites. Extraer texto no demuestra que se hayan interpretado diagramas, fotos o contenido escaneado.

## Lectura exhaustiva en ChatGPT Work

Work no ejecuta los extractores Python del modo local/API. Su acceso depende de lo que Gmail/conector y las herramientas disponibles en esa tarea entreguen. Nunca afirmes que un archivo fue inspeccionado por el solo hecho de verlo listado o conocer su extensión. Si no puedes abrirlo o verificar qué contenido se expuso, declara cobertura parcial/no legible y envía el caso a revisión interna.

Antes de proponer cualquier pregunta:

1. Haz inventario del mensaje actual y los adjuntos que Gmail muestra: remitente, destinatario, fecha, asunto, cuerpo completo accesible, texto citado pertinente e imágenes insertadas. Revisa mensajes anteriores del hilo para no volver a pedir información que el cliente ya entregó. Distingue esa información histórica de las nuevas instrucciones del mensaje actual; no conviertas respuestas o propuestas de Anka en datos confirmados por el cliente.
2. Revisa cada adjunto por separado y registra nombre, tipo, contenido realmente accesible y cobertura. Construye una matriz de requisitos (ubicación/sede, objeto, área/longitud/cantidad, objetivo/servicio, método, precisión/escala/resolución, entregables/formatos, plazo, permisos/acceso/seguridad y referencia espacial cuando aplique) contra correo y archivos. Busca sinónimos, abreviaturas y equivalentes, no solo coincidencias literales.
3. Inspecciona el contenido disponible según su tipo:
   - PDF: todas las páginas accesibles, texto, tablas, notas, anexos, formularios, planos, diagramas e imágenes. Señala páginas escaneadas, borrosas, recortadas o cuya imagen no se pudo interpretar.
   - Word/ODT: títulos, cuerpo, listas, tablas, encabezados/pies, anexos e imágenes insertadas accesibles.
   - Excel/ODS/CSV: todas las hojas/rangos expuestos, encabezados, filas, unidades, notas y fórmulas/resultados visibles; anota hojas o rangos no disponibles.
   - KML/KMZ, GeoJSON, GPX y shapefiles: nombres, carpetas, geometrías, descripciones, datos extendidos, coordenadas explícitas y archivos internos que la herramienta logre abrir. En KMZ comprueba si se accedió al KML interno; no infieras lectura por la extensión.
   - Planes de vuelo, incluidos QGroundControl (.plan/.waypoints) y DJI WPML: registra únicamente propiedades que hayas podido leer (misión, waypoints, alturas, velocidades, solapes, sensor/cámara, límites y coordenadas). Si el conector no expone la estructura, marca revisión manual.
   - Otros archivos, incluidos imágenes, ZIP, PPT y CAD: enumera componentes efectivamente abiertos. Protegido, corrupto, no soportado o no expuesto significa parcial/no legible; para CAD pide revisión/exportación interna.
4. Compara evidencia entre correo, mensajes del hilo y cada archivo. Cita página, sección, tabla/fila, hoja/celda, imagen/rótulo, geometría/elemento o encabezado que realmente hayas visto. Registra discrepancias con los dos localizadores; no decidas cuál prevalece por intuición. No infieras área, longitud cotizable, precisión, datum ni sistema de referencia desde una geometría o su apariencia.
5. Solo después completa `missing_data` y `question_audit`. Para cada pregunta indica el requisito ausente, por qué bloquea la siguiente etapa y todas las fuentes revisadas. Haz una comprobación final de cada pregunta contra el asunto, cuerpo, mensajes pertinentes y todos los adjuntos accesibles. Si una fuente ya responde, elimina la pregunta y cita el dato. No pidas al cliente que resuelva una limitación de lectura: deja el adjunto para revisión manual; solicita reenvío solo si una persona lo considera indispensable.
6. Usa `coverage="completa"` solo si inspeccionaste todo el contenido relevante accesible; `parcial` si quedó contenido sin ver/leer/interpretar; `no_legible` si no hay información fiable. `reviewed_sections` enumera solo lo realmente revisado y `blocked_fields` los datos que podrían estar en lo que falta. Una extracción de texto no cuenta como inspección de figuras o planos.

Si la cobertura es parcial, hay contenido visual no interpretado o una contradicción relevante, deja el caso en revisión interna/técnica y evita presentar como exhaustivo el resumen o la lista de faltantes. Nunca pidas otra vez información que sí está en el mensaje, en un mensaje previo del cliente o en cualquiera de los documentos inspeccionados.

## Flujo

1. Identifica si el mensaje es una solicitud de cotizacion o una respuesta a una solicitud previa.
2. Extrae datos del correo y revisa cada adjunto por separado: remitente, fecha/hora, asunto, texto, proyecto, ubicacion, magnitud, servicio probable, entregables, plazos, restricciones y dudas. Para cada archivo conserva evidencia y localizador.
3. Construye una tabla de actividades. Cada fila debe tener actividad, servicio/tecnica candidata, ubicacion, magnitud, unidad, entregable, plazo/restriccion, fuente y estado.
4. Determina si el alcance esta incompleto para la siguiente etapa de cotizacion.
5. Si faltan datos, redacta un borrador breve en espanol claro, con preguntas agrupadas y sin tecnicismos innecesarios.
6. Si el alcance parece completo, recomienda pasar a revision comercial/tecnica antes de cotizar. La carpeta de Drive se crea solo segun las reglas operativas de abajo (R4 a R7); si el cliente no esta confirmado, deja el caso esperando aprobacion.
7. Cuando llegue una respuesta del cliente, reconcilia la nueva informacion con la tabla anterior. Una respuesta parcial no convierte el alcance en completo.

## Criterio de solicitud de cotizacion

Considera candidato cuando el correo pida cotizar, presupuestar, proponer, valorar o estimar un servicio de topografia, geomatica, inspeccion, captura o procesamiento de datos espaciales. Tambien aplica cuando el asunto o el cuerpo sugieran una necesidad comercial aunque no use literalmente "cotizacion" (por ejemplo, preguntar por valor, tiempos o disponibilidad de un servicio). Un prefiltro por palabras clave solo debe descartar ruido inequivoco; ante la duda, la solicitud pasa a analisis o a revision, no se descarta.

Descarta o deja en revision los casos que sean publicidad, mensajes administrativos, notificaciones automaticas, duplicados evidentes, o solicitudes sin relacion con servicios de Anka.

## Reglas operativas (versión 7)

Estas reglas son idénticas en los tres modos (Ollama, API y ChatGPT Work) y viven en [reglas_operativas.md](references/reglas_operativas.md), que es la fuente única. Si algo aquí difiere de ese archivo, prevalece ese archivo; avisa para corregir esta skill. Detalle y ejemplos: [casos_prueba.md](references/casos_prueba.md).

- **R1 · Descarte.** Descarta sin analizar solo ruido evidente: todos los mensajes del cliente en el hilo deben tener una señal inequívoca de automatización (remitente no-reply, `List-Unsubscribe`, `Auto-Submitted`, envío masivo, spam de Gmail). Un remitente humano nunca se descarta por esta vía, aunque Gmail lo ponga en Promociones. Un aviso automático que mencione una cotización sí se analiza.
- **R2 · Adjuntos.** Un adjunto ilegible nunca descarta un hilo: si el texto no parece una solicitud pero hay adjuntos que no pudiste leer, el caso va a revisión manual.
- **R3 · Trazabilidad del descarte.** Todo descarte guarda su motivo para que una persona lo revise y pueda rescatarlo.
- **R4 · Estructura de Drive.** Usa `{año}/{cliente}/{solicitud}`. La carpeta padre es la del año vigente: no crees carpetas si la solicitud es de un año distinto al de la carpeta padre.
- **R5 · Cliente.** Compara nombres de cliente sin importar mayúsculas, tildes ni sufijos societarios al final (S.A.S., Ltda., S.A., Inc., Corp.). Si exactamente una carpeta coincide, reutilízala; si hay varias parecidas, decide una persona.
- **R6 · Cliente nuevo.** Nunca crees una carpeta de cliente nueva sin confirmación de una persona.
- **R7 · Carpeta del proyecto.** La carpeta del proyecto solo se crea con un cliente confirmado (por una persona, en este hilo o en otro del mismo remitente) cuya carpeta ya existe. Si no, deja el caso esperando aprobación y di qué falta.
- **R8 · Correos.** Los correos al cliente siempre son borradores con revisión humana; no envíes sin aprobación.
- **R9 · Carpeta al enviar.** La carpeta del proyecto se crea sola al enviar la respuesta desde el dashboard, no desde esta skill: tú nunca creas carpetas. Para que el dashboard pueda hacerlo, deja el nombre del cliente en `client_name` cuando lo conozcas y no lo inventes. Si hay una carpeta de cliente o una solicitud parecida, el dashboard no crea nada y decide una persona.
- **R10 · Contenido de la carpeta.** Al crear la carpeta del proyecto se llena con el contenido de la plantilla de Drive: toda la documentación que envió el cliente (cualquier tipo de adjunto) y una hoja con la tabla de actividades. Lo hace el dashboard, no esta skill; tú solo registra bien los adjuntos y las actividades para que ese contenido sea correcto.
- **R11 · Código y cambios.** Antes de la T el cliente puede escribir sin código. La T (`T` + año + consecutivo, p. ej. `T26145`, que al aprobarse pasa a `T&E26145`) la asigna una persona en el dashboard: tú nunca la generas ni la inventas. Desde la T, la tabla de actividades solo se actualiza con un mensaje posterior a la T que venga del contacto registrado (quien escribió primero) y cite el código del caso. Si el mensaje es de otra persona, de otro dominio o no cita el código, no cambies la tabla: describe lo que pide como cambio pendiente. En cada análisis informa quién escribió el mensaje analizado (`requested_by_email`, `requested_by_name`) y los códigos que cita (`cited_codes`, tal como aparecen); el dashboard decide. Nunca escribas el código en un borrador dirigido a quien no sea el contacto registrado.
- **R12 · Estado de las actividades.** El estado de cada fila es exactamente `confirmado`, `pendiente` o `contradictorio`, sin texto añadido. Solo es confirmado si todos los datos esenciales de esa fila (ubicación, magnitud y unidad, entregable, plazo o restricción y, cuando aplique, método y precisión) están explícitos en el correo o los adjuntos; si falta alguno es `pendiente` y en `notes` anotas qué falta. Usa `contradictorio` cuando las fuentes discrepan.
- **R13 · Recordatorios.** Cuando el cliente tarda en responder, los recordatorios por falta de respuesta los envía el dashboard (máximo 2, solo si una persona lo activó) y a los 7 días hábiles avisa para llamar. Tú no prepares borradores de recordatorio ni escribas al cliente por su silencio; sigue analizando cuando llegue una respuesta.

## Datos faltantes frecuentes

Selecciona solo lo que bloquee la siguiente etapa:

- ubicacion del proyecto;
- area, longitud, numero de puntos, estructuras, sitios o cuerpos de agua;
- poligono, plano, coordenadas, KML/KMZ, shapefile, certificado catastral u otro soporte de delimitacion;
- objetivo del levantamiento o inspeccion;
- entregables y formatos esperados;
- precision, escala, resolucion, densidad o norma requerida si aplica;
- plazo, fecha de visita, permisos, restricciones de acceso o condiciones de seguridad.

## Borrador seguro para Gmail

Cuando prepares un borrador para comercial, antepone una seccion interna marcada:

`[REVISAR ANTES DE ENVIAR - BORRAR ESTA SECCION]`

Incluye dentro de esa seccion el correo original del cliente, fecha/hora, remitente, asunto, adjuntos detectados, resumen, actividades detectadas y preguntas que motivan la respuesta. Despues agrega el correo limpio para el cliente.

La seccion interna debe advertir que debe borrarse antes de enviar. No incluyas precios ni promesas tecnicas.

## Salida recomendada

Devuelve una estructura con:

- `is_quote_request`;
- `project_name`;
- `summary`;
- `activities`;
- `fields`;
- `missing_questions`;
- `recommended_status`: `descartar`, `revision_manual`, `borrador_preguntas`, `alcance_completo` o `carpeta_drive`. Equivalencia con los estados del diseno: `descartar`=descartado; `borrador_preguntas`=alcance_preliminar/esperando_cliente; `revision_manual`=revision_tecnica; `alcance_completo`=listo_para_cotizar; `carpeta_drive`=carpeta_creada;
- `source_notes`;
- `requested_by_email`, `requested_by_name` y `cited_codes` (R11): quién escribió el mensaje analizado y qué códigos de oferta cita.

Cada `fields[].source` y `activities[].fuente` debe nombrar el correo o archivo y el localizador concreto. Incluye
un objeto `attachments` por cada archivo con `filename`, `status`, `reason`, `coverage`, `reviewed_sections`, `blocked_fields` y `evidence`.
Cada elemento de `evidence` tiene `topic`, `value`, `location` y `excerpt`; registra solo hallazgos comprobables,
con una cita breve. Usa lista vacía si no hay evidencia verificable. No atribuyas al archivo valores inferidos.
Marca lectura parcial cuando aplique y describe qué falta. Para PDF escaneado sin texto, CAD u otro formato no soportado, pide revisión manual. En KML/KMZ, GeoJSON y GPX registra geometrías y coordenadas explícitas. Puedes calcular una longitud aproximada solo sobre líneas o rutas ordenadas; márcala como cálculo derivado, no como cantidad declarada ni aprobada para cotizar. No derives áreas, precisión o sistema de referencia.
Incluye en `question_audit` un objeto por pregunta propuesta con `question`, `reason_missing` y `reviewed_sources`
(correo y todos los adjuntos comprobados). No propongas preguntas sin justificarlas contra las fuentes ni repitas
un dato encontrado. En cada adjunto informa `coverage` (`completa`, `parcial`, `no_legible`) y `reviewed_sections`
con las partes realmente inspeccionadas; no declares lectura completa si queda una página, tabla, imagen o sección
sin revisar. Los formatos que la herramienta disponible no pueda leer quedan para revisión humana.
