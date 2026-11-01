"""依据（basis_refs）校验服务。

对应 BUILD_SPEC §4/§7：basis_refs 的 id 必须可追溯——
  L1：id 属于（本次工具 source.ids ∪ 任务关联资料 material_no ∪ rule_code ∪ 参数业务数据）；
  L2：id 在库中存在且启用，否则标记"依据已变更"。
提交审核时重跑 L2，失败 → 409 BASIS_MISMATCH（T06 用例：停用被引用资料后提交应被拒绝）。
"""

from __future__ import annotations

import json
from typing import Any, Iterable, List, Optional

from sqlalchemy.orm import Session

from app.models import (
    Material,
    ResultVersion,
    Rule,
    TaskMaterial,
    ToolCallRecord,
    BusinessTask,
)

MATERIAL_REF_TYPES = {"material", "paper", "process", "binding", "rate", "book", "quote"}


def parse_json(raw: Any, default: Any = None) -> Any:
    """安全解析 TEXT 中的 JSON，失败返回 default。"""
    if raw is None or raw == "":
        return default
    if isinstance(raw, (dict, list)):
        return raw
    try:
        return json.loads(raw)
    except (TypeError, ValueError):
        return default


def dump_json(value: Any, default: str = "{}") -> str:
    """把 dict/list 序列化为中文不转义的 JSON 文本。"""
    if value is None:
        return default
    if isinstance(value, str):
        return value
    try:
        return json.dumps(value, ensure_ascii=False)
    except (TypeError, ValueError):
        return default


def as_list(value: Any) -> list:
    """把 JSON 字段规范为 list。"""
    parsed = parse_json(value, [])
    return parsed if isinstance(parsed, list) else []


def as_dict(value: Any) -> dict:
    parsed = parse_json(value, {})
    return parsed if isinstance(parsed, dict) else {}


# ============================ 依据归一化 ============================
def basis_refs_of(obj: Any) -> List[dict]:
    """从 ai_result / result_version / 原始 JSON / list 中取出 basis_refs 列表。"""
    if obj is None:
        return []
    if isinstance(obj, list):
        raw = obj
    elif isinstance(obj, str):
        raw = parse_json(obj, [])
    else:
        raw = parse_json(getattr(obj, "basis_refs_json", None), [])
    if isinstance(raw, dict):
        raw = raw.get("basis_refs") or raw.get("refs") or []
    if not isinstance(raw, list):
        return []
    refs: List[dict] = []
    for item in raw:
        if isinstance(item, str):
            refs.append({"type": "ref", "id": item, "label": item})
        elif isinstance(item, dict):
            refs.append(dict(item))
    return refs


def ref_id(ref: Any) -> str:
    if isinstance(ref, str):
        return ref.strip()
    if not isinstance(ref, dict):
        return ""
    for key in ("id", "ref_id", "material_no", "rule_code", "code"):
        value = ref.get(key)
        if value not in (None, ""):
            return str(value).strip()
    return ""


def ref_type(ref: Any) -> str:
    if isinstance(ref, dict):
        return str(ref.get("type") or ref.get("ref_type") or "").strip().lower()
    return ""


# ============================ 证据来源集合 ============================
def collect_tool_source_ids(
    db: Session,
    business_task_id: int,
    ai_task_id: Optional[int] = None,
) -> set:
    """本次（或该任务全部）工具调用产生的 source.ids。"""
    query = db.query(ToolCallRecord).filter(ToolCallRecord.business_task_id == business_task_id)
    if ai_task_id is not None:
        query = query.filter(ToolCallRecord.ai_task_id == ai_task_id)
    ids: set = set()
    for record in query.all():
        for item in as_list(record.source_ids_json):
            if item not in (None, ""):
                ids.add(str(item).strip())
    return ids


def collect_task_material_nos(db: Session, task_id: int) -> set:
    """任务关联资料的 material_no（含历史版本）。"""
    rows = (
        db.query(Material.material_no)
        .join(TaskMaterial, TaskMaterial.material_id == Material.id)
        .filter(TaskMaterial.task_id == task_id)
        .all()
    )
    return {str(row[0]).strip() for row in rows if row[0]}


def collect_rule_codes(db: Session, business_type: Optional[str] = None) -> set:
    query = db.query(Rule.rule_code)
    if business_type:
        query = query.filter(Rule.business_type == business_type)
    return {str(row[0]).strip() for row in query.all() if row[0]}


def collect_param_ids(task: BusinessTask) -> set:
    """参数中的业务数据（键与值都算依据候选，L1 从宽）。"""
    params = as_dict(getattr(task, "params_json", None))
    ids: set = set()
    for key, value in params.items():
        ids.add(str(key))
        if value not in (None, "") and not isinstance(value, (dict, list)):
            ids.add(str(value))
    return ids


def collect_allowed_ids(db: Session, task: BusinessTask, ai_task_id: Optional[int] = None) -> set:
    """L1 允许集合：工具来源 + 关联资料 + 规则 + 参数业务数据。"""
    allowed = set()
    allowed |= collect_tool_source_ids(db, task.id, ai_task_id)
    allowed |= collect_task_material_nos(db, task.id)
    allowed |= collect_rule_codes(db, task.business_type)
    allowed |= collect_param_ids(task)
    return {item for item in allowed if item}


def l1_check(db: Session, task: BusinessTask, refs: Iterable[dict], ai_task_id: Optional[int] = None) -> dict:
    """L1 校验：返回 {"ok": bool, "unmatched": [id...]}（不命中只做软标记，不判失败）。"""
    allowed = collect_allowed_ids(db, task, ai_task_id)
    unmatched: List[str] = []
    total = 0
    for ref in refs:
        rid = ref_id(ref)
        if not rid:
            continue
        total += 1
        if rid not in allowed:
            unmatched.append(rid)
    return {"ok": not unmatched, "unmatched": unmatched, "total": total}


# ============================ L2：提交前依据重校验 ============================
def check_submit_basis(db: Session, version: ResultVersion, task: BusinessTask) -> List[dict]:
    """提交审核前重跑 L2：依据记录必须存在且启用。

    返回问题列表（空列表表示通过）：
        [{"id": "PAPER-001", "type": "material", "reason": "missing|disabled",
          "message": "依据资料 PAPER-001 已被删除/停用"}]
    """
    problems: List[dict] = []
    for ref in basis_refs_of(version):
        rid = ref_id(ref)
        rtype = ref_type(ref)
        label = (ref.get("label") if isinstance(ref, dict) else None) or rid
        if not rid:
            problems.append(
                {"id": "", "type": rtype or "unknown", "reason": "invalid", "message": "依据条目缺少 id"}
            )
            continue

        if rtype in MATERIAL_REF_TYPES:
            material = (
                db.query(Material)
                .filter(Material.material_no == rid)
                .order_by(Material.version_no.desc())
                .first()
            )
            if material is None:
                problems.append(
                    {
                        "id": rid,
                        "type": "material",
                        "reason": "missing",
                        "message": f"依据资料 {rid}（{label}）已不存在，请重新运行 AI 或更新依据",
                    }
                )
            elif not int(material.enabled or 0):
                problems.append(
                    {
                        "id": rid,
                        "type": "material",
                        "reason": "disabled",
                        "message": f"依据资料 {rid}（{label}）已被停用，请重新确认依据",
                    }
                )
        elif rtype == "rule":
            rule = db.query(Rule).filter(Rule.rule_code == rid).first()
            if rule is None:
                problems.append(
                    {
                        "id": rid,
                        "type": "rule",
                        "reason": "missing",
                        "message": f"依据规则 {rid}（{label}）已不存在，请重新确认依据",
                    }
                )
            elif not int(rule.enabled or 0):
                problems.append(
                    {
                        "id": rid,
                        "type": "rule",
                        "reason": "disabled",
                        "message": f"依据规则 {rid}（{label}）已被停用，请重新确认依据",
                    }
                )
        elif rtype == "calc":
            # 计算依据来自工具执行记录（公式+结果），不要求库中有独立记录
            continue
        else:
            # 类型未知：只要在资料号或规则号中能命中即视为有效
            exists_material = db.query(Material.id).filter(Material.material_no == rid).first()
            exists_rule = db.query(Rule.id).filter(Rule.rule_code == rid).first()
            if not exists_material and not exists_rule:
                problems.append(
                    {
                        "id": rid,
                        "type": rtype or "unknown",
                        "reason": "missing",
                        "message": f"依据 {rid}（{label}）无法追溯，请重新确认依据",
                    }
                )
    return problems


# ============================ 展示增强 ============================
def enrich_basis_refs(db: Session, refs: Any) -> List[dict]:
    """为页面补齐依据标题与可点击链接（/materials/{id} 或 /rules/{id}）。"""
    enriched: List[dict] = []
    for ref in basis_refs_of(refs):
        rid = ref_id(ref)
        rtype = ref_type(ref)
        label = (ref.get("label") if isinstance(ref, dict) else None) or rid
        href = None
        target_id = None
        try:
            if rtype in MATERIAL_REF_TYPES or (not rtype and rid):
                material = db.query(Material).filter(Material.material_no == rid).first()
                if material is not None:
                    href = f"/materials/{material.id}"
                    target_id = material.id
                    label = label or material.title
            if rtype == "rule" or (href is None and rid):
                rule = db.query(Rule).filter(Rule.rule_code == rid).first()
                if rule is not None:
                    href = f"/rules/{rule.id}"
                    target_id = rule.id
                    label = label or rule.name
        except Exception:  # pragma: no cover - 展示增强失败不影响主流程
            href = None
        item = dict(ref) if isinstance(ref, dict) else {"type": rtype, "id": rid, "label": label}
        item["id"] = rid
        item["type"] = rtype or "ref"
        item["label"] = label
        item["href"] = href
        item["target_id"] = target_id
        enriched.append(item)
    return enriched


__all__ = [
    "parse_json",
    "dump_json",
    "as_list",
    "as_dict",
    "basis_refs_of",
    "ref_id",
    "ref_type",
    "collect_tool_source_ids",
    "collect_task_material_nos",
    "collect_rule_codes",
    "collect_param_ids",
    "collect_allowed_ids",
    "l1_check",
    "check_submit_basis",
    "enrich_basis_refs",
    "MATERIAL_REF_TYPES",
]
