# Investigación de mejoras (9 de octubre de 2026)

Resumen de una revisión del proyecto hecha con seis agentes de investigación (adjuntos y OCR, LLM, Gmail, seguridad,
arquitectura y dominio topográfico), más una revisión del código («Ola 0»). Es un documento para leer después: **no es
una especificación** y ningún punto está aprobado. Para convertir algo en trabajo, sigue `docs/ESTANDAR_CAMBIOS.md`
(requisito en `spec/` → código → skill de Work → pruebas).

## 1. Objetivo que guía la revisión

Los siete pasos de la Fase 1: leer correos, identificar solicitudes de cotización, resumirlas en una tabla de
actividades, detectar datos faltantes, pedirlos al cliente, esperar y cerrar la tabla, y crear la carpeta en Drive.
Criterio añadido: que todo funcione de forma correcta y organizada, con código sostenible y duradero.

Grupo Anka (https://anka.com.co/) ofrece LiDAR, fotogrametría, termografía, inspección aérea, batimetría, georradar,
topografía convencional, catastro multipropósito y escáner terrestre, para energía, hidrocarburos, infraestructura y
ambiente. Su web no describe un proceso formal de cotización: ofrece un formulario de contacto y WhatsApp.

## 2. Estado encontrado (Ola 0, solo lectura)

Línea base: 1048 pruebas OK (2 omitidas) y `check_spec` con 78 requisitos, todos con prueba. No se corrió ruff, mypy ni
la cobertura. No se modificó ningún archivo del código.

- **Catálogo de servicios, parcial y duplicado.**
  - `anka/dominio/reception.py:140-147` reconoce LiDAR, Batimetría, Fotogrametría, Termografía, Georradar, Escáner
    terrestre, Topografía y Catastro.
  - Escáner terrestre y Catastro no tienen lista propia en `_TECHNICAL_CHECKS` (`reception.py:163-174`).
  - Solo Batimetría, Termografía y Topografía tienen pregunta específica de magnitud.
  - La inspección aérea se mezcla con Termografía por la palabra genérica «inspeccion».
  - `anka/servicios/commercial_index.py:40-48` guarda otro catálogo con nombres distintos. Pueden divergir.
  - Posible error por el orden de las reglas: «inspección de túnel con escáner terrestre» se clasificaría como
    Termografía. Es una deducción, no se ejecutó.
- **Drive (paso 7).** La estructura `Año/Cliente/Solicitud` se cumple. El padre es `2026`, con 7 carpetas de cliente.
  Solo «cliente 1» tiene una solicitud («nombre de la solicitud de cotizacion»), que parece la estructura de arranque.
  Hay dos carpetas parecidas, «OTACC» y «OTACC (razón social no indicada)», y varios clientes que parecen de pruebas.
  No se movió ni se creó nada.
- **SQLite.** `anka/infra/store.py:426` abre con `sqlite3.connect(db_path)` y no hay ningún PRAGMA en el paquete
  (sin WAL). Varios procesos (dashboard, puente, supervisor) escriben en la misma base. Python ya espera 5 s por
  defecto antes de fallar con «database is locked». El respaldo sí usa la API online (`backup.py`).
- **Correcciones a los agentes.** `References` ya acumula la cadena (`anka/dominio/correo.py:221-222`);
  `Precedence: bulk` ya se detecta (`anka/dominio/reception.py:49`); `defusedxml` no se usa.

## 3. Mejoras propuestas, por prioridad

### Hacer primero (cada una atada a un paso)

| Mejora | Paso | Por qué |
|---|---|---|
| Unificar el catálogo de los nueve servicios en un solo lugar, con palabras clave, preguntas y checklist por servicio | 4 | Es lo que más cambia lo que recibe el cliente. Un topógrafo de Anka debe validar los umbrales. |
| Verificador de evidencia: cada dato lleva una cita literal y Python comprueba que exista | 3 | Impide que un dato inventado entre a la tabla. |
| Prueba de fallo inyectado en los envíos (cortar entre enviar y registrar) | 5 | Un correo doble al cliente es el riesgo más caro (D-12). |
| Detectar rebotes (DSN, RFC 3464) y autorespuestas (`X-Autoreply`, `MAILER-DAEMON`) | 2 y 6 | R13 no debe insistir a direcciones muertas. |
| Aviso de vencimiento del token de Google | 1 | En estado «Testing» el refresh token caduca a los 7 días (D-27). |
| WAL con `busy_timeout`, como cambio aparte de pocas líneas | todos | Menos bloqueos entre procesos. |

### Después

- Conjunto dorado pequeño y banco de calidad de extracción con adjuntos reales de oficios y planos.
- Validaciones de coherencia como **advertencia**, nunca bloqueo: coordenadas de otro sistema, área frente al
  polígono, alias de sistemas de referencia (CTM12, EPSG:9377).
- Sin dependencias nuevas: rechazar `DOCTYPE` en el XML de adjuntos y probar que el restore de backups rechaza rutas
  con `..` o absolutas.
- Defensa en profundidad contra prompt injection: delimitar el correo como dato y probar correos con «ignora lo
  anterior».
- Mostrar un aviso de remitente no autenticado (`Authentication-Results`: SPF/DKIM/DMARC).
- Verificar tras cada envío que la respuesta quedó en el `threadId` esperado.
- Sondeo incremental con `history.list`.
- Preprocesado antes del OCR (300 dpi, deskew, binarización).
- Tratar `refusal` e `incomplete` de OpenAI antes de parsear.

### Para más adelante o la Fase 2

Hypothesis sobre transiciones, mutation testing (mutmut) en `anka/dominio/`, DPAPI para token y copias, CSP completa,
anclaje de la huella del historial, `pip-audit`, SBOM, medición de conversión y motivo de pérdida, y un catálogo de
precios solo desde una tabla propia aprobada (nunca generado por el modelo).

### Descartar por ahora

Docling (arrastra torch y choca con el CI con hashes), SQLCipher, Litestream y Pub/Sub de Gmail.

## 4. Plan por olas (propuesto, no iniciado salvo la Ola 0)

| Ola | Contenido |
|---|---|
| 0. Base | Hecha: revisión de catálogo, Drive y SQLite. |
| 1. Fiabilidad de datos | Catálogo único con checklist por servicio, verificador de evidencia, advertencias de coherencia. |
| 2. Fiabilidad de envíos | Fallo inyectado, `threadId`, rebotes y autorespuestas, aviso de OAuth. |
| 3. Medición | Conjunto dorado pequeño y banco de extracción. |
| 4. Endurecimiento | XML, restore, CSP, prompt injection, minimización de datos. |
| 5. Sostenibilidad | Pruebas de propiedades y de mutación, prueba de restauración, `pip-audit`, `/salud` ampliada. |

Cada ola es una rama y un PR propios, y cierra con `check.bat` en verde. Una versión más corta del plan se reduce a
las seis primeras mejoras de la sección 3.

## 5. Lectura de documentos con imágenes

- **Hoy.** El OCR (RapidOCR con PDFium) ya lee cada página con poco texto o contenido visual: hasta 200 páginas por
  PDF, 100 imágenes y 40 millones de píxeles por imagen (`anka/dominio/attachments.py`). Las páginas marcadas llevan
  «COBERTURA VISUAL PARCIAL».
- **Por qué sigue siendo parcial.** El OCR lee texto, no entiende dibujos: relaciones dentro de tablas, planos,
  leyendas, fotografías y texto manuscrito quedan sin interpretar.
- **Modo ChatGPT Work.** El prompt V4 y la skill ya le piden interpretar planos, tablas e imágenes y declarar la
  cobertura real. Pedirlo con más fuerza no cambia lo que la herramienta puede abrir (ver D-35), y puede empujar al
  modelo a declarar cobertura completa sin tenerla. Lo útil ahí es medir por qué declara «parcial» y, si es por límites
  de la herramienta, mejorar el paquete local.
- **Modos local y API.** Se podría pasar solo las páginas con cobertura visual parcial a un modelo con visión (en
  Ollama, por ejemplo, `qwen2.5-vl` o `llama3.2-vision`; por API, uno de Claude u otro). Reglas: opcional en
  `config.json`, resultado como hallazgo citado marcado «interpretación visual, verificar», revisión humana (R8), y una
  decisión en `spec/05-decisiones.md` antes de empezar. Por API, el documento sale de la máquina: es una decisión tuya
  por la Ley 1581. No se ha probado con documentos reales; haría falta una prueba con 10 o 15 adjuntos difíciles.

## 6. Opinión sobre la complejidad del proyecto

Casi todo lo construido nació de un fallo real (envío doble, código no recibido, token caducado, 82 rechazos en
bucle). Lo que más sobra es lo duplicado (dos catálogos de servicios, varias versiones del prompt de Work en `docs/`)
y las piezas pensadas para un volumen mayor al real (el propio D-23 habla de «decenas de solicitudes al mes»). Los tres
modos de operación triplican lo que se prueba y se mantiene: vale la pena saber cuál se usa de verdad.

## 7. Límites de esta investigación

- Los agentes de LLM y de Gmail leyeron muy poco código; una propuesta ya estaba hecha (`References`).
- La búsqueda devolvió casi ningún hilo de Stack Overflow, GitHub o Reddit: el respaldo viene sobre todo de
  documentación oficial y artículos.
- Sin verificar: la URL del patrón outbox, la cifra de «85 % menos alucinaciones» de SafePassage (solo se vio el
  resumen), y la fuente de KPMG sobre la Ley 1581 (usar sic.gov.co).
- Normativa colombiana sin confirmar: vigencia y texto de la Resolución IGAC 471/2020, y el apéndice sobre UAS del
  RAC 91 (Aerocivil). Validar con la fuente oficial antes de ponerlas en reglas.
- Ninguna cifra de los benchmarks se contrastó con los documentos de Anka.

## 8. Fuentes citadas por los agentes

- SQLite WAL: https://www.sqlite.org/wal.html · Backup online: https://www.sqlite.org/backup.html
- Hypothesis (máquinas de estado): https://hypothesis.readthedocs.io/en/latest/stateful.html · mutmut:
  https://github.com/boxed/mutmut
- Gmail: https://developers.google.com/workspace/gmail/api/guides/sync · /push · /threads ·
  https://developers.google.com/identity/protocols/oauth2
- RFC 3834 (autorespuestas): https://www.rfc-editor.org/rfc/rfc3834 · RFC 3464 (DSN):
  https://www.rfc-editor.org/rfc/rfc3464
- OWASP LLM01 y LLM06: https://genai.owasp.org/llmrisk/llm01-prompt-injection/ ·
  https://genai.owasp.org/llmrisk/llm062025-excessive-agency/
- DPAPI: https://learn.microsoft.com/en-us/windows/win32/api/dpapi/nf-dpapi-cryptprotectdata · XML en Python:
  https://docs.python.org/3/library/xml.html
- Tesseract (calidad): https://github.com/tesseract-ocr/tessdoc/blob/main/ImproveQuality.md · OCRmyPDF:
  https://ocrmypdf.readthedocs.io/en/latest/introduction.html · ezdxf: https://ezdxf.readthedocs.io/en/stable/
- Parsers de PDF (artículos): https://arxiv.org/pdf/2511.16134 · https://arxiv.org/pdf/2409.05137 ·
  https://arxiv.org/pdf/2410.09871
- Salida estructurada: https://ollama.com/blog/structured-outputs ·
  https://developers.openai.com/api/docs/guides/structured-outputs · Tam et al.: https://arxiv.org/abs/2408.02442 ·
  Self-consistency: https://arxiv.org/abs/2203.11171 · LLM como juez: https://arxiv.org/abs/2306.05685 ·
  Calibración: https://arxiv.org/abs/2207.05221 · SafePassage: https://arxiv.org/abs/2510.00276
- IGAC Res. 471/2020: https://www.igac.gov.co/transparencia-y-acceso-a-la-informacion-publica/normograma/resolucion-no-471-de-2020 ·
  Res. 370/2021: https://redgeodesica.igac.gov.co/documentos/resolucion_370_de_2021.pdf · ASPRS (USGS):
  https://www.usgs.gov/ngp-standards-and-specifications/adopt-updated-accuracy-standards · ISO 19157-1:
  https://www.iso.org/standard/78900.html

## 9. Preguntas abiertas

1. ¿Qué modo se usa de verdad (local, API o Work) y cuántas solicitudes entran por semana?
2. ¿Quién valida los umbrales técnicos por servicio?
3. ¿Qué se hace con las carpetas duplicadas y de prueba en Drive?
4. ¿Se quiere atender solicitudes que llegan por formulario web o WhatsApp, además del correo?
