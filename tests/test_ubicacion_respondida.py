"""El archivo de ubicación adjunto en la respuesta cierra su pregunta (U-97).

Cubre: U-97
"""

from tests import _isolate  # noqa: F401  (debe ir primero)

import unittest

from anka.dominio.preguntas import anotar_envio, conservar_rondas

PREGUNTAS = ["¿Podría enviar el archivo de ubicación del proyecto? (KML/KMZ o plano del trazado)",
             "¿Podría precisar la extensión o área total a levantar? (ha o km)",
             "¿Podría suministrar el documento Anexo Capitulo_1_Topografia?"]


def estados(adjuntos, al_enviar=()):
    anterior = {"attachments": list(al_enviar)}
    anotar_envio(anterior, "2026-11-05T10:00:00-05:00", "aaaaaa000001", PREGUNTAS, True)
    nuevo = {"received_at": "2026-11-05T11:00:00-05:00", "attachments": adjuntos}
    conservar_rondas(anterior, nuevo, "bbbbbb000002")
    return [p["estado"] for p in nuevo["rondas_de_preguntas"][0]["preguntas"]]


def kmz(**datos):
    return {"filename": "Calle153.kmz", "source_message_id": "bbbbbb000002", "status": "extraido"} | datos


# Cubre: U-97
class ArchivoDeUbicacion(unittest.TestCase):
    def test_el_kmz_leido_cierra_solo_la_pregunta_de_ubicacion(self):
        self.assertEqual(estados([kmz()]), ["resuelta", "sin_confirmar", "sin_confirmar"])

    def test_sin_adjunto_no_se_cierra_nada(self):
        self.assertEqual(estados([]), ["sin_confirmar"] * 3)

    def test_un_adjunto_que_no_se_pudo_leer_no_prueba_nada(self):
        self.assertEqual(estados([kmz(status="bloqueado")])[0], "sin_confirmar")

    def test_un_pdf_no_es_archivo_geografico(self):
        self.assertEqual(estados([kmz(filename="plano.pdf")])[0], "sin_confirmar")

    def test_el_kmz_que_llega_en_otro_correo_posterior_tambien_cierra_la_pregunta(self):
        # el cliente manda el archivo en un correo y el texto en otro: lo que cuenta es que no estaba al enviar
        self.assertEqual(estados([kmz(source_message_id="cccccc000003")])[0], "resuelta")

    def test_un_archivo_que_ya_estaba_al_enviar_no_cuenta(self):
        antes = kmz(source_message_id="aaaaaa000001")
        self.assertEqual(estados([antes], al_enviar=[antes])[0], "sin_confirmar")

    def test_una_ronda_antigua_sin_la_lista_solo_acepta_el_mensaje_de_respuesta(self):
        anterior = {}
        anotar_envio(anterior, "2026-11-05T10:00:00-05:00", "aaaaaa000001", PREGUNTAS, True)
        del anterior["rondas_de_preguntas"][0]["adjuntos_al_enviar"]
        nuevo = {"received_at": "2026-11-05T11:00:00-05:00",
                 "attachments": [kmz(source_message_id="cccccc000003")]}
        conservar_rondas(anterior, nuevo, "bbbbbb000002")
        self.assertEqual(nuevo["rondas_de_preguntas"][0]["preguntas"][0]["estado"], "sin_confirmar")


if __name__ == "__main__":
    unittest.main()
