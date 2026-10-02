# Arquitectura

Requisito: **T-12**. Las reglas de esta página se verifican con `tests/test_arquitectura.py`; si una falla, el cambio
metió una dependencia que el diseño no permite, y se corrige moviendo el código, no relajando la regla.

## Capas
```
cli ─┐
web ─┴─▶ servicios ─▶ infra ─▶ dominio
```
Una capa solo importa de las que tiene **debajo**. `web` y `cli` no se importan entre sí.

| Capa (`anka/…`) | Qué contiene | Qué NO puede hacer |
|---|---|---|
| `dominio/` | Reglas del negocio y funciones puras: estados, tabla de actividades, recepción (qué falta y qué preguntar), contrato con Work, lectura de un mensaje de Gmail (`correo`), los objetos del caso (`Caso`, `Evento`, `Cambio`, `Vinculo`: dataclasses inmutables que también se leen como mapa) y la forma del análisis (`Analisis`, `Actividad`…: `TypedDict` que mypy verifica y que se guardan como JSON sin cambios), extracción de texto de adjuntos. | Hablar con Gmail, Drive, la base de datos ni el disco. |
| `infra/` | Todo lo que toca el mundo exterior: configuración (`settings`), base SQLite (`store`), clientes de Google (`google_services`, `mail`), modelos de lenguaje (`llm`), registro (`logging_setup`), copias de seguridad, modo de operación. | Conocer casos de uso ni pantallas. |
| `servicios/` | Los casos de uso: importar un borrador de Work (`work_import`), ver y decidir casos (`quotes`), editar la tabla (`ediciones`), enviar y borrar casos (`case_actions`), el ciclo de revisión (`pipeline`), control de cambios y código de la oferta, seguimiento de hilos y recordatorios, carpetas de Drive. | Conocer el servidor web ni los comandos. |
| `web/` | El servidor HTTP local y las acciones de los formularios (`server`), las páginas (`paginas`: resumen y listas; `pantalla_caso`: un caso y su acta), el token CSRF y la comprobación de Host/Origen (`seguridad`), los fragmentos (`views`) y las plantillas. | Importar de `cli`. |
| `cli/` | Los puntos de entrada: comandos (`app`), lanzador (`supervisor`) y puente con Work (`work_sync`). | — |

## Clases y funciones: cuándo cada una
Se usa una clase donde hay **estado y operaciones que viajan juntas**, y funciones donde no:
- `RepositorioDeCasos` (`servicios/repositorio.py`): sabe cómo se guardan y leen los casos; recibe cómo abrir la base.
  Las funciones de `quotes.py` (`get_quote`, `all_quotes`…) son una fachada sobre una instancia por defecto.
- `Caso` y los demás objetos del dominio llevan las reglas que dependen solo de sus campos (`cerrado`, `reabierto`,
  `admite_correo`, `informacion_validada`, `espera_al_cliente`), para que no se repitan en cada pantalla.
- `RevisorDeHilos` (`servicios/pipeline.py`): una pasada de revisión de Gmail; guarda el contexto (configuración,
  Gmail, Drive, base, modo) que antes viajaba como argumento por cada función.
- Las reglas puras (recepción, estados, correo) siguen siendo funciones: no tienen estado y así se prueban sin montar nada.

## Dónde poner código nuevo
1. ¿Es una regla o un cálculo que no necesita nada externo? → `dominio/`.
2. ¿Habla con Gmail, Drive, la base, el disco o la red? → `infra/`.
3. ¿Orquesta varias cosas para cumplir un caso de uso? → `servicios/`.
4. ¿Es lo que ve o hace la persona en el navegador? → `web/`.
5. ¿Es algo que se ejecuta desde la terminal o el lanzador? → `cli/`.

## Otras reglas
- **Una conexión por petición web**: `request_scope()` (`anka/infra/store.py`) hace que todas las consultas de una
  petición compartan una conexión; `close()` descarta lo no confirmado. Fuera de una petición cada `connect()` abre la suya.
- **Sin ciclos** de importación entre módulos.
- **Solo la línea de comandos termina el programa**: un servicio lanza `ErrorDeAnka` (`anka/dominio/errores.py`) con
  un mensaje para la persona; `anka/cli` lo convierte en salida con código 1 y el dashboard en un aviso.
- **Lo privado no se importa**: un nombre con `_` es de su módulo; si otro lo necesita, se hace público o se mueve.
- **Cada módulo empieza con un docstring** que dice para qué sirve; cada capa lo tiene en su `__init__.py`.
- **Toda función, clase y método público del paquete tiene docstring** (una frase que dice qué hace, no cómo) y
  **ninguna función pasa de complejidad 15**: ruff lo comprueba en `check.bat` y en el CI (`pyproject.toml`).
- **La raíz está ordenada**: solo los puntos de entrada (`app.py`, `supervisor.py`, `work_dashboard_sync.py`, cada uno de
  pocas líneas) y `check_spec.py`; el resto vive en `anka/`, `tests/`, `docs/`, `spec/`, `tools/` y `skills/`.

## Puntos de entrada
`app.py`, `supervisor.py` y `work_dashboard_sync.py` en la raíz son envoltorios de una línea que llaman a
`anka/cli/…`. Se conservan con su nombre porque los lanzadores `.bat`, la tarea programada de Windows y el lanzador
único los invocan así.

## Pruebas
Viven en `tests/` (con `tests/golden/` para las instantáneas de pantalla) y se ejecutan con
`python -m unittest discover -s tests -t .` (lo hace `check.bat`).
