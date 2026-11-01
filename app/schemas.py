"""Pydantic v2 请求模型（API 层入参校验）。

约定：
- 缺必填 / 类型错误由 FastAPI 抛出 RequestValidationError，全局处理器统一转为
  400 + `{code:400, message:"参数校验失败: ...", data:{fields:[...]}}`。
- 业务范围校验（状态、归属、白名单）在服务层完成，错误码见 BUILD_SPEC §4。
"""

from __future__ import annotations

from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator


class _Base(BaseModel):
    model_config = ConfigDict(extra="ignore", str_strip_whitespace=True)


# ---------------- 认证 ----------------
class LoginRequest(_Base):
    username: str = Field(..., min_length=1, max_length=64, description="用户名")
    password: str = Field(..., min_length=1, max_length=128, description="密码")


# ---------------- 业务任务 ----------------
class BusinessTaskCreate(_Base):
    business_type: str = Field(default="print_cost", max_length=32)
    title: str = Field(..., min_length=1, max_length=200)
    goal: str = Field(..., min_length=1, max_length=2000)
    params: Dict[str, Any] = Field(default_factory=dict)
    material_ids: List[int] = Field(default_factory=list)

    @field_validator("title", "goal")
    @classmethod
    def _not_blank(cls, value: str) -> str:
        if not value or not value.strip():
            raise ValueError("不能为空")
        return value.strip()


class BusinessTaskUpdate(_Base):
    business_type: Optional[str] = Field(default=None, max_length=32)
    title: Optional[str] = Field(default=None, min_length=1, max_length=200)
    goal: Optional[str] = Field(default=None, min_length=1, max_length=2000)
    params: Optional[Dict[str, Any]] = None
    expected_revision: Optional[int] = Field(default=None, ge=1, description="乐观锁版本号")

    @field_validator("title", "goal")
    @classmethod
    def _not_blank(cls, value: Optional[str]) -> Optional[str]:
        if value is not None and not value.strip():
            raise ValueError("不能为空")
        return value.strip() if value is not None else value


class MaterialLinkRequest(_Base):
    material_id: int = Field(..., ge=1)
    link_role: str = Field(default="related", max_length=32)


# ---------------- 结果 / 版本 / 审核 ----------------
class ResultCreateRequest(_Base):
    content_md: Optional[str] = Field(default=None, max_length=200000)
    structured: Optional[Dict[str, Any]] = None
    change_note: Optional[str] = Field(default=None, max_length=500)
    ai_task_id: Optional[int] = Field(default=None, ge=1, description="采用某次 AI 结果时传入")


class SubmitRequest(_Base):
    version_no: Optional[int] = Field(default=None, ge=1, description="不传则使用最新版本")
    comment: Optional[str] = Field(default=None, max_length=1000)


class ReviewRequest(_Base):
    version_no: Optional[int] = Field(default=None, ge=1)
    action: Literal["APPROVE", "REJECT"]
    opinion: Optional[str] = Field(default=None, max_length=2000)


# ---------------- 资料 / 规则 ----------------
class MaterialCreateRequest(_Base):
    type: Literal["paper", "process", "binding", "rate", "book"]
    title: str = Field(..., min_length=1, max_length=200)
    source: str = Field(default="手工录入", max_length=200)
    content: Any = Field(default_factory=dict, description="报价/资料内容，JSON 对象")
    material_no: Optional[str] = Field(default=None, max_length=64)
    version_no: Optional[int] = Field(default=None, ge=1)


class RuleCreateRequest(_Base):
    rule_code: str = Field(..., min_length=1, max_length=64)
    name: str = Field(..., min_length=1, max_length=200)
    business_type: str = Field(default="print_cost", max_length=32)
    rule_type: str = Field(default="spec", max_length=32)
    content: Any = Field(default_factory=dict)
    severity: str = Field(default="error", max_length=16)
    source: str = Field(default="", max_length=200)


__all__ = [
    "LoginRequest",
    "BusinessTaskCreate",
    "BusinessTaskUpdate",
    "MaterialLinkRequest",
    "ResultCreateRequest",
    "SubmitRequest",
    "ReviewRequest",
    "MaterialCreateRequest",
    "RuleCreateRequest",
]
