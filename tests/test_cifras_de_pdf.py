"""T-131: las cifras de alcance de un PDF que ninguna actividad de la tabla menciona."""

from tests import _isolate  # noqa: F401  (debe ir primero)

import unittest
from pathlib import Path

from anka.dominio.cifras_de_pdf import cifras_aun_sin_mencionar, cifras_de_alcance, cifras_sin_mencionar, pistas_de_cifras
from anka.dominio.status import QuoteStatus
from anka.infra import store
from anka.servicios import work_import
from anka.web import pantalla_caso
from anka.web.views import cifras_de_pdf_panel
from tests.test_app import complete_analysis
from tests.test_errores_y_proteccion import Base

TEXTO = ("Levantamiento perimetral de un predio con área aproximada de 41.5 ha y topo-batimétrico de 13,8 ha. "
         "Secciones transversales cada 50 metros. El campo dura 12 días.")


def _pdf(nombre: str, texto: str) -> dict:
    return {"filename": nombre, "text": texto}


# Cubre: T-131
class LecturaDeCifrasTests(unittest.TestCase):
    def cifras(self, texto: str) -> list[str]:
        return [c["cifra"] for c in cifras_de_alcance("a.pdf", texto)]

    def test_lee_areas_longitudes_y_plazos_con_su_unidad(self) -> None:
        self.assertEqual(self.cifras(TEXTO), ["41.5 ha", "13,8 ha", "50 m", "12 días"])

    def test_normaliza_las_unidades(self) -> None:
        self.assertEqual(self.cifras("Área de 10.015 metros cuadrados, 3 kilómetros y 56 HORAS, 2 Hectáreas."),
                         ["10.015 m²", "3 km", "56 horas", "2 ha"])
        self.assertEqual(self.cifras("500 METROS CUADRADOS CON OBSTÁCULOS"), ["500 m²"])

    def test_descarta_velocidades_garantias_normas_y_certificados(self) -> None:
        ruido = ("Velocidad máxima 90 km/h. Garantía por 6 meses y 12 días. Norma ISO 9001 de 10 ha. "
                 "Resolución 330 de 2017. Certificado expedido a los 6 días del mes de enero. Hora: 13:48 horas.")
        self.assertEqual(self.cifras(ruido), [])

    def test_una_palabra_de_ruido_lejana_no_descarta_una_cifra_pero_la_de_un_certificado_si(self) -> None:
        relleno = "Texto de relleno del documento. " * 6   # unos 190 caracteres
        self.assertEqual(self.cifras(f"Garantía de la obra. {relleno}Levantamiento de 7 km de cauce."), ["7 km"])
        self.assertEqual(self.cifras(f"Certificado de existencia. {relleno}Plazo de 60 días."), [])

    def test_descarta_lo_que_no_es_de_alcance_y_las_cantidades_cero(self) -> None:
        self.assertEqual(self.cifras("16 GB de memoria, 5 baterías, 0 ha de afectación."), [])

    def test_dos_numeros_pegados_de_una_tabla_no_rompen_ni_cuentan(self) -> None:
        self.assertEqual(self.cifras("Valor 323.000 323.000 días"), ["323.000 días"])

    def test_un_numero_ambiguo_cuenta_con_sus_dos_lecturas_y_no_se_repite(self) -> None:
        texto = "Área de 10,015 metros cuadrados. En total 10.015 m2."
        cifras = cifras_de_alcance("a.pdf", texto)
        self.assertEqual(len(cifras), 1)
        self.assertEqual(cifras[0]["lecturas"], [10.015, 10015.0])

    def test_la_misma_cifra_en_otro_archivo_no_se_repite(self) -> None:
        adjuntos = [_pdf("a.pdf", TEXTO), _pdf("b.pdf", "El área es de 41,5 ha.")]
        self.assertEqual(len([c for c in cifras_sin_mencionar(adjuntos, []) if c["unidad"] == "ha"]), 2)


# Cubre: T-131
class CifrasSinMencionarTests(unittest.TestCase):
    ADJUNTOS = [_pdf("tdr.pdf", TEXTO)]

    def sin_mencionar(self, actividades: list[dict]) -> list[str]:
        return [c["cifra"] for c in cifras_sin_mencionar(self.ADJUNTOS, actividades)]

    def test_con_la_tabla_vacia_se_senalan_todas_y_las_areas_primero(self) -> None:
        self.assertEqual(self.sin_mencionar([]), ["41.5 ha", "13,8 ha", "50 m", "12 días"])

    def test_una_cifra_que_la_tabla_menciona_en_cualquier_campo_ya_no_se_senala(self) -> None:
        tabla = [{"activity": "Levantamiento", "quantity": "41,5", "unit": "ha", "notes": "Plazo de campo: 12 días"}]
        self.assertEqual(self.sin_mencionar(tabla), ["13,8 ha", "50 m"])

    def test_un_area_en_metros_cuadrados_equivale_a_la_misma_en_hectareas(self) -> None:
        adjuntos = [_pdf("a.pdf", "Área de 10.015 metros cuadrados.")]
        self.assertEqual(cifras_sin_mencionar(adjuntos, [{"quantity": "1,0015", "unit": "ha"}]), [])
        self.assertEqual(len(cifras_sin_mencionar(adjuntos, [{"quantity": "2", "unit": "ha"}])), 1)

    def test_solo_lee_pdf_y_no_las_impresiones_de_correo(self) -> None:
        for adjunto in (_pdf("hoja.xlsx", TEXTO), _pdf("Gmail - Solicitud.pdf", TEXTO), None, "x", {"filename": None}):
            self.assertEqual(cifras_sin_mencionar([adjunto], []), [])   # type: ignore[list-item]

    def test_tope_de_diez_con_las_areas_antes_que_los_plazos(self) -> None:
        texto = " ".join(f"Plazo {n} días." for n in range(1, 15)) + " Área de 7 ha."
        cifras = cifras_sin_mencionar([_pdf("a.pdf", texto)], [])
        self.assertEqual(len(cifras), 10)
        self.assertEqual(cifras[0]["cifra"], "7 ha")

    def test_pistas_de_cifras_devuelve_la_clave_del_analisis(self) -> None:
        self.assertEqual(len(pistas_de_cifras(self.ADJUNTOS, [])["cifras_de_pdf_sin_mencionar"]), 4)

    def test_aun_sin_mencionar_ignora_basura_guardada(self) -> None:
        self.assertEqual(cifras_aun_sin_mencionar([None, "x", {}], []), [{}])   # type: ignore[list-item]


# Cubre: T-131
class CifrasEnElFlujoTests(unittest.TestCase):
    def test_el_import_de_work_conserva_las_cifras(self) -> None:
        cifra = {"archivo": "a.pdf", "cifra": "41.5 ha", "contexto": "área de 41.5 ha", "lecturas": [41.5], "unidad": "ha"}
        base = {"status": "Pendiente de datos", "project_name": "P", "client_email": "c@x.co", "thread_id": "t1"}
        self.assertEqual(work_import._work_analysis({**base, "cifras_de_pdf_sin_mencionar": [cifra]})
                         ["cifras_de_pdf_sin_mencionar"], [cifra])
        self.assertEqual(work_import._work_analysis(base)["cifras_de_pdf_sin_mencionar"], [])


# Cubre: T-131
class PanelDeCifrasTests(unittest.TestCase):
    CIFRA = {"archivo": "tdr.pdf", "cifra": "41.5 ha", "contexto": "área aproximada de 41.5 ha", "lecturas": [41.5],
             "unidad": "ha"}

    def test_muestra_la_cifra_con_su_contexto_y_dice_que_no_se_agrega(self) -> None:
        html = str(cifras_de_pdf_panel([self.CIFRA]))
        self.assertIn("Cifras de los PDF que la tabla no menciona", html)
        self.assertIn("área aproximada de 41.5 ha", html)
        self.assertIn("no se agregan solas", html)
        self.assertNotIn("<form", html)

    def test_una_cifra_que_la_persona_ya_escribio_en_la_tabla_desaparece(self) -> None:
        self.assertEqual(str(cifras_de_pdf_panel([self.CIFRA], [{"quantity": "41,5"}])), "")

    def test_sin_cifras_no_hay_panel(self) -> None:
        self.assertEqual(str(cifras_de_pdf_panel([])), "")

    def test_no_inyecta_html_desde_el_archivo(self) -> None:
        html = str(cifras_de_pdf_panel([{**self.CIFRA, "contexto": "<script>x</script>", "archivo": "<b>a</b>.pdf"}]))
        self.assertNotIn("<script>", html)
        self.assertNotIn("<b>a</b>", html)


# Cubre: T-131
class PantallaConCifrasTests(Base):
    def test_la_pantalla_del_caso_muestra_la_pista(self) -> None:
        analisis = complete_analysis()
        analisis["cifras_de_pdf_sin_mencionar"] = [PanelDeCifrasTests.CIFRA]
        db = store.open_db(Path(self.temp.name))
        store.save(db, "t-1", "m-1", QuoteStatus.READY_REVIEW, "ana@acme.com", analisis)
        db.commit()
        db.close()
        self.assertIn("Cifras de los PDF que la tabla no menciona", pantalla_caso.render_case("t-1"))


if __name__ == "__main__":
    unittest.main()
