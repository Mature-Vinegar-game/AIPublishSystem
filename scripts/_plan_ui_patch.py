# -*- coding: utf-8 -*-
"""步骤 3/4：任务详情页展示方案表 + 采用方案按钮/JS。"""
from pathlib import Path

ROOT = Path(r"F:\deepseek workarea\出版AI系统")
TPL = ROOT / "app" / "templates" / "task_detail.html"
JS = ROOT / "app" / "static" / "app.js"


def replace(path: Path, old: str, new: str, label: str):
    text = path.read_text(encoding="utf-8")
    if new in text:
        print("skip:", label)
        return
    if old not in text:
        raise SystemExit("anchor missing: " + label)
    path.write_text(text.replace(old, new, 1), encoding="utf-8")
    print("patched:", label)


t = TPL.read_text(encoding="utf-8")
if "{% set plans = structured.plans" not in t:
    anchor = "{% set line_items = structured.line_items|default([], true) or [] %}"
    t = t.replace(anchor, anchor + "\n{% set plans = structured.plans|default([], true) or [] %}", 1)
    TPL.write_text(t, encoding="utf-8")
    print("plans var added")

# 业务类型标识
replace(
    TPL,
    """<div class="meta-item"><span class="meta-label">业务类型</span><span class="meta-value">{{ t.business_type|default('print_cost') }}</span></div>""",
    """<div class="meta-item"><span class="meta-label">业务类型</span><span class="meta-value">{{ t.business_type|default('print_cost') }}{% if t.business_type == 'print_cost_plan' %} <span class="badge badge-blue">方案模式</span>{% endif %}</span></div>""",
    "business type badge",
)

# 隐藏标准采用按钮（方案模式改用单个方案按钮）
replace(
    TPL,
    """{% if allow_edit %}<button class="btn btn-ghost btn-sm" type="button" id="btn-adopt-ai"{% if not result %} hidden{% endif %}>采用此结果</button>{% endif %}""",
    """{% if allow_edit and not plans %}<button class="btn btn-ghost btn-sm" type="button" id="btn-adopt-ai"{% if not result %} hidden{% endif %}>采用此结果</button>{% endif %}""",
    "hide standard adopt in plan mode",
)

# 方案表（放在成本明细之前）
plan_block = '''{% if t.business_type == 'print_cost_plan' %}
        <div id="result-plans-wrap"{% if not plans %} hidden{% endif %}>
          <h3 class="sub-title">候选方案 <span class="count-pill" id="result-plans-count">{{ plans|length }}</span>
            <span class="muted small" id="result-plan-budget">{% if structured.budget_total is not none %}预算 {{ "%.2f"|format(structured.budget_total) }} 元{% endif %}</span>
          </h3>
          <div class="table-wrap">
            <table class="data-table compact">
              <thead>
                <tr>
                  <th class="nowrap">#</th><th>纸张</th><th>装订</th><th>工艺</th>
                  <th class="text-right nowrap">总成本（元）</th><th class="text-right nowrap">单册（元）</th>
                  <th class="nowrap">预算</th>{% if allow_edit %}<th>操作</th>{% endif %}
                </tr>
              </thead>
              <tbody id="result-plans-body">
                {% for plan in plans %}
                <tr>
                  <td class="mono">{{ plan.plan_no|default(loop.index) }}</td>
                  <td>{{ plan.paper_type|default('—') }}</td>
                  <td>{{ plan.binding|default('—') }}</td>
                  <td>{{ plan.process|default('无') }}</td>
                  <td class="text-right mono">{% if plan.total is number %}{{ "%.2f"|format(plan.total) }}{% else %}—{% endif %}</td>
                  <td class="text-right mono">{% if plan.per_copy is number %}{{ "%.2f"|format(plan.per_copy) }}{% else %}—{% endif %}</td>
                  <td>{% if plan.within_budget is sameas false %}<span class="badge badge-amber">超预算</span>{% else %}<span class="badge badge-green">预算内</span>{% endif %}</td>
                  {% if allow_edit %}
                  <td><button class="btn btn-primary btn-sm" type="button" data-adopt-plan="{{ loop.index0 }}" data-plan-ai-id="{{ result.ai_task_id|default(ai_id) }}">采用该方案</button></td>
                  {% endif %}
                </tr>
                {% endfor %}
              </tbody>
            </table>
          </div>
          <p class="muted small" id="result-plans-note">方案金额由 plan_candidates 从资料库真实计算；采用后生成版本，可继续提交审核。</p>
        </div>
        {% endif %}

        <h3 class="sub-title">成本明细</h3>'''
if "result-plans-wrap" not in t:
    anchor = '        <h3 class="sub-title">成本明细</h3>'
    t = t.replace(anchor, plan_block, 1)
    TPL.write_text(t, encoding="utf-8")
    print("plan table added")

# ---------------- JS ----------------
js = JS.read_text(encoding="utf-8")
if "function renderPlans(" not in js:
    anchor = "    function renderResult(rawData) {"
    helper = '''    function renderPlans(plans, structured) {
      var wrap = el('result-plans-wrap');
      var body = el('result-plans-body');
      if (!wrap || !body) { return; }
      while (body.firstChild) { body.removeChild(body.firstChild); }
      if (!Array.isArray(plans) || !plans.length) {
        wrap.hidden = true;
        return;
      }
      var budget = structured && structured.budget_total !== undefined && structured.budget_total !== null
        ? structured.budget_total : null;
      var budgetNode = el('result-plan-budget');
      if (budgetNode) {
        budgetNode.textContent = budget !== null ? ('预算 ' + formatMoney(budget) + ' 元') : '';
      }
      setText('result-plans-count', plans.length);
      plans.forEach(function (plan, index) {
        var tr = document.createElement('tr');
        function cell(text, cls) {
          var td = document.createElement('td');
          if (cls) { td.className = cls; }
          td.textContent = text === null || text === undefined || text === '' ? '—' : str(text);
          tr.appendChild(td);
        }
        cell(plan.plan_no || (index + 1), 'mono');
        cell(plan.paper_type);
        cell(plan.binding);
        cell(plan.process || '无');
        cell(formatMoney(plan.total), 'text-right mono');
        cell(formatMoney(plan.per_copy), 'text-right mono');
        var budgetTd = document.createElement('td');
        var badge = document.createElement('span');
        var within = plan.within_budget !== false;
        badge.className = 'badge ' + (within ? 'badge-green' : 'badge-amber');
        badge.textContent = within ? '预算内' : '超预算';
        budgetTd.appendChild(badge);
        tr.appendChild(budgetTd);
        if (canEdit) {
          var opTd = document.createElement('td');
          var btn = document.createElement('button');
          btn.type = 'button';
          btn.className = 'btn btn-primary btn-sm';
          btn.setAttribute('data-adopt-plan', String(index));
          btn.setAttribute('data-plan-ai-id', lastResultAiTaskId || currentAiId || '');
          btn.textContent = '采用该方案';
          opTd.appendChild(btn);
          tr.appendChild(opTd);
        }
        body.appendChild(tr);
      });
      wrap.hidden = false;
    }

    function renderResult(rawData) {'''
    js = js.replace(anchor, helper, 1)
    js = js.replace(
        "      var lineItems = Array.isArray(structured.line_items) ? structured.line_items : [];",
        "      var lineItems = Array.isArray(structured.line_items) ? structured.line_items : [];\n      renderPlans(structured.plans, structured);",
        1,
    )
    js = js.replace(
        "      var adopt = el('btn-adopt-ai');\n      if (adopt && canEdit) { adopt.hidden = false; }",
        "      var adopt = el('btn-adopt-ai');\n      if (adopt && canEdit && (!Array.isArray(structured.plans) || !structured.plans.length)) { adopt.hidden = false; }",
        1,
    )
    # 采用单个方案：事件委托
    if "data-adopt-plan" not in js:
        anchor2 = "      var adopt = el('btn-adopt-ai');\n      if (adopt) {"
        listener = '''      document.addEventListener('click', async function (event) {
        var origin = event.target;
        if (!origin || typeof origin.closest !== 'function') { return; }
        var btn = origin.closest('[data-adopt-plan]');
        if (!btn) { return; }
        var index = Number(btn.getAttribute('data-adopt-plan'));
        var aiId = str(btn.getAttribute('data-plan-ai-id')) || lastResultAiTaskId || currentAiId;
        if (!aiId || !isFinite(index) || index < 0) { toast('方案数据未就绪，请刷新结果后重试', 'warn'); return; }
        btn.disabled = true;
        try {
          await apiFetch('/api/business-tasks/' + encodeURIComponent(taskId) + '/results', {
            method: 'POST',
            body: { ai_task_id: Number(aiId), plan_index: index, change_note: '采用方案 ' + (index + 1) }
          });
          flash('已采用方案，版本已生成', 'success');
          window.setTimeout(function () { location.reload(); }, 350);
        } catch (e) {
          toast('采用方案失败：' + e.message, 'error', 7000);
          btn.disabled = false;
        }
      });

      var adopt = el('btn-adopt-ai');
      if (adopt) {'''
        if anchor2 not in js:
            raise SystemExit("js adopt anchor missing")
        js = js.replace(anchor2, listener, 1)
    JS.write_text(js, encoding="utf-8")
    print("app.js plan rendering added")
else:
    print("app.js plan already")
print("done")
