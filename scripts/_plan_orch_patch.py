# -*- coding: utf-8 -*-
"""步骤 2/4：orchestrator + prompts 接入方案模式（不影响 print_cost）。"""
from pathlib import Path

ROOT = Path(r"F:\deepseek workarea\出版AI系统")
ORCH = ROOT / "app" / "agent" / "orchestrator.py"
PROMPTS = ROOT / "app" / "agent" / "prompts.py"


def replace(path: Path, old: str, new: str, label: str, count: int = 1):
    text = path.read_text(encoding="utf-8")
    if new in text:
        print("skip:", label)
        return
    if old not in text:
        raise SystemExit("anchor missing: " + label)
    text = text.replace(old, new, count)
    path.write_text(text, encoding="utf-8")
    print("patched:", label)


# 1) state 增加 plan 字段
replace(
    ORCH,
    "    calculation_ok: bool = False\n    last_calc: dict[str, Any] | None = None",
    "    calculation_ok: bool = False\n    last_calc: dict[str, Any] | None = None\n    plan_mode: bool = False\n    last_plan: dict[str, Any] | None = None",
    "state plan fields",
)

# 2) run_ai_task 设置 plan_mode
replace(
    ORCH,
    """        state = _AgentState(
            messages=messages,""",
    """        plan_mode = str(getattr(task, "business_type", "") or "").strip() == "print_cost_plan"
        state = _AgentState(
            plan_mode=plan_mode,
            messages=messages,""",
    "run_ai_task plan_mode",
)

# 3) agent loop 工具过滤 + 方案依据兜底
replace(
    ORCH,
    """        response = state.client.chat(
            state.messages,
            tools=get_tool_schemas(),
            tool_choice="auto",
        )""",
    """        response = state.client.chat(
            state.messages,
            tools=get_tool_schemas(plan_mode=state.plan_mode),
            tool_choice="auto",
        )""",
    "agent loop tools",
)

replace(
    ORCH,
    """        # ---- 完成判定护栏：≥2 类工具各成功 ≥1，且 basis_refs 非空 ----
        basis_refs = _normalize_basis_refs(final_obj.get("basis_refs"))""",
    """        # ---- 完成判定护栏：≥2 类工具各成功 ≥1，且 basis_refs 非空 ----
        basis_refs = _normalize_basis_refs(final_obj.get("basis_refs"))
        # 方案模式：若模型漏写依据，用 plan_candidates 本次真实 source_ids 兜底，避免把有效方案判失败
        if not basis_refs and state.plan_mode and state.last_plan and state.last_plan.get("ok"):
            plan_ids = (state.last_plan.get("source") or {}).get("ids") or []
            for sid in plan_ids:
                sid = str(sid or "").strip()
                if not sid:
                    continue
                ref_type = "rule" if sid.startswith("RULE-") else "material"
                basis_refs.append({"type": ref_type, "id": sid, "label": sid})""",
    "plan basis fallback",
)

# 4) execute tool：禁止普通模式调用 plan；记录 last_plan
replace(
    ORCH,
    """    # 1) 未知工具
    if name not in TOOL_HANDLERS:
        return finish(_tool_failure("TOOL_NOT_FOUND", f"未知工具：{name or '（空）'}"))""",
    """    # 1) 未知工具（方案工具只在方案模式可用）
    if name not in TOOL_HANDLERS:
        return finish(_tool_failure("TOOL_NOT_FOUND", f"未知工具：{name or '（空）'}"))
    if name == "plan_candidates" and not state.plan_mode:
        return finish(_tool_failure("TOOL_NOT_ALLOWED", "方案工具仅在方案模式（print_cost_plan）中可用"))""",
    "execute plan guard",
)

replace(
    ORCH,
    """        if name == "calculate_cost":
            state.last_calc = result
        return _compact_tool_result(result)""",
    """        if name == "calculate_cost":
            state.last_calc = result
        if name == "plan_candidates" and result.get("ok"):
            state.last_plan = result
        return _compact_tool_result(result)""",
    "execute last_plan",
)

# 5) _save_result 注入方案数据 + 结果内容兜底
replace(
    ORCH,
    """    structured = _normalize_structured(final_obj)

    uncertain = _as_list(final_obj.get("uncertain_items"))""",
    """    structured = _normalize_structured(final_obj)

    # 方案模式：以 plan_candidates 的真实计算结果为准，覆盖模型自由发挥的金额/明细
    plan_mode = str((snapshot.get("business_task") or {}).get("business_type") or "") == "print_cost_plan"
    plan_data = (state.last_plan or {}).get("data") if (state.plan_mode and state.last_plan and state.last_plan.get("ok")) else None
    plans = (plan_data or {}).get("plans") if isinstance(plan_data, dict) else None
    if plan_mode and isinstance(plans, list) and plans:
        first = plans[0]
        structured["plans"] = plans
        structured["plan_mode"] = True
        structured["budget_total"] = (plan_data or {}).get("budget_total")
        structured["budget_feasible"] = bool((plan_data or {}).get("feasible", True))
        structured["line_items"] = first.get("line_items") or []
        structured["total"] = first.get("total")
        structured["per_copy"] = first.get("per_copy")
        structured["missing"] = []

    uncertain = _as_list(final_obj.get("uncertain_items"))""",
    "save plan structured",
)

# 6) 方案模式 fallback 正文
replace(
    ORCH,
    """def _fallback_content(structured: dict[str, Any], missing: list[str]) -> str:
    if structured.get("total") is not None:""",
    """def _fallback_content(structured: dict[str, Any], missing: list[str]) -> str:
    plans = structured.get("plans") or []
    if plans:
        lines = ["## 方案模式：候选方案", ""]
        budget = structured.get("budget_total")
        if budget is not None:
            lines.append(f"- 预算：{float(budget):.2f} 元；预算内可行：{'是' if structured.get('budget_feasible', True) else '否'}")
        lines.append("")
        lines.append("| 方案 | 纸张 | 装订 | 工艺 | 总成本（元） | 单册（元） | 是否在预算内 |")
        lines.append("|---|---|---|---|---|---|---|")
        for plan in plans:
            lines.append(
                "| {no} | {paper} | {binding} | {process} | {total:.2f} | {per:.2f} | {within} |".format(
                    no=plan.get("plan_no", "—"),
                    paper=plan.get("paper_type", "—"),
                    binding=plan.get("binding", "—"),
                    process=plan.get("process", "无"),
                    total=float(plan.get("total") or 0),
                    per=float(plan.get("per_copy") or 0),
                    within="是" if plan.get("within_budget", True) else "否",
                )
            )
        return "\\n".join(lines)
    if structured.get("total") is not None:""",
    "plan fallback content",
)

# 7) 提示词：方案模式说明
replace(
    PROMPTS,
    """    lines.append("【任务参数 JSON】")""",
    """    if str(task.get("business_type") or "") == "print_cost_plan":
        lines.append("【方案模式要求】")
        lines.append(
            "- 本任务允许部分参数留白：format/print_sheet/qty 已确定；paper_type/binding/process 可为空，表示由系统从资料库推荐组合。"
        )
        lines.append(
            "- 必须先调用 get_rules 读取规则；然后调用 plan_candidates（retrieval/calculation 两类工具）。"
            "plan_candidates 的 params 传已知参数（含 budget_total），budget_total 可传任务预算。"
        )
        lines.append(
            "- plan_candidates 会从资料库枚举纸张/装订/工艺组合，逐一校验并真实计算，返回 plans 数组；"
            "不得自行编造组合或价格，也不得假设未返回的组合可行。"
        )
        lines.append(
            "- 最终 JSON 的 content_md 写方案对比与推荐理由；basis_refs 至少引用 plan_candidates 返回的 source_ids（material_no）；"
            "structured 中的金额以 plan_candidates 返回为准，系统会保存 plans 供页面展示。"
        )
        lines.append(
            "- 若预算内无可行方案，必须明确说明“预算内无合规方案”、最接近方案及差额，不得声称满足预算。"
        )
        lines.append("")
    lines.append("【任务参数 JSON】")""",
    "prompt plan instructions",
)

print("orchestrator/prompts done")
