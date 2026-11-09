"""Una nota de la tabla no llega al cliente como nota: se vuelve una pregunta (U-99); las celdas no son áreas (U-101).

Caso real (La Calera, 2026-11-09): el correo salió con «No hay una cifra explícita de longitud declarada en el texto.
Fuente revisada: PDF y KMZ.» y el PDF, que pide «celdas de 1 m²», se leyó como si el proyecto midiera 1 m².
"""

from tests import _isolate  # noqa: F401  (debe ir primero)

import unittest

from anka.dominio.areas_declaradas import areas_declaradas, diferencias_de_area
from anka.dominio.preguntas import como_peticion, sin_nota_interna
from anka.dominio.reception import correo_con_preguntas, preguntas_de_pendientes

LONGITUD = "No hay una cifra explícita de longitud declarada en el texto. Fuente revisada: PDF y KMZ."


# Cubre: U-99
class NotasQueSeVuelvenPreguntas(unittest.TestCase):
    def test_lo_que_falta_se_pide_con_una_pregunta(self):
        self.assertEqual(
            como_peticion("Falta la cantidad/superficie explícita de las manzanas a levantar en Bogotá y La Calera"),
            "¿Podría indicar la cantidad/superficie explícita de las manzanas a levantar en Bogotá y La Calera?")
        self.assertEqual(como_peticion("Falta también la cifra explícita de superficie en texto."),
                         "¿Podría indicar la cifra explícita de superficie?")

    def test_no_hay_se_vuelve_pregunta_sin_la_fuente_ni_declarada_en_el_texto(self):
        self.assertEqual(como_peticion(LONGITUD), "¿Podría indicar la cifra explícita de longitud?")
        self.assertEqual(como_peticion("No hay un plano del predio."), "¿Podría indicar el plano del predio?")

    def test_las_notas_internas_nunca_salen(self):
        for texto in ("Falta la delimitación exacta. Fuente revisada: correo. Paso siguiente: precisar polígonos",
                      "Falta la delimitación exacta. Fuentes revisadas: correo y PDF. Siguiente paso: llamar",
                      "Falta la delimitación exacta.\nFuente  revisada: correo"):
            with self.subTest(texto=texto):
                self.assertEqual(como_peticion(texto), "¿Podría indicar la delimitación exacta?")
        self.assertEqual(sin_nota_interna("Algo. Paso siguiente: x"), "Algo.")

    def test_una_pregunta_de_verdad_queda_igual_salvo_la_nota(self):
        self.assertEqual(como_peticion("¿Podría adjuntar el Anexo? Fuente revisada: PDF"), "¿Podría adjuntar el Anexo?")
        self.assertEqual(como_peticion("¿Cuál es el área del lote?"), "¿Cuál es el área del lote?")

    def test_lo_que_no_encaja_queda_como_se_escribio(self):
        self.assertEqual(como_peticion("Se dispone de la ubicación del trazado"), "Se dispone de la ubicación del trazado")
        self.assertEqual(como_peticion("Resolver la ubicación del lote"), "La ubicación del lote")   # U-33 sigue igual

    def test_es_idempotente(self):
        for texto in (LONGITUD, "Falta la cantidad de pozos", "Resolver el datum"):
            self.assertEqual(como_peticion(como_peticion(texto)), como_peticion(texto))

    def test_el_correo_al_cliente_lleva_preguntas_y_ninguna_nota(self):
        cuerpo = correo_con_preguntas(["Falta la cantidad de las manzanas a levantar", LONGITUD,
                                       "Falta también la cifra explícita de superficie en texto."])
        self.assertNotIn("Fuente revisada", cuerpo)
        self.assertNotIn("Falta", cuerpo)
        self.assertNotIn("en texto", cuerpo)
        self.assertIn("1. ¿Podría indicar la cantidad de las manzanas a levantar?", cuerpo)
        self.assertIn("2. ¿Podría indicar la cifra explícita de longitud?", cuerpo)
        self.assertIn("3. ¿Podría indicar la cifra explícita de superficie?", cuerpo)

    def test_el_correo_con_codigo_tambien(self):
        cuerpo = correo_con_preguntas([LONGITUD], proyecto="P", codigo="T26001")
        self.assertNotIn("Fuente revisada", cuerpo)
        self.assertIn("1. ¿Podría indicar la cifra explícita de longitud?", cuerpo)

    def test_las_sugerencias_de_que_falta_tampoco_filtran_la_nota(self):
        analisis = {"activities": [
            {"activity": "Levantamiento de detalles", "estado": "pendiente",
             "notes": "Falta la cantidad de manzanas. Fuente revisada: PDF. Paso siguiente: precisar polígonos."}]}
        preguntas = preguntas_de_pendientes(analisis)
        self.assertEqual(preguntas, ["Sobre «Levantamiento de detalles»: ¿puede indicar la cantidad de manzanas?"])


# Cubre: U-101
class MedidasQueNoSonElAreaDelProyecto(unittest.TestCase):
    def test_una_celda_de_1_m2_no_es_el_area_del_proyecto(self):
        texto = "Se requiere que al menos un 95% de las celdas de 1m² en tierra, contengan mínimo un (1) punto LIDAR"
        self.assertEqual(areas_declaradas(texto), [])

    def test_resolucion_gsd_y_puntos_por_m2_tampoco(self):
        for texto in ("GSD de 4 cm y una resolución de 0,25 m²", "densidad de 8 puntos por 1 m²",
                      "cuadrículas de 25 m² para la nube", "2 pixeles de 1 m2 cada uno"):
            with self.subTest(texto=texto):
                self.assertEqual(areas_declaradas(texto), [])

    def test_una_medida_de_menos_de_100_m2_no_cuenta(self):
        self.assertEqual(areas_declaradas("La caseta ocupa 40 m²."), [])

    def test_un_area_de_proyecto_si_cuenta(self):
        self.assertEqual([e for e, _ in areas_declaradas("El predio tiene 12 ha en total.")], ["12 ha"])
        self.assertEqual([e for e, _ in areas_declaradas("El lote es de 500 m².")], ["500 m²"])
        self.assertEqual([e for e, _ in areas_declaradas("Intervenir 136,4 ha de la franja")], ["136,4 ha"])

    def test_el_caso_real_ya_no_inventa_una_diferencia(self):
        pdf = ("1.9 Se requiere que al menos un 95% de las celdas de 1m² en tierra, contengan mínimo un (1) punto LIDAR. "
               "La resolución espacial de cada fotografía deberá ser un GSD ≤ 4 cm.")
        poligono = [{"archivo": "Calle153.kmz", "poligono": "0", "area_m2": 1364063.1, "area_ha": 136.4063,
                     "perimetro_m": 13951.7, "anillos_interiores": 0}]
        self.assertEqual(diferencias_de_area(pdf, poligono), [])


if __name__ == "__main__":
    unittest.main()
