# -*- coding: utf-8 -*-
"""方案模式（print_cost_plan）单元测试。

使用独立临时 SQLite + monkeypatch tools.SessionLocal，避免依赖/污染演示库；
只验证新增方案工具，不改变原 print_cost 测试。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

pytest.importorskip("sqlalchemy")

try:
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    from app.agent import tools
    from app.db import Base
    from app.models import Material
except Exception as exc:  # pragma: no cover
    pytest.skip(f"方案模式测试依赖未就绪：{exc}", allow_module_level=True)


def _json(value):
    return json.dumps(value, ensure_ascii=False)


def _add_material(db, material_no: str, material_type: str, title: str, content: dict):
    db.add(
        Material(
            material_no=material_no,
            type=material_type,
            title=title,
            source="pytest 方案模式测试数据",
            version_no=1,
            content_json=_json(content),
            enabled=1,
            created_at="2026-11-04T00:00:00+08:00",
        )
    )


@pytest.fixture()
def plan_env(tmp_path, monkeypatch):
    db_file = (tmp_path / "test_plan.db").as_posix()
    engine = create_engine(f"sqlite:///{db_file}", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    TestSession = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    db = TestSession()
    try:
        _add_material(db, "PAPER-001", "paper", "胶版纸80g", {"price_per_ream": 265.0, "format_fit": ["16开", "32开", "大32开"]})
        _add_material(db, "BIND-001", "binding", "胶装", {"price_per_1000": 180.0})
        _add_material(db, "PROC-001", "process", "覆膜", {"price_per_1000": 180.0})
        _add_material(db, "RATE-001", "rate", "印刷费率（单色千印张）", {
            "price_per_1000_sheets_per_color": 22.0, "plate_fee_per_color": 80.0,
        })
        db.commit()
    finally:
        db.close()
    monkeypatch.setattr(tools, "SessionLocal", TestSession)
    yield TestSession
    engine.dispose()


def _plan_names(plan_mode: bool):
    return [item["function"]["name"] for item in tools.get_tool_schemas(plan_mode=plan_mode)]


def test_normal_mode_does_not_expose_plan_tool():
    assert "plan_candidates" not in _plan_names(False)
    assert "plan_candidates" in _plan_names(True)


def test_plan_candidates_budget_filter(plan_env):
    result = tools.plan_candidates(
        {"format": "16开", "print_sheet": 12, "qty": 3000, "color_pages": 4},
        budget_total=15000,
        max_plans=3,
    )
    assert result["ok"] is True
    data = result["data"]
    plans = data["plans"]
    assert 1 <= len(plans) <= 3
    assert data["feasible"] is True
    assert all(plan["total"] <= 15000 for plan in plans)
    assert all(plan["per_copy"] is not None for plan in plans)
    assert all(plan["source_ids"] for plan in plans)


def test_plan_candidates_infeasible_budget_returns_closest(plan_env):
    result = tools.plan_candidates(
        {"format": "16开", "print_sheet": 12, "qty": 3000, "color_pages": 4},
        budget_total=14000,
        max_plans=2,
    )
    assert result["ok"] is True
    data = result["data"]
    assert data["feasible"] is False
    assert len(data["plans"]) == 1
    plan = data["plans"][0]
    assert plan["total"] > 14000
    assert plan["within_budget"] is False
    assert result["warnings"]


def test_plan_candidates_requires_base_params():
    result = tools.plan_candidates({"format": "16开"}, budget_total=15000)
    assert result["ok"] is False
    assert result["error_code"] == "TOOL_ARGS_INVALID"
    assert result["data"]["missing"]
