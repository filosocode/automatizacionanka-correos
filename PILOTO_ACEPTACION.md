# Piloto de aceptación (Fase 1, modo ChatGPT Work)

Prueba guiada de punta a punta con un caso real (p. ej. Llanos 8). Es también la **prueba manual en Work**
que exige `ESTANDAR_CAMBIOS.md` para las reglas versión 5 (R11). Anota fecha y resultado de cada paso.

Fecha: __________  Versión de las reglas: **5**  Caso: __________

## 0. Antes de empezar
- [ ] Anka está corriendo (`Iniciar Anka.bat` o autoarranque) y la etiqueta del encabezado dice **Puente activo**.
- [ ] En la tarea de ChatGPT Work pegaste el bloque de `INSTRUCCIONES.txt` (sección «AÑADIR A LAS INSTRUCCIONES DE LA TAREA ACTUAL»), con `requested_by_email`, `requested_by_name`, `cited_codes` y la regla de la T.
- [ ] Tienes dos correos de prueba: el del **contacto registrado** (quien escribió primero) y **otro** distinto.

## 1. Aceptar el proyecto
1. Abre el caso → panel **Aceptar el proyecto** → confirma el número de la T → **Aceptar proyecto y asignar la T**.
   - [ ] El caso muestra `T…` en la cabecera y en el panel «Código de la oferta».
2. Se abre el correo de **proyecto recibido**: revísalo y **Enviar ahora**.
   - [ ] El correo incluye el código y recomienda escribir desde el correo registrado.
   - [ ] En *Seguimiento* aparecen «Se aceptó el proyecto…» y «Correo enviado…».

## 2. Tres mensajes del cliente (responde en el mismo hilo)
Espera 1–2 minutos entre cada uno y mira el aviso arriba del caso.

| # | Quién escribe | Texto | Debe pasar |
|---|---|---|---|
| A | Contacto registrado | «Ref. T… : el plazo es de 30 días» | Aviso *Cita la T · Contacto registrado*. Al confirmar «Es un cambio»: solo falta **«Cambio recibido»** al contacto. Cuando Work responda, la tabla **sí** se actualiza. |
| B | Contacto registrado | «El plazo ahora es de 25 días» (sin código) | Aviso *No cita la T*. La tabla **no** cambia (queda como propuesta). Falta **pedir el código**. |
| C | **Otro** correo | «Ahora son 20 días» (con o sin código) | Aviso *Otra persona*. La tabla **no** cambia. Faltan el correo a quien escribió **y el aviso al contacto registrado**. |

- [ ] A: la tabla se actualizó sola.
- [ ] B: la tabla se conservó y aparece «El modelo sugiere…» o el seguimiento dice «no se aplicaron (R11)».
- [ ] C: ídem, y hay dos borradores pendientes.
- [ ] El correo para quien **no** es el contacto **no** contiene el código.

## 3. Lo que debe haber informado Work (revisa en el seguimiento o en el borrador)
- [ ] `requested_by_email` = quien escribió el mensaje analizado.
- [ ] `cited_codes` = los códigos tal como aparecen (vacío si no cita).
- [ ] Work **no** escribió el código en ningún borrador dirigido a otra persona.

## 4. Edición del comercial (no debe enviar nada)
1. Edita una fila de la tabla, elige un motivo y guarda.
   - [ ] Aparece «Última edición tuya: versión N… el cliente no ha sido avisado».
   - [ ] **No** salió ningún correo. «Preparar un correo de actualización» es opcional.

## 5. Evidencia
- [ ] Abre **Acta de cambios**: integridad **Verificada**, quién ha escrito, solicitudes de cambio, versiones de la tabla.
- [ ] Existen copias `.eml` en `evidence/<hilo>/` (carpeta de datos) de los mensajes posteriores a la T.
- [ ] Elimina un caso de prueba y confirma que su historial aparece en **Archivo**.

## Resultado
- [ ] Todo conforme → las reglas versión 5 quedan validadas en Work.
- [ ] Fallas (descríbelas): ______________________________________________
