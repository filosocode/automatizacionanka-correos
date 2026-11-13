# T-117 · Línea base y contrato de evidencia documental (V1.1)

Fase de **laboratorio y diseño**. No modifica `anka/`, contratos, configuración, SQLite, Gmail, Drive ni la tarea de Work; no ejecuta
Work ni OCR en producción; no hay commits ni push. Todo lo de esta carpeta es nuevo y solo laboratorio (nada de `anka/` lo importa).

## Lo que este trabajo NO afirma
- El OCR **no certifica interpretación visual**: tachaduras, anotaciones, zonas rayadas, figuras, escala y norte quedan «pendientes de revisión visual»
  hasta que una persona, con nombre, las vea en el original.
- La **revisión visual humana de C2 sigue pendiente**; no se simuló.
- La evidencia de referencia («dorada») la escribí leyendo los originales y las referencias v2 del banco, que son una propuesta **posterior** a
  las respuestas de Work: no es una evaluación ciega ni independiente del banco. Sirve para probar el validador, no para medir a un sistema.
- La línea base es **un documento por tipo y una sola ejecución cada uno**: no es precisión estadística.
- El texto OCR de C2 sale de la prueba aislada T-108 (motor local, sobre el SHA-256 de `tdr_02.pdf`); no forma parte del procesamiento integrado de V1.0.

## A. Línea base congelada (C1, C2, C3)
Fuente: ejecución integrada de Work, evaluada con el evaluador inverso tras T-105. Archivo: `datos/linea_base_t117.json` (con las huellas SHA-256
del retorno de Work, de la evaluación y del documento). Se guardan solo cifras: ni correos ni texto del borrador.

| | C1 · PDF digital | C2 · PDF escaneado | C3 · Excel |
|---|---|---|---|
| Actividades esperadas / obtenidas / encontradas | 5 / 5 / 5 | 4 / **0** / 0 | 4 / 4 / 4 |
| Con cantidad **y** unidad correctas | 5 (100 %) | 0 | 4 (100 %) |
| Actividades omitidas | 0 | 4 | 0 |
| Restricciones omitidas | 0 | 3 | 0 |
| Faltantes sin reconocer | 1 | 3 | 0 |
| Revisiones internas omitidas | 1 | 1 | 1 |
| Contradicciones exigidas / detectadas | 1 / 1 | 1 / **0** | 2 / 2 (+2 fundadas adicionales) |
| Errores críticos / no críticos / revisión humana | 0 / 3 / 2 | **6** / 6 / 4 | 0 / 1 / 1 |
| Cobertura que declaró Work | completa | **parcial; adjunto «bloqueado»** | completa |
| Elementos visuales esperados | 0 | 3 | 0 |
| Decisión documental | aprobado con reservas | **NO aprobado** | aprobado con reservas |

Lectura: dónde hay texto o celdas, Work acierta cantidades y unidades; donde el documento es imagen, no lee nada y lo declara (no inventa).
Lo que **hoy no existe** es una forma de comprobar mecánicamente que una cita de Work apunta a algo real: la cobertura «completa» de C1 y C3 es una
declaración, no una verificación. Eso es lo que el modelo de evidencia añade.

## B. Esquema de evidencia documental (`esquema.py`, `esquema_evidencia.schema.json`)
Un documento de evidencia tiene `documentos` (con cobertura), `evidencias`, `interpretaciones`, `contradicciones` y `analisis`. **La evidencia es lo
observado; la interpretación es lo que se concluye y siempre cita evidencias.**

| Campo de una evidencia | Contenido |
|---|---|
| `documento` | nombre del archivo original; el documento lleva su **SHA-256** y su tipo (`pdf_digital`, `pdf_escaneado`, `xlsx`) |
| `localizador` | PDF: `pagina` (+ `tabla`, `fila` o `region`, descriptivos). Excel: `hoja` + `celda` o `rango` |
| `texto_literal` | el texto tal como está en el original |
| `cantidad_literal`, `unidad_literal` | **texto**, nunca número: «2,350», «km», «ha», «m2». Nada se convierte ni recalcula |
| `valor_normalizado` | opcional: `valor`, `unidad`, `regla` (identidad, separador decimal/de miles, `conversion_declarada` con `factor`). Acompaña al literal, no lo sustituye |
| `metodo` | `texto_nativo`, `celda`, `ocr`, `vision`, `humano` |
| `estado_de_lectura` | `leido`, `no_encontrado`, `ilegible`, `contradictorio`, `parcialmente_leido`, `pendiente_revision_visual` |
| `vigencia` | `vigente` o `historica` (p. ej. una hoja oculta) |
| `confianza`, `revision_humana_requerida` | `alta`/`media`/`baja`; el OCR y la visión nunca pasan de `media` |
| `certifica_interpretacion_visual` | siempre `false` |
| `concepto` | de qué trata la cantidad; permite detectar dos cifras vigentes distintas del mismo concepto |
| `derivacion` | para lo que se **cuenta** y no está escrito (p. ej. 6 lotes listados): se recalcula |

**Cobertura** por documento: `total`, `leidas`, `ilegibles` y `detalle` por página u hoja. **`analisis.declarado_completo`** solo es posible si
la cobertura es total, no hay nada ilegible, parcial ni pendiente de revisión visual y todo archivo recibido está cubierto.

### Los cinco estados que no son «leído»
| Estado | Significa | Obligatorio | Prohibido |
|---|---|---|---|
| `no_encontrado` | se buscó y **el dato no está** (p. ej. el polígono «se enviará por separado») | `motivo` (qué se buscó) | cantidad, unidad o texto |
| `ilegible` | el lugar existe y **no se pudo leer** | `motivo`, localizador, revisión humana | cantidad, unidad o texto |
| `contradictorio` | otra evidencia vigente dice algo distinto del mismo concepto | citado por una contradicción con ≥ 2 evidencias distintas | callarlo |
| `parcialmente_leido` | se leyó una parte | `faltante` (qué parte no) | declarar la lectura completa |
| `pendiente_revision_visual` | solo una persona viendo el original lo confirma | revisión humana requerida | confianza alta; darlo por leído |

## C. Reglas del validador independiente (`validador.py`)
Determinista, sin modelo ni red: relee los originales (`pypdf`, `openpyxl`) y no usa las referencias del banco ni el sistema evaluado. Cada hallazgo
tiene código estable, severidad (`error` invalida, `aviso` no) y la ruta del dato. Lo que no tiene respaldo localizable **no es evidencia válida**.

| Código | Qué impide |
|---|---|
| `forma_invalida` | la estructura no cumple el esquema |
| `id_duplicado` | dos elementos comparten id |
| `documento_no_entregado` | la evidencia cita un archivo que no está en el paquete |
| `huella_distinta` | el SHA-256 declarado no es el del archivo entregado |
| `documento_sin_cubrir` | un archivo del paquete no aparece en la evidencia |
| `documento_desconocido` | la evidencia cita un documento que no está en la lista de documentos |
| `tipo_de_documento_incorrecto` | el tipo declarado no corresponde al contenido |
| `cobertura_inconsistente` | la cobertura no cuadra con el documento |
| `localizador_ausente` | falta el localizador |
| `localizador_mixto` | el localizador mezcla página con hoja/celda |
| `pagina_inexistente` | la página no existe |
| `hoja_inexistente` | la hoja no existe |
| `celda_invalida` | celda o rango mal escrito |
| `rango_fuera_de_la_hoja` | el rango se sale de la hoja |
| `hoja_no_vigente` | se cita como vigente una hoja oculta o histórica |
| `localizador_fino_no_verificable` | tabla, fila o región de un PDF no se puede comprobar automáticamente |
| `metodo_incompatible` | el método declarado no es posible para esa fuente |
| `texto_no_encontrado` | el texto literal no está en la fuente citada |
| `cantidad_ausente` | una evidencia de cantidad no trae cantidad literal |
| `cantidad_no_respaldada` | la cantidad literal no está en la fuente citada |
| `unidad_no_respaldada` | la unidad literal no está en la fuente citada |
| `unidad_alterada` | la cifra está en la fuente pero con otra unidad |
| `normalizado_sin_regla` | valor normalizado sin regla declarada |
| `normalizado_inconsistente` | el valor normalizado no se deduce del literal con la regla declarada |
| `normalizado_ambiguo` | la cifra tiene dos lecturas posibles y no se declaró la ambigüedad |
| `ocr_certifica_visual` | se pretende que el OCR o la visión certifique interpretación visual |
| `confianza_excesiva` | confianza alta con un método que no la permite |
| `vision_sin_revision` | lectura visual sin revisión humana requerida |
| `no_verificable_mecanicamente` | no hay texto contra el que contrastar este valor |
| `estado_con_cantidad` | un dato ilegible o no encontrado trae cantidad o texto |
| `estado_sin_motivo` | falta el motivo del estado |
| `estado_inconsistente` | el estado no es coherente con el resto de la evidencia |
| `parcial_sin_faltante` | lectura parcial sin declarar qué falta |
| `pendiente_visual_sin_revision` | pendiente de revisión visual sin pedir revisión humana |
| `elemento_visual_no_pendiente` | un elemento visual no puede darse por leído sin una persona |
| `contradictorio_sin_par` | evidencia marcada contradictoria que ninguna contradicción cita |
| `contradiccion_invalida` | la contradicción no cita al menos dos evidencias existentes y distintas |
| `contradiccion_sin_diferencia` | las evidencias de la contradicción dicen lo mismo |
| `contradiccion_resuelta_sin_persona` | contradicción resuelta sin una persona que la resuelva |
| `contradiccion_no_declarada` | dos cantidades vigentes distintas del mismo concepto sin contradicción declarada |
| `interpretacion_cita_inexistente` | la interpretación cita una evidencia que no existe |
| `interpretacion_sin_evidencia` | la interpretación no cita ninguna evidencia |
| `interpretacion_cifra_nueva` | la interpretación contiene una cifra que ninguna evidencia citada respalda |
| `completitud_falsa` | se declara lectura completa y la evidencia no lo permite |
| `incompleto_sin_motivo` | se declara incompleto sin decir por qué |

Cómo se decide «tiene respaldo»:
- **PDF digital:** la página existe; la cifra aparece **completa** en su texto («8,5» no está en «18,5») y la unidad la **sigue** como palabra completa («m» no está en «km»).
- **PDF escaneado:** solo con el texto OCR de esa página en el paquete; la comparación ignora espacios (el OCR junta palabras) y la confianza máxima es `media`.
- **Excel:** la hoja existe, el rango está dentro de la hoja, la cantidad es el **valor guardado** de una celda del rango (con fórmulas, el valor calculado) y la unidad está en el rango o en el encabezado de su columna. Una hoja oculta solo es válida como `historica`.
- **Contradicciones:** dos cantidades vigentes distintas del mismo `concepto` exigen una contradicción declarada; una contradicción abierta solo la cierra una persona con nombre.
- **Interpretación:** cada cifra que contenga debe estar en las evidencias que cita (las referencias «Tabla 1», «p. 2», «ítem 3» no cuentan como cifras).

### Límites conocidos del validador (no se esconden)
1. En un PDF solo se comprueba la **página**; `tabla`, `fila` y `region` emiten un aviso porque ninguna regla simple los verifica.
2. No comprueba **exhaustividad**: que se hayan extraído todas las cifras relevantes. Una evidencia puede ser válida y estar incompleta; la completitud depende de la cobertura y del bloqueo por estados, no de las cifras. (Mejora: avisar de cifras del original que ninguna evidencia cita.)
3. La detección de contradicciones por `concepto` depende de que el concepto esté bien declarado; dos conceptos distintos para lo mismo la evaden.
4. El OCR puede equivocar caracteres (0/O, coma/punto): el validador solo prueba que lo declarado **es lo que el OCR leyó**, no que el OCR leyó bien el original.
5. No interpreta figuras, tachaduras ni manuscritos; los deja pendientes. No valida comentarios de celda ni imágenes dentro de un libro.
6. No decide si un caso alcanza para cotizar: eso sigue siendo de una persona (R8).

## D. Pruebas de laboratorio (`test_evidencia.py`: 77 pruebas)
`python -X utf8 -m unittest laboratorio.evidencia_documental.test_evidencia` (≈ 3 s; solo lee los originales del banco).

- **Positivas (7):** la evidencia de C1, C2 y C3 es válida; C1 y C3 pueden declararse completos y C2 no; los literales se conservan; la hoja histórica se acepta solo como histórica; el validador no modifica lo que valida; los estados son distintos.
- **Negativas (por familia):**
  - *documento:* huella alterada, archivo no entregado, archivo recibido sin cubrir, cobertura que no cuadra, tipo declarado incorrecto;
  - *localizadores:* página inexistente (3 y 0), página equivocada, hoja inexistente, rango fuera de la hoja, celda mal escrita, rango equivocado, hoja oculta citada como vigente, página en un Excel y hoja en un PDF, sin localizador;
  - *valores inventados:* cantidad inventada (PDF, Excel, OCR), cifra parcial, unidad modificada (`km`, `m2`, `m` dentro de `km`), texto que no está, cantidad numérica en vez de literal, conteo derivado incorrecto;
  - *normalización:* sin regla, inconsistente, cifra ambigua sin declarar, conversión sin factor;
  - *método y visual:* texto nativo en página escaneada, OCR sin texto OCR, celda en un PDF, OCR que «certifica» lo visual, confianza alta con OCR, visión sin revisión, elemento visual dado por leído;
  - *estados:* ilegible con cantidad, sin motivo, parcial sin faltante, pendiente sin revisión;
  - *análisis falsamente completo:* C2 declarado completo, página ilegible con análisis completo, incompleto sin motivo, completo con motivos, pendiente visual con análisis completo;
  - *contradicciones e interpretaciones:* no declarada, sin par, sin diferencia, cita inexistente, resuelta sin persona, cifra nueva, sin evidencia;
  - *forma:* campo ausente, valor fuera de la lista, huella mal escrita, ids duplicados.
- **Las pruebas no son decorativas:** anulando una regla a propósito, fallan pruebas (cifra completa: 4; unidad: 4; completitud: 7; contradicciones: 4; interpretaciones: 2).
- **Hallazgo propio:** al escribir la prueba «archivo no entregado» el validador se caía (`KeyError`); se corrigió para informar `documento_no_entregado` y seguir.
- **Independencia:** una prueba comprueba que el validador no importa `anka`, no lee las referencias del banco y no usa red ni modelos.

## E. Compatibilidad con el JSON contractual vigente (`ANKA_DASHBOARD_JSON_V1`), sin modificarlo
Experimento (`adaptador_work.py`): el JSON que Work ya devolvió en C1, C2 y C3 se convierte a evidencia y se pasa por el validador.
Los retornos reducidos y sin correos están en `datos/work_*.reducido.json`.

| Caso | Resultado del validador sobre lo que Work devolvió | Lectura |
|---|---|---|
| C1 | 2 errores `unidad_alterada` | No son cantidades falsas: en «10 puntos; 12 fotografías por punto» Work junta dos datos en `value`, y en «25 días calendario desde orden de inicio» **parafrasea** la unidad. El contrato no distingue literal de resumen |
| C3 | 0 errores (T-118) | El `excerpt` «Pendiente de confirmar con el cliente.» es el **comentario de la celda C9**. T-117 lo marcó como error por no mirar comentarios: era un **falso positivo del validador**, corregido en T-118 |
| C2 | 0 errores; adjunto `ilegible`, cobertura 0 de 2, no completo | Work declaró el adjunto sin leer y el modelo lo representa sin inventar nada |

Hallazgos de compatibilidad:
1. **El esquema vigente ya tiene una base:** `attachments[].evidence[]` (`topic`, `value`, `location`, `excerpt`), `reading_audit` y `analysis_complete`. Faltan la página/celda **estructurada**, el SHA-256, la cantidad y la unidad **separadas y literales**, el método y el estado de lectura.
2. **Un campo nuevo no rompe el contrato** (el validador vigente ignora campos desconocidos: prueba 114), **pero el importador no lo guarda**: solo calcula una huella del JSON crudo. Hace falta persistir la evidencia aparte (archivo versionado por mensaje o una tabla), sin tocar el análisis existente.
3. **Las contradicciones del contrato citan fuentes en texto libre**, sin ids de evidencia: no se pueden enlazar mecánicamente.
4. **El prompt PRECISO_V6.1.3 está cerca de su tope de tamaño:** no conviene pedirle a Work que genere evidencia estructurada. Mejor que **Python** la genere (texto nativo, celdas, OCR) y Work solo **cite ids** de evidencia.
5. **No está probado que Work use un canal auxiliar** (p. ej. una hoja `TEXTO_OCR_AUXILIAR`): la etapa 4 debe probarlo antes de depender de él.
6. **Byte a byte:** una evidencia congelada cambia de huella si Git convierte saltos de línea (lección de T-113): se guarda con `-text`.
7. **Privacidad:** la evidencia contiene texto literal de documentos de clientes; vive solo en local y no se incluye en paquetes compartibles.

## F. Plan mínimo de implementación sin regresiones
Protegido por una opción **desactivada** al inicio (`evidencia_documental_enabled: false` en `config.json`, declarada en `anka/dominio/configuracion.py`).
Con la opción apagada el comportamiento y las instantáneas de pantalla deben ser **idénticos** a V1.0. Cada etapa es reversible apagando la opción.

| Etapa | Qué | Criterio de aceptación (propuesto; los umbrales finales los fija quien opera el negocio) |
|---|---|---|
| 0 · hecha | Esquema, validador, pruebas y línea base en `laboratorio/` | 77 pruebas verdes; 0 errores sobre la evidencia de referencia; cada regla anulada hace fallar pruebas |
| 1 · extractor local | Python genera la evidencia (texto nativo y celdas; OCR solo si el PDF es escaneado y la opción de OCR está activa) y la guarda aparte, sin mostrarla ni enviarla a Work | C1 y C3: 100 % de las cantidades y unidades de la referencia con su localizador, 0 errores del validador. C2: 100 % de las cifras legibles por OCR localizadas, 100 % de los elementos visuales en `pendiente_revision_visual`, **0** `declarado_completo` |
| 2 · sombra | El validador revisa cada análisis de Work (adaptador) y solo mide | Sin cambios en estados ni textos; métrica visible en Desempeño: % de citas verificables y causas de rechazo. Meta a fijar tras ≥ 20 hilos reales |
| 3 · bloqueo objetivo | La pantalla del caso muestra cobertura y pendientes y **extiende** «Lectura pendiente» (T-110) con `completitud_falsa`/`ilegible` | 0 casos con documento ilegible llegando a «Con esto alcanza»; instantáneas sin cambio con la opción apagada; `check.bat` verde |
| 4 · Work cita evidencia | Work recibe la evidencia (canal a probar) y cita ids; prompt V6.1.4 podado; cambio retrocompatible del contrato | Un caso nuevo (otro mensaje; sin republicar C2): cada cifra citada resuelve a una evidencia válida; lo visual queda pendiente; 0 falsos «completo» |
| 5 · visión selectiva | Solo páginas con figuras o poco texto; comparación con el OCR | Acuerdo OCR/visión medido por caso; toda discrepancia pendiente de revisión humana independiente |

Antes de la etapa 1 hay que decidir: dónde se guarda la evidencia (archivo por mensaje o tabla), el presupuesto de OCR y qué se hace con documentos de
clientes en el almacenamiento local.

## Archivos de esta carpeta
`esquema.py` (esquema y forma) · `esquema_evidencia.schema.json` (JSON Schema) · `fuentes.py` (lectura de originales) · `validador.py` (reglas) ·
`casos.py` (evidencia de referencia de C1, C2 y C3) · `adaptador_work.py` (compatibilidad con el JSON vigente) · `test_evidencia.py` ·
`datos/linea_base_t117.json`, `datos/work_*.reducido.json`, `datos/ocr_tdr_02.txt`.
