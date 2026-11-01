# -*- coding: utf-8 -*-
"""API 冒烟测试：登录 → 建任务 → 资料列表 → 系统 meta → 版本/审核闭环。

运行：python -m pytest tests -q
说明：使用临时 SQLite 库，不污染 data/app.db；依赖缺失或 S2/S3 文件未就绪时自动 skip。
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# 测试库放在 data/（已被 .gitignore 排除），必须在导入 app.config 之前设置；
# 使用独立文件名，避免污染演示库 data/app.db
DATA_DIR = ROOT / "data"
DATA_DIR.mkdir(parents=True, exist_ok=True)
TEST_DB_FILE = DATA_DIR / f"test_api_smoke_{os.getpid()}.db"
os.environ["APP_ENV"] = "demo"
os.environ["DATABASE_URL"] = f"sqlite:///{TEST_DB_FILE.as_posix()}"
os.environ["SECRET_KEY"] = "test-secret-key-for-pytest"
os.environ["LLM_MODE"] = "mock"

pytest.importorskip("fastapi")
pytest.importorskip("sqlalchemy")
pytest.importorskip("httpx")
pytest.importorskip("jinja2")

from fastapi.testclient import TestClient  # noqa: E402

try:
    from app.auth import hash_password
    from app.config import settings as _test_settings
    from app.db import SessionLocal, engine, init_db
    from app.main import app
    from app.models import ROLE_OPERATOR, ROLE_REVIEWER, User
except Exception as exc:  # pragma: no cover - 等待 S2/S3 文件就绪
    pytest.skip(f"应用尚无法导入（等待 S2/S3 文件就绪）：{exc}", allow_module_level=True)

# 若其它测试模块先导入了 app.config，环境变量不会再生效；此时跳过，避免污染演示库
_db_file = _test_settings.database_file()
if _db_file is None or _db_file.resolve() != TEST_DB_FILE.resolve():
    pytest.skip(
        "测试数据库未生效（app.config 已被其它测试模块提前导入），跳过以免污染 data/app.db",
        allow_module_level=True,
    )


PASSWORD = "123456"


@pytest.fixture(scope="module", autouse=True)
def prepared_app():
    """建表 + 创建两个演示账号 + 跑一次 lifespan（启动清理）。"""
    init_db()
    db = SessionLocal()
    try:
        for username, role_code, display_name in (
            ("operator", ROLE_OPERATOR, "张伟"),
            ("reviewer", ROLE_REVIEWER, "李静"),
        ):
            if db.query(User).filter(User.username == username).first() is None:
                password_hash, salt = hash_password(PASSWORD)
                db.add(
                    User(
                        username=username,
                        password_hash=password_hash,
                        salt=salt,
                        role_code=role_code,
                        display_name=display_name,
                    )
                )
        db.commit()
    finally:
        db.close()

    with TestClient(app):
        yield

    engine.dispose()
    for suffix in ("", "-wal", "-shm"):
        try:
            Path(str(TEST_DB_FILE) + suffix).unlink(missing_ok=True)
        except OSError:
            pass


@pytest.fixture()
def client():
    """每个测试独立 Cookie 容器。"""
    return TestClient(app)


def login(client: TestClient, username: str = "operator", password: str = PASSWORD) -> dict:
    response = client.post("/api/auth/login", json={"username": username, "password": password})
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["code"] == 0
    assert body["data"]["user"]["username"] == username
    return body["data"]


def test_login_and_me(client: TestClient):
    data = login(client)
    assert data["token"]

    me = client.get("/api/auth/me")
    assert me.status_code == 200
    body = me.json()
    assert body["code"] == 0
    assert body["data"]["username"] == "operator"
    assert body["data"]["role_code"] == "operator"
    assert body["trace_id"]


def test_unauthorized_and_role_guard(client: TestClient):
    anonymous = TestClient(app)
    response = anonymous.get("/api/auth/me")
    assert response.status_code == 401
    assert response.json()["code"] == 401

    login(client)
    # operator 不能审核（403）
    review = client.post("/api/business-tasks/1/review", json={"version_no": 1, "action": "APPROVE"})
    assert review.status_code == 403

    reviewer = TestClient(app)
    login(reviewer, "reviewer")
    # reviewer 不能创建任务（403）
    created = reviewer.post(
        "/api/business-tasks",
        json={"title": "审核员越权创建", "goal": "应被拒绝", "params": {}},
    )
    assert created.status_code == 403
    assert created.json()["code"] == 403


def test_task_result_review_flow(client: TestClient):
    """登录 → 建资料/任务 → 保存两版 → 提交 → 审核通过 → 版本可查。"""
    login(client)

    material = client.post(
        "/api/materials",
        json={
            "type": "paper",
            "title": "胶版纸80g",
            "source": "pytest 冒烟数据",
            "content": {"price_per_ream": 265.0, "grammage": 80, "format_fit": ["16开"]},
        },
    )
    assert material.status_code == 200, material.text
    material_id = material.json()["data"]["id"]

    task_response = client.post(
        "/api/business-tasks",
        json={
            "business_type": "print_cost",
            "title": "《API 冒烟测试图书》印制成本测算",
            "goal": "校验参数并测算总成本",
            "params": {
                "format": "16开",
                "print_sheet": 12,
                "qty": 3000,
                "paper_type": "胶版纸80g",
                "binding": "胶装",
                "color_pages": 4,
                "process": "覆膜",
            },
            "material_ids": [material_id],
        },
    )
    assert task_response.status_code == 200, task_response.text
    task = task_response.json()["data"]
    task_id = task["id"]
    assert task["task_no"].startswith("BT")
    assert task["status"] == "PENDING"
    assert task["revision"] == 1
    assert task["linked_materials"], "创建时应关联 1 条纸张资料"

    # 任务列表（operator 仅本人）
    tasks = client.get("/api/business-tasks")
    assert tasks.status_code == 200
    listing = tasks.json()["data"]
    assert listing["total"] >= 1
    assert any(item["id"] == task_id for item in listing["items"])

    # 资料列表
    materials = client.get("/api/materials", params={"type": "paper"})
    assert materials.status_code == 200
    assert any(item["title"] == "胶版纸80g" for item in materials.json()["data"]["items"])

    # 保存 v1、v2
    v1 = client.post(
        f"/api/business-tasks/{task_id}/results",
        json={"content_md": "# 测算\n人工版本一", "structured": {"total": 1000.0}, "change_note": "初稿"},
    )
    assert v1.status_code == 200, v1.text
    assert v1.json()["data"]["version_no"] == 1
    v2 = client.post(
        f"/api/business-tasks/{task_id}/results",
        json={"content_md": "# 测算\n人工版本二", "structured": {"total": 1200.0}, "change_note": "修订"},
    )
    assert v2.status_code == 200
    assert v2.json()["data"]["version_no"] == 2

    # 提交审核 v2
    submit = client.post(f"/api/business-tasks/{task_id}/submit", json={"version_no": 2})
    assert submit.status_code == 200, submit.text
    assert submit.json()["data"]["status"] == "PENDING_REVIEW"

    # 已提交版本冻结：再保存 → 409
    frozen = client.post(
        f"/api/business-tasks/{task_id}/results",
        json={"content_md": "冻结后不应保存"},
    )
    assert frozen.status_code == 409

    # 重复提交 → 409
    duplicate = client.post(f"/api/business-tasks/{task_id}/submit", json={"version_no": 2})
    assert duplicate.status_code == 409

    # reviewer 审核通过
    login(client, "reviewer")
    review = client.post(
        f"/api/business-tasks/{task_id}/review",
        json={"version_no": 2, "action": "APPROVE", "opinion": "参数与测算依据一致，通过"},
    )
    assert review.status_code == 200, review.text
    assert review.json()["data"]["status"] == "COMPLETED"

    # 版本仍可查（≥2 版）
    versions = client.get(f"/api/business-tasks/{task_id}/versions")
    assert versions.status_code == 200
    items = versions.json()["data"]
    assert len(items) >= 2
    assert {item["version_no"] for item in items} >= {1, 2}


def test_object_level_permission(client: TestClient):
    """operator 新建的 PENDING 任务，reviewer 不可见（404）。"""
    login(client)
    created = client.post(
        "/api/business-tasks",
        json={"title": "对象级权限测试任务", "goal": "reviewer 不可见", "params": {"qty": 1000}},
    )
    assert created.status_code == 200
    task_id = created.json()["data"]["id"]

    reviewer = TestClient(app)
    login(reviewer, "reviewer")
    response = reviewer.get(f"/api/business-tasks/{task_id}")
    assert response.status_code == 404
    assert response.json()["code"] == 404


def test_system_meta(client: TestClient):
    login(client)
    response = client.get("/api/system/meta")
    assert response.status_code == 200, response.text
    data = response.json()["data"]
    assert data["llm_mode"] == "mock"
    assert isinstance(data["key_configured"], bool)
    limits = data["limits"]
    for key in ("max_tool_calls", "max_llm_rounds", "llm_timeout_s", "tool_timeout_ms", "ai_run_timeout_s"):
        assert key in limits
    assert data["server_time"]


LOGIN_TEMPLATE = ROOT / "app" / "templates" / "login.html"


@pytest.mark.skipif(not LOGIN_TEMPLATE.exists(), reason="等待 S3 提供 app/templates/login.html")
def test_login_page_renders(client: TestClient):
    response = client.get("/login")
    assert response.status_code == 200
    assert "text/html" in response.headers.get("content-type", "")
