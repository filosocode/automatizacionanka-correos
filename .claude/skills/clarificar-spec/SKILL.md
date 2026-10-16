---
name: clarificar-spec
description: Antes de programar un cambio, detecta lo que le falta o es ambiguo al requisito en spec/ y hace hasta 5 preguntas dirigidas; las respuestas se escriben de vuelta en spec/ (requisitos.md, 05-decisiones.md). Úsala cuando se pida aclarar, afinar o revisar un requisito o idea antes de implementarlo.
---

# Clarificar un requisito antes de programarlo

`spec/` es la única fuente de verdad (ver `spec/constitucion.md`). Esta skill no crea documentos paralelos: todo lo que
se decida queda escrito en `spec/`.

## Pasos
1. Identifica el requisito: un id existente (`T-xx`, `U-xx`, `R1`–`R13`) o la idea nueva que describe la persona. Lee su
   texto en `spec/requisitos.md`, las reglas relacionadas (`skills/anka-cotizaciones-correo/references/reglas_operativas.md`),
   `spec/transiciones.json` si toca estados, y las decisiones afines en `spec/05-decisiones.md` (no las deshagas sin
   leer por qué se tomaron).
2. Busca vacíos que cambiarían el diseño o las pruebas, no detalles de estilo. Revisa en este orden:
   - alcance: qué entra y qué no (¿cotiza? no, Fase 1 no tiene precios);
   - comportamiento y estados: transiciones, qué ve la persona, qué queda en el historial (quién, cuándo, qué);
   - datos y contrato con Work: campos nuevos, `spec/contrato_work.schema.json`, paridad entre los tres modos;
   - casos límite y fallos: Gmail/Drive caídos, datos ilegibles (nunca se concluye «no hay» por no haber podido leer);
   - privacidad y seguridad: R8 (nada sale al cliente sin revisión), datos fuera del repo;
   - cómo se prueba: qué prueba automática lo cubre y si exige prueba manual en Work.
3. Haz **hasta 5 preguntas**, una por vez, solo las de mayor impacto. Cada una con una recomendación tuya y 2–4 opciones
   cuando sea de elegir. Si algo se resuelve leyendo `spec/` o el código, no lo preguntes.
4. Después de cada respuesta, escríbela de inmediato en `spec/`:
   - el comportamiento acordado → el requisito en `spec/requisitos.md` (conserva su id; si es nuevo, usa el siguiente
     T-xx/U-xx y añade «casos de aceptación»);
   - el porqué de una decisión → una entrada nueva en `spec/05-decisiones.md`.
5. Cierra con un resumen breve: preguntas hechas, secciones de `spec/` tocadas, vacíos que siguen abiertos y siguiente
   paso (`docs/ESTANDAR_CAMBIOS.md`: casos → código → skill de Work → pruebas → prueba manual).

## No hagas
- Crear carpetas o archivos de especificación fuera de `spec/`.
- Tocar código ni pruebas: esta skill solo aclara y documenta.
- Marcar algo como aceptado si falta la prueba manual en Work.
