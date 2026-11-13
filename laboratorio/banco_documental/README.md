# Banco de evaluación documental (T-71)

Tres documentos **ficticios** con su respuesta correcta, para medir qué tan bien se extraen cifras, unidades y
origen, y si se separan las preguntas al cliente de las revisiones técnicas internas. Es fase de preparación: **no
ejecuta Work ni la automatización comercial** y nada del paquete `anka/` lo importa.

## Estructura

| Ruta | Qué es | ¿Lo ve el sistema evaluado? |
|---|---|---|
| `datos.py` | Los datos correctos de los tres casos (fuente única) | **No** (contiene las respuestas) |
| `generar.py`, `pdf_minimo.py` | Escriben los documentos a partir de `datos.py` | No |
| `construir.py` | Genera `documentos/` y `referencias/` con manifiestos SHA-256; `--verificar` comprueba que se reproduce | No |
| `documentos/` | `solicitud_01.pdf`, `tdr_02.pdf`, `cantidades_03.xlsx` y `manifiesto_documentos.json` | **Sí** (solo esto) |
| `referencias/` | `C1/C2/C3.referencia.json` y `manifiesto_referencias.json` | **No** |
| `entrega.py` | Copia solo `documentos/` a una carpeta fuera del banco y rechaza cualquier rastro de referencias | — |
| `comparador.py` | Compara una extracción con su referencia y clasifica los errores | No lee nada por su cuenta |

Regenerar: `python -m laboratorio.banco_documental.construir`. Comprobar: `... construir --verificar`.
Preparar la entrega: `python -m laboratorio.banco_documental.entrega <carpeta_fuera_del_repositorio>`.
Evaluar: `python -m laboratorio.banco_documental.comparador <referencias> <extracciones>` (una `<caso>.extraccion.json`
por caso; formato en la cabecera de `comparador.py`).

## Los tres casos

- **C1 · `solicitud_01.pdf`** (PDF digital, 2 páginas): solicitud de levantamiento con Tabla 1 de 5 actividades.
  Trampas: «12 fotografías por punto» frente a 12 puntos, 21,0 ha del predio frente a 18,5 ha a levantar, «25 de
  noviembre» frente a 25 días, «Nota 7» frente a 7 construcciones, «2,350 km» con separador ambiguo. Contradicción
  crítica: 12 puntos en la tabla y 10 en el texto. Faltante bloqueante: el polígono «se enviará por separado».
- **C2 · `tdr_02.pdf`** (PDF escaneado, 2 páginas, sin capa de texto): términos de referencia de batimetría con
  Cuadro 1 y 2, cifra impresa tachada con anotación a mano (45,0 → 41,8 ha), croquis con zona rayada «excluida»
  (3,2 ha), 6,8 m de rampa frente a 6,8 km de ronda, sello «0457», 14 m de profundidad. Faltante bloqueante:
  ubicación remitida a un «Anexo 3» que no viene.
- **C3 · `cantidades_03.xlsx`** (4 hojas, una oculta): fórmulas con su valor guardado, áreas en m² junto a hectáreas,
  comentario de celda, contradicción «7 predios» frente a 6 lotes, hoja oculta con cantidades antiguas (31,2 ha).

## Método de evaluación

1. **Datos primero.** Cada valor y unidad se guardan literales (`2,350`, `ha`); `valor` es la lectura numérica
   aceptada. No hay conversión implícita: 337000 m² no es 33,7 ha para el comparador, y 33,7 m² tampoco.
2. **Origen.** Cada actividad lleva documento, página, tabla, fila (o hoja y celda). El inventario de páginas,
   hojas y tablas está en la referencia.
3. **Emparejamiento.** Cada actividad esperada se empareja, una a una, con la extraída de mayor coincidencia de
   claves; luego se compara valor y unidad.
4. **Clasificación.**
   - *Crítico:* cantidad o unidad equivocada, actividad omitida, cifra de una trampa atada al concepto equivocado,
     contradicción crítica o faltante bloqueante omitidos, restricción crítica omitida, o extracción que cita ids
     internos de la referencia (posible fuga).
   - *No crítico:* lectura ambigua aceptada, entregable o restricción omitidos, faltante no bloqueante omitido,
     pregunta de más o dirigida al destino equivocado (revisión interna enviada al cliente), localizador ausente o
     incorrecto, actividad extra sin cifra de trampa.
   - *No evaluable:* interpretación visual (tachadura, zona rayada, escala): se informa si se mencionó, pero juzgarla
     exige una persona; y todo caso sin extracción.
5. **Informe** con conteos y denominadores reales (`exhaustividad` = correctas/esperadas, `precisión` =
   correctas/obtenidas); si el denominador es cero se dice «sin denominador». Que una extracción cumpla un esquema JSON
   no prueba que sea exacta: cada cifra se contrasta con la referencia.

## Qué requiere OCR y qué interpretación visual

- C1 y C3: texto y celdas extraíbles; no requieren OCR.
- C2: las dos páginas son una imagen; **todo** requiere OCR. La tachadura sobre 45,0, la anotación a mano, la zona
  rayada y la relación entre la leyenda y el croquis requieren **interpretación visual**; el área neta de 41,8 ha es
  derivada (45,0 − 3,2), no está impresa.

## Cómo se evita que el sistema evaluado vea la respuesta

Documentos y referencias están en carpetas hermanas con manifiestos separados; los nombres de archivo y los metadatos
son neutros; `entrega.py` copia solo `documentos/`, exige un destino fuera del banco y vacío, y verifica que no haya
referencias, ids internos (`C1-A1`…) ni pistas. El comparador solo recibe la referencia que se le pasa. Aun así, nada
impide que alguien lea `referencias/` si lo tiene a la vista: **ejecute al sistema evaluado solo con la carpeta de
entrega**, nunca con el repositorio.

## Procedimiento propuesto para una ejecución supervisada de Work (NO ejecutado)

1. **Autorización.** Una persona de Anka autoriza expresamente la prueba y el espacio que se usará; no se corre sobre
   cuentas ni hilos reales.
2. **Entrega.** `entrega` a una carpeta fuera del repositorio; comprobar los SHA-256 con `manifiesto_documentos.json`.
3. **Espacio aislado.** Un buzón o carpeta de Drive de prueba, sin conexión con el dashboard de producción
   (`work_dispatch_enabled` y `work_drive_enabled` siguen en `false`); ningún correo sale a un cliente (R8).
4. **Ejecución por caso.** Un correo ficticio por documento, con el mismo prompt y la misma skill vigentes; sin pistas
   extra ni mención del banco.
5. **Captura.** Guardar la salida de Work tal cual y convertirla, sin corregirla, al formato de extracción; registrar
   versión de prompt, skill y fecha.
6. **Evaluación.** Correr `comparador` fuera del entorno de Work; una persona revisa los hallazgos «no evaluable».
7. **Informe.** Resultado por caso con conteos y denominadores; no se promedian casos distintos sin decirlo.
8. **Repetición.** Repetir al cambiar el prompt, la skill o los lectores, regenerando el banco con `--verificar`.

## Limitaciones conocidas

- El banco son **tres** casos: sirve para detectar fallos, no para estimar tasas de acierto generales.
- El escaneado depende de las fuentes TrueType del equipo (su huella queda en el manifiesto); otra máquina puede
  producir otros píxeles, aunque los datos sean los mismos.
- Las reglas de emparejamiento usan raíces de palabras: un nombre de actividad muy distinto se contará como omisión.
- Las referencias de C2 asumen la anotación a mano y el croquis tal como se dibujaron; no cubren manuscritos reales.
