"""API 层公共能力：统一响应包、错误抛出、trace_id。

所有成功响应形如：{"code": 0, "message": "ok", "data": ..., "trace_id": "..."}
错误通过 HTTPException(status_code, detail={"code","message","data"?}) 抛出，
由 app.main 的全局异常处理器转为同一信封。
"""

from __future__ import annotations

from typing import Any, Optional

from fastapi import HTTPException

from app.config import trace_id_ctx


def current_trace_id() -> str:
    return trace_id_ctx.get() or "-"


def success(data: Any = None, message: str = "ok") -> dict:
    """成功响应信封（code 固定 0）。"""
    return {"code": 0, "message": message, "data": data, "trace_id": current_trace_id()}


def api_error(
    status_code: int,
    message: str,
    code: Optional[int] = None,
    data: Any = None,
) -> None:
    """抛出统一错误（永不返回）。"""
    detail: dict = {"code": code if code is not None else status_code, "message": message}
    if data is not None:
        detail["data"] = data
    raise HTTPException(status_code=status_code, detail=detail)


def bad_request(message: str, data: Any = None) -> None:
    api_error(400, message, data=data)


def not_found(message: str = "对象不存在或不在你的权限范围内") -> None:
    api_error(404, message)


def conflict(message: str, data: Any = None) -> None:
    api_error(409, message, data=data)


__all__ = ["success", "api_error", "bad_request", "not_found", "conflict", "current_trace_id"]
