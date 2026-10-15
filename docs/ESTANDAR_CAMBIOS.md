# Estándar de cambios para los tres modos

El sistema opera en tres modos (`config.json` → `active_mode`). Un cambio de comportamiento solo está terminado cuando **los tres** hacen lo mismo.

| Modo `active_mode` | Quién procesa Gmail | Dónde viven sus reglas |
|---|---|---|
| `local` | Código Python con Ollama | `anka/dominio/reception.py`, `anka/servicios/drive_layout.py`, `anka/servicios/pipeline.py` |
| `api` | El mismo código Python con la API de OpenAI | Igual que `local`: solo cambia el motor que analiza el correo (`anka/infra/llm.py`) |
| `chatgpt_work` | La tarea de ChatGPT Work con la skill | `skills/anka-cotizaciones-correo/SKILL.md` |

Solo uno de los tres puede estar activo sobre la misma cuenta de Gmail (ver `MODO_OPERACION.md`).

## Qué se comparte y qué no

- **`local` y `api` son el mismo programa.** El flujo, las reglas, la estructura de Drive y el prompt son idénticos; lo único que cambia es qué función analiza el correo. Por eso un cambio en el código Python vale para los dos automáticamente, y `tests/test_paridad_modos.py` comprueba que con el mismo correo dejan el mismo estado, el mismo borrador y el mismo prompt.
- **`chatgpt_work` es otro orquestador.** Interpreta la skill. Con el prompt V6 (T-41) Work abre los adjuntos directamente en Gmail y el puente los verifica por nombre y tamaño. El preparador optativo T-34 (prompt V5 y anteriores) ejecuta lectores Python y publica sus resultados en Drive para que Work los consulte; no analiza ni envía. Todo cambio de reglas debe escribirse también en `SKILL.md`; ninguna prueba automática garantiza cómo lo interpreta ChatGPT, por eso hay una prueba manual (abajo).
- **`config.json` solo lo lee Python.** Work no lo ve. Si un ajuste de configuración cambia el comportamiento (por ejemplo `required_fields`), anótalo en la skill; hoy esa sincronización es manual.

## Fuente única de las reglas

`skills/anka-cotizaciones-correo/references/reglas_operativas.md` define las reglas (R1, R2…) con una **versión** y un bloque legible por máquina. Es lo primero que se edita. El catálogo `references/casos_paridad.json` contiene los correos de ejemplo con su resultado esperado, y es el mismo que ejecutan las pruebas en Python y el que se usa a mano en Work.

## Procedimiento para un cambio de comportamiento

0. **Especificación.** Todo empieza en `spec/` (ver `spec/README.md`): el requisito en `spec/requisitos.md`, la tabla `spec/transiciones.json` si cambian los estados, el esquema `spec/contrato_work.schema.json` si cambia lo que Work entrega, y una línea en `spec/05-decisiones.md` si se decide algo con consecuencias. Las pruebas nuevas se etiquetan con `# Cubre: ID`; `python check_spec.py` falla si un requisito queda sin prueba.
1. **Regla.** Edita `reglas_operativas.md`: la regla (tabla), el bloque `json reglas` y, si cambia el contenido, sube la versión (`Versión de las reglas: N`).
2. **Casos.** Añade o ajusta casos en `casos_paridad.json` (correo + resultado esperado) y, si hace falta, en `casos_prueba.md`.
3. **Python (`local` y `api`).** Implementa el cambio. Si es un dato que también debe leer la skill (señales, sufijos, ruta), exponlo como constante y compárala en `tests/test_paridad_modos.py`.
4. **Skill (`chatgpt_work`).** Actualiza `SKILL.md`: copia la regla, sube «Reglas operativas (versión N)» y añade la frase de la regla al bloque `frases_en_skill`.
5. **Verifica.** `check.bat` debe pasar (incluye `check_spec.py` y las pruebas de transiciones y del contrato de Work). Si falla `tests/test_paridad_modos.py`, un modo se separó de las reglas: corrígelo, no desactives la prueba.
6. **Prueba manual en Work.** Antes de activar el modo `chatgpt_work` con reglas nuevas, pega en Work cada caso de `casos_paridad.json` y comprueba la decisión. Para el contrato de evidencia, comprueba que cada pregunta apunte a hallazgos, que no se pregunte un dato ya explícito y que una limitación de lectura pase a revisión interna; revisa también que el dashboard muestre las advertencias sin descartar el borrador. Para cambios de lectura documental, usa además el control de profundidad de `PILOTO_ACEPTACION.md`: incluye un dato en el correo, otro en documento y otro en tabla/archivo geoespacial, y comprueba que no se repitan como preguntas. Anota fecha, versión del prompt y resultado en el pull request.
7. **Pull request.** Usa la plantilla (`.github/pull_request_template.md`), que repite esta lista.

## Qué prueban las pruebas de paridad y qué no

Prueban, sin red ni servicios externos:

- que las señales de descarte, los sufijos societarios, la ruta de Drive y los indicadores de comportamiento (`crear_cliente_automatico`, `bloquear_si_anio_distinto`…) del código coinciden con el bloque de reglas;
- que `SKILL.md` declara la misma versión, contiene la frase de cada regla y enlaza la fuente única;
- que los casos compartidos producen la decisión esperada en Python;
- que Ollama y API dejan el mismo resultado para el mismo correo y envían el mismo esquema a su servicio;
- que el programa Python se niega a procesar Gmail en modo `chatgpt_work`.

No prueban:

- que ChatGPT Work **interprete bien** la skill (de ahí la prueba manual del paso 6);
- que el modelo clasifique bien un correo concreto (eso lo mide la muestra etiquetada, `evaluacion/`);
- la sincronización de `config.json` con la skill.

## Auditar dependencias (pip-audit)

El CI corre `pip-audit` sobre `requirements.lock` y `requirements-dev.lock` cada lunes y en cada subida (trabajo `auditoria` de `.github/workflows/ci.yml`). No es una dependencia del proyecto y no se instala en la máquina de desarrollo. Si quieres correrlo a mano en un entorno aparte:

```
python -m venv %TEMP%\auditoria && %TEMP%\auditoria\Scripts\python -m pip install pip-audit==2.9.0
%TEMP%\auditoria\Scripts\python -m pip_audit --require-hashes --disable-pip --no-deps -r requirements.lock -r requirements-dev.lock
```

Un hallazgo no rompe nada solo: se actualiza el rango en `requirements*.txt`, se regeneran los `.lock` (ver README) y se corre `check.bat`.

## Verificación rápida mientras trabajas

`check.bat quick` corre ruff, mypy, `check_spec.py` y las pruebas sin los módulos lentos (`tools/pruebas_rapidas.py --rapido`; `--medir` muestra los más lentos y `LENTOS` se ajusta ahí). No sustituye a `check.bat`: no mide cobertura y salta lo lento. En una terminal sin `.bat`: `python tools/pruebas_rapidas.py --rapido` después de `python -m ruff check .`, `python -m mypy` y `python check_spec.py`.
