"""Cuando Work manda un análisis que no se puede leer: qué se guarda, qué se ve, cómo se limpia y cuándo se avisa.

Cubre: T-28, T-36, U-41
"""

from tests import _isolate  # noqa: F401  (debe ir primero)

import json
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

from anka.cli import work_sync as sync
from anka.dominio.caso_minimo import caso_minimo
from anka.infra import rechazos_de_work, settings, store, work_heartbeat
from anka.servicios import quotes, work_import
from anka.web import acciones, openapi, paginas, pantalla_caso
from tests.test_app import test_config
from tests.test_puente_determinista import GmailConHilos, borrador_sin_json, recibido
from tests.test_work_sync import draft_message, payload

SECRETO = "SECRETO-DEL-CLIENTE-7"


class Base(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        parche = patch.object(settings, "DATA_DIR", Path(self.temp.name))
        parche.start()
        self.addCleanup(parche.stop)
        self.checkpoint = Path(self.temp.name) / "state.json"
        self.cache: dict = {}

    def pasada(self, gmail: GmailConHilos) -> dict:
        return sync.sync_once(gmail, work_import.dashboard_upsert, self.checkpoint, self.cache,
                              {"ankacomercialia@gmail.com"})

    def malo(self, message_id: str = "gm-malo", stamp: str = "1760000000000", **extra) -> dict:
        """Un borrador con la identidad rota (el hilo del JSON no es el del borrador)."""
        borrador = draft_message(payload(thread="OTRO", **extra), message_id=message_id)
        borrador["internalDate"] = stamp
        return borrador

    def roto(self, message_id: str = "gm-roto", stamp: str = "1760000000000") -> dict:
        """Un borrador con el JSON dañado, con contenido del cliente dentro."""
        borrador = draft_message(payload(summary=SECRETO), message_id=message_id)
        texto = "[REVISAR ANTES DE ENVIAR - BORRAR ESTA SECCION]\n" + sync.START + '\n{"thread_id": "T1", "summary": "' \
            + SECRETO + '" "x": 1\n' + sync.END
        borrador["payload"]["body"]["data"] = __import__("base64").urlsafe_b64encode(texto.encode()).decode()
        borrador["internalDate"] = stamp
        return borrador

    def caso(self, thread: str = "T1") -> dict:
        return quotes.get_quote(thread)


class QueSeGuarda(Base):
    def test_del_rechazo_solo_se_guardan_motivo_causa_campos_y_fecha(self) -> None:
        # Cubre: T-36, U-41
        gmail = GmailConHilos({"d1": self.roto()}, {"T1": [recibido()]})
        resultado = self.pasada(gmail)
        rechazo = resultado["rejected"][0]
        self.assertEqual(rechazo["causa"], "json_ilegible")
        self.assertRegex(rechazo["fecha"], r"^\d{4}-\d{2}-\d{2}$")
        # ni el latido, ni la base, ni el caso mínimo, ni el historial, ni el punto de control llevan el contenido
        work_heartbeat.write_heartbeat(30, rejected=resultado["rejected"])
        lugares = [json.dumps(work_heartbeat.read_heartbeat()), self.checkpoint.read_text(encoding="utf-8")]
        db = store.connect()
        try:
            lugares.append(json.dumps([dict(f) for f in db.execute("SELECT * FROM work_rejections")]))
            lugares.append(db.execute("SELECT analysis_json FROM quotes WHERE thread_id='T1'").fetchone()[0])
            lugares.append(json.dumps([dict(f) for f in db.execute("SELECT summary FROM case_events")]))
        finally:
            db.close()
        for lugar in lugares:
            self.assertNotIn(SECRETO, lugar)
        for lugar in lugares[:1]:
            self.assertNotIn("Expecting", lugar)

    def test_el_motivo_se_muestra_en_espanol_y_sin_errores_de_python(self) -> None:
        # Cubre: T-28, U-41
        gmail = GmailConHilos({"d1": self.roto()}, {"T1": [recibido()]})
        self.pasada(gmail)
        analisis = self.caso()["analysis"]
        texto = analisis["sin_analisis_de_work"]["motivo"] + analisis["scope_dependencies"][0]["description"]
        self.assertIn("línea", texto)
        for jerga in ("Expecting", "delimiter", "T-36", "JSON", "Pídele a Work"):
            self.assertNotIn(jerga, texto)

    def test_cada_rechazo_se_cuenta_una_sola_vez_aunque_se_revise_en_cada_ciclo(self) -> None:
        # Cubre: T-28
        gmail = GmailConHilos({"d1": self.malo()}, {"T1": [recibido()]})
        for _ in range(3):
            self.pasada(gmail)
        self.assertEqual(rechazos_de_work.cuenta_del_dia(), 1)


class SeLimpiaSolo(Base):
    def test_un_analisis_valido_posterior_del_mismo_hilo_limpia_el_aviso(self) -> None:
        # Cubre: T-28, U-41
        gmail = GmailConHilos({"d1": self.roto(stamp="1760000000000")}, {"T1": [recibido()]})
        self.assertEqual(len(self.pasada(gmail)["rejected"]), 1)
        gmail.items["d2"] = draft_message(payload(thread="T1", message="in1"), message_id="gm-valido")
        gmail.items["d2"]["internalDate"] = "1760000003000"
        resultado = self.pasada(gmail)            # el borrador roto sigue en Gmail, pero ya no se avisa
        self.assertEqual((resultado["updated"], resultado["rejected"]), (1, []))
        self.assertEqual(self.pasada(gmail)["rejected"], [])
        self.assertNotIn("sin_analisis_de_work", self.caso()["analysis"])

    def test_un_rechazo_mas_nuevo_que_el_ultimo_analisis_sigue_a_la_vista(self) -> None:
        # Cubre: T-28
        gmail = GmailConHilos({"d1": draft_message(payload(thread="T1"), message_id="gm-bueno")}, {"T1": [recibido()]})
        gmail.items["d1"]["internalDate"] = "1760000000000"
        self.assertEqual(self.pasada(gmail)["updated"], 1)
        gmail.items["d2"] = self.roto(stamp="1760000005000")           # Work no supo analizar el mensaje nuevo
        self.assertEqual(len(self.pasada(gmail)["rejected"]), 1)

    def test_el_caso_minimo_no_cuenta_como_analisis_valido_para_limpiar_su_propio_aviso(self) -> None:
        # Cubre: T-28
        gmail = GmailConHilos({"d1": self.roto()}, {"T1": [recibido()]})
        self.pasada(gmail)
        self.assertEqual(len(self.pasada(gmail)["rejected"]), 1)


class Historial(Base):
    def test_el_caso_minimo_no_dice_que_work_lo_analizo(self) -> None:
        # Cubre: T-36, U-41
        self.pasada(GmailConHilos({"d1": borrador_sin_json()}, {"T1": [recibido()]}))
        db = store.connect()
        try:
            hechos = [f["summary"] for f in db.execute("SELECT summary FROM case_events WHERE thread_id='T1'")]
        finally:
            db.close()
        self.assertTrue(any("no se pudo leer" in h for h in hechos), hechos)
        self.assertFalse(any("Work analizó el caso" in h for h in hechos), hechos)

    def test_un_analisis_real_sigue_diciendo_que_work_lo_analizo(self) -> None:
        # Cubre: T-36
        self.pasada(GmailConHilos({"d1": draft_message(payload(thread="T1"))}, {"T1": [recibido()]}))
        db = store.connect()
        try:
            hechos = [f["summary"] for f in db.execute("SELECT summary FROM case_events WHERE thread_id='T1'")]
        finally:
            db.close()
        self.assertTrue(any("Work analizó el caso" in h for h in hechos), hechos)


class PantallaDelCasoMinimo(Base):
    def setUp(self) -> None:
        super().setUp()
        for parche in (patch("anka.web.pantalla_caso.load_config", return_value={**test_config(), "active_mode": "chatgpt_work"}),
                       patch("anka.web.paginas.load_config", return_value={**test_config(), "active_mode": "chatgpt_work"})):
            parche.start()
            self.addCleanup(parche.stop)
        self.pasada(GmailConHilos({"d1": borrador_sin_json()}, {"T1": [recibido()]}))

    def test_la_franja_explica_y_ofrece_completar_la_tabla(self) -> None:
        # Cubre: U-41
        pagina = pantalla_caso.render_case("T1")
        self.assertIn("Este caso aún no tiene análisis de Work", pagina)
        self.assertIn(">Completar la tabla yo<", pagina)
        self.assertIn("Si Work vuelve a mandar el análisis, se revisa solo", pagina)
        self.assertIn('id="editar-tabla"', pagina)                        # el editor existente al que lleva el botón
        self.assertIn("Corregir la tabla", pagina)
        for jerga in ("T-36", "Pide a Work", "Pídele a Work", "arréglalo en Gmail"):
            self.assertNotIn(jerga, pagina)

    def test_confirmar_que_alcanza_esta_desactivado_con_su_razon_mientras_no_haya_actividades(self) -> None:
        # Cubre: U-41
        pagina = pantalla_caso.render_case("T1")
        boton = pagina[pagina.index('id="btn-suficiente"'):]
        self.assertIn("disabled", boton[:boton.index(">")])
        self.assertIn("se activa cuando la tabla tenga al menos una actividad", pagina)

    def test_el_servidor_tampoco_deja_confirmar_sin_actividades(self) -> None:
        # Cubre: U-41
        with self.assertRaisesRegex(ValueError, "Todavía no hay actividades"):
            quotes.resolve_technical_review("T1", "sufficient")
        self.assertEqual(self.caso()["status"], "review")        # no se movió

    def test_con_una_actividad_ya_se_puede_confirmar(self) -> None:
        # Cubre: U-41
        from anka.servicios.ediciones import save_activities
        save_activities("T1", [{"activity": "Levantamiento", "estado": "confirmado"}])
        pagina = pantalla_caso.render_case("T1")
        boton = pagina[pagina.index('id="btn-suficiente"'):]
        self.assertNotIn("disabled", boton[:boton.index(">")])
        self.assertNotIn("se activa cuando la tabla tenga", pagina)

    def test_el_caso_con_actividades_de_un_analisis_real_no_cambia(self) -> None:
        # Cubre: U-41
        valido = draft_message(payload(thread="T1", message="in1"), message_id="g2")
        valido["internalDate"] = "1760000003000"
        self.pasada(GmailConHilos({"d1": valido}, {"T1": [recibido()]}))
        pagina = pantalla_caso.render_case("T1")
        self.assertNotIn("Este caso aún no tiene análisis de Work", pagina)


class PanelDelResumen(Base):
    def setUp(self) -> None:
        super().setUp()
        for parche in (patch("anka.web.pantalla_caso.load_config", return_value={**test_config(), "active_mode": "chatgpt_work"}),
                       patch("anka.web.paginas.load_config", return_value={**test_config(), "active_mode": "chatgpt_work"})):
            parche.start()
            self.addCleanup(parche.stop)
        self.gmail = GmailConHilos({"d1": self.roto()}, {"T1": [recibido()]})
        resultado = self.pasada(self.gmail)
        work_heartbeat.write_heartbeat(30, rejected=resultado["rejected"])

    def test_el_panel_se_explica_solo_y_su_boton_principal_es_completar_la_tabla(self) -> None:
        # Cubre: U-41
        panel = str(paginas._work_rejected_panel())
        self.assertIn("Falta el análisis de un caso", panel)
        self.assertIn("Work dejó el análisis, pero Anka no pudo leerlo", panel)
        self.assertIn("Si Work vuelve a mandar el análisis, se revisa solo", panel)
        self.assertLess(panel.index(">Completar la tabla yo<"), panel.index(">Dejarlo para después<"))
        self.assertIn('class="button" href="/case?thread_id=T1#editar-tabla"', panel)
        self.assertIn('class="secondary">Dejarlo para después', panel)
        for sobra in ("Pide a Work", "arréglalo en Gmail", "Work no pudo entender", "Expecting", "T-36"):
            self.assertNotIn(sobra, panel)

    def test_dejarlo_para_despues_oculta_el_aviso_deja_huella_y_no_toca_el_caso(self) -> None:
        # Cubre: U-41
        antes = self.caso()["analysis"]
        mensaje = acciones.POST_ROUTES["/rejection-dismiss"](
            {"draft": ["d1"], "message": ["gm-roto"], "thread_id": ["T1"]})
        self.assertIn("message=", mensaje)
        self.assertEqual(str(paginas._work_rejected_panel()), "")
        self.assertEqual(self.caso()["analysis"], antes)
        db = store.connect()
        try:
            hechos = [f["summary"] for f in db.execute("SELECT summary FROM case_events WHERE thread_id='T1'")]
        finally:
            db.close()
        self.assertTrue(any("dejó para después el aviso" in h for h in hechos), hechos)
        # y el puente tampoco lo vuelve a listar ni a contar
        self.assertEqual(self.pasada(self.gmail)["rejected"], [])

    def test_dejar_dos_veces_el_mismo_aviso_dice_que_ya_no_esta(self) -> None:
        # Cubre: U-41
        datos = {"draft": ["d1"], "message": ["gm-roto"], "thread_id": ["T1"]}
        acciones.POST_ROUTES["/rejection-dismiss"](datos)
        self.assertIn("ya+no+est", acciones.POST_ROUTES["/rejection-dismiss"](datos).replace("%C3%A1", "a")
                      .replace("%20", "+"))

    def test_la_ruta_esta_documentada_en_swagger(self) -> None:
        # Cubre: U-41
        _, post = openapi.rutas_documentadas()
        self.assertIn("/rejection-dismiss", post)


class AlertaDelDia(Base):
    def rechazar(self, cuantos: int) -> None:
        for n in range(cuantos):
            rechazos_de_work.registrar(f"d{n}", f"m{n}", "T1", "json_ilegible", ["x"], "motivo")

    def test_con_tres_rechazos_no_hay_alerta_y_con_cuatro_si(self) -> None:
        # Cubre: U-41
        self.rechazar(3)
        self.assertEqual(rechazos_de_work.alerta_del_dia(), "")
        self.rechazar(4)
        self.assertEqual(rechazos_de_work.alerta_del_dia(),
                         "Work está mandando análisis con errores: 4 hoy. Pide revisar el prompt.")

    def test_solo_cuenta_el_dia_de_hoy(self) -> None:
        # Cubre: U-41
        ayer = datetime(2026, 1, 1, 9, 0).astimezone()
        for n in range(6):
            rechazos_de_work.registrar(f"d{n}", f"m{n}", "T1", "x", [], "motivo", cuando=ayer)
        self.assertEqual(rechazos_de_work.cuenta_del_dia(), 0)
        self.assertEqual(rechazos_de_work.cuenta_del_dia(ayer.date()), 6)

    def test_la_alerta_aparece_en_el_estado_de_la_revision_de_correos_y_en_el_resumen(self) -> None:
        # Cubre: U-41
        work_heartbeat.write_heartbeat(30)
        self.rechazar(4)
        self.assertIn("Work está mandando análisis con errores: 4 hoy", work_heartbeat.bridge_state()[1])
        self.assertIn("Pide revisar el prompt", str(paginas._work_rejected_panel()))

    def test_sin_alerta_ni_rechazos_no_hay_panel(self) -> None:
        # Cubre: U-41
        self.assertEqual(str(paginas._work_rejected_panel()), "")

    def test_un_fallo_de_la_cuenta_no_tumba_la_pagina(self) -> None:
        # Cubre: U-41
        with patch.object(rechazos_de_work, "lectura", side_effect=OSError("disco")),                 patch.object(rechazos_de_work, "transaccion", side_effect=OSError("disco")):
            self.assertEqual(rechazos_de_work.cuenta_del_dia(), 0)
            rechazos_de_work.registrar("d", "m", "t", "x", [], "motivo")        # no lanza


class CasoMinimoPuro(unittest.TestCase):
    def test_guarda_motivo_campos_y_fecha_y_sin_identificadores_internos(self) -> None:
        # Cubre: T-36
        minimo = caso_minimo("T1", recibido(), "El estado no es válido.", ("status",), "2026-10-15")
        self.assertEqual(minimo["_rechazo_de_work"], {"motivo": "El estado no es válido.", "campos": ["status"],
                                                      "fecha": "2026-10-15"})
        self.assertTrue(minimo["_caso_minimo"])
        self.assertNotIn("T-36", json.dumps(minimo, ensure_ascii=False))


if __name__ == "__main__":
    unittest.main()
