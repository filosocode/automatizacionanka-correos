"""Cubre: T-66. Verificación contextual de cantidades y unidades de la tabla de Work, en modo sombra."""

from tests import _isolate  # noqa: F401  (debe ir primero)

import unittest
from decimal import Decimal
from unittest.mock import patch

from anka.dominio import contexto_de_cantidad as ctx
from anka.dominio import verificacion_de_evidencia as ver
from anka.dominio.contexto_de_cantidad import (
    ASOCIACION_DUDOSA,
    CONTRADICTORIA_UNIDAD,
    CONTRADICTORIA_VALOR,
    NO_COMPROBABLE,
    NO_COMPROBABLE_OCR,
    NO_ENCONTRADA,
    VERIFICADA,
    VERIFICADA_SIN_UNIDAD,
)

PLANO = "[Página 1]\nLevantamiento planimétrico: 4,5 ha\n"


def adjunto(nombre, texto):
    return {"filename": nombre, "source_id": "s", "coverage": "completa", "text": texto}


def actividad(nombre="Levantamiento planimétrico", cantidad="4,5 ha", unidad="ha", fuente="plano.pdf, p. 1"):
    return {"activity": nombre, "quantity": cantidad, "unit": unidad, "fuente": fuente}


def observar(act, correo="", adjuntos=(), incompleto=False):
    resultado = ctx.observar([act], correo, list(adjuntos), incompleto)
    return resultado[0] if resultado else None


def estado(act, correo="", adjuntos=(), incompleto=False):
    return observar(act, correo, adjuntos, incompleto)["estado"]


class NumerosTests(unittest.TestCase):
    def test_4_coma_5_no_se_confunde_con_4_5_ni_45(self):
        # Cubre: T-66
        self.assertEqual(ctx.valores("4,5"), (frozenset({Decimal("4.5")}), False))
        self.assertEqual(ctx.valores("4.5"), (frozenset({Decimal("4.5")}), False))
        self.assertEqual(ctx.valores("45"), (frozenset({Decimal("45")}), False))
        self.assertFalse(ctx.valores("4,5")[0] & ctx.valores("45")[0])
        self.assertFalse(ctx.valores("4,5")[0] & {Decimal(4), Decimal(5)})

    def test_separadores_de_miles_y_ambiguos_no_cambian_el_valor_en_silencio(self):
        # Cubre: T-66
        self.assertEqual(ctx.valores("1.250"), (frozenset({Decimal("1250"), Decimal("1.250")}), True))
        self.assertEqual(ctx.valores("1,250"), (frozenset({Decimal("1250"), Decimal("1.250")}), True))
        self.assertEqual(ctx.valores("1.250,5"), (frozenset({Decimal("1250.5")}), False))
        self.assertEqual(ctx.valores("1,250.5"), (frozenset({Decimal("1250.5")}), False))
        self.assertEqual(ctx.valores("1.250.000"), (frozenset({Decimal("1250000")}), False))
        self.assertEqual(ctx.valores("0,500"), (frozenset({Decimal("0.5")}), False))
        self.assertEqual(ctx.valores("1.2.3"), (frozenset(), False))

    def test_las_unidades_equivalen_solo_por_reglas_explicitas(self):
        # Cubre: T-66
        self.assertEqual(ctx.leer_unidad(" hectáreas".replace("á", "a")), "area_ha")
        self.assertEqual(ctx.leer_unidad(" ha"), "area_ha")
        self.assertEqual(ctx.leer_unidad(" metros cuadrados"), "area_m2")
        self.assertEqual(ctx.leer_unidad(" m2"), "area_m2")
        self.assertEqual(ctx.leer_unidad(" m de ancho"), "longitud_m")
        self.assertNotEqual(ctx.UNIDADES["ha"], ctx.UNIDADES["m2"])      # nunca se convierte una en otra
        self.assertEqual(ctx.leer_unidad(" de lote"), None)


class CasosDeT651Tests(unittest.TestCase):
    def test_01_4_5_ha_correcto(self):
        # Cubre: T-66
        obs = observar(actividad(), adjuntos=[adjunto("plano.pdf", PLANO)])
        self.assertEqual((obs["estado"], obs["nivel"], obs["ubicacion"]), (VERIFICADA, "pagina", "plano.pdf, página 1"))
        self.assertIn("4,5 ha", obs["fragmento"])

    def test_02_45_ha_incorrecto_es_contradictoria_por_valor(self):
        # Cubre: T-66
        obs = observar(actividad(cantidad="45 ha"), adjuntos=[adjunto("plano.pdf", PLANO)])
        self.assertEqual((obs["estado"], obs["severidad"]), (CONTRADICTORIA_VALOR, "critico_futuro"))
        self.assertIn("4.5", obs["motivo"])

    def test_03_4_5_m2_en_lugar_de_ha_es_contradictoria_por_unidad(self):
        # Cubre: T-66
        obs = observar(actividad(cantidad="4,5 m²", unidad="m²"), adjuntos=[adjunto("plano.pdf", PLANO)])
        self.assertEqual(obs["estado"], CONTRADICTORIA_UNIDAD)
        self.assertIn("area_ha", obs["motivo"])

    def test_04_cifra_correcta_encontrada_en_otra_actividad(self):
        # Cubre: T-66
        texto = "[Página 2]\nLevantamiento: 12 ha\nBatimetría: 3 ha\n"
        act = actividad("Batimetría", "12 ha", fuente="plano.pdf, p. 2")
        self.assertEqual(estado(act, adjuntos=[adjunto("plano.pdf", texto)]), CONTRADICTORIA_VALOR)

    def test_05_numero_correcto_solo_en_otra_pagina(self):
        # Cubre: T-66
        texto = "[Página 1]\nLevantamiento planimétrico: 10 ha\n[Página 3]\nLevantamiento planimétrico: 4,5 ha\n"
        obs = observar(actividad(), adjuntos=[adjunto("plano.pdf", texto)])
        self.assertEqual(obs["estado"], ASOCIACION_DUDOSA)
        self.assertIn("página 3", obs["motivo"])

    def test_06_varias_cantidades_en_una_misma_tabla_cada_una_por_su_fila(self):
        # Cubre: T-66
        texto = ("Tabla 1, fila 1: columna 1=Actividad | columna 2=Área (ha)\n"
                 "Tabla 1, fila 2: columna 1=Levantamiento planimétrico | columna 2=4,5\n"
                 "Tabla 1, fila 3: columna 1=Batimetría | columna 2=12")
        docs = [adjunto("plan.docx", texto)]
        fila3 = observar(actividad("Batimetría", "12 ha", fuente="plan.docx, tabla 1, fila 3"), adjuntos=docs)
        fila2 = observar(actividad(fuente="plan.docx, tabla 1, fila 2"), adjuntos=docs)
        self.assertEqual((fila3["estado"], fila3["nivel"]), (VERIFICADA, "fila"))
        self.assertEqual((fila2["estado"], fila2["nivel"]), (VERIFICADA, "fila"))
        cruzada = actividad("Batimetría", "4,5 ha", fuente="plan.docx, tabla 1, fila 3")
        self.assertEqual(estado(cruzada, adjuntos=docs), CONTRADICTORIA_VALOR)

    def test_07_un_numero_que_es_fecha_o_codigo_no_es_cantidad(self):
        # Cubre: T-66
        correo = "Reunión el 20 de octubre de 2026. Código ANK-4500. Teléfono 3001234567. Escala 1:500. Página 12."
        for cantidad in ("20 ha", "2026 ha", "4500 ha", "500 ha", "12 ha"):
            with self.subTest(cantidad=cantidad):
                self.assertEqual(estado(actividad(cantidad=cantidad, fuente="correo"), correo=correo), NO_ENCONTRADA)

    def test_08_separadores_decimales_y_de_miles(self):
        # Cubre: T-66
        correo = "El eje del proyecto mide 1.250 m de longitud."
        obs = observar(actividad("Eje del proyecto", "1.250 m", "m", fuente="correo"), correo=correo)
        self.assertEqual(obs["estado"], VERIFICADA)
        obs = observar(actividad("Eje del proyecto", "1,25 m", "m", fuente="correo"), correo=correo)
        self.assertEqual(obs["estado"], VERIFICADA)       # `1.250` admite también 1,25; no se afirma otra cosa
        # una cifra ambigua nunca demuestra una contradicción: 125 no es ninguna de sus dos lecturas, pero no se afirma
        self.assertEqual(estado(actividad("Eje del proyecto", "125 m", "m", fuente="correo"), correo=correo),
                          NO_ENCONTRADA)
        claro = "El eje del proyecto mide 1.250,5 m de longitud."
        self.assertEqual(estado(actividad("Eje del proyecto", "125 m", "m", fuente="correo"), correo=claro),
                         CONTRADICTORIA_VALOR)

    def test_09_unidad_no_indicada(self):
        # Cubre: T-66
        obs = observar(actividad(cantidad="4,5", unidad=""), adjuntos=[adjunto("plano.pdf", PLANO)])
        self.assertEqual(obs["estado"], VERIFICADA_SIN_UNIDAD)

    def test_10_fuente_citada_inexistente(self):
        # Cubre: T-66
        obs = observar(actividad(fuente="otro.pdf, p. 1"), adjuntos=[adjunto("plano.pdf", "[Página 1]\nSin cifras\n")])
        self.assertEqual(obs["estado"], NO_COMPROBABLE)         # T-68: antes caía a buscar en todo el expediente
        self.assertTrue(obs["motivo"].startswith("fuente_inexistente"))

    def test_11_ocr_con_cifra_dudosa_no_es_contradiccion(self):
        # Cubre: T-66
        texto = "[Página 1]\n[OCR visual adicional]\nLevantamiento planimétrico: 4,S ha\n"
        obs = observar(actividad(), adjuntos=[adjunto("plano.pdf", texto)])
        self.assertEqual((obs["estado"], obs["ocr"]), (NO_COMPROBABLE_OCR, True))

    def test_12_excel_con_varias_hojas_y_celdas_y_unidad_en_el_encabezado(self):
        # Cubre: T-66
        texto = ("Hoja: Resumen; visibilidad: visible; rango declarado: A1:B2\n"
                 "A1=Actividad | B1=Cantidad (ha)\nA2=Levantamiento planimétrico | B2=4,5\n"
                 "Hoja: Otra; visibilidad: visible; rango declarado: A1:B2\n"
                 "A1=Actividad | B1=Cantidad (ha)\nA2=Levantamiento planimétrico | B2=45")
        libro = [adjunto("calculo.xlsx", texto)]
        buena = observar(actividad(fuente="calculo.xlsx, hoja Resumen, celda B2"), adjuntos=libro)
        self.assertEqual((buena["estado"], buena["nivel"]), (VERIFICADA, "celda"))
        fuera = observar(actividad(fuente="calculo.xlsx, hoja Otra, celda B2"), adjuntos=libro)
        self.assertEqual(fuera["estado"], ASOCIACION_DUDOSA)            # el 4,5 existe en otra hoja: se dice dónde
        self.assertIn("hoja Resumen", fuera["motivo"])
        otra = "\n".join(["Hoja: Otra; visibilidad: visible; rango declarado: A1:B2",
                          "A1=Actividad | B1=Cantidad (ha)", "A2=Levantamiento planimétrico | B2=45"])
        mala = observar(actividad(fuente="calculo.xlsx, hoja Otra, celda B2"), adjuntos=[adjunto("calculo.xlsx", otra)])
        self.assertEqual(mala["estado"], CONTRADICTORIA_VALOR)
        sin_celda = observar(actividad(fuente="calculo.xlsx, hoja Resumen"), adjuntos=libro)
        self.assertEqual((sin_celda["estado"], sin_celda["nivel"]), (VERIFICADA, "pagina"))

    def test_13_documento_con_evidencia_insuficiente(self):
        # Cubre: T-66
        self.assertEqual(estado(actividad(), adjuntos=[adjunto("plano.pdf", "")]), NO_COMPROBABLE)

    def test_14_actividad_y_cantidad_correctas_en_el_correo(self):
        # Cubre: T-66
        correo = "Solicito un levantamiento planimétrico y altimétrico.\nÁrea aproximada: 4,5 hectáreas.\nGracias."
        act = {"activity": "Levantamiento topográfico planimétrico y altimétrico",
               "quantity": "4,5 hectáreas (área aproximada)", "unit": "ha", "fuente": "correo 1a15b58b, párrafo de solicitud"}
        obs = observar(act, correo=correo)
        self.assertEqual((obs["estado"], obs["nivel"]), (VERIFICADA, "correo"))

    def test_15_un_dato_como_5_no_se_verifica_con_4_5(self):
        # Cubre: T-66
        obs = observar(actividad(cantidad="5 ha"), adjuntos=[adjunto("plano.pdf", PLANO)])
        self.assertEqual(obs["estado"], CONTRADICTORIA_VALOR)
        self.assertEqual(estado(actividad(cantidad="4 ha", nombre="Servicio"), adjuntos=[adjunto("plano.pdf", PLANO)]),
                         NO_ENCONTRADA)

    def test_16_una_cifra_no_demuestra_asociacion_si_la_actividad_no_trae_palabras(self):
        # Cubre: T-66
        obs = observar(actividad(nombre="Servicio", cantidad="4,5 ha"), adjuntos=[adjunto("plano.pdf", PLANO)])
        self.assertEqual(obs["estado"], ASOCIACION_DUDOSA)


class CasosAdicionalesTests(unittest.TestCase):
    def test_cantidad_ausente_sin_valor_alternativo_no_es_contradiccion(self):
        # Cubre: T-66
        texto = "[Página 1]\nLevantamiento planimétrico requerido, sin cifras.\n"
        obs = observar(actividad(cantidad="7 ha"), adjuntos=[adjunto("plano.pdf", texto)])
        self.assertEqual((obs["estado"], obs["severidad"]), (NO_ENCONTRADA, "aviso"))

    def test_valor_diferente_demostrado_en_la_misma_actividad_y_unidad(self):
        # Cubre: T-66
        obs = observar(actividad(cantidad="6 ha"), adjuntos=[adjunto("plano.pdf", PLANO)])
        self.assertEqual(obs["estado"], CONTRADICTORIA_VALOR)

    def test_valor_diferente_en_otra_unidad_no_es_alternativa(self):
        # Cubre: T-66
        texto = "[Página 1]\nLevantamiento planimétrico: 4,5 km\n"
        self.assertEqual(estado(actividad(cantidad="7 ha"), adjuntos=[adjunto("plano.pdf", texto)]), NO_ENCONTRADA)

    def test_dos_actividades_con_palabras_genericas_comunes_no_se_asocian_por_ellas(self):
        # Cubre: T-66
        correo = "Levantamiento topográfico de lote: 4,5 ha\nLevantamiento topográfico de vía: 12 km\n"
        act = actividad("Levantamiento topográfico de vía", "4,5 ha", fuente="correo")
        self.assertEqual(estado(act, correo=correo), ASOCIACION_DUDOSA)
        propia = actividad("Levantamiento topográfico de lote", "4,5 ha", fuente="correo")
        self.assertEqual(estado(propia, correo=correo), VERIFICADA)

    def test_ocr_parcial_sin_la_cifra_no_permite_concluir(self):
        # Cubre: T-66
        texto = "[Página 1]\n[OCR visual adicional]\nLevantamiento planimétrico: 12 ha\n"
        obs = observar(actividad(cantidad="7 ha"), adjuntos=[adjunto("plano.pdf", texto)])
        self.assertEqual(obs["estado"], NO_COMPROBABLE_OCR)
        texto = "[Página 1]\nLevantamiento planimétrico: 12 ha\nLECTURA PARCIAL: faltan páginas\n"
        self.assertEqual(estado(actividad(cantidad="7 ha"), adjuntos=[adjunto("plano.pdf", texto)]), NO_COMPROBABLE)
        texto = "[Página 1; sin texto extraíble]\n"
        self.assertEqual(estado(actividad(cantidad="7 ha"), adjuntos=[adjunto("plano.pdf", texto)]), NO_COMPROBABLE)

    def test_un_expediente_incompleto_nunca_concluye_que_algo_falta(self):
        # Cubre: T-66
        self.assertEqual(estado(actividad(cantidad="7 ha"), adjuntos=[adjunto("plano.pdf", PLANO)], incompleto=True),
                         NO_COMPROBABLE)

    def test_una_actividad_sin_cifra_no_genera_observacion(self):
        # Cubre: T-66
        self.assertEqual(ctx.observar([{"activity": "Informe", "quantity": "", "fuente": "correo"}], "x", []), [])
        self.assertEqual(ctx.observar([{"activity": "Informe", "quantity": "varias", "fuente": "correo"}], "x", []), [])
        self.assertEqual(ctx.observar(["no es un objeto", None], "x", []), [])

    def test_las_observaciones_conservan_localizador_fragmento_nivel_y_motivo(self):
        # Cubre: T-66
        obs = observar(actividad(), adjuntos=[adjunto("plano.pdf", PLANO)])
        for clave in ("actividad", "nombre", "cantidad", "estado", "severidad", "nivel", "ubicacion", "fragmento",
                      "unidad_fuente", "unidad_citada", "ocr", "motivo"):
            self.assertIn(clave, obs)
        self.assertEqual((obs["unidad_citada"], obs["unidad_fuente"]), ("area_ha", "area_ha"))
        self.assertLessEqual(len(obs["fragmento"]), ctx.MAX_FRAGMENTO)


class FuenteCitadaNoResolubleTests(unittest.TestCase):
    """C07 y variantes: una cita explícita que no se puede leer nunca produce una contradicción."""

    LIBRO = ("Hoja: Resumen; visibilidad: visible; rango declarado: A1:B2\n"
             "A1=Actividad | B1=Cantidad (ha)\nA2=Levantamiento planimétrico | B2=4,5")

    def comprobar(self, fuente, adjuntos, motivo, cantidad="7 ha", correo=""):
        obs = observar(actividad(cantidad=cantidad, fuente=fuente), correo=correo, adjuntos=adjuntos)
        self.assertEqual(obs["estado"], NO_COMPROBABLE, obs)
        self.assertTrue(obs["motivo"].startswith(motivo), obs["motivo"])
        self.assertEqual(obs["severidad"], "info")
        return obs

    def test_archivo_ausente_no_busca_alternativas_en_otros_documentos(self):
        # Cubre: T-66
        self.comprobar("otro.pdf, p. 1", [adjunto("plano.pdf", PLANO)], "fuente_inexistente")

    def test_archivo_ausente_tampoco_se_salva_por_el_correo(self):
        # Cubre: T-66
        self.comprobar("otro.pdf, p. 1", [adjunto("plano.pdf", PLANO)], "fuente_inexistente",
                       cantidad="4,5 ha", correo="Levantamiento planimétrico: 4,5 ha")

    def test_pagina_inexistente_no_declara_contradiccion(self):
        # Cubre: T-66
        self.comprobar("plano.pdf, p. 9", [adjunto("plano.pdf", PLANO)], "ubicacion_no_resuelta")

    def test_hoja_inexistente_no_declara_contradiccion(self):
        # Cubre: T-66
        self.comprobar("calculo.xlsx, hoja Nada, celda B2", [adjunto("calculo.xlsx", self.LIBRO)],
                       "ubicacion_no_resuelta")

    def test_celda_no_localizable_no_declara_contradiccion(self):
        # Cubre: T-66
        self.comprobar("calculo.xlsx, hoja Resumen, celda Z99", [adjunto("calculo.xlsx", self.LIBRO)],
                       "ubicacion_no_resuelta")

    def test_unidad_distinta_en_otra_ubicacion_tampoco_es_contradiccion(self):
        # Cubre: T-66
        obs = observar(actividad(cantidad="4,5 m2", unidad="m2", fuente="plano.pdf, p. 9"),
                       adjuntos=[adjunto("plano.pdf", PLANO)])
        self.assertEqual(obs["estado"], NO_COMPROBABLE)
        self.assertTrue(obs["motivo"].startswith("ubicacion_no_resuelta"))

    def test_referencia_ambigua_no_elige_un_archivo(self):
        # Cubre: T-66
        docs = [adjunto("norte_plano.pdf", PLANO), adjunto("sur_plano.pdf", PLANO.replace("4,5", "9"))]
        self.comprobar("plano.pdf, p. 1", docs, "referencia_ambigua")

    def test_un_nombre_identico_gana_a_los_equivalentes(self):
        # Cubre: T-66
        docs = [adjunto("viejo_plano.pdf", PLANO.replace("4,5", "9")), adjunto("plano.pdf", PLANO)]
        self.assertEqual(estado(actividad(fuente="plano.pdf, p. 1"), adjuntos=docs), VERIFICADA)

    def test_con_la_ubicacion_sin_resolver_un_valor_hallado_en_el_archivo_sigue_verificandose(self):
        # Cubre: T-66
        obs = observar(actividad(fuente="plano.pdf, p. 9"), adjuntos=[adjunto("plano.pdf", PLANO)])
        self.assertEqual((obs["estado"], obs["nivel"]), (VERIFICADA, "archivo"))

    def test_dos_adjuntos_con_el_mismo_nombre_no_rompen_la_busqueda(self):
        # Cubre: T-66
        docs = [adjunto("plano.pdf", PLANO), adjunto("plano.pdf", PLANO)]
        self.assertEqual(estado(actividad(fuente="correo"), correo="Levantamiento planimétrico: 4,5 ha", adjuntos=docs),
                         VERIFICADA)
        self.comprobar("plano.pdf, p. 1", docs, "referencia_ambigua")

    def test_sin_cita_de_archivo_se_conserva_la_busqueda_en_el_expediente(self):
        # Cubre: T-66
        obs = observar(actividad(cantidad="7 ha", fuente="lista preliminar"), adjuntos=[adjunto("plano.pdf", PLANO)])
        self.assertEqual(obs["estado"], CONTRADICTORIA_VALOR)


class ModoSombraTests(unittest.TestCase):
    TEXTOS = {"thread_id": "T1", "revision": "r", "incompleto": False, "correo": "Se pide un levantamiento de 126 ha.",
              "adjuntos": [adjunto("TdR.pdf", "[Página 1]\nÁrea aproximada 126 ha\n")]}
    TABLA = {"activities": [{"activity": "Levantamiento", "quantity": "126 ha", "unit": "ha", "fuente": "TdR.pdf, p. 1"},
                            {"activity": "Levantamiento", "quantity": "999 ha", "fuente": "TdR.pdf, p. 1"}]}

    def test_el_resultado_conserva_las_claves_y_los_cubos_de_siempre(self):
        # Cubre: T-66
        con = ver.comprobar_tabla(self.TABLA, self.TEXTOS)
        with patch.object(ver.contexto_de_cantidad, "observar", return_value=[]):
            sin = ver.comprobar_tabla(self.TABLA, self.TEXTOS)
        # T-69 suma `cambios_de_magnitud` (también en sombra); las cuatro claves históricas y `observaciones` no cambian
        self.assertEqual(set(con), {"comprobadas", "diferencias", "no_comprobables", "estado", "observaciones",
                                    "cambios_de_magnitud"})
        self.assertEqual({k: v for k, v in con.items() if k != "observaciones"},
                         {k: v for k, v in sin.items() if k != "observaciones"})
        self.assertEqual(sin["observaciones"], [])
        self.assertTrue(con["observaciones"])

    def test_los_avisos_visibles_no_incluyen_las_observaciones(self):
        # Cubre: T-66
        resultado = ver.comprobar_tabla(self.TABLA, self.TEXTOS)
        self.assertTrue(any(o["severidad"] == "critico_futuro" for o in resultado["observaciones"])
                        or any(o["severidad"] == "aviso" for o in resultado["observaciones"]))
        avisos = ver.avisos_de(resultado)
        self.assertFalse([a for a in avisos if "Calidad documental" in a or "CONTRADICTORIA" in a])
        solo_observaciones = {**resultado, "diferencias": []}
        self.assertEqual(ver.avisos_de(solo_observaciones), [])

    def test_un_fallo_del_verificador_nuevo_no_frena_la_comprobacion_existente(self):
        # Cubre: T-66
        with patch.object(ver.contexto_de_cantidad, "observar", side_effect=RuntimeError("falla")):
            with self.assertLogs("anka.dominio.verificacion_de_evidencia", level="WARNING"):
                resultado = ver.comprobar_tabla(self.TABLA, self.TEXTOS)
        self.assertEqual(resultado["observaciones"], [])
        self.assertEqual(resultado["comprobadas"], 3)

    def test_sin_textos_sigue_sin_comprobarse_nada(self):
        # Cubre: T-66
        self.assertIsNone(ver.comprobar_tabla(self.TABLA, None))

    def test_la_funcion_numerica_antigua_no_cambia(self):
        # Cubre: T-66
        self.assertTrue({4.0, 5.0, 4.5} <= ver.numeros_del_texto("4,5"))
        self.assertEqual(ver.valores("1.250"), {1.25, 1250.0})


if __name__ == "__main__":
    unittest.main()
