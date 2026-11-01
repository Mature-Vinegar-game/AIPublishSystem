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

    if not (content_md or "").strip() and not structured:
        api_error(400, "请提供结果内容（content_md 或 structured），或指定 ai_task_id 采用 AI 结果")

    basis_refs = None
    source = VERSION_SOURCE_MANUAL
    if ai_result is not None:
        source = VERSION_SOURCE_AI
        basis_refs = basis_service.basis_refs_of(ai_result)

    version = version_service.create_version(
        db,
        task,
        user_id=int(user.get("id") or 0),
        content_md=content_md or "",
        structured=structured if structured is not None else {},
        basis_refs=basis_refs,
        source=source,
        change_note=payload.change_note,
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
