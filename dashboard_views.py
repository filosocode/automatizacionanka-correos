"""Fragmentos HTML del dashboard sin acceso a datos ni a Gmail/Drive."""

from __future__ import annotations

import html
from collections import Counter

from activities import ACTIVITY_COLUMNS, ESTADOS


MODE_LABELS = {
    "local": "Ollama (local)",
    "api": "API de OpenAI",
    "chatgpt_work": "ChatGPT Work",
}


def mode_selector(current: str | None, api_key_set: bool = True) -> str:
    labels = dict(MODE_LABELS)
    if not api_key_set:
        labels["api"] += " (sin clave)"
    options = "".join(
        f"<option value='{value}'{' selected' if value == current else ''}>{html.escape(label)}</option>"
        for value, label in labels.items()
    )
    if current not in MODE_LABELS:
        options = "<option value='' selected disabled>Sin configurar</option>" + options
    return (
        "<form method=\"post\" action=\"/mode\" class=\"mode-form\">"
        "<label for=\"mode\">Motor</label>"
        f"<select id=\"mode\" name=\"mode\">{options}</select>"
        "<button type=\"submit\" class=\"secondary\">Cambiar</button></form>"
    )


def render_page(title: str, body: str, message: str = "", unread_count: int = 0,
                mode: str | None = None, api_key_set: bool = True) -> str:
    notice = f"<div class='notice'>{html.escape(message)}</div>" if message else ""
    notification_badge = (
        f"<span class='nav-count'>{unread_count}</span>" if unread_count else ""
    )
    return f"""<!doctype html>
<html lang="es">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{html.escape(title)} - Anka</title>
  <style>
    :root {{
      color-scheme: light;
      --ink: #18232b;
      --muted: #63717b;
      --line: #d9e0e3;
      --bg: #f3f6f5;
      --panel: #ffffff;
      --accent: #126a62;
      --accent-dark: #0d514b;
      --accent-soft: #e1f0ed;
      --warn: #9b5b13;
      --danger: #a23c34;
      --blue: #32667a;
    }}
    * {{ box-sizing: border-box; }}
    body {{
      margin: 0;
      font-family: "Segoe UI", Arial, sans-serif;
      color: var(--ink);
      background: var(--bg);
      overflow-x: hidden;
    }}
    header {{
      display: flex;
      justify-content: space-between;
      align-items: center;
      gap: 16px;
      padding: 14px 28px;
      background: var(--panel);
      border-bottom: 1px solid var(--line);
      position: sticky;
      top: 0;
      z-index: 5;
    }}
    .brand {{ display: flex; align-items: center; gap: 12px; min-width: 210px; }}
    .brand-mark {{
      display: grid; place-items: center; width: 34px; height: 34px;
      border-radius: 6px; background: var(--ink); color: white; font-weight: 750;
    }}
    .brand strong {{ display: block; font-size: 15px; }}
    .brand span {{ color: var(--muted); font-size: 12px; }}
    nav {{ display: flex; gap: 4px; }}
    nav a {{
      color: var(--muted); text-decoration: none; padding: 9px 11px;
      border-radius: 6px; font-weight: 600; font-size: 14px;
    }}
    nav a:hover {{ background: #edf1f1; color: var(--ink); }}
    .nav-count {{
      display: inline-grid; place-items: center; min-width: 20px; height: 20px;
      margin-left: 5px; padding: 0 5px; border-radius: 10px;
      background: var(--danger); color: white; font-size: 11px; font-weight: 750;
    }}
    h1 {{ margin: 0; font-size: 24px; font-weight: 680; letter-spacing: 0; }}
    h2 {{ margin: 0 0 12px; font-size: 18px; }}
    h3 {{ margin: 0; font-size: 15px; }}
    p {{ line-height: 1.5; }}
    main {{ width: min(1240px, 100%); margin: 0 auto; padding: 28px 24px 48px; }}
    .page-head {{
      display: flex; justify-content: space-between; align-items: flex-end;
      gap: 18px; margin-bottom: 22px;
    }}
    .page-head p {{ margin: 5px 0 0; color: var(--muted); }}
    .mode-form {{ display: flex; gap: 6px; align-items: center; }}
    .mode-form label {{ font-size: 13px; color: var(--muted); }}
    .toolbar {{ display: flex; gap: 10px; align-items: center; flex-wrap: wrap; }}
    .button, button {{
      border: 0;
      border-radius: 6px;
      padding: 10px 14px;
      background: var(--accent);
      color: white;
      font: inherit;
      font-weight: 600;
      cursor: pointer;
      text-decoration: none;
      display: inline-flex;
      align-items: center;
      min-height: 40px;
    }}
    .button:hover, button:hover {{ background: var(--accent-dark); }}
    .secondary {{ background: #e7ecef; color: var(--ink); }}
    .secondary:hover {{ background: #d9e1e6; }}
    .danger {{ background: var(--warn); }}
    .notice {{
      margin-bottom: 18px;
      padding: 12px 14px;
      border: 1px solid #b7d6d1;
      background: #e9f5f3;
      border-radius: 6px;
    }}
    .grid {{ display: grid; gap: 12px; }}
    .kpis {{ grid-template-columns: repeat(4, minmax(0, 1fr)); margin-bottom: 24px; }}
    .kpi {{
      min-height: 112px; padding: 17px; background: var(--panel);
      border: 1px solid var(--line); border-radius: 8px;
    }}
    .kpi-label {{ color: var(--muted); font-size: 13px; font-weight: 600; }}
    .kpi-value {{ font-size: 31px; font-weight: 720; margin-top: 12px; }}
    .kpi-note {{ color: var(--muted); font-size: 12px; margin-top: 5px; }}
    .dashboard-grid {{ grid-template-columns: minmax(0, 1.15fr) minmax(280px, .85fr); }}
    .case {{
      display: grid;
      gap: 8px;
      padding: 15px 16px;
      background: var(--panel);
      border: 1px solid var(--line);
      border-radius: 8px;
    }}
    .case-row {{ grid-template-columns: minmax(180px, 1.4fr) 1fr 160px 130px auto; align-items: center; }}
    .case-row .button {{ min-height: 36px; padding: 8px 11px; justify-content: center; }}
    .meta {{ color: var(--muted); font-size: 14px; overflow-wrap: anywhere; }}
    .badge {{
      width: fit-content;
      padding: 4px 8px;
      border-radius: 999px;
      background: #e4f1ef;
      color: #124f49;
      font-size: 13px;
      font-weight: 650;
    }}
    .badge.warn {{ background: #fbedd9; color: #78450e; }}
    .badge.info {{ background: #e3edf1; color: #244f60; }}
    .section-head {{ display: flex; justify-content: space-between; gap: 12px; align-items: center; }}
    .metric-list {{ display: grid; gap: 14px; margin-top: 18px; }}
    .metric-row {{ display: grid; grid-template-columns: minmax(110px, 1fr) 2fr 38px; gap: 10px; align-items: center; }}
    .metric-label {{ overflow: hidden; text-overflow: ellipsis; white-space: nowrap; font-size: 14px; }}
    .metric-track {{ height: 9px; background: #e8eeee; border-radius: 3px; overflow: hidden; }}
    .metric-fill {{ height: 100%; background: var(--accent); border-radius: 3px; }}
    .metric-fill.blue {{ background: var(--blue); }}
    .metric-count {{ text-align: right; font-weight: 650; }}
    .insight {{ padding: 12px 0; border-bottom: 1px solid var(--line); }}
    .insight:last-child {{ border-bottom: 0; }}
    .notifications {{ display: grid; gap: 10px; margin-bottom: 24px; }}
    .notification {{
      display: grid; grid-template-columns: 12px minmax(0, 1fr) auto;
      gap: 12px; align-items: center; padding: 14px 16px;
      background: #fff; border: 1px solid #bdd9d4; border-radius: 8px;
    }}
    .notification-dot {{ width: 10px; height: 10px; border-radius: 50%; background: var(--accent); }}
    .notification strong {{ display: block; margin-bottom: 3px; }}
    .notification .button {{ min-height: 34px; padding: 7px 10px; }}
    .process {{ display: grid; grid-template-columns: repeat(4, 1fr); gap: 8px; margin-top: 14px; }}
    .process-step {{ padding: 12px; background: #eef2f2; border-left: 3px solid #b9c4c8; }}
    .process-step.done {{ background: var(--accent-soft); border-left-color: var(--accent); }}
    .process-step.current {{ background: #fbedd9; border-left-color: var(--warn); }}
    .process-step strong {{ display: block; font-size: 13px; }}
    .process-step span {{ display: block; margin-top: 3px; color: var(--muted); font-size: 12px; }}
    .insight strong {{ display: block; margin-bottom: 3px; }}
    .filters {{
      display: grid; grid-template-columns: 2fr 1fr 1fr; gap: 10px;
      margin-bottom: 16px;
    }}
    input, select, textarea {{
      width: 100%; padding: 10px 11px; border: 1px solid var(--line);
      border-radius: 6px; background: white; color: var(--ink); font: inherit;
    }}
    label {{ display: grid; gap: 6px; font-size: 13px; font-weight: 650; color: var(--muted); }}
    .layout {{ display: grid; grid-template-columns: 1fr; gap: 18px; }}
    .section {{
      padding: 18px;
      background: var(--panel);
      border: 1px solid var(--line);
      border-radius: 8px;
    }}
    .internal-note {{ margin-top: 12px; background: #f4f0df; border-color: #d9cd9b; }}
    .context-profile {{
      display: grid; grid-template-columns: 2fr 1.4fr 1fr; gap: 14px;
      padding: 14px 0; border-bottom: 1px solid var(--line); margin-bottom: 16px;
    }}
    .context-profile strong, .context-profile small {{ display: block; margin-top: 4px; }}
    .context-profile small {{ color: var(--muted); }}
    .context-grid {{ display: grid; grid-template-columns: repeat(3, 1fr); gap: 22px; }}
    .context-grid h3 {{ margin-bottom: 10px; }}
    .context-list {{ list-style: none; margin: 0; padding: 0; }}
    .context-list li {{
      display: grid; grid-template-columns: minmax(0, 1fr) auto; gap: 8px;
      padding: 7px 0; border-bottom: 1px solid var(--line); font-size: 14px;
    }}
    .context-list span {{ overflow-wrap: anywhere; }}
    .assessment-head {{ display: flex; gap: 12px; align-items: center; flex-wrap: wrap; }}
    .assessment-grid {{ display: grid; grid-template-columns: 1fr 1fr; gap: 24px; margin-top: 16px; }}
    .assessment-grid h3 {{ margin-bottom: 8px; }}
    .assessment-list {{ margin: 0; padding-left: 20px; }}
    .assessment-list li {{ padding: 4px 0; line-height: 1.4; }}
    .intake-note {{ margin: 18px 0 0; background: #e9f1f5; border-color: #b8ccd6; }}
    table {{ width: 100%; border-collapse: collapse; }}
    th, td {{
      padding: 10px;
      border-bottom: 1px solid var(--line);
      text-align: left;
      vertical-align: top;
    }}
    .section, .layout, form, label {{ min-width: 0; }}
    textarea {{
      min-height: 120px; resize: vertical; padding: 12px;
      font: 15px/1.45 Consolas, "Segoe UI", sans-serif;
      white-space: pre-wrap; overflow-wrap: anywhere;
    }}
    pre {{ white-space: pre-wrap; overflow-wrap: anywhere; margin: 0; }}
    .empty {{
      padding: 28px;
      background: var(--panel);
      border: 1px solid var(--line);
      border-radius: 8px;
      color: var(--muted);
    }}
    @media (max-width: 720px) {{
      header {{ align-items: stretch; flex-direction: column; padding: 13px 16px; position: static; }}
      nav {{ order: 3; overflow-x: auto; }}
      main {{ padding: 16px; }}
      .page-head {{ align-items: flex-start; flex-direction: column; }}
      .toolbar {{ width: 100%; }}
      .button, button {{ width: 100%; justify-content: center; }}
      .kpis, .dashboard-grid {{ grid-template-columns: 1fr 1fr; }}
      .case-row, .filters {{ grid-template-columns: 1fr; }}
      .notification {{ grid-template-columns: 12px minmax(0, 1fr); }}
      .notification .button {{ grid-column: 1 / -1; }}
      .process {{ grid-template-columns: 1fr 1fr; }}
      .context-profile, .context-grid, .assessment-grid {{ grid-template-columns: 1fr; }}
      .case-row .button {{ width: 100%; }}
    }}
    @media (max-width: 460px) {{
      .kpis, .dashboard-grid {{ grid-template-columns: 1fr; }}
    }}
  </style>
</head>
<body>
  <header>
    <div class="brand"><span class="brand-mark">A</span><div><strong>Anka</strong><span>Inteligencia comercial</span></div></div>
    <nav><a href="/">Resumen{notification_badge}</a><a href="/requests">Solicitudes</a></nav>
    <div class="toolbar">
      {mode_selector(mode, api_key_set)}
      <form method="post" action="/refresh">
        <button type="submit">Actualizar correo</button>
      </form>
    </div>
  </header>
  <main>{notice}{body}</main>
</body>
</html>"""


def _metric_rows(counter: Counter, color: str = "") -> str:
    if not counter:
        return "<p class='meta'>Aún no hay datos suficientes.</p>"
    maximum = max(counter.values()) or 1
    rows = []
    for label, count in counter.most_common(6):
        width = max(5, round(count / maximum * 100))
        rows.append(
            "<div class='metric-row'>"
            f"<div class='metric-label' title='{html.escape(str(label))}'>{html.escape(str(label))}</div>"
            f"<div class='metric-track'><div class='metric-fill {color}' style='width:{width}%'></div></div>"
            f"<div class='metric-count'>{count}</div></div>"
        )
    return "<div class='metric-list'>" + "".join(rows) + "</div>"


def _activities_table(activities: list[dict]) -> str:
    if not activities:
        return "<p class='meta'>No hay actividades identificadas.</p>"
    rows = []
    for item in activities:
        value = lambda key: html.escape(str(item.get(key) or ""))
        rows.append(
            "<tr>"
            f"<td>{value('activity')}</td>"
            f"<td>{value('servicio_tecnica')}</td>"
            f"<td>{value('ubicacion')}</td>"
            f"<td>{value('quantity')}</td>"
            f"<td>{value('unit')}</td>"
            f"<td>{value('entregable')}</td>"
            f"<td>{value('plazo_restriccion')}</td>"
            f"<td>{value('fuente')}</td>"
            f"<td>{value('estado')}</td>"
            "</tr>"
        )
    return (
        "<div style='overflow-x:auto'><table><thead><tr><th>Actividad</th>"
        "<th>Servicio/técnica</th><th>Ubicación</th><th>Cantidad</th>"
        "<th>Unidad</th><th>Entregable</th><th>Plazo/restricción</th>"
        "<th>Fuente</th><th>Estado</th></tr></thead><tbody>"
        + "\n".join(rows) + "</tbody></table>"
        "</div>"
    )


def _attachments_table(attachments: list[dict]) -> str:
    if not attachments:
        return "<p class='meta'>No hay adjuntos registrados.</p>"
    rows = []
    for item in attachments:
        blocked = ", ".join(str(value) for value in (item.get("blocked_fields") or [])) or "ninguno"
        value = lambda key: html.escape(str(item.get(key) or ""))
        rows.append(
            "<tr>"
            f"<td>{value('filename')}</td>"
            f"<td>{value('status')}</td>"
            f"<td>{value('reason') or 'lectura correcta'}</td>"
            f"<td>{html.escape(blocked)}</td>"
            "</tr>"
        )
    return (
        "<div style='overflow-x:auto'><table><thead><tr><th>Archivo</th>"
        "<th>Estado</th><th>Detalle</th><th>Datos bloqueados</th>"
        "</tr></thead><tbody>" + "\n".join(rows) + "</tbody></table></div>"
    )


def _reception_assessment_panel(assessment: dict) -> str:
    questions = assessment.get("questions", [])
    checks = assessment.get("technical_checks", [])
    question_html = (
        "<ol class='assessment-list'>" + "".join(
            f"<li>{html.escape(str(question))}</li>" for question in questions
        ) + "</ol>"
        if questions else
        "<p class='meta'>No faltan datos básicos para iniciar la revisión interna.</p>"
    )
    check_html = "<ul class='assessment-list'>" + "".join(
        f"<li>{html.escape(str(check))}</li>" for check in checks
    ) + "</ul>"
    status_class = {
        "needs_client_information": "warn",
        "technical_review": "info",
        "sufficient_for_internal_review": "",
    }.get(assessment.get("status"), "info")
    return f"""
<div class="assessment-head">
  <span class="badge {status_class}">{html.escape(assessment.get('label', 'Pendiente de evaluación'))}</span>
  <span class="meta">Tipo detectado: {html.escape(assessment.get('service_family', 'Por clasificar'))}</span>
</div>
<p>{html.escape(assessment.get('reason', ''))}</p>
<div class="assessment-grid">
  <div><h3>Preguntas necesarias al cliente</h3>{question_html}</div>
  <div><h3>Validaciones para el equipo de Anka</h3>{check_html}</div>
</div>
<div class="notice intake-note"><strong>Etapa actual:</strong> recepción, lectura y aclaración. Esta evaluación no calcula precios ni confirma un alcance definitivo.</div>"""


def activities_editor(thread_id: str, activities: list[dict]) -> str:
    """Tabla editable: colores por estado, celdas vacias en gris y una fila nueva al final."""
    def cell(index: int, key: str, value: str) -> str:
        if key == "estado":
            options = "".join(
                f"<option value='{state}'{' selected' if state == value else ''}>{state}</option>"
                for state in ESTADOS
            )
            return f"<td><select name='a{index}_estado' class='estado-{html.escape(value or 'pendiente')}'>{options}</select></td>"
        empty = " class='vacio'" if not value or value == "no indicado" else ""
        return (
            f"<td><textarea name='a{index}_{key}' rows='2'{empty} "
            f"placeholder='no indicado'>{html.escape(value)}</textarea></td>"
        )

    rows = []
    for index, item in enumerate(activities):
        cells = "".join(cell(index, key, str(item.get(key) or "")) for key, _ in ACTIVITY_COLUMNS)
        rows.append(
            f"<tr>{cells}<td class='del'><label><input type='checkbox' name='a{index}_del' value='1'> Quitar</label></td></tr>"
        )
    new_index = len(activities)
    cells = "".join(
        cell(new_index, key, "pendiente" if key == "estado" else "")
        for key, _ in ACTIVITY_COLUMNS
    )
    rows.append(f"<tr class='nueva'>{cells}<td class='del'>Fila nueva</td></tr>")
    head = "".join(f"<th>{html.escape(label)}</th>" for _, label in ACTIVITY_COLUMNS)
    return f"""
<style>
  .act-table textarea, .act-table select {{ width: 100%; min-width: 120px; font: inherit; font-size: 13px; }}
  .act-table td {{ padding: 4px; vertical-align: top; }}
  .act-table textarea.vacio {{ background: #f0f2f3; color: #63717b; }}
  .act-table select.estado-confirmado {{ background: #d9ead3; }}
  .act-table select.estado-pendiente {{ background: #fff2cc; }}
  .act-table select.estado-contradictorio {{ background: #f4cccc; }}
  .act-table tr.nueva td {{ border-top: 2px dashed var(--line); }}
  .act-table .del {{ white-space: nowrap; font-size: 12px; color: var(--muted); }}
</style>
<form method="post" action="/activities">
  <input type="hidden" name="thread_id" value="{html.escape(thread_id)}">
  <input type="hidden" name="row_count" value="{len(activities)}">
  <div style="overflow-x:auto"><table class="act-table"><thead><tr>{head}<th></th></tr></thead>
  <tbody>{''.join(rows)}</tbody></table></div>
  <div class="toolbar" style="margin-top:12px">
    <button type="submit">Guardar tabla</button>
    <a class="button secondary" href="/export.xlsx?thread_id={html.escape(thread_id)}">Descargar Excel</a>
  </div>
  <p class="meta">Los cambios quedan marcados como edición manual. Una fila nueva se guarda solo si tiene datos.</p>
</form>"""
