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
    def test_una_pregunta_general_del_mismo_dato_no_cuenta_pero_se_señala(self) -> None:
        # E-109c: «el archivo de ubicación» no nombra la actividad (D-205): se avisa, pero diciendo qué pregunta
        # probablemente la cubre; las que nada cubre van primero
        cuerpo = correo("¿Podrían remitir el archivo de ubicación del proyecto? (KML/KMZ)")
        sin = actividades_sin_nombrar(FILAS, cuerpo)
        self.assertEqual([(s["actividad"], s["cubierta_por"]) for s in sin],
                         [("Redes húmedas", None), ("Control vertical", 1), ("Levantamiento LiDAR", 1)])

    # Cubre: U-109
    def test_una_general_de_otro_tema_o_que_nombra_otra_actividad_no_cubre(self) -> None:
        # E-109i: «plazo» no cubre una falta de ubicación; un punto que nombra «Control vertical» no es general
        cuerpo = correo("¿Cuál es el plazo de entrega?", "Control vertical: la ubicación.")
        sin = {s["actividad"]: s["cubierta_por"] for s in actividades_sin_nombrar(FILAS, cuerpo)}
        self.assertEqual(sin, {"Levantamiento LiDAR": None, "Redes húmedas": None})

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
    def test_el_aviso_dice_que_pregunta_puede_cubrir_la_fila(self) -> None:
        # E-109i: con una pregunta general de ubicación, la fila lo dice en vez de parecer un faltante sin más
        pagina = self.pantalla("¿Podrían remitir el archivo de ubicación del proyecto? (KML/KMZ)")
        aviso = pagina[pagina.index('id="correo-cobertura"'):]
        aviso = aviso[:aviso.index("</div>")]
        self.assertEqual(aviso.count("puede cubrirla la pregunta 1"), 2)
        self.assertEqual(aviso.count(">Agregar la pregunta<"), 2)                # sigue pudiendo agregarla

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


class DocumentosAlRehacerElCorreo(Base):
    UBICACION = "Archivo de ubicación y delimitación del área"
    PLANOS = "Planos, diseños preliminares, cartografía base o levantamientos previos"

    def cuerpo(self, *preguntas: str) -> str:
        self.crear(estado=QuoteStatus.REVIEW_PENDING, actividades=[pendiente("Control vertical")])
        pagina = pantalla_caso.render_case("t-1", template="preguntas", preguntas_texto="\n".join(preguntas))
        return pagina.split('id="cuerpo-correo"')[1].split("</textarea>")[0]

    # Cubre: U-100
    def test_rehacer_el_correo_conserva_la_lista_de_documentos_opcionales(self) -> None:
        # defecto del caso La Calera: «Agregar la pregunta» y «Preparar el correo de nuevo» perdían el bloque
        cuerpo = self.cuerpo("Control vertical: la longitud total.")
        self.assertIn("también puede adjuntar", cuerpo)
        self.assertIn(self.UBICACION, cuerpo)
        self.assertIn(self.PLANOS, cuerpo)

    # Cubre: U-100
    def test_sigue_sin_repetir_el_archivo_de_ubicacion_si_una_pregunta_ya_lo_pide(self) -> None:
        cuerpo = self.cuerpo("¿Podrían remitir el archivo de ubicación del proyecto? (KML/KMZ)")
        self.assertNotIn(self.UBICACION, cuerpo)
        self.assertIn(self.PLANOS, cuerpo)


if __name__ == "__main__":
    unittest.main()
