from tests import _isolate  # noqa: F401

import unittest
from unittest.mock import Mock, patch

from anka.servicios import aviso_work


class AvisoWork(unittest.TestCase):
    def test_envio_incierto_se_reconcilia_sin_enviar_dos_veces(self):
        trabajo = dict(job_id='ANKAJOB-0123456789abcdef', account=aviso_work.CUENTA,
                       referencia={'sender': aviso_work.REMITENTE_PILOTO}, state='publicado',
                       sheet_file_id='hoja', thread_id='hilo', message_id='mensaje')
        gmail = Mock()
        mensajes = gmail.users.return_value.messages.return_value
        mensajes.send.return_value.execute.side_effect = TimeoutError
        mensajes.list.return_value.execute.side_effect = [{}, {'messages': [{'id': 'aviso'}]}]
        with patch.object(aviso_work, 'lectura') as lectura, patch.object(aviso_work, 'anotar_evento') as anotar:
            consulta = lectura.return_value.__enter__.return_value.execute
            consulta.side_effect = [[], [(aviso_work.INICIO,)], [(aviso_work.INICIO,)]]
            with self.assertRaises(TimeoutError):
                aviso_work.avisar(gmail, trabajo)
            self.assertEqual(aviso_work.avisar(gmail, trabajo), 'envio_por_verificar')
            self.assertEqual(aviso_work.avisar(gmail, trabajo), 'enviado')
            anotar.assert_called_with(trabajo['job_id'], aviso_work.ENTREGA, {'gmail_message_id': 'aviso'})
        mensajes.send.assert_called_once()
