"""Lo que la persona escribe sale tal cual; solo el borrador de Work pierde el historial citado (U-39, D-104)."""

from tests import _isolate  # noqa: F401  (debe ir primero)

import base64
import email
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from anka.dominio.correo import incluye_mensaje_citado, reply_body_without_quoted_history, split_quoted_reply
from anka.dominio.status import QuoteStatus
from anka.infra import settings, store
from anka.servicios import case_actions, work_import
from anka.web import pantalla_caso
from tests.test_app import FakeGmail, complete_analysis, encoded_message, test_config
from tests.test_emitir_t import TEXTO

CON_FLECHAS = "Hola Ana,\n\n> necesitamos 50 ha\n> en Bogotá\n\nTe confirmo lo que pediste.\nSaludos"
CON_CABECERA = ("Hola Ana,\n\nCopio lo que dijiste:\n\nEl jue, 1 oct 2026 a las 17:59, Ana <ana@acme.com> escribió:\n"
                "> necesitamos 50 ha\n\nGracias")
FRASE_SUELTA = "Hola Ana,\n\nEl cliente nos escribió: necesitamos 50 ha.\n\n> una sola línea con flecha\n\nSaludos"
BORRADOR_DE_WORK = ("Hola Ana,\n\n¿Cuál es el datum?\n\nSaludos\n\n"
                    "El jue, 1 oct 2026 a las 17:59, Ana <ana@acme.com> escribió:\n> necesitamos 50 ha")


def correo_en(gmail_raw: str) -> str:
    raw = base64.urlsafe_b64decode(gmail_raw.encode("ascii"))
    return email.message_from_bytes(raw).get_payload(decode=True).decode("utf-8")


class Base(unittest.TestCase):
    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        patcher = patch.object(settings, "DATA_DIR", Path(tmp.name))
        patcher.start()
        self.addCleanup(patcher.stop)
        thread = {"id": "t-1", "messages": [encoded_message("c1", "ana@acme.com", "Solicito cotización.", 1)]}
        self.gmail = FakeGmail(thread)
        analysis = complete_analysis(location="")
        analysis["routing"] = {"is_forwarded": False, "original_sender_email": "ana@acme.com",
                               "selected_recipient": "ana@acme.com"}
        for destino, kwargs in (("anka.servicios.case_actions.load_config", {"return_value": test_config()}),
                                ("anka.servicios.case_actions.services", {"return_value": (self.gmail, object())})):
            p = patch(destino, **kwargs)
            p.start()
            self.addCleanup(p.stop)
        db = store.connect()
        store.save(db, "t-1", "c1", "review_pending", "ana@acme.com", analysis)
        db.close()


# Cubre: U-39
class LoQueEscribeLaPersonaSaleTalCual(Base):
    def enviado(self, texto: str) -> str:
        case_actions.send_dashboard_reply("t-1", texto)
        return correo_en(self.gmail.sent_messages[0]["raw"]).strip()

    def test_enviar_conserva_las_flechas_tecleadas(self) -> None:
        self.assertEqual(self.enviado(CON_FLECHAS), CON_FLECHAS.strip())

    def test_enviar_conserva_la_cabecera_tecleada(self) -> None:
        self.assertEqual(self.enviado(CON_CABECERA), CON_CABECERA.strip())

    def test_enviar_conserva_la_frase_con_escribio(self) -> None:
        self.assertEqual(self.enviado(FRASE_SUELTA), FRASE_SUELTA.strip())

    def test_guardar_borrador_conserva_las_flechas_y_las_cabeceras_tecleadas(self) -> None:
        for texto in (CON_FLECHAS, CON_CABECERA, FRASE_SUELTA):
            with self.subTest(texto=texto[:30]):
                case_actions.save_dashboard_draft("t-1", texto)
                raw = (self.gmail.updated_drafts[-1]["body"] if self.gmail.updated_drafts
                       else self.gmail.created_drafts[-1])["message"]["raw"]
                self.assertEqual(correo_en(raw).strip(), texto.strip())

    def test_emitir_la_t_pasa_el_texto_al_envio_sin_recortarlo(self) -> None:
        enviados: list[str] = []
        texto = TEXTO + "\n\n" + CON_CABECERA
        with patch.object(case_actions, "case_email_context", return_value=({"recipient": "ana@acme.com"}, None, {}, set())), \
                patch.object(case_actions, "_recipient", return_value="ana@acme.com"), \
                patch.object(case_actions, "auto_create_project_folder", return_value="Carpeta en Drive: creada."), \
                patch.object(case_actions, "send_dashboard_reply",
                             side_effect=lambda hilo, cuerpo, *a, **k: enviados.append(cuerpo) or "Correo enviado."):
            db = store.connect()
            store.save(db, "t-2", "m2", QuoteStatus.READY_REVIEW, "ana@acme.com", complete_analysis())
            db.close()
            case_actions.issue_code_and_notify("t-2", "26001", texto, "26001")
        self.assertEqual(enviados, [texto])


# Cubre: U-39
class ElBorradorDeWorkSiPierdeLaCita(unittest.TestCase):
    def test_se_recorta_al_importarlo(self) -> None:
        analisis = work_import._work_analysis({
            "thread_id": "t-w", "status": "Faltan datos", "project_name": "P", "summary": "s",
            "client_email": "ana@acme.com", "draft_reply": BORRADOR_DE_WORK, "activities": "Levantamiento"})
        self.assertEqual(analisis["work_reply"]["text"], "Hola Ana,\n\n¿Cuál es el datum?\n\nSaludos")

    def pagina(self, analisis: dict, draft_body: str = "", draft_id: str = "") -> str:
        with tempfile.TemporaryDirectory() as tmp, patch.object(settings, "DATA_DIR", Path(tmp)),                 patch("anka.web.pantalla_caso.load_config", return_value=test_config()):
            db = store.open_db(Path(tmp))
            store.save(db, "t-w", "m1", QuoteStatus.READY_REVIEW, "ana@acme.com", analisis)
            if draft_body:
                db.execute("UPDATE quotes SET draft_body=?, draft_id=? WHERE thread_id='t-w'", (draft_body, draft_id))
            db.commit()
            db.close()
            return pantalla_caso.render_case("t-w")

    def test_un_borrador_de_work_ya_guardado_con_cita_se_recorta_al_mostrarlo(self) -> None:
        analisis = complete_analysis()
        analisis["work_reply"] = {"text": BORRADOR_DE_WORK, "draft_id": "w1"}
        pagina = self.pagina(analisis)
        self.assertIn("¿Cuál es el datum?", pagina)
        self.assertNotIn("necesitamos 50 ha", pagina)

    def test_el_texto_guardado_por_la_persona_no_se_recorta_al_mostrarlo(self) -> None:
        pagina = self.pagina(complete_analysis(), draft_body=CON_CABECERA, draft_id="mio")
        self.assertIn("necesitamos 50 ha", pagina)
        self.assertIn("Copio lo que dijiste:", pagina)


# Cubre: U-39
class ElDetectorNoConfundeFrases(unittest.TestCase):
    def test_una_frase_con_escribio_no_es_una_cabecera(self) -> None:
        texto = "Hola Ana,\nEl cliente nos escribió: necesitamos 50 ha.\nSaludos"
        self.assertEqual(reply_body_without_quoted_history(texto), texto)
        self.assertFalse(incluye_mensaje_citado(texto))

    def test_una_flecha_suelta_no_es_una_cita_pero_varias_si(self) -> None:
        self.assertFalse(incluye_mensaje_citado("Hola\n> una sola\nChao"))
        self.assertTrue(incluye_mensaje_citado(CON_FLECHAS))

    def test_una_cabecera_real_con_fecha_o_correo_si_lo_es(self) -> None:
        self.assertTrue(incluye_mensaje_citado(CON_CABECERA))
        self.assertEqual(split_quoted_reply(CON_CABECERA)[0], "Hola Ana,\n\nCopio lo que dijiste:")
        self.assertTrue(incluye_mensaje_citado("Hola\nOn Mon, 5 Oct 2026, Ana wrote:\n> x"))

    def test_el_dialogo_de_envio_y_de_emision_avisan_sin_recortar(self) -> None:
        layout = (Path(__file__).resolve().parents[1] / "anka" / "web" / "templates" / "layout.html").read_text(
            encoding="utf-8")
        self.assertIn("Tu respuesta incluye un mensaje citado. ¿Enviar así?", layout)
        self.assertEqual(layout.count("return avisoDeCita(boton, filas)"), 2)


if __name__ == "__main__":
    unittest.main()
