"""La forma de `config.json` (vive fuera del repositorio, en la carpeta de datos): sus claves y sus tipos.

Son `TypedDict`: en ejecución siguen siendo el diccionario que se lee del archivo, pero mypy conoce cada clave, así que
una clave mal escrita se detecta al verificar y no cuando alguien ya usa el sistema. La validación de los valores
(que el modo sea uno de los tres, que el motor exista…) sigue siendo `anka.infra.settings.validate_config`.
"""

from __future__ import annotations

from typing import TypedDict


class ConfigLlm(TypedDict, total=False):
    """El motor de análisis: Ollama (local) u OpenAI (API)."""

    provider: str          # ollama | openai
    model: str
    base_url: str          # solo Ollama
    timeout_seconds: float
    num_ctx_max: int       # solo Ollama: tope de la ventana que se puede pedir (lo que admite el modelo)
    num_ctx: int           # solo Ollama: tokens de contexto; Ollama usa 4096 por defecto y recorta el prompt


class Configuracion(TypedDict, total=False):
    """Todo lo que se puede configurar; las claves no listadas aquí se avisan como posible error de tipeo."""

    active_mode: str                  # local | api | chatgpt_work
    gmail_query: str                  # qué correos revisa el piloto de Python
    own_addresses: list[str]          # cuenta principal y aliases desde los que responde Anka
    test_sender_addresses: list[str]  # remitentes controlados de correos de prueba
    forwarded_reply_policy: str       # internal_reviewer | original_sender
    llm: ConfigLlm
    required_fields: list[str]        # datos indispensables para cotizar (los pide al cliente si faltan)
    commercial_filter_terms: list[str]
    drive_parent_id: str              # carpeta del año vigente en Drive
    folder_prefix: str                # prefijo de la carpeta de cada solicitud
    work_sync_account: str            # cuenta de Gmail que lee el puente de Work
