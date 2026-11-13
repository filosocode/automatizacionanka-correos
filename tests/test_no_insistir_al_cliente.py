"""No se le pide dos veces lo mismo al cliente, no sale el resumen de la pantalla como pregunta y el correo no
repite la ubicación (U-98, U-99, U-100).

Cubre: U-98, U-99, U-100
"""

from tests import _isolate  # noqa: F401  (debe ir primero)

import unittest

from anka.dominio.errores import ErrorDeValidacion
from anka.dominio.preguntas import (
    candidatas, decidir, preguntas_escritas, texto_de_pregunta, ya_pedida_el,
)
from anka.dominio.reception import correo_con_preguntas

ANEXO = "¿Podrían adjuntar el Anexo Capitulo_1_Topografia?"
GUIA = "¿Podrían suministrar la guía IDU GU-DP-02 “Elaboración de estudios topográficos”?"


def analisis(rondas, otras):
    return {"rondas_de_preguntas": rondas, "other_questions": otras, "question_audit": []}


def ronda(preguntas, en="2026-11-06T11:55:25-05:00"):
    return {"en": en, "mensaje": "m1", "respuesta": {"mensaje": "m2"},
            "preguntas": [{"id": "x", "texto": t, "estado": e} for t, e in preguntas]}


# Cubre: U-98
class YaSePidio(unittest.TestCase):
    def test_la_pregunta_reformulada_se_reconoce_como_la_ya_pedida(self):
        a = analisis([ronda([(ANEXO, "sin_confirmar"), (GUIA, "sin_confirmar")])],
                     ["¿Podría adjuntar el documento Anexo Capitulo_1_Topografia?"])
        self.assertEqual(ya_pedida_el(a["other_questions"][0], a), "2026-11-06 11:55")

    def test_una_pregunta_nueva_no_es_una_ya_pedida(self):
        a = analisis([ronda([(ANEXO, "sin_confirmar")])], [])
        self.assertEqual(ya_pedida_el("¿Cuál es el datum del levantamiento?", a), "")

    def test_una_pregunta_resuelta_no_cuenta_como_pendiente_de_respuesta(self):
        a = analisis([ronda([(ANEXO, "resuelta")])], [])
        self.assertEqual(ya_pedida_el(ANEXO, a), "")

    def test_sin_rondas_no_hay_nada_ya_pedido(self):
        self.assertEqual(ya_pedida_el(ANEXO, analisis([], [])), "")

    def test_solo_cuenta_la_ultima_ronda(self):
        a = analisis([ronda([(ANEXO, "sin_confirmar")], "2026-11-06T09:00:00-05:00"),
                      ronda([(GUIA, "sin_confirmar")])], [])
        self.assertEqual(ya_pedida_el(ANEXO, a), "")

    def test_las_candidatas_traen_cuando_se_pidieron(self):
        a = analisis([ronda([(ANEXO, "sin_confirmar")])], ["¿Podría adjuntar el Anexo Capitulo_1_Topografia?",
                                                          "¿Cuál es el datum?"])
        marcadas = {c["texto"][:20]: c.get("ya_pedida", "") for c in candidatas(a)}
        self.assertEqual(sorted(marcadas.values()), ["", "2026-11-06 11:55"])


# Cubre: U-99
class TextoDeLasPreguntas(unittest.TestCase):
    def test_el_resumen_pegado_se_limpia_y_queda_la_pregunta(self):
        for texto in ("Por resolver: Pendiente del cliente: ¿Podrían adjuntar el Anexo Capitulo_1_Topografia?",
                      "or resolver: Pendiente del cliente: ¿Podrían adjuntar el Anexo Capitulo_1_Topografia?",
                      "Pendiente del cliente: ¿Podrían adjuntar el Anexo Capitulo_1_Topografia?"):
            with self.subTest(texto=texto):
                self.assertEqual(preguntas_escritas(texto), ["¿Podrían adjuntar el Anexo Capitulo_1_Topografia?"])

    def test_el_resumen_con_dos_preguntas_queda_en_dos(self):
        self.assertEqual(preguntas_escritas(f"or resolver: Pendiente del cliente: {ANEXO}; {GUIA}"), [ANEXO, GUIA])

    def test_el_resumen_pegado_con_una_peticion_sin_signos_tambien_se_limpia(self):
        self.assertEqual(
            preguntas_escritas("Por resolver: Pendiente del cliente: favor adjuntar el Anexo (PDF o DOCX)."),
            ["Favor adjuntar el Anexo (PDF o DOCX)."])

    def test_un_resumen_sin_ninguna_peticion_se_rechaza(self):
        for texto in ("Faltan: 3 actividades por completar. Míralo en la tabla.", "Por resolver:",
                      "Por resolver: Faltan: 3 actividades por completar."):
            with self.subTest(texto=texto), self.assertRaises(ErrorDeValidacion):
                preguntas_escritas(texto)

    def test_una_pregunta_normal_pasa(self):
        self.assertEqual(texto_de_pregunta(ANEXO), ANEXO)
        self.assertEqual(preguntas_escritas(ANEXO), [ANEXO])

    def test_la_linea_con_varias_preguntas_se_separa_en_una_por_pregunta(self):
        self.assertEqual(preguntas_escritas(f"{ANEXO}; {GUIA}"), [ANEXO, GUIA])

    def test_las_lineas_vacias_y_repetidas_se_ignoran(self):
        self.assertEqual(preguntas_escritas(f"{ANEXO}\n\n{ANEXO}\n{GUIA}"), [ANEXO, GUIA])

    def test_una_pregunta_agregada_a_mano_tambien_se_limpia_o_se_rechaza(self):
        hechas = decidir([], {}, [f"Por resolver: Pendiente del cliente: {ANEXO}"], "dashboard", "2026-11-06")
        self.assertEqual([p["texto"] for p in hechas], [ANEXO])
        with self.assertRaises(ErrorDeValidacion):
            decidir([], {}, ["Faltan: 3 actividades por completar."], "dashboard", "2026-11-06")


# Cubre: U-100
class CorreoSinRepetir(unittest.TestCase):
    UBICACION = ("Archivo de ubicación y delimitación del área, preferiblemente KMZ/KML, polígono, coordenadas o "
                 "formato GIS disponible.")
    PLANOS = "Planos, diseños preliminares, cartografía base o levantamientos previos que debamos considerar."

    def test_si_una_pregunta_ya_pide_la_ubicacion_no_se_repite_en_la_lista(self):
        correo = correo_con_preguntas(["¿Podrían remitir el archivo de ubicación del proyecto? (KML/KMZ)"],
                                      documentos=[self.UBICACION, self.PLANOS])
        self.assertNotIn("Archivo de ubicación y delimitación", correo)
        self.assertIn(self.PLANOS, correo)

    def test_sin_pregunta_de_ubicacion_la_lista_la_conserva(self):
        correo = correo_con_preguntas([ANEXO], documentos=[self.UBICACION, self.PLANOS])
        self.assertIn("Archivo de ubicación y delimitación", correo)

    def test_si_solo_quedaba_la_ubicacion_no_queda_un_bloque_vacio(self):
        correo = correo_con_preguntas(["¿Podrían remitir el archivo de ubicación del proyecto?"],
                                      documentos=[self.UBICACION])
        self.assertNotIn("también puede adjuntar", correo)


# Cubre: U-98
class EnLaPantallaDelCaso(unittest.TestCase):
    PREGUNTA = "¿Podrían compartir el documento Anexo Capitulo_1_Topografia?"

    def setUp(self):
        import re
        import tempfile
        from pathlib import Path
        from unittest.mock import patch

        from anka.dominio.status import QuoteStatus
        from anka.infra import settings, store
        from tests.test_app import complete_analysis, test_config

        self.re = re
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        for parche in (patch.object(settings, "DATA_DIR", Path(self.temp.name)),
                       patch("anka.web.paginas.load_config", return_value=test_config()),
                       patch("anka.web.pantalla_caso.load_config", return_value=test_config())):
            parche.start()
            self.addCleanup(parche.stop)
        analisis = complete_analysis(location="")
        analisis["other_questions"] = [self.PREGUNTA]
        analisis["rondas_de_preguntas"] = [ronda([("¿Podrían adjuntar el Anexo Capitulo_1_Topografia?",
                                                   "sigue_abierta")])]
        db = store.open_db(Path(self.temp.name))
        db.execute("DELETE FROM quotes")
        store.save(db, "t-1", "m-1", QuoteStatus.REVIEW, "cliente@empresa.co", analisis)
        db.commit()
        db.close()

    def cuadro(self, pagina):
        from tests.cuadro_unico import cuadro_de
        return cuadro_de(pagina).strip() if 'id="correo-unico"' in pagina else None

    def test_lo_ya_pedido_se_avisa_y_no_se_pone_en_el_cuadro(self):
        from anka.web import pantalla_caso
        pagina = str(pantalla_caso.render_case("t-1"))
        self.assertIn("Esto ya se lo pedimos y no ha contestado", pagina)
        self.assertIn("volver-a-pedir", pagina)
        self.assertNotIn("Anexo Capitulo_1_Topografia", self.cuadro(pagina))

    def test_volver_a_pedirlo_llena_el_cuadro_y_marca_preguntar_al_cliente(self):
        from anka.web import pantalla_caso
        pagina = str(pantalla_caso.render_case("t-1", template="preguntas", preguntas_texto=self.PREGUNTA))
        self.assertIn(self.PREGUNTA.rstrip("?."), self.cuadro(pagina))          # la pregunta ya va en el correo
        self.assertIn('id="btn-enviar-preguntas"', pagina)


if __name__ == "__main__":
    unittest.main()
