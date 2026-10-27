# Documentación

| Documento | Para quién | Qué contiene |
|---|---|---|
| [ENTREGA_V1_0.md](ENTREGA_V1_0.md) | Quien recibe y opera la V1.0 | Estado, matriz de aceptación, limitaciones, operación y revisión humana de la entrega supervisada |
| [WORK_DRIVE_OCR.md](WORK_DRIVE_OCR.md) | Quien configura Work | Preparación automática de adjuntos de Gmail y extracción OCR en Drive |
| `INVESTIGACION_MEJORAS.md` (borrador local, si existe) | Quien desarrolla | Investigación local previa; no forma parte de esta implementación |
| [INVENTARIO_FORMATOS_HISTORICO_TE.md](INVENTARIO_FORMATOS_HISTORICO_TE.md) | Quien mantiene los lectores | Formatos y límites de extracción |
| [`../README.md`](../README.md) | Quien llega por primera vez | Qué es, cómo se instala y se usa |
| [`GUIA_COMERCIAL.md`](GUIA_COMERCIAL.md) | El comercial | Cómo usar el dashboard en el día a día |
| [`FLUJO_FASE1.md`](FLUJO_FASE1.md) | Todos | El flujo de la Fase 1, paso a paso |
| [`MODO_OPERACION.md`](MODO_OPERACION.md) | Quien opera | Los tres modos (Ollama, API, Work) y cómo cambiar |
| [`PILOTO_TRIGGER_GMAIL.md`](PILOTO_TRIGGER_GMAIL.md) | Quien autorice un piloto futuro | Observación, criterios de éxito, detención y reversión del trigger Gmail de solo lectura |
| [`PILOTO_COLA_WORK.md`](PILOTO_COLA_WORK.md) | Quien autorice la prueba manual de la cola Python → Work | Crear el trabajo, revisar la hoja, publicarla en Drive de laboratorio, importar el retorno y verificar (T-49) |
| [`PILOTO_6C_ACTIVACION.md`](PILOTO_6C_ACTIVACION.md) | Quien autorice el piloto automático de un solo mensaje ficticio | Verificar, activar, supervisar y desactivar el coordinador T-55 con puntos de parada y reversión |
| [`TAREA_WORK_COLA.md`](TAREA_WORK_COLA.md) | Quien vaya a crear la tarea de Work que recoge un trabajo por ejecución | Texto propuesto (no es una tarea creada): qué hace Work y qué hace Python con PENDIENTES y CERRADOS (T-53) |
| [`paquete_work/`](paquete_work/) | Quien mantenga el paquete que Work lee desde Drive | Fuentes del paquete de referencia del contrato (T-54): LEEME, T-52, formato de retorno e instrucciones de una ejecución |
| [`PILOTO_ACEPTACION.md`](PILOTO_ACEPTACION.md) | Quien prueba | Checklist de la prueba de aceptación en Work |
| [`PLAN_PILOTO_LECTURA.md`](PLAN_PILOTO_LECTURA.md) | Dueño del proyecto y quien revisa | Plan del piloto supervisado de lectura: qué se mide, criterios de decisión y herramienta de cobertura |
| [`PROMPT_WORK_ANKA_V6.txt`](PROMPT_WORK_ANKA_V6.txt) | Quien configura la tarea de Work | Vigente **V6.1.1** (T-57): la V6.1 más la regla «revisión interna sin bloqueo» (una solicitud legible, aunque sea ficticia, conserva su tabla preliminar). Es el prompt que lleva el paquete de referencia |
| [`PROMPT_WORK_ANKA_V6_1.txt`](PROMPT_WORK_ANKA_V6_1.txt) | Quien revisa antecedentes | La V6.1 tal como era antes de T-57 (la del piloto 6C), conservada como antecedente |
| [`PROPUESTA_PROMPT_V6_2.md`](PROPUESTA_PROMPT_V6_2.md) | Quien configura la tarea de Work | Propuesta (no aplicada) de V6.2: núcleo y extras, valores permitidos junto a cada campo y lista de comprobación; exige prueba manual en Work |
| [`PROMPT_WORK_ANKA_PRECISO_V4_6.txt`](PROMPT_WORK_ANKA_PRECISO_V4_6.txt) | Quien revisa antecedentes | Versión anterior a la V5, conservada como antecedente |
| [`PROMPT_WORK_ANKA_PRECISO_V4_7.txt`](PROMPT_WORK_ANKA_PRECISO_V4_7.txt) | Quien revisa antecedentes | Versión anterior a la V5 (unos 19 500 caracteres), conservada como antecedente |
| [`PROMPT_WORK_ANKA_V5.txt`](PROMPT_WORK_ANKA_V5.txt) | Quien configura la tarea de Work | Candidato corto (T-39): unos 11 000 caracteres con un JSON de ejemplo que una prueba valida contra el esquema; Work solo declara qué segmentos leyó. Aún no es el de la tarea: se prueba primero en una conversación aparte |
| [`PROMPT_WORK_ANKA_RIGUROSO_V2.txt`](PROMPT_WORK_ANKA_RIGUROSO_V2.txt) | Quien configura Work | Prompt vigente con contrato de evidencia y preguntas auditables para copiar en la tarea |
| [PROMPT_WORK_ANKA.txt](PROMPT_WORK_ANKA.txt) | Quien revisa antecedentes | Prompt histórico inicial |
| [PROMPT_WORK_ANKA_RIGUROSO.txt](PROMPT_WORK_ANKA_RIGUROSO.txt) | Quien revisa antecedentes | Prompt histórico riguroso |
| [PROMPT_WORK_ANKA_PRECISO_V4.txt](PROMPT_WORK_ANKA_PRECISO_V4.txt) | Quien configura la tarea de Work | Prompt vigente: la V3 más el registro de `model` y `skill_version` (T-32) |
| [PROMPT_WORK_ANKA_PRECISO_V3.txt](PROMPT_WORK_ANKA_PRECISO_V3.txt) | Quien revisa antecedentes | Versión anterior conservada; el piloto referencia Riguroso V2 |
| [`INSTRUCCIONES.txt`](INSTRUCCIONES.txt) | Quien opera el puente | Sincronización entre Gmail y el dashboard, prueba y limitaciones |
| [`ESTANDAR_CAMBIOS.md`](ESTANDAR_CAMBIOS.md) | Quien desarrolla | Cómo se cambia el comportamiento sin separar los tres modos |
| [`POLITICA_CONTEXTO_COMERCIAL.md`](POLITICA_CONTEXTO_COMERCIAL.md) | Quien desarrolla | Qué contexto interno puede (y no puede) aparecer en correos |
| [`DISENO_HIBRIDO.md`](DISENO_HIBRIDO.md) | Quien desarrolla | Diseño de la automatización híbrida (propuesta original) |
| [`PLAN_FASE_1_PLUS.md`](PLAN_FASE_1_PLUS.md) | Quien desarrolla | Plan de la Fase 1 ampliada |

La **especificación** (requisitos, reglas, contratos, decisiones) vive en [`../spec/`](../spec/README.md).
