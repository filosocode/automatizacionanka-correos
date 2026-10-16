"""Lo que se decide por error se puede deshacer y borrar un caso con T o correo enviado pide escribir ELIMINAR (U-57)."""

from tests import _isolate  # noqa: F401  (debe ir primero)

import json
import unittest
from unittest.mock import patch

from anka.infra.store import verify_chain
from anka.servicios import case_actions, change_control, ediciones, quotes, work_import
from anka.servicios.quotes import connect
from anka.web import acciones, paginas, pantalla_caso
from tests.js_de_las_paginas import JS
from tests.test_change_control import CONFIG, Base, coded_mail
from tests.test_work_sync import b64, payload


def eventos(clase):
    return [e for e in quotes.thread_events("T1") if e["kind"] == clase]


def pagina_del_caso():
    with patch("anka.web.paginas.load_config", return_value=CONFIG), \
            patch("anka.web.pantalla_caso.load_config", return_value=CONFIG):
        return pantalla_caso.render_case("T1")


class ReabrirNoEsUnCambio(Base):
    def setUp(self):
        super().setUp()
        self.assign()
        self.sync({"m1": coded_mail("T1", "Luis <luis@acme.com>", self.t0 + 7200, "Cambiar plazo a 30")})
        self.cambio = self.changes()[0]["id"]

    def descartar(self):
        return acciones.POST_ROUTES["/change"]({"thread_id": ["T1"], "change_id": [str(self.cambio)],
                                                  "decision": ["no"]})

    def reabrir(self):
        return acciones.POST_ROUTES["/change"]({"thread_id": ["T1"], "change_id": [str(self.cambio)],
                                                  "decision": ["reopen"]})

    # Cubre: U-57
    def test_el_mensaje_de_exito_dice_donde_deshacerlo(self):
        self.assertIn("Historial", self.descartar())

    # Cubre: U-57
    def test_el_historial_ofrece_reabrir_y_reabrir_deja_huella(self):
        self.descartar()
        self.assertEqual([c["state"] for c in self.changes()], ["not_change"])
        pagina = pagina_del_caso()
        self.assertIn("Decisiones que puedes deshacer", pagina)
        self.assertIn('value="reopen"', pagina)
        self.reabrir()
        self.assertEqual([c["state"] for c in self.changes()], ["pending"])
        self.assertEqual(len(eventos("change_reopened")), 1)
        self.assertIn("Reabriste", eventos("change_reopened")[0]["summary"])
        self.assertNotIn("Decisiones que puedes deshacer", pagina_del_caso())
        db = connect()
        self.addCleanup(db.close)
        self.assertTrue(verify_chain(db, "T1")[0])

    # Cubre: U-57
    def test_reabrir_dos_veces_o_un_cambio_ya_confirmado_no_hace_nada(self):
        self.descartar()
        self.reabrir()
        antes = len(quotes.thread_events("T1"))
        self.reabrir()
        self.assertEqual(len(quotes.thread_events("T1")), antes)
        change_control.resolve_change("T1", self.cambio, True)
        self.assertIn("confirmado", change_control.reabrir_cambio("T1", self.cambio))
        self.assertEqual(change_control.reabrir_cambio("T1", 999), "Ese mensaje ya no está en este caso.")


class RecuperarLaPropuesta(Base):
    def setUp(self):
        super().setUp()
        ediciones.save_activities("T1", [{"activity": "mía"}])
        work_import.dashboard_upsert(b64(json.dumps(payload(message="m3", activities=[{"activity": "z"}]))))

    # Cubre: U-57
    def test_dejar_mi_tabla_se_puede_deshacer(self):
        mensaje = acciones.POST_ROUTES["/proposal"]({"thread_id": ["T1"], "decision": ["keep"]})
        self.assertIn("Historial", mensaje)
        self.assertEqual(quotes.get_quote("T1")["proposal"], [])
        self.assertIn("Recuperar la propuesta", pagina_del_caso())
        acciones.POST_ROUTES["/proposal"]({"thread_id": ["T1"], "decision": ["restore"]})
        caso = quotes.get_quote("T1")
        self.assertTrue(caso["proposal"])
        self.assertEqual(caso["analysis"]["activities"], [{"activity": "mía"}])      # la tabla de la persona no se toca
        self.assertNotIn("Recuperar la propuesta", pagina_del_caso())
        resumenes = [e["summary"] for e in quotes.thread_events("T1") if e["kind"] == "proposal"]
        self.assertTrue(any("Recuperaste la propuesta" in r for r in resumenes), resumenes)

    # Cubre: U-57
    def test_sin_nada_que_recuperar_o_con_una_propuesta_pendiente_no_cambia_nada(self):
        self.assertIn("Ya hay una propuesta", quotes.recuperar_propuesta("T1"))
        quotes.resolve_model_proposal("T1", use_proposal=True)
        self.assertEqual(quotes.recuperar_propuesta("T1"), "No hay ninguna propuesta que recuperar.")

    # Cubre: U-57
    def test_aceptar_la_propuesta_borra_la_guardada(self):
        quotes.resolve_model_proposal("T1", use_proposal=False)
        work_import.dashboard_upsert(b64(json.dumps(payload(message="m4", activities=[{"activity": "y"}]))))
        quotes.resolve_model_proposal("T1", use_proposal=True)
        self.assertNotIn("propuesta_descartada", quotes.get_quote("T1")["analysis"])


class BorrarConCodigoOCorreo(Base):
    # Cubre: U-57
    def test_un_caso_sin_codigo_ni_correo_se_borra_como_siempre(self):
        self.assertIn("Se borraron 1", case_actions.delete_cases(["T1"]))

    # Cubre: U-57
    def test_con_codigo_t_no_se_borra_sin_escribir_eliminar(self):
        self.assign()
        mensaje = case_actions.delete_cases(["T1"])
        self.assertIn("No se borró nada", mensaje)
        self.assertIn("ELIMINAR", mensaje)
        self.assertIsNotNone(quotes.get_quote("T1"))
        self.assertIn("No se borró nada", case_actions.delete_cases(["T1"], "SI"))
        self.assertIsNotNone(quotes.get_quote("T1"))
        self.assertIn("Se borraron 1", case_actions.delete_cases(["T1"], " eliminar "))
        self.assertIsNone(quotes.get_quote("T1"))

    # Cubre: U-57
    def test_con_correo_enviado_tambien_y_el_formulario_del_caso_vuelve_al_caso(self):
        db = connect()
        db.execute("UPDATE quotes SET sent_at='2026-10-02T10:00:00-05:00' WHERE thread_id='T1'")
        db.commit()
        db.close()
        destino = acciones.POST_ROUTES["/delete"]({"thread_id": ["T1"]})
        self.assertTrue(destino.startswith("/case?thread_id=T1"), destino)
        self.assertIsNotNone(quotes.get_quote("T1"))
        destino = acciones.POST_ROUTES["/delete"]({"thread_id": ["T1"], "confirmacion": ["ELIMINAR"]})
        self.assertTrue(destino.startswith("/requests"), destino)
        self.assertIsNone(quotes.get_quote("T1"))

    # Cubre: U-57
    def test_la_lista_pide_el_texto_solo_para_los_casos_protegidos(self):
        self.assertNotIn("data-protegido", paginas.quote_row(quotes.get_quote("T1"), selectable=True))
        self.assign()
        self.assertIn("data-protegido", paginas.quote_row(quotes.get_quote("T1"), selectable=True))
        with patch("anka.web.paginas.load_config", return_value=CONFIG):
            self.assertIn('name="confirmacion"', paginas.render_requests())

    # Cubre: U-57
    def test_el_guion_muestra_el_cuadro_solo_con_casos_protegidos(self):
        self.assertIn("#eliminar-fuerte", JS)
        self.assertIn("data-protegido", JS)


class VaciarDatosComerciales(Base):
    # Cubre: U-57
    def test_el_formulario_y_el_guion_piden_confirmar_al_vaciar_un_dato_que_ya_estaba(self):
        pagina = pagina_del_caso()
        self.assertIn("data-datos-comerciales", pagina)
        self.assertIn('data-nombre="Notas comerciales"', pagina)
        self.assertIn("Vas a dejar vacío", JS)
        self.assertIn("defaultValue", JS)


if __name__ == "__main__":
    unittest.main()
