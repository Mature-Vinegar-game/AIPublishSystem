"""结果与版本接口（BUILD_SPEC §4 #8-#9）。

#8 POST /api/business-tasks/{id}/results   保存结果版本（采用 AI 结果或人工保存）
#9 GET  /api/business-tasks/{id}/versions  版本列表（含 content_md/结构化/来源/操作人）

规则：版本只增不改；PENDING_REVIEW/COMPLETED 冻结（409）；AI 重跑不自动提版。
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.api import api_error, success
from app.auth import require_operator, require_user
from app.db import get_db
from app.models import AI_SUCCEEDED, VERSION_SOURCE_AI, VERSION_SOURCE_MANUAL, AiResult, AiTask
from app.schemas import ResultCreateRequest
from app.services import basis_service, task_service, version_service

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/business-tasks", tags=["结果与版本"])




def _plan_markdown(plan: dict, index: int) -> str:
    """由选中的方案生成版本正文（含可复算明细）。"""
    lines = [
        f"## 采用方案 {index + 1}：{plan.get('paper_type', '—')} / {plan.get('binding', '—')} / {plan.get('process', '无')}",
        "",
        f"- 总成本：{float(plan.get('total') or 0):.2f} 元",
        f"- 单册成本：{float(plan.get('per_copy') or 0):.2f} 元",
        f"- 色数档位：{plan.get('color_pages', '—')}",
    ]
    if plan.get("budget_total") is not None:
        lines.append(
            f"- 预算：{float(plan.get('budget_total')):.2f} 元；"
            f"{'在预算内' if plan.get('within_budget', True) else '超出预算'}"
        )
    lines += ["", "### 成本明细", ""]
    for item in plan.get("line_items") or []:
        amount = item.get("amount")
        amount_text = f"{float(amount):.2f}" if isinstance(amount, (int, float)) else "—"
        lines.append(f"- {item.get('name', '—')}：{item.get('formula', '—')} = {amount_text} 元")
    return "\n".join(lines)


def _plan_version_structured(plan: dict, index: int) -> dict:
    return {
        "plan_mode": True,
        "plan_no": index + 1,
        "paper_type": plan.get("paper_type"),
        "binding": plan.get("binding"),
        "process": plan.get("process"),
        "color_pages": plan.get("color_pages"),
        "line_items": plan.get("line_items") or [],
        "total": plan.get("total"),
        "per_copy": plan.get("per_copy"),
        "budget_total": plan.get("budget_total"),
        "within_budget": plan.get("within_budget"),
    }


def _plan_version_basis(plan: dict, ai_result) -> list:
    refs = []
    for ref in plan.get("basis_refs") or []:
        if isinstance(ref, dict) and ref.get("id"):
            refs.append(ref)
    # 规则依据沿用 AI 结果中已校验的 rule refs
    for ref in basis_service.basis_refs_of(ai_result) if ai_result is not None else []:
        if isinstance(ref, dict) and str(ref.get("type") or "").lower() == "rule" and ref.get("id"):
            if all(str(x.get("id")) != str(ref.get("id")) for x in refs):
                refs.append(ref)
    return refs


@router.post("/{task_id}/results", summary="保存结果版本（operator 本人；PENDING/REJECTED）")
def save_result(
    task_id: int,
    payload: ResultCreateRequest,
    user: dict = Depends(require_operator),
    db: Session = Depends(get_db),
):
    task = task_service.get_visible_task(db, task_id, user)

    ai_task = None
    ai_result = None
    if payload.ai_task_id:
        ai_task = (
            db.query(AiTask)
            .filter(AiTask.id == int(payload.ai_task_id), AiTask.business_task_id == task.id)
            .first()
        )
        if ai_task is None:
            api_error(400, "AI 运行记录不存在或不属于该任务")
        if ai_task.status != AI_SUCCEEDED:
            api_error(400, f"AI 运行尚未成功完成（当前状态 {ai_task.status}），不能采用其结果")
        ai_result = db.query(AiResult).filter(AiResult.ai_task_id == ai_task.id).first()
        if ai_result is None:
            api_error(400, "该 AI 运行没有可用结果")

    content_md = payload.content_md
    if content_md is None and ai_result is not None:
        content_md = ai_result.content_md or ""

    structured = payload.structured
    if structured is None and ai_result is not None:
        structured = basis_service.parse_json(ai_result.structured_json, {})

    # 方案模式：采用指定方案（0 起）生成版本，而不是把整张方案对比表作为版本
    plan_basis_refs = None
    if payload.plan_index is not None:
        if ai_result is None:
            api_error(400, "采用方案时必须指定 ai_task_id")
        full = basis_service.parse_json(ai_result.structured_json, {})
        plans = full.get("plans") if isinstance(full, dict) else None
        if not isinstance(plans, list) or not plans:
            api_error(400, "该 AI 结果不是方案模式结果，无法按方案采用")
        index = int(payload.plan_index)
        if index < 0 or index >= len(plans):
            api_error(400, f"方案序号超出范围（0..{len(plans) - 1}）")
        plan = plans[index]
        if not isinstance(plan, dict):
            api_error(400, "方案数据格式异常")
        content_md = content_md or _plan_markdown(plan, index)
        structured = _plan_version_structured(plan, index)
        plan_basis_refs = _plan_version_basis(plan, ai_result)

    if not (content_md or "").strip() and not structured:
        api_error(400, "请提供结果内容（content_md 或 structured），或指定 ai_task_id 采用 AI 结果")

    basis_refs = None
    source = VERSION_SOURCE_MANUAL
    if ai_result is not None:
        source = VERSION_SOURCE_AI
        basis_refs = basis_service.basis_refs_of(ai_result)
    if plan_basis_refs is not None:
        basis_refs = plan_basis_refs

    version = version_service.create_version(
        db,
        task,
        user_id=int(user.get("id") or 0),
        content_md=content_md or "",
        structured=structured if structured is not None else {},
        basis_refs=basis_refs,
        source=source,
        change_note=payload.change_note or (
            f"采用方案 {int(payload.plan_index) + 1}" if payload.plan_index is not None else None
        ),
    )
    logger.info(
        "保存结果版本 task_id=%s version_no=%s source=%s user=%s",
        task.id,
        version.version_no,
        version.source,
        user.get("username"),
    )
    return success(
        {
            "version_no": version.version_no,
            "version_id": version.id,
            "source": version.source,
            "source_label": version_service.SOURCE_LABELS.get(version.source, version.source),
            "created_at": version.created_at,
        }
    )


@router.get("/{task_id}/versions", summary="版本列表（登录 + 对象级）")
def list_versions(
    task_id: int,
    user: dict = Depends(require_user),
    db: Session = Depends(get_db),
):
    task = task_service.get_visible_task(db, task_id, user)
    versions = version_service.list_versions(db, task.id)
    return success(versions)
