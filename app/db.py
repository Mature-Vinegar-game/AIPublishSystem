"""数据库访问层：SQLAlchemy 2.x + SQLite（WAL / busy_timeout / foreign_keys）。

对外契约（S2/S3 按此依赖）：
    engine                        -> SQLAlchemy Engine（SQLite，check_same_thread=False）
    SessionLocal                  -> sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    get_db()                      -> FastAPI 依赖生成器，逐请求创建/关闭 Session
    init_db()                     -> 建表（幂等）
    Base                          -> 声明式模型基类
附加：session_scope() 上下文管理器（后台线程/脚本使用）、now_iso() 统一时间格式。
"""

from __future__ import annotations

import logging
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Iterator, Optional

from sqlalchemy import create_engine, event
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from app.config import settings

logger = logging.getLogger(__name__)


def now_iso() -> str:
    """当前时间（ISO8601，带本地时区偏移，精确到秒）。"""
    return datetime.now().astimezone().replace(microsecond=0).isoformat()


# ---------- 引擎 ----------
def _build_engine():
    url = settings.resolved_database_url()
    is_sqlite = url.lower().startswith("sqlite")

    if is_sqlite:
        db_file: Optional[Path] = settings.database_file()
        if db_file is not None:
            db_file.parent.mkdir(parents=True, exist_ok=True)
        connect_args = {"check_same_thread": False}
        return create_engine(url, connect_args=connect_args), is_sqlite

    return create_engine(url, pool_pre_ping=True), is_sqlite


engine, _IS_SQLITE = _build_engine()


if _IS_SQLITE:

    @event.listens_for(engine, "connect")
    def _set_sqlite_pragma(dbapi_connection, _connection_record):  # noqa: ANN001
        """每条连接开启 WAL、忙等待 5s、外键约束。"""
        cursor = dbapi_connection.cursor()
        try:
            cursor.execute("PRAGMA journal_mode=WAL")
            cursor.execute("PRAGMA busy_timeout=5000")
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.execute("PRAGMA synchronous=NORMAL")
        finally:
            cursor.close()


# 与契约完全一致：autoflush=False、expire_on_commit=False（提交后对象仍可读）
SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


class Base(DeclarativeBase):
    """所有 ORM 模型的声明式基类。"""


def get_db() -> Iterator[Session]:
    """FastAPI 依赖：逐请求创建 Session，请求结束关闭。"""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


@contextmanager
def session_scope() -> Iterator[Session]:
    """后台线程 / 脚本使用：自动关闭，异常时回滚。"""
    db = SessionLocal()
    try:
        yield db
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def init_db() -> None:
    """建表（幂等）。导入 models 以注册全部映射类。"""
    from app import models  # noqa: F401  （延迟导入避免循环依赖）

    Base.metadata.create_all(bind=engine)
    logger.info("数据库初始化完成：%s（共 %d 张表）", settings.resolved_database_url(), len(Base.metadata.tables))


__all__ = [
    "engine",
    "SessionLocal",
    "Base",
    "get_db",
    "session_scope",
    "init_db",
    "now_iso",
]
