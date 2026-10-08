# Inventario de formatos del histórico T&E

**Fuente:** `I:\T&E`, lectura recursiva de nombres, extensiones, tamaños y estructura de carpetas. No se modificó ningún archivo.  
**Fecha del inventario:** 2026-10-07.

## Resumen

Se encontraron **20.714 archivos** (aprox. **75,38 GiB**) en **652 carpetas de primer nivel**. Es una biblioteca de propuestas, especificaciones, insumos de cliente y entregables, no un corpus homogéneo de correos. Los formatos no se deben forzar a un único lector de texto: conviene enrutar por tipo, declarar cobertura y dejar las fuentes no interpretables en revisión interna.

| Grupo | Archivos observados | Lectura para recepción de cotizaciones |
|---|---:|---|
| PDF | 7.772 | Texto y tablas cuando son digitales; OCR para escaneos; láminas/figuras requieren cotejo visual si condicionan el alcance. |
| Word | DOCX 3.138; DOC 72 | Extraer cuerpo, tablas, encabezados/pies, comentarios/revisiones e imágenes; DOC legado necesita conversor. |
| Excel | XLSX 1.535; XLS 70; XLSM 26 | Leer todas las hojas, incluso ocultas; celdas, fórmulas y valores guardados, tablas, comentarios, gráficos e imágenes. La muestra incluye cuestionarios RFQ, cronogramas, formatos de campo, fichas técnicas, análisis de precios, materiales y catálogos. |
| Geoespacial | KMZ 2.620; KML 237; shapefile: SHP 283 + DBF 270 + SHX 279 + PRJ 278 + CPG 258 | Inspeccionar todo KML interno, capas/atributos/CRS declarados y componentes faltantes. No inferir CRS ni área contractual. |
| CAD | DWG 250; DXF 1 | Revisión manual/local por ahora; capas, layouts, referencias externas y bloques pueden contener información no visible en una extracción simple. |
| Imágenes | JPG 1.044; JPEG 284; PNG 150; TIFF 9 | OCR solo lee texto; límites, planos, tablas e imágenes requieren revisión visual, en especial si están insertados en documentos. |
| Archivos empaquetados | ZIP 427; RAR 185; 7Z 12 | ZIP se inspecciona parcialmente por entradas compatibles y shapefiles. RAR/7Z no tienen extracción implementada. Inventariar contenido y marcar faltantes. |
| GIS y cartografía pesada | ECW 117; estructuras File Geodatabase (`.gdbtable` 100 y otros índices laterales) | Ráster, bases GIS y capas requieren lector GIS y control de CRS; no son texto genérico. |
| Productos de captura/topografía | GMW 69; QMD 24; LAS/LAZ no aparecen en esta muestra por extensión | Proyectos nativos de software y metadatos/proyectos de captura; requieren software de origen o exportación a formatos interoperables. La ausencia observada de LAS/LAZ no demuestra que no existan en archivos comprimidos o con extensión distinta. |
| Otros | XML 108; CSV 35; TXT 34; PPTX 29; además formatos aislados | XML/CSV/TXT y presentaciones pueden aportar alcance; el resto se enruta por formato y no se ignora silenciosamente. |

Las cantidades son conteos por extensión y pueden incluir versiones, copias, plantillas, formatos de salida y expedientes administrativos. No equivalen a cantidad de solicitudes ni a prevalencia por proyecto.

## Ejemplos de estructuras de tabla observadas

Los nombres de archivos muestran tablas de naturaleza distinta, que requieren tratamientos distintos:

- **Requisitos/RFQ del cliente:** cuestionarios de oferta y formularios de proveedor.
- **Alcance técnico:** fichas técnicas, tablas de parámetros, entregables y formatos.
- **Planeación:** cronogramas y secuencias de actividades.
- **Campo:** formatos de captura y carteras/registros.
- **Comercial:** análisis de precios unitarios y materiales; solo para contexto interno autorizado, nunca asumir que son un precio vigente.
- **Catálogos/administración:** códigos, dotación, hojas de vida y registro de proveedores; no confundir con requerimientos del proyecto actual.

En Excel, una lectura completa debe cubrir todas las hojas y rangos usados, incluidas hojas ocultas; preservar encabezados, unidades, celdas combinadas, fórmulas y valores guardados. Si hay fórmulas sin valor calculado, imágenes, diagramas, tablas dinámicas u objetos no interpretados, marcar cobertura parcial y revisión interna.

## Límites actuales del lector de adjuntos del proyecto de correo

El lector Python ya enruta PDF, DOC/DOCX, XLS/XLSX/XLSM, KMZ/KML, shapefiles dentro de ZIP, imágenes/OCR, texto, GPX, GeoJSON y algunos planes de vuelo/paquetes ZIP. Conserva localizadores y devuelve motivo/cobertura parcial cuando identifica límites. **Eso aplica a los modos local/API; ChatGPT Work no ejecuta estas librerías ni recibe su resultado automáticamente.**

Límites operativos observados en código:

- 50 MiB máximo por adjunto descargado (el máximo de Gmail es 25 MB), igual en los tres modos. El texto extraído **no se recorta**: si el correo y sus adjuntos no caben en el modelo, el modo local y el de API leen el documento entero por tramos (T-25) y Work lo recibe en el paquete segmentado.
- PDF: máximo 200 páginas; OCR depende de componentes locales instalados y no sustituye la interpretación de diagramas.
- Hojas de cálculo: máximo 50.000 filas/celdas inspeccionadas por hoja; algunos gráficos, imágenes, pivots y fórmulas sin valor requieren revisión.
- ZIP/KMZ y documentos Word, Excel, PowerPoint y OpenDocument (que son ZIP): máximo 200 entradas y 40 MiB expandidos, comprobado antes de descomprimir; si se superan, el archivo pasa a revisión manual. ZIP puede marcar entradas internas no soportadas.
- Shapefile debe llegar completo. Sin `.prj`, coordenadas no deben tratarse como georreferenciadas con CRS conocido.
- RAR/7Z, DWG/DXF, ECW, File Geodatabase y proyectos GMW/QMD requieren lector/conversor especializado o revisión humana; hoy no deben tratarse como contenido leído.

En el histórico, superan 10 MiB (el antiguo límite): **88 PDF, 1.833 DOCX, 68 DWG, 104 ZIP, 53 RAR, 19 KMZ, 22 ECW y 4 XLSX/XLSM/XLS**. Con el límite actual de 50 MiB la mayoría ya se puede leer; los que lo superan se declaran bloqueados con su motivo y pasan a revisión interna. La brecha que queda para una lectura exhaustiva son los formatos sin lector (RAR/7Z, DWG/DXF, ECW, GDB, proyectos nativos), no el tamaño.

## Ruta recomendada para abarcar los formatos sin preguntar redundancias

1. **Inventario por mensaje/hilo:** nombre original, MIME, tamaño, extensión, remitente/mensaje al que pertenece, hash y estado de extracción. No mezclar archivos históricos con adjuntos del hilo.
2. **Lectura estructurada local:** documentos y tablas con localizadores estables (página, párrafo, tabla/fila, hoja/celda, capa/feature/atributo). Recuperar primero versiones pequeñas y completas cuando existan; cualquier límite queda explícito.
3. **Geoespacial:** validar que shapefile esté completo y conserve `.prj`; recorrer todos los KML internos del KMZ; detectar overlays/enlaces, capas, atributos, unidades y CRS declarados. Ningún CRS se infiere por apariencia.
4. **Visual/OCR:** usar OCR como búsqueda auxiliar; los valores que dependen de mapas, figuras, tablas escaneadas o gráficos se verifican visualmente o pasan a persona revisora.
5. **Formatos especializados:** DWG/DXF, RAR/7Z, ECW, GDB y formatos nativos de topografía se convierten con herramientas locales aprobadas, manteniendo original, registro de conversión y advertencias; si no, revisión humana.
6. **Cruce antes de preguntar:** cotejar cada campo pendiente contra correo, hilo y cada evidencia extraída. Si cualquier adjunto relevante no se pudo leer, el estado es revisión interna y no “dato ausente del cliente”.
7. **Medir en casos reales:** usar paquetes representativos por familia y medir cobertura, omisiones, preguntas redundantes, falsos faltantes, errores de OCR y volumen en revisión antes de ampliar límites o automatizar CAD/GIS.

Este inventario guía prioridades, no certifica que un proyecto individual esté completo ni define qué información es comercialmente obligatoria.
