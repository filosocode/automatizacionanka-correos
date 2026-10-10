"""Las acciones de los formularios del dashboard (POST): una función por ruta.

Cada acción recibe el formulario, llama al servicio que corresponde y devuelve a dónde redirigir. Un
`ValueError` o un `ErrorDeAnka` es orientación para la persona: se muestra como mensaje, no como fallo.
El servidor (`server.py`) solo valida la petición, busca la acción en `POST_ROUTES` y redirige.
"""

from __future__ import annotations

from collections.abc import Callable
import urllib.error
import urllib.parse
import urllib.request
from anka.dominio.errores import ErrorDeAnka
from anka.dominio.activities import parse_activities_form
from anka.infra.modes import (
    set_mode,
)
from anka.infra.settings import load_config
from anka.infra.work_heartbeat import request_check_now

from anka.servicios.case_actions import (
    approve_review, delete_cases, issue_code_and_notify, save_dashboard_draft, send_dashboard_reply,
)
from anka.infra.logging_setup import get_logger
from anka.servicios.autorizacion import reautorizar_google
from anka.servicios.hoja_de_calculo import sincronizar_hoja, texto_del_error
from anka.servicios.repositorio import RepositorioDeCasos
from anka.servicios.estado_de_revision import checked_run, describe_check, sync_status_text
from anka.servicios import followup_reminders as fup
from anka.servicios.change_control import (
    assign_code, promote_to_te, resolve_change,
)
from anka.servicios.quotes import (
    dismiss_pending_links, dismiss_pending_links_many, mark_notifications_read, rescue_discarded, volver_a_decidir,
    merge_project_thread, resolve_link, resolve_model_proposal, resolve_technical_review, save_quote_metadata,
    seguir_sin_respuesta,
    separate_project_thread,
)
from anka.servicios.ediciones import save_activities



# --- Acciones de los formularios (POST). Cada una recibe el formulario y devuelve a dónde redirigir.
# Un ValueError (o un ErrorDeAnka) es orientación para la persona: se muestra
# como mensaje, no como fallo del servidor.

log = get_logger(__name__)

Form = dict[str, list[str]]


def field(form: Form, name: str, default: str = "") -> str:
    """Primer valor del campo del formulario (o de la URL), o el valor por defecto si no vino."""
    return form.get(name, [default])[0]


MAX_ENTERO = 2**53


def int_field(form: Form, name: str) -> int:
    """El campo como entero; vacío, ausente o que no es un número es 0 (una URL manipulada no es un error interno)."""
    try:
        valor = int(field(form, name, "0") or 0)
    except ValueError:
        return 0
    return valor if abs(valor) <= MAX_ENTERO else 0   # SQLite no guarda enteros de más de 64 bits


def _to_case(thread_id: str, message: str, suffix: str = "") -> str:
    return ("/case?thread_id=" + urllib.parse.quote(thread_id)
            + "&message=" + urllib.parse.quote(message) + suffix)


def _to(page: str, message: str) -> str:
    return page + "?message=" + urllib.parse.quote(message)


def _guidance(action: Callable[[], str], *errors: type[BaseException]) -> str:
    """Ejecuta la acción; si falla por algo que se le explica a la persona, devuelve ese texto como mensaje."""
    try:
        return action()
    except errors as exc:
        return str(exc)


def _post_refresh(form: Form) -> str:
    if load_config().get("active_mode") == "chatgpt_work":
        # El dashboard no lee Gmail en este modo: solo despierta al puente (que sí lo lee, en solo lectura).
        request_check_now()
        return _to("/", "Pedí al puente que revise Gmail ahora; el dashboard se actualiza solo en unos segundos. "
                   + sync_status_text())
    return _to("/", describe_check(checked_run(True, True, False, "dashboard")))


def _post_approve(form: Form) -> str:
    thread_id = field(form, "thread_id")
    message = _guidance(lambda: approve_review(
        thread_id, body_text=form.get("body", [None])[0],
        client_name=form.get("client_name", [None])[0],
        new_client=field(form, "new_client") == "1",
        allow_similar_project=field(form, "allow_similar") == "1",
    ), ErrorDeAnka)
    return _to_case(thread_id, message)


def _post_metadata(form: Form) -> str:
    thread_id = field(form, "thread_id")
    save_quote_metadata(
        thread_id, field(form, "client_name"), field(form, "client_type", "No clasificado"),
        field(form, "sector", "No clasificado"), field(form, "notes"))
    return _to_case(thread_id, "Información comercial guardada.")


def _post_draft(form: Form) -> str:
    thread_id = field(form, "thread_id")
    message = _guidance(lambda: save_dashboard_draft(
        thread_id, field(form, "body"), field(form, "to")), ValueError)
    return _to_case(thread_id, message)


def _post_send(form: Form) -> str:
    thread_id = field(form, "thread_id")
    # Un ValueError aquí suele ser «el mismo correo ya salió»: se explica, no es un fallo.
    message = _guidance(lambda: send_dashboard_reply(
        thread_id, field(form, "body"), field(form, "to"),
        int_field(form, "change_id"), field(form, "purpose"), esperar=field(form, "esperar") == "1"), ValueError)
    return _to_case(thread_id, message)


def _post_activities(form: Form) -> str:
    thread_id = field(form, "thread_id")
    return _to_case(thread_id, save_activities(thread_id, parse_activities_form(form), field(form, "reason")))


def _post_mode(form: Form) -> str:
    return _to("/", set_mode(field(form, "mode")))


def _post_rescue(form: Form) -> str:
    return _to("/discarded", rescue_discarded(field(form, "thread_id")))


def _post_review_decision(form: Form) -> str:
    thread_id = field(form, "thread_id")
    decision = field(form, "decision")
    ids = [i.strip() for i in form.get("q_id", []) if i.strip()]
    elecciones = {i: (field(form, f"q_decision_{i}"), field(form, f"q_text_{i}")) for i in ids} if ids else None
    message = _guidance(lambda: resolve_technical_review(
        thread_id, decision, field(form, "questions"),
        elecciones=elecciones, nuevas=field(form, "q_nuevas")), ValueError)
    # Tras decidir, directo al paso que sigue: el correo, o la emisión de la T; guardar decisiones no cambia de paso.
    anchor = {"ask_client": "#respuesta", "sufficient": "#emitir-t"}.get(decision, "")
    return _to_case(thread_id, message, anchor)


def _post_review_reopen(form: Form) -> str:
    """Cambiar de idea antes de enviar las preguntas (U-27): volver a elegirlas o desistir de preguntar."""
    thread_id, accion = field(form, "thread_id"), field(form, "accion")
    message = _guidance(lambda: volver_a_decidir(thread_id, accion), ValueError)
    return _to_case(thread_id, message, {"reconsiderar": "#decision", "suficiente": "#emitir-t"}.get(accion, ""))


def _post_seguir_sin_respuesta(form: Form) -> str:
    """Seguir con la T sin esperar la respuesta del cliente (U-28)."""
    thread_id = field(form, "thread_id")
    message = _guidance(lambda: seguir_sin_respuesta(thread_id), ValueError)
    return _to_case(thread_id, message, "#emitir-t")


def _post_project_merge(form: Form) -> str:
    thread_id = field(form, "thread_id")
    message = _guidance(lambda: merge_project_thread(thread_id, field(form, "other_thread_id")), ValueError)
    return _to_case(thread_id, message)


def _post_project_separate(form: Form) -> str:
    thread_id = field(form, "thread_id")
    message = _guidance(lambda: separate_project_thread(thread_id, field(form, "other_thread_id")), ValueError)
    return _to_case(thread_id, message)


def _post_followup(form: Form) -> str:
    thread_id = field(form, "thread_id")
    message = _guidance(lambda: fup.set_followup(thread_id, field(form, "action")), ValueError)
    return _to_case(thread_id, message, "#seguimiento")


def _post_code(form: Form) -> str:
    thread_id = field(form, "thread_id")
    message = _guidance(lambda: assign_code(thread_id, field(form, "number")), ValueError)
    return _to_case(thread_id, message, "&template=received#respuesta")


def _post_code_issue(form: Form) -> str:
    thread_id = field(form, "thread_id")
    message = _guidance(lambda: issue_code_and_notify(
        thread_id, field(form, "number"), field(form, "body"), field(form, "suggested_number")), ValueError)
    return _to_case(thread_id, message)


def _post_code_approve(form: Form) -> str:
    thread_id = field(form, "thread_id")
    message = _guidance(lambda: promote_to_te(thread_id), ValueError)
    return _to_case(thread_id, message, "&template=te_aprobada#respuesta")   # se prepara el correo; no se envía solo


def _post_change(form: Form) -> str:
    thread_id = field(form, "thread_id")
    message = _guidance(lambda: resolve_change(
        thread_id, int_field(form, "change_id"), field(form, "decision") == "yes"), ValueError)
    return _to_case(thread_id, message)


def _post_link(form: Form) -> str:
    thread_id = field(form, "thread_id")
    message = _guidance(lambda: resolve_link(
        thread_id, int(field(form, "link_id", "0")), field(form, "decision") == "yes"), ValueError)
    return _to_case(thread_id, message)


def _post_links_dismiss(form: Form) -> str:
    thread_id = field(form, "thread_id")
    ids: list[int] = []
    for value in form.get("link_id", []):
        try:
            ids.append(int(value))
        except (TypeError, ValueError):
            continue
    return _to_case(thread_id, dismiss_pending_links(thread_id, ids))


def _post_links_dismiss_all(form: Form) -> str:
    references: list[tuple[str, int]] = []
    for value in form.get("link_ref", []):
        try:
            thread_id, link_id = value.rsplit(":", 1)
            references.append((thread_id, int(link_id)))
        except (ValueError, TypeError):
            continue
    return _to("/requests", dismiss_pending_links_many(references))


def _post_proposal(form: Form) -> str:
    thread_id = field(form, "thread_id")
    message = _guidance(lambda: resolve_model_proposal(
        thread_id, field(form, "decision") == "use"), ValueError)
    return _to_case(thread_id, message)


def _post_delete(form: Form) -> str:
    return _to("/requests", delete_cases(form.get("thread_id", [])))


def _post_sheet(form: Form) -> str:
    thread_id = field(form, "thread_id")
    try:
        message = sincronizar_hoja(thread_id).mensaje
    except Exception as exc:     # Google o la red: se explica en el caso y se puede reintentar
        log.warning("%s: no se pudo actualizar la hoja de Google (%s)", thread_id, type(exc).__name__, exc_info=True)
        message = texto_del_error(exc)
        RepositorioDeCasos().registrar_error_de_hoja(thread_id, message)
    return _to_case(thread_id, message, "#hoja-google")


def _post_google_reauthorize(form: Form) -> str:
    return _to("/", reautorizar_google())


def _post_notifications_seen(form: Form) -> str:
    """La página de un caso, ya a la vista, marca como leídas las novedades de ese caso (U-25). Responde a un fetch."""
    thread_id = field(form, "thread_id")
    if thread_id:
        mark_notifications_read(thread_id)
    return "/state"


def _post_notifications_read(form: Form) -> str:
    mark_notifications_read()
    return _to("/", "Novedades marcadas como revisadas.")


POST_ROUTES: dict[str, Callable[[Form], str]] = {
    "/refresh": _post_refresh,
    "/approve": _post_approve,
    "/metadata": _post_metadata,
    "/draft": _post_draft,
    "/send": _post_send,
    "/activities": _post_activities,
    "/mode": _post_mode,
    "/rescue": _post_rescue,
    "/review-decision": _post_review_decision,
    "/review-reopen": _post_review_reopen,
    "/seguir-sin-respuesta": _post_seguir_sin_respuesta,
    "/followup": _post_followup,
    "/project-merge": _post_project_merge,
    "/project-separate": _post_project_separate,
    "/code": _post_code,
    "/code-issue": _post_code_issue,
    "/code-approve": _post_code_approve,
    "/change": _post_change,
    "/link": _post_link,
    "/links-dismiss": _post_links_dismiss,
    "/links-dismiss-all": _post_links_dismiss_all,
    "/proposal": _post_proposal,
    "/delete": _post_delete,
    "/hoja": _post_sheet,
    "/google/reautorizar": _post_google_reauthorize,
    "/notifications/read": _post_notifications_read,
    "/notifications/seen": _post_notifications_seen,
}
