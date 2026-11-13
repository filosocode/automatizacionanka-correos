"""T-61: el panel «Work propone cambios a tu tabla» explica cada cambio (campo, valor vigente, valor propuesto, motivo y
respaldo) con lo que ya guarda el caso (`revision_de_tabla`), sin cambiar cómo se acepta o se deja de lado.

SQLite temporal y el manejador real del servidor; no se inicia nada ni se toca Gmail.

Cubre: T-61
"""

from tests import _isolate  # noqa: F401  (debe ir primero)

import copy
import json
import re
import unittest
from unittest.mock import patch

from anka.dominio.caso import explicar_propuesta
from anka.infra import store
from anka.servicios.revision_tecnica import resolve_model_proposal
from tests.test_app import test_config
from tests.test_continuidad_de_casos import BATIMETRIA, Contexto, fila
from tests.test_openapi import request

CORREO = "1a155f5531f151d3"
RESPALDO = f"Gmail {CORREO}, párrafo 1 (área nueva)"


def area(cantidad, estado="pendiente", **extra):
    return fila("Levantamiento planimétrico", cantidad, "ha", estado, entregable=extra.pop("entregable", "Curvas de nivel"),
                **extra)


class PanelBase(Contexto):
    """El caso t-1 con un levantamiento de «aproximadamente 6 hectáreas», como el expediente de laboratorio."""

    def setUp(self):
        super().setUp()
        with store.transaccion() as db:
            analisis = json.loads(db.execute("SELECT analysis_json FROM quotes WHERE thread_id='t-1'").fetchone()[0])
            analisis["activities"] = [area("aproximadamente 6 hectáreas", "confirmado"), copy.deepcopy(BATIMETRIA)]
            db.execute("UPDATE quotes SET analysis_json=? WHERE thread_id='t-1'", (json.dumps(analisis),))

    def pagina(self):
        with patch("anka.web.paginas.load_config", return_value=test_config()),                 patch("anka.web.pantalla_caso.load_config", return_value=test_config()):
            respuesta = request("GET", "/case?thread_id=t-1")
        self.assertEqual(respuesta["status"], 200)
        return respuesta["body"].decode("utf-8")

    def panel(self):
        html = self.pagina()
        inicio = html.index('<div class="proposal" id="proposal">')
        return html[inicio:html.index("</form>", inicio)]

    def texto(self, html):
        return " ".join(re.sub(r"<[^>]+>", " ", html).split())

    def proponer(self, filas, mensaje="m-2", **extra):
        return self.seguimiento(filas, mensaje=mensaje, **extra)


class Magnitud(PanelBase):
    def test_el_cambio_de_area_muestra_el_valor_vigente_el_propuesto_el_motivo_y_el_correo(self):
        self.proponer([area("6,5 hectáreas", "contradictorio", fuente=RESPALDO, entregable="Plano PDF y DWG")])
        panel = self.texto(self.panel())
        self.assertIn("Work propone 1 cambio(s) a tu tabla", panel)
        self.assertIn("Cantidad hoy aproximadamente 6 hectáreas", panel)
        self.assertIn("Work propone 6,5 hectáreas", panel)
        self.assertIn("Dato distinto", panel)
        self.assertIn("otro valor de cantidad", panel)
        self.assertIn(f"Respaldo: {RESPALDO}", panel)
        self.assertIn(f"https://mail.google.com/mail/u/0/#all/{CORREO}", self.panel())
        self.assertIn("dato_distinto · quantity", panel)           # el dato original sigue a la mano

    def test_el_cambio_de_entregable_y_la_contradiccion_se_muestran_aparte(self):
        self.proponer([area("6,5 hectáreas", "contradictorio", fuente=RESPALDO, entregable="Plano PDF y DWG")])
        panel = self.texto(self.panel())
        self.assertIn("Entregable hoy Curvas de nivel", panel)
        self.assertIn("Work propone Plano PDF y DWG", panel)
        self.assertIn("Contradicción", panel)
        self.assertIn("se contradice con lo que ya constaba", panel)
        self.assertEqual(self.panel().count('class="cambio '), 3)

    def test_la_vista_no_cambia_nada_del_caso(self):
        self.proponer([area("6,5 hectáreas", "contradictorio", fuente=RESPALDO)])

        def estado():
            with store.transaccion() as db:
                return (db.execute("SELECT analysis_json, status FROM quotes WHERE thread_id='t-1'").fetchone()[:],
                        db.execute("SELECT COUNT(*) FROM case_events").fetchone()[0],
                        db.execute("SELECT COUNT(*) FROM table_versions").fetchone()[0])
        antes = estado()
        self.pagina()
        self.pagina()
        self.assertEqual(estado(), antes)


class OtrosMotivos(PanelBase):
    def test_una_eliminacion_propuesta_se_explica_y_dice_que_aceptar_la_quita(self):
        self.proponer([fila("Batimetría", servicio="Batimetría", ubicacion="Embalse",
                            notes="El cliente indica que ya no se requiere", fuente=RESPALDO)])
        panel = self.texto(self.panel())
        self.assertIn("Eliminación propuesta", panel)
        self.assertIn("«Batimetría»", panel)
        self.assertIn("Aceptar la propuesta la saca de la tabla", panel)

    def test_varios_cambios_se_agrupan_por_actividad(self):
        self.proponer([area("7 hectáreas", "confirmado", fuente=RESPALDO, entregable="Plano"),
                       fila("Fotogrametría", "3", "ha", "confirmado", servicio="Fotogrametría", fuente="correo 2")])
        grupos = explicar_propuesta(self.analisis())
        self.assertEqual([g["actividad"] for g in grupos], ["Levantamiento planimétrico"])
        panel = self.texto(self.panel())
        self.assertIn("Work propone", panel)
        self.assertEqual(self.panel().count('<section class="motivos-actividad">'), len(explicar_propuesta(
            self.analisis())))

    def test_sin_fuente_lo_dice_y_no_inventa_una(self):
        self.proponer([area("7 hectáreas", "confirmado", fuente="")])
        html = self.panel()
        self.assertIn("Sin respaldo registrado.", self.texto(html))
        self.assertNotIn("mail.google.com", html)

    def test_una_propuesta_sin_motivos_estructurados_se_muestra_por_lo_que_difiere(self):
        self.proponer([area("7 hectáreas", "confirmado", fuente=RESPALDO)])
        with store.transaccion() as db:
            analisis = json.loads(db.execute("SELECT analysis_json FROM quotes WHERE thread_id='t-1'").fetchone()[0])
            analisis.pop("revision_de_tabla")
            analisis["pending_model_activities"].append(fila("Nueva", "1", "u", "confirmado", servicio="Otro"))
            db.execute("UPDATE quotes SET analysis_json=? WHERE thread_id='t-1'", (json.dumps(analisis),))
        panel = self.texto(self.panel())
        self.assertIn("No dejó un motivo registrado", panel)
        self.assertIn("Cantidad hoy aproximadamente 6 hectáreas", panel)
        self.assertIn("Work propone 7 hectáreas", panel)
        self.assertIn("Actividad nueva", panel)

    def test_un_motivo_desconocido_se_muestra_tal_cual_sin_inventar(self):
        grupos = explicar_propuesta({"activities": [area("6")], "pending_model_activities": [area("7")],
                                     "revision_de_tabla": [{"tipo": "algo_nuevo", "actividad": "Levantamiento planimétrico"}]})
        (cambio,) = grupos[0]["cambios"]
        self.assertEqual((cambio["titulo"], cambio["codigo"], cambio["fuente"]), ("Motivo registrado", "algo_nuevo", ""))
        self.assertFalse(cambio["con_valores"])

    def test_sin_propuesta_no_hay_nada_que_explicar(self):
        self.assertEqual(explicar_propuesta({"activities": [area("6")]}), [])


class Visibilidad(PanelBase):
    def test_una_propuesta_que_solo_quita_una_actividad_tambien_se_ve(self):
        from anka.dominio.caso import model_proposal
        self.proponer([fila("Batimetría", servicio="Batimetría", ubicacion="Embalse",
                            notes="El cliente indica que ya no se requiere", fuente=RESPALDO)])
        self.assertEqual(model_proposal(self.analisis()), [{"kind": "quitar", "activity": "Batimetría", "detail": ""}])
        panel = self.texto(self.panel())
        self.assertIn("Se quitaría Batimetría", panel)
        self.assertIn("Eliminación propuesta", panel)

    def test_sin_motivo_una_actividad_que_se_quitaria_se_explica_igual(self):
        grupos = explicar_propuesta({"activities": [area("6"), fila("Batimetría")], "pending_model_activities": [area("6")]})
        (cambio,) = grupos[0]["cambios"]
        self.assertEqual((grupos[0]["actividad"], cambio["titulo"]), ("Batimetría", "Se quitaría"))
        self.assertIn("No dejó un motivo registrado", cambio["texto"])

    def test_el_resumen_de_lista_ya_cuenta_lo_que_se_quitaria(self):
        from anka.dominio.caso import resumen_de_analisis
        analisis = {"activities": [area("6"), fila("Batimetría")], "pending_model_activities": [area("6")]}
        self.assertEqual(resumen_de_analisis(analisis)["propuesta"][0]["kind"], "quitar")


class Seguridad(PanelBase):
    def test_el_texto_de_work_o_de_gmail_se_escapa(self):
        mal = '<script>alert("x")</script>'
        self.proponer([fila("Levantamiento planimétrico", "7 <b>ha</b>", "ha", "confirmado", fuente=mal,
                            entregable=mal)])
        with store.transaccion() as db:
            analisis = json.loads(db.execute("SELECT analysis_json FROM quotes WHERE thread_id='t-1'").fetchone()[0])
            analisis["revision_de_tabla"].append({"tipo": mal, "actividad": mal, "campo": mal, "propuesto": mal,
                                                  "anterior": mal, "fuente": f"Gmail {mal}"})
            db.execute("UPDATE quotes SET analysis_json=? WHERE thread_id='t-1'", (json.dumps(analisis),))
        panel = self.panel()
        self.assertNotIn("<script>", panel)
        self.assertNotIn("<b>ha</b>", panel)
        self.assertIn("&lt;script&gt;", panel)

    def test_solo_un_identificador_de_correo_hexadecimal_se_vuelve_enlace(self):
        grupos = explicar_propuesta({
            "activities": [area("6")], "pending_model_activities": [area("7")],
            "revision_de_tabla": [{"tipo": "dato_distinto", "actividad": "Levantamiento planimétrico", "campo": "quantity",
                                   "propuesto": "7", "fuente": 'Gmail "><img src=x>; Gmail 1a155f5531f151d3'}]})
        self.assertEqual(grupos[0]["cambios"][0]["correos"], ["1a155f5531f151d3"])


class Decision(PanelBase):
    def test_los_dos_botones_y_sus_confirmaciones_siguen_igual(self):
        self.proponer([area("7 hectáreas", "confirmado", fuente=RESPALDO)])
        html = self.panel()
        self.assertIn('data-confirm="Tu tabla se cambiará por la que propone Work. ¿Seguimos?">Aceptar la propuesta', html)
        self.assertIn('name="decision" value="keep">Dejar mi tabla', html)
        self.assertIn('action="/proposal"', html)

    def test_aceptar_cambia_la_tabla_y_el_panel_desaparece(self):
        self.proponer([area("7 hectáreas", "confirmado", fuente=RESPALDO)])
        resolve_model_proposal("t-1", True)
        self.assertEqual(self.analisis()["activities"][0]["quantity"], "7 hectáreas")
        self.assertNotIn('id="proposal"', self.pagina())

    def test_una_propuesta_descartada_ya_no_se_muestra_y_la_tabla_sigue_igual(self):
        self.proponer([area("7 hectáreas", "confirmado", fuente=RESPALDO)])
        resolve_model_proposal("t-1", False)
        self.assertEqual(self.analisis()["activities"][0]["quantity"], "aproximadamente 6 hectáreas")
        self.assertNotIn('id="proposal"', self.pagina())


if __name__ == "__main__":
    unittest.main()
