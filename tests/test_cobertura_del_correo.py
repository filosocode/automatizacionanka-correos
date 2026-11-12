"""El correo avisa de las actividades por completar que no nombra (U-109, D-205)."""
from tests import _isolate  # noqa: F401  (debe ir primero)

import re
import unittest

from anka.dominio.reception import correo_con_preguntas
from anka.dominio.status import QuoteStatus
from anka.web import pantalla_caso
from anka.web.views import actividades_sin_nombrar
from tests.test_pantalla_por_pasos import Base, actividad


def pendiente(nombre: str) -> dict:
    return actividad(nombre, "pendiente") | {"ubicacion": "no indicado", "notes": ""}


def correo(*puntos: str) -> str:
    return correo_con_preguntas(list(puntos), proyecto="Proyecto Norte")


FILAS = [pendiente("Control vertical"), pendiente("Levantamiento LiDAR"), actividad("Batimetría", "confirmado"),
         actividad("Redes húmedas", "contradictorio")]


class QuéActividadesNoNombra(unittest.TestCase):
    # Cubre: U-109
    def test_lista_las_pendientes_que_ningun_punto_nombra(self) -> None:
        # E-109a: dos pendientes, el correo nombra una; E-109f: la confirmada nunca se avisa
        cuerpo = correo("Para «Control vertical», ¿podría indicar la ubicación?", "¿Cuál es el datum?")
        sin = actividades_sin_nombrar(FILAS, cuerpo)
        self.assertEqual([s["actividad"] for s in sin], ["Levantamiento LiDAR", "Redes húmedas"])
        self.assertIn("Levantamiento LiDAR", sin[0]["pregunta"])
        self.assertEqual(sin[0]["preguntas"][-1], sin[0]["pregunta"])           # las del correo + la nueva
        self.assertEqual(len(sin[0]["preguntas"]), 3)

    # Cubre: U-109
    def test_la_contradictoria_se_muestra_marcada_para_que_la_decida_la_persona(self) -> None:
        # E-109h: el comercial debe saber que existe para decidir si pregunta al cliente o la resuelve él (R12)
        cuerpo = correo("Control vertical: la ubicación.", "Levantamiento LiDAR: la ubicación.")
        sin = actividades_sin_nombrar(FILAS, cuerpo)
        self.assertEqual([(s["actividad"], s["contradictoria"]) for s in sin], [("Redes húmedas", True)])
        self.assertIn("Redes húmedas", sin[0]["pregunta"])                      # la opción de preguntarla existe

    # Cubre: U-109
    def test_con_todas_nombradas_no_avisa_ni_distingue_tildes_o_mayusculas(self) -> None:
        # E-109b
        cuerpo = correo("CONTROL VERTICAL: la ubicación.", "Levantamiento lidar: la ubicacion.",
                        "Redes húmedas: ¿cuál es el dato correcto?")
        self.assertEqual(actividades_sin_nombrar(FILAS, cuerpo), [])

    # Cubre: U-109
    def test_una_pregunta_general_del_mismo_dato_no_cuenta(self) -> None:
        # E-109c: «el archivo de ubicación» no nombra la actividad (D-205): se avisa con humildad
        cuerpo = correo("¿Podrían remitir el archivo de ubicación del proyecto? (KML/KMZ)")
        self.assertEqual([s["actividad"] for s in actividades_sin_nombrar(FILAS, cuerpo)],
                         ["Control vertical", "Levantamiento LiDAR", "Redes húmedas"])

    # Cubre: U-109
    def test_sin_correo_con_preguntas_no_hay_nada_que_cubrir(self) -> None:
        self.assertEqual(actividades_sin_nombrar(FILAS, ""), [])
        self.assertEqual(actividades_sin_nombrar(FILAS, "Hola, gracias por escribirnos."), [])


class AvisoEnLaPantalla(Base):
    def pantalla(self, *puntos: str) -> str:
        self.crear(estado=QuoteStatus.REVIEW_PENDING, actividades=[pendiente("Control vertical"),
                                                                    pendiente("Levantamiento LiDAR")])
        return pantalla_caso.render_case("t-1", template="preguntas", preguntas_texto="\n".join(puntos))

    # Cubre: U-109
    def test_el_aviso_lista_lo_que_no_nombra_y_el_envio_sigue_disponible(self) -> None:
        # E-109a, E-109d y E-109g
        pagina = self.pantalla("Control vertical: la ubicación.")
        aviso = pagina[pagina.index('id="correo-cobertura"'):]
        aviso = aviso[:aviso.index("</div>")]
        self.assertIn("Este correo no nombra", aviso)
        self.assertIn("Levantamiento LiDAR", aviso)
        self.assertNotIn(">Control vertical<", aviso)
        self.assertIn(">Agregar la pregunta<", aviso)
        self.assertIn("data-confirm=", aviso)                                    # E-109e: confirma antes de reemplazar
        self.assertIn('name="template" value="preguntas"', aviso)
        enviadas = re.search(r'name="preguntas" value="([^"]*)"', aviso).group(1)
        self.assertIn("Control vertical", enviadas)                              # conserva las que ya tenía
        self.assertIn("Levantamiento LiDAR", enviadas)                           # y suma la de la fila
        self.assertEqual(pagina.count('formaction="/send"'), 1)                  # no bloquea el envío

    # Cubre: U-109
    def test_con_todas_nombradas_no_se_muestra_el_aviso(self) -> None:
        # E-109b
        pagina = self.pantalla("Control vertical: la ubicación.", "Levantamiento LiDAR: la ubicación.")
        self.assertNotIn('id="correo-cobertura"', pagina)

    # Cubre: U-109
    def test_agregar_la_pregunta_rehace_el_correo_sin_duplicar(self) -> None:
        # E-109d: el enlace del aviso vuelve a armar el correo con las del correo más la de la fila
        pagina = self.pantalla("Control vertical: la ubicación.")
        enviadas = re.search(r'name="preguntas" value="([^"]*)"', pagina).group(1).replace("&#10;", "\n")
        rehecho = pantalla_caso.render_case("t-1", template="preguntas", preguntas_texto=enviadas)
        cuerpo = rehecho.split('id="cuerpo-correo"')[1].split("</textarea>")[0]
        self.assertEqual(cuerpo.count("Control vertical"), 1)
        self.assertIn("Levantamiento LiDAR", cuerpo)
        self.assertNotIn('id="correo-cobertura"', rehecho)                       # ya no falta nada


if __name__ == "__main__":
    unittest.main()
