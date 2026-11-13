"""Una sola política de validación del contrato con Work, para el puente y para la importación (U-51, D-116)."""

from tests import _isolate  # noqa: F401  (debe ir primero)

import base64
import copy
import inspect
import json
import unittest

from anka.cli import work_sync
from anka.dominio import tolerancia_work, work_contract
from anka.dominio.errores import ErrorDeAnka
from anka.servicios import work_import
from tests.test_work_sync import payload


def en_base64(datos) -> str:
    return base64.urlsafe_b64encode(json.dumps(datos, ensure_ascii=False).encode("utf-8")).decode()


def por_el_puente(datos):
    """Lo que hace el puente con el bloque de un borrador, hasta dejarlo listo para importar."""
    copia = copy.deepcopy(datos)
    try:
        tolerancia_work.comprobar_identidad(copia)
        tolerancia_work.normalizar_analisis(copia)
    except tolerancia_work.Rechazo as exc:
        return ("rechazado", exc.causa)
    return ("aceptado", copia)


def por_la_importacion(datos):
    try:
        return ("aceptado", work_import._decode_work_payload(en_base64(datos))[1])
    except ErrorDeAnka:
        return ("rechazado", None)


def sin(datos, *campos):
    datos = copy.deepcopy(datos)
    for campo in campos:
        datos.pop(campo, None)
    return datos


# Cubre: U-51
class UnSoloCriterio(unittest.TestCase):
    CASOS = {
        "completo": lambda: payload(),
        "sin message_id": lambda: sin(payload(), "message_id"),
        "sin engine": lambda: sin(payload(), "engine"),
        "engine distinto": lambda: payload(engine="otro"),
        "sin nombre de proyecto": lambda: sin(payload(), "project_name"),
        "sin client_email": lambda: sin(payload(), "client_email"),
        "sin thread_id": lambda: sin(payload(), "thread_id"),
        "estado sin tildes": lambda: payload(status="en analisis"),
        "estado desconocido": lambda: payload(status="Cualquier cosa"),
        "tamano como texto": lambda: payload(attachments=[{"filename": "a.kml", "status": "legible", "reason": "",
                                                          "blocked_fields": [], "size_bytes": "10"}]),
        "actividades como texto": lambda: payload(activities="Levantamiento topografico"),
        "actividades como numero": lambda: payload(activities=5),
        "lista de codigos como texto": lambda: payload(cited_codes="T26145"),
        "nulos en opcionales": lambda: payload(activities=None, attachments=None, missing_data=None),
    }

    def test_el_puente_y_la_importacion_aceptan_y_rechazan_lo_mismo(self):
        for nombre, hacer in self.CASOS.items():
            with self.subTest(nombre):
                puente, importacion = por_el_puente(hacer()), por_la_importacion(hacer())
                self.assertEqual(puente[0], importacion[0])
                if puente[0] == "aceptado":
                    self.assertEqual(puente[1], importacion[1])         # y dejan el mismo análisis, con los mismos avisos

    def test_lo_que_el_esquema_rechaza_ya_no_entra_por_la_importacion(self):
        for nombre in ("sin message_id", "sin client_email", "sin thread_id", "estado desconocido"):
            with self.subTest(nombre):
                self.assertEqual(por_la_importacion(self.CASOS[nombre]())[0], "rechazado")

    def test_lo_secundario_se_tolera_y_se_avisa_en_los_dos_caminos(self):
        for nombre in ("sin engine", "sin nombre de proyecto", "tamano como texto", "actividades como texto",
                       "lista de codigos como texto", "nulos en opcionales", "estado sin tildes"):
            with self.subTest(nombre):
                estado, datos = por_la_importacion(self.CASOS[nombre]())
                self.assertEqual(estado, "aceptado")
        _, datos = por_la_importacion(self.CASOS["sin nombre de proyecto"]())
        self.assertEqual(datos["project_name"], tolerancia_work.SIN_NOMBRE)
        self.assertTrue(datos["_correcciones_de_work"])

    def test_los_dos_modulos_usan_la_misma_funcion_y_ninguno_tiene_su_propia_lista(self):
        self.assertIs(work_sync.normalizar_analisis, tolerancia_work.normalizar_analisis)
        self.assertIs(work_import.normalizar_analisis, tolerancia_work.normalizar_analisis)
        self.assertIs(work_sync.comprobar_identidad, work_import.comprobar_identidad)
        self.assertFalse(hasattr(work_import, "_TIPOS_DE_CAMPOS"))
        self.assertNotIn("required = (", inspect.getsource(work_import._decode_work_payload))

    def test_el_rechazo_dice_el_motivo_y_la_importacion_a_mano_ademas_el_estado_que_llego(self):
        with self.assertRaises(ErrorDeAnka) as capturado:
            work_import._decode_work_payload(en_base64(payload(status="Cualquier cosa")))
        self.assertIn("status", str(capturado.exception))
        self.assertIn("Cualquier cosa", str(capturado.exception))       # T-33: la importación a mano lo dice
        with self.assertRaises(tolerancia_work.Rechazo) as rechazo:
            tolerancia_work.normalizar_analisis(payload(status="Cualquier cosa"))
        self.assertNotIn("Cualquier cosa", str(rechazo.exception))          # el puente nunca lleva el contenido (T-36)

    def test_importar_dos_veces_lo_ya_normalizado_no_cambia_nada(self):
        primero = por_el_puente(payload(status="en analisis", activities="Levantamiento"))[1]
        segundo = por_la_importacion(primero)[1]
        self.assertEqual(primero, segundo)


# Cubre: U-51
class ElValidadorNoIgnoraEnSilencio(unittest.TestCase):
    def test_una_palabra_clave_que_no_comprueba_es_un_error_y_no_se_ignora(self):
        for palabra, valor in (("format", "date"), ("minItems", 1), ("oneOf", []), ("additionalProperties", False),
                               ("$ref", "#/x"), ("maxLength", 3)):
            with self.subTest(palabra=palabra):
                esquema = {"type": "object", "properties": {"a": {"type": "string", palabra: valor}}}
                errores = work_contract.validate({"a": "x"}, esquema)
                self.assertTrue(any(palabra in e and "no sabe comprobar" in e for e in errores), errores)

    def test_un_tipo_desconocido_no_lanza_keyerror_y_se_nombra(self):
        esquema = {"type": "object", "properties": {"a": {"type": "date"}, "b": {"type": ["string", "decimal"]}}}
        errores = work_contract.validate({"a": "2026-01-01", "b": "x"}, esquema)
        self.assertTrue(any("«date»" in e and "no conoce" in e for e in errores), errores)
        self.assertTrue(any("«decimal»" in e for e in errores), errores)
        self.assertFalse(work_contract.tipo_cumple("x", "date"))

    def test_las_anotaciones_no_son_un_problema(self):
        esquema = {"$comment": "x", "description": "d", "version": 1, "type": "object",
                   "properties": {"a": {"type": "string", "$comment": "y", "minLength": 1}}}
        self.assertEqual(work_contract.validate({"a": "x"}, esquema), [])

    def test_el_esquema_real_solo_usa_lo_que_el_validador_comprueba(self):
        self.assertEqual(work_contract.palabras_no_soportadas(work_contract.load_schema()), [])

    def test_un_esquema_con_palabra_nueva_se_nota_tambien_en_items(self):
        esquema = {"type": "array", "items": {"type": "string", "format": "x"}}
        self.assertTrue(work_contract.palabras_no_soportadas(esquema))


# Cubre: U-51
class VersionDelContrato(unittest.TestCase):
    def test_el_esquema_declara_su_version_y_la_propiedad_es_opcional(self):
        esquema = work_contract.load_schema()
        self.assertEqual(work_contract.version_del_contrato(esquema), 1)
        self.assertNotIn("schema_version", esquema["required"])      # opcional y fuera del esquema: el prompt no cambia
        self.assertEqual(work_contract.version_del_contrato({}), 1)
        self.assertEqual(work_contract.version_del_contrato({"version": "x"}), 1)

    def test_sin_version_o_con_la_actual_no_hay_aviso(self):
        for datos in (payload(), payload(schema_version=1)):
            _, analisis = por_la_importacion(datos)
            self.assertFalse(analisis.get("_avisos_del_puente"))

    def test_una_version_mas_nueva_se_acepta_con_aviso(self):
        estado, analisis = por_la_importacion(payload(schema_version=2))
        self.assertEqual(estado, "aceptado")
        self.assertTrue(any("versión 2" in a and "hasta la 1" in a for a in analisis["_avisos_del_puente"]))

    def test_una_version_escrita_como_texto_se_lee_y_una_ilegible_cuenta_como_la_uno(self):
        self.assertTrue(por_el_puente(payload(schema_version="3"))[1]["_avisos_del_puente"])
        for rara in ("x", None, True, 2.5, [], "1"):
            with self.subTest(rara=rara):
                self.assertFalse(por_el_puente(payload(schema_version=rara))[1].get("_avisos_del_puente"))


if __name__ == "__main__":
    unittest.main()
