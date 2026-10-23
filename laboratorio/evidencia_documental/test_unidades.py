"""Pruebas de T-119, fases 1 y 2: unidades desconocidas, cobertura y límites explícitos. Solo laboratorio."""

from laboratorio.evidencia_documental import aislamiento

aislamiento.carpeta_temporal()

import hashlib  # noqa: E402
import io  # noqa: E402
import unittest  # noqa: E402

import openpyxl  # noqa: E402

from laboratorio.evidencia_documental import casos, extractor, unidades  # noqa: E402
from laboratorio.evidencia_documental import validador as v  # noqa: E402
from laboratorio.evidencia_documental.test_extractor import RELLENO, extraer, pdf_ficticio  # noqa: E402

R = unidades.RECONOCIDA
NR = unidades.NO_RECONOCIDA
AM = unidades.AMBIGUA


def obs(texto, pegada=False):
    hechas, _ = unidades.observar(texto, pegada=pegada)
    return [(o.cantidad, o.unidad, o.estado) for o in hechas]


def sobrantes(texto):
    return unidades.observar(texto)[1]


def xlsx(filas):
    libro = openpyxl.Workbook()
    hoja = libro.active
    hoja.title = "D"
    for fila in filas:
        hoja.append(fila)
    s = io.BytesIO()
    libro.save(s)
    return s.getvalue()


class Reconocidas(unittest.TestCase):
    def test_01_catalogo(self):
        self.assertEqual([("12", "puntos", R), ("2,350", "km", R), ("8", "hitos", R)], obs("Hay 12 puntos, 2,350 km y 8 hitos."))

    def test_02_nuevas_tecnicas_del_catalogo_ampliado(self):
        self.assertEqual([("6", "vértices", R), ("4", "estaciones", R), ("30", "kg", R)], obs("6 vértices, 4 estaciones y 30 kg"))

    def test_03_compuesta_con_partes_conocidas(self):
        o = unidades.observar("Densidad de 120 puntos/km en la vía")[0][0]
        self.assertEqual(("120", "puntos/km", R, True), (o.cantidad, o.unidad, o.estado, o.compuesta))


class Desconocidas(unittest.TestCase):
    def test_10_unidad_fuera_del_catalogo_se_conserva_literal(self):
        self.assertEqual([("5", "ingenieros", NR)], obs("Se requieren 5 ingenieros en campo"))

    def test_11_no_se_normaliza_ni_se_adivina(self):
        self.assertEqual([("3", "bms", NR)], obs("Materializar 3 bms de control"))
        self.assertEqual([("12,5", "yardas", NR)], obs("Longitud de 12,5 yardas"))

    def test_12_compuesta_con_parte_desconocida(self):
        o = unidades.observar("Velocidad de 3 m/s máxima")[0][0]
        self.assertEqual(("3", "m/s", NR, True), (o.cantidad, o.unidad, o.estado, o.compuesta))

    def test_13_ambigua_conserva_posibles_sin_elegir(self):
        o = unidades.observar("Suministrar 2 gl de pintura")[0][0]
        self.assertEqual(("2", "gl", AM), (o.cantidad, o.unidad, o.estado))
        self.assertEqual(("global", "galón"), o.posibles)
        self.assertEqual(AM, unidades.observar("Rendimiento de 5 km/h")[0][0].estado)

    def test_14_compuesta_ambigua_y_desconocida_prevalece_la_desconocida(self):
        self.assertEqual(NR, unidades.clasificar("gl/xyz")[0])
        self.assertEqual(AM, unidades.clasificar("km/h")[0])
        self.assertEqual(R, unidades.clasificar("m2/ha")[0])


class SinFalsosPositivos(unittest.TestCase):
    def test_20_horas_y_fechas(self):
        for texto in ("Reunión a las 10:30 horas", "Inicio 10:30 m", "Fecha 2026-10-23 ha", "Radicado 23/10/2026 y 10/2026 del contrato",
                      "El 25 de noviembre", "Salida 10 am", "Llegada 3 pm", "El 5 septiembre"):
            with self.subTest(texto):
                self.assertEqual([], obs(texto))

    def test_21_codigos_folios_y_numeracion(self):
        for texto in ("Nota 7 sobre el plano", "Folio 12 del libro", "Cuadro 2 muestra áreas", "Figura 1 indica el norte", "Lote 5 colinda",
                      "No. 14 del contrato", "Ítem 3 describe", "Código AB12 ml", "Plano P-12 ver", "Sello 0457 de copia controlada",
                      "Artículo 4 establece"):
            with self.subTest(texto):
                self.assertEqual([], obs(texto))

    def test_22_numeracion_de_lista_al_inicio_de_linea(self):
        self.assertEqual([], obs("1 condiciones generales\n2 alcance del trabajo"))

    def test_23_lo_excluido_no_desaparece_del_informe(self):
        self.assertIn("0457", sobrantes("Sello 0457 de copia controlada"))
        self.assertIn("12", sobrantes("Folio 12 del libro"))
        self.assertIn("7", sobrantes("Nota 7 sobre el plano"))

    def test_24_cifra_sin_palabra_de_medida_no_es_cantidad(self):
        self.assertEqual([], obs("Valores 1234 y 56,7 sin medida."))

    def test_25_el_relleno_de_las_pruebas_no_es_una_cantidad(self):
        self.assertEqual([], obs(RELLENO))


class EnLosArchivos(unittest.TestCase):
    def test_30_pdf_unidad_desconocida_baja_confianza_y_pide_revision(self):
        doc, paquete, _ = extraer("a.pdf", pdf_ficticio(["Se requieren 5 ingenieros y 12 puntos de control"]))
        e = next(x for x in doc["evidencias"] if x["cantidad_literal"] == "5")
        self.assertEqual(("ingenieros", NR, "baja", True), (e["unidad_literal"], e["unidad_estado"], e["confianza"], e["revision_humana_requerida"]))
        self.assertFalse(doc["analisis"]["declarado_completo"])
        self.assertTrue(any("unidad no reconocida" in m for m in doc["analisis"]["motivos_de_incompletitud"]))
        self.assertEqual([], [str(h) for h in v.errores(v.validar(doc, paquete))])
        self.assertFalse(any(x.get("valor_normalizado") for x in doc["evidencias"]))

    def test_31_pdf_ambigua_registra_posibles(self):
        doc, _, _ = extraer("a.pdf", pdf_ficticio(["Suministrar 2 gl de pintura"]))
        e = next(x for x in doc["evidencias"] if x["cantidad_literal"] == "2")
        self.assertEqual((AM, ["global", "galón"]), (e["unidad_estado"], e["unidades_posibles"]))

    def test_32_excel_unidad_desconocida_en_columna_unidad(self):
        doc, paquete, _ = extraer("a.xlsx", xlsx([["Concepto", "Cantidad", "Unidad"], ["Control", 12, "bms"], ["Área", 3.5, "ha"]]))
        estados = {e["cantidad_literal"]: (e["unidad_literal"], e["unidad_estado"]) for e in doc["evidencias"]}
        self.assertEqual({"12": ("bms", NR), "3.5": ("ha", R)}, estados)
        self.assertEqual([], [str(x) for x in v.errores(v.validar(doc, paquete))])

    def test_33_excel_cifra_sin_unidad_se_conserva_y_se_cuenta(self):
        doc, _, _ = extraer("a.xlsx", xlsx([["Concepto", "Cantidad"], ["Algo", 7]]))
        self.assertEqual("sin_unidad", doc["evidencias"][0]["unidad_estado"])
        d = doc["documentos"][0]["cobertura"]["detalle"][0]
        self.assertEqual((1, 1), (d["celdas_numericas"], d["celdas_numericas_sin_unidad"]))


class CoberturaYLimites(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.c = {}
        for caso, nombre in casos.ARCHIVOS.items():
            cls.c[caso] = extractor.evidencia_del_caso(caso, {nombre: (casos.DOCUMENTOS / nombre).read_bytes()})

    def test_40_ningun_caso_afirma_exhaustividad_ni_actividades(self):
        for caso, (doc, paquete, _) in self.c.items():
            with self.subTest(caso):
                a = doc["analisis"]
                self.assertEqual(("no_determinable", "evidencia_localizada", 0), (a["exhaustividad"], a["alcance"], a["actividades_interpretadas"]))
                self.assertEqual([], doc["interpretaciones"])
                self.assertTrue(any("no puede determinar" in x for x in a["limitaciones"]))
                self.assertEqual([], [str(h) for h in v.errores(v.validar(doc, paquete))])

    def test_41_un_validador_sin_errores_no_prueba_recuperacion(self):
        doc = self.c["C1"][0]   # pasa el validador y se declara completo, pero no puede afirmar exhaustividad ni actividades
        self.assertTrue(doc["analisis"]["declarado_completo"])
        self.assertEqual("no_determinable", doc["analisis"]["exhaustividad"])
        self.assertTrue(any("declarado_completo" in x for x in doc["analisis"]["limitaciones"]))

    def test_42_cobertura_por_pagina_y_hoja_con_motivo(self):
        for caso, (doc, _, _) in self.c.items():
            for d in doc["documentos"][0]["cobertura"]["detalle"]:
                with self.subTest((caso, d["ref"])):
                    self.assertIn(d["lectura"], ("sin_indicios_de_omision", "no_determinable"))
                    self.assertTrue(d["motivo_lectura"])

    def test_43_c2_toda_pagina_es_no_determinable_y_lo_visual_sigue_pendiente(self):
        doc = self.c["C2"][0]
        self.assertEqual(["no_determinable"] * 2, [d["lectura"] for d in doc["documentos"][0]["cobertura"]["detalle"]])
        self.assertTrue(all(e["estado_de_lectura"] == "pendiente_revision_visual" for e in doc["evidencias"] if e["tipo"] == "elemento_visual"))

    def test_44_c2_registra_cifras_que_no_capturo(self):
        d = self.c["C2"][0]["documentos"][0]["cobertura"]["detalle"][1]
        self.assertIn("45,0", d["muestra_cifras_no_registradas"])   # el Cuadro 2 trae la unidad en el encabezado, no junto a la cifra
        self.assertGreater(d["cifras_no_registradas"], 0)

    def test_45_c3_sin_cambios_respecto_a_t118(self):
        doc = self.c["C3"][0]
        self.assertEqual(29, len(doc["evidencias"]))
        self.assertFalse(doc["analisis"]["declarado_completo"])

    def test_46_sin_ocr_ninguna_pagina_escaneada_es_determinable(self):
        escaneado = (casos.DOCUMENTOS / "tdr_02.pdf").read_bytes()
        doc, _, _ = extraer("tdr_02.pdf", escaneado, ocr=False)
        self.assertEqual({"no_determinable"}, {d["lectura"] for d in doc["documentos"][0]["cobertura"]["detalle"]})

    def test_47_criterios_de_exhaustividad_por_pagina(self):
        self.assertEqual("no_determinable", extractor._exhaustividad_de_pagina("leido", True, False, False)[0])
        self.assertEqual("sin_indicios_de_omision", extractor._exhaustividad_de_pagina("leido", False, False, False)[0])


class ReferenciasCongeladas(unittest.TestCase):
    HUELLAS = {
        "C1.referencia.v2.json": "97bb553139c2afa4d44b1968eca87b64499beb0b6736c1c77acfa44a9d91308c",
        "C2.referencia.v2.json": "4f57d8490e6f4cffc38cf56e5ab43768aad3835693fa8ba5cea5ef51661a3fd4",
        "C3.referencia.v2.json": "baeeb2146dc9bc5477a34180f3d803469652a00edf2757d54f0acb24ca44368b",
        "manifiesto_referencias_v2.json": "ee4712655d90607c5de3b5dfa570182d376be65e483c64f4cc7cbddc68a23224",
    }

    def test_50_las_referencias_no_cambiaron(self):
        from laboratorio.evidencia_documental.comparar_referencia import REFERENCIAS
        for nombre, esperada in self.HUELLAS.items():
            with self.subTest(nombre):
                self.assertEqual(esperada, hashlib.sha256((REFERENCIAS / nombre).read_bytes()).hexdigest())


if __name__ == "__main__":
    unittest.main()
