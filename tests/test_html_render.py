"""Red de seguridad del HTML del dashboard.

1. Golden: cada página se compara (en forma canónica, sin diferencias de espacios ni de
   entidades) con una instantánea versionada en `golden/`. Sirve para migrar el motor de
   plantillas sin cambiar lo que ve el usuario. Regenerar a propósito con
   `UPDATE_GOLDEN=1 python -m unittest test_html_render`.
2. Inyección: datos hostiles en todos los campos que controla un cliente no pueden crear
   etiquetas, atributos ni JavaScript nuevos en ninguna página.
"""

from tests import _isolate  # noqa: F401  (debe ir primero)

import io
import os
import re
import tempfile
import unittest
from html.parser import HTMLParser
from pathlib import Path
from datetime import datetime
from unittest.mock import patch

from anka.dominio.errores import ErrorDeAnka
from anka.infra import settings
from anka.infra import store
from anka.servicios import followup_reminders as fup
from anka.web import server as web
from anka.web import seguridad
from anka.web import paginas
from anka.web import pantalla_caso
from anka.dominio.status import QuoteStatus
from tests.test_app import test_config

GOLDEN_DIR = Path(__file__).with_name("golden")
MARK = "ZZINJ"
HOSTILE = f"\"><img src=x onerror={MARK}(1)><script>{MARK}(2)</script>'&<b>"
HOSTILE_EMAIL = f"x'+{MARK}+'@a.co"  # pasa valid_email_address y cierra un string JS

ACTIVITY = {
    "activity": "Levantamiento topográfico", "servicio_tecnica": "Topografía convencional",
    "ubicacion": "Bogotá", "quantity": "10", "unit": "ha", "entregable": "Planos",
    "plazo_restriccion": "30 días", "fuente": "correo", "estado": "confirmado", "notes": "",
}


def analysis(**extra):
    base = {
        "is_quote_request": True, "project_name": "Proyecto Norte",
        "summary": "Levantamiento topográfico solicitado.",
        "received_at": "2026-09-01T09:00:00-05:00",
        "activities": [ACTIVITY, {**ACTIVITY, "activity": "Batimetría", "ubicacion": "no indicado",
                                  "estado": "pendiente", "servicio_tecnica": "Batimetría"}],
        "attachments": [
            {"filename": "alcance.csv", "status": "extraido", "reason": "", "blocked_fields": []},
        ],
        "routing": {"is_forwarded": False, "selected_recipient": "cliente@example.com",
                    "original_sender_email": "cliente@example.com"},
    }
    base.update(extra)
    return base


def hostile_analysis():
    h = HOSTILE
    return analysis(
        project_name=h, summary=h,
        activities=[{key: h for key in ACTIVITY} | {"estado": "pendiente"}],
        attachments=[{"filename": h, "status": h, "reason": h, "blocked_fields": [h]}],
        routing={"is_forwarded": True, "internal_forwarder": h, "original_sender": h,
                 "selected_recipient": HOSTILE_EMAIL, "original_sender_email": HOSTILE_EMAIL},
    )


def hostile_scope_analysis():
    """Con asuntos de alcance pendientes el caso pasa a revisión técnica (sin formulario)."""
    h = HOSTILE
    return hostile_analysis() | {
        "scope_dependencies": [{"description": h, "source": h, "status": "pendiente"}],
        "contradictions": [{"topic": h, "details": h, "sources": [h], "status": "pendiente"}],
    }


# (thread_id, estado, destinatario, análisis, metadatos, borrador, id_borrador)
CASES = [
    ("t-review", QuoteStatus.REVIEW_PENDING, "cliente@example.com",
     analysis(), ("Energía Norte", "Empresa privada", "Energía", "Nota interna"), "", None),
    ("t-draft", QuoteStatus.DRAFT_CREATED, "otro@example.com",
     analysis(project_name="Proyecto Sur"), ("Cliente Sur", "Entidad pública", "Infraestructura", ""),
     "Hola,\n\nTexto del borrador.", "draft-1"),
    ("t-ready", QuoteStatus.READY_REVIEW, "listo@example.com",
     analysis(project_name="Proyecto Listo", activities=[ACTIVITY]), ("", "No clasificado", "No clasificado", ""),
     "", None),
    ("t-sent", QuoteStatus.RESPONSE_SENT, "enviado@example.com",
     analysis(project_name="Proyecto Enviado"), ("Cliente Enviado", "Empresa privada", "Minería", ""),
     "Respuesta enviada al cliente.", "draft-2"),
    ("t-review-tech", QuoteStatus.REVIEW, "tecnico@example.com",
     analysis(project_name="Proyecto Técnico", activities=[], attachments=[
         {"filename": "plano.dwg", "status": "bloqueado", "reason": "Formato no soportado",
          "blocked_fields": ["ubicación del proyecto"]}]), ("", "No clasificado", "No clasificado", ""),
     "", None),
    ("t-ignored", QuoteStatus.IGNORED, "ignorado@example.com", {}, ("", "No clasificado", "No clasificado", ""),
     "", None),
    ("t-hostile", QuoteStatus.REVIEW_PENDING, HOSTILE_EMAIL, hostile_analysis(),
     (HOSTILE, HOSTILE, HOSTILE, HOSTILE), HOSTILE, None),
    ("t-folder-held", QuoteStatus.READY_REVIEW, "listo@example.com",
     analysis(project_name="Proyecto Retenido", drive={"pending": "No hay un cliente confirmado para este remitente."}),
     ("Minera Sur", "No clasificado", "No clasificado", ""), "", None),
    ("t-folder-hostile", QuoteStatus.READY_REVIEW, "listo@example.com",
     analysis(project_name="Proyecto Hostil", drive={"pending": HOSTILE}),
     (HOSTILE, "No clasificado", "No clasificado", ""), "", None),
    ("t-folder-done", QuoteStatus.READY, "listo@example.com",
     analysis(project_name="Proyecto con Carpeta"), ("Minera Sur", "No clasificado", "No clasificado", ""), "", None),
    ("t-discarded", QuoteStatus.IGNORED, "noreply@tienda.com",
     {"ignored_reason": "remitente automático (no-reply)",
      "email": {"subject": "Ofertas de oficina", "from": "Tienda <noreply@tienda.com>"}},
     ("", "No clasificado", "No clasificado", ""), "", None),
    ("t-discarded-rescued", QuoteStatus.IGNORED, "ana@empresa.co",
     {"ignored_reason": "El modelo no la identificó como solicitud de cotización",
      "email": {"subject": "Gracias", "from": "Ana <ana@empresa.co>"}, "force_analysis": True},
     ("", "No clasificado", "No clasificado", ""), "", None),
    ("t-discarded-hostile", QuoteStatus.IGNORED, HOSTILE_EMAIL,
     {"ignored_reason": HOSTILE, "email": {"subject": HOSTILE, "from": HOSTILE}},
     (HOSTILE, "No clasificado", "No clasificado", HOSTILE), "", None),
    ("t-hostile-scope", QuoteStatus.REVIEW, HOSTILE_EMAIL, hostile_scope_analysis(),
     (HOSTILE, "No clasificado", "No clasificado", HOSTILE), "", None),
]
CONTEXT_INDEX = {
    "privacy": {"internal_only": True}, "generated_at": "2026-09-01",
    "client_profiles": 2,
    "project_types": [["Topografía", 5]], "sectors": [["Energía", 3]],
    "usual_requests": [["Ubicación", 4]],
    "clients": {"energia norte": {
        "client_name": "Energía Norte S.A.S.", "company_type": "Privada",
        "company_type_confidence": "alta", "commercial_document_count": 3,
        "project_types": {"Topografía": 2}, "sectors": {"Energía": 2},
        "usual_requests": {"Ubicación": 2},
    }},
}


def seed(data_dir: Path) -> None:
    db = store.open_db(data_dir)
    for thread_id, status, recipient, data, meta, body, draft_id in CASES:
        data = dict(data)
        store.save(db, thread_id, f"in-{thread_id}", status, recipient, data)
        db.execute(
            "UPDATE quotes SET draft_body=?, draft_id=?, sent_at=?, updated_at=? WHERE thread_id=?",
            (body, draft_id, "2026-09-02T10:00:00-05:00" if status == QuoteStatus.RESPONSE_SENT else None,
             "2026-09-02T10:00:00-05:00", thread_id),
        )
        if status == QuoteStatus.READY:
            db.execute("UPDATE quotes SET folder_id='folder-abc123' WHERE thread_id=?", (thread_id,))
        db.execute(
            "INSERT INTO quote_metadata (thread_id, client_name, client_type, sector, notes, updated_at)"
            " VALUES (?, ?, ?, ?, ?, '2026-09-02T10:00:00-05:00')",
            (thread_id, meta[0], meta[1], meta[2], meta[3]),
        )
    db.execute("DELETE FROM dashboard_notifications")
    db.commit()
    db.close()


def _squash(value):
    return re.sub(r"\s+", " ", value or "").strip()


class Canonical(HTMLParser):
    """Convierte HTML en una lista de eventos comparable, ignorando espacios y entidades."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.events = []
        self.tags = set()
        self.attrs = set()
        self.scripts = 0

    def handle_starttag(self, tag, attrs):
        self.tags.add(tag)
        self.scripts += tag == "script"
        self.attrs.update(name for name, _ in attrs)
        rendered = "".join(f' {n}="{_squash(v)}"' for n, v in attrs)
        self.events.append(f"<{tag}{rendered}>")

    def handle_decl(self, decl):
        self.events.append(f"<!{_squash(decl).lower()}>")

    def handle_endtag(self, tag):
        self.events.append(f"</{tag}>")

    def handle_data(self, data):
        text = _squash(data)
        if text:
            self.events.append(text)


def canonical(html_text: str) -> Canonical:
    parser = Canonical()
    parser.feed(html_text)
    parser.close()
    return parser


class Pages(unittest.TestCase):
    """Renderiza todas las páginas con datos fijos."""

    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.TemporaryDirectory()
        cls.data_dir = Path(cls._tmp.name)
        with patch.object(settings, "DATA_DIR", cls.data_dir):
            seed(cls.data_dir)
        cls.pages = cls.render_all()

    @classmethod
    def tearDownClass(cls):
        cls._tmp.cleanup()

    @classmethod
    def render_all(cls):
        pages = {}
        real_waiting_days = fup.waiting_days
        # Los días de espera dependen de hoy: se fija el reloj para que la instantánea no cambie con la fecha.
        fixed_now = datetime.fromisoformat("2026-10-01T12:00:00-05:00")
        with patch.object(settings, "DATA_DIR", cls.data_dir),                 patch.object(fup, "waiting_days", lambda since, now=None: real_waiting_days(since, fixed_now)), \
                patch("anka.web.paginas.load_config", return_value=test_config()), patch("anka.web.pantalla_caso.load_config", return_value=test_config()), \
                patch("anka.web.paginas.sync_status_text", return_value="Sincronización de prueba."), \
                patch("anka.servicios.internal_context.load_internal_commercial_index", return_value=CONTEXT_INDEX):
            pages["index"] = paginas.render_index()
            pages["index_mensaje"] = paginas.render_index("Mensaje de prueba")
            pages["requests"] = paginas.render_requests()
            pages["discarded"] = paginas.render_discarded()
            pages["discarded_mensaje"] = paginas.render_discarded("Se volverá a mirar <pronto>")
            for case in CASES:
                pages[f"case_{case[0]}"] = pantalla_caso.render_case(case[0])
            pages["case_no_existe"] = pantalla_caso.render_case("no-existe")
            pages["case_mensaje"] = pantalla_caso.render_case("t-review", "Guardado <ok>")
        return pages


# Cubre: U-01
class GoldenTests(Pages):
    def test_every_page_matches_its_snapshot(self):
        update = os.getenv("UPDATE_GOLDEN") == "1"
        GOLDEN_DIR.mkdir(exist_ok=True)
        for name, page in self.pages.items():
            with self.subTest(page=name):
                current = "\n".join(canonical(page).events) + "\n"
                path = GOLDEN_DIR / f"{name}.txt"
                if update:
                    path.write_text(current, encoding="utf-8", newline="\n")
                    continue
                self.assertTrue(path.exists(), f"Falta {path}; genera con UPDATE_GOLDEN=1")
                self.assertEqual(current, path.read_text(encoding="utf-8"))


# Cubre: T-07
class InjectionTests(Pages):
    def test_hostile_data_never_creates_tags_attributes_or_scripts(self):
        # Referencia: qué etiquetas/atributos usa la misma página con datos normales.
        baseline_pages = {"index", "requests", "case_t-review"}
        safe = [canonical(self.pages[name]) for name in baseline_pages]
        allowed_tags = set().union(*(p.tags for p in safe)) | {"pre", "ol", "ul", "li", "small", "fieldset", "legend"}
        allowed_attrs = set().union(*(p.attrs for p in safe))
        # Scripts propios: el del tema (en el encabezado, antes de pintar), el de confirmación en toda página
        # y el de filtros en /requests.
        legit_scripts = {name: 3 if name == "requests" else 2 for name in self.pages}
        for name, page in self.pages.items():
            parsed = canonical(page)
            with self.subTest(page=name):
                self.assertLessEqual(parsed.tags, allowed_tags | {"html", "head", "body", "meta",
                                                                 "title", "style", "script", "a",
                                                                 "form", "table", "thead", "tbody",
                                                                 "tr", "th", "td"})
                # La única imagen es el logo propio de la cabecera (U-36); ningún dato crea otra.
                self.assertEqual([e for e in parsed.events if e.startswith("<img")],
                                 ['<img class="brand-logo" src="/static/logo-anka.png" alt="Anka" width="106" height="38">'])
                self.assertNotIn("iframe", parsed.tags)
                self.assertFalse(
                    {a for a in parsed.attrs if a.startswith("on")},
                    "No debe haber manejadores de eventos en línea (onclick, onerror, ...)",
                )
                # data-confirm, required y pattern son atributos propios del código, nunca de datos
                # (pattern valida el número de la T, que solo aparece con la información validada).
                # `open` lo pone el código en el aviso plegable «Parece el mismo proyecto»; nunca viene de datos.
                self.assertLessEqual(parsed.attrs, allowed_attrs | {"data-confirm", "required", "checked", "pattern", "open",
                                                         "data-sugerido", "data-anio"})   # resumen de la emisión de la T
                self.assertEqual(parsed.scripts, legit_scripts[name])

    def test_hostile_page_does_not_add_scripts_beyond_the_legitimate_ones(self):
        for name in ("case_t-hostile", "case_t-hostile-scope"):
            self.assertEqual(canonical(self.pages[name]).scripts, 2)

    def test_hostile_text_is_present_but_only_as_escaped_text(self):
        page = self.pages["case_t-hostile"]
        self.assertNotIn("<img src=x", page)
        self.assertNotIn(f"<script>{MARK}", page)
        self.assertIn("&lt;img src=x", page.replace("&#34;", '"').replace("&#39;", "'"))

    def test_recipient_with_quote_cannot_break_out_of_the_send_confirmation(self):
        # Antes: confirm('...{destinatario}...') dentro de onclick permitía ejecutar JS.
        parsed = canonical(self.pages["case_t-hostile"])
        for event in parsed.events:
            if event.startswith("<button") and "/send" in event:
                self.assertNotIn("onclick", event)
                self.assertIn("data-confirm=", event)
                break
        else:
            self.fail("No se encontró el botón de envío en la página del caso hostil")


# Cubre: T-07
class ErrorPageTests(unittest.TestCase):
    """403/404/500 pasan por el mismo layout: el mensaje se escapa, el contenedor no."""

    def request(self, method, path, body=b"", host="127.0.0.1:1", origin=None):
        handler = web.ReviewHandler.__new__(web.ReviewHandler)
        sent = {}
        handler.headers = {"Host": host, "Content-Length": str(len(body))}
        if origin:
            handler.headers["Origin"] = origin
        handler.path = path
        handler.rfile = io.BytesIO(body)
        handler._send_html = lambda content, status=200: sent.update(status=status, page=content)
        with tempfile.TemporaryDirectory() as temp_dir,                 patch.object(settings, "DATA_DIR", Path(temp_dir)),                 patch("anka.web.paginas.load_config", return_value=test_config()), patch("anka.web.pantalla_caso.load_config", return_value=test_config()), patch("anka.web.server.load_config", return_value=test_config()), patch("anka.web.acciones.load_config", return_value=test_config()):
            getattr(handler, f"do_{method}")()
        return sent

    def test_not_found_page_keeps_its_markup(self):
        sent = self.request("GET", "/no-existe")
        self.assertEqual(sent["status"], 404)
        self.assertIn("<div class='empty'>Página no encontrada.</div>", sent["page"])

    def test_forbidden_page_escapes_the_reason(self):
        body = ("csrf_token=" + seguridad.CSRF_TOKEN).encode()
        with self.assertLogs("anka.web.server", "WARNING") as logs:
            sent = self.request("POST", "/refresh", body, origin="http://evil.example")
        self.assertEqual(sent["status"], 403)
        self.assertIn("<div class='empty'>", sent["page"])
        self.assertNotIn("&lt;div class=", sent["page"])
        self.assertIn("Origen no permitido", logs.output[0])  # los rechazos quedan registrados

    def test_error_text_from_an_exception_is_escaped(self):
        body = ("csrf_token=" + seguridad.CSRF_TOKEN).encode()
        boom = ErrorDeAnka(f"<img src=x onerror={MARK}(1)>")  # mensaje pensado para la persona
        with patch("anka.web.acciones.checked_run", side_effect=boom):
            with self.assertLogs("anka.web.server", "ERROR") as logs:
                sent = self.request("POST", "/refresh", body)
        self.assertEqual(sent["status"], 500)
        self.assertNotIn("<img src=x", sent["page"])
        self.assertIn("&lt;img src=x", sent["page"])
        self.assertIn("<div class='empty'>", sent["page"])
        self.assertIn("Error atendiendo POST /refresh", logs.output[0])
        self.assertIsNotNone(logs.records[0].exc_info)  # el traceback ya no se pierde

    def test_unexpected_error_detail_is_not_shown_to_the_browser(self):
        body = ("csrf_token=" + seguridad.CSRF_TOKEN).encode()
        boom = RuntimeError("detalle interno C:\\ruta\\secreta")
        with patch("anka.web.acciones.checked_run", side_effect=boom):
            with self.assertLogs("anka.web.server", "ERROR") as logs:
                sent = self.request("POST", "/refresh", body)
        self.assertEqual(sent["status"], 500)
        self.assertNotIn("ruta", sent["page"])
        self.assertIn("código de error", sent["page"])
        self.assertIsNotNone(logs.records[0].exc_info)  # el detalle va solo al registro

    def test_get_that_raises_returns_a_500_page_and_logs_the_traceback(self):
        failure = RuntimeError("falla al armar el resumen")
        with patch("anka.web.paginas.render_index", side_effect=failure):
            with self.assertLogs("anka.web.server", "ERROR") as logs:
                sent = self.request("GET", "/?message=hola")
        self.assertEqual(sent["status"], 500)
        self.assertNotIn("falla al armar el resumen", sent["page"])  # el detalle no sale al navegador
        self.assertIn("código de error", sent["page"])
        self.assertIn("Error atendiendo GET /", logs.output[0])
        self.assertNotIn("message=hola", logs.output[0])  # la consulta no se registra
        self.assertIsNotNone(logs.records[0].exc_info)


if __name__ == "__main__":
    unittest.main()
