# -*- coding: utf-8 -*-
"""步骤：新增运行仪表盘（统计页面，只读，additive）。"""
from pathlib import Path

ROOT = Path(r"F:\deepseek workarea\出版AI系统")
MAIN = ROOT / "app" / "main.py"
TPL = ROOT / "app" / "templates" / "dashboard.html"
CSS = ROOT / "app" / "static" / "style.css"
BASE = ROOT / "app" / "templates" / "base.html"

# 1) 模型 import 补齐
text = MAIN.read_text(encoding="utf-8")
if "BusinessTask," not in text:
    text = text.replace(
        "    AiResult,\n    AiTask,\n    Material,\n    Rule,\n)",
        "    AiResult,\n    AiTask,\n    BusinessTask,\n    Material,\n    ResultVersion,\n    ReviewRecord,\n    Rule,\n    ToolCallRecord,\n)",
        1,
    )
    MAIN.write_text(text, encoding="utf-8")
    print("imports added")

# 2) dashboard context + route
text = MAIN.read_text(encoding="utf-8")
if "@app.get(\"/dashboard\"" not in text:
    anchor = '@app.get("/tasks", include_in_schema=False)'
    block = '''def _dashboard_context(user: Optional[dict], db: Session) -> dict:
    """运行仪表盘：任务/AI/工具/版本/审核统计（只读）。"""
    from collections import Counter

    tasks = db.query(BusinessTask).all()
    ai_tasks = db.query(AiTask).all()
    tool_calls = db.query(ToolCallRecord).all()
    versions = db.query(ResultVersion).all()
    reviews = db.query(ReviewRecord).all()

    task_status_order = ["PENDING", "PENDING_REVIEW", "REJECTED", "COMPLETED"]
    task_counts = Counter(str(getattr(t, "status", "") or "") for t in tasks)
    task_total = len(tasks) or 1
    task_status_items = []
    for key in task_status_order:
        count = int(task_counts.get(key, 0))
        label = TASK_STATUS_LABELS.get(key, key)
        task_status_items.append(
            {"key": key, "label": label, "count": count, "pct": round(count * 100.0 / task_total, 1)}
        )

    ai_counts = Counter(str(getattr(a, "status", "") or "") for a in ai_tasks)
    ai_total = len(ai_tasks) or 1
    ai_status_items = []
    for key, label in (("SUCCEEDED", "完成"), ("FAILED", "失败"), ("RUNNING", "运行中"), ("PENDING", "待执行")):
        count = int(ai_counts.get(key, 0))
        ai_status_items.append({"key": key, "label": label, "count": count, "pct": round(count * 100.0 / ai_total, 1)})
    success_rate = round(ai_counts.get("SUCCEEDED", 0) * 100.0 / ai_total, 1) if ai_tasks else 0.0

    elapsed_values = []
    for ai in ai_tasks:
        counters = basis_service.parse_json(getattr(ai, "counters_json", None), {})
        if isinstance(counters, dict) and isinstance(counters.get("elapsed_ms"), (int, float)):
            elapsed_values.append(float(counters["elapsed_ms"]))
    avg_elapsed = round(sum(elapsed_values) / len(elapsed_values) / 1000.0, 1) if elapsed_values else 0.0

    tool_counts = Counter(str(getattr(c, "tool_name", "") or "") for c in tool_calls)
    tool_items = [
        {"name": name, "count": count}
        for name, count in tool_counts.most_common(10)
    ]

    source_counter: Counter = Counter()
    for call in tool_calls:
        ids = basis_service.parse_json(getattr(call, "source_ids_json", None), [])
        if isinstance(ids, list):
            for sid in ids:
                sid = str(sid or "").strip()
                if sid:
                    source_counter[sid] += 1
    source_items = [{"id": sid, "count": count} for sid, count in source_counter.most_common(8)]

    plan_tasks = [t for t in tasks if str(getattr(t, "business_type", "")) == "print_cost_plan"]
    recent = sorted(tasks, key=lambda t: str(getattr(t, "created_at", "") or ""), reverse=True)[:8]
    recent_items = [
        {
            "id": t.id,
            "task_no": t.task_no,
            "title": t.title,
            "status": t.status,
            "status_label": TASK_STATUS_LABELS.get(t.status, t.status),
            "business_type": t.business_type,
            "created_at": t.created_at,
        }
        for t in recent
    ]

    baseline = db.query(AiResult).filter(AiResult.business_task_id == 1).order_by(AiResult.id.desc()).first()
    baseline_data = {}
    if baseline is not None:
        structured = basis_service.parse_json(baseline.structured_json, {})
        baseline_data = {
            "total": structured.get("total"),
            "per_copy": structured.get("per_copy"),
            "created_at": baseline.created_at,
        }

    return _page_context(
        user,
        "dashboard",
        dashboard={
            "task_total": len(tasks),
            "ai_total": len(ai_tasks),
            "tool_total": len(tool_calls),
            "version_total": len(versions),
            "review_total": len(reviews),
            "task_status_items": task_status_items,
            "ai_status_items": ai_status_items,
            "success_rate": success_rate,
            "avg_elapsed": avg_elapsed,
            "tool_items": tool_items,
            "source_items": source_items,
            "plan_total": len(plan_tasks),
            "plan_pending": sum(1 for t in plan_tasks if t.status == "PENDING"),
            "recent_items": recent_items,
            "baseline": baseline_data,
        },
    )


@app.get("/dashboard", include_in_schema=False)
def dashboard_page(request: Request, db: Session = Depends(get_db)):
    user = get_current_user(request)
    if user is None:
        return _login_redirect()
    return _render(request, "dashboard.html", _dashboard_context(user, db))


@app.get("/tasks", include_in_schema=False)'''
    text = text.replace(anchor, block, 1)
    MAIN.write_text(text, encoding="utf-8")
    print("dashboard route added")
else:
    print("dashboard route exists")

# 3) 导航链接
base = BASE.read_text(encoding="utf-8")
if 'href="/dashboard"' not in base:
    base = base.replace(
        '<a class="nav-link{% if active_nav == \'tasks\' %} is-active{% endif %}" href="/tasks">任务列表</a>',
        '<a class="nav-link{% if active_nav == \'tasks\' %} is-active{% endif %}" href="/tasks">任务列表</a>\n      <a class="nav-link{% if active_nav == \'dashboard\' %} is-active{% endif %}" href="/dashboard">仪表盘</a>',
        1,
    )
    BASE.write_text(base, encoding="utf-8")
    print("nav added")

# 4) dashboard 模板
if not TPL.exists():
    TPL.write_text('''{% extends "base.html" %}
{% block title %}运行仪表盘 · 印制参数校验与成本测算 AI 系统{% endblock %}
{% block content %}
{% set d = dashboard %}
<div class="page-head">
  <div>
    <h1 class="page-title">运行仪表盘</h1>
    <p class="page-sub">任务、AI 运行、工具调用与审核情况总览（只读统计）。</p>
  </div>
  <div class="page-actions">
    <a class="btn btn-ghost" href="/tasks">任务列表</a>
    <a class="btn btn-ghost" href="/system">系统信息</a>
  </div>
</div>

<div class="dash-cards">
  <div class="dash-card"><span class="dash-label">业务任务</span><strong class="dash-value">{{ d.task_total }}</strong><span class="dash-sub">全部状态</span></div>
  <div class="dash-card"><span class="dash-label">AI 运行</span><strong class="dash-value">{{ d.ai_total }}</strong><span class="dash-sub">成功率 {{ d.success_rate }}%</span></div>
  <div class="dash-card"><span class="dash-label">工具调用</span><strong class="dash-value">{{ d.tool_total }}</strong><span class="dash-sub">执行记录</span></div>
  <div class="dash-card"><span class="dash-label">平均耗时</span><strong class="dash-value">{{ d.avg_elapsed }}s</strong><span class="dash-sub">AI 单次运行</span></div>
  <div class="dash-card"><span class="dash-label">方案任务</span><strong class="dash-value">{{ d.plan_total }}</strong><span class="dash-sub">待运行 {{ d.plan_pending }}</span></div>
  <div class="dash-card"><span class="dash-label">版本 / 审核</span><strong class="dash-value">{{ d.version_total }} / {{ d.review_total }}</strong><span class="dash-sub">可追溯记录</span></div>
</div>

<div class="dash-grid">
  <section class="card">
    <div class="card-header"><h2 class="card-title">任务状态分布</h2></div>
    <div class="card-body">
      {% for item in d.task_status_items %}
      <div class="bar-row">
        <span class="bar-label">{{ item.label }}</span>
        <div class="bar-track"><span class="bar-fill" style="width: {{ item.pct }}%"></span></div>
        <span class="bar-value">{{ item.count }}</span>
      </div>
      {% endfor %}
    </div>
  </section>

  <section class="card">
    <div class="card-header"><h2 class="card-title">AI 运行状态</h2></div>
    <div class="card-body">
      {% for item in d.ai_status_items %}
      <div class="bar-row">
        <span class="bar-label">{{ item.label }}</span>
        <div class="bar-track"><span class="bar-fill {{ 'bar-ok' if item.key == 'SUCCEEDED' else ('bar-bad' if item.key == 'FAILED' else '') }}" style="width: {{ item.pct }}%"></span></div>
        <span class="bar-value">{{ item.count }}</span>
      </div>
      {% endfor %}
      <p class="muted small">成功率 {{ d.success_rate }}%；平均单次耗时 {{ d.avg_elapsed }}s（含真实模型调用）。</p>
    </div>
  </section>

  <section class="card">
    <div class="card-header"><h2 class="card-title">工具调用 Top</h2></div>
    <div class="card-body table-wrap">
      <table class="data-table compact">
        <thead><tr><th>工具</th><th class="text-right">调用次数</th></tr></thead>
        <tbody>
          {% for item in d.tool_items %}
          <tr><td class="mono">{{ item.name }}</td><td class="text-right">{{ item.count }}</td></tr>
          {% else %}
          <tr><td colspan="2" class="muted">暂无调用记录</td></tr>
          {% endfor %}
        </tbody>
      </table>
    </div>
  </section>

  <section class="card">
    <div class="card-header"><h2 class="card-title">依据来源 Top</h2></div>
    <div class="card-body table-wrap">
      <table class="data-table compact">
        <thead><tr><th>资料 / 规则 ID</th><th class="text-right">被引用</th></tr></thead>
        <tbody>
          {% for item in d.source_items %}
          <tr><td class="mono">{{ item.id }}</td><td class="text-right">{{ item.count }}</td></tr>
          {% else %}
          <tr><td colspan="2" class="muted">暂无依据记录</td></tr>
          {% endfor %}
        </tbody>
      </table>
    </div>
  </section>

  <section class="card dash-wide">
    <div class="card-header"><h2 class="card-title">最近任务</h2></div>
    <div class="card-body table-wrap">
      <table class="data-table compact">
        <thead><tr><th>任务编号</th><th>标题</th><th>类型</th><th>状态</th><th>创建时间</th></tr></thead>
        <tbody>
          {% for t in d.recent_items %}
          <tr>
            <td class="mono nowrap"><a href="/tasks/{{ t.id }}">{{ t.task_no }}</a></td>
            <td>{{ t.title }}</td>
            <td><span class="badge {{ 'badge-blue' if t.business_type == 'print_cost_plan' else 'badge-gray' }}">{{ '方案模式' if t.business_type == 'print_cost_plan' else '标准任务' }}</span></td>
            <td>{{ t.status_label }}</td>
            <td class="muted nowrap">{{ t.created_at }}</td>
          </tr>
          {% endfor %}
        </tbody>
      </table>
    </div>
  </section>

  <section class="card">
    <div class="card-header"><h2 class="card-title">验收基线（示例任务）</h2></div>
    <div class="card-body">
      {% if d.baseline.total is not none %}
      <div class="baseline-row"><span>总成本</span><strong>{{ "%.2f"|format(d.baseline.total) }} 元</strong></div>
      <div class="baseline-row"><span>单册成本</span><strong>{{ "%.2f"|format(d.baseline.per_copy) }} 元</strong></div>
      <div class="baseline-row"><span>预期基线</span><strong>14638.00 / 4.88</strong></div>
      <p class="muted small">来自示例任务 BT-DEMO-0001 的最新结果，用于快速核对真实调用结果。</p>
      {% else %}
      <p class="muted">暂无示例任务结果。</p>
      {% endif %}
    </div>
  </section>
</div>
{% endblock %}
''', encoding="utf-8")
    print("dashboard template written")

# 5) CSS
css = CSS.read_text(encoding="utf-8")
if ".dash-cards" not in css:
    css += """

/* ---- 运行仪表盘 ---- */
.dash-cards { display: grid; grid-template-columns: repeat(auto-fit, minmax(160px, 1fr)); gap: 12px; margin-bottom: 16px; }
.dash-card { background: #fff; border: 1px solid var(--line); border-radius: 10px; padding: 14px 16px; box-shadow: var(--shadow-sm); display: flex; flex-direction: column; gap: 4px; }
.dash-label { font-size: 12px; color: var(--muted); }
.dash-value { font-size: 26px; line-height: 1.2; color: var(--primary); }
.dash-sub { font-size: 12px; color: var(--muted); }
.dash-grid { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 16px; }
.dash-wide { grid-column: 1 / -1; }
.bar-row { display: flex; align-items: center; gap: 10px; margin: 8px 0; }
.bar-label { width: 80px; font-size: 12.5px; color: var(--ink-2); }
.bar-track { flex: 1; height: 10px; background: #EEF2F7; border-radius: 6px; overflow: hidden; }
.bar-fill { display: block; height: 100%; background: linear-gradient(90deg, var(--primary-2), var(--primary)); border-radius: 6px; min-width: 2px; }
.bar-fill.bar-ok { background: #2E9E6B; }
.bar-fill.bar-bad { background: #D9534F; }
.bar-value { width: 36px; text-align: right; font-family: var(--mono); font-size: 12px; color: var(--ink); }
.baseline-row { display: flex; align-items: center; justify-content: space-between; padding: 6px 0; border-bottom: 1px dashed var(--line); font-size: 13px; }
.baseline-row strong { color: var(--primary); font-family: var(--mono); }
@media (max-width: 900px) { .dash-grid { grid-template-columns: 1fr; } }
"""
    CSS.write_text(css, encoding="utf-8")
    print("dashboard css added")

print("done")
