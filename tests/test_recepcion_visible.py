"""La recepción es visible antes del análisis y no inventa actividad de Work."""
from tests import _isolate  # noqa: F401
from datetime import datetime
from unittest.mock import patch
from anka.infra import store
from anka.dominio.trabajo_work import evaluar_elegibilidad, SENAL_NOTIFICACION_DE_CHATGPT
from anka.servicios.recepcion_visible import solicitudes_en_recepcion
from anka.servicios.quotes import change_marker
from anka.web import paginas
from tests.test_suficiencia_documental import Base, analisis_valido


class RecepcionVisible(Base):
    def setUp(self):
        super().setUp()
        # La lista «Recibidas» solo existe en el modo Work; el modo API (el que se usa) no la muestra (U-110)
        parche = patch('anka.web.paginas._en_modo_work', return_value=True)
        parche.start()
        self.addCleanup(parche.stop)

    def test_en_modo_api_la_portada_no_habla_de_work(self):
        # Cubre: U-110
        self.recibir()
        self.trabajo()
        with patch('anka.web.paginas._en_modo_work', return_value=False):
            pagina = paginas.render_index()
        self.assertNotIn('Esperando análisis de Work', pagina)
        self.assertNotIn('Recibidas · pendientes de análisis', pagina)

    def recibir(self, mensaje='m1', hilo='t1', state='pendiente_de_procesar', asunto='Solicitud La Calera'):
        with store.transaccion() as db:
            db.execute("""INSERT INTO recepcion_mensajes
                (account,thread_id,message_id,state,subject,sender,detected_at,updated_at,internal_date)
                VALUES ('ankacomercialia@gmail.com',?,?,?,?,?,?,?,?)""",
                (hilo,mensaje,state,asunto,'91andresmunoz@gmail.com','2026-11-04T09:16:42-05:00',
                 '2026-11-04T09:16:42-05:00',mensaje))

    def trabajo(self, estado='publicado', mensaje='m1', error=''):
        with store.transaccion() as db:
            db.execute("""INSERT INTO trabajos_work
                (job_id,account,thread_id,message_id,job_type,contract_version,state,last_error,created_at,updated_at)
                VALUES (?,?,?,?,?,?,?,?,?,?)""",('job-'+mensaje,'ankacomercialia@gmail.com','t1',mensaje,
                'analizar_solicitud','ANKA_DASHBOARD_JSON_V1',estado,error,'2026-11-04','2026-11-04'))

    def test_aparece_sin_analisis_y_refresca_al_recibir(self):
        antes=change_marker()
        self.recibir()
        self.assertNotEqual(antes,change_marker())
        pagina=paginas.render_index()
        self.assertIn('1 solicitud · 1 recibida pendiente de análisis',pagina)
        self.assertIn('Solicitud La Calera',pagina)
        self.assertIn('Correo recibido',pagina)
        self.assertNotIn('Todavía no hay solicitudes de cotización.',pagina)
        with store.lectura() as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM quotes').fetchone()[0],0)

    def test_publicado_no_significa_analizando_y_el_avance_refresca(self):
        self.recibir()
        antes=change_marker()
        self.trabajo()
        self.assertNotEqual(antes,change_marker())
        item=solicitudes_en_recepcion()[0]
        self.assertEqual(item['estado'],'Esperando análisis de Work')
        self.assertIn('Todavía no se ha recibido',item['detalle'])
        self.assertNotIn('Analizando',paginas.render_index())

    def test_una_fila_por_hilo_y_respuesta_no_suma_otra_solicitud(self):
        self.recibir()
        self.recibir('m2')
        self.assertEqual(len(solicitudes_en_recepcion()),1)
        self.guardar('t1',analisis_valido())
        pagina=paginas.render_index()
        self.assertNotIn('2 solicitudes',pagina)
        self.assertIn('Respuesta de una solicitud existente',pagina)
        self.assertIn('/case?thread_id=t1',pagina)

    def test_importado_cancelado_y_ruido_no_aparecen(self):
        self.recibir()
        self.trabajo('conciliado')
        self.recibir('m2',state='procesado')
        self.recibir('m3',hilo='t3',state='procesado')
        self.assertEqual(solicitudes_en_recepcion(),[])
        with store.transaccion() as db:
            db.execute("UPDATE trabajos_work SET state='cancelado'")
        self.assertEqual(solicitudes_en_recepcion(),[])

    def test_fallo_visible_y_asunto_hostil_escapado(self):
        self.recibir(asunto='<script>alert(1)</script>')
        self.trabajo(error='red')
        pagina=paginas.render_index()
        self.assertIn('Requiere atención',pagina)
        self.assertIn('&lt;script&gt;alert(1)&lt;/script&gt;',pagina)
        self.assertNotIn('<script>alert(1)</script>',pagina)

    def test_notificacion_de_work_no_se_cuenta_como_solicitud(self):
        self.recibir(asunto='[Task Update] ANKA cotizaciones')
        with store.transaccion() as db:
            db.execute("""UPDATE recepcion_mensajes SET sender='noreply@tm.openai.com',
                evidence_json='{"noise_signal":"remitente_no_reply"}'""")
            fila = dict(db.execute('SELECT * FROM recepcion_mensajes').fetchone())
        self.assertEqual(evaluar_elegibilidad(fila, None).motivo_id, SENAL_NOTIFICACION_DE_CHATGPT)
        self.assertEqual(solicitudes_en_recepcion(),[])

    def test_encolado_sin_cabeceras_no_aparece_como_solicitud_sin_asunto(self):
        self.recibir(state='por_leer',asunto='')
        with store.transaccion() as db:
            db.execute("UPDATE recepcion_mensajes SET sender=''")
        self.assertEqual(solicitudes_en_recepcion(),[])
        self.assertNotIn('Correo sin asunto',paginas.render_index())
        # Un correo humano realmente sin asunto sí aparece tras leer sus cabeceras.
        with store.transaccion() as db:
            db.execute("UPDATE recepcion_mensajes SET state='pendiente_de_procesar',sender='91andresmunoz@gmail.com'")
        self.assertEqual(len(solicitudes_en_recepcion()),1)

    def test_espera_se_mide_desde_publicacion_y_avisa_en_el_umbral(self):
        self.recibir()
        self.trabajo()
        with store.transaccion() as db:
            db.execute("UPDATE trabajos_work SET published_at='2026-11-04T09:20:00-05:00'")
        with patch('anka.servicios.recepcion_visible.reloj.ahora',return_value=datetime.fromisoformat('2026-11-04T15:49:00+01:00')), \
             patch('anka.servicios.recepcion_visible.settings.load_config',return_value={'work_coordination_alert_minutes':30}):
            item=solicitudes_en_recepcion()[0]
            self.assertEqual(item['tiempo'],'Hace 29 min')
            self.assertFalse(item['demora'])
        with patch('anka.servicios.recepcion_visible.reloj.ahora',return_value=datetime.fromisoformat('2026-11-04T09:50:00-05:00')), \
             patch('anka.servicios.recepcion_visible.settings.load_config',return_value={'work_coordination_alert_minutes':30}):
            item=solicitudes_en_recepcion()[0]
            self.assertTrue(item['demora'])
            self.assertIn('Revisar la tarea',item['siguiente'])

    def test_detalle_plegado_con_evidencia_y_sin_declarar_archivos_leidos(self):
        self.recibir()
        self.trabajo()
        with store.transaccion() as db:
            db.execute("""UPDATE recepcion_mensajes SET attempts=3,
                attachments_json='[{"filename":"alcance.pdf"}]'""")
            db.execute("UPDATE trabajos_work SET sheet_file_id='hoja-prueba',published_at='2026-11-04T09:20:00-05:00'")
        pagina=paginas.render_index()
        self.assertIn('Ver seguimiento y archivos',pagina)
        self.assertIn('<details class="more">',pagina)
        self.assertNotIn('<details class="more" open',pagina)
        self.assertIn('alcance.pdf',pagina)
        self.assertIn('Reintentos registrados: 2',pagina)
        self.assertIn('esto no confirma que ya se hayan leído o validado',pagina)
        self.assertIn('https://docs.google.com/spreadsheets/d/hoja-prueba',pagina)
        self.assertIn('authuser=ankacomercialia%40gmail.com#all/t1',pagina)

    def test_fecha_desconocida_y_adjunto_malformado_no_rompen_la_vista(self):
        self.recibir()
        with store.transaccion() as db:
            db.execute("UPDATE recepcion_mensajes SET detected_at='sin fecha',attachments_json='no json'")
        item=solicitudes_en_recepcion()[0]
        self.assertEqual(item['tiempo'],'Tiempo por confirmar')
        self.assertEqual(item['archivos'],[])
        self.assertFalse(item['demora'])
