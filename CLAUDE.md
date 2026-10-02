# Anka · Automatización de correos de cotización (Fase 1)

Sistema local de apoyo comercial para Grupo Anka (topografía y geomática). Recibe solicitudes por correo, las resume
en una tabla de actividades, pide al cliente lo que falte y deja el proyecto listo para cotizar. **No cotiza** (sin
precios). Idioma de la interfaz, de los textos y de la documentación: **español**.

## Primero lee la especificación
`spec/` es la **fuente de verdad** (`spec/README.md` explica cada documento). Todo cambio de comportamiento empieza
ahí y se verifica contra ahí:
- Requisitos con id: `spec/requisitos.md` y las reglas **R1–R13** en
  `skills/anka-cotizaciones-correo/references/reglas_operativas.md` (versión vigente en su primera línea).
- Estados y transiciones: `spec/transiciones.json` (se ejecuta en `test_transiciones.py`).
- Contrato con ChatGPT Work: `spec/contrato_work.schema.json` (se valida en el puente).
- Decisiones y sus motivos: `spec/05-decisiones.md`. No las deshagas sin leer por qué se tomaron.

## Cómo está armado
- **Tres modos** (`active_mode` en `config.json`, fuera del repo): `local` (Ollama) y `api` comparten el código de
  `pipeline.py`; `chatgpt_work` es otro orquestador (la tarea de ChatGPT con la skill) y **Python no lee Gmail**: un
  puente de solo lectura (`work_dashboard_sync.py`) importa los borradores de Work al dashboard.
- Dashboard web local (`web.py`, plantillas Jinja en `templates/`), base SQLite (`store.py`, `quotes.py`), seguimiento
  de hilos (`gmail_followup.py`), código de la oferta T/T&E y cambios (`change_control.py`), recordatorios
  (`followup_reminders.py`), copias de seguridad (`backup.py`), lanzador único (`supervisor.py`).
- Datos y credenciales viven en `%LOCALAPPDATA%\AnkaCorreos` (config, token, base, evidencia, copias): **nunca** se
  versionan ni se imprimen.

## Cómo se trabaja
1. **Especificación primero** (`ESTANDAR_CAMBIOS.md`): requisito/regla → casos → código → skill de Work → pruebas →
   prueba manual en Work si cambia lo que Work debe hacer.
2. **Cada requisito tiene pruebas**: etiqueta las nuevas con `# Cubre: ID` y corre `python check_spec.py`.
3. **Antes de dar algo por hecho corre `check.bat`** (ruff, mypy, `check_spec`, pruebas con cobertura ≥ 82 %): es lo
   mismo que el CI. En esta máquina: `.venv\Scripts\python.exe -m unittest discover -q`.
4. Las instantáneas de pantalla (`golden/`) se regeneran a propósito: `UPDATE_GOLDEN=1 python -m unittest test_html_render`.
5. Rama por tema, commits con trailer de coautoría, y **no hagas push ni commit sin que lo pidan**.

## Trampas que ya costaron tiempo
- **El lanzador reinicia dashboard y puente cuando cambia el código.** Una página abierta antes de un cambio debe
  recargarse (el token CSRF ya sobrevive a los reinicios, pero la plantilla no).
- **Work escribe a veces los estados sin tildes**: se normalizan (`status.canonical_work_status`). Nunca compares
  etiquetas de estado con `==` sobre texto de Work.
- **Un estado nuevo de un caso exige tocar** `spec/transiciones.json`, `STATUS_LABELS`, `_case_stage` (lo que se ve en
  la lista) y la prueba de transiciones.
- **Cambiar una plantilla cambia las instantáneas**: regenera y revisa el diff antes de aceptarlo.
- **Consola de Windows (cp1252)**: usa `PYTHONIOENCODING=utf-8` al imprimir tildes.
- **Sin dependencias nuevas** a la ligera: el CI instala con hashes (`requirements-dev.lock`).
- Para scripts de parcheo con comillas, escribe el archivo con la herramienta de archivos en vez de heredocs.

## Principios que no se negocian
Nada sale al cliente sin que una persona lo revise (R8; única excepción: los recordatorios opcionales de R13); no se
inventan datos; todo cambio deja huella (quién, cuándo, qué); los tres modos se comportan igual.
