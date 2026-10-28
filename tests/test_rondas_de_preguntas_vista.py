"""T-61 (Mejora 2): la pantalla del caso muestra las rondas de preguntas enviadas y sus respuestas, solo con lo que ya
guarda el expediente (`rondas_de_preguntas`), sin emparejar preguntas con respuestas ni escribir nada.

Cubre: T-61
"""

from tests import _isolate  # noqa: F401  (debe ir primero)

import json
import re
import unittest
from unittest.mock import patch

from anka.dominio.preguntas import historial_de_rondas
from anka.infra import store
from tests.test_app import test_config
from tests.test_continuidad_de_casos import Contexto
from tests.test_openapi import request

ENVIADO = "1a15911c4b7e3fb7"
RESPUESTA = "1a1591dbcb668de4"
TEXTOS = ["¿Puede ratificar el área de 6,5 ha y enviar el KMZ?", "¿Qué sistema de referencia se usará?",
          "¿Qué formatos de entrega necesita?"]


def ronda(en, mensaje, estados=("enviada",) * 3, respuesta=None, textos=TEXTOS):
    return {"en": en, "mensaje": mensaje, "esperada": True, "respuesta": respuesta,
            "preguntas": [{"texto": t, "estado": e} for t, e in zip(textos, estados, strict=False)]}


CON_RESPUESTA = ronda("2026-10-19T16:20:00-05:00", ENVIADO, ("sin_confirmar",) * 3,
                      {"mensaje": RESPUESTA, "en": "2026-10-19T16:41:00-05:00"})


class VistaBase(Contexto):
    def guardar(self, **claves):
        with store.transaccion() as db:
            analisis = json.loads(db.execute("SELECT analysis_json FROM quotes WHERE thread_id='t-1'").fetchone()[0])
            analisis.update(claves)
            db.execute("UPDATE quotes SET analysis_json=? WHERE thread_id='t-1'", (json.dumps(analisis),))

    def pagina(self):
        with patch("anka.web.paginas.load_config", return_value=test_config()), \
                patch("anka.web.pantalla_caso.load_config", return_value=test_config()):
            respuesta = request("GET", "/case?thread_id=t-1")
        self.assertEqual(respuesta["status"], 200)
        return respuesta["body"].decode("utf-8")

    def seccion(self):
        html = self.pagina()
        if 'id="rondas"' not in html:
            return ""
        inicio = html.index('<details class="more rondas"')
        return html[inicio:html.index("</details>", inicio)]

    def texto(self, html):
        return " ".join(re.sub(r"<[^>]+>", " ", html).split())


class Rondas(VistaBase):
    def test_sin_rondas_ni_preguntas_enviadas_no_hay_seccion(self):
        self.guardar(rondas_de_preguntas=[], preguntas_enviadas={})
        self.assertEqual(self.seccion(), "")
        self.assertEqual(historial_de_rondas({}), [])

    def test_una_ronda_sin_respuesta(self):
        self.guardar(rondas_de_preguntas=[ronda("2026-10-19T16:20:00-05:00", ENVIADO)])
        html = self.seccion()
        texto = self.texto(html)
        self.assertIn("1 ronda)", texto)
        self.assertIn("Pregunta enviada el 2026-10-19 16:20", texto)
        self.assertIn(f"https://mail.google.com/mail/u/0/#all/{ENVIADO}", html)
        self.assertIn("Sin respuesta registrada todavía", texto)
        self.assertNotIn("Respuesta recibida", texto)
        self.assertEqual(html.count('class="estado-pregunta enviada"'), 3)

    def test_una_ronda_con_respuesta_y_tres_preguntas_sin_confirmar(self):
        self.guardar(rondas_de_preguntas=[CON_RESPUESTA])
        html = self.seccion()
        texto = self.texto(html)
        self.assertIn("Respuesta recibida en la ronda el 2026-10-19 16:41", texto)
        self.assertIn(f"#all/{RESPUESTA}", html)
        self.assertEqual(html.count("Sin confirmar</span>"), 3)
        self.assertIn("no es lo mismo que resuelta", texto)
        self.assertNotRegex(texto, r"(?i)respondida")
        self.assertIn("no relaciona cada respuesta con cada pregunta", texto)
        self.assertIn(" open", html.split(">")[0])

    def test_varias_rondas_se_conservan_en_orden_cronologico(self):
        self.guardar(rondas_de_preguntas=[
            ronda("2026-10-21T10:00:00-05:00", "1a15911c4b7e3fb8", textos=["Tercera"]),
            CON_RESPUESTA,
            ronda("2026-10-18T09:00:00-05:00", "1a15911c4b7e3fb6", ("sigue_abierta",) * 3,
                  {"mensaje": "1a15911c4b7e3fb9", "en": "2026-10-18T12:00:00-05:00"})])
        texto = self.texto(self.seccion())
        self.assertIn("3 rondas", texto)
        self.assertLess(texto.index("2026-10-18 09:00"), texto.index("2026-10-19 16:20"))
        self.assertLess(texto.index("2026-10-19 16:20"), texto.index("2026-10-21 10:00"))
        self.assertEqual([r["numero"] for r in historial_de_rondas(self.analisis())], [1, 2, 3])

    def test_sigue_abierta_y_sin_confirmar_se_distinguen(self):
        self.guardar(rondas_de_preguntas=[ronda("2026-10-19T16:20:00-05:00", ENVIADO,
                                               ("sigue_abierta", "sin_confirmar", "enviada"), {"mensaje": RESPUESTA})])
        html = self.seccion()
        self.assertEqual((html.count("estado-pregunta abierta"), html.count("estado-pregunta sin-confirmar"),
                          html.count("estado-pregunta enviada")), (1, 1, 1))
        self.assertIn("Sigue abierta", html)
        self.assertIn("Respuesta recibida en la ronda (sin fecha registrada)", self.texto(html))


class Historicos(VistaBase):
    def test_un_expediente_con_solo_preguntas_enviadas_es_una_ronda_historica(self):
        self.guardar(rondas_de_preguntas=[], preguntas_enviadas={"en": "2026-09-01T10:00:00-05:00", "mensaje": ENVIADO,
                                                                "preguntas": TEXTOS[:2]})
        texto = self.texto(self.seccion())
        self.assertIn("Registro anterior", texto)
        self.assertIn("no se sabe si el cliente respondió", texto)
        self.assertNotIn("Sin respuesta registrada todavía", texto)

    def test_datos_incompletos_no_rompen_ni_inventan(self):
        (r,) = historial_de_rondas({"rondas_de_preguntas": [{"preguntas": [{"texto": "A"}, "B", {"estado": "rara"}]}, "x"]})
        self.assertEqual((r["enviada_en"], r["mensaje_id"], r["con_respuesta"]), ("", "", False))
        self.assertEqual([p["clase"] for p in r["preguntas"]], ["desconocido"] * 3)
        self.assertEqual(r["preguntas"][2]["titulo"], "rara")
        self.guardar(rondas_de_preguntas=[{"preguntas": []}])
        texto = self.texto(self.seccion())
        self.assertIn("sin fecha registrada", texto)
        self.assertIn("Sin identificador del mensaje", texto)
        self.assertIn("no guardó el texto de las preguntas", texto)

    def test_identificadores_incorrectos_no_son_enlaces(self):
        self.guardar(rondas_de_preguntas=[ronda("2026-10-19T16:20:00-05:00", '"><img src=x>',
                                               respuesta={"mensaje": "<CAB@mail.gmail.com>"})])
        html = self.seccion()
        self.assertNotIn("mail.google.com", html)
        self.assertNotIn("<img", html)
        self.assertIn("&lt;img src=x&gt;", html)
        self.assertIn("no parece un identificador de Gmail", html)

    def test_texto_hostil_en_preguntas_se_escapa(self):
        self.guardar(rondas_de_preguntas=[ronda("2026-10-19T16:20:00-05:00", ENVIADO,
                                               textos=["<script>alert(1)</script>"])])
        html = self.seccion()
        self.assertNotIn("<script>", html)
        self.assertIn("&lt;script&gt;", html)


class SinEscrituras(VistaBase):
    def test_consultar_la_pagina_no_cambia_el_expediente(self):
        self.guardar(rondas_de_preguntas=[CON_RESPUESTA])

        def estado():
            with store.transaccion() as db:
                return (db.execute("SELECT analysis_json, status FROM quotes WHERE thread_id='t-1'").fetchone()[:],
                        db.execute("SELECT COUNT(*) FROM case_events").fetchone()[0],
                        db.execute("SELECT COUNT(*) FROM table_versions").fetchone()[0])
        antes = estado()
        self.pagina()
        self.pagina()
        self.assertEqual(estado(), antes)

    def test_la_vista_anterior_sigue_ahi_con_un_expediente_que_preguntaba(self):
        self.guardar(rondas_de_preguntas=[CON_RESPUESTA],
                     preguntas_enviadas={"en": "2026-10-19T16:20:00-05:00", "mensaje": ENVIADO, "preguntas": TEXTOS})
        html = self.pagina()
        self.assertIn('id="respuesta"', html)
        self.assertIn("Preguntas enviadas al cliente", html)


if __name__ == "__main__":
    unittest.main()
