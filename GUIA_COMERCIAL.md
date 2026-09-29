# Guia rapida del dashboard comercial

## Abrir el dashboard

Haz doble clic en `Abrir dashboard comercial.bat`.

La vista `Resumen` muestra solicitudes registradas, clientes identificados,
casos que requieren atencion, servicios mas solicitados, estados y tipos de
cliente. La vista `Solicitudes` permite buscar y filtrar todos los casos.

La seccion `Novedades` avisa por separado cuando llega un correo nuevo, cuando
hay una respuesta sugerida lista, cuando el borrador se guarda en Gmail y
cuando la respuesta se envia. El numero rojo junto a `Resumen` muestra cuantas
novedades siguen sin marcar como revisadas.

Dentro de cada solicitud puedes completar el nombre de la empresa, tipo de
cliente, sector y notas comerciales. Estos datos alimentan las metricas del
resumen.

La `Evaluacion de recepcion` separa tres resultados:

- `Requiere aclaraciones del cliente`: faltan datos basicos que impiden
  entender la solicitud, como ubicacion, magnitud u objetivo.
- `Requiere revision tecnica interna`: hay contradicciones, documentos no
  legibles o referencias que debe revisar primero el equipo de Anka.
- `Informacion suficiente para revision interna`: el equipo ya puede comenzar
  a evaluar la solicitud. Esto no significa que exista una cotizacion ni un
  alcance definitivo.

Las preguntas al cliente incluyen solo faltantes bloqueantes. Precision,
sistema de referencia, metodologia, entregables, plazos y restricciones se
muestran como validaciones internas y no se preguntan automaticamente cuando
Anka puede definirlos despues.

## Contexto comercial interno

La seccion `Contexto comercial interno` consulta un indice local construido a
partir del archivo historico `I:\T&E`. Muestra tipos de proyecto, sectores y
datos solicitados habitualmente. Si completas el nombre de la empresa, intenta
encontrar su perfil historico; si no hay coincidencia, presenta tendencias
generales.

Este contexto esta marcado como `Solo Anka`: no se copia al borrador, no se
sincroniza con Notion y no guarda precios ni texto original de los documentos.
Los documentos personales, afiliaciones, hojas de vida y archivos de operacion
de campo se excluyen de la lectura.

Para actualizar el indice despues de agregar informacion al archivo historico,
ejecuta `python commercial_index.py`. El proceso lee `I:\T&E` sin modificarlo y
guarda el resumen dentro de la carpeta local protegida `internal_context`.

## Responder desde el dashboard

1. Abre la solicitud y verifica el destinatario, el alcance y los datos
   faltantes.
2. Revisa o edita el texto de `Respuesta al cliente`.
   Cuando aplique y no se hayan recibido, la sugerencia incluye documentos
   opcionales como KMZ/KML, planos, cartografia base, terminos de referencia,
   especificaciones o normas del contratante. Los archivos ya adjuntos no se
   vuelven a solicitar.
3. Usa `Guardar borrador en Gmail` para conservarlo en el mismo hilo sin
   enviarlo. Si vuelves a guardar, se actualiza ese borrador y no se duplica.
4. Usa `Enviar ahora` solo cuando el texto este aprobado. El dashboard pide
   una confirmacion final y registra el caso como `Respuesta enviada`.

La automatizacion que detecta y analiza correos sigue siendo de solo lectura.
Crear borradores y enviar son acciones manuales iniciadas desde cada caso.

## Piloto con borradores de Gmail

Haz doble clic en `Piloto borradores Gmail.bat`.

La automatizacion revisa Gmail cada 5 minutos. Cuando encuentre una solicitud con datos faltantes, crea un borrador en el mismo hilo de Gmail. El comercial abre Gmail, revisa el correo original del cliente, corrige el borrador si hace falta y decide si lo envia.

El borrador empieza con una seccion interna marcada como:

`[REVISAR ANTES DE ENVIAR - BORRAR ESTA SECCION]`

Esa seccion sirve para validar lo que detecto la automatizacion, pero debe borrarse antes de enviar el correo al cliente.

Dentro de esa seccion aparece:

- correo original del cliente,
- remitente,
- fecha y hora,
- asunto,
- documentos o adjuntos detectados,
- resumen y actividades detectadas por la automatizacion,
- preguntas que motivan la respuesta.

## Abrir la bandeja anterior

`Abrir revision comercial.bat` sigue funcionando y abre el mismo dashboard.

Se abrira una ventana del navegador con la bandeja de revision. Deja abierta la ventana negra que aparece detras; esa ventana mantiene funcionando la bandeja.

## Revisar casos

1. Entra a un caso con el boton `Revisar caso`.
2. Verifica el destinatario, resumen y actividades detectadas.
3. Si faltan datos, revisa el correo propuesto y editalo en la pantalla si hace falta.
4. Haz clic en `Enviar preguntas al cliente` para dar visto bueno y enviar el correo real.

Si la informacion es suficiente, el caso queda listo para revision interna. En
esta etapa el dashboard no calcula precios ni crea una cotizacion.

## Actualizar Gmail

Usa el boton `Revisar Gmail ahora` para buscar correos nuevos sin cerrar la bandeja.

## Cerrar

Cierra la pestaña del navegador y presiona `Ctrl+C` en la ventana negra.
