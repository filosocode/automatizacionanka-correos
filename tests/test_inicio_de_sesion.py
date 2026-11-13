"""Inicio de sesión por usuario (U-115, D-210): dominio, archivo de usuarios, sesiones y la puerta del servidor."""

from tests import _isolate  # noqa: F401  (debe ir primero)

import io
import json
import tempfile
import unittest
import urllib.parse
from datetime import timedelta
from pathlib import Path
from unittest.mock import patch

from anka.dominio import usuarios as dominio_usuarios
from anka.dominio.errores import ErrorDeAnka
from anka.dominio.reloj import ahora
from anka.infra import sesiones, settings, usuarios_store
from anka.servicios import autenticacion
from anka.web import server as web
from anka.web import seguridad


# --- dominio: hash, verificación y la espera tras fallos
class Hash(unittest.TestCase):
    def test_la_contraseña_correcta_verifica_y_la_incorrecta_no(self) -> None:
        # Cubre: U-115
        registro = dominio_usuarios.generar_hash("una-clave-larga")
        self.assertTrue(dominio_usuarios.verificar("una-clave-larga", registro))
        self.assertFalse(dominio_usuarios.verificar("otra-clave", registro))

    def test_el_registro_nunca_lleva_la_contraseña_en_claro(self) -> None:
        # Cubre: U-115, E-115g
        registro = dominio_usuarios.generar_hash("mi-contraseña-secreta")
        self.assertNotIn("mi-contraseña-secreta", json.dumps(registro))

    def test_nombres_validos_e_invalidos(self) -> None:
        # Cubre: U-115
        self.assertTrue(dominio_usuarios.nombre_valido("ana.gomez"))
        self.assertFalse(dominio_usuarios.nombre_valido("a"))
        self.assertFalse(dominio_usuarios.nombre_valido(".ana"))
        self.assertFalse(dominio_usuarios.nombre_valido("Ana"))


class EsperaTrasFallos(unittest.TestCase):
    def test_sin_fallos_suficientes_no_hay_espera(self) -> None:
        # Cubre: U-115
        self.assertEqual(dominio_usuarios.espera_tras(4), timedelta(0))
        self.assertIsNone(dominio_usuarios.bloqueada_hasta(4, ahora()))

    def test_al_quinto_fallo_espera_un_minuto_y_crece_si_sigue(self) -> None:
        # Cubre: U-115, E-115e
        self.assertEqual(dominio_usuarios.espera_tras(5), timedelta(minutes=1))
        self.assertEqual(dominio_usuarios.espera_tras(6), timedelta(minutes=2))
        self.assertLessEqual(dominio_usuarios.espera_tras(50), timedelta(minutes=30))

    def test_bloqueada_hasta_depende_del_ultimo_fallo(self) -> None:
        # Cubre: U-115
        momento = ahora()
        hasta = dominio_usuarios.bloqueada_hasta(5, momento)
        self.assertEqual(hasta, momento + timedelta(minutes=1))


# --- infra: usuarios.json
class AltaDeUsuarios(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        parche = patch.object(settings, "DATA_DIR", Path(self.temp.name))
        parche.start()
        self.addCleanup(parche.stop)

    def test_agregar_listar_y_quitar(self) -> None:
        # Cubre: U-115
        usuarios_store.agregar_o_actualizar("ana", "contraseña-larga")
        self.assertEqual(usuarios_store.listar(), ["ana"])
        self.assertTrue(usuarios_store.obtener("ana"))
        self.assertTrue(usuarios_store.quitar("ana"))
        self.assertFalse(usuarios_store.quitar("ana"))
        self.assertEqual(usuarios_store.listar(), [])

    def test_agregar_de_nuevo_cambia_la_contraseña(self) -> None:
        # Cubre: U-115
        usuarios_store.agregar_o_actualizar("ana", "contraseña-larga")
        primero = usuarios_store.obtener("ana")
        usuarios_store.agregar_o_actualizar("ana", "otra-contraseña")
        self.assertNotEqual(primero, usuarios_store.obtener("ana"))

    def test_nombre_o_contraseña_invalidos_se_rechazan(self) -> None:
        # Cubre: U-115
        with self.assertRaises(ErrorDeAnka):
            usuarios_store.agregar_o_actualizar("A", "contraseña-larga")
        with self.assertRaises(ErrorDeAnka):
            usuarios_store.agregar_o_actualizar("ana", "corta")

    def test_el_archivo_en_disco_nunca_tiene_la_contraseña_en_claro(self) -> None:
        # Cubre: U-115, E-115g
        usuarios_store.agregar_o_actualizar("ana", "mi-contraseña-secreta")
        contenido = (Path(self.temp.name) / "usuarios.json").read_text(encoding="utf-8")
        self.assertNotIn("mi-contraseña-secreta", contenido)


# --- infra: sesiones
class Sesiones(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        parche = patch.object(settings, "DATA_DIR", Path(self.temp.name))
        parche.start()
        self.addCleanup(parche.stop)
        sesiones._fallos.clear()

    def test_una_sesion_creada_se_reconoce_y_se_puede_cerrar(self) -> None:
        # Cubre: U-115, E-115h
        sid = sesiones.crear("ana", recordar=True)
        self.assertEqual(sesiones.usuario_de(sid), "ana")
        sesiones.cerrar(sid)
        self.assertIsNone(sesiones.usuario_de(sid))

    def test_una_sesion_vencida_ya_no_vale(self) -> None:
        # Cubre: U-115, E-115c
        with patch("anka.infra.sesiones.ahora", return_value=ahora() - timedelta(days=30)):
            sid = sesiones.crear("ana", recordar=True)
        self.assertIsNone(sesiones.usuario_de(sid))

    def test_sin_recordar_vence_antes_que_con_recordar(self) -> None:
        # Cubre: U-115, E-115c
        inicio = ahora()
        with patch("anka.infra.sesiones.ahora", return_value=inicio):
            sin_recordar = sesiones.crear("ana", recordar=False)
            con_recordar = sesiones.crear("ana", recordar=True)
        with patch("anka.infra.sesiones.ahora", return_value=inicio + timedelta(days=2)):
            self.assertIsNone(sesiones.usuario_de(sin_recordar))
            self.assertEqual(sesiones.usuario_de(con_recordar), "ana")

    def test_fallos_se_cuentan_y_un_exito_los_olvida(self) -> None:
        # Cubre: U-115, E-115f
        for _ in range(5):
            sesiones.registrar_fallo("ana")
        self.assertIsNotNone(sesiones.bloqueada_hasta("ana"))
        sesiones.registrar_exito("ana")
        self.assertIsNone(sesiones.bloqueada_hasta("ana"))


# --- servicio: autenticacion
class IniciarSesion(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        parche = patch.object(settings, "DATA_DIR", Path(self.temp.name))
        parche.start()
        self.addCleanup(parche.stop)
        sesiones._fallos.clear()
        usuarios_store.agregar_o_actualizar("ana", "contraseña-larga")

    def test_credenciales_correctas_abren_sesion(self) -> None:
        # Cubre: U-115, E-115b
        sid = autenticacion.iniciar_sesion("ana", "contraseña-larga", recordar=False)
        self.assertEqual(sesiones.usuario_de(sid), "ana")

    def test_usuario_inexistente_y_contraseña_incorrecta_dan_el_mismo_mensaje(self) -> None:
        # Cubre: U-115, E-115d
        with self.assertRaises(autenticacion.CredencialesInvalidas) as primero:
            autenticacion.iniciar_sesion("nadie", "lo-que-sea", recordar=False)
        with self.assertRaises(autenticacion.CredencialesInvalidas) as segundo:
            autenticacion.iniciar_sesion("ana", "clave-mala", recordar=False)
        self.assertEqual(str(primero.exception), str(segundo.exception))

    def test_al_sexto_fallo_queda_bloqueada_sin_gastar_el_intento(self) -> None:
        # Cubre: U-115, E-115e
        for _ in range(5):
            with self.assertRaises(autenticacion.CredencialesInvalidas):
                autenticacion.iniciar_sesion("ana", "clave-mala", recordar=False)
        with self.assertRaises(autenticacion.CuentaBloqueada):
            autenticacion.iniciar_sesion("ana", "contraseña-larga", recordar=False)

    def test_un_exito_despues_de_fallos_reinicia_el_conteo(self) -> None:
        # Cubre: U-115, E-115f
        for _ in range(4):
            with self.assertRaises(autenticacion.CredencialesInvalidas):
                autenticacion.iniciar_sesion("ana", "clave-mala", recordar=False)
        autenticacion.iniciar_sesion("ana", "contraseña-larga", recordar=False)
        for _ in range(4):
            with self.assertRaises(autenticacion.CredencialesInvalidas):
                autenticacion.iniciar_sesion("ana", "clave-mala", recordar=False)
        # el quinto de esta segunda tanda no debería estar bloqueado: el éxito de antes reinició el conteo
        sid = autenticacion.iniciar_sesion("ana", "contraseña-larga", recordar=False)
        self.assertIsNotNone(sid)


# --- seguridad: la cookie
class Cookie(unittest.TestCase):
    def test_con_recordar_lleva_max_age_y_sin_recordar_no(self) -> None:
        # Cubre: U-115, E-115c
        self.assertIn("Max-Age=", seguridad.cookie_de_sesion("abc", True))
        self.assertNotIn("Max-Age=", seguridad.cookie_de_sesion("abc", False))

    def test_se_lee_el_valor_de_la_cabecera_completa(self) -> None:
        # Cubre: U-115
        cabecera = "otra=1; anka_sesion=xyz; mas=2"
        self.assertEqual(seguridad.sesion_de_cookies(cabecera), "xyz")
        self.assertEqual(seguridad.sesion_de_cookies(""), "")


# --- el servidor: la puerta de sesión y /entrar, /salir (E-115a, E-115b, E-115h)
def request(method, path, body=b"", host="127.0.0.1:8765", origin=None, cookie=""):
    handler = web.ReviewHandler.__new__(web.ReviewHandler)
    sent = {}
    handler.headers = {"Host": host, "Content-Length": str(len(body))}
    if origin:
        handler.headers["Origin"] = origin
    if cookie:
        handler.headers["Cookie"] = cookie
    handler.path = path
    handler.rfile = io.BytesIO(body)
    handler.client_address = ("127.0.0.1", 1)
    handler._send_bytes = lambda status, ctype, data, extra=None: sent.update(
        status=status, type=ctype, body=data, headers=extra or {})
    handler._redirect = lambda location, extra=None: sent.update(status=303, location=location, headers=extra or {})
    getattr(handler, "do_" + method)()
    return sent


class PuertaDeSesion(unittest.TestCase):
    """Con la sesión real (no la de `prueba` de `_isolate`): así se prueba de verdad la puerta."""

    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        for parche in (patch.object(settings, "DATA_DIR", Path(self.temp.name)),
                       patch.object(autenticacion, "usuario_de_la_sesion", sesiones.usuario_de)):
            parche.start()
            self.addCleanup(parche.stop)
        sesiones._fallos.clear()
        usuarios_store.agregar_o_actualizar("ana", "contraseña-larga")

    def test_sin_sesion_una_pagina_redirige_a_entrar(self) -> None:
        # Cubre: U-115, E-115a
        sent = request("GET", "/requests")
        self.assertEqual(sent["status"], 303)
        self.assertTrue(sent["location"].startswith("/entrar"))

    def test_sin_sesion_una_ruta_de_api_devuelve_401(self) -> None:
        # Cubre: U-115, E-115a
        sent = request("GET", "/api/casos")
        self.assertEqual(sent["status"], 401)

    def test_entrar_es_publica_sin_sesion(self) -> None:
        # Cubre: U-115
        sent = request("GET", "/entrar")
        self.assertEqual(sent["status"], 200)

    def _csrf(self):
        from anka.web.seguridad import CSRF_TOKEN
        return CSRF_TOKEN

    def test_entrar_con_credenciales_correctas_abre_sesion_y_redirige(self) -> None:
        # Cubre: U-115, E-115b
        body = urllib.parse.urlencode({
            "usuario": "ana", "clave": "contraseña-larga", "csrf_token": self._csrf(), "next": "/requests",
        }).encode()
        sent = request("POST", "/entrar", body=body, origin="http://127.0.0.1:8765")
        self.assertEqual(sent["status"], 303)
        self.assertEqual(sent["location"], "/requests")
        cookie = sent["headers"]["Set-Cookie"]
        self.assertIn("anka_sesion=", cookie)
        valor = cookie.split("anka_sesion=")[1].split(";")[0]
        self.assertEqual(sesiones.usuario_de(valor), "ana")

    def test_entrar_con_credenciales_incorrectas_vuelve_con_aviso(self) -> None:
        # Cubre: U-115, E-115d
        body = urllib.parse.urlencode({
            "usuario": "ana", "clave": "mala", "csrf_token": self._csrf(),
        }).encode()
        sent = request("POST", "/entrar", body=body, origin="http://127.0.0.1:8765")
        self.assertEqual(sent["status"], 303)
        self.assertTrue(sent["location"].startswith("/entrar?"))
        self.assertIn("aviso=error", sent["location"])

    def test_un_next_ajeno_no_saca_del_dashboard(self) -> None:
        # Cubre: U-115
        body = urllib.parse.urlencode({
            "usuario": "ana", "clave": "contraseña-larga", "csrf_token": self._csrf(), "next": "https://otro.com/",
        }).encode()
        sent = request("POST", "/entrar", body=body, origin="http://127.0.0.1:8765")
        self.assertEqual(sent["location"], "/")

    def test_salir_cierra_la_sesion(self) -> None:
        # Cubre: U-115, E-115h
        sid = sesiones.crear("ana", recordar=False)
        body = urllib.parse.urlencode({"csrf_token": self._csrf()}).encode()
        sent = request("POST", "/salir", body=body, origin="http://127.0.0.1:8765", cookie=f"anka_sesion={sid}")
        self.assertEqual(sent["status"], 303)
        self.assertIsNone(sesiones.usuario_de(sid))


if __name__ == "__main__":
    unittest.main()
