"""Errores con salida, protección de la T, recarga segura, pantalla vieja y tabla de actividades (U-34, D-99)."""

from tests import _isolate  # noqa: F401  (debe ir primero)

import tempfile
import unittest
import urllib.parse
from pathlib import Path
from unittest.mock import patch

from anka.dominio.activities import huella_de_tabla, parse_activities_form
from anka.dominio.errores import AutorizacionDeGoogle, ErrorDeAnka
from anka.dominio.status import QuoteStatus
from anka.infra import settings, store
from anka.servicios import case_actions, change_control, ediciones, quotes
from anka.web import formularios_pendientes as pendientes
from anka.web import pantalla_caso, seguridad
from anka.web import server as web
from tests.test_app import complete_analysis, test_config
from tests.test_seguridad_http import atender

RAIZ = Path(__file__).resolve().parents[1]
PLANTILLAS = RAIZ / "anka" / "web" / "templates"
TEXTO = ("Hola,\n\nRecibimos toda la información de su proyecto «P». Hemos generado el código T26001.\n\n"
         "Saludos,\nGrupo Anka")


def formulario(**campos) -> bytes:
    return urllib.parse.urlencode({"csrf_token": seguridad.CSRF_TOKEN, **campos}).encode()


class Base(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        for parche in (patch.object(settings, "DATA_DIR", Path(self.temp.name)),
                       patch("anka.web.paginas.load_config", return_value=test_config()),
                       patch("anka.web.pantalla_caso.load_config", return_value=test_config())):
            parche.start()
            self.addCleanup(parche.stop)
        pendientes.limpiar_todo()
        self.addCleanup(pendientes.limpiar_todo)
        db = store.open_db(Path(self.temp.name))
        store.save(db, "t-1", "m-1", QuoteStatus.READY_REVIEW, "ana@acme.com", complete_analysis())
        db.commit()
        db.close()

    def codigo(self) -> str:
        db = store.open_db(Path(self.temp.name))
        try:
            return db.execute("SELECT quote_code FROM quotes WHERE thread_id='t-1'").fetchone()[0] or ""
        finally:
            db.close()


# Cubre: U-34
class ErroresConSalida(Base):
    def test_un_error_de_anka_en_una_accion_es_un_mensaje_en_el_caso_no_un_500(self) -> None:
        # Cubre: U-34
        for ruta, servicio in (("/send", "send_dashboard_reply"), ("/draft", "save_dashboard_draft"),
                               ("/approve", "approve_review")):
            with self.subTest(ruta=ruta), patch(f"anka.web.acciones.{servicio}", side_effect=AutorizacionDeGoogle()):
                estado, cabeceras, _, _ = atender("POST", ruta, formulario(thread_id="t-1", body="Hola"))
            self.assertEqual(estado, 303)

    def test_el_mensaje_de_autorizacion_viaja_en_la_redireccion_y_lo_escrito_se_conserva(self) -> None:
        # Cubre: U-34
        with patch("anka.web.acciones.send_dashboard_reply", side_effect=AutorizacionDeGoogle()):
            atender("POST", "/send", formulario(thread_id="t-1", body="Texto que no debe perderse"))
        self.assertIsNotNone(pendientes.ver("t-1"))

    def test_un_modo_invalido_es_un_mensaje_no_un_500(self) -> None:
        # Cubre: U-34
        estado, _, _, _ = atender("POST", "/mode", formulario(mode="inventado"))
        self.assertEqual(estado, 303)

    def test_un_enlace_con_numero_ilegible_no_es_un_500(self) -> None:
        # Cubre: U-34
        estado, _, _, _ = atender("POST", "/link", formulario(thread_id="t-1", link_id="abc", decision="yes"))
        self.assertLess(estado, 500)

    def test_un_caso_inexistente_es_404_con_salida_a_mis_casos(self) -> None:
        # Cubre: U-34
        estado, _, pagina, _ = atender("GET", "/case?thread_id=no-existe")
        self.assertEqual(estado, 404)
        self.assertIn("La solicitud no existe.", pagina)
        self.assertIn("← Mis casos", pagina)

    def test_un_error_de_anka_no_pide_avisar_con_un_codigo(self) -> None:
        # Cubre: U-34
        def falla(form):
            raise ErrorDeAnka("Falta la clave de OpenAI: configúrala y vuelve a intentarlo.")
        with patch.dict(web.POST_ROUTES, {"/refresh": falla}), self.assertLogs("anka.web.server", "ERROR"):
            estado, _, pagina, _ = atender("POST", "/refresh", formulario(thread_id="t-1"))
        self.assertEqual(estado, 500)
        self.assertIn("Falta la clave de OpenAI", pagina)
        self.assertNotIn("código de error", pagina)
        self.assertIn("Volver al caso", pagina)
        self.assertIn("← Mis casos", pagina)

    def test_un_fallo_inesperado_sigue_dando_su_codigo(self) -> None:
        # Cubre: U-34
        def falla(form):
            raise RuntimeError("detalle interno")
        with patch.dict(web.POST_ROUTES, {"/refresh": falla}), self.assertLogs("anka.web.server", "ERROR"):
            _, _, pagina, _ = atender("POST", "/refresh", formulario())
        self.assertIn("código de error", pagina)
        self.assertNotIn("detalle interno", pagina)

    def test_la_autorizacion_de_google_ofrece_reautorizar_solo_con_un_boton(self) -> None:
        # Cubre: U-34
        def falla(form):
            raise AutorizacionDeGoogle()
        with patch.dict(web.POST_ROUTES, {"/refresh": falla}), self.assertLogs("anka.web.server", "ERROR"):
            _, _, pagina, _ = atender("POST", "/refresh", formulario(thread_id="t-1"))
        self.assertIn('action="/google/reautorizar"', pagina)
        self.assertIn("data-confirm", pagina)          # la persona confirma antes de que se abra el navegador
        self.assertIn("Volver a conectar Google", pagina)

    def test_un_error_de_configuracion_al_abrir_el_caso_dice_que_falta(self) -> None:
        # Cubre: U-34
        with patch("anka.web.pantalla_caso.render_case", side_effect=ErrorDeAnka("Crea config.json.")), \
                self.assertLogs("anka.web.server", "WARNING"):
            estado, _, pagina, _ = atender("GET", "/case?thread_id=t-1")
        self.assertEqual(estado, 500)
        self.assertIn("Crea config.json.", pagina)
        self.assertNotIn("código de error", pagina)


# Cubre: U-34
class EmitirLaT(Base):
    def test_sin_google_no_se_consume_la_t(self) -> None:
        # Cubre: U-34
        with patch.object(case_actions, "case_email_context", side_effect=AutorizacionDeGoogle()):
            with self.assertRaises(AutorizacionDeGoogle):
                case_actions.issue_code_and_notify("t-1", "26001", TEXTO, "26001")
        self.assertEqual(self.codigo(), "")

    def test_sin_destinatario_valido_no_se_consume_la_t(self) -> None:
        # Cubre: U-34
        with patch.object(case_actions, "case_email_context", return_value=({"recipient": ""}, None, {}, set())), \
                patch.object(case_actions, "_recipient", side_effect=ValueError("Sin destinatario.")):
            with self.assertRaises(ValueError):
                case_actions.issue_code_and_notify("t-1", "26001", TEXTO, "26001")
        self.assertEqual(self.codigo(), "")

    def test_un_numero_invalido_o_repetido_se_rechaza_antes_de_tocar_nada(self) -> None:
        # Cubre: U-34
        with patch.object(case_actions, "case_email_context") as contexto:
            with self.assertRaises(ValueError):
                case_actions.issue_code_and_notify("t-1", "12", TEXTO, "26001")
            contexto.assert_not_called()
        self.assertEqual(change_control.comprobar_numero("t-1", " 26001 "), "26001")
        self.assertEqual(self.codigo(), "")        # comprobar no asigna

    def test_si_el_envio_falla_por_autorizacion_la_t_queda_y_el_mensaje_no_culpa_a_gmail(self) -> None:
        # Cubre: U-34
        with patch.object(case_actions, "case_email_context", return_value=({"recipient": "ana@acme.com"}, None, {}, set())), \
                patch.object(case_actions, "_recipient", return_value="ana@acme.com"), \
                patch.object(case_actions, "auto_create_project_folder", return_value="Carpeta lista."), \
                patch.object(case_actions, "send_dashboard_reply", side_effect=AutorizacionDeGoogle()):
            mensaje = case_actions.issue_code_and_notify("t-1", "26001", TEXTO, "26001")
        self.assertEqual(self.codigo(), "26001")
        self.assertIn(case_actions.AVISO, mensaje)
        self.assertIn("autorización de Google", mensaje)
        self.assertNotIn("error de Gmail", mensaje)

    def test_si_el_correo_no_salio_el_texto_revisado_vuelve_al_cuadro_de_respuesta(self) -> None:
        # Cubre: U-34
        aviso = f"T26001 emitida. {case_actions.AVISO} El correo no se envió."
        with patch("anka.web.acciones.issue_code_and_notify", return_value=aviso):
            estado, _, _, _ = atender("POST", "/code-issue", formulario(
                thread_id="t-1", number="26001", suggested_number="26001", body=TEXTO))
        self.assertEqual(estado, 303)
        self.assertIsNotNone(pendientes.ver("t-1"))
        db = store.open_db(Path(self.temp.name))
        db.execute("UPDATE quotes SET quote_code='26001', code_stage='T' WHERE thread_id='t-1'")
        db.commit()
        db.close()
        pagina = pantalla_caso.render_case("t-1")
        self.assertIn("Recibimos toda la información de su proyecto", pagina)

    def test_el_dialogo_de_emision_muestra_numero_destinatario_y_avisos(self) -> None:
        # Cubre: U-34
        panel = (PLANTILLAS / "code_panel.html").read_text(encoding="utf-8")
        self.assertIn('data-resumen="emision"', panel)
        for atributo in ("data-para=", "data-sugerido=", "data-anio="):
            self.assertIn(atributo, panel)
        layout = (PLANTILLAS / "layout.html").read_text(encoding="utf-8")
        for pieza in ("=== 'emision'", "Número de la T", "no es el sugerido", "no es el actual"):
            self.assertIn(pieza, layout)
        pagina = pantalla_caso.render_case("t-1")
        self.assertIn('data-anio="', pagina)


# Cubre: U-34
class RecargaAutomatica(unittest.TestCase):
    def test_no_recarga_si_hay_trabajo_en_curso_y_el_aviso_es_un_estado(self) -> None:
        # Cubre: U-34
        layout = (PLANTILLAS / "layout.html").read_text(encoding="utf-8")
        for pieza in ("function puedeRecargar()", "window.scrollY > 0", "dialogo.open", "campo.value !== campo.defaultValue",
                      "if (puedeRecargar()) location.reload();", 'id="fresh-banner" class="notice" role="status"'):
            self.assertIn(pieza, layout)
        self.assertEqual(layout.count("location.reload()"), 3)       # pageshow, el enlace del aviso y la recarga segura


# Cubre: U-34
class PantallaVieja(Base):
    def _con_contexto(self):
        return (patch.object(case_actions, "case_email_context",
                             side_effect=AssertionError("no debía llegar a Gmail")))

    def test_un_envio_desde_una_pantalla_vieja_se_rechaza_sin_enviar(self) -> None:
        # Cubre: U-34
        with self._con_contexto():
            with self.assertRaises(ValueError) as error:
                case_actions.send_dashboard_reply("t-1", "Hola", state_token="2000-01-01T00:00:00|")
        self.assertIn("cambió desde que abriste la pantalla", str(error.exception))

    def test_la_marca_vigente_si_pasa_y_sin_marca_no_se_comprueba(self) -> None:
        # Cubre: U-34
        marca = quotes.get_quote("t-1").marca_de_estado
        for token in (marca, ""):
            with self.subTest(token=token), patch.object(
                    case_actions, "case_email_context", side_effect=AssertionError("llegó a Gmail")):
                with self.assertRaises(AssertionError):
                    case_actions.send_dashboard_reply("t-1", "Hola", state_token=token)

    def test_la_ruta_conserva_lo_escrito_y_la_pantalla_lleva_la_marca(self) -> None:
        # Cubre: U-34
        atender("POST", "/send", formulario(thread_id="t-1", body="Texto revisado", state_token="vieja|"))
        self.assertIsNotNone(pendientes.ver("t-1"))
        pagina = pantalla_caso.render_case("t-1")
        self.assertIn('name="state_token" value="' + quotes.get_quote("t-1").marca_de_estado, pagina)

    def test_el_dialogo_avisa_si_ya_se_envio_un_correo(self) -> None:
        # Cubre: U-34
        caso = (PLANTILLAS / "case.html").read_text(encoding="utf-8")
        self.assertIn("{% if review.sent_at %}Ya se envió un correo", caso)


# Cubre: U-34
class TablaDeActividades(Base):
    def _filas(self, **extra) -> dict:
        campos = {"row_count": ["1"], "a0_activity": ["Levantamiento topográfico"], "a0_estado": ["confirmado"]}
        campos.update({k: [v] for k, v in extra.items()})
        return campos

    def test_un_formulario_sin_row_count_se_rechaza(self) -> None:
        # Cubre: U-34
        with self.assertRaises(ValueError) as error:
            parse_activities_form({"a0_activity": ["x"]})
        self.assertIn("llegó incompleto", str(error.exception))

    def test_row_count_cero_con_filas_guardadas_se_rechaza_y_no_borra_la_tabla(self) -> None:
        # Cubre: U-34
        with self.assertRaises(ValueError):
            ediciones.save_activities("t-1", [], "", "", filas_enviadas=0)
        self.assertEqual(len(quotes.get_quote("t-1")["analysis"]["activities"]), 1)

    def test_una_fila_con_datos_necesita_nombre(self) -> None:
        # Cubre: U-34
        with self.assertRaises(ValueError) as error:
            parse_activities_form({"row_count": ["1"], "a0_activity": ["Algo"], "a1_quantity": ["5"]})
        self.assertIn("la fila nueva tiene datos pero no tiene nombre", str(error.exception).lower())

    def test_una_celda_demasiado_larga_se_avisa_en_vez_de_recortarse(self) -> None:
        # Cubre: U-34
        with self.assertRaises(ValueError) as error:
            parse_activities_form(self._filas(a0_notes="x" * 2001))
        self.assertIn("2000", str(error.exception))
        plantilla = (PLANTILLAS / "activities_editor.html").read_text(encoding="utf-8")
        self.assertIn("maxlength='{{ max_celda }}'", plantilla)
        self.assertIn("maxlength='2000'", pantalla_caso.render_case("t-1"))

    def test_una_tabla_que_cambio_desde_que_se_abrio_se_rechaza_y_no_se_guarda(self) -> None:
        # Cubre: U-34
        antes = huella_de_tabla(quotes.get_quote("t-1")["analysis"]["activities"])
        ediciones.save_activities("t-1", [{"activity": "Otra edición"}], "", antes)       # alguien más guardó
        with self.assertRaises(ValueError) as error:
            ediciones.save_activities("t-1", [{"activity": "Mi edición"}], "", antes)
        self.assertIn("La tabla cambió desde que la abriste", str(error.exception))
        self.assertEqual(quotes.get_quote("t-1")["analysis"]["activities"][0]["activity"], "Otra edición")

    def test_el_rechazo_vuelve_al_caso_con_lo_escrito_y_la_pantalla_trae_la_huella(self) -> None:
        # Cubre: U-34
        estado, _, _, _ = atender("POST", "/activities", formulario(
            thread_id="t-1", row_count="1", base_version="vieja", a0_activity="Mi edición", a0_estado="pendiente"))
        self.assertEqual(estado, 303)
        self.assertIsNotNone(pendientes.ver("t-1"))
        huella = huella_de_tabla(quotes.get_quote("t-1")["analysis"]["activities"])
        pagina = pantalla_caso.render_case("t-1")
        self.assertIn(f'name="base_version" value="{huella}"', pagina)
        self.assertIn("Mi edición", pagina)         # lo escrito vuelve al editor

    def test_guardar_sin_cambios_no_crea_version(self) -> None:
        # Cubre: U-34
        actuales = quotes.get_quote("t-1")["analysis"]["activities"]
        antes = len(ediciones.table_versions("t-1"))
        mensaje = ediciones.save_activities("t-1", [dict(fila) for fila in actuales], "", huella_de_tabla(actuales))
        self.assertIn("No hay cambios", mensaje)
        self.assertEqual(len(ediciones.table_versions("t-1")), antes)

    def test_una_edicion_real_sigue_guardando_su_version(self) -> None:
        # Cubre: U-34
        actuales = quotes.get_quote("t-1")["analysis"]["activities"]
        antes = len(ediciones.table_versions("t-1"))
        nuevas = [{**actuales[0], "notes": "Revisada con el cliente"}]
        mensaje = ediciones.save_activities("t-1", nuevas, "", huella_de_tabla(actuales), filas_enviadas=1)
        self.assertIn("Tabla guardada", mensaje)
        self.assertEqual(len(ediciones.table_versions("t-1")), antes + 1)

    def test_el_script_cuenta_como_quitada_la_fila_vaciada(self) -> None:
        # Cubre: U-34
        layout = (PLANTILLAS / "layout.html").read_text(encoding="utf-8")
        self.assertIn("const vaciada = !fila.querySelector('.act-main.nueva')", layout)
        self.assertIn("c.checked || vaciada", layout)


if __name__ == "__main__":
    unittest.main()
