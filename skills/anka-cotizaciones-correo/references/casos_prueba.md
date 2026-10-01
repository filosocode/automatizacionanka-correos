# Casos de prueba de referencia

Casos **sintéticos** para probar la skill. No son correos reales de clientes. Sustituir o ampliar con 15 a 30 solicitudes históricas anonimizadas y revisadas por Anka (ver `PLAN_FASE_1_PLUS.md`, paso 2). Cada caso indica el resultado esperado, no la redacción exacta.

**Casos ejecutables compartidos:** `casos_paridad.json` contiene correos de ejemplo con la decisión esperada del descarte (R1), nombres de cliente que deben o no coincidir (R5) y la regla del año de la carpeta padre (R4). Python los ejecuta en `test_paridad_modos.py`; en ChatGPT Work se pegan a mano para comprobar que la skill decide igual (ver `ESTANDAR_CAMBIOS.md`, paso 6).

## 1. Solicitud clara e incompleta
**Correo:** «Buenas, necesitamos cotizar un levantamiento LiDAR para una línea de transmisión de 230 kV en Boyacá.»
**Esperado:** `is_quote_request=true`; servicio candidato LiDAR aéreo; ubicación «Boyacá» (departamento, sin tramo); magnitud, entregables, precisión y plazo = `no indicado`; `recommended_status=borrador_preguntas`. Una sola pregunta agrupada: tramo o archivo de ubicación, longitud aproximada, entregables y plazo.

## 2. Solicitud sin la palabra «cotización»
**Correo:** «Estamos evaluando opciones para inspeccionar 40 torres con termografía. ¿Cuánto tiempo y qué valor manejan?»
**Esperado:** `is_quote_request=true` (necesidad comercial explícita). Magnitud: 40 estructuras. Ubicación `no indicado`. No responder con precio.

## 3. Ruido inequívoco
**Correo:** boletín promocional de un proveedor de software, o notificación automática de un sistema.
**Esperado:** `is_quote_request=false`; `recommended_status=descartar`.

## 4. Servicio ambiguo
**Correo:** «Necesitamos un levantamiento del lote para diseño.»
**Esperado:** no elegir método (topografía convencional, fotogrametría, LiDAR). Servicio = `no indicado`; preguntar objetivo, ubicación/área y entregables esperados.

## 5. Adjunto ilegible
**Correo:** cotización solicitada con un plano DWG o un PDF escaneado adjunto.
**Esperado:** registrar el adjunto y el motivo; `recommended_status=revision_manual`; no tratar el adjunto como ausencia de datos ni inventar su contenido.

## 6. Respuesta parcial en el mismo hilo
**Tabla previa:** ubicación y magnitud pendientes. **Respuesta:** «Es en Cesar, unas 300 ha; el resto lo confirmo mañana.»
**Esperado:** actualizar ubicación y magnitud como confirmadas; conservar las demás pendientes; el alcance **no** pasa a completo; preguntar solo lo que sigue faltando.

## 7. Contradicción entre correo y adjunto
**Correo:** «80 ha»; el KMZ adjunto encierra unas 120 ha.
**Esperado:** actividad en estado `contradictorio`, con ambas fuentes; `revision_manual`; no crear carpeta.

## 8. Reenvío interno
**Correo:** un comercial de Anka reenvía la solicitud de un cliente.
**Esperado:** identificar al cliente original dentro del mensaje, pero dirigir el borrador al comercial que reenvió (`forwarded_reply_policy=internal_reviewer`).

## 9. Proyecto con varios componentes
**Correo:** parque solar, línea de conexión y bahía de subestación.
**Esperado:** actividades separadas por componente, cada una con su propia magnitud y estado.

## 10. Fuga de contexto interno
**Situación:** el índice histórico muestra que un cliente similar pidió cierto entregable.
**Esperado:** usar el dato solo en la sección interna marcada; el correo al cliente nunca menciona otros clientes, precios ni patrones históricos (ver `POLITICA_CONTEXTO_COMERCIAL.md`).

## 11. Pregunta de valor sin la palabra «cotización» (R1)
**Correo:** «¿Cuánto vale medir un lote de 5 hectáreas en Cota? Necesito tiempos.» de una persona.
**Esperado:** pasa a análisis y se identifica como solicitud. Nunca se descarta por no usar palabras clave.

## 12. Boletín o notificación automática (R1, R3)
**Correo:** boletín con enlace de baja, o un aviso `no-reply`, sin mención de cotizaciones.
**Esperado:** se descarta sin analizar **y** guarda su motivo para que una persona lo revise. Si el aviso automático menciona una cotización (por ejemplo, un portal de un cliente), se analiza.

## 13. Cliente nuevo (R5, R6, R7)
**Situación:** alcance completo de un cliente que no tiene carpeta en Drive.
**Esperado:** no crear la carpeta del cliente ni la del proyecto por cuenta propia; dejar el caso esperando aprobación diciendo que falta confirmar el cliente. «Energía Norte S.A.S.» y «ENERGIA NORTE» se consideran el mismo cliente; «Energía Norte» y «Norte Energía» no.

## 15. Cambios después de la T (R11)
**Situación:** el caso ya tiene la T `T26145`; el contacto registrado es Ana. Llega un mensaje que pide cambiar el plazo.
**Esperado:** solo si lo envía Ana **y** cita `T26145` (o `T&E26145`) la tabla se actualiza. Si lo envía otra persona (aunque sea de la misma empresa), de otro dominio, o no cita el código, la tabla **no cambia**: el cambio queda como propuesta pendiente. En todos los casos informa `requested_by_email`, `requested_by_name` y `cited_codes`. Los casos exactos están en `casos_paridad.json` (clave `cambios`).

## 14. Cliente conocido (R4, R5, R7)
**Situación:** alcance completo de un remitente cuyo cliente ya confirmó una persona y cuya carpeta existe, en el año de la carpeta padre.
**Esperado:** crear `{año}/{cliente}/{solicitud}`; si la carpeta padre es de otro año, no crear nada y avisar.
