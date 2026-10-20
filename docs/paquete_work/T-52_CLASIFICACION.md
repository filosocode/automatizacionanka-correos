# T-52 · Clasificación comercial de cada mensaje (adición al contrato V1)

El contrato `ANKA_DASHBOARD_JSON_V1` **no cambia**. Se le añaden dos campos **opcionales**:

- `classification`: una de `SOLICITUD_NUEVA`, `SEGUIMIENTO`, `NO_SOLICITUD`, `REVISION_MANUAL` (exactamente así, en mayúsculas).
- `classification_reason`: una frase que explica por qué (sin copiar texto largo del correo).

Los campos obligatorios no cambian: `thread_id`, `message_id`, `client_email`, `project_name`, `status`, `engine`
(= `chatgpt_work`). `PRECISO_V6.1.3` además exige `skill_version` = `PRECISO_V6.1.3`. Todo lo demás de `PRECISO_V6.1.3` sigue vigente.

Se mantienen independientes: la clasificación, el estado del expediente, la completitud del análisis y la autorización para
contactar al cliente. Ninguna se deduce de las otras. El estado `publicando` de la hoja `TRABAJO` (`estado_del_trabajo`) es una
instantánea del momento en que se creó la hoja; el estado operativo definitivo vive en la base de Python. No es un motivo para
vaciar el análisis.

## Qué te llega
La hoja `TRABAJO` del trabajo trae `clasificacion_preliminar` y `motivo_preliminar`: es **solo una pista** de Python, calculada con el
asunto, el fragmento y los nombres de adjuntos. **Tú lees el mensaje completo y decides.** Confírmala o corrígela. No decidas por
palabras clave ni descartes por el remitente: un mensaje de una persona no es por sí solo una solicitud.

## Las cuatro clasificaciones
| Clase | Cuándo | `status` coherente | Qué hace Python al importarla |
|---|---|---|---|
| `SOLICITUD_NUEVA` | Una persona pide, directa o indirectamente, un servicio de Grupo Anka (topografía, geomática, LiDAR, fotogrametría, batimetría, inspección, georradar, catastro, escáner terrestre) o una cotización. | el que corresponda al análisis (p. ej. `Requiere aclaraciones del cliente`) | Sigue su camino: tabla de actividades, faltantes y respuesta preparada. |
| `SEGUIMIENTO` | El mensaje continúa una solicitud ya existente del hilo (responde, aclara, aporta datos). | el que corresponda | Igual que la anterior. |
| `NO_SOLICITUD` | Con el mensaje leído estás seguro de que no pide nada de Anka (saludo, mensaje interno, aviso sin relación). | `Descartada` | El caso se descarta, sin actividades ni respuesta. En un hilo con expediente existente queda en revisión interna en vez de descartarse. |
| `REVISION_MANUAL` | Hay duda real, el mensaje no se pudo leer bien, o puede ser solicitud pero no estás seguro. | `Requiere revisión técnica interna` | Queda en revisión interna **sin** actividades, preguntas ni respuesta preparada; decide una persona. |

Ante la duda entre `NO_SOLICITUD` y `REVISION_MANUAL`, elige **`REVISION_MANUAL`**. Nunca descartes una solicitud potencial.

## Cómo llenar el JSON en cada caso
- `SOLICITUD_NUEVA` / `SEGUIMIENTO`: análisis completo como en `PRECISO_V6.1.3`, más `classification` y `classification_reason`.
  **Revisión interna no es bloqueo (T-57).** El estado `Requiere revisión técnica interna` no vacía la tabla. Si el mensaje se
  pudo leer, conserva `activities` (una por servicio pedido, con `fuente`; `quantity`, `unit` y `entregable` solo si el texto los
  dice; `estado` = `pendiente`), `fields`, `missing_data` y `evidence_register` (`unconfirmed` o el que corresponda), aunque el caso sea
  de prueba, ficticio o de laboratorio, tenga datos sin vigencia, contradicciones o lectura parcial documentada. Mantén
  `analysis_complete` = `false`, usa `internal_review` para lo interno (`ask_client` solo si el caso es real y falta algo
  indispensable) y no escribas texto para el cliente. Solo se vacía la tabla con un **bloqueo documentado** (mensaje o adjunto
  esencial ilegible, identidad o hilo que no coinciden con la hoja) o con `REVISION_MANUAL`. Ver `ejemplo_SOLICITUD_NUEVA_REVISION_INTERNA.json`.
  No inventes métodos, equipos, precisión, sistemas de referencia, cantidades, fechas ni precios.
- `NO_SOLICITUD`: `status` = `Descartada`; `activities`, `fields`, `missing_data`, `evidence_register`, `question_audit`,
  `contradictions` y `scope_dependencies` vacíos (`[]`); `attachments` con los adjuntos reales del mensaje (o `[]` si no hay);
  `summary` con el motivo; `analysis_complete` = `true` solo si no hay adjuntos sin leer. **Sin texto para el cliente.**
- `REVISION_MANUAL` (y solo un bloqueo documentado): forma de «BLOQUEO» de `PRECISO_V6.1.3`: `status` = `Requiere revisión técnica interna`, `activities` = `[]`,
  `fields` = `[]`, `analysis_complete` = `false`, `question_audit` solo con `internal_review` y el motivo exacto en `summary`.
  **Sin texto para el cliente.**
- Un valor de `classification` que no sea una de las cuatro se trata en Python como `REVISION_MANUAL` (con aviso): no lo inventes.

## Drive: solo lectura, y solo lo del flujo
`PRECISO_V6.1.3` prohíbe **escribir** en Drive, no leerlo. **Esta tarea de la cola lee Drive, solo para leer**: la carpeta `PENDIENTES` (la
hoja del trabajo) y esta carpeta de referencia. No escribe, no mueve, no borra ni crea archivos o carpetas en Drive. Python mueve las
hojas a `CERRADOS`. Para todo lo demás, `PRECISO_V6.1.3` manda.

## Seguimiento con contexto (T-59)
Cuando el hilo ya tiene expediente, la hoja del trabajo trae tres hojas de solo lectura que **no cambian el esquema**:
- `CONTEXTO_PREVIO` (campo/valor): el expediente, su estado, el último mensaje analizado, el `mensaje_por_analizar`, si ese mensaje
  `ya se analizó`, si una persona editó la tabla, si hay una propuesta pendiente (y sus motivos), las contradicciones abiertas y
  si el contexto se recortó.
- `ACTIVIDADES_VIGENTES`: la tabla que Python tiene, con las diez columnas y su fuente.
- `PREGUNTAS`: cada pregunta con su `tipo` (`enviada`, `propuesta_no_enviada`, `interna`); las enviadas llevan la ronda, la fecha,
  el mensaje de Gmail y su estado (`enviada`, `sigue_abierta`, `sin_confirmar`) y el mensaje que las siguió.

Es un **estado provisional** de Python: contrástalo con el hilo. Devuelve la tabla **acumulada** (las filas vigentes más lo nuevo,
con el mismo `activity` cuando es la misma actividad). Que el mensaje nuevo no mencione una actividad **no** la elimina; si el
cliente la retira, conserva la fila y escribe «ya no se requiere» en `notes`. Un dato vigente que el mensaje contradice no se
sobrescribe: marca la fila `contradictorio` y deja el caso en revisión interna. Python integra tu tabla con las vigentes: aplica solo lo
seguro (agregar, completar un dato vacío) y deja lo demás como propuesta para una persona. Una pregunta `sigue_abierta` o
`sin_confirmar` no está respondida sin una respuesta explícita en el hilo. Una `SOLICITUD_NUEVA` no trae estas hojas.
