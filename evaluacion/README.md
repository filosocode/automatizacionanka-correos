# Muestra etiquetada para medir el piloto

Objetivo: medir con correos reales antes de ampliar la automatizacion (`docs/PLAN_FASE_1_PLUS.md`, pasos 2 y 6).

## Como llenarla
1. Reunir de 15 a 30 solicitudes historicas variadas: completas, incompletas, respuestas parciales, con adjuntos legibles e ilegibles, reenvios y ruido que no es solicitud.
2. **Anonimizar** antes de guardar: sin nombres de personas ni empresas, correos, telefonos, precios ni coordenadas exactas. Guardar los textos originales fuera del repositorio (carpeta de datos `%LOCALAPPDATA%\AnkaCorreos`), y en el CSV solo un identificador (`C001`…).
3. Un integrante de Anka completa el resultado esperado de cada caso en `muestra_etiquetada.csv`. Escribir `no indicado` cuando el correo no lo diga; no completar por suposicion.

## Metricas a calcular
- Solicitudes perdidas (era solicitud y se descarto) — la mas grave.
- Falsas solicitudes (no lo era y se proceso).
- Exactitud de actividades, ubicacion, magnitud y unidad.
- Preguntas innecesarias o repetidas al cliente.
- Casos enviados a revision manual y su motivo.
- Tokens y costo por solicitud completada.
- Duplicados de borrador o de carpeta.

## Criterio de salida
Decidir con estos numeros si se mantiene el prefiltro por palabras clave, si se pasa a un motor mas fuerte y que acciones pueden automatizarse sin revision.

## Ejecución reproducible

`python evaluacion/evaluar.py muestra_etiquetada.csv resultados.json informe.json`

El JSON de entrada contiene `modelo`, `prompt` (versión y hash del archivo), `fecha` ISO,
`tipo_corpus` (`real_anonimizado` o `sintetico`) y `casos`. Cada caso incluye `id_caso`,
`es_solicitud` (`si`/`no`), `servicio`, `ubicacion`, `magnitud`, `unidad`, `entregables`, `plazo`,
`datos_faltantes` y `estado`. Registra resultados realmente observados; no copies las etiquetas esperadas.
Usa `no indicado` cuando corresponda. El comando rechaza muestras incompletas o identificadores repetidos.

El informe conserva hashes de las entradas y mide acuerdo textual por campo, solicitudes perdidas y falsas
solicitudes. Equivalencias semánticas, calidad de preguntas, costo y duplicados requieren revisión del piloto;
no se infieren de estos porcentajes. Las pruebas sintéticas de la herramienta no miden precisión de un modelo.

Estado al 2026-10-08: **aceptación con corpus real pendiente**. El CSV incluido es una plantilla sin etiquetas;
no hay base para afirmar un porcentaje de precisión. Registrar aquí el informe y el piloto fechado cuando Anka
complete la evaluación. Hasta entonces se conserva la revisión humana de cada respuesta.
