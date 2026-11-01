"""全局配置：从项目根目录 .env 读取环境变量，供 S1/S2/S3 共同使用。

对外契约（其他智能体按此依赖，不要改属性名）：
    from app.config import settings
    settings.app_env / database_url / secret_key
    settings.llm_mode / llm_base_url / llm_model / llm_api_key / llm_timeout_s
    settings.tool_timeout_ms / ai_run_timeout_s
    settings.max_tool_calls / max_llm_rounds / max_ai_runs

附加能力（非契约，可安全使用）：
    settings.limits             -> 五项限额字典（页面页脚/meta 接口复用）
    settings.key_configured     -> 模型 Key 是否已真实配置（不泄露 Key 内容）
    settings.is_demo            -> 是否 demo 环境（故障注入开关）
    settings.resolved_database_url() / settings.database_file()
    trace_id_ctx                -> 当前请求 trace_id 的 ContextVar（日志用）
"""

from __future__ import annotations

import os
from contextvars import ContextVar
from pathlib import Path
from typing import Optional

from dotenv import load_dotenv

# 项目根目录（app/ 的上一级），所有相对路径以此为准，避免受启动 CWD 影响
BASE_DIR: Path = Path(__file__).resolve().parent.parent
ENV_FILE: Path = BASE_DIR / ".env"

# 不覆盖已存在的进程环境变量（便于测试/命令行临时覆盖 DATABASE_URL 等）
load_dotenv(dotenv_path=ENV_FILE, override=False)

# 当前请求 trace_id（由 app.main 的中间件写入，日志 Formatter 读取）
trace_id_ctx: ContextVar[str] = ContextVar("trace_id", default="-")


def _get_str(name: str, default: str) -> str:
    raw = os.getenv(name)
    if raw is None:
        return default
    value = raw.strip()
    return value if value != "" else default


def _get_int(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None or raw.strip() == "":
        return default
    try:
        return int(float(raw.strip()))
    except (TypeError, ValueError):
        return default


def _get_float(name: str, default: float) -> float:
    raw = os.getenv(name)
    if raw is None or raw.strip() == "":
        return default
    try:
        return float(raw.strip())
    except (TypeError, ValueError):
        return default


class Settings:
    """项目运行配置（只读快照，进程启动后不再变化）。"""

    def __init__(self) -> None:
        self.project_root: Path = BASE_DIR
        self.env_file: Path = ENV_FILE

        self.app_env: str = _get_str("APP_ENV", "demo").lower()
        self.database_url: str = _get_str("DATABASE_URL", "sqlite:///./data/app.db")
        self.secret_key: str = _get_str("SECRET_KEY", "dev-secret-change-me-2026-11-01")

        self.llm_mode: str = _get_str("LLM_MODE", "mock").lower()
        self.llm_base_url: str = _get_str("LLM_BASE_URL", "https://api.deepseek.com/v1").rstrip("/")
        self.llm_model: str = _get_str("LLM_MODEL", "deepseek-chat")
        self.llm_api_key: str = _get_str("LLM_API_KEY", "")
        self.llm_timeout_s: int = _get_int("LLM_TIMEOUT_S", 30)

        self.tool_timeout_ms: int = _get_int("TOOL_TIMEOUT_MS", 3000)
        self.ai_run_timeout_s: int = _get_int("AI_RUN_TIMEOUT_S", 90)

        self.max_tool_calls: int = _get_int("MAX_TOOL_CALLS", 6)
        self.max_llm_rounds: int = _get_int("MAX_LLM_ROUNDS", 8)
        self.max_ai_runs: int = _get_int("MAX_AI_RUNS", 10)

    # ---------- 派生属性 ----------
    @property
    def is_demo(self) -> bool:
        return self.app_env == "demo"

    @property
    def key_configured(self) -> bool:
        """Key 是否已真实填写（占位符/空值都算未配置）。"""
        key = (self.llm_api_key or "").strip()
        if not key:
            return False
        lowered = key.lower()
        if "replace" in lowered or "please" in lowered or lowered in {"none", "null", "sk-xxx"}:
            return False
        return True

    @property
    def limits(self) -> dict:
        """五项限额（工具 6 次 / 模型 8 轮 / 模型 30s / 工具 3000ms / 总 90s + 单任务运行次数）。"""
        return {
            "max_tool_calls": self.max_tool_calls,
            "max_llm_rounds": self.max_llm_rounds,
            "llm_timeout_s": self.llm_timeout_s,
            "tool_timeout_ms": self.tool_timeout_ms,
            "ai_run_timeout_s": self.ai_run_timeout_s,
            "max_ai_runs": self.max_ai_runs,
        }

    # ---------- 数据库地址解析 ----------
    def resolved_database_url(self) -> str:
        """把 sqlite 相对路径解析为基于项目根目录的绝对路径，保证任意 CWD 下一致。"""
        url = self.database_url
        if not url.lower().startswith("sqlite"):
            return url
        scheme, sep, raw_path = url.partition(":///")
        if not sep or not raw_path:
            return url
        path = Path(raw_path)
        if path.is_absolute():
            return url
        absolute = (BASE_DIR / raw_path).resolve()
        return f"{scheme}:///{absolute.as_posix()}"

    def database_file(self) -> Optional[Path]:
        """sqlite 数据库文件路径；非 sqlite 或内存库返回 None。"""
        url = self.resolved_database_url()
        if not url.lower().startswith("sqlite"):
            return None
        _, sep, raw_path = url.partition(":///")
        if not sep or raw_path in ("", ":memory:"):
            return None
        return Path(raw_path)


# 单例配置对象（导入即加载）
settings = Settings()

__all__ = ["settings", "Settings", "BASE_DIR", "ENV_FILE", "trace_id_ctx"]
