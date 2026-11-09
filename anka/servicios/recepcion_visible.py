"""Proyección de recepción para la portada. No crea análisis ni modifica estados."""
from __future__ import annotations

import json
from typing import Any
from urllib.parse import quote
from anka.dominio import reloj
from anka.dominio.errores import ErrorDeAnka
from anka.infra import settings
from anka.infra.store import lectura
from anka.dominio.trabajo_work import evaluar_elegibilidad, SENAL_NOTIFICACION_DE_CHATGPT
from anka.servicios.trabajos_work import veredicto_del_mensaje


def _tiempo(desde: str, ahora: Any) -> tuple[str, int | None]:
    fecha = reloj.instante(desde)
    if fecha is None:
        return 'Tiempo por confirmar', None
    minutos = max(0, int((ahora - fecha).total_seconds() // 60))
    if minutos < 1:
        return 'Hace menos de un minuto', minutos
    if minutos < 60:
        return f'Hace {minutos} min', minutos
    horas, resto = divmod(minutos, 60)
    return f'Hace {horas} h' + (f' {resto} min' if resto else ''), minutos


def solicitudes_en_recepcion() -> list[dict]:
    """Último mensaje identificado por hilo; los IDs aún sin cabeceras no cuentan como solicitudes."""
    with lectura() as db:
        filas = db.execute("""
            WITH pendientes AS (
                SELECT r.*, ROW_NUMBER() OVER (
                    PARTITION BY r.account,r.thread_id
                    ORDER BY r.internal_date DESC,r.detected_at DESC,r.message_id DESC
                ) AS orden
                FROM recepcion_mensajes r
                WHERE r.state IN ('pendiente_de_procesar','requiere_reintento','revision_manual')
                  AND TRIM(r.sender) != ''
                  AND NOT EXISTS (SELECT 1 FROM trabajos_work t
                      WHERE t.account=r.account AND t.thread_id=r.thread_id AND t.message_id=r.message_id
                      AND t.state IN ('conciliado','cancelado'))
            )
            SELECT r.account,r.thread_id,r.message_id,r.subject,r.sender,r.detected_at,r.state,r.error_kind,
                r.evidence_json,r.attachments_json,r.attempts AS lecturas,
                t.state AS trabajo_estado,t.last_error AS trabajo_error,t.job_id,t.sheet_file_id,
                t.created_at AS preparado_at,t.published_at,t.updated_at AS trabajo_at,t.attempts AS intentos,
                EXISTS(SELECT 1 FROM recepcion_eventos e WHERE e.account=r.account
                    AND e.thread_id=r.thread_id AND e.message_id=r.message_id
                    AND e.event_kind='aviso_work_intentado'
                    AND json_extract(e.detail_json,'$.job_id')=t.job_id) AS aviso_intentado,
                EXISTS(SELECT 1 FROM recepcion_eventos e WHERE e.account=r.account
                    AND e.thread_id=r.thread_id AND e.message_id=r.message_id
                    AND e.event_kind='aviso_work_enviado'
                    AND json_extract(e.detail_json,'$.job_id')=t.job_id) AS aviso_enviado,
                EXISTS(SELECT 1 FROM quotes q WHERE q.thread_id=r.thread_id) AS existente
            FROM pendientes r LEFT JOIN trabajos_work t ON t.job_id=(
                SELECT j.job_id FROM trabajos_work j WHERE j.account=r.account
                AND j.thread_id=r.thread_id AND j.message_id=r.message_id
                ORDER BY j.updated_at DESC,j.job_id DESC LIMIT 1)
            WHERE r.orden=1 ORDER BY r.detected_at DESC,r.thread_id
        """).fetchall()
        filas = [fila for fila in filas
                 if evaluar_elegibilidad({'sender': fila['sender']}, None).motivo_id != SENAL_NOTIFICACION_DE_CHATGPT
                 and veredicto_del_mensaje(db, fila).estado != 'excluido']
    resultado = []
    ahora = reloj.ahora()
    try:
        umbral = settings.load_config().get('work_coordination_alert_minutes', 120)
    except (ErrorDeAnka, OSError, ValueError):
        umbral = 120
    if not isinstance(umbral, int) or isinstance(umbral, bool) or umbral < 15:
        umbral = 120
    for fila in filas:
        item = dict(fila)
        aviso_incierto = item['trabajo_estado'] == 'publicado' and item['aviso_intentado'] and not item['aviso_enviado']
        if aviso_incierto:
            estado = 'Requiere atención'
            detalle = 'La hoja está lista. Falta confirmar la entrega del aviso interno a Work.'
        elif (item['error_kind'] or item['trabajo_error'] or item['state'] == 'revision_manual'
              or item['trabajo_estado'] == 'ambiguo'):
            estado, detalle = 'Requiere atención', 'El procesamiento tiene un aviso. Revisa el estado de recepción.'
        elif item['trabajo_estado'] == 'publicado':
            estado = 'Esperando análisis de Work'
            detalle = 'El trabajo está preparado. Todavía no se ha recibido el análisis.'
        elif item['trabajo_estado'] in ('publicando', 'pendiente_de_publicar'):
            estado, detalle = 'Preparando análisis', 'El correo está registrado y se está preparando el trabajo.'
        else:
            estado, detalle = 'Correo recibido', 'Pendiente de análisis; aún no se ha validado el alcance.'
        ultimo = item['published_at'] or item['preparado_at'] or item['detected_at']
        if estado == 'Requiere atención':
            ultimo = item['trabajo_at'] or item['detected_at']
            siguiente = ('Comprobar el aviso en Gmail antes de repetirlo.' if aviso_incierto
                         else 'Revisar el aviso de procesamiento.')
        elif item['trabajo_estado'] == 'publicado':
            siguiente = 'Work debe entregar el análisis; después podrás revisar la tabla.'
        else:
            siguiente = 'El sistema debe preparar el análisis; por ahora no necesitas reenviar el correo.'
        tiempo, minutos = _tiempo(ultimo, ahora)
        demora = estado == 'Esperando análisis de Work' and minutos is not None and minutos >= umbral
        if demora and estado != 'Requiere atención':
            siguiente = 'Revisar la tarea de análisis: aún no ha entregado un resultado.'
        try:
            archivos = json.loads(item['attachments_json'])
        except (TypeError, ValueError):
            archivos = []
        archivos = [str(a.get('filename') or 'Archivo sin nombre') for a in archivos
                    if isinstance(a, dict)] if isinstance(archivos, list) else []
        gmail = ('https://mail.google.com/mail/?authuser=' + quote(item['account'], safe='')
                 + '#all/' + quote(item['thread_id'], safe=''))
        item.update(ultimo=ultimo[:16].replace('T',' '), ultimo_iso=ultimo, tiempo=tiempo, siguiente=siguiente,
                    demora=demora,
                    umbral=umbral, archivos=archivos, gmail=gmail,
                    reintentos=max(0, (item['lecturas'] or 0) - 1) + max(0, (item['intentos'] or 0) - 1))
        item.update(estado=estado, detalle=detalle, fecha=item['detected_at'][:16].replace('T', ' '))
        resultado.append(item)
    return resultado
