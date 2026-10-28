"""T-130: una persona pone un área calculada de un KML/KMZ como cantidad de una actividad sin cantidad."""

from tests import _isolate  # noqa: F401  (debe ir primero)

import unittest
from pathlib import Path

from anka.dominio.activities import huella_de_tabla
from anka.dominio.areas_calculadas import (
    actividad_con_area,
    area_elegida,
    clave_de_area,
    clave_del_total,
    sin_cantidad,
    totales_por_archivo,
)
from anka.dominio.status import QuoteStatus
from anka.infra import store
from anka.servicios import ediciones, quotes
from anka.web import pantalla_caso
from anka.web.views import areas_calculadas_panel
from tests.test_app import complete_analysis
from tests.test_errores_y_proteccion import Base, formulario
from tests.test_seguridad_http import atender

AREAS = [
    {"archivo": "lote.kmz", "poligono": "Lote A", "area_m2": 48231.0, "area_ha": 4.8231, "perimetro_m": 900.0,
     "anillos_interiores": 0},
    {"archivo": "lote.kmz", "poligono": "Lote B", "area_m2": 10000.0, "area_ha": 1.0, "perimetro_m": 400.0,
     "anillos_interiores": 0},
    {"archivo": "otro.kml", "poligono": "Único", "area_m2": 5000.0, "area_ha": 0.5, "perimetro_m": 300.0,
     "anillos_interiores": 0},
]
A = clave_de_area(0, "lote.kmz", 4.8231)
TOTAL = clave_del_total("lote.kmz", 5.8231)


# Cubre: T-130
class AreaComoCantidadTests(unittest.TestCase):
    def test_solo_una_actividad_sin_cantidad_acepta_un_area(self) -> None:
        for vacia in ("", None, "no indicado", " No indicado ", "N/A", "-", "por definir"):
            self.assertTrue(sin_cantidad(vacia), vacia)
        for escrita in ("544,5", "1", "0"):
            self.assertFalse(sin_cantidad(escrita), escrita)

    def test_totales_solo_de_archivos_con_mas_de_un_poligono(self) -> None:
        self.assertEqual(totales_por_archivo(AREAS), [("lote.kmz", 2, 5.8231)])

    def test_la_clave_encuentra_el_poligono_o_la_suma_y_rechaza_lo_que_cambio(self) -> None:
        self.assertEqual(area_elegida(AREAS, A), ("polígono «Lote A» de lote.kmz", 4.8231))
        self.assertEqual(area_elegida(AREAS, TOTAL), ("suma de 2 polígonos de lote.kmz", 5.8231))
        for mala in ("", "x", clave_de_area(9, "lote.kmz", 4.8231), clave_de_area(0, "otro.kml", 4.8231),
                     clave_de_area(0, "lote.kmz", 9.0), clave_del_total("lote.kmz", 9.0), "lote.kmz|a|1"):
            self.assertIsNone(area_elegida(AREAS, mala), mala)

    def test_la_actividad_queda_pendiente_en_hectareas_y_con_nota_de_origen(self) -> None:
        nueva = actividad_con_area({"activity": "Levantamiento", "quantity": "no indicado", "estado": "confirmado",
                                    "notes": "Urgente."}, "polígono «Lote A» de lote.kmz", 4.8231)
        self.assertEqual((nueva["quantity"], nueva["unit"], nueva["estado"]), ("4,8231", "ha", "pendiente"))
        self.assertTrue(nueva["notes"].startswith("Urgente. Cantidad = área aproximada calculada por el programa"))
        self.assertEqual(actividad_con_area({}, "x", 1.0)["quantity"], "1")


class ConAreas(Base):
    def setUp(self) -> None:
        super().setUp()
        analisis = complete_analysis()
        analisis["areas_calculadas"] = AREAS
        analisis["activities"] = [
            {"activity": "Levantamiento topográfico", "quantity": "", "unit": "", "estado": "pendiente"},
            {"activity": "Ortofoto", "quantity": "120", "unit": "ha", "estado": "confirmado"}]
        db = store.open_db(Path(self.temp.name))
        store.save(db, "t-1", "m-1", QuoteStatus.READY_REVIEW, "ana@acme.com", analisis)
        db.commit()
        db.close()

    def tabla(self) -> list[dict]:
        return quotes.get_quote("t-1")["analysis"]["activities"]


# Cubre: T-130
class PonerAreaTests(ConAreas):
    def test_pone_el_area_en_la_actividad_elegida_y_no_toca_las_demas(self) -> None:
        otra = dict(self.tabla()[1])
        mensaje = ediciones.poner_area_en_actividad("t-1", A, "0")
        self.assertIn("Pusiste el polígono «Lote A»", mensaje)
        primera = self.tabla()[0]
        self.assertEqual((primera["quantity"], primera["unit"], primera["estado"]), ("4,8231", "ha", "pendiente"))
        self.assertEqual(self.tabla()[1], otra)

    def test_la_suma_del_archivo_tambien_se_puede_poner(self) -> None:
        ediciones.poner_area_en_actividad("t-1", TOTAL, "0")
        self.assertEqual(self.tabla()[0]["quantity"], "5,8231")

    def test_nunca_cambia_una_cantidad_escrita(self) -> None:
        with self.assertRaises(ValueError) as error:
            ediciones.poner_area_en_actividad("t-1", A, "1")
        self.assertIn("ya tiene cantidad", str(error.exception))
        self.assertEqual(self.tabla()[1]["quantity"], "120")

    def test_rechaza_sin_area_o_sin_actividad_y_no_cambia_la_tabla(self) -> None:
        antes = self.tabla()
        for area, actividad in (("", "0"), (A, ""), (A, "7"), (A, "x"), (clave_de_area(0, "lote.kmz", 9.0), "0")):
            with self.subTest(area=area, actividad=actividad), self.assertRaises(ValueError):
                ediciones.poner_area_en_actividad("t-1", area, actividad)
        self.assertEqual(self.tabla(), antes)

    def test_guarda_version_con_el_motivo_y_rechaza_una_tabla_que_cambio(self) -> None:
        vieja = huella_de_tabla(self.tabla())
        ediciones.save_activities("t-1", [{"activity": "Otra edición"}], "", vieja)
        with self.assertRaises(ValueError) as error:
            ediciones.poner_area_en_actividad("t-1", A, "0", vieja)
        self.assertIn("La tabla cambió", str(error.exception))
        antes = len(ediciones.table_versions("t-1"))
        ediciones.save_activities("t-1", [{"activity": "Nueva", "quantity": ""}], "", "")
        ediciones.poner_area_en_actividad("t-1", A, "0")
        self.assertEqual(len(ediciones.table_versions("t-1")), antes + 2)

    def test_un_caso_que_no_existe_se_explica(self) -> None:
        with self.assertRaises(ValueError) as error:
            ediciones.poner_area_en_actividad("no-existe", A, "0")
        self.assertIn("ya no existe", str(error.exception))


# Cubre: T-130
class FormularioDeAreaTests(ConAreas):
    def test_la_ruta_pone_el_area_y_vuelve_al_caso(self) -> None:
        estado, cabeceras, _, _ = atender("POST", "/activities-set-area", formulario(
            thread_id="t-1", area=A, actividad="0", base_version=huella_de_tabla(self.tabla())))
        self.assertEqual(estado, 303)
        self.assertIn("/case?thread_id=t-1", dict(cabeceras).get("Location", ""))
        self.assertEqual(self.tabla()[0]["quantity"], "4,8231")

    def test_sin_el_token_de_seguridad_no_cambia_nada(self) -> None:
        estado, _, _, _ = atender("POST", "/activities-set-area", b"thread_id=t-1&area=x&actividad=0")
        self.assertEqual(estado, 403)
        self.assertEqual(self.tabla()[0]["quantity"], "")

    def test_la_pantalla_ofrece_el_formulario_solo_con_actividades_sin_cantidad(self) -> None:
        pagina = pantalla_caso.render_case("t-1")
        self.assertIn('action="/activities-set-area"', pagina)
        self.assertIn(f'value="{A}"', pagina)
        self.assertIn(f'value="{TOTAL}"', pagina)
        self.assertIn('<option value="0">Levantamiento topográfico</option>', pagina)
        self.assertNotIn('<option value="1">', pagina)

    def test_si_todas_tienen_cantidad_no_hay_formulario(self) -> None:
        html = str(areas_calculadas_panel(AREAS, (), "t-1", "h", [{"activity": "Ortofoto", "quantity": "120"}]))
        self.assertNotIn("/activities-set-area", html)
        self.assertIn("ya tienen cantidad", html)

    def test_el_panel_sin_caso_sigue_siendo_solo_una_pista(self) -> None:
        html = str(areas_calculadas_panel(AREAS))
        self.assertNotIn("<form", html)
        self.assertIn("Lote A", html)


if __name__ == "__main__":
    unittest.main()
