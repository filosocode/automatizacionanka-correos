"""Cubre: T-41. Work lee los adjuntos desde Gmail y el puente verifica su lectura por nombre y tamaño, sin Drive."""

from tests import _isolate  # noqa: F401  (debe ir primero)

import base64
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from anka.cli import work_sync as sync
from anka.dominio.correo import adjuntos_recibidos
from anka.dominio.status import QuoteStatus
from anka.dominio.verificacion_de_evidencia import comprobar_tabla
from anka.dominio.work_contract import (
    reading_summary, usa_verificacion_por_adjuntos, validate, verificar_adjuntos,
)
from anka.infra import settings
from anka.servicios import quotes, work_import
from anka.servicios.lectura_de_adjuntos import verificar_lectura
from anka.web import pantalla_caso
from tests.test_puente_determinista import GmailConHilos
from tests.test_work_sync import _Exec, draft_message, payload

PROPIAS = {"ankacomercialia@gmail.com"}


def b64(contenido):
    datos = contenido.encode() if isinstance(contenido, str) else contenido
    return base64.urlsafe_b64encode(datos).decode()


def mensaje(mid="in1", adjuntos=(), de="Ana <c@example.com>", etiquetas=("INBOX",), fecha="1000", cuerpo="Hola"):
    """Un mensaje de Gmail con sus adjuntos (nombre, contenido, tipo); `inline` los marca como logo de firma."""
    partes = [{"mimeType": "text/plain", "body": {"data": b64(cuerpo)}}]
    for nombre, contenido, mime, *inline in adjuntos:
        parte = {"filename": nombre, "mimeType": mime, "body": {"size": len(contenido.encode()), "data": b64(contenido)}}
        if inline:
            parte["headers"] = [{"name": "Content-ID", "value": "<logo>"}]
        partes.append(parte)
    return {"id": mid, "threadId": "T1", "internalDate": fecha, "labelIds": list(etiquetas),
            "payload": {"mimeType": "multipart/mixed", "headers": [{"name": "From", "value": de}], "parts": partes}}


NOTAS = ("notas.txt", "Área aproximada 126 ha", "text/plain")
PLANO = ("plano.txt", "Vértices 7, perímetro 4,16 km", "text/plain")


def reportado(nombre, contenido, cobertura="completa", **extra):
    """Un adjunto como lo reporta Work en el borrador."""
    return {"filename": nombre, "size_bytes": len(contenido.encode()), "status": "extraido", "reason": "",
            "reviewed_sections": ["todo"], "blocked_fields": [], **({"coverage": cobertura} if cobertura else {}), **extra}


def borrador(*adjuntos, **extra):
    return payload(skill_version="PRECISO_V6.0", analysis_complete=True, attachments=list(adjuntos), **extra)


def en_gmail(*adjuntos):
    return [{"filename": n, "size": len(c.encode())} for n, c, _ in adjuntos]


# Cubre: T-41
class VerificarAdjuntosTests(unittest.TestCase):
    def test_todos_los_adjuntos_reportados_con_su_tamano_quedan_verificados(self):
        datos = borrador(reportado(NOTAS[0], NOTAS[1]), reportado(PLANO[0], PLANO[1]))
        self.assertEqual(verificar_adjuntos(datos, en_gmail(NOTAS, PLANO)), [])
        self.assertEqual(datos["reading_audit"]["verification"], "verificada")
        self.assertIs(datos["analysis_complete"], True)
        self.assertEqual(datos["reading_audit"]["attachment_files_pending"], [])

    def test_un_adjunto_que_falta_se_nombra_y_deja_el_analisis_incompleto(self):
        datos = borrador(reportado(NOTAS[0], NOTAS[1]))
        avisos = verificar_adjuntos(datos, en_gmail(NOTAS, PLANO))
        self.assertEqual(datos["reading_audit"]["verification"], "incompleta")
        self.assertEqual(datos["reading_audit"]["attachment_files_pending"], ["plano.txt"])
        self.assertIs(datos["analysis_complete"], False)
        self.assertTrue(any("1 de 2 adjuntos sin lectura verificada: plano.txt." in a for a in avisos))

    def test_un_tamano_distinto_deja_el_adjunto_pendiente(self):
        datos = borrador(reportado(NOTAS[0], NOTAS[1]), reportado(PLANO[0], "otro contenido distinto"))
        verificar_adjuntos(datos, en_gmail(NOTAS, PLANO))
        self.assertEqual(datos["reading_audit"]["attachment_files_pending"], ["plano.txt"])
        self.assertEqual(datos["reading_audit"]["attachment_files_unknown"], [])   # el nombre sí existe en Gmail
        self.assertIs(datos["analysis_complete"], False)

    def test_un_adjunto_sin_tamano_o_sin_cobertura_no_cuenta_como_leido(self):
        sin_tamano = reportado(PLANO[0], PLANO[1])
        del sin_tamano["size_bytes"]
        datos = borrador(reportado(NOTAS[0], NOTAS[1], cobertura=None), sin_tamano)
        verificar_adjuntos(datos, en_gmail(NOTAS, PLANO))
        self.assertEqual(datos["reading_audit"]["attachment_files_pending"], ["notas.txt", "plano.txt"])

    def test_un_adjunto_que_gmail_no_tiene_deja_la_lectura_incompleta_pero_el_ignorado_no_cuenta(self):
        datos = borrador(reportado(NOTAS[0], NOTAS[1]), reportado("fantasma.pdf", "x"),
                         reportado("logo.png", "x", status="ignorado"))
        avisos = verificar_adjuntos(datos, en_gmail(NOTAS))
        self.assertEqual(datos["reading_audit"]["attachment_files_unknown"], ["fantasma.pdf"])
        self.assertEqual(datos["reading_audit"]["verification"], "incompleta")
        self.assertTrue(any("no están en Gmail: fantasma.pdf" in a for a in avisos))

    def test_dos_adjuntos_con_el_mismo_nombre_y_tamano_cuentan_como_uno(self):
        datos = borrador(reportado(NOTAS[0], NOTAS[1]))
        verificar_adjuntos(datos, en_gmail(NOTAS, NOTAS))       # el cliente reenvió el mismo archivo
        self.assertEqual(datos["reading_audit"]["verification"], "verificada")

    def test_sin_adjuntos_en_el_hilo_ni_en_el_informe_la_lectura_esta_verificada(self):
        datos = borrador()
        verificar_adjuntos(datos, [])
        self.assertEqual(datos["reading_audit"]["verification"], "verificada")

    def test_si_gmail_no_responde_queda_sin_verificar_y_no_se_da_por_completo(self):
        datos = borrador(reportado(NOTAS[0], NOTAS[1]))
        avisos = verificar_adjuntos(datos, None)
        self.assertEqual(datos["reading_audit"]["verification"], "sin_verificar")
        self.assertIs(datos["analysis_complete"], False)
        self.assertTrue(any("Lectura sin verificar" in a for a in avisos))

    def test_solo_el_prompt_v6_en_adelante_se_verifica_por_adjuntos(self):
        for version, esperado in (("PRECISO_V6.0", True), ("PRECISO_V7.1", True), ("PRECISO_V5.0", False),
                                  ("PRECISO_V4.5_DRIVE", False), ("", False), (None, False)):
            self.assertIs(usa_verificacion_por_adjuntos({"skill_version": version}), esperado, version)

    def test_el_resumen_de_lectura_nombra_lo_que_falta(self):
        datos = borrador(reportado(NOTAS[0], NOTAS[1]))
        verificar_adjuntos(datos, en_gmail(NOTAS, PLANO))
        resumen = reading_summary(datos)
        self.assertEqual(resumen["status"], "pendiente")
        self.assertIn("plano.txt", " ".join(resumen["warnings"]))
        completo = borrador(reportado(NOTAS[0], NOTAS[1]))
        verificar_adjuntos(completo, en_gmail(NOTAS))
        self.assertEqual(reading_summary(completo)["status"], "completa declarada")

    def test_el_esquema_acepta_size_bytes_entero_y_read_via_conocido(self):
        bueno = borrador(reportado(NOTAS[0], NOTAS[1], read_via="raw_mime"))
        self.assertEqual(validate(bueno), [])
        for campo, valor in (("size_bytes", "3391"), ("size_bytes", True), ("read_via", "drive")):
            malo = borrador(reportado(NOTAS[0], NOTAS[1]))
            malo["attachments"][0][campo] = valor
            self.assertTrue(validate(malo), (campo, valor))


# Cubre: T-41
class AdjuntosDeGmailTests(unittest.TestCase):
    def test_solo_cuentan_los_adjuntos_reales_que_mando_el_cliente(self):
        hilo = {"id": "T1", "messages": [
            mensaje("in1", [NOTAS, ("logo.png", "x", "image/png", True)], fecha="1"),
            mensaje("propio", [PLANO], de="Anka <ankacomercialia@gmail.com>", fecha="2"),
            mensaje("borrador", [PLANO], etiquetas=("DRAFT",), fecha="3"),
            mensaje("in2", [PLANO], fecha="4")]}
        lista = adjuntos_recibidos(hilo, PROPIAS)
        self.assertEqual([(a["message_id"], a["filename"], a["size"]) for a in lista],
                         [("in1", "notas.txt", len(NOTAS[1].encode())), ("in2", "plano.txt", len(PLANO[1].encode()))])


class GmailConAdjuntos(GmailConHilos):
    """Gmail de pruebas que además entrega adjuntos por `attachmentId` y cuenta cuántas veces se bajaron."""

    def __init__(self, drafts, hilos):
        super().__init__(drafts, hilos)
        self.descargas = 0
        self.contenidos: dict[str, str] = {}

    def messages(self):
        gmail, base = self, super().messages()

        class _Adjuntos:
            def get(self, **kw):
                gmail.descargas += 1
                return _Exec({"data": gmail.contenidos[kw["id"]]})

        class _Mensajes:
            def get(self, *a, **k):
                return base.get(*a, **k)

            def attachments(self):
                return _Adjuntos()
        return _Mensajes()


def hilo_con_adjunto_por_id(gmail, nombre, contenido, mid="in1"):
    """Un hilo cuyo adjunto no viene en el mensaje: hay que pedirlo a Gmail por su id."""
    gmail.contenidos["adj-" + nombre] = b64(contenido)
    parte = {"filename": nombre, "mimeType": "text/plain",
             "body": {"size": len(contenido.encode()), "attachmentId": "adj-" + nombre}}
    gmail.hilos["T1"] = [{"id": mid, "threadId": "T1", "internalDate": "1000", "labelIds": ["INBOX"],
                          "payload": {"mimeType": "multipart/mixed",
                                      "headers": [{"name": "From", "value": "c@example.com"}], "parts": [parte]}}]


# Cubre: T-41
class ServicioDeVerificacionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.directorio = Path(self.temp.name)

    def test_verifica_la_lectura_y_comprueba_la_tabla_contra_el_texto_local(self):
        gmail = GmailConAdjuntos({}, {})
        hilo_con_adjunto_por_id(gmail, *NOTAS[:2])
        datos = borrador(reportado(NOTAS[0], NOTAS[1]),
                         activities=[{"activity": "Levantamiento", "quantity": "126 ha", "fuente": "notas.txt"}])
        avisos, consultado = verificar_lectura(gmail, datos, PROPIAS, self.directorio)
        self.assertTrue(consultado)
        self.assertEqual(datos["reading_audit"]["verification"], "verificada")
        self.assertEqual(datos["evidence_check"]["estado"], "verificada")
        self.assertEqual(avisos, [])

    def test_baja_cada_adjunto_una_sola_vez_mientras_el_hilo_no_cambie(self):
        gmail = GmailConAdjuntos({}, {})
        hilo_con_adjunto_por_id(gmail, *NOTAS[:2])
        for _ in range(3):
            verificar_lectura(gmail, borrador(reportado(NOTAS[0], NOTAS[1])), PROPIAS, self.directorio)
        self.assertEqual(gmail.descargas, 1)
        hilo_con_adjunto_por_id(gmail, "otro.txt", "Datos nuevos", mid="in2")      # el hilo cambió
        verificar_lectura(gmail, borrador(reportado("otro.txt", "Datos nuevos")), PROPIAS, self.directorio)
        self.assertEqual(gmail.descargas, 2)

    def test_gmail_que_no_responde_no_descarta_el_borrador(self):
        gmail = GmailConAdjuntos({}, {})            # el hilo no está disponible: ConnectionError
        datos = borrador(reportado(NOTAS[0], NOTAS[1]))
        avisos, consultado = verificar_lectura(gmail, datos, PROPIAS, self.directorio)
        self.assertFalse(consultado)
        self.assertEqual(datos["reading_audit"]["verification"], "sin_verificar")
        self.assertIs(datos["analysis_complete"], False)
        self.assertTrue(avisos)

    def test_un_adjunto_que_no_baja_deja_la_tabla_no_comprobable_y_no_se_guarda_el_texto(self):
        gmail = GmailConAdjuntos({}, {})
        hilo_con_adjunto_por_id(gmail, *NOTAS[:2])
        del gmail.contenidos["adj-notas.txt"]
        datos = borrador(reportado(NOTAS[0], NOTAS[1]),
                         activities=[{"activity": "Levantamiento", "quantity": "126 ha", "fuente": "notas.txt"}])
        verificar_lectura(gmail, datos, PROPIAS, self.directorio)
        self.assertEqual(datos["evidence_check"]["estado"], "no_comprobable")
        self.assertEqual(datos["evidence_check"]["diferencias"], [])
        self.assertEqual(list(self.directorio.glob("*.textos.json")), [])


# Cubre: T-41
class PuenteConVerificacionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        patcher = patch.object(settings, "DATA_DIR", Path(self.temp.name))
        patcher.start()
        self.addCleanup(patcher.stop)
        self.checkpoint = Path(self.temp.name) / "state.json"

    def pasada(self, gmail, cache):
        return sync.sync_once(gmail, work_import.dashboard_upsert, self.checkpoint, cache, PROPIAS)

    def gmail(self, datos, con_hilo=True):
        gmail = GmailConAdjuntos({"d1": draft_message(datos)}, {})
        if con_hilo:
            gmail.hilos["T1"] = [mensaje("m1", [NOTAS, PLANO])]
        return gmail

    def guardado(self):
        return quotes.get_quote("T1")["analysis"]

    def test_un_borrador_v6_que_omite_un_adjunto_se_importa_y_nombra_lo_que_falta(self):
        self.pasada(self.gmail(borrador(reportado(NOTAS[0], NOTAS[1]))), {})
        analisis = self.guardado()
        self.assertEqual(analisis["reading_audit"]["attachment_files_pending"], ["plano.txt"])
        self.assertIs(analisis["analysis_complete"], False)
        self.assertEqual(quotes.get_quote("T1")["status"], QuoteStatus.REVIEW)
        self.assertTrue(pantalla_caso._reading_is_blocked(analisis))
        self.assertIn("Falta leer: plano.txt.", pantalla_caso._texto_de_lectura_pendiente(analisis))

    def test_un_borrador_v6_completo_no_se_oculta_ni_necesita_drive(self):
        gmail = self.gmail(borrador(reportado(NOTAS[0], NOTAS[1]), reportado(PLANO[0], PLANO[1])))
        self.pasada(gmail, {})
        analisis = self.guardado()
        self.assertEqual(analisis["reading_audit"]["verification"], "verificada")
        self.assertFalse(pantalla_caso._reading_is_blocked(analisis))
        self.assertEqual(analisis.get("DRIVE_PREFLIGHT"), {})

    def test_si_gmail_falla_el_borrador_se_importa_sin_verificar_y_se_reintenta(self):
        gmail, cache = self.gmail(borrador(reportado(NOTAS[0], NOTAS[1]), reportado(PLANO[0], PLANO[1])), False), {}
        self.pasada(gmail, cache)
        self.assertEqual(self.guardado()["reading_audit"]["verification"], "sin_verificar")
        self.assertFalse(pantalla_caso._reading_is_blocked(self.guardado()))     # se avisa, pero no se oculta
        self.assertNotIn("d1", cache)                                           # no se recuerda: se reintentará
        gmail.hilos["T1"] = [mensaje("m1", [NOTAS, PLANO])]                       # Gmail vuelve
        self.pasada(gmail, cache)
        self.assertEqual(self.guardado()["reading_audit"]["verification"], "verificada")
        self.assertIs(self.guardado()["analysis_complete"], True)

    def test_un_borrador_anterior_sin_drive_ni_verificacion_no_se_oculta(self):
        # un caso de Work sin DRIVE_PREFLIGHT ya no desaparece de la pantalla por eso
        self.pasada(FakeGmailSolo({"d1": draft_message(payload(skill_version="PRECISO_V5.0"))}), {})
        self.assertFalse(pantalla_caso._reading_is_blocked(self.guardado()))


class FakeGmailSolo(GmailConAdjuntos):
    def __init__(self, drafts):
        super().__init__(drafts, {})


# Cubre: T-41
class PantallaTests(unittest.TestCase):
    def test_solo_la_verificacion_incompleta_oculta_los_resultados(self):
        def bloqueado(**audit):
            return pantalla_caso._reading_is_blocked({"engine": "chatgpt_work", "reading_audit": audit})
        self.assertTrue(bloqueado(verification="incompleta"))
        self.assertFalse(bloqueado(verification="verificada"))
        self.assertFalse(bloqueado(verification="sin_verificar"))
        self.assertFalse(bloqueado())
        self.assertFalse(pantalla_caso._reading_is_blocked({"engine": "local", "reading_audit": {
            "verification": "incompleta"}}))

    def test_el_expediente_de_drive_anterior_sigue_ocultando_si_su_preflight_falla(self):
        self.assertTrue(pantalla_caso._reading_is_blocked({
            "engine": "chatgpt_work", "DRIVE_PREFLIGHT": {"thread_id": "T1"}, "reading_audit": {}}))

    def test_el_aviso_nombra_los_archivos_y_habla_de_gmail(self):
        texto = pantalla_caso._texto_de_lectura_pendiente({"reading_audit": {
            "verification": "incompleta", "attachment_files_pending": ["plano.kml"]}})
        self.assertIn("verificar los adjuntos contra Gmail", texto)
        self.assertIn("Falta leer: plano.kml.", texto)
        self.assertNotIn("manifiesto", texto)
        extra = pantalla_caso._texto_de_lectura_pendiente({"reading_audit": {
            "verification": "incompleta", "attachment_files_unknown": ["x.pdf"]}})
        self.assertIn("Work reportó adjuntos que Gmail no tiene: x.pdf.", extra)


# Cubre: T-41, T-38
class ComprobacionNumericaTests(unittest.TestCase):
    TEXTOS = {"correo": "", "adjuntos": [{"filename": "poligono.kml", "coverage": "completa", "source_id": "x", "text":
              "Elemento: CP-01\n  lon=-74.30250000 lat=4.77900000 alt=2584.30000000\n  Área: 126,51 ha"}]}

    def tabla(self, extracto):
        return {"activities": [], "fields": [], "attachments": [{"filename": "poligono.kml", "evidence": [{
            "topic": "Punto", "value": "x", "location": "CP-01", "excerpt": extracto}]}]}

    def test_las_coordenadas_con_otro_formato_no_son_una_diferencia(self):
        resultado = comprobar_tabla(self.tabla("-74.3025,4.779,2584.3"), self.TEXTOS)
        self.assertEqual(resultado["diferencias"], [])
        self.assertEqual(resultado["estado"], "verificada")

    def test_un_numero_que_no_esta_en_el_archivo_si_es_una_diferencia(self):
        resultado = comprobar_tabla(self.tabla("-74.9999,4.779,2584.3"), self.TEXTOS)
        self.assertEqual(resultado["estado"], "con_diferencias")

    def test_las_coordenadas_en_pares_lon_lat_separados_por_punto_y_coma_se_leen_una_a_una(self):
        # el formato real de mi lector: «Muestras (longitud, latitud): -74.19000000,4.93000000; -74.18650000,4.92500000»
        textos = {"correo": "", "adjuntos": [{"filename": "zonas.kml", "coverage": "completa", "source_id": "x", "text":
                  "Muestras (longitud, latitud): -74.19000000,4.93000000; -74.18650000,4.92500000\n"
                  "Altitud registrada en coordenadas: mín. 2985.000, máx. 2985.000"}]}
        tabla = {"activities": [], "fields": [], "attachments": [{"filename": "zonas.kml", "evidence": [{
            "topic": "Punto", "value": "x", "location": "p", "excerpt": "-74.1865,4.925,2985.0"}]}]}
        self.assertEqual(comprobar_tabla(tabla, textos)["diferencias"], [])
        tabla["attachments"][0]["evidence"][0]["excerpt"] = "-74.1111,4.925,2985.0"
        self.assertEqual(comprobar_tabla(tabla, textos)["estado"], "con_diferencias")

    def test_un_extracto_con_palabras_se_sigue_comprobando_literal(self):
        resultado = comprobar_tabla(self.tabla("Área: 126,51 ha"), self.TEXTOS)
        self.assertEqual(resultado["diferencias"], [])
        resultado = comprobar_tabla(self.tabla("Área total: 126,51 ha"), self.TEXTOS)
        self.assertEqual(resultado["estado"], "con_diferencias")


# Cubre: T-24, T-41
class LectorDeKmlTests(unittest.TestCase):
    KML = (b'<?xml version="1.0"?><kml xmlns="http://www.opengis.net/kml/2.2"><Document>'
           b'<name>Areas de operacion</name><description>Archivo SIMULADO para pruebas. Datos ficticios.</description>'
           b'<Folder><name>Zona A</name><description>Cota media 2950 m</description>'
           b'<Placemark><name>P1</name><Point><coordinates>-74.1865,4.925,2985</coordinates></Point></Placemark>'
           b'</Folder></Document></kml>')

    def test_el_nombre_y_la_descripcion_del_documento_y_de_las_carpetas_no_se_pierden(self):
        from anka.dominio.lectores_geograficos import kml_text_file
        texto = kml_text_file("zonas.kml", self.KML)
        for esperado in ("Contenedor Document: Areas de operacion", "Archivo SIMULADO para pruebas. Datos ficticios.",
                         "Contenedor Folder: Zona A", "Cota media 2950 m", "Elemento: P1"):
            self.assertIn(esperado, texto)

    def test_un_kml_sin_nombre_ni_descripcion_en_los_contenedores_no_agrega_lineas(self):
        from anka.dominio.lectores_geograficos import kml_text_file
        simple = (b'<kml xmlns="http://www.opengis.net/kml/2.2"><Document><Placemark><name>P1</name>'
                  b'<Point><coordinates>-74.1,4.9,0</coordinates></Point></Placemark></Document></kml>')
        self.assertNotIn("Contenedor", kml_text_file("s.kml", simple))


if __name__ == "__main__":
    unittest.main()
