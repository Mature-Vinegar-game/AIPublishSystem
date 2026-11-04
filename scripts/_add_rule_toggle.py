# -*- coding: utf-8 -*-
"""规则库支持启用/停用（operator）：schema + API PATCH + 页面按钮 + JS。"""
from pathlib import Path

ROOT = Path(r"F:\deepseek workarea\出版AI系统")


def patch(path: Path, old: str, new: str, label: str):
    text = path.read_text(encoding="utf-8")
    if new in text:
        print("skip (already):", label)
        return
    if old not in text:
        raise SystemExit("anchor not found: " + label)
    path.write_text(text.replace(old, new, 1), encoding="utf-8")
    print("patched:", label)


# 1) schema
patch(
    ROOT / "app" / "schemas.py",
    """class RuleCreateRequest(_Base):
    rule_code: str = Field(..., min_length=1, max_length=64)""",
    """class RuleUpdateRequest(_Base):
    \"\"\"规则更新：启用/停用、名称、来源、严重级别、内容（operator）。\"\"\"

    enabled: Optional[bool] = Field(default=None, description="true=启用，false=停用")
    name: Optional[str] = Field(default=None, min_length=1, max_length=200)
    source: Optional[str] = Field(default=None, max_length=200)
    severity: Optional[str] = Field(default=None, max_length=16)
    content: Optional[Any] = None


class RuleCreateRequest(_Base):
    rule_code: str = Field(..., min_length=1, max_length=64)""",
    "schemas.RuleUpdateRequest",
)

patch(
    ROOT / "app" / "schemas.py",
    '    "RuleCreateRequest",',
    '    "RuleCreateRequest",\n    "RuleUpdateRequest",',
    "schemas.__all__",
)

# 2) API
patch(
    ROOT / "app" / "api" / "rules.py",
    "from app.schemas import RuleCreateRequest",
    "from app.schemas import RuleCreateRequest, RuleUpdateRequest",
    "rules import",
)

text = (ROOT / "app" / "api" / "rules.py").read_text(encoding="utf-8")
if '@router.patch("/{rule_id}"' not in text:
    text += '''


@router.patch("/{rule_id}", summary="更新规则：启用/停用/名称/来源/严重级别/内容（operator）")
def update_rule(
    rule_id: int,
    payload: RuleUpdateRequest,
    user: dict = Depends(require_operator),
    db: Session = Depends(get_db),
):
    """停用规则后，后续 AI 校验不再应用该规则；历史结果与执行记录仍保留原规则 ID（可追溯）。"""
    rule = db.query(Rule).filter(Rule.id == int(rule_id)).first()
    if rule is None:
        api_error(404, "规则不存在")
    if payload.enabled is not None:
        rule.enabled = 1 if payload.enabled else 0
    if payload.name is not None:
        rule.name = payload.name.strip()
    if payload.source is not None:
        rule.source = payload.source.strip() or rule.source
    if payload.severity is not None:
        severity = payload.severity.strip().lower()
        if severity not in SEVERITIES:
            api_error(400, f"severity 非法，只能是 {', '.join(SEVERITIES)}")
        rule.severity = severity
    if payload.content is not None:
        content = payload.content
        if isinstance(content, str):
            parsed = basis_service.parse_json(content, None)
            if not isinstance(parsed, (dict, list)):
                api_error(400, "content 必须是 JSON 对象")
            content = parsed
        if not isinstance(content, (dict, list)):
            api_error(400, "content 必须是 JSON 对象")
        rule.content_json = basis_service.dump_json(content, "{}")
    db.commit()
    db.refresh(rule)
    logger.info("更新规则 rule_id=%s rule_code=%s enabled=%s user=%s",
                rule.id, rule.rule_code, rule.enabled, user.get("username"))
    return success(_rule_to_dict(rule))
'''
    (ROOT / "app" / "api" / "rules.py").write_text(text, encoding="utf-8")
    print("patched: rules PATCH endpoint")
else:
    print("skip (already): rules PATCH endpoint")

# 3) 页面按钮
patch(
    ROOT / "app" / "templates" / "rules.html",
    """              {% else %}
              <span class="badge badge-gray">停用</span>
              {% endif %}
            </td>""",
    """              {% else %}
              <span class="badge badge-gray">停用</span>
              {% endif %}
              {% if is_operator %}
              <button class="btn btn-ghost btn-sm" type="button"
                      data-toggle-rule="{{ r.id }}"
                      data-rule-enabled="{{ '1' if en|string in ['1', 'true', 'True', 'enabled'] else '0' }}"
                      title="停用后该规则不再参与后续 AI 校验；历史结果仍保留规则 ID">
                {{ '停用' if en|string in ['1', 'true', 'True', 'enabled'] else '启用' }}
              </button>
              {% endif %}
            </td>""",
    "rules.html toggle button",
)

# 4) JS
app_js = ROOT / "app" / "static" / "app.js"
js = app_js.read_text(encoding="utf-8")
if "initRuleToggle" not in js:
    anchor = "  function initMaterialToggle() {"
    insert = '''  function initRuleToggle() {
    var buttons = document.querySelectorAll('[data-toggle-rule]');
    Array.prototype.forEach.call(buttons, function (btn) {
      btn.addEventListener('click', async function () {
        var id = btn.getAttribute('data-toggle-rule');
        var enabled = btn.getAttribute('data-rule-enabled') === '1';
        var next = !enabled;
        if (!next && !window.confirm('停用后该规则不再参与后续 AI 校验（历史结果与执行记录仍保留规则 ID）。确认停用？')) {
          return;
        }
        btn.disabled = true;
        try {
          await apiFetch('/api/rules/' + encodeURIComponent(id), { method: 'PATCH', body: { enabled: next } });
          toast('规则已' + (next ? '启用' : '停用'), 'success');
          window.setTimeout(function () { location.reload(); }, 600);
        } catch (e) {
          toast(e.message || '操作失败', 'error');
          btn.disabled = false;
        }
      });
    });
  }

  function initMaterialToggle() {'''
    js = js.replace(anchor, insert, 1)
    js = js.replace("    initMaterialToggle();", "    initMaterialToggle();\n    initRuleToggle();", 1)
    app_js.write_text(js, encoding="utf-8")
    print("patched: app.js initRuleToggle")
else:
    print("skip (already): app.js initRuleToggle")

print("done")
