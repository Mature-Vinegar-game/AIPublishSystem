# -*- coding: utf-8 -*-
"""S2 工具单元测试（BUILD_SPEC §5）。

不依赖演示数据库：在临时 SQLite 文件中建表并写入测试资料/规则，
通过 monkeypatch 把 ``app.agent.tools.SessionLocal`` 指向测试 sessionmaker，
直接调用 validate_spec / calculate_cost / search_quotes / get_rules。

运行：python -m pytest tests/test_tools.py -q
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

# 兼容离线部署：项目根 vendor/ 在 sys.path 末尾（避免 shadow pytest 自身依赖）
_ROOT = Path(__file__).resolve().parent.parent
_VENDOR = _ROOT / "vendor"
if _VENDOR.is_dir() and str(_VENDOR) not in sys.path:
    sys.path.append(str(_VENDOR))

pytest.importorskip("sqlalchemy")

try:
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    from app.agent import tools
    from app.db import Base
    from app.models import Material, Rule
except Exception as exc:  # pragma: no cover - S1 依赖未就绪时跳过
    pytest.skip(f"工具测试依赖未就绪：{exc}", allow_module_level=True)


def _json(value) -> str:
    return json.dumps(value, ensure_ascii=False)


def _add_material(db, material_no: str, material_type: str, title: str, content: dict, version_no: int = 1):
    db.add(
        Material(
            material_no=material_no,
            type=material_type,
            title=title,
            source="pytest 测试数据",
            version_no=version_no,
            content_json=_json(content),
            enabled=1,
            created_at="2026-11-01T00:00:00+08:00",
        )
    )


def _add_rule(db, rule_code: str, name: str, content: dict, rule_type: str = "spec", severity: str = "error"):
    db.add(
        Rule(
            rule_code=rule_code,
            name=name,
            business_type="print_cost",
            rule_type=rule_type,
            content_json=_json(content),
            severity=severity,
            source="pytest 测试数据",
            enabled=1,
            created_at="2026-11-01T00:00:00+08:00",
        )
    )


@pytest.fixture()
def tool_env(tmp_path, monkeypatch):
    """临时 SQLite + 测试资料/规则，并替换 tools.SessionLocal。"""
    db_file = (tmp_path / "test_tools.db").as_posix()
    engine = create_engine(
        f"sqlite:///{db_file}",
        connect_args={"check_same_thread": False},
    )
    Base.metadata.create_all(engine)
    TestSession = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)

    db = TestSession()
    try:
        _add_material(
            db,
            "PAPER-001",
            "paper",
            "胶版纸80g",
            {"price_per_ream": 265.0, "format_fit": ["16开", "32开", "大32开"]},
        )
        _add_material(
            db,
            "PAPER-002",
            "paper",
            "铜版纸200g",
            {"price_per_ream": 620.0, "format_fit": ["16开"]},
        )
        _add_material(
            db,
            "PAPER-V1",
            "paper",
            "版本纸",
            {"price_per_ream": 100.0, "format_fit": ["16开", "32开"]},
            version_no=1,
        )
        _add_material(
            db,
            "PAPER-V1",
            "paper",
            "版本纸",
            {"price_per_ream": 120.0, "format_fit": ["16开", "32开"]},
            version_no=2,
        )
        _add_material(
            db,
            "RATE-001",
            "rate",
            "印刷费率（单色千印张）",
            {"price_per_1000_sheets_per_color": 22.0, "plate_fee_per_color": 80.0},
        )
        _add_material(db, "BIND-001", "binding", "胶装", {"price_per_1000": 180.0})
        _add_material(db, "BIND-002", "binding", "骑马订", {"price_per_1000": 120.0})
        _add_material(db, "BIND-003", "binding", "精装", {"price_per_1000": 480.0})
        _add_material(db, "PROC-001", "process", "覆膜", {"price_per_1000": 180.0})
        _add_material(db, "PROC-002", "process", "UV", {"price_per_1000": 200.0})

        _add_rule(
            db,
            "RULE-FMT-001",
            "开本必须在白名单内",
            {"field": "format", "type": "enum", "allowed": ["16开", "32开", "大32开"]},
        )
        _add_rule(
            db,
            "RULE-SHEET-002",
            "印张上限 50",
            {"field": "print_sheet", "type": "range", "min": 1, "max": 50},
        )
        _add_rule(db, "RULE-QTY-004", "印数下限 500 册", {"field": "qty", "type": "range", "min": 500})
        _add_rule(db, "RULE-QTY-005", "印数必须为整数", {"field": "qty", "type": "integer"})
        _add_rule(
            db,
            "RULE-BIND-006",
            "装订方式必须在白名单内",
            {"field": "binding", "type": "enum", "allowed": ["胶装", "骑马订", "精装"]},
        )
        _add_rule(
            db,
            "RULE-BIND-007",
            "骑马订与印张匹配",
            {
                "field": "binding",
                "type": "dependency",
                "when": {"binding": "骑马订"},
                "max_print_sheet": 8,
                "message": "骑马订最多 8 印张，超过请改胶装",
            },
        )
        _add_rule(
            db,
            "RULE-COLOR-011",
            "色数范围 0-4",
            {"field": "color_pages", "type": "range", "min": 0, "max": 4},
        )
        _add_rule(
            db,
            "RULE-REQ-013",
            "成本测算必填参数",
            {
                "type": "required",
                "fields": ["format", "print_sheet", "qty", "paper_type", "binding", "color_pages"],
            },
        )
        _add_rule(
            db,
            "RULE-PROCESS-012",
            "工艺必须在白名单内（可空）",
            {"field": "process", "type": "enum", "allowed": ["", "覆膜", "UV", "烫金"]},
        )
        db.commit()
    finally:
        db.close()

    monkeypatch.setattr(tools, "SessionLocal", TestSession)
    yield TestSession
    engine.dispose()


DEMO_PARAMS = {
    "format": "16开",
    "print_sheet": 12,
    "qty": 3000,
    "paper_type": "胶版纸80g",
    "binding": "胶装",
    "color_pages": 4,
    "process": "覆膜",
}


# ---------------------------------------------------------------------------
# validate_spec
# ---------------------------------------------------------------------------


def test_validate_spec_accepts_demo_params(tool_env):
    result = tools.validate_spec(DEMO_PARAMS)
    assert result["ok"] is True
    assert result["data"]["valid"] is True, result["data"]["issues"]
    assert result["data"]["issues"] == []
    normalized = result["data"]["normalized"]
    assert normalized["qty"] == 3000
    assert normalized["print_sheet"] == 12
    assert normalized["color_pages"] == 4
    assert result["source"]["ids"] == []


def test_validate_spec_reports_invalid_fields(tool_env):
    params = {
        "format": "8开",
        "print_sheet": 60,
        "qty": 300,
        "paper_type": "不存在的纸",
        "binding": "线装",
        "color_pages": 5,
        "process": "烫金",
    }
    result = tools.validate_spec(params)
    assert result["ok"] is True
    assert result["data"]["valid"] is False
    fields = {issue["field"] for issue in result["data"]["issues"]}
    assert {"format", "print_sheet", "qty", "paper_type", "binding", "color_pages"} <= fields


def test_validate_spec_dependency_rule_blocks_saddle_stitch(tool_env):
    params = dict(DEMO_PARAMS, binding="骑马订", print_sheet=12)
    result = tools.validate_spec(params)
    assert result["data"]["valid"] is False
    assert any(issue.get("rule_code") == "RULE-BIND-007" for issue in result["data"]["issues"])


def test_validate_spec_paper_format_fit(tool_env):
    params = dict(DEMO_PARAMS, format="32开", paper_type="铜版纸200g")
    result = tools.validate_spec(params)
    assert result["data"]["valid"] is False
    assert any(issue["field"] == "format" and "不适配" in issue["message"] for issue in result["data"]["issues"])


def test_validate_spec_non_integer_qty(tool_env):
    result = tools.validate_spec(dict(DEMO_PARAMS, qty=1500.5))
    assert result["data"]["valid"] is False
    assert any(issue["field"] == "qty" for issue in result["data"]["issues"])


# ---------------------------------------------------------------------------
# calculate_cost
# ---------------------------------------------------------------------------


def test_calculate_cost_demo(tool_env):
    result = tools.calculate_cost(DEMO_PARAMS)
    assert result["ok"] is True, result.get("error_message")
    data = result["data"]
    assert data["currency"] == "CNY"
    assert data["missing"] == []
    assert data["total"] == 14638.0
    assert data["per_copy"] == 4.88
    assert len(data["line_items"]) == 5
    assert {item["name"] for item in data["line_items"]} == {"纸张费", "印刷费", "制版费", "装订费", "工艺费（覆膜）"}
    for item in data["line_items"]:
        assert isinstance(item["formula"], str) and "=" in item["formula"]
        assert isinstance(item["amount"], (int, float))
    assert round(sum(item["amount"] for item in data["line_items"]), 2) == data["total"]
    assert set(result["source"]["ids"]) == {"PAPER-001", "RATE-001", "BIND-001", "PROC-001"}

    paper_item = next(item for item in data["line_items"] if item["name"] == "纸张费")
    assert paper_item["amount"] == 10070.0
    assert "38令" in paper_item["formula"]


def test_calculate_cost_single_color_without_process(tool_env):
    params = {
        "format": "16开",
        "print_sheet": 3.5,
        "qty": 1000,
        "paper_type": "胶版纸80g",
        "binding": "胶装",
        "color_pages": 0,
        "process": "",
    }
    result = tools.calculate_cost(params)
    assert result["ok"] is True
    data = result["data"]
    amounts = {item["name"]: item["amount"] for item in data["line_items"]}
    assert amounts["纸张费"] == 1060.0  # ceil(3.675)=4 令 × 265
    assert amounts["印刷费"] == 77.0  # 1000/1000 × 3.5 × 22 × 1
    assert amounts["制版费"] == 80.0
    assert amounts["装订费"] == 180.0
    assert amounts["工艺费"] == 0.0
    assert data["total"] == 1397.0
    assert data["per_copy"] == 1.4


def test_calculate_cost_uses_latest_material_version(tool_env):
    params = {
        "format": "16开",
        "print_sheet": 1,
        "qty": 1000,
        "paper_type": "版本纸",
        "binding": "胶装",
        "color_pages": 0,
        "process": "无",
    }
    result = tools.calculate_cost(params)
    assert result["ok"] is True
    paper_item = next(item for item in result["data"]["line_items"] if item["name"] == "纸张费")
    # ceil(1000×1÷1000×1.05)=2 令；应取 v2 的 120 元/令 → 240 元
    assert paper_item["amount"] == 240.0


def test_calculate_cost_missing_paper(tool_env):
    params = dict(DEMO_PARAMS, paper_type="白卡纸250g")
    result = tools.calculate_cost(params)
    assert result["ok"] is False
    assert result["error_code"] == "DATA_NOT_FOUND"
    assert result["data"]["missing"]
    assert any("纸张单价" in item for item in result["data"]["missing"])


def test_calculate_cost_missing_process(tool_env):
    params = dict(DEMO_PARAMS, process="烫金")
    result = tools.calculate_cost(params)
    assert result["ok"] is False
    assert result["error_code"] == "DATA_NOT_FOUND"
    assert any("工艺单价" in item for item in result["data"]["missing"])


def test_calculate_cost_invalid_params(tool_env):
    result = tools.calculate_cost({"qty": 0, "print_sheet": 0})
    assert result["ok"] is False
    assert result["error_code"] == "TOOL_ARGS_INVALID"


# ---------------------------------------------------------------------------
# search_quotes / get_rules / schema 校验
# ---------------------------------------------------------------------------


def test_search_quotes_and_get_rules(tool_env):
    papers = tools.search_quotes(category="paper")
    assert papers["ok"] is True
    assert papers["data"]["items"]
    assert all(item["type"] == "paper" for item in papers["data"]["items"])
    assert "PAPER-001" in papers["source"]["ids"]

    hit = tools.search_quotes(keyword="铜版纸200g")
    assert hit["ok"] is True
    assert any(item["material_no"] == "PAPER-002" for item in hit["data"]["items"])

    missing = tools.search_quotes(keyword="不存在的资料XYZ")
    assert missing["ok"] is False
    assert missing["error_code"] == "DATA_NOT_FOUND"
    assert missing["data"]["items"] == []

    rules = tools.get_rules("print_cost")
    assert rules["ok"] is True
    codes = {rule["rule_code"] for rule in rules["data"]["rules"]}
    assert {"RULE-FMT-001", "RULE-QTY-004", "RULE-BIND-007"} <= codes
    assert "RULE-FMT-001" in rules["source"]["ids"]


def test_validate_tool_args_schema(tool_env):
    assert tools.validate_tool_args("search_quotes", {"keyword": "纸张", "category": "paper"}) == []
    assert tools.validate_tool_args("search_quotes", {"category": "book"})
    assert tools.validate_tool_args("search_quotes", {"unknown": 1})
    assert tools.validate_tool_args("validate_spec", {})
    assert tools.validate_tool_args("calculate_cost", {"params": "not-a-dict"})
    assert tools.validate_tool_args("unknown_tool", {})
