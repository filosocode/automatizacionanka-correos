# Especificación del proyecto

Esta carpeta es la **fuente de verdad** de lo que el sistema debe hacer. El código, las pruebas y la skill de ChatGPT
Work se derivan de aquí y se comparan contra aquí.

| Documento | Qué responde |
|---|---|
| [00-alcance.md](00-alcance.md) | ¿Qué es la Fase 1, qué no se hace y cuándo está terminada? |
| [01-flujo-y-estados.md](01-flujo-y-estados.md) | ¿Cómo avanza un caso? (estados y transiciones; datos en `transiciones.json`) |
| [02-reglas.md](02-reglas.md) | Índice de las reglas operativas R1 a R13 (texto en `skills/.../reglas_operativas.md`) |
| [03-contrato-work.md](03-contrato-work.md) | ¿Qué debe entregar ChatGPT Work y cómo se revisa la evidencia de sus preguntas? (esquema en `contrato_work.schema.json`) |
| [04-ux.md](04-ux.md) | ¿Cómo debe sentirse el dashboard? (criterios y nota vigente) |
| [05-decisiones.md](05-decisiones.md) | ¿Por qué se decidió así? (registro de decisiones) |
| [06-arquitectura.md](06-arquitectura.md) | ¿Cómo está organizado el código? (capas y reglas de dependencia) |
| [07-api.md](07-api.md) | ¿Cómo se documenta y se prueba cada petición? (Swagger UI y OpenAPI) |
| [requisitos.md](requisitos.md) | Lista de requisitos con identificador, uno por línea de trazabilidad; incluye los cambios de trazabilidad (T-xx) y de experiencia (U-xx) |

## Cómo se trabaja con ella
1. **Cambio de comportamiento ⇒ empieza aquí.** Edita el requisito, la regla, el esquema o la tabla de
   transiciones; después el código; después la skill de Work si aplica (`docs/ESTANDAR_CAMBIOS.md`).
2. **Cada requisito tiene pruebas.** Las pruebas se etiquetan con `# Cubre: ID` y `python check_spec.py` falla si un
   requisito queda sin prueba o si una etiqueta apunta a algo que no existe. Corre en `check.bat` y en el CI.
3. **Los datos mandan.** `transiciones.json` y `contrato_work.schema.json` no son documentación: se ejecutan en las
   pruebas (`tests/test_transiciones.py`, `tests/test_contrato_work.py`).
4. **Lo que Python no puede probar** (cómo interpreta ChatGPT la skill) se registra como prueba manual con fecha y
   versión de las reglas, siguiendo `docs/PILOTO_ACEPTACION.md`.

## Qué no es esta carpeta
No describe pantallas píxel a píxel ni cómo está escrito el código: eso cambia a diario y se lee en el código.
