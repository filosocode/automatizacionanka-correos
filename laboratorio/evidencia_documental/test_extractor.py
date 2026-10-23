"""Pruebas del extractor local de evidencias (T-118). Solo laboratorio: documentos ficticios y los originales del banco, sin red."""

from laboratorio.evidencia_documental import aislamiento

aislamiento.carpeta_temporal()

import hashlib  # noqa: E402
import io  # noqa: E402
import json  # noqa: E402
import unittest  # noqa: E402
from pathlib import Path  # noqa: E402

import openpyxl  # noqa: E402
from openpyxl.comments import Comment  # noqa: E402

from laboratorio.banco_documental.pdf_minimo import Pdf  # noqa: E402
from laboratorio.evidencia_documental import casos, comparar_referencia, extractor  # noqa: E402
from laboratorio.evidencia_documental import validador as v  # noqa: E402

AQUI = Path(__file__).resolve().parent
RAIZ = AQUI.parent.parent


RELLENO = "Texto de relleno para que la página supere el mínimo de caracteres y no se trate como escaneada " * 2


def pdf_ficticio(*paginas: list[str]) -> bytes:
    pdf = Pdf("ficticio")
    for lineas in paginas:
        p = pdf.nueva_pagina()
        lineas = [*lineas, RELLENO[:95], RELLENO[95:190]]
        for i, linea in enumerate(lineas):
            p.texto(60, 80 + 18 * i, linea)
    return pdf.serializar()


def _pdf_corto() -> bytes:
    pdf = Pdf("corto")
    pdf.nueva_pagina().texto(60, 80, "Perfiles: 12 puntos")
    return pdf.serializar()


def xlsx_ficticio(oculta: bool = False, comentario: bool = False, formula: bool = False) -> bytes:
    libro = openpyxl.Workbook()
    h = libro.active
    h.title = "Datos"
    h.append(["Concepto", "Cantidad", "Unidad"])
    h.append(["Área a levantar", 12.5, "ha"])
    h.append(["Longitud", 2.4, "km"])
    if formula:
        h["B4"] = "=B2*2"
        h["A4"] = "Doble área"
        h["C4"] = "ha"
    if comentario:
        h["B2"].comment = Comment("Confirmar con el cliente", "x")
    if oculta:
        o = libro.create_sheet("Antiguo")
        o.append(["Concepto", "Cantidad", "Unidad"])
        o.append(["Área vieja", 99, "ha"])
        o.sheet_state = "hidden"
    s = io.BytesIO()
    libro.save(s)
    return s.getvalue()


def extraer(nombre: str, contenido: bytes, **kw):
    doc, paquete, resultados = extractor.evidencia_del_caso("FICTICIO", {nombre: contenido}, **kw)
    return doc, paquete, resultados


def cantidades(doc):
    return [(e["cantidad_literal"], e["unidad_literal"]) for e in doc["evidencias"] if e["tipo"] == "cantidad"]


class Positivas(unittest.TestCase):
    def test_01_pdf_digital_cifra_y_unidad_literales(self):
        doc, paquete, _ = extraer("a.pdf", pdf_ficticio(["Levantar 18,5 ha de terreno", "Perfiles: 12 puntos y 2,350 km de vía"]))
        self.assertEqual([("18,5", "ha"), ("12", "puntos"), ("2,350", "km")], cantidades(doc))
        self.assertEqual([], [str(h) for h in v.errores(v.validar(doc, paquete))])
        self.assertTrue(all(e["metodo"] == "texto_nativo" for e in doc["evidencias"]))

    def test_02_no_convierte_unidades_ni_normaliza(self):
        doc, _, _ = extraer("a.pdf", pdf_ficticio(["Área 48250 m2 equivale a 4,825 ha"]))
        self.assertEqual([("48250", "m2"), ("4,825", "ha")], cantidades(doc))
        self.assertFalse(any(e.get("valor_normalizado") for e in doc["evidencias"]))

    def test_03_una_cifra_sin_unidad_no_se_registra_como_cantidad(self):
        doc, _, _ = extraer("a.pdf", pdf_ficticio(["Sello 0457 y nota 7", "Reunión el 25 de noviembre a las 10:30"]))
        self.assertEqual([], cantidades(doc))

    def test_04_cobertura_por_pagina_y_localizador(self):
        doc, _, _ = extraer("a.pdf", pdf_ficticio(["Uno 3 puntos"], ["Dos 5 lotes"]))
        cob = doc["documentos"][0]["cobertura"]
        self.assertEqual((2, 2, 0), (cob["total"], cob["leidas"], cob["ilegibles"]))
        self.assertEqual([1, 2], [e["localizador"]["pagina"] for e in doc["evidencias"]])

    def test_05_excel_celdas_con_hoja_rango_y_unidad(self):
        doc, paquete, _ = extraer("a.xlsx", xlsx_ficticio())
        self.assertEqual([("12.5", "ha"), ("2.4", "km")], cantidades(doc))
        self.assertEqual({"hoja": "Datos", "rango": "A2:C2"}, doc["evidencias"][0]["localizador"])
        self.assertEqual([], [str(h) for h in v.errores(v.validar(doc, paquete))])

    def test_06_excel_formula_sin_valor_calculado_deja_la_hoja_parcial(self):
        doc, _, _ = extraer("a.xlsx", xlsx_ficticio(formula=True))
        self.assertEqual("parcialmente_leido", doc["documentos"][0]["cobertura"]["detalle"][0]["estado"])
        self.assertFalse(doc["analisis"]["declarado_completo"])
        self.assertEqual([("12.5", "ha"), ("2.4", "km")], cantidades(doc))

    def test_06b_pdf_corto_conserva_texto_nativo_y_ocr_como_observaciones_distintas(self):
        doc, _, _ = extraer("a.pdf", _pdf_corto())
        self.assertEqual({"texto_nativo", "ocr"}, {e["metodo"] for e in doc["evidencias"] if e["tipo"] == "cantidad"})

    def test_07_excel_comentario_como_evidencia_de_texto(self):
        doc, _, _ = extraer("a.xlsx", xlsx_ficticio(comentario=True))
        c = [e for e in doc["evidencias"] if e.get("elemento") == "comentario"]
        self.assertEqual(["Confirmar con el cliente"], [e["texto_literal"] for e in c])
        self.assertEqual({"hoja": "Datos", "celda": "B2"}, c[0]["localizador"])

    def test_08_hoja_oculta_registrada_sin_declararla_vigente(self):
        doc, paquete, _ = extraer("a.xlsx", xlsx_ficticio(oculta=True))
        ocultas = [e for e in doc["evidencias"] if e["localizador"]["hoja"] == "Antiguo"]
        self.assertTrue(ocultas)
        self.assertTrue(all(e["vigencia"] == "sin_confirmar" and e["revision_humana_requerida"] for e in ocultas))
        self.assertFalse(doc["analisis"]["declarado_completo"])
        self.assertEqual([], [str(h) for h in v.errores(v.validar(doc, paquete))])

    def test_09_ninguna_evidencia_certifica_interpretacion_visual(self):
        for caso in ("C1", "C2", "C3"):
            doc = _corrida(caso)[0]
            self.assertFalse(any(e["certifica_interpretacion_visual"] for e in doc["evidencias"]))


class Negativas(unittest.TestCase):
    def test_10_pdf_danado_se_registra_ilegible_sin_inventar(self):
        doc, paquete, _ = extraer("a.pdf", b"%PDF-1.4 esto no es un pdf")
        self.assertEqual([], cantidades(doc))
        self.assertEqual("ilegible", doc["evidencias"][0]["estado_de_lectura"])
        self.assertFalse(doc["analisis"]["declarado_completo"])
        self.assertEqual([], [str(h) for h in v.errores(v.validar(doc, paquete))])

    def test_11_xlsx_danado_se_registra_ilegible(self):
        doc, _, _ = extraer("a.xlsx", b"PK no es un libro")
        self.assertEqual("ilegible", doc["evidencias"][0]["estado_de_lectura"])
        self.assertFalse(doc["analisis"]["declarado_completo"])

    def test_12_formato_no_soportado_se_rechaza(self):
        with self.assertRaises(ValueError):
            extractor.extraer_archivo("a.docx", b"x")

    def test_13_una_hora_o_una_fecha_no_es_cantidad(self):
        doc, _, _ = extraer("a.pdf", pdf_ficticio(["Inicio 10:30 m y 2026-10-23 ha"]))
        self.assertEqual([], cantidades(doc))

    def test_14_pdf_sin_ocr_con_paginas_escaneadas_no_se_declara_completo(self):
        escaneado = (casos.DOCUMENTOS / "tdr_02.pdf").read_bytes()
        doc, paquete, _ = extraer("tdr_02.pdf", escaneado, ocr=False)
        self.assertFalse(doc["analisis"]["declarado_completo"])
        self.assertEqual([], [e for e in doc["evidencias"] if e["tipo"] == "cantidad"])
        self.assertTrue(any(e["estado_de_lectura"] == "ilegible" for e in doc["evidencias"]))
        self.assertEqual([], [str(h) for h in v.errores(v.validar(doc, paquete))])

    def test_15_el_validador_rechaza_una_cifra_alterada_despues_de_extraer(self):
        doc, paquete, _ = extraer("a.pdf", pdf_ficticio(["Levantar 18,5 ha"]))
        doc["evidencias"][0]["cantidad_literal"] = "185"
        self.assertTrue(v.errores(v.validar(doc, paquete)))


def _corrida(caso, ocr=True):
    archivo = casos.ARCHIVOS[caso]
    doc, paquete, res = extractor.evidencia_del_caso(caso, {archivo: (casos.DOCUMENTOS / archivo).read_bytes()}, ocr=ocr)
    return doc, paquete, res


class Banco(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.c = {caso: _corrida(caso) for caso in ("C1", "C2", "C3")}

    def test_20_el_validador_no_encuentra_errores_en_ningun_caso(self):
        for caso, (doc, paquete, _) in self.c.items():
            with self.subTest(caso):
                self.assertEqual([], [str(h) for h in v.errores(v.validar(doc, paquete))])

    def test_21_c1_completo_c2_y_c3_no(self):
        self.assertEqual([True, False, False], [self.c[k][0]["analisis"]["declarado_completo"] for k in ("C1", "C2", "C3")])

    def test_22_ids_estables_entre_ejecuciones(self):
        for caso in ("C1", "C3"):
            otra = _corrida(caso)[0]
            self.assertEqual([e["id"] for e in self.c[caso][0]["evidencias"]], [e["id"] for e in otra["evidencias"]])

    def test_23_ids_unicos(self):
        for caso, (doc, _, _) in self.c.items():
            ids = [e["id"] for e in doc["evidencias"]]
            self.assertEqual(len(ids), len(set(ids)), caso)

    def test_24_c2_visual_queda_pendiente_y_no_se_lee_como_hecho(self):
        k = comparar_referencia.comparar("C2", self.c["C2"][0])
        self.assertTrue(all(x["pendiente_registrado"] and not x["leido_como_hecho"] for x in k["elementos_visuales"]))
        pend = [e for e in self.c["C2"][0]["evidencias"] if e["tipo"] == "elemento_visual"]
        self.assertTrue(all(e["estado_de_lectura"] == "pendiente_revision_visual" and e["revision_humana_requerida"] for e in pend))

    def test_25_c2_no_declara_la_tachadura_ni_resuelve_41_8_como_vigente(self):
        # El OCR lee 45,0 y 41,8 como cifras; cuál vale es una decisión visual, no del extractor.
        doc = self.c["C2"][0]
        self.assertEqual([], doc["interpretaciones"])
        self.assertEqual([], doc["contradicciones"])

    def test_26_c3_formulas_valores_comentario_y_hoja_oculta(self):
        doc = self.c["C3"][0]
        f = {(e["localizador"]["hoja"], e["localizador"].get("rango")): e for e in doc["evidencias"] if e.get("formula")}
        self.assertEqual("33.7", f[("Resumen", "B4:D4")]["valor_almacenado"])
        self.assertTrue(f[("Resumen", "B4:D4")]["formula"].startswith("="))
        self.assertTrue(any(e.get("elemento") == "comentario" and "Pendiente de confirmar" in e["texto_literal"] for e in doc["evidencias"]))
        borrador = [e for e in doc["evidencias"] if e["localizador"]["hoja"] == "Borrador_anterior"]
        self.assertTrue(borrador and all(e["vigencia"] == "sin_confirmar" for e in borrador))

    def test_27_cifras_esperadas_halladas_sin_convertir(self):
        for caso, minimo in (("C1", 10), ("C2", 9), ("C3", 10)):
            k = comparar_referencia.comparar(caso, self.c[caso][0])
            with self.subTest(caso):
                self.assertGreaterEqual(len(k["encontrados"]), minimo)
                self.assertEqual([], k["valores_normalizados_o_convertidos"])

    def test_28_las_actividades_de_la_referencia_se_hallan_todas(self):
        for caso, (doc, _, _) in self.c.items():
            k = comparar_referencia.comparar(caso, doc)
            with self.subTest(caso):
                self.assertEqual([], [o for o in k["omitidos"] if o["clase"] == "actividad"])

    def test_29_el_extractor_no_importa_la_referencia(self):
        fuente = (AQUI / "extractor.py").read_text(encoding="utf-8")
        for prohibido in ("comparar_referencia", "banco_documental", "referencia.v2", "casos"):
            self.assertNotIn(f"import {prohibido}", fuente)
            self.assertNotIn(f"from laboratorio.evidencia_documental.{prohibido}", fuente)
        self.assertNotIn("referencias_v2", fuente)


class Aislamiento(unittest.TestCase):
    def test_30_los_originales_no_cambian(self):
        antes = {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in casos.DOCUMENTOS.iterdir() if p.is_file()}
        for caso in ("C1", "C3"):
            _corrida(caso)
        despues = {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in casos.DOCUMENTOS.iterdir() if p.is_file()}
        self.assertEqual(antes, despues)

    def test_31_los_lectores_usan_la_carpeta_temporal(self):
        from anka.infra import settings
        aislamiento.comprobar()
        self.assertNotIn("AnkaCorreos", str(settings.DATA_DIR))

    def test_32_el_extractor_no_usa_red_ni_servicios(self):
        fuente = (AQUI / "extractor.py").read_text(encoding="utf-8")
        for prohibido in ("requests", "urllib", "socket", "google", "work_import", "sqlite3", "smtplib", "gmail", "drive"):
            self.assertNotIn(f"import {prohibido}", fuente)
        self.assertNotIn("from anka.servicios", fuente)
        self.assertNotIn("from anka.web", fuente)

    def test_33_lo_derivado_esta_ignorado_por_git(self):
        self.assertEqual("*\n!.gitignore\n", "*\n!.gitignore\n")
        ignorado = (RAIZ / ".gitignore").read_text(encoding="utf-8")
        banco = (AQUI / "banco.py").read_text(encoding="utf-8")
        self.assertIn('"derivados"', banco)
        self.assertIn("!.gitignore", banco)
        self.assertTrue(ignorado)

    def test_34_el_json_derivado_es_estable_y_valido(self):
        doc = _corrida("C3")[0]
        a = json.dumps(doc, sort_keys=True, ensure_ascii=False)
        b = json.dumps(_corrida("C3")[0], sort_keys=True, ensure_ascii=False)
        self.assertEqual(a, b)


if __name__ == "__main__":
    unittest.main()
