# T-119 · Robustez del extractor y preparación del modo sombra (laboratorio)

Estado: **T119_LAB_VALIDADO**. Nada de `anka/`, contratos, configuración, SQLite operativa, Gmail, Drive ni Work fue tocado. No hay
commits ni push. Lo derivado sigue en `derivados/` (ignorado por Git) y la base de pruebas del modo sombra vive en carpetas temporales.

## 1. Unidades (fase 1) — `unidades.py`
Antes (T-118): una cifra cuya palabra de medida no estaba en el catálogo **se descartaba en silencio**. Ahora:
| Clase | Qué es | Qué se registra |
|---|---|---|
| `reconocida` | catálogo: km, ha, m, m², m³, cm, mm, %, kg, ton, litros, minutos, segundos, grados, años, puntos, perfiles, lotes, predios, quebradas, construcciones, fotografías, tramos, secciones, ejes, vías, vértices, hitos, mojones, estaciones, unidades, horas, días, semanas, meses… | cantidad y unidad literales |
| `unidad_ambigua` | abreviaturas con varios significados: `gl` (global/galón), `ml` (metro lineal/mililitro), `pt`, `pts`, `h` | literal + `unidades_posibles`; **no se elige** |
| `unidad_no_reconocida` | minúscula tras la cifra y fuera del catálogo («5 ingenieros», «3 bms», «12,5 yardas», «3 m/s») | literal; confianza `baja`; revisión humana; el caso deja de ser «completo» |
| `sin_unidad` | celda de Excel numérica sin unidad | se conserva y se cuenta |
| compuestas | `puntos/km`, `km/h`, `m/s`: se evalúa cada parte; basta una desconocida para `unidad_no_reconocida`, una ambigua para `unidad_ambigua` | `unidad_compuesta: true` |

Nunca se convierte ni se normaliza. En Excel, la celda a la derecha es unidad si está en el catálogo, es ambigua o su columna se titula «Unidad»; un
«(…)» en el encabezado también se toma como unidad (y se clasifica igual).

**Falsos positivos evitados** (con pruebas): horas (`10:30 m`, `10 am`), fechas (`2026-10-23 ha`, `23/10/2026`, `25 de noviembre`), cifras tras una etiqueta
(`Nota 7`, `Folio 12`, `Cuadro 2`, `Figura 1`, `Lote 5`, `No. 14`, `Ítem 3`, `Sello 0457`, `Artículo 4`), códigos pegados a letras o guiones (`AB12`, `P-12`),
numeración de lista de 1–2 cifras al inicio de línea, y cifras seguidas de artículos, preposiciones o meses.
**Lo excluido no desaparece**: cada página lista sus «cifras no registradas» (cuenta + muestra de 20).

**Límites que quedan** (declarados en el código y en `analisis.limitaciones`): una unidad desconocida en MAYÚSCULAS o pegada a la cifra no se detecta como tal
(`5 ML`, `5hitos`); una palabra común en minúscula («3 primeros») se registra como unidad desconocida y pide revisión; una numeración de 1–2 cifras al inicio de
línea seguida de un sustantivo («10 ingenieros» en su propia línea) se trata como numeración y queda solo en «cifras no registradas»; en un PDF, una cifra cuya
unidad está en el encabezado de la tabla (p. ej. el Cuadro 2 de C2, «Área (ha)») no se asocia a su unidad: aparece en «cifras no registradas».

## 2. Cobertura y omisiones (fase 2)
- **Evidencia localizada ≠ actividad interpretada**: `analisis.alcance = "evidencia_localizada"`, `actividades_interpretadas = 0`, `interpretaciones = []`.
- **Sin inferir recuperación**: `analisis.exhaustividad = "no_determinable"` siempre. `declarado_completo` solo significa «cobertura de lectura sin pendientes»; así
  lo dice la lista `limitaciones`. C1 pasa el validador sin errores y se declara completo, y aun así no puede afirmar que estén todas las actividades.
- **Por página/hoja**: cada elemento de `cobertura.detalle` lleva `lectura` (`sin_indicios_de_omision` o `no_determinable`) y `motivo_lectura`. Son `no_determinable`:
  páginas leídas con OCR, con contenido visual pendiente, sin OCR, ilegibles u hojas con lectura parcial/fórmulas sin valor. «Sin indicios» no prueba nada: es solo la
  ausencia de motivos concretos para dudar.
- Por página PDF: `cifras_no_registradas` y su muestra; por hoja Excel: `celdas_numericas` y `celdas_numericas_sin_unidad`.
- Lo visual de C2 (tachadura, zona excluida, norte, escala) sigue `pendiente_revision_visual`.
- Referencias congeladas C1/C2/C3 y su manifiesto: sin cambios (huellas verificadas por prueba).
- Los 3 casos conservan exactamente los mismos identificadores de evidencia que en T-118 (comprobado contra la copia de la corrida anterior).

## 3. Modo sombra (fase 3) — `sombra.py`, `sombra_trabajador.py`, `versiones.py`
| Requisito | Cómo está resuelto | Prueba |
|---|---|---|
| Interruptor desactivado por defecto | `Configuracion.habilitada=False`; `desde_entorno` solo enciende con `ANKA_EVIDENCIA_SOMBRA=1`; archivo `archivo_apagado` lo detiene aunque esté encendido; apagado = no crea ni la base | 01–04 |
| SQLite separada | `sombra_lab.db` propio; se rechaza cualquier ruta dentro de `%LOCALAPPDATA%\AnkaCorreos` o llamada `anka.db`, `anka.sqlite`, `store.db` | 30, 31 |
| Identidad | `UNIQUE(job_id, sha256, version_extractor)`; se guardan también versión del validador y del modelo | 10, 12, 13, 15 |
| Idempotencia | repetir devuelve `existente` sin tocar filas; una fila buena nunca se sobrescribe; una fallida se reemplaza al reintentar (transacción única) | 11, 22 |
| Plazo máximo | proceso hijo que se mata al vencer; el plazo cubre el trabajo completo | 20, 21 |
| Fallos aislados | `procesar` nunca lanza (error de hijo, plazo, base inaccesible o ruta prohibida vuelven como estado); `lanzar` usa un hilo demonio y devuelve en <0,5 s | 23–28 |
| Sin escrituras externas | el padre no importa extractor, lectores, Gmail, Drive, Work, red ni `anka.*`; el hijo solo lee su entrada y escribe su salida | 32–35 |
| Sin veredicto de exhaustividad | columna `exhaustividad = no_determinable` | 10, 40 |
| Casos históricos | C1, C2 y C3 originales por la sombra: 11/15/29 evidencias, 0 errores, originales intactos | 40, 41 |

Tablas: `meta`, `extraccion`, `evidencia` (JSON por evidencia), `hallazgo`. El contenido del adjunto viaja al hijo por un archivo temporal que se borra al terminar.

## 4. Lo que NO se hizo (a propósito)
Ninguna conexión a ANKA: no hay llamada desde la recepción, ni bandera en `config.json`, ni tabla en la SQLite operativa, ni cambio en el contrato con Work. El OCR
operativo sigue sin activarse (el hijo usa el OCR de laboratorio con carpeta de datos temporal).

## 5. Cómo repetirlo
`.venv\Scripts\python.exe -X utf8 -m unittest discover -s laboratorio/evidencia_documental -t . -p "test_*.py"` (162 pruebas) y
`.venv\Scripts\python.exe -X utf8 -m laboratorio.evidencia_documental.banco`.
