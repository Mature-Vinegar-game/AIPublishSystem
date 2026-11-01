"""认证与授权：PBKDF2 密码、HMAC-SHA256 签名 Cookie、角色依赖。

对外契约（S1/S2/S3 共用）：
    create_token(user_id:int, role_code:str) -> str        # uid|role|exp|sig
    verify_token(token:str) -> dict | None                 # {"user_id","role_code","exp"}
    get_current_user(request) -> dict | None               # 当前登录用户
    require_user / require_operator / require_reviewer     # FastAPI 依赖
    set_auth_cookie(response, token) / clear_auth_cookie(response)
    authenticate(db, username, password) -> User | None
    hash_password(password, salt=None) -> (hash_hex, salt)

Cookie 名固定 `auth_token`（HttpOnly，12 小时过期），SECRET_KEY 签名。
"""

from __future__ import annotations

import hashlib
import hmac
import logging
import os
import time
from typing import Optional

from fastapi import Depends, HTTPException, Request, Response
from sqlalchemy.orm import Session

from app.config import settings
from app.db import SessionLocal
from app.models import ROLE_NAMES, ROLE_OPERATOR, ROLE_REVIEWER, User

logger = logging.getLogger(__name__)

TOKEN_COOKIE = "auth_token"
TOKEN_TTL_SECONDS = 12 * 3600
PBKDF2_ITERATIONS = 100000


# ============================ 密码 ============================
def hash_password(password: str, salt: Optional[str] = None) -> tuple[str, str]:
    """PBKDF2-HMAC-SHA256 加盐哈希，返回 (十六进制摘要, salt)。"""
    salt = salt or os.urandom(16).hex()
    digest = hashlib.pbkdf2_hmac(
        "sha256",
        (password or "").encode("utf-8"),
        salt.encode("utf-8"),
        PBKDF2_ITERATIONS,
    )
    return digest.hex(), salt


def verify_password(password: str, password_hash: str, salt: str) -> bool:
    """恒定时间比较密码。"""
    if not password_hash or not salt:
        return False
    candidate, _ = hash_password(password, salt)
    return hmac.compare_digest(candidate, password_hash)


# ============================ Token ============================
def create_token(user_id: int, role_code: str) -> str:
    """签发 `uid|role|exp|sig` 形式的 HMAC-SHA256 令牌。"""
    expires_at = int(time.time()) + TOKEN_TTL_SECONDS
    payload = f"{int(user_id)}|{role_code}|{expires_at}"
    signature = hmac.new(
        settings.secret_key.encode("utf-8"),
        payload.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
    return f"{payload}|{signature}"


def verify_token(token: str) -> Optional[dict]:
    """校验签名与过期时间；失败返回 None。"""
    if not token or not isinstance(token, str):
        return None
    parts = token.strip().split("|")
    if len(parts) != 4:
        return None
    uid_raw, role_code, exp_raw, signature = parts
    try:
        user_id = int(uid_raw)
        expires_at = int(exp_raw)
    except (TypeError, ValueError):
        return None
    payload = f"{uid_raw}|{role_code}|{exp_raw}"
    expected = hmac.new(
        settings.secret_key.encode("utf-8"),
        payload.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
    if not hmac.compare_digest(expected, signature):
        return None
    if expires_at < int(time.time()):
        return None
    return {"user_id": user_id, "role_code": role_code, "exp": expires_at}


# ============================ 用户 ============================
def user_public(user) -> dict:
    """把 User ORM 对象（或已序列化 dict）转为前端可见的用户信息。"""
    if isinstance(user, dict):
        role_code = user.get("role_code", "")
        return {
            "id": user.get("id"),
            "username": user.get("username", ""),
            "role_code": role_code,
            "role_name": user.get("role_name") or ROLE_NAMES.get(role_code, role_code),
            "display_name": user.get("display_name", ""),
        }
    role_code = getattr(user, "role_code", "") or ""
    return {
        "id": getattr(user, "id", None),
        "username": getattr(user, "username", ""),
        "role_code": role_code,
        "role_name": ROLE_NAMES.get(role_code, role_code),
        "display_name": getattr(user, "display_name", "") or getattr(user, "username", ""),
    }


def authenticate(db: Session, username: str, password: str) -> Optional[User]:
    """按用户名+密码认证，成功返回 User，失败返回 None。"""
    username = (username or "").strip()
    if not username or password is None:
        return None
    user = db.query(User).filter(User.username == username).first()
    if user is None:
        return None
    if not verify_password(password, user.password_hash, user.salt):
        return None
    return user


def get_user_by_id(user_id: int) -> Optional[User]:
    """独立 Session 查询用户（请求级鉴权用，避免依赖注入耦合）。"""
    db = SessionLocal()
    try:
        return db.query(User).filter(User.id == user_id).first()
    except Exception:  # pragma: no cover - 极端情况：库未初始化
        logger.exception("查询用户失败 user_id=%s", user_id)
        return None
    finally:
        db.close()


def _extract_token(request: Request) -> Optional[str]:
    token = request.cookies.get(TOKEN_COOKIE)
    if token:
        return token
    authorization = request.headers.get("Authorization") or ""
    if authorization.lower().startswith("bearer "):
        return authorization[7:].strip()
    return None


def get_current_user(request: Request) -> Optional[dict]:
    """从 Cookie / Authorization 解析当前用户；未登录返回 None。"""
    cached = getattr(request.state, "current_user", None)
    if cached is not None:
        return cached
    token = _extract_token(request)
    if not token:
        return None
    payload = verify_token(token)
    if not payload:
        return None
    user = get_user_by_id(payload["user_id"])
    if user is None:
        return None
    current = user_public(user)
    try:
        request.state.current_user = current
    except Exception:  # pragma: no cover
        pass
    return current


# ============================ FastAPI 依赖 ============================
def _unauthorized(message: str = "未登录或登录已过期，请重新登录") -> HTTPException:
    return HTTPException(status_code=401, detail={"code": 401, "message": message})


def require_user(request: Request) -> dict:
    """依赖：必须登录。"""
    user = get_current_user(request)
    if user is None:
        raise _unauthorized()
    return user


def require_operator(user: dict = Depends(require_user)) -> dict:
    """依赖：业务操作员（创建/编辑/保存结果/提交/运行 AI）。"""
    if user.get("role_code") != ROLE_OPERATOR:
        raise HTTPException(
            status_code=403,
            detail={"code": 403, "message": "当前角色无此操作权限（需要业务操作员）"},
        )
    return user


def require_reviewer(user: dict = Depends(require_user)) -> dict:
    """依赖：审核员（仅审核通过/退回，不可运行 AI、不可创建任务）。"""
    if user.get("role_code") != ROLE_REVIEWER:
        raise HTTPException(
            status_code=403,
            detail={"code": 403, "message": "当前角色无此操作权限（需要审核员）"},
        )
    return user


# ============================ Cookie ============================
def set_auth_cookie(response: Response, token: str) -> None:
    response.set_cookie(
        key=TOKEN_COOKIE,
        value=token,
        max_age=TOKEN_TTL_SECONDS,
        httponly=True,
        samesite="lax",
        path="/",
    )


def clear_auth_cookie(response: Response) -> None:
    response.delete_cookie(key=TOKEN_COOKIE, path="/")


__all__ = [
    "TOKEN_COOKIE",
    "TOKEN_TTL_SECONDS",
    "hash_password",
    "verify_password",
    "create_token",
    "verify_token",
    "user_public",
    "authenticate",
    "get_user_by_id",
    "get_current_user",
    "require_user",
    "require_operator",
    "require_reviewer",
    "set_auth_cookie",
    "clear_auth_cookie",
]
