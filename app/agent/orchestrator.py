"""AI 编排器（BUILD_SPEC §6）。

职责：
- ``start_ai_task``：写 ``ai_tasks(PENDING)`` + 输入快照，后台 daemon 线程执行；
- ``run_ai_task``：PENDING→RUNNING，循环调用 LLM / 执行工具 / 回填 tool 消息，
  完成判定与依据校验后写 ``ai_results``，异常写 FAILED 终态；
- ``cleanup_stale_running``：服务启动时把残留 RUNNING 置 FAILED(STALE_RUNNING)。

关键约束：
- 后台线程自己创建 Session（SQLite check_same_thread=False）；
- 每轮/每个工具都检查轮次、工具总数与总时长限额；
- 每个 tool_call 都必须回填 ``role=tool`` + 相同 ``tool_call_id``；
- 重复同参调用第二次不执行，回填“已调用过”；
- 数据不足仍输出 total → 重试 1 次仍犯 → FAILED DATA_FABRICATED；
- basis_refs L1 不在允许集合 → basis_ok=false + uncertain（不 FAILED）；
  L2 库中不存在 → 软标记“依据已变更”（不 FAILED）。
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from app.agent.llm import LLMClient, LLMError
from app.agent.prompts import (
    JSON_ONLY_REMINDER,
    NO_FABRICATION_REMINDER,
    SYSTEM_PROMPT,
    TOOL_EVIDENCE_REMINDER,
    build_user_prompt,
)
from app.agent.tools import (
    TOOL_HANDLERS,
    get_tool_schemas,
    get_tool_type,
    summarize_tool_result,
    validate_tool_args,
)
from app.config import settings
from app.db import SessionLocal
from app.models import AiResult, AiTask, BusinessTask, Material, Rule, TaskMaterial, ToolCallRecord


logger = logging.getLogger("app.agent.orchestrator")

VALID_FAULTS = ("tool_timeout", "model_error", "invalid_tool_args")
TERMINAL_STATUSES = ("SUCCEEDED", "FAILED")
COMPLETION_TYPES = {"retrieval", "validation", "calculation"}


# ---------------------------------------------------------------------------
# 基础工具
# ---------------------------------------------------------------------------


def _setting(name: str, default: Any = None) -> Any:
    if hasattr(settings, name):
        return getattr(settings, name)
    upper = name.upper()
    if hasattr(settings, upper):
        return getattr(settings, upper)
    return default


def _now_iso() -> str:
    """ISO8601 字符串（带本地时区偏移，精确到秒），与 S1 的 now_iso 口径一致。"""
    return datetime.now().astimezone().replace(microsecond=0).isoformat()


def _json_loads(value: Any, default: Any = None) -> Any:
    if value is None:
        return default
    if isinstance(value, (dict, list)):
        return value
    try:
        return json.loads(value)
    except (TypeError, ValueError):
        return default


def _json_dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, default=str)


def _as_int(value: Any, default: int) -> int:
    try:
        result = int(value)
    except (TypeError, ValueError):
        return default
    return result if result > 0 else default


def _as_float(value: Any, default: float) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return default
    return result if result > 0 else default


def _limits() -> dict[str, Any]:
    return {
        "max_tool_calls": _as_int(_setting("max_tool_calls", 10), 10),
        "max_llm_rounds": _as_int(_setting("max_llm_rounds", 8), 8),
        "max_ai_runs": _as_int(_setting("max_ai_runs", 10), 10),
        "tool_timeout_ms": _as_int(_setting("tool_timeout_ms", 3000), 3000),
        "llm_timeout_s": _as_float(_setting("llm_timeout_s", 30), 30.0),
        "ai_run_timeout_s": _as_float(_setting("ai_run_timeout_s", 120), 120.0),
    }


def _demo_env() -> bool:
    return str(_setting("app_env", "demo") or "demo").strip().lower() == "demo"


def _model_mode() -> str:
    mode = str(_setting("llm_mode", "mock") or "mock").strip().lower()
    return "mock" if mode == "mock" else "real"


def _model_name() -> str:
    if _model_mode() == "mock":
        return "mock-script"
    return str(_setting("llm_model", "deepseek-chat") or "deepseek-chat")


def _normalize_fault(fault: Any) -> str | None:
    """故障注入仅 APP_ENV=demo 且取值合法时生效。"""
    if not _demo_env():
        return None
    value = str(fault or "").strip()
    return value if value in VALID_FAULTS else None


def _truncate(text: Any, limit: int = 500) -> str:
    content = "" if text is None else str(text)
    return content if len(content) <= limit else content[: max(0, limit - 1)] + "…"


class AgentFailure(Exception):
    """带错误码的编排失败；写入 ai_tasks.error_code。"""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass
class _AgentState:
    messages: list[dict[str, Any]]
    client: LLMClient
    limits: dict[str, Any]
    deadline: float
    fault: str | None = None
    fault_used: bool = False
    rounds: int = 0
    model_calls: int = 0
    tool_calls: int = 0
    seq: int = 0
    distinct_types: set[str] = field(default_factory=set)
    source_ids: set[str] = field(default_factory=set)
    seen_calls: set[str] = field(default_factory=set)
    evidence_retry: int = 0
    json_retry: int = 0
    fabricated_retry: int = 0
    calculation_ok: bool = False
    last_calc: dict[str, Any] | None = None
    plan_mode: bool = False
    last_plan: dict[str, Any] | None = None

    def counters(self, elapsed_ms: int | None = None) -> dict[str, Any]:
        data: dict[str, Any] = {
            "rounds": self.rounds,
            "model_calls": self.model_calls,
            "tool_calls": self.tool_calls,
            "distinct_types": sorted(self.distinct_types),
            "max_tool_calls": self.limits["max_tool_calls"],
            "max_llm_rounds": self.limits["max_llm_rounds"],
            "tool_timeout_ms": self.limits["tool_timeout_ms"],
            "llm_timeout_s": self.limits["llm_timeout_s"],
            "ai_run_timeout_s": self.limits["ai_run_timeout_s"],
        }
        if elapsed_ms is not None:
            data["elapsed_ms"] = elapsed_ms
        if self.fault:
            data["fault"] = self.fault
        return data


# ---------------------------------------------------------------------------
# 快照
# ---------------------------------------------------------------------------


def _serialize_material(row: Any) -> dict[str, Any]:
    content = _json_loads(getattr(row, "content_json", None), {})
    if not isinstance(content, dict):
        content = {"raw": content}
    return {
        "material_no": getattr(row, "material_no", None),
        "type": getattr(row, "type", None),
        "title": getattr(row, "title", None),
        "source": getattr(row, "source", None),
        "version_no": getattr(row, "version_no", None),
        "content": content,
    }


def _serialize_rule(row: Any) -> dict[str, Any]:
    content = _json_loads(getattr(row, "content_json", None), {})
    return {
        "rule_code": getattr(row, "rule_code", None),
        "name": getattr(row, "name", None),
        "rule_type": getattr(row, "rule_type", None),
        "severity": getattr(row, "severity", None),
        "source": getattr(row, "source", None),
        "content": content,
    }


def _build_snapshot(db: Any, task: Any, note: str | None = None, fault: str | None = None) -> dict[str, Any]:
    """任务参数 + 关联资料 + 规则摘要 + 限额，作为本次运行的输入快照。"""
    params = _json_loads(getattr(task, "params_json", None), {})
    if not isinstance(params, dict):
        params = {}

    linked_rows = (
        db.query(TaskMaterial, Material)
        .join(Material, TaskMaterial.material_id == Material.id)
        .filter(TaskMaterial.task_id == task.id)
        .order_by(TaskMaterial.id)
        .all()
    )
    linked_materials: list[dict[str, Any]] = []
    seen_material: set[tuple[Any, Any]] = set()
    for _link, material in linked_rows:
        key = (getattr(material, "material_no", None), getattr(material, "version_no", None))
        if key in seen_material:
            continue
        seen_material.add(key)
        linked_materials.append(_serialize_material(material))

    business_type = str(getattr(task, "business_type", None) or "print_cost")
    rule_rows = (
        db.query(Rule)
        .filter(
            Rule.enabled == 1,
            Rule.business_type.in_(tuple(dict.fromkeys([business_type, "print_cost", "common", ""]))),
        )
        .order_by(Rule.id)
        .all()
    )
    rules = [_serialize_rule(rule) for rule in rule_rows]

    return {
        "business_task": {
            "id": getattr(task, "id", None),
            "task_no": getattr(task, "task_no", None),
            "title": getattr(task, "title", None),
            "goal": getattr(task, "goal", None),
            "business_type": business_type,
            "status": getattr(task, "status", None),
            "revision": getattr(task, "revision", None),
        },
        "params": params,
        "linked_materials": linked_materials,
        "rules": rules,
        "note": _truncate(note, 500) if note else None,
        "fault": _normalize_fault(fault),
        "limits": _limits(),
    }


def _load_snapshot(db: Any, task: Any, ai_task: Any) -> dict[str, Any]:
    snapshot = _json_loads(getattr(ai_task, "input_snapshot_json", None), None)
    if not isinstance(snapshot, dict) or "params" not in snapshot or "business_task" not in snapshot:
        snapshot = _build_snapshot(db, task, note=None, fault=None)
    # 限额按当前配置刷新；故障注入只认 demo 环境
    snapshot["limits"] = _limits()
    snapshot["fault"] = _normalize_fault(snapshot.get("fault"))
    return snapshot


# ---------------------------------------------------------------------------
# 启动 / 清理
# ---------------------------------------------------------------------------


def start_ai_task(
    business_task_id: int,
    created_by: int,
    fault: str | None = None,
    note: str | None = None,
) -> int:
    """创建 PENDING ai_task + 快照，启动后台线程，返回 ai_task_id。"""
    fault = _normalize_fault(fault)
    db = SessionLocal()
    ai_task_id: int | None = None
    try:
        task = db.get(BusinessTask, business_task_id)
        if task is None:
            raise ValueError(f"业务任务不存在：{business_task_id}")
        snapshot = _build_snapshot(db, task, note=note, fault=fault)
        limits = snapshot["limits"]
        ai_task = AiTask(
            business_task_id=business_task_id,
            status="PENDING",
            model_mode=_model_mode(),
            model_name=_model_name(),
            input_snapshot_json=_json_dumps(snapshot),
            counters_json=_json_dumps(
                {
                    "rounds": 0,
                    "model_calls": 0,
                    "tool_calls": 0,
                    "distinct_types": [],
                    "max_tool_calls": limits["max_tool_calls"],
                    "max_llm_rounds": limits["max_llm_rounds"],
                    "tool_timeout_ms": limits["tool_timeout_ms"],
                    "llm_timeout_s": limits["llm_timeout_s"],
                    "ai_run_timeout_s": limits["ai_run_timeout_s"],
                }
            ),
            error_code=None,
            error_message=None,
            started_at=None,
            finished_at=None,
            created_by=created_by,
            created_at=_now_iso(),
        )
        db.add(ai_task)
        db.commit()
        db.refresh(ai_task)
        ai_task_id = int(ai_task.id)
    finally:
        db.close()

    thread = threading.Thread(
        target=_run_thread,
        args=(ai_task_id,),
        daemon=True,
        name=f"ai-task-{ai_task_id}",
    )
    thread.start()
    logger.info("已启动 AI 任务 ai_task_id=%s business_task_id=%s", ai_task_id, business_task_id)
    return int(ai_task_id)


def _run_thread(ai_task_id: int) -> None:
    try:
        run_ai_task(ai_task_id)
    except BaseException as exc:  # noqa: BLE001 - 线程兜底，绝不能把 RUNNING 留在库里
        logger.exception("AI 任务线程异常 ai_task_id=%s", ai_task_id)
        _mark_failed(None, ai_task_id, "INTERNAL_ERROR", f"{type(exc).__name__}: {exc}")


def cleanup_stale_running() -> int:
    """启动前把残留 RUNNING 置 FAILED(STALE_RUNNING)，返回清理条数。"""
    db = SessionLocal()
    try:
        count = (
            db.query(AiTask)
            .filter(AiTask.status == "RUNNING")
            .update(
                {
                    AiTask.status: "FAILED",
                    AiTask.error_code: "STALE_RUNNING",
                    AiTask.error_message: "服务启动时清理：上次运行未正常结束（可能进程被终止）",
                    AiTask.finished_at: _now_iso(),
                },
                synchronize_session=False,
            )
        )
        db.commit()
        if count:
            logger.warning("清理残留 RUNNING 的 AI 任务 %s 条", count)
        return int(count or 0)
    except Exception:  # noqa: BLE001 - 启动钩子不能因清理失败而崩溃
        db.rollback()
        logger.exception("清理残留 RUNNING 任务失败")
        return 0
    finally:
        db.close()


def count_ai_runs(business_task_id: int) -> int:
    """某业务任务已创建的 AI 运行次数（含失败/重试）。"""
    db = SessionLocal()
    try:
        return int(db.query(AiTask).filter(AiTask.business_task_id == business_task_id).count())
    except Exception:  # noqa: BLE001
        logger.exception("统计 AI 运行次数失败 business_task_id=%s", business_task_id)
        return 0
    finally:
        db.close()


# ---------------------------------------------------------------------------
# 主循环
# ---------------------------------------------------------------------------


def run_ai_task(ai_task_id: int) -> None:
    """执行一次 AI 测算（线程内调用）。"""
    db = SessionLocal()
    started = time.monotonic()
    state: _AgentState | None = None
    business_task_id: int | None = None
    try:
        ai_task = db.get(AiTask, ai_task_id)
        if ai_task is None:
            logger.warning("AI 任务不存在 ai_task_id=%s", ai_task_id)
            return
        if ai_task.status in TERMINAL_STATUSES:
            logger.warning("AI 任务已是终态，跳过 ai_task_id=%s status=%s", ai_task_id, ai_task.status)
            return
        if ai_task.status == "RUNNING":
            logger.warning("AI 任务已在运行，跳过重复执行 ai_task_id=%s", ai_task_id)
            return

        ai_task.status = "RUNNING"
        ai_task.started_at = _now_iso()
        ai_task.error_code = None
        ai_task.error_message = None
        db.commit()

        business_task_id = int(ai_task.business_task_id)
        task = db.get(BusinessTask, business_task_id)
        if task is None:
            raise AgentFailure("TASK_NOT_FOUND", f"关联业务任务不存在：{business_task_id}")

        snapshot = _load_snapshot(db, task, ai_task)
        messages = [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": build_user_prompt(snapshot)},
        ]
        limits = _limits()
        plan_mode = str(getattr(task, "business_type", "") or "").strip() == "print_cost_plan"
        state = _AgentState(
            plan_mode=plan_mode,
            messages=messages,
            client=LLMClient(
                mode=_model_mode(),
                timeout_s=limits["llm_timeout_s"],
            ),
            limits=limits,
            deadline=started + limits["ai_run_timeout_s"],
            fault=snapshot.get("fault"),
        )
        # 释放本次读取事务，避免主线程读锁与工具/记录写入互相阻塞
        db.rollback()
        logger.info(
            "AI 任务开始 ai_task_id=%s business_task_id=%s model_mode=%s fault=%s",
            ai_task_id,
            business_task_id,
            _model_mode(),
            state.fault,
        )
        _agent_loop(db, ai_task_id, business_task_id, snapshot, state)
    except LLMError as exc:
        logger.warning("模型调用失败 ai_task_id=%s：%s", ai_task_id, exc)
        _mark_failed(db, ai_task_id, exc.code or "MODEL_ERROR", str(exc))
    except AgentFailure as exc:
        logger.warning("AI 任务失败 ai_task_id=%s code=%s：%s", ai_task_id, exc.code, exc.message)
        _mark_failed(db, ai_task_id, exc.code, exc.message)
    except Exception as exc:  # noqa: BLE001
        logger.exception("AI 任务异常 ai_task_id=%s", ai_task_id)
        _mark_failed(db, ai_task_id, "INTERNAL_ERROR", f"{type(exc).__name__}: {exc}")
    finally:
        _finalize(db, ai_task_id, state, started)
        db.close()


def _check_deadline(state: _AgentState) -> None:
    if time.monotonic() > state.deadline:
        raise AgentFailure(
            "AI_RUN_TIMEOUT",
            f"AI 运行超过总时长上限 {state.limits['ai_run_timeout_s']:g}s",
        )


def _agent_loop(
    db: Any,
    ai_task_id: int,
    business_task_id: int,
    snapshot: dict[str, Any],
    state: _AgentState,
) -> None:
    while state.rounds < state.limits["max_llm_rounds"]:
        _check_deadline(state)

        if state.fault == "model_error" and not state.fault_used:
            state.fault_used = True
            raise LLMError("故障注入：模型调用失败（model_error）")

        state.rounds += 1
        state.model_calls += 1
        response = state.client.chat(
            state.messages,
            tools=get_tool_schemas(plan_mode=state.plan_mode),
            tool_choice="auto",
        )
        _check_deadline(state)
        tool_calls = response.get("tool_calls") or []
        if not isinstance(tool_calls, list):
            tool_calls = []
        tool_calls = [call for call in tool_calls if isinstance(call, dict)]

        if tool_calls:
            for index, call in enumerate(tool_calls):
                if not call.get("id"):
                    call["id"] = f"call_{state.rounds}_{index + 1}"
            _append_assistant_tool_calls(state.messages, response.get("content"), tool_calls)
            for call in tool_calls:
                _check_deadline(state)
                if state.tool_calls >= state.limits["max_tool_calls"]:
                    raise AgentFailure(
                        "LIMIT_EXCEEDED",
                        f"工具调用超过上限 {state.limits['max_tool_calls']} 次",
                    )
                state.tool_calls += 1
                compact = _execute_tool_call(db, ai_task_id, business_task_id, call, state)
                _append_tool_message(state.messages, call["id"], compact)
            continue

        # ---- 模型想收尾：解析最终 JSON ----
        content = response.get("content")
        final_obj, parse_error = _parse_final_json(content)
        if final_obj is None:
            if state.json_retry < 2:
                state.json_retry += 1
                _append_assistant_text(state.messages, content or "")
                _append_user_message(state.messages, JSON_ONLY_REMINDER)
                continue
            raise AgentFailure("MODEL_ERROR", f"模型连续返回非法 JSON：{parse_error}")

        # ---- 完成判定护栏：≥2 类工具各成功 ≥1，且 basis_refs 非空 ----
        basis_refs = _normalize_basis_refs(final_obj.get("basis_refs"))
        # 方案模式：若模型漏写依据，用 plan_candidates 本次真实 source_ids 兜底，避免把有效方案判失败
        if not basis_refs and state.plan_mode and state.last_plan and state.last_plan.get("ok"):
            plan_ids = (state.last_plan.get("source") or {}).get("ids") or []
            for sid in plan_ids:
                sid = str(sid or "").strip()
                if not sid:
                    continue
                ref_type = "rule" if sid.startswith("RULE-") else "material"
                basis_refs.append({"type": ref_type, "id": sid, "label": sid})
        categories = state.distinct_types & COMPLETION_TYPES
        if len(categories) < 2 or not basis_refs:
            if state.evidence_retry < 1:
                state.evidence_retry += 1
                _append_assistant_text(state.messages, content or "")
                reminder = (
                    TOOL_EVIDENCE_REMINDER
                    + f"（当前成功工具类别：{'/'.join(sorted(categories)) or '无'}；"
                    + f"basis_refs 数量：{len(basis_refs)}）"
                )
                _append_user_message(state.messages, reminder)
                continue
            raise AgentFailure(
                "NO_TOOL_CALL",
                "未成功调用至少两类业务工具，或最终 basis_refs 为空",
            )

        # ---- 数据不足仍输出 total → 重试 1 次 → DATA_FABRICATED ----
        if _has_fabricated_total(state, final_obj):
            if state.fabricated_retry < 1:
                state.fabricated_retry += 1
                _append_assistant_text(state.messages, content or "")
                _append_user_message(state.messages, NO_FABRICATION_REMINDER)
                continue
            raise AgentFailure(
                "DATA_FABRICATED",
                "工具已返回数据不足（DATA_NOT_FOUND/missing_data），模型仍输出总价或单册成本",
            )

        # ---- 依据校验 + 保存结果 ----
        allowed_ids = _build_allowed_ids(db, snapshot, state)
        basis_ok, basis_uncertain = _validate_basis_refs(db, basis_refs, allowed_ids)
        _save_result(db, ai_task_id, business_task_id, snapshot, final_obj, basis_refs, basis_ok, basis_uncertain, state)
        _mark_succeeded(db, ai_task_id, state)
        logger.info(
            "AI 任务成功 ai_task_id=%s rounds=%s tool_calls=%s types=%s",
            ai_task_id,
            state.rounds,
            state.tool_calls,
            ",".join(sorted(state.distinct_types)),
        )
        return

    raise AgentFailure(
        "LIMIT_EXCEEDED",
        f"模型轮次超过上限 {state.limits['max_llm_rounds']} 轮仍未给出最终结果",
    )


# ---------------------------------------------------------------------------
# 消息协议
# ---------------------------------------------------------------------------


def _append_assistant_tool_calls(
    messages: list[dict[str, Any]], content: Any, tool_calls: list[dict[str, Any]]
) -> None:
    payload_calls = []
    for call in tool_calls:
        raw_arguments = call.get("raw_arguments")
        if raw_arguments is None:
            raw_arguments = _json_dumps(call.get("arguments") or {})
        elif not isinstance(raw_arguments, str):
            raw_arguments = _json_dumps(raw_arguments)
        payload_calls.append(
            {
                "id": call.get("id"),
                "type": "function",
                "function": {"name": call.get("name"), "arguments": raw_arguments},
            }
        )
    messages.append(
        {
            "role": "assistant",
            "content": content if content else None,
            "tool_calls": payload_calls,
        }
    )


def _append_tool_message(messages: list[dict[str, Any]], tool_call_id: Any, payload: dict[str, Any]) -> None:
    messages.append(
        {
            "role": "tool",
            "tool_call_id": str(tool_call_id),
            "content": _json_dumps(payload),
        }
    )


def _append_assistant_text(messages: list[dict[str, Any]], content: Any) -> None:
    messages.append({"role": "assistant", "content": content if content else ""})


def _append_user_message(messages: list[dict[str, Any]], content: str) -> None:
    messages.append({"role": "user", "content": content})


def _compact_tool_result(result: dict[str, Any]) -> dict[str, Any]:
    source = result.get("source") or {}
    return {
        "ok": bool(result.get("ok")),
        "error_code": result.get("error_code"),
        "error_message": result.get("error_message"),
        "data": result.get("data"),
        "source_ids": source.get("ids") if isinstance(source, dict) else [],
        "warnings": result.get("warnings") or [],
    }


def _tool_failure(error_code: str, message: str, data: Any = None) -> dict[str, Any]:
    return {
        "ok": False,
        "data": data,
        "source": {"ids": []},
        "warnings": [],
        "error_code": error_code,
        "error_message": message,
    }


# ---------------------------------------------------------------------------
# 工具执行器
# ---------------------------------------------------------------------------


def _execute_tool_call(
    db: Any,
    ai_task_id: int,
    business_task_id: int,
    call: dict[str, Any],
    state: _AgentState,
) -> dict[str, Any]:
    """执行单个 tool_call，落 tool_call_records，返回回填给模型的精简结果。"""
    name = str(call.get("name") or "").strip()
    arguments = call.get("arguments")
    arguments_error = call.get("arguments_error")
    if arguments is None:
        arguments = {}
    if not isinstance(arguments, dict):
        arguments = {}
        arguments_error = arguments_error or "工具参数必须是 JSON 对象"

    original_args = dict(arguments)
    args_for_record: dict[str, Any] = dict(original_args)
    if arguments_error:
        args_for_record = {"_raw_arguments": call.get("raw_arguments"), "_parse_error": arguments_error}

    state.seq += 1
    record = ToolCallRecord(
        ai_task_id=ai_task_id,
        business_task_id=business_task_id,
        seq=state.seq,
        tool_name=name or "unknown",
        tool_type=get_tool_type(name),
        args_json=_json_dumps(args_for_record),
        result_summary="",
        source_ids_json="[]",
        status="FAILED",
        error_code=None,
        latency_ms=0,
        created_at=_now_iso(),
    )
    started = time.monotonic()

    def finish(result: dict[str, Any]) -> dict[str, Any]:
        latency_ms = int(round((time.monotonic() - started) * 1000))
        record.latency_ms = latency_ms
        record.result_summary = summarize_tool_result(name, result)[:200]
        source_ids = (result.get("source") or {}).get("ids") if isinstance(result.get("source"), dict) else []
        record.source_ids_json = _json_dumps(source_ids or [])
        record.status = "SUCCESS" if result.get("ok") else "FAILED"
        record.error_code = None if result.get("ok") else (result.get("error_code") or "TOOL_EXEC_ERROR")
        db.add(record)
        db.commit()

        tool_type = get_tool_type(name)
        if result.get("ok"):
            state.distinct_types.add(tool_type)
            state.source_ids.update(str(x) for x in (source_ids or []) if x)
            if tool_type == "calculation":
                state.calculation_ok = True
        if name == "calculate_cost":
            state.last_calc = result
        if name == "plan_candidates" and result.get("ok"):
            state.last_plan = result
        return _compact_tool_result(result)

    # 1) 未知工具（方案工具只在方案模式可用）
    if name not in TOOL_HANDLERS:
        return finish(_tool_failure("TOOL_NOT_FOUND", f"未知工具：{name or '（空）'}"))
    if name == "plan_candidates" and not state.plan_mode:
        return finish(_tool_failure("TOOL_NOT_ALLOWED", "方案工具仅在方案模式（print_cost_plan）中可用"))

    # 2) 重复同参熔断
    try:
        args_key = hashlib.sha256(_json_dumps(original_args).encode("utf-8")).hexdigest()
    except Exception:  # noqa: BLE001
        args_key = repr(original_args)
    call_key = f"{name}|{args_key}"
    if call_key in state.seen_calls:
        result = _tool_failure(
            "DUPLICATE_CALL",
            "该工具与参数已调用过，未重复执行；请更换参数或基于已有结果给出结论",
            data={"duplicate": True},
        )
        return finish(result)
    state.seen_calls.add(call_key)

    # 3) 故障注入（仅 demo，首次工具）：记录失败后终止本次 AI 任务，便于现场演示"失败态 + 可重试"
    if state.fault and not state.fault_used:
        if state.fault == "tool_timeout":
            state.fault_used = True
            time.sleep(state.limits["tool_timeout_ms"] / 1000.0 + 0.25)
            finish(
                _tool_failure(
                    "TOOL_TIMEOUT",
                    f"故障注入：工具执行超过 {state.limits['tool_timeout_ms']}ms 超时限制",
                )
            )
            raise AgentFailure(
                "TOOL_TIMEOUT",
                f"故障注入（demo）：工具执行超时（>{state.limits['tool_timeout_ms']}ms），本次 AI 任务置失败；可点击重试",
            )
        if state.fault == "invalid_tool_args":
            state.fault_used = True
            args_for_record = dict(original_args)
            args_for_record["_fault"] = "invalid_tool_args"
            record.args_json = _json_dumps(args_for_record)
            finish(
                _tool_failure(
                    "TOOL_ARGS_INVALID",
                    "故障注入：首次工具参数被强制置为非法（包含不支持的参数 _fault）",
                )
            )
            raise AgentFailure(
                "TOOL_ARGS_INVALID",
                "故障注入（demo）：工具参数非法，后端校验拒绝（已记录 TOOL_ARGS_INVALID）；本次 AI 任务置失败，可重试",
            )

    # 4) 参数校验（LLM 原始 JSON 解析错误 + JSON Schema 校验）
    errors: list[str] = []
    if arguments_error:
        errors.append(str(arguments_error))
    errors.extend(validate_tool_args(name, original_args))
    if errors:
        return finish(
            _tool_failure(
                "TOOL_ARGS_INVALID",
                "工具参数校验失败：" + "；".join(errors),
                data={"errors": errors},
            )
        )

    # 5) 执行（异常捕获 + 事后超时判定）
    handler = TOOL_HANDLERS[name]
    try:
        result = handler(**original_args)
    except TypeError as exc:
        result = _tool_failure("TOOL_ARGS_INVALID", f"工具入参不匹配：{exc}")
    except Exception as exc:  # noqa: BLE001
        logger.exception("工具执行异常 ai_task_id=%s tool=%s", ai_task_id, name)
        result = _tool_failure("TOOL_EXEC_ERROR", f"工具执行异常：{type(exc).__name__}: {exc}")

    if not isinstance(result, dict):
        result = _tool_failure("TOOL_EXEC_ERROR", "工具返回值格式异常（非对象）")

    elapsed_ms = int(round((time.monotonic() - started) * 1000))
    if elapsed_ms > state.limits["tool_timeout_ms"]:
        result = _tool_failure(
            "TOOL_TIMEOUT",
            f"工具执行耗时 {elapsed_ms}ms，超过 {state.limits['tool_timeout_ms']}ms 超时限制",
            data={"partial": result.get("data")},
        )
    return finish(result)


# ---------------------------------------------------------------------------
# 最终 JSON / 依据校验
# ---------------------------------------------------------------------------


def _balanced_json_object(text: str, start: int) -> str | None:
    depth = 0
    in_string = False
    escaped = False
    for index in range(start, len(text)):
        char = text[index]
        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
            continue
        if char == '"':
            in_string = True
        elif char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return text[start : index + 1]
    return None


def _parse_final_json(content: Any) -> tuple[dict[str, Any] | None, str | None]:
    """剥离 ```json 围栏后取第一个完整 JSON 对象；失败返回 (None, 原因)。"""
    if content is None:
        return None, "模型未返回内容"
    text = content if isinstance(content, str) else _json_dumps(content)
    cleaned = re.sub(r"```(?:json|JSON)?", "", text).strip()
    starts = [index for index, char in enumerate(cleaned) if char == "{"]
    if not starts:
        return None, "未找到 JSON 对象起始符 {"
    last_error = ""
    for start in starts:
        candidate = _balanced_json_object(cleaned, start)
        if candidate is None:
            continue
        try:
            parsed = json.loads(candidate)
        except (TypeError, ValueError) as exc:
            last_error = str(exc)
            continue
        if isinstance(parsed, dict):
            return parsed, None
        last_error = "顶层不是 JSON 对象"
    return None, last_error or "未找到完整 JSON 对象"


def _as_list(value: Any) -> list[str]:
    """把模型输出转成字符串列表，过滤“无”类占位。占位不参与缺失判定。"""
    placeholders = {"", "无", "none", "null", "n/a", "na", "-", "—", "[]", "{}"}
    if value is None:
        return []
    if isinstance(value, (str, int, float)):
        value = [value]
    if not isinstance(value, (list, tuple)):
        return [str(value)]
    result: list[str] = []
    for item in value:
        text = str(item).strip() if item is not None else ""
        if not text or text.lower() in placeholders:
            continue
        if text not in result:
            result.append(text)
    return result


def _normalize_basis_refs(value: Any) -> list[dict[str, Any]]:
    if isinstance(value, dict):
        value = [value]
    if not isinstance(value, (list, tuple)):
        return []
    refs: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in value:
        if isinstance(item, str):
            ref_id = item.strip()
            ref = {"type": "data", "id": ref_id, "label": ref_id}
        elif isinstance(item, dict):
            ref_id = str(
                item.get("id")
                or item.get("material_no")
                or item.get("rule_code")
                or item.get("ref")
                or ""
            ).strip()
            ref = {
                "type": str(item.get("type") or "data"),
                "id": ref_id,
                "label": str(item.get("label") or item.get("name") or ref_id),
            }
        else:
            continue
        if not ref_id or ref_id in seen:
            continue
        seen.add(ref_id)
        refs.append(ref)
    return refs


def _build_allowed_ids(db: Any, snapshot: dict[str, Any], state: _AgentState) -> set[str]:
    """L1 允许集合 = 本次工具 source_ids ∪ 任务关联 material_no ∪ rule_code ∪ 参数业务数据。"""
    allowed: set[str] = set(str(x) for x in state.source_ids if x)

    for material in snapshot.get("linked_materials") or []:
        if material.get("material_no"):
            allowed.add(str(material["material_no"]))
    for rule in snapshot.get("rules") or []:
        if rule.get("rule_code"):
            allowed.add(str(rule["rule_code"]))

    try:
        for (rule_code,) in db.query(Rule.rule_code).all():
            if rule_code:
                allowed.add(str(rule_code))
    except Exception:  # noqa: BLE001
        logger.exception("查询规则编码失败，使用快照规则作为允许集合")

    params = snapshot.get("params") or {}
    if isinstance(params, dict):
        for key, value in params.items():
            allowed.add(str(key))
            if isinstance(value, (str, int, float)) and not isinstance(value, bool):
                allowed.add(str(value))

    task = snapshot.get("business_task") or {}
    if task.get("task_no"):
        allowed.add(str(task["task_no"]))
    return allowed


def _validate_basis_refs(
    db: Any,
    refs: list[dict[str, Any]],
    allowed_ids: set[str],
) -> tuple[bool, list[str]]:
    """L1 允许集合 + L2 存在性校验；都软处理，不 FAILED。"""
    basis_ok = True
    uncertain: list[str] = []

    for ref in refs:
        ref_id = str(ref.get("id") or "").strip()
        ref_type = str(ref.get("type") or "data").lower()
        if not ref_id:
            continue

        if ref_id not in allowed_ids:
            basis_ok = False
            uncertain.append(f"依据未能核对：{ref_id}（不在本次工具返回或任务关联资料中）")

        try:
            if ref_type == "material":
                material = (
                    db.query(Material)
                    .filter(Material.material_no == ref_id)
                    .order_by(Material.version_no.desc(), Material.id.desc())
                    .first()
                )
                if material is None:
                    uncertain.append(f"依据已变更：{ref_id}（资料库中不存在该 material_no）")
                elif int(getattr(material, "enabled", 1) or 0) != 1:
                    uncertain.append(f"依据已变更：{ref_id}（资料已停用）")
            elif ref_type == "rule":
                rule = db.query(Rule).filter(Rule.rule_code == ref_id).first()
                if rule is None:
                    uncertain.append(f"依据已变更：{ref_id}（规则库中不存在该 rule_code）")
                elif int(getattr(rule, "enabled", 1) or 0) != 1:
                    uncertain.append(f"依据已变更：{ref_id}（规则已停用）")
        except Exception:  # noqa: BLE001 - 存在性校验失败不阻断结果保存
            logger.exception("依据存在性校验失败 ref=%s", ref_id)

    # 去重
    deduped = list(dict.fromkeys(uncertain))
    return basis_ok, deduped


def _has_fabricated_total(state: _AgentState, final_obj: dict[str, Any]) -> bool:
    structured = final_obj.get("structured")
    if not isinstance(structured, dict):
        return False
    total = structured.get("total")
    if total is None:
        return False
    missing = _as_list(final_obj.get("missing_data"))
    missing += [item for item in _as_list(structured.get("missing")) if item not in missing]
    if missing:
        return True
    if not state.calculation_ok:
        return True
    if state.last_calc is not None and not state.last_calc.get("ok"):
        return True
    return False


def _coerce_number(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value.strip().replace(",", ""))
        except ValueError:
            return None
    return None


def _normalize_structured(final_obj: dict[str, Any]) -> dict[str, Any]:
    structured = final_obj.get("structured")
    structured = structured if isinstance(structured, dict) else {}
    items = structured.get("line_items")
    clean_items: list[dict[str, Any]] = []
    if isinstance(items, list):
        for item in items:
            if not isinstance(item, dict):
                continue
            clean_items.append(
                {
                    "name": str(item.get("name") or ""),
                    "formula": str(item.get("formula") or ""),
                    "amount": _coerce_number(item.get("amount")),
                }
            )
    return {
        "line_items": clean_items,
        "total": _coerce_number(structured.get("total")),
        "per_copy": _coerce_number(structured.get("per_copy")),
        "missing": _as_list(structured.get("missing")),
        "uncertain": _as_list(structured.get("uncertain")),
    }


def _consistency_uncertain(state: _AgentState, structured: dict[str, Any]) -> list[str]:
    """模型结果与 calculate_cost 结果不一致时做软提示（不阻断保存）。"""
    if not state.last_calc or not state.last_calc.get("ok"):
        return []
    calc_total = _coerce_number((state.last_calc.get("data") or {}).get("total"))
    model_total = _coerce_number(structured.get("total"))
    if calc_total is None or model_total is None:
        return []
    if abs(calc_total - model_total) > 0.011:
        return [
            f"模型输出总金额 {model_total:.2f} 与 calculate_cost 计算值 {calc_total:.2f} 不一致，"
            "请以执行记录中的公式复核"
        ]
    return []


def _tool_missing_items(state: _AgentState) -> list[str]:
    if not state.last_calc or state.last_calc.get("ok"):
        return []
    data = state.last_calc.get("data")
    if isinstance(data, dict):
        return _as_list(data.get("missing"))
    return []


def _fallback_content(structured: dict[str, Any], missing: list[str]) -> str:
    plans = structured.get("plans") or []
    if plans:
        lines = ["## 方案模式：候选方案", ""]
        budget = structured.get("budget_total")
        if budget is not None:
            lines.append(f"- 预算：{float(budget):.2f} 元；预算内可行：{'是' if structured.get('budget_feasible', True) else '否'}")
        lines.append("")
        lines.append("| 方案 | 纸张 | 装订 | 工艺 | 总成本（元） | 单册（元） | 是否在预算内 |")
        lines.append("|---|---|---|---|---|---|---|")
        for plan in plans:
            lines.append(
                "| {no} | {paper} | {binding} | {process} | {total:.2f} | {per:.2f} | {within} |".format(
                    no=plan.get("plan_no", "—"),
                    paper=plan.get("paper_type", "—"),
                    binding=plan.get("binding", "—"),
                    process=plan.get("process", "无"),
                    total=float(plan.get("total") or 0),
                    per=float(plan.get("per_copy") or 0),
                    within="是" if plan.get("within_budget", True) else "否",
                )
            )
        return "\n".join(lines)
    if structured.get("total") is not None:
        lines = [
            "## 印制成本测算结果",
            "",
            f"- 总成本：{structured['total']:.2f} 元",
            f"- 单册成本：{structured['per_copy']:.2f} 元" if structured.get("per_copy") is not None else "- 单册成本：—",
            "",
            "### 成本明细",
            "",
        ]
        for item in structured.get("line_items") or []:
            amount = item.get("amount")
            amount_text = f"{amount:.2f}" if isinstance(amount, (int, float)) else "—"
            lines.append(f"- {item.get('name')}：{item.get('formula')} = {amount_text} 元")
        return "\n".join(lines)
    lines = ["## 印制成本测算结果", "", "当前资料不足，未给出总成本。", "", "### 缺失项", ""]
    lines.extend([f"- {item}" for item in missing] or ["- 未取得完整报价资料"])
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# 结果落库 / 终态
# ---------------------------------------------------------------------------


def _save_result(
    db: Any,
    ai_task_id: int,
    business_task_id: int,
    snapshot: dict[str, Any],
    final_obj: dict[str, Any],
    basis_refs: list[dict[str, Any]],
    basis_ok: bool,
    basis_uncertain: list[str],
    state: _AgentState,
) -> None:
    structured = _normalize_structured(final_obj)

    # 方案模式：以 plan_candidates 的真实计算结果为准，覆盖模型自由发挥的金额/明细
    plan_mode = str((snapshot.get("business_task") or {}).get("business_type") or "") == "print_cost_plan"
    plan_data = (state.last_plan or {}).get("data") if (state.plan_mode and state.last_plan and state.last_plan.get("ok")) else None
    plans = (plan_data or {}).get("plans") if isinstance(plan_data, dict) else None
    if plan_mode and isinstance(plans, list) and plans:
        first = plans[0]
        structured["plans"] = plans
        structured["plan_mode"] = True
        structured["budget_total"] = (plan_data or {}).get("budget_total")
        structured["budget_feasible"] = bool((plan_data or {}).get("feasible", True))
        structured["line_items"] = first.get("line_items") or []
        structured["total"] = first.get("total")
        structured["per_copy"] = first.get("per_copy")
        structured["missing"] = []

    uncertain = _as_list(final_obj.get("uncertain_items"))
    uncertain += [item for item in _as_list(structured.get("uncertain")) if item not in uncertain]
    uncertain += [item for item in basis_uncertain if item not in uncertain]
    uncertain += [item for item in _consistency_uncertain(state, structured) if item not in uncertain]

    missing = _as_list(final_obj.get("missing_data"))
    missing += [item for item in _as_list(structured.get("missing")) if item not in missing]
    missing += [item for item in _tool_missing_items(state) if item not in missing]

    content_md = final_obj.get("content_md")
    if not isinstance(content_md, str) or not content_md.strip():
        content_md = _fallback_content(structured, missing)

    result = AiResult(
        ai_task_id=ai_task_id,
        business_task_id=business_task_id,
        content_md=content_md,
        structured_json=_json_dumps(structured),
        basis_refs_json=_json_dumps(basis_refs),
        basis_ok=1 if basis_ok else 0,
        uncertain_items_json=_json_dumps(uncertain),
        missing_data_json=_json_dumps(missing),
        created_at=_now_iso(),
    )
    db.add(result)
    db.commit()
    logger.info(
        "已保存 AI 结果 ai_task_id=%s basis_ok=%s basis_refs=%s missing=%s",
        ai_task_id,
        basis_ok,
        len(basis_refs),
        len(missing),
    )
    _ = snapshot  # 快照已用于提示词；保留参数便于后续扩展


def _mark_succeeded(db: Any, ai_task_id: int, state: _AgentState) -> None:
    try:
        ai_task = db.get(AiTask, ai_task_id)
        if ai_task is None:
            return
        ai_task.status = "SUCCEEDED"
        ai_task.error_code = None
        ai_task.error_message = None
        ai_task.finished_at = _now_iso()
        ai_task.counters_json = _json_dumps(state.counters())
        db.commit()
    except Exception:  # noqa: BLE001
        db.rollback()
        logger.exception("写入 SUCCEEDED 终态失败 ai_task_id=%s", ai_task_id)
        raise


def _mark_failed(db: Any, ai_task_id: int, error_code: str, error_message: str) -> None:
    """写 FAILED 终态；db 可为 None（线程兜底时新建 Session）。"""
    message = _truncate(error_message, 1000)
    try:
        if db is None:
            db = SessionLocal()
            owns_session = True
        else:
            owns_session = False
            try:
                db.rollback()
            except Exception:  # noqa: BLE001
                pass
        ai_task = db.get(AiTask, ai_task_id)
        if ai_task is not None:
            if ai_task.status != "SUCCEEDED":
                ai_task.status = "FAILED"
                ai_task.error_code = error_code or "INTERNAL_ERROR"
                ai_task.error_message = message
                if not ai_task.finished_at:
                    ai_task.finished_at = _now_iso()
                db.commit()
        if owns_session:
            db.close()
    except Exception:  # noqa: BLE001
        logger.exception("写入 FAILED 终态失败 ai_task_id=%s code=%s", ai_task_id, error_code)
        try:
            db.rollback()
        except Exception:  # noqa: BLE001
            pass
        try:
            with SessionLocal() as fallback:
                ai_task = fallback.get(AiTask, ai_task_id)
                if ai_task is not None and ai_task.status != "SUCCEEDED":
                    ai_task.status = "FAILED"
                    ai_task.error_code = error_code or "INTERNAL_ERROR"
                    ai_task.error_message = message
                    ai_task.finished_at = ai_task.finished_at or _now_iso()
                    fallback.commit()
        except Exception:  # noqa: BLE001
            logger.exception("FAILED 终态兜底写入仍失败 ai_task_id=%s", ai_task_id)


def _finalize(db: Any, ai_task_id: int, state: _AgentState | None, started: float) -> None:
    """finally 兜底：保证 RUNNING 一定有终态、finished_at/counters 一定写入。"""
    elapsed_ms = int(round((time.monotonic() - started) * 1000))
    try:
        db.rollback()
    except Exception:  # noqa: BLE001
        pass
    try:
        ai_task = db.get(AiTask, ai_task_id)
        if ai_task is None:
            return
        if ai_task.status == "RUNNING":
            ai_task.status = "FAILED"
            ai_task.error_code = ai_task.error_code or "INTERNAL_ERROR"
            ai_task.error_message = ai_task.error_message or "AI 任务异常终止（未写入终态）"
        if not ai_task.finished_at:
            ai_task.finished_at = _now_iso()
        if state is not None:
            ai_task.counters_json = _json_dumps(state.counters(elapsed_ms))
        db.commit()
    except Exception:  # noqa: BLE001
        logger.exception("AI 任务终态兜底失败 ai_task_id=%s", ai_task_id)
        try:
            db.rollback()
        except Exception:  # noqa: BLE001
            pass
