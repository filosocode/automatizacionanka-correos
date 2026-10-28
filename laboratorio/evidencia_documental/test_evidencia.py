"""Pruebas del modelo de evidencia documental y de su validador (T-117). Solo laboratorio: no toca Gmail, Drive, Work ni SQLite."""

import copy
import json
import unittest
from pathlib import Path

from laboratorio.evidencia_documental import adaptador_work, casos, esquema, fuentes
from laboratorio.evidencia_documental import validador as v

AQUI = Path(__file__).resolve().parent


def validar(caso, mutar=None, paquete=None):
    doc = casos.copia(caso)
    if mutar:
        mutar(doc)
    return v.validar(doc, paquete or casos.paquete(caso))


def evid(doc, id_):
    return next(e for e in doc["evidencias"] if e["id"] == id_)


class Positivas(unittest.TestCase):
    def test_01_la_evidencia_de_referencia_es_valida_en_los_tres_casos(self):
        for caso in ("C1", "C2", "C3"):
            with self.subTest(caso):
                self.assertEqual([], [str(h) for h in v.errores(validar(caso))])

    def test_02_c1_y_c3_pueden_declararse_completos_y_c2_no(self):
        self.assertTrue(casos.copia("C1")["analisis"]["declarado_completo"])
        self.assertTrue(casos.copia("C3")["analisis"]["declarado_completo"])
        c2 = casos.copia("C2")
        self.assertFalse(c2["analisis"]["declarado_completo"])
        self.assertTrue(c2["analisis"]["motivos_de_incompletitud"])

    def test_03_las_cifras_y_unidades_se_conservan_literales(self):
        e = evid(casos.copia("C1"), "E3")
        self.assertEqual(("2,350", "km"), (e["cantidad_literal"], e["unidad_literal"]))
        self.assertEqual(2.35, e["valor_normalizado"]["valor"])   # el normalizado acompaña, no sustituye
        self.assertEqual("33.7", evid(casos.copia("C3"), "E1")["cantidad_literal"])

    def test_04_la_hoja_historica_se_acepta_solo_como_historica(self):
        self.assertEqual("historica", evid(casos.copia("C3"), "E7")["vigencia"])
        self.assertEqual([], v.errores(validar("C3")))

    def test_05_el_validador_no_modifica_lo_que_valida(self):
        doc = casos.copia("C1")
        antes = copy.deepcopy(doc)
        v.validar(doc, casos.paquete("C1"))
        self.assertEqual(antes, doc)

    def test_06_c2_distingue_lo_pendiente_de_lo_ilegible_y_de_lo_no_encontrado(self):
        estados = {e["id"]: e["estado_de_lectura"] for e in casos.copia("C2")["evidencias"]}
        self.assertEqual("pendiente_revision_visual", estados["E1"])
        self.assertEqual("no_encontrado", estados["E8"])
        self.assertEqual("leido", estados["E4"])

    def test_07_las_cuatro_causas_de_no_tener_un_dato_son_estados_distintos(self):
        self.assertEqual({"no_encontrado", "ilegible", "contradictorio", "parcialmente_leido", "pendiente_revision_visual", "leido"},
                         set(esquema.ESTADOS_DE_LECTURA))


class Documento(unittest.TestCase):
    def test_10_huella_alterada(self):
        self.assertIn("huella_distinta", v.codigos(validar("C1", lambda d: d["documentos"][0].update(sha256="0" * 64))))

    def test_11_documento_no_entregado(self):
        paquete = fuentes.PaqueteDeOriginales({})
        self.assertIn("documento_no_entregado", v.codigos(validar("C1", paquete=paquete)))

    def test_12_archivo_recibido_y_no_cubierto_impide_completo(self):
        extra = casos.paquete("C1")
        extra.archivos["otro.pdf"] = extra.archivos["solicitud_01.pdf"]
        self.assertIn("documento_sin_cubrir", v.codigos(validar("C1", paquete=extra)))

    def test_13_cobertura_que_no_cuadra(self):
        self.assertIn("cobertura_inconsistente", v.codigos(validar("C1", lambda d: d["documentos"][0]["cobertura"].update(total=5))))
        self.assertIn("cobertura_inconsistente", v.codigos(validar("C1", lambda d: d["documentos"][0]["cobertura"].update(leidas=1))))

    def test_14_pdf_digital_declarado_con_paginas_sin_texto(self):
        self.assertIn("tipo_de_documento_incorrecto", v.codigos(validar("C2", lambda d: d["documentos"][0].update(tipo="pdf_digital"))))


class Localizadores(unittest.TestCase):
    def test_20_pagina_inexistente(self):
        for pagina in (3, 0):
            with self.subTest(pagina):
                self.assertIn("pagina_inexistente", v.codigos(validar("C1", lambda d, pagina=pagina: evid(d, "E1")["localizador"].update(pagina=pagina))))

    def test_21_pagina_equivocada_no_respalda_la_cifra(self):
        # «10 puntos» está en la página 2: citarlo en la 1 no es evidencia
        self.assertIn("cantidad_no_respaldada", v.codigos(validar("C1", lambda d: evid(d, "E5")["localizador"].update(pagina=1))))

    def test_22_hoja_inexistente(self):
        self.assertIn("hoja_inexistente", v.codigos(validar("C3", lambda d: evid(d, "E1")["localizador"].update(hoja="Totales"))))

    def test_23_rango_fuera_de_la_hoja(self):
        self.assertIn("rango_fuera_de_la_hoja", v.codigos(validar("C3", lambda d: evid(d, "E1")["localizador"].update(rango="B4:E40"))))

    def test_24_celda_mal_escrita(self):
        for ref in ("A0:B2", "ZZZZ", "B4:"):
            with self.subTest(ref):
                self.assertTrue(v.codigos(validar("C3", lambda d, ref=ref: evid(d, "E1")["localizador"].update(rango=ref))) &
                                {"celda_invalida", "rango_fuera_de_la_hoja"})

    def test_25_rango_equivocado_no_respalda_la_cifra(self):
        # B5:E5 es la fila de ejes viales (3.625 km); 33.7 ha no está ahí
        self.assertIn("cantidad_no_respaldada", v.codigos(validar("C3", lambda d: evid(d, "E1")["localizador"].update(rango="B5:E5"))))

    def test_26_hoja_oculta_citada_como_vigente(self):
        self.assertIn("hoja_no_vigente", v.codigos(validar("C3", lambda d: evid(d, "E7").update(vigencia="vigente"))))

    def test_27_pagina_en_un_excel_y_hoja_en_un_pdf(self):
        self.assertIn("localizador_mixto", v.codigos(validar("C3", lambda d: evid(d, "E1")["localizador"].update(pagina=1))))
        self.assertIn("localizador_mixto", v.codigos(validar("C1", lambda d: evid(d, "E1")["localizador"].update(hoja="Resumen"))))

    def test_28_sin_localizador(self):
        self.assertIn("localizador_ausente", v.codigos(validar("C1", lambda d: evid(d, "E1").pop("localizador"))))

    def test_29_tabla_y_fila_de_un_pdf_solo_avisan(self):
        h = validar("C1")
        self.assertIn("localizador_fino_no_verificable", v.codigos(h, v.AVISO))
        self.assertNotIn("localizador_fino_no_verificable", v.codigos(h))


class ValoresInventados(unittest.TestCase):
    def test_30_cantidad_inventada(self):
        self.assertIn("cantidad_no_respaldada", v.codigos(validar("C1", lambda d: evid(d, "E1").update(cantidad_literal="19,5"))))
        self.assertIn("cantidad_no_respaldada", v.codigos(validar("C3", lambda d: evid(d, "E1").update(cantidad_literal="34.7"))))

    def test_31_una_cifra_parcial_no_vale(self):
        # «8,5» está dentro de «18,5»; «1» dentro de «12»
        self.assertIn("cantidad_no_respaldada", v.codigos(validar("C1", lambda d: evid(d, "E1").update(cantidad_literal="8,5"))))
        # «1» sí aparece suelto en la página («1. OBJETO»), pero no seguido de «puntos»: tampoco se acepta
        self.assertTrue(v.codigos(validar("C1", lambda d: evid(d, "E4").update(cantidad_literal="1"))) & {"cantidad_no_respaldada", "unidad_alterada"})

    def test_32_unidad_modificada(self):
        self.assertIn("unidad_alterada", v.codigos(validar("C1", lambda d: evid(d, "E1").update(unidad_literal="km"))))
        self.assertIn("unidad_no_respaldada", v.codigos(validar("C3", lambda d: evid(d, "E1").update(unidad_literal="m2"))))

    def test_33_m_no_se_acepta_dentro_de_km(self):
        self.assertIn("unidad_alterada", v.codigos(validar("C1", lambda d: evid(d, "E3").update(unidad_literal="m", valor_normalizado=None))))
        self.assertIn("unidad_alterada", v.codigos(validar("C2", lambda d: evid(d, "E5").update(unidad_literal="m"))))

    def test_34_texto_literal_que_no_esta(self):
        self.assertIn("texto_no_encontrado", v.codigos(validar("C1", lambda d: evid(d, "E1").update(texto_literal="Levantamiento satelital"))))
        self.assertIn("texto_no_encontrado", v.codigos(validar("C3", lambda d: evid(d, "E1").update(texto_literal="Pendiente de confirmar"))))

    def test_35_cantidad_numerica_en_vez_de_literal(self):
        self.assertIn("forma_invalida", v.codigos(validar("C1", lambda d: evid(d, "E1").update(cantidad_literal=18.5))))

    def test_36_conteo_derivado_incorrecto(self):
        self.assertIn("cantidad_no_respaldada", v.codigos(validar("C3", lambda d: evid(d, "E6").update(cantidad_literal="5"))))
        self.assertIn("confianza_excesiva", v.codigos(validar("C3", lambda d: evid(d, "E6").update(confianza="alta"))))

    def test_37_cantidad_inventada_en_ocr(self):
        self.assertIn("cantidad_no_respaldada", v.codigos(validar("C2", lambda d: evid(d, "E3").update(cantidad_literal="4"))))

    def test_38_evidencia_de_cantidad_sin_cantidad(self):
        self.assertIn("cantidad_ausente", v.codigos(validar("C1", lambda d: evid(d, "E1").pop("cantidad_literal"))))


class Normalizacion(unittest.TestCase):
    def test_40_normalizado_sin_regla(self):
        self.assertIn("normalizado_sin_regla", v.codigos(validar("C1", lambda d: evid(d, "E3")["valor_normalizado"].pop("regla"))))

    def test_41_normalizado_inconsistente(self):
        self.assertIn("normalizado_inconsistente", v.codigos(validar("C1", lambda d: evid(d, "E3")["valor_normalizado"].update(valor=23.5))))

    def test_42_cifra_ambigua_sin_declarar(self):
        self.assertIn("normalizado_ambiguo", v.codigos(validar("C1", lambda d: evid(d, "E3").pop("ambiguedad"))))

    def test_43_conversion_sin_factor_o_con_la_misma_unidad(self):
        def mutar(d):
            evid(d, "E1")["valor_normalizado"] = {"valor": 185000.0, "unidad": "m2", "regla": "conversion_declarada"}
        self.assertIn("normalizado_inconsistente", v.codigos(validar("C1", mutar)))

    def test_44_conversion_declarada_correcta(self):
        def mutar(d):
            evid(d, "E1")["valor_normalizado"] = {"valor": 185000.0, "unidad": "m2", "regla": "conversion_declarada", "factor": 10000}
        self.assertEqual([], v.errores(validar("C1", mutar)))


class MetodosYVisual(unittest.TestCase):
    def test_50_texto_nativo_en_una_pagina_escaneada(self):
        self.assertIn("metodo_incompatible", v.codigos(validar("C2", lambda d: evid(d, "E3").update(metodo="texto_nativo"))))

    def test_51_ocr_sin_texto_ocr_en_el_paquete(self):
        sin_ocr = fuentes.PaqueteDeOriginales(dict(casos.paquete("C2").archivos))
        self.assertIn("metodo_incompatible", v.codigos(validar("C2", paquete=sin_ocr)))

    def test_52_celda_en_un_pdf_y_ocr_en_un_excel(self):
        self.assertIn("metodo_incompatible", v.codigos(validar("C1", lambda d: evid(d, "E1").update(metodo="celda"))))
        self.assertIn("metodo_incompatible", v.codigos(validar("C3", lambda d: evid(d, "E1").update(metodo="ocr"))))

    def test_53_el_ocr_no_certifica_interpretacion_visual(self):
        self.assertIn("ocr_certifica_visual", v.codigos(validar("C2", lambda d: evid(d, "V1").update(certifica_interpretacion_visual=True))))

    def test_54_confianza_alta_con_ocr(self):
        self.assertIn("confianza_excesiva", v.codigos(validar("C2", lambda d: evid(d, "E3").update(confianza="alta"))))

    def test_55_lectura_visual_exige_revision_humana(self):
        def mutar(d):
            evid(d, "E3").update(metodo="vision", confianza="media", revision_humana_requerida=False)
        self.assertIn("vision_sin_revision", v.codigos(validar("C2", mutar)))

    def test_56_un_elemento_visual_no_se_da_por_leido(self):
        self.assertIn("elemento_visual_no_pendiente", v.codigos(validar("C2", lambda d: evid(d, "V1").update(estado_de_lectura="leido"))))

    def test_57_un_elemento_visual_solo_lo_cierra_una_persona_con_nombre(self):
        def sin_nombre(d):
            evid(d, "V1").update(estado_de_lectura="leido", metodo="humano")
        def con_nombre(d):
            evid(d, "V1").update(estado_de_lectura="leido", metodo="humano", revisor="Revisor independiente")
        self.assertIn("elemento_visual_no_pendiente", v.codigos(validar("C2", sin_nombre)))
        self.assertNotIn("elemento_visual_no_pendiente", v.codigos(validar("C2", con_nombre)))


class Estados(unittest.TestCase):
    def test_60_ilegible_o_no_encontrado_no_traen_cantidad(self):
        self.assertIn("estado_con_cantidad", v.codigos(validar("C2", lambda d: evid(d, "E8").update(cantidad_literal="3"))))

    def test_61_estado_sin_motivo(self):
        self.assertIn("estado_sin_motivo", v.codigos(validar("C2", lambda d: evid(d, "E8").pop("motivo"))))

    def test_62_parcial_sin_declarar_lo_que_falta(self):
        def mutar(d):
            evid(d, "E3").update(estado_de_lectura="parcialmente_leido")
        self.assertIn("parcial_sin_faltante", v.codigos(validar("C2", mutar)))

    def test_63_pendiente_visual_sin_pedir_revision(self):
        self.assertIn("pendiente_visual_sin_revision", v.codigos(validar("C2", lambda d: evid(d, "E1").update(revision_humana_requerida=False))))

    def test_64_ilegible_exige_revision_y_lugar(self):
        def mutar(d):
            e = evid(d, "E8")
            e.update(estado_de_lectura="ilegible", revision_humana_requerida=False)
            e.pop("localizador", None)
        codigos = v.codigos(validar("C2", mutar))
        self.assertTrue({"estado_inconsistente", "localizador_ausente"} <= codigos)

    def test_65_un_ilegible_declarado_con_honestidad_es_valido_pero_no_completo(self):
        def mutar(d):
            e = evid(d, "E8")
            e.update(estado_de_lectura="ilegible", revision_humana_requerida=True, localizador={"pagina": 2})
            d["analisis"]["declarado_completo"] = True
            d["analisis"]["motivos_de_incompletitud"] = []
        self.assertIn("completitud_falsa", v.codigos(validar("C2", mutar)))


class Completitud(unittest.TestCase):
    def test_70_c2_no_puede_declararse_completo(self):
        def mutar(d):
            d["analisis"].update(declarado_completo=True, motivos_de_incompletitud=[])
        self.assertIn("completitud_falsa", v.codigos(validar("C2", mutar)))

    def test_71_pagina_ilegible_con_analisis_completo(self):
        def mutar(d):
            d["documentos"][0]["cobertura"].update(leidas=1, ilegibles=1)
        self.assertIn("completitud_falsa", v.codigos(validar("C1", mutar)))

    def test_72_incompleto_debe_decir_por_que(self):
        def mutar(d):
            d["analisis"].update(declarado_completo=False, motivos_de_incompletitud=[])
        self.assertIn("incompleto_sin_motivo", v.codigos(validar("C1", mutar)))

    def test_73_completo_con_motivos_se_contradice(self):
        self.assertIn("completitud_falsa", v.codigos(validar("C1", lambda d: d["analisis"].update(motivos_de_incompletitud=["falta algo"]))))

    def test_74_una_evidencia_pendiente_de_revision_visual_impide_completo(self):
        def mutar(d):
            evid(d, "E1").update(estado_de_lectura="pendiente_revision_visual", revision_humana_requerida=True, confianza="media")
        self.assertIn("completitud_falsa", v.codigos(validar("C1", mutar)))


class ContradiccionesEInterpretaciones(unittest.TestCase):
    def test_80_contradiccion_no_declarada(self):
        def mutar(d):
            d["contradicciones"].clear()
            for e in d["evidencias"]:
                if e["estado_de_lectura"] == "contradictorio":
                    e["estado_de_lectura"] = "leido"
        self.assertIn("contradiccion_no_declarada", v.codigos(validar("C1", mutar)))

    def test_81_contradictorio_que_ninguna_contradiccion_cita(self):
        self.assertIn("contradictorio_sin_par", v.codigos(validar("C1", lambda d: d["contradicciones"].clear())))

    def test_82_contradiccion_sin_diferencia_o_con_citas_falsas(self):
        self.assertIn("contradiccion_invalida", v.codigos(validar("C1", lambda d: d["contradicciones"][0].update(evidencias=["E4", "E4"]))))
        self.assertIn("contradiccion_invalida", v.codigos(validar("C1", lambda d: d["contradicciones"][0].update(evidencias=["E4", "E99"]))))
        self.assertIn("contradiccion_sin_diferencia", v.codigos(validar("C1", lambda d: d["contradicciones"][0].update(evidencias=["E4", "E6"]) or evid(d, "E6").update(cantidad_literal="12", unidad_literal="puntos"))))

    def test_83_resuelta_solo_por_una_persona(self):
        def mutar(d):
            d["contradicciones"][0]["estado"] = "resuelta_por_persona"
        self.assertIn("contradiccion_resuelta_sin_persona", v.codigos(validar("C1", mutar)))

    def test_84_cifras_distintas_de_conceptos_distintos_no_son_contradiccion(self):
        # 12 puntos y 12 fotografías por punto comparten la cifra y no el concepto: ninguna alarma
        self.assertNotIn("contradiccion_no_declarada", v.codigos(validar("C1")))

    def test_85_interpretacion_con_cifra_que_ninguna_evidencia_respalda(self):
        self.assertIn("interpretacion_cifra_nueva", v.codigos(validar("C1", lambda d: d["interpretaciones"][0].update(texto="Levantamiento de 19,5 ha."))))

    def test_86_interpretacion_cita_inexistente_o_sin_evidencia(self):
        self.assertIn("interpretacion_cita_inexistente", v.codigos(validar("C1", lambda d: d["interpretaciones"][0].update(evidencias=["E99"]))))
        self.assertIn("interpretacion_sin_evidencia", v.codigos(validar("C1", lambda d: d["interpretaciones"][0].update(evidencias=[]))))

    def test_87_las_referencias_a_tabla_o_pagina_no_cuentan_como_cifras_nuevas(self):
        self.assertNotIn("interpretacion_cifra_nueva", v.codigos(validar("C1", lambda d: d["interpretaciones"][0].update(texto="Ver Tabla 1, ítem 1, p. 1: 18,5 ha."))))


class Forma(unittest.TestCase):
    def test_90_forma_invalida(self):
        for mutar in (lambda d: d.pop("evidencias"), lambda d: evid(d, "E1").update(metodo="adivinar"),
                      lambda d: d["documentos"][0].update(sha256="xyz"), lambda d: d.update(version_modelo="otra"),
                      lambda d: evid(d, "E1").update(revision_humana_requerida="si"), lambda d: d["analisis"].pop("declarado_completo")):
            with self.subTest(mutar):
                self.assertIn("forma_invalida", v.codigos(validar("C1", mutar)))

    def test_91_ids_duplicados(self):
        self.assertIn("id_duplicado", v.codigos(validar("C1", lambda d: evid(d, "E2").update(id="E1"))))

    def test_92_no_es_un_objeto(self):
        self.assertIn("forma_invalida", v.codigos(v.validar([], casos.paquete("C1"))))


class Independencia(unittest.TestCase):
    def test_100_el_validador_no_usa_el_sistema_evaluado_ni_las_referencias(self):
        for nombre in ("validador.py", "fuentes.py", "esquema.py"):
            fuente = (AQUI / nombre).read_text(encoding="utf-8")
            for prohibido in ("import anka", "from anka", "referencias", "comparador", "sqlite", "gmail", "requests", "openai", "urllib"):
                self.assertNotIn(prohibido, fuente.replace("referencias:", ""), f"{nombre} menciona {prohibido}")

    def test_101_las_enumeraciones_del_json_schema_coinciden_con_las_del_validador(self):
        props = esquema.ESQUEMA_JSON["properties"]["evidencias"]["items"]["properties"]
        self.assertEqual(list(esquema.METODOS), props["metodo"]["enum"])
        self.assertEqual(list(esquema.ESTADOS_DE_LECTURA), props["estado_de_lectura"]["enum"])
        self.assertEqual(list(esquema.VIGENCIAS), props["vigencia"]["enum"])
        self.assertEqual(sorted(esquema.ESQUEMA_JSON["required"]), sorted(esquema._OBLIGATORIOS_RAIZ))

    def test_102_el_texto_ocr_es_el_de_la_prueba_aislada_sobre_el_pdf_correcto(self):
        self.assertEqual(casos.SHA_TDR_02, casos.paquete("C2").sha256("tdr_02.pdf"))
        self.assertEqual({1, 2}, set(casos.paginas_ocr(casos.OCR_TDR_02.read_text(encoding="utf-8"))))


class CompatibilidadConElContratoVigente(unittest.TestCase):
    """Lo que Work ya devuelve, pasado por el adaptador y el validador, sin cambiar el contrato."""

    def _adaptar(self, caso):
        retorno = json.loads((AQUI / "datos" / f"work_{caso}.reducido.json").read_text(encoding="utf-8"))
        antes = copy.deepcopy(retorno)
        doc, sin_mapear = adaptador_work.adaptar(retorno, casos.paquete(caso), caso)
        self.assertEqual(antes, retorno)   # el retorno no se altera
        return doc, sin_mapear, v.validar(doc, casos.paquete(caso))

    def test_110_c1_las_cifras_de_work_se_respaldan_y_lo_parafraseado_se_detecta(self):
        doc, sin_mapear, h = self._adaptar("C1")
        self.assertEqual({"unidad_alterada"}, v.codigos(h))          # unidades escritas con palabras de más, no cantidades falsas
        self.assertFalse(doc["analisis"]["declarado_completo"])        # Work no declaró análisis completo
        self.assertTrue(any("contradicción" in s for s in sin_mapear))   # las contradicciones del contrato no se enlazan a evidencias

    def test_111_c3_el_extracto_de_work_es_un_comentario_de_celda_y_se_respalda(self):
        # T-117 lo marcó como error por no mirar comentarios (falso positivo del validador, corregido en T-118)
        doc, _, h = self._adaptar("C3")
        self.assertEqual([], [str(x) for x in v.errores(h)])
        extracto = next(e for e in doc["evidencias"] if e.get("texto_literal") == "Pendiente de confirmar con el cliente.")
        self.assertEqual("C9", fuentes.PaqueteDeOriginales(casos.paquete("C3").archivos).libro("cantidades_03.xlsx")["Resumen"].comentarios and "C9")
        self.assertIn("B9", extracto["localizador"]["rango"])

    def test_112_c2_work_declaro_el_adjunto_sin_leer_y_el_adaptador_no_inventa_nada(self):
        doc, _, h = self._adaptar("C2")
        self.assertEqual([], v.errores(h))
        self.assertEqual({"ilegible"}, {e["estado_de_lectura"] for e in doc["evidencias"]})
        self.assertEqual(2, doc["documentos"][0]["cobertura"]["ilegibles"])
        self.assertFalse(doc["analisis"]["declarado_completo"])

    def test_113_la_evidencia_se_puede_llevar_en_un_campo_nuevo_sin_tocar_el_esquema_vigente(self):
        esquema_work = json.loads((AQUI.parent.parent / "spec" / "contrato_work.schema.json").read_text(encoding="utf-8"))
        self.assertNotIn("additionalProperties", esquema_work)   # el esquema vigente no prohíbe campos nuevos en la raíz
        self.assertNotIn("evidencia_documental", esquema_work.get("properties", {}))

    def test_114_el_validador_del_contrato_vigente_ignora_un_campo_nuevo(self):
        from anka.dominio import work_contract   # solo lectura: función pura del dominio, sin tocar nada
        base = {"thread_id": "t", "message_id": "m", "client_email": "a@ejemplo.invalid", "project_name": "p",
                "status": "Información incompleta", "engine": "chatgpt_work"}
        con_campo = {**base, "evidencia_documental": {"version_modelo": esquema.VERSION_MODELO},
                     "attachments": [{"filename": "a.pdf", "evidencia_documental": {"x": 1}}]}
        self.assertEqual(work_contract.validate(base), work_contract.validate(con_campo))


class LineaBase(unittest.TestCase):
    def test_120_la_linea_base_congelada_es_coherente_con_los_documentos_y_las_referencias(self):
        base = json.loads((AQUI / "datos" / "linea_base_t117.json").read_text(encoding="utf-8"))
        for caso, datos in base["casos"].items():
            with self.subTest(caso):
                archivo = casos.ARCHIVOS[caso]
                self.assertEqual(casos.paquete(caso).sha256(archivo), datos["sha256_documento"])
                self.assertEqual(archivo, datos["documento"])
                self.assertLessEqual(datos["actividades"]["encontradas"], datos["actividades"]["esperadas"])
                self.assertEqual(datos["contradicciones"]["exigidas"], datos["contradicciones"]["omitidas"] + datos["contradicciones"]["detectadas"])
        self.assertEqual("NO aprobado documentalmente", base["casos"]["C2"]["aprobacion_documental"])
        self.assertEqual(0, base["casos"]["C2"]["actividades"]["obtenidas"])

    def test_121_la_linea_base_no_lleva_correos_ni_datos_de_clientes(self):
        for ruta in (AQUI / "datos").glob("*.json"):
            self.assertNotIn("@", ruta.read_text(encoding="utf-8"), ruta.name)

    def test_122_el_json_schema_publicado_es_el_del_codigo(self):
        publicado = json.loads((AQUI / "esquema_evidencia.schema.json").read_text(encoding="utf-8"))
        self.assertEqual(esquema.ESQUEMA_JSON, publicado)


if __name__ == "__main__":
    unittest.main()
