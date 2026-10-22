# Pruebas integradas C2 y C3 (T-98) — PREPARADAS, NO EJECUTADAS

Mismo flujo validado en C1 (T-94/T-95): Gmail → Python → Drive → Work → borrador → importación → conciliación.
Un caso por vez, **cada uno con su propia ventana de pausa (tope 15 min)**. No se juntan C2 y C3 en una sola pausa:
`evaluar` y `restaurar` razonan sobre un solo mensaje y una ventana común obligaría a relajar la regla de oro.
Orden recomendado: **C3 primero** (rápido, sin revisión visual), después C2 (necesita revisión humana).

## 1. Identidad de cada caso (`c1.py --perfil C2|C3`; sin `--perfil` es C1, sin cambios)

| | C1 (T-92, ya ejecutado) | C2 | C3 |
|---|---|---|---|
| Marcador | `[PI-C1-T92-001]` | `[PI-C2-T98-001]` | `[PI-C3-T98-001]` |
| Asunto exacto | ver `c1.PERFILES["C1"]` | `[PI-C2-T98-001] Solicitud ficticia de laboratorio - levantamiento batimétrico (SIN COTIZACIÓN REAL)` | `[PI-C3-T98-001] Solicitud ficticia de laboratorio - proyecto vial, resumen de cantidades (SIN COTIZACIÓN REAL)` |
| Adjunto original | `solicitud_01.pdf` | `tdr_02.pdf` (PDF escaneado) | `cantidades_03.xlsx` (Excel) |
| Bytes | 4913 | **492 471** | **9 087** |
| SHA-256 | `f47ed4da…d844` | `0287414f91bb1c9b3ecfd05e98de713adaa81177261885f89e4fe8f23c66d732` | `12c52e788feb76d4c7879726d7402cb0c51f8178101e5a7a9e1bfb10cb7c0c6a` |
| Carpeta de evidencia | `evidencia_t92` | `evidencia_c2_t98` | `evidencia_c3_t98` |
| Remitente → destino | `91andresmunoz@gmail.com` → `ankacomercialia@gmail.com` (igual para los tres) | | |

Los SHA-256 y los tamaños se comprueban contra los archivos de `documentos/`, el manifiesto y las referencias v2 en `test_perfiles.py`.
Los archivos se adjuntan **desde `laboratorio/banco_documental/documentos/`**, sin abrirlos ni guardarlos de nuevo (Excel puede regrabar el libro y cambiar el SHA).

### Criterios de identificación (los aplica `ids`/`localizar`, en solo lectura)
Un único mensaje con el marcador en el asunto (`in:anywhere`); asunto exacto; remitente y destinatario exactos, sin Cc; un solo mensaje en el hilo;
**un solo adjunto** con el nombre, los bytes declarados y descargados y el SHA-256 de la tabla. Un adjunto de otro caso, con el mismo nombre y otro contenido o
sin contenido accesible se rechaza (no se publica; solo AUT-CANCELAR). En SQLite: asunto igual, adjunto registrado `(nombre, bytes)` igual, sin trabajo previo y elegible.

### Evidencias independientes (no se confía en una sola fuente)
1. Gmail (API, solo lectura): ids, cabeceras, adjunto descargado y su SHA-256.
2. SQLite (solo lectura): registro, adjunto registrado, trabajo y su estado, eventos, `integrity_check`.
3. Drive (solo lectura): una única hoja con `anka_job_id` del trabajo; PENDIENTES/CERRADOS antes y después.
4. Borrador de Work: guardado tal cual (SHA-256), evaluado por el evaluador inverso, **no** por lo que Work diga de sí mismo.
5. `config.json`: SHA-256 antes y después (debe coincidir tras restaurar).
6. Carpeta Enviados/etiqueta `SENT` sin mensajes nuevos.

## 2. Textos exactos de los correos ficticios
Están en `correos_t98.py` (`CUERPO_C2`, `CUERPO_C3`; las pruebas verifican que piden la tabla, las preguntas al cliente y «como si fuera real»,
que aclaran que no se envía nada y que **no adelantan** cifras ni faltantes del documento).

**Asunto C2:** `[PI-C2-T98-001] Solicitud ficticia de laboratorio - levantamiento batimétrico (SIN COTIZACIÓN REAL)`
**Asunto C3:** `[PI-C3-T98-001] Solicitud ficticia de laboratorio - proyecto vial, resumen de cantidades (SIN COTIZACIÓN REAL)`

```
(C2)
Buen día.

Les escribo desde la Empresa de Servicios Ejemplo E.S.P. (ficticia) para solicitar cotización de los trabajos descritos en los términos de referencia adjuntos (tdr_02.pdf). El documento es un escaneado.

Por favor traten esta solicitud como si fuera real y hagan la interpretación comercial completa: la tabla de actividades con sus cantidades y unidades tal como están escritas, las restricciones del documento, los datos que falten o se contradigan para poder cotizar y las preguntas que habría que hacerle al cliente.

Es una prueba de laboratorio: no se debe enviar ninguna respuesta al cliente ni cotizar. Solo se espera el borrador de análisis para revisión interna.

Gracias.
Prueba de laboratorio Anka (ficticia)
```
```
(C3)
Buen día.

Les escribo para solicitar cotización del proyecto vial ficticio cuyo resumen de cantidades va en el libro adjunto (cantidades_03.xlsx).

Por favor traten esta solicitud como si fuera real y hagan la interpretación comercial completa: la tabla de actividades con sus cantidades y unidades tal como están en el libro, las restricciones, los datos que falten o no cuadren para poder cotizar y las preguntas que habría que hacerle al cliente.

Es una prueba de laboratorio: no se debe enviar ninguna respuesta al cliente ni cotizar. Solo se espera el borrador de análisis para revisión interna.

Gracias.
Prueba de laboratorio Anka (ficticia)
```

## 3. Procedimiento supervisado (por caso; sustituir `X` por `C3` o `C2`)
Todas las órdenes: `.venv\Scripts\python.exe -I laboratorio\banco_documental\ejecucion_c1\c1.py <orden> --perfil X [--confirmar]`.

| # | Paso | Autorización | Notas |
|---|---|---|---|
| 0 | `verificar_post_t83.ps1` en verde; PENDIENTES vacía; sin `publicando`; `c1 estado` sin pausa; SHA-256 de `config.json` anotado | — | si algo falla, no seguir |
| 1 | Redactar en Gmail el correo (asunto y cuerpo de §2, adjunto desde `documentos/`), **sin enviar** | — | verificar nombre, tamaño y destinatario en pantalla |
| 2 | `pausar --perfil X --confirmar` (inicia el reloj de 15 min) | AUT-CONFIG-1 | rechaza si hay ciclo reciente, trabajos sin conciliar o pausa previa |
| 3 | `vigilar --perfil X` en una segunda consola | — | solo restaura al vencer si se cumple 1, 2 o 3 y hay `--confirmar` |
| 4 | El operador envía y declara: `confirmar-envio enviado --operador NOMBRE` | AUT-ENVIO | si no se envía: `no_enviado` |
| 5 | `ids --perfil X` | — | adjunto verificado (nombre, bytes, SHA-256) |
| 6 | Esperar el registro del receptor (≤ 60 s); `localizar --perfil X` | — | habilita `comandos publicar` durante 5 min |
| 7 | `comandos publicar --perfil X` e **imprimir sin ejecutar**; ejecutar el que aparece (`work_jobs despachar … --confirmar-drive`) | AUT-PUBLICACION | alternativa excluyente: `comandos cancelar` + AUT-CANCELAR |
| 8 | `restaurar --perfil X --confirmar` apenas haya una hoja verificada (no hace falta esperar a Work) | AUT-RESTAURAR | exige 1, 2 o 3 de la regla de oro; luego `c1 estado` y SHA-256 de `config.json` = el del paso 0 |
| 9 | Ejecutar Work (el operador, con el `job_id` de la hoja); el borrador se importa solo por el puente | — | no importar a mano |
| 10 | Tras la conciliación y el archivado (≤ 2 ciclos de 5 min): `verificar_post_t83.ps1`, evaluación del retorno y, en C2, revisión humana | — | |

**Si falla algo antes del paso 7:** AUT-CANCELAR (`--preventiva` si el mensaje aún no está registrado) y restaurar. **No pausar** si el preflight falla.
**Vigilante:** durante la pausa el vigilante sigue activo; no se toca. Un reinicio de ANKA con la pausa abierta restauraría la coordinación solo con `config.json`
tal como esté: por eso la pausa se mantiene mínima y se restaura en cuanto hay hoja.

## 4. Criterios de aceptación documental (de `PLAN_PRUEBA_INTEGRADA.md` §2–§3 y referencias v2)
**Comunes (E5–E8):** un solo borrador `[ANKA_DASHBOARD_JSON_V1]` con ese `message_id`, ningún envío, ninguna escritura de Work en Drive; el adjunto aparece en `attachments`
con estado de lectura coherente; el JSON pasa el validador y el contrato `PRECISO_V6.1.3` (sin modificarlo); 0 errores críticos del comparador v2; valor y unidad literales
de cada actividad con `fuente` existente en el documento; la segunda importación no cambia nada; `integrity_check=ok`.
**Preguntas al cliente:** `preguntas_cliente` no vacía (se deriva de las acciones `ask_client`) y cada faltante de la tabla se reconoce ahí o en el texto correspondiente.

| | C2 · `tdr_02.pdf` | C3 · `cantidades_03.xlsx` |
|---|---|---|
| Actividades exigidas | batimetría 45,0 ha · 3 perfiles · 50 m · topografía 6,8 km | topografía 33,7 ha · 3,625 km · geodesia 24 puntos · 3 perfiles |
| Contradicción crítica | **C2-K1**: 45,0 tachada, manuscrito 41,8, croquis excluye 3,2 ha | **C3-K1**: 7 predios (Resumen) frente a 6 lotes (Detalle) · C3-K2 (hoja oculta) |
| Faltantes | **C2-F1 ubicación (bloqueante: Anexo 3 no viene)**, C2-F2 plazo, C2-F3 área final | C3-F1 cantidad de lotes (no bloqueante) |
| Trampas (no deben tomarse como cantidad) | 6,8 m de rampa · 3,2 ha excluida · sello 0457 · profundidad 14 m | 337 000 m² · 31,2 de la hoja oculta · 4 puntos por lote |
| Restricciones | permiso de navegación del contratista · ronda 25 m · calado 0,6 m | equidistancia 0,5 m · precisión 0,02 m |
| Revisiones internas | C2-I1 ecosonda (juicio) · C2-I2 nivel de referencia | C3-I1 hoja oculta no vigente |
| Especial | OCR en ambas páginas; revisión visual **humana** (§5) | ubicación `esperanza`/`mirador` (vereda); fórmulas con valor guardado |

## 5. Revisión humana de C2 (otra persona distinta de quien preparó el banco)
La tabla de abajo es la plantilla: se copia a `evidencia_c2_t98/revision_visual_humana.md` y se rellena (una sola pasada, con el PDF original `tdr_02.pdf` abierto, **antes** de ver el borrador de Work):

| Id | Página | ¿Qué debe ver la persona? | ¿Coincide el borrador? (sí/no/parcial) | Nota |
|---|---|---|---|---|
| C2-V1 | 1 | cifra 45,0 tachada y anotación a mano 41,8: el área impresa fue corregida; hay que preguntar cuál rige | | |
| C2-V2 | 2 | Figura 1: zona rayada «excluida del levantamiento» (3,2 ha); el área neta 41,8 es derivada, no impresa | | |
| C2-V3 | 2 | flecha norte y barra 0-250-500 m (el título dice «sin escala»: incoherencia opcional C2-K2); sin cantidad contratable | | |

Aprobado si V1 y V2 coinciden y no hay errores críticos humanos; V3 no genera cantidad. Ver también la lectura de Python: el OCR del lector local es **parcial**
(recoge «41,8 ha» pero declara «cobertura visual parcial»); lo que se evalúa es la lectura de Work, no la de Python.

## 6. Pruebas y resultados (T-98)
`test_perfiles.py` (13 pruebas): C1 intacto; adjuntos/SHA/bytes de C2 y C3 contra los archivos reales, el manifiesto y las referencias v2; marcadores únicos; cada perfil acepta su
correo y rechaza el adjunto de otro caso o con contenido alterado; el correo de un caso no se confunde con la consulta de otro; la consola usa `--perfil` y una carpeta de evidencia propia;
los cuerpos no adelantan la respuesta. Junto con `test_c1.py` (75) y `test_vigilante.py` (19): 107 en verde (13 + 75 + 19); `ruff` limpio.

## 7. Riesgos operativos concretos
1. **Tiempo del OCR de C2:** el lector tarda ~8 s por el PDF de 492 KB; el ciclo de despacho y la ventana de 15 min lo toleran, pero C2 es el caso más lento. Si `despachar` tarda más de lo previsto, restaurar con la hoja ya verificada; no prolongar la pausa.
2. **El preparador de OCR en Drive está apagado** (`work_drive_enabled=false`): en C2 Work recibe el texto de Python (cobertura visual parcial) y el original. Que Work lea la imagen del original es justo lo que se evalúa; si declara «no pude abrir el adjunto», es un hallazgo, no un fallo del procedimiento.
3. **El comparador pone críticos falsos** si se evalúa solo `missing_data`: el evaluador inverso toma `preguntas_cliente` de `ask_client`. Un cuerpo que no pide preguntas dejó C1 con la lista vacía; los cuerpos nuevos sí las piden, pero no se garantiza que Work las emita.
4. **Dos pausas, dos riesgos:** cada ventana detiene la coordinación hasta 15 min; no se hace C2 y C3 el mismo día sin comprobar que `verificar_post_t83.ps1` volvió a verde entre ambas.
5. **Reinicio de ANKA durante la pausa** (vigilante tras una caída): la coordinación quedaría detenida hasta restaurar; `vigilar` y el tope de 15 min lo cubren, pero conviene no pausar si `vigilante estado` muestra alertas.
6. **Cambiar el libro de C3** (abrirlo y guardarlo en Excel) altera el SHA-256: adjuntar el original sin abrirlo.
7. **Entrega 23/10 10:00:** C3 + C2 + revisión humana suman ~1,5 h con operador y revisor disponibles; sin revisor distinto para C2, ese criterio queda sin cumplir.
