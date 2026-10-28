"""Pruebas de los perfiles C1/C2/C3 del controlador y de los correos de T-98 (Gmail simulado; nada toca Google ni lo real).

Se ejecuta con: python -m unittest laboratorio.banco_documental.ejecucion_c1.test_perfiles
"""

from __future__ import annotations

from tests import _isolate  # noqa: F401  (debe ir primero: manda los datos a una carpeta temporal)

import hashlib
import json
import unittest
from pathlib import Path

from laboratorio.banco_documental.ejecucion_c1 import c1, correos_t98
from laboratorio.banco_documental.ejecucion_c1.test_c1 import GmailSim, adjunto, mensaje

DOCS = Path(c1.__file__).resolve().parents[1] / "documentos"
C1_ORIGEN = (c1.MARCA, c1.ASUNTO, c1.ADJUNTO, c1.SHA_PDF, c1.BYTES_PDF, c1.CONSULTA)


def _bytes(nombre: str) -> bytes:
    return (DOCS / nombre).read_bytes()


class PerfilesTest(unittest.TestCase):
    def tearDown(self) -> None:
        c1.usar_perfil("C1")

    def test_70_c1_queda_intacto_al_importar_y_tras_usar_otro_perfil(self) -> None:
        self.assertEqual(C1_ORIGEN[:5], ("[PI-C1-T92-001]", c1.PERFILES["C1"].asunto, "solicitud_01.pdf",
                                         "f47ed4da974e982b09954bfd1b71472b0fefa5859309d7ab1e24c924c92bd844", 4913))
        c1.usar_perfil("C2")
        c1.usar_perfil("C1")
        self.assertEqual((c1.MARCA, c1.ASUNTO, c1.ADJUNTO, c1.SHA_PDF, c1.BYTES_PDF, c1.CONSULTA), C1_ORIGEN)

    def test_71_cada_perfil_coincide_con_su_documento_real_y_con_el_manifiesto(self) -> None:
        manifiesto = json.loads((DOCS / "manifiesto_documentos.json").read_text(encoding="utf-8"))
        texto = json.dumps(manifiesto)
        for caso, perfil in c1.PERFILES.items():
            contenido = _bytes(perfil.adjunto)
            self.assertEqual(hashlib.sha256(contenido).hexdigest(), perfil.sha256, caso)
            self.assertEqual(len(contenido), perfil.bytes_, caso)
            self.assertIn(perfil.sha256, texto, f"{caso}: el SHA-256 no está en el manifiesto del banco")

    def test_72_las_referencias_v2_apuntan_al_mismo_documento(self) -> None:
        for caso in ("C2", "C3"):
            ref = json.loads((DOCS.parent / "referencias_v2" / f"{caso}.referencia.v2.json").read_text(encoding="utf-8"))
            self.assertEqual((ref["documento"]["archivo"], ref["documento"]["sha256"]),
                             (c1.PERFILES[caso].adjunto, c1.PERFILES[caso].sha256), caso)

    def test_73_marcadores_y_carpetas_son_unicos_y_ninguno_contiene_a_otro(self) -> None:
        marcas = [p.marca for p in c1.PERFILES.values()] + [c1.MARCA_T91]
        for a in marcas:
            for b in marcas:
                if a != b:
                    self.assertNotIn(a.lower(), b.lower(), f"{a} contenido en {b}")
        self.assertEqual(len({p.carpeta for p in c1.PERFILES.values()}), 3)
        for p in c1.PERFILES.values():
            self.assertTrue(p.asunto.startswith(p.marca), p.caso)
            self.assertIn("SIN COTIZACIÓN REAL", p.asunto)

    def test_74_cada_perfil_acepta_su_correo_y_su_adjunto(self) -> None:
        for caso, perfil in c1.PERFILES.items():
            c1.usar_perfil(caso)
            parte = adjunto(perfil.adjunto, _bytes(perfil.adjunto))
            h = c1.buscar_en_gmail(GmailSim([mensaje(asunto=perfil.asunto, adjuntos=[parte])]))
            self.assertEqual((h.clase, h.adjunto_ok), ("unico_valido", True), caso)
            self.assertEqual(h.adjunto["sha256"], perfil.sha256, caso)

    def test_75_un_adjunto_de_otro_caso_se_rechaza_aunque_el_asunto_sea_el_correcto(self) -> None:
        for caso, perfil in c1.PERFILES.items():
            c1.usar_perfil(caso)
            for otro, op in c1.PERFILES.items():
                if otro == caso:
                    continue
                parte = adjunto(op.adjunto, _bytes(op.adjunto))
                h = c1.buscar_en_gmail(GmailSim([mensaje(asunto=perfil.asunto, adjuntos=[parte])]))
                self.assertEqual((h.clase, h.adjunto_ok), ("unico_valido", False), f"{caso} con el adjunto de {otro}")

    def test_76_mismo_nombre_y_otro_contenido_se_rechaza_en_c2_y_c3(self) -> None:
        for caso in ("C2", "C3"):
            perfil = c1.usar_perfil(caso)
            contenido = _bytes(perfil.adjunto)
            alterado = bytes([contenido[0] ^ 1]) + contenido[1:]
            h = c1.buscar_en_gmail(GmailSim([mensaje(asunto=perfil.asunto, adjuntos=[adjunto(perfil.adjunto, alterado)])]))
            self.assertFalse(h.adjunto_ok, caso)
            self.assertIn("SHA-256", h.detalle, caso)

    def test_77_el_correo_de_un_caso_no_se_reconoce_con_la_consulta_de_otro(self) -> None:
        c1.usar_perfil("C2")
        sim = GmailSim([mensaje(asunto=c1.PERFILES["C3"].asunto, adjuntos=[adjunto("cantidades_03.xlsx", _bytes("cantidades_03.xlsx"))]),
                        mensaje(id_="g0", hilo="gt0", asunto=c1.PERFILES["C1"].asunto)])
        self.assertEqual(c1.buscar_en_gmail(sim).clase, "ninguno")
        self.assertIn(c1.PERFILES["C2"].marca, sim.consultas[0])

    def test_78_el_perfil_por_defecto_de_la_consola_es_c1_y_la_evidencia_es_propia(self) -> None:
        import argparse  # noqa: F401
        from unittest import mock
        capturado = {}

        def falso_estado(cfg: Path, carpeta: Path) -> int:
            capturado["carpeta"], capturado["marca"] = carpeta, c1.MARCA
            return 0

        with mock.patch.object(c1, "cmd_estado", falso_estado):
            for argumentos, caso in (([], "C1"), (["--perfil", "C2"], "C2"), (["--perfil", "C3"], "C3")):
                c1.main(["estado", *argumentos])
                self.assertEqual(capturado["carpeta"].name, c1.PERFILES[caso].carpeta)
                self.assertEqual(capturado["marca"], c1.PERFILES[caso].marca)


class CorreosTest(unittest.TestCase):
    def test_80_el_asunto_del_correo_es_el_del_perfil(self) -> None:
        for caso, (asunto, _cuerpo) in correos_t98.CORREOS.items():
            self.assertEqual(asunto, c1.PERFILES[caso].asunto)

    def test_81_los_cuerpos_piden_interpretacion_completa_y_preguntas_al_cliente_sin_autorizar_envios(self) -> None:
        for caso, (_asunto, cuerpo) in correos_t98.CORREOS.items():
            bajo = cuerpo.lower()
            for frase in ("tabla de actividades", "preguntas", "como si fuera real", "no se debe enviar ninguna respuesta"):
                self.assertIn(frase, bajo, f"{caso}: falta «{frase}»")
            self.assertNotIn("no se requiere", bajo, caso)
            self.assertIn("prueba de laboratorio", bajo)

    def test_82_los_cuerpos_no_adelantan_la_respuesta_de_la_evaluacion(self) -> None:
        # Evaluación ciega: ni cifras del documento ni sus faltantes/contradicciones aparecen en el cuerpo.
        prohibidas = ("45", "41,8", "6,8", "ubicaci", "anexo", "plazo", "tachad", "manuscrit", "contradic",
                      "33,7", "33.7", "24", "337", "7 predios", "lote 7", "oculta", "borrador_anterior")
        for caso, (_asunto, cuerpo) in correos_t98.CORREOS.items():
            bajo = cuerpo.lower()
            for palabra in prohibidas:
                self.assertNotIn(palabra, bajo, f"{caso}: «{palabra}» adelanta la respuesta")

    def test_83_ni_el_asunto_ni_el_cuerpo_traen_datos_reales(self) -> None:
        for _caso, (asunto, cuerpo) in correos_t98.CORREOS.items():
            self.assertIn("ficticia", (asunto + cuerpo).lower())
            self.assertNotIn("@", cuerpo)


if __name__ == "__main__":
    unittest.main()
