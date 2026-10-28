"""El contrato de Work se ejecuta solo: sin skill externa, sin «modo» que comprobar y sin contradecir el acceso a Drive. Cubre: T-62"""

from tests import _isolate  # noqa: F401  (debe ir primero)

import re
import tempfile
import unittest
from pathlib import Path

from anka.cli import paquete_work
from anka.dominio.work_contract import usa_verificacion_por_adjuntos
from anka.servicios import work_import
from tests.test_analisis_conservado import ejemplo_en_revision

RAIZ = Path(__file__).resolve().parents[1]
PROMPT = (RAIZ / "docs" / "PROMPT_WORK_ANKA_V6.txt").read_text(encoding="utf-8")
DOCUMENTOS = sorted((RAIZ / "docs" / "paquete_work").glob("*.md"))
VERSION = "PRECISO_V6.1.3"


class ElPromptEsAutonomo(unittest.TestCase):
    def test_no_exige_ninguna_skill_externa(self) -> None:
        # Cubre: T-62
        self.assertNotIn("anka-cotizaciones-correo", PROMPT)
        self.assertNotRegex(PROMPT, r"(?i)\busa la skill\b|\bskill\s+(?:de|del)\s+anka\b")
        for documento in DOCUMENTOS:
            self.assertNotIn("anka-cotizaciones-correo", documento.read_text(encoding="utf-8"), documento.name)
        self.assertIn("no dependas de ninguna skill", PROMPT)

    def test_no_manda_comprobar_un_modo_inexistente(self) -> None:
        # Cubre: T-62
        self.assertNotRegex(PROMPT, r"(?i)si el modo no es")
        self.assertNotRegex(PROMPT, r"(?i)\bmodo\b\s+(?:no\s+)?(?:es|sea)\s+chatgpt_work")
        self.assertIn("no hay un modo que comprobar", PROMPT)

    def test_el_mensaje_sale_de_las_hojas_del_trabajo_y_no_de_un_evento(self) -> None:
        # Cubre: T-62
        self.assertNotRegex(PROMPT, r"(?i)mensaje real del evento")
        self.assertIn("TRABAJO y VERIFICACION", PROMPT)

    def test_leer_drive_esta_permitido_y_escribirlo_no(self) -> None:
        # Cubre: T-62
        self.assertNotRegex(PROMPT, r"(?m)^- Usar Drive\b")
        self.assertRegex(PROMPT, r"(?m)^- Escribir en Drive")
        self.assertIn("Leer PENDIENTES y REFERENCIA_ANKA_V1 sí está permitido", PROMPT)
        instrucciones = (RAIZ / "docs" / "paquete_work" / "INSTRUCCIONES_UNA_EJECUCION.md").read_text(encoding="utf-8")
        self.assertIn("Escribir, crear, mover, renombrar o borrar cualquier archivo o carpeta de Drive", instrucciones)

    def test_conserva_lo_que_protege_al_negocio(self) -> None:
        # Cubre: T-62
        for frase in ("[ANKA_DASHBOARD_JSON_V1]", "question_audit", "attachments", "size_bytes",
                      "BORRAR ESTA SECCION", "thread_id", "message_id"):
            self.assertIn(frase, PROMPT, frase)
        self.assertRegex(PROMPT, r"(?i)nunca[^\n]*envi")
        self.assertIn("sin enviarlo", PROMPT)
        self.assertIn("una persona lo revise", PROMPT)


class LaVersionEsCoherente(unittest.TestCase):
    def test_ningun_lugar_contractual_conserva_la_version_anterior(self) -> None:
        # Cubre: T-62
        fuentes = [RAIZ / "docs" / "PROMPT_WORK_ANKA_V6.txt", RAIZ / "anka" / "cli" / "paquete_work.py", *DOCUMENTOS]
        for fuente in fuentes:
            texto = fuente.read_text(encoding="utf-8")
            self.assertNotIn("V6.1.2", texto, fuente.name)
        self.assertTrue(PROMPT.rstrip().endswith("FIN_PROMPT_ANKA_V6.1.3"))

    def test_cabe_en_el_limite_de_longitud(self) -> None:
        # Cubre: T-62
        self.assertLess(len(PROMPT), 14500)

    def test_el_paquete_se_construye_se_verifica_y_declara_la_version(self) -> None:
        # Cubre: T-62
        with tempfile.TemporaryDirectory() as temp:
            carpeta = Path(temp)
            manifiesto = paquete_work.construir(carpeta)
            self.assertEqual(paquete_work.verificar_paquete(carpeta), [])
            self.assertEqual(manifiesto["prompt"]["version"], VERSION)
            self.assertEqual(manifiesto["prompt"]["file"], f"{VERSION}.txt")
            self.assertEqual((carpeta / f"{VERSION}.txt").read_bytes(), paquete_work.leer(paquete_work.PROMPT))
            for entrada in manifiesto["files"]:
                nombre = entrada["path"] if isinstance(entrada, dict) else entrada
                if nombre.endswith(".json") and nombre.startswith("ejemplo_"):
                    self.assertIn(f'"skill_version": "{VERSION}"', (carpeta / nombre).read_text(encoding="utf-8"))
            self.assertRegex(manifiesto["paquete_version"], r"^[0-9a-f]{12}$")
            self.assertEqual(manifiesto["paquete_version"], paquete_work.version_del_paquete())

    def test_el_importador_acepta_la_version_sin_cambiar_el_esquema(self) -> None:
        # Cubre: T-62
        datos = ejemplo_en_revision(PROMPT)
        self.assertEqual(datos["skill_version"], VERSION)
        self.assertTrue(usa_verificacion_por_adjuntos(datos))
        self.assertEqual(work_import._work_analysis(datos)["work_skill_version"], VERSION)
        esquema = paquete_work.ESQUEMA.read_text(encoding="utf-8")
        self.assertNotIn("V6.1", esquema)
        self.assertFalse(re.search(r"anka-cotizaciones", esquema))


if __name__ == "__main__":
    unittest.main()
