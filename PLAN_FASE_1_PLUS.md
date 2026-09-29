# Fase 1: cotizaciones con ChatGPT Plus

**Objetivo:** probar el flujo completo desde un correo entrante hasta una tabla de alcance y una carpeta de proyecto en Drive, usando la suscripción Plus existente y sin contratar n8n ni API de IA. La activación automática queda condicionada a las acciones disponibles en las conexiones de Gmail y Drive.

## Infraestructura mínima

| Componente | Función |
|---|---|
| ChatGPT Work en la web | Ejecutar una tarea cuando llega un correo nuevo de Gmail. Las tareas por evento se configuran en web o móvil. |
| Skill `anka-automation` | Contexto de Anka, criterios de análisis y formato de salida. Comprobar su disponibilidad en Work web; la instalación local en Codex no implica que aparezca allí automáticamente. |
| Gmail conectado | Leer mensaje e hilo; comprobar si puede preparar y enviar respuestas en el mismo hilo. |
| Google Drive conectado | Inspeccionar la estructura existente; comprobar si puede crear carpetas en la ubicación elegida. |
| Registro persistente | Una tabla en Google Sheets dentro de Drive, si la conexión permite crearla y actualizarla. Clave única: ID del hilo de Gmail. |

El registro debe contener, al menos: ID de hilo, remitente, fecha, servicio probable, estado, versión de la tabla, datos faltantes, fecha/ID de la pregunta enviada y ID/URL de la carpeta. El historial de Gmail y los archivos originales siguen siendo las fuentes; el registro permite reanudar y evitar acciones duplicadas.

## Pasos de desarrollo y criterio de salida

1. **Verificar las conexiones.** Conectar la cuenta correcta de Gmail y Drive y comprobar que la skill está disponible para Work web o cargar allí sus instrucciones. Probar lectura de un hilo de ejemplo y de la carpeta de arranque. Probar de forma controlada si Gmail permite responder en el hilo y Drive crear una carpeta. **Salida:** lista comprobada de acciones disponibles; si una acción falta, rediseñar ese tramo antes de programar tareas.
2. **Definir el alcance comercial.** Elegir una familia de servicios para el piloto y documentar qué campos son indispensables en la tabla preliminar y cuáles se requieren para pasar a cotización. Reunir de 15 a 30 solicitudes históricas variadas, incluidas solicitudes incompletas, respuestas parciales y casos con adjuntos. **Salida:** ejemplos con resultado esperado revisados por Anka.
3. **Fijar el formato.** Acordar columnas de la tabla de actividades, estados (`nuevo`, `esperando_cliente`, `revisión`, `alcance_completo`, `carpeta_creada`) y convención de nombre/carpeta padre. Cada dato extraído debe señalar su mensaje o adjunto fuente. **Salida:** plantilla y registro de casos.
4. **Probar manualmente la skill.** Analizar los ejemplos en Work sin tarea automática. Comparar clasificación, actividades y preguntas con las respuestas esperadas; corregir las reglas de la skill. **Salida:** casos de prueba aprobados y excepciones conocidas.
5. **Montar un piloto supervisado.** Crear una tarea de Work activada por Gmail que lea el mensaje y el estado del hilo, use la skill, actualice el registro y prepare una tabla o una propuesta de respuesta. Durante el piloto, una persona revisa cada correo saliente y cada carpeta. **Salida:** un flujo real completo probado de inicio a fin, incluyendo la respuesta del cliente.
6. **Medir antes de ampliar.** Revisar solicitudes perdidas, preguntas innecesarias, tablas erróneas, duplicados, permisos solicitados y límites de uso de Plus. Solo después decidir qué acciones repetibles pueden automatizarse sin revisión. **Salida:** decisión basada en resultados reales sobre continuar con Plus o añadir Python/API.

## Puertas de decisión

- **Gmail sin acción de respuesta habilitada:** Work puede analizar y preparar borradores, pero el envío automático exige otra vía o revisión manual.
- **Drive sin creación de carpetas habilitada:** se puede terminar el alcance, pero el último paso requiere otra vía o una acción humana.
- **Registro en Sheets no disponible o inestable:** no usar la memoria de una conversación como único estado del proceso; definir un registro persistente alternativo.
- **Volumen o límites de Plus insuficientes:** conservar la skill y los casos de prueba; trasladar la orquestación a Python/n8n y pagar solo las consultas de IA necesarias.

`app.py` implementa hoy la vía Python (borradores en Gmail y panel de revisión). Es una alternativa a la tarea de ChatGPT Work: solo uno de los dos orquestadores puede estar activo (ver `MODO_OPERACION.md`). No ejecutar `--apply` sin `--review-only` o `--gmail-drafts` hasta completar la medición del paso 6.
