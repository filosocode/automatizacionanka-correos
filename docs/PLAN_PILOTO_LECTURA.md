# Plan del piloto supervisado de lectura (Fase 1, modo ChatGPT Work)

**Estado: propuesta, sin ejecutar.** Complementa `ENTREGA_V1_0.md` (producción supervisada del 23/10/2026) y `PILOTO_ACEPTACION.md` (prueba guiada
de un caso). No cambia el comportamiento de ANKA. Los umbrales de §6 son propuestos: se ajustan con Anka antes de empezar.

## 1. Qué debe decidir el piloto
1. **¿Work deja fuera cifras de los adjuntos?** Hoy se sabe que lo que escribe es fiel al texto del programa (60 cifras comprobadas en 10 casos,
   0 diferencias; medición del 2026-10-27 sobre la base real), pero **no** cuánto de lo que dicen los adjuntos llega a la tabla (cobertura).
2. **¿Las ayudas nuevas (T-124 a T-131) sirven en casos reales?** Se midieron con 20 casos de carpetas, no con hilos reales: filas de Excel y Word
   que se agregan con un botón, áreas de KML/KMZ, cifras de los PDF que la tabla no menciona.
3. **¿Se justifica cambiar la forma de leer?** (por ejemplo darle a Work el texto que extrae el programa, o la hoja de OCR auxiliar V1.1). Solo si
   la cobertura medida lo pide: no se cambia el prompt ni el contrato por una opinión.

## 2. Alcance
- **De 5 a 10 hilos reales y recientes**, en modo `chatgpt_work`, elegidos para cubrir formatos: al menos 2 con Excel o Word con tablas, 2 con
  KML/KMZ, 2 con PDF con texto, **1 o 2 con PDF escaneado** (se cuentan aparte: V1.0 ya documenta que Work puede no leerlos) y 1 con varios adjuntos.
- **Una persona de Anka revisa cada caso** antes de que algo salga al cliente (R8). El piloto no envía nada nuevo: usa el flujo supervisado de V1.0.
- Duración propuesta: una semana de trabajo; el tamaño real lo fija la llegada de solicitudes.

## 3. Antes de empezar (todo marcado)
- [ ] **Rama revisada y subida** (push y PR; lo decide el dueño). El piloto corre sobre código revisado, no sobre commits locales.
- [ ] **Prueba manual de Work con el prompt V6 completa (T-41):** faltan un hilo con KMZ, uno con ZIP y uno con adjunto grande (pasos en [`PRUEBA_MANUAL_T41.md`](PRUEBA_MANUAL_T41.md)).
- [ ] **Anka revisó los 20 casos esperados** (`hoja_de_revision_esperados.xlsx`, hoy «revisado por Claude, pendiente de Anka»), y confirmó los valores del caso 04.
- [ ] **Operación:** equipo encendido y sesión de Windows iniciada, copia del intérprete fuera de `Temp` (ENTREGA_V1_0 §3.4), respaldo reciente, dashboard reiniciado
  y páginas recargadas (el lanzador reinicia dashboard y puente al cambiar el código).
- [ ] **Herramienta de cobertura** (§5.1): `python -m laboratorio.piloto.cobertura` (T-132, D-191). Ya está construida y probada. Lee solo la base de ANKA (copia de solo lectura) y los
  textos que el flujo ya guarda en `manifiestos/`: **no abre Gmail**, así que no pide acceso nuevo a correos reales.
- [ ] **Los hilos del piloto tienen textos guardados.** La herramienta omite un hilo sin tabla, sin textos o con textos incompletos y dice por qué; hoy solo 2 de los 14 casos de la
  base se pueden medir. Correrla con el primer hilo y comprobar que no lo omite.
- [ ] **Hoja de registro del piloto** (§7) creada, fuera del repositorio.

## 4. Qué se prueba que V1.0 no probó
| Cambio | Dónde se ve en el dashboard | Qué se espera |
|---|---|---|
| Lectura de PDF por celdas y ligaduras, OCR que conserva cifras (T-124) | Pestaña «Archivos» | Las cifras del cliente salen en la lectura |
| Áreas calculadas de KML/KMZ (T-125) y aviso si el correo dice otra (T-126) | «Áreas calculadas…» | Área coherente con el archivo; aviso solo cuando difiere |
| Poner un área como cantidad de una actividad sin cantidad (T-130) | Mismo panel, botón «Poner el área elegida como cantidad» | La actividad queda «pendiente» con nota de origen |
| Filas de Excel y Word que la tabla no recoge (T-127, T-129) y botón para agregarlas (T-128) | «Filas con cantidad de los archivos…» | Solo se agregan las que pidió el cliente |
| Cifras de los PDF que la tabla no menciona (T-131) | «Cifras de los PDF…» | Pista útil, sin botón |

## 5. Qué se mide
1. **Cobertura (lo que falta saber).** Por hilo, la herramienta toma los textos que el flujo ya extrajo del correo y de cada adjunto, busca las cifras con unidad de alcance
   (las mismas de T-131) y las contrasta con la tabla de Work. Deja en pantalla solo conteos y una hoja `cobertura_piloto.xlsx` fuera del repositorio. Una persona clasifica cada cifra que falta:
   **real** (debía estar en la tabla), **contexto** (va en notas o plazo) o **ruido**. Con `--clasificadas` la herramienta cuenta lo clasificado.
   Métrica: cifras reales presentes en la tabla ÷ cifras reales totales. Se reporta aparte lo medido sobre PDF escaneados (los adjuntos «no legibles» salen contados por hilo).
2. **Fidelidad (ya existe).** `evidence_check`: cifras comprobadas, diferencias y no comprobables por hilo. Se espera 0 diferencias.
3. **Uso de las ayudas.** Por caso: filas agregadas con el botón y cuántas se borraron después; áreas puestas; cifras de PDF que la persona marcó como útiles o como ruido; avisos de área inútiles.
4. **Tiempo por caso** frente a la estimación de hacerlo a mano (la da la persona que revisa).
5. **Operación.** Borradores rechazados por el puente (hoy 7, el último el 19/10), casos con «lectura sin verificar» y caídas del vigilante.

## 6. Criterios de decisión (propuestos)
| Resultado | Decisión |
|---|---|
| Alguna cifra inventada por Work o con diferencia sin explicar | **Parar** y corregir antes de seguir |
| Cobertura ≥ 90 % de las cifras reales en los casos con texto | Work lee suficiente: sin cambio de lectura |
| Cobertura < 90 % en los casos con texto | Evaluar entregarle a Work el texto del programa (requiere prompt y contrato nuevos, y su prueba manual) |
| PDF escaneados con lectura pendiente en más de la mitad de los escaneados | Priorizar la hoja de OCR auxiliar V1.1 |
| Más de un tercio de las pistas marcadas como ruido | Ajustar los filtros de T-127 y T-131 antes de ampliar |
| Tiempo por caso no menor que a mano | Replantear qué ayuda de verdad antes de ampliar el piloto |

## 7. Procedimiento por caso
1. Esperar el borrador de Work y abrir el caso (recargar la página). Anotar fecha, hilo (solo el código interno) y formatos de los adjuntos.
2. Leer **Actividades**, **Archivos** y la nota de lectura (revisión obligatoria, ENTREGA_V1_0 §5). Si dice «No pudimos leer…», el caso no alcanza hasta leer el original.
3. Revisar las ayudas de §4: agregar o descartar y **anotar por qué**.
4. Ejecutar `python -m laboratorio.piloto.cobertura <hilo>` y clasificar en la hoja las cifras que falten (real, contexto o ruido).
5. Decidir como siempre: confirmar, pedir datos o preparar el correo. **No enviar nada fuera del flujo de V1.0.**
6. Registrar en la hoja del piloto: formatos, cifras reales/contexto/ruido, filas y áreas agregadas, minutos, observaciones.

## 8. Riesgos
- **Privacidad:** los textos de los adjuntos y la hoja del piloto contienen datos de clientes. Se guardan solo en este equipo, nunca en el repositorio, y la hoja lleva el código interno, no nombres.
- **Textos guardados:** la cobertura solo se mide en hilos cuyos textos guardó el flujo. Si un hilo sale omitido, no se mide a mano: se anota y se revisa por qué no se guardó.
- **Muestra pequeña:** 5 a 10 hilos dan una señal, no una estadística. Cada criterio de §6 se lee con esa cautela.
- **Dos manos en la tabla:** si Work reprocesa un hilo mientras una persona edita, la edición queda protegida (U-34), pero se avisa al revisor y se registra.
- **Cambios de código durante el piloto:** se congelan; un arreglo urgente reinicia dashboard y puente y se anota en la hoja.

## 9. Calendario propuesto
| Día | Qué |
|---|---|
| 0 | Antes de empezar (§3) y hilo de prueba de la herramienta de cobertura |
| 1 a 4 | Hilos reales a medida que llegan, con el procedimiento de §7 |
| 5 | Cierre: tabla de resultados, aplicar §6 y decidir con Anka |

## Resultado
- [ ] Decisión de §6 anotada con fecha, versión del código (`git log --oneline -1`) y responsables.
- [ ] Hoja del piloto archivada fuera del repositorio.
