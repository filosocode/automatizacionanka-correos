"""Instancia única entre procesos: candado del sistema, recuperación tras un cierre brusco y liberación de lo propio.

Cubre: U-81
"""

from tests import _isolate  # noqa: F401  (debe ir primero)

import json
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

from anka.infra import instancia_unica
from anka.infra.instancia_unica import InstanciaUnica, OtraInstanciaActiva, leer_informacion

RAIZ = Path(__file__).resolve().parents[1]
# Un proceso aparte que toma el candado, avisa por la salida estándar y lo conserva hasta que le cierren la entrada.
TENEDOR = """
import sys
sys.path.insert(0, {raiz!r})
from pathlib import Path
from anka.infra.instancia_unica import InstanciaUnica, OtraInstanciaActiva
try:
    candado = InstanciaUnica(Path({ruta!r}), "receptor de prueba").adquirir()
except OtraInstanciaActiva:
    print("PERDIO", flush=True)
    raise SystemExit(0)
import os
print("GANO", os.getpid(), flush=True)
sys.stdin.read()          # conserva el candado hasta que el padre cierre la entrada o mate el proceso
candado.liberar()
"""
CARRERA = """
import sys, time
sys.path.insert(0, {raiz!r})
from pathlib import Path
from anka.infra.instancia_unica import InstanciaUnica, OtraInstanciaActiva
try:
    candado = InstanciaUnica(Path({ruta!r}), "receptor de prueba").adquirir()
except OtraInstanciaActiva:
    print("PERDIO", flush=True)
    raise SystemExit(0)
print("GANO", flush=True)
time.sleep({retener})
candado.liberar()
"""


def esperar_ganador(proceso: subprocess.Popen) -> int:
    """Espera a que el proceso diga que tomó el candado y devuelve su PID."""
    linea = proceso.stdout.readline().split()
    assert linea[:1] == ["GANO"], linea
    return int(linea[1])


def lanzar(codigo: str, **valores: object) -> subprocess.Popen:
    # el intérprete real, no el lanzador del entorno virtual: así el PID del proceso es el que toma el candado
    interprete = getattr(sys, "_base_executable", sys.executable)
    return subprocess.Popen([interprete, "-I", "-c", codigo.format(raiz=str(RAIZ), **valores)],
                            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True)


class InstanciaUnicaTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.ruta = Path(self.temp.name) / "datos" / "receptor.lock"

    def terminar(self, proceso: subprocess.Popen) -> None:
        if proceso.poll() is None:
            proceso.kill()
        proceso.wait(10)
        for flujo in (proceso.stdin, proceso.stdout):
            if flujo:
                flujo.close()

    def test_toma_el_candado_deja_su_informacion_y_la_borra_al_salir(self):
        with InstanciaUnica(self.ruta, "receptor") as instancia:
            info = leer_informacion(self.ruta)
            self.assertEqual((info["pid"], info["token"], info["nombre"]),
                             (__import__("os").getpid(), instancia.token, "receptor"))
            self.assertIsNone(instancia.recuperada_de)
        self.assertTrue(self.ruta.exists())                 # el archivo no se borra: otro proceso pudo abrirlo
        self.assertIsNone(leer_informacion(self.ruta))      # pero ya no dice que alguien lo tiene

    def test_una_segunda_instancia_se_niega_y_dice_quien_lo_tiene(self):
        with InstanciaUnica(self.ruta, "receptor"):
            with self.assertRaises(OtraInstanciaActiva) as error:
                InstanciaUnica(self.ruta, "receptor").adquirir()
            self.assertIn(f"PID {__import__('os').getpid()}", str(error.exception))
            self.assertIsNotNone(leer_informacion(self.ruta))   # la información sigue legible con el candado tomado

    def test_el_candado_se_puede_volver_a_tomar_tras_liberarlo_y_liberar_dos_veces_es_inofensivo(self):
        primera = InstanciaUnica(self.ruta, "receptor").adquirir()
        primera.liberar()
        primera.liberar()
        with InstanciaUnica(self.ruta, "receptor"):
            pass

    def test_la_excepcion_dentro_del_with_libera_el_candado(self):
        with self.assertRaises(RuntimeError), InstanciaUnica(self.ruta, "receptor"):
            raise RuntimeError("fallo del receptor")
        with InstanciaUnica(self.ruta, "receptor"):
            pass

    def test_otro_proceso_independiente_lo_tiene_y_este_se_niega(self):
        otro = lanzar(TENEDOR, ruta=str(self.ruta))
        self.addCleanup(self.terminar, otro)
        pid = esperar_ganador(otro)
        with self.assertRaises(OtraInstanciaActiva) as error:
            InstanciaUnica(self.ruta, "receptor").adquirir()
        self.assertIn(f"PID {pid}", str(error.exception))
        self.assertEqual(leer_informacion(self.ruta)["pid"], pid)

    def test_tras_un_cierre_brusco_el_sistema_libera_el_candado_y_se_informa_la_instancia_muerta(self):
        otro = lanzar(TENEDOR, ruta=str(self.ruta))
        self.addCleanup(self.terminar, otro)
        pid = esperar_ganador(otro)
        otro.kill()                                         # sin `liberar()`: el archivo conserva su información
        otro.wait(10)
        self.assertEqual(leer_informacion(self.ruta)["pid"], pid)
        with InstanciaUnica(self.ruta, "receptor") as instancia:
            self.assertEqual(instancia.recuperada_de["pid"], pid)         # detecta el bloqueo obsoleto
            self.assertEqual(leer_informacion(self.ruta)["token"], instancia.token)

    def test_con_varios_procesos_a_la_vez_gana_exactamente_uno(self):
        self.ruta.parent.mkdir(parents=True)
        procesos = [lanzar(CARRERA, ruta=str(self.ruta), retener=6) for _ in range(6)]
        for proceso in procesos:
            self.addCleanup(self.terminar, proceso)
        resultados = [proceso.stdout.readline().strip() for proceso in procesos]
        self.assertEqual(sorted(resultados), ["GANO"] + ["PERDIO"] * 5)

    def test_una_instancia_no_borra_ni_suelta_lo_de_otra(self):
        vieja = InstanciaUnica(self.ruta, "receptor").adquirir()
        ajena = {"nombre": "receptor", "pid": 999999, "token": "de-otra-instancia", "iniciado": "2026-10-17T00:00:00-05:00"}
        crudo = json.dumps(ajena).encode().ljust(instancia_unica.TAMANO_INFORMACION, b"\0")
        with self.ruta.open("r+b") as archivo:              # otra instancia (más nueva) dejó su información
            archivo.write(crudo)
        vieja.liberar()
        self.assertEqual(leer_informacion(self.ruta)["token"], "de-otra-instancia")   # no se borró lo ajeno
        with InstanciaUnica(self.ruta, "receptor") as nueva:                          # y el candado propio sí se soltó
            self.assertEqual(nueva.recuperada_de["token"], "de-otra-instancia")

    def test_el_mismo_objeto_no_toma_dos_veces_su_propio_candado(self):
        with InstanciaUnica(self.ruta, "receptor") as instancia:
            with self.assertRaises(RuntimeError):
                instancia.adquirir()
            self.assertIsNotNone(leer_informacion(self.ruta))     # el error no tocó el candado ni la información

    def test_informacion_ilegible_o_ausente_no_rompe_la_lectura(self):
        self.assertIsNone(leer_informacion(self.ruta))
        self.ruta.parent.mkdir(parents=True)
        self.ruta.write_bytes(b"\xff\xfe no es json" + b"\0" * 50)
        self.assertIsNone(leer_informacion(self.ruta))
        with InstanciaUnica(self.ruta, "receptor") as instancia:      # y se puede tomar encima
            self.assertIsNone(instancia.recuperada_de)

    def test_un_cierre_ordenado_deja_el_archivo_listo_para_el_siguiente_arranque(self):
        otro = lanzar(TENEDOR, ruta=str(self.ruta))
        self.addCleanup(self.terminar, otro)
        esperar_ganador(otro)
        otro.stdin.close()                                  # el proceso sale por su camino normal y libera
        for _ in range(100):
            if otro.poll() is not None:
                break
            time.sleep(0.1)
        self.assertIsNotNone(otro.poll())
        self.assertIsNone(leer_informacion(self.ruta))
        with InstanciaUnica(self.ruta, "receptor") as instancia:
            self.assertIsNone(instancia.recuperada_de)


if __name__ == "__main__":
    unittest.main()
