"""Prefiltro: solo descarta ruido evidente; una solicitud legítima nunca llega a descartarse."""

import _isolate  # noqa: F401  (debe ir primero)

import base64
import unittest

from reception import automated_noise_reason, mentions_commercial_terms

TERMS = ["cotización", "cotizar", "presupuesto", "propuesta", "levantamiento", "topográfico",
         "lidar", "fotogrametría", "batimetría"]


def mail(sender="Cliente <cliente@empresa.co>", subject="Consulta", body="Hola", labels=None,
         **headers):
    encoded = base64.urlsafe_b64encode(body.encode()).decode()
    header_list = [{"name": "From", "value": sender}, {"name": "Subject", "value": subject}]
    header_list += [{"name": name.replace("_", "-"), "value": value} for name, value in headers.items()]
    return {
        "id": "m-1", "labelIds": labels or ["INBOX"],
        "payload": {"headers": header_list,
                    "parts": [{"mimeType": "text/plain", "body": {"data": encoded}}]},
    }


class RealRequestsAreNeverNoise(unittest.TestCase):
    """Casos que el filtro anterior descartaba por no contener las palabras exactas."""

    REQUESTS = {
        "pregunta de valor": "Buenas tardes, ¿cuánto vale medir un lote de 5 hectáreas en Cota? Necesito tiempos.",
        "disponibilidad y costo": "Quisiera saber si tienen disponibilidad y el costo para inspeccionar con dron una línea.",
        "estimación": "Necesitamos una estimación de costo para un estudio de ortofoto de un predio de 200 ha.",
        "explícita": "Solicito cotización de un levantamiento de 10 ha en Cota.",
    }

    def test_none_of_them_is_marked_as_noise(self):
        for name, body in self.REQUESTS.items():
            with self.subTest(case=name):
                self.assertIsNone(automated_noise_reason([mail(body=body)]))

    def test_a_human_sender_is_never_noise_even_in_the_promotions_tab(self):
        # Gmail a veces manda consultas reales a Promociones: sin otra señal, no se descartan.
        message = mail(body="¿Cuánto cuesta una batimetría?", labels=["INBOX", "CATEGORY_PROMOTIONS"])
        self.assertIsNone(automated_noise_reason([message]))


class AutomatedSignals(unittest.TestCase):
    def reason(self, **kwargs):
        return automated_noise_reason([mail(**kwargs)])

    def test_automated_sender_addresses(self):
        for sender in ("noreply@tienda.com", "No-Reply <no-reply@x.co>", "do-not-reply@x.co",
                       "donotreply@x.co", "notifications@x.co", "MAILER-DAEMON@x.co",
                       "postmaster@x.co", "noreply+abc123@x.co", "bounce-77@x.co"):
            with self.subTest(sender=sender):
                self.assertIsNotNone(self.reason(sender=sender))

    def test_similar_looking_human_addresses_are_not_flagged(self):
        for sender in ("Ana Reyes <ana.reyes@x.co>", "noel@x.co", "notario@x.co", "boun@x.co"):
            with self.subTest(sender=sender):
                self.assertIsNone(self.reason(sender=sender))

    def test_mailing_list_header(self):
        self.assertIn("lista", self.reason(List_Unsubscribe="<mailto:baja@x.co>"))

    def test_auto_submitted_and_bulk_precedence(self):
        self.assertIsNotNone(self.reason(Auto_Submitted="auto-replied"))
        self.assertIsNone(self.reason(Auto_Submitted="no"))
        for value in ("bulk", "list", "junk", "auto_reply"):
            with self.subTest(precedence=value):
                self.assertIsNotNone(self.reason(Precedence=value))
        self.assertIsNone(self.reason(Precedence="normal"))

    def test_gmail_spam_label_is_enough(self):
        self.assertIn("spam", self.reason(labels=["SPAM"]).lower())

    def test_promotions_label_alone_is_not_enough_but_with_another_signal_it_is(self):
        self.assertIsNone(self.reason(labels=["INBOX", "CATEGORY_PROMOTIONS"]))
        self.assertIsNotNone(self.reason(labels=["INBOX", "CATEGORY_PROMOTIONS"],
                                         List_Unsubscribe="<x>"))

    def test_reason_is_generic_and_never_contains_the_address(self):
        reason = self.reason(sender="secreto@tienda.com", List_Unsubscribe="<x>")
        self.assertNotIn("secreto", reason)
        self.assertNotIn("@", reason)

    def test_thread_is_noise_only_if_every_message_is_automated(self):
        automated = mail(sender="noreply@x.co")
        human = mail(sender="Ana <ana@x.co>", body="Respondo con los datos que pidieron.")
        self.assertIsNotNone(automated_noise_reason([automated, automated]))
        self.assertIsNone(automated_noise_reason([automated, human]))

    def test_empty_thread_is_not_noise(self):
        self.assertIsNone(automated_noise_reason([]))


class CommercialTermsRescue(unittest.TestCase):
    def test_automated_message_mentioning_a_quote_is_rescued(self):
        message = mail(sender="noreply@portal.co", subject="Solicitud de cotización #88",
                       body="Se registró una solicitud.")
        self.assertTrue(mentions_commercial_terms([message], TERMS))

    def test_terms_match_without_accents_and_case(self):
        self.assertTrue(mentions_commercial_terms([mail(body="COTIZACION urgente")], TERMS))

    def test_attachment_names_and_text_count(self):
        attachments = [{"filename": "propuesta.pdf", "text": ""}]
        self.assertTrue(mentions_commercial_terms([mail(body="Adjunto.")], TERMS, attachments))

    def test_no_terms_means_nothing_to_rescue_with(self):
        self.assertFalse(mentions_commercial_terms([mail(body="cotización")], []))
        self.assertFalse(mentions_commercial_terms([mail(body="Descuentos de oficina")], TERMS))


if __name__ == "__main__":
    unittest.main()
