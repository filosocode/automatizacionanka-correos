"""Pruebas de la auditoría de cancelaciones (T-93A) con bases en memoria: cero, autorizada, sin diario, inconsistente, extra y publicada."""

from __future__ import annotations

import json
import sqlite3
import unittest
from typing import Any

from laboratorio.banco_documental.respaldo_t83 import auditoria_cancelaciones as aud

T91 = {"job_id": "ANKAJOB-765f88a6cd32d661", "thread_id": "T91", "message_id": "M91", "etiqueta": "[PI-C1-T89-001]",
       "operador": "OPERADOR", "motivo": "Adjunto diferente", "cancelado_at": "2026-10-22T03:36:17-05:00", "estado_previo": "sin_trabajo"}
DIARIO_T91 = {k: T91[k] for k in ("job_id", "thread_id", "message_id", "etiqueta", "operador", "motivo", "cancelado_at")}


class Base(unittest.TestCase):
    def setUp(self) -> None:
        self.db = sqlite3.connect(":memory:")
        self.db.executescript("""CREATE TABLE trabajos_work(job_id TEXT, thread_id TEXT, message_id TEXT, state TEXT, sheet_file_id TEXT);
            CREATE TABLE recepcion_eventos(thread_id TEXT, message_id TEXT, event_kind TEXT, occurred_at TEXT, detail_json TEXT);""")
        self.db.execute("INSERT INTO trabajos_work VALUES('ANKAJOB-real1','TR','MR','conciliado','hoja-real')")

    def cancelar(self, base: dict[str, Any] = T91, con_evento: bool = True, hoja: str = "") -> None:
        self.db.execute("INSERT INTO trabajos_work VALUES(?,?,?,'cancelado',?)", (base["job_id"], base["thread_id"], base["message_id"], hoja))
        if con_evento:
            detalle = {k: base[k] for k in ("job_id", "etiqueta", "operador", "motivo")}
            self.db.execute("INSERT INTO recepcion_eventos VALUES(?,?,'trabajo_cancelado',?,?)",
                            (base["thread_id"], base["message_id"], base["cancelado_at"], json.dumps(detalle)))

    def evento(self, tipo: str, hilo: str = "T91", mensaje: str = "M91") -> None:
        self.db.execute("INSERT INTO recepcion_eventos VALUES(?,?,?,'2026-10-22T04:00:00-05:00','{}')", (hilo, mensaje, tipo))

    def auditar(self, diario: list[dict[str, Any]] | None = None, rotas: int = 0, autorizadas: list[dict[str, Any]] | None = None,
                **kwargs: Any) -> dict[str, Any]:
        return aud.auditar(self.db, [DIARIO_T91] if diario is None else diario, rotas, [T91] if autorizadas is None else autorizadas, **kwargs)

    def texto(self, r: dict[str, Any]) -> str:
        return " | ".join(r["problemas"])


class EscenariosTest(Base):
    def test_01_cero_cancelaciones_sin_expectativa_es_correcto(self) -> None:
        r = self.auditar(diario=[], autorizadas=[])
        self.assertEqual((r["ok"], r["cancelados"]), (True, 0))

    def test_02_cero_cancelaciones_cuando_se_espera_la_de_t91b_falla(self) -> None:
        r = self.auditar(diario=[])
        self.assertFalse(r["ok"])
        self.assertIn("falta la cancelación autorizada", self.texto(r))
        self.assertTrue(self.auditar(diario=[], exigir_presentes=False)["ok"])

    def test_03_una_cancelacion_autorizada_y_consistente_es_correcta(self) -> None:
        self.cancelar()
        r = self.auditar()
        self.assertEqual((r["ok"], r["cancelados"], r["problemas"]), (True, 1, []))

    def test_04_cancelacion_sin_diario_falla(self) -> None:
        self.cancelar()
        r = self.auditar(diario=[])
        self.assertFalse(r["ok"])
        self.assertIn("sin entrada en el diario", self.texto(r))

    def test_05_diario_inconsistente_falla(self) -> None:
        self.cancelar()
        casos = {"otro mensaje": {**DIARIO_T91, "message_id": "OTRO"}, "otro operador": {**DIARIO_T91, "operador": "X"},
                 "otra etiqueta": {**DIARIO_T91, "etiqueta": "[PI-X-1]"}}
        for nombre, entrada in casos.items():
            self.assertFalse(self.auditar(diario=[entrada])["ok"], nombre)
        self.assertIn("ilegible", self.texto(self.auditar(rotas=1)))
        self.assertIn("repetida", self.texto(self.auditar(diario=[DIARIO_T91, DIARIO_T91])))
        extra = {**DIARIO_T91, "job_id": "ANKAJOB-fantasma"}
        self.assertIn("no hay trabajo cancelado", self.texto(self.auditar(diario=[DIARIO_T91, extra])))

    def test_06_cancelacion_adicional_no_autorizada_falla(self) -> None:
        self.cancelar()
        otra = {**T91, "job_id": "ANKAJOB-otra", "thread_id": "T2", "message_id": "M2"}
        self.cancelar(otra)
        r = self.auditar(diario=[DIARIO_T91, {**DIARIO_T91, "job_id": "ANKAJOB-otra", "thread_id": "T2", "message_id": "M2"}])
        self.assertFalse(r["ok"])
        self.assertIn("ANKAJOB-otra: cancelación NO autorizada", self.texto(r))
        self.assertEqual(r["cancelados"], 2)

    def test_07_cancelacion_sin_evento_falla(self) -> None:
        self.cancelar(con_evento=False)
        self.assertIn("sin evento trabajo_cancelado", self.texto(self.auditar()))

    def test_08_cancelado_con_publicacion_posterior_falla(self) -> None:
        self.cancelar(hoja="hoja-xyz")
        self.assertIn("guarda una hoja de Drive", self.texto(self.auditar()))

    def test_09_cancelado_con_eventos_de_publicacion_falla(self) -> None:
        for tipo in aud.EVENTOS_DE_PUBLICACION:
            self.setUp()
            self.cancelar()
            self.evento(tipo)
            self.assertIn(tipo, self.texto(self.auditar()), tipo)

    def test_10_cancelado_sin_trabajo_previo_con_evento_de_creacion_falla(self) -> None:
        self.cancelar()
        self.evento("trabajo_creado")
        self.assertIn("trabajo_creado", self.texto(self.auditar()))

    def test_11_cancelado_con_hoja_en_drive_falla_y_un_error_de_drive_no_se_ignora(self) -> None:
        self.cancelar()
        self.assertTrue(self.auditar(hojas_de=lambda _j: [])["ok"])
        self.assertIn("1 hoja(s)", self.texto(self.auditar(hojas_de=lambda _j: [{"id": "h"}])))

        def falla(_j: str) -> list[Any]:
            raise TimeoutError("sin red")

        self.assertIn("no se pudo comprobar Drive", self.texto(self.auditar(hojas_de=falla)))

    def test_12_un_trabajo_real_conciliado_no_cuenta_como_cancelacion(self) -> None:
        self.cancelar()
        self.evento("trabajo_publicado", hilo="TR", mensaje="MR")           # la publicación de un real no afecta a T-91B
        self.assertTrue(self.auditar()["ok"])

    def test_13_el_registro_real_describe_la_cancelacion_de_t91b(self) -> None:
        registro = json.loads(aud.REGISTRO.read_text(encoding="utf-8"))
        self.assertEqual([r["job_id"] for r in registro], ["ANKAJOB-765f88a6cd32d661"])
        self.assertEqual((registro[0]["thread_id"], registro[0]["message_id"], registro[0]["etiqueta"]),
                         ("1a162bea8208dce5", "1a162bea8208dce5", "[PI-C1-T89-001]"))


if __name__ == "__main__":
    unittest.main()
