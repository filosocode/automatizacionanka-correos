# Recuperación segura de la prueba integrada C1 (T-80) — SIMULACIÓN, NO EJECUTADO

No autoriza nada. Solo se leyó código y se simuló sobre SQLite temporal, Gmail y Drive simulados (`tests/test_recuperacion_t80.py`, 18 pruebas). No se tocó producción,
configuración, Gmail, Drive, SQLite operativo, procesos ni tareas. El protocolo actualizado está en `PLAN_PRUEBA_INTEGRADA.md` §8.

## 1. Hallazgo central
**El código actual no puede descartar nada.** No hay orden que cancele un trabajo ni que excluya un mensaje: el estado `cancelado` existe (el consumidor lo respeta: no se publica, no se importa, y
`mensajes_nuevos` ignora cualquier mensaje que ya tenga trabajo) pero **nada lo escribe**. Y un trabajo inexistente tampoco se puede «descartar»: un mensaje pendiente, de remitente humano y posterior al corte
se publica solo al restaurar. La única salida con los controles actuales es **continuar hacia adelante**: darle trabajo y publicarlo (`despachar` de ese mensaje), o, de forma excepcional, adelantar el corte.

## 2. Matriz de recuperación por etapa (restaurar sin hacer nada → qué pasa → recuperación)

| Etapa de la cancelación | Si solo se restaura la coordinación | Recuperación con los controles actuales | ¿Humano? |
|---|---|---|---|
| E0 Antes de recibir el correo | No hay residuo; sigue lo normal | Restaurar | No |
| E0b Enviado pero aún no registrado por el receptor | Se registraría y se publicaría solo | Mantener la pausa hasta que se registre y darle trabajo; si Gmail falla, **sin control** | **Sí** |
| E1 Registrado, sin trabajo | **Se publica solo** (prueba e1) | Continuar hacia adelante (`despachar` del mensaje) y restaurar; o adelantar el corte (excepcional) | **Sí** (decide) |
| E2 Trabajo creado, sin publicar | Se publica primero, antes que los reales; adelantar el corte **no** lo evita | Dejar que se publique; no hay cancelación | Sí (aceptar) |
| E3 Publicado, Work sin responder | Sigue `publicado`; sin duplicados | Restaurar. Borrar la hoja **no** cancela: Python la recrea | No |
| E4 Work devolvió un borrador | Se importa y concilia | Restaurar. Si hay dos borradores → `ambiguo` y a los 5 intentos pide persona | No (salvo ambiguo) |
| E5 Llegó un real durante la pausa | Se procesa después, uno por ciclo, por orden de detección | Restaurar | No |
| E6 Restauración repetida | Sin duplicados de trabajos, hojas ni eventos | — | No |
| E7a Drive falla al publicar | Pendiente y se reintenta; al quinto fallo queda **agotado**: ni el ciclo ni `despachar` lo toman | Resolver Drive y decidir con una persona | **Sí** |
| E7b Gmail falla al importar | Fallos con retroceso; al quinto, pide persona | No afecta a la publicación | Sí (al quinto) |

## 3. Opciones de recuperación (Fase 2)

- **A. Restauración con los comandos actuales.** Viable sin cambiar producción, pero no hay descarte real: «cancelar» = continuar hacia adelante. Costo: Work recibe un trabajo ficticio y puede tardar en
  atender los reales (el orden de Work no se controla). Variante A2: adelantar `work_coordination_since` un segundo tras la detección del ficticio; solo funciona **sin trabajo creado**, excluye también a los reales
  anteriores (siguen pendientes; se despachan a mano porque el despacho manual no mira el corte) y exige editar la configuración operativa de nuevo.
- **B. Exclusión controlada del ficticio.** Demostrado que el consumidor ya la respeta (prueba b: un trabajo `cancelado` no se publica, no se importa y no estorba a los reales), pero **falta el productor**: una orden
  `work_jobs cancelar` es un cambio de producción (nuevo requisito + pruebas + decisión en `spec/05`). Es la solución limpia; **no se implementa aquí**. Hacerla con SQL directo está descartado.
- **C. Entorno separado.** Sin cambios respecto a T-79: la cuenta del buzón es una constante del código (`CUENTA`) y Work solo lee el buzón y la carpeta productivos; no verificable.

**Recomendación:** A (continuar hacia adelante) con el protocolo del plan §8; proponer B como mejora futura. Se prefirió A porque no modifica producción.

## 4. Resultados de la simulación (18 pruebas, todas con SQLite temporal)
Etapas e0 a e7, exclusión B y constante de cuenta C. Resultados clave: e1 el ficticio se publica solo al restaurar; no existe orden de descarte; continuar hacia adelante conserva a los 3 reales (4 trabajos, 4 hojas, sin duplicados);
adelantar el corte deja al ficticio y a los 2 reales anteriores pendientes y publica al posterior; con trabajo ya creado el corte no ayuda; e2 el pendiente pasa primero; e3 la hoja borrada se recrea (una hoja viva, nueva);
e4 conciliación y dos borradores → persona; e5 orden de llegada uno por ciclo; e6 sin duplicados en 5 ciclos extra; e7 cinco fallos de Drive agotan el trabajo y el despacho manual responde `agotado`; cinco fallos de Gmail piden persona;
ningún comando deja un trabajo en `cancelado`.

**Recuperables sin intervención:** E0, E3, E4 (un borrador), E5, E6. **Requieren persona:** E0b, E1, E2 (aceptar), E4 con ambiguo, E7a, E7b al quinto fallo.

## 5. Riesgos residuales
1. No hay descarte real: el ficticio siempre acaba como trabajo publicado (consume la atención de Work y deja un borrador interno ficticio, nunca enviado: R8).
2. Gmail caído entre el envío y el registro: el correo existe y no se puede proteger; restaurar lo publicaría solo. Depende de A1 de una persona y de no restaurar.
3. La pausa detiene a los reales hasta el paso 8 (≤ 30 min objetivo): vaciado posterior N × 300 s.
4. Adelantar el corte (A2) es una segunda edición operativa y deja reales «huérfanos» que alguien debe despachar.
5. Work puede atender primero el ficticio que algún real: no verificable (tarea Work NO_VERIFICABLE).
6. Instantáneas y registro de recuperación son manuales: error humano posible.
7. `anka.log` sin actividad desde el 20/10 (T-78) sigue sin explicar: afecta a la confianza en la evidencia.

## 6. Protocolo actualizado
En `PLAN_PRUEBA_INTEGRADA.md` §8: pausa solo hasta publicar el ficticio (objetivo 30 min, tope propuesto 60), «continuar hacia adelante» como única cancelación, 6 reglas de restauración, tratamiento del ficticio pendiente,
continuidad de reales, evidencias y paradas, fallos de Gmail/Drive, y A10 y A11 adicionales. La restauración puede hacerse en cuanto el trabajo del ficticio esté publicado, sin esperar a Work.

## 7. Condiciones para restaurar sin interferencias
Las seis del plan §8.2: trabajo del ficticio publicado o conciliado y ninguno pendiente o agotado; ninguna fila ficticia pendiente sin trabajo; candado libre y config igual a la copia salvo la clave; PENDIENTES con
solo lo esperado; conjunto N de reales anotado; una sola restauración sin tocar `work_coordination_since`.

## 8. Dictamen
**RECUPERACION_CON_BLOQUEOS.** La mayoría de los escenarios se recupera sin cambiar producción, pero hay dos bloqueos que no se pueden resolver con el código actual: (1) no existe descarte, de modo que la cancelación después de recibir el
correo solo puede resolverse completando el ficticio; (2) si Gmail falla entre el envío y el registro no hay control posible. Se levantarían con la orden de cancelación de la opción B (cambio de producción separado) y con una decisión humana sobre el riesgo (2).

## 9. Primera acción ejecutable recomendada (no realizada)
Sin escribir en ningún sistema: que una persona de Anka apruebe por escrito la política «cancelar = continuar hacia adelante», el tope de 60 min y quién restaura; después, tomar la instantánea base de solo lectura (paso 0 del plan).
En paralelo, decidir si se abre una tarea aparte para la orden `cancelar` (opción B).
