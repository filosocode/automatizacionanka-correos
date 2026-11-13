"""Un hilo borrado de Gmail da un aviso claro al responder (U-89).

Cubre: U-89
"""

from tests import _isolate  # noqa: F401  (debe ir primero)

import unittest
from unittest.mock import MagicMock, patch

import httplib2
from googleapiclient.errors import HttpError

from anka.dominio.errores import ErrorDeValidacion
from anka.infra import mail
from anka.servicios import case_actions


def error_http(estado: int) -> HttpError:
    return HttpError(httplib2.Response({"status": estado}), b"{}")


# Cubre: U-89
class HiloBorrado(unittest.TestCase):
    def test_solo_el_404_es_hilo_no_encontrado(self):
        self.assertTrue(mail.es_hilo_no_encontrado(error_http(404)))
        self.assertFalse(mail.es_hilo_no_encontrado(error_http(500)))
        self.assertFalse(mail.es_hilo_no_encontrado(ValueError("x")))

    def _responder_con(self, error):
        gmail = MagicMock()
        gmail.users.return_value.threads.return_value.get.return_value.execute.side_effect = error
        with patch.object(case_actions, "get_quote", return_value={"thread_id": "t"}), \
                patch.object(case_actions, "load_config", return_value={}), \
                patch.object(case_actions, "services", return_value=(gmail, None)), \
                patch.object(case_actions, "own_addresses", return_value=set()):
            case_actions.resolve_case_recipient("t")

    def test_un_404_da_un_aviso_claro(self):
        with self.assertRaisesRegex(ErrorDeValidacion, "ya no está en Gmail"):
            self._responder_con(error_http(404))

    def test_otro_error_de_gmail_sigue_siendo_inesperado(self):
        with self.assertRaises(HttpError):
            self._responder_con(error_http(500))


if __name__ == "__main__":
    unittest.main()
