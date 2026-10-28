"""Pruebas del controlador de la sesión C1 (T-90): Gmail, Drive y SQLite simulados o temporales; nada toca Google ni lo real.

Se ejecuta con: python -m unittest laboratorio.banco_documental.ejecucion_c1.test_c1
"""

from __future__ import annotations

from tests import _isolate  # noqa: F401  (debe ir primero: manda los datos a una carpeta temporal)

import base64
import contextlib
import hashlib
import io
import json
import sqlite3
import tempfile
import time
import unittest
from pathlib import Path
from typing import Any
from unittest import mock

from anka.dominio.cancelacion_work import Autorizacion
from anka.infra import lapidas, store
from anka.servicios import cancelaciones, despacho
from anka.servicios.despacho import Alcance
from laboratorio.banco_documental.ejecucion_c1 import c1
from tests.test_aislamiento_t79 import REAL
from tests.test_aislamiento_t79 import Base as BaseDeT79
from tests.test_coordinacion import CARPETA, ConfiguracionTests

C1 = ("TC1", "mC1")
SIN_HOJAS = lambda _job: []  # noqa: E731


class _Llamada:
    def __init__(self, valor: Any = None, error: Exception | None = None) -> None:
        self.valor, self.error = valor, error

    def execute(self) -> Any:
        if self.error:
            raise self.error
        return self.valor


PDF_C1 = (Path(c1.__file__).resolve().parents[1] / "documentos" / "solicitud_01.pdf").read_bytes()
PDF_MISMO_NOMBRE = bytes([PDF_C1[0] ^ 1]) + PDF_C1[1:]                # mismo nombre y tamaño, otro contenido
PDF_DE_T91 = b"%PDF-1.4 otro archivo " + bytes(range(256)) * 192      # 49 394 bytes como el recibido en T-91


def adjunto(nombre: str = c1.ADJUNTO, contenido: bytes | None = PDF_C1, declarado: int | None = None,
            id_: str = "att1") -> dict[str, Any]:
    """Una parte con adjunto; `contenido=None` simula un adjunto sin contenido accesible."""
    cuerpo: dict[str, Any] = {"size": len(contenido or b"") if declarado is None else declarado}
    if contenido is not None:
        cuerpo["attachmentId"] = id_
    return {"filename": nombre, "mimeType": "application/pdf", "body": cuerpo, "_contenido": contenido}


def mensaje(id_: str = "g1", hilo: str = "gt1", asunto: str = c1.ASUNTO, de: str = c1.REMITENTE,
            para: str = c1.DESTINATARIO, labels: tuple[str, ...] = ("INBOX",), cc: str = "",
            adjuntos: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    cabeceras = [{"name": "From", "value": f"Operador <{de}>"}, {"name": "To", "value": para},
                 {"name": "Subject", "value": asunto}]
    if cc:
        cabeceras.append({"name": "Cc", "value": cc})
    partes = [{"mimeType": "text/plain", "filename": "", "body": {"size": 10}}] + (adjuntos if adjuntos is not None else [adjunto()])
    return {"id": id_, "threadId": hilo, "labelIds": list(labels), "payload": {"headers": cabeceras, "parts": partes}}


class _Adjuntos:
    def __init__(self, gmail: GmailSim) -> None:
        self.gmail = gmail

    def get(self, userId: str, messageId: str, id: str) -> _Llamada:  # noqa: N803,A002
        self.gmail.metodos.append("attachments.get")
        if self.gmail.falla_adjunto:
            return _Llamada(error=TimeoutError("sin red"))
        for m in self.gmail.mensajes:
            for parte in m["payload"]["parts"]:
                if parte.get("body", {}).get("attachmentId") == id and parte.get("_contenido") is not None:
                    return _Llamada({"data": base64.urlsafe_b64encode(parte["_contenido"]).decode()})
        return _Llamada({})


class GmailSim:
    """Lo mínimo de Gmail que usa el controlador: lista por consulta (filtra por el marcador), mensaje, hilo y adjuntos."""

    def __init__(self, mensajes: list[dict[str, Any]] | None = None, falla_lista: bool = False, falla_mensaje: bool = False,
                 mensajes_en_hilo: int = 1, falla_adjunto: bool = False) -> None:
        self.mensajes, self.falla_lista, self.falla_mensaje, self.en_hilo = mensajes or [], falla_lista, falla_mensaje, mensajes_en_hilo
        self.falla_adjunto = falla_adjunto
        self.consultas: list[str] = []
        self.metodos: list[str] = []

    def users(self) -> GmailSim:
        return self

    def messages(self) -> GmailSim:
        return self

    def threads(self) -> GmailSim:
        return self

    def attachments(self) -> _Adjuntos:
        return _Adjuntos(self)

    def _asunto(self, m: dict[str, Any]) -> str:
        return next(h["value"] for h in m["payload"]["headers"] if h["name"] == "Subject")

    def list(self, userId: str, q: str, includeSpamTrash: bool, maxResults: int) -> _Llamada:  # noqa: N803
        self.metodos.append("list")
        self.consultas.append(q)
        if self.falla_lista:
            return _Llamada(error=TimeoutError("sin red"))
        marcador = q.split('"')[1]
        return _Llamada({"messages": [{"id": m["id"], "threadId": m["threadId"]} for m in self.mensajes if marcador.lower() in self._asunto(m).lower()]})

    def get(self, userId: str, id: str, format: str, metadataHeaders: list[str] | None = None) -> _Llamada:  # noqa: N803,A002
        self.metodos.append("get")
        if self.falla_mensaje:
            return _Llamada(error=TimeoutError("sin red"))
        if format == "minimal":
            return _Llamada({"messages": [{"id": f"x{i}"} for i in range(self.en_hilo)]})
        return _Llamada(next(m for m in self.mensajes if m["id"] == id))


def valido() -> c1.Hallazgo:
    return c1.buscar_en_gmail(GmailSim([mensaje(id_=C1[1], hilo=C1[0])]))


def con_adjunto_incorrecto() -> c1.Hallazgo:
    return c1.buscar_en_gmail(GmailSim([mensaje(id_=C1[1], hilo=C1[0], adjuntos=[adjunto("solicitud_ficticia_alternativa_NO_C1_original.pdf",
                                                                                        PDF_DE_T91)])]))


class Base(BaseDeT79):
    def hechos(self, ids: tuple[str, str] = C1) -> c1.Hechos:
        with store.lectura() as db:
            return c1.hechos_de_base(db, *ids)

    def hojas_de(self, job_id: str) -> list[dict[str, Any]]:
        return self.drive.vivos(job_id)

    def evaluar(self, hallazgo: c1.Hallazgo, envio: str = "", hojas: Any = None) -> c1.Veredicto:
        return c1.evaluar(hallazgo, envio, self.hechos() if hallazgo.clase == "unico_valido" else None,
                          hojas or self.hojas_de, lapidas.esta_registrada)

    def registrar_c1(self, asunto: str = c1.ASUNTO, adjuntos: list[dict[str, Any]] | None = None, ids: tuple[str, str] = C1) -> None:
        self.fila(*ids, asunto=asunto, remitente=c1.REMITENTE)
        lista = adjuntos if adjuntos is not None else [{"filename": c1.ADJUNTO, "size_bytes": c1.BYTES_PDF}]
        with store.transaccion() as db:
            db.execute("UPDATE recepcion_mensajes SET attachments_json=? WHERE thread_id=? AND message_id=?",
                       (json.dumps(lista), *ids))

    def autorizacion(self, **cambios: Any) -> Autorizacion:
        datos: dict[str, Any] = {"thread_id": C1[0], "message_id": C1[1], "operador": "operador.prueba",
                                 "motivo": "aborto de C1", "etiqueta": c1.MARCA}
        datos.update(cambios)
        return Autorizacion(**datos)


class GmailTest(unittest.TestCase):
    def test_01_un_mensaje_entrante_exacto_es_valido_y_devuelve_los_ids_del_buzon_destino(self) -> None:
        gmail = GmailSim([mensaje(id_="g9", hilo="h9")])
        h = c1.buscar_en_gmail(gmail)
        self.assertEqual((h.clase, h.thread_id, h.message_id), ("unico_valido", "h9", "g9"))
        self.assertEqual(gmail.consultas, [f'subject:"{c1.MARCA}" in:anywhere'], "marcador exacto, solo lectura")

    def test_02_sin_resultados_es_ninguno(self) -> None:
        self.assertEqual(c1.buscar_en_gmail(GmailSim([])).clase, "ninguno")

    def test_03_varios_resultados_se_rechazan(self) -> None:
        h = c1.buscar_en_gmail(GmailSim([mensaje(id_="a", hilo="ha"), mensaje(id_="b", hilo="hb")]))
        self.assertEqual(h.clase, "multiples")

    def test_04_un_mensaje_enviado_por_la_cuenta_comercial_se_rechaza(self) -> None:
        for kwargs in ({"de": c1.DESTINATARIO}, {"labels": ("SENT",)}, {"labels": ("DRAFT",)}):
            h = c1.buscar_en_gmail(GmailSim([mensaje(**kwargs)]))
            self.assertEqual(h.clase, "invalido", kwargs)

    def test_05_una_coincidencia_solo_en_texto_citado_o_respuesta_se_rechaza(self) -> None:
        for asunto in ("Re: " + c1.ASUNTO, "Fwd: " + c1.ASUNTO, c1.ASUNTO + " (copia)", c1.ASUNTO.lower()):
            h = c1.buscar_en_gmail(GmailSim([mensaje(asunto=asunto)]))
            self.assertEqual(h.clase, "invalido", asunto)
            self.assertIn("asunto", h.detalle)

    def test_06_remitente_destinatario_copia_y_hilo_se_verifican(self) -> None:
        casos = {"remitente": {"de": "otro@example.com"}, "destinatario": {"para": "otro@example.com"},
                 "copia": {"cc": "tercero@example.com"}, "dos destinatarios": {"para": f"{c1.DESTINATARIO}, x@example.com"}}
        for nombre, kwargs in casos.items():
            self.assertEqual(c1.buscar_en_gmail(GmailSim([mensaje(**kwargs)])).clase, "invalido", nombre)
        self.assertEqual(c1.buscar_en_gmail(GmailSim([mensaje()], mensajes_en_hilo=2)).clase, "invalido", "hilo con 2 mensajes")

    def test_07_gmail_inaccesible_no_se_confunde_con_ausencia(self) -> None:
        self.assertEqual(c1.buscar_en_gmail(GmailSim([], falla_lista=True)).clase, "inaccesible")
        self.assertEqual(c1.buscar_en_gmail(GmailSim([mensaje()], falla_mensaje=True)).clase, "inaccesible")

    def test_08_un_mensaje_real_del_buzon_nunca_es_el_de_c1(self) -> None:
        sin_marcador = mensaje(id_="r1", hilo="hr", asunto="Solicitud de levantamiento", de="cliente@example.com")
        self.assertEqual(c1.buscar_en_gmail(GmailSim([sin_marcador])).clase, "ninguno", "la consulta nunca lo selecciona")
        cita_el_marcador = mensaje(id_="r2", hilo="hr2", asunto="Re: " + c1.ASUNTO, de="cliente@example.com")
        self.assertEqual(c1.buscar_en_gmail(GmailSim([cita_el_marcador])).clase, "invalido")

    def test_09_solo_se_usan_metodos_de_lectura(self) -> None:
        gmail = GmailSim([mensaje()])
        c1.buscar_en_gmail(gmail)
        self.assertEqual(set(gmail.metodos), {"list", "get", "attachments.get"})


class RestauracionTest(Base):
    def test_10_no_enviado_con_confirmacion_y_gmail_vacio_es_ausencia_comprobada(self) -> None:
        v = self.evaluar(c1.Hallazgo("ninguno"), envio="no_enviado")
        self.assertEqual((v.permitido, v.categoria), (True, "ausencia_comprobada"))

    def test_11_la_simple_ausencia_en_gmail_no_prueba_que_no_se_envio(self) -> None:
        v = self.evaluar(c1.Hallazgo("ninguno"), envio="")
        self.assertEqual((v.permitido, v.categoria), (False, "incertidumbre"))

    def test_12_enviado_pero_invisible_nunca_permite_restaurar(self) -> None:
        v = self.evaluar(c1.Hallazgo("ninguno"), envio="enviado")
        self.assertEqual((v.permitido, v.categoria), (False, "enviado_invisible"))
        self.assertIn("nueva autorización", v.que_hacer)

    def test_13_gmail_inaccesible_no_permite_restaurar_aunque_el_operador_diga_no_enviado(self) -> None:
        v = self.evaluar(c1.Hallazgo("inaccesible", detalle="x"), envio="no_enviado")
        self.assertEqual((v.permitido, v.categoria), (False, "incertidumbre"))

    def test_14_gmail_muestra_el_correo_y_el_operador_dijo_no_enviado_es_contradiccion(self) -> None:
        v = self.evaluar(valido(), envio="no_enviado")
        self.assertEqual((v.permitido, v.categoria), (False, "contradiccion"))

    def test_15_enviado_visible_y_todavia_no_registrado_no_permite_restaurar(self) -> None:
        v = self.evaluar(valido(), envio="enviado")
        self.assertEqual((v.permitido, v.categoria), (False, "enviado_no_registrado"))
        self.assertIn("AUT-CANCELAR", v.que_hacer)
        self.assertIn("AUT-PUBLICACION", v.que_hacer)

    def test_16_registrado_sin_trabajo_no_permite_restaurar(self) -> None:
        self.registrar_c1()
        v = self.evaluar(valido(), envio="enviado")
        self.assertEqual((v.permitido, v.categoria), (False, "registrado_sin_trabajo"))

    def test_17_cancelacion_preventiva_valida_antes_del_registro_permite_restaurar(self) -> None:
        cancelaciones.cancelar_mensaje(self.autorizacion(preventiva=True))
        self.assertEqual(self.evaluar(valido(), envio="enviado").categoria, "cancelacion_verificada")
        self.registrar_c1()                                                   # el receptor lo registra después
        v = self.evaluar(valido(), envio="enviado")
        self.assertEqual((v.permitido, v.categoria), (True, "cancelacion_verificada"))
        self.assertEqual(self.trabajos_en_total(), 1, "la lápida no se convirtió en trabajo publicable")

    def test_18_cancelacion_incompatible_con_otro_asunto_no_permite_restaurar(self) -> None:
        cancelaciones.cancelar_mensaje(self.autorizacion(preventiva=True))
        self.registrar_c1(asunto="Solicitud de levantamiento")             # el mensaje registrado no es el de C1
        v = self.evaluar(valido(), envio="enviado")
        self.assertEqual((v.permitido, v.categoria), (False, "cancelacion_incompatible"))

    def test_19_lapida_sin_diario_no_permite_restaurar(self) -> None:
        cancelaciones.cancelar_mensaje(self.autorizacion(preventiva=True))
        v = c1.evaluar(valido(), "enviado", self.hechos(), self.hojas_de, lambda _job: False)
        self.assertEqual((v.permitido, v.categoria), (False, "cancelacion_incompatible"))

    def test_20_la_lapida_de_otro_mensaje_no_protege_a_c1(self) -> None:
        self.fila(*REAL, asunto="[PI-REAL-1] Solicitud de levantamiento", remitente="cliente@example.com")
        cancelaciones.cancelar_mensaje(Autorizacion(REAL[0], REAL[1], "operador.prueba", "otro", "[PI-REAL-1]"))
        self.registrar_c1()
        v = self.evaluar(valido(), envio="enviado")
        self.assertEqual((v.permitido, v.categoria), (False, "registrado_sin_trabajo"))

    def publicar_c1(self) -> dict[str, Any]:
        self.registrar_c1()
        return despacho.despachar(self.drive, CARPETA, Alcance(*C1))

    def test_21_trabajo_publicado_con_una_hoja_permite_restaurar(self) -> None:
        self.publicar_c1()
        v = self.evaluar(valido(), envio="enviado")
        self.assertEqual((v.permitido, v.categoria), (True, "trabajo_publicado"))

    def test_22_trabajo_conciliado_con_una_hoja_permite_restaurar(self) -> None:
        self.publicar_c1()
        with store.transaccion() as db:
            db.execute("UPDATE trabajos_work SET state='conciliado' WHERE thread_id=?", (C1[0],))
        v = self.evaluar(valido(), envio="enviado")
        self.assertEqual((v.permitido, v.categoria), (True, "trabajo_conciliado"))

    def test_23_dos_hojas_o_ninguna_no_permiten_restaurar(self) -> None:
        self.publicar_c1()
        hoja = self.hojas_de(self.hechos().trabajos[0]["job_id"])[0]
        for hojas in ([hoja, dict(hoja)], []):
            v = self.evaluar(valido(), envio="enviado", hojas=lambda _job, h=hojas: h)
            self.assertEqual((v.permitido, v.categoria), (False, "hojas_incorrectas"), len(hojas))

    def test_24_drive_inaccesible_no_permite_restaurar(self) -> None:
        self.publicar_c1()

        def falla(_job: str) -> list[Any]:
            raise TimeoutError("sin red")

        v = self.evaluar(valido(), envio="enviado", hojas=falla)
        self.assertEqual((v.permitido, v.categoria), (False, "incertidumbre"))

    def test_25_dos_trabajos_o_estados_no_resueltos_no_permiten_restaurar(self) -> None:
        self.publicar_c1()
        job = self.hechos().trabajos[0]["job_id"]
        con_dos = c1.Hechos(self.hechos().fila, [{"job_id": job, "state": "publicado"}, {"job_id": "otro", "state": "publicado"}])
        self.assertEqual(c1.evaluar(valido(), "enviado", con_dos, self.hojas_de, lapidas.esta_registrada).categoria, "dos_trabajos")
        for estado in ("pendiente_de_publicar", "publicando", "ambiguo"):
            with store.transaccion() as db:
                db.execute("UPDATE trabajos_work SET state=? WHERE job_id=?", (estado, job))
            v = self.evaluar(valido(), envio="enviado")
            self.assertEqual((v.permitido, v.categoria), (False, "trabajo_" + estado))

    def test_26_un_trabajo_ajeno_sin_cerrar_no_permite_restaurar_pero_los_reales_cancelados_o_conciliados_no_cuentan(self) -> None:
        self.publicar_c1()
        self.fila(*REAL, asunto="Solicitud de levantamiento", remitente="cliente@example.com")
        despacho.despachar(self.drive, CARPETA, Alcance(*REAL))             # un real publicado sin cerrar
        v = self.evaluar(valido(), envio="enviado")
        self.assertEqual((v.permitido, v.categoria), (False, "otros_trabajos"))
        with store.transaccion() as db:
            db.execute("UPDATE trabajos_work SET state='conciliado' WHERE thread_id=?", (REAL[0],))
        self.assertTrue(self.evaluar(valido(), envio="enviado").permitido)

    def test_27_un_mensaje_real_nunca_puede_ser_el_elegido(self) -> None:
        real = c1.buscar_en_gmail(GmailSim([mensaje(id_="r1", hilo="hr", asunto="Re: " + c1.ASUNTO, de="cliente@example.com")]))
        v = c1.evaluar(real, "enviado", None, self.hojas_de, lapidas.esta_registrada)
        self.assertEqual((v.permitido, v.categoria), (False, "anomalia"))
        self.assertEqual(self.trabajos_en_total(), 0)


class PausaConCanceladoTest(Base):
    """T-93C: un trabajo cancelado con lápida consistente no impide AUT-CONFIG-1; todo lo demás sin conciliar, sí."""

    def impedimentos(self) -> list[str]:
        with store.lectura() as db:
            return c1.impedimentos_de_trabajos(db)

    def clonar(self, estado: str, n: int) -> None:
        """Otro trabajo ajeno, copiado del de C1 con otro mensaje y el estado dado."""
        with store.transaccion() as db:
            fila = dict(db.execute("SELECT * FROM trabajos_work LIMIT 1").fetchone())
            fila.update(job_id=f"ANKAJOB-x{n}", message_id=f"m{n}", state=estado)
            db.execute(f"INSERT INTO trabajos_work ({','.join(fila)}) VALUES ({','.join('?' * len(fila))})", list(fila.values()))

    def cancelado_valido(self) -> None:
        self.registrar_c1()
        cancelaciones.cancelar_mensaje(self.autorizacion())

    def test_90_ocho_conciliados_y_un_cancelado_valido_permiten_pausar(self) -> None:
        self.cancelado_valido()
        for n in range(8):
            self.clonar("conciliado", n)
        self.assertEqual(self.impedimentos(), [])

    def test_91_cancelado_sin_leyenda_en_el_diario_bloquea(self) -> None:
        self.cancelado_valido()
        lapidas.ruta().write_text("", encoding="utf-8")
        self.assertIn("sin lápida consistente", " ".join(self.impedimentos()))

    def test_92_cancelado_con_diario_ilegible_bloquea(self) -> None:
        self.cancelado_valido()
        with lapidas.ruta().open("a", encoding="utf-8") as f:
            f.write("{no es json\n")
        self.assertIn("ilegible", " ".join(self.impedimentos()))

    def test_93_publicando_pendiente_publicado_ambiguo_agotado_o_desconocido_bloquean(self) -> None:
        self.cancelado_valido()
        for n, estado in enumerate(("publicando", "pendiente_de_publicar", "publicado", "ambiguo", "agotado", "estado_raro")):
            self.clonar(estado, n)
            self.assertIn(f"({estado})", " ".join(self.impedimentos()), estado)
            with store.transaccion() as db:
                db.execute("UPDATE trabajos_work SET state='conciliado' WHERE job_id=?", (f"ANKAJOB-x{n}",))
            self.assertEqual(self.impedimentos(), [], f"{estado} ya conciliado no bloquea")

    def test_94_pausar_se_niega_con_un_trabajo_publicando_y_acepta_con_el_cancelado(self) -> None:
        self.cancelado_valido()
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        cfg = Path(tmp.name) / "config.json"
        cfg.write_bytes(c1.VIEJO * 2)
        with mock.patch.object(c1, "_base", side_effect=self._lectura):
            salida = io.StringIO()
            with contextlib.redirect_stdout(salida):
                self.assertEqual(c1.pausar(cfg, Path(tmp.name) / "ev", comprobar_lock=True, reloj=lambda: 1e12), 4, "pasa el control de trabajos y se detiene después, en la clave")
            self.assertNotIn("trabajos que impiden", salida.getvalue())
            self.clonar("publicando", 0)
            salida = io.StringIO()
            with contextlib.redirect_stdout(salida):
                self.assertEqual(c1.pausar(cfg, Path(tmp.name) / "ev", comprobar_lock=True, reloj=lambda: 1e12), 3)
            self.assertIn("publicando", salida.getvalue())

    def _lectura(self) -> sqlite3.Connection:
        db = store.connect()
        self.addCleanup(db.close)
        return db


class PlazoTest(unittest.TestCase):
    def pausa(self) -> dict[str, Any]:
        return {"inicio_epoch": 0.0, "vence_epoch": float(c1.TOPE_PAUSA_S)}

    def correr(self, veredicto: c1.Veredicto, autorizado: bool) -> tuple[str, list[str], list[float]]:
        reloj = {"t": 0.0}
        restauros: list[float] = []
        mensajes: list[str] = []

        def dormir(s: float) -> None:
            reloj["t"] += s

        resultado = c1.vigilar(self.pausa(), lambda: veredicto, lambda: restauros.append(reloj["t"]), autorizado,
                               reloj=lambda: reloj["t"], dormir=dormir, paso=30.0, salida=mensajes.append)
        return resultado, mensajes, restauros

    def test_30_no_restaura_antes_del_vencimiento_y_restaura_al_vencer_si_es_seguro_y_esta_autorizado(self) -> None:
        resultado, mensajes, restauros = self.correr(c1.Veredicto(True, "trabajo_publicado", "ok"), True)
        self.assertEqual(resultado, "restaurado")
        self.assertEqual(len(restauros), 1)
        self.assertGreaterEqual(restauros[0], c1.TOPE_PAUSA_S, "nunca antes de los 15 minutos")
        self.assertTrue(any("AVISO" in m for m in mensajes), "avisa antes de vencer")

    def test_31_al_vencer_con_c1_sin_resolver_no_restaura_y_queda_bloqueado(self) -> None:
        for categoria in ("enviado_invisible", "registrado_sin_trabajo", "incertidumbre"):
            resultado, mensajes, restauros = self.correr(c1.Veredicto(False, categoria, "x", "Decidir."), True)
            self.assertEqual((resultado, restauros), ("bloqueado", []), categoria)
            self.assertTrue(any("BLOQUEADO" in m for m in mensajes))

    def test_32_sin_autorizacion_no_restaura_aunque_sea_seguro(self) -> None:
        resultado, _mensajes, restauros = self.correr(c1.Veredicto(True, "ausencia_comprobada", "ok"), False)
        self.assertEqual((resultado, restauros), ("bloqueado_sin_autorizacion", []))

    def test_33_segundos_restantes_es_negativo_tras_el_vencimiento(self) -> None:
        self.assertEqual(c1.segundos_restantes(self.pausa(), 100.0), 800.0)
        self.assertLess(c1.segundos_restantes(self.pausa(), 901.0), 0)


class ConfiguracionDeEnsayoTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.dir = Path(self.temp.name)
        self.cfg = self.dir / "config.json"
        texto = json.dumps(ConfiguracionTests.ACTIVA, ensure_ascii=False, indent=2) + "\n"
        self.cfg.write_bytes(texto.replace("\n", "\r\n").encode("utf-8"))
        self.original = self.cfg.read_bytes()
        self.ev = self.dir / "evidencia"

    def salida(self, funcion: Any, *args: Any, **kwargs: Any) -> tuple[Any, str]:
        buffer = io.StringIO()
        with contextlib.redirect_stdout(buffer):
            resultado = funcion(*args, **kwargs)
        return resultado, buffer.getvalue()

    def test_40_pausar_cambia_solo_la_clave_y_guarda_original_y_vencimiento(self) -> None:
        codigo, _ = self.salida(c1.pausar, self.cfg, self.ev, reloj=lambda: 1_800_000_000.0, comprobar_lock=False)
        self.assertEqual(codigo, 0)
        self.assertEqual(self.cfg.read_bytes().replace(c1.NUEVO, c1.VIEJO), self.original)
        pausa = json.loads((self.ev / "pausa.json").read_text())
        self.assertEqual(pausa["vence_epoch"], 1_800_000_000.0 + c1.TOPE_PAUSA_S)
        self.assertEqual((self.ev / "config.json.original").read_bytes(), self.original)

    def test_41_una_segunda_pausa_sin_restaurar_se_rechaza(self) -> None:
        self.salida(c1.pausar, self.cfg, self.ev, comprobar_lock=False)
        codigo, _ = self.salida(c1.pausar, self.cfg, self.ev, comprobar_lock=False)
        self.assertEqual(codigo, 6)

    def test_42_restaurar_devuelve_el_original_exacto_y_marca_la_pausa(self) -> None:
        self.salida(c1.pausar, self.cfg, self.ev, comprobar_lock=False)
        codigo, _ = self.salida(c1.aplicar_restauracion, self.cfg, self.ev)
        self.assertEqual(codigo, 0)
        self.assertEqual(hashlib.sha256(self.cfg.read_bytes()).hexdigest(), hashlib.sha256(self.original).hexdigest())
        self.assertIn("restaurada", json.loads((self.ev / "pausa.json").read_text()))

    def test_43_si_el_archivo_cambio_en_algo_mas_no_se_restaura(self) -> None:
        self.salida(c1.pausar, self.cfg, self.ev, comprobar_lock=False)
        self.cfg.write_bytes(self.cfg.read_bytes().replace(b'"active_mode"', b'"active_mode "'))
        codigo, _ = self.salida(c1.aplicar_restauracion, self.cfg, self.ev)
        self.assertEqual(codigo, 3)

    def test_44_cmd_restaurar_rechaza_si_c1_no_esta_resuelto_y_no_toca_el_archivo(self) -> None:
        self.salida(c1.pausar, self.cfg, self.ev, comprobar_lock=False)
        pausado = self.cfg.read_bytes()
        no = c1.Veredicto(False, "enviado_invisible", "x", "Esperar.")
        with mock.patch.object(c1, "evaluar_real", return_value=no):
            codigo, texto = self.salida(c1.cmd_restaurar, self.cfg, self.ev)
        self.assertEqual(codigo, 7)
        self.assertIn("NO SE RESTAURA", texto)
        self.assertEqual(self.cfg.read_bytes(), pausado)
        si = c1.Veredicto(True, "trabajo_publicado", "ok")
        with mock.patch.object(c1, "evaluar_real", return_value=si):
            codigo, _ = self.salida(c1.cmd_restaurar, self.cfg, self.ev)
        self.assertEqual((codigo, self.cfg.read_bytes()), (0, self.original))

    def test_45_pausar_y_restaurar_sin_confirmar_no_escriben(self) -> None:
        for orden in ("pausar", "restaurar"):
            codigo, _ = self.salida(c1.main, [orden, "--config", str(self.cfg), "--evidencia", str(self.ev)])
            self.assertEqual(codigo, 1)
            self.assertEqual(self.cfg.read_bytes(), self.original)
        self.assertFalse(self.ev.exists())

    def test_46_comandos_separan_cancelar_de_publicar_y_usan_la_raiz_de_drive(self) -> None:
        self.ev.mkdir()
        (self.ev / "ids.json").write_text(json.dumps({"thread_id": "tX", "message_id": "mX", "adjunto_ok": True}))
        (self.ev / "verificacion.json").write_text(json.dumps({"thread_id": "tX", "message_id": "mX", "hecho_en": time.time(),
                                                               "listo_para_publicar": True, "faltas": []}))
        _, cancelar = self.salida(c1.cmd_comandos, self.ev, "cancelar")
        _, publicar = self.salida(c1.cmd_comandos, self.ev, "publicar")
        self.assertIn("AUT-CANCELAR", cancelar)
        self.assertNotIn("despachar", cancelar)
        self.assertIn("AUT-PUBLICACION", publicar)
        self.assertNotIn("cancelar --thread", publicar)
        self.assertIn(f"--carpeta-id {c1.RAIZ_DRIVE} ", publicar)
        self.assertNotIn(c1.PENDIENTES_ID, publicar)
        self.assertIn("tX", cancelar + publicar)

    def test_47_confirmar_envio_exige_valor_y_operador(self) -> None:
        for argv in (["confirmar-envio", "quiza", "--operador", "x"], ["confirmar-envio", "enviado"]):
            codigo, _ = self.salida(c1.main, argv + ["--evidencia", str(self.ev)])
            self.assertEqual(codigo, 1)
        codigo, _ = self.salida(c1.main, ["confirmar-envio", "no_enviado", "--operador", "Ana", "--evidencia", str(self.ev)])
        self.assertEqual(codigo, 0)
        self.assertEqual(json.loads((self.ev / "operador.json").read_text())["envio"], "no_enviado")


class AdjuntoTest(unittest.TestCase):
    def hallazgo(self, *adjuntos: dict[str, Any], **kwargs: Any) -> c1.Hallazgo:
        return c1.buscar_en_gmail(GmailSim([mensaje(adjuntos=list(adjuntos))], **kwargs))

    def test_60_pdf_correcto(self) -> None:
        h = self.hallazgo(adjunto())
        self.assertEqual((h.clase, h.adjunto_ok), ("unico_valido", True))
        self.assertEqual(h.adjunto["sha256"], c1.SHA_PDF)

    def test_61_pdf_incorrecto_con_el_mismo_nombre_se_rechaza(self) -> None:
        h = self.hallazgo(adjunto(contenido=PDF_MISMO_NOMBRE))
        self.assertEqual((h.clase, h.adjunto_ok), ("unico_valido", False))
        self.assertIn("SHA-256", h.detalle)
        self.assertNotIn("nombre", h.detalle)

    def test_62_pdf_incorrecto_con_nombre_distinto_es_el_caso_de_t91(self) -> None:
        h = self.hallazgo(adjunto("solicitud_ficticia_alternativa_NO_C1_original.pdf", PDF_DE_T91))
        self.assertEqual((h.clase, h.adjunto_ok), ("unico_valido", False))
        self.assertTrue(all(palabra in h.detalle for palabra in ("nombre", "tamaño", "SHA-256")))
        self.assertEqual(h.adjunto["bytes"], len(PDF_DE_T91))

    def test_63_tamano_incorrecto_declarado(self) -> None:
        h = self.hallazgo(adjunto(declarado=4000))
        self.assertFalse(h.adjunto_ok)
        self.assertIn("tamaño", h.detalle)

    def test_64_dos_adjuntos_o_ninguno_se_rechazan(self) -> None:
        for adjuntos in ([adjunto(), adjunto("otro.pdf", PDF_MISMO_NOMBRE, id_="att2")], []):
            h = c1.buscar_en_gmail(GmailSim([mensaje(adjuntos=adjuntos)]))
            self.assertEqual((h.clase, h.adjunto_ok), ("unico_valido", False), len(adjuntos))
            self.assertIn("exactamente 1 adjunto", h.detalle)

    def test_65_adjunto_sin_contenido_accesible_se_rechaza(self) -> None:
        h = self.hallazgo(adjunto(contenido=None, declarado=c1.BYTES_PDF))
        self.assertEqual((h.clase, h.adjunto_ok), ("unico_valido", False))
        self.assertIn("contenido accesible", h.detalle)

    def test_66_un_error_de_gmail_al_descargar_es_inaccesible_no_incorrecto(self) -> None:
        h = self.hallazgo(adjunto(), falla_adjunto=True)
        self.assertEqual((h.clase, h.adjunto_ok), ("inaccesible", False))

    def test_67_mensaje_duplicado(self) -> None:
        h = c1.buscar_en_gmail(GmailSim([mensaje(id_="a", hilo="ha"), mensaje(id_="b", hilo="hb")]))
        self.assertEqual(h.clase, "multiples")

    def test_68_el_mensaje_cancelado_de_t91_no_se_confunde_con_el_de_t92(self) -> None:
        t91 = mensaje(id_="1a162bea8208dce5", hilo="1a162bea8208dce5", asunto=c1.ASUNTO.replace(c1.MARCA, c1.MARCA_T91),
                      adjuntos=[adjunto("solicitud_ficticia_alternativa_NO_C1_original.pdf", PDF_DE_T91)])
        self.assertEqual(c1.buscar_en_gmail(GmailSim([t91])).clase, "ninguno", "solo T-91 en el buzón: C1 de T-92 aún no existe")
        t92 = mensaje(id_="nuevo", hilo="hnuevo", adjuntos=[adjunto(id_="att-t92")])
        h = c1.buscar_en_gmail(GmailSim([t91, t92]))
        self.assertEqual((h.clase, h.message_id, h.adjunto_ok), ("unico_valido", "nuevo", True))

    def test_69_el_adjunto_se_descarga_solo_con_metodos_de_lectura(self) -> None:
        gmail = GmailSim([mensaje()])
        c1.buscar_en_gmail(gmail)
        self.assertEqual(set(gmail.metodos), {"list", "get", "attachments.get"})


class CicloT92Test(Base):
    def verificar(self, gmail: GmailSim, elegible: bool = True, carpeta: Path | None = None, reloj: Any = time.time) -> dict[str, Any]:
        with store.lectura() as db:
            return c1.verificar_para_publicar(carpeta or self.ev, gmail, db, lambda _t, _m: elegible, reloj)

    def setUp(self) -> None:
        super().setUp()
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.ev = Path(self.tmp.name)
        self.gmail = GmailSim([mensaje(id_=C1[1], hilo=C1[0])])
        (self.ev / "ids.json").write_text(json.dumps({"thread_id": C1[0], "message_id": C1[1], "adjunto_ok": True}))

    def comando(self, cual: str = "publicar", reloj: Any = time.time) -> tuple[int, str]:
        buffer = io.StringIO()
        with contextlib.redirect_stdout(buffer):
            if cual == "publicar":
                faltas = c1.faltas_para_publicar(self.ev, reloj)
                codigo = c1.cmd_comandos(self.ev, cual) if reloj is time.time else (1 if faltas else 0)
            else:
                codigo = c1.cmd_comandos(self.ev, cual)
        return codigo, buffer.getvalue()

    def test_70_trabajo_correcto_autorizado_para_publicacion(self) -> None:
        self.registrar_c1()
        v = self.verificar(self.gmail)
        self.assertEqual((v["listo_para_publicar"], v["faltas"]), (True, []))
        codigo, texto = self.comando()
        self.assertEqual(codigo, 0)
        self.assertIn("despachar", texto)
        self.assertIn(C1[1], texto)

    def test_71_sin_registro_no_hay_comando_de_publicar(self) -> None:
        v = self.verificar(self.gmail)
        self.assertFalse(v["listo_para_publicar"])
        self.assertIn("aún no está registrado", " ".join(v["faltas"]))
        self.assertEqual(self.comando()[0], 1)

    def test_72_adjunto_incorrecto_en_gmail_bloquea_la_publicacion_aunque_asunto_y_remitente_coincidan(self) -> None:
        self.registrar_c1()
        malo = GmailSim([mensaje(id_=C1[1], hilo=C1[0], adjuntos=[adjunto(contenido=PDF_MISMO_NOMBRE)])])
        v = self.verificar(malo)
        self.assertFalse(v["listo_para_publicar"])
        self.assertFalse(v["adjunto_gmail_ok"])
        codigo, texto = self.comando()
        self.assertEqual(codigo, 1)
        self.assertNotIn("despachar --carpeta-id", texto)
        self.assertIn("NO SE ENTREGA", texto)

    def test_73_adjunto_incorrecto_registrado_en_sqlite_bloquea(self) -> None:
        self.registrar_c1(adjuntos=[{"filename": "otro.pdf", "size_bytes": 49394}])
        v = self.verificar(self.gmail)
        self.assertFalse(v["listo_para_publicar"])
        self.assertIn("SQLite", " ".join(v["faltas"]))

    def test_74_no_elegible_ya_con_trabajo_o_con_lapida_bloquea(self) -> None:
        self.registrar_c1()
        self.assertIn("elegible", " ".join(self.verificar(self.gmail, elegible=False)["faltas"]))
        cancelaciones.cancelar_mensaje(self.autorizacion())
        self.assertIn("trabajo o lápida", " ".join(self.verificar(self.gmail)["faltas"]))

    def test_75_la_verificacion_caduca_y_exige_los_mismos_ids(self) -> None:
        self.registrar_c1()
        self.verificar(self.gmail)
        tarde = time.time() + c1.VIGENCIA_VERIFICACION_S + 1
        self.assertIn("caducó", " ".join(c1.faltas_para_publicar(self.ev, lambda: tarde)))
        (self.ev / "ids.json").write_text(json.dumps({"thread_id": "otro", "message_id": "otro", "adjunto_ok": True}))
        self.assertIn("otros ids", " ".join(c1.faltas_para_publicar(self.ev)))

    def test_76_sin_localizar_o_sin_adjunto_validado_por_ids_no_hay_comando(self) -> None:
        self.assertIn("falta `localizar`", " ".join(c1.faltas_para_publicar(self.ev)))
        (self.ev / "ids.json").write_text(json.dumps({"thread_id": C1[0], "message_id": C1[1], "adjunto_ok": False}))
        self.assertIn("`ids` no validó el adjunto", " ".join(c1.faltas_para_publicar(self.ev)))

    def test_77_gmail_inaccesible_o_duplicado_no_habilitan_la_publicacion(self) -> None:
        self.registrar_c1()
        for gmail in (GmailSim([], falla_lista=True), GmailSim([mensaje(id_="a", hilo="ha"), mensaje(id_="b", hilo="hb")])):
            self.assertFalse(self.verificar(gmail)["listo_para_publicar"])

    def test_78_cancelar_si_se_entrega_con_adjunto_incorrecto_porque_hace_falta_para_abortar(self) -> None:
        (self.ev / "ids.json").write_text(json.dumps({"thread_id": C1[0], "message_id": C1[1], "adjunto_ok": False}))
        codigo, texto = self.comando("cancelar")
        self.assertEqual(codigo, 0)
        self.assertIn("cancelar --thread-id", texto)
        self.assertEqual(self.comando("publicar")[0], 1)

    def test_79_aborto_con_adjunto_incorrecto_ya_registrado_permite_restaurar_solo_tras_la_lapida(self) -> None:
        self.registrar_c1()
        malo = con_adjunto_incorrecto()
        self.assertEqual((malo.clase, malo.adjunto_ok), ("unico_valido", False))
        antes = self.evaluar(malo, envio="enviado")
        self.assertEqual((antes.permitido, antes.categoria), (False, "registrado_sin_trabajo"))
        self.assertIn("no publicar", antes.que_hacer)
        cancelaciones.cancelar_mensaje(self.autorizacion())
        despues = self.evaluar(malo, envio="enviado")
        self.assertEqual((despues.permitido, despues.categoria), (True, "cancelacion_verificada"))

    def test_80_con_adjunto_incorrecto_un_trabajo_publicado_no_permite_restaurar(self) -> None:
        self.registrar_c1()
        despacho.despachar(self.drive, CARPETA, Alcance(*C1))
        v = self.evaluar(con_adjunto_incorrecto(), envio="enviado")
        self.assertEqual((v.permitido, v.categoria), (False, "adjunto_incorrecto"))
        self.assertTrue(self.evaluar(valido(), envio="enviado").permitido, "con el adjunto correcto sí")

    def test_81_el_mensaje_cancelado_de_t91_no_protege_ni_bloquea_a_t92(self) -> None:
        t91 = ("1a162bea8208dce5", "1a162bea8208dce5")
        self.registrar_c1(asunto=c1.ASUNTO.replace(c1.MARCA, c1.MARCA_T91), ids=t91)
        cancelaciones.cancelar_mensaje(Autorizacion(*t91, "operador.prueba", "aborto", c1.MARCA_T91))
        self.registrar_c1()
        v = self.evaluar(valido(), envio="enviado")
        self.assertEqual((v.permitido, v.categoria), (False, "registrado_sin_trabajo"), "la lápida de T-91 no cubre a T-92")
        self.assertEqual(self.hechos(t91).trabajos[0]["state"], "cancelado", "T-91 permanece cancelado")
        self.assertEqual(self.hechos().trabajos, [])

    def test_82_cmd_ids_guarda_los_ids_aun_con_adjunto_incorrecto_pero_sale_con_error(self) -> None:
        for mensajes, esperado in (([mensaje(id_="g7", hilo="h7")], 0),
                                   ([mensaje(id_="g7", hilo="h7", adjuntos=[adjunto(contenido=PDF_MISMO_NOMBRE)])], 1)):
            carpeta = Path(tempfile.mkdtemp(dir=self.tmp.name))
            with mock.patch.object(c1, "_servicios", return_value=(GmailSim(mensajes), None)), contextlib.redirect_stdout(io.StringIO()):
                codigo = c1.cmd_ids(carpeta)
            guardado = json.loads((carpeta / "ids.json").read_text())
            self.assertEqual((codigo, guardado["adjunto_ok"], guardado["message_id"]), (esperado, esperado == 0, "g7"))


class SoloLecturaTest(unittest.TestCase):
    def test_50_el_controlador_no_cancela_ni_despacha_ni_escribe_en_sqlite_ni_envia(self) -> None:
        codigo = Path(c1.__file__).read_text(encoding="utf-8")
        fuera_de_docs = codigo.split('"""', 2)[2]
        for prohibido in ("cancelar_mensaje(", "despachar(", "INSERT INTO", "UPDATE ", "DELETE FROM", ".send(",
                          ".create(", "files().update", "messages().modify", "messages().trash", "drafts()"):
            self.assertNotIn(prohibido, fuera_de_docs, prohibido)

    def test_51_el_marcador_y_el_asunto_son_los_de_t92(self) -> None:
        self.assertEqual(c1.MARCA, "[PI-C1-T92-001]")
        self.assertEqual(c1.ASUNTO, "[PI-C1-T92-001] Solicitud ficticia de laboratorio - levantamiento topográfico (SIN COTIZACIÓN REAL)")
        self.assertTrue(c1.ASUNTO.startswith(c1.MARCA))
        self.assertEqual(len(c1.ASUNTO), 99)
        self.assertEqual(c1.TOPE_PAUSA_S, 900)
        self.assertNotEqual(c1.MARCA, c1.MARCA_T91)
        self.assertEqual((c1.ADJUNTO, c1.BYTES_PDF, c1.SHA_PDF),
                         ("solicitud_01.pdf", 4913, "f47ed4da974e982b09954bfd1b71472b0fefa5859309d7ab1e24c924c92bd844"))
        self.assertEqual(hashlib.sha256(PDF_C1).hexdigest(), c1.SHA_PDF, "el PDF del banco es el esperado")
        self.assertEqual(len(PDF_C1), c1.BYTES_PDF)

    def test_52_la_evidencia_de_t91_se_conserva_y_el_intento_nuevo_usa_otra_carpeta(self) -> None:
        carpeta = Path(c1.__file__).resolve().parent
        ids = json.loads((carpeta / "evidencia_t91" / "ids.json").read_text())
        self.assertEqual(ids["message_id"], "1a162bea8208dce5")
        self.assertTrue((carpeta / "evidencia_t91" / "pausa.json").exists())
        self.assertNotEqual(c1.MARCA, ids.get("marca", ""), "la evidencia de T-91 no se reutiliza para el marcador nuevo")


if __name__ == "__main__":
    unittest.main()
