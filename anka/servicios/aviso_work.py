"""Aviso interno de un trabajo publicado, con recuperación de envíos inciertos."""
import base64
import re
from email.message import EmailMessage

from anka.infra.clientes_google import ClienteGmail
from anka.infra.store import lectura
from anka.servicios.trabajos_work import anotar_evento

CUENTA = 'ankacomercialia@gmail.com'
REMITENTE_PILOTO = '91andresmunoz@gmail.com'
INICIO = 'aviso_work_intentado'
ENTREGA = 'aviso_work_enviado'


def avisar(gmail: ClienteGmail, trabajo: dict) -> str:
    """Solo bajo el candado del coordinador. Un POST incierto se verifica en Gmail."""
    job_id = trabajo['job_id']
    if (trabajo['account'] != CUENTA or trabajo['referencia'].get('sender') != REMITENTE_PILOTO
            or trabajo['state'] != 'publicado' or not trabajo['sheet_file_id']):
        return 'no_aplica'
    if not re.fullmatch(r'ANKAJOB-[0-9a-f]{16}', job_id):
        raise ValueError('identidad_de_trabajo_invalida')
    with lectura() as db:
        eventos = {r[0] for r in db.execute(
            "SELECT event_kind FROM recepcion_eventos WHERE account=? AND thread_id=? AND message_id=? "
            "AND event_kind IN (?,?) AND json_extract(detail_json,'$.job_id')=?",
            (CUENTA, trabajo['thread_id'], trabajo['message_id'], INICIO, ENTREGA, job_id))}
    if ENTREGA in eventos:
        return 'ya_enviado'
    mid = f'{job_id.lower()}@ankacomercialia.gmail.com'
    if INICIO in eventos:
        respuesta = gmail.users().messages().list(userId='me', q=f'in:sent rfc822msgid:{mid}', maxResults=2).execute()
        encontrados = respuesta.get('messages', [])
        if len(encontrados) != 1:
            return 'envio_por_verificar'
        enviado = encontrados[0]['id']
    else:
        mensaje = EmailMessage()
        mensaje['From'] = mensaje['To'] = CUENTA
        mensaje['Subject'] = f'[ANKA-WORK-LISTO] {job_id}'
        mensaje['Message-ID'] = f'<{mid}>'
        mensaje.set_content(f'ANKA_WORK_LISTO_V1\njob_id={job_id}\n'
                            f'thread_id={trabajo["thread_id"]}\nmessage_id={trabajo["message_id"]}\n'
                            f'spreadsheet_id={trabajo["sheet_file_id"]}\n')
        if not anotar_evento(job_id, INICIO, {}):
            return 'envio_por_verificar'
        raw = base64.urlsafe_b64encode(mensaje.as_bytes()).decode('ascii')
        enviado = gmail.users().messages().send(userId='me', body={'raw': raw}).execute()['id']
    anotar_evento(job_id, ENTREGA, {'gmail_message_id': enviado})
    return 'enviado'
