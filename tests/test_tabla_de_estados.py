"""La tabla única de estados y las consultas por estado del repositorio (U-52, D-117)."""

from tests import _isolate  # noqa: F401  (debe ir primero)

import json
import re
import tempfile
import unittest
from pathlib import Path

from anka.dominio import caso, status
from anka.dominio.status import ESTADOS, QuoteStatus, estado_tras_un_analisis, info_de_estado
from anka.infra import store
from anka.servicios import repositorio
from anka.web import paginas

RAIZ = Path(__file__).resolve().parents[1]
TRANSICIONES = json.loads((RAIZ / "spec" / "transiciones.json").read_text(encoding="utf-8"))


# Cubre: U-52
class TablaUnicaDeEstados(unittest.TestCase):
    def test_hay_una_fila_por_estado_y_ninguna_de_mas(self):
        self.assertEqual(set(ESTADOS), set(QuoteStatus))
        self.assertEqual(set(TRANSICIONES["estados"]), {e.value for e in QuoteStatus})

    def test_cada_fila_esta_completa(self):
        for estado, info in ESTADOS.items():
            with self.subTest(estado=estado):
                self.assertTrue(info.etiqueta.strip() and info.etapa.strip())

    def test_los_grupos_son_los_de_siempre(self):
        # Congelados: añadir un estado no debe cambiar a los demás; cambiar un grupo existente es una decisión.
        S = QuoteStatus
        self.assertEqual(status.CLOSED_STATUSES, {S.IGNORED, S.MISSING, S.READY, S.RESPONSE_SENT})
        self.assertEqual(status.ACTION_STATUSES, {S.REVIEW_PENDING, S.READY_REVIEW, S.SENDING, S.REVIEW, S.MISSING})
        self.assertEqual(status.REVIEW_QUEUE_STATUSES, (S.REVIEW_PENDING, S.READY_REVIEW, S.SENDING, S.REVIEW))
        self.assertEqual(status.ESTADOS_QUE_UN_ANALISIS_NO_MUEVE, {S.READY, S.SENDING})
        self.assertEqual(set(status.ESTADOS_CON_RESPUESTA_SUGERIDA),
                         {S.REVIEW_PENDING, S.REVIEW, S.READY_REVIEW, S.DRAFT_CREATED})

    def test_las_etiquetas_son_las_de_siempre(self):
        self.assertEqual({e.value: t for e, t in caso.STATUS_LABELS.items()}, {
            "review_pending": "Correo por revisar", "ready_review": "Información validada", "sending": "Enviando",
            "review": "Por decidir", "waiting": "Esperando al cliente", "draft_created": "Correo por revisar",
            "response_sent": "Respuesta enviada", "ready": "Proyecto creado", "missing": "Ya no está en Gmail",
            "ignored": "Descartada"})

    def test_la_etapa_visible_de_cada_estado_sale_de_la_tabla(self):
        esperadas = {
            "review_pending": ("Correo por revisar", True), "draft_created": ("Correo por revisar", True),
            "sending": ("El correo no salió", True), "missing": ("Ya no está en Gmail", True),
            "waiting": ("Esperando al cliente", False), "response_sent": ("Esperando al cliente", False),
            "ready_review": ("Información validada", True), "ready": ("Información validada", True),
            "ignored": ("Descartada", False), "review": ("Por decidir", True),
        }
        for estado in QuoteStatus:
            with self.subTest(estado=estado):
                etapa = paginas.case_stage({"status": estado.value, "status_label": "x", "analysis": {}})
                self.assertEqual(etapa, esperadas[estado.value])

    def test_un_acuse_enviado_no_espera_nada(self):
        analisis = {"preguntas_enviadas": {"preguntas": []}}
        etapa = paginas.case_stage({"status": "response_sent", "status_label": "x", "analysis": analisis})
        self.assertEqual(etapa, ("Respuesta enviada · sin espera", False))

    def test_un_estado_desconocido_no_rompe_la_lista(self):
        self.assertIsNone(info_de_estado("viejo"))
        self.assertIsNone(info_de_estado(None))
        self.assertEqual(paginas.case_stage({"status": "viejo", "status_label": "viejo", "analysis": {}}),
                         ("viejo", False))

    def test_la_proteccion_ante_un_analisis_sale_de_la_tabla(self):
        for estado, info in ESTADOS.items():
            with self.subTest(estado=estado):
                self.assertEqual(estado_tras_un_analisis(estado.value, "review", False),
                                 estado.value if info.protegido else "review")
                self.assertEqual(estado_tras_un_analisis(estado.value, "review", True),
                                 estado.value if info.protegido or info.protegido_del_mismo_mensaje else "review")
        self.assertEqual(estado_tras_un_analisis(None, "review", True), "review")


# Cubre: U-52
class ConsultasPorEstado(unittest.TestCase):
    def setUp(self):
        carpeta = tempfile.TemporaryDirectory()
        self.addCleanup(carpeta.cleanup)
        self.db = store.open_db(Path(carpeta.name))
        self.addCleanup(self.db.close)
        for hilo, estado, extra in (("a", QuoteStatus.WAITING, {}), ("b", QuoteStatus.REVIEW_PENDING, {}),
                                    ("c", QuoteStatus.IGNORED, {"force_analysis": True}),
                                    ("d", QuoteStatus.IGNORED, {}), ("e", QuoteStatus.READY_REVIEW, {}),
                                    ("f", QuoteStatus.READY, {})):
            store.save(self.db, hilo, f"m-{hilo}", estado, "ana@acme.com", {"project_name": hilo, **extra})

    def test_hilos_en_estado(self):
        self.assertEqual(set(repositorio.hilos_en_estado(self.db, QuoteStatus.WAITING)), {"a"})
        self.assertEqual(set(repositorio.hilos_en_estado(self.db, QuoteStatus.WAITING, QuoteStatus.READY)), {"a", "f"})
        self.assertEqual(repositorio.hilos_en_estado(self.db), [])

    def test_hilos_en_estado_rechaza_un_texto_que_no_es_estado(self):
        with self.assertRaises(ValueError):
            repositorio.hilos_en_estado(self.db, "ready'); DROP TABLE quotes;--")     # type: ignore[arg-type]

    def test_descartados_por_reanalizar(self):
        self.assertEqual(repositorio.hilos_descartados_por_reanalizar(self.db), ["c"])

    def test_la_cola_de_revision_lleva_solo_sus_estados_y_va_ordenada(self):
        self.assertEqual([f["thread_id"] for f in repositorio.filas_de_la_cola_de_revision(self.db)], ["b", "e"])

    def test_casos_esperando_con_recordatorios(self):
        self.assertEqual(repositorio.casos_esperando_con_recordatorios(self.db), [])
        self.db.execute("UPDATE quotes SET sent_at='2026-10-01T10:00:00-05:00' WHERE thread_id='a'")
        self.db.execute("INSERT INTO case_followup (thread_id, auto_enabled, paused, updated_at) VALUES ('a', 1, 0, '2026-10-01')")
        self.db.commit()
        self.assertEqual([f["thread_id"] for f in repositorio.casos_esperando_con_recordatorios(self.db)], ["a"])


# Cubre: U-52
class SinEstadosEscritosAMano(unittest.TestCase):
    NOMBRES = "|".join(e.value for e in QuoteStatus)
    LITERAL = re.compile(rf"status\s*(=|<>|!=|IN)\s*\(?\s*'(?:{NOMBRES})'", re.I)

    def test_ninguna_consulta_fuera_del_repositorio_escribe_un_estado_en_el_sql(self):
        ofensores = []
        for ruta in sorted((RAIZ / "anka").rglob("*.py")):
            if ruta.name in {"repositorio.py", "status.py"}:
                continue
            for numero, linea in enumerate(ruta.read_text(encoding="utf-8").splitlines(), 1):
                if self.LITERAL.search(linea):
                    ofensores.append(f"{ruta.relative_to(RAIZ)}:{numero}")
        self.assertEqual(ofensores, [], "Una consulta por estado va en servicios/repositorio.py, con parámetros")

    def test_ninguna_consulta_incrusta_un_estado_con_formato(self):
        ofensores = []
        for ruta in sorted((RAIZ / "anka").rglob("*.py")):
            texto = ruta.read_text(encoding="utf-8")
            for numero, linea in enumerate(texto.splitlines(), 1):
                if re.search(r"status\s*(=|<>|!=)\s*'\{", linea) and ruta.name not in {"repositorio.py", "store.py"}:
                    ofensores.append(f"{ruta.relative_to(RAIZ)}:{numero}")
        self.assertEqual(ofensores, [])


if __name__ == "__main__":
    unittest.main()
