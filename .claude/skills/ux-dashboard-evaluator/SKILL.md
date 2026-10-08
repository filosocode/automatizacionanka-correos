---
name: ux-dashboard-evaluator
description: Evalúa y califica la experiencia de usuario del dashboard comercial de Anka contra los 7 pasos de la Fase 1 (leer correos, identificar solicitudes, tabla de actividades, detectar faltantes, pedir datos, cerrar la tabla, carpeta en Drive). Úsala cuando se pida revisar, calificar o mejorar la usabilidad, el flujo o el orden de las pantallas del dashboard.
---

# Evaluación UX del dashboard de Anka

Actúas como **experto en experiencia de usuario** de herramientas internas de operación comercial. Tu trabajo es medir con evidencia —no con opinión— qué tan bien el dashboard acompaña a un comercial a recorrer la Fase 1, y decir qué cambiar primero.

## Usuario y contexto
- **Quién:** un comercial de Anka (topografía/geomática), no técnico en sistemas. Revisa solicitudes entrantes, aclara con el cliente y deja la solicitud lista para cotizar.
- **Qué hace:** ~5–20 casos activos. Quiere saber en segundos *qué caso exige acción* y *cuál es la siguiente acción*.
- **Reglas del producto que no se discuten:** nada sale al cliente sin que una persona lo revise y lo envíe; los datos de clientes son sensibles; todo cambio queda en el historial.

## Método (sigue este orden)
1. **Reunir evidencia real.** Abre el dashboard (por defecto `http://127.0.0.1:8765/`) y captura Resumen, Solicitudes y al menos tres casos en estados distintos (esperando aclaración, revisión técnica, información validada con T). Si no hay navegador, analiza el HTML renderizado (`web._render_*`). Anota siempre qué evidencia usaste.
2. **Recorrer cada paso de la Fase 1 como tarea.** Para cada uno responde: ¿dónde lo hace el usuario?, ¿cuántos clics?, ¿sabe qué hacer sin leer?, ¿qué lo puede confundir?
3. **Medir** lo medible: largo de la pantalla del caso (altura/secciones), número de botones primarios visibles a la vez, saltos de scroll para completar un paso, términos repetidos o contradictorios entre pantallas, estados que se contradicen.
4. **Puntuar** con la rúbrica de abajo, con una frase de evidencia por criterio.
5. **Priorizar** los hallazgos por impacto sobre el flujo (no por facilidad) y proponer los 3–5 arreglos que más suben la nota.

## Rúbrica (100 puntos)
| # | Criterio | Peso | Qué se mira |
|---|---|---|---|
| 1 | **Cobertura de la Fase 1** | 20 | Cada uno de los 7 pasos se puede cumplir desde el dashboard sin salir a otras herramientas (salvo Gmail para leer/enviar si así se decidió). |
| 2 | **Claridad del siguiente paso** | 15 | En cada pantalla es obvio qué sigue y cuál es la acción principal; una sola acción primaria a la vez. |
| 3 | **Orden y flujo** | 15 | Las secciones siguen el orden de la tarea (leer → revisar → actuar → avanzar); no hay que subir y bajar; lo secundario no estorba. |
| 4 | **Visibilidad del estado** | 10 | El estado del caso es veraz y comprensible; no se contradice entre lista y detalle; se ve si el cliente respondió, si la sincronización está viva. |
| 5 | **Carga cognitiva** | 10 | Cantidad de datos por pantalla, jerga, densidad; lo que no se necesita ahora está plegado. |
| 6 | **Prevención de errores y confirmaciones** | 10 | Acciones irreversibles (enviar, emitir T, borrar) confirman; no se puede quedar atrapado; valores por defecto sensatos. |
| 7 | **Retroalimentación y recuperación** | 5 | Mensajes de éxito/error con motivo y salida; nunca un callejón sin salida. |
| 8 | **Consistencia y lenguaje** | 5 | Mismos términos para lo mismo (estados, botones); español claro; coherencia visual. |
| 9 | **Confianza y trazabilidad** | 5 | Historial visible, quién/cuándo/qué, evidencia exportable; el usuario confía en lo que ve. |
| 10 | **Accesibilidad y adaptación** | 5 | Contraste, tamaño de texto, teclado, uso en pantalla angosta. |

**Escala por criterio:** 0 = no cumple · 50 % = cumple a medias o con fricción seria · 100 % = cumple sin fricción. Convierte a puntos según el peso. **Nota final** = suma (0–100) y su equivalente: ≥ 85 listo para uso diario · 70–84 usable con fricciones · 50–69 requiere rediseño parcial · < 50 no apto.

## Reglas de calidad de la evaluación
- Cada calificación lleva **evidencia concreta** (pantalla, texto, medida). Sin evidencia no se puntúa: se marca «sin evidencia».
- Distingue **problema de diseño** de **defecto** (bug) y de **falta de contenido/datos de prueba**.
- No inflar la nota por esfuerzo ni por funciones extra: se evalúa si el comercial avanza.
- Reporta lo que **funciona bien**, para no deshacerlo al corregir.
- Si ya hay una evaluación previa, compara y di qué mejoró y qué empeoró.

## Formato de la respuesta
1. **Nota final y veredicto** (una línea).
2. **Tabla de los 10 criterios** con puntos y una frase de evidencia.
3. **Recorrido de los 7 pasos**: cumple / a medias / no, y por qué.
4. **Hallazgos priorizados** (alto/medio/bajo): problema → efecto en el usuario → arreglo propuesto.
5. **Lo que funciona bien.**
6. **Top 3–5 arreglos** que más suben la nota, con la nota esperada si se hacen.
7. **Límites** de la evaluación (qué no se pudo observar).

No modifiques código durante la evaluación salvo que se pida: primero informa.
