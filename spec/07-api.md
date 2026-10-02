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

## Cómo se prueba una acción (POST)
1. Abre `http://127.0.0.1:8765/docs`.
2. Elige la operación y pulsa **Try it out**. El campo `csrf_token` ya viene relleno con el token vigente.
3. Completa los campos (p. ej. `thread_id` de un caso de prueba) y pulsa **Execute**.
4. La respuesta real es `303` con `Location` hacia la pantalla del caso con un mensaje; el navegador sigue la
   redirección, así que Swagger muestra la página resultante (estado `200`). El mensaje también queda en la URL.

Las acciones con «⚠» actúan de verdad (envían correo, crean carpetas, eliminan): úsalas con casos de prueba.

## Reglas que no cambian
- Las peticiones `POST` exigen `Host` local, `Origin` igual al del servidor y el `csrf_token` (T-07). Swagger UI
  se sirve desde el mismo origen, así que las cumple sin configuración.
- La documentación **se genera de las rutas reales** (`GET_ROUTES` y `POST_ROUTES`): una prueba falla si hay una
  ruta sin documentar o documentada que ya no existe.
- La única dependencia externa es Swagger UI (`swagger-ui-dist`, versión fijada y con huella de integridad SRI),
  que el navegador carga desde un CDN al abrir `/docs`. Los datos de los casos no salen de la máquina.

## Cómo agregar o cambiar una ruta
1. Añade la función en `server.py` y regístrala en `GET_ROUTES` o `POST_ROUTES`.
2. Descríbela en `anka/web/openapi.py` (resumen, campos, efecto, etiqueta).
3. Corre `check.bat`: la prueba de cobertura de la documentación te avisa si falta algo.
