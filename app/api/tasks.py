"""业务任务接口（BUILD_SPEC §4 #3-#7、#10-#11）。

#3  POST   /api/business-tasks
#4  GET    /api/business-tasks
#5  GET    /api/business-tasks/{id}
#6  PUT    /api/business-tasks/{id}
#7  POST   /api/business-tasks/{id}/materials
#10 POST   /api/business-tasks/{id}/submit
#11 POST   /api/business-tasks/{id}/review
（#8/#9 见 results.py，#12-#16 由 S2 的 ai.py 提供）
"""

from __future__ import annotations

import logging
from typing import Optional

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.api import success
from app.auth import require_operator, require_reviewer, require_user
from app.db import get_db
from app.models import ROLE_OPERATOR, ROLE_REVIEWER, TASK_EDITABLE_STATUSES, TASK_PENDING_REVIEW
from app.schemas import (
    BusinessTaskCreate,
    BusinessTaskUpdate,
    MaterialLinkRequest,
    ReviewRequest,
    SubmitRequest,
)
from app.services import task_service

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/business-tasks", tags=["业务任务"])


def _permissions(user: dict, task) -> dict:
    role = (user or {}).get("role_code")
    return {
        "can_edit": role == ROLE_OPERATOR
        and int(task.created_by or 0) == int((user or {}).get("id") or 0)
        and task.status in TASK_EDITABLE_STATUSES,
        "can_review": role == ROLE_REVIEWER and task.status == TASK_PENDING_REVIEW,
    }


@router.post("", summary="创建业务任务（operator）")
def create_business_task(
    payload: BusinessTaskCreate,
    user: dict = Depends(require_operator),
    db: Session = Depends(get_db),
):
    task = task_service.create_task(
        db,
        user,
        title=payload.title,
        goal=payload.goal,
        business_type=payload.business_type,
        params=payload.params,
        material_ids=payload.material_ids,
    )
    data = task_service.serialize_task(db, task, detail=True)
    data.update(_permissions(user, task))
    return success(data)


@router.get("", summary="任务列表（operator 仅本人；reviewer 仅待审/已审）")
def list_business_tasks(
    status: Optional[str] = Query(default=None, description="PENDING/PENDING_REVIEW/REJECTED/COMPLETED"),
    keyword: Optional[str] = Query(default=None, description="任务编号/标题/目标模糊搜索"),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    user: dict = Depends(require_user),
    db: Session = Depends(get_db),
):
    items, total, page, page_size = task_service.list_tasks(
        db, user, status=status, keyword=keyword, page=page, page_size=page_size
    )
    return success({"items": items, "total": total, "page": page, "page_size": page_size})


@router.get("/{task_id}", summary="任务详情（对象级权限）")
def get_business_task(
    task_id: int,
    user: dict = Depends(require_user),
    db: Session = Depends(get_db),
):
    task = task_service.get_visible_task(db, task_id, user)
    data = task_service.serialize_task(db, task, detail=True)
    data.update(_permissions(user, task))
    return success(data)


@router.put("/{task_id}", summary="更新任务（operator 本人；PENDING/REJECTED；乐观锁）")
def update_business_task(
    task_id: int,
    payload: BusinessTaskUpdate,
    user: dict = Depends(require_operator),
    db: Session = Depends(get_db),
):
    task = task_service.get_visible_task(db, task_id, user)
    task = task_service.update_task(
        db,
        task,
        title=payload.title,
        goal=payload.goal,
        params=payload.params,
        business_type=payload.business_type,
        expected_revision=payload.expected_revision,
    )
    data = task_service.serialize_task(db, task, detail=True)
    data.update(_permissions(user, task))
    return success(data)


@router.post("/{task_id}/materials", summary="关联资料（operator 本人）")
def link_task_material(
    task_id: int,
    payload: MaterialLinkRequest,
    user: dict = Depends(require_operator),
    db: Session = Depends(get_db),
):
    task = task_service.get_visible_task(db, task_id, user)
    linked = task_service.link_material(
        db, task, material_id=payload.material_id, link_role=payload.link_role
    )
    return success(linked)


@router.post("/{task_id}/submit", summary="提交审核（operator 本人；提交前重跑依据校验）")
def submit_business_task(
    task_id: int,
    payload: SubmitRequest,
    user: dict = Depends(require_operator),
    db: Session = Depends(get_db),
):
    task = task_service.get_visible_task(db, task_id, user)
    result = task_service.submit_task(
        db, task, version_no=payload.version_no, comment=payload.comment
    )
    return success(result)


@router.post("/{task_id}/review", summary="审核通过/退回（reviewer；仅 PENDING_REVIEW）")
def review_business_task(
    task_id: int,
    payload: ReviewRequest,
    user: dict = Depends(require_reviewer),
    db: Session = Depends(get_db),
):
    task = task_service.get_visible_task(db, task_id, user)
    result = task_service.review_task(
        db,
        task,
        reviewer=user,
        version_no=payload.version_no,
        action=payload.action,
        opinion=payload.opinion,
    )
    return success(result)
