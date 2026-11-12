"""U-112.A: la barra lateral de casos, el inicio guiado, el «siguiente caso» y el nombre de quien trabaja."""

from tests import _isolate  # noqa: F401  (debe ir primero)

import unittest
from unittest.mock import patch

from anka.dominio.errores import ErrorDeValidacion
from anka.dominio.status import QuoteStatus
from anka.infra import settings
from anka.servicios import quotes
from anka.servicios.actividad_manual import cambiar_actividad
from anka.servicios.comercial import nombre_del_comercial, poner_nombre
from anka.web import acciones, paginas, pantalla_caso
from tests.js_de_las_paginas import JS
from tests.test_app import test_config
from tests.test_pantalla_por_pasos import Base


class ConVariosCasos(Base):
    def setUp(self) -> None:
        super().setUp()
        self.crear("t-1", QuoteStatus.REVIEW, proyecto="Batimetría del embalse")
        self.crear("t-2", QuoteStatus.WAITING, proyecto="Ortofoto del lote")


# Cubre: U-112
class BarraTests(ConVariosCasos):
    def test_la_barra_esta_en_todas_las_paginas_con_los_casos_y_marca_el_abierto(self) -> None:
        for pagina in (paginas.render_index(), pantalla_caso.render_case("t-1")):
            self.assertIn('id="barra-casos"', pagina)
            self.assertIn("Batimetría del embalse", pagina)
            self.assertIn("Ortofoto del lote", pagina)
            self.assertIn('href="/"', pagina)
        abierto = pantalla_caso.render_case("t-1")
        barra = abierto[abierto.index('id="barra-casos"'):abierto.index('id="contenido"')]
        self.assertEqual(barra.count('aria-current="page"'), 1)
        self.assertRegex(barra, r'<a href="/case\?thread_id=t-1" aria-current="page">')

    # Cubre: U-112
    def test_el_caso_tiene_un_enlace_mas_que_lleva_al_historial(self) -> None:
        pagina = pantalla_caso.render_case("t-1")
        self.assertIn('id="enlace-mas" href="#p-hist"', pagina)
        self.assertIn('id="p-hist"', pagina)

    def test_cada_caso_lleva_su_punto_con_su_significado_para_lectores(self) -> None:
        barra = pantalla_caso.render_case("t-1")
        self.assertIn('class="punto ambar"', barra)           # t-1 espera a la persona
        self.assertIn('class="punto gris"', barra)            # t-2 espera al cliente
        self.assertIn("(te espera)", barra)
        self.assertIn("(espera al cliente)", barra)

    def test_dentro_de_un_caso_ofrece_el_siguiente_y_solo_si_hay_otro_que_espere(self) -> None:
        self.assertNotIn("Siguiente caso", pantalla_caso.render_case("t-1"))     # el otro espera al cliente
        self.crear("t-3", QuoteStatus.REVIEW, proyecto="Replanteo del predio")
        pagina = pantalla_caso.render_case("t-1")
        self.assertIn('class="barra-siguiente" href="/case?thread_id=t-3', pagina)
        self.assertNotIn("Siguiente caso", paginas.render_index())               # en Inicio está «Atender a»

    def test_por_debajo_de_900_px_se_abre_con_un_boton_y_sin_javascript_queda_a_la_vista(self) -> None:
        pagina = paginas.render_index()
        self.assertIn('id="abrir-barra"', pagina)
        self.assertIn('aria-controls="barra-casos" aria-expanded="false" hidden', pagina)
        self.assertIn("abrir-barra", JS)


# Cubre: U-112
class InicioTests(ConVariosCasos):
    def test_un_solo_boton_lleva_al_mas_urgente_y_las_tarjetas_dicen_una_frase(self) -> None:
        pagina = paginas.render_index()
        inicio = pagina[pagina.index('id="inicio-guiado"'):pagina.index('class="page-head"')]
        self.assertEqual(inicio.count('id="atender-primero"'), 1)
        self.assertRegex(inicio, r'<a class="button" id="atender-primero" href="/case\?thread_id=t-1[^"]*">Atender a ')
        self.assertIn("Hay 1 caso que te espera.", inicio)
        self.assertIn("<h3>Te esperan</h3>", inicio)
        self.assertIn("<h3>Esperando al cliente</h3>", inicio)
        self.assertIn("Ortofoto del lote", inicio)

    def test_sin_nada_que_atender_no_hay_boton(self) -> None:
        with patch("anka.web.barra_de_casos.cola_de_hoy", return_value=[]):
            pagina = paginas.render_index()
        self.assertIn("Nada espera por ti ahora mismo.", pagina)
        self.assertNotIn('id="atender-primero"', pagina)

    def test_saluda_por_el_nombre_o_lo_pide(self) -> None:
        sin_nombre = paginas.render_index()
        self.assertIn("<h2 id=\"inicio-titulo\">Hola</h2>", sin_nombre)
        self.assertIn('action="/nombre"', sin_nombre)
        with patch("anka.web.paginas.nombre_del_comercial", return_value="Ana"):
            con_nombre = paginas.render_index()
        self.assertIn("Hola, Ana", con_nombre)
        self.assertNotIn('id="inicio-nombre-campo"', con_nombre)


# Cubre: U-112
class NombreTests(Base):
    def setUp(self) -> None:
        super().setUp()
        settings.write_config(test_config())

    def test_se_guarda_en_la_configuracion_y_se_lee(self) -> None:
        self.assertEqual(nombre_del_comercial(), "")
        self.assertIn("Listo, Ana Pérez", poner_nombre("  Ana   Pérez "))
        self.assertEqual(nombre_del_comercial(), "Ana Pérez")
        self.assertEqual(settings.load_config()["comercial"], "Ana Pérez")

    def test_rechaza_un_nombre_vacio_o_larguisimo(self) -> None:
        for malo in ("", " a ", "x" * 61):
            with self.assertRaises(ErrorDeValidacion):
                poner_nombre(malo)
        self.assertEqual(nombre_del_comercial(), "")

    def test_el_formulario_lo_guarda_y_vuelve_al_inicio(self) -> None:
        destino = acciones.POST_ROUTES["/nombre"]({"nombre": ["Luis"]})
        self.assertTrue(destino.startswith("/?message="))
        self.assertEqual(nombre_del_comercial(), "Luis")

    def test_el_criterio_tecnico_exige_nombre_y_lo_deja_en_el_respaldo(self) -> None:
        self.crear("t-1", QuoteStatus.REVIEW, actividades=[
            {"activity": "Área", "estado": "contradictorio", "notes": "10 ha o 12 ha", "ubicacion": "Lote 1",
             "quantity": "2", "unit": "ha", "entregable": "Plano", "plazo_restriccion": "10 días",
             "servicio_tecnica": ""}])
        datos = {"origen": "tecnico", "decision": "Se mide con GNSS RTK", "nota": "Con ese relieve la precisión lo exige"}
        with self.assertRaises(ErrorDeValidacion) as error:
            cambiar_actividad("t-1", "resolver", 0, datos)
        self.assertIn("escribe tu nombre en Ajustes", str(error.exception))
        poner_nombre("Andrés")
        acciones.POST_ROUTES["/activity"]({"thread_id": ["t-1"], "accion": ["resolver"], "indice": ["0"],
                                           **{k: [v] for k, v in datos.items()}})
        notas = quotes.get_quote("t-1")["analysis"]["activities"][0]["notes"]
        self.assertIn("Por la experiencia de Andrés · se lo presentamos como recomendación", notas)


if __name__ == "__main__":
    unittest.main()
