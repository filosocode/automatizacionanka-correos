# Piloto de aceptación (Fase 1, modo API)

Prueba guiada de punta a punta con un caso real, en el **modo `api`** (D-203: Work está en pausa; su piloto sigue en
`PILOTO_ACEPTACION.md`). Es la prueba manual que exige `ESTANDAR_CAMBIOS.md` para lo que solo el modelo real puede
confirmar. Anota fecha y resultado de cada paso.

Fecha: __________  Rama/commit: __________  Modelo de la API: __________  Caso: __________

## 0. Antes de empezar
- [ ] Instancia aislada (datos propios y puerto 8766, ver `prueba-modo-api-t43` en la memoria) o la de pruebas; **no** la real.
- [ ] `OPENAI_API_KEY` puesta por ti en esa ventana. Nadie más la escribe ni la imprime.
- [ ] `active_mode = api` y el encabezado no dice «Revisión de correos: apagada».
- [ ] Recargaste el navegador con Ctrl+F5 después del último cambio de código.

## 1. Lista de solicitudes (U-107)
- [ ] Cada caso es una fila: proyecto como título, cliente · código · motivo debajo, un solo botón con la acción.
- [ ] Letra fina (15 px) y sin número de posición delante del título.

## 2. Pantalla del caso con la decisión pendiente (U-103, U-106, U-107)
- [ ] Dos columnas iguales: «Actividades detectadas» a la izquierda y «Mensaje para el cliente» a la derecha.
- [ ] Cabecera, recorrido de pasos y pestañas tienen el mismo ancho que las dos tarjetas.
- [ ] Si la tabla o la caja tienen mucho contenido, se desplaza **por dentro** sin cambiar el tamaño de las tarjetas.
- [ ] Cada fila tiene su casilla (redondeada, naranja al marcarla). Marcar o desmarcar suma o quita su línea en la caja.
- [ ] La pregunta de cada fila dice lo mismo que su «Qué hace falta» y está escrita como dato pedido
      («Control vertical: la longitud total del trazado (km).»), no como «¿Podría…?».
- [ ] Las preguntas que no son de una actividad salen en «Generales», marcadas; las del modelo llevan
      «sugerida por el análisis». Desmarcar una la quita de la caja.
- [ ] Lo que escribes a mano en la caja no se pierde al marcar o desmarcar casillas.

## 3. Lo que debe devolver el modelo (U-108, D-202) — **lo único que solo se prueba con la API real**
Cuenta las preguntas de `other_questions` del análisis de este caso:
- Total: ____  Como dato pedido: ____  Siguen con «¿Podría…?»: ____  Elección entre opciones (deben ser pregunta): ____
- [ ] Ninguna pregunta repite un dato que el correo o un adjunto ya traían.
- [ ] Cada una pide UNA sola cosa y nombra el documento tal como lo cita el cliente.

## 4. El correo (U-24, U-26, U-108)
Pulsa «Redactar el correo» y revisa el texto sin enviar:
- [ ] Nombra el proyecto: «Gracias por su solicitud para el proyecto «X»…» (con código T: «Sobre su proyecto «X» (T…)»).
- [ ] La apertura dice «necesitamos confirmar lo siguiente:» y los puntos van numerados.
- [ ] Se lee natural: no repite «podría» en cada punto.
- [ ] Sin código T no cita ninguno; con código solo se cita al contacto registrado (R11).
- [ ] Nada sale al cliente sin que lo envíes tú (R8).

## 5. Segunda ronda (U-40, U-98)
Responde al correo desde otra cuenta con un dato y deja que el sistema lo lea:
- [ ] El caso pasa a ronda 2 y el correo nuevo abre con «Gracias por su respuesta…» y «Nos queda por confirmar lo siguiente:».
- [ ] **No** vuelve a pedir lo que el cliente ya contestó ni lo ya preguntado.

## 6. Evidencia
- [ ] Capturas de la lista, del caso y del correo, y el conteo del paso 3, guardados con la fecha.
- [ ] Si algo falló, anota el id del caso y qué dijo el modelo (sin pegar datos del cliente en ningún chat).

## Resultado
Aceptado / Aceptado con observaciones / No aceptado: __________   Observaciones: __________
