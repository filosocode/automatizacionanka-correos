"""Cubre: T-38. La tabla de Work se comprueba contra los textos del expediente sin interpretar ni bloquear."""

from tests import _isolate  # noqa: F401  (debe ir primero)

import base64
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from anka.cli import work_sync as sync
from anka.dominio.verificacion_de_evidencia import avisos_de, comprobar_tabla, numeros_del_texto, valores
from anka.infra import instantaneas_de_manifiesto as copias
from anka.infra.drive_exchange import DriveExchange
from anka.servicios import work_drive
from tests.fake_work_drive import MemoryDrive
from tests.test_puente_determinista import GmailConHilos, instantanea
from tests.test_work_drive import message as mensaje_de_drive
from tests.test_work_sync import draft_message, payload


def textos(**cambios):
    base = {"thread_id": "T1", "revision": "rev-2", "incompleto": False,
            "correo": "Necesitamos un levantamiento de 126 ha en Madrid. Plazo: 2 meses.",
            "adjuntos": [
                {"filename": "TdR.pdf", "source_id": "s1", "coverage": "completa",
                 "text": "[Página 1]\nÁrea aproximada 126 ha\n[Página 2]\nSe requieren 12 puntos de control.\n"
                         "El eje mide 1.250 m y el predio colinda con la vía Madrid-Facatativá."},
                {"filename": "Cronograma.xlsx", "source_id": "s2", "coverage": "completa",
                 "text": "Hoja Plan: 8 h/día, 60 días"}]}
    base.update(cambios)
    return base


def tabla(cantidad="126 ha", fuente="TdR.pdf, p. 1", **extra):
    return {"activities": [{"activity": "Levantamiento", "quantity": cantidad, "fuente": fuente}], **extra}


class NumerosTests(unittest.TestCase):
    def test_los_numeros_con_puntos_y_comas_se_interpretan_sin_adivinar(self):
        # Cubre: T-38
        self.assertEqual(valores("82"), {82.0})
        self.assertEqual(valores("12,5"), {12.5})
        self.assertEqual(valores("2.5"), {2.5})
        self.assertEqual(valores("1.250"), {1.25, 1250.0})        # ambiguo: se admiten ambos
        self.assertEqual(valores("1.250,5"), {1250.5})
        self.assertEqual(valores("1,250.5"), {1250.5})
        self.assertEqual(valores("1.250.000"), {1250000.0})
        self.assertIn(1250.0, numeros_del_texto("El eje mide 1.250 m"))


class ComprobarTablaTests(unittest.TestCase):
    def test_una_cantidad_que_esta_en_el_archivo_citado_queda_comprobada(self):
        # Cubre: T-38
        resultado = comprobar_tabla(tabla(), textos())
        self.assertEqual(resultado["estado"], "verificada")
        self.assertEqual((resultado["diferencias"], resultado["no_comprobables"]), ([], []))
        self.assertGreaterEqual(resultado["comprobadas"], 2)       # la cantidad y el archivo citado
        self.assertEqual(avisos_de(resultado), [])

    def test_una_cantidad_inventada_es_una_diferencia_con_su_ubicacion(self):
        # Cubre: T-38
        resultado = comprobar_tabla(tabla(cantidad="300 ha"), textos())
        self.assertEqual(resultado["estado"], "con_diferencias")
        self.assertIn("actividad 1", resultado["diferencias"][0])
        self.assertIn("la cantidad 300", resultado["diferencias"][0])
        self.assertTrue(avisos_de(resultado)[0].startswith("Comprobación de fuentes: "))

    def test_una_cantidad_que_esta_en_otro_archivo_se_dice(self):
        # Cubre: T-38
        resultado = comprobar_tabla(tabla(cantidad="60 días", fuente="TdR.pdf, p. 2"), textos())
        self.assertIn("no está en el archivo citado (TdR.pdf)", resultado["diferencias"][0])

    def test_con_un_adjunto_sin_texto_lo_que_no_se_encuentra_es_no_comprobable_no_diferencia(self):
        # Cubre: T-38
        ilegible = textos()
        ilegible["adjuntos"].append({"filename": "predio.kml", "source_id": "s3", "coverage": None, "text": ""})
        resultado = comprobar_tabla(tabla(cantidad="300 ha", fuente="predio.kml"), ilegible)
        self.assertEqual(resultado["diferencias"], [])
        self.assertEqual(resultado["estado"], "no_comprobable")
        self.assertIn("adjuntos sin texto", resultado["no_comprobables"][0])
        self.assertEqual(comprobar_tabla(tabla(cantidad="300 ha"), textos(incompleto=True))["diferencias"], [])

    def test_un_archivo_que_no_esta_en_el_expediente_y_una_pagina_inexistente_son_diferencias(self):
        # Cubre: T-38
        resultado = comprobar_tabla(tabla(fuente="Plano_final.dwg, lámina 2; TdR.pdf, p. 5"), textos())
        detalle = " ".join(resultado["diferencias"])
        self.assertIn("«Plano_final.dwg», que no está en el expediente", detalle)
        self.assertIn("página 5 de «TdR.pdf», que se leyó con 2 páginas", detalle)

    def test_los_campos_tambien_citan_archivos(self):
        # Cubre: T-38
        resultado = comprobar_tabla({"fields": [{"name": "Plazo", "value": "x", "source": "Oferta.pdf p. 1"}]},
                                    textos())
        self.assertIn("campo 1", resultado["diferencias"][0])

    def test_los_extractos_deben_aparecer_literales_aunque_se_corten_con_puntos_suspensivos(self):
        # Cubre: T-38
        def con(extracto):
            return comprobar_tabla({"attachments": [{"filename": "TdR.pdf", "evidence": [
                {"topic": "t", "value": "v", "location": "p. 2", "excerpt": extracto}]}]}, textos())
        self.assertEqual(con("Se requieren 12 puntos de control")["estado"], "verificada")
        self.assertEqual(con("«El eje  mide 1.250 m» … vía Madrid-Facatativá")["estado"], "verificada")
        self.assertEqual(con("Se necesitan quince puntos de apoyo topográfico")["estado"], "con_diferencias")
        self.assertIn("sí en otro texto", con("Hoja Plan: 8 h/día, 60 días")["diferencias"][0])

    def test_sin_textos_no_se_comprueba_nada(self):
        # Cubre: T-38
        self.assertIsNone(comprobar_tabla(tabla(), None))
        self.assertEqual(avisos_de(None), [])

    def test_los_avisos_tienen_tope(self):
        # Cubre: T-38
        filas = [{"activity": f"a{i}", "quantity": f"{900 + i} ha", "fuente": ""} for i in range(14)]
        avisos = avisos_de(comprobar_tabla({"activities": filas}, textos()))
        self.assertEqual(len(avisos), 11)
        self.assertIn("4 diferencia(s) más", avisos[-1])


class PuenteYPreparadorTests(unittest.TestCase):
    def test_el_puente_agrega_avisos_y_el_resultado_al_caso(self):
        # Cubre: T-38
        with tempfile.TemporaryDirectory() as carpeta:
            directorio = Path(carpeta) / "manifiestos"
            directorio.mkdir()
            (directorio / "T1.json").write_text(json.dumps(instantanea()), encoding="utf-8")
            copias.guardar_textos(directorio, "T1", "rev-2", "Se requieren 126 ha.", [])
            datos = payload(thread="T1", message="in1", activities=[
                {"activity": "Levantamiento", "quantity": "999 ha", "fuente": "correo"}])
            gmail = GmailConHilos({"d1": draft_message(datos)}, {})
            capturado = []
            sync.sync_once(gmail, capturado.append, Path(carpeta) / "state.json", {})
            enviado = json.loads(base64.urlsafe_b64decode(capturado[0]))
            self.assertEqual(enviado["evidence_check"]["estado"], "con_diferencias")
            self.assertTrue(any("la cantidad 999" in aviso for aviso in enviado["_avisos_del_puente"]))

    def test_unos_textos_de_otra_revision_no_se_usan(self):
        # Cubre: T-38
        with tempfile.TemporaryDirectory() as carpeta:
            copias.guardar_textos(Path(carpeta), "T1", "rev-vieja", "texto", [])
            self.assertIsNone(copias.cargar_textos(Path(carpeta), "T1", "rev-2"))
            self.assertIsNotNone(copias.cargar_textos(Path(carpeta), "T1", "rev-vieja"))

    def test_el_tope_de_texto_local_marca_el_hilo_como_incompleto(self):
        # Cubre: T-38
        with tempfile.TemporaryDirectory() as carpeta, patch.object(copias, "MAX_TEXTO_LOCAL", 10):
            copias.guardar_textos(Path(carpeta), "T1", "r", "correo largo largo",
                                  [{"filename": "a.txt", "text": "x" * 50}])
            guardado = copias.cargar_textos(Path(carpeta), "T1")
            self.assertTrue(guardado["incompleto"])
            self.assertEqual(comprobar_tabla(tabla(cantidad="999 ha", fuente="a.txt"), guardado)["diferencias"], [])

    def test_el_preparador_guarda_el_texto_una_vez_por_revision(self):
        # Cubre: T-38
        drive, gmail = MemoryDrive(), MagicMock()
        hilo = {"id": "t1", "messages": [mensaje_de_drive()]}
        with tempfile.TemporaryDirectory() as carpeta:
            directorio = Path(carpeta)

            def publicar():
                return work_drive.prepare_thread(gmail, DriveExchange(drive), "reception", hilo,
                                                 {"anka@example.com"}, directorio)
            publicar()
            manifiesto = copias.cargar(directorio, "t1")
            guardado = copias.cargar_textos(directorio, "t1", manifiesto["revision"])
            self.assertIn("Correo", guardado["correo"])
            self.assertIn("Area 82 ha", guardado["adjuntos"][0]["text"])
            self.assertEqual(guardado["adjuntos"][0]["filename"], "alcance.txt")
            with patch.object(DriveExchange, "read_bytes", side_effect=AssertionError("no debe releer")):
                self.assertEqual(publicar()["status"], "sin_cambios")

    def test_un_fallo_al_bajar_el_texto_no_frena_la_preparacion(self):
        # Cubre: T-38
        drive, gmail = MemoryDrive(), MagicMock()
        hilo = {"id": "t1", "messages": [mensaje_de_drive()]}
        with tempfile.TemporaryDirectory() as carpeta, \
                patch.object(DriveExchange, "read_bytes", side_effect=ConnectionError("sin red")):
            resultado = work_drive.prepare_thread(gmail, DriveExchange(drive), "reception", hilo,
                                                  {"anka@example.com"}, Path(carpeta))
            self.assertEqual(resultado["status"], "preparado")
            self.assertIsNone(copias.cargar_textos(Path(carpeta), "t1"))


if __name__ == "__main__":
    unittest.main()
