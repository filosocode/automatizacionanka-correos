# Anka · Automatización de correos de cotización (Fase 1)

Sistema local de apoyo comercial para Grupo Anka (topografía y geomática). Recibe solicitudes por correo, las resume
en una tabla de actividades, pide al cliente lo que falte y deja el proyecto listo para cotizar. **No cotiza** (sin
precios). Idioma de la interfaz, de los textos y de la documentación: **español**.

## Primero lee la especificación
`spec/` es la **fuente de verdad** (`spec/README.md` explica cada documento). Todo cambio de comportamiento empieza
ahí y se verifica contra ahí:
- Requisitos con id: `spec/requisitos.md` y las reglas **R1–R13** en
  `skills/anka-cotizaciones-correo/references/reglas_operativas.md` (versión vigente en su primera línea).
- Estados y transiciones: `spec/transiciones.json` (se ejecuta en `tests/test_transiciones.py`).
- Contrato con ChatGPT Work: `spec/contrato_work.schema.json` (se valida en el puente).
- Decisiones y sus motivos: `spec/05-decisiones.md`. No las deshagas sin leer por qué se tomaron.

## Cómo está armado
Código en el paquete `anka/` (detalle en `spec/06-arquitectura.md`), en cinco capas que solo dependen hacia abajo
(`cli`/`web` → `servicios` → `infra` → `dominio`); `tests/test_arquitectura.py` lo comprueba:
- `anka/dominio/`: reglas puras (`status`, `activities`, `reception`, `correo`, `caso` y `registros` (objetos `Caso`, `Evento`, `Cambio`, `Vinculo`), `work_contract`, `attachments`).
- `anka/infra/`: lo externo (`settings`, `store` SQLite, `google_services`, `mail`, `llm`, `logging_setup`, `backup`).
- `anka/servicios/`: casos de uso (`quotes`, `ediciones`, `work_import`, `pipeline`, `case_actions`, `change_control`, `followup_reminders`,
  `gmail_followup`, `drive_layout`, `drive_contents`).
- `anka/web/`: servidor y formularios (`server.py`), páginas (`paginas.py`), seguridad CSRF (`seguridad.py`), vistas (`views.py`) y `templates/`. `anka/cli/`: `app`, `supervisor`, `work_sync`.
- **Tres modos** (`active_mode` en `config.json`, fuera del repo): `local` (Ollama) y `api` comparten el código de
  `pipeline`; `chatgpt_work` es otro orquestador (la tarea de ChatGPT con la skill) y **Python no lee Gmail**: un
  puente de solo lectura (`anka/cli/work_sync.py`) importa los borradores de Work al dashboard.
- `app.py`, `supervisor.py` y `work_dashboard_sync.py` en la raíz son envoltorios de una línea hacia `anka/cli/`.
- Datos y credenciales viven en `%LOCALAPPDATA%\AnkaCorreos` (config, token, base, evidencia, copias): **nunca** se
  versionan ni se imprimen.

## Cómo se trabaja
1. **Especificación primero** (`docs/ESTANDAR_CAMBIOS.md`): requisito/regla → casos → código → skill de Work → pruebas →
   prueba manual en Work si cambia lo que Work debe hacer.
2. **Cada requisito tiene pruebas**: etiqueta las nuevas con `# Cubre: ID` y corre `python check_spec.py`.
3. **Antes de dar algo por hecho corre `check.bat`** (ruff, mypy, `check_spec`, pruebas con cobertura ≥ 82 %): es lo
   mismo que el CI. En esta máquina: `.venv\Scripts\python.exe -m unittest discover -s tests -t . -q`.
4. Las instantáneas de pantalla (`tests/golden/`) se regeneran a propósito: `UPDATE_GOLDEN=1 python -m unittest tests.test_html_render`.
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
