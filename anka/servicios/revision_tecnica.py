"""Las decisiones de una persona sobre la revisión técnica de un caso y sobre lo que propone el modelo.

Decidir con qué preguntas seguir (o que con lo que hay alcanza), cambiar de idea antes de enviar, asumir la revisión de
una lectura sin verificar y adoptar o descartar la tabla que propone el modelo. Ninguna envía nada: solo guardan la
decisión, mueven el estado del caso (con `cambiar_estado`) y dejan constancia en el historial.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from datetime import datetime
from uuid import uuid4

from anka.dominio.caso import load_analysis
from anka.dominio.errores import ErrorDeValidacion
from anka.dominio.preguntas import DESCARTAR, candidatas, con_decisiones, decidir, internas, para_el_cliente
from anka.dominio.reception import reception_assessment
from anka.dominio.status import QuoteStatus
from anka.dominio.work_contract import archivos_sin_verificar, lectura_sin_verificar
from anka.infra.logging_setup import get_logger
from anka.infra.store import add_table_version, record_event, transaccion
from anka.servicios.estados import cambiar_estado
from anka.servicios.hoja_de_calculo import programar_actualizacion_de_hoja
from anka.servicios.repositorio import actualizar_caso

log = get_logger(__name__)


def resolve_model_proposal(thread_id: str, use_proposal: bool) -> str:
    """La persona adopta la tabla que propone el modelo o conserva la suya."""
    with transaccion() as db:
        row = db.execute("SELECT analysis_json FROM quotes WHERE thread_id=?", (thread_id,)).fetchone()
        if not row:
            raise ErrorDeValidacion("Este caso ya no existe. Vuelve a Mis casos.")
        analysis = load_analysis(row["analysis_json"])
        proposed = analysis.pop("pending_model_activities", None)
        if not isinstance(proposed, list):
            return "No había una propuesta pendiente."
        now = datetime.now().astimezone().isoformat(timespec="seconds")
        if use_proposal:
            add_table_version(db, thread_id, proposed, "dashboard",
                              "Adoptaste la tabla propuesta por el modelo", "otro")
            analysis["activities"] = proposed
            analysis.pop("manual_edit", None)   # vuelve a seguir al modelo
            analysis.pop("propuesta_descartada", None)
            summary = f"Adoptaste la tabla propuesta por el modelo ({len(proposed)} actividades)."
        else:
            analysis["propuesta_descartada"] = proposed      # para poder recuperarla (U-57)
            summary = "Mantuviste tu tabla y descartaste la propuesta del modelo."
        actualizar_caso(db, thread_id,
            analysis_json=json.dumps(analysis, ensure_ascii=False),
            updated_at=now)
        record_event(db, thread_id, "proposal", "dashboard", f"{now}-{uuid4().hex[:6]}", summary, now)
    programar_actualizacion_de_hoja(thread_id)   # la tabla pudo cambiar: la hoja de Google se actualiza sola
    if not use_proposal:
        summary += " Si fue un error, puedes recuperar la propuesta en la pestaña «Historial»."
    return summary


def recuperar_propuesta(thread_id: str) -> str:
    """Deshace «Dejar mi tabla»: la propuesta que Work había hecho vuelve a quedar por revisar (U-57).

    No toca la tabla de la persona ni envía nada; solo vuelve a poner la propuesta como pendiente y lo anota."""
    with transaccion() as db:
        row = db.execute("SELECT analysis_json FROM quotes WHERE thread_id=?", (thread_id,)).fetchone()
        if not row:
            raise ErrorDeValidacion("Este caso ya no existe. Vuelve a Mis casos.")
        analysis = load_analysis(row["analysis_json"])
        if isinstance(analysis.get("pending_model_activities"), list):
            return "Ya hay una propuesta de Work por revisar: está arriba, en el caso."
        guardada = analysis.pop("propuesta_descartada", None)
        if not isinstance(guardada, list) or not guardada:
            return "No hay ninguna propuesta que recuperar."
        analysis["pending_model_activities"] = guardada
        now = datetime.now().astimezone().isoformat(timespec="seconds")
        summary = f"Recuperaste la propuesta de Work que habías dejado de lado ({len(guardada)} actividades)."
        actualizar_caso(db, thread_id, analysis_json=json.dumps(analysis, ensure_ascii=False), updated_at=now)
        record_event(db, thread_id, "proposal", "dashboard", f"{now}-{uuid4().hex[:6]}", summary, now)
    return summary + " Arriba puedes aceptarla o dejar tu tabla."


def volver_a_decidir(thread_id: str, accion: str) -> str:
    """Cambiar de idea antes de enviar las preguntas (U-27): volver a decidirlas o desistir de preguntar.

    Solo mientras el correo no ha salido. `reconsiderar` devuelve el caso a la decisión técnica con las elecciones
    conservadas; `suficiente` da la información por suficiente, como en la decisión técnica. No envía nada ni borra el
    borrador que ya esté en Gmail.
    """
    if accion not in ("reconsiderar", "suficiente"):
        raise ErrorDeValidacion("No entendimos esa opción. Recarga la página e inténtalo otra vez.")
    with transaccion() as db:
        row = db.execute(
            "SELECT status, analysis_json, sent_at, draft_id FROM quotes WHERE thread_id=?", (thread_id,)).fetchone()
        if not row:
            raise ErrorDeValidacion("Este caso ya no existe. Vuelve a Mis casos.")
        analysis = load_analysis(row["analysis_json"])
        decision = analysis.get("manual_review") or {}
        enviado = bool(row["sent_at"]) and str(row["sent_at"]) >= str(decision.get("at") or "")
        if (row["status"] not in (QuoteStatus.REVIEW_PENDING, QuoteStatus.DRAFT_CREATED)
                or decision.get("decision") != "ask_client" or enviado):
            raise ErrorDeValidacion("Ya no puedes cambiar la decisión: el correo con las preguntas ya salió.")
        now = datetime.now().astimezone().isoformat(timespec="seconds")
        if accion == "reconsiderar":
            analysis.pop("manual_review", None)         # las elecciones (`preguntas`) se conservan
            nuevo = QuoteStatus.REVIEW
            texto = "Volviste a la decisión técnica antes de enviar el correo: puedes cambiar qué preguntas hacer."
            mensaje = "Volviste a decidir. Tus preguntas siguen ahí: cámbialas y prepara el correo."
        else:
            analysis["manual_review"] = {"decision": "sufficient", "at": now, "questions": []}
            nuevo = QuoteStatus.READY_REVIEW
            texto = "Decidiste no preguntar al cliente antes de enviar el correo: con esto alcanza."
            mensaje = "Listo: con esto alcanza. Ya puedes emitir la T y crear la carpeta en Drive."
        if row["draft_id"]:
            mensaje += " El borrador que guardaste en Gmail sigue ahí; no se borra."
        cambiar_estado(db, thread_id, nuevo, "volver_a_decidir", updated_at=now,
                       analysis_json=json.dumps(analysis, ensure_ascii=False))
        record_event(db, thread_id, "review_decision", "dashboard", f"{now}-{uuid4().hex[:6]}", texto, now)
    log.info("%s: volvió a la decisión técnica antes de enviar (%s)", thread_id, accion)
    return mensaje


def aceptar_lectura_revisada(thread_id: str) -> str:
    """Una persona asume la revisión de los archivos que Anka no pudo verificar y el caso sigue su curso (U-29).

    No cambia el estado ni envía nada: levanta el bloqueo de la pantalla y deja constancia en el historial de quién lo
    hizo, cuándo y sobre qué archivos. Un análisis nuevo de Work reemplaza esta marca y se verifica de nuevo.
    """
    with transaccion() as db:
        row = db.execute("SELECT analysis_json FROM quotes WHERE thread_id=?", (thread_id,)).fetchone()
        if not row:
            raise ErrorDeValidacion("Este caso ya no existe. Vuelve a Mis casos.")
        analysis = load_analysis(row["analysis_json"])
        if not lectura_sin_verificar(analysis) or analysis.get("reading_review"):
            raise ErrorDeValidacion("La lectura de este caso no está pendiente de verificar.")
        now = datetime.now().astimezone().isoformat(timespec="seconds")
        archivos = archivos_sin_verificar(analysis)
        analysis["reading_review"] = {"at": now, "files": archivos}
        actualizar_caso(db, thread_id, updated_at=now, analysis_json=json.dumps(analysis, ensure_ascii=False))
        cuales = f": {', '.join(archivos)}" if archivos else ""
        record_event(db, thread_id, "reading_reviewed", "dashboard", f"{now}-{uuid4().hex[:6]}",
                     f"Una persona asumió la revisión de los archivos que Anka no pudo verificar{cuales}.", now)
    log.info("%s: una persona asumió la revisión de la lectura sin verificar", thread_id)
    return "Listo: la lectura quedó como revisada por ti. Ya puedes ver los resultados de Work y decidir cómo seguir."


def resolve_technical_review(
    thread_id: str, decision: str, questions_text: str = "", *,
    elecciones: Mapping[str, tuple[str, str]] | None = None, nuevas: str = "",
) -> str:
    """Una persona cierra la revisión técnica: información suficiente, preguntar al cliente o guardar sus decisiones.

    Guarda la decisión en `analysis["manual_review"]` (quita el bloqueo técnico de la evaluación) y mueve el estado. No
    envía nada. Con `elecciones` (id de pregunta → decisión y texto) cada pregunta se decide por separado (U-22): las
    que se le harán al cliente van al correo, las internas quedan en los pendientes de Anka y las descartadas
    desaparecen; `decision="internal_only"` guarda esas decisiones sin pasar el caso a preguntas. Sin `elecciones`,
    `questions_text` (una pregunta por línea) sigue funcionando como antes. Si llega un mensaje nuevo en el hilo, el
    reanálisis reemplaza el análisis y las decisiones se descartan: lo nuevo se revisa otra vez.
    """
    if decision not in ("sufficient", "ask_client", "internal_only"):
        raise ErrorDeValidacion("No entendimos esa opción. Recarga la página e inténtalo otra vez.")
    if decision == "internal_only" and elecciones is None:
        raise ErrorDeValidacion("Falta elegir qué hacer con alguna pregunta.")
    with transaccion() as db:
        row = db.execute(
            "SELECT status, analysis_json FROM quotes WHERE thread_id=?", (thread_id,)
        ).fetchone()
        if not row:
            raise ErrorDeValidacion("Este caso ya no existe. Vuelve a Mis casos.")
        if row["status"] != QuoteStatus.REVIEW:
            raise ErrorDeValidacion("Este caso ya no espera una decisión tuya.")
        now = datetime.now().astimezone().isoformat(timespec="seconds")
        analysis = load_analysis(row["analysis_json"])
        if decision == "sufficient" and not analysis.get("activities"):
            raise ErrorDeValidacion("Todavía no hay actividades en la tabla. Agrega al menos una antes de confirmar "
                                    "que alcanza.")
        por_dentro = descartadas = 0
        if elecciones is not None:
            actuales = con_decisiones(candidatas(analysis, reception_assessment(analysis)), analysis.get("preguntas"))
            decididas = decidir(actuales, elecciones, nuevas.splitlines(), "dashboard", now)
            analysis["preguntas"] = decididas
            questions = para_el_cliente(decididas)
            por_dentro = len(internas(decididas))
            descartadas = sum(1 for p in decididas if p.get("decision") == DESCARTAR)
        else:
            questions = [line.strip()[:500] for line in questions_text.splitlines() if line.strip()][:10]
        if decision == "ask_client" and not questions:
            raise ErrorDeValidacion("Escribe al menos una pregunta para el cliente o elige «Con esto alcanza».")
        if decision == "ask_client" and elecciones is None:
            # el cuadro de la pantalla las vuelve a traer si la persona reconsidera (U-31)
            analysis["ultimas_preguntas"] = questions
        if decision == "internal_only":
            new_status = QuoteStatus.REVIEW  # la revisión técnica sigue abierta: solo se guardaron decisiones
            texto = (f"Decidiste cómo tratar las preguntas: {por_dentro} por resolver dentro de Anka, "
                     f"{descartadas} descartada(s).")
        else:
            analysis["manual_review"] = {
                "decision": decision, "at": now, "questions": questions if decision == "ask_client" else [],
            }
            new_status = QuoteStatus.READY_REVIEW if decision == "sufficient" else QuoteStatus.REVIEW_PENDING
            aparte = (f"; {por_dentro} por resolver dentro de Anka" if por_dentro else "") + (
                f"; {descartadas} descartada(s)" if descartadas else "")
            texto = ("Decidiste que con esto alcanza." if decision == "sufficient"
                     else f"Decidiste preguntar al cliente ({len(questions)} pregunta(s){aparte}).")
        cambiar_estado(db, thread_id, new_status, "decidir_revision_tecnica",
            analysis_json=json.dumps(analysis, ensure_ascii=False),
            updated_at=now)
        record_event(db, thread_id, "review_decision", "dashboard", f"{now}-{uuid4().hex[:6]}", texto, now)
    log.info("%s: revisión técnica resuelta por una persona (%s)", thread_id, decision)
    if decision == "sufficient":
        return "Listo: con esto alcanza. Ya puedes crear la carpeta del proyecto en Drive."
    if decision == "internal_only":
        return "Guardado. Las preguntas internas las verá el equipo de Anka."
    return "Listo. Lee el correo en «Respuesta al cliente» y envíalo."
