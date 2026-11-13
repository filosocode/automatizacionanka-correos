"""T-128: una persona agrega a la tabla las filas de hojas de cálculo que la tabla no recogía."""

from tests import _isolate  # noqa: F401  (debe ir primero)

import unittest
from pathlib import Path

from anka.dominio.activities import huella_de_tabla
from anka.dominio.filas_de_hoja import actividad_de_fila, clave_de_fila, filas_aun_sin_cubrir
from anka.dominio.status import QuoteStatus
from anka.infra import store
from anka.servicios import ediciones, quotes
from anka.web import pantalla_caso
from tests.test_app import complete_analysis
from tests.test_errores_y_proteccion import Base, formulario
from tests.test_seguridad_http import atender

SONDEOS = {"archivo": "plantilla.xlsx", "hoja": "PR35", "fila": 6, "descripcion": "Sondeos mecánicos en depósitos coluviales",
           "cantidad": 240.0, "veces": 1.0, "unidad": "ml"}
CELADURIA = {"archivo": "plantilla.xlsx", "hoja": "PR35", "fila": 12, "descripcion": "Celaduría del equipo",
             "cantidad": 3.0, "veces": 55.0, "unidad": "día"}


# Cubre: T-128
class ActividadDeUnaFilaTests(unittest.TestCase):
    def test_conserva_lo_que_dice_la_hoja_y_queda_pendiente_con_su_fuente(self) -> None:
        fila = actividad_de_fila(SONDEOS)
        self.assertEqual((fila["activity"], fila["quantity"], fila["unit"], fila["estado"]),
                         ("Sondeos mecánicos en depósitos coluviales", "240", "ml", "pendiente"))
        self.assertEqual(fila["fuente"], "plantilla.xlsx · hoja «PR35» · fila 6")

    def test_la_cantidad_decimal_usa_coma_y_las_veces_quedan_en_la_nota(self) -> None:
        self.assertEqual(actividad_de_fila({**SONDEOS, "cantidad": 0.5})["quantity"], "0,5")
        self.assertIn("3 × 55 veces", actividad_de_fila(CELADURIA)["notes"])
        self.assertNotIn("veces", actividad_de_fila(SONDEOS)["notes"])

    def test_la_clave_identifica_archivo_hoja_y_fila(self) -> None:
        self.assertEqual(clave_de_fila(SONDEOS), "plantilla.xlsx|PR35|6")

    def test_una_fila_agregada_deja_de_ser_pista(self) -> None:
        self.assertEqual(filas_aun_sin_cubrir([SONDEOS, CELADURIA], [actividad_de_fila(SONDEOS)]), [CELADURIA])


class ConFilasDeHoja(Base):
    def setUp(self) -> None:
        super().setUp()
        analisis = complete_analysis()
        analisis["filas_de_hoja_sin_cubrir"] = [SONDEOS, CELADURIA]
        db = store.open_db(Path(self.temp.name))
        store.save(db, "t-1", "m-1", QuoteStatus.READY_REVIEW, "ana@acme.com", analisis)
        db.commit()
        db.close()

    def tabla(self) -> list[dict]:
        return quotes.get_quote("t-1")["analysis"]["activities"]


# Cubre: T-128
class AgregarFilasTests(ConFilasDeHoja):
    def test_agrega_solo_las_marcadas_como_pendientes_y_conserva_las_que_estaban(self) -> None:
        antes = self.tabla()
        mensaje = ediciones.agregar_filas_de_hoja("t-1", [clave_de_fila(SONDEOS)])
        self.assertIn("Agregaste 1 fila", mensaje)
        despues = self.tabla()
        self.assertEqual(despues[: len(antes)], antes)
        self.assertEqual(len(despues), len(antes) + 1)
        self.assertEqual((despues[-1]["activity"], despues[-1]["estado"]), (SONDEOS["descripcion"], "pendiente"))

    def test_todas_agrega_todas_las_que_la_tabla_no_recoge(self) -> None:
        antes = len(self.tabla())
        mensaje = ediciones.agregar_filas_de_hoja("t-1", [], todas=True)
        self.assertIn("Agregaste 2 filas", mensaje)
        self.assertEqual(len(self.tabla()), antes + 2)

    def test_guarda_una_version_con_el_motivo_y_deja_huella_en_el_historial(self) -> None:
        antes = len(ediciones.table_versions("t-1"))
        ediciones.agregar_filas_de_hoja("t-1", [clave_de_fila(SONDEOS)])
        self.assertGreater(len(ediciones.table_versions("t-1")), antes)
        self.assertIsNotNone(ediciones.last_manual_version("t-1"))

    def test_sin_marcar_nada_se_rechaza_y_no_cambia_la_tabla(self) -> None:
        antes = self.tabla()
        with self.assertRaises(ValueError) as error:
            ediciones.agregar_filas_de_hoja("t-1", [])
        self.assertIn("No marcaste ninguna fila", str(error.exception))
        self.assertEqual(self.tabla(), antes)

    def test_una_clave_que_no_existe_no_agrega_nada(self) -> None:
        with self.assertRaises(ValueError):
            ediciones.agregar_filas_de_hoja("t-1", ["otro.xlsx|H|1"])

    def test_agregar_dos_veces_la_misma_fila_no_la_duplica(self) -> None:
        ediciones.agregar_filas_de_hoja("t-1", [clave_de_fila(SONDEOS)])
        with self.assertRaises(ValueError):
            ediciones.agregar_filas_de_hoja("t-1", [clave_de_fila(SONDEOS)])
        self.assertEqual(sum(f["activity"] == SONDEOS["descripcion"] for f in self.tabla()), 1)

    def test_una_tabla_que_cambio_desde_que_se_abrio_se_rechaza(self) -> None:
        vieja = huella_de_tabla(self.tabla())
        ediciones.save_activities("t-1", [{"activity": "Otra edición"}], "", vieja)
        with self.assertRaises(ValueError) as error:
            ediciones.agregar_filas_de_hoja("t-1", [clave_de_fila(SONDEOS)], base_version=vieja)
        self.assertIn("La tabla cambió", str(error.exception))

    def test_un_caso_que_no_existe_se_explica(self) -> None:
        with self.assertRaises(ValueError) as error:
            ediciones.agregar_filas_de_hoja("no-existe", ["x"])
        self.assertIn("ya no existe", str(error.exception))


# Cubre: T-128
class FormularioDeFilasTests(ConFilasDeHoja):
    def test_la_ruta_agrega_las_filas_marcadas_y_vuelve_al_caso(self) -> None:
        antes = len(self.tabla())
        estado, cabeceras, _, _ = atender("POST", "/activities-add-sheet-rows", formulario(
            thread_id="t-1", fila=clave_de_fila(CELADURIA), base_version=huella_de_tabla(self.tabla())))
        self.assertEqual(estado, 303)
        self.assertIn("/case?thread_id=t-1", dict(cabeceras).get("Location", ""))
        self.assertEqual(len(self.tabla()), antes + 1)

    def test_sin_el_token_de_seguridad_no_agrega_nada(self) -> None:
        antes = len(self.tabla())
        estado, _, _, _ = atender("POST", "/activities-add-sheet-rows", b"thread_id=t-1&todas=1")
        self.assertEqual(estado, 403)
        self.assertEqual(len(self.tabla()), antes)

    def test_la_pantalla_del_caso_ofrece_el_formulario_con_la_huella_de_la_tabla(self) -> None:
        pagina = pantalla_caso.render_case("t-1")
        self.assertIn('action="/activities-add-sheet-rows"', pagina)
        self.assertIn(f'name="base_version" value="{huella_de_tabla(self.tabla())}"', pagina)
        self.assertIn(f'value="{clave_de_fila(SONDEOS)}"', pagina)

    def test_una_fila_ya_agregada_desaparece_de_la_pantalla(self) -> None:
        ediciones.agregar_filas_de_hoja("t-1", [clave_de_fila(SONDEOS)])
        pagina = pantalla_caso.render_case("t-1")
        self.assertNotIn(f'value="{clave_de_fila(SONDEOS)}"', pagina)
        self.assertIn(f'value="{clave_de_fila(CELADURIA)}"', pagina)


if __name__ == "__main__":
    unittest.main()
