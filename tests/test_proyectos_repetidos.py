"""Cubre: U-19 — hilos del mismo proyecto, agrupados a la vista (sin unir nada)."""
from __future__ import annotations

import re
import unittest
from pathlib import Path
from unittest import mock

from anka.dominio.status import QuoteStatus
from anka.servicios.proyectos_repetidos import (
    MOTIVO_ARCHIVO, MOTIVO_CLIENTE, MOTIVO_NOMBRE, agrupar)
from anka.web import paginas

RAIZ = Path(__file__).resolve().parents[1]


def hilo(thread_id: str, proyecto: str, cliente: str = "no indicado", correo: str = "", archivos: tuple = (),
         codigo: str = "", estado: QuoteStatus = QuoteStatus.REVIEW, fecha: str = "2026-10-01T09:00:00") -> dict:
    return {"thread_id": thread_id, "status": estado, "status_label": "x", "client_name": cliente,
            "client_email": correo, "quote_code": codigo, "changes": [], "services": [], "client_type": "No clasificado",
            "sector": "No clasificado", "first_seen_at": fecha,
            "analysis": {"project_name": proyecto, "attachments": [
                {"filename": a[0], "extracted_chars": a[1]} if isinstance(a, tuple) else {"filename": a}
                for a in archivos]}}


def ids(grupos: list) -> list[list[str]]:
    return [[q["thread_id"] for q in g.miembros] for g in grupos]


class CriterioDeAgrupacion(unittest.TestCase):
    def test_nombre_parecido_y_cliente_sin_identificar_agrupa_y_pide_verificar(self) -> None:
        # Cubre: U-19
        grupos = agrupar([hilo("a", "Mantenimiento y gestión de activos GRB/GRC"),
                          hilo("b", "Mantenimiento y gestión de activos GRB/GRC"),
                          hilo("c", "Volumetría cantera Los Pinos")])
        self.assertEqual(ids(grupos), [["a", "b"], ["c"]])
        self.assertEqual(grupos[0].motivo, MOTIVO_NOMBRE)
        self.assertTrue(grupos[0].verificar)
        self.assertEqual(grupos[1].motivo, "")

    def test_el_mismo_documento_agrupa_aunque_el_nombre_sea_distinto(self) -> None:
        # Cubre: U-19
        grupos = agrupar([hilo("a", "LLANOS 8", "OTACC", archivos=(("E.T Topografia PFV Llanos 8 2.pdf", 41000),)),
                          hilo("b", "Parque quimico", "no indicado", archivos=(("e.t topografia pfv llanos 8 2.PDF", 41000),))])
        self.assertEqual(ids(grupos), [["a", "b"]])
        self.assertEqual(grupos[0].motivo, MOTIVO_ARCHIVO)
        self.assertFalse(grupos[0].verificar)

    def test_un_archivo_generico_no_agrupa_si_el_contenido_es_distinto_o_los_clientes_son_otros(self) -> None:
        # Cubre: U-19
        distinto = agrupar([hilo("a", "Estudio hidrológico", "OTACC", archivos=(("alcance.csv", 100),)),
                            hilo("b", "Levantamiento vial", "OTACC", archivos=(("alcance.csv", 5000),))])
        self.assertEqual(ids(distinto), [["a"], ["b"]])                  # mismo nombre, otro contenido
        clientes = agrupar([hilo("a", "Estudio hidrológico", "OTACC", archivos=(("TDR.pdf", 900),)),
                            hilo("b", "Levantamiento vial", "HidroAndina", archivos=(("TDR.pdf", 900),))])
        self.assertEqual(ids(clientes), [["a"], ["b"]])                  # mismo archivo, clientes distintos
        sin_leer = agrupar([hilo("a", "Estudio hidrológico", archivos=("TDR.pdf",)),
                            hilo("b", "Levantamiento vial", archivos=("TDR.pdf",))])
        self.assertEqual(ids(sin_leer), [["a"], ["b"]])                  # un archivo que no se pudo leer no prueba nada

    def test_las_imagenes_de_las_firmas_no_agrupan(self) -> None:
        # Cubre: U-19
        grupos = agrupar([hilo("a", "Proyecto Norte", archivos=("image001.png", "logo.jpg")),
                          hilo("b", "Estudio de suelos", archivos=("image001.png", "logo.jpg"))])
        self.assertEqual(ids(grupos), [["a"], ["b"]])

    def test_mismo_cliente_y_nombre_parecido_es_motivo_de_cliente(self) -> None:
        # Cubre: U-19
        grupos = agrupar([hilo("a", "LLANOS 8", "OTACC"),
                          hilo("b", "Planta Solar LLANOS 8", "OTACC (razón social no indicada)")])
        self.assertEqual(ids(grupos), [["a", "b"]])
        self.assertEqual(grupos[0].motivo, MOTIVO_CLIENTE)

    def test_palabras_comunes_del_cliente_no_los_hacen_el_mismo(self) -> None:
        # Cubre: U-19
        grupos = agrupar([hilo("a", "Variante Sur", "Consorcio Vías Sur"), hilo("b", "Variante Sur", "Consorcio Norte")])
        self.assertEqual(ids(grupos), [["a"], ["b"]])

    def test_el_mismo_remitente_no_basta(self) -> None:
        # Cubre: U-19
        grupos = agrupar([hilo("a", "Puentes peatonales", "Andrés Muñoz", "a@x.co"),
                          hilo("b", "Cantera Los Pinos", "Andrés Muñoz", "a@x.co")])
        self.assertEqual(ids(grupos), [["a"], ["b"]])

    def test_dos_clientes_distintos_con_nombre_parecido_no_se_agrupan(self) -> None:
        # Cubre: U-19
        grupos = agrupar([hilo("a", "Variante Sur", "Consorcio Vías Sur", "v@sur.co"),
                          hilo("b", "Variante Sur", "HidroAndina", "h@hidro.co")])
        self.assertEqual(ids(grupos), [["a"], ["b"]])

    def test_los_numeros_deben_coincidir(self) -> None:
        # Cubre: U-19
        grupos = agrupar([hilo("a", "Calle 170 espacio público"), hilo("b", "Calle 171 espacio público")])
        self.assertEqual(ids(grupos), [["a"], ["b"]])

    def test_dos_ofertas_con_t_distinta_nunca_se_agrupan(self) -> None:
        # Cubre: U-19
        grupos = agrupar([hilo("a", "Batimetría La Esperanza", codigo="26003", archivos=("a.pdf",)),
                          hilo("b", "Batimetría La Esperanza", codigo="26007", archivos=("a.pdf",))])
        self.assertEqual(ids(grupos), [["a"], ["b"]])

    def test_conserva_el_orden_y_agrupa_en_cadena(self) -> None:
        # Cubre: U-19
        grupos = agrupar([hilo("a", "Proyecto Sol", archivos=(("x.pdf", 10),)), hilo("z", "Otro asunto distinto"),
                          hilo("b", "Cosa distinta", archivos=(("x.pdf", 10), ("y.pdf", 20))),
                          hilo("c", "Algo más", archivos=(("y.pdf", 20),))])
        self.assertEqual(ids(grupos), [["a", "b", "c"], ["z"]])        # a-b por x.pdf y b-c por y.pdf
        self.assertEqual(agrupar([]), [])


class VistaAgrupada(unittest.TestCase):
    CASOS = [hilo("vieja", "Mantenimiento GRB", fecha="2026-09-01T09:00:00"),
             hilo("nueva", "Mantenimiento GRB", fecha="2026-10-08T06:10:00"),
             hilo("sola", "Cantera Los Pinos", fecha="2026-10-05T09:00:00"),
             hilo("listo", "Mantenimiento GRB", estado=QuoteStatus.READY, fecha="2026-10-02T09:00:00")]

    def _pagina(self, funcion, **argumentos) -> str:
        with mock.patch.object(paginas, "all_quotes", return_value=self.CASOS), \
                mock.patch.object(paginas, "unread_notifications", return_value=[]), \
                mock.patch.object(paginas, "sync_status_text", return_value=""), \
                mock.patch.object(paginas, "page", side_effect=lambda titulo, cuerpo, mensaje="": str(cuerpo)):
            return funcion(**argumentos)

    def test_solicitudes_muestra_un_grupo_con_el_hilo_mas_reciente_al_frente(self) -> None:
        # Cubre: U-19
        html = self._pagina(paginas.render_requests)
        self.assertEqual(html.count('class="grupo"'), 1)
        self.assertIn("3 hilos del mismo proyecto", html)
        self.assertIn("verifica que sea el mismo proyecto", html)
        inicio = html.index('class="grupo"')
        grupo = html[inicio:html.index("</section>", inicio)]
        self.assertLess(grupo.index("thread_id=nueva"), grupo.index("thread_id=listo"))   # el más reciente primero
        self.assertLess(grupo.index("thread_id=listo"), grupo.index("thread_id=vieja"))
        self.assertIn('<div class="grupo-resto" hidden>', grupo)
        self.assertIn("agrupar=0", html)                                                 # puede verlos por separado
        encontrado = re.search(r'<section class="grupo" data-fecha="(\d+)" data-urgencia="(\d+)"', html)
        self.assertIsNotNone(encontrado)
        self.assertGreater(int(encontrado.group(1)), 0)                                  # type: ignore[union-attr]

    def test_sin_agrupar_cada_hilo_va_por_separado(self) -> None:
        # Cubre: U-19
        html = self._pagina(paginas.render_requests, agrupar_hilos=False)
        self.assertNotIn('class="grupo"', html)
        self.assertEqual(html.count('<article class="case case-row"'), 4)
        self.assertIn('href="/requests">Agrupar por proyecto', html)

    def test_el_orden_de_la_lista_usa_el_hilo_mas_reciente_del_grupo(self) -> None:
        # Cubre: U-19
        html = self._pagina(paginas.render_requests)
        self.assertLess(html.index("thread_id=nueva"), html.index("thread_id=sola"))     # grupo (8 oct) antes que el suelto

    def test_el_resumen_pone_un_proyecto_en_una_sola_entrada(self) -> None:
        # Cubre: U-19
        html = self._pagina(paginas.render_index)
        self.assertEqual(html.count('class="queue-row"'), 2)                             # el proyecto y el suelto
        self.assertIn("+2 hilo(s) más del mismo proyecto", html)
        self.assertIn("2 caso(s) por atender", html)

    def test_el_script_despliega_filtra_y_ordena_los_grupos(self) -> None:
        # Cubre: U-19
        plantilla = (RAIZ / "anka" / "web" / "templates" / "requests.html").read_text(encoding="utf-8")
        for pieza in ("function abrirGrupo", "grupo.hidden = coinciden === 0", "classList.contains('grupo')"):
            self.assertIn(pieza, plantilla)


if __name__ == "__main__":
    unittest.main()
