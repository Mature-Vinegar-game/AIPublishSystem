"""认证接口（BUILD_SPEC §4 #1-#2）。

POST /api/auth/login：兼容 JSON 与表单提交，成功后下发 HttpOnly 签名 Cookie。
GET  /api/auth/me  ：返回当前登录用户。
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session

from app.api import current_trace_id, success
from app.auth import authenticate, create_token, get_current_user, set_auth_cookie, user_public
from app.db import get_db

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/auth", tags=["认证"])


def _error_response(status_code: int, message: str) -> JSONResponse:
    return JSONResponse(
        status_code=status_code,
        content={
            "code": status_code,
            "message": message,
            "data": None,
            "trace_id": current_trace_id(),
        },
    )


async def _read_credentials(request: Request) -> tuple[str, str]:
    """同时兼容 JSON 与表单两种提交方式（页面/脚本/前端 fetch）。"""
    content_type = (request.headers.get("content-type") or "").lower()
    username = ""
    password = ""
    if "application/json" in content_type:
        try:
            payload = await request.json()
        except Exception:
            payload = {}
        if isinstance(payload, dict):
            username = str(payload.get("username") or "").strip()
            password = str(payload.get("password") or "")
    else:
        try:
            form = await request.form()
        except Exception:
            form = {}
        if hasattr(form, "get"):
            username = str(form.get("username") or "").strip()
            password = str(form.get("password") or "")
    return username, password


@router.post("/login", summary="登录并签发签名 Cookie")
async def api_login(request: Request, db: Session = Depends(get_db)):
    username, password = await _read_credentials(request)
    if not username or not password:
        return _error_response(400, "用户名和密码不能为空")

    user = authenticate(db, username, password)
    if user is None:
        logger.info("登录失败 username=%s", username)
        return _error_response(401, "用户名或密码错误")

    token = create_token(user.id, user.role_code)
    response = JSONResponse(success({"token": token, "user": user_public(user)}))
    set_auth_cookie(response, token)
    logger.info("登录成功 user_id=%s username=%s role=%s", user.id, user.username, user.role_code)
    return response


@router.get("/me", summary="当前登录用户")
def api_me(request: Request):
    from app.api import api_error

    user = get_current_user(request)
    if user is None:
        api_error(401, "未登录或登录已过期，请重新登录")
    return success(user)
