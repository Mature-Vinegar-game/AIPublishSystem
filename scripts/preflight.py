# -*- coding: utf-8 -*-
"""启动前环境预检（start.bat 第一步调用）。

检查项：
1. Python 版本（≥3.10）
2. 依赖可 import（vendor/ 或已安装）
3. .env 是否存在
4. 配置可读取（LLM_MODE / 模型 / Key 是否配置，Key 只判断不打印）
5. data/ 与 logs/ 目录可写、SQLite 可连接
6. 8000 端口是否空闲

退出码：0 = 可启动（有警告但可继续）；1 = 存在阻断问题。
"""

from __future__ import annotations

import socket
import sys
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
VENDOR = ROOT / "vendor"
if VENDOR.is_dir() and str(VENDOR) not in sys.path:
    sys.path.insert(0, str(VENDOR))

try:  # 避免 Windows 旧控制台编码导致中文打印失败
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:  # pragma: no cover
    pass

errors: list[str] = []
warnings: list[str] = []


def line(char: str = "-", length: int = 64) -> None:
    print(char * length)


def ok(message: str) -> None:
    print(f"  [OK]   {message}")


def warn(message: str) -> None:
    warnings.append(message)
    print(f"  [WARN] {message}")


def fail(message: str) -> None:
    errors.append(message)
    print(f"  [FAIL] {message}")


def check_python() -> None:
    version = sys.version_info
    print(f"[1/6] Python 版本：{version.major}.{version.minor}.{version.micro}（{sys.executable}）")
    if version < (3, 10):
        fail("需要 Python 3.10 及以上版本")
    else:
        ok("Python 版本满足要求（≥3.10）")


def check_dependencies() -> None:
    print("[2/6] 依赖检查（vendor/ 或已安装）")
    required = [
        ("fastapi", "fastapi"),
        ("starlette", "starlette"),
        ("uvicorn", "uvicorn"),
        ("pydantic", "pydantic"),
        ("sqlalchemy", "sqlalchemy"),
        ("jinja2", "jinja2"),
        ("dotenv", "python-dotenv"),
        ("multipart", "python-multipart"),
        ("httpx", "httpx"),
    ]
    missing: list[str] = []
    for module_name, package_name in required:
        try:
            __import__(module_name)
        except Exception:
            missing.append(package_name)
    if missing:
        fail("缺少依赖：" + ", ".join(missing))
        print("         安装：python -m pip install -r requirements.txt --target vendor")
    else:
        ok(f"全部 {len(required)} 个依赖可导入")
    try:
        import pytest  # noqa: F401

        ok("pytest 可用（可执行 python -m pytest tests -q）")
    except Exception:
        warn("pytest 未安装，跳过自测（不影响运行）")


def check_env_file() -> None:
    print("[3/6] 配置文件检查")
    env_file = ROOT / ".env"
    if env_file.exists():
        ok(f".env 存在（{env_file}）")
    else:
        warn(".env 不存在，将使用默认配置（建议复制 .env.example 为 .env 并填写）")


def check_settings() -> None:
    print("[4/6] 配置与模型检查")
    from app.config import settings

    mode = settings.llm_mode
    print(f"       APP_ENV={settings.app_env}  LLM_MODE={mode}  LLM_MODEL={settings.llm_model}")
    if mode == "real" and not settings.key_configured:
        warn("LLM_MODE=real 但 LLM_API_KEY 未配置（或仍是占位符），AI 调用会失败；开发可改 LLM_MODE=mock")
    elif mode == "real":
        ok("LLM_MODE=real 且 Key 已配置（Key 内容不打印）")
    else:
        warn("LLM_MODE=mock：AI 将返回确定性模拟结果，页面/记录会标注 mock（验收需切回 real）")
    if not settings.secret_key or settings.secret_key.startswith("replace-with"):
        warn("SECRET_KEY 仍是默认值/占位符，演示前请替换为随机串")


def check_storage() -> None:
    print("[5/6] 目录与数据库检查")
    from app.config import settings

    for directory, label in ((ROOT / "data", "data/"), (ROOT / "logs", "logs/")):
        try:
            directory.mkdir(parents=True, exist_ok=True)
            probe = directory / ".write_test"
            probe.write_text("ok", encoding="utf-8")
            probe.unlink()
            ok(f"{label} 可写（{directory}）")
        except Exception as exc:
            fail(f"{label} 不可写：{exc}")

    try:
        from sqlalchemy import text

        from app.db import engine

        with engine.connect() as connection:
            connection.execute(text("SELECT 1"))
        ok(f"数据库可连接：{settings.resolved_database_url()}")
    except Exception as exc:
        fail(f"数据库不可连接：{exc}")
        traceback.print_exc()


def check_port(host: str = "127.0.0.1", port: int = 8000) -> None:
    print(f"[6/6] 端口检查：{host}:{port}")
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.settimeout(1.0)
    try:
        sock.bind((host, port))
        ok(f"端口 {port} 空闲")
    except OSError:
        warn(f"端口 {port} 已被占用，可用 --port 8001 启动，或先关闭占用进程")
    finally:
        sock.close()


def main() -> int:
    line("=")
    print(" 出版业务 AI 系统 - 环境预检")
    print(f" 项目目录：{ROOT}")
    line("=")
    check_python()
    check_dependencies()
    check_env_file()
    if not errors:
        try:
            check_settings()
            check_storage()
        except Exception:
            fail("读取配置/初始化数据库时发生异常")
            traceback.print_exc()
    check_port()

    line("=")
    if errors:
        print(f"预检未通过：{len(errors)} 个阻断问题，{len(warnings)} 个警告")
        for item in errors:
            print(f"  - {item}")
        line("=")
        return 1
    if warnings:
        print(f"预检通过（{len(warnings)} 个警告，不阻断启动）：")
        for item in warnings:
            print(f"  - {item}")
    else:
        print("预检全部通过，可以启动服务。")
    line("=")
    return 0


if __name__ == "__main__":
    sys.exit(main())
