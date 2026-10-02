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
