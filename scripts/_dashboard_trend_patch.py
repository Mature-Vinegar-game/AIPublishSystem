# -*- coding: utf-8 -*-
"""仪表盘增强：时间范围（7/30/全部）、趋势、失败原因分布。"""
from pathlib import Path

ROOT = Path(r"F:\deepseek workarea\出版AI系统")
MAIN = ROOT / "app" / "main.py"
TPL = ROOT / "app" / "templates" / "dashboard.html"
CSS = ROOT / "app" / "static" / "style.css"

m = MAIN.read_text(encoding="utf-8")
if "def _dashboard_context(user: Optional[dict], db: Session, days: int = 7)" not in m:
    old_sig = "def _dashboard_context(user: Optional[dict], db: Session) -> dict:"
    new_sig = "def _dashboard_context(user: Optional[dict], db: Session, days: int = 7) -> dict:"
    m = m.replace(old_sig, new_sig, 1)
    print("dashboard signature updated")

old_rows = """    tasks = db.query(BusinessTask).all()
    ai_tasks = db.query(AiTask).all()
    tool_calls = db.query(ToolCallRecord).all()
    versions = db.query(ResultVersion).all()
    reviews = db.query(ReviewRecord).all()"""
new_rows = """    from datetime import datetime as _dt, timedelta as _td

    all_tasks = db.query(BusinessTask).all()
    all_ai = db.query(AiTask).all()
    all_tool = db.query(ToolCallRecord).all()
    all_versions = db.query(ResultVersion).all()
    all_reviews = db.query(ReviewRecord).all()

    days = int(days or 7)
    if days <= 0:
        range_label = "全部时间"
        trend_days = 14
        cutoff_date = None
    else:
        range_label = f"近 {days} 天"
        trend_days = days
        cutoff_date = (_dt.now() - _td(days=days)).date().isoformat()

    def _in_range(row) -> bool:
        if cutoff_date is None:
            return True
        return str(getattr(row, "created_at", "") or "")[:10] >= cutoff_date

    tasks = [row for row in all_tasks if _in_range(row)]
    ai_tasks = [row for row in all_ai if _in_range(row)]
    tool_calls = [row for row in all_tool if _in_range(row)]
    versions = [row for row in all_versions if _in_range(row)]
    reviews = [row for row in all_reviews if _in_range(row)]"""
if old_rows in m:
    m = m.replace(old_rows, new_rows, 1)
    print("dashboard filtering added")
else:
    raise SystemExit("dashboard rows anchor missing")

old_plan = """    plan_tasks = [t for t in tasks if str(getattr(t, "business_type", "")) == "print_cost_plan"]"""
new_plan = """    plan_tasks = [t for t in all_tasks if str(getattr(t, "business_type", "")) == "print_cost_plan"]"""
m = m.replace(old_plan, new_plan, 1)

old_ctx = """    baseline = db.query(AiResult).filter(AiResult.business_task_id == 1).order_by(AiResult.id.desc()).first()
    baseline_data = {}"""
new_ctx = """    # 失败原因分布
    failure_counts = Counter(
        str(getattr(ai, "error_code", "") or "UNKNOWN")
        for ai in ai_tasks
        if str(getattr(ai, "status", "")) == "FAILED"
    )
    failure_items = [{"code": code, "count": count} for code, count in failure_counts.most_common(8)]

    # 每日趋势（任务创建 / AI 运行）
    today = _dt.now().date()
    dates = [today - _td(days=i) for i in range(trend_days - 1, -1, -1)]
    task_day_counts = Counter(str(getattr(t, "created_at", "") or "")[:10] for t in all_tasks)
    ai_day_counts = Counter(str(getattr(a, "created_at", "") or "")[:10] for a in all_ai)
    trend_items = []
    max_count = 1
    for d in dates:
        key = d.isoformat()
        tcount = int(task_day_counts.get(key, 0))
        acount = int(ai_day_counts.get(key, 0))
        max_count = max(max_count, tcount, acount)
        trend_items.append({"date": key[5:], "tasks": tcount, "ai": acount})
    for item in trend_items:
        item["task_pct"] = round(item["tasks"] * 100.0 / max_count, 1)
        item["ai_pct"] = round(item["ai"] * 100.0 / max_count, 1)

    baseline = db.query(AiResult).filter(AiResult.business_task_id == 1).order_by(AiResult.id.desc()).first()
    baseline_data = {}"""
if old_ctx in m:
    m = m.replace(old_ctx, new_ctx, 1)
    print("dashboard trend added")
else:
    raise SystemExit("dashboard baseline anchor missing")

old_map = """            "recent_items": recent_items,
            "baseline": baseline_data,
        },"""
new_map = """            "recent_items": recent_items,
            "baseline": baseline_data,
            "range_label": range_label,
            "days": days,
            "trend_items": trend_items,
            "failure_items": failure_items,
        },"""
m = m.replace(old_map, new_map, 1)

old_route = """@app.get("/dashboard", include_in_schema=False)
def dashboard_page(request: Request, db: Session = Depends(get_db)):
    user = get_current_user(request)
    if user is None:
        return _login_redirect()
    return _render(request, "dashboard.html", _dashboard_context(user, db))"""
new_route = """@app.get("/dashboard", include_in_schema=False)
def dashboard_page(request: Request, days: int = Query(default=7, ge=0, le=365), db: Session = Depends(get_db)):
    user = get_current_user(request)
    if user is None:
        return _login_redirect()
    return _render(request, "dashboard.html", _dashboard_context(user, db, days=days))"""
if old_route not in m:
    raise SystemExit("dashboard route anchor missing")
m = m.replace(old_route, new_route, 1)
MAIN.write_text(m, encoding="utf-8")
print("dashboard logic updated")

# Template
t = TPL.read_text(encoding="utf-8")
if "days=7" not in t:
    old_sub = """    <p class="page-sub">任务、AI 运行、工具调用与审核情况总览（只读统计）。</p>"""
    new_sub = """    <p class="page-sub">任务、AI 运行、工具调用与审核情况总览（只读统计） · 当前范围：{{ d.range_label }}</p>"""
    t = t.replace(old_sub, new_sub, 1)
    old_actions = """  <div class="page-actions">
    <a class="btn btn-ghost" href="/tasks">任务列表</a>
    <a class="btn btn-ghost" href="/system">系统信息</a>
  </div>"""
    new_actions = """  <div class="page-actions">
    <a class="btn btn-ghost{% if d.days == 7 %} is-active{% endif %}" href="/dashboard?days=7">近 7 天</a>
    <a class="btn btn-ghost{% if d.days == 30 %} is-active{% endif %}" href="/dashboard?days=30">近 30 天</a>
    <a class="btn btn-ghost{% if d.days == 0 %} is-active{% endif %}" href="/dashboard?days=0">全部</a>
    <a class="btn btn-ghost" href="/tasks">任务列表</a>
    <a class="btn btn-ghost" href="/system">系统信息</a>
  </div>"""
    t = t.replace(old_actions, new_actions, 1)
    old_baseline = """  <section class="card">
    <div class="card-header"><h2 class="card-title">验收基线（示例任务）</h2></div>"""
    trend_block = """  <section class="card card-wide">
    <div class="card-header"><h2 class="card-title">每日趋势（近 {{ d.trend_items|length }} 天）</h2></div>
    <div class="card-body">
      <div class="trend-table">
        {% for item in d.trend_items %}
        <div class="trend-row">
          <span class="trend-date">{{ item.date }}</span>
          <span class="trend-label">任务</span>
          <div class="bar-track"><span class="bar-fill" style="width: {{ item.task_pct }}%"></span></div>
          <span class="bar-value">{{ item.tasks }}</span>
          <span class="trend-label">AI</span>
          <div class="bar-track"><span class="bar-fill bar-ok" style="width: {{ item.ai_pct }}%"></span></div>
          <span class="bar-value">{{ item.ai }}</span>
        </div>
        {% endfor %}
      </div>
    </div>
  </section>

  <section class="card">
    <div class="card-header"><h2 class="card-title">失败原因 Top</h2></div>
    <div class="card-body table-wrap">
      <table class="data-table compact">
        <thead><tr><th>错误码</th><th class="text-right">次数</th></tr></thead>
        <tbody>
          {% for item in d.failure_items %}
          <tr><td class="mono">{{ item.code }}</td><td class="text-right">{{ item.count }}</td></tr>
          {% else %}
          <tr><td colspan="2" class="muted">当前范围内暂无失败记录</td></tr>
          {% endfor %}
        </tbody>
      </table>
    </div>
  </section>

  <section class="card">
    <div class="card-header"><h2 class="card-title">验收基线（示例任务）</h2></div>"""
    if old_baseline in t:
        t = t.replace(old_baseline, trend_block, 1)
    TPL.write_text(t, encoding="utf-8")
    print("dashboard template updated")

# CSS
css = CSS.read_text(encoding="utf-8")
if ".trend-table" not in css:
    css += """

/* ---- 仪表盘趋势 ---- */
.trend-table { display: flex; flex-direction: column; gap: 6px; }
.trend-row { display: grid; grid-template-columns: 52px 32px minmax(80px, 1fr) 34px 24px minmax(80px, 1fr) 34px; align-items: center; gap: 8px; font-size: 12px; }
.trend-date { font-family: var(--mono); color: var(--muted); }
.trend-label { color: var(--muted); text-align: right; }
.trend-row .bar-track { height: 8px; }
"""
    CSS.write_text(css, encoding="utf-8")
    print("trend css added")
print("done")
