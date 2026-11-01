"""AI/Agent 相关接口（BUILD_SPEC §4 #12-#16）。

接口：
- POST   /api/business-tasks/{id}/ai-tasks   创建 AI 运行（202）
- GET    /api/ai-tasks/{id}                  查询运行状态/计数器
- GET    /api/ai-tasks/{id}/tool-calls        执行记录（全字段）
- GET    /api/ai-tasks/{id}/result            最终结果
- POST   /api/ai-tasks/{id}/retry             失败重试（202，受 MAX_AI_RUNS 限制）

统一返回 ``{"code":0,"message":"ok","data":...}``；错误抛 HTTPException（detail 为对象，
由 S1 的全局异常处理器包装）。对象级校验在接口内用 SessionLocal 查
``business_task.created_by``；reviewer 仅可查看待审/已审任务。
"""

from __future__ import annotations

import json
from typing import Any, Iterator

from fastapi import APIRouter, Body, Depends, HTTPException
from sqlalchemy.orm import Session

from app.agent.orchestrator import count_ai_runs, start_ai_task
from app.api import success
from app.auth import require_operator, require_user
from app.config import settings
from app.db import SessionLocal
from app.models import AiResult, AiTask, BusinessTask, ToolCallRecord


router = APIRouter(prefix="/api", tags=["ai"])

FAULT_VALUES = ("tool_timeout", "model_error", "invalid_tool_args")
EDITABLE_STATUSES = ("PENDING", "REJECTED")
REVIEWER_VIEW_STATUSES = ("PENDING_REVIEW", "REJECTED", "COMPLETED")


# ---------------------------------------------------------------------------
# 基础依赖 / 工具
# ---------------------------------------------------------------------------


def get_db() -> Iterator[Session]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def _setting(name: str, default: Any = None) -> Any:
    if hasattr(settings, name):
        return getattr(settings, name)
    upper = name.upper()
    if hasattr(settings, upper):
        return getattr(settings, upper)
    return default


def _json_loads(value: Any, default: Any = None) -> Any:
    if value is None:
        return default
    if isinstance(value, (dict, list)):
        return value
    try:
        return json.loads(value)
    except (TypeError, ValueError):
        return default


def _user_id(user: Any) -> int | None:
    if isinstance(user, dict):
        value = user.get("id") or user.get("user_id")
    else:
        value = getattr(user, "id", None)
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _user_role(user: Any) -> str:
    if isinstance(user, dict):
        value = user.get("role_code") or user.get("role")
    else:
        value = getattr(user, "role_code", None)
    return str(value or "")


def _error(status_code: int, message: str, data: Any = None) -> None:
    """抛统一错误；detail.code 必须是数字（S1 全局处理器会 int() 转换）。"""
    detail: dict[str, Any] = {"code": status_code, "message": message}
    if data is not None:
        detail["data"] = data
    raise HTTPException(status_code=status_code, detail=detail)


def _can_view(task: BusinessTask, user: Any) -> bool:
    uid = _user_id(user)
    owner_id = getattr(task, "created_by", None)
    if uid is not None and owner_id is not None and int(owner_id) == uid:
        return True
    role = _user_role(user)
    if role == "reviewer" and str(getattr(task, "status", "") or "") in REVIEWER_VIEW_STATUSES:
        return True
    return False


def _get_ai_task_for_view(db: Session, ai_task_id: int, user: Any) -> tuple[AiTask, BusinessTask]:
    ai_task = db.get(AiTask, ai_task_id)
    if ai_task is None:
        _error(404, "AI 任务不存在")
    business_task = db.get(BusinessTask, ai_task.business_task_id)
    if business_task is None or not _can_view(business_task, user):
        _error(404, "AI 任务不存在或无权访问")
    return ai_task, business_task


def _ensure_operator_owner(db: Session, business_task_id: int, user: Any) -> BusinessTask:
    if _user_role(user) != "operator":
        _error(403, "仅业务操作员可发起 AI 测算")
    task = db.get(BusinessTask, business_task_id)
    uid = _user_id(user)
    if task is None or uid is None or int(getattr(task, "created_by", 0) or 0) != uid:
        _error(404, "业务任务不存在或无权操作")
    return task


def _max_ai_runs() -> int:
    try:
        value = int(_setting("max_ai_runs", 10))
    except (TypeError, ValueError):
        value = 10
    return value if value > 0 else 10


def _ensure_run_limit(business_task_id: int) -> None:
    limit = _max_ai_runs()
    if count_ai_runs(business_task_id) >= limit:
        _error(
            429,
            f"该业务任务的 AI 运行次数已达上限（{limit} 次），请复用已有结果或联系管理员",
        )


def _parse_fault(raw: Any) -> str | None:
    if raw is None or raw == "":
        return None
    if not isinstance(raw, str):
        _error(400, "fault 必须是字符串")
    fault = raw.strip()
    if fault not in FAULT_VALUES:
        _error(400, f"fault 仅支持：{'/'.join(FAULT_VALUES)}")
    app_env = str(_setting("app_env", "demo") or "").strip().lower()
    if app_env != "demo":
        _error(400, "故障注入仅 APP_ENV=demo 时可用")
    return fault


def _note_from_payload(payload: Any) -> str | None:
    if payload is None:
        return None
    if not isinstance(payload, dict):
        _error(400, "请求体必须是 JSON 对象")
    note = payload.get("note")
    if note is None or note == "":
        return None
    if not isinstance(note, str):
        _error(400, "note 必须是字符串")
    note = note.strip()
    if len(note) > 500:
        _error(400, "note 不能超过 500 个字符")
    return note or None


# ---------------------------------------------------------------------------
# 12. 创建 AI 运行
# ---------------------------------------------------------------------------


@router.post("/business-tasks/{business_task_id}/ai-tasks", status_code=202)
def create_ai_task(
    business_task_id: int,
    payload: dict[str, Any] | None = Body(default=None),
    user: dict = Depends(require_operator),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    task = _ensure_operator_owner(db, business_task_id, user)
    if str(getattr(task, "status", "") or "") not in EDITABLE_STATUSES:
        _error(409, "仅 PENDING/REJECTED 状态的业务任务可运行 AI 测算")

    note = _note_from_payload(payload)
    fault = _parse_fault((payload or {}).get("fault"))
    _ensure_run_limit(business_task_id)

    # 释放读事务，避免与后台写入抢 SQLite 锁
    uid = _user_id(user)
    db.rollback()
    try:
        ai_task_id = start_ai_task(business_task_id, uid, fault=fault, note=note)
    except ValueError as exc:
        _error(404, str(exc))

    return success(
        {
            "ai_task_id": ai_task_id,
            "status": "PENDING",
            "model_mode": str(_setting("llm_mode", "mock") or "mock").lower(),
        }
    )


# ---------------------------------------------------------------------------
# 13. 查询 AI 运行状态
# ---------------------------------------------------------------------------


@router.get("/ai-tasks/{ai_task_id}")
def get_ai_task(
    ai_task_id: int,
    user: dict = Depends(require_user),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    ai_task, business_task = _get_ai_task_for_view(db, ai_task_id, user)
    result = (
        db.query(AiResult)
        .filter(AiResult.ai_task_id == ai_task.id)
        .order_by(AiResult.id.desc())
        .first()
    )
    counters = _json_loads(ai_task.counters_json, {})
    return success(
        {
            "ai_task_id": ai_task.id,
            "business_task_id": ai_task.business_task_id,
            "business_task_no": getattr(business_task, "task_no", None),
            "status": ai_task.status,
            "model_mode": ai_task.model_mode,
            "model_name": ai_task.model_name,
            "counters": counters if isinstance(counters, dict) else {},
            "error_code": ai_task.error_code,
            "error_message": ai_task.error_message,
            "result_id": result.id if result is not None else None,
            "started_at": ai_task.started_at,
            "finished_at": ai_task.finished_at,
            "created_at": ai_task.created_at,
        }
    )


# ---------------------------------------------------------------------------
# 14. 执行记录
# ---------------------------------------------------------------------------


@router.get("/ai-tasks/{ai_task_id}/tool-calls")
def get_ai_task_tool_calls(
    ai_task_id: int,
    user: dict = Depends(require_user),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    ai_task, _business_task = _get_ai_task_for_view(db, ai_task_id, user)
    rows = (
        db.query(ToolCallRecord)
        .filter(ToolCallRecord.ai_task_id == ai_task.id)
        .order_by(ToolCallRecord.seq, ToolCallRecord.id)
        .all()
    )
    records = []
    for row in rows:
        args = _json_loads(row.args_json, {})
        source_ids = _json_loads(row.source_ids_json, [])
        records.append(
            {
                "id": row.id,
                "ai_task_id": row.ai_task_id,
                "business_task_id": row.business_task_id,
                "seq": row.seq,
                "tool_name": row.tool_name,
                "tool_type": row.tool_type,
                "args": args if isinstance(args, (dict, list)) else {},
                "args_json": row.args_json,
                "result_summary": row.result_summary,
                "source_ids": source_ids if isinstance(source_ids, list) else [],
                "source_ids_json": row.source_ids_json,
                "status": row.status,
                "error_code": row.error_code,
                "latency_ms": row.latency_ms,
                "created_at": row.created_at,
            }
        )
    return success({"ai_task_id": ai_task.id, "business_task_id": ai_task.business_task_id, "records": records, "total": len(records)})


# ---------------------------------------------------------------------------
# 15. 结果
# ---------------------------------------------------------------------------


@router.get("/ai-tasks/{ai_task_id}/result")
def get_ai_task_result(
    ai_task_id: int,
    user: dict = Depends(require_user),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    ai_task, _business_task = _get_ai_task_for_view(db, ai_task_id, user)
    result = (
        db.query(AiResult)
        .filter(AiResult.ai_task_id == ai_task.id)
        .order_by(AiResult.id.desc())
        .first()
    )
    if result is None:
        _error(404, "该 AI 任务暂无结果（可能仍在运行或运行失败）")

    structured = _json_loads(result.structured_json, {})
    basis_refs = _json_loads(result.basis_refs_json, [])
    uncertain_items = _json_loads(result.uncertain_items_json, [])
    missing_data = _json_loads(result.missing_data_json, [])
    return success(
        {
            "ai_task_id": ai_task.id,
            "business_task_id": ai_task.business_task_id,
            "result_id": result.id,
            "content_md": result.content_md,
            "structured": structured if isinstance(structured, dict) else {},
            "basis_refs": basis_refs if isinstance(basis_refs, list) else [],
            "basis_ok": bool(result.basis_ok),
            "uncertain_items": uncertain_items if isinstance(uncertain_items, list) else [],
            "missing_data": missing_data if isinstance(missing_data, list) else [],
            "created_at": result.created_at,
        }
    )


# ---------------------------------------------------------------------------
# 16. 失败重试
# ---------------------------------------------------------------------------


@router.post("/ai-tasks/{ai_task_id}/retry", status_code=202)
def retry_ai_task(
    ai_task_id: int,
    user: dict = Depends(require_operator),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    ai_task = db.get(AiTask, ai_task_id)
    if ai_task is None:
        _error(404, "AI 任务不存在")
    business_task_id = ai_task.business_task_id
    business_task = _ensure_operator_owner(db, business_task_id, user)
    if str(getattr(ai_task, "status", "") or "") != "FAILED":
        _error(409, "仅 FAILED 状态的 AI 任务可重试")
    if str(getattr(business_task, "status", "") or "") not in EDITABLE_STATUSES:
        _error(409, "业务任务已进入审核/完成状态，不能重新运行 AI 测算")

    _ensure_run_limit(business_task_id)
    uid = _user_id(user)
    db.rollback()
    try:
        new_ai_task_id = start_ai_task(business_task_id, uid)
    except ValueError as exc:
        _error(404, str(exc))

    return success(
        {
            "ai_task_id": new_ai_task_id,
            "retry_of": ai_task_id,
            "status": "PENDING",
            "model_mode": str(_setting("llm_mode", "mock") or "mock").lower(),
        }
    )
