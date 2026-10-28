"""T-61 (Mejora 3): el panel «Recepción y trabajos de Work» muestra lo registrado en SQLite, sin consultar Gmail ni Drive,
sin escribir nada y sin dar por «funcionando» lo que solo está registrado.

SQLite temporal y el manejador real del servidor.

Cubre: T-61
"""

from tests import _isolate  # noqa: F401  (debe ir primero)

import re
import unittest
from unittest.mock import patch

from anka.dominio import trabajo_work as tw
from anka.infra import store
from anka.servicios.estado_operativo import estado_operativo
from tests.test_app import test_config
from tests.test_continuidad_de_casos import Contexto
from tests.test_openapi import request

AHORA = "2026-10-20T09:00:00-05:00"


class PanelBase(Contexto):
    def mensaje(self, mensaje, estado="procesado", hilo="t-1", **extra):
        valores = {"detected_at": AHORA, "updated_at": AHORA, "processed_at": None, "disposition": "",
                   "error_kind": "", "attempts": 0, **extra}
        with store.transaccion() as db:
            db.execute("""INSERT INTO recepcion_mensajes(account, thread_id, message_id, state, disposition, error_kind,
                          attempts, detected_at, processed_at, updated_at) VALUES('cuenta', ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                       (hilo, mensaje, estado, valores["disposition"], valores["error_kind"], valores["attempts"],
                        valores["detected_at"], valores["processed_at"], valores["updated_at"]))

    def trabajo(self, job, estado, hilo="t-1", **extra):
        valores = {"created_at": AHORA, "published_at": None, "conciliated_at": None, "updated_at": AHORA,
                   "attempts": 0, "last_error": "", **extra}
        with store.transaccion() as db:
            db.execute("""INSERT INTO trabajos_work(job_id, account, thread_id, message_id, job_type, contract_version,
                          state, attempts, last_error, created_at, published_at, conciliated_at, updated_at)
                          VALUES(?, 'cuenta', ?, ?, ?, 'v', ?, ?, ?, ?, ?, ?, ?)""",
                       (job, hilo, "m-" + job, tw.TIPO_ANALIZAR, estado, valores["attempts"], valores["last_error"],
                        valores["created_at"], valores["published_at"], valores["conciliated_at"],
                        valores["updated_at"]))

    def pagina(self, ruta="/operacion"):
        respuesta = request("GET", ruta)
        self.assertEqual(respuesta["status"], 200)
        return respuesta["body"].decode("utf-8")

    def texto(self, html=None):
        return " ".join(re.sub(r"<[^>]+>", " ", html or self.pagina()).split())

    def foto(self):
        with store.lectura() as db:
            return {t: db.execute(f"SELECT * FROM {t}").fetchall() and
                    [tuple(r) for r in db.execute(f"SELECT * FROM {t} ORDER BY 1, 2").fetchall()]
                    for t in ("recepcion_mensajes", "trabajos_work", "recepcion_control", "quotes", "case_events")}


class Vacio(PanelBase):
    def test_sin_mensajes_ni_trabajos_lo_explica(self):
        texto = self.texto()
        self.assertIn("Todavía no hay mensajes registrados", texto)
        self.assertIn("Todavía no hay trabajos para Work", texto)
        self.assertEqual(estado_operativo()["total_de_mensajes"], 0)

    def test_dice_que_registrado_no_es_funcionando_y_no_comprueba_servicios(self):
        texto = self.texto()
        self.assertIn("Registrado no es lo mismo que funcionando", texto)
        self.assertEqual(texto.count("Estado no comprobado"), 2)
        self.assertNotIn("Funcionando", texto)

    def test_un_mensaje_reciente_no_convierte_nada_en_funcionando(self):
        self.mensaje("m1", "procesado", processed_at=AHORA)
        self.trabajo("ANKAJOB-1", tw.CONCILIADO, conciliated_at=AHORA)
        self.assertNotIn("Funcionando", self.texto())
        self.assertEqual(self.texto().count("Estado no comprobado"), 2)

    def test_el_puente_se_muestra_con_su_propio_latido(self):
        with patch("anka.servicios.estado_operativo.bridge_state", return_value=("ok", "Activo según su latido.")):
            texto = self.texto()
        self.assertIn("Comprobado con su latido local: Activo según su latido.", texto)


class Mensajes(PanelBase):
    def test_cuenta_por_estado_real_y_sin_mezclar_categorias(self):
        for n, estado in enumerate(["por_leer", "por_leer", "requiere_reintento", "revision_manual", "procesado",
                                    "procesado", "procesado"]):
            self.mensaje(f"m{n}", estado, disposition="comercial" if estado == "procesado" else "")
        datos = estado_operativo()
        self.assertEqual({c["estado"]: c["n"] for c in datos["mensajes"]},
                         {"por_leer": 2, "requiere_reintento": 1, "revision_manual": 1, "procesado": 3})
        self.assertEqual(datos["total_de_mensajes"], 7)
        gravedad = {c["estado"]: c["gravedad"] for c in datos["mensajes"]}
        self.assertEqual((gravedad["por_leer"], gravedad["revision_manual"], gravedad["procesado"]),
                         ("pendiente", "error", "ok"))
        texto = self.texto()
        self.assertIn("Registrado y clasificado 3", texto)
        self.assertIn("comercial: 3", texto)
        self.assertEqual(len(datos["atencion"]), 4)             # los procesados no se listan como «no listos»

    def test_un_estado_desconocido_se_muestra_con_su_codigo(self):
        self.mensaje("m1", "algo_nuevo")
        self.assertIn("algo_nuevo", self.texto())

    def test_fechas_presentes_y_ausentes(self):
        self.mensaje("m1", "procesado", processed_at="2026-10-20T10:30:00-05:00")
        texto = self.texto()
        self.assertIn("Último mensaje detectado: 2026-10-20 09:00", texto)
        self.assertIn("Último procesado: 2026-10-20 10:30", texto)
        with store.transaccion() as db:
            db.execute("UPDATE recepcion_mensajes SET processed_at=NULL")
        self.assertIn("Último procesado: sin fecha registrada", self.texto())

    def test_el_intento_de_lectura_se_presenta_como_anotado_no_como_comprobacion(self):
        with store.transaccion() as db:
            db.execute("""INSERT INTO recepcion_control(account, history_id, last_success_at, last_attempt_at,
                          consecutive_errors, last_error_kind, updated_at) VALUES('cuenta', 'h', ?, ?, 3, 'red', ?)""",
                       ("2026-10-20T08:00:00-05:00", AHORA, AHORA))
        texto = self.texto()
        self.assertIn("último con éxito: 2026-10-20 08:00", texto)
        self.assertIn("3 errores seguidos", texto)
        self.assertIn("no una comprobación actual", texto)

    def test_no_muestra_remitente_asunto_ni_texto_del_mensaje(self):
        self.mensaje("m1", "por_leer")
        with store.transaccion() as db:
            db.execute("UPDATE recepcion_mensajes SET sender='secreto@cliente.com', subject='ASUNTO PRIVADO', "
                       "snippet='CUERPO PRIVADO'")
        html = self.pagina()
        for privado in ("secreto@cliente.com", "ASUNTO PRIVADO", "CUERPO PRIVADO"):
            self.assertNotIn(privado, html)


class Trabajos(PanelBase):
    def test_trabajos_por_estado_con_su_fecha_relevante(self):
        self.trabajo("ANKAJOB-A", tw.PENDIENTE_DE_PUBLICAR)
        self.trabajo("ANKAJOB-B", tw.PUBLICADO, published_at="2026-10-20T09:05:00-05:00")
        self.trabajo("ANKAJOB-C", tw.CONCILIADO, conciliated_at="2026-10-20T09:30:00-05:00")
        datos = estado_operativo()
        self.assertEqual({c["estado"]: c["n"] for c in datos["trabajos"]},
                         {tw.PENDIENTE_DE_PUBLICAR: 1, tw.PUBLICADO: 1, tw.CONCILIADO: 1})
        texto = self.texto()
        self.assertIn("ANKAJOB-A", texto)
        self.assertIn("publicado 2026-10-20 09:05", texto)
        self.assertIn("conciliado 2026-10-20 09:30", texto)
        self.assertIn("creado 2026-10-20 09:00", texto)
        self.assertIn("Ninguno sin cerrar tiene un error anotado", texto)

    def test_ambiguo_y_error_anotado_se_distinguen_de_los_pendientes(self):
        self.trabajo("ANKAJOB-X", tw.AMBIGUO, last_error="La marca aparece dos veces")
        self.trabajo("ANKAJOB-Y", tw.PENDIENTE_DE_PUBLICAR, attempts=3, last_error="Drive no respondió")
        self.trabajo("ANKAJOB-Z", tw.PENDIENTE_DE_PUBLICAR)
        datos = estado_operativo()
        self.assertEqual(datos["con_error"], 2)
        self.assertEqual(datos["lista_de_trabajos"][0]["job_id"], "ANKAJOB-X")          # lo ambiguo primero
        gravedad = {t["job_id"]: t["gravedad"] for t in datos["lista_de_trabajos"]}
        self.assertEqual((gravedad["ANKAJOB-X"], gravedad["ANKAJOB-Z"]), ("error", "pendiente"))
        texto = self.texto()
        self.assertIn("2 sin cerrar con un error anotado", texto)
        self.assertIn("La marca aparece dos veces", texto)

    def test_un_error_de_un_trabajo_conciliado_ya_no_cuenta(self):
        self.trabajo("ANKAJOB-C", tw.CONCILIADO, last_error="viejo", conciliated_at=AHORA)
        self.assertEqual(estado_operativo()["con_error"], 0)

    def test_expediente_existente_e_inexistente(self):
        self.trabajo("ANKAJOB-1", tw.PUBLICADO, hilo="t-1")
        self.trabajo("ANKAJOB-2", tw.PUBLICADO, hilo="no-existe")
        self.mensaje("m1", "revision_manual", hilo="no-existe", error_kind="id_en_otro_hilo")
        html = self.pagina()
        self.assertIn('href="/case?thread_id=t-1"', html)
        self.assertNotIn("no-existe", html)
        self.assertEqual(self.texto(html).count("Sin expediente en Anka"), 2)

    def test_las_listas_son_cortas_pero_los_conteos_exactos(self):
        for n in range(30):
            self.trabajo(f"ANKAJOB-{n:02d}", tw.CONCILIADO, conciliated_at=AHORA)
        datos = estado_operativo()
        self.assertEqual((datos["total_de_trabajos"], len(datos["lista_de_trabajos"])), (30, 25))
        self.assertIn("Se muestran los 25 más importantes de 30", self.texto())


class Seguridad(PanelBase):
    def test_los_textos_inesperados_se_escapan(self):
        mal = '<script>alert("x")</script>'
        self.trabajo("ANKAJOB-1", tw.AMBIGUO, last_error=mal)
        self.mensaje("m1", "requiere_reintento", error_kind=mal)
        self.mensaje("m2", "estado<b>raro</b>")
        html = self.pagina()
        self.assertNotIn("<script>", html)
        self.assertNotIn("<b>raro</b>", html)
        self.assertIn("&lt;script&gt;", html)

    def test_el_error_largo_se_recorta(self):
        self.trabajo("ANKAJOB-1", tw.AMBIGUO, last_error="x" * 5000)
        self.assertLess(len(estado_operativo()["lista_de_trabajos"][0]["error"]), 250)

    def test_no_ofrece_acciones(self):
        self.trabajo("ANKAJOB-1", tw.PENDIENTE_DE_PUBLICAR)
        self.mensaje("m1", "por_leer")
        html = self.pagina()
        cuerpo = html[html.index("<h1>Recepción y trabajos"):html.rindex("</section>")]
        self.assertNotIn("<form", cuerpo)
        self.assertNotIn("<button", cuerpo)

    def test_consultar_el_panel_no_escribe_nada(self):
        self.mensaje("m1", "por_leer")
        self.trabajo("ANKAJOB-1", tw.AMBIGUO, last_error="x")
        antes = self.foto()
        self.pagina()
        self.pagina()
        self.assertEqual(self.foto(), antes)


class Portada(PanelBase):
    def test_la_portada_sigue_igual_y_enlaza_al_panel(self):
        with patch("anka.web.paginas.load_config", return_value=test_config()):
            html = self.pagina("/")
        self.assertIn('href="/operacion">Recepción y trabajos de Work</a>', html)
        self.assertNotIn("Registrado no es lo mismo", html)

    def test_la_ruta_esta_documentada_y_solo_acepta_get(self):
        from anka.web import openapi, server
        self.assertIn("/operacion", server.GET_ROUTES)
        self.assertNotIn("/operacion", server.POST_ROUTES)
        self.assertIn("/operacion", openapi.rutas_documentadas()[0])


if __name__ == "__main__":
    unittest.main()
