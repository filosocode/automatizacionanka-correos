"""Tolerar y avisar: un análisis de Work con descuidos secundarios se importa; solo se rechaza lo que compromete la identidad.

Cubre: T-28, U-41
"""

from tests import _isolate  # noqa: F401  (debe ir primero)

import base64
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from anka.cli import work_sync as sync
from anka.dominio import tolerancia_work as tol
from anka.dominio.tolerancia_work import Rechazo
from anka.infra import settings
from anka.servicios import quotes, work_import
from tests.test_work_sync import FakeGmail, b64, draft_message, payload

AVISO = "Work escribió algo distinto de lo esperado; se corrigió solo: "
EVIDENCIA = {"id": "E1", "topic": "Plazo", "status": "explicit", "value": "30 días", "source": "correo",
             "location": "p. 1", "owner": "client", "blocking": True}
ADJUNTO = {"filename": "a.pdf", "status": "extraido", "coverage": "completa", "size_bytes": 2048,
           "reviewed_sections": ["p. 1"]}


def extraer(**extra):
    """El análisis que el puente entrega al importador para un borrador con esos campos."""
    return sync.extract(draft_message(payload(**extra)), "D1")[0]


def con_texto(texto: str, thread: str = "T1") -> dict:
    """Un borrador cuyo bloque es exactamente `texto` (para JSON dañado)."""
    cuerpo = "[REVISAR ANTES DE ENVIAR - BORRAR ESTA SECCION]\n" + sync.START + "\n" + texto + "\n" + sync.END + "\nHola"
    return {"id": "gm1", "threadId": thread, "internalDate": "1000",
            "payload": {"mimeType": "text/plain", "body": {"data": b64(cuerpo)}}}


def avisos(analisis: dict) -> str:
    return " ".join(analisis.get("_correcciones_de_work", []))


class LoQueSeToleraConAviso(unittest.TestCase):
    def test_no_indicado_donde_va_un_numero_o_un_booleano_se_trata_como_desconocido(self) -> None:
        a = extraer(attachments=[{**ADJUNTO, "size_bytes": "no indicado"}], analysis_complete="no indicado")
        self.assertNotIn("size_bytes", a["attachments"][0])
        self.assertNotIn("analysis_complete", a)                      # desconocido: omitido, no inventado
        self.assertIn("attachments[0].size_bytes", avisos(a))
        self.assertTrue(avisos(a).startswith(AVISO))

    def test_no_indicado_en_un_enumerado_se_omite_y_la_fila_queda_sin_confirmar(self) -> None:
        a = extraer(evidence_register=[{**EVIDENCIA, "owner": "no indicado", "blocking": "no indicado"}])
        fila = a["evidence_register"][0]
        self.assertEqual((fila["owner"], fila["blocking"]), ("unknown", False))   # relleno explícito, no inventado
        self.assertEqual(fila["status"], "explicit")
        self.assertIn("evidence_register[0].owner", avisos(a))

    def test_enumerados_en_espanol_o_con_punto_final_o_mayusculas_van_al_valor_permitido(self) -> None:
        a = extraer(
            evidence_register=[{**EVIDENCIA, "status": "Explícito.", "owner": "cliente"},
                               {**EVIDENCIA, "id": "E2", "status": "PARCIAL", "owner": "Contratista"}],
            question_audit=[{"question": "¿Datum?", "reason_missing": "no figura", "reviewed_sources": ["correo"],
                             "disposition": "preguntar al cliente", "target_finding_ids": ["E1"]}],
            attachments=[{**ADJUNTO, "read_via": "Raw MIME"}])
        self.assertEqual([(f["status"], f["owner"]) for f in a["evidence_register"]],
                         [("explicit", "client"), ("partial", "contractor")])
        self.assertEqual(a["question_audit"][0]["disposition"], "ask_client")
        self.assertEqual(a["attachments"][0]["read_via"], "raw_mime")
        self.assertIn("«Explícito.» se tomó como «explicit»", avisos(a))

    def test_un_enumerado_que_no_se_entiende_queda_desconocido_y_no_se_adivina(self) -> None:
        a = extraer(question_audit=[{"question": "¿Datum?", "reason_missing": "x", "reviewed_sources": ["correo"],
                                     "disposition": "quizás"}])
        self.assertNotIn("disposition", a["question_audit"][0])
        self.assertIn("question_audit[0].disposition", avisos(a))

    def test_el_estado_con_punto_final_o_sin_tildes_se_reconoce(self) -> None:
        for escrito in ("Faltan datos.", "faltan datos", "  Requiere aclaraciones del cliente.  "):
            with self.subTest(escrito=escrito):
                self.assertIn(extraer(status=escrito)["status"], {"Faltan datos", "Requiere aclaraciones del cliente"})

    def test_listas_escritas_como_texto_son_listas_de_un_elemento(self) -> None:
        a = extraer(cited_codes="T-123", contradictions=[{"topic": "datum", "sources": "TdR p. 2"}],
                    question_audit=[{"question": "¿Datum?", "reason_missing": "x", "reviewed_sources": "correo"}],
                    attachments=[{**ADJUNTO, "reviewed_sections": "p. 1"}])
        self.assertEqual(a["cited_codes"], ["T-123"])
        self.assertEqual(a["contradictions"][0]["sources"], ["TdR p. 2"])
        self.assertEqual(a["question_audit"][0]["reviewed_sources"], ["correo"])
        self.assertEqual(a["attachments"][0]["reviewed_sections"], ["p. 1"])
        self.assertIn("contradictions[0].sources", avisos(a))

    def test_evidencia_sin_cita_ni_ubicacion_se_conserva(self) -> None:
        a = extraer(attachments=[{**ADJUNTO, "evidence": [{"topic": "Área", "value": "40 ha"}]}])
        fila = a["attachments"][0]["evidence"][0]
        self.assertEqual((fila["value"], fila["excerpt"], fila["location"]), ("40 ha", "", ""))
        self.assertIn("attachments[0].evidence[0].excerpt", avisos(a))

    def test_un_campo_sin_fuente_queda_marcado_sin_fuente(self) -> None:
        a = extraer(fields=[{"name": "Datum", "value": "MAGNA"}])
        self.assertEqual(a["fields"][0]["source"], tol.SIN_FUENTE)
        self.assertIn("fields[0].source", avisos(a))

    def test_size_bytes_como_texto_o_decimal(self) -> None:
        for escrito, esperado in (("2048", 2048), (2048.0, 2048), ("2048.0", 2048), (2048.5, None), ("2 MB", None)):
            with self.subTest(escrito=escrito):
                adjunto = extraer(attachments=[{**ADJUNTO, "size_bytes": escrito}])["attachments"][0]
                self.assertEqual(adjunto.get("size_bytes"), esperado)

    def test_quantity_y_unit_numericos_se_guardan_como_texto_y_la_fila_queda_pendiente(self) -> None:
        a = extraer(activities=[{"activity": "Levantamiento", "quantity": 40, "unit": 5, "estado": "confirmado"},
                                {"activity": "Batimetría", "quantity": "10", "unit": "ha", "estado": "confirmado"}])
        primera, segunda = a["activities"]
        self.assertEqual(primera["unit"], "5")
        self.assertEqual(primera["estado"], "pendiente")          # el valor cambió de significado: sin confirmar
        self.assertEqual(segunda["estado"], "confirmado")         # la fila sin descuidos no se toca
        self.assertIn("activities[0].unit", avisos(a))
        self.assertNotIn("activities[1]", avisos(a))

    def test_un_valor_que_no_se_puede_convertir_en_una_fila_deja_la_fila_pendiente_y_vacio_el_campo(self) -> None:
        a = extraer(activities=[{"activity": "Levantamiento", "unit": True, "estado": "confirmado"}])
        self.assertNotIn("unit", a["activities"][0])
        self.assertEqual(a["activities"][0]["estado"], "pendiente")

    def test_las_actividades_escritas_como_texto_son_una_fila_pendiente(self) -> None:
        a = extraer(activities="Levantamiento topográfico de 40 ha")
        self.assertEqual(a["activities"], ["Levantamiento topográfico de 40 ha"])
        analisis = work_import._work_analysis({**a, "draft_reply": ""})
        self.assertEqual(analisis["activities"][0]["estado"], "pendiente")

    def test_sin_nombre_de_proyecto_o_con_otro_engine(self) -> None:
        a = extraer(project_name="  ", engine="local")
        self.assertEqual((a["project_name"], a["engine"]), (tol.SIN_NOMBRE, "chatgpt_work"))
        self.assertIn("project_name", avisos(a))
        self.assertIn("engine", avisos(a))

    def test_una_fila_a_la_que_le_falta_algo_que_no_se_puede_vaciar_se_omite_con_aviso(self) -> None:
        sin_id = {k: v for k, v in EVIDENCIA.items() if k != "id"}
        a = extraer(evidence_register=[sin_id, EVIDENCIA], question_audit=[{"reason_missing": "x"}])
        self.assertEqual([f["id"] for f in a["evidence_register"]], ["E1"])
        self.assertEqual(a["question_audit"], [])
        self.assertIn("evidence_register[0]: se omitió porque le faltaba «id»", avisos(a))

    def test_un_analisis_limpio_no_trae_aviso(self) -> None:
        self.assertNotIn("_correcciones_de_work", extraer(attachments=[ADJUNTO], evidence_register=[EVIDENCIA]))

    def test_muchas_correcciones_se_cuentan_en_un_solo_aviso_agrupado(self) -> None:
        filas = [{"activity": f"a{n}", "unit": n} for n in range(30)]
        texto = avisos(extraer(activities=filas))
        self.assertEqual(len(extraer(activities=filas)["_correcciones_de_work"]), 1)
        self.assertIn("y 27 más", texto)

    def test_el_aviso_llega_al_caso_importado(self) -> None:
        with tempfile.TemporaryDirectory() as carpeta, patch.object(settings, "DATA_DIR", Path(carpeta)):
            gmail = FakeGmail({"d1": draft_message(payload(attachments=[{**ADJUNTO, "size_bytes": "no indicado"}]))})
            resultado = sync.sync_once(gmail, work_import.dashboard_upsert, Path(carpeta) / "estado.json")
            self.assertEqual((resultado["updated"], resultado["failures"], len(resultado["rejected"])), (1, 0, 0))
            guardado = quotes.get_quote("T1")["analysis"]
            self.assertTrue(guardado["correcciones_de_work"][0].startswith(AVISO))


class LoQueSigueRechazandose(unittest.TestCase):
    def rechazo(self, borrador: dict) -> Rechazo:
        with self.assertRaises(Rechazo) as capturado:
            sync.extract(borrador, "D1")
        return capturado.exception

    def test_sin_identidad(self) -> None:
        for campo in ("thread_id", "message_id", "client_email"):
            with self.subTest(campo=campo):
                datos = payload()
                datos[campo] = "  "
                rechazo = self.rechazo(draft_message(datos))
                self.assertEqual((rechazo.causa, rechazo.campos), ("falta_identidad", (campo,)))
        self.assertEqual(self.rechazo(draft_message(payload(thread=123))).causa, "falta_identidad")

    def test_un_hilo_ajeno(self) -> None:
        rechazo = self.rechazo(draft_message(payload(thread="OTRO"), thread="T1"))
        self.assertEqual((rechazo.causa, rechazo.campos), ("hilo_ajeno", ("thread_id",)))

    def test_un_estado_desconocido_aunque_se_normalice_la_forma(self) -> None:
        for escrito in ("Pendiente", "En revisión", "Cerrada.", 5, None):
            with self.subTest(escrito=escrito):
                datos = payload()
                datos["status"] = escrito
                rechazo = self.rechazo(draft_message(datos))
                self.assertEqual((rechazo.causa, rechazo.campos), ("estado_desconocido", ("status",)))

    def test_un_json_irrecuperable_dice_donde_falla_y_no_el_error_de_python(self) -> None:
        rechazo = self.rechazo(con_texto('{"thread_id": "T1", "message_id": "m1" "client_email": "x"'))
        self.assertEqual(rechazo.causa, "json_ilegible")
        self.assertIn("línea 1", rechazo.motivo)
        for ingles in ("Expecting", "delimiter", "JSON"):
            self.assertNotIn(ingles, rechazo.motivo)

    def test_otros_rechazos_de_forma(self) -> None:
        self.assertEqual(self.rechazo(con_texto("[1, 2]")).causa, "no_es_objeto")
        doble = con_texto("{}")
        doble["payload"]["body"]["data"] = b64(base64.urlsafe_b64decode(
            doble["payload"]["body"]["data"]).decode() + "\n" + sync.START + "\n{}\n" + sync.END)
        self.assertEqual(self.rechazo(doble).causa, "bloques_repetidos")

    def test_el_motivo_nunca_lleva_el_contenido_del_cliente(self) -> None:
        secreto = "SECRETO-DEL-CLIENTE-9"
        texto = json.dumps(payload(summary=secreto)).replace("}", ', "x": [') + ""     # JSON sin cerrar
        rechazo = self.rechazo(con_texto(texto))
        self.assertNotIn(secreto, rechazo.motivo)
        otro = self.rechazo(draft_message(payload(thread="OTRO", summary=secreto, project_name=secreto)))
        self.assertNotIn(secreto, otro.motivo)


class ReparacionesMecanicasDelJson(unittest.TestCase):
    def leer(self, texto: str):
        return sync.extract(con_texto(texto), "D1")[0]

    def test_cercas_texto_suelto_y_comas_sobrantes(self) -> None:
        base = json.dumps(payload())
        for variante in ("```json\n" + base + "\n```", "Aquí va el análisis: " + base + " Gracias.",
                         base[:-1] + ',}', base.replace('"activities": [{"activity": "a"}]', '"activities": [{"activity": "a"},]')):
            with self.subTest(variante=variante[:30]):
                analisis = self.leer(variante)
                self.assertEqual(analisis["thread_id"], "T1")
                self.assertIn("se limpió", avisos(analisis))

    def test_un_salto_de_linea_dentro_de_un_texto_no_tumba_el_analisis(self) -> None:
        analisis = self.leer(json.dumps(payload(summary="LINEA_A")).replace("LINEA_A", "línea 1\nlínea 2\ttab"))
        self.assertIn("línea 2", analisis["summary"])

    def test_la_coma_dentro_de_un_texto_no_se_toca(self) -> None:
        analisis = self.leer(json.dumps(payload(summary="uno,}dos,]")) + "")
        self.assertEqual(analisis["summary"], "uno,}dos,]")
        self.assertNotIn("_correcciones_de_work", analisis)


class BorradorHtml(unittest.TestCase):
    KML = "<Polygon><outerBoundaryIs><coordinates>-74.1,4.6,0 -74.2,4.7,0</coordinates></outerBoundaryIs></Polygon>"

    def html(self, extracto: str, escapado: bool) -> dict:
        datos = payload(attachments=[{**ADJUNTO, "evidence": [
            {"topic": "Geometría", "value": "polígono", "location": "KML", "excerpt": extracto}]}])
        import html as h
        texto = json.dumps(datos, ensure_ascii=False)
        if escapado:
            texto = h.escape(texto, quote=False)
        cuerpo = ("<div>[REVISAR ANTES DE ENVIAR - BORRAR ESTA SECCION]</div><div>" + sync.START + "</div><div>" + texto
                  + "</div><div>" + sync.END + "</div><div>Hola<br>cliente</div>")
        return {"id": "gm1", "threadId": "T1", "internalDate": "1000",
                "payload": {"mimeType": "text/html", "body": {"data": b64(cuerpo)}}}

    def test_las_etiquetas_del_extracto_no_se_borran_ni_con_entidades_ni_sin_ellas(self) -> None:
        # Cubre: T-28, U-41
        for escapado in (True, False):
            with self.subTest(escapado=escapado):
                analisis = sync.extract(self.html(self.KML, escapado), "D1")[0]
                extracto = analisis["attachments"][0]["evidence"][0]["excerpt"]
                self.assertEqual(extracto, self.KML)

    def test_un_menor_que_suelto_y_las_entidades_se_convierten(self) -> None:
        extracto = "área < 40 ha & plazo > 30 días"
        analisis = sync.extract(self.html(extracto, True), "D1")[0]
        self.assertEqual(analisis["attachments"][0]["evidence"][0]["excerpt"], extracto)

    def test_el_html_de_formato_se_sigue_quitando_y_el_correo_al_cliente_conserva_sus_lineas(self) -> None:
        analisis = sync.extract(self.html("texto", True), "D1")[0]
        self.assertEqual(analisis["draft_reply"], "Hola\ncliente")


class MapaDeEnumerados(unittest.TestCase):
    def test_cada_sinonimo_lleva_a_un_valor_que_el_esquema_permite(self) -> None:
        esquema = json.loads((Path(__file__).resolve().parents[1] / "spec" / "contrato_work.schema.json")
                             .read_text(encoding="utf-8"))["properties"]
        permitidos = {
            "evidence_register[].status": esquema["evidence_register"]["items"]["properties"]["status"]["enum"],
            "evidence_register[].owner": esquema["evidence_register"]["items"]["properties"]["owner"]["enum"],
            "question_audit[].disposition": esquema["question_audit"]["items"]["properties"]["disposition"]["enum"],
            "attachments[].read_via": esquema["attachments"]["items"]["properties"]["read_via"]["enum"],
        }
        self.assertEqual(set(tol.SINONIMOS_DE_ENUMERADOS), set(permitidos))
        for ruta, mapa in tol.SINONIMOS_DE_ENUMERADOS.items():
            for sinonimo, valor in mapa.items():
                with self.subTest(ruta=ruta, sinonimo=sinonimo):
                    self.assertIn(valor, permitidos[ruta])
                    self.assertEqual(tol.forma_simple(sinonimo), sinonimo)     # las claves ya están en forma simple


if __name__ == "__main__":
    unittest.main()
