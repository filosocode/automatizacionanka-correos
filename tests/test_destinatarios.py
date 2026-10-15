"""Quien revisa ve de dónde sale cada destinatario antes de enviar.

Cubre: U-45
"""

from tests import _isolate  # noqa: F401

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from anka.infra import settings, store
from anka.servicios import change_control
from anka.servicios import work_import
from anka.servicios.change_control import (
    ORIGEN_CAMBIO, ORIGEN_CONTACTO, ORIGEN_DESTINATARIO, ORIGEN_NUEVO, ORIGEN_PARTICIPANTE, ORIGEN_VINCULADO,
    origen_en_texto,
)
from tests.test_work_sync import b64, payload


class Origen(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        patcher = patch.object(settings, "DATA_DIR", Path(self.temp.name))
        patcher.start()
        self.addCleanup(patcher.stop)
        work_import.dashboard_upsert(b64(json.dumps(payload(thread="T1", message="m1"))))
        db = store.connect()
        db.execute("UPDATE quotes SET recipient='jefe@anka.co' WHERE thread_id='T1'")
        store.record_event(db, "T1", "received", "gmail", "g1", "Escribió", actor_email="Colega@Cliente.co")
        store.register_change(db, "T1", "m9", "tercero@otro.co", "Tercero", "Asunto", "texto", "2026-10-01",
                              False, False, False)
        db.execute("""INSERT INTO case_links (thread_id, other_thread_id, message_id, sender_email, reason, occurred_at)
                      VALUES ('T1', 'T2', 'mm', 'vinculado@otro.co', 'mismo asunto', '2026-10-01')""")
        db.commit()
        db.close()

    def test_cada_direccion_dice_de_donde_sale(self):
        casos = {"c@example.com": ORIGEN_CONTACTO, "JEFE@anka.co": ORIGEN_DESTINATARIO,
                 "colega@cliente.co": ORIGEN_PARTICIPANTE, "tercero@otro.co": ORIGEN_CAMBIO,
                 "vinculado@otro.co": ORIGEN_VINCULADO, "nadie@nada.co": ORIGEN_NUEVO}
        for direccion, esperado in casos.items():
            with self.subTest(direccion=direccion):
                self.assertEqual(change_control.origen_de_destinatario("T1", direccion), esperado)

    def test_los_externos_se_mantienen_como_destinatarios_permitidos(self):
        self.assertIn("tercero@otro.co", change_control.known_addresses("T1"))     # no se prohíben (decisión del dueño)

    def test_el_texto_del_historial_solo_marca_a_los_que_no_son_el_contacto_ni_el_destinatario(self):
        self.assertEqual(origen_en_texto(ORIGEN_CONTACTO), "")
        self.assertEqual(origen_en_texto(ORIGEN_DESTINATARIO), "")
        self.assertEqual(origen_en_texto(ORIGEN_CAMBIO), f" ({ORIGEN_CAMBIO})")

    def test_la_pantalla_muestra_el_origen_solo_de_quien_no_es_el_contacto(self):
        from anka.web import pantalla_caso
        self.assertEqual(pantalla_caso._compose_origen("T1", "tercero@otro.co"), ORIGEN_CAMBIO)
        self.assertEqual(pantalla_caso._compose_origen("T1", "c@example.com"), "")
        self.assertEqual(pantalla_caso._compose_origen("T1", ""), "")

    def test_el_envio_a_un_externo_deja_su_origen_en_el_historial(self):
        from anka.servicios import case_actions
        quote = {"analysis": {}}
        case_actions._record_sent_reply(quote, "T1", "id-1", "Hola", "tercero@otro.co", "waiting",
                                        "2026-10-02T10:00:00-05:00")
        db = store.connect()
        textos = [r["summary"] for r in db.execute("SELECT summary FROM case_events WHERE kind='sent'")]
        db.close()
        self.assertTrue(any("tercero@otro.co (remitente de un cambio)" in t for t in textos), textos)


if __name__ == "__main__":
    unittest.main()
