"""Pruebas del validador estructural, la propuesta v2 del banco y las evidencias de T-72 a T-74 (T-75, solo laboratorio)."""

from __future__ import annotations

import copy
import hashlib
import io
import json
import tempfile
import unittest
from pathlib import Path

from laboratorio.banco_documental import comparador_v2, referencias_v2, validador

BANCO = Path(__file__).resolve().parent.parent / "laboratorio" / "banco_documental"
EVIDENCIAS = BANCO / "evidencias_t72_t74"
DOCUMENTOS = {"C1": "solicitud_01.pdf", "C2": "tdr_02.pdf", "C3": "cantidades_03.xlsx"}


def _respuesta(caso: str) -> bytes:
    return (EVIDENCIAS / f"respuesta_work_{caso}.raw.txt").read_bytes()


def _documento(caso: str) -> bytes:
    return (BANCO / "documentos" / DOCUMENTOS[caso]).read_bytes()


def _referencia_v2(caso: str) -> dict:
    return json.loads((BANCO / "referencias_v2" / f"{caso}.referencia.v2.json").read_text(encoding="utf-8"))


def _minima(caso: str = "C1") -> dict:
    """Una extracción mínima y estructuralmente válida, para variar un campo por prueba."""
    return {"caso": caso, "actividades": [{"nombre": "Levantamiento", "cantidad": "18,5", "unidad": "ha", "entregable": "x",
                                           "origen": {"pagina": 1, "tabla": 1, "fila": 1}}],
            "entregables": [], "restricciones": [], "contradicciones": [], "preguntas_cliente": [],
            "revisiones_internas": [], "elementos_visuales": [], "limitaciones_de_lectura": ["ninguna"]}


def _bytes(valor: object) -> bytes:
    return json.dumps(valor, ensure_ascii=False).encode("utf-8")


def _codigos(resultado: validador.Resultado) -> set[str]:
    return {h.codigo for h in resultado.hallazgos}


class EvidenciasTest(unittest.TestCase):
    # Cubre: T-75
    def test_01_las_evidencias_conservadas_coinciden_con_su_manifiesto(self) -> None:
        manifiesto = json.loads((EVIDENCIAS / "manifiesto_evidencias.json").read_text(encoding="utf-8"))
        for entrada in manifiesto["archivos"]:
            self.assertEqual(hashlib.sha256((EVIDENCIAS / entrada["archivo"]).read_bytes()).hexdigest(), entrada["sha256"])
        for entrada in manifiesto["documentos"]:
            self.assertEqual(hashlib.sha256((BANCO / "documentos" / entrada["archivo"]).read_bytes()).hexdigest(),
                             entrada["sha256"])
        for entrada in manifiesto["referencias_v1"]:
            self.assertEqual(hashlib.sha256((BANCO / "referencias" / entrada["archivo"]).read_bytes()).hexdigest(),
                             entrada["sha256"], "la referencia v1 está congelada")
        esperados = {"respuesta_work_C2.raw.txt": "c8a7015d3eef358364610d620cd75030b4d84bfea67df7423fe089e9133c9476",
                     "respuesta_work_C3.raw.txt": "5d24a4883d21d4bac1edfa594aa758f03210f1f863cfc631949b48debe4e716d"}
        for nombre, huella in esperados.items():
            self.assertEqual(hashlib.sha256((EVIDENCIAS / nombre).read_bytes()).hexdigest(), huella)


class ValidadorTest(unittest.TestCase):
    # Cubre: T-75
    def test_10_c3_se_rechaza_por_sintaxis_con_el_error_exacto(self) -> None:
        resultado = validador.validar(_respuesta("C3"), "C3")
        self.assertEqual(resultado.estado, validador.SINTAXIS_INVALIDA)
        self.assertFalse(resultado.aceptada)
        error = resultado.error_de_sintaxis
        assert error is not None
        self.assertEqual((error["linea"], error["columna"], error["posicion"]), (378, 37, 12754))
        self.assertIn("fullCalcOnLoad", error["texto_de_la_linea"])
        self.assertIsNone(resultado.datos, "no se entrega contenido de una respuesta inválida")

    # Cubre: T-75
    def test_11_c1_y_c2_son_aceptadas_solo_estructuralmente(self) -> None:
        for caso in ("C1", "C2"):
            resultado = validador.validar(_respuesta(caso), caso, _documento(caso), DOCUMENTOS[caso])
            self.assertEqual(resultado.estado, validador.VALIDA_CON_ADVERTENCIAS, caso)
            self.assertTrue(resultado.aceptada)
            self.assertIn("aviso", resultado.como_dict())
        self.assertIn("limitaciones_vacias", _codigos(validador.validar(_respuesta("C1"), "C1")))
        self.assertIn("tipo_inesperado_opcional", _codigos(validador.validar(_respuesta("C2"), "C2")))

    # Cubre: T-75
    def test_12_cuarentena_conserva_los_bytes_y_no_importa_nada(self) -> None:
        contenido = _respuesta("C3")
        with tempfile.TemporaryDirectory() as carpeta:
            destino = Path(carpeta)
            resultado = validador.procesar(contenido, destino, "C3")
            self.assertEqual((destino / "originales" / f"{resultado.sha256}.raw").read_bytes(), contenido)
            self.assertTrue((destino / "cuarentena" / f"{resultado.sha256}.motivo.json").is_file())
            self.assertFalse((destino / "validadas").exists(), "lo inválido nunca llega a validadas/")
            informe = json.loads((destino / "informes" / f"{resultado.sha256}.json").read_text(encoding="utf-8"))
            self.assertEqual(informe["error_de_sintaxis"]["linea"], 378)
            validador.procesar(contenido, destino, "C3")                       # idempotente
            self.assertEqual(len(list((destino / "originales").iterdir())), 1)

    # Cubre: T-75
    def test_13_lo_aceptado_se_copia_byte_a_byte_a_validadas(self) -> None:
        contenido = _respuesta("C1")
        with tempfile.TemporaryDirectory() as carpeta:
            resultado = validador.procesar(contenido, Path(carpeta), "C1", _documento("C1"), "solicitud_01.pdf")
            self.assertEqual((Path(carpeta) / "validadas" / f"{resultado.sha256}.json").read_bytes(), contenido)
            self.assertFalse((Path(carpeta) / "cuarentena").exists())

    # Cubre: T-75
    def test_14_no_se_repara_en_silencio_y_la_validacion_json_no_basta(self) -> None:
        crudo = _respuesta("C3").decode("utf-8")
        reparada = crudo.replace('fullCalcOnLoad="1"', 'fullCalcOnLoad=\\"1\\"').encode("utf-8")      # solo en memoria
        self.assertNotEqual(validador.sha256(reparada), validador.sha256(_respuesta("C3")))
        resultado = validador.validar(reparada, "C3")
        self.assertEqual(resultado.estado, validador.ESQUEMA_INVALIDO, "aun con JSON válido, el esquema falla")
        rutas = {h.ruta.split("[")[0] for h in resultado.errores()}
        self.assertLessEqual({"entregables", "restricciones", "contradicciones"}, rutas)
        self.assertIsNone(resultado.datos)

    # Cubre: T-75
    def test_15_distingue_sintaxis_esquema_y_contenido(self) -> None:
        self.assertEqual(validador.validar(b"{no es json").estado, validador.SINTAXIS_INVALIDA)
        incompleta = _minima()
        del incompleta["preguntas_cliente"]
        self.assertEqual(validador.validar(_bytes(incompleta)).estado, validador.ESQUEMA_INVALIDO)
        self.assertEqual(validador.validar(_bytes(_minima())).estado, validador.VALIDA)
        inexistente = _minima("C3")
        inexistente["actividades"][0]["origen"] = {"hoja": "Fantasma", "celda": "A1"}
        resultado = validador.validar(_bytes(inexistente), "C3", _documento("C3"), "cantidades_03.xlsx")
        self.assertEqual(resultado.estado, validador.CONTENIDO_INCONSISTENTE)
        self.assertIn("fuente_inexistente", _codigos(resultado))

    # Cubre: T-75
    def test_16_tipos_y_campos_obligatorios(self) -> None:
        casos = {
            "cantidad numerica": lambda d: d["actividades"][0].update(cantidad=18.5),
            "cantidad vacia": lambda d: d["actividades"][0].update(cantidad="  "),
            "pagina booleana": lambda d: d["actividades"][0]["origen"].update(pagina=True),
            "pagina cero": lambda d: d["actividades"][0]["origen"].update(pagina=0),
            "origen sin localizador": lambda d: d["actividades"][0].update(origen={"nota": "x"}),
            "sin origen": lambda d: d["actividades"][0].pop("origen"),
            "sin unidad": lambda d: d["actividades"][0].pop("unidad"),
            "lista de objetos": lambda d: d.update(entregables=[{"descripcion": "x"}]),
            "caso mal escrito": lambda d: d.update(caso="caso 1"),
            "actividad no objeto": lambda d: d.update(actividades=["texto"]),
        }
        for nombre, cambio in casos.items():
            datos = _minima()
            cambio(datos)
            self.assertEqual(validador.validar(_bytes(datos)).estado, validador.ESQUEMA_INVALIDO, nombre)
        self.assertEqual(validador.validar(_bytes(_minima("C2")), "C1").estado, validador.ESQUEMA_INVALIDO)
        self.assertEqual(validador.validar(b"[1, 2]").estado, validador.ESQUEMA_INVALIDO)

    # Cubre: T-75
    def test_17_opcionales_con_otro_tipo_solo_advierten(self) -> None:
        datos = _minima()
        datos.update(calidad_ocr=[{"pagina": 1}], hojas={"x": 1}, extra_inventado=True)
        resultado = validador.validar(_bytes(datos))
        self.assertEqual(resultado.estado, validador.VALIDA_CON_ADVERTENCIAS)
        self.assertEqual({"tipo_inesperado_opcional", "campo_desconocido"}, _codigos(resultado))

    # Cubre: T-75
    def test_18_sintaxis_dura_clave_repetida_nan_bom_y_utf8(self) -> None:
        self.assertEqual(validador.validar(b'{"caso": "C1", "caso": "C2"}').error_de_sintaxis["tipo"], "clave_duplicada")
        self.assertEqual(validador.validar(b'{"caso": NaN}').error_de_sintaxis["tipo"], "constante")
        self.assertEqual(validador.validar(b"\xef\xbb\xbf{}").error_de_sintaxis["tipo"], "bom")
        self.assertEqual(validador.validar(b'{"caso": "\xff"}').error_de_sintaxis["tipo"], "utf8")
        self.assertEqual(validador.validar(b"").estado, validador.SINTAXIS_INVALIDA)
        self.assertEqual(validador.validar(b"[" * 5000).estado, validador.SINTAXIS_INVALIDA)

    # Cubre: T-75
    def test_19_fuentes_verificables_en_excel_y_pdf(self) -> None:
        bueno = _minima("C3")
        bueno["actividades"][0].update(cantidad="33.7", unidad="ha", origen={"hoja": "Resumen", "celda": "C4"})
        self.assertEqual(validador.validar(_bytes(bueno), "C3", _documento("C3"), "cantidades_03.xlsx").estado,
                         validador.VALIDA)
        for origen, codigo in (({"hoja": "Resumen", "celda": "Z99"}, "fuente_inexistente"),
                               ({"hoja": "Resumen", "celda": "A20:B21"}, "fuente_inexistente"),
                               ({"hoja": "Resumen", "celda": "C7"}, "valor_no_coincide_con_la_fuente")):
            malo = copy.deepcopy(bueno)
            malo["actividades"][0]["origen"] = origen
            resultado = validador.validar(_bytes(malo), "C3", _documento("C3"), "cantidades_03.xlsx")
            self.assertEqual(resultado.estado, validador.CONTENIDO_INCONSISTENTE, origen)
            self.assertIn(codigo, _codigos(resultado))
        pdf = _minima("C1")
        pdf["actividades"][0]["origen"] = {"pagina": 3}
        self.assertIn("fuente_inexistente", _codigos(validador.validar(_bytes(pdf), "C1", _documento("C1"), "solicitud_01.pdf")))

    # Cubre: T-75
    def test_20_duplicados_y_sin_actividades_advierten(self) -> None:
        datos = _minima()
        datos["actividades"].append(copy.deepcopy(datos["actividades"][0]))
        self.assertIn("actividad_duplicada", _codigos(validador.validar(_bytes(datos))))
        datos["actividades"] = []
        self.assertIn("sin_actividades", _codigos(validador.validar(_bytes(datos))))

    # Cubre: T-75
    def test_21_un_archivo_existente_con_otro_contenido_no_se_pisa(self) -> None:
        with tempfile.TemporaryDirectory() as carpeta:
            ruta = Path(carpeta) / "x.raw"
            validador._escribir_una_vez(ruta, b"uno")
            validador._escribir_una_vez(ruta, b"uno")
            with self.assertRaises(FileExistsError):
                validador._escribir_una_vez(ruta, b"otro")
            self.assertEqual(ruta.read_bytes(), b"uno")

    # Cubre: T-75
    def test_22_la_linea_de_comandos_devuelve_cero_solo_si_se_acepta(self) -> None:
        with tempfile.TemporaryDirectory() as carpeta:
            base = Path(carpeta)
            bueno, malo = base / "bueno.txt", base / "malo.txt"
            bueno.write_bytes(_bytes(_minima()))
            malo.write_bytes(b"{")
            salida = io.StringIO()
            import contextlib

            with contextlib.redirect_stdout(salida):
                self.assertEqual(validador.main([str(bueno), str(base / "a"), "--caso", "C1"]), 0)
                self.assertEqual(validador.main([str(malo), str(base / "b")]), 1)
                self.assertEqual(validador.main([str(malo)]), 2)
            self.assertTrue((base / "b" / "cuarentena").is_dir())


class PropuestaV2Test(unittest.TestCase):
    # Cubre: T-75
    def test_30_las_referencias_v2_se_derivan_de_las_v1_congeladas(self) -> None:
        self.assertEqual(referencias_v2.verificar(), [])
        for caso in DOCUMENTOS:
            v2 = _referencia_v2(caso)
            v1_bytes = (BANCO / "referencias" / f"{caso}.referencia.json").read_bytes()
            self.assertEqual(v2["deriva_de"]["sha256"], hashlib.sha256(v1_bytes).hexdigest())
            self.assertEqual(v2["version"], "2")
            self.assertEqual(v2["documento"]["sha256"], json.loads(v1_bytes)["documento"]["sha256"])
            self.assertTrue(v2["cambios"])
        textos = (BANCO / "CAMBIOS_V2.md").read_text(encoding="utf-8")
        for caso in DOCUMENTOS:
            for cambio in _referencia_v2(caso)["cambios"]:
                self.assertIn(cambio["id"], textos)
                self.assertTrue(cambio["por_que"] and cambio["efecto"] and cambio["origen_del_cambio"])

    # Cubre: T-75
    def test_31_c2_ya_no_exige_entregables_que_el_documento_no_tiene(self) -> None:
        v2 = _referencia_v2("C2")
        self.assertTrue(all(a["entregable"] == "" and a["grupos_entregable"] == [] for a in v2["actividades"]))
        v1 = json.loads((BANCO / "referencias" / "C2.referencia.json").read_text(encoding="utf-8"))
        self.assertTrue(any(a["entregable"] for a in v1["actividades"]), "la v1 sigue como estaba")

    # Cubre: T-75
    def test_32_c3_acepta_coma_o_punto_decimal(self) -> None:
        r1 = _referencia_v2("C3")["restricciones"][0]
        for texto in ("Equidistancia de curvas: 0.5 m", "Equidistancia de curvas: 0,5 m"):
            self.assertTrue(comparador_v2.coincide([texto], r1["grupos"]), texto)
        self.assertFalse(comparador_v2.coincide(["Equidistancia de curvas: 5 m"], r1["grupos"]))

    # Cubre: T-75
    def test_33_una_sola_palabra_ya_no_basta(self) -> None:
        f3 = next(f for f in _referencia_v2("C1")["faltantes"] if f["id"] == "C1-F3")
        self.assertFalse(comparador_v2.coincide(["¿Cuántos puntos hay?"], f3["grupos"]))
        self.assertFalse(comparador_v2.coincide(["Son 12 puntos"], f3["grupos"]))
        self.assertTrue(comparador_v2.coincide(["¿Son 12 o 10 puntos de control?"], f3["grupos"]))
        self.assertFalse(comparador_v2.coincide(["cualquier cosa"], []))

    # Cubre: T-75
    def test_34_efecto_de_la_v2_sobre_las_respuestas_originales(self) -> None:
        c1 = comparador_v2.evaluar_respuesta(_respuesta("C1"), _referencia_v2("C1"), None, _documento("C1"), "solicitud_01.pdf")
        self.assertEqual((c1["errores_criticos"], c1["errores_no_criticos"], c1["actividades_con_valor_y_unidad_correctos"]),
                         (0, 1, 5))
        self.assertEqual([h["codigo"] for h in c1["hallazgos"] if h["gravedad"] == "no_critico"], ["ambiguedad_no_declarada"])
        c2 = comparador_v2.evaluar_respuesta(_respuesta("C2"), _referencia_v2("C2"), None, _documento("C2"), "tdr_02.pdf")
        self.assertEqual((c2["errores_criticos"], c2["errores_no_criticos"], c2["para_revision_humana"]), (0, 0, 6))
        self.assertEqual(c2["aciertos_adicionales"], 1)
        self.assertTrue(all(h["verificacion"] == "humana" for h in c2["hallazgos"] if h["gravedad"] == "revision_humana"))
        self.assertIn("no es una aprobación", c2["aviso"])

    # Cubre: T-75
    def test_35_una_respuesta_invalida_no_se_compara(self) -> None:
        informe = comparador_v2.evaluar_respuesta(_respuesta("C3"), _referencia_v2("C3"))
        self.assertEqual(informe["estado"], comparador_v2.NO_EVALUABLE_POR_FORMATO)
        self.assertFalse(informe["sin_errores_criticos_automaticos"])
        self.assertNotIn("hallazgos", informe)
        self.assertEqual(informe["validacion"]["error_de_sintaxis"]["linea"], 378)

    # Cubre: T-75
    def test_36_validar_la_estructura_no_aprueba_una_cifra_equivocada(self) -> None:
        datos = _minima("C1")
        datos["actividades"] = [{"nombre": "Levantamiento topográfico planimétrico y altimétrico del área de intervención",
                                 "cantidad": "21,0", "unidad": "ha", "entregable": "Plano en DWG y PDF",
                                 "origen": {"pagina": 1, "tabla": 1, "fila": 1}}]
        informe = comparador_v2.evaluar_respuesta(_bytes(datos), _referencia_v2("C1"))
        self.assertTrue(informe["validacion"]["aceptada"])
        self.assertGreaterEqual(informe["errores_criticos"], 1)
        self.assertFalse(informe["sin_errores_criticos_automaticos"])

    # Cubre: T-75
    def test_37_lo_no_previsto_va_a_revision_humana_y_no_es_error(self) -> None:
        extraccion = _minima("C1")
        extraccion["preguntas_cliente"] = ["¿Pueden confirmar quién es el contacto en obra?"]
        informe = comparador_v2.comparar_v2(_referencia_v2("C1"), extraccion)
        previstas = [h for h in informe["hallazgos"] if h["codigo"] == "pregunta_no_prevista_para_revision_humana"]
        self.assertEqual(len(previstas), 1)
        self.assertEqual(previstas[0]["verificacion"], "humana")
        self.assertNotIn("pregunta_de_mas", {h["codigo"] for h in informe["hallazgos"]})

    # Cubre: T-75
    def test_38_el_sistema_de_coordenadas_se_acepta_como_revision_interna(self) -> None:
        extraccion = _minima("C1")
        extraccion["revisiones_internas"] = ["Registrar que el sistema de coordenadas se definirá en la reunión de inicio."]
        informe = comparador_v2.comparar_v2(_referencia_v2("C1"), extraccion)
        self.assertNotIn("C1-F2", {h["ref"] for h in informe["hallazgos"] if h["codigo"] == "faltante_sin_reconocer"})
        sin = comparador_v2.comparar_v2(_referencia_v2("C1"), _minima("C1"))
        self.assertIn("C1-F2", {h["ref"] for h in sin["hallazgos"] if h["codigo"] == "faltante_sin_reconocer"})

    # Cubre: T-75
    def test_39_el_faltante_bloqueante_sigue_siendo_critico_y_las_v1_no_cambian(self) -> None:
        informe = comparador_v2.comparar_v2(_referencia_v2("C1"), _minima("C1"))
        criticos = {h["ref"] for h in informe["hallazgos"] if h["gravedad"] == "critico"}
        self.assertLessEqual({"C1-F1", "C1-K1"}, criticos)
        from laboratorio.banco_documental import comparador

        self.assertEqual(comparador.CRITICO, "critico")
        self.assertFalse((BANCO / "referencias" / "C1.referencia.v2.json").exists())


class PlanTest(unittest.TestCase):
    # Cubre: T-75
    def test_40_el_plan_integrado_esta_diseñado_y_no_ejecutado(self) -> None:
        plan = (BANCO / "PLAN_PRUEBA_INTEGRADA.md").read_text(encoding="utf-8")
        self.assertIn("DISEÑADO, NO EJECUTADO", plan)
        for etapa in ("E0", "E5", "E9", "N1", "N2", "Condiciones de detención"):
            self.assertIn(etapa, plan)
        self.assertNotRegex(plan, r"ANKAJOB-[0-9a-f]{16}\b")           # ningún job_id real


if __name__ == "__main__":
    unittest.main()
