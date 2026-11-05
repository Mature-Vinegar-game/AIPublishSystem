# -*- coding: utf-8 -*-
"""轻量增强：版本对比页面（选做功能，只读，不改主流程）。"""
from pathlib import Path

ROOT = Path(r"F:\deepseek workarea\出版AI系统")
MAIN = ROOT / "app" / "main.py"
TPL = ROOT / "app" / "templates" / "version_compare.html"
CSS = ROOT / "app" / "static" / "style.css"
DETAIL = ROOT / "app" / "templates" / "task_detail.html"

text = MAIN.read_text(encoding="utf-8")
if "@app.get(\"/tasks/{task_id}/versions/compare\"" not in text:
    anchor = '@app.get("/tasks/{task_id}", include_in_schema=False)'
    block = '''@app.get("/tasks/{task_id}/versions/compare", include_in_schema=False)
def version_compare_page(
    task_id: int,
    request: Request,
    left: Optional[int] = Query(default=None),
    right: Optional[int] = Query(default=None),
    db: Session = Depends(get_db),
):
    """版本对比：金额/明细/依据 + 正文差异（只读）。"""
    import difflib

    user = get_current_user(request)
    if user is None:
        return _login_redirect()
    try:
        task = task_service.get_visible_task(db, task_id, user)
    except StarletteHTTPException as exc:
        detail = exc.detail if isinstance(exc.detail, dict) else {}
        return _message_page("无法查看该任务", str(detail.get("message") or "任务不存在或不在你的权限范围内"), exc.status_code)

    versions = version_service.list_versions(db, task.id, newest_first=False)
    if len(versions) < 2:
        return _message_page("暂无可对比版本", "当前任务至少需要两个版本才能对比。", 400, f"/tasks/{task.id}")
    by_no = {int(v.get("version_no") or 0): v for v in versions}
    left_no = left if left in by_no else versions[0]["version_no"]
    right_no = right if right in by_no else versions[-1]["version_no"]
    lv = by_no[left_no]
    rv = by_no[right_no]

    left_struct = lv.get("structured") or {}
    right_struct = rv.get("structured") or {}
    left_items = {str(x.get("name")): x.get("amount") for x in (left_struct.get("line_items") or [])}
    right_items = {str(x.get("name")): x.get("amount") for x in (right_struct.get("line_items") or [])}
    names = list(dict.fromkeys(list(left_items.keys()) + list(right_items.keys())))
    item_rows = []
    for name in names:
        lval = left_items.get(name)
        rval = right_items.get(name)
        delta = None
        if isinstance(lval, (int, float)) and isinstance(rval, (int, float)):
            delta = round(float(rval) - float(lval), 2)
        item_rows.append({"name": name, "left": lval, "right": rval, "delta": delta})

    left_basis = {(str(b.get("type")), str(b.get("id"))) for b in (lv.get("basis_refs") or []) if isinstance(b, dict)}
    right_basis = {(str(b.get("type")), str(b.get("id"))) for b in (rv.get("basis_refs") or []) if isinstance(b, dict)}
    basis_added = sorted(right_basis - left_basis)
    basis_removed = sorted(left_basis - right_basis)

    diff_lines = []
    for line in difflib.unified_diff(
        (lv.get("content_md") or "").splitlines(),
        (rv.get("content_md") or "").splitlines(),
        fromfile="v" + str(left_no),
        tofile="v" + str(right_no),
        lineterm="",
    ):
        if line.startswith("+++") or line.startswith("---"):
            continue
        if line.startswith("@@"):
            kind = "meta"
        elif line.startswith("+"):
            kind = "add"
        elif line.startswith("-"):
            kind = "del"
        else:
            kind = "same"
        diff_lines.append({"kind": kind, "text": line})

    context = _page_context(
        user,
        "tasks",
        task_info=task_service.serialize_task(db, task),
        versions=versions,
        left=lv,
        right=rv,
        item_rows=item_rows,
        changed_amounts={
            "total": (left_struct.get("total"), right_struct.get("total")),
            "per_copy": (left_struct.get("per_copy"), right_struct.get("per_copy")),
        },
        basis_added=basis_added,
        basis_removed=basis_removed,
        diff_lines=diff_lines,
    )
    return _render(request, "version_compare.html", context)


@app.get("/tasks/{task_id}", include_in_schema=False)'''
    MAIN.write_text(text.replace(anchor, block, 1), encoding="utf-8")
    print("version compare route added")
else:
    print("version compare route exists")

if not TPL.exists():
    TPL.write_text('''{% extends "base.html" %}
{% block title %}版本对比 · 印制参数校验与成本测算 AI 系统{% endblock %}
{% block content %}
{% set lv = left %}{% set rv = right %}
<div class="page-head">
  <div>
    <h1 class="page-title">版本对比</h1>
    <p class="page-sub">任务 {{ task_info.task_no|default('—') }} · {{ task_info.title|default('—') }}</p>
  </div>
  <div class="page-actions">
    <a class="btn btn-ghost" href="/tasks/{{ task_info.id }}">← 返回任务</a>
  </div>
</div>

<form class="card compare-form" method="get" action="/tasks/{{ task_info.id }}/versions/compare">
  <div class="card-body form-row">
    <div class="field grow">
      <label for="left">左侧版本</label>
      <select id="left" name="left">
        {% for v in versions %}
        <option value="{{ v.version_no }}"{% if v.version_no == lv.version_no %} selected{% endif %}>v{{ v.version_no }}（{{ v.source_label|default(v.source) }}）</option>
        {% endfor %}
      </select>
    </div>
    <span class="compare-arrow">→</span>
    <div class="field grow">
      <label for="right">右侧版本</label>
      <select id="right" name="right">
        {% for v in versions %}
        <option value="{{ v.version_no }}"{% if v.version_no == rv.version_no %} selected{% endif %}>v{{ v.version_no }}（{{ v.source_label|default(v.source) }}）</option>
        {% endfor %}
      </select>
    </div>
    <button class="btn btn-primary" type="submit">对比</button>
  </div>
</form>

<div class="compare-grid">
  <section class="card">
    <div class="card-header"><h2 class="card-title">金额摘要</h2></div>
    <div class="card-body">
      <div class="compare-summary">
        <div class="compare-col"><span class="muted small">v{{ lv.version_no }}</span><strong>{{ "%.2f"|format(changed_amounts.total[0] or 0) }} 元</strong><span class="muted small">单册 {{ "%.2f"|format(changed_amounts.per_copy[0] or 0) }} 元</span></div>
        <div class="compare-arrow">→</div>
        <div class="compare-col"><span class="muted small">v{{ rv.version_no }}</span><strong>{{ "%.2f"|format(changed_amounts.total[1] or 0) }} 元</strong><span class="muted small">单册 {{ "%.2f"|format(changed_amounts.per_copy[1] or 0) }} 元</span></div>
      </div>
      <p class="muted small">版本来源：{{ lv.source_label|default(lv.source) }} / {{ rv.source_label|default(rv.source) }}；创建时间：{{ lv.created_at|default('—') }} / {{ rv.created_at|default('—') }}</p>
    </div>
  </section>

  <section class="card">
    <div class="card-header"><h2 class="card-title">依据变化</h2></div>
    <div class="card-body">
      {% if basis_added %}<p><b>右侧新增：</b>{% for item in basis_added %}<span class="badge badge-blue">{{ item[1] }}</span> {% endfor %}</p>{% endif %}
      {% if basis_removed %}<p><b>右侧移除：</b>{% for item in basis_removed %}<span class="badge badge-amber">{{ item[1] }}</span> {% endfor %}</p>{% endif %}
      {% if not basis_added and not basis_removed %}<p class="muted">两个版本的依据集合一致。</p>{% endif %}
    </div>
  </section>

  <section class="card card-wide">
    <div class="card-header"><h2 class="card-title">成本明细差异</h2></div>
    <div class="card-body table-wrap">
      <table class="data-table compact">
        <thead><tr><th>费用项</th><th class="text-right">v{{ lv.version_no }}（元）</th><th class="text-right">v{{ rv.version_no }}（元）</th><th class="text-right">差额</th></tr></thead>
        <tbody>
          {% for row in item_rows %}
          <tr>
            <td>{{ row.name }}</td>
            <td class="text-right mono">{{ "%.2f"|format(row.left or 0) }}</td>
            <td class="text-right mono">{{ "%.2f"|format(row.right or 0) }}</td>
            <td class="text-right mono {% if row.delta and row.delta > 0 %}diff-up{% elif row.delta and row.delta < 0 %}diff-down{% endif %}">{{ "%+.2f"|format(row.delta) if row.delta is not none else '—' }}</td>
          </tr>
          {% endfor %}
        </tbody>
      </table>
    </div>
  </section>

  <section class="card card-wide">
    <div class="card-header"><h2 class="card-title">正文差异（v{{ lv.version_no }} → v{{ rv.version_no }}）</h2></div>
    <div class="card-body">
      <pre class="diff-block">{% for line in diff_lines %}<span class="diff-{{ line.kind }}">{{ line.text }}</span>
{% endfor %}</pre>
      <p class="muted small">高亮仅为文本差异；金额请以上方明细/摘要为准。</p>
    </div>
  </section>
</div>
{% endblock %}
''', encoding="utf-8")
    print("version compare template written")

# detail link
detail = DETAIL.read_text(encoding="utf-8")
if "versions/compare" not in detail:
    old = '''      <h2 class="card-title"><span class="sec-num">⑥</span> 结果版本 <span class="count-pill">{{ vlist|length }}</span></h2>
      <span class="hint">版本只增不改；提交审核/通过后对应版本冻结</span>'''
    new = '''      <h2 class="card-title"><span class="sec-num">⑥</span> 结果版本 <span class="count-pill">{{ vlist|length }}</span></h2>
      <div class="card-actions">
        {% if vlist|length >= 2 %}<a class="btn btn-ghost btn-sm" href="/tasks/{{ task_id }}/versions/compare">版本对比</a>{% endif %}
        <span class="hint">版本只增不改；提交审核/通过后对应版本冻结</span>
      </div>'''
    if old in detail:
        detail = detail.replace(old, new, 1)
        DETAIL.write_text(detail, encoding="utf-8")
        print("version compare link added")
    else:
        print("detail link anchor missing")

# CSS
css = CSS.read_text(encoding="utf-8")
if ".compare-grid" not in css:
    css += """

/* ---- 版本对比 ---- */
.compare-form { margin-bottom: 14px; }
.compare-grid { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 16px; }
.compare-grid .card-wide { grid-column: 1 / -1; }
.compare-summary { display: flex; align-items: center; gap: 16px; }
.compare-col { display: flex; flex-direction: column; gap: 4px; }
.compare-col strong { font-size: 20px; color: var(--primary); font-family: var(--mono); }
.compare-arrow { color: var(--muted); font-size: 18px; }
.diff-block { margin: 0; padding: 10px 12px; background: #FBFCFE; border: 1px solid var(--line); border-radius: 8px; max-height: 460px; overflow: auto; font-family: var(--mono); font-size: 12px; line-height: 1.7; }
.diff-block .diff-add { display: block; background: #EAF7F0; color: #1F7A4D; }
.diff-block .diff-del { display: block; background: #FDECEA; color: #B23B36; }
.diff-block .diff-meta { display: block; color: #7A8699; }
.diff-block .diff-same { display: block; color: var(--ink-2); }
.diff-up { color: #B23B36; }
.diff-down { color: #1F7A4D; }
@media (max-width: 900px) { .compare-grid { grid-template-columns: 1fr; } }
"""
    CSS.write_text(css, encoding="utf-8")
    print("compare css added")
print("done")
