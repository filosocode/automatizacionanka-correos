"""Paquete de referencia del contrato para la tarea Work de la cola (T-54, D-158).

SQLite temporal y Drive simulado; ningún dato real.

Cubre: T-54
"""

from tests import _isolate  # noqa: F401  (debe ir primero)

import copy
import json
import re
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from anka.cli import paquete_work
from anka.cli.paquete_work import (
    CARPETA_EN_DRIVE, OBLIGATORIOS, construir, ejemplos, incoherencias, publicar, verificar_paquete,
)
from anka.dominio.clasificacion import CLASIFICACIONES
from anka.dominio.errores import ErrorDeAnka
from anka.dominio.status import QuoteStatus
from anka.dominio.tolerancia_work import normalizar_analisis
from anka.dominio.work_contract import load_schema, validate
from anka.infra import settings
from anka.servicios import quotes, work_import
from tests.test_work_sync import b64

PROMPT = paquete_work.PROMPT.read_text(encoding="utf-8")
CARPETA_RAIZ = "raiz-lab"


class _Ejecutable:
    def __init__(self, valor):
        self.valor = valor

    def execute(self):
        return self.valor() if callable(self.valor) else self.valor


class DriveDeArchivos:
    """Drive simulado con carpetas y archivos de texto: list/create/update con su `appProperties`."""

    def __init__(self):
        self.elementos: dict[str, dict] = {}
        self.escrituras: list[tuple[str, str]] = []

    def files(self):
        return self

    def list(self, q, fields=None, pageSize=None, pageToken=None):
        padre = q.split("'")[1]
        nombre = re.search(r"name = '((?:[^'\\]|\\.)*)'", q).group(1)
        solo_carpetas = "mimeType" in q

        def listar():
            return {"files": [dict(e) for e in self.elementos.values()
                              if padre in e["parents"] and e["name"] == nombre and not e["trashed"]
                              and (not solo_carpetas or e["mimeType"] == "application/vnd.google-apps.folder")]}
        return _Ejecutable(listar)

    def _guardar(self, id_, body, media):
        e = self.elementos.setdefault(id_, {"id": id_, "parents": [], "trashed": False, "mimeType": "", "name": "",
                                            "appProperties": {}, "bytes": b"", "webViewLink": f"https://drive/{id_}"})
        e.update({k: v for k, v in (body or {}).items() if k in ("name", "parents", "mimeType")})
        if body and "appProperties" in body:
            e["appProperties"] = dict(body["appProperties"])
        if media is not None:
            e["bytes"] = media.getbytes(0, media.size())
        return e

    def create(self, body, fields=None, media_body=None):
        def crear():
            id_ = f"id-{len(self.elementos) + 1}"
            self.escrituras.append(("create", body["name"]))
            return dict(self._guardar(id_, body, media_body))
        return _Ejecutable(crear)

    def update(self, fileId, body=None, media_body=None, fields=None):
        def actualizar():
            self.escrituras.append(("update", self.elementos[fileId]["name"]))
            return dict(self._guardar(fileId, body, media_body))
        return _Ejecutable(actualizar)

    def archivos_de(self, carpeta):
        return {e["name"]: e for e in self.elementos.values() if carpeta in e["parents"] and not e["trashed"]
                and e["mimeType"] != "application/vnd.google-apps.folder"}


class Base(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.carpeta = Path(self.temp.name) / "paquete"
        self.manifiesto = construir(self.carpeta)


class ConstruccionTests(Base):
    def test_es_determinista_misma_version_y_mismos_bytes(self):
        otra = Path(self.temp.name) / "otra"
        manifiesto = construir(otra)
        self.assertEqual(manifiesto, self.manifiesto)
        for ruta in OBLIGATORIOS:
            self.assertEqual((otra / ruta).read_bytes(), (self.carpeta / ruta).read_bytes(), ruta)

    def test_trae_todos_los_archivos_y_el_manifiesto_declara_version_origen_y_huellas(self):
        for ruta in OBLIGATORIOS:
            self.assertTrue((self.carpeta / ruta).is_file(), ruta)
        m = self.manifiesto
        self.assertEqual((m["contract"]["name"], m["prompt"]["version"]), ("ANKA_DASHBOARD_JSON_V1", "PRECISO_V6.1.3"))
        self.assertEqual(m["contract"]["classification_enum"], list(CLASIFICACIONES))
        self.assertEqual(m["contract"]["required"], load_schema()["required"])
        self.assertEqual(len(m["paquete_version"]), 12)
        self.assertIn("spec/contrato_work.schema.json", json.dumps(m["origen"]))
        self.assertEqual({f["path"] for f in m["files"]}, set(OBLIGATORIOS) - {"manifest.json"})
        self.assertIn(m["paquete_version"], (self.carpeta / "00_LEEME.md").read_text(encoding="utf-8"))
        self.assertNotIn("{{", (self.carpeta / "00_LEEME.md").read_text(encoding="utf-8"))

    def test_el_esquema_y_el_prompt_son_copias_exactas_de_las_fuentes_canonicas(self):
        import hashlib
        self.assertEqual((self.carpeta / "contrato_work.schema.json").read_bytes(), paquete_work.leer(paquete_work.ESQUEMA))
        self.assertEqual((self.carpeta / "PRECISO_V6.1.3.txt").read_bytes(), paquete_work.leer(paquete_work.PROMPT))
        self.assertEqual(self.manifiesto["schema_sha256"],
                         hashlib.sha256(paquete_work.leer(paquete_work.ESQUEMA)).hexdigest())
        self.assertEqual(self.manifiesto["prompt"]["sha256"],
                         hashlib.sha256(paquete_work.leer(paquete_work.PROMPT)).hexdigest())

    def test_es_autonomo_sin_zip_ni_rutas_locales(self):
        for ruta in OBLIGATORIOS:
            if ruta == "PRECISO_V6.1.3.txt":
                continue
            texto = (self.carpeta / ruta).read_text(encoding="utf-8")
            for prohibido in paquete_work.PROHIBIDO_EN_TEXTO:
                self.assertNotIn(prohibido, texto, f"{ruta}: {prohibido}")

    def test_las_instrucciones_obligan_a_detenerse_si_el_paquete_no_se_puede_verificar(self):
        texto = (self.carpeta / "INSTRUCCIONES_UNA_EJECUCION.md").read_text(encoding="utf-8")
        for frase in ("Completo", "Íntegro", "Vigente", "contradicciones", "DETENTE", "no crees borrador",
                      "un único trabajo", "PENDIENTES", "No envíes", "ANKA 01"):
            self.assertIn(frase.lower(), texto.lower(), frase)
        t52 = (self.carpeta / "T-52_CLASIFICACION.md").read_text(encoding="utf-8")
        for clase in CLASIFICACIONES:
            self.assertIn(clase, t52)
        for obligatorio in load_schema()["required"]:
            self.assertIn(obligatorio, t52)
        self.assertIn("REVISION_MANUAL", t52.split("Ante la duda")[1])

    def test_no_modifica_el_prompt_ni_el_esquema_del_repositorio(self):
        self.assertIn("PRECISO_V6.1.3", PROMPT)
        self.assertEqual(load_schema()["properties"]["classification"]["enum"], list(CLASIFICACIONES))


class EjemplosTests(Base):
    def test_hay_un_ejemplo_por_clasificacion_y_cumple_el_esquema_sin_correcciones(self):
        for clase, datos in ejemplos(PROMPT).items():
            with self.subTest(clase=clase):
                self.assertEqual(datos["classification"], clase)
                self.assertEqual(validate(datos), [])
                self.assertTrue(set(load_schema()["required"]) <= set(datos))
                self.assertEqual(datos["skill_version"], "PRECISO_V6.1.3")
                copia = copy.deepcopy(datos)
                normalizar_analisis(copia)
                self.assertNotIn("_correcciones_de_work", copia)
                self.assertNotIn("_avisos_del_puente", copia)
                escrito = json.loads((self.carpeta / f"ejemplo_{clase}.json").read_text(encoding="utf-8"))
                self.assertEqual(escrito, datos)

    def test_los_ejemplos_son_ficticios_y_no_traen_texto_para_el_cliente(self):
        for datos in ejemplos(PROMPT).values():
            self.assertNotIn("draft_reply", datos)
            self.assertTrue(datos["client_email"].endswith(("@ejemplo.com", "@ejemplo-ingenieria.com")))
        for clase in ("NO_SOLICITUD", "REVISION_MANUAL"):
            self.assertEqual(ejemplos(PROMPT)[clase]["activities"], [])

    def test_el_ejemplo_oficial_sin_bloque_se_rechaza(self):
        with self.assertRaises(ErrorDeAnka):
            ejemplos("un prompt sin ejemplo")


class CoherenciaSemanticaTests(Base):
    """El esquema no ve contradicciones de sentido: un dato recibido no puede seguir como faltante (piloto 5B)."""

    def test_los_cuatro_ejemplos_son_coherentes_y_el_paquete_lo_comprueba(self):
        for clase, datos in ejemplos(PROMPT).items():
            self.assertEqual(incoherencias(datos), [], clase)
        self.assertEqual(verificar_paquete(self.carpeta), [])

    def test_el_seguimiento_no_vuelve_a_preguntar_lo_que_el_cliente_ya_respondio(self):
        seguimiento = ejemplos(PROMPT)["SEGUIMIENTO"]
        self.assertIn("sistema de referencia", seguimiento["summary"])
        self.assertEqual(seguimiento["missing_data"], [])
        self.assertNotEqual(seguimiento["status"], "Requiere aclaraciones del cliente")
        self.assertTrue(all(q["disposition"] != "ask_client" for q in seguimiento["question_audit"]))
        self.assertTrue(all(e["status"] != "absent" and e["blocking"] is False
                            for e in seguimiento["evidence_register"]))
        self.assertTrue(all(a["estado"] != "pendiente" for a in seguimiento["activities"]))
        referencia = [e for e in seguimiento["evidence_register"] if "referencia" in e["topic"].lower()]
        self.assertEqual([e["status"] for e in referencia], ["explicit"])
        self.assertTrue(any("MAGNA-SIRGAS" in str(f.get("value")) for f in seguimiento["fields"]))
        self.assertNotIn("Falta el sistema de referencia", json.dumps(seguimiento, ensure_ascii=False))

    def test_el_ejemplo_original_defectuoso_se_detecta(self):
        # la forma que Work señaló: el resumen dice que el cliente respondió y el resto sigue pidiendo el dato
        malo = {**copy.deepcopy(ejemplos(PROMPT)["SOLICITUD_NUEVA"]), "classification": "SEGUIMIENTO",
                "summary": "El cliente responde con el sistema de referencia de entrega pedido antes."}
        self.assertTrue(any("sigue pidiéndolo" in p for p in incoherencias(malo)))

    def test_otras_contradicciones_equivalentes_se_detectan(self):
        base = ejemplos(PROMPT)["SOLICITUD_NUEVA"]
        explicito = copy.deepcopy(base)
        explicito["evidence_register"][1].update(status="explicit", blocking=False)
        self.assertTrue(any("explícito" in p for p in incoherencias(explicito)))
        cerrado = {**copy.deepcopy(base), "status": "Alcance completo"}
        self.assertTrue(any("no admite preguntas" in p for p in incoherencias(cerrado)))
        sin_preguntas = {**copy.deepcopy(base), "question_audit": []}
        self.assertTrue(any("ninguna pregunta" in p for p in incoherencias(sin_preguntas)))
        descartada = {**copy.deepcopy(ejemplos(PROMPT)["SEGUIMIENTO"]), "status": "Descartada"}
        self.assertTrue(incoherencias(descartada))
        no_solicitud = {**copy.deepcopy(ejemplos(PROMPT)["NO_SOLICITUD"]), "status": "En análisis"}
        self.assertTrue(incoherencias(no_solicitud))
        revision = {**copy.deepcopy(ejemplos(PROMPT)["REVISION_MANUAL"]), "activities": base["activities"]}
        self.assertTrue(incoherencias(revision))

    def test_un_paquete_con_un_ejemplo_contradictorio_no_pasa_la_verificacion(self):
        import hashlib
        malo = {**copy.deepcopy(ejemplos(PROMPT)["SOLICITUD_NUEVA"]), "classification": "SEGUIMIENTO",
                "summary": "El cliente responde con el sistema de referencia de entrega pedido antes."}
        datos = (json.dumps(malo, ensure_ascii=False, indent=2) + chr(10)).encode()
        (self.carpeta / "ejemplo_SEGUIMIENTO.json").write_bytes(datos)
        mani = self.carpeta / "manifest.json"
        m = json.loads(mani.read_text(encoding="utf-8"))
        for f in m["files"]:
            if f["path"] == "ejemplo_SEGUIMIENTO.json":
                f.update(sha256=hashlib.sha256(datos).hexdigest(), size_bytes=len(datos))
        mani.write_text(json.dumps(m), encoding="utf-8")
        self.assertTrue(any("sigue pidiéndolo" in p for p in verificar_paquete(self.carpeta)))


class ImportacionDeEjemplosTests(Base):
    def setUp(self):
        super().setUp()
        for parche in (patch.object(settings, "DATA_DIR", Path(self.temp.name)),
                       patch.object(work_import, "programar_actualizacion_de_hoja", MagicMock())):
            parche.start()
            self.addCleanup(parche.stop)

    def importar(self, clase):
        datos = ejemplos(PROMPT)[clase]
        work_import.dashboard_upsert(b64(json.dumps(datos)))
        return quotes.get_quote(datos["thread_id"])

    def test_solicitud_nueva_y_seguimiento_avanzan_a_actividades(self):
        for clase in ("SOLICITUD_NUEVA", "SEGUIMIENTO"):
            caso = self.importar(clase)
            self.assertEqual(len(caso["analysis"]["activities"]), 1, clase)
            self.assertEqual(caso["analysis"]["classification"], clase)

    def test_no_solicitud_descarta_sin_actividades(self):
        caso = self.importar("NO_SOLICITUD")
        self.assertEqual(caso["status"], QuoteStatus.IGNORED.value)
        self.assertEqual(caso["analysis"]["activities"], [])
        self.assertFalse(caso["analysis"]["is_quote_request"])

    def test_revision_manual_queda_en_revision_sin_actividades_ni_respuesta(self):
        caso = self.importar("REVISION_MANUAL")
        self.assertEqual(caso["status"], QuoteStatus.REVIEW.value)
        self.assertEqual((caso["analysis"]["activities"], caso["analysis"]["work_reply"]), ([], {}))
        self.assertEqual(caso["analysis"]["classification"], "REVISION_MANUAL")

    def test_el_seguimiento_corregido_queda_listo_para_revision_sin_pendientes(self):
        self.importar("SOLICITUD_NUEVA")
        caso = self.importar("SEGUIMIENTO")
        analisis = caso["analysis"]
        self.assertEqual(caso["status"], QuoteStatus.READY_REVIEW.value)
        self.assertEqual(analisis["other_questions"], [])
        self.assertEqual(analisis["question_quality_warnings"], [])
        self.assertTrue(analisis["is_quote_request"])

    def test_ningun_ejemplo_deja_avisos_de_formato(self):
        for clase in CLASIFICACIONES:
            caso = self.importar(clase)
            self.assertEqual([a for a in caso["analysis"].get("correcciones_de_work", [])], [], clase)


class VerificacionTests(Base):
    def test_un_paquete_recien_construido_pasa(self):
        self.assertEqual(verificar_paquete(self.carpeta), [])

    def test_un_archivo_alterado_no_pasa(self):
        with (self.carpeta / "T-52_CLASIFICACION.md").open("a", encoding="utf-8") as f:
            f.write("\nSOLICITUD_NUEVA ya no existe.")
        self.assertTrue(any("T-52_CLASIFICACION.md" in p for p in verificar_paquete(self.carpeta)))

    def test_un_archivo_faltante_no_pasa(self):
        (self.carpeta / "ejemplo_NO_SOLICITUD.json").unlink()
        self.assertIn("Falta el archivo ejemplo_NO_SOLICITUD.json.", verificar_paquete(self.carpeta))

    def test_sin_manifiesto_o_manifiesto_roto_no_pasa(self):
        (self.carpeta / "manifest.json").write_text("{", encoding="utf-8")
        self.assertEqual(verificar_paquete(self.carpeta), ["Falta manifest.json o no es JSON válido."])
        (self.carpeta / "manifest.json").unlink()
        self.assertTrue(verificar_paquete(self.carpeta))

    def test_un_manifiesto_incompleto_no_pasa(self):
        ruta = self.carpeta / "manifest.json"
        m = json.loads(ruta.read_text(encoding="utf-8"))
        m["files"] = [f for f in m["files"] if f["path"] != "FORMATO_DE_RETORNO.md"]
        ruta.write_text(json.dumps(m), encoding="utf-8")
        self.assertIn("El manifiesto no lista FORMATO_DE_RETORNO.md.", verificar_paquete(self.carpeta))

    def reescribir_esquema(self, cambio):
        """Cambia el esquema Y su manifiesto: solo queda la incoherencia de contenido, no la de huella."""
        import hashlib
        ruta, mani = self.carpeta / "contrato_work.schema.json", self.carpeta / "manifest.json"
        esquema = json.loads(ruta.read_text(encoding="utf-8"))
        cambio(esquema)
        datos = json.dumps(esquema).encode("utf-8")
        ruta.write_bytes(datos)
        m = json.loads(mani.read_text(encoding="utf-8"))
        for f in m["files"]:
            if f["path"] == "contrato_work.schema.json":
                f.update(sha256=hashlib.sha256(datos).hexdigest(), size_bytes=len(datos))
        mani.write_text(json.dumps(m), encoding="utf-8")

    def test_un_esquema_sin_la_clasificacion_t52_no_pasa(self):
        self.reescribir_esquema(lambda e: e["properties"].pop("classification"))
        self.assertIn("El esquema no trae la clasificación T-52 declarada en el manifiesto.",
                      verificar_paquete(self.carpeta))

    def test_un_esquema_de_otra_version_no_pasa(self):
        self.reescribir_esquema(lambda e: e.update(version=99))
        self.assertIn("La versión del esquema no es la del manifiesto.", verificar_paquete(self.carpeta))

    def test_un_paquete_de_otro_contrato_esta_desactualizado(self):
        self.assertTrue(any("no de ANKA_DASHBOARD_JSON_V2" in p
                            for p in verificar_paquete(self.carpeta, "ANKA_DASHBOARD_JSON_V2")))

    def test_un_leeme_de_otra_version_es_una_contradiccion(self):
        import hashlib
        leeme, mani = self.carpeta / "00_LEEME.md", self.carpeta / "manifest.json"
        datos = leeme.read_text(encoding="utf-8").replace(self.manifiesto["paquete_version"], "000000000000").encode()
        leeme.write_bytes(datos)
        m = json.loads(mani.read_text(encoding="utf-8"))
        for f in m["files"]:
            if f["path"] == "00_LEEME.md":
                f.update(sha256=hashlib.sha256(datos).hexdigest(), size_bytes=len(datos))
        mani.write_text(json.dumps(m), encoding="utf-8")
        self.assertIn("La versión de 00_LEEME.md no es la del manifiesto.", verificar_paquete(self.carpeta))

    def test_la_orden_verificar_responde_con_el_codigo_de_salida(self):
        salida = []
        self.assertEqual(paquete_work.main(["verificar", "--carpeta", str(self.carpeta)], salida=salida.append), 0)
        (self.carpeta / "PRECISO_V6.1.3.txt").write_text("otro", encoding="utf-8")
        self.assertEqual(paquete_work.main(["verificar", "--carpeta", str(self.carpeta)], salida=salida.append), 2)


class PublicacionTests(Base):
    def setUp(self):
        super().setUp()
        self.drive = DriveDeArchivos()

    def test_publicar_crea_la_subcarpeta_y_los_archivos_una_sola_vez(self):
        informe = publicar(self.drive, CARPETA_RAIZ, self.carpeta)
        carpetas = [e for e in self.drive.elementos.values() if e["name"] == CARPETA_EN_DRIVE]
        self.assertEqual(len(carpetas), 1)
        self.assertEqual(set(self.drive.archivos_de(carpetas[0]["id"])), set(OBLIGATORIOS))
        self.assertTrue(all(a["resultado"] == "creado" for a in informe["archivos"].values()))
        for nombre, e in self.drive.archivos_de(carpetas[0]["id"]).items():
            self.assertEqual(e["bytes"], (self.carpeta / nombre).read_bytes())
            self.assertEqual(len(e["appProperties"]["anka_paquete_sha256"]), 64)

    def test_repetir_no_duplica_ni_escribe(self):
        publicar(self.drive, CARPETA_RAIZ, self.carpeta)
        antes = len(self.drive.escrituras)
        informe = publicar(self.drive, CARPETA_RAIZ, self.carpeta)
        self.assertEqual(len(self.drive.escrituras), antes)
        self.assertTrue(all(a["resultado"] == "sin_cambios" for a in informe["archivos"].values()))

    def test_un_cambio_actualiza_solo_el_archivo_cambiado(self):
        publicar(self.drive, CARPETA_RAIZ, self.carpeta)
        self.drive.escrituras.clear()
        with patch.object(paquete_work, "DE_PLANTILLA", paquete_work.DE_PLANTILLA):
            nuevo = Path(self.temp.name) / "v2"
            construir(nuevo)
            ruta = nuevo / "FORMATO_DE_RETORNO.md"
            datos = ruta.read_bytes() + b"\nNota nueva.\n"
            ruta.write_bytes(datos)
            m = json.loads((nuevo / "manifest.json").read_text(encoding="utf-8"))
            import hashlib
            for f in m["files"]:
                if f["path"] == "FORMATO_DE_RETORNO.md":
                    f.update(sha256=hashlib.sha256(datos).hexdigest(), size_bytes=len(datos))
            (nuevo / "manifest.json").write_text(json.dumps(m), encoding="utf-8")
            informe = publicar(self.drive, CARPETA_RAIZ, nuevo)
        self.assertEqual({r for r, a in informe["archivos"].items() if a["resultado"] != "sin_cambios"},
                         {"FORMATO_DE_RETORNO.md", "manifest.json"})
        self.assertEqual({n for _, n in self.drive.escrituras}, {"FORMATO_DE_RETORNO.md", "manifest.json"})

    def test_un_duplicado_detiene_la_publicacion_sin_elegir(self):
        publicar(self.drive, CARPETA_RAIZ, self.carpeta)
        carpeta = [e["id"] for e in self.drive.elementos.values() if e["name"] == CARPETA_EN_DRIVE][0]
        self.drive._guardar("copia", {"name": "manifest.json", "parents": [carpeta], "mimeType": "application/json"}, None)
        with self.assertRaises(ErrorDeAnka):
            publicar(self.drive, CARPETA_RAIZ, self.carpeta)

    def test_dos_carpetas_de_referencia_detienen_la_publicacion(self):
        for n in (1, 2):
            self.drive._guardar(f"c{n}", {"name": CARPETA_EN_DRIVE, "parents": [CARPETA_RAIZ],
                                          "mimeType": "application/vnd.google-apps.folder"}, None)
        with self.assertRaises(ErrorDeAnka):
            publicar(self.drive, CARPETA_RAIZ, self.carpeta)
        self.assertEqual(self.drive.escrituras, [])

    def test_un_paquete_que_no_pasa_la_verificacion_no_se_publica(self):
        (self.carpeta / "PRECISO_V6.1.3.txt").write_text("alterado", encoding="utf-8")
        with self.assertRaises(ErrorDeAnka):
            publicar(self.drive, CARPETA_RAIZ, self.carpeta)
        self.assertEqual(self.drive.elementos, {})

    def test_la_orden_publicar_exige_confirmacion_y_no_toca_google_sin_ella(self):
        with patch("anka.infra.google_services.services", side_effect=AssertionError("no debe tocar Google")):
            codigo = paquete_work.main(["publicar", "--carpeta", str(self.carpeta), "--carpeta-id", CARPETA_RAIZ],
                                       salida=lambda _: None)
        self.assertEqual(codigo, 2)

    def test_la_orden_publicar_con_confirmacion_sube_el_paquete(self):
        with patch("anka.infra.google_services.services", return_value=(None, self.drive)):
            codigo = paquete_work.main(["publicar", "--carpeta", str(self.carpeta), "--carpeta-id", CARPETA_RAIZ,
                                        "--confirmar-drive"], salida=lambda _: None)
        self.assertEqual(codigo, 0)
        self.assertEqual(len(self.drive.elementos), len(OBLIGATORIOS) + 1)


if __name__ == "__main__":
    unittest.main()
