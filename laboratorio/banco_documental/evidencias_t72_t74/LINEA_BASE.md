# Línea base de las pruebas T-72, T-73 y T-74 (congelada)

Resultados de la primera ejecución de Work sobre cada caso del banco T-71, tal como se evaluaron. **No son precisión estadística
generalizable**: es un documento por tipo y una sola ejecución cada uno.

| Caso | Documento | Formato de la respuesta | Actividades | Críticos | Observaciones |
|---|---|---|---|---|---|
| C1 | PDF digital | JSON válido | 5/5 correctas (valor, unidad, origen) | 0 | 3 no críticos del comparador v1: sistema de coordenadas sin pregunta, «2,350 km» sin declarar ambigüedad (real), una pregunta marcada «de más» (falso positivo) |
| C2 | PDF escaneado | JSON válido | 4/4 correctas | 0 | La revisión visual la hizo el creador del banco (no independiente). 2 `entregable_omitido` = defecto de la referencia; 2 revisiones internas con criterio opinable; 1 pregunta pertinente marcada «de más» |
| C3 | Excel | **JSON inválido** (comillas sin escapar, línea 378, columna 37, car. 12754) | 4/4 correctas según evaluación manual | 0 manual | Comparador bloqueado: no se reparó la respuesta. Fallo de formato, no de lectura |

Hashes verificados (21/10/2026): ver `manifiesto_evidencias.json`. Los documentos, las referencias v1 y las respuestas originales no se
alteran; las respuestas se guardaron con LF y sin salto final y reproducen el SHA-256 que dio el usuario (C2 y C3) o el calculado al
guardarlas (C1).
