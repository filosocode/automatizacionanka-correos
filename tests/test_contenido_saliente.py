"""T-59 Fase A: ningún texto interno de Work llega a un borrador comercial ni a un correo enviado al cliente.

Solo SQLite temporal y Gmail falso. Las pruebas de «Caracterización» fijan lo que se comprobó antes de cambiar el
código (la pérdida de `preguntas_enviadas` se corrigió en la Fase B: ver test_continuidad_de_casos).

Cubre: T-59
"""

from tests import _isolate  # noqa: F401  (debe ir primero)

import hashlib
import re
import unittest
from pathlib import Path
from unittest.mock import patch

from anka.cli import work_sync
from anka.dominio.correo import reply_email
from anka.dominio.errores import ErrorDeAnka, ErrorDeValidacion
from anka.dominio.work_contract import marcadores_internos
from anka.servicios import case_actions, quotes
from anka.infra import store
from tests.test_envios_inciertos import Base
from tests.test_work_sync import draft_message, payload

RAIZ = Path(__file__).resolve().parents[1]
CON_MARCADORES = {
    "inicio del bloque": "Hola.\n[ANKA_DASHBOARD_JSON_V1]\n{}\n",
    "cierre del bloque": "Hola.\n[/ANKA_DASHBOARD_JSON_V1]",
    "encabezado interno": "[REVISAR ANTES DE ENVIAR - BORRAR ESTA SECCION]\nHola, necesitamos confirmar el área.",
    "encabezado con otra forma": "  [Revisar antes de enviar – borrar esta sección]\nHola.",
    "rótulo de borrador interno": "BORRADOR INTERNO — NO ENVIAR. Hola, necesitamos el área.",
    "texto pegado a mitad": "Hola, gracias.\n\nNecesitamos el área.\n[REVISAR ANTES DE ENVIAR - BORRAR ESTA SECCION]\nNota.",
}
LEGITIMOS = (
    "Hola, por favor puede revisar antes de enviar los planos que le solicitamos?",
    "Hola, el borrador de la propuesta es interno hasta que usted lo confirme. ¿Cuál es el área?",
    "Hola, necesitamos el archivo JSON del levantamiento y el dashboard de su contratista.",
)




class Caracterizacion(Base):
    """Lo comprobado ANTES de la protección: fallos demostrados frente a hipótesis (Paso 1)."""

    def test_sync_thread_activity_solo_lo_llama_el_puente_general_que_esta_apagado_por_defecto(self):
        # Cubre: T-59
        # Demostrado: quién lo llama y que el puente general no corre sin autorización. Hipótesis sin probar: qué
        # avisos del dashboard dejan de actualizarse en ese caso.
        llamadores = sorted(str(p.relative_to(RAIZ)).replace("\\", "/") for p in (RAIZ / "anka").rglob("*.py")
                            if re.search(r"\bsync_thread_activity\(", p.read_text(encoding="utf-8"))
                            and p.name != "gmail_followup.py")
        self.assertEqual(llamadores, ["anka/cli/work_sync.py"])
        self.assertEqual(work_sync.motivo_de_bloqueo({}), work_sync.MENSAJE_SIN_AUTORIZACION)

    def test_inventario_de_las_rutas_que_guardan_o_envian_correos(self):
        # Cubre: T-59
        # Cualquier ruta nueva que escriba en Gmail debe pasar por `reply_email` (donde está la protección).
        patron = re.compile(r"\.send\(|drafts\(\)\.(?:create|update)\(")
        rutas = {str(p.relative_to(RAIZ)).replace("\\", "/"): p.read_text(encoding="utf-8")
                 for p in (RAIZ / "anka").rglob("*.py")}
        con_escritura = sorted(ruta for ruta, texto in rutas.items() if patron.search(texto))
        self.assertEqual(con_escritura, ["anka/infra/mail.py", "anka/servicios/case_actions.py",
                                         "anka/servicios/followup_reminders.py"])
        for ruta in con_escritura:
            self.assertIn("reply_email(", rutas[ruta], ruta)


# Cubre: T-59
class ProteccionDeContenido(Base):
    def lo_que_cambio(self):
        db = store.connect()
        try:
            return (db.execute("SELECT status, draft_id, draft_body, sent_message_id, updated_at FROM quotes "
                               "WHERE thread_id='t-1'").fetchone()[:],
                    db.execute("SELECT COUNT(*) FROM mail_operations").fetchone()[0],
                    db.execute("SELECT COUNT(*) FROM case_events WHERE thread_id='t-1'").fetchone()[0],
                    db.execute("SELECT COUNT(*) FROM case_messages WHERE thread_id='t-1'").fetchone()[0])
        finally:
            db.close()

    def reabrir(self):
        """Deja el caso otra vez en revisión (tras un envío queda «respuesta enviada»)."""
        with store.transaccion() as db:
            db.execute("UPDATE quotes SET status='review' WHERE thread_id='t-1'")

    def assert_nada_salio(self, antes):
        self.assertEqual((len(self.gmail.created_drafts), len(self.gmail.updated_drafts), self.gmail.envios_pedidos()),
                         (0, 0, 0))
        self.assertEqual(self.lo_que_cambio(), antes)

    def test_guardar_un_borrador_con_texto_interno_se_rechaza_sin_tocar_nada(self):
        for nombre, texto in CON_MARCADORES.items():
            antes = self.lo_que_cambio()
            with self.subTest(nombre), self.assertRaises(ErrorDeValidacion) as caido:
                case_actions.save_dashboard_draft("t-1", texto)
            self.assertIn("texto interno de Work", str(caido.exception))
            self.assert_nada_salio(antes)

    def test_enviar_con_texto_interno_se_rechaza_sin_reservar_ni_enviar(self):
        for nombre, texto in CON_MARCADORES.items():
            antes = self.lo_que_cambio()
            with self.subTest(nombre), self.assertRaises(ErrorDeValidacion) as caido:
                case_actions.send_dashboard_reply("t-1", texto)
            self.assertIn("no se guardó ni se envió", str(caido.exception))
            self.assert_nada_salio(antes)

    def test_las_preguntas_enviadas_por_el_flujo_de_python_tambien_se_comprueban(self):
        antes = self.lo_que_cambio()
        db = store.connect()
        try:
            with self.assertRaises(ErrorDeValidacion):
                case_actions._send_questions(db, self.gmail, {"required_fields": []}, "t-1", "m-1", "cliente@example.com",
                                             {}, None, "Hola.\n[ANKA_DASHBOARD_JSON_V1]", {"alias@anka.com.co"})
        finally:
            db.close()
        self.assert_nada_salio(antes)

    def test_un_correo_legitimo_se_guarda_y_se_envia_con_su_huella(self):
        for texto in LEGITIMOS:
            self.assertEqual(marcadores_internos(texto), [], texto)
        case_actions.save_dashboard_draft("t-1", LEGITIMOS[0])
        self.assertEqual(len(self.gmail.created_drafts), 1)
        case_actions.send_dashboard_reply("t-1", LEGITIMOS[1])
        self.assertEqual(self.gmail.envios_pedidos(), 1)
        huella = hashlib.sha256(LEGITIMOS[1].encode("utf-8")).hexdigest()
        self.assertTrue(any(f"Huella SHA-256 del texto aprobado: {huella}." in s for s in self.eventos("sent")))

    def test_lo_que_se_entrega_a_gmail_es_lo_aprobado(self):
        original = reply_email

        def distinto(thread, recipient, body, own=None, **kw):
            return original(thread, recipient, body + "\nPD: texto añadido por el camino.", own, **kw)

        antes = self.lo_que_cambio()
        with patch("anka.servicios.case_actions.reply_email", distinto), self.assertRaises(ErrorDeAnka) as caido:
            case_actions.send_dashboard_reply("t-1", "Hola, recibimos su solicitud.")
        self.assertIn("no coincide con el que aprobaste", str(caido.exception))
        self.assert_nada_salio(antes)
        with patch("anka.servicios.case_actions.reply_email", distinto), self.assertRaises(ErrorDeAnka):
            case_actions.save_dashboard_draft("t-1", "Hola, recibimos su solicitud.")
        self.assert_nada_salio(antes)

    def test_un_cambio_posterior_a_la_aprobacion_es_otro_envio_con_otra_huella(self):
        with patch.object(case_actions, "_send_through_gmail", side_effect=[{"id": "g-1"}, {"id": "g-2"}]) as enviar:
            case_actions.send_dashboard_reply("t-1", "Hola, recibimos su solicitud.")
            self.reabrir()
            case_actions.send_dashboard_reply("t-1", "Hola, recibimos su solicitud y la revisamos.")
        huellas = [re.search(r"([0-9a-f]{64})", s).group(1) for s in self.eventos("sent")]
        self.assertEqual(len(set(huellas)), 2)
        self.assertEqual(enviar.call_count, 2)

    def test_la_marca_de_estado_vieja_sigue_impidiendo_el_envio(self):
        marca = quotes.get_quote("t-1").marca_de_estado
        with store.transaccion() as db:
            db.execute("UPDATE quotes SET updated_at='2099-01-01T00:00:00-05:00' WHERE thread_id='t-1'")   # el caso cambia
        with self.assertRaises(ErrorDeValidacion) as caido:
            case_actions.send_dashboard_reply("t-1", "Hola, recibimos su solicitud.", state_token=marca)
        self.assertIn("cambió desde que abriste la pantalla", str(caido.exception))
        self.assertEqual(self.gmail.envios_pedidos(), 0)

    def test_el_mismo_texto_no_sale_dos_veces(self):
        case_actions.send_dashboard_reply("t-1", "Hola, recibimos su solicitud.")
        with self.assertRaises(ErrorDeValidacion):
            case_actions.send_dashboard_reply("t-1", "Hola, recibimos su solicitud.")
        self.assertEqual(self.gmail.envios_pedidos(), 1)

    def test_un_resultado_incierto_no_se_reenvia(self):
        with patch.object(case_actions, "_send_through_gmail", side_effect=ConnectionError("sin red")):
            with self.assertRaises(ConnectionError):
                case_actions.send_dashboard_reply("t-1", "Hola, recibimos su solicitud.")
        self.assertEqual(self.estado_de_las_reservas(), ["pending"])
        with self.assertRaises(ErrorDeValidacion):
            case_actions.send_dashboard_reply("t-1", "Hola, recibimos su solicitud.")
        self.assertEqual(self.gmail.envios_pedidos(), 0)
        self.assertEqual(self.estado_de_las_reservas(), ["pending"])


# Cubre: T-59
class CompatibilidadConWork(unittest.TestCase):
    def test_el_importador_sigue_aceptando_los_marcadores_y_el_correo_para_el_cliente_sale_limpio(self):
        datos = payload(thread="T9", message="m9")
        mensaje = draft_message(datos, thread="T9")
        encontrado = work_sync.extract(mensaje, "d-1")
        self.assertIsNotNone(encontrado)
        analisis, _ = encontrado
        self.assertEqual(analisis["thread_id"], "T9")
        self.assertEqual(analisis["draft_reply"], "Hola")
        self.assertEqual(marcadores_internos(analisis["draft_reply"]), [])

    def test_el_borrador_de_revision_de_python_conserva_su_encabezado_pero_nunca_el_bloque_json(self):
        thread = {"id": "t", "messages": [{"id": "m", "payload": {"headers": [
            {"name": "From", "value": "cliente@example.com"}, {"name": "Subject", "value": "Solicitud"},
            {"name": "Message-ID", "value": "<m@example.com>"}]}, "labelIds": ["INBOX"]}]}
        texto = "[REVISAR ANTES DE ENVIAR - BORRAR ESTA SECCION]\nNota interna.\n\nHola."
        with self.assertRaises(ErrorDeValidacion):
            reply_email(thread, "cliente@example.com", texto)
        self.assertIn("Nota interna", reply_email(thread, "cliente@example.com", texto,
                                                  permitir_seccion_interna=True).get_content())
        with self.assertRaises(ErrorDeValidacion):
            reply_email(thread, "cliente@example.com", texto + "\n[ANKA_DASHBOARD_JSON_V1]",
                        permitir_seccion_interna=True)

    def test_palabras_comunes_no_se_confunden_con_los_delimitadores(self):
        for texto in LEGITIMOS + ("Por favor revisar antes de enviar el informe.", "El json y el borrador interno."):
            self.assertEqual(marcadores_internos(texto), [], texto)


if __name__ == "__main__":
    unittest.main()
