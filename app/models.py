"""SQLAlchemy 2.x 声明式模型（10 张表）。

字段口径严格对齐 BUILD_SPEC §3；时间为 ISO8601 字符串，JSON 字段以 TEXT 存储
（访问时用 json.loads/json.dumps，S2 工具层与 S1 API 层共用）。

表清单：
    users / business_tasks / materials / task_materials / rules
    ai_tasks / tool_call_records / ai_results / result_versions / review_records
"""

from __future__ import annotations

from typing import Optional

from sqlalchemy import Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base, now_iso

# ============ 状态 / 角色 / 枚举常量（API、服务、前端共用口径） ============

ROLE_OPERATOR = "operator"
ROLE_REVIEWER = "reviewer"
ROLE_NAMES = {ROLE_OPERATOR: "业务操作员", ROLE_REVIEWER: "审核员"}

TASK_PENDING = "PENDING"
TASK_PENDING_REVIEW = "PENDING_REVIEW"
TASK_REJECTED = "REJECTED"
TASK_COMPLETED = "COMPLETED"
TASK_STATUSES = (TASK_PENDING, TASK_PENDING_REVIEW, TASK_REJECTED, TASK_COMPLETED)
TASK_STATUS_LABELS = {
    TASK_PENDING: "待处理",
    TASK_PENDING_REVIEW: "待审核",
    TASK_REJECTED: "已退回",
    TASK_COMPLETED: "已完成",
}
# operator 可编辑 / 可保存结果 / 可提交的状态
TASK_EDITABLE_STATUSES = (TASK_PENDING, TASK_REJECTED)

AI_PENDING = "PENDING"
AI_RUNNING = "RUNNING"
AI_SUCCEEDED = "SUCCEEDED"
AI_FAILED = "FAILED"
AI_STATUSES = (AI_PENDING, AI_RUNNING, AI_SUCCEEDED, AI_FAILED)
AI_STATUS_LABELS = {
    AI_PENDING: "待执行",
    AI_RUNNING: "运行中",
    AI_SUCCEEDED: "已完成",
    AI_FAILED: "失败",
}

MATERIAL_TYPES = ("paper", "process", "binding", "rate", "book")
MATERIAL_TYPE_LABELS = {
    "paper": "纸张报价",
    "process": "工艺报价",
    "binding": "装订报价",
    "rate": "费率",
    "book": "图书资料",
}

TOOL_STATUS_SUCCESS = "SUCCESS"
TOOL_STATUS_FAILED = "FAILED"
TOOL_STATUS_SKIPPED = "SKIPPED"

VERSION_SOURCE_AI = "AI"
VERSION_SOURCE_MANUAL = "MANUAL"

REVIEW_APPROVE = "APPROVE"
REVIEW_REJECT = "REJECT"


class User(Base):
    """账号表：operator（业务操作员）/ reviewer（审核员）。"""

    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    username: Mapped[str] = mapped_column(String(64), unique=True, nullable=False, index=True)
    password_hash: Mapped[str] = mapped_column(String(128), nullable=False)
    salt: Mapped[str] = mapped_column(String(64), nullable=False)
    role_code: Mapped[str] = mapped_column(String(32), nullable=False, default=ROLE_OPERATOR)
    display_name: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    created_at: Mapped[str] = mapped_column(String(40), nullable=False, default=now_iso)

    def __repr__(self) -> str:  # pragma: no cover - 调试用
        return f"<User id={self.id} username={self.username!r} role={self.role_code}>"


class BusinessTask(Base):
    """业务任务：印制参数校验与成本测算。"""

    __tablename__ = "business_tasks"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    task_no: Mapped[str] = mapped_column(String(64), unique=True, nullable=False, index=True)
    business_type: Mapped[str] = mapped_column(String(32), nullable=False, default="print_cost")
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    goal: Mapped[str] = mapped_column(Text, nullable=False, default="")
    params_json: Mapped[str] = mapped_column(Text, nullable=False, default="{}")
    status: Mapped[str] = mapped_column(String(32), nullable=False, default=TASK_PENDING, index=True)
    revision: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    created_by: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    created_at: Mapped[str] = mapped_column(String(40), nullable=False, default=now_iso)
    updated_at: Mapped[str] = mapped_column(String(40), nullable=False, default=now_iso)

    def __repr__(self) -> str:  # pragma: no cover
        return f"<BusinessTask id={self.id} task_no={self.task_no!r} status={self.status}>"


class Material(Base):
    """资料/报价库：同一 material_no 的多条记录表示多个版本。"""

    __tablename__ = "materials"
    __table_args__ = (
        UniqueConstraint("material_no", "version_no", name="uq_materials_no_version"),
        Index("ix_materials_type_enabled", "type", "enabled"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    material_no: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    type: Mapped[str] = mapped_column(String(32), nullable=False, default="paper", index=True)
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    source: Mapped[str] = mapped_column(String(200), nullable=False, default="")
    version_no: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    content_json: Mapped[str] = mapped_column(Text, nullable=False, default="{}")
    enabled: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    created_at: Mapped[str] = mapped_column(String(40), nullable=False, default=now_iso)

    def __repr__(self) -> str:  # pragma: no cover
        return f"<Material id={self.id} no={self.material_no!r} v={self.version_no} type={self.type}>"


class TaskMaterial(Base):
    """任务 ↔ 资料 关联表。"""

    __tablename__ = "task_materials"
    __table_args__ = (
        UniqueConstraint("task_id", "material_id", name="uq_task_materials_task_material"),
        Index("ix_task_materials_task", "task_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    task_id: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    material_id: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    link_role: Mapped[str] = mapped_column(String(32), nullable=False, default="related")
    created_at: Mapped[str] = mapped_column(String(40), nullable=False, default=now_iso)


class Rule(Base):
    """业务规则库（校验规则 / 成本口径）。"""

    __tablename__ = "rules"
    __table_args__ = (Index("ix_rules_business_type_enabled", "business_type", "enabled"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    rule_code: Mapped[str] = mapped_column(String(64), unique=True, nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    business_type: Mapped[str] = mapped_column(String(32), nullable=False, default="print_cost")
    rule_type: Mapped[str] = mapped_column(String(32), nullable=False, default="spec")
    content_json: Mapped[str] = mapped_column(Text, nullable=False, default="{}")
    severity: Mapped[str] = mapped_column(String(16), nullable=False, default="error")
    source: Mapped[str] = mapped_column(String(200), nullable=False, default="")
    enabled: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    created_at: Mapped[str] = mapped_column(String(40), nullable=False, default=now_iso)


class AiTask(Base):
    """AI 运行任务（一次运行一条，重试新建）。"""

    __tablename__ = "ai_tasks"
    __table_args__ = (Index("ix_ai_tasks_business_task", "business_task_id"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    business_task_id: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default=AI_PENDING, index=True)
    model_mode: Mapped[str] = mapped_column(String(16), nullable=False, default="mock")
    model_name: Mapped[str] = mapped_column(String(128), nullable=False, default="")
    input_snapshot_json: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    counters_json: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    error_code: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    error_message: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    started_at: Mapped[Optional[str]] = mapped_column(String(40), nullable=True)
    finished_at: Mapped[Optional[str]] = mapped_column(String(40), nullable=True)
    created_by: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_at: Mapped[str] = mapped_column(String(40), nullable=False, default=now_iso)


class ToolCallRecord(Base):
    """工具调用执行记录（每次调用一条，seq 连续）。"""

    __tablename__ = "tool_call_records"
    __table_args__ = (
        Index("ix_tool_calls_ai_task_seq", "ai_task_id", "seq"),
        Index("ix_tool_calls_business_task", "business_task_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    ai_task_id: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    business_task_id: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    seq: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    tool_name: Mapped[str] = mapped_column(String(64), nullable=False)
    tool_type: Mapped[str] = mapped_column(String(32), nullable=False, default="retrieval")
    args_json: Mapped[str] = mapped_column(Text, nullable=False, default="{}")
    result_summary: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    source_ids_json: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default=TOOL_STATUS_SUCCESS)
    error_code: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    latency_ms: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_at: Mapped[str] = mapped_column(String(40), nullable=False, default=now_iso)


class AiResult(Base):
    """AI 运行结果（每次成功运行写一条；不自动生成业务版本）。"""

    __tablename__ = "ai_results"
    __table_args__ = (Index("ix_ai_results_business_task", "business_task_id"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    ai_task_id: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    business_task_id: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    content_md: Mapped[str] = mapped_column(Text, nullable=False, default="")
    structured_json: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    basis_refs_json: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    basis_ok: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    uncertain_items_json: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    missing_data_json: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[str] = mapped_column(String(40), nullable=False, default=now_iso)


class ResultVersion(Base):
    """业务结果版本（只增不改；已提交/已通过版本冻结）。"""

    __tablename__ = "result_versions"
    __table_args__ = (
        UniqueConstraint("business_task_id", "version_no", name="uq_result_versions_task_version"),
        Index("ix_result_versions_business_task", "business_task_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    business_task_id: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    version_no: Mapped[int] = mapped_column(Integer, nullable=False)
    source: Mapped[str] = mapped_column(String(16), nullable=False, default=VERSION_SOURCE_MANUAL)
    content_md: Mapped[str] = mapped_column(Text, nullable=False, default="")
    structured_json: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    basis_refs_json: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    change_note: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)
    created_by: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_at: Mapped[str] = mapped_column(String(40), nullable=False, default=now_iso)


class ReviewRecord(Base):
    """审核记录（通过/退回，含意见、审核人、时间）。"""

    __tablename__ = "review_records"
    __table_args__ = (Index("ix_review_records_business_task", "business_task_id"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    business_task_id: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    version_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    action: Mapped[str] = mapped_column(String(16), nullable=False)
    opinion: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    reviewer_id: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    created_at: Mapped[str] = mapped_column(String(40), nullable=False, default=now_iso)


__all__ = [
    "Base",
    "User",
    "BusinessTask",
    "Material",
    "TaskMaterial",
    "Rule",
    "AiTask",
    "ToolCallRecord",
    "AiResult",
    "ResultVersion",
    "ReviewRecord",
    "ROLE_OPERATOR",
    "ROLE_REVIEWER",
    "ROLE_NAMES",
    "TASK_PENDING",
    "TASK_PENDING_REVIEW",
    "TASK_REJECTED",
    "TASK_COMPLETED",
    "TASK_STATUSES",
    "TASK_STATUS_LABELS",
    "TASK_EDITABLE_STATUSES",
    "AI_PENDING",
    "AI_RUNNING",
    "AI_SUCCEEDED",
    "AI_FAILED",
    "AI_STATUSES",
    "AI_STATUS_LABELS",
    "MATERIAL_TYPES",
    "MATERIAL_TYPE_LABELS",
    "TOOL_STATUS_SUCCESS",
    "TOOL_STATUS_FAILED",
    "TOOL_STATUS_SKIPPED",
    "VERSION_SOURCE_AI",
    "VERSION_SOURCE_MANUAL",
    "REVIEW_APPROVE",
    "REVIEW_REJECT",
]
