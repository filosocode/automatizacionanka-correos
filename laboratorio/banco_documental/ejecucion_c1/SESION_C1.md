# Sesión supervisada C1 — SEGUNDO INTENTO (T-92) — PREPARADA, NO EJECUTADA

Complementa el §11 de `PLAN_PRUEBA_INTEGRADA.md` y no concede ninguna autorización. Herramienta: `ejecucion_c1/c1.py`
(70 pruebas en `test_c1.py`, con Gmail y Drive simulados y bases temporales). Todos los comandos se ejecutan desde `D:\Automatizacion_Correos`
con `.venv\Scripts\python.exe -I laboratorio\banco_documental\ejecucion_c1\c1.py <orden>` (abreviado `c1` abajo).

## 1. El correo (exacto)
- **De:** 91andresmunoz@gmail.com · **Para:** ankacomercialia@gmail.com (sin copia ni copia oculta) · **Adjunto único:** `laboratorio\banco_documental\documentos\solicitud_01.pdf` (4913 bytes, SHA-256 `f47ed4da974e982b09954bfd1b71472b0fefa5859309d7ab1e24c924c92bd844`).
- **Asunto (99 caracteres, exacto):** `[PI-C1-T92-001] Solicitud ficticia de laboratorio - levantamiento topográfico (SIN COTIZACIÓN REAL)`
- **Cuerpo:**
  ```
  PRUEBA DE LABORATORIO ANKA - identificador PI-C1-T92-001.
  Esta es una solicitud FICTICIA para probar la automatización. No corresponde a ningún cliente ni proyecto real
  y no debe cotizarse. Los datos están en el PDF adjunto (solicitud_01.pdf).
  Por favor, no enviar respuesta al remitente.
  ```
- Se envía **a mano** por el operador. Enviar no es reversible.
- **Antes de pulsar «Enviar», comprobar el adjunto en el borrador:** nombre `solicitud_01.pdf` y 4913 bytes, copiado de `laboratorioanco_documental\documentos\`. En el primer intento (T-91) se adjuntó otro archivo (`solicitud_ficticia_alternativa_NO_C1_original.pdf`, 49 394 bytes) y hubo que cancelar. El controlador ya lo detecta, pero ocurre después del envío.
- **Historia (T-91):** el mensaje `[PI-C1-T89-001]` (ids `1a162bea8208dce5`) está **cancelado** con lápida y su evidencia se conserva en `evidencia_t91\`. El marcador nuevo no lo toca ni se confunde con él.

## 2. Regla de oro (decisión T-89B)
**La coordinación no se restaura mientras exista un correo C1 enviado cuya identidad o registro no esté resuelto y que pueda publicarse solo.**
`c1 restaurar` y `c1 vigilar` lo hacen cumplir: consultan Gmail, SQLite y Drive en solo lectura y **solo restauran** en uno de tres casos:

| # | Condición comprobada | Categoría |
|---|---|---|
| 1 | Gmail no muestra el marcador **y** el operador declaró `confirmar-envio no_enviado` | `ausencia_comprobada` |
| 2 | Lápida del mensaje C1 (trabajo `cancelado` en SQLite + entrada en el diario), con ids y asunto verificados en Gmail (y en SQLite si ya se registró) | `cancelacion_verificada` |
| 3 | Un único trabajo `publicado` o `conciliado` del mensaje C1 con **exactamente una** hoja con su marca en PENDIENTES/CERRADOS | `trabajo_publicado` / `trabajo_conciliado` |

Todo lo demás es **NO** (y la herramienta dice qué hacer): Gmail inaccesible, resultados múltiples o inválidos, ausencia sin declaración
del operador (**la ausencia en Gmail no prueba que no se envió**), «enviado pero invisible», declaración contradictoria, registrado sin trabajo,
`pendiente_de_publicar`, `publicando`, `ambiguo`, dos trabajos, 0 o 2 hojas, Drive inaccesible, lápida sin diario o con asunto distinto, o un
trabajo ajeno sin cerrar. No existe `--forzar`.

## 3. Secuencia con autorizaciones independientes (cada una por escrito; ninguna implica otra)
| # | Acción | Autorización | Comando | Parar si… |
|---|---|---|---|---|
| 0 | Preflight rápido (lectura) | — | `verificar_post_t83.ps1`; `c1 estado`; PENDIENTES vacía | algo difiere de la instantánea (SHA `config.json` `73b6f4ec…ca38`, eventos máx. 917, 8 trabajos conciliados) |
| 1 | Pausar (**inicia el reloj de 15 min**) | **AUT-CONFIG-1** | `c1 pausar --confirmar` | rechaza (ciclo reciente, trabajos no conciliados, pausa previa sin restaurar) |
| 2 | Vigilar el plazo (en una segunda consola, durante toda la sesión) | — (con `--confirmar` solo restaura si se cumple 1, 2 o 3) | `c1 vigilar` (o `c1 vigilar --confirmar` si se dio AUT-RESTAURAR por adelantado) | — |
| 3 | Comprobación rápida de pausa | — | `c1 estado`: `work_coordination_enabled=False`, plan vacío `True`, trabajos sin publicando, receptor avanzando, eventos sin cambio | cualquier cosa distinta |
| 4 | Enviar el correo | **AUT-ENVIO** | a mano; inmediatamente `c1 confirmar-envio enviado --operador NOMBRE` | más de un mensaje, otro destinatario, otro adjunto |
| 5 | Ids exactos desde Gmail (sin esperar al receptor) | — | `c1 ids` (reintentar hasta 3 min). Exige UN mensaje entrante del remitente al destinatario, asunto exacto y un solo mensaje en el hilo, y **descarga el adjunto**: uno solo, `solicitud_01.pdf`, 4913 bytes, SHA-256 `f47ed4da…d844`. Con un adjunto distinto guarda los ids (para poder cancelar) pero sale con error y **no habilita publicar** | varios, enviado por la cuenta comercial, asunto distinto, cc, hilo con más mensajes, **adjunto distinto (nombre, tamaño o SHA-256), dos adjuntos o sin contenido** |
| 6 | Contrastar | — | los ids que imprime `ids` son los del buzón de ankacomercialia; el SHA-256 del adjunto ya lo comprobó `ids` (contrastar a ojo el resumen impreso) | difieren |
| 7a | **Continuar:** esperar el registro (≤ 60 s) y comprobar | — | `c1 localizar` **repite la verificación del adjunto en Gmail** y comprueba en SQLite: registrado, asunto exacto, adjunto registrado `solicitud_01.pdf` 4913 bytes, fila pendiente, sin trabajo ni lápida y elegible. Guarda `verificacion.json` (vale 5 min) | no registrado a los ~3 min, algo distinto, o no elegible (no forzar sin otra autorización) |
| 7b | **Publicar** | **AUT-PUBLICACION** | `c1 comandos publicar` imprime el comando **solo si** `ids` y un `localizar` reciente (≤ 5 min, mismos ids) lo dejaron todo en orden; si falta cualquier verificación crítica **no entrega ningún comando**. Se ejecuta aparte | `ambiguo`, `error`, más de un trabajo o de una hoja |
| 7c | **Abortar** (alternativa, nunca las dos) | **AUT-CANCELAR** (distinta de AUT-PUBLICACION) | `c1 comandos cancelar` imprime el comando (`--preventiva` solo si aún no está registrado); luego `work_jobs verificar-lapidas` | `rechazada` por la cancelación: leer el motivo y no restaurar |
| 8 | Verificar | — | tras 7b: PENDIENTES = 1 hoja con `anka_job_id`, 9 trabajos (1 `publicado`). Tras 7c: trabajo `cancelado`, `verificar-lapidas` en orden total y, una vez registrado, `c1 localizar` con asunto exacto | cualquier otro cambio |
| 9 | Restaurar (**cierra el reloj**) | **AUT-RESTAURAR** | `c1 restaurar --confirmar` (evalúa 1, 2 o 3 antes de escribir); luego `c1 estado` y SHA-256 de `config.json` = `73b6f4ec…ca38` | rechaza: no se fuerza |

**Despacho manual (producido por `c1 comandos publicar`, no ejecutado):**
`.venv\Scripts\python.exe -m anka.cli.work_jobs despachar --carpeta-id 1IvNxAoIQmPN8o7_HUB8oqqidtA3asK2w --thread-id <T> --message-id <M> --confirmar-drive`
— `--carpeta-id` es la **raíz** (`_PRUEBA_ESCRITURA_WORK`), no PENDIENTES (`1cFvXLrilal2hWMc_JyU8Xa-5fKd5ZlNW`). No hace falta `work_dispatch_enabled`.

**Cancelación (producida por `c1 comandos cancelar`, no ejecutada):**
`work_jobs cancelar --thread-id <T> --message-id <M> --etiqueta "[PI-C1-T92-001]" --operador "<NOMBRE>" --motivo "<MOTIVO>" [--preventiva] --confirmar`

## 4. Plazo (control efectivo) y recuperación
- **Tope: 15 min** desde `PAUSA_APLICADA`; `pausa.json` guarda el vencimiento y `c1 estado` muestra los segundos que quedan o «VENCIDA».
- `c1 vigilar` avisa a 3 min del final. **Al vencer**: si C1 cumple 1, 2 o 3 y hay `--confirmar`, restaura; en cualquier otro caso queda **BLOQUEADO** sin tocar la configuración y dice qué hacer. Nunca restaura antes del vencimiento por su cuenta. **El tope no es permiso para prolongar la pausa**: extenderla exige una nueva autorización escrita (nueva AUT-CONFIG-1).
- Presupuesto: envío ≈ 2 min, ids ≤ 3 min, comprobaciones ≈ 3 min, despacho o cancelación ≈ 1 min, restauración ≈ 1 min.
- **Por estado de C1:**
  | Situación | Qué hacer | ¿Restaurar? |
  |---|---|---|
  | Pausado, no enviado | `confirmar-envio no_enviado` y restaurar | Sí (caso 1) |
  | Enviado, `ids` ve 1 mensaje, no registrado | Continuar (7a/7b) o AUT-CANCELAR `--preventiva` (7c) | Solo tras publicar o tener lápida |
  | Registrado, elegible, sin trabajo | Preferido: publicar (7b). Si se decide abortar antes de publicar: AUT-CANCELAR | Solo tras publicar o lápida |
  | `pendiente_de_publicar` sin hoja | Publicar o cancelar | Igual |
  | Registrado con **adjunto incorrecto** (como en T-91) | No publicar: solo AUT-CANCELAR (normal si ya está registrado) y `verificar-lapidas` | Solo tras la lápida (caso 2) |
  | `publicado` / `conciliado` con 1 hoja y adjunto correcto | No se cancela; seguir | Sí (caso 3) |
  | `ambiguo`, `agotado`, `publicando`, 0 o 2 hojas, 2 trabajos | Decisión humana | No |
  | **Enviado pero invisible** (`ids` no lo ve tras 3 min) | Mantener la pausa, volver a consultar | **No** (ver §6) |

## 5. Evidencias
Carpeta `ejecucion_c1\evidencia_t92\` (la crea la primera orden que escribe): `config.json.original`, `pausa.json`, `ids.json`, `operador.json`, `verificacion.json`. La del intento anterior (`evidencia_t91\`) se conserva intacta y no se reutiliza.
No es evidencia de repositorio (contiene copias de configuración): no debe versionarse. Añadir las salidas, la hora del envío y el SHA del adjunto descargado.

## 6. Limitación residual: «enviado pero invisible»
Si el correo se envía y Gmail no lo muestra, no hay ids y por tanto **no hay lápida posible**, y la regla de oro impide restaurar.
**En ese caso el piloto no puede garantizar una recuperación segura dentro de los 15 minutos**: queda BLOQUEADO hasta que aparezca (entonces se sigue 7a/7b o 7c) o una persona decida, con una autorización nueva.
Mitigación previa al envío: comprobar que `c1 ids` responde (devuelve `ninguno`, no `inaccesible`) y que la cuenta de destino recibe correo con normalidad.
Si se restaurara pese a todo, el coordinador publicaría el ficticio solo (Work crearía un borrador interno; no se envía nada a ningún cliente).

## 7. Otros riesgos aceptados
1. La tarea de Work de producción tomará la hoja como cualquier otra; lo que hace Work no es verificable desde el computador.
2. Que la tarea esté ligada a esta raíz solo puede confirmarlo el operador en Work.
3. Los reales elegibles que lleguen durante la pausa se publicarán de uno en uno tras restaurar.
4. El ficticio entra en la base operativa (no hay `ANKA_DATA_DIR` aislado con servicios vivos).
