# Prueba manual de T-41 en Work: KMZ, ZIP y adjunto grande

Completa la prueba manual de T-41 (`spec/requisitos.md`, «Work lee los adjuntos directamente desde Gmail»). La primera, del 2026-10-11, leyó un hilo con
cuatro adjuntos y dejó el borrador completo. **Faltan tres casos:** un KMZ, un ZIP y un adjunto grande. Si los tres pasan, T-41 queda probado en Work y se
puede pedir el retiro del camino de Drive (T-45, T-40, `work_drive_enabled`).

Fecha: __________  Versión del prompt (`skill_version` del borrador): __________  Código (`git log --oneline -1`): __________

## 0. Antes de empezar
- [ ] Anka corre (`Iniciar Anka.bat`) y el encabezado dice **Puente activo**. Páginas recargadas si el código cambió.
- [ ] La tarea de ChatGPT Work tiene pegado el prompt vigente completo (el que declara el `skill_version` que se espera; V6 o posterior).
- [ ] **Los hilos son de prueba, con datos ficticios.** Se escriben desde un correo propio hacia el buzón de Anka; no se usan archivos ni hilos de clientes.
- [ ] Un hilo por caso (tres hilos), con asunto que lo distinga: «PRUEBA T-41 KMZ», «PRUEBA T-41 ZIP», «PRUEBA T-41 grande».
- [ ] Copia de seguridad reciente y **sin ejecuciones manuales repetidas de Work** durante la prueba (cuota de Gmail).
- [ ] Hoja de apuntes: tamaño exacto en Gmail de cada adjunto (se compara con lo que Work informa).

## Qué se mira en todos los casos
Se anota para cada hilo, en el caso del dashboard y en la pestaña **Archivos**:
- [ ] Cada adjunto aparece **una vez**, con el **mismo nombre y tamaño** que muestra Gmail.
- [ ] La lectura queda **verificada** (sin aviso de «lectura incompleta» ni «sin verificar») y el caso no pasa a revisión técnica interna.
- [ ] Cada adjunto trae `coverage` (completa, parcial o no legible) coherente con lo que se pudo abrir. Nunca «completa» para algo que Work no abrió.
- [ ] Si Work leyó por el MIME crudo, `read_via` dice `raw_mime`; si lo abrió el conector, `attachment`.
- [ ] Las cifras de la tabla coinciden con las del archivo. Comprobar al menos tres a mano contra el original, además de la comprobación automática de la pestaña (T-38).
- [ ] Work **no** usó Drive para leer ni pidió al cliente algo que ya decía el archivo.

## 1. Hilo con KMZ
**Preparación.** Un KMZ propio y pequeño (Google Earth, o un `.kml` comprimido), con 2 o 3 polígonos con nombre y una descripción con una cifra
conocida («Área prevista: 12,5 ha»). Anotar el área real de cada polígono si se conoce. Enviar el hilo con un texto breve que pida cotizar un levantamiento.

- [ ] Work abre el KMZ (no lo marca «no leído»). Si el conector no lo admite, lo extrae del MIME y lo informa (`raw_mime`).
- [ ] El informe lista los **elementos** (polígonos y nombres) y no inventa el datum ni el CRS.
- [ ] Una etiqueta de área del archivo **no** se presenta como área calculada.
- [ ] Anka muestra el panel **Áreas calculadas** (T-125) con los polígonos del KMZ; el área calculada se acerca a la real y está marcada como dato del programa.
- [ ] Si el correo dice otra área, aparece el aviso de diferencia (T-126).
- [ ] Probar **«Poner el área elegida como cantidad»** (T-130) en una actividad sin cantidad: queda «pendiente» con nota de origen.
- Si falla: anotar si Work no abrió el KMZ, lo marcó completo sin abrirlo o lo leyó mal, y el nombre/tamaño del archivo.

## 2. Hilo con ZIP
**Preparación.** Un ZIP propio con 3 o 4 archivos de tipos distintos (un PDF con texto, una hoja de Excel con una cantidad y un `.kml` o imagen), más un cuarto que
Work **no** pueda abrir (un `.dwg` o un archivo con contraseña, para ver el caso difícil).

- [ ] Work enumera los **componentes que realmente abrió** y no declara el ZIP como completo si dejó alguno sin abrir.
- [ ] El componente que no pudo abrir queda **parcial o no legible, con su motivo** y revisión interna; no sale como pregunta al cliente.
- [ ] El tamaño y el nombre del **ZIP** (no los de sus archivos) coinciden con Gmail y verifican el adjunto.
- [ ] Las cifras de los componentes abiertos llegan a la tabla con su fuente; Anka propone las filas de la hoja con el botón de **Filas con cantidad de los archivos** (T-127/T-128) solo si la tabla no las recogió.
- [ ] Un ZIP dentro de otro ZIP o un nombre con tildes (`Planímetro.xlsx`) no rompe la verificación de nombres.
- Si falla: anotar si Work dio por leído todo el ZIP o si el puente marcó un adjunto «desconocido» por el nombre.

## 3. Hilo con adjunto grande
**Preparación.** Un PDF o una hoja de Excel propios de **más de 10 MiB** (el límite es 50 MiB), con una cifra reconocible al final del archivo
(última página o última hoja). Si hay un archivo más largo de lo que cabe en la ventana del modelo, mejor: es el caso de la lectura por tramos.

- [ ] El tamaño que informa Work (`size_bytes`) es el que muestra Gmail, al byte.
- [ ] Work lee **por tramos** y lo dice (partes revisadas); la cifra del **final** llega a la tabla: prueba de que no se quedó en las primeras páginas.
- [ ] Si Work no puede leerlo completo, queda **parcial**, con campos afectados y revisión interna. No lo marca «sin información».
- [ ] Con un archivo que **supere** los 50 MiB (opcional): se registra nombre y tamaño y queda revisión interna.
- [ ] La importación del borrador no se queda colgada ni reintenta sin parar (mirar el seguimiento del caso y el estado del puente).
- Si falla: anotar tamaño, página o tramo donde dejó de leer y el mensaje de Work.

## 4. Casos de Gmail que conviene provocar una vez
- [ ] **Sin respuesta de Gmail al verificar** (por ejemplo, con la red apagada un momento): el borrador no se descarta ni se da por verificado; la lectura queda «sin verificar» y se reintenta en la siguiente pasada.
- [ ] **Un adjunto de más en el hilo** que Work no informó: la lectura queda incompleta, el caso va a revisión interna y el aviso nombra el archivo; con un análisis posterior de Work se resuelve.
- [ ] **La imagen de la firma** del correo no cuenta como adjunto ni como «adjunto desconocido».

## Resultado
| Caso | Pasó | Observaciones (nombre, tamaño, qué falló) |
|---|---|---|
| KMZ | ☐ sí ☐ no | |
| ZIP | ☐ sí ☐ no | |
| Adjunto grande | ☐ sí ☐ no | |
| Gmail sin respuesta / adjunto de más / firma | ☐ sí ☐ no | |

- [ ] Los tres pasan → anotar la fecha en T-41 de `spec/requisitos.md` (estado de T-41 como «probado en Work») y la decisión correspondiente en `spec/05-decisiones.md`. **No retirar** todavía el código de Drive: es otra tarea, con su propia especificación.
- [ ] Alguno falla → no se avanza al piloto (`PLAN_PILOTO_LECTURA.md` §3). Se describe la falla arriba y se abre un requisito con el hilo (código interno, no nombres) y los archivos de prueba.
