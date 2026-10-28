"""Pruebas del mapeador de laboratorio (T-76): extracción del banco -> formato estructural del contrato de Work."""

from __future__ import annotations

import copy
import json
import tempfile
import unittest
from pathlib import Path

from laboratorio.banco_documental import mapeador, validador

BANCO = Path(__file__).resolve().parent.parent / "laboratorio" / "banco_documental"
EVIDENCIAS = BANCO / "evidencias_t72_t74"
DOCUMENTOS = {"C1": "solicitud_01.pdf", "C2": "tdr_02.pdf", "C3": "cantidades_03.xlsx"}
CONTEXTO = {"thread_id": "PRUEBA-HILO-SIN-GMAIL", "message_id": "PRUEBA-MENSAJE-SIN-GMAIL",
            "client_email": "cliente.ficticio@example.invalid", "project_name": "Prueba documental T-76"}
CLAVES_DE_FILA = {"activity", "quantity", "unit", "entregable", "fuente", "estado", "notes"}


def _respuesta(caso: str) -> bytes:
    return (EVIDENCIAS / f"respuesta_work_{caso}.raw.txt").read_bytes()


def _documento(caso: str) -> bytes:
    return (BANCO / "documentos" / DOCUMENTOS[caso]).read_bytes()


def _mapear(caso: str) -> mapeador.ResultadoMapeo:
    return mapeador.mapear_respuesta(_respuesta(caso), CONTEXTO, caso, _documento(caso), DOCUMENTOS[caso])


def _extraccion_c3_sintetica() -> dict:
    """FICTICIA, con la forma de C3: la C3 original no se puede usar (JSON inválido) y no se repara."""
    return {"caso": "C3",
            "hojas": [{"nombre": "Resumen", "visibilidad": "visible"}, {"nombre": "Borrador_anterior", "visibilidad": "oculta"}],
            "actividades": [
                {"nombre": "Levantamiento de lotes", "cantidad": "33.7", "unidad": "ha", "entregable": "no especificado",
                 "origen": {"hoja": "Resumen", "celda": "C4"}, "tipo_de_dato": "formula", "formula": "=Detalle!D8",
                 "valor_almacenado": "33.7", "vigente": True},
                {"nombre": "Levantamiento de lotes (borrador)", "cantidad": "31.2", "unidad": "ha", "entregable": "",
                 "origen": {"hoja": "Borrador_anterior", "celda": "C2"}, "vigente": True},
                {"nombre": "Tramo retirado", "cantidad": "2.0", "unidad": "km", "entregable": "",
                 "origen": {"hoja": "Resumen", "celda": "C5"}, "vigente": False},
                {"nombre": "Hoja inexistente", "cantidad": "1", "unidad": "ha", "entregable": "",
                 "origen": {"hoja": "Fantasma", "celda": "A1"}}],
            "entregables": [], "restricciones": ["Equidistancia de curvas: 0.5 m"],
            "contradicciones": ["Resumen declara 7 lotes y Detalle identifica 6."],
            "valores_historicos_u_obsoletos": [{"hoja": "Borrador_anterior", "celda": "C2", "valor": "31.2", "unidad": "ha", "motivo": "m"}],
            "otros_datos_numericos": [{"concepto": "Lotes", "valor": "7", "unidad": "lotes", "hoja": "Resumen", "celda": "C9"}],
            "preguntas_cliente": ["¿7 o 6 lotes?"], "revisiones_internas": [], "elementos_visuales": [],
            "limitaciones_de_lectura": []}


class MapeoDeLasEvidenciasTest(unittest.TestCase):
    # Cubre: T-76
    def test_01_c1_y_c2_se_mapean_con_cantidades_unidades_y_fuentes_literales(self) -> None:
        for caso in ("C1", "C2"):
            resultado = _mapear(caso)
            self.assertEqual(resultado.estado, mapeador.MAPEADO_CON_PENDIENTES, caso)
            origen = json.loads(_respuesta(caso))["actividades"]
            filas = resultado.payload["activities"]
            self.assertEqual(len(filas), len(origen))
            for fila, actividad in zip(filas, origen, strict=True):
                self.assertEqual((fila["activity"], fila["quantity"], fila["unit"], fila["entregable"]),
                                 (actividad["nombre"], actividad["cantidad"], actividad["unidad"], actividad["entregable"]))
                self.assertTrue(fila["fuente"].startswith(DOCUMENTOS[caso] + ", "))
                self.assertLessEqual(set(fila), CLAVES_DE_FILA, "no se inventan columnas (servicio, ubicación, plazo)")

    # Cubre: T-76
    def test_02_la_c3_original_se_rechaza_antes_del_mapeo(self) -> None:
        resultado = _mapear("C3")
        self.assertEqual(resultado.estado, mapeador.MAPEO_NO_POSIBLE)
        self.assertIsNone(resultado.payload)
        self.assertIn(mapeador.RECHAZADO_ANTES_DEL_MAPEO, resultado.motivo)
        self.assertEqual(resultado.validacion["error_de_sintaxis"]["linea"], 378)

    # Cubre: T-76
    def test_03_lo_rechazado_va_a_cuarentena_y_no_se_mapea(self) -> None:
        with tempfile.TemporaryDirectory() as carpeta:
            resultado = mapeador.mapear_respuesta(_respuesta("C3"), CONTEXTO, "C3", _documento("C3"), "cantidades_03.xlsx", Path(carpeta))
            self.assertIsNone(resultado.payload)
            self.assertEqual(len(list((Path(carpeta) / "cuarentena").iterdir())), 1)
            self.assertFalse((Path(carpeta) / "validadas").exists())

    # Cubre: T-76
    def test_04_un_esquema_invalido_tampoco_se_mapea(self) -> None:
        crudo = _respuesta("C3").decode("utf-8").replace('fullCalcOnLoad="1"', 'fullCalcOnLoad=\\"1\\"').encode("utf-8")
        resultado = mapeador.mapear_respuesta(crudo, CONTEXTO, "C3")      # JSON válido, pero con objetos donde van cadenas
        self.assertEqual(resultado.estado, mapeador.MAPEO_NO_POSIBLE)
        self.assertIn(validador.ESQUEMA_INVALIDO, resultado.motivo)
        self.assertIsNone(resultado.payload)

    # Cubre: T-76
    def test_05_las_contradicciones_se_copian_pendientes_y_nada_queda_confirmado(self) -> None:
        for caso in ("C1", "C2"):
            payload = _mapear(caso).payload
            esperadas = json.loads(_respuesta(caso))["contradicciones"]
            self.assertEqual([c["details"] for c in payload["contradictions"]], esperadas)
            self.assertEqual({c["status"] for c in payload["contradictions"]}, {"pendiente"})
            self.assertEqual({f["estado"] for f in payload["activities"]}, {"pendiente"})
            self.assertFalse(payload["analysis_complete"])
            self.assertEqual(payload["status"], mapeador.ESTADO_INTERNO)

    # Cubre: T-76
    def test_06_sin_incidencias_en_el_documento_la_actividad_puede_quedar_confirmada(self) -> None:
        datos = {"caso": "C1", "actividades": [{"nombre": "A", "cantidad": "1", "unidad": "ha", "entregable": "x", "origen": {"pagina": 1}}],
                 "entregables": [], "restricciones": [], "contradicciones": [], "preguntas_cliente": [], "revisiones_internas": [],
                 "elementos_visuales": [], "limitaciones_de_lectura": []}
        resultado = mapeador.mapear(datos, CONTEXTO, "doc.pdf")
        self.assertEqual(resultado.estado, mapeador.MAPEADO)
        self.assertEqual(resultado.payload["activities"][0]["estado"], "confirmado")
        self.assertFalse(resultado.payload["analysis_complete"], "el mapeo nunca declara análisis completo")

    # Cubre: T-76
    def test_07_la_informacion_visual_sigue_siendo_evidencia_pendiente(self) -> None:
        resultado = _mapear("C2")
        adjunto = resultado.payload["attachments"][0]
        self.assertEqual((adjunto["visual_interpretation"], adjunto["coverage"]), ("no_certificada", "parcial"))
        self.assertIn("lectura visual sin validación independiente", adjunto["reason"])
        visuales = json.loads(_respuesta("C2"))["evidencia_visual"]
        dependencias = resultado.payload["scope_dependencies"]
        self.assertEqual(len(dependencias), len(visuales))
        self.assertEqual({d["status"] for d in dependencias}, {"pendiente"})
        self.assertTrue(any("lectura visual no certificada" in a for a in mapeador.comprobar_contra_contrato(resultado.payload)["avisos_de_cobertura"]))
        self.assertIn("evidencia_visual", {n.campo for n in resultado.no_mapeado})

    # Cubre: T-76
    def test_08_las_hojas_ocultas_y_lo_no_vigente_no_se_mezclan_con_cantidades_vigentes(self) -> None:
        resultado = mapeador.mapear(_extraccion_c3_sintetica(), CONTEXTO, "cantidades_03.xlsx")
        filas = resultado.payload["activities"]
        self.assertEqual([f["quantity"] for f in filas], ["33.7"])
        self.assertIn("Fórmula =Detalle!D8; valor almacenado 33.7.", filas[0]["notes"])
        texto = json.dumps(resultado.payload, ensure_ascii=False)
        for ajeno in ("31.2", "Borrador_anterior", "Fantasma", "2.0"):
            self.assertNotIn(ajeno, texto)
        descartes = [n for n in resultado.no_mapeado if n.campo == "actividades"]
        self.assertEqual(len(descartes), 3)
        self.assertTrue(any("no visibles" in n.motivo for n in descartes))
        self.assertTrue(any("no vigente" in n.motivo for n in descartes))
        self.assertTrue(any("no declara" in n.motivo for n in descartes))
        self.assertLessEqual({"valores_historicos_u_obsoletos", "otros_datos_numericos"}, {n.campo for n in resultado.no_mapeado})
        self.assertEqual(filas[0]["estado"], "pendiente")

    # Cubre: T-76
    def test_09_lo_que_no_tiene_equivalencia_se_declara_y_se_conserva_literal(self) -> None:
        resultado = _mapear("C1")
        por_campo = {n.campo: n for n in resultado.no_mapeado}
        origen = json.loads(_respuesta("C1"))
        self.assertEqual(por_campo["preguntas_cliente"].contenido, origen["preguntas_cliente"])
        self.assertEqual(por_campo["revisiones_internas"].contenido, origen["revisiones_internas"])
        self.assertNotIn("question_audit", resultado.payload)
        self.assertNotIn("missing_data", resultado.payload)

    # Cubre: T-76
    def test_10_si_falta_la_identidad_el_mapeo_no_es_posible(self) -> None:
        for campo in mapeador.IDENTIDAD_OBLIGATORIA:
            contexto = {**CONTEXTO, campo: "  "}
            resultado = mapeador.mapear_respuesta(_respuesta("C1"), contexto, "C1", _documento("C1"), "solicitud_01.pdf")
            self.assertEqual(resultado.estado, mapeador.MAPEO_NO_POSIBLE, campo)
            self.assertIn(campo, resultado.motivo)
            self.assertIsNone(resultado.payload)
        sin_clave = {k: v for k, v in CONTEXTO.items() if k != "thread_id"}
        self.assertEqual(mapeador.mapear_respuesta(_respuesta("C1"), sin_clave, "C1").estado, mapeador.MAPEO_NO_POSIBLE)
        self.assertEqual(mapeador.mapear_respuesta(_respuesta("C1"), CONTEXTO, "C1", None, "").estado, mapeador.MAPEO_NO_POSIBLE)

    # Cubre: T-76
    def test_11_la_fuente_solo_usa_las_claves_que_vienen(self) -> None:
        self.assertEqual(mapeador.formatear_fuente("a.pdf", {"pagina": 2, "tabla": 1, "fila": 3}), "a.pdf, p. 2, tabla 1, fila 3")
        self.assertEqual(mapeador.formatear_fuente("a.xlsx", {"hoja": "Resumen", "celda": "C4"}), "a.xlsx, hoja Resumen, celda C4")
        self.assertEqual(mapeador.formatear_fuente("a.pdf", {}), "a.pdf")


class ComprobacionContraElContratoTest(unittest.TestCase):
    # Cubre: T-76
    def test_20_c1_y_c2_cumplen_el_esquema_real_sin_correcciones_ni_cambios_de_cantidad(self) -> None:
        for caso in ("C1", "C2"):
            informe = mapeador.comprobar_contra_contrato(_mapear(caso).payload)
            self.assertEqual(informe["errores_de_esquema"], [], caso)
            self.assertEqual((informe["rechazo"], informe["correcciones"], informe["avisos_del_puente"]), ("", [], []), caso)
            self.assertEqual(informe["diferencias_del_importador"], [], caso)

    # Cubre: T-76
    def test_21_la_comprobacion_no_modifica_la_carga(self) -> None:
        payload = _mapear("C1").payload
        copia = copy.deepcopy(payload)
        mapeador.comprobar_contra_contrato(payload)
        self.assertEqual(payload, copia)

    # Cubre: T-76
    def test_22_el_esquema_real_rechaza_lo_que_no_cumple_y_se_informa(self) -> None:
        payload = _mapear("C1").payload
        payload["status"] = "Cotización lista"
        self.assertTrue(mapeador.comprobar_contra_contrato(payload)["errores_de_esquema"])
        self.assertTrue(mapeador.comprobar_contra_contrato(payload)["rechazo"])
        sin_hilo = {**_mapear("C1").payload, "thread_id": ""}
        self.assertTrue(mapeador.comprobar_contra_contrato(sin_hilo)["rechazo"])

    # Cubre: T-76
    def test_23_se_informa_cuando_el_importador_cambiaria_la_unidad(self) -> None:
        datos = _extraccion_c3_sintetica()
        datos["actividades"] = [{"nombre": "Área auxiliar", "cantidad": "48250", "unidad": "m2", "entregable": "x",
                                 "origen": {"hoja": "Resumen", "celda": "C4"}}]
        payload = mapeador.mapear(datos, CONTEXTO, "c.xlsx").payload
        self.assertEqual(payload["activities"][0]["unit"], "m2", "el mapeador conserva la unidad literal")
        cambios = mapeador.comprobar_contra_contrato(payload)["diferencias_del_importador"]
        self.assertEqual([(c["campo"], c["antes"], c["despues"]) for c in cambios], [("unit", "m2", "m²")])


class AislamientoTest(unittest.TestCase):
    # Cubre: T-76
    def test_30_el_mapeador_no_toca_produccion_ni_las_evidencias(self) -> None:
        lineas = (BANCO / "mapeador.py").read_text(encoding="utf-8").splitlines()
        importaciones = " ".join(linea for linea in lineas if linea.startswith(("import ", "from ")))
        for prohibido in ("sqlite3", "google", "anka.infra", "anka.servicios", "anka.web", "anka.cli", "requests", "urllib"):
            self.assertNotIn(prohibido, importaciones, prohibido)
        self.assertIn("anka.dominio", importaciones, "solo funciones puras del dominio")
        anka = Path(__file__).resolve().parent.parent / "anka"
        for ruta in anka.rglob("*.py"):
            texto = ruta.read_text(encoding="utf-8")
            self.assertNotIn("from laboratorio", texto, ruta.name)
            self.assertNotIn("import laboratorio", texto, ruta.name)
        manifiesto = json.loads((EVIDENCIAS / "manifiesto_evidencias.json").read_text(encoding="utf-8"))
        import hashlib

        for entrada in manifiesto["archivos"]:
            self.assertEqual(hashlib.sha256((EVIDENCIAS / entrada["archivo"]).read_bytes()).hexdigest(), entrada["sha256"])

    # Cubre: T-76
    def test_31_el_plan_integrado_declara_el_mapeador_y_sus_limites(self) -> None:
        plan = (BANCO / "PLAN_PRUEBA_INTEGRADA.md").read_text(encoding="utf-8")
        for texto in ("T-76", "mapeador", "Verificable automáticamente", "Requiere revisión humana", "Autorización expresa"):
            self.assertIn(texto, plan)
        self.assertIn("DISEÑADO, NO EJECUTADO", plan)


if __name__ == "__main__":
    unittest.main()
