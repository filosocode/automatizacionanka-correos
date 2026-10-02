# Contrato con ChatGPT Work

La tarea de ChatGPT Work no ejecuta código de este repositorio: analiza los correos y deja en Gmail un **borrador**
cuyo texto interno lleva un bloque JSON. El puente (`work_dashboard_sync.py`) lo lee y lo importa al dashboard.
Requisito: **T-02**. La definición formal es [`contrato_work.schema.json`](contrato_work.schema.json): se valida en el
puente y en `tests/test_contrato_work.py`.

## El borrador
```
[REVISAR ANTES DE ENVIAR - BORRAR ESTA SECCION]
… correo original, resumen, actividades y preguntas (para la persona) …
[ANKA_DASHBOARD_JSON_V1]
{ … el JSON del contrato … }
[/ANKA_DASHBOARD_JSON_V1]
Hola, … (el correo limpio para el cliente)
```
- El bloque debe estar **una sola vez** y dentro de la sección interna inicial; nunca se copia desde correos citados.
- Lo que sigue al bloque es el **correo para el cliente**: el dashboard lo muestra en «Respuesta al cliente» y, al
  enviar, **usa ese mismo borrador** (así no queda uno huérfano con la sección interna).

## Campos
Obligatorios: `thread_id`, `message_id`, `client_email`, `project_name`, `status` y `engine` (= `chatgpt_work`).
Recomendados: `client_name`, `summary`, `received_at`, `gmail_url`, `activities`, `missing_data`, `attachments`,
`contradictions`, `scope_dependencies`. Control de cambios (R11): `requested_by_email`, `requested_by_name`,
`cited_codes`. El puente agrega `draft_reply` y `draft_id`. Los valores `null` en campos opcionales se tratan como
ausentes; los campos desconocidos se toleran.

## Estado
`status` es una de las etiquetas oficiales del esquema; **se acepta sin tildes ni mayúsculas** y se normaliza. El estado
de cada actividad sigue R12.

## Qué hace el dashboard con el JSON
- Mapea `status` al estado interno (`transiciones.json`, filas `work_import`).
- Aplica R11: con T asignada, la tabla solo se actualiza si lo escribió el contacto registrado y cita el código;
  si no, lo propuesto queda como propuesta pendiente.
- Normaliza el estado de las filas (R12) y guarda el correo limpio como borrador propuesto.

## Cambiar el contrato
1. Edita el esquema (y este documento si cambia el sentido).
2. Actualiza `docs/INSTRUCCIONES.txt` (lo que se pega en la tarea de ChatGPT): `tests/test_contrato_work.py` falla si un campo del
   esquema no está documentado ahí.
3. Actualiza la skill de Work (`SKILL.md`) y repite la prueba manual (`docs/PILOTO_ACEPTACION.md`).
