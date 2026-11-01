"""AI Agent 提示词（BUILD_SPEC §6）。

包含：
- ``SYSTEM_PROMPT``：印制成本测算 Agent 的角色、取证要求、数据不足禁用规则、
  最终 JSON 契约；
- ``build_user_prompt``：把任务参数、关联资料、规则摘要等组装成用户消息；
- 护栏重提示文案（完成判定 / JSON 格式 / 不得虚构总价）。

提示词全部为中文，不要求模型输出内部推理（页面只展示业务结果与执行记录）。
"""

from __future__ import annotations

import json
from typing import Any


SYSTEM_PROMPT = """你是“印制参数校验与成本测算”业务系统中的 AI Agent（印制成本测算 Agent）。

你的职责：根据业务任务参数，检索资料库中的纸张/工艺/装订/费率报价与规范规则，
先校验印制参数是否合规，再计算成本，最后形成可追溯、可复核的中文测算说明。

【硬性工作规则】
1. 必须先调用工具取证，再给结论。可用工具：
   - search_quotes：检索纸张/工艺/装订/费率报价等资料（retrieval）
   - get_rules：读取业务规则（retrieval）
   - validate_spec：校验印制参数是否合规（validation）
   - calculate_cost：按资料单价计算成本（calculation）
2. 工具选择、调用顺序和调用参数由你根据任务自行决定，不要假定固定顺序，也不要跳过工具直接给结论。
3. 所有单价、规则、金额必须来自本次工具的实际返回；禁止编造、猜测或使用记忆中的价格与规则。
4. 工具返回 ok=false 时，阅读 error_code、error_message 与缺失项；可以调整参数或更换关键词重试，
   但不要重复完全相同的工具+参数。确实无法取得数据时，必须在最终结果中明确列出缺失项。
5. 数据不足时（missing_data 非空、工具返回 DATA_NOT_FOUND、未取得必需单价），
   structured.total 与 structured.per_copy 必须为 null，不得给出总价或单册成本；
   正文只说明缺少哪些资料/参数、需要补充什么。数据不足仍编造总价属于严重错误。
6. basis_refs 必须引用本次工具返回的 source.ids 中的 material_no 或 rule_code，
   以及任务关联资料；禁止引用不存在、未返回或未核对的数据 ID。
7. 最终只输出一个 JSON 对象：不要输出 Markdown 代码围栏（```），不要输出解释性前后缀，
   不要输出你的内部推理过程。JSON 中的中文内容使用中文。
8. **工具调用要精简**（系统有调用次数上限，超限会被终止）：
   - 建议路线：get_rules → search_quotes(category=paper) → search_quotes(category=rate) →
     search_quotes(category=binding) →（任务有工艺时）search_quotes(category=process) →
     validate_spec → calculate_cost。
   - 每个类别检索一次即可；检索结果已够用时立即进入 validate_spec 与 calculate_cost，不要重复检索。
   - 费率资料用 category=rate（印刷/制版费率），纸张用 category=paper，装订用 category=binding，工艺用 category=process。
   - calculate_cost 只需任务参数（format/print_sheet/qty/paper_type/binding/color_pages/process），
     无需传入检索到的 material_no；其返回的 line_items/total/per_copy 与 source.ids 可直接用于依据。

【最终 JSON 契约】
{
  "content_md": "中文 Markdown 成本测算说明（含结论、口径、依据与风险提示）",
  "structured": {
    "line_items": [{"name": "费用项", "formula": "可人工复算的公式文本", "amount": 0.00}],
    "total": 0.00,
    "per_copy": 0.00,
    "missing": [],
    "uncertain": []
  },
  "basis_refs": [
    {"type": "material", "id": "实际 material_no", "label": "资料名称/用途"},
    {"type": "rule", "id": "实际 rule_code", "label": "规则名称"}
  ],
  "uncertain_items": ["需要人工确认的不确定项"],
  "missing_data": ["缺少的资料或参数"]
}

说明：
- 成本计算以 calculate_cost 的 line_items / total / per_copy 为准，不要自行改写金额。
- 若 calculate_cost 返回 ok=false，则 total/per_copy 置 null，并在 missing_data 中列出缺失项。
- formula 保留工具返回的可复算公式，便于人工核对。
- 没有缺失或不确定项时使用空数组 []，不要写“无”等占位文本。
"""


JSON_ONLY_REMINDER = (
    "你的上一条输出不是合法 JSON。请只输出一个 JSON 对象（不要 Markdown 代码围栏、"
    "不要解释文字），字段包括 content_md、structured、basis_refs、uncertain_items、missing_data。"
)

TOOL_EVIDENCE_REMINDER = (
    "当前还不满足取证要求：至少需要成功调用两类工具（检索/读取类 retrieval 与 "
    "校验/计算类 validation 或 calculation，各至少 1 次成功），且最终 basis_refs 非空。"
    "请先调用工具获取真实数据（例如先 search_quotes 或 get_rules 取得依据，"
    "再 validate_spec / calculate_cost），然后重新输出 JSON。"
)

NO_FABRICATION_REMINDER = (
    "当前数据不足（工具返回 DATA_NOT_FOUND 或 missing_data 非空），但你仍给出了总价/单册成本。"
    "请重写最终 JSON：structured.total 与 structured.per_copy 必须为 null，"
    "在 missing_data 中逐项列出缺少的资料或参数，正文不得出现推测的具体金额。"
)

DUPLICATE_TOOL_REMINDER = (
    "该工具与参数已经调用过，结果已在上下文里；请更换参数/关键词，或基于已有结果给出最终 JSON。"
)


def _fmt(value: Any, default: str = "（未提供）") -> str:
    """把可能为 None 的展示字段转成字符串。"""
    if value is None or value == "":
        return default
    return str(value)


def build_user_prompt(snapshot: dict[str, Any] | None) -> str:
    """根据 run 快照构建 user 消息。

    ``snapshot`` 结构由 ``orchestrator._build_snapshot`` 生成：
    ``business_task`` / ``params`` / ``linked_materials`` / ``rules`` / ``note`` / ``limits``。
    """
    snapshot = snapshot or {}
    task = snapshot.get("business_task") or {}
    params = snapshot.get("params") or {}
    materials = snapshot.get("linked_materials") or []
    rules = snapshot.get("rules") or []
    note = snapshot.get("note")
    limits = snapshot.get("limits") or {}

    lines: list[str] = []
    lines.append("【业务任务】")
    lines.append(f"任务编号：{_fmt(task.get('task_no'))}")
    lines.append(f"任务标题：{_fmt(task.get('title'))}")
    lines.append(f"业务类型：{_fmt(task.get('business_type'), 'print_cost')}")
    lines.append(f"任务目标：{_fmt(task.get('goal'))}")
    lines.append("")

    lines.append("【任务参数 JSON】")
    lines.append(json.dumps(params, ensure_ascii=False, indent=2))
    lines.append("")

    lines.append("【任务已关联资料】")
    if materials:
        for m in materials:
            lines.append(
                json.dumps(
                    {
                        "material_no": m.get("material_no"),
                        "type": m.get("type"),
                        "title": m.get("title"),
                        "source": m.get("source"),
                        "version_no": m.get("version_no"),
                        "content": m.get("content"),
                    },
                    ensure_ascii=False,
                )
            )
    else:
        lines.append("（无关联资料，请使用 search_quotes 检索资料库）")
    lines.append("")

    lines.append("【业务规则摘要】")
    if rules:
        for r in rules:
            lines.append(
                json.dumps(
                    {
                        "rule_code": r.get("rule_code"),
                        "name": r.get("name"),
                        "rule_type": r.get("rule_type"),
                        "severity": r.get("severity"),
                        "content": r.get("content"),
                    },
                    ensure_ascii=False,
                )
            )
    else:
        lines.append("（未读取到规则，请使用 get_rules 查询）")
    lines.append("")

    if note:
        lines.append("【本次运行备注】")
        lines.append(str(note))
        lines.append("")

    if limits:
        lines.append("【本次运行限额】")
        lines.append(
            "工具调用总次数≤{max_tool_calls}；模型轮次≤{max_llm_rounds}；"
            "单次工具超时 {tool_timeout_ms}ms；单次模型超时 {llm_timeout_s}s；"
            "总时长≤{ai_run_timeout_s}s。超限会被系统终止。".format(
                max_tool_calls=limits.get("max_tool_calls", "?"),
                max_llm_rounds=limits.get("max_llm_rounds", "?"),
                tool_timeout_ms=limits.get("tool_timeout_ms", "?"),
                llm_timeout_s=limits.get("llm_timeout_s", "?"),
                ai_run_timeout_s=limits.get("ai_run_timeout_s", "?"),
            )
        )
        lines.append("")

    lines.append(
        "请先调用工具取得真实报价/规则并完成校验、计算，再输出最终 JSON。"
        "不得编造单价；数据不足时明确列出缺失项，total/per_copy 置 null。"
    )
    return "\n".join(lines)
