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
- Conserva el hilo: cualquier borrador debe responder en el mismo hilo y permitir que comercial revise el correo original antes de enviar.

## Flujo

1. Identifica si el mensaje es una solicitud de cotizacion o una respuesta a una solicitud previa.
2. Extrae datos del correo: remitente, fecha/hora, asunto, texto, adjuntos, proyecto, ubicacion, magnitud, servicio probable, entregables, plazos, restricciones y dudas.
3. Construye una tabla de actividades. Cada fila debe tener actividad, servicio/tecnica candidata, ubicacion, magnitud, unidad, entregable, plazo/restriccion, fuente y estado.
4. Determina si el alcance esta incompleto para la siguiente etapa de cotizacion.
5. Si faltan datos, redacta un borrador breve en espanol claro, con preguntas agrupadas y sin tecnicismos innecesarios.
6. Si el alcance parece completo, recomienda pasar a revision comercial/tecnica antes de crear carpeta o cotizacion final.
7. Cuando llegue una respuesta del cliente, reconcilia la nueva informacion con la tabla anterior. Una respuesta parcial no convierte el alcance en completo.

## Criterio de solicitud de cotizacion

Considera candidato cuando el correo pida cotizar, presupuestar, proponer, valorar o estimar un servicio de topografia, geomatica, inspeccion, captura o procesamiento de datos espaciales. Tambien aplica cuando el asunto o el cuerpo sugieran una necesidad comercial aunque no use literalmente "cotizacion" (por ejemplo, preguntar por valor, tiempos o disponibilidad de un servicio). Un prefiltro por palabras clave solo debe descartar ruido inequivoco; ante la duda, la solicitud pasa a analisis o a revision, no se descarta.

Descarta o deja en revision los casos que sean publicidad, mensajes administrativos, notificaciones automaticas, duplicados evidentes, o solicitudes sin relacion con servicios de Anka.

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
- `source_notes`.
