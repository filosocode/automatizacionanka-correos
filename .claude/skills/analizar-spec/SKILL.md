---
name: analizar-spec
description: Revisa sin modificar nada la consistencia entre spec/, las reglas operativas, la skill de Work, el código y las pruebas, y entrega un informe de hallazgos por gravedad. Úsala cuando se pida analizar, auditar o verificar la coherencia de la especificación o de un cambio antes de darlo por hecho.
---

# Analizar la coherencia de la especificación (solo lectura)

No edites ningún archivo. Entrega un informe; las correcciones las decide la persona.

## Qué cruzar
Si la persona nombra un requisito o cambio, limita el análisis a él; si no, revisa lo cambiado en la rama
(`git diff main...HEAD --stat`).

1. **Constitución** (`spec/constitucion.md`): cualquier choque con sus principios es **CRÍTICO**.
2. **Requisito ↔ pruebas**: cada requisito en `spec/requisitos.md` tiene pruebas con `# Cubre: ID`; ninguna etiqueta apunta
   a algo inexistente (`python check_spec.py` lo comprueba: ejecútalo y cita su salida).
3. **Requisito ↔ reglas**: sin contradicción con R1–R13 (`skills/anka-cotizaciones-correo/references/reglas_operativas.md`)
   ni con `spec/02-reglas.md`.
4. **Estados**: `spec/transiciones.json`, `STATUS_LABELS`, `_case_stage` y `tests/test_transiciones.py` dicen lo mismo.
5. **Contrato con Work**: `spec/contrato_work.schema.json`, `spec/03-contrato-work.md`, la skill y el prompt
   (`docs/PROMPT_WORK_ANKA_V6.txt`) coinciden.
6. **Decisiones**: nada deshace una decisión de `spec/05-decisiones.md` sin motivo nuevo.
7. **Arquitectura y rutas**: módulos nuevos en el «Mapa de módulos» de `spec/06-arquitectura.md`; rutas nuevas en
   `anka/web/openapi.py`.
8. **Prueba manual**: lo que depende de cómo interpreta ChatGPT la skill tiene su registro en `docs/PILOTO_ACEPTACION.md`;
   no cuentes como cumplido lo que solo pasó con pruebas simuladas.
9. **Redacción**: términos distintos para lo mismo, requisitos vagos («rápido», «fácil») sin criterio medible,
   pendientes sin dueño.

## Formato del informe
Tabla `Id | Gravedad (CRÍTICO/ALTO/MEDIO/BAJO) | Dónde (archivo:línea) | Hallazgo | Recomendación`, máximo 30 filas
(el resto, resumido). Después: requisitos sin prueba, qué sigue pendiente de aceptación manual y el siguiente paso
sugerido. Cada hallazgo debe poder verificarse leyendo el archivo citado; no inventes.
