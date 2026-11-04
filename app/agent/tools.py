"""业务工具实现（BUILD_SPEC §5）。

四个工具全部同步执行、直接读写 SQLite（通过 ``app.db.SessionLocal``）：

- ``search_quotes``  retrieval   检索纸张/工艺/装订/费率资料
- ``get_rules``      retrieval   读取业务规则
- ``validate_spec``  validation  校验印制参数
- ``calculate_cost`` calculation 按资料库单价计算成本

统一返回信封::

    {"ok": bool, "data": any, "source": {"ids": [str]}, "warnings": [str],
     "error_code": str?, "error_message": str?}

约定：工具不吞异常后返回成功；缺数据返回 ``ok=false, error_code=DATA_NOT_FOUND``，
异常返回 ``TOOL_EXEC_ERROR``，参数不合法返回 ``TOOL_ARGS_INVALID``。
``source.ids`` 为 material_no / rule_code，供依据（basis）校验。
"""

from __future__ import annotations

import json
import logging
import math
import re
from typing import Any, Iterable

from app.db import SessionLocal
from app.models import Material, Rule

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# 常量
# ---------------------------------------------------------------------------

QUOTE_TYPES = ("paper", "process", "binding", "rate")
VALID_FORMATS = ("16开", "32开", "大32开")
VALID_BINDINGS = ("胶装", "骑马订", "精装", "锁线胶装", "线圈装")
TYPE_LABELS = {
    "paper": "纸张",
    "process": "工艺",
    "binding": "装订",
    "rate": "费率",
    "book": "图书",
}
TOOL_TYPES = {
    "search_quotes": "retrieval",
    "get_rules": "retrieval",
    "validate_spec": "validation",
    "calculate_cost": "calculation",
    "plan_candidates": "calculation",
}
TOOL_LABELS = {
    "search_quotes": "检索报价资料",
    "get_rules": "读取业务规则",
    "validate_spec": "校验印制参数",
    "calculate_cost": "测算印制成本",
}

# demo 口径的制版费默认值（rate 资料未提供 plate_fee_per_color 时使用）
DEFAULT_PLATE_FEE_PER_COLOR = 80.0
# 用纸损耗系数（BUILD_SPEC §5）
PAPER_WASTE_FACTOR = 1.05


class ToolArgsError(ValueError):
    """直接调用工具时参数非法（执行器会先做一次 schema 校验）。"""


# ---------------------------------------------------------------------------
# 通用小工具
# ---------------------------------------------------------------------------


def _session():
    """每次工具调用使用独立 Session（后台线程安全）。"""
    return SessionLocal()


def _json_loads(value: Any, default: Any = None) -> Any:
    if value is None:
        return default
    if isinstance(value, (dict, list)):
        return value
    try:
        return json.loads(value)
    except (TypeError, ValueError):
        return default


def _content_of(material: Any) -> dict[str, Any]:
    content = _json_loads(getattr(material, "content_json", None), {})
    return content if isinstance(content, dict) else {}


def _to_number(value: Any) -> float | None:
    """把数字/数字字符串转 float；bool、非法字符串返回 None。"""
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return None
        try:
            return float(text)
        except ValueError:
            return None
    return None


def _to_int(value: Any) -> int | None:
    """把整数/整数值字符串转 int；非整数返回 None。"""
    number = _to_number(value)
    if number is None:
        return None
    if abs(number - round(number)) > 1e-9:
        return None
    return int(round(number))


def _fmt_num(value: float) -> str:
    """公式中的数字：整数不带小数点，小数最多保留 4 位并去尾零。"""
    if abs(value - round(value)) < 1e-9:
        return str(int(round(value)))
    return f"{value:.4f}".rstrip("0").rstrip(".")


def _truncate(text: Any, limit: int = 200) -> str:
    """结果摘要统一截断到 ``limit`` 字符（中文按字计）。"""
    content = "" if text is None else str(text)
    if len(content) > limit:
        content = content[: max(0, limit - 1)] + "…"
    return content


def _ok(data: Any, source_ids: Iterable[str] | None = None, warnings: list[str] | None = None) -> dict[str, Any]:
    return {
        "ok": True,
        "data": data,
        "source": {"ids": list(dict.fromkeys(str(x) for x in (source_ids or []) if x))},
        "warnings": list(warnings or []),
    }


def _fail(
    error_code: str,
    error_message: str,
    *,
    data: Any = None,
    source_ids: Iterable[str] | None = None,
    warnings: list[str] | None = None,
) -> dict[str, Any]:
    return {
        "ok": False,
        "data": data,
        "source": {"ids": list(dict.fromkeys(str(x) for x in (source_ids or []) if x))},
        "warnings": list(warnings or []),
        "error_code": error_code,
        "error_message": str(error_message),
    }


def _kind_label(material_type: str | None) -> str:
    return TYPE_LABELS.get(str(material_type or ""), str(material_type or "资料"))


# ---------------------------------------------------------------------------
# 资料匹配（工具内部复用）
# ---------------------------------------------------------------------------


def _query_materials(db: Any, material_types: Iterable[str]) -> list[Any]:
    return (
        db.query(Material)
        .filter(Material.enabled == 1, Material.type.in_(tuple(material_types)))
        .order_by(Material.type, Material.material_no, Material.version_no.desc(), Material.id.desc())
        .all()
    )


def _material_text(row: Any) -> str:
    content = _content_of(row)
    parts = [
        str(getattr(row, "material_no", "") or ""),
        str(getattr(row, "title", "") or ""),
        str(getattr(row, "source", "") or ""),
        str(getattr(row, "type", "") or ""),
    ]
    for key in (
        "name",
        "title",
        "paper_type",
        "paper_name",
        "binding",
        "binding_type",
        "process",
        "process_name",
        "category",
    ):
        if content.get(key):
            parts.append(str(content.get(key)))
    # 按 BUILD_SPEC：keyword 需同时匹配 content（JSON 全文，含 note/unit 等）
    try:
        parts.append(json.dumps(content, ensure_ascii=False))
    except (TypeError, ValueError):
        parts.append(str(content))
    return " ".join(parts)


def _match_named(rows: Iterable[Any], name: str) -> Any:
    """在资料行中按名称匹配；优先 title 完全相同，其次包含关系，同分取最高版本。"""
    target = str(name or "").strip()
    if not target:
        return None
    rows = list(rows)
    exact = [r for r in rows if str(getattr(r, "title", "") or "").strip() == target]
    candidates = exact or [
        r
        for r in rows
        if target in _material_text(r) or (getattr(r, "title", None) and str(r.title) in target)
    ]
    if not candidates:
        return None
    candidates.sort(key=lambda r: (getattr(r, "version_no", 0) or 0, getattr(r, "id", 0) or 0), reverse=True)
    return candidates[0]


def _find_paper(rows: Iterable[Any], paper_type: str | None) -> Any:
    return _match_named(rows, paper_type or "")


def _price_from(content: dict[str, Any], keys: Iterable[str]) -> float | None:
    for key in keys:
        if key in content:
            number = _to_number(content.get(key))
            if number is not None:
                return number
    return None


PAPER_PRICE_KEYS = ("price_per_ream", "ream_price", "unit_price", "price")
BINDING_PRICE_KEYS = ("price_per_1000", "price_per_1000_copies", "unit_price", "price")
PROCESS_PRICE_KEYS = ("price_per_1000", "price_per_1000_copies", "unit_price", "price")
RATE_PRICE_KEYS = (
    "price_per_1000_sheets_per_color",
    "price_per_1000_sheets",
    "price_per_color",
    "unit_price",
    "price",
)
PLATE_FEE_KEYS = ("plate_fee_per_color", "plate_fee", "price_per_color_plate")


def _pick_rate(rows: Iterable[Any]) -> tuple[Any | None, float | None, dict[str, Any]]:
    """选择费率资料：优先标题含“千印张/印刷”的记录，其次最高版本。"""
    candidates: list[tuple[int, int, Any, float]] = []
    for row in rows:
        content = _content_of(row)
        price = _price_from(content, RATE_PRICE_KEYS)
        if price is None:
            continue
        title = str(getattr(row, "title", "") or "")
        priority = 1 if ("千印张" in title or "印刷" in title) else 0
        version = getattr(row, "version_no", 0) or 0
        candidates.append((priority, version, row, price))
    if not candidates:
        return None, None, {}
    candidates.sort(key=lambda x: (x[0], x[1], getattr(x[2], "id", 0) or 0), reverse=True)
    _, _, row, price = candidates[0]
    return row, price, _content_of(row)


# ---------------------------------------------------------------------------
# 工具 1：search_quotes
# ---------------------------------------------------------------------------


def search_quotes(keyword: str | None = None, category: str | None = None) -> dict[str, Any]:
    """检索资料库中的报价/资料（paper/process/binding/rate）。"""
    keyword = (keyword or "").strip() if keyword is not None else None
    category = (category or "").strip() if category is not None else None

    if keyword and len(keyword) > 50:
        return _fail("TOOL_ARGS_INVALID", "keyword 长度不能超过 50 个字符")
    if category and category not in QUOTE_TYPES:
        return _fail("TOOL_ARGS_INVALID", f"category 必须是 {'/'.join(QUOTE_TYPES)} 之一")

    db = None
    try:
        db = _session()
        rows = _query_materials(db, (category,) if category else QUOTE_TYPES)
        if keyword:
            needle = keyword.lower()
            rows = [
                row
                for row in rows
                if needle in _material_text(row).lower()
                or needle in str(getattr(row, "material_no", "") or "").lower()
            ]
        items = [
            {
                "material_no": row.material_no,
                "title": row.title,
                "type": row.type,
                "source": row.source,
                "version_no": row.version_no,
                "content": _content_of(row),
            }
            for row in rows
        ]
        if not items:
            scope = _kind_label(category) if category else "报价"
            condition = f"关键词“{keyword}”" if keyword else "筛选条件"
            return _fail(
                "DATA_NOT_FOUND",
                f"资料库中未找到{scope}（{condition}），不得猜测单价",
                data={"items": [], "keyword": keyword or "", "category": category or ""},
            )
        source_ids = [item["material_no"] for item in items]
        return _ok({"items": items}, source_ids)
    except Exception as exc:  # noqa: BLE001 - 工具失败必须显式返回
        return _fail("TOOL_EXEC_ERROR", f"检索资料失败：{exc}")
    finally:
        if db is not None:
            db.close()


# ---------------------------------------------------------------------------
# 工具 2：get_rules
# ---------------------------------------------------------------------------


def get_rules(business_type: str | None = None) -> dict[str, Any]:
    """读取业务规则（默认 print_cost）。"""
    business_type = (business_type or "").strip() or "print_cost"
    if business_type == "print_cost_plan":
        business_type = "print_cost"
    if len(business_type) > 50:
        return _fail("TOOL_ARGS_INVALID", "business_type 长度不能超过 50 个字符")

    db = None
    try:
        db = _session()
        rows = (
            db.query(Rule)
            .filter(Rule.enabled == 1, Rule.business_type == business_type)
            .order_by(Rule.id)
            .all()
        )
        if not rows:
            rows = (
                db.query(Rule)
                .filter(Rule.enabled == 1, Rule.business_type.in_(("common", "")))
                .order_by(Rule.id)
                .all()
            )
        rules = [
            {
                "rule_code": rule.rule_code,
                "name": rule.name,
                "rule_type": rule.rule_type,
                "severity": rule.severity,
                "content": _json_loads(rule.content_json, {}),
                "source": rule.source,
            }
            for rule in rows
        ]
        if not rules:
            return _fail("DATA_NOT_FOUND", f"规则库中未找到业务类型 {business_type} 的规则")
        source_ids = [rule["rule_code"] for rule in rules]
        return _ok({"rules": rules, "business_type": business_type}, source_ids)
    except Exception as exc:  # noqa: BLE001
        return _fail("TOOL_EXEC_ERROR", f"读取规则失败：{exc}")
    finally:
        if db is not None:
            db.close()


# ---------------------------------------------------------------------------
# 工具 3：validate_spec
# ---------------------------------------------------------------------------


def _issue(field: str, message: str, rule_code: str | None = None) -> dict[str, Any]:
    item: dict[str, Any] = {"field": field, "message": message}
    if rule_code:
        item["rule_code"] = rule_code
    return item


def _is_empty(value: Any) -> bool:
    return value is None or value == "" or value == [] or value == {} or value == ()


def _iter_rule_checks(content: Any) -> list[dict[str, Any]]:
    """尽力兼容种子规则的多种 content 结构。"""
    if not isinstance(content, dict):
        return []
    checks: list[dict[str, Any]] = []
    for key in ("checks", "constraints", "conditions", "items"):
        seq = content.get(key)
        if isinstance(seq, list):
            checks.extend(c for c in seq if isinstance(c, dict))
            return checks
    if any(k in content for k in ("field", "param", "target", "field_name")):
        checks.append(content)
    return checks


def _condition_matches(condition: Any, params: dict[str, Any]) -> bool:
    if not isinstance(condition, dict):
        return False
    field = condition.get("field") or condition.get("param")
    if not field:
        return False
    value = params.get(field)
    if "in" in condition and value not in (condition.get("in") or []):
        return False
    if "not_in" in condition and value in (condition.get("not_in") or []):
        return False
    if "eq" in condition and value != condition.get("eq"):
        return False
    if "value" in condition:
        op = str(condition.get("op") or condition.get("operator") or "==")
        if not _compare(value, op, condition.get("value")):
            return False
    return True


def _compare(value: Any, op: str, expected: Any) -> bool:
    op = str(op or "").strip()
    if op in ("in", "∈"):
        return value in (expected or [])
    if op in ("not in", "not_in"):
        return value not in (expected or [])
    if op in ("==", "=", "eq"):
        return value == expected
    if op in ("!=", "<>", "ne"):
        return value != expected
    left = _to_number(value)
    right = _to_number(expected)
    if left is None or right is None:
        return True  # 无法比较时不重复报错，交给类型校验
    if op in (">=", "gte", "min"):
        return left >= right
    if op in ("<=", "lte", "max"):
        return left <= right
    if op in (">", "gt"):
        return left > right
    if op in ("<", "lt"):
        return left < right
    return True


def _apply_rule_content(
    rule: Any, content: Any, params: dict[str, Any], issues: list[dict[str, Any]]
) -> None:
    if not isinstance(content, dict):
        return
    rule_code = getattr(rule, "rule_code", None)
    rule_name = getattr(rule, "name", None) or rule_code or "规则"
    severity = str(getattr(rule, "severity", "") or "")

    # 必填字段规则：{"type":"required","fields":[...],"message":"..."}
    if str(content.get("type") or "") == "required":
        fields = content.get("fields")
        if isinstance(fields, (list, tuple)):
            message = str(content.get("message") or content.get("error_message") or "")
            for field in fields:
                field = str(field)
                if _is_empty(params.get(field)):
                    issues.append(_issue(field, message or f"{field} 为必填项", rule_code))
        return

    # 依赖规则：{"type":"dependency","when":{"binding":"骑马订"},"max_print_sheet":8}
    if isinstance(content.get("when"), dict) and (
        "max_print_sheet" in content or "min_print_sheet" in content
    ):
        when = content.get("when") or {}
        if all(str(params.get(key)) == str(value) for key, value in when.items()):
            sheet = _to_number(params.get("print_sheet"))
            message = str(content.get("message") or content.get("error_message") or rule_name)
            max_sheet = _to_number(content.get("max_print_sheet"))
            min_sheet = _to_number(content.get("min_print_sheet"))
            if sheet is not None and max_sheet is not None and sheet > max_sheet:
                issues.append(_issue("print_sheet", message, rule_code))
            if sheet is not None and min_sheet is not None and sheet < min_sheet:
                issues.append(_issue("print_sheet", message, rule_code))
        return

    # 条件规则：{"if": {...}, "then": {...}} / {"when": {...}, "checks": [...]}
    condition = content.get("if") or content.get("when")
    if isinstance(condition, dict) and not _condition_matches(condition, params):
        return
    then = content.get("then")
    if isinstance(then, dict):
        _apply_rule_content(rule, then, params, issues)

    for check in _iter_rule_checks(content):
        field = str(check.get("field") or check.get("param") or check.get("target") or "").strip()
        if not field:
            continue
        value = params.get(field)
        message = str(check.get("message") or check.get("error_message") or "")
        required = bool(check.get("required"))
        if required and _is_empty(value):
            issues.append(_issue(field, message or f"{field} 为必填项", rule_code))
            continue
        if _is_empty(value):
            continue

        check_type = str(check.get("type") or "").strip().lower()
        if check_type == "integer":
            integer = _to_int(value)
            if integer is None:
                issues.append(_issue(field, message or f"{field} 必须是整数", rule_code))
                continue
            value = integer

        enum = (
            check.get("enum")
            or check.get("allowed")
            or check.get("allowed_values")
            or check.get("in")
            or check.get("values")
        )
        if isinstance(enum, str):
            enum = [part.strip() for part in re.split(r"[|,，/、]", enum) if part.strip()]
        if isinstance(enum, (list, tuple)) and enum and value not in enum:
            issues.append(
                _issue(field, message or f"{field} 必须是 {'/'.join(str(x) for x in enum)} 之一", rule_code)
            )
            continue

        min_value = check.get("min", check.get("minimum"))
        max_value = check.get("max", check.get("maximum"))
        if isinstance(check.get("range"), (list, tuple)) and len(check["range"]) == 2:
            min_value, max_value = check["range"][0], check["range"][1]
        number = _to_number(value)
        if min_value is not None:
            bound = _to_number(min_value)
            if number is not None and bound is not None and number < bound:
                issues.append(_issue(field, message or f"{field} 不能小于 {_fmt_num(bound)}", rule_code))
                continue
        if max_value is not None:
            bound = _to_number(max_value)
            if number is not None and bound is not None and number > bound:
                issues.append(_issue(field, message or f"{field} 不能大于 {_fmt_num(bound)}", rule_code))
                continue
        min_exclusive = _to_number(check.get("min_exclusive"))
        if min_exclusive is not None and number is not None and number <= min_exclusive:
            issues.append(_issue(field, message or f"{field} 必须大于 {_fmt_num(min_exclusive)}", rule_code))
            continue

        op = check.get("operator") or check.get("op")
        if op and "value" in check:
            if not _compare(value, str(op), check.get("value")):
                expected = check.get("value")
                issues.append(
                    _issue(field, message or f"{field} 应满足 {op} {expected}", rule_code)
                )
                continue

        pattern = check.get("pattern") or check.get("regex")
        if pattern:
            try:
                if not re.search(str(pattern), str(value)):
                    issues.append(_issue(field, message or f"{field} 格式不合法", rule_code))
            except re.error:
                pass

        # 规则只给了 name、没有结构化字段时不强行判定
        _ = (rule_name, severity)


def _query_spec_rules(db: Any) -> list[Any]:
    return (
        db.query(Rule)
        .filter(Rule.enabled == 1, Rule.rule_type == "spec")
        .order_by(Rule.id)
        .all()
    )


def _normalize_params(params: dict[str, Any]) -> dict[str, Any]:
    normalized = dict(params)
    for key in ("format", "paper_type", "binding", "process"):
        if key in normalized and normalized[key] is not None:
            normalized[key] = str(normalized[key]).strip()
    if "print_sheet" in normalized:
        number = _to_number(normalized["print_sheet"])
        if number is not None:
            normalized["print_sheet"] = int(number) if abs(number - round(number)) < 1e-9 else number
    if "qty" in normalized:
        integer = _to_int(normalized["qty"])
        if integer is not None:
            normalized["qty"] = integer
    if "color_pages" in normalized:
        integer = _to_int(normalized["color_pages"])
        if integer is not None:
            normalized["color_pages"] = integer
    else:
        normalized["color_pages"] = 0
    process = normalized.get("process")
    if process in (None, "", "无", "none", "None", "NONE"):
        normalized["process"] = None
    return normalized


def validate_spec(params: dict[str, Any] | None) -> dict[str, Any]:
    """按 rules(rule_type=spec) + 硬编码白名单校验印制参数。"""
    if not isinstance(params, dict):
        return _fail("TOOL_ARGS_INVALID", "params 必须是 JSON 对象")

    issues: list[dict[str, Any]] = []
    normalized = _normalize_params(params)

    # ---- 硬编码基础校验（规则表之外的兜底，保证核心口径始终生效） ----
    required_fields = {
        "format": "开本",
        "print_sheet": "印张",
        "qty": "印数",
        "paper_type": "纸张类型",
        "binding": "装订方式",
    }
    for field, label in required_fields.items():
        if _is_empty(normalized.get(field)):
            issues.append(_issue(field, f"{label}（{field}）不能为空", "SPEC-REQUIRED"))

    fmt = normalized.get("format")
    if not _is_empty(fmt) and fmt not in VALID_FORMATS:
        issues.append(
            _issue("format", f"开本必须是 {'/'.join(VALID_FORMATS)} 之一", "SPEC-FORMAT")
        )

    sheet = _to_number(normalized.get("print_sheet"))
    if not _is_empty(normalized.get("print_sheet")) and sheet is None:
        issues.append(_issue("print_sheet", "印张必须是数字", "SPEC-PRINT-SHEET"))
    elif sheet is not None:
        if sheet <= 0 or sheet > 50:
            issues.append(_issue("print_sheet", "印张必须大于 0 且不超过 50", "SPEC-PRINT-SHEET"))
        normalized["print_sheet"] = int(sheet) if abs(sheet - round(sheet)) < 1e-9 else sheet

    qty = _to_int(normalized.get("qty"))
    if not _is_empty(normalized.get("qty")) and qty is None:
        issues.append(_issue("qty", "印数必须是整数", "SPEC-QTY-INTEGER"))
    elif qty is not None:
        if qty < 500:
            issues.append(_issue("qty", "印数不得低于 500 册", "SPEC-QTY-MIN"))
        normalized["qty"] = qty

    color_pages = _to_int(normalized.get("color_pages"))
    if color_pages is None:
        issues.append(_issue("color_pages", "彩色页数必须是整数", "SPEC-COLOR-PAGES"))
    else:
        if color_pages < 0 or color_pages > 4:
            issues.append(_issue("color_pages", "彩色页数必须在 0-4 之间", "SPEC-COLOR-PAGES"))
        normalized["color_pages"] = color_pages

    binding = normalized.get("binding")
    if not _is_empty(binding) and binding not in VALID_BINDINGS:
        issues.append(
            _issue("binding", f"装订方式必须是 {'/'.join(VALID_BINDINGS)} 之一", "SPEC-BINDING")
        )

    db = None
    try:
        db = _session()
        # ---- 纸张必须能在资料库中找到 ----
        paper_rows: list[Any] = []
        paper_type = normalized.get("paper_type")
        if not _is_empty(paper_type):
            paper_rows = _query_materials(db, ("paper",))
            paper = _find_paper(paper_rows, str(paper_type))
            if paper is None:
                issues.append(
                    _issue(
                        "paper_type",
                        f"纸张“{paper_type}”未在资料库中找到对应报价",
                        "MATERIAL-PAPER-NOT-FOUND",
                    )
                )
            else:
                content = _content_of(paper)
                format_fit = content.get("format_fit")
                if (
                    not _is_empty(fmt)
                    and isinstance(format_fit, (list, tuple))
                    and format_fit
                    and str(fmt) not in [str(x) for x in format_fit]
                ):
                    issues.append(
                        _issue(
                            "format",
                            f"纸张“{paper.title}”不适配开本 {fmt}（适配：{'/'.join(str(x) for x in format_fit)}）",
                            "SPEC-PAPER-FORMAT",
                        )
                    )

        # ---- 规则表（rule_type=spec）结构化校验 ----
        for rule in _query_spec_rules(db):
            _apply_rule_content(rule, _json_loads(rule.content_json, {}), normalized, issues)
    except Exception as exc:  # noqa: BLE001
        return _fail("TOOL_EXEC_ERROR", f"参数校验读取资料/规则失败：{exc}")
    finally:
        if db is not None:
            db.close()

    # 去重并整理
    deduped: list[dict[str, Any]] = []
    seen: set[tuple[Any, Any]] = set()
    for issue in issues:
        key = (issue.get("field"), issue.get("message"))
        if key in seen:
            continue
        seen.add(key)
        deduped.append(issue)

    return _ok(
        {
            "valid": len(deduped) == 0,
            "issues": deduped,
            "normalized": normalized,
        },
        [],
    )


# ---------------------------------------------------------------------------
# 工具 4：calculate_cost
# ---------------------------------------------------------------------------


def _select_process(rows: Iterable[Any], process: str) -> Any:
    return _match_named(rows, process)


def calculate_cost(params: dict[str, Any] | None) -> dict[str, Any]:
    """从 materials 读取单价并计算成本（BUILD_SPEC §5 唯一口径）。"""
    if not isinstance(params, dict):
        return _fail("TOOL_ARGS_INVALID", "params 必须是 JSON 对象")

    normalized = _normalize_params(params)
    qty = _to_int(normalized.get("qty"))
    sheet = _to_number(normalized.get("print_sheet"))
    color_pages = _to_int(normalized.get("color_pages"))
    paper_type = normalized.get("paper_type")
    binding = normalized.get("binding")
    process = normalized.get("process")

    arg_issues: list[dict[str, Any]] = []
    if qty is None or qty <= 0:
        arg_issues.append(_issue("qty", "印数必须是正整数"))
    if sheet is None or sheet <= 0:
        arg_issues.append(_issue("print_sheet", "印张必须是大于 0 的数字"))
    if color_pages is None:
        arg_issues.append(_issue("color_pages", "彩色页数必须是整数"))
    if arg_issues:
        return _fail("TOOL_ARGS_INVALID", "计算参数不合法：" + "；".join(i["message"] for i in arg_issues), data={"issues": arg_issues})

    db = None
    try:
        db = _session()
        paper_rows = _query_materials(db, ("paper",))
        binding_rows = _query_materials(db, ("binding",))
        process_rows = _query_materials(db, ("process",))
        rate_rows = _query_materials(db, ("rate",))

        paper = _find_paper(paper_rows, str(paper_type)) if not _is_empty(paper_type) else None
        paper_content = _content_of(paper) if paper is not None else {}
        paper_price = _price_from(paper_content, PAPER_PRICE_KEYS)

        binding_row = _select_process(binding_rows, str(binding)) if not _is_empty(binding) else None
        binding_content = _content_of(binding_row) if binding_row is not None else {}
        binding_price = _price_from(binding_content, BINDING_PRICE_KEYS)

        process_row = _select_process(process_rows, str(process)) if not _is_empty(process) else None
        process_content = _content_of(process_row) if process_row is not None else {}
        process_price = _price_from(process_content, PROCESS_PRICE_KEYS) if process_row is not None else 0.0

        rate_row, rate_price, rate_content = _pick_rate(rate_rows)
        plate_price = _price_from(rate_content, PLATE_FEE_KEYS)

        missing: list[str] = []
        if _is_empty(paper_type):
            missing.append("纸张类型（paper_type 未提供）")
        elif paper is None or paper_price is None:
            missing.append(f"纸张单价（{paper_type}，materials.type=paper.price_per_ream）")
        if _is_empty(binding):
            missing.append("装订方式（binding 未提供）")
        elif binding_row is None or binding_price is None:
            missing.append(f"装订单价（{binding}，materials.type=binding.price_per_1000）")
        if rate_row is None or rate_price is None:
            missing.append("印刷费率（materials.type=rate.price_per_1000_sheets_per_color）")
        if not _is_empty(process) and (process_row is None or process_price is None):
            missing.append(f"工艺单价（{process}，materials.type=process.price_per_1000）")

        found_source_ids = [
            getattr(row, "material_no", None)
            for row in (paper, rate_row, binding_row, process_row)
            if row is not None
        ]
        if missing:
            return _fail(
                "DATA_NOT_FOUND",
                "缺少必需单价，无法计算总成本：" + "；".join(missing),
                data={"missing": missing, "params": normalized},
                source_ids=found_source_ids,
                warnings=["资料不足时不得给出总金额，请补充/关联对应报价后再计算"],
            )

        assert qty is not None and sheet is not None and color_pages is not None
        assert paper_price is not None and binding_price is not None and rate_price is not None

        color_count = 4 if color_pages > 0 else 1
        finished_plate_price = plate_price if plate_price is not None else DEFAULT_PLATE_FEE_PER_COLOR

        reams = math.ceil(qty * sheet / 1000 * PAPER_WASTE_FACTOR)
        paper_amount = round(reams * paper_price, 2)
        print_amount = round(qty / 1000 * sheet * rate_price * color_count, 2)
        plate_amount = round(color_count * finished_plate_price, 2)
        binding_amount = round(qty / 1000 * binding_price, 2)
        process_amount = round(qty / 1000 * (process_price or 0.0), 2) if process else 0.0

        warnings: list[str] = []
        if plate_price is None:
            warnings.append("费率资料未提供制版单价，制版费按演示口径 80 元/色计算")
        if color_pages:
            warnings.append("彩色页数大于 0 时印刷费与制版费按四色档计算（演示口径）")

        line_items = [
            {
                "name": "纸张费",
                "formula": (
                    f"ceil({_fmt_num(qty)}×{_fmt_num(sheet)}÷1000×{PAPER_WASTE_FACTOR})"
                    f"={reams}令；{reams}×{_fmt_num(paper_price)}={paper_amount:.2f}元"
                ),
                "amount": paper_amount,
            },
            {
                "name": "印刷费",
                "formula": (
                    f"{_fmt_num(qty)}÷1000×{_fmt_num(sheet)}×{_fmt_num(rate_price)}"
                    f"×{color_count}色={print_amount:.2f}元"
                ),
                "amount": print_amount,
            },
            {
                "name": "制版费",
                "formula": f"{color_count}色×{_fmt_num(finished_plate_price)}={plate_amount:.2f}元",
                "amount": plate_amount,
            },
            {
                "name": "装订费",
                "formula": f"{_fmt_num(qty)}÷1000×{_fmt_num(binding_price)}={binding_amount:.2f}元",
                "amount": binding_amount,
            },
        ]
        if process:
            line_items.append(
                {
                    "name": f"工艺费（{process}）",
                    "formula": f"{_fmt_num(qty)}÷1000×{_fmt_num(process_price or 0.0)}={process_amount:.2f}元",
                    "amount": process_amount,
                }
            )
        else:
            line_items.append(
                {
                    "name": "工艺费",
                    "formula": "未指定工艺=0.00元",
                    "amount": 0.0,
                }
            )

        total = round(sum(item["amount"] for item in line_items), 2)
        per_copy = round(total / qty, 2)

        return _ok(
            {
                "line_items": line_items,
                "total": total,
                "per_copy": per_copy,
                "currency": "CNY",
                "missing": [],
                "warnings": warnings,
            },
            found_source_ids,
            warnings=warnings,
        )
    except Exception as exc:  # noqa: BLE001
        return _fail("TOOL_EXEC_ERROR", f"成本计算失败：{exc}")
    finally:
        if db is not None:
            db.close()


# ---------------------------------------------------------------------------
# 工具注册表 / OpenAI function schema / 参数校验
# ---------------------------------------------------------------------------

_TASK_PARAMS_SCHEMA = {
    "type": "object",
    "description": "业务任务参数；通常包含 format/print_sheet/qty/paper_type/binding，可选 color_pages/process",
    "properties": {
        "format": {"type": "string", "enum": list(VALID_FORMATS), "description": "开本"},
        "print_sheet": {"type": "number", "description": "印张，必须 >0 且 ≤50"},
        "qty": {"type": "integer", "description": "印数，必须 ≥500"},
        "paper_type": {"type": "string", "description": "纸张名称，需能在资料库找到"},
        "binding": {"type": "string", "enum": list(VALID_BINDINGS), "description": "装订方式"},
        "color_pages": {"type": "integer", "description": "彩色页数，0 表示单色"},
        "process": {"type": "string", "description": "工艺名称，可为空或“无”"},
    },
}



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


PARAM_SCHEMAS: dict[str, dict[str, Any]] = {
    "search_quotes": {
        "type": "object",
        "properties": {
            "keyword": {"type": "string", "maxLength": 50, "description": "标题/内容关键词，最长 50 字"},
            "category": {
                "type": "string",
                "enum": list(QUOTE_TYPES),
                "description": "资料类型：paper/process/binding/rate",
            },
        },
        "additionalProperties": False,
        "required": [],
    },
    "get_rules": {
        "type": "object",
        "properties": {
            "business_type": {"type": "string", "maxLength": 50, "description": "业务类型，默认 print_cost"}
        },
        "additionalProperties": False,
        "required": [],
    },
    "validate_spec": {
        "type": "object",
        "properties": {"params": _TASK_PARAMS_SCHEMA},
        "additionalProperties": False,
        "required": ["params"],
    },
    "calculate_cost": {
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
}

TOOL_SCHEMAS: list[dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": "search_quotes",
            "description": (
                "检索资料库中的纸张、工艺、装订、费率报价资料。"
                "按关键词或类型检索，返回 material_no/title/type/source/version_no/content。"
                "category：paper=纸张报价、rate=印刷/制版费率、binding=装订费、process=工艺费。"
                "每个类别检索一次即可；无结果返回 DATA_NOT_FOUND；不得猜测单价。"
            ),
            "parameters": PARAM_SCHEMAS["search_quotes"],
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_rules",
            "description": (
                "读取业务规则（默认 print_cost），返回 rule_code/name/rule_type/severity/content。"
                "用于了解印数下限、开本白名单、印张上限、装订匹配等校验口径。"
            ),
            "parameters": PARAM_SCHEMAS["get_rules"],
        },
    },
    {
        "type": "function",
        "function": {
            "name": "validate_spec",
            "description": (
                "按规则库与硬编码白名单校验印制参数，返回 valid/issues/normalized。"
                "issues 中给出 field/message/rule_code，供修正参数或写入不确定项。"
            ),
            "parameters": PARAM_SCHEMAS["validate_spec"],
        },
    },
    {
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
    {
        "type": "function",
        "function": {
            "name": "calculate_cost",
            "description": (
                "从资料库读取纸张/工艺/装订/费率单价，按统一公式计算成本。"
                "只需传任务参数（format/print_sheet/qty/paper_type/binding/color_pages/process），"
                "无需先传 material_no；内部按 paper_type/binding/process 查找资料。"
                "返回 line_items（含 formula）、total、per_copy、currency、missing、source.ids。"
                "缺少任何必需单价时返回 DATA_NOT_FOUND 与 missing，不得估算。"
            ),
            "parameters": PARAM_SCHEMAS["calculate_cost"],
        },
    },
]

TOOL_HANDLERS = {
    "search_quotes": search_quotes,
    "get_rules": get_rules,
    "validate_spec": validate_spec,
    "calculate_cost": calculate_cost,
    "plan_candidates": plan_candidates,
}


def get_tool_schemas(plan_mode: bool = False) -> list[dict[str, Any]]:
    """返回 OpenAI tools 参数（深拷贝，调用方可安全修改）。

    plan_mode=False 时隐藏方案模式工具，保证原有 print_cost 任务行为不变。
    """
    schemas = json.loads(json.dumps(TOOL_SCHEMAS, ensure_ascii=False))
    if not plan_mode:
        schemas = [item for item in schemas if (item.get("function") or {}).get("name") != "plan_candidates"]
    return schemas


def get_tool_type(name: str) -> str:
    return TOOL_TYPES.get(str(name or ""), "unknown")


def get_tool_label(name: str) -> str:
    return TOOL_LABELS.get(str(name or ""), str(name or "未知工具"))


def validate_tool_args(name: str, args: Any) -> list[str]:
    """按 JSON Schema 做轻量参数校验，返回错误列表（空列表=通过）。

    说明：嵌套 params 的字段级校验交给工具自身（需给出业务化 issue），
    这里只校验外层结构、必填、类型、枚举与长度。
    """
    if name not in PARAM_SCHEMAS:
        return [f"未知工具：{name}"]
    if not isinstance(args, dict):
        return ["工具参数必须是 JSON 对象"]

    schema = PARAM_SCHEMAS[name]
    props: dict[str, Any] = schema.get("properties") or {}
    errors: list[str] = []

    for key in args.keys():
        if key not in props:
            errors.append(f"不支持的参数：{key}")

    for key in schema.get("required") or []:
        if key not in args or args.get(key) is None:
            errors.append(f"缺少必填参数：{key}")

    for key, spec in props.items():
        if key not in args:
            continue
        value = args[key]
        if value is None:
            continue
        expected = spec.get("type")
        if expected == "string":
            if not isinstance(value, str):
                errors.append(f"参数 {key} 类型应为字符串")
                continue
            text = value.strip()
            max_length = spec.get("maxLength")
            if max_length and len(text) > int(max_length):
                errors.append(f"参数 {key} 长度不能超过 {max_length}")
            enum = spec.get("enum")
            if enum and text not in enum:
                errors.append(f"参数 {key} 必须是 {'/'.join(str(x) for x in enum)} 之一")
        elif expected == "object":
            if not isinstance(value, dict):
                errors.append(f"参数 {key} 类型应为 JSON 对象")
        elif expected == "integer":
            if isinstance(value, bool) or not isinstance(value, int):
                errors.append(f"参数 {key} 类型应为整数")
        elif expected == "number":
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                errors.append(f"参数 {key} 类型应为数字")
    return errors


def summarize_tool_result(name: str, result: dict[str, Any]) -> str:
    """生成 ≤200 字的执行记录摘要（tool_call_records.result_summary）。"""
    if not isinstance(result, dict):
        return _truncate(f"{name} 返回格式异常")
    if not result.get("ok"):
        message = result.get("error_message") or result.get("error_code") or "执行失败"
        return _truncate(f"{get_tool_label(name)}失败：{message}")

    data = result.get("data") or {}
    if name == "search_quotes":
        items = data.get("items") or []
        types = sorted({str(item.get("type") or "") for item in items})
        label = "/".join(t for t in types if t) or "资料"
        return _truncate(f"找到 {len(items)} 条{label}报价资料")
    if name == "get_rules":
        rules = data.get("rules") or []
        codes = [str(rule.get("rule_code") or "") for rule in rules[:6] if rule.get("rule_code")]
        suffix = "、".join(codes)
        return _truncate(f"读取 {len(rules)} 条规则" + (f"：{suffix}" if suffix else ""))
    if name == "validate_spec":
        if data.get("valid"):
            return "参数校验通过，未发现问题"
        issues = data.get("issues") or []
        messages = [str(item.get("message") or item.get("field") or "") for item in issues[:3]]
        return _truncate(f"参数校验发现 {len(issues)} 个问题：" + "；".join(m for m in messages if m))
    if name == "plan_candidates":
        plans = data.get("plans") or []
        feasible = data.get("feasible", True)
        if plans:
            totals = [p.get("total") for p in plans if p.get("total") is not None]
            low = min(totals) if totals else "—"
            suffix = "" if feasible else "（预算内无可行方案，已给最接近方案）"
            return _truncate(f"生成 {len(plans)} 个候选方案，最低总成本 {low} 元{suffix}")
        return "方案候选计算完成（无可行方案）"
    if name == "calculate_cost":
        total = data.get("total")
        per_copy = data.get("per_copy")
        items = data.get("line_items") or []
        if total is None:
            return "成本计算完成（无总价）"
        return _truncate(
            f"总成本 {float(total):.2f} 元，单册 {float(per_copy or 0):.2f} 元，{len(items)} 个分项"
        )
    return _truncate(f"{get_tool_label(name)}执行成功")
