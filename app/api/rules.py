"""规则库接口（BUILD_SPEC §4 #20-#22）。"""

from __future__ import annotations

import logging
from typing import Any, Optional

from fastapi import APIRouter, Depends, Query
from sqlalchemy import or_
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.api import api_error, success
from app.auth import require_operator, require_user
from app.db import get_db, now_iso
from app.models import Rule
from app.schemas import RuleCreateRequest, RuleUpdateRequest
from app.services import basis_service

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/rules", tags=["规则库"])

# 规则类型为自由文本，这里给出常用白名单（与前端 datalist 保持兼容，避免误拒）
RULE_TYPES = (
    "spec",
    "calc",
    "calculation",
    "data",
    "policy",
    "validation",
    "cost",
    "evidence",
    "process",
)
SEVERITIES = ("error", "warning", "warn", "info", "high", "medium")


def _rule_to_dict(rule: Rule, with_content: bool = True) -> dict:
    data = {
        "id": rule.id,
        "rule_code": rule.rule_code,
        "name": rule.name,
        "business_type": rule.business_type,
        "rule_type": rule.rule_type,
        "severity": rule.severity,
        "source": rule.source,
        "enabled": bool(int(rule.enabled or 0)),
        "created_at": rule.created_at,
    }
    if with_content:
        data["content"] = basis_service.parse_json(rule.content_json, {})
    return data


@router.post("", summary="新增规则（operator）")
def create_rule(
    payload: RuleCreateRequest,
    user: dict = Depends(require_operator),
    db: Session = Depends(get_db),
):
    rule_code = payload.rule_code.strip()
    if not rule_code:
        api_error(400, "rule_code 不能为空")
    if payload.rule_type not in RULE_TYPES:
        api_error(400, f"rule_type 非法，只能是 {', '.join(RULE_TYPES)}")
    severity = (payload.severity or "error").lower()
    if severity not in SEVERITIES:
        api_error(400, f"severity 非法，只能是 {', '.join(SEVERITIES)}")

    content: Any = payload.content
    if isinstance(content, str):
        parsed = basis_service.parse_json(content, None)
        if not isinstance(parsed, (dict, list)):
            api_error(400, "content 必须是 JSON 对象")
        content = parsed
    if not isinstance(content, (dict, list)):
        api_error(400, "content 必须是 JSON 对象")

    exists = db.query(Rule.id).filter(Rule.rule_code == rule_code).first()
    if exists:
        api_error(409, f"规则编号 {rule_code} 已存在")

    rule = Rule(
        rule_code=rule_code,
        name=payload.name.strip(),
        business_type=(payload.business_type or "print_cost").strip(),
        rule_type=payload.rule_type,
        content_json=basis_service.dump_json(content, "{}"),
        severity=severity,
        source=(payload.source or "").strip(),
        enabled=1,
        created_at=now_iso(),
    )
    db.add(rule)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        api_error(409, f"规则编号 {rule_code} 已存在")
    db.refresh(rule)
    logger.info("新增规则 rule_code=%s user=%s", rule.rule_code, user.get("username"))
    return success({"id": rule.id, "rule_code": rule.rule_code})


@router.get("", summary="规则列表（登录）")
def list_rules(
    business_type: Optional[str] = Query(default=None),
    keyword: Optional[str] = Query(default=None),
    user: dict = Depends(require_user),
    db: Session = Depends(get_db),
):
    query = db.query(Rule)
    business_type = (business_type or "").strip()
    if business_type:
        query = query.filter(Rule.business_type == business_type)
    keyword = (keyword or "").strip()
    if keyword:
        like = f"%{keyword}%"
        query = query.filter(
            or_(Rule.rule_code.like(like), Rule.name.like(like), Rule.source.like(like))
        )
    rows = query.order_by(Rule.id.asc()).limit(500).all()
    return success({"items": [_rule_to_dict(row) for row in rows], "total": len(rows)})


@router.get("/{rule_id}", summary="规则详情（登录）")
def get_rule(
    rule_id: int,
    user: dict = Depends(require_user),
    db: Session = Depends(get_db),
):
    rule = db.query(Rule).filter(Rule.id == int(rule_id)).first()
    if rule is None:
        api_error(404, "规则不存在")
    return success(_rule_to_dict(rule))



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
