"""Cubre: T-69. Detección conservadora de cambios de magnitud entre mensajes del cliente, en modo sombra."""

from tests import _isolate  # noqa: F401  (debe ir primero)

import base64
import copy
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from anka.dominio import cambios_de_magnitud as cm
from anka.dominio import verificacion_de_evidencia as ver
from anka.dominio.cambios_de_magnitud import CAMBIO_POSIBLE, NO_COMPROBABLE, RECTIFICACION_EXPLICITA
from anka.infra import instantaneas_de_manifiesto as copias
from anka.servicios import lectura_de_adjuntos


def msg(identidad, hora, texto, remitente="cliente@ejemplo.co", direccion="received", truncado=False):
    return {"message_id": identidad, "fecha": f"2026-10-2{hora[0]}T{hora[1:]}:00+00:00", "remitente": remitente,
            "direccion": direccion, "texto": texto, "truncado": truncado}


def lote(cantidad="4 ha", unidad="ha", nombre="Levantamiento de lote"):
    return {"activity": nombre, "quantity": cantidad, "unit": unidad, "fuente": "correo"}


M1 = msg("m1", "109:00", "Necesito el levantamiento de lote de 4 ha en Tenjo.")


def detectar(texto2, actividades=None, primero=M1, **extra):
    mensajes = [primero, msg("m2", "210:00", texto2, **extra)]
    return cm.detectar(actividades or [lote()], mensajes, "T1")


class SinCambioTests(unittest.TestCase):
    def test_t59_un_seguimiento_sin_cambio_de_magnitud_no_genera_observaciones(self):
        # Cubre: T-69
        self.assertEqual(detectar("Gracias. Les envío el plano. El levantamiento de lote sigue siendo de 4 ha."), [])
        self.assertEqual(detectar("Quedo atento a la cotización."), [])

    def test_con_un_solo_mensaje_recibido_no_hay_nada_que_comparar(self):
        # Cubre: T-69
        self.assertEqual(cm.detectar([lote()], [M1], "T1"), [])
        self.assertEqual(cm.detectar([lote()], None, "T1"), [])
        self.assertEqual(cm.detectar([lote()], "texto", "T1"), [])

    def test_una_actividad_sin_cifra_no_genera_observacion(self):
        # Cubre: T-69
        sin_cifra = {"activity": "Plano de localización", "quantity": "", "unit": "", "fuente": "correo"}
        self.assertEqual(detectar("El lote ahora es de 6 ha.", [sin_cifra]), [])


class RectificacionTests(unittest.TestCase):
    def test_s01_cuatro_hectareas_corregidas_expresamente_a_seis(self):
        # Cubre: T-69
        obs = detectar("El área correcta es 6 ha, no 4 ha.")
        self.assertEqual(len(obs), 1)
        o = obs[0]
        self.assertEqual((o["clasificacion"], o["cambio"], o["evidencia"]), (RECTIFICACION_EXPLICITA, "valor", "media"))
        self.assertEqual((o["thread_id"], o["actividad"], o["nombre"]), ("T1", 1, "Levantamiento de lote"))
        self.assertEqual((o["message_id_origen"], o["message_id_posterior"]), ("m1", "m2"))
        self.assertEqual((o["valor_anterior"], o["valor_posterior"]), ("4", "6"))
        self.assertEqual((o["unidad_anterior"], o["unidad_posterior"]), ("area_ha", "area_ha"))
        self.assertIn("4 ha", o["fragmento_anterior"])
        self.assertIn("6 ha", o["fragmento_posterior"])
        self.assertEqual(o["remitente_posterior"], "cliente@ejemplo.co")

    def test_s01_no_afirma_que_el_valor_nuevo_sea_el_vigente_ni_que_anka_lo_acepto(self):
        # Cubre: T-69
        o = detectar("El área correcta es 6 ha, no 4 ha.")[0]
        self.assertEqual(o["vigencia"], "no_determinada")
        self.assertEqual(o["tabla"], "coincide_con_anterior")           # la tabla sigue con 4 ha: solo se informa
        for texto in (o["motivo"], json.dumps(o, ensure_ascii=False).replace("vigencia", "")):
            self.assertNotRegex(texto.lower(), r"vigente|aceptad|modific|actualiz")
        # lo que está en la tabla es el valor nuevo: el resultado tampoco elige
        nuevo = detectar("El área correcta es 6 ha, no 4 ha.", [lote("6 ha")])[0]
        self.assertEqual((nuevo["tabla"], nuevo["vigencia"]), ("coincide_con_posterior", "no_determinada"))

    def test_las_formas_explicitas_de_corregir(self):
        # Cubre: T-69
        casos = {
            "En lugar de 4 ha, considerar 6 ha.": RECTIFICACION_EXPLICITA,
            "No son 4 ha sino 6 ha.": RECTIFICACION_EXPLICITA,
            "Corrección: el lote es de 6 ha.": RECTIFICACION_EXPLICITA,
            "Rectifico: el área del lote es 6 ha, no 4 ha.": RECTIFICACION_EXPLICITA,
        }
        for texto, esperado in casos.items():
            with self.subTest(texto=texto):
                obs = detectar(texto)
                self.assertEqual([o["clasificacion"] for o in obs], [esperado])
                self.assertEqual((obs[0]["valor_anterior"], obs[0]["valor_posterior"]), ("4", "6"))

    def test_corregir_nombrando_la_actividad_es_evidencia_alta(self):
        # Cubre: T-69
        o = detectar("Corrección: el lote es de 6 ha.")[0]
        self.assertEqual((o["clasificacion"], o["evidencia"]), (RECTIFICACION_EXPLICITA, "alta"))

    def test_una_correccion_que_no_se_puede_ligar_a_la_actividad_es_solo_un_cambio_posible(self):
        # Cubre: T-69
        o = detectar("Corrección: son 6 ha.")[0]
        self.assertEqual((o["clasificacion"], o["evidencia"]), (CAMBIO_POSIBLE, "baja"))
        self.assertEqual((o["message_id_origen"], o["message_id_posterior"]), ("m1", "m2"))

    def test_la_correccion_confirmada_con_el_mismo_valor_no_es_un_cambio(self):
        # Cubre: T-69
        self.assertEqual(detectar("Corrección: el lote es de 4 ha, no 4 ha."), [])

    def test_correccion_de_unidades_sin_cambio_numerico(self):
        # Cubre: T-69
        eje = {"activity": "Eje vial", "quantity": "4 km", "unit": "km", "fuente": "correo"}
        primero = msg("m1", "109:00", "El eje vial mide 4 km.")
        obs = detectar("En lugar de 4 km, considerar 4 m.", [eje], primero)
        self.assertEqual(len(obs), 1)
        self.assertEqual((obs[0]["clasificacion"], obs[0]["cambio"]), (RECTIFICACION_EXPLICITA, "unidad"))
        self.assertEqual((obs[0]["valor_anterior"], obs[0]["valor_posterior"]), ("4", "4"))
        self.assertEqual((obs[0]["unidad_anterior"], obs[0]["unidad_posterior"]), ("longitud_km", "longitud_m"))

    def test_dos_actividades_con_el_mismo_valor_impiden_atribuir_la_correccion(self):
        # Cubre: T-69
        gemelas = [lote(), lote(nombre="Levantamiento de predio vecino")]
        obs = detectar("El área correcta es 6 ha, no 4 ha.", gemelas)
        self.assertEqual({o["clasificacion"] for o in obs}, {NO_COMPROBABLE})
        self.assertEqual(len(obs), 2)


class CambioImplicitoTests(unittest.TestCase):
    def test_un_cambio_sin_confirmacion_explicita_es_solo_un_cambio_posible(self):
        # Cubre: T-69
        o = detectar("Les confirmo el levantamiento de lote de 6 ha.")[0]
        self.assertEqual((o["clasificacion"], o["evidencia"], o["cambio"]), (CAMBIO_POSIBLE, "media", "valor"))
        self.assertEqual((o["message_id_origen"], o["message_id_posterior"]), ("m1", "m2"))
        self.assertEqual((o["valor_anterior"], o["valor_posterior"]), ("4", "6"))
        self.assertEqual(o["vigencia"], "no_determinada")

    def test_el_mensaje_mas_reciente_no_se_toma_como_vigente(self):
        # Cubre: T-69
        o = detectar("Les confirmo el levantamiento de lote de 6 ha.")[0]
        self.assertEqual(o["tabla"], "coincide_con_anterior")
        self.assertNotIn("vigente", o["motivo"].lower())

    def test_una_actividad_nueva_con_otra_magnitud_no_es_un_cambio(self):
        # Cubre: T-69
        self.assertEqual(detectar("Además se requiere el levantamiento de la vía: 12 km."), [])
        self.assertEqual(detectar("Además solicito otro lote de 6 ha."), [])
        self.assertEqual(detectar("También necesito el levantamiento del lote vecino, de 6 ha."), [])

    def test_dos_actividades_semejantes_en_el_mismo_hilo_no_se_atribuyen_a_una_sola(self):
        # Cubre: T-69
        norte = {"activity": "Levantamiento lote norte", "quantity": "4 ha", "unit": "ha", "fuente": "correo"}
        sur = {"activity": "Levantamiento lote sur", "quantity": "6 ha", "unit": "ha", "fuente": "correo"}
        primero = msg("m1", "109:00", "Lote norte: 4 ha.\nLote sur: 6 ha.")
        obs = detectar("Lote sur: 8 ha.", [norte, sur], primero)
        self.assertTrue(obs)
        self.assertEqual({o["clasificacion"] for o in obs}, {NO_COMPROBABLE})
        self.assertTrue(all("semejantes" in o["motivo"] for o in obs))
        # y si repiten lo mismo no hay nada que decir
        self.assertEqual(detectar("Lote norte: 4 ha.\nLote sur: 6 ha.", [norte, sur], primero), [])

    def test_un_mensaje_con_varias_magnitudes_solo_compara_la_misma_unidad_y_concepto(self):
        # Cubre: T-69
        quebradas = {"activity": "Localización de quebradas", "quantity": "3", "unit": "quebradas", "fuente": "correo"}
        primero = msg("m1", "109:00", "Levantamiento de lote de 4 ha, 3 quebradas y 12 km de vía.")
        obs = detectar("Levantamiento de lote de 6 ha, 3 quebradas y 12 km de vía.", [lote(), quebradas], primero)
        self.assertEqual([(o["actividad"], o["clasificacion"]) for o in obs], [(1, CAMBIO_POSIBLE)])

    def test_un_mensaje_con_dos_valores_para_el_mismo_concepto_no_se_decide(self):
        # Cubre: T-69
        obs = detectar("Levantamiento de lote de 6 ha.\nLevantamiento de lote de 8 ha.")
        self.assertEqual([o["clasificacion"] for o in obs], [NO_COMPROBABLE])
        self.assertIn("varios valores", obs[0]["motivo"])

    def test_un_separador_ambiguo_nunca_demuestra_un_cambio(self):
        # Cubre: T-69
        eje = {"activity": "Eje vial", "quantity": "1.250 m", "unit": "m", "fuente": "correo"}
        primero = msg("m1", "109:00", "El eje vial mide 1.250 m.")
        obs = detectar("El eje vial mide 125 m.", [eje], primero)
        self.assertEqual([o["clasificacion"] for o in obs], [NO_COMPROBABLE])
        self.assertIn("ambiguo", obs[0]["motivo"])


class ProcedenciaTests(unittest.TestCase):
    def test_las_cifras_dentro_de_texto_citado_no_son_declaraciones_nuevas(self):
        # Cubre: T-69
        citado = ("Gracias por la cotización.\n\nEl lun, 19 oct 2026 a las 10:00, Cliente <c@ejemplo.co> escribió:\n"
                  "> Necesito el levantamiento de lote de 9 ha.\n")
        self.assertEqual(detectar(citado), [])
        reenvio = "Ok.\n\nDe: Ana <ana@ejemplo.co>\nEnviado: lunes\nPara: Anka\nAsunto: Lote\nLevantamiento de lote de 9 ha"
        self.assertEqual(detectar(reenvio), [])
        self.assertEqual(detectar("Ok.\n-----Original Message-----\nLevantamiento de lote de 9 ha"), [])

    def test_sin_citas_conserva_lo_escrito_y_quita_lo_citado(self):
        # Cubre: T-69
        self.assertEqual(cm.sin_citas("Hola\n> cita\nChao"), "Hola\nChao")
        self.assertEqual(cm.sin_citas("Hola\nOn Mon, 19 Oct 2026, X wrote:\n> viejo"), "Hola")
        self.assertEqual(cm.sin_citas("De: Anka es mi empresa.\nSigue el texto"), "De: Anka es mi empresa.\nSigue el texto")

    def test_un_mensaje_enviado_por_anka_nunca_prueba_una_rectificacion(self):
        # Cubre: T-69
        mensajes = [M1, msg("a1", "209:30", "Confirmamos el levantamiento de lote de 6 ha.", "anka@anka.co", "sent"),
                    msg("m2", "310:00", "Gracias.")]
        self.assertEqual(cm.detectar([lote()], mensajes, "T1"), [])
        # ni aunque llegue marcado como recibido pero la dirección sea una propia
        mensajes[1] = msg("a1", "209:30", "El área correcta es 6 ha, no 4 ha.", "anka@anka.co")
        self.assertEqual(cm.detectar([lote()], mensajes, "T1", propias=["ANKA@anka.co"]), [])

    def test_un_mensaje_sin_direccion_declarada_no_es_comprobable(self):
        # Cubre: T-69
        sin = {**msg("m2", "210:00", "El lote es de 6 ha."), "direccion": None}
        self.assertEqual(cm.detectar([lote()], [M1, sin], "T1"), [])    # no cuenta como recibido: queda un solo mensaje

    def test_texto_incompleto_o_truncado_no_es_comprobable(self):
        # Cubre: T-69
        obs = detectar("El lote es de 6 ha.", truncado=True)
        self.assertEqual([o["clasificacion"] for o in obs], [NO_COMPROBABLE])
        self.assertIn("incompleto", obs[0]["motivo"])
        self.assertEqual(obs[0]["message_id_posterior"], "")

    def test_remitente_no_verificable_no_es_comprobable(self):
        # Cubre: T-69
        for remitente in ("", "alguien", "<>"):
            with self.subTest(remitente=remitente):
                obs = detectar("El área correcta es 6 ha, no 4 ha.", remitente=remitente)
                self.assertEqual([o["clasificacion"] for o in obs], [NO_COMPROBABLE])
                self.assertIn("remitente", obs[0]["motivo"])

    def test_sin_fecha_verificable_no_se_puede_ordenar(self):
        # Cubre: T-69
        sin_fecha = {**msg("m2", "210:00", "El lote es de 6 ha."), "fecha": ""}
        obs = cm.detectar([lote()], [M1, sin_fecha], "T1")
        self.assertEqual([o["clasificacion"] for o in obs], [NO_COMPROBABLE])
        self.assertIn("fecha", obs[0]["motivo"])

    def test_sin_identificador_o_con_identificador_repetido_no_es_comprobable(self):
        # Cubre: T-69
        for segundo in ({**msg("", "210:00", "El lote es de 6 ha.")}, msg("m1", "210:00", "El lote es de 6 ha.")):
            obs = cm.detectar([lote()], [M1, segundo], "T1")
            self.assertEqual([o["clasificacion"] for o in obs], [NO_COMPROBABLE])

    def test_el_orden_lo_da_la_fecha_y_no_la_posicion_en_la_lista(self):
        # Cubre: T-69
        mensajes = [msg("m2", "210:00", "Les confirmo el levantamiento de lote de 6 ha."), M1]
        o = cm.detectar([lote()], mensajes, "T1")[0]
        self.assertEqual((o["message_id_origen"], o["message_id_posterior"]), ("m1", "m2"))

    def test_un_adjunto_nuevo_sin_procedencia_no_se_usa(self):
        # Cubre: T-69
        textos = {"thread_id": "T1", "correo": "x", "incompleto": False, "mensajes": [M1, msg("m2", "210:00", "Gracias.")],
                  "adjuntos": [{"filename": "nuevo.pdf", "source_id": "s", "coverage": "completa",
                                "text": "[Página 1]\nLevantamiento de lote: 9 ha\n"}]}
        resultado = ver.comprobar_tabla({"activities": [lote()]}, textos)
        self.assertEqual(resultado["cambios_de_magnitud"], [])

    def test_el_resultado_es_idempotente_y_no_modifica_las_entradas(self):
        # Cubre: T-69
        actividades = [lote()]
        mensajes = [M1, msg("m2", "210:00", "El área correcta es 6 ha, no 4 ha.")]
        antes = copy.deepcopy((actividades, mensajes))
        primero = cm.detectar(actividades, mensajes, "T1")
        self.assertEqual(primero, cm.detectar(actividades, mensajes, "T1"))
        self.assertEqual(primero, cm.detectar(actividades, list(reversed(mensajes)), "T1"))
        self.assertEqual((actividades, mensajes), antes)


class ModoSombraTests(unittest.TestCase):
    MENSAJES = [M1, msg("m2", "210:00", "El área correcta es 6 ha, no 4 ha.")]
    TEXTOS = {"thread_id": "T1", "revision": "r", "incompleto": False, "adjuntos": [],
              "correo": "Necesito el levantamiento de lote de 4 ha en Tenjo.\n\nEl área correcta es 6 ha, no 4 ha.",
              "mensajes": MENSAJES}
    TABLA = {"activities": [lote()]}

    def test_evidence_check_conserva_las_claves_y_los_cubos_y_suma_una_clave(self):
        # Cubre: T-69
        con = ver.comprobar_tabla(self.TABLA, self.TEXTOS)
        with patch.object(ver.cambios_de_magnitud, "detectar", return_value=[]):
            sin = ver.comprobar_tabla(self.TABLA, self.TEXTOS)
        sin_mensajes = ver.comprobar_tabla(self.TABLA, {k: v for k, v in self.TEXTOS.items() if k != "mensajes"})
        claves = {"comprobadas", "diferencias", "no_comprobables", "estado", "observaciones", "cambios_de_magnitud"}
        self.assertEqual((set(con), set(sin), set(sin_mensajes)), (claves, claves, claves))
        for clave in ("comprobadas", "diferencias", "no_comprobables", "estado", "observaciones"):
            self.assertEqual(con[clave], sin[clave])
            self.assertEqual(con[clave], sin_mensajes[clave])
        self.assertEqual([o["clasificacion"] for o in con["cambios_de_magnitud"]], [RECTIFICACION_EXPLICITA])
        self.assertEqual((sin["cambios_de_magnitud"], sin_mensajes["cambios_de_magnitud"]), ([], []))

    def test_los_avisos_visibles_no_incluyen_los_cambios(self):
        # Cubre: T-69
        resultado = ver.comprobar_tabla(self.TABLA, self.TEXTOS)
        self.assertTrue(resultado["cambios_de_magnitud"])
        self.assertEqual(ver.avisos_de(resultado), [])
        self.assertEqual(ver.avisos_de({**resultado, "diferencias": ["x"]}), ["Comprobación de fuentes: x"])

    def test_un_fallo_del_detector_no_frena_la_comprobacion_existente(self):
        # Cubre: T-69
        with patch.object(ver.cambios_de_magnitud, "detectar", side_effect=RuntimeError("falla")):
            with self.assertLogs("anka.dominio.verificacion_de_evidencia", level="WARNING"):
                resultado = ver.comprobar_tabla(self.TABLA, self.TEXTOS)
        self.assertEqual(resultado["cambios_de_magnitud"], [])
        self.assertTrue(resultado["observaciones"])
        self.assertEqual(resultado["comprobadas"], 1)

    def test_la_funcion_numerica_antigua_no_cambia(self):
        # Cubre: T-69
        self.assertTrue({4.0, 5.0, 4.5} <= ver.numeros_del_texto("4,5"))


class AlmacenamientoTests(unittest.TestCase):
    def test_los_mensajes_se_guardan_por_separado_con_su_procedencia(self):
        # Cubre: T-69
        with tempfile.TemporaryDirectory() as carpeta:
            copias.guardar_textos(Path(carpeta), "T1", "r", "correo", [], [M1, msg("m2", "210:00", "x" * 5)])
            guardado = copias.cargar_textos(Path(carpeta), "T1", "r")
        self.assertEqual([m["message_id"] for m in guardado["mensajes"]], ["m1", "m2"])
        self.assertEqual(guardado["mensajes"][0]["remitente"], "cliente@ejemplo.co")
        self.assertFalse(any(m["truncado"] for m in guardado["mensajes"]))

    def test_un_mensaje_demasiado_largo_se_recorta_y_queda_marcado(self):
        # Cubre: T-69
        largo = msg("m3", "311:00", "y" * (copias.MAX_TEXTO_POR_MENSAJE + 5))
        with tempfile.TemporaryDirectory() as carpeta:
            copias.guardar_textos(Path(carpeta), "T1", "r", "correo", [], [largo])
            guardado = copias.cargar_textos(Path(carpeta), "T1", "r")
        self.assertEqual(len(guardado["mensajes"][0]["texto"]), copias.MAX_TEXTO_POR_MENSAJE)
        self.assertTrue(guardado["mensajes"][0]["truncado"])

    def test_sin_mensajes_el_archivo_se_guarda_como_antes(self):
        # Cubre: T-69
        with tempfile.TemporaryDirectory() as carpeta:
            copias.guardar_textos(Path(carpeta), "T1", "r", "correo", [])
            guardado = copias.cargar_textos(Path(carpeta), "T1", "r")
        self.assertEqual(set(guardado), {"thread_id", "revision", "correo", "adjuntos", "incompleto"})

    def test_el_registro_de_un_mensaje_de_gmail_trae_identidad_fecha_y_remitente(self):
        # Cubre: T-69
        cuerpo = base64.urlsafe_b64encode(b"El lote es de 6 ha.").decode()
        gmail = {"id": "abc", "internalDate": "1760000000000", "labelIds": ["INBOX"],
                 "payload": {"mimeType": "text/plain", "headers": [{"name": "From", "value": "Ana <ANA@Ejemplo.co>"}],
                             "body": {"data": cuerpo}}}
        registro = lectura_de_adjuntos._registro_de_mensaje(gmail)
        self.assertEqual(registro, {"message_id": "abc", "fecha": "2025-10-09T08:53:20+00:00",
                                    "remitente": "ana@ejemplo.co", "direccion": "received",
                                    "texto": "El lote es de 6 ha.", "truncado": False})
        vacio = lectura_de_adjuntos._registro_de_mensaje({"id": "x", "payload": {}})
        self.assertEqual((vacio["fecha"], vacio["remitente"]), ("", ""))


if __name__ == "__main__":
    unittest.main()
