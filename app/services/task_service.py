"""任务服务：业务任务 CRUD、对象级权限、状态机、序列化。

权限口径（BUILD_SPEC §4/§7）：
- operator 只能看/改本人任务；reviewer 只能看待审/已审任务（PENDING_REVIEW/REJECTED/COMPLETED）。
- 对象不属于本人或不在审核范围 → 404（不泄露存在性）；角色不足 → 403（由依赖层处理）。
- 状态机：PENDING → PENDING_REVIEW → COMPLETED；PENDING_REVIEW → REJECTED → PENDING_REVIEW。
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Iterable, List, Optional, Sequence, Tuple

from fastapi import HTTPException
from sqlalchemy import or_
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.db import now_iso
from app.models import (
    AI_STATUS_LABELS,
    MATERIAL_TYPE_LABELS,
    REVIEW_APPROVE,
    REVIEW_REJECT,
    ROLE_OPERATOR,
    ROLE_REVIEWER,
    TASK_COMPLETED,
    TASK_EDITABLE_STATUSES,
    TASK_PENDING,
    TASK_PENDING_REVIEW,
    TASK_REJECTED,
    TASK_STATUS_LABELS,
    TASK_STATUSES,
    AiResult,
    AiTask,
    BusinessTask,
    Material,
    ResultVersion,
    ReviewRecord,
    TaskMaterial,
    ToolCallRecord,
    User,
)
from app.services import basis_service, version_service

logger = logging.getLogger(__name__)

# reviewer 可见（= 已提交审核及之后的状态）
REVIEWER_VISIBLE_STATUSES = (TASK_PENDING_REVIEW, TASK_REJECTED, TASK_COMPLETED)


# ============================ 通用 ============================
def status_label(status: str) -> str:
    return TASK_STATUS_LABELS.get(status, status)


def _bad_request(message: str) -> HTTPException:
    return HTTPException(status_code=400, detail={"code": 400, "message": message})


def _not_found(message: str = "对象不存在或不在你的权限范围内") -> HTTPException:
    return HTTPException(status_code=404, detail={"code": 404, "message": message})


def _conflict(message: str, data: Optional[dict] = None) -> HTTPException:
    detail: dict = {"code": 409, "message": message}
    if data:
        detail["data"] = data
    return HTTPException(status_code=409, detail=detail)


def user_display(db: Session, user_id: Optional[int]) -> str:
    if not user_id:
        return "系统"
    user = db.query(User).filter(User.id == user_id).first()
    if user is None:
        return f"用户{user_id}"
    return user.display_name or user.username


def parse_params(task: BusinessTask) -> dict:
    return basis_service.as_dict(getattr(task, "params_json", None))


def normalize_params(params: Optional[dict]) -> dict:
    """规范化任务参数：去空格、工艺"无"→空、数字类型归一（供工具层直接使用）。"""
    if not isinstance(params, dict):
        return {}
    result = dict(params)

    for key in ("format", "paper_type", "binding", "book_title", "note"):
        if isinstance(result.get(key), str):
            result[key] = result[key].strip()

    process = result.get("process")
    if isinstance(process, str):
        process = process.strip()
        result["process"] = "" if process in ("", "无", "none", "None", "null") else process

    print_sheet = result.get("print_sheet")
    if isinstance(print_sheet, str) and print_sheet.strip():
        try:
            value = float(print_sheet)
            result["print_sheet"] = int(value) if value.is_integer() else value
        except ValueError:
            pass

    for key in ("qty", "color_pages"):
        value = result.get(key)
        if isinstance(value, str) and value.strip():
            try:
                result[key] = int(float(value))
            except ValueError:
                pass
    return result


# ============================ 序列化 ============================
def ai_task_to_dict(db: Session, ai_task: Optional[AiTask]) -> Optional[dict]:
    if ai_task is None:
        return None
    result = db.query(AiResult.id).filter(AiResult.ai_task_id == ai_task.id).first()
    counters = basis_service.as_dict(ai_task.counters_json)
    return {
        "id": ai_task.id,
        "business_task_id": ai_task.business_task_id,
        "status": ai_task.status,
        "status_label": AI_STATUS_LABELS.get(ai_task.status, ai_task.status),
        "model_mode": ai_task.model_mode,
        "model_name": ai_task.model_name,
        "counters": counters,
        "error_code": ai_task.error_code,
        "error_message": ai_task.error_message,
        "started_at": ai_task.started_at,
        "finished_at": ai_task.finished_at,
        "created_by": ai_task.created_by,
        "created_at": ai_task.created_at,
        "result_id": result[0] if result else None,
        "has_result": bool(result),
    }


def ai_result_to_dict(db: Session, result: Optional[AiResult]) -> Optional[dict]:
    if result is None:
        return None
    return {
        "id": result.id,
        "ai_task_id": result.ai_task_id,
        "business_task_id": result.business_task_id,
        "content_md": result.content_md or "",
        "structured": basis_service.parse_json(result.structured_json, {}),
        "basis_refs": basis_service.enrich_basis_refs(db, result.basis_refs_json),
        "basis_ok": bool(int(result.basis_ok or 0)),
        "uncertain_items": basis_service.as_list(result.uncertain_items_json),
        "missing_data": basis_service.as_list(result.missing_data_json),
        "created_at": result.created_at,
    }


def linked_materials(db: Session, task_id: int) -> List[dict]:
    rows = (
        db.query(TaskMaterial, Material)
        .join(Material, Material.id == TaskMaterial.material_id)
        .filter(TaskMaterial.task_id == task_id)
        .order_by(TaskMaterial.id.asc())
        .all()
    )
    items: List[dict] = []
    for link, material in rows:
        items.append(
            {
                "id": link.id,
                "material_id": material.id,
                "material_no": material.material_no,
                "title": material.title,
                "type": material.type,
                "type_label": MATERIAL_TYPE_LABELS.get(material.type, material.type),
                "source": material.source,
                "version_no": material.version_no,
                "link_role": link.link_role,
                "enabled": bool(int(material.enabled or 0)),
                "content": basis_service.parse_json(material.content_json, {}),
                "created_at": link.created_at,
            }
        )
    return items


def get_latest_ai_task(db: Session, business_task_id: int) -> Optional[AiTask]:
    return (
        db.query(AiTask)
        .filter(AiTask.business_task_id == business_task_id)
        .order_by(AiTask.id.desc())
        .first()
    )


def get_latest_ai_result(db: Session, business_task_id: int) -> Optional[AiResult]:
    return (
        db.query(AiResult)
        .filter(AiResult.business_task_id == business_task_id)
        .order_by(AiResult.id.desc())
        .first()
    )


def tool_calls_for_task(
    db: Session,
    business_task_id: int,
    ai_task_id: Optional[int] = None,
) -> List[dict]:
    query = db.query(ToolCallRecord).filter(ToolCallRecord.business_task_id == business_task_id)
    if ai_task_id is not None:
        query = query.filter(ToolCallRecord.ai_task_id == ai_task_id)
    rows = query.order_by(ToolCallRecord.ai_task_id.asc(), ToolCallRecord.seq.asc()).all()
    items: List[dict] = []
    for row in rows:
        items.append(
            {
                "id": row.id,
                "ai_task_id": row.ai_task_id,
                "business_task_id": row.business_task_id,
                "seq": row.seq,
                "tool_name": row.tool_name,
                "tool_type": row.tool_type,
                "args": basis_service.parse_json(row.args_json, {}),
                "args_json": row.args_json,
                "result_summary": row.result_summary or "",
                "source_ids": basis_service.as_list(row.source_ids_json),
                "source_ids_json": row.source_ids_json or "[]",
                "status": row.status,
                "error_code": row.error_code,
                "latency_ms": row.latency_ms,
                "created_at": row.created_at,
            }
        )
    return items


def reviews_for_task(db: Session, business_task_id: int) -> List[dict]:
    rows = (
        db.query(ReviewRecord)
        .filter(ReviewRecord.business_task_id == business_task_id)
        .order_by(ReviewRecord.id.desc())
        .all()
    )
    version_nos = {}
    version_ids = [row.version_id for row in rows if row.version_id]
    if version_ids:
        for version in (
            db.query(ResultVersion).filter(ResultVersion.id.in_(version_ids)).all()
        ):
            version_nos[version.id] = version.version_no
    items: List[dict] = []
    for row in rows:
        items.append(
            {
                "id": row.id,
                "business_task_id": row.business_task_id,
                "version_id": row.version_id,
                "version_no": version_nos.get(row.version_id),
                "action": row.action,
                "action_label": "审核通过" if row.action == REVIEW_APPROVE else "退回修改",
                "opinion": row.opinion or "",
                "reviewer_id": row.reviewer_id,
                "reviewer_name": user_display(db, row.reviewer_id),
                "created_at": row.created_at,
            }
        )
    return items


def serialize_task(db: Session, task: BusinessTask, detail: bool = False) -> dict:
    latest_version = version_service.latest_version(db, task.id)
    latest_ai = get_latest_ai_task(db, task.id)
    data = {
        "id": task.id,
        "task_no": task.task_no,
        "business_type": task.business_type,
        "title": task.title,
        "goal": task.goal or "",
        "params": parse_params(task),
        "status": task.status,
        "status_label": status_label(task.status),
        "revision": task.revision,
        "created_by": task.created_by,
        "created_by_name": user_display(db, task.created_by),
        "created_at": task.created_at,
        "updated_at": task.updated_at,
        "versions_count": version_service.count_versions(db, task.id),
        "latest_version_no": latest_version.version_no if latest_version else None,
        "latest_ai": ai_task_to_dict(db, latest_ai),
        "latest_ai_status": latest_ai.status if latest_ai else None,
        "latest_ai_status_label": AI_STATUS_LABELS.get(latest_ai.status, latest_ai.status) if latest_ai else None,
    }
    if detail:
        data["linked_materials"] = linked_materials(db, task.id)
        data["latest_result"] = ai_result_to_dict(db, get_latest_ai_result(db, task.id))
        data["latest_version"] = (
            version_service.version_to_dict(db, latest_version) if latest_version else None
        )
    return data


# ============================ 权限 ============================
def can_view(user: Optional[dict], task: BusinessTask) -> bool:
    if not user:
        return False
    if user.get("role_code") == ROLE_OPERATOR:
        return int(task.created_by or 0) == int(user.get("id") or 0)
    return task.status in REVIEWER_VISIBLE_STATUSES


def get_visible_task(db: Session, task_id: int, user: Optional[dict]) -> BusinessTask:
    """对象级读取权限：不属于本人/不在审核范围一律 404。"""
    task = db.query(BusinessTask).filter(BusinessTask.id == int(task_id)).first()
    if task is None or not can_view(user, task):
        raise _not_found("任务不存在或不在你的权限范围内")
    return task


def ensure_editable(task: BusinessTask) -> None:
    """operator 写操作的状态门禁：只有 PENDING / REJECTED 可编辑。"""
    if task.status not in TASK_EDITABLE_STATUSES:
        raise _conflict(
            f"任务当前状态为「{status_label(task.status)}」，不允许该操作"
            "（仅待处理/已退回状态可编辑、保存结果或提交）"
        )


def _latest_totals(db: Session, task_ids: list[int]) -> dict:
    """批量取任务最新 AI 结果的总成本，用于列表展示/金额排序。"""
    if not task_ids:
        return {}
    rows = (
        db.query(AiResult)
        .filter(AiResult.business_task_id.in_(list(task_ids)))
        .order_by(AiResult.business_task_id, AiResult.id.asc())
        .all()
    )
    totals: dict = {}
    for row in rows:
        structured = basis_service.parse_json(row.structured_json, {})
        if isinstance(structured, dict) and structured.get("total") is not None:
            totals[row.business_task_id] = structured.get("total")
    return totals


def list_tasks(
    db: Session,
    user: dict,
    status: Optional[str] = None,
    keyword: Optional[str] = None,
    page: int = 1,
    page_size: int = 20,
    sort: Optional[str] = None,
) -> Tuple[List[dict], int, int, int]:
    """按角色过滤的任务列表，返回 (items, total, page, page_size)。"""
    query = db.query(BusinessTask)
    role = (user or {}).get("role_code")
    if role == ROLE_OPERATOR:
        query = query.filter(BusinessTask.created_by == int(user.get("id") or 0))
    elif role == ROLE_REVIEWER:
        query = query.filter(BusinessTask.status.in_(REVIEWER_VISIBLE_STATUSES))
    else:
        query = query.filter(BusinessTask.id < 0)

    status = (status or "").strip().upper()
    if status and status != "ALL" and status in TASK_STATUSES:
        query = query.filter(BusinessTask.status == status)

    keyword = (keyword or "").strip()
    if keyword:
        like = f"%{keyword}%"
        query = query.filter(
            or_(
                BusinessTask.task_no.like(like),
                BusinessTask.title.like(like),
                BusinessTask.goal.like(like),
            )
        )

    total = query.count()
    page = max(1, int(page or 1))
    page_size = max(1, min(100, int(page_size or 20)))
    sort = (sort or "updated_desc").strip()
    latest_map: dict = {}
    if sort == "amount_desc":
        # 任务量级在演示场景较小（几十到几百），按最新结果金额排序即可
        all_rows = query.all()
        latest_map = _latest_totals(db, [row.id for row in all_rows])
        all_rows.sort(key=lambda row: latest_map.get(row.id, -1), reverse=True)
        total = len(all_rows)
        rows = all_rows[(page - 1) * page_size : page * page_size]
    else:
        if sort == "created_desc":
            order = (BusinessTask.created_at.desc(), BusinessTask.id.desc())
        elif sort == "task_no_asc":
            order = (BusinessTask.task_no.asc(), BusinessTask.id.asc())
        elif sort == "updated_asc":
            order = (BusinessTask.updated_at.asc(), BusinessTask.id.asc())
        else:
            sort = "updated_desc"
            order = (BusinessTask.updated_at.desc(), BusinessTask.id.desc())
        rows = (
            query.order_by(*order)
            .offset((page - 1) * page_size)
            .limit(page_size)
            .all()
        )
        latest_map = _latest_totals(db, [row.id for row in rows])

    items = []
    for row in rows:
        item = serialize_task(db, row)
        item["latest_total"] = latest_map.get(row.id)
        items.append(item)
    return items, total, page, page_size


# ============================ 写操作 ============================
def generate_task_no(db: Session) -> str:
    prefix = f"BT{datetime.now().strftime('%Y%m%d')}"
    total = db.query(BusinessTask).filter(BusinessTask.task_no.like(f"{prefix}%")).count()
    seq = total + 1
    while True:
        candidate = f"{prefix}-{seq:04d}"
        exists = db.query(BusinessTask.id).filter(BusinessTask.task_no == candidate).first()
        if not exists:
            return candidate
        seq += 1


def create_task(
    db: Session,
    user: dict,
    title: str,
    goal: str,
    business_type: str = "print_cost",
    params: Optional[dict] = None,
    material_ids: Optional[Sequence[int]] = None,
) -> BusinessTask:
    title = (title or "").strip()
    goal = (goal or "").strip()
    if not title:
        raise _bad_request("任务标题不能为空")
    if not goal:
        raise _bad_request("任务目标不能为空")
    if params is not None and not isinstance(params, dict):
        raise _bad_request("params 必须是 JSON 对象")

    material_ids = _unique_ints(material_ids or [])
    materials: List[Material] = []
    if material_ids:
        materials = db.query(Material).filter(Material.id.in_(material_ids)).all()
        found = {material.id for material in materials}
        missing = [str(mid) for mid in material_ids if mid not in found]
        if missing:
            raise _bad_request(f"关联资料不存在：id={','.join(missing)}")

    task = BusinessTask(
        task_no=generate_task_no(db),
        business_type=(business_type or "print_cost").strip() or "print_cost",
        title=title,
        goal=goal,
        params_json=basis_service.dump_json(normalize_params(params), "{}"),
        status=TASK_PENDING,
        revision=1,
        created_by=int(user.get("id") or 0),
        created_at=now_iso(),
        updated_at=now_iso(),
    )
    db.add(task)
    try:
        db.flush()
        for material in materials:
            db.add(
                TaskMaterial(
                    task_id=task.id,
                    material_id=material.id,
                    link_role="related",
                    created_at=now_iso(),
                )
            )
        db.commit()
    except IntegrityError:
        db.rollback()
        logger.exception("创建任务冲突 title=%s", title)
        raise _conflict("任务编号冲突，请重试")
    db.refresh(task)
    return task


def update_task(
    db: Session,
    task: BusinessTask,
    title: Optional[str] = None,
    goal: Optional[str] = None,
    params: Optional[dict] = None,
    business_type: Optional[str] = None,
    expected_revision: Optional[int] = None,
) -> BusinessTask:
    ensure_editable(task)
    if expected_revision is not None and int(expected_revision) != int(task.revision or 1):
        raise _conflict(
            f"任务已被其他人修改（当前修订号 {task.revision}，提交的期望修订号 {expected_revision}），请刷新后重试"
        )
    if params is not None and not isinstance(params, dict):
        raise _bad_request("params 必须是 JSON 对象")

    changed = False
    if title is not None and title.strip() and title.strip() != task.title:
        task.title = title.strip()
        changed = True
    if goal is not None and goal.strip() and goal.strip() != task.goal:
        task.goal = goal.strip()
        changed = True
    if business_type is not None and business_type.strip() and business_type.strip() != task.business_type:
        task.business_type = business_type.strip()
        changed = True
    if params is not None:
        merged = parse_params(task)
        merged.update(params)
        task.params_json = basis_service.dump_json(normalize_params(merged), "{}")
        changed = True

    if not changed:
        raise _bad_request("没有需要更新的字段")

    task.revision = int(task.revision or 1) + 1
    task.updated_at = now_iso()
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise _conflict("更新冲突，请刷新后重试")
    db.refresh(task)
    return task


def link_material(
    db: Session,
    task: BusinessTask,
    material_id: int,
    link_role: str = "related",
) -> List[dict]:
    ensure_editable(task)
    material = db.query(Material).filter(Material.id == int(material_id)).first()
    if material is None:
        raise _not_found("资料不存在")
    if not int(material.enabled or 0):
        raise _bad_request("该资料已停用，不能关联")
    exists = (
        db.query(TaskMaterial)
        .filter(TaskMaterial.task_id == task.id, TaskMaterial.material_id == material.id)
        .first()
    )
    if exists is not None:
        raise _conflict("该资料已关联此任务，请勿重复关联")
    db.add(
        TaskMaterial(
            task_id=task.id,
            material_id=material.id,
            link_role=(link_role or "related").strip()[:32] or "related",
            created_at=now_iso(),
        )
    )
    task.revision = int(task.revision or 1) + 1
    task.updated_at = now_iso()
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise _conflict("该资料已关联此任务，请勿重复关联")
    return linked_materials(db, task.id)


def submit_task(
    db: Session,
    task: BusinessTask,
    version_no: Optional[int] = None,
    comment: Optional[str] = None,
) -> dict:
    """提交审核：状态 PENDING/REJECTED → PENDING_REVIEW；提交前重跑依据校验。"""
    if task.status not in TASK_EDITABLE_STATUSES:
        raise _conflict(
            f"任务当前状态为「{status_label(task.status)}」，不能提交审核"
            "（请勿重复提交；被退回后修改再提交）"
        )
    latest = version_service.latest_version(db, task.id)
    if latest is None:
        raise _bad_request("请先保存至少一个结果版本（可先运行 AI 或保存人工结果）再提交审核")
    target_no = int(version_no) if version_no else int(latest.version_no)
    version = version_service.get_version(db, task.id, target_no)
    if version is None:
        raise _bad_request(f"版本 v{target_no} 不存在")

    problems = basis_service.check_submit_basis(db, version, task)
    if problems:
        raise _conflict(
            f"提交校验未通过：版本 v{target_no} 有 {len(problems)} 条依据已失效，请重新运行 AI 或更新依据后重试",
            data={"error_code": "BASIS_MISMATCH", "version_no": target_no, "problems": problems},
        )

    task.status = TASK_PENDING_REVIEW
    task.updated_at = now_iso()
    db.commit()
    db.refresh(task)
    logger.info("任务提交审核 task_id=%s task_no=%s version_no=%s", task.id, task.task_no, target_no)
    return {
        "status": task.status,
        "status_label": status_label(task.status),
        "version_no": target_no,
        "comment": comment or "",
        "submitted_at": task.updated_at,
    }


def review_task(
    db: Session,
    task: BusinessTask,
    reviewer: dict,
    version_no: Optional[int] = None,
    action: str = REVIEW_APPROVE,
    opinion: Optional[str] = None,
) -> dict:
    """审核：仅 PENDING_REVIEW；APPROVE → COMPLETED，REJECT → REJECTED（需意见）。"""
    if task.status != TASK_PENDING_REVIEW:
        raise _conflict(
            f"任务当前状态为「{status_label(task.status)}」，只有待审核任务可审核"
        )
    action = (action or "").strip().upper()
    if action not in (REVIEW_APPROVE, REVIEW_REJECT):
        raise _bad_request("action 只能是 APPROVE 或 REJECT")
    opinion = (opinion or "").strip()
    if action == REVIEW_REJECT and not opinion:
        raise _bad_request("退回时必须填写审核意见")

    if version_no:
        version = version_service.get_version(db, task.id, int(version_no))
    else:
        version = version_service.latest_version(db, task.id)
    if version is None:
        raise _bad_request("待审核版本不存在，无法审核")

    if int(task.created_by or 0) == int(reviewer.get("id") or 0):
        raise HTTPException(
            status_code=403,
            detail={"code": 403, "message": "不能审核自己创建的任务"},
        )

    record = ReviewRecord(
        business_task_id=task.id,
        version_id=version.id,
        action=action,
        opinion=opinion or "",
        reviewer_id=int(reviewer.get("id") or 0),
        created_at=now_iso(),
    )
    db.add(record)
    task.status = TASK_COMPLETED if action == REVIEW_APPROVE else TASK_REJECTED
    task.updated_at = now_iso()
    db.commit()
    db.refresh(record)
    db.refresh(task)
    logger.info(
        "任务审核 task_id=%s action=%s reviewer=%s version_no=%s",
        task.id,
        action,
        reviewer.get("username"),
        version.version_no,
    )
    return {
        "status": task.status,
        "status_label": status_label(task.status),
        "action": action,
        "version_no": version.version_no,
        "review_id": record.id,
        "opinion": opinion,
        "reviewed_at": record.created_at,
    }


def _unique_ints(values: Iterable) -> List[int]:
    seen = set()
    result: List[int] = []
    for value in values:
        try:
            number = int(value)
        except (TypeError, ValueError):
            continue
        if number > 0 and number not in seen:
            seen.add(number)
            result.append(number)
    return result


__all__ = [
    "REVIEWER_VISIBLE_STATUSES",
    "status_label",
    "user_display",
    "parse_params",
    "normalize_params",
    "ai_task_to_dict",
    "ai_result_to_dict",
    "linked_materials",
    "get_latest_ai_task",
    "get_latest_ai_result",
    "tool_calls_for_task",
    "reviews_for_task",
    "serialize_task",
    "can_view",
    "get_visible_task",
    "ensure_editable",
    "list_tasks",
    "generate_task_no",
    "create_task",
    "update_task",
    "link_material",
    "submit_task",
    "review_task",
]
