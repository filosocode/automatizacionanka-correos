"""La forma de `config.json` (vive fuera del repositorio, en la carpeta de datos): sus claves y sus tipos.

Son `TypedDict`: en ejecución siguen siendo el diccionario que se lee del archivo, pero mypy conoce cada clave, así que
una clave mal escrita se detecta al verificar y no cuando alguien ya usa el sistema. La validación de los valores
(que el modo sea uno de los tres, que el motor exista…) sigue siendo `anka.infra.settings.validate_config`.
"""

from __future__ import annotations

from collections.abc import Mapping
from enum import StrEnum
from typing import TypedDict


class Modo(StrEnum):
    """Los tres modos de operación (`active_mode`); al ser `str`, cada uno se compara igual que su texto."""

    LOCAL = "local"                 # Ollama en este equipo
    API = "api"                     # API de OpenAI
    CHATGPT_WORK = "chatgpt_work"   # Work es el único analizador; Python no lee Gmail por su cuenta


MODOS = frozenset(modo.value for modo in Modo)
MODO_POR_DEFECTO = Modo.LOCAL      # el que se asume si `config.json` no dice ninguno


def modo_activo(config: Mapping[str, object]) -> str:
    """El texto de `active_mode` tal como está en la configuración, o el modo por defecto si falta."""
    return str(config.get("active_mode", MODO_POR_DEFECTO))


def es_modo_work(config: Mapping[str, object]) -> bool:
    """¿Está activo el modo ChatGPT Work? Solo entonces corre el puente de borradores."""
    return config.get("active_mode") == Modo.CHATGPT_WORK


class ConfigLlm(TypedDict, total=False):
    """El motor de análisis: Ollama (local) u OpenAI (API)."""

    provider: str          # ollama | openai
    model: str
    base_url: str          # solo Ollama
    timeout_seconds: float
    context_tokens: int    # solo API: tokens de entrada que se aceptan por llamada (por defecto 100 000)
    chunk_chars: int       # tamaño de un tramo al leer documentos largos (por defecto 30 000 caracteres)
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
    work_drive_enabled: bool          # preparación automática de expedientes; desactivada por defecto
    work_drive_parent_id: str         # carpeta de recepción, distinta de la carpeta comercial del año
    work_drive_query: str             # búsqueda explícita de correos para preparar
    ocr_segundos_por_archivo: float   # segundos de OCR local por archivo; 0 = sin límite (por defecto)
    ocr_segundos_por_caso: float      # segundos de OCR local por caso (todos sus adjuntos); 0 = sin límite
    ocr_omitir_escaneados: bool       # no hacer OCR a PDF e imágenes escaneados: marcarlos «requiere visión»
    gmail_reception_enabled: bool     # trigger Gmail determinista, solo lectura y opt-in
    gmail_reception_interval_seconds: int  # frecuencia del sondeo (30–3600 s, por defecto 60)
    work_dispatch_enabled: bool       # despacho automático a Work (T-53); desactivado por defecto
    work_dispatch_folder_id: str      # raíz de laboratorio con PENDIENTES y CERRADOS
    work_dispatch_thread_id: str      # el único hilo del primer piloto
    work_dispatch_message_id: str     # el único mensaje del primer piloto
    work_general_bridge_enabled: bool  # puente general de borradores (T-56); apagado por defecto, autorización expresa
    work_coordination_enabled: bool   # coordinación automática Python-Work (T-55); desactivada por defecto
    work_coordination_folder_id: str  # raíz con PENDIENTES y CERRADOS
    work_coordination_since: str      # fecha y hora ISO con zona: lo detectado antes nunca se procesa
    work_coordination_interval_seconds: int   # cada cuánto corre un ciclo (60-3600)
    work_coordination_max_publish: int        # trabajos nuevos por ciclo (1-5)
    work_coordination_max_import: int         # importaciones por ciclo (1-5)
    work_coordination_max_archive: int        # archivados por ciclo (1-10)
    work_coordination_thread_id: str          # laboratorio: solo este hilo (opcional, junto con el mensaje)
    work_coordination_message_id: str         # laboratorio: solo este mensaje
    work_coordination_alert_minutes: int      # minutos sin respuesta de Work antes de alertar (15-1440)
