# Piloto ANKA 02 — 3 de noviembre de 2026

Estado: circuito completo verificado con intervención supervisada. No acredita operación autónoma ni calidad generalizable del modelo.

## Resultado final

- Caso visible: http://127.0.0.1:8765/case?thread_id=1a1a1b35102f007b
- Carpeta única encontrada: https://drive.google.com/drive/folders/1p24GbgBcVOL6yydqwnonUDKQ4Z0Hi4JF
- Hoja: https://docs.google.com/spreadsheets/d/1K4zm7iqdmLJkI4blThGkz01IAGL6euLX-e6VhsmKscE/edit
- Destino comprobado: Solicitudes cotizacion / 2026 / Andrés Muñoz / Cotizacion - PILOTO-ANKA-02 — Topografía para diseño de bodega en Rionegro - 102f007b.
- Se verificaron en Actividades!A1:K3 la cantidad literal 12.500, unidad m², ubicación, entregables, plazo de 15 días, fuentes y notas de prueba ficticia. El libro tiene Actividades, Solicitud inicial, Historial y Léeme.
- Se creó mediante «Crear carpeta del proyecto» en el dashboard; no se emitió una T, no se generaron precios ni una oferta real. La base conserva el vínculo a la carpeta y quote_code vacío.

## Hallazgos pendientes de mejora

1. La búsqueda global de borradores agotó cuota. Se acotó en la tarea Work a `in:drafts subject:PILOTO-ANKA-02`, con lectura del hilo y todos los candidatos/páginas aplicables; el reintento pasó.
2. El conector añade automáticamente texto citado al borrador. Se ajustó la instrucción de transporte del piloto para permitir esa cita fuera de un bloque contractual íntegro y cerrar el contenido interno con «Fin del borrador interno. No enviar.». El código existente filtra ese marcador como respuesta no utilizable para el cliente. Pasaron 52 pruebas existentes de tolerancia Work, contenido saliente y revisión de casos ficticios. No se modificó código de aplicación.
3. Al preparar las preguntas, la interfaz reintrodujo una pregunta genérica de cantidad además de la específica de área; se corrigió el correo manualmente antes de enviar.
4. Work incorporó cantidad y unidad mezcladas en `quantity` y dejó `unit` vacío. El revisor separó el literal 12.500 y m² desde «Corregir la tabla», conservando la equivalencia original en notas. También corrigió notas obsoletas del análisis inicial y dejó explícito el carácter ficticio y las decisiones técnicas pendientes.
5. Work indicó preguntas respondidas en su auditoría, pero el historial de la interfaz siguió mostrando «Sin confirmar» en cada pregunta de la ronda. Por tanto, no queda aprobado el cierre automático de preguntas individuales.
6. La propuesta del seguimiento requirió «Aceptar la propuesta» y la información se validó manualmente para el circuito ficticio. Las publicaciones/importaciones se adelantaron con ciclos manuales del coordinador; no se midió la latencia desatendida del programador.

### Reintento tras recuperar internet

La lectura directa del hilo y una consulta de borradores volvieron a responder. La búsqueda `in:drafts subject:PILOTO-ANKA-02` terminó sin coincidencias ni página siguiente. Esto no prueba por sí solo la causa exacta del fallo anterior, pero confirma que no persistía una denegación general de lectura.

Se guardó en la tarea Work una instrucción para comprobar duplicados mediante el hilo autorizado y una búsqueda de borradores limitada a PILOTO-ANKA-02, verificando identificadores y todas las páginas de esa búsqueda. No se desactivó el control de duplicados. Se solicitó nuevamente «Ejecutar ahora» tras comprobar que el trabajo seguía publicado y que no había caso importado.

El reintento creó el borrador `r4649921554972430032`, mensaje `1a1a1c48ccc6c823`. Un ciclo real del coordinador devolvió `importado`, sin errores. El caso apareció en el dashboard con una actividad topográfica, sin cantidad inventada y con revisión interna por tratarse de datos ficticios. Work informó que el conector añadió texto citado después del bloque contractual; esto no impidió la importación observada.

### Preguntas y respuesta en el mismo hilo

Se actuó como revisor del piloto en el dashboard: se eligió «Hay que preguntarle al cliente» y se revisaron las preguntas. Al preparar el correo, la interfaz añadió una pregunta genérica de cantidad además de la pregunta específica de área; se corrigió manualmente el texto antes de enviarlo. Esto es intervención supervisada, no envío autónomo ni aprobación de la calidad de las preguntas sin revisión.

Se enviaron desde el dashboard tres preguntas: área, delimitación/ubicación y entregables. Se verificó una única operación `sent`, mensaje Gmail `1a1a1c6ccc0c3c9d`, a las 09:18:42; el caso pasó a `waiting`.

La respuesta de Andrés llegó a las 09:21:57 al mismo hilo, mensaje `1a1a1c9f8c9c0221`. Los datos ficticios declarados son 12.500 m² (1,25 ha), lote rectangular de 100 m por 125 m en una ubicación de referencia en Rionegro, alcance planimétrico y altimétrico, planos DWG/PDF, curvas cada 0,50 m, puntos CSV, informe técnico y plazo de 15 días. No se aportaron archivos. Se dejó precisión y sistema de referencia a revisión técnica de Anka, sin inventarlos.

La recepción registró esa respuesta a las 09:22:22. El coordinador creó el trabajo de seguimiento `ANKAJOB-049841ff67e65c07` mediante un ciclo manual limitado y sin errores.

Un primer análisis de seguimiento se detuvo por la cita automática añadida por el conector. Tras el ajuste de transporte descrito arriba, Work creó `r-4742904160143768676`, mensaje `1a1a1d32a6bac5ec`, para el mensaje de seguimiento correcto. La lectura posterior verificó DRAFT, destinatario interno, bloque único y marcador de cierre. El coordinador importó el seguimiento sin errores. Durante esa importación aislada descartó correctamente el borrador del mensaje inicial por corresponder a otro message_id.

Se aceptó la propuesta de Work en el dashboard, se revisó la tabla y se validó la información exclusivamente como caso de prueba. La carpeta se creó a las 09:37, con su hoja nativa. La consulta posterior de Drive encontró una única carpeta coincidente y un único libro dentro de ella.

## Caso real de prueba

- Remitente: 91andresmunoz@gmail.com.
- Receptor: ankacomercialia@gmail.com.
- Asunto: [PILOTO-ANKA-02] Cotización topografía para diseño de bodega en Rionegro.
- Enviado y recibido: 08:57:13, America/Bogota.
- Hilo y mensaje inicial: `1a1a1b35102f007b`.
- Trabajo: `ANKAJOB-549d2906a1a3855f`.
- Hoja publicada: `1nppHANIXtzQxqx0lI89yFpZ8M4_IGpzVtbDVCBwnjug`.
- Contenido ficticio: levantamiento topográfico en Rionegro, Antioquia, para diseño de una bodega; sin área ni entregables declarados y sin adjuntos.

## Preparación y acciones observadas

Se abrió el dashboard y el panel de operación. Se envió el correo desde la sesión de Andrés en Gmail y se verificó su llegada mediante el conector de la cuenta de Anka.

La recepción lo registró a las 08:58:19. Se ejecutó un ciclo real del coordinador limitado a ese mensaje, usando los servicios del proyecto: terminó con `resultado=ok`, una publicación creada y sin errores. Esta intervención manual adelanta la publicación; no demuestra la latencia del ciclo programado.

Se guardó en la tarea de Work existente un alcance exclusivo para este hilo y las respuestas del mismo remitente que Python publique como trabajos. Se conservaron las reglas contractuales y de duplicados. Se ejecutó la tarea con «Ejecutar ahora»; la interfaz mostró «Ejecutándose ahora».

## Resultado observado de Work

La ejecución informó haber encontrado cuatro hojas en PENDIENTES y seleccionado exclusivamente PILOTO-ANKA-02. Verificó los identificadores de su hoja y declaró lectura de la documentación contractual PRECISO_V6.1.3, paquete `1797370a19dd`; no certificó verificación criptográfica.

Clasificó preliminarmente el mensaje como SOLICITUD_NUEVA y señaló revisión interna por tratarse de un caso ficticio. Esta clasificación es un informe de Work, no un resultado importado a Anka.

Se detuvo al paginar borradores: `RATE_LIMIT_EXCEEDED`, con respuesta `FORBIDDEN / PERMISSION_DENIED`. No completó la comprobación de duplicados y no creó el borrador contractual. Una lectura posterior del hilo desde el conector falló por transporte; al intentar abrir la configuración de la tarea, el navegador informó `ERR_CONNECTION_TIMED_OUT`.

## Matriz de validación

| Etapa | Resultado |
|---|---|
| Envío y recepción del correo | Verificado |
| Registro por Anka | Verificado |
| Publicación del trabajo en Drive | Verificado con ciclo manual limitado |
| Inicio efectivo y selección correcta en Work | Verificado |
| Identificación de solicitud | Verificado mediante retorno contractual importado |
| Tabla y faltantes importados al dashboard | Verificado; revisión interna por carácter ficticio |
| Envío de preguntas desde el dashboard | Verificado, con edición supervisada; una operación sent |
| Respuesta de Andrés en el mismo hilo | Verificado |
| Actualización y cierre del alcance | Verificado con aceptación y corrección manual de unidad/notas; no autónomo |
| Creación única de carpeta de proyecto | Verificado desde Anka y por lectura posterior de Drive |

## Acciones que quedan para mejorar la autonomía

1. Corregir la duplicación de preguntas en la preparación de correo.
2. Normalizar cantidad/unidad sin perder los literales ni sus fuentes.
3. Conciliar las respuestas individuales de las rondas con la auditoría de Work.
4. Consolidar la compatibilidad de borradores con citas en el contrato operativo y probar más casos.
5. Medir una ejecución sin adelantos manuales del coordinador ni de Work.

No se sustituyó el análisis de Work por uno elaborado fuera del proyecto ni se editó directamente la base para forzar un resultado. Las decisiones y correcciones supervisadas se hicieron desde el dashboard, y la carpeta fue creada por el flujo de Anka.
