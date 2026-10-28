"""T-59: el paquete contractual se reconstruye igual desde Git, sin importar los finales de línea del entorno.

Solo archivos temporales: no toca Drive, Gmail, SQLite ni servicios.

Cubre: T-59
"""

from tests import _isolate  # noqa: F401  (debe ir primero)

import fnmatch
import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from anka.cli import paquete_work

RAIZ = Path(__file__).resolve().parents[1]
LF, CRLF = b"\n", b"\r\n"


def _convertir(origen: Path, destino: Path, final: bytes) -> None:
    datos = origen.read_bytes().replace(CRLF, LF)
    destino.parent.mkdir(parents=True, exist_ok=True)
    destino.write_bytes(datos.replace(LF, final))


def _entradas(raiz: Path, final: bytes) -> dict[str, Path]:
    """Copia los archivos fuente del paquete con ese final de línea (como los dejaría un checkout) y da sus rutas."""
    esquema, prompt, plantillas = raiz / "esquema.json", raiz / "prompt.txt", raiz / "plantillas"
    _convertir(paquete_work.ESQUEMA, esquema, final)
    _convertir(paquete_work.PROMPT, prompt, final)
    for nombre in paquete_work.DE_PLANTILLA:
        _convertir(paquete_work.PLANTILLAS / nombre, plantillas / nombre, final)
    return {"ESQUEMA": esquema, "PROMPT": prompt, "PLANTILLAS": plantillas}


def _construir(raiz: Path, final: bytes) -> tuple[dict, Path]:
    rutas = _entradas(raiz / "fuente", final)
    destino = raiz / "paquete"
    with patch.multiple(paquete_work, **rutas):
        return paquete_work.construir(destino), destino


def _archivos(carpeta: Path) -> dict[str, bytes]:
    return {p.name: p.read_bytes() for p in sorted(carpeta.iterdir())}


class Reproducibilidad(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.raiz = Path(self.temp.name)

    def construir(self, nombre, final):
        return _construir(self.raiz / nombre, final)

    def test_lf_y_crlf_producen_el_mismo_paquete_byte_a_byte(self):
        manifiesto_lf, carpeta_lf = self.construir("lf", LF)
        manifiesto_crlf, carpeta_crlf = self.construir("crlf", CRLF)
        self.assertEqual(manifiesto_lf, manifiesto_crlf)
        self.assertEqual(_archivos(carpeta_lf), _archivos(carpeta_crlf))
        self.assertEqual(paquete_work.verificar_paquete(carpeta_crlf), [])

    def test_un_checkout_mixto_tambien_da_lo_mismo(self):
        manifiesto_lf, _ = self.construir("lf", LF)
        rutas = _entradas(self.raiz / "mixto" / "fuente", LF)
        rutas["ESQUEMA"].write_bytes(rutas["ESQUEMA"].read_bytes().replace(LF, CRLF))      # solo el esquema en CRLF
        with patch.multiple(paquete_work, **rutas):
            mixto = paquete_work.construir(self.raiz / "mixto" / "paquete")
            self.assertEqual(paquete_work.version_del_paquete(), manifiesto_lf["paquete_version"])
        self.assertEqual(mixto, manifiesto_lf)

    def test_la_version_sin_escribir_es_la_del_manifiesto_con_cualquier_final(self):
        for nombre, final in (("a", LF), ("b", CRLF)):
            rutas = _entradas(self.raiz / nombre, final)
            with patch.multiple(paquete_work, **rutas):
                version = paquete_work.version_del_paquete()
                manifiesto = paquete_work.construir(self.raiz / nombre / "paquete")
            self.assertEqual(version, manifiesto["paquete_version"])

    def test_el_arbol_real_da_la_misma_version_que_una_copia_en_lf_y_en_crlf(self):
        manifiesto_lf, _ = self.construir("lf", LF)
        self.assertEqual(paquete_work.version_del_paquete(), manifiesto_lf["paquete_version"])

    def test_las_huellas_del_manifiesto_son_las_del_texto_en_lf(self):
        manifiesto, carpeta = self.construir("crlf", CRLF)
        for ficha in manifiesto["files"]:
            datos = (carpeta / ficha["path"]).read_bytes()
            self.assertNotIn(CRLF, datos)
            self.assertEqual(hashlib.sha256(datos).hexdigest(), ficha["sha256"])
        esquema = paquete_work.ESQUEMA.read_bytes().replace(CRLF, LF)
        self.assertEqual(manifiesto["schema_sha256"], hashlib.sha256(esquema).hexdigest())

    def test_un_paquete_ya_publicado_se_verifica_con_los_bytes_que_se_publicaron(self):
        # La integridad compara cada archivo con SU manifiesto: un paquete publicado con esquema en CRLF no cambia.
        _, carpeta = self.construir("lf", LF)
        esquema = carpeta / "contrato_work.schema.json"
        esquema.write_bytes(esquema.read_bytes().replace(LF, CRLF))
        manifiesto = json.loads((carpeta / "manifest.json").read_text(encoding="utf-8"))
        ficha = next(f for f in manifiesto["files"] if f["path"] == "contrato_work.schema.json")
        ficha["sha256"], ficha["size_bytes"] = hashlib.sha256(esquema.read_bytes()).hexdigest(), esquema.stat().st_size
        (carpeta / "manifest.json").write_text(json.dumps(manifiesto), encoding="utf-8")
        self.assertEqual(paquete_work._problemas_de_archivos(carpeta, manifiesto), [])


class ReglasDeGit(unittest.TestCase):
    """Los archivos fuente del paquete tienen reglas explícitas de final de línea (`.gitattributes`)."""

    def reglas(self):
        lineas = (RAIZ / ".gitattributes").read_text(encoding="utf-8").splitlines()
        reglas = []
        for linea in lineas:
            partes = linea.split()
            if len(partes) >= 2 and not linea.lstrip().startswith("#"):
                reglas.append((partes[0], set(partes[1:])))
        return reglas

    def test_cada_fuente_del_paquete_esta_fijada_a_lf(self):
        fuentes = [paquete_work.ESQUEMA, paquete_work.PROMPT,
                   *(paquete_work.PLANTILLAS / n for n in paquete_work.DE_PLANTILLA)]
        for fuente in fuentes:
            relativa = fuente.relative_to(RAIZ).as_posix()
            with self.subTest(fuente=relativa):
                atributos = set()
                for patron, valores in self.reglas():
                    if fnmatch.fnmatch(relativa, patron.lstrip("/")) or relativa == patron.lstrip("/"):
                        atributos |= valores
                self.assertIn("eol=lf", atributos)

    def test_el_arbol_de_trabajo_actual_esta_en_lf(self):
        for fuente in (paquete_work.ESQUEMA, paquete_work.PROMPT, *paquete_work.PLANTILLAS.glob("*.md")):
            with self.subTest(fuente=fuente.name):
                self.assertNotIn(CRLF, fuente.read_bytes())

    def test_el_contenido_semantico_del_esquema_no_depende_del_final_de_linea(self):
        crudo = paquete_work.ESQUEMA.read_bytes()
        self.assertEqual(json.loads(crudo), json.loads(crudo.replace(LF, CRLF)))


if __name__ == "__main__":
    unittest.main()
