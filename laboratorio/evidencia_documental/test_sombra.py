"""Pruebas del modo sombra (T-119, fase 3). Solo laboratorio: documentos ficticios y los casos del banco, base SQLite temporal, sin red."""

import hashlib
import os
import sqlite3
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

from laboratorio.banco_documental.pdf_minimo import Pdf
from laboratorio.evidencia_documental import casos, sombra
from laboratorio.evidencia_documental.versiones import VERSION_EXTRACTOR, VERSION_VALIDADOR

AQUI = Path(__file__).resolve().parent
RELLENO = "Texto de relleno para que la página supere el mínimo de caracteres y no se trate como escaneada " * 2


def pdf(*lineas):
    p = Pdf("ficticio")
    pagina = p.nueva_pagina()
    for i, linea in enumerate([*lineas, RELLENO[:95], RELLENO[95:190]]):
        pagina.texto(60, 80 + 18 * i, linea)
    return p.serializar()


COLGAR = (sys.executable, "-c", "import time; time.sleep(60)")
CAER = (sys.executable, "-c", "import sys; sys.exit(3)")
BASURA = (sys.executable, "-c", "import sys; open(sys.argv[4], 'w').write('no es json')")


class Base(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.dir = Path(self._tmp.name)
        self.db = self.dir / "sombra" / "sombra_lab.db"

    def sombra(self, **kw):
        kw.setdefault("habilitada", True)
        kw.setdefault("plazo_s", 120)
        return sombra.Sombra(sombra.Configuracion(ruta_db=self.db, **kw))

    def filas(self, tabla="extraccion"):
        con = sqlite3.connect(self.db)
        try:
            return con.execute(f"SELECT * FROM {tabla}").fetchall()
        finally:
            con.close()


class Interruptor(Base):
    def test_01_apagado_por_defecto_no_hace_nada_ni_crea_la_base(self):
        s = sombra.Sombra(sombra.Configuracion(ruta_db=self.db))
        self.assertFalse(sombra.Configuracion(ruta_db=self.db).habilitada)
        r = s.procesar("job1", {"a.pdf": pdf("Levantar 3 ha")})
        self.assertEqual("desactivado", r["estado"])
        self.assertFalse(self.db.exists())
        self.assertFalse(self.db.parent.exists())

    def test_02_el_entorno_solo_enciende_con_1(self):
        for valor, esperado in ((None, False), ("0", False), ("true", False), ("", False), ("1", True)):
            with self.subTest(valor), mock.patch.dict(os.environ, clear=False):
                os.environ.pop(sombra.INTERRUPTOR_ENTORNO, None)
                if valor is not None:
                    os.environ[sombra.INTERRUPTOR_ENTORNO] = valor
                self.assertEqual(esperado, sombra.desde_entorno(self.db).habilitada)

    def test_03_el_archivo_interruptor_detiene_aunque_este_habilitado(self):
        apagado = self.dir / "sombra.off"
        apagado.write_text("", encoding="utf-8")
        r = self.sombra(archivo_apagado=apagado).procesar("job1", {"a.pdf": pdf("Levantar 3 ha")})
        self.assertEqual("desactivado", r["estado"])
        self.assertFalse(self.db.exists())

    def test_04_apagar_a_mitad_conserva_lo_guardado_y_no_procesa_mas(self):
        apagado = self.dir / "sombra.off"
        s = self.sombra(archivo_apagado=apagado)
        s.procesar("job1", {"a.pdf": pdf("Levantar 3 ha")})
        apagado.write_text("", encoding="utf-8")
        self.assertEqual("desactivado", s.procesar("job2", {"b.pdf": pdf("Levantar 4 ha")})["estado"])
        self.assertEqual(["job1"], [f[1] for f in self.filas()])


class Almacenamiento(Base):
    def test_10_guarda_extraccion_evidencias_y_versiones(self):
        r = self.sombra().procesar("job1", {"a.pdf": pdf("Levantar 3 ha y 12 puntos")})
        self.assertEqual("ok", r["archivos"]["a.pdf"]["estado"])
        fila = self.sombra().consultar("job1")[0]
        self.assertEqual((VERSION_EXTRACTOR, VERSION_VALIDADOR, "no_determinable", 0), (
            fila["version_extractor"], fila["version_validador"], fila["exhaustividad"], fila["n_errores"]))
        self.assertEqual(2, fila["n_evidencias"])
        self.assertEqual(2, len(self.filas("evidencia")))
        self.assertEqual(hashlib.sha256(pdf("Levantar 3 ha y 12 puntos")).hexdigest(), fila["sha256"])

    def test_11_idempotente_repetir_no_duplica(self):
        s = self.sombra()
        archivos = {"a.pdf": pdf("Levantar 3 ha")}
        s.procesar("job1", archivos)
        antes = (self.filas(), self.filas("evidencia"))
        r = s.procesar("job1", archivos)
        self.assertEqual("existente", r["archivos"]["a.pdf"]["estado"])
        self.assertEqual(antes, (self.filas(), self.filas("evidencia")))

    def test_12_version_nueva_del_extractor_crea_fila_y_conserva_la_anterior(self):
        s = self.sombra()
        archivos = {"a.pdf": pdf("Levantar 3 ha")}
        s.procesar("job1", archivos)
        with mock.patch.object(sombra, "VERSION_EXTRACTOR", "extractor-lab/99"):
            r = s.procesar("job1", archivos)
        self.assertEqual("nueva", r["archivos"]["a.pdf"]["fila"])
        self.assertEqual([VERSION_EXTRACTOR, "extractor-lab/99"], [f["version_extractor"] for f in s.consultar("job1")])

    def test_13_mismo_archivo_en_otro_trabajo_es_otra_fila(self):
        s = self.sombra()
        s.procesar("job1", {"a.pdf": pdf("Levantar 3 ha")})
        s.procesar("job2", {"a.pdf": pdf("Levantar 3 ha")})
        filas = self.filas()
        self.assertEqual(2, len(filas))
        self.assertEqual(filas[0][2], filas[1][2])   # misma huella

    def test_14_la_base_se_crea_con_version_de_esquema(self):
        self.sombra().procesar("job1", {"a.pdf": pdf("Levantar 3 ha")})
        self.assertEqual([("version_esquema", "1")], self.filas("meta"))

    def test_15_un_archivo_distinto_con_el_mismo_nombre_no_se_confunde(self):
        s = self.sombra()
        s.procesar("job1", {"a.pdf": pdf("Levantar 3 ha")})
        s.procesar("job1", {"a.pdf": pdf("Levantar 9 ha")})
        self.assertEqual(2, len(self.filas()))


class Fallos(Base):
    def test_20_plazo_excedido_mata_al_hijo_y_registra(self):
        s = self.sombra(comando=COLGAR, plazo_s=1.5)
        t = time.monotonic()
        r = s.procesar("job1", {"a.pdf": pdf("Levantar 3 ha")})
        self.assertLess(time.monotonic() - t, 10)
        self.assertEqual("plazo_excedido", r["archivos"]["a.pdf"]["estado"])
        self.assertEqual("plazo_excedido", s.consultar("job1")[0]["estado"])
        self.assertEqual([], self.filas("evidencia"))

    def test_21_el_plazo_es_del_trabajo_completo(self):
        s = self.sombra(comando=COLGAR, plazo_s=1.5)
        t = time.monotonic()
        r = s.procesar("job1", {"a.pdf": pdf("Uno 1 ha"), "b.pdf": pdf("Dos 2 ha"), "c.pdf": pdf("Tres 3 ha")})
        self.assertLess(time.monotonic() - t, 10)
        self.assertEqual({"plazo_excedido"}, {x["estado"] for x in r["archivos"].values()})

    def test_22_un_fallo_se_registra_y_se_reintenta_reemplazandolo(self):
        archivos = {"a.pdf": pdf("Levantar 3 ha")}
        self.assertEqual("fallo", self.sombra(comando=CAER).procesar("job1", archivos)["archivos"]["a.pdf"]["estado"])
        r = self.sombra().procesar("job1", archivos)
        self.assertEqual(("ok", "reemplazada"), (r["archivos"]["a.pdf"]["estado"], r["archivos"]["a.pdf"]["fila"]))
        self.assertEqual(["ok"], [f["estado"] for f in self.sombra().consultar("job1")])

    def test_23_salida_ilegible_es_fallo_no_excepcion(self):
        r = self.sombra(comando=BASURA).procesar("job1", {"a.pdf": pdf("Levantar 3 ha")})
        self.assertEqual("fallo", r["archivos"]["a.pdf"]["estado"])

    def test_24_archivo_no_soportado_falla_aislado_y_no_frena_a_los_demas(self):
        r = self.sombra().procesar("job1", {"a.docx": b"x", "b.pdf": pdf("Levantar 3 ha")})
        self.assertEqual(("fallo", "ok"), (r["archivos"]["a.docx"]["estado"], r["archivos"]["b.pdf"]["estado"]))

    def test_25_procesar_nunca_lanza(self):
        s = self.sombra()
        with mock.patch.object(sombra.Sombra, "_conectar", side_effect=RuntimeError("boom")):
            r = s.procesar("job1", {"a.pdf": pdf("Levantar 3 ha")})
        self.assertEqual("error_aislado", r["estado"])

    def test_26_base_inaccesible_no_lanza(self):
        s = sombra.Sombra(sombra.Configuracion(ruta_db=self.dir, habilitada=True))   # una carpeta, no un archivo
        self.assertEqual("error_aislado", s.procesar("job1", {"a.pdf": pdf("Levantar 3 ha")})["estado"])

    def test_27_lanzar_no_bloquea_al_receptor(self):
        s = self.sombra(comando=COLGAR, plazo_s=3)
        t = time.monotonic()
        hilo = s.lanzar("job1", {"a.pdf": pdf("Levantar 3 ha")})
        self.assertLess(time.monotonic() - t, 0.5)
        self.assertTrue(hilo.daemon)
        hilo.join(15)
        self.assertFalse(hilo.is_alive())
        self.assertEqual("plazo_excedido", s.consultar("job1")[0]["estado"])

    def test_28_el_receptor_sigue_aunque_la_sombra_falle(self):
        def recibir(correo, s):
            s.lanzar(correo["job"], correo["adjuntos"]).join(15)
            return "registrado"
        with mock.patch.object(sombra.Sombra, "procesar", side_effect=RuntimeError("boom")):
            # el hilo muere con su excepción, pero quien recibe el correo termina bien
            with mock.patch("threading.excepthook"):
                self.assertEqual("registrado", recibir({"job": "j", "adjuntos": {}}, self.sombra()))


class SinEfectosExternos(Base):
    def test_30_rutas_operativas_rechazadas_sin_crear_nada(self):
        base = os.environ.get("LOCALAPPDATA") or str(Path.home())
        prohibida = Path(base) / "AnkaCorreos" / "sombra_no_debe_existir.db"
        existia = prohibida.exists()
        r = sombra.Sombra(sombra.Configuracion(ruta_db=prohibida, habilitada=True)).procesar("job1", {"a.pdf": pdf("Levantar 3 ha")})
        self.assertEqual("error_aislado", r["estado"])
        self.assertEqual(existia, prohibida.exists())
        r = sombra.Sombra(sombra.Configuracion(ruta_db=self.dir / "anka.db", habilitada=True)).procesar("job1", {"a.pdf": pdf("Levantar 3 ha")})
        self.assertEqual("error_aislado", r["estado"])
        self.assertFalse((self.dir / "anka.db").exists())

    def test_31_una_base_operativa_simulada_no_cambia(self):
        operativa = self.dir / "operativa.sqlite"
        con = sqlite3.connect(operativa)
        con.execute("CREATE TABLE casos (id TEXT)")
        con.execute("INSERT INTO casos VALUES ('x')")
        con.commit()
        con.close()
        antes = hashlib.sha256(operativa.read_bytes()).hexdigest()
        self.sombra().procesar("job1", {"a.pdf": pdf("Levantar 3 ha")})
        self.assertEqual(antes, hashlib.sha256(operativa.read_bytes()).hexdigest())

    def test_32_el_codigo_no_toca_gmail_drive_work_ni_red(self):
        for nombre in ("sombra.py", "sombra_trabajador.py"):
            fuente = (AQUI / nombre).read_text(encoding="utf-8")
            for prohibido in ("gmail", "google", "drive", "work_import", "work_sync", "requests", "urllib", "socket", "smtplib", "http.client",
                              "from anka.servicios", "from anka.web", "from anka.cli", "anka.infra.store", "anka.infra.settings"):
                with self.subTest((nombre, prohibido)):
                    self.assertNotIn(f"import {prohibido}", fuente)
                    self.assertNotIn(f"from {prohibido}", fuente)

    def test_33_el_receptor_no_carga_extractor_ni_lectores(self):
        fuente = (AQUI / "sombra.py").read_text(encoding="utf-8")
        for modulo in ("extractor", "validador", "anka"):
            self.assertNotIn(f"import {modulo}", fuente)
            self.assertNotIn(f"evidencia_documental.{modulo} import", fuente)

    def test_34_el_hijo_corre_aislado_de_los_datos_reales(self):
        fuente = (AQUI / "sombra_trabajador.py").read_text(encoding="utf-8")
        self.assertLess(fuente.index("aislamiento.carpeta_temporal()"), fuente.index("from laboratorio.evidencia_documental import extractor"))

    def test_35_no_hay_residuos_del_hijo_en_el_disco(self):
        antes = set(Path(tempfile.gettempdir()).glob("anka-sombra-*"))
        self.sombra().procesar("job1", {"a.pdf": pdf("Levantar 3 ha")})
        self.assertEqual(antes, set(Path(tempfile.gettempdir()).glob("anka-sombra-*")))


class HistoricosDelBanco(Base):
    def test_40_c1_c2_c3_originales_por_la_sombra(self):
        huellas = {n: hashlib.sha256((casos.DOCUMENTOS / n).read_bytes()).hexdigest() for n in casos.ARCHIVOS.values()}
        s = self.sombra(plazo_s=180)
        esperado = {"C1": (11, False), "C2": (15, True), "C3": (29, False)}
        for caso, nombre in casos.ARCHIVOS.items():
            r = s.procesar(f"hist-{caso}", {nombre: (casos.DOCUMENTOS / nombre).read_bytes()})
            with self.subTest(caso):
                self.assertEqual("ok", r["archivos"][nombre]["estado"])
                fila = s.consultar(f"hist-{caso}")[0]
                self.assertEqual((esperado[caso][0], 0, "no_determinable"), (fila["n_evidencias"], fila["n_errores"], fila["exhaustividad"]))
        self.assertEqual(huellas, {n: hashlib.sha256((casos.DOCUMENTOS / n).read_bytes()).hexdigest() for n in casos.ARCHIVOS.values()})

    def test_41_el_resultado_guardado_coincide_con_el_directo(self):
        from laboratorio.evidencia_documental import extractor
        nombre = casos.ARCHIVOS["C3"]
        contenido = (casos.DOCUMENTOS / nombre).read_bytes()
        self.sombra().procesar("hist-C3", {nombre: contenido})
        guardados = sorted(f[1] for f in self.filas("evidencia"))
        directo = sorted(e["id"] for e in extractor.evidencia_del_caso("hist-C3", {nombre: contenido})[0]["evidencias"])
        self.assertEqual(directo, guardados)


if __name__ == "__main__":
    unittest.main()
