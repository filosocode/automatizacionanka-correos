"""Pruebas del vigilante de ANKA (T-97) con procesos y relojes simulados: no arranca nada real."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from typing import Any

from laboratorio.estabilidad import vigilante as v


class Hijo:
    """Proceso simulado: `codigo=None` sigue vivo."""

    def __init__(self, pid: int = 1000) -> None:
        self.pid = pid
        self.codigo: int | None = None

    def poll(self) -> int | None:
        return self.codigo

    def terminate(self) -> None:
        self.codigo = 1


class Mundo:
    def __init__(self) -> None:
        self.t = 1_800_000_000.0
        self.hijos: list[Hijo] = []
        self.filas: list[dict[str, Any]] = []
        self.matados: list[int] = []

    def reloj(self) -> float:
        return self.t

    def lanzar(self) -> Hijo:
        h = Hijo(1000 + len(self.hijos))
        self.hijos.append(h)
        return h

    def procesos(self) -> list[dict[str, Any]]:
        return self.filas

    def matar(self, pid: int) -> None:
        self.matados.append(pid)
        for h in self.hijos:
            if h.pid == pid:
                h.codigo = 1


class Base(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.carpeta = Path(self.tmp.name)
        self.m = Mundo()
        self.vig = v.Vigilante(self.carpeta, self.m.lanzar, self.m.procesos, self.m.matar, self.m.reloj, lambda: None, pid_propio=1)

    def log(self) -> str:
        ruta = self.carpeta / "logs" / "vigilante.log"
        return ruta.read_text(encoding="utf-8") if ruta.exists() else ""


class CicloTest(Base):
    def test_01_arranca_y_no_duplica_mientras_corre(self) -> None:
        self.assertEqual(self.vig.paso(), "arrancado")
        self.assertEqual([self.vig.paso(), self.vig.paso()], ["corriendo", "corriendo"])
        self.assertEqual(len(self.m.hijos), 1)

    def test_02_si_termina_anota_la_causa_y_lo_reinicia_tras_la_espera(self) -> None:
        self.vig.paso()
        self.m.t += 3600
        self.m.hijos[0].codigo = 0xC000013A
        self.assertEqual(self.vig.paso(), "salio")
        self.assertIn("cerrada desde fuera", self.log())
        self.assertIn("tras 3600 s", self.log())
        self.assertEqual(self.vig.paso(), "esperando")
        self.m.t += 6
        self.assertEqual(self.vig.paso(), "arrancado")
        self.assertEqual(len(self.m.hijos), 2)

    def test_03_un_error_no_capturado_apunta_al_archivo_de_errores(self) -> None:
        self.vig.paso()
        self.m.hijos[0].codigo = 1
        self.vig.paso()
        self.assertIn("supervisor_stderr.log", self.log())

    def test_04_la_espera_crece_con_las_salidas_seguidas_y_tiene_tope(self) -> None:
        esperas = []
        for _ in range(3):
            while self.vig.paso() != "arrancado":
                self.m.t += 1
            self.m.hijos[-1].codigo = 3
            self.vig.paso()
            esperas.append(self.vig.no_antes_de - self.m.t)
        self.assertEqual(esperas, [5.0, 10.0, 15.0])
        self.assertLessEqual(v.ESPERA_MAX_S, 60)

    def test_05_no_arranca_si_hay_otro_lanzador_vivo(self) -> None:
        self.m.filas = [{"ProcessId": 77, "ParentProcessId": 4, "CommandLine": r"C:\x\python.exe D:\A\supervisor.py"}]
        self.assertEqual(self.vig.paso(), "externa")
        self.assertEqual(self.m.hijos, [])
        self.assertIn("otro lanzador", self.log())
        self.m.filas = [{"ProcessId": 1, "ParentProcessId": 4, "CommandLine": "pythonw -m laboratorio.estabilidad.vigilante supervisor.py"}]
        self.m.t += v.REVISAR_EXTERNA_CADA_S
        self.assertEqual(self.vig.paso(), "arrancado", "el propio vigilante no cuenta como otro lanzador")

    def test_05b_con_otro_lanzador_vivo_consulta_los_procesos_solo_cada_30_s_y_toma_el_relevo_si_muere(self) -> None:
        consultas = []
        self.m.filas = [{"ProcessId": 77, "ParentProcessId": 4, "CommandLine": r"python.exe D:\A\supervisor.py --servicio"}]
        original = self.vig.procesos
        self.vig.procesos = lambda: consultas.append(self.m.t) or original()
        resultados = []
        for _ in range(30):
            resultados.append(self.vig.paso())
            self.m.t += 2
        self.assertLessEqual(len(consultas), 3, "60 s de espera = como mucho 2-3 consultas, no 30")
        self.assertEqual(set(resultados), {"externa", "esperando"})
        self.assertEqual(self.m.hijos, [])
        self.m.filas = []                                  # el lanzador manual se cerró
        self.m.t += v.REVISAR_EXTERNA_CADA_S
        self.assertEqual(self.vig.paso(), "arrancado", "toma el relevo en menos de 30 s")

    def test_06_tormenta_de_salidas_deja_alerta_y_espera(self) -> None:
        resultados = []
        for _ in range(20):
            r = self.vig.paso()
            resultados.append(r)
            if r == "tormenta":
                break
            if r == "corriendo":
                self.m.hijos[-1].codigo = 1
            self.m.t += 70
        self.assertIn("tormenta", resultados)
        self.assertEqual(len(self.m.hijos), v.MAX_ARRANQUES)
        self.assertTrue((self.carpeta / "ALERTA_ANKA.txt").exists())
        self.m.t += v.VENTANA_S + v.ESPERA_TORMENTA_S
        self.assertEqual(self.vig.paso(), "arrancado", "pasada la ventana vuelve a intentarlo")

    def test_07_salida_con_codigo_2_no_reinicia_enseguida_y_avisa(self) -> None:
        self.vig.paso()
        self.m.hijos[0].codigo = 2
        self.vig.paso()
        self.m.t += 120
        self.assertEqual(self.vig.paso(), "esperando")
        self.assertTrue((self.carpeta / "ALERTA_ANKA.txt").exists())


class DetencionTest(Base):
    def test_10_la_bandera_detiene_y_evita_reinicios(self) -> None:
        self.vig.paso()
        (self.carpeta / "DETENIDO.flag").write_text("x")
        self.assertEqual(self.vig.paso(), "detenido")
        self.assertEqual(self.m.matados, [1000])
        for _ in range(5):
            self.m.t += 600
            self.assertEqual(self.vig.paso(), "detenido")
        self.assertEqual(len(self.m.hijos), 1, "nunca se arranca otro mientras exista la bandera")
        self.assertEqual(self.log().count("DETENIDO.flag presente"), 1)

    def test_11_retirar_la_bandera_reanuda(self) -> None:
        (self.carpeta / "DETENIDO.flag").write_text("x")
        self.vig.paso()
        v.reanudar(self.carpeta)
        self.assertEqual(self.vig.paso(), "arrancado")
        self.assertIn("se reanuda", self.log())

    def test_12_detener_crea_la_bandera_y_cierra_los_lanzadores(self) -> None:
        cerrados: list[int] = []
        filas = [{"ProcessId": 50, "ParentProcessId": 4, "CommandLine": r"python.exe D:\A\supervisor.py --servicio"},
                 {"ProcessId": 51, "ParentProcessId": 50, "CommandLine": "python.exe app.py review-ui"}]
        texto = v.detener(self.carpeta, lambda: filas, cerrados.append)
        self.assertTrue((self.carpeta / "DETENIDO.flag").exists())
        self.assertEqual(cerrados, [50])
        self.assertIn("[50]", texto)

    def test_13_estado_resume_sin_escribir(self) -> None:
        r = v.estado(self.carpeta, lambda: [])
        self.assertEqual((r["lanzadores"], r["detenida_a_proposito"], r["alerta"]), ([], False, False))


class PiezasTest(unittest.TestCase):
    def test_20_clasificacion_de_salidas(self) -> None:
        self.assertIn("cerrada desde fuera", v.clasificar_salida(-1073741510, 5))
        self.assertIn("cerrada desde fuera", v.clasificar_salida(0xC000013A, 5))
        self.assertIn("normal", v.clasificar_salida(0, 5))
        self.assertIn("no reiniciarse", v.clasificar_salida(2, 5))
        self.assertIn("error no capturado", v.clasificar_salida(1, 5))
        self.assertIn("propósito", v.clasificar_salida(1, 5, True))
        self.assertIn("0xC0000005", v.clasificar_salida(-1073741819, 5))
        self.assertIn("sin código", v.clasificar_salida(None, 5))

    def test_20b_solo_el_lanzador_real_cuenta_y_un_texto_que_menciona_su_nombre_no(self) -> None:
        reales = [r"C:\x\.venv\Scripts\python.exe supervisor.py", r'"C:\x\python.exe" "D:\A\supervisor.py" --servicio',
                  r"C:\x\python.exe D:\A\supervisor.py --servicio"]
        ajenos = [r'python.exe -c "print(1)  # supervisor.py"', "python.exe -m ruff check supervisor.py", r"python.exe app.py review-ui",
                  r"powershell.exe -Command Get-Content supervisor.py", r"python.exe D:\A\no_es_supervisor.py", "", "python.exe 'sin cerrar"]
        for linea in reales:
            self.assertEqual(v.instancias_externas([{"ProcessId": 9, "CommandLine": linea}], set()), [9], linea)
        for linea in ajenos:
            self.assertEqual(v.instancias_externas([{"ProcessId": 9, "CommandLine": linea}], set()), [], linea)
        self.assertEqual(v.instancias_externas([{"ProcessId": 9, "CommandLine": reales[0]}], {9}), [])

    def test_21_el_candado_deja_una_sola_instancia(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            primero = v.bloqueo_unico(Path(tmp) / "v.lock")
            self.assertIsNotNone(primero)
            self.assertIsNone(v.bloqueo_unico(Path(tmp) / "v.lock"))
            primero.close()
            otra = v.bloqueo_unico(Path(tmp) / "v.lock")
            self.assertIsNotNone(otra)
            otra.close()

    def test_22_ejecutar_sale_en_0_si_ya_hay_un_vigilante(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            primero = v.bloqueo_unico(Path(tmp) / "vigilante.lock")
            self.assertEqual(v.ejecutar(Path(tmp)), 0)
            primero.close()

    def test_23_el_registro_rota_al_pasar_el_tope(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            ruta = Path(tmp) / "l.log"
            ruta.write_text("x" * (v.TOPE_LOG + 1))
            v.Registro(ruta).escribir("INFO", "hola")
            self.assertTrue(ruta.with_name("l.log.1").exists())
            self.assertIn("hola", ruta.read_text())

    def test_24_la_actividad_previa_se_lee_en_solo_lectura(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(v.actividad_de_anka(Path(tmp)), {})

    def test_25_no_importa_anka_ni_envia_nada(self) -> None:
        texto = Path(v.__file__).read_text(encoding="utf-8")
        for prohibido in ("from anka", "import anka", "send_message", "messages().send"):
            self.assertNotIn(prohibido, texto)


if __name__ == "__main__":
    unittest.main()
