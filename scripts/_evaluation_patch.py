# -*- coding: utf-8 -*-
"""新增效果评测页（确定性 benchmark，不调用模型）。"""
from pathlib import Path

ROOT = Path(r"F:\deepseek workarea\出版AI系统")
MAIN = ROOT / "app" / "main.py"
TPL = ROOT / "app" / "templates" / "evaluation.html"
BASE = ROOT / "app" / "templates" / "base.html"
CSS = ROOT / "app" / "static" / "style.css"

BENCHMARKS = [
    {"name": "教辅重印基线", "params": {"format": "16开", "print_sheet": 12, "qty": 3000, "paper_type": "胶版纸80g", "binding": "胶装", "color_pages": 4, "process": "覆膜"}, "expected_total": 14638.00, "expected_per": 4.88},
    {"name": "输入变化（14印张/5000册）", "params": {"format": "16开", "print_sheet": 14, "qty": 5000, "paper_type": "胶版纸80g", "binding": "胶装", "color_pages": 4, "process": "覆膜"}, "expected_total": 27890.00, "expected_per": 5.58},
    {"name": "线圈培训手册（单色/无工艺）", "params": {"format": "32开", "print_sheet": 6, "qty": 2000, "paper_type": "双胶纸80g", "binding": "线圈装", "color_pages": 0, "process": ""}, "expected_total": 4384.00, "expected_per": 2.19},
    {"name": "铜版纸精装画册", "params": {"format": "16开", "print_sheet": 8, "qty": 2000, "paper_type": "铜版纸157g", "binding": "精装", "color_pages": 4, "process": "覆膜"}, "expected_total": 11888.00, "expected_per": 5.94},
]

text = MAIN.read_text(encoding="utf-8")
if "@app.get(\"/evaluation\"" not in text:
    anchor = '@app.get("/dashboard", include_in_schema=False)'
    block = '''@app.get("/evaluation", include_in_schema=False)
def evaluation_page(request: Request, db: Session = Depends(get_db)):
    """确定性效果评测：固定 benchmark 参数 → calculate_cost/validate_spec → 与预期对比。"""
    user = get_current_user(request)
    if user is None:
        return _login_redirect()
    from app.agent.tools import calculate_cost, validate_spec

    cases = []
    passed = 0
    for item in BENCHMARKS:
        params = dict(item["params"])
        calc = calculate_cost(params)
        val = validate_spec(params)
        data = calc.get("data") or {}
        actual_total = data.get("total")
        actual_per = data.get("per_copy")
        valid = bool((val.get("data") or {}).get("valid"))
        ok = (
            valid
            and actual_total is not None
            and abs(float(actual_total) - float(item["expected_total"])) < 0.011
            and actual_per is not None
            and abs(float(actual_per) - float(item["expected_per"])) < 0.011
        )
        if ok:
            passed += 1
        cases.append(
            {
                "name": item["name"],
                "params": params,
                "expected_total": item["expected_total"],
                "expected_per": item["expected_per"],
                "actual_total": actual_total,
                "actual_per": actual_per,
                "valid": valid,
                "ok": ok,
                "source_ids": (calc.get("source") or {}).get("ids") or [],
                "error_code": calc.get("error_code"),
            }
        )
    context = _page_context(
        user,
        "evaluation",
        cases=cases,
        passed=passed,
        total=len(cases),
    )
    return _render(request, "evaluation.html", context)


@app.get("/dashboard", include_in_schema=False)'''
    MAIN.write_text(text.replace(anchor, block, 1), encoding="utf-8")
    print("evaluation route added")
else:
    print("evaluation exists")

if not TPL.exists():
    TPL.write_text('''{% extends "base.html" %}
{% block title %}效果评测 · 印制参数校验与成本测算 AI 系统{% endblock %}
{% block content %}
<div class="page-head">
  <div>
    <h1 class="page-title">效果评测</h1>
    <p class="page-sub">固定 benchmark 参数 → 真实工具计算 → 与预期值自动比对（不调用模型，可重复执行）。</p>
  </div>
  <div class="page-actions">
    <a class="btn btn-ghost" href="/dashboard">仪表盘</a>
    <a class="btn btn-ghost" href="/evaluation">重新评测</a>
  </div>
</div>

<div class="dash-cards">
  <div class="dash-card"><span class="dash-label">评测通过</span><strong class="dash-value">{{ passed }} / {{ total }}</strong><span class="dash-sub">确定性工具回归</span></div>
  <div class="dash-card"><span class="dash-label">口径</span><strong class="dash-value">calculate_cost</strong><span class="dash-sub">资料库单价 + 规则库校验</span></div>
</div>

<div class="card">
  <div class="card-header"><h2 class="card-title">用例与结果</h2><span class="hint">预期值来自任务书演示口径与人工复算；金额误差阈值 0.01 元</span></div>
  <div class="card-body table-wrap">
    <table class="data-table compact">
      <thead><tr><th>#</th><th>用例</th><th>参数</th><th class="text-right">预期总价</th><th class="text-right">实际总价</th><th class="text-right">预期单册</th><th class="text-right">实际单册</th><th>规则</th><th>结果</th></tr></thead>
      <tbody>
        {% for c in cases %}
        <tr>
          <td class="mono">{{ loop.index }}</td>
          <td><b>{{ c.name }}</b></td>
          <td class="muted small">{{ c.params.format }} / {{ c.params.print_sheet }}印张 / {{ c.params.qty }}册 / {{ c.params.paper_type }} / {{ c.params.binding }}</td>
          <td class="text-right mono">{{ "%.2f"|format(c.expected_total) }}</td>
          <td class="text-right mono">{{ "%.2f"|format(c.actual_total or 0) }}</td>
          <td class="text-right mono">{{ "%.2f"|format(c.expected_per) }}</td>
          <td class="text-right mono">{{ "%.2f"|format(c.actual_per or 0) }}</td>
          <td>{% if c.valid %}<span class="badge badge-green">通过</span>{% else %}<span class="badge badge-red">问题</span>{% endif %}</td>
          <td>{% if c.ok %}<span class="badge badge-green">PASS</span>{% else %}<span class="badge badge-red">FAIL</span>{% endif %}</td>
        </tr>
        {% endfor %}
      </tbody>
    </table>
  </div>
</div>

<div class="card">
  <div class="card-header"><h2 class="card-title">说明</h2></div>
  <div class="card-body">
    <p class="muted">本页用于回答“AI 计算效果如何验证”：</p>
    <ul class="md-list">
      <li>金额由 <code>calculate_cost</code> 从资料库取价计算，不经过模型，结果确定、可重复；</li>
      <li>每个用例同时执行 <code>validate_spec</code>，确保参数合规；</li>
      <li>预期值来自需求/演示口径，失败时可直接对照参数与来源 ID 排查；</li>
      <li>真实模型调用的结果与执行记录，仍以任务详情页为准（本页只评测“工具计算正确性”）。</li>
    </ul>
  </div>
</div>
{% endblock %}
''', encoding="utf-8")
    print("evaluation template written")

base = BASE.read_text(encoding="utf-8")
if 'href="/evaluation"' not in base:
    base = base.replace(
        '<a class="nav-link{% if active_nav == \'dashboard\' %} is-active{% endif %}" href="/dashboard">仪表盘</a>',
        '<a class="nav-link{% if active_nav == \'dashboard\' %} is-active{% endif %}" href="/dashboard">仪表盘</a>\n      <a class="nav-link{% if active_nav == \'evaluation\' %} is-active{% endif %}" href="/evaluation">效果评测</a>',
        1,
    )
    BASE.write_text(base, encoding="utf-8")
    print("nav evaluation added")

css = CSS.read_text(encoding="utf-8")
if ".evaluation" not in css:
    # 复用 dashboard 样式即可
    pass
print("done")
