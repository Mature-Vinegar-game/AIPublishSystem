"""版本服务：结果版本只增不改，已提交/已通过版本冻结。

规则（BUILD_SPEC §4）：
- version_no 连续递增、不跳号、不复用；UNIQUE(business_task_id, version_no) 兜底。
- 只有 PENDING / REJECTED 状态可新增版本；PENDING_REVIEW / COMPLETED → 409。
- AI 重跑只新增 ai_result，不自动提版；人工"采用/保存"才生成版本（source=AI/MANUAL）。
"""

from __future__ import annotations

import logging
from typing import Optional

from fastapi import HTTPException
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.db import now_iso
from app.models import (
    TASK_EDITABLE_STATUSES,
    VERSION_SOURCE_AI,
    VERSION_SOURCE_MANUAL,
    BusinessTask,
    ResultVersion,
    User,
)
from app.services import basis_service

logger = logging.getLogger(__name__)

SOURCE_LABELS = {VERSION_SOURCE_AI: "AI 生成", VERSION_SOURCE_MANUAL: "人工保存"}


def _conflict(message: str, data: Optional[dict] = None) -> HTTPException:
    detail: dict = {"code": 409, "message": message}
    if data:
        detail["data"] = data
    return HTTPException(status_code=409, detail=detail)


def _bad_request(message: str) -> HTTPException:
    return HTTPException(status_code=400, detail={"code": 400, "message": message})


def freeze_guard(task: BusinessTask) -> None:
    """状态门禁：已提交/已完成任务的版本被冻结。"""
    if task.status not in TASK_EDITABLE_STATUSES:
        from app.models import TASK_STATUS_LABELS

        raise _conflict(
            f"任务当前状态为「{TASK_STATUS_LABELS.get(task.status, task.status)}」，"
            "已提交/已通过的版本已冻结，不能再保存新版本（请退回后再修改）"
        )


def next_version_no(db: Session, business_task_id: int) -> int:
    row = (
        db.query(ResultVersion.version_no)
        .filter(ResultVersion.business_task_id == business_task_id)
        .order_by(ResultVersion.version_no.desc())
        .first()
    )
    return 1 if row is None or row[0] is None else int(row[0]) + 1


def get_version(db: Session, business_task_id: int, version_no: int) -> Optional[ResultVersion]:
    return (
        db.query(ResultVersion)
        .filter(
            ResultVersion.business_task_id == business_task_id,
            ResultVersion.version_no == int(version_no),
        )
        .first()
    )


def latest_version(db: Session, business_task_id: int) -> Optional[ResultVersion]:
    return (
        db.query(ResultVersion)
        .filter(ResultVersion.business_task_id == business_task_id)
        .order_by(ResultVersion.version_no.desc())
        .first()
    )


def count_versions(db: Session, business_task_id: int) -> int:
    return (
        db.query(ResultVersion)
        .filter(ResultVersion.business_task_id == business_task_id)
        .count()
    )


def create_version(
    db: Session,
    task: BusinessTask,
    user_id: int,
    content_md: str = "",
    structured: Optional[dict] = None,
    basis_refs: Optional[list] = None,
    source: str = VERSION_SOURCE_MANUAL,
    change_note: Optional[str] = None,
) -> ResultVersion:
    """新增一个结果版本（不覆盖历史），返回已持久化的版本对象。"""
    freeze_guard(task)
    if source not in (VERSION_SOURCE_AI, VERSION_SOURCE_MANUAL):
        source = VERSION_SOURCE_MANUAL

    version_no = next_version_no(db, task.id)
    version = ResultVersion(
        business_task_id=task.id,
        version_no=version_no,
        source=source,
        content_md=content_md or "",
        structured_json=basis_service.dump_json(structured, "{}") if structured is not None else None,
        basis_refs_json=basis_service.dump_json(basis_refs, "[]") if basis_refs is not None else None,
        change_note=(change_note or "")[:500] or None,
        created_by=user_id,
        created_at=now_iso(),
    )
    db.add(version)
    task.updated_at = now_iso()
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        logger.warning("版本写入冲突 task_id=%s version_no=%s", task.id, version_no)
        raise _conflict("版本号冲突，请刷新后重试")
    db.refresh(version)
    return version


def _creator_name(db: Session, user_id: int, cache: dict) -> str:
    if not user_id:
        return "系统"
    if user_id in cache:
        return cache[user_id]
    user = db.query(User).filter(User.id == user_id).first()
    name = (user.display_name or user.username) if user else f"用户{user_id}"
    cache[user_id] = name
    return name


def version_to_dict(db: Session, version: ResultVersion, user_cache: Optional[dict] = None) -> dict:
    """版本序列化（含 content_md / structured / 依据 / 操作人）。"""
    cache = user_cache if user_cache is not None else {}
    return {
        "id": version.id,
        "business_task_id": version.business_task_id,
        "version_no": version.version_no,
        "source": version.source,
        "source_label": SOURCE_LABELS.get(version.source, version.source),
        "content_md": version.content_md or "",
        "structured": basis_service.parse_json(version.structured_json, {}),
        "basis_refs": basis_service.enrich_basis_refs(db, version.basis_refs_json),
        "change_note": version.change_note or "",
        "created_by": version.created_by,
        "created_by_name": _creator_name(db, version.created_by, cache),
        "created_at": version.created_at,
    }


def list_versions(db: Session, business_task_id: int, newest_first: bool = True) -> list:
    order = ResultVersion.version_no.desc() if newest_first else ResultVersion.version_no.asc()
    rows = (
        db.query(ResultVersion)
        .filter(ResultVersion.business_task_id == business_task_id)
        .order_by(order)
        .all()
    )
    cache: dict = {}
    return [version_to_dict(db, row, cache) for row in rows]


__all__ = [
    "SOURCE_LABELS",
    "freeze_guard",
    "next_version_no",
    "get_version",
    "latest_version",
    "count_versions",
    "create_version",
    "version_to_dict",
    "list_versions",
]
