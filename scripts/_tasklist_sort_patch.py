# -*- coding: utf-8 -*-
"""任务列表：关键词高亮 + 排序（更新时间/创建时间/任务编号/最新总成本）。"""
from pathlib import Path

ROOT = Path(r"F:\deepseek workarea\出版AI系统")
SERVICE = ROOT / "app" / "services" / "task_service.py"
MAIN = ROOT / "app" / "main.py"
TPL = ROOT / "app" / "templates" / "tasks.html"
JS = ROOT / "app" / "static" / "app.js"
CSS = ROOT / "app" / "static" / "style.css"

text = SERVICE.read_text(encoding="utf-8")
if "def _latest_totals" not in text:
    anchor = "def list_tasks(\n"
    helper = '''def _latest_totals(db: Session, task_ids: list[int]) -> dict:
    """批量取任务最新 AI 结果的总成本，用于列表展示/金额排序。"""
    if not task_ids:
        return {}
    rows = (
        db.query(AiResult)
        .filter(AiResult.business_task_id.in_(list(task_ids)))
        .order_by(AiResult.business_task_id, AiResult.id.asc())
        .all()
    )
    totals: dict = {}
    for row in rows:
        structured = basis_service.parse_json(row.structured_json, {})
        if isinstance(structured, dict) and structured.get("total") is not None:
            totals[row.business_task_id] = structured.get("total")
    return totals


def list_tasks(
'''
    if anchor not in text:
        raise SystemExit("service anchor missing")
    text = text.replace(anchor, helper, 1)
    SERVICE.write_text(text, encoding="utf-8")
    print("task_service helper added")

text = SERVICE.read_text(encoding="utf-8")
old_sig = """def list_tasks(
    db: Session,
    user: dict,
    status: Optional[str] = None,
    keyword: Optional[str] = None,
    page: int = 1,
    page_size: int = 20,
) -> Tuple[List[dict], int, int, int]:"""
new_sig = """def list_tasks(
    db: Session,
    user: dict,
    status: Optional[str] = None,
    keyword: Optional[str] = None,
    page: int = 1,
    page_size: int = 20,
    sort: Optional[str] = None,
) -> Tuple[List[dict], int, int, int]:"""
if "sort: Optional[str] = None," not in text:
    if old_sig not in text:
        raise SystemExit("list_tasks signature anchor missing")
    text = text.replace(old_sig, new_sig, 1)
    SERVICE.write_text(text, encoding="utf-8")
    print("task_service signature updated")

old_rows = """    total = query.count()
    page = max(1, int(page or 1))
    page_size = max(1, min(100, int(page_size or 20)))
    rows = (
        query.order_by(BusinessTask.updated_at.desc(), BusinessTask.id.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
        .all()
    )
    return [serialize_task(db, row) for row in rows], total, page, page_size"""
new_rows = """    total = query.count()
    page = max(1, int(page or 1))
    page_size = max(1, min(100, int(page_size or 20)))
    sort = (sort or "updated_desc").strip()
    latest_map: dict = {}
    if sort == "amount_desc":
        # 任务量级在演示场景较小（几十到几百），按最新结果金额排序即可
        all_rows = query.all()
        latest_map = _latest_totals(db, [row.id for row in all_rows])
        all_rows.sort(key=lambda row: latest_map.get(row.id, -1), reverse=True)
        total = len(all_rows)
        rows = all_rows[(page - 1) * page_size : page * page_size]
    else:
        if sort == "created_desc":
            order = (BusinessTask.created_at.desc(), BusinessTask.id.desc())
        elif sort == "task_no_asc":
            order = (BusinessTask.task_no.asc(), BusinessTask.id.asc())
        elif sort == "updated_asc":
            order = (BusinessTask.updated_at.asc(), BusinessTask.id.asc())
        else:
            sort = "updated_desc"
            order = (BusinessTask.updated_at.desc(), BusinessTask.id.desc())
        rows = (
            query.order_by(*order)
            .offset((page - 1) * page_size)
            .limit(page_size)
            .all()
        )
        latest_map = _latest_totals(db, [row.id for row in rows])

    items = []
    for row in rows:
        item = serialize_task(db, row)
        item["latest_total"] = latest_map.get(row.id)
        items.append(item)
    return items, total, page, page_size"""
if old_rows not in text:
    raise SystemExit("list_tasks body anchor missing")
text = text.replace(old_rows, new_rows, 1)
SERVICE.write_text(text, encoding="utf-8")
print("task_service body updated")

# main.py route
m = MAIN.read_text(encoding="utf-8")
old_route = """    keyword: Optional[str] = Query(default=None),
    page: int = Query(default=1, ge=1),
    db: Session = Depends(get_db),
):
    user = get_current_user(request)
    if user is None:
        return _login_redirect()
    selected_status = status_filter or status or "ALL"
    items, total, page, _page_size = task_service.list_tasks(
        db, user, status=selected_status, keyword=keyword, page=page, page_size=20
    )"""
new_route = """    keyword: Optional[str] = Query(default=None),
    page: int = Query(default=1, ge=1),
    sort: Optional[str] = Query(default=None),
    db: Session = Depends(get_db),
):
    user = get_current_user(request)
    if user is None:
        return _login_redirect()
    selected_status = status_filter or status or "ALL"
    items, total, page, _page_size = task_service.list_tasks(
        db, user, status=selected_status, keyword=keyword, page=page, page_size=20, sort=sort
    )"""
if old_route not in m:
    raise SystemExit("main route anchor missing")
m = m.replace(old_route, new_route, 1)
m = m.replace('        keyword=keyword or "",\n    )', '        keyword=keyword or "",\n        sort=(sort or "updated_desc"),\n    )', 1)
MAIN.write_text(m, encoding="utf-8")
print("main route sort updated")

# template
t = TPL.read_text(encoding="utf-8")
t = t.replace("{% set kw = keyword|default('') %}", "{% set kw = keyword|default('') %}\n{% set sort = sort|default('updated_desc') %}", 1)
old_filter = """      <input class="filter-keyword" type="search" name="keyword" value="{{ kw }}"
             placeholder="任务编号 / 标题 / 目标" aria-label="关键词搜索">
      <button class="btn btn-ghost btn-sm" type="submit">搜索</button>"""
new_filter = """      <input class="filter-keyword" type="search" name="keyword" value="{{ kw }}"
             placeholder="任务编号 / 标题 / 目标" aria-label="关键词搜索">
      <label class="filter-label" for="sort-filter">排序</label>
      <select id="sort-filter" name="sort" data-auto-submit>
        <option value="updated_desc"{% if sort == 'updated_desc' %} selected{% endif %}>最近更新</option>
        <option value="created_desc"{% if sort == 'created_desc' %} selected{% endif %}>最近创建</option>
        <option value="amount_desc"{% if sort == 'amount_desc' %} selected{% endif %}>总成本从高到低</option>
        <option value="task_no_asc"{% if sort == 'task_no_asc' %} selected{% endif %}>任务编号升序</option>
      </select>
      <button class="btn btn-ghost btn-sm" type="submit">搜索</button>"""
if old_filter in t:
    t = t.replace(old_filter, new_filter, 1)
# quick chips and pager include sort
t = t.replace('<a class="chip{% if sf == value %} is-active{% endif %}" href="/tasks?status={{ value|urlencode }}{% if kw %}&keyword={{ kw|urlencode }}{% endif %}">',
              '<a class="chip{% if sf == value %} is-active{% endif %}" href="/tasks?status={{ value|urlencode }}{% if kw %}&keyword={{ kw|urlencode }}{% endif %}&sort={{ sort|urlencode }}">', 1)
t = t.replace('href="/tasks?status={{ sf|urlencode }}&keyword={{ kw|urlencode }}&page={{ cur_page - 1 }}"',
              'href="/tasks?status={{ sf|urlencode }}&keyword={{ kw|urlencode }}&sort={{ sort|urlencode }}&page={{ cur_page - 1 }}"', 1)
t = t.replace('href="/tasks?status={{ sf|urlencode }}&keyword={{ kw|urlencode }}&page={{ cur_page + 1 }}"',
              'href="/tasks?status={{ sf|urlencode }}&keyword={{ kw|urlencode }}&sort={{ sort|urlencode }}&page={{ cur_page + 1 }}"', 1)
# card marker for keyword highlight
t = t.replace('<div class="card">\n  <div class="card-header">\n    <h2 class="card-title">任务列表</h2>',
              '<div class="card task-list-card" data-highlight-keyword="{{ kw }}">\n  <div class="card-header">\n    <h2 class="card-title">任务列表</h2>', 1)
# add latest total column header/body
t = t.replace('<th class="nowrap text-center">版本数</th>', '<th class="nowrap text-center">版本数</th>\n          <th class="nowrap text-right">最新总成本</th>', 1)
t = t.replace('<td class="text-center">{{ t.versions_count|default(t.version_count|default(0)) }}</td>',
              '<td class="text-center">{{ t.versions_count|default(t.version_count|default(0)) }}</td>\n          <td class="text-right mono">{% if t.latest_total is not none %}{{ "%.2f"|format(t.latest_total) }}{% else %}<span class="muted">—</span>{% endif %}</td>', 1)
TPL.write_text(t, encoding="utf-8")
print("tasks template updated")

# JS highlight
js = JS.read_text(encoding="utf-8")
if "initTaskListHighlight" not in js:
    anchor = "  function initTaskDetail() {"
    fn = '''  function initTaskListHighlight() {
    var card = qs('.task-list-card[data-highlight-keyword]');
    if (!card) { return; }
    var keyword = str(card.getAttribute('data-highlight-keyword')).trim();
    if (!keyword) { return; }
    var needle = keyword.toLowerCase();
    var walker = document.createTreeWalker(card, NodeFilter.SHOW_TEXT, null, false);
    var nodes = [];
    while (walker.nextNode()) { nodes.push(walker.currentNode); }
    nodes.forEach(function (node) {
      var parent = node.parentNode;
      if (!parent || ['SCRIPT', 'STYLE', 'MARK'].indexOf(parent.tagName) !== -1) { return; }
      var text = node.nodeValue || '';
      var index = text.toLowerCase().indexOf(needle);
      if (index < 0) { return; }
      var frag = document.createDocumentFragment();
      var cursor = 0;
      while (index >= 0) {
        if (index > cursor) { frag.appendChild(document.createTextNode(text.slice(cursor, index))); }
        var mark = document.createElement('mark');
        mark.textContent = text.slice(index, index + keyword.length);
        frag.appendChild(mark);
        cursor = index + keyword.length;
        index = text.toLowerCase().indexOf(needle, cursor);
      }
      if (cursor < text.length) { frag.appendChild(document.createTextNode(text.slice(cursor))); }
      parent.replaceChild(frag, node);
    });
  }

  function initTaskDetail() {'''
    js = js.replace(anchor, fn, 1)
    js = js.replace("    initMaterialToggle();", "    initMaterialToggle();\n    initTaskListHighlight();", 1)
    JS.write_text(js, encoding="utf-8")
    print("task list highlight js added")

# CSS mark
css = CSS.read_text(encoding="utf-8")
if "mark {" not in css:
    css += """

/* ---- 任务列表关键词高亮 ---- */
mark { background: #FFF1B8; color: inherit; border-radius: 2px; padding: 0 1px; }
"""
    CSS.write_text(css, encoding="utf-8")
    print("mark css added")
print("done")
