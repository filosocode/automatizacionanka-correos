# Constitución del proyecto Anka · Automatización de cotizaciones

Esta constitución guía la planificación y revisión de cambios. La especificación funcional vigente está en [`spec/`](README.md); este archivo no reemplaza sus requisitos, reglas, transiciones ni contratos.

## Principios fundamentales

### I. Evidencia antes que inferencia
El sistema no inventa datos comerciales o técnicos. Cada dato extraído debe poder rastrearse hasta el correo o un adjunto y, cuando aplique, incluir un localizador verificable. Lo no indicado se conserva como tal; la lectura parcial, ilegible o no verificada nunca se presenta como ausencia de información.

### II. Control humano de las comunicaciones y decisiones
Ningún correo al cliente se envía sin revisión y acción humana, según R8. Las decisiones comerciales, técnicas y de vinculación entre hilos deben quedar bajo control de una persona. La excepción de recordatorios automáticos solo aplica cuando está habilitada expresamente por caso, según R13. ChatGPT Work no envía correos ni crea carpetas.

### III. Trazabilidad y preservación del historial
Las decisiones, cambios de datos y acciones humanas relevantes deben conservar quién actuó, cuándo y sobre qué información. No se debe perder una versión o evidencia previa al reconciliar resultados, editar una tabla, cambiar el estado o archivar un caso. El manejo de datos sensibles debe seguir las reglas de privacidad de `spec/`.

### IV. Paridad de reglas entre modos
Los modos `local`, `api` y `chatgpt_work` deben aplicar las mismas reglas operativas y producir resultados compatibles con el contrato común. Las reglas compartidas se mantienen en `skills/anka-cotizaciones-correo/references/reglas_operativas.md`; cualquier cambio aplicable a Work también se refleja en su skill y prompt, y se valida manualmente cuando corresponda.

### V. Arquitectura simple y dependencias controladas
El código reside en `anka/` y respeta las capas y dependencias unidireccionales definidas en `spec/06-arquitectura.md`. Las funciones de dominio permanecen independientes de Gmail, Drive, SQLite, modelos y servidor web. Los puntos de entrada de la raíz son delgados. No se agregan dependencias externas sin justificar necesidad, mantenimiento y costo operativo.

## Restricciones del producto

- El alcance vigente es preparar solicitudes para cotización: leer correos, identificar solicitudes, ordenar actividades, detectar faltantes, apoyar aclaraciones y organizar el caso en Drive. El sistema no calcula precios ni emite una cotización en la Fase 1.
- La interfaz y los documentos de uso están en español.
- Los datos, tokens, credenciales y la base operativa permanecen en la carpeta local de datos fuera del repositorio. Los registros no deben exponer contenido de clientes ni secretos.
- Solo un orquestador puede revisar la cuenta de Gmail a la vez.
- Las integraciones externas deben fallar de forma visible y recuperable, sin duplicar envíos ni convertir un fallo de lectura en una conclusión comercial.

## Flujo de trabajo y criterios de calidad

1. Todo cambio de comportamiento comienza actualizando el requisito, regla, contrato o transición correspondiente en `spec/` antes de modificar código.
2. Los requisitos conservan identificadores estables y trazabilidad a pruebas mediante etiquetas `Cubre:`. La especificación ejecutable se valida con `python check_spec.py`.
3. Los cambios que alteren el contrato de Work deben mantener compatible `spec/contrato_work.schema.json`, el importador, la skill y el prompt. Los casos de integración con Gmail y Drive deben cubrirse con pruebas aisladas; los comportamientos propios de Work requieren evidencia de aceptación manual.
4. Las rutas del dashboard se documentan en OpenAPI y las respuestas que cambian deben actualizar sus pruebas de presentación cuando corresponda.
5. Antes de considerar completo un cambio, se ejecutan las verificaciones del proyecto definidas en `check.bat` y CI (lint, tipos, trazabilidad, pruebas y cobertura mínima configurada). Las pruebas no sustituyen la aceptación manual requerida por la especificación.
6. No se relajan requisitos de seguridad, privacidad, cobertura o validación para hacer pasar una entrega. Las decisiones y excepciones se registran en `spec/05-decisiones.md`.

## Gobernanza

`spec/` es la fuente de verdad funcional y prevalece sobre esta guía, README, prompts antiguos y documentación derivada. Si una propuesta, borrador o análisis difiere de `spec/`, primero se actualiza y revisa la fuente de verdad existente; no se mantiene una segunda copia de los requisitos del producto.

Una propuesta de cambio debe enlazar los requisitos existentes y reflejar con honestidad qué está implementado, qué está probado automáticamente y qué sigue pendiente de aceptación manual. No se declara completa una integración externa solo porque el código y sus pruebas simuladas estén completos.

Las modificaciones de esta constitución deben actualizar su versión y fecha, y cualquier cambio de comportamiento debe seguir `docs/ESTANDAR_CAMBIOS.md`.

**Versión**: 1.1.0 | **Ratificada**: 2026-10-13 | **Última enmienda**: 2026-10-14 (movida a `spec/`; se retira Spec Kit)
