"""Entradas mal formadas de Work y de Gmail: se rechazan con su motivo o se leen como vacías, nunca rompen Python.

Cada caso sale de una prueba adversa de QA (borradores con tipos inesperados, respuestas de Gmail incompletas).
Cubre: T-31, T-29, T-33, T-41, R12
"""

from tests import _isolate  # noqa: F401  (debe ir primero)

import base64
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from anka.dominio import correo
from anka.dominio.activities import _cell, normalize_estado
from anka.dominio.errores import ErrorDeAnka
from anka.dominio.reception import question_email, safe_folder_name
from anka.dominio.status import canonical_work_status, reconocer_estado_de_work
from anka.dominio.tramos import ultimo_encabezado
from anka.dominio.work_contract import (
    reading_summary, reconciliar_lectura, usa_verificacion_por_adjuntos, verificar_adjuntos,
)
from anka.infra import settings
from anka.servicios import quotes, work_import
from tests.test_work_sync import b64, payload


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode("ascii")


class ConBase(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        parche = patch.object(settings, "DATA_DIR", Path(self.temp.name))
        parche.start()
        self.addCleanup(parche.stop)

    def importar(self, **extra):
        work_import.dashboard_upsert(b64(json.dumps(payload(**extra))))


# Cubre: T-31, T-33
class ImportacionDeWork(ConBase):
    def assertRechaza(self, codificado, *fragmentos):
        with self.assertRaises(ErrorDeAnka) as error:
            work_import.dashboard_upsert(codificado)
        for fragmento in fragmentos:
            self.assertIn(fragmento, str(error.exception))

    def test_un_contrato_que_no_es_un_objeto_se_rechaza_con_su_motivo(self):
        for valor in ([1], "x", None, 5):
            self.assertRechaza(b64(json.dumps(valor)), "objeto JSON")

    def test_un_estado_desconocido_se_rechaza_y_no_se_guarda_tal_cual(self):
        self.assertRechaza(b64(json.dumps(payload(thread="T5", status="Aprobada y cotizada"))),
                           "status", "Aprobada y cotizada")
        self.assertIsNone(quotes.get_quote("T5"))

    def test_un_estado_que_no_es_texto_se_rechaza(self):
        for valor in (True, ["Faltan datos"], 3):
            self.assertRechaza(b64(json.dumps(payload(thread="T6", status=valor))), "status")
        self.assertIsNone(quotes.get_quote("T6"))

    def test_campos_obligatorios_que_no_son_texto_se_rechazan(self):
        self.assertRechaza(b64(json.dumps(payload(client_email=["a@b.co"]))), "client_email")
        self.assertRechaza(b64(json.dumps(payload(thread=12345))), "thread_id")

    def test_un_sustituto_suelto_se_rechaza_antes_de_guardar(self):
        crudo = json.dumps(payload(thread="T24")).replace('"Proyecto"', '"\\ud800"')
        self.assertRechaza(b64(crudo), "no válido")

    def test_listas_con_un_numero_se_rechazan_y_null_es_como_no_escribirlo(self):
        self.assertRechaza(b64(json.dumps(payload(activities=5))), "activities")
        self.assertRechaza(b64(json.dumps(payload(cited_codes=5))), "cited_codes")
        self.importar(thread="T9", activities=None, cited_codes=None)
        self.assertEqual(quotes.get_quote("T9")["analysis"]["activities"], [])

    def test_un_codigo_citado_suelto_cuenta_como_uno_y_no_letra_por_letra(self):
        with patch.object(work_import, "apply_guard") as guardia:
            self.importar(thread="T16", cited_codes="T-123")
        self.assertEqual(guardia.call_args.args[5], ["T-123"])

    def test_una_fila_nula_no_aparece_como_none_en_la_tabla(self):
        self.importar(thread="T11", activities=[None, {"activity": "Levantamiento"}])
        filas = quotes.get_quote("T11")["analysis"]["activities"]
        self.assertEqual([fila["activity"] for fila in filas], ["Levantamiento"])

    def test_una_hora_del_borrador_sin_zona_no_descarta_el_borrador(self):
        # Cubre: T-29
        self.assertEqual(work_import._demora_del_puente({"draft_at": "2026-01-01T00:00:00"}), "")
        self.importar(thread="T21", draft_at="2026-01-01T00:00:00")
        self.assertIsNotNone(quotes.get_quote("T21"))
        self.assertIn("llegó al dashboard",
                      work_import._demora_del_puente({"draft_at": "2026-01-01T00:00:00+00:00"}))


# Cubre: T-33
class EstadosConFormasRaras(unittest.TestCase):
    def test_ancho_completo_y_ancho_cero_se_reconocen(self):
        self.assertEqual(canonical_work_status("Ｆａｌｔａｎ ｄａｔｏｓ"), "Faltan datos")
        self.assertEqual(canonical_work_status("faltan​ datos"), "Faltan datos")
        self.assertEqual(canonical_work_status("Faltan datos"), "Faltan datos")

    def test_un_valor_que_no_es_texto_no_se_reconoce_ni_rompe(self):
        class Raro:
            def __str__(self):
                raise ValueError
        for valor in (Raro(), 0, False, None, b"x", ["Faltan datos"]):
            self.assertEqual(reconocer_estado_de_work(valor), (None, ""))


# Cubre: T-41
class VerificacionDeAdjuntos(unittest.TestCase):
    def borrador(self, *adjuntos):
        return {"attachments": [{"coverage": "completa", **a} for a in adjuntos]}

    def test_el_nombre_se_compara_en_nfc_y_sin_mayusculas(self):
        datos = self.borrador({"filename": "PLÁNO.pdf", "size_bytes": 1})
        self.assertEqual(verificar_adjuntos(datos, [{"filename": "pláno.pdf", "size": 1}]), [])
        self.assertEqual(datos["reading_audit"]["verification"], "verificada")

    def test_el_tamano_se_acepta_como_decimal_entero_o_texto(self):
        for tamano in (10.0, "10", 10):
            datos = self.borrador({"filename": "a.pdf", "size_bytes": tamano})
            self.assertEqual(verificar_adjuntos(datos, [{"filename": "a.pdf", "size": 10}]), [], tamano)
        datos = self.borrador({"filename": "a.pdf", "size_bytes": 10.5})
        self.assertTrue(verificar_adjuntos(datos, [{"filename": "a.pdf", "size": 10}]))
        sin_tamano = self.borrador({"filename": "a.pdf"})          # sin tamaño en ningún lado no se puede verificar
        verificar_adjuntos(sin_tamano, [{"filename": "a.pdf"}])
        self.assertEqual(sin_tamano["reading_audit"]["verification"], "incompleta")

    def test_datos_mal_formados_no_rompen_la_verificacion(self):
        for auditoria in ("x", [1]):
            datos = {"reading_audit": auditoria}
            self.assertEqual(verificar_adjuntos(datos, []), [])
        avisos = verificar_adjuntos({}, [{"size": 1}])
        self.assertIn("(adjunto sin nombre)", avisos[0])

    def test_un_nombre_que_no_es_texto_no_se_muestra_como_lista(self):
        datos = self.borrador({"filename": ["x"]})
        avisos = verificar_adjuntos(datos, [{"filename": "a"}])
        self.assertFalse(any("['x']" in aviso for aviso in avisos))
        self.assertIn("(adjunto sin nombre)", " ".join(avisos))

    def test_una_foto_adjunta_con_content_id_cuenta_como_adjunto_del_cliente(self):
        # caso real: «Work reportó adjuntos que Gmail no tiene: foto_referencia_1.jpg» bloqueaba el caso
        foto = {"filename": "foto_referencia_1.jpg", "mimeType": "image/jpeg",
                "body": {"attachmentId": "f1", "size": 2048},
                "headers": [{"name": "Content-Disposition", "value": 'attachment; filename="foto_referencia_1.jpg"'},
                            {"name": "Content-ID", "value": "<f1@outlook>"}]}
        logo = {"filename": "logo.png", "mimeType": "image/png", "body": {"attachmentId": "l1", "size": 90},
                "headers": [{"name": "Content-ID", "value": "<logo>"}]}
        hilo = {"messages": [{"id": "m1", "internalDate": "1", "payload": {"parts": [foto, logo]}}]}
        self.assertEqual([a["filename"] for a in correo.adjuntos_recibidos(hilo)], ["foto_referencia_1.jpg"])
        self.assertEqual([a["filename"] for a in correo.imagenes_incrustadas(hilo)], ["logo.png"])
        datos = self.borrador({"filename": "foto_referencia_1.jpg", "size_bytes": 2048})
        self.assertEqual(verificar_adjuntos(datos, correo.adjuntos_recibidos(hilo)), [])

    def test_una_imagen_incrustada_que_work_reporta_no_es_desconocida_ni_se_exige(self):
        datos = self.borrador({"filename": "foto_pegada.jpg", "size_bytes": 5})
        incrustadas = [{"filename": "foto_pegada.jpg", "size": 5}]
        self.assertEqual(verificar_adjuntos(datos, [], incrustadas), [])
        self.assertEqual(datos["reading_audit"]["verification"], "verificada")
        sin_lista = self.borrador({"filename": "foto_pegada.jpg", "size_bytes": 5})
        self.assertTrue(verificar_adjuntos(sin_lista, []))          # sin la lista sigue siendo desconocida

    def test_la_version_del_prompt_tiene_tope_y_el_borrador_debe_ser_un_objeto(self):
        self.assertFalse(usa_verificacion_por_adjuntos({"skill_version": "PRECISO_V" + "9" * 5000}))
        self.assertFalse(usa_verificacion_por_adjuntos([]))
        self.assertTrue(usa_verificacion_por_adjuntos({"skill_version": "PRECISO_V10"}))
        self.assertTrue(usa_verificacion_por_adjuntos({"skill_version": "PRECISO_V6.1"}))

    def test_reconciliar_sin_hilo_no_toca_nada(self):
        self.assertEqual(reconciliar_lectura({}, {"segments": [1]}), [])
        self.assertEqual(reconciliar_lectura({"thread_id": None}, {"thread_id": None}), [])
        self.assertEqual(reconciliar_lectura({"thread_id": "T"}, "no es un objeto"), [])

    def test_el_resumen_tolera_auditoria_y_adjuntos_mal_formados(self):
        resumen = reading_summary({"reading_audit": {"verification": "verificada"}, "attachments": [1, None, "a"]})
        self.assertEqual(resumen["status"], "completa declarada")
        self.assertEqual(reading_summary({"reading_audit": "x"})["status"], "no indicado")


# Cubre: R12
class EstadoDeLasFilas(unittest.TestCase):
    def test_los_textos_de_duda_son_pendientes(self):
        for texto in ("confirmado, por verificar", "confirmado - por validar", "confirmado, por definir",
                      "confirmado; revisar con cliente", "confirmado, sin soporte", "confirmado (supuesto)",
                      "confirmado pero dudoso", "confirmado, estimado", "confirmado, a verificar", "Confirmado, asumido",
                      "a verificar", "asumido"):
            self.assertEqual(normalize_estado(texto), "pendiente", texto)

    def test_los_casos_buenos_siguen_igual(self):
        for texto, esperado in (("confirmado", "confirmado"), ("Confirmado", "confirmado"),
                                ("  confirmado  ", "confirmado"), ("confirmado, verificado", "confirmado"),
                                ("confirmado, contradictorio", "contradictorio"), ("", "pendiente")):
            self.assertEqual(normalize_estado(texto), esperado, texto)

    def test_una_formula_con_espacios_o_tabulador_delante_no_se_ejecuta_en_excel(self):
        for texto in ("=1+1", "\t=1", " =1", "  +1", "\n@x"):
            self.assertTrue(_cell(texto).startswith("'"), repr(texto))
        self.assertEqual(_cell("Levantamiento"), "Levantamiento")


# Cubre: T-31
class NombreDeCarpetaYCorreo(unittest.TestCase):
    def test_el_final_del_id_del_hilo_se_limpia(self):
        self.assertEqual(safe_folder_name("x", "../../../etc/passwd", "P"), "P - x - tcpasswd")
        self.assertEqual(safe_folder_name("x‮", "id\x00", "P"), "P - x - id")
        self.assertEqual(safe_folder_name("x", "a\\b.", "P"), "P - x - ab")
        self.assertEqual(safe_folder_name(None, None, "P"), "P - Sin nombre - sin-id")
        self.assertEqual(safe_folder_name("Obra: Norte/Sur", "1234567890abcdef", "C"), "C - Obra Norte Sur - 90abcdef")

    def test_una_pregunta_vacia_no_se_numera(self):
        texto = question_email([None, "¿Cuál es el área?", "  "])
        self.assertIn("1. ¿Cuál es el área?", texto)
        self.assertNotIn("None", texto)
        self.assertNotIn("2.", texto)


# Cubre: T-31
class DatosDeGmailIncompletos(unittest.TestCase):
    def test_cabeceras_incompletas(self):
        self.assertEqual(correo.header({"payload": {"headers": [{"value": "x"}]}}, "From"), "")
        self.assertEqual(correo.header({"payload": None}, "From"), "")
        self.assertEqual(correo.header({"payload": {"headers": [{"name": None, "value": "x"}]}}, "From"), "")
        self.assertEqual(correo.header({"payload": {"headers": [{"name": "from", "value": "a@b.co"}]}}, "From"),
                         "a@b.co")

    def test_cuerpos_incompletos_o_mal_codificados(self):
        self.assertEqual(correo.message_text({"mimeType": "text/plain", "body": {"data": "A"}}), "")
        self.assertEqual(correo.message_text({"mimeType": "text/plain", "body": None}), "")
        self.assertEqual(correo.message_text({"parts": None}), "")
        self.assertFalse(correo.has_attachments({"parts": None}))

    def test_el_html_no_filtra_scripts_ni_estilos_y_un_menor_que_no_recorta(self):
        html = b"<p>Hola&nbsp;<b>a</b><script>alert(1)</script><style>.a{}</style></p><!-- nota -->"
        texto = correo.message_text({"mimeType": "text/html", "body": {"data": _b64(html)}})
        self.assertNotIn("alert", texto)
        self.assertNotIn(".a{}", texto)
        self.assertNotIn("nota", texto)
        self.assertIn("Hola", texto)
        suelto = correo.message_text({"mimeType": "text/html", "body": {"data": _b64(b"a < b and c > d")}})
        self.assertEqual(suelto, "a < b and c > d")
        escapado = correo.message_text({"mimeType": "text/html", "body": {"data": _b64(b"&lt;b&gt;texto")}})
        self.assertEqual(escapado, "<b>texto")

    def test_adjuntos_incompletos(self):
        self.assertEqual(correo.attachment_parts({"filename": None, "body": None}), [])
        partes = correo.attachment_parts({"filename": None, "body": {"attachmentId": "a", "size": "abc"}})
        self.assertEqual((partes[0]["filename"], partes[0]["size"]), ("adjunto-sin-nombre", 0))
        self.assertEqual(correo.attachment_parts({"filename": "a", "headers": None})[0]["filename"], "a")
        hilo = {"messages": [{"internalDate": "1", "payload": {"filename": "a", "body": {"attachmentId": "x",
                                                                                       "size": "12"}}}]}
        self.assertEqual(correo.adjuntos_recibidos(hilo)[0]["message_id"], "")
        self.assertEqual(correo.adjuntos_recibidos(hilo)[0]["size"], 12)

    def test_fechas_que_no_son_numeros(self):
        for valor in ("abc", None, "1.5e12"):
            self.assertEqual(len(correo.inbound_messages({"messages": [{"internalDate": valor}]})), 1)
        orden = correo.inbound_messages({"messages": [{"id": "b", "internalDate": "5"}, {"id": "a", "internalDate": 3}]})
        self.assertEqual([m["id"] for m in orden], ["a", "b"])
        self.assertEqual(correo.inbound_messages({"messages": None}), [])
        self.assertEqual(correo.message_datetime({"internalDate": "99999999999999999999"}), "no indicado")


class Tramos(unittest.TestCase):
    def test_el_marcador_de_pagina_se_reconoce_sin_avisos_de_la_expresion(self):
        self.assertEqual(ultimo_encabezado("[Página 1]\nHola\n[Hoja 2; fila 3]\nHola"), "[Hoja 2; fila 3]")
        self.assertEqual(ultimo_encabezado("[Nota] sin marcador"), "")


if __name__ == "__main__":
    unittest.main()
