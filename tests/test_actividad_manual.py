"""U-112.B: el panel cambia UNA actividad (resolver, reabrir, quitar, corregir, agregar) y siempre con su respaldo."""

from tests import _isolate  # noqa: F401  (debe ir primero)

import unittest
from pathlib import Path

from anka.dominio.activities import huella_de_tabla
from anka.dominio.errores import ErrorDeValidacion
from anka.dominio.status import QuoteStatus
from anka.infra import store
from anka.servicios import quotes
from anka.servicios.actividad_manual import cambiar_actividad
from tests.test_app import complete_analysis
from tests.test_errores_y_proteccion import Base

CLIENTE = {"origen": "cliente", "medio": "Llamada", "fecha": "2026-11-10", "nota": "Me lo confirmó por teléfono"}


def fila(nombre: str, estado: str, **mas: str) -> dict:
    base = {"activity": nombre, "servicio_tecnica": "", "ubicacion": "Embalse La Esmeralda", "quantity": "2",
            "unit": "km", "entregable": "Informe", "plazo_restriccion": "30 días", "estado": estado, "notes": ""}
    return base | mas


class ConActividades(Base):
    def setUp(self) -> None:
        super().setUp()
        analisis = complete_analysis()
        analisis["activities"] = [
            fila("Batimetría", "pendiente", ubicacion="no indicado", notes="Falta la ubicación."),
            fila("Ortofoto", "confirmado"),
            fila("Área", "contradictorio", notes="El correo dice 10 ha y el anexo 12 ha."),
            fila("Perfil", "pendiente", plazo_restriccion="")]
        db = store.open_db(Path(self.temp.name))
        store.save(db, "t-1", "m-1", QuoteStatus.READY_REVIEW, "ana@acme.com", analisis)
        db.commit()
        db.close()

    def tabla(self) -> list[dict]:
        return quotes.get_quote("t-1")["analysis"]["activities"]

    def respaldos(self) -> dict:
        return quotes.get_quote("t-1")["analysis"].get("respaldos") or {}

    def rechaza(self, accion: str, indice: int, datos: dict, frase: str) -> None:
        antes = self.tabla()
        with self.assertRaises(ErrorDeValidacion) as error:
            cambiar_actividad("t-1", accion, indice, datos)
        self.assertIn(frase, str(error.exception))
        self.assertEqual(self.tabla(), antes)


# Cubre: U-112
class ResolverTests(ConActividades):
    def test_pide_el_dato_que_faltaba_antes_del_respaldo(self) -> None:
        self.rechaza("resolver", 0, CLIENTE, "Escribe la ubicación")

    def test_con_el_dato_y_el_respaldo_queda_lista_con_su_rastro_en_las_notas(self) -> None:
        mensaje = cambiar_actividad("t-1", "resolver", 0, {**CLIENTE, "dato_ubicacion": "Embalse La Esmeralda, Cundinamarca"})
        self.assertEqual(mensaje, "Listo: «Batimetría» quedó resuelta.")
        resuelta = self.tabla()[0]
        self.assertEqual((resuelta["estado"], resuelta["ubicacion"]), ("confirmado", "Embalse La Esmeralda, Cundinamarca"))
        self.assertIn("Respaldo: Lo confirmó el cliente por llamada, el 10 nov — «Me lo confirmó por teléfono»",
                      resuelta["notes"])
        self.assertEqual(self.respaldos()["batimetría"]["origen"], "cliente")

    def test_sin_respaldo_no_se_guarda_aunque_el_dato_este(self) -> None:
        self.rechaza("resolver", 0, {"dato_ubicacion": "Embalse X"}, "Elige de dónde viene")

    def test_una_contradictoria_pide_que_quedo_decidido(self) -> None:
        self.rechaza("resolver", 2, CLIENTE, "qué quedó decidido")
        cambiar_actividad("t-1", "resolver", 2, {**CLIENTE, "decision": "El área es de 12 ha"})
        area = self.tabla()[2]
        self.assertEqual(area["estado"], "confirmado")
        self.assertIn("Decisión: El área es de 12 ha.", area["notes"])

    def test_el_criterio_tecnico_se_registra_como_recomendacion(self) -> None:
        cambiar_actividad("t-1", "resolver", 2, {"origen": "tecnico", "decision": "Se mide con GNSS RTK",
                                                 "nota": "Con ese relieve la precisión pedida lo exige"}, quien="Andrés")
        self.assertIn("Por la experiencia de Andrés · se lo presentamos como recomendación", self.tabla()[2]["notes"])

    def test_no_resuelve_lo_que_ya_esta_listo(self) -> None:
        self.rechaza("resolver", 1, CLIENTE, "ya está lista")


# Cubre: U-112
class ReabrirQuitarCorregirTests(ConActividades):
    def test_reabrir_pide_respaldo_y_deja_la_razon_en_la_nota(self) -> None:
        self.rechaza("reabrir", 1, {}, "Elige de dónde viene")
        cambiar_actividad("t-1", "reabrir", 1, {**CLIENTE, "nota": "El cliente cambió la ubicación"})
        ortofoto = self.tabla()[1]
        self.assertEqual(ortofoto["estado"], "pendiente")
        self.assertIn("Falta: El cliente cambió la ubicación.", ortofoto["notes"])

    def test_no_reabre_lo_que_ya_falta(self) -> None:
        self.rechaza("reabrir", 0, CLIENTE, "ya falta algo")

    def test_quitar_pide_respaldo_y_la_quita(self) -> None:
        self.rechaza("quitar", 3, {}, "Elige de dónde viene")
        cambiar_actividad("t-1", "quitar", 3, {**CLIENTE, "nota": "El cliente pidió quitar el perfil"})
        self.assertEqual([f["activity"] for f in self.tabla()], ["Batimetría", "Ortofoto", "Área"])

    def test_corregir_un_dato_no_cambia_el_estado_ni_pide_respaldo(self) -> None:
        cambiar_actividad("t-1", "corregir", 1, {"campo_quantity": "3", "campo_unit": "km"})
        ortofoto = self.tabla()[1]
        self.assertEqual((ortofoto["quantity"], ortofoto["estado"]), ("3", "confirmado"))
        self.assertEqual(self.respaldos(), {})

    def test_corregir_sin_cambios_lo_dice(self) -> None:
        self.assertIn("No hay cambios", cambiar_actividad("t-1", "corregir", 1, {"campo_quantity": "2"}))


# Cubre: U-112
class AgregarTests(ConActividades):
    def test_pide_nombre_y_no_repite_uno_existente(self) -> None:
        self.rechaza("agregar", -1, CLIENTE, "cómo se llama")
        self.rechaza("agregar", -1, {**CLIENTE, "nombre": " ortofoto "}, "Ya hay una actividad")

    def test_pide_respaldo(self) -> None:
        self.rechaza("agregar", -1, {"nombre": "Control GNSS"}, "Elige de dónde viene")

    def test_sin_datos_esenciales_queda_por_completar_y_con_todos_queda_lista(self) -> None:
        cambiar_actividad("t-1", "agregar", -1, {**CLIENTE, "nombre": "Control GNSS"})
        self.assertEqual(self.tabla()[-1]["estado"], "pendiente")
        cambiar_actividad("t-1", "agregar", -1, {**CLIENTE, "nombre": "Nivelación", "dato_ubicacion": "Lote 4",
                                                 "dato_quantity": "5", "dato_unit": "km", "dato_entregable": "Plano",
                                                 "dato_plazo_restriccion": "10 días"})
        nueva = self.tabla()[-1]
        self.assertEqual((nueva["estado"], nueva["ubicacion"]), ("confirmado", "Lote 4"))
        self.assertIn("Agregada: Lo confirmó el cliente por llamada", nueva["notes"])


# Cubre: U-112
class SeguridadTests(ConActividades):
    def test_una_accion_desconocida_o_una_fila_que_no_existe(self) -> None:
        self.rechaza("borrar_todo", 0, CLIENTE, "No entendimos")
        self.rechaza("resolver", 9, CLIENTE, "ya no está en la tabla")
        self.rechaza("resolver", -1, CLIENTE, "ya no está en la tabla")

    def test_si_la_tabla_cambio_desde_que_se_abrio_no_se_guarda(self) -> None:
        vieja = huella_de_tabla(self.tabla())
        cambiar_actividad("t-1", "corregir", 1, {"campo_quantity": "3"})
        antes = self.tabla()
        with self.assertRaises(ErrorDeValidacion) as error:
            cambiar_actividad("t-1", "resolver", 0, {**CLIENTE, "dato_ubicacion": "Embalse X"}, vieja)
        self.assertIn("La tabla cambió", str(error.exception))
        self.assertEqual(self.tabla(), antes)

    def test_un_caso_que_no_existe(self) -> None:
        with self.assertRaises(ErrorDeValidacion):
            cambiar_actividad("no-hay", "resolver", 0, CLIENTE)


if __name__ == "__main__":
    unittest.main()
