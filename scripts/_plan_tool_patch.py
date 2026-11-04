# -*- coding: utf-8 -*-
"""步骤 1/4：在 tools.py 中新增 plan_candidates 工具（方案模式，枚举资料库组合）。"""
from pathlib import Path

ROOT = Path(r"F:\deepseek workarea\出版AI系统")
P = ROOT / "app" / "agent" / "tools.py"
text = P.read_text(encoding="utf-8")

TOOL_CODE = r'''

# ---------------------------------------------------------------------------
# 方案模式：从资料库枚举「纸张 × 装订 × 工艺」候选组合并真实计算
# ---------------------------------------------------------------------------

PLAN_TOOL_NAME = "plan_candidates"


def _plan_unit_prices(row: Any) -> dict[str, Any]:
    content = _content_of(row) if row is not None else {}
    return content


def plan_candidates(
    params: dict[str, Any] | None = None,
    budget_total: Any = None,
    max_plans: Any = 3,
) -> dict[str, Any]:
    """方案模式：按已确定参数枚举资料库中的纸张/装订/工艺组合，预算可选。

    - 只枚举**已启用**资料；单价/规则全部来自资料库与规则库；
    - 每个候选组合都真实执行 validate_spec + calculate_cost；
    - 组合数量有上限（shortlist），避免组合爆炸；
    - 若预算内无可行方案，返回最接近的一个方案并明确标记 feasible=false。
    """
    if params is not None and not isinstance(params, dict):
        return _fail("TOOL_ARGS_INVALID", "params 必须是 JSON 对象")
    base = _normalize_params(dict(params or {}))
    fmt = str(base.get("format") or "").strip()
    sheet = _to_number(base.get("print_sheet"))
    qty = _to_int(base.get("qty"))
    missing_base: list[str] = []
    if not fmt:
        missing_base.append("开本（format）")
    if sheet is None or sheet <= 0:
        missing_base.append("印张（print_sheet）")
    if qty is None or qty <= 0:
        missing_base.append("印数（qty）")
    if missing_base:
        return _fail(
            "TOOL_ARGS_INVALID",
            "方案模式至少需要确定：" + "、".join(missing_base),
            data={"missing": missing_base},
        )
    if fmt not in VALID_FORMATS:
        return _fail("TOOL_ARGS_INVALID", f"开本必须是 {'/'.join(VALID_FORMATS)} 之一")
    if sheet > 50:
        return _fail("TOOL_ARGS_INVALID", "印张必须不超过 50")
    if qty < 500:
        return _fail("TOOL_ARGS_INVALID", "印数必须不低于 500 册")

    raw_color = _to_int(base.get("color_pages"))
    color_pages = 4 if raw_color is None else int(raw_color)
    if color_pages < 0 or color_pages > 4:
        return _fail("TOOL_ARGS_INVALID", "色数档位必须在 0-4 之间")
    color_count = 4 if color_pages > 0 else 1

    budget = _to_number(budget_total if budget_total is not None else base.get("budget_total"))
    if budget is not None and budget <= 0:
        budget = None
    try:
        max_n = int(max_plans or 3)
    except (TypeError, ValueError):
        max_n = 3
    max_n = max(1, min(5, max_n))

    db = None
    try:
        db = _session()
        if db is None:
            return _fail("TOOL_EXEC_ERROR", "数据库未初始化")
        papers = _query_materials(db, ("paper",))
        bindings = _query_materials(db, ("binding",))
        processes = _query_materials(db, ("process",))
        rate_rows = _query_materials(db, ("rate",))
        rate_row, rate_price, rate_content = _pick_rate(rate_rows)
        if rate_row is None or rate_price is None:
            return _fail(
                "DATA_NOT_FOUND",
                "缺少印刷费率资料（materials.type=rate），无法生成方案",
                source_ids=[],
            )
        plate_price = _price_from(rate_content, PLATE_FEE_KEYS)
        finished_plate = plate_price if plate_price is not None else DEFAULT_PLATE_FEE_PER_COLOR

        # 纸张候选：固定纸张则只取该纸张；否则按 format_fit 过滤
        paper_rows: list[Any] = []
        fixed_paper = str(base.get("paper_type") or "").strip()
        if fixed_paper:
            paper = _find_paper(papers, fixed_paper)
            if paper is None:
                return _fail("DATA_NOT_FOUND", f"纸张“{fixed_paper}”不在资料库或未启用", missing=[f"纸张报价（{fixed_paper}）"])
            paper_rows = [paper]
        else:
            for paper in papers:
                content = _content_of(paper)
                fits = content.get("format_fit")
                if isinstance(fits, (list, tuple)) and fits and fmt not in fits:
                    continue
                if _price_from(content, PAPER_PRICE_KEYS) is None:
                    continue
                paper_rows.append(paper)

        fixed_binding = str(base.get("binding") or "").strip()
        if fixed_binding:
            binding = _select_process(bindings, fixed_binding)
            if binding is None:
                return _fail("DATA_NOT_FOUND", f"装订“{fixed_binding}”不在资料库或未启用", missing=[f"装订报价（{fixed_binding}）"])
            binding_rows = [binding]
        else:
            binding_rows = [b for b in bindings if _price_from(_content_of(b), BINDING_PRICE_KEYS) is not None]

        fixed_process = str(base.get("process") or "").strip()
        if fixed_process and fixed_process.lower() not in ("none", "null", "无"):
            process = _select_process(processes, fixed_process)
            if process is None:
                return _fail("DATA_NOT_FOUND", f"工艺“{fixed_process}”不在资料库或未启用", missing=[f"工艺报价（{fixed_process}）"])
            process_rows: list[Any | None] = [process]
        else:
            process_rows = [None] + [p for p in processes if _price_from(_content_of(p), PROCESS_PRICE_KEYS) is not None]

        if not paper_rows:
            return _fail("DATA_NOT_FOUND", f"资料库中没有适配“{fmt}”的纸张报价", data={"missing": ["纸张报价"]})
        if not binding_rows:
            return _fail("DATA_NOT_FOUND", "资料库中没有可用装订报价", data={"missing": ["装订报价"]})

        combos: list[dict[str, Any]] = []
        for paper in paper_rows:
            paper_content = _content_of(paper)
            paper_price = _price_from(paper_content, PAPER_PRICE_KEYS)
            if paper_price is None:
                continue
            reams = math.ceil(qty * sheet / 1000 * PAPER_WASTE_FACTOR)
            paper_amount = round(reams * paper_price, 2)
            for binding in binding_rows:
                b_price = _price_from(_content_of(binding), BINDING_PRICE_KEYS)
                if b_price is None:
                    continue
                binding_amount = round(qty / 1000 * b_price, 2)
                for process in process_rows:
                    process_price = 0.0
                    if process is not None:
                        process_price = _price_from(_content_of(process), PROCESS_PRICE_KEYS) or 0.0
                    process_amount = round(qty / 1000 * process_price, 2) if process is not None else 0.0
                    print_amount = round(qty / 1000 * sheet * rate_price * color_count, 2)
                    plate_amount = round(color_count * finished_plate, 2)
                    estimate = round(paper_amount + print_amount + plate_amount + binding_amount + process_amount, 2)
                    combos.append(
                        {
                            "estimate": estimate,
                            "paper": paper,
                            "binding": binding,
                            "process": process,
                            "source_ids": list(
                                dict.fromkeys(
                                    [
                                        getattr(paper, "material_no", None),
                                        getattr(rate_row, "material_no", None),
                                        getattr(binding, "material_no", None),
                                        getattr(process, "material_no", None) if process is not None else None,
                                    ]
                                )
                            ),
                        }
                    )
        combos.sort(key=lambda item: item["estimate"])
        # 组合爆炸保护：只对最接近预算/最低价的有限组合做完整校验与计算
        shortlist = combos[:24]

        valid_plans: list[dict[str, Any]] = []
        for combo in shortlist:
            p = dict(base)
            p["paper_type"] = combo["paper"].title
            p["binding"] = combo["binding"].title
            p["process"] = combo["process"].title if combo["process"] is not None else ""
            p["color_pages"] = color_pages
            val = validate_spec(p)
            if not val.get("ok") or not (val.get("data") or {}).get("valid"):
                continue
            calc = calculate_cost(p)
            if not calc.get("ok"):
                continue
            data = calc.get("data") or {}
            total = data.get("total")
            if total is None:
                continue
            plan = {
                "paper_no": getattr(combo["paper"], "material_no", ""),
                "paper_type": combo["paper"].title,
                "binding": combo["binding"].title,
                "process": combo["process"].title if combo["process"] is not None else "无",
                "process_name": combo["process"].title if combo["process"] is not None else "",
                "color_pages": color_pages,
                "total": total,
                "per_copy": data.get("per_copy"),
                "line_items": data.get("line_items") or [],
                "source_ids": [sid for sid in combo["source_ids"] if sid],
                "within_budget": True if budget is None else total <= budget,
                "budget_delta": None if budget is None else round(budget - total, 2),
            }
            plan["basis_refs"] = [
                {"type": "material", "id": sid, "label": sid}
                for sid in plan["source_ids"]
            ]
            valid_plans.append(plan)
            if len(valid_plans) >= 12:
                break

        if not valid_plans:
            return _fail(
                "NO_PLAN",
                "在当前资料库/规则库约束下没有可行的参数组合，请补充或调整开本、印张、印数或资料",
                data={"missing": ["可行方案（纸张×装订×工艺组合均未通过规则校验或缺少单价）"]},
            )

        warnings: list[str] = []
        pool = valid_plans
        feasible = True
        if budget is not None:
            within = [p for p in valid_plans if p["total"] <= budget]
            if within:
                pool = within
            else:
                feasible = False
                pool = [min(valid_plans, key=lambda p: p["total"])]
                warnings.append(
                    f"预算 {budget:.2f} 元内没有可行方案；以下为最接近方案（超预算 {pool[0]['total'] - budget:.2f} 元）"
                )
        pool.sort(key=lambda p: p["total"])

        chosen: list[dict[str, Any]] = []

        def _add(plan: dict[str, Any]) -> None:
            if len(chosen) < max_n and plan not in chosen:
                chosen.append(plan)

        first = pool[0]
        _add(first)
        # 选方案时保留多样性：工艺不同 / 纸张不同 / 装订不同
        for field in ("process", "paper_no", "binding"):
            if len(chosen) >= max_n:
                break
            for plan in pool:
                if plan in chosen:
                    continue
                if plan.get(field) != first.get(field):
                    _add(plan)
                    break
        for plan in pool:
            if len(chosen) >= max_n:
                break
            _add(plan)

        for index, plan in enumerate(chosen, start=1):
            plan["plan_no"] = index
            plan["budget_total"] = budget
            plan["feasible"] = feasible
            if budget is not None:
                plan["within_budget"] = plan["total"] <= budget
                plan["budget_delta"] = round(budget - plan["total"], 2)

        source_ids: list[str] = []
        for plan in chosen:
            source_ids.extend(plan.get("source_ids") or [])
        return _ok(
            {
                "mode": "plan",
                "plans": chosen,
                "budget_total": budget,
                "feasible": feasible,
                "base_params": {
                    "format": fmt,
                    "print_sheet": sheet,
                    "qty": qty,
                    "color_pages": color_pages,
                },
                "candidate_count": len(combos),
                "shortlist_count": len(shortlist),
                "valid_count": len(valid_plans),
                "missing": [],
            },
            source_ids=source_ids,
            warnings=warnings,
        )
    except Exception as exc:  # noqa: BLE001
        logger.exception("方案模式生成失败")
        return _fail("TOOL_EXEC_ERROR", f"方案生成异常：{type(exc).__name__}: {exc}")
    finally:
        if db is not None:
            try:
                db.close()
            except Exception:  # noqa: BLE001
                pass
'''

anchor = "PARAM_SCHEMAS: dict[str, dict[str, Any]] = {"
if "def plan_candidates(" not in text:
    text = text.replace(anchor, TOOL_CODE + "\n\n" + anchor, 1)
    P.write_text(text, encoding="utf-8")
    print("plan_candidates inserted")
else:
    print("plan_candidates exists")

# ---- 工具注册：类型 / 标签 / schema / handler ----
text = P.read_text(encoding="utf-8")
old_types = '    "calculate_cost": "calculation",'
if '    "plan_candidates": "calculation",' not in text and old_types in text:
    text = text.replace(old_types, old_types + '\n    "plan_candidates": "calculation",', 1)
text = text.replace(
    '    "calculate_cost": "成本计算",',
    '    "calculate_cost": "成本计算",\n    "plan_candidates": "方案候选计算",',
) if '"calculate_cost": "成本计算",' in text else text

# schema
schema_anchor = '''    "calculate_cost": {
        "type": "object",
        "properties": {"params": _TASK_PARAMS_SCHEMA},
        "additionalProperties": False,
        "required": ["params"],
    },
}'''
schema_new = '''    "calculate_cost": {
        "type": "object",
        "properties": {"params": _TASK_PARAMS_SCHEMA},
        "additionalProperties": False,
        "required": ["params"],
    },
    "plan_candidates": {
        "type": "object",
        "properties": {
            "params": {
                "type": "object",
                "description": "已确定的业务参数（至少 format/print_sheet/qty；paper_type/binding/process 可空，由系统枚举）",
                "properties": {
                    "format": {"type": "string"},
                    "print_sheet": {"type": "number"},
                    "qty": {"type": "integer"},
                    "paper_type": {"type": "string"},
                    "binding": {"type": "string"},
                    "color_pages": {"type": "integer"},
                    "process": {"type": "string"},
                    "budget_total": {"type": "number"},
                },
            },
            "budget_total": {"type": "number", "description": "总预算（元），可选；不传则不按预算过滤"},
            "max_plans": {"type": "integer", "description": "返回方案数上限，默认 3，最大 5"},
        },
        "additionalProperties": False,
        "required": ["params"],
    },
}'''
if '"plan_candidates": {' not in text:
    assert schema_anchor in text
    text = text.replace(schema_anchor, schema_new, 1)

# tool schemas 列表加入 plan（由 get_tool_schemas(plan_mode) 控制是否暴露）
tools_anchor = '''    {
        "type": "function",
        "function": {
            "name": "calculate_cost",'''
plan_schema_entry = '''    {
        "type": "function",
        "function": {
            "name": "plan_candidates",
            "description": (
                "【方案模式专用】根据已确定的基础参数（format/print_sheet/qty/color_pages）"
                "和可选预算，枚举资料库中适配的纸张、装订、工艺组合，逐一执行规则校验与成本计算，"
                "返回 1-5 个可行方案（含总价/单册/明细/来源 ID/是否在预算内）。"
                "paper_type/binding/process 留空表示由系统推荐；组合数量服务端已限流，不会爆炸。"
            ),
            "parameters": PARAM_SCHEMAS["plan_candidates"],
        },
    },
'''
if '"name": "plan_candidates"' not in text:
    # 插入到 calculate_cost 条目之前
    calc_pos = text.find(tools_anchor)
    if calc_pos < 0:
        raise SystemExit("tool schemas anchor missing")
    text = text[:calc_pos] + plan_schema_entry + text[calc_pos:]

# handlers
if '"plan_candidates": plan_candidates' not in text:
    text = text.replace(
        '    "calculate_cost": calculate_cost,\n}',
        '    "calculate_cost": calculate_cost,\n    "plan_candidates": plan_candidates,\n}',
        1,
    )

# get_tool_schemas 支持 plan_mode 过滤
old_get = '''def get_tool_schemas() -> list[dict[str, Any]]:
    """返回 OpenAI tools 参数（深拷贝，调用方可安全修改）。"""
    return json.loads(json.dumps(TOOL_SCHEMAS, ensure_ascii=False))'''
new_get = '''def get_tool_schemas(plan_mode: bool = False) -> list[dict[str, Any]]:
    """返回 OpenAI tools 参数（深拷贝，调用方可安全修改）。

    plan_mode=False 时隐藏方案模式工具，保证原有 print_cost 任务行为不变。
    """
    schemas = json.loads(json.dumps(TOOL_SCHEMAS, ensure_ascii=False))
    if not plan_mode:
        schemas = [item for item in schemas if (item.get("function") or {}).get("name") != "plan_candidates"]
    return schemas'''
if old_get in text:
    text = text.replace(old_get, new_get, 1)
else:
    # 可能已被改过
    if "def get_tool_schemas(plan_mode" not in text:
        raise SystemExit("get_tool_schemas anchor missing")

# summarize_tool_result 增加方案分支
summary_anchor = '''    if name == "calculate_cost":'''
summary_new = '''    if name == "plan_candidates":
        plans = data.get("plans") or []
        feasible = data.get("feasible", True)
        if plans:
            totals = [p.get("total") for p in plans if p.get("total") is not None]
            low = min(totals) if totals else "—"
            suffix = "" if feasible else "（预算内无可行方案，已给最接近方案）"
            return _truncate(f"生成 {len(plans)} 个候选方案，最低总成本 {low} 元{suffix}")
        return "方案候选计算完成（无可行方案）"
    if name == "calculate_cost":'''
if 'if name == "plan_candidates":' not in text:
    text = text.replace(summary_anchor, summary_new, 1)

P.write_text(text, encoding="utf-8")
print("tools.py registration done")
