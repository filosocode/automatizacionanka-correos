"""La línea de comandos nunca envía correos al cliente (R8, U-77).

Cubre: U-77
"""

from tests import _isolate  # noqa: F401  (debe ir primero)

import ast
import contextlib
import io
import unittest
from pathlib import Path
from unittest.mock import patch

from anka.dominio import decision
from anka.dominio.status import QuoteStatus
from anka.servicios import pipeline
from tests.test_app import FakeGmail, complete_analysis, test_config
from tests import test_run_flow as flujo

RAIZ = Path(__file__).resolve().parents[1]


class _SinEnvio:
    """Proxy: delega todo en el objeto real y registra (o prohíbe) cualquier `.send(`."""

    def __init__(self, real, registro):
        self._real, self._registro = real, registro

    def __getattr__(self, nombre):
        valor = getattr(self._real, nombre)
        if nombre == "send":
            def enviar(*args, **kwargs):
                self._registro.append(nombre)
                raise AssertionError("La línea de comandos intentó enviar un correo (R8)")
            return enviar
        return _SinEnvio(valor, self._registro) if callable(valor) else valor

    def __call__(self, *args, **kwargs):
        resultado = self._real(*args, **kwargs)
        if isinstance(resultado, (dict, list, str, bytes, int, float, type(None))):
            return resultado        # el JSON que devuelve `execute()` no se envuelve
        return _SinEnvio(resultado, self._registro)


class GmailQueNoEnvia(FakeGmail):
    def __init__(self, thread):
        super().__init__(thread)
        self.intentos_de_envio: list[str] = []

    def users(self):
        return _SinEnvio(super().users(), self.intentos_de_envio)


def ejecutar(gmail, **flags):
    """Corre `pipeline.run(True, ...)` con datos faltantes; devuelve (filas, paquetes, texto de stderr)."""
    pipeline._AVISADO = False
    stderr = io.StringIO()
    with contextlib.redirect_stderr(stderr):
        filas, _, paquetes, _ = flujo.RunFlowTests().run_flow(gmail, complete_analysis(location=""), **flags)
    return filas, paquetes, stderr.getvalue()


def hilo():
    return flujo.RunFlowTests().thread()


# Cubre: U-77
class LaCliNuncaEnvia(unittest.TestCase):
    def test_sin_banderas_deja_el_caso_en_revision_y_avisa(self):
        gmail = GmailQueNoEnvia(hilo())
        filas, paquetes, aviso = ejecutar(gmail)
        self.assertEqual(filas[0][1], QuoteStatus.REVIEW_PENDING)
        self.assertEqual(len(paquetes), 1)
        self.assertEqual(gmail.intentos_de_envio, [])
        self.assertEqual(gmail.sent_messages, [])
        self.assertIn("ya no envía correos al cliente", aviso)
        self.assertIn("solo desde el dashboard", aviso)

    def test_review_only_y_gmail_drafts_no_envian_ni_avisan(self):
        for banderas in ({"review_only": True}, {"gmail_drafts": True, "review_only": False}):
            with self.subTest(banderas=banderas):
                gmail = GmailQueNoEnvia(hilo())
                filas, _, aviso = ejecutar(gmail, **banderas)
                self.assertEqual(gmail.intentos_de_envio, [])
                self.assertEqual(gmail.sent_messages, [])
                self.assertEqual(aviso, "")
                self.assertIn(filas[0][1], (QuoteStatus.REVIEW_PENDING, QuoteStatus.DRAFT_CREATED))

    def test_apply_solo_agrega_el_aviso_al_resultado_de_review_only(self):
        sin_banderas = ejecutar(GmailQueNoEnvia(hilo()))[0][0]
        con_review = ejecutar(GmailQueNoEnvia(hilo()), review_only=True)[0][0]
        self.assertEqual(sin_banderas[:2], con_review[:2])           # mismo hilo y mismo estado
        self.assertEqual(sin_banderas[5], con_review[5])             # ningún id de mensaje enviado

    def test_el_aviso_sale_una_vez_por_proceso(self):
        pipeline._AVISADO = False
        stderr = io.StringIO()
        with contextlib.redirect_stderr(stderr), patch.object(pipeline, "log"):
            pipeline._avisar_que_no_se_envia()
            pipeline._avisar_que_no_se_envia()
        self.assertEqual(stderr.getvalue().count("ya no envía"), 1)

    def test_una_configuracion_antigua_no_puede_pedir_el_envio(self):
        config = test_config() | {"auto_send": True, "send_questions": True, "send_mode": "direct"}
        gmail = GmailQueNoEnvia(hilo())
        with patch("anka.servicios.pipeline.load_config", return_value=config):
            filas, _, _ = ejecutar(gmail)
        self.assertEqual(gmail.intentos_de_envio, [])
        self.assertEqual(filas[0][1], QuoteStatus.REVIEW_PENDING)

    def test_la_decision_nunca_devuelve_un_envio(self):
        self.assertEqual({a.value for a in decision.Action}, {"none", "draft", "queue", "folder"})
        for review_only in (False, True):
            for borradores in (False, True):
                with self.subTest(review_only=review_only, borradores=borradores):
                    hecho = decision.decide_analysis(
                        "T1", complete_analysis(location=""), [], "cliente@example.com", False, test_config(),
                        review_only=review_only, gmail_drafts=borradores)
                    self.assertIn(hecho.action, (decision.Action.QUEUE, decision.Action.GMAIL_DRAFT))

    def test_ningun_modulo_de_la_cli_ni_del_revisor_llama_al_envio(self):
        modulos = [*sorted((RAIZ / "anka" / "cli").glob("*.py")),
                   RAIZ / "anka" / "servicios" / "pipeline.py", RAIZ / "anka" / "dominio" / "decision.py",
                   RAIZ / "anka" / "servicios" / "estado_de_revision.py"]
        for ruta in modulos:
            arbol = ast.parse(ruta.read_text(encoding="utf-8"))
            for nodo in ast.walk(arbol):
                with self.subTest(modulo=ruta.name):
                    if isinstance(nodo, ast.Call) and isinstance(nodo.func, ast.Attribute):
                        self.assertNotEqual(nodo.func.attr, "send")
                        self.assertNotEqual(nodo.func.attr, "send_reply")
                    if isinstance(nodo, ast.Name):
                        self.assertNotEqual(nodo.id, "send_reply")
                    if isinstance(nodo, ast.ImportFrom):
                        self.assertNotIn("send_reply", [a.name for a in nodo.names])


if __name__ == "__main__":
    unittest.main()
