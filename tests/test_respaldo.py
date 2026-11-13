"""U-112.C: cambiar el estado de una actividad exige un respaldo (cliente, documento o criterio técnico) y deja rastro."""

from tests import _isolate  # noqa: F401  (debe ir primero)

import unittest
from datetime import date, timedelta
from pathlib import Path

from anka.dominio.errores import ErrorDeValidacion
from anka.dominio.respaldo import cambios_de_estado, describir_respaldo, exige_respaldo, normalizar_respaldo
from anka.dominio.status import QuoteStatus
from anka.infra import store
from anka.servicios import ediciones, quotes
from tests.test_app import complete_analysis
from tests.test_errores_y_proteccion import Base

HOY = date(2026, 11, 12)


def fila(nombre: str, estado: str, **mas: str) -> dict:
    return {"activity": nombre, "quantity": "", "unit": "", "estado": estado, **mas}


CLIENTE = {"origen": "cliente", "medio": "llamada", "fecha": "2026-11-10", "nota": "Confirmó el embalse La Esmeralda"}


# Cubre: U-112
class QueCambiosPidenRespaldoTests(unittest.TestCase):
    def test_confirmar_reabrir_agregar_y_quitar_lo_piden(self) -> None:
        antes = [fila("Batimetría", "pendiente"), fila("Ortofoto", "confirmado"), fila("Perfil", "pendiente")]
        despues = [fila("Batimetría", "confirmado"), fila("Ortofoto", "pendiente"), fila("Control", "pendiente")]
        self.assertEqual(cambios_de_estado(antes, despues), {
            "confirmadas": ["Batimetría"], "reabiertas": ["Ortofoto"], "agregadas": ["Control"], "quitadas": ["Perfil"]})
        self.assertTrue(exige_respaldo(cambios_de_estado(antes, despues)))

    def test_las_mayusculas_del_nombre_no_crean_un_cambio(self) -> None:
        cambios = cambios_de_estado([fila("Batimetría", "pendiente")], [fila("BATIMETRÍA", "pendiente")])
        self.assertFalse(exige_respaldo(cambios))

    def test_corregir_un_dato_o_pasar_entre_pendiente_y_contradictorio_no_lo_pide(self) -> None:
        antes = [fila("Batimetría", "pendiente", quantity="2"), fila("Ortofoto", "confirmado")]
        despues = [fila("Batimetría", "contradictorio", quantity="3"), fila("Ortofoto", "confirmado", quantity="9")]
        self.assertFalse(exige_respaldo(cambios_de_estado(antes, despues)))


# Cubre: U-112
class QueSePideTests(unittest.TestCase):
    def rechaza(self, datos: dict | None, frase: str) -> None:
        with self.assertRaises(ErrorDeValidacion) as error:
            normalizar_respaldo(datos, HOY)
        self.assertIn(frase, str(error.exception))

    def test_sin_origen_o_con_uno_desconocido(self) -> None:
        self.rechaza(None, "Elige de dónde viene")
        self.rechaza({"origen": "supuesto", "nota": "Lo supongo por su proyecto anterior"}, "Elige de dónde viene")

    def test_del_cliente_pide_medio_y_un_dia_que_no_sea_futuro(self) -> None:
        self.rechaza({**CLIENTE, "medio": ""}, "por qué medio")
        self.rechaza({**CLIENTE, "medio": "telepatía"}, "por qué medio")
        self.rechaza({**CLIENTE, "fecha": ""}, "el día")
        self.rechaza({**CLIENTE, "fecha": "ayer"}, "el día")
        self.rechaza({**CLIENTE, "fecha": (HOY + timedelta(days=1)).isoformat()}, "no puede ser futuro")

    def test_de_un_documento_pide_cual(self) -> None:
        self.rechaza({"origen": "documento", "nota": "La página 2 del alcance lo indica"}, "cuál documento")

    def test_en_los_tres_casos_pide_una_frase_de_al_menos_diez_letras(self) -> None:
        for origen in ("cliente", "documento", "tecnico"):
            self.rechaza({**CLIENTE, "origen": origen, "documento": "alcance.pdf", "nota": "  ok  "}, "mínimo 10 letras")

    def test_lo_valido_queda_normalizado(self) -> None:
        r = normalizar_respaldo({**CLIENTE, "medio": " LLAMADA ", "nota": "  Confirmó   el   embalse  La Esmeralda "},
                                HOY, "Andrés")
        self.assertEqual(r, {"origen": "cliente", "medio": "Llamada", "fecha": "2026-11-10",
                             "nota": "Confirmó el embalse La Esmeralda", "quien": "Andrés"})

    def test_el_criterio_tecnico_no_pide_medio_ni_documento(self) -> None:
        r = normalizar_respaldo({"origen": "tecnico", "nota": "Con ese relieve se requiere GNSS RTK"}, HOY, "Andrés")
        self.assertEqual(set(r), {"origen", "nota", "quien"})

    def test_la_linea_que_se_ve_bajo_la_actividad(self) -> None:
        self.assertEqual(describir_respaldo(normalizar_respaldo(CLIENTE, HOY)),
                         "Lo confirmó el cliente por llamada, el 10 nov")
        self.assertEqual(describir_respaldo({"origen": "documento", "documento": "alcance.pdf", "nota": "x"}),
                         "Según alcance.pdf")
        self.assertEqual(describir_respaldo({"origen": "tecnico", "nota": "x", "quien": "Andrés"}),
                         "Por la experiencia de Andrés · se lo presentamos como recomendación")


class ConTabla(Base):
    def setUp(self) -> None:
        super().setUp()
        analisis = complete_analysis()
        analisis["activities"] = [fila("Batimetría", "pendiente"), fila("Ortofoto", "confirmado", quantity="120"),
                                  fila("Perfil", "pendiente")]
        db = store.open_db(Path(self.temp.name))
        store.save(db, "t-1", "m-1", QuoteStatus.READY_REVIEW, "ana@acme.com", analisis)
        db.commit()
        db.close()

    def tabla(self) -> list[dict]:
        return quotes.get_quote("t-1")["analysis"]["activities"]

    def respaldos(self) -> dict:
        return quotes.get_quote("t-1")["analysis"].get("respaldos") or {}

    def editar(self, cambio, **kw) -> str:
        nueva = [dict(f) for f in self.tabla()]
        cambio(nueva)
        return ediciones.save_activities("t-1", nueva, exigir_respaldo=True, **kw)

    def versiones(self) -> int:
        return len(ediciones.table_versions("t-1"))


# Cubre: U-112
class GuardarConRespaldoTests(ConTabla):
    def confirmar_batimetria(self, nueva: list[dict]) -> None:
        nueva[0]["estado"] = "confirmado"
        nueva[0]["quantity"] = "2"

    def test_sin_respaldo_no_se_guarda_nada(self) -> None:
        antes, versiones = self.tabla(), self.versiones()
        with self.assertRaises(ErrorDeValidacion) as error:
            self.editar(self.confirmar_batimetria)
        self.assertIn("Elige de dónde viene", str(error.exception))
        self.assertEqual((self.tabla(), self.versiones()), (antes, versiones))

    def test_con_un_respaldo_incompleto_tampoco(self) -> None:
        with self.assertRaises(ErrorDeValidacion):
            self.editar(self.confirmar_batimetria, respaldo={"origen": "cliente", "nota": "Lo dijo por teléfono"})
        self.assertEqual(self.tabla()[0]["estado"], "pendiente")

    def test_confirmar_con_respaldo_guarda_el_estado_el_rastro_y_el_motivo(self) -> None:
        self.editar(self.confirmar_batimetria, respaldo=CLIENTE, quien="Andrés")
        self.assertEqual(self.tabla()[0]["estado"], "confirmado")
        guardado = self.respaldos()["batimetría"]
        self.assertEqual((guardado["origen"], guardado["medio"], guardado["tipo"], guardado["quien"]),
                         ("cliente", "Llamada", "confirmadas", "Andrés"))
        self.assertEqual(ediciones.table_versions("t-1")[-1]["reason"], "cliente")
        evento = [e for e in quotes.thread_events("t-1") if e["kind"] == "table_edit"][0]
        self.assertIn("Respaldo: Lo confirmó el cliente por llamada, el 10 nov — «Confirmó el embalse La Esmeralda»",
                      evento["summary"])
        self.assertEqual(evento["actor_name"], "Andrés")

    def test_el_criterio_tecnico_y_el_documento_tienen_su_propio_motivo(self) -> None:
        self.editar(self.confirmar_batimetria, respaldo={"origen": "tecnico", "nota": "Con ese relieve se requiere RTK"},
                    quien="Andrés")
        self.assertEqual(ediciones.table_versions("t-1")[-1]["reason"], "tecnico")
        self.assertEqual(self.respaldos()["batimetría"]["origen"], "tecnico")

        def confirmar_perfil(nueva: list[dict]) -> None:
            nueva[2]["estado"] = "confirmado"
        self.editar(confirmar_perfil, respaldo={"origen": "documento", "documento": "alcance.pdf",
                                                "nota": "Página 2: perfil de 3 km"})
        self.assertEqual(ediciones.table_versions("t-1")[-1]["reason"], "archivo")

    def test_reabrir_agregar_y_quitar_tambien_lo_piden(self) -> None:
        def reabrir(nueva: list[dict]) -> None:
            nueva[1]["estado"] = "pendiente"

        def agregar(nueva: list[dict]) -> None:
            nueva.append(fila("Control GNSS", "pendiente"))

        def quitar(nueva: list[dict]) -> None:
            del nueva[2]
        versiones = self.versiones()
        for cambio in (reabrir, agregar, quitar):
            with self.assertRaises(ErrorDeValidacion, msg=cambio.__name__):
                self.editar(cambio)
        self.assertEqual(self.versiones(), versiones)

    def test_quitar_con_respaldo_retira_su_rastro_pero_queda_en_el_historial(self) -> None:
        self.editar(self.confirmar_batimetria, respaldo=CLIENTE)
        self.editar(lambda nueva: nueva.pop(0), respaldo={"origen": "cliente", "medio": "Correo",
                                                         "fecha": "2026-11-11", "nota": "Pidió quitar la batimetría"})
        self.assertNotIn("batimetría", self.respaldos())
        resumenes = [e["summary"] for e in quotes.thread_events("t-1") if e["kind"] == "table_edit"]
        self.assertTrue(any("Pidió quitar la batimetría" in r for r in resumenes))
        self.assertTrue(any("Confirmó el embalse" in r for r in resumenes))

    def test_corregir_un_dato_sin_cambiar_el_estado_no_pide_respaldo(self) -> None:
        def corregir(nueva: list[dict]) -> None:
            nueva[1]["quantity"] = "125"
        self.editar(corregir)
        self.assertEqual(self.tabla()[1]["quantity"], "125")
        self.assertEqual(self.respaldos(), {})

    def test_el_editor_clasico_sigue_sin_exigirlo_pero_valida_el_que_llegue(self) -> None:
        nueva = [dict(f) for f in self.tabla()]
        nueva[0]["estado"] = "confirmado"
        ediciones.save_activities("t-1", nueva)
        self.assertEqual(self.tabla()[0]["estado"], "confirmado")
        otra = [dict(f) for f in self.tabla()]
        otra[2]["estado"] = "confirmado"
        with self.assertRaises(ErrorDeValidacion):
            ediciones.save_activities("t-1", otra, respaldo={"origen": "cliente", "nota": "corto"})
        self.assertEqual(self.tabla()[2]["estado"], "pendiente")

    def test_el_rastro_sobrevive_a_un_reproceso_del_modelo(self) -> None:
        self.editar(self.confirmar_batimetria, respaldo=CLIENTE)
        analisis = complete_analysis()
        analisis["activities"] = [fila("Batimetría", "pendiente")]
        db = store.open_db(Path(self.temp.name))
        store.save(db, "t-1", "m-2", QuoteStatus.READY_REVIEW, "ana@acme.com", analisis)
        db.commit()
        db.close()
        self.assertIn("batimetría", self.respaldos())
        self.assertEqual(self.tabla()[0]["estado"], "confirmado")


if __name__ == "__main__":
    unittest.main()
