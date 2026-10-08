"""Cubre: U-09, U-10, U-11 — siguiente paso fijo, una sola acción de emisión, fuentes en rangos y cola de hoy."""
from __future__ import annotations

import unittest
from pathlib import Path

from anka.dominio.attachments import compactar_paginas
from anka.dominio.status import QuoteStatus
from anka.web import paginas
from anka.web.templating import render

RAIZ = Path(__file__).resolve().parents[1]


class FuentesCompactas(unittest.TestCase):
    def test_agrupa_paginas_consecutivas_sin_perder_ninguna(self) -> None:
        # Cubre: U-10
        origen = ("adjunto E.T.pdf, Página 3, Página 5, Página 6, Página 7, Página 10, Página 11, Página 12, "
                  "Página 13, Página 14, Página 15")
        self.assertEqual(compactar_paginas(origen), "adjunto E.T.pdf, págs. 3, 5–7, 10–15")

    def test_una_sola_pagina_y_dos_consecutivas(self) -> None:
        # Cubre: U-10
        self.assertEqual(compactar_paginas("Fuente: Página 4"), "Fuente: Página 4")
        self.assertEqual(compactar_paginas("Página 8; Página 9"), "págs. 8, 9")

    def test_orden_y_repetidas(self) -> None:
        # Cubre: U-10
        self.assertEqual(compactar_paginas("Página 9, Página 2, Página 9, Página 3"), "págs. 2, 3, 9")


class EmisionDeLaT(unittest.TestCase):
    def _panel(self) -> str:
        return str(render("code_panel.html", code="", stage="", stage_label="", thread_id="h1", approved_code="",
                          can_assign=True, suggestion="26006", contact="c@x.co", received_text="Hola"))

    def test_solo_una_accion_primaria_de_emision_visible(self) -> None:
        # Cubre: U-09
        html = self._panel()
        primario = html.index("Emitir la T, crear la carpeta y enviar el correo")
        plegado = html.index("Solo emitir la T")
        self.assertLess(primario, html.index("Otras opciones"))
        self.assertGreater(plegado, html.index("<details"))

    def test_aviso_de_siguiente_paso_queda_fijo(self) -> None:
        # Cubre: U-09
        css = (RAIZ / "anka" / "web" / "templates" / "style.css").read_text(encoding="utf-8")
        regla = next(linea for linea in css.splitlines() if linea.startswith(".next-step {") and "sticky" in linea)
        self.assertIn("position: sticky", regla)


def _caso(hilo: str, estado: QuoteStatus, **extra: object) -> dict:
    return {"thread_id": hilo, "status": estado, "status_label": "x", "client_name": "OTACC", "changes": [],
            "analysis": {"project_name": f"Proyecto {hilo}"}, "services": [], "client_type": "No clasificado",
            "client_email": "", **extra}


class ColaDeHoy(unittest.TestCase):
    def test_ordena_por_urgencia_y_conserva_el_orden_entre_iguales(self) -> None:
        # Cubre: U-11
        casos = [
            _caso("decidir-1", QuoteStatus.REVIEW),
            _caso("emitir", QuoteStatus.READY),
            _caso("respondio", QuoteStatus.WAITING, reply_pending="2026-10-08T06:00:00"),
            _caso("decidir-2", QuoteStatus.REVIEW),
            _caso("cotizando", QuoteStatus.READY, quote_code="26005", code_stage="T"),
        ]
        orden = [q["thread_id"] for q in paginas.cola_de_hoy(casos)]
        self.assertEqual(orden, ["respondio", "emitir", "decidir-1", "decidir-2"])

    def test_cada_fila_tiene_un_boton_con_verbo_y_su_ancla(self) -> None:
        # Cubre: U-11
        decidir = str(paginas.queue_row(_caso("h1", QuoteStatus.REVIEW), 3))
        self.assertIn(">Decidir</a>", decidir)
        self.assertIn("/case?thread_id=h1#decision", decidir)
        self.assertEqual(decidir.count("<a "), 1)
        emitir = str(paginas.queue_row(_caso("h2", QuoteStatus.READY), 1))
        self.assertIn(">Emitir la T</a>", emitir)
        self.assertIn("#emitir-t", emitir)
        self.assertNotIn("secondary", emitir)  # el primero es el botón principal
        self.assertIn("secondary", decidir)

    def test_estado_desconocido_cae_al_final_con_abrir(self) -> None:
        # Cubre: U-11
        self.assertEqual(paginas.accion_de_caso(_caso("h", QuoteStatus.MISSING))[1], "Abrir")

    def test_el_resumen_limita_la_cola_y_avisa_del_resto(self) -> None:
        # Cubre: U-11
        casos = [_caso(f"t{n}", QuoteStatus.REVIEW) for n in range(7)]
        from unittest import mock
        with mock.patch.object(paginas, "all_quotes", return_value=casos),                 mock.patch.object(paginas, "unread_notifications", return_value=[]),                 mock.patch.object(paginas, "sync_status_text", return_value=""),                 mock.patch.object(paginas, "page", side_effect=lambda titulo, cuerpo, mensaje="": str(cuerpo)):
            html = paginas.render_index()
        self.assertEqual(html.count('class="queue-row"'), paginas.COLA_MAXIMA)
        self.assertIn("Hay 2 caso(s) más", html)


if __name__ == "__main__":
    unittest.main()
