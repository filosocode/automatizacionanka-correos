"""Preguntas al cliente agrupadas por el insumo que las resuelve (U-114)."""

from tests import _isolate  # noqa: F401  (debe ir primero)

import unittest

from anka.dominio.insumos import PREGUNTA_DE_PLAZO, SALIDA_UBICACION, agrupar_preguntas, cubierta_en_el_correo
from anka.dominio.preguntas import PREFIJO_UBICACION
from anka.dominio.reception import correo_con_preguntas
from anka.web.correo_marcado import correo_de_preguntas_marcadas
from anka.web.views import casillas_del_correo
from tests.test_pantalla_por_pasos import Base

NOMBRES = ["Levantamiento topográfico", "Batimetría", "Vuelo con dron"]
ACTIVIDADES = [{"activity": n, "estado": "pendiente"} for n in NOMBRES]
UBICACION = [f"{n}: el archivo KMZ de la ubicación." for n in NOMBRES]


class Agrupar(unittest.TestCase):
    def test_tres_pedidos_de_ubicacion_son_una_linea_con_salida(self) -> None:
        # Cubre: U-114
        resultado = agrupar_preguntas([*UBICACION, "¿Cuál es el área?"], ACTIVIDADES)
        self.assertEqual(len(resultado), 2)
        self.assertTrue(resultado[0].startswith(PREFIJO_UBICACION))
        for nombre in NOMBRES:
            self.assertIn(nombre, resultado[0])
        self.assertIn(SALIDA_UBICACION, resultado[0])
        self.assertEqual(resultado[1], "¿Cuál es el área?")

    def test_una_sola_ubicacion_no_se_toca(self) -> None:
        # Cubre: U-114
        self.assertEqual(agrupar_preguntas(UBICACION[:1], ACTIVIDADES), UBICACION[:1])

    def test_el_plazo_de_varias_actividades_es_una_pregunta_del_proyecto(self) -> None:
        # Cubre: U-114
        filas = [f"{n}: el plazo." for n in NOMBRES]
        resultado = agrupar_preguntas([*filas, "¿Cuál es el plazo del proyecto?"], ACTIVIDADES)
        self.assertEqual(resultado, [PREGUNTA_DE_PLAZO])

    def test_la_magnitud_general_sobra_si_cada_actividad_pregunta_la_suya(self) -> None:
        # Cubre: U-114
        filas = [f"{n}: la cantidad." for n in NOMBRES]
        actividades = [{**a, "cantidad": "", "unidad": ""} for a in ACTIVIDADES]
        resultado = agrupar_preguntas(["¿Cuál es la cantidad total?", *filas], actividades)
        self.assertEqual(resultado, filas)

    def test_la_magnitud_general_se_queda_si_una_actividad_no_pregunta_la_suya(self) -> None:
        # Cubre: U-114
        filas = [f"{n}: la cantidad." for n in NOMBRES[:2]]
        actividades = [{**a, "cantidad": "", "unidad": ""} for a in ACTIVIDADES]
        general = "¿Cuál es la cantidad total?"
        self.assertIn(general, agrupar_preguntas([general, *filas], actividades))

    def test_las_contradictorias_nunca_se_agrupan(self) -> None:
        # Cubre: U-114
        actividades = [{**a, "estado": "contradictorio"} for a in ACTIVIDADES]
        self.assertEqual(agrupar_preguntas(UBICACION, actividades), UBICACION)

    def test_el_anexo_va_primero(self) -> None:
        # Cubre: U-114
        anexo = "Por favor envíe el Anexo 2 con las especificaciones."
        self.assertEqual(agrupar_preguntas(["¿Cuál es el área?", anexo], ACTIVIDADES)[0], anexo)

    def test_sin_actividades_nombradas_solo_se_quita_lo_repetido(self) -> None:
        # Cubre: U-114
        general = "¿Nos envía el archivo KMZ del predio?"
        self.assertEqual(agrupar_preguntas([general, "¿Cuál es el área?"], ACTIVIDADES), [general, "¿Cuál es el área?"])


class CubiertaEnElCorreo(unittest.TestCase):
    def test_la_pregunta_suelta_o_dentro_del_grupo_cuenta_como_hecha(self) -> None:
        # Cubre: U-114
        grupo = agrupar_preguntas(UBICACION, ACTIVIDADES)
        for pregunta in UBICACION:
            self.assertTrue(cubierta_en_el_correo(pregunta, ACTIVIDADES, grupo))
            self.assertFalse(cubierta_en_el_correo(pregunta, ACTIVIDADES, []))
            self.assertTrue(cubierta_en_el_correo(pregunta, ACTIVIDADES, [pregunta]))

    def test_el_grupo_de_plazo_cubre_las_filas_de_plazo(self) -> None:
        # Cubre: U-114
        self.assertTrue(cubierta_en_el_correo("Batimetría: el plazo.", ACTIVIDADES, [PREGUNTA_DE_PLAZO]))


class EnElCorreo(unittest.TestCase):
    def test_el_correo_sale_con_las_preguntas_agrupadas(self) -> None:
        # Cubre: U-114
        correo = correo_con_preguntas(UBICACION, actividades=ACTIVIDADES)
        numeradas = [linea for linea in correo.splitlines() if linea[:3] in ("1. ", "2. ", "3. ")]
        self.assertEqual(len(numeradas), 1)
        self.assertIn(PREFIJO_UBICACION, numeradas[0])


class Servicio(Base):
    def test_el_servicio_devuelve_lineas_conocidas_y_grupos(self) -> None:
        # Cubre: U-114
        self.crear(actividades=ACTIVIDADES)
        resultado = correo_de_preguntas_marcadas("t-1", UBICACION, UBICACION)
        self.assertIsNotNone(resultado)
        self.assertEqual(len(resultado["lineas"]), 1)
        self.assertEqual(len(resultado["conocidas"]), 3)
        self.assertIn(PREFIJO_UBICACION, resultado["grupos"])
        self.assertIn(PREGUNTA_DE_PLAZO, resultado["grupos"])

    def test_las_casillas_quedan_marcadas_cuando_el_grupo_las_cubre(self) -> None:
        # Cubre: U-114
        actividades = [{**a, "ubicacion": "", "notes": ""} for a in ACTIVIDADES]
        cuerpo = "Hola,\n\n1. " + agrupar_preguntas(UBICACION, ACTIVIDADES)[0] + "\n\nGracias."
        casillas = casillas_del_correo(actividades, cuerpo)
        self.assertEqual(len(casillas), 3)


if __name__ == "__main__":
    unittest.main()
