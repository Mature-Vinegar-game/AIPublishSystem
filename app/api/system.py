"""系统信息接口（BUILD_SPEC §4 #23）。"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends
from sqlalchemy import text
from sqlalchemy.orm import Session

from app import APP_NAME, APP_VERSION
from app.api import success
from app.auth import require_user
from app.config import settings
from app.db import get_db, now_iso

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/system", tags=["系统"])


@router.get("/meta", summary="系统元信息（登录）：模型模式/Key 状态/限额")
def system_meta(user: dict = Depends(require_user), db: Session = Depends(get_db)):
    db_file = settings.database_file()
    db_status = "ok"
    try:
        db.execute(text("SELECT 1"))
    except Exception:  # pragma: no cover - 数据库异常时仍让接口可用
        logger.exception("系统信息：数据库连通性检查失败")
        db_status = "error"
    return success(
        {
            "app": APP_NAME,
            "version": APP_VERSION,
            "db": "sqlite" if db_file is not None else "external",
            "db_file": db_file.name if db_file is not None else "",
            "db_status": db_status,
            "llm_mode": settings.llm_mode,
            "model": settings.llm_model,
            "llm_base_url": settings.llm_base_url,
            "key_configured": settings.key_configured,
            "app_env": settings.app_env,
            "limits": settings.limits,
            "server_time": now_iso(),
        }
    )
