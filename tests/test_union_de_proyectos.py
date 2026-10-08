"""Cubre: U-20 — unir o separar un hilo del mismo proyecto, con confirmación."""

from tests import _isolate  # noqa: F401  (debe ir primero)

import io
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from anka.dominio.status import QuoteStatus
from anka.infra import settings, store
from anka.servicios import quotes
from anka.servicios.proyectos_repetidos import agrupar
from anka.web import pantalla_caso, paginas, seguridad
from anka.web import server as web
from tests.test_app import complete_analysis, test_config

ARCHIVO = {"filename": "E.T Llanos 8.pdf", "status": "extraido", "extracted_chars": 41000}


def analisis(proyecto: str, **extra) -> dict:
    base = complete_analysis()
    base.update(project_name=proyecto, attachments=[ARCHIVO], received_at="2026-10-01T09:00:00-05:00")
    base.update(extra)
    return base


# Cubre: U-20
class UnionDeProyectosTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        for p in (patch.object(settings, "DATA_DIR", Path(self._tmp.name)),
                  patch("anka.web.paginas.load_config", return_value=test_config()),
                  patch("anka.web.pantalla_caso.load_config", return_value=test_config())):
            p.start()
            self.addCleanup(p.stop)
        db = store.open_db(Path(self._tmp.name))
        store.save(db, "t-a", "m-a", QuoteStatus.REVIEW_PENDING, "a@x.co", analisis("LLANOS 8"))
        store.save(db, "t-b", "m-b", QuoteStatus.REVIEW_PENDING, "b@x.co", analisis("Planta Solar LLANOS 8"))
        store.save(db, "t-c", "m-c", QuoteStatus.REVIEW_PENDING, "c@x.co",
                   analisis("Cantera Los Pinos", attachments=[]))
        db.close()

    def grupos(self) -> list[list[str]]:
        # all_quotes devuelve el más reciente primero: se compara sin depender del orden
        return sorted(sorted(q["thread_id"] for q in g.miembros)
                      for g in agrupar(quotes.all_quotes(), quotes.separated_project_pairs()))

    def request(self, path: str, body: str) -> dict:
        datos = (body + f"&csrf_token={seguridad.CSRF_TOKEN}").encode()
        handler = web.ReviewHandler.__new__(web.ReviewHandler)
        sent: dict = {}
        handler.headers = {"Host": "127.0.0.1:1", "Content-Length": str(len(datos))}
        handler.path = path
        handler.rfile = io.BytesIO(datos)
        handler._send_html = lambda content, status=200: sent.update(status=status, page=content)
        handler._redirect = lambda location: sent.update(status=303, location=location)
        handler.do_POST()
        return sent

    def test_la_base_tiene_la_tabla_de_pares_separados(self) -> None:
        db = store.open_db(Path(self._tmp.name))
        tablas = {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        db.close()
        self.assertIn("project_dismissals", tablas)

    def test_el_caso_avisa_del_hilo_parecido_con_sus_dos_botones_y_la_confirmacion(self) -> None:
        pagina = pantalla_caso.render_case("t-a")
        self.assertIn("Parece el mismo proyecto", pagina)
        self.assertIn("Planta Solar LLANOS 8", pagina)
        self.assertIn("mismo archivo", pagina)
        self.assertIn('action="/project-merge"', pagina)
        self.assertIn('action="/project-separate"', pagina)
        self.assertIn("Unir a este caso", pagina)
        self.assertIn("No es el mismo proyecto", pagina)
        self.assertIn("data-confirm", pagina[pagina.index("Unir a este caso") - 700:pagina.index("Unir a este caso")])
        self.assertNotIn("Parece el mismo proyecto", pantalla_caso.render_case("t-c"))      # sin parecidos, sin aviso

    def test_con_muchos_parecidos_el_aviso_va_plegado_y_con_pocos_abierto(self) -> None:
        abierto = pantalla_caso.render_case("t-a")
        self.assertIn('<details class="section project-suggestion" id="mismo-proyecto" open>', abierto)    # 1 parecido
        db = store.open_db(Path(self._tmp.name))
        for hilo in ("t-d", "t-e"):
            store.save(db, hilo, "m-" + hilo, QuoteStatus.REVIEW_PENDING, hilo + "@x.co", analisis("LLANOS 8"))
        db.close()
        plegado = pantalla_caso.render_case("t-a")                                                         # 3 parecidos
        self.assertIn('<details class="section project-suggestion" id="mismo-proyecto">', plegado)
        self.assertIn("3 hilo(s) parecido(s)", plegado)

    def test_unir_deja_el_otro_en_descartadas_con_huella_en_los_dos_casos(self) -> None:
        mensaje = quotes.merge_project_thread("t-a", "t-b")
        self.assertIn("Descartadas", mensaje)
        otro = quotes.get_quote("t-b")
        self.assertEqual(otro["status"], QuoteStatus.IGNORED)
        self.assertEqual(otro["analysis"]["merged_into"], "t-a")
        self.assertEqual(quotes.get_quote("t-a")["status"], QuoteStatus.REVIEW_PENDING)    # el de este caso sigue igual
        self.assertTrue(any(e["kind"] == "project_merged" for e in quotes.thread_events("t-a")))
        self.assertTrue(any(e["kind"] == "merged" for e in quotes.thread_events("t-b")))
        self.assertEqual(self.grupos(), [["t-a"], ["t-c"]])
        self.assertNotIn("Parece el mismo proyecto", pantalla_caso.render_case("t-a"))

    def test_no_se_une_un_hilo_que_avanzo_y_el_aviso_lo_explica(self) -> None:
        db = store.open_db(Path(self._tmp.name))
        db.execute("UPDATE quotes SET quote_code='26009' WHERE thread_id='t-b'")
        db.commit()
        db.close()
        with self.assertRaisesRegex(ValueError, "código de oferta"):
            quotes.merge_project_thread("t-a", "t-b")
        self.assertNotEqual(quotes.get_quote("t-b")["status"], QuoteStatus.IGNORED)
        pagina = pantalla_caso.render_case("t-a")
        self.assertIn("No se puede unir: ya tiene código de oferta", pagina)
        self.assertNotIn('action="/project-merge"', pagina)
        self.assertIn('action="/project-separate"', pagina)                                # separar sigue disponible

    def test_validaciones_de_la_union(self) -> None:
        with self.assertRaisesRegex(ValueError, "otro hilo"):
            quotes.merge_project_thread("t-a", "t-a")
        with self.assertRaisesRegex(ValueError, "ya no existe"):
            quotes.merge_project_thread("t-a", "no-existe")

    def test_separar_se_recuerda_y_no_vuelve_a_sugerirse_en_ningun_caso(self) -> None:
        self.assertEqual(self.grupos(), [["t-a", "t-b"], ["t-c"]])
        quotes.separate_project_thread("t-a", "t-b")
        quotes.separate_project_thread("t-b", "t-a")                                       # repetir no duplica ni falla
        self.assertEqual(quotes.separated_project_pairs(), frozenset({frozenset({"t-a", "t-b"})}))
        self.assertEqual(self.grupos(), [["t-a"], ["t-b"], ["t-c"]])
        for hilo in ("t-a", "t-b"):
            self.assertNotIn("Parece el mismo proyecto", pantalla_caso.render_case(hilo))
            self.assertTrue(any(e["kind"] == "project_separated" for e in quotes.thread_events(hilo)))
        with patch.object(paginas, "unread_notifications", return_value=[]):
            self.assertNotIn('class="grupo"', paginas.render_requests())                  # ni en la lista agrupada
        self.assertEqual(quotes.get_quote("t-b")["status"], QuoteStatus.REVIEW_PENDING)    # separar no cambia el caso

    def test_las_rutas_exigen_csrf_y_vuelven_al_caso_con_el_mensaje(self) -> None:
        with self.assertLogs("anka.web.server", "WARNING"):
            denegado = self.request("/project-merge", "thread_id=t-a&other_thread_id=t-b&csrf_token=malo")
        self.assertEqual(denegado["status"], 403)
        self.assertEqual(quotes.get_quote("t-b")["status"], QuoteStatus.REVIEW_PENDING)
        separado = self.request("/project-separate", "thread_id=t-a&other_thread_id=t-b")
        self.assertEqual(separado["status"], 303)
        self.assertTrue(separado["location"].startswith("/case?thread_id=t-a&message="))
        unido = self.request("/project-merge", "thread_id=t-a&other_thread_id=t-c")         # t-c ya no se parece: se une igual si se pide
        self.assertEqual(unido["status"], 303)


if __name__ == "__main__":
    unittest.main()
