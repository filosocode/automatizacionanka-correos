# API del dashboard y Swagger

Requisito: **T-13**. El dashboard es una aplicación local con páginas HTML y acciones por formulario; esta página
describe cómo se documenta y se prueba cada petición con **Swagger UI**.

## Dónde está
| Qué | Ruta | Para qué |
|---|---|---|
| Swagger UI | `GET /docs` | Probar cada petición desde el navegador («Try it out»). |
| Especificación OpenAPI 3 | `GET /openapi.json` | La misma documentación en JSON, para importarla en Postman, Insomnia, etc. |

Solo se atienden en local (`127.0.0.1`, `localhost`, `[::1]`), igual que el resto del dashboard.

## Qué documenta
Todas las rutas que atiende el servidor, agrupadas en etiquetas:

| Etiqueta | Contenido |
|---|---|
| **Lectura (JSON)** | `GET /api/salud`, `GET /api/casos`, `GET /api/casos/{thread_id}`, `GET /state`: devuelven JSON, no cambian nada. |
| **Páginas** | Las pantallas HTML (`/`, `/requests`, `/case`, `/acta`, `/discarded`, `/archive`) y la descarga `/export.xlsx`. |
| **Acciones del caso** | Los formularios `POST` que actúan sobre un caso (tabla, respuesta, código T, cambios, vínculos…). |
| **Acciones generales** | `POST` que no son de un caso: modo de operación, revisar Gmail, rescatar, eliminar, novedades. |

Cada operación dice qué recibe (campos con tipo, obligatoriedad y ejemplo), qué responde y **qué efecto tiene**:
`lectura`, `guarda en la base`, `envía un correo real al cliente`, `crea carpetas en Drive` o `elimina`. Las que
tienen efecto fuera del dashboard llevan «⚠» en el resumen.

## Salud de las conexiones (T-14)
`GET /api/salud` contesta «¿conecta todo?»: **200** si ninguna comprobación falla y **503** si alguna sí, con el
detalle de cada una:

| Componente | Qué mira |
|---|---|
| `base_de_datos` | La base local abre y tiene el esquema esperado. |
| `configuracion` | `config.json` existe y es válido; si no, no se mira lo demás. |
| `puente_work` | En modo ChatGPT Work, que el puente haya revisado Gmail hace poco. Fuera de ese modo: `no_aplica`. |
| `autorizacion_google` | Que haya autorización guardada (con `profundo=1`, que siga vigente). |
| `gmail`, `drive` | Solo con `profundo=1`: una consulta real de solo lectura. |
| `motor_de_analisis` | Ollama responde y tiene el modelo (modo local), o hay clave de OpenAI (modo api). En Work: `no_aplica`. |

**Work no se consulta directamente**: ChatGPT Work no expone una API a la que Anka llame. La conexión es indirecta
(Work deja borradores en Gmail y el puente los lee), por eso se mide con la última revisión del puente y, a fondo,
con Gmail.

## Hoja de Google Sheets del caso (T-17)
`POST /hoja` crea la hoja del caso en la carpeta del proyecto o la actualiza ahora; después se actualiza sola cada vez
que cambia la tabla. El libro tiene cuatro hojas: **Actividades** (la tabla vigente), **Solicitud inicial** (el primer
correo del cliente y la primera tabla), **Historial** (cada versión: fecha, origen y qué cambió) y **Léeme**.
- Requiere que el caso tenga carpeta de proyecto (se crea al emitir la T); antes, solo el Excel descargable.
- Siempre se reescribe **el mismo archivo**: Google conserva sus versiones anteriores.
- Si alguien la editó directamente en Drive, se avisa y esos cambios quedan en el historial de versiones de Google.
- Se puede apagar la actualización automática con la variable de entorno `ANKA_HOJA_AUTOMATICA=0`.

## Reautorizar Google (T-15)
La autorización de Google (Gmail y Drive) puede vencer (si el proyecto de Google Cloud está en modo «Testing»,
cada 7 días) o ser revocada. Sin ella el puente de Work no lee Gmail y el dashboard no recibe correos.

| Qué | Dónde |
|---|---|
| Ver el estado | «Cuenta de Google» en Ajustes y, si hace falta, el aviso «Google desconectado» en el encabezado; `GET /api/autorizacion` (`ok`, `vencida`, `en_curso`, `fallida`, `desconocido`); campo `google` de `GET /state`; componente `autorizacion_google` de `GET /api/salud` |
| Reautorizar | Botón «Reautorizar Google» **siempre disponible en Ajustes** (junto al estado de la cuenta); además aparece en el encabezado, junto al aviso, cuando hace falta; o `POST /google/reautorizar` |
| Desde la terminal | `python app.py auth` |

Cómo funciona:
- El consentimiento (iniciar sesión y aceptar permisos) lo hace una persona en el **navegador de este equipo**; el
  dashboard solo lo lanza en segundo plano y responde enseguida. Por eso hay que estar frente al computador donde corre.
- La espera tiene tope de 5 minutos, no se abre una segunda ventana mientras hay una en curso, y si se cierra la
  ventana o vence la espera queda como `fallida` con un mensaje claro (sin el detalle técnico de Google).
- El token anterior no se toca hasta que la autorización nueva termina bien.
- `credentials()` **nunca** abre el navegador: sin nadie delante lanza `AutorizacionDeGoogle` con la instrucción.
  Solo `authorize_interactive()` (botón o `python app.py auth`) abre el consentimiento.
- La verificación se guarda 5 minutos (1 si falló) y no congela la pantalla: sin internet el estado es
  `desconocido` y no se ofrece reautorizar por eso.
- El puente de Work se reconecta solo en su siguiente ciclo (hasta 30 segundos, T-30) después de reautorizar.

## Cómo se prueba una acción (POST)
1. Abre `http://127.0.0.1:8765/docs`.
2. Elige la operación y pulsa **Try it out**. El campo `csrf_token` ya viene relleno con el token vigente: lo pone la
   página `/docs`, no el JSON. Fuera de Swagger (Postman, `curl`), copia el valor del campo oculto `csrf_token` de
   cualquier formulario del dashboard; `/openapi.json` no lo incluye (D-71).
3. Completa los campos (p. ej. `thread_id` de un caso de prueba) y pulsa **Execute**.
4. La respuesta real es `303` con `Location` hacia la pantalla del caso con un mensaje; el navegador sigue la
   redirección, así que Swagger muestra la página resultante (estado `200`). El mensaje también queda en la URL.

Las acciones con «⚠» actúan de verdad (envían correo, crean carpetas, eliminan): úsalas con casos de prueba.

## Reglas que no cambian
- Las peticiones `POST` exigen `Host` local, `Origin` igual al del servidor y el `csrf_token` (T-07). Swagger UI
  se sirve desde el mismo origen, así que las cumple sin configuración.
- `/openapi.json` **no lleva el token**: se puede copiar o importar en otra herramienta sin llevarse el secreto
  (D-71). `/docs` lo toma de su propia página y su único script en línea va autorizado por un `nonce` de la CSP.
- Un `POST` de más de **2 MiB** responde `413`; un `Content-Length` no numérico o negativo, un cuerpo que no es UTF-8
  o con más de 10.000 campos, `400`.
- Toda respuesta (páginas, JSON, `303`, `/eventos`; la excepción de `Cache-Control` son los archivos de `/static`, abajo) lleva `X-Content-Type-Options: nosniff`,
  `Referrer-Policy: same-origin`, `Cache-Control: no-store`, `X-Frame-Options: DENY` y una CSP: en las páginas
  `default-src 'self'` con scripts y estilos en línea (las plantillas los usan; no hay manejadores de eventos en
  línea), en `/docs` además el CDN de Swagger UI y el `nonce`.
- `/static/{archivo}` (U-48) es la única excepción a `no-store`: lleva `ETag` y responde `304` si llega `If-None-Match`; el CSS
  compartido se llama `style.<huella>.css` (huella del contenido) y es `public, max-age=31536000, immutable`; la fuente y el
  logo, `public, max-age=86400`. El resto de nombres es `404`.
- Si el cliente manda `Accept-Encoding: gzip`, el HTML, el JSON y el CSS de 1 KB o más salen comprimidos (`Content-Encoding: gzip`,
  `Vary: Accept-Encoding`). El JSON de la API es compacto (sin sangrías). El servidor habla HTTP/1.1.
- Un `500` muestra «Ocurrió un error inesperado» con un **código de error** de 8 caracteres; el detalle está en el
  registro junto a ese código. Un `400` muestra el motivo solo si es un mensaje de Anka para la persona.
- La documentación **se genera de las rutas reales** (`GET_ROUTES` y `POST_ROUTES`): una prueba falla si hay una
  ruta sin documentar o documentada que ya no existe.
- La única dependencia externa es Swagger UI (`swagger-ui-dist`, versión fijada y con huella de integridad SRI),
  que el navegador carga desde un CDN al abrir `/docs`. Los datos de los casos no salen de la máquina.

## Cómo agregar o cambiar una ruta
1. Añade la función: una página o lectura (GET) en `server.py` y `GET_ROUTES`; una acción (POST) en `acciones.py` y
   `POST_ROUTES`.
2. Descríbela en `anka/web/openapi.py` (resumen, campos, efecto, etiqueta).
3. Corre `check.bat`: la prueba de cobertura de la documentación te avisa si falta algo.
