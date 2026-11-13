"""Un solo reloj con zona y fechas ordenadas por instante, no por texto (U-62).

Cubre: U-62
"""

from tests import _isolate  # noqa: F401  (debe ir primero)

import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from anka.dominio import reloj
from anka.dominio.status import QuoteStatus
from anka.infra import settings, store
from anka.servicios import change_control
from anka.servicios.repositorio import RepositorioDeCasos

# Mismo instante que la hora local de Bogotá escrita en UTC: por texto, «T15» < «T20» y se desordenarían.
BOGOTA = "2026-10-15T16:00:00-05:00"       # = 21:00 UTC
UTC_ANTES = "2026-10-15T20:00:00+00:00"     # una hora ANTES que la de Bogotá, aunque su texto sea mayor
UTC_DESPUES = "2026-10-15T22:00:00+00:00"   # una hora después


# Cubre: U-62
class ElReloj(unittest.TestCase):
    def test_ahora_trae_zona(self):
        self.assertIsNotNone(reloj.ahora().tzinfo)
        self.assertRegex(reloj.ahora_iso("seconds"), r"[+-]\d\d:\d\d$")

    def test_el_anio_de_la_t_sale_del_reloj_unico(self):
        fijo = datetime(2031, 1, 1, 0, 30, tzinfo=timezone(timedelta(hours=-5)))
        with patch.object(change_control, "ahora", return_value=fijo), \
                patch.object(change_control, "connect") as conectar:
            conectar.return_value.execute.return_value = []
            self.assertTrue(change_control.suggest_number().startswith("31"))

    def test_ningun_modulo_toma_la_hora_sin_zona(self):
        import ast
        raiz = Path(__file__).resolve().parents[1] / "anka"
        for nombre in ("infra/backup.py", "servicios/change_control.py", "web/pantalla_caso.py", "web/server.py",
                       "servicios/repositorio.py", "infra/store.py", "servicios/envios.py", "servicios/case_actions.py"):
            arbol = ast.parse((raiz / nombre).read_text(encoding="utf-8"))
            for nodo in ast.walk(arbol):
                if (isinstance(nodo, ast.Call) and isinstance(nodo.func, ast.Attribute) and nodo.func.attr == "now"
                        and isinstance(nodo.func.value, ast.Name) and nodo.func.value.id == "datetime"):
                    self.fail(f"{nombre}:{nodo.lineno} usa datetime.now() en vez de reloj.ahora()")

    def test_instante_entiende_zonas_z_y_fechas_sin_zona(self):
        self.assertEqual(reloj.instante("2026-10-15T20:00:00Z"), reloj.instante("2026-10-15T15:00:00-05:00"))
        self.assertIsNotNone(reloj.instante("2026-10-15 09:30").tzinfo)       # sin zona: hora local
        for malo in ("", None, "ayer", "2026-13-45"):
            self.assertIsNone(reloj.instante(malo))

    def test_la_clave_ordena_por_instante_y_no_por_texto(self):
        textos = [UTC_DESPUES, BOGOTA, UTC_ANTES]
        self.assertEqual(sorted(textos), [BOGOTA, UTC_ANTES, UTC_DESPUES])                        # por texto: mal
        self.assertEqual(sorted(textos, key=reloj.clave_de_orden), [UTC_ANTES, BOGOTA, UTC_DESPUES])

    def test_lo_ilegible_va_primero_y_no_rompe_el_orden(self):
        textos = [UTC_ANTES, "", "basura", None, "2026-10-15 09:30"]
        orden = sorted(textos, key=reloj.clave_de_orden)
        self.assertEqual(orden[-1], UTC_ANTES)
        self.assertEqual(set(orden[:3]), {"", "basura", None})


# Cubre: U-62
class ElOrdenEnLaBase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        parche = patch.object(settings, "DATA_DIR", Path(self._tmp.name))
        parche.start()
        self.addCleanup(parche.stop)
        self.db = store.open_db(Path(self._tmp.name))
        self.addCleanup(self.db.close)

    def guardar(self, hilo, actualizado):
        store.save(self.db, hilo, "m", QuoteStatus.REVIEW, "a@b.co", {})
        self.db.execute("UPDATE quotes SET updated_at=? WHERE thread_id=?", (actualizado, hilo))
        self.db.commit()

    def test_la_lista_de_casos_ordena_por_instante_entre_offsets_distintos(self):
        self.guardar("a", UTC_DESPUES)       # 22:00 UTC: el más reciente
        self.guardar("b", BOGOTA)            # 21:00 UTC
        self.guardar("c", UTC_ANTES)         # 20:00 UTC: el más antiguo
        orden = [caso["thread_id"] for caso in RepositorioDeCasos().listar()]
        self.assertEqual(orden, ["a", "b", "c"])
        # por texto habría salido a, c, b (UTC_ANTES > BOGOTA como cadena)
        self.assertEqual(sorted([UTC_DESPUES, BOGOTA, UTC_ANTES], reverse=True)[1], UTC_ANTES)

    def test_la_ultima_respuesta_y_el_seguimiento_van_por_instante(self):
        self.guardar("t", BOGOTA)
        store.record_event(self.db, "t", "received", "gmail", "viejo", "uno", UTC_ANTES)
        store.record_event(self.db, "t", "received", "gmail", "nuevo", "dos", BOGOTA)
        self.db.commit()
        eventos = [fila["ref"] for fila in store.case_events(self.db, "t")]
        self.assertEqual(eventos, ["viejo", "nuevo"])                  # por texto habría salido al revés
        # la última respuesta del cliente es la de las 21:00 UTC aunque su texto sea menor
        caso = RepositorioDeCasos().obtener("t")
        self.assertEqual(caso["last_inbound_id"] if "last_inbound_id" in caso else "", caso.get("last_inbound_id", ""))
        relaciones = RepositorioDeCasos._relaciones(self.db, "t")
        self.assertEqual(relaciones.latest_reply["t"][1], "nuevo")

    def test_la_conversacion_y_el_primer_mensaje_van_por_instante(self):
        self.guardar("t", BOGOTA)
        from anka.infra.store import add_message
        add_message(self.db, "t", "m1", "received", "x@y.co", "uno", "viejo", UTC_ANTES)
        add_message(self.db, "t", "m2", "received", "x@y.co", "dos", "nuevo", BOGOTA)
        self.db.commit()
        repo = RepositorioDeCasos()
        self.assertEqual(repo.primer_mensaje("t")["body"], "viejo")
        self.assertEqual([m["body"] for m in repo.conversacion("t")], ["nuevo", "viejo"])

    def test_fechas_sin_zona_vacias_o_ilegibles_no_rompen_ni_se_reescriben(self):
        self.guardar("a", "2026-10-15 16:00")
        self.guardar("b", "")
        self.guardar("c", "basura")
        orden = [caso["thread_id"] for caso in RepositorioDeCasos().listar()]
        self.assertEqual(orden[0], "a")
        self.assertEqual(set(orden), {"a", "b", "c"})
        textos = {fila["thread_id"]: fila["updated_at"] for fila in self.db.execute("SELECT * FROM quotes")}
        self.assertEqual(textos, {"a": "2026-10-15 16:00", "b": "", "c": "basura"})       # nada se tocó


if __name__ == "__main__":
    unittest.main()
