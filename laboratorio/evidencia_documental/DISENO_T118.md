# T-118 · Extracción local verificable de evidencias (laboratorio)

Estado: **EXTRACTOR_LAB_VALIDADO** para el alcance del banco (C1, C2, C3 + documentos ficticios). No es producción: nada de `anka/`,
contratos, configuración, SQLite, Gmail, Drive ni Work fue tocado. Lo derivado vive en `derivados/` (ignorado por Git).

## 1. Qué hay
| Archivo | Papel |
|---|---|
| `aislamiento.py` | Fija `ANKA_DATA_DIR` a una carpeta temporal antes de importar los lectores; `comprobar()` falla si no es así. |
| `extractor.py` | Reutiliza sin modificar `lectores_pdf.pdf_text` (con su OCR local) y `lectores_office.xlsx_text`; produce evidencias del modelo T-117. No importa la referencia. |
| `comparar_referencia.py` | Lee las referencias v2 **después** de extraer y compara. |
| `banco.py` | Corre C1/C2/C3 → extrae → valida (T-117) → compara → escribe `derivados/`. |
| `test_extractor.py` | 31 pruebas (ficticias + banco). Con las 77 de T-117: 108 OK; ruff y mypy limpios. |

## 2. Reglas del extractor
- Una **observación** = cifra + unidad reconocida (catálogo `UNIDADES`) con su texto cercano, página u hoja/rango, método (`texto_nativo`, `ocr`, `celda`) y SHA-256 del documento. No es una actividad: `interpretaciones` queda vacío.
- No convierte ni redondea: «2,350» sigue «2,350»; «48250 m2» y «4,825 ha» son dos observaciones.
- Cifras sin unidad (sello 0457, «Nota 7») **no** se registran como cantidad (decisión de diseño; ver §6).
- Texto nativo y OCR de una página se separan por la marca `[OCR visual adicional]` del lector: si el lector hizo OCR sobre una página con poco texto, ambas lecturas se conservan como observaciones distintas.
- Lo visual (tachaduras, notas a mano, zonas rayadas, norte, escala, figuras) **nunca** se lee: se crea `elemento_visual` en `pendiente_revision_visual`, con la pista de texto que el OCR sí leyó y una pendiente general por página.
- Excel: una observación por celda numérica con su etiqueta (izquierda) y unidad (derecha o encabezado); fórmula y valor guardado como campos extra; comentarios como evidencia de texto en su celda; hoja oculta → `vigencia: sin_confirmar` + revisión humana, sin declararla vigente ni histórica.
- Archivo dañado/cifrado/de otro formato: evidencia `ilegible`, sin inventar páginas ni cifras. Formato no soportado: `ValueError`.
- `declarado_completo` solo si cobertura completa y sin pendientes: C1 sí; C2 no (visual); C3 no (hoja oculta).
- Identificador: `ev-`/`vis-` + 12 hex de SHA-256 sobre (huella, localizador, método, cantidad, unidad, nº de ocurrencia). Estable entre ejecuciones (probado en las tres corridas del banco, bytes idénticos).

## 3. Resultados (corrida real)
| Caso | Archivo | Evidencias | Cobertura | Extracción | Validador T-117 | Cifras esperadas halladas |
|---|---|---|---|---|---|---|
| C1 | solicitud_01.pdf | 11 (texto nativo) | 2/2 páginas | 0,11 s | 0 errores, 0 avisos | 10/11 |
| C2 | tdr_02.pdf | 15 (11 OCR + 4 visuales pendientes) | 2/2 páginas con OCR, ambas con visual pendiente | 7,9–8,6 s (la primera carga el motor) | 0 errores | 9/10 |
| C3 | cantidades_03.xlsx | 29 (28 celdas + 1 comentario) | 4/4 hojas (1 oculta sin confirmar) | 0,04 s | 0 errores | 10/11 |

Omitidas (todas por diseño, ninguna es lectura fallida): C1-T4 («Nota 7», sin unidad), C2-T3 (sello 0457, sin unidad), C3-K1.2 («6 lotes»: conteo de filas A2:A7, no cifra escrita; el validador lo cubre con `derivacion`).
Actividades de la referencia: **100 % halladas** (5/5, 4/4, 4/4). Valores convertidos o normalizados: 0. Cifras inventadas: 0 (el validador relee el original y las respalda todas).
C2: el OCR lee «45,0 ha» y «41,8 ha», pero **no** dice cuál está tachada ni cuál vale; tampoco la zona excluida, el norte ni la escala. Los tres elementos visuales de la referencia (V1–V3) quedan registrados como pendientes, ninguno como leído.
C3: se conservan fórmulas (`=…`) con su valor guardado (33.7, 3.625, 24, 3), el comentario «Pendiente de confirmar con el cliente» en Resumen!C9 y la hoja `Borrador_anterior` sin vigencia confirmada.

## 4. Fallos: de dónde vienen
- Del **validador** (T-117), hallados al correr el extractor: (a) no entendía comentarios de celda → falso positivo en C3 (corregido en la tarea previa); (b) trataba como error un archivo que no se abre aunque la cobertura lo declaraba ilegible → ahora es aviso `documento_no_legible`.
- Del **extractor**, hallados por pruebas propias: ventana de texto que empezaba a mitad de palabra; horas («10:30 m») tomadas como cifra con unidad; palabra pegada por OCR («excluidadel») en el rótulo; PDF dañado que abortaba. Todos corregidos y con prueba.
- Ninguno quedó abierto en C1/C2/C3.

## 5. Diseño del modo sombra (no activado)
Objetivo: extraer y validar en paralelo a la recepción, **sin influir** en ella ni en Work, y sin que la persona lo vea todavía.
- **Dónde guardar**: tablas nuevas en una base SQLite *aparte* (`evidencia_sombra.db` en `%LOCALAPPDATA%\AnkaCorreos`), nunca en la SQLite operativa. Tablas: `extraccion(job_id, sha256, version_extractor, version_validador, creado, duracion_s, estado)`, `evidencia(extraccion_id, id, json)`, `hallazgo(extraccion_id, codigo, severidad, ruta, mensaje)`. Alternativa mínima: JSON por extracción en `…/sombra/<job_id>/<sha256>/`.
- **Vínculo**: `job_id` (hilo/mensaje de Gmail ya registrado) + `sha256` del adjunto; el contenido del adjunto se lee de la copia ya guardada, no se vuelve a pedir a Gmail.
- **Sin duplicados**: clave única `(sha256, version_extractor, version_validador)`; el mismo adjunto en otro hilo reutiliza la extracción y solo añade un vínculo `(job_id, sha256)`.
- **Versiones**: `VERSION_EXTRACTOR`, `VERSION_MODELO` y versión del validador se guardan con cada extracción; cambiar cualquiera crea una fila nueva, no sobrescribe. Los ids estables permiten comparar versiones.
- **Aislamiento de fallos**: ejecución en un proceso/hilo aparte con tiempo máximo (el OCR tarda ~8 s por PDF escaneado de 2 páginas), cola con tope, todo dentro de `try/except` que solo registra; la recepción no espera ni lee su resultado. Un error del validador se guarda como hallazgo `fallo_interno`, nunca se propaga.
- **Apagado total**: bandera `evidencia_sombra_enabled` (por defecto `false`, como `work_drive_enabled`) más un archivo-interruptor (`sombra.off`) que se comprueba antes de cada tarea; al apagar no se borra nada y no queda ningún componente en el camino del correo. Revertir = quitar la bandera.
- **Privacidad**: la evidencia contiene texto de documentos de clientes; vive local, se excluye de copias públicas y del paquete de entrega, y no se envía a Work ni a servicios externos.
- **Fase siguiente (no hecha)**: comparar en sombra lo extraído con lo que Work devuelve y medir antes de mostrar nada a una persona.

## 6. Riesgos antes de integrar
1. **Cobertura de unidades**: el catálogo es finito; una unidad no listada (p. ej. «hitos», «vértices») se omite en silencio. Hay que ampliar con datos reales y medir omisiones.
2. **Cifras sin unidad** relevantes (códigos, cantidades en tablas con la unidad en el encabezado de PDF) no se capturan en PDF; sí en Excel por encabezado.
3. **Banco pequeño**: tres casos sintéticos; la exhaustividad real en documentos de clientes es desconocida. No extrapolar.
4. **OCR**: ~4 s por página escaneada, texto con espacios perdidos («Batimetriamonohazdel»), minúsculas/tildes alteradas; el texto literal queda tal cual. El motor y el modelo OCR deben fijarse por versión.
5. **Lo visual sigue siendo humano**: ni el extractor ni el validador certifican tachaduras, zonas excluidas, norte o escala; C2 sigue exigiendo revisión visual de una persona.
6. **Dependencia de la salida de texto de los lectores** (marcas `[OCR visual adicional]`, «Hoja: …; visibilidad: …», « | »): un cambio en `anka/infra/` rompe el análisis; hay pruebas, pero deben vigilarse al cambiar lectores. Un valor de celda con « | » se leería mal.
7. **Excel**: el extractor lee el valor guardado por Excel; si el libro nunca se calculó, la hoja queda parcial (probado). No evalúa fórmulas.
8. **Entorno V1.0 (sin cambio)**: Python bajo Temp, sesión interactiva de Windows, rama remota sin reconciliar; la integración debe hacerse después de la entrega del 23/10, no antes.
9. **Gobierno**: tocar contratos, esquema de Work o la SQLite operativa exige cambio en `spec/` primero (CLAUDE.md); este laboratorio no lo hace.

## 7. Cómo repetirlo
`.venv\Scripts\python.exe -X utf8 -m laboratorio.evidencia_documental.banco` y
`.venv\Scripts\python.exe -X utf8 -m unittest discover -s laboratorio/evidencia_documental -t . -p "test_*.py"`.
