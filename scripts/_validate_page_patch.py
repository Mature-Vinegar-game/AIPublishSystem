# -*- coding: utf-8 -*-
"""轻量增强：校验试算页面（不运行 AI，直接调用 validate_spec）。"""
from pathlib import Path

ROOT = Path(r"F:\deepseek workarea\出版AI系统")
MAIN = ROOT / "app" / "main.py"
TPL = ROOT / "app" / "templates" / "validate.html"
CSS = ROOT / "app" / "static" / "style.css"
BASE = ROOT / "app" / "templates" / "base.html"

text = MAIN.read_text(encoding="utf-8")
if "@app.get(\"/validate\"" not in text:
    anchor = '@app.get("/rules", include_in_schema=False)'
    block = '''@app.get("/validate", include_in_schema=False)
def validate_page(request: Request, db: Session = Depends(get_db)):
    user = get_current_user(request)
    if user is None:
        return _login_redirect()
    context = _page_context(
        user,
        "validate",
        **_form_options(db),
        form_data={},
        validation=None,
    )
    return _render(request, "validate.html", context)


@app.post("/validate", include_in_schema=False)
async def validate_submit(request: Request, db: Session = Depends(get_db)):
    user = get_current_user(request)
    if user is None:
        return _login_redirect()
    try:
        form = await request.form()
    except Exception:
        form = {}
    params: dict = {}
    fmt = _form_value(form, "format")
    if fmt:
        params["format"] = fmt
    sheet = _form_value(form, "print_sheet")
    if sheet:
        params["print_sheet"] = _to_float(sheet, 0)
    qty = _form_value(form, "qty")
    if qty:
        params["qty"] = _to_int(qty, 0)
    paper = _form_value(form, "paper_type")
    if paper:
        params["paper_type"] = paper
    binding = _form_value(form, "binding")
    if binding:
        params["binding"] = binding
    color = _form_value(form, "color_pages")
    if color:
        params["color_pages"] = _to_int(color, 0)
    process = _form_value(form, "process")
    if process:
        params["process"] = process

    from app.agent.tools import validate_spec

    result = validate_spec(params) if params else {"ok": False, "error_code": "TOOL_ARGS_INVALID", "data": {"valid": False, "issues": [{"field": "params", "message": "请至少填写一个参数", "rule_code": ""}]}}
    data = result.get("data") or {}
    context = _page_context(
        user,
        "validate",
        **_form_options(db),
        form_data=dict(form),
        validation={
            "ok": bool(result.get("ok")),
            "valid": bool(data.get("valid")),
            "issues": data.get("issues") or [],
            "normalized": data.get("normalized") or {},
            "error_code": result.get("error_code"),
            "error_message": result.get("error_message"),
            "params": params,
        },
    )
    return _render(request, "validate.html", context)


@app.get("/rules", include_in_schema=False)'''
    MAIN.write_text(text.replace(anchor, block, 1), encoding="utf-8")
    print("validate route added")
else:
    print("validate route exists")

if not TPL.exists():
    TPL.write_text('''{% extends "base.html" %}
{% block title %}校验试算 · 印制参数校验与成本测算 AI 系统{% endblock %}
{% block content %}
{% set f = form_data|default({}) %}
{% set paper_opts = paper_types if paper_types is defined and paper_types else [] %}
{% set binding_opts = binding_options if binding_options is defined and binding_options else [] %}
{% set process_opts = process_options if process_options is defined and process_options else [] %}
{% set v = validation %}
<div class="page-head">
  <div>
    <h1 class="page-title">校验试算</h1>
    <p class="page-sub">不运行 AI，直接用规则库对参数做一次 dry-run 校验；适合核对规则是否按预期生效。</p>
  </div>
  <div class="page-actions">
    <a class="btn btn-ghost" href="/rules">规则库</a>
    <a class="btn btn-ghost" href="/tasks">任务列表</a>
  </div>
</div>

<div class="form-layout">
  <div class="card">
    <div class="card-header"><h2 class="card-title">输入参数（可只填部分）</h2></div>
    <form method="post" action="/validate">
      <div class="form-grid">
        <div class="field">
          <label for="format">开本</label>
          <select id="format" name="format">
            <option value="">未确定</option>
            {% for opt in (formats if formats is defined and formats else ['16开','32开','大32开']) %}
            <option value="{{ opt }}"{% if f.get('format') == opt %} selected{% endif %}>{{ opt }}</option>
            {% endfor %}
          </select>
        </div>
        <div class="field">
          <label for="print_sheet">印张</label>
          <input id="print_sheet" name="print_sheet" type="number" min="0.5" max="50" step="0.5" value="{{ f.get('print_sheet','') }}">
        </div>
        <div class="field">
          <label for="qty">印数（册）</label>
          <input id="qty" name="qty" type="number" min="500" step="1" value="{{ f.get('qty','') }}">
        </div>
        <div class="field">
          <label for="paper_type">纸张</label>
          <select id="paper_type" name="paper_type">
            <option value="">未确定</option>
            {% for paper in paper_opts %}<option value="{{ paper }}"{% if f.get('paper_type') == paper %} selected{% endif %}>{{ paper }}</option>{% endfor %}
          </select>
        </div>
        <div class="field">
          <label for="binding">装订方式</label>
          <select id="binding" name="binding">
            <option value="">未确定</option>
            {% for opt in binding_opts %}<option value="{{ opt }}"{% if f.get('binding') == opt %} selected{% endif %}>{{ opt }}</option>{% endfor %}
          </select>
        </div>
        <div class="field">
          <label for="color_pages">色数档位</label>
          <select id="color_pages" name="color_pages">
            <option value="">未确定</option>
            {% for opt in [0,1,2,3,4] %}<option value="{{ opt }}"{% if f.get('color_pages')|string == opt|string %} selected{% endif %}>{{ opt }}{% if opt == 0 %}（单色）{% elif opt == 4 %}（四色）{% endif %}</option>{% endfor %}
          </select>
        </div>
        <div class="field">
          <label for="process">表面工艺</label>
          <select id="process" name="process">
            <option value="">未确定</option>
            {% for opt in process_opts %}<option value="{{ opt }}"{% if f.get('process') == opt %} selected{% endif %}>{{ opt }}</option>{% endfor %}
          </select>
        </div>
      </div>
      <div class="form-actions">
        <button class="btn btn-primary" type="submit">执行校验</button>
        <a class="btn btn-ghost" href="/validate">重置</a>
        <span class="hint form-actions-hint">校验直接调用 validate_spec；不写 AI 运行记录、不生成版本。</span>
      </div>
    </form>
  </div>

  <aside class="card side-card">
    <div class="card-header"><h2 class="card-title">校验结果</h2></div>
    <div class="side-body">
      {% if v %}
        {% if v.valid %}
        <div class="notice notice-success" role="status">校验通过：当前参数未发现规则问题。</div>
        {% else %}
        <div class="notice notice-danger" role="alert">校验未通过：发现 {{ v.issues|length }} 个问题</div>
        {% endif %}
        {% if v.error_message %}<p class="muted small">{{ v.error_code }}：{{ v.error_message }}</p>{% endif %}
        {% if v.issues %}
        <ul class="issue-list">
          {% for issue in v.issues %}
          <li>
            <span class="badge badge-amber">{{ issue.field|default('—') }}</span>
            {{ issue.message|default('') }}
            {% if issue.rule_code %}<span class="mono muted small">（{{ issue.rule_code }}）</span>{% endif %}
          </li>
          {% endfor %}
        </ul>
        {% endif %}
        {% if v.normalized %}
        <h3 class="side-title">normalized</h3>
        <pre class="json-block">{{ v.normalized|tojson(indent=2) }}</pre>
        {% endif %}
      {% else %}
      <p class="muted">填写参数并执行校验后，这里显示规则命中情况。</p>
      <p class="muted small">可以直接用这个页面演示“规则库是独立可运营的”：新增/停用规则后，校验结果会变化。</p>
      {% endif %}
    </div>
  </aside>
</div>
{% endblock %}
''', encoding="utf-8")
    print("validate template written")

# nav
base = BASE.read_text(encoding="utf-8")
if 'href="/validate"' not in base:
    base = base.replace(
        '<a class="nav-link{% if active_nav == \'rules\' %} is-active{% endif %}" href="/rules">规则库</a>',
        '<a class="nav-link{% if active_nav == \'rules\' %} is-active{% endif %}" href="/rules">规则库</a>\n      <a class="nav-link{% if active_nav == \'validate\' %} is-active{% endif %}" href="/validate">校验试算</a>',
        1,
    )
    BASE.write_text(base, encoding="utf-8")
    print("nav validate added")

# CSS
css = CSS.read_text(encoding="utf-8")
if ".issue-list" not in css:
    css += """

/* ---- 校验试算 ---- */
.issue-list { margin: 8px 0 0; padding-left: 18px; }
.issue-list li { margin: 6px 0; line-height: 1.6; font-size: 13px; }
"""
    CSS.write_text(css, encoding="utf-8")
    print("validate css added")
print("done")
