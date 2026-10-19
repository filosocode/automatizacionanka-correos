# Paquete de referencia ANKA para la tarea Work de la cola (contrato V1 + T-52)

**Versión del paquete:** {{PAQUETE_VERSION}} · **Contrato:** ANKA_DASHBOARD_JSON_V1 (esquema versión {{ESQUEMA_VERSION}}) · **Prompt:** PRECISO_V6.1.2

Este paquete es **autónomo**: todo lo que una ejecución de Work necesita para devolver un borrador que Python acepte está
en esta carpeta de Drive. No depende de archivos ZIP, de rutas del equipo de Anka ni de otra carpeta. Es de **solo lectura**
para Work: no lo edites, no lo muevas, no lo borres.

## Qué hay aquí
| Archivo | Para qué |
|---|---|
| `manifest.json` | Versión, origen y huella SHA-256 (y tamaño) de cada archivo. Es lo primero que se lee. |
| `contrato_work.schema.json` | El esquema oficial del bloque `[ANKA_DASHBOARD_JSON_V1]` (copia exacta del repositorio). |
| `PRECISO_V6.1.2.txt` | El prompt operativo de ANKA 01 (copia exacta). Manda sobre el formato de salida, salvo lo que diga `T-52_CLASIFICACION.md`. |
| `T-52_CLASIFICACION.md` | Las cuatro clasificaciones comerciales y los campos que se añaden al contrato. |
| `FORMATO_DE_RETORNO.md` | Cómo debe quedar el borrador y el bloque JSON. |
| `INSTRUCCIONES_UNA_EJECUCION.md` | Qué hace la tarea programada en cada ejecución y cuándo debe detenerse. |
| `ejemplo_*.json` | Cinco ejemplos ficticios válidos: uno por clasificación y uno de SOLICITUD_NUEVA en revisión interna que conserva su tabla preliminar. Los ids son inventados: usa siempre los de la hoja del trabajo. |

## Orden de lectura
1. `manifest.json` y la verificación de `INSTRUCCIONES_UNA_EJECUCION.md` (sección «Antes de empezar»).
2. `INSTRUCCIONES_UNA_EJECUCION.md`, luego `T-52_CLASIFICACION.md` y `FORMATO_DE_RETORNO.md`.
3. `PRECISO_V6.1.2.txt` para las reglas de lectura, tabla y preguntas, y el esquema para los tipos exactos.

## Si algo no cuadra
Si falta un archivo, un tamaño o una huella no coincide, la versión es distinta de la de la hoja del trabajo, o dos archivos se
contradicen: **detente y no crees borrador**. Informa qué comprobación falló. Quien mantiene el paquete lo vuelve a publicar.
