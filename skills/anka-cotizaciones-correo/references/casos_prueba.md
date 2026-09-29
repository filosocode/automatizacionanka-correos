# Casos de prueba de referencia

Casos **sintéticos** para probar la skill. No son correos reales de clientes. Sustituir o ampliar con 15 a 30 solicitudes históricas anonimizadas y revisadas por Anka (ver `PLAN_FASE_1_PLUS.md`, paso 2). Cada caso indica el resultado esperado, no la redacción exacta.

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
