# Cambios propuestos para las referencias v2 (T-75)

Las referencias v1 (`referencias/`) siguen congeladas y son la línea base. Las v2 (`referencias_v2/`) se **derivan** de ellas con
`python -m laboratorio.banco_documental.referencias_v2` y cada archivo v2 guarda el SHA-256 de su v1 (`deriva_de`). Los
documentos y las respuestas originales de Work no cambian.

**Advertencia:** la v2 se diseñó después de ver las respuestas de C1, C2 y C3. Reevaluarlas con la v2 mide el efecto de los
cambios, no es una evaluación ciega, y no debe presentarse como mejora de Work.

## Cambios transversales (comparador v2)

- Una respuesta que no pasa el validador estructural no se compara (`NO_EVALUABLE_POR_FORMATO`).
- Cada coincidencia de texto exige todos los grupos de la referencia (cada grupo = sinónimos), no una palabra suelta.
- Las preguntas no previstas ya no son «de más»: van a revisión humana. Los criterios técnicos opinables y la interpretación
  visual también; cada hallazgo declara si es de verificación `automatica` o `humana`.

## Cambios por caso

| Id | Caso | Qué cambia | Por qué | Origen | Efecto |
|---|---|---|---|---|---|
| V2-C1-1 | C1 | El sistema de coordenadas (C1-F2) pasa a destino «cliente_o_revision_interna»: se reconoce en preguntas o en revisiones internas. | La Nota 6 del documento ya difiere su definición a la reunión de inicio; Work lo trató como revisión interna y no preguntó. | respuesta de Work C1 (T-72) | C1-F2 deja de contarse como omisión de Work (v1: no crítico). |
| V2-C1-2 | C1 | La ambigüedad de «2,350 km» sale de las revisiones internas y pasa a una lista de ambigüedades (C1-M1), reconocible en limitaciones, revisiones, preguntas o contradicciones. | Era un juicio interno que el prompt ya pedía declarar como ambigüedad de lectura. | T-72 (informe C1) | Sigue siendo un hallazgo no crítico si Work no la declara (en C1 no la declaró). |
| V2-C1-3 | C1 | C1-F1, C1-F3 y C1-I1 exigen dos conceptos, no una palabra suelta. | Evitar coincidencias por una sola palabra. | T-73 (límites del comparador) | C1-F3 exige «12» y «10»; C1-F1, un término de polígono y otro de predio o alcance. |
| V2-C1-4 | C1 | Se añade C1-K2 (Nota 7 frente a planta y fachada) como contradicción opcional. | Work la señaló y está fundada en el documento; la referencia v1 no la contemplaba. | respuesta de Work C1 (T-72) | Si aparece, se cuenta como hallazgo adicional correcto; si no, no se penaliza. |
| V2-C2-1 | C2 | Se retiran los entregables de las cuatro actividades. | El PDF no tiene columna ni texto de entregables: la v1 los inventó. Work respondió correctamente «no se especifica». | revisión de T-73 | Desaparecen los dos `entregable_omitido` de C2 (eran un defecto de la referencia). |
| V2-C2-2 | C2 | C2-I1 (ecosonda de doble frecuencia) pasa a juicio técnico: no se penaliza su ausencia y se reporta como revisión humana. C2-I2 (nivel de referencia vertical) se acepta en revisiones o en preguntas. | Son criterios técnicos opinables; Work cubrió el datum vertical como pregunta al cliente. | T-73 | Desaparecen las dos `revision_interna_omitida` de C2. |
| V2-C2-3 | C2 | Se añade C2-K2 («sin escala» frente a la barra de escala) como contradicción opcional. | Es una incoherencia real que mi generación del PDF dejó y Work detectó. | respuesta de Work C2 (T-73) | Hallazgo adicional correcto si aparece; no se penaliza si falta. |
| V2-C2-4 | C2 | C2-F1 y C2-F3 exigen dos conceptos. | Evitar coincidencias por una sola palabra. | T-73 | C2-F3 exige hablar de área y de confirmación/vigencia. |
| V2-C3-1 | C3 | Las restricciones numéricas aceptan coma o punto decimal («0,5» o «0.5», «0,02» o «0.02»). | El libro guarda 0.5 y 0.02; exigir coma penalizaba a quien conservaba el literal. | T-74 (límite conocido) | Desaparecen las dos `restriccion_omitida` falsas. |
| V2-C3-2 | C3 | Se declara C3-F1: preguntar la discrepancia 7/6 lotes es una pregunta esperada (no bloqueante, afecta cantidades). | La v1 no tenía faltantes y toda pregunta salía «de más»; la discrepancia sí cambia el área y los 24 puntos. | T-74 | La pregunta de los 7 lotes deja de ser «pregunta de más». |
| V2-C3-3 | C3 | Se añaden C3-K3 y C3-K4 (otras dos diferencias de la hoja oculta) como contradicciones opcionales. | La hoja oculta difiere en las tres actividades; la v1 solo recogía una. | T-74 | Hallazgos adicionales correctos si aparecen; no se penalizan si faltan. |
| V2-C3-4 | C3 | C3-K1 exige «7», «6» y «lote/predio» juntos; C3-I1 exige hoja oculta y vigencia. | Evitar coincidencias por una sola palabra. | T-73 | Menos falsos aciertos. |

## Efecto medido sobre las respuestas originales (no ciego)

| Caso | v1 (críticos / no críticos / no evaluables) | v2 (críticos / no críticos / revisión humana / aciertos adicionales) |
|---|---|---|
| C1 | 0 / 3 / 0 | 0 / 1 / 0 / 1 |
| C2 | 0 / 5 / 3 | 0 / 0 / 6 / 1 |
| C3 | bloqueado (JSON inválido) | `NO_EVALUABLE_POR_FORMATO` (la respuesta no se compara ni se repara) |

Qué no cambia: los criterios críticos (cantidad, unidad, trampas, faltante bloqueante, contradicción crítica), los documentos y el
resultado de las cuatro y cinco actividades. El 0 de no críticos en C2 se debe en parte a haber retirado lo que la v1 exigía sin
respaldo en el documento; en C1 queda un no crítico real (la ambigüedad de «2,350 km» no se declaró).
