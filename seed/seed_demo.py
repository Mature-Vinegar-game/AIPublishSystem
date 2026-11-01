# -*- coding: utf-8 -*-
"""演示数据初始化（幂等，可重复执行）。

写入内容：
- 账号：operator/123456（业务操作员 张伟）、reviewer/123456（审核员 李静）
- 资料：seed/paper_quotes.json（10 种纸张）、seed/process_quotes.json（3 工艺 + 3 装订 + 1 费率）
- 规则：seed/rules.json（15 条）
- 示例任务：BT-DEMO-0001（PENDING，关联 1 条纸张报价，参数 rule_codes 关联规则）

用法：python seed/seed_demo.py（start.bat 会自动执行）
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Optional, Tuple

# 允许直接 `python seed/seed_demo.py` 运行（把项目根与 vendor 加入 sys.path）
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

from app.auth import hash_password  # noqa: E402
from app.config import settings  # noqa: E402
from app.db import SessionLocal, init_db, now_iso  # noqa: E402
from app.models import (  # noqa: E402
    ROLE_OPERATOR,
    ROLE_REVIEWER,
    BusinessTask,
    Material,
    Rule,
    TaskMaterial,
    User,
)

SEED_DIR = Path(__file__).resolve().parent
DEFAULT_PASSWORD = "123456"

SEED_USERS = [
    {"username": "operator", "role_code": ROLE_OPERATOR, "display_name": "张伟"},
    {"username": "reviewer", "role_code": ROLE_REVIEWER, "display_name": "李静"},
]

DEMO_TASK_NO = "BT-DEMO-0001"


# ============================ 工具 ============================
def load_json(filename: str) -> list:
    path = SEED_DIR / filename
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, list):
        raise ValueError(f"{filename} 顶层必须是数组")
    return data


def _ensure_json_text(value) -> str:
    return json.dumps(value if value is not None else {}, ensure_ascii=False)


# ============================ 账号 ============================
def seed_users(db) -> Tuple[int, int]:
    created = 0
    skipped = 0
    for item in SEED_USERS:
        exists = db.query(User).filter(User.username == item["username"]).first()
        if exists is not None:
            skipped += 1
            continue
        password_hash, salt = hash_password(DEFAULT_PASSWORD)
        db.add(
            User(
                username=item["username"],
                password_hash=password_hash,
                salt=salt,
                role_code=item["role_code"],
                display_name=item["display_name"],
                created_at=now_iso(),
            )
        )
        created += 1
    db.commit()
    return created, skipped


# ============================ 资料 ============================
def seed_materials(db, items: list, default_type: Optional[str] = None) -> Tuple[int, int]:
    created = 0
    skipped = 0
    for item in items:
        material_no = str(item.get("material_no") or "").strip()
        title = str(item.get("title") or "").strip()
        if not material_no or not title:
            skipped += 1
            continue
        version_no = int(item.get("version_no") or 1)
        material_type = str(item.get("type") or default_type or "").strip()
        exists = (
            db.query(Material)
            .filter(Material.material_no == material_no, Material.version_no == version_no)
            .first()
        )
        if exists is not None:
            skipped += 1
            continue
        db.add(
            Material(
                material_no=material_no,
                type=material_type,
                title=title,
                source=str(item.get("source") or "seed 演示数据"),
                version_no=version_no,
                content_json=_ensure_json_text(item.get("content") or {}),
                enabled=1,
                created_at=now_iso(),
            )
        )
        created += 1
    db.commit()
    return created, skipped


# ============================ 规则 ============================
def seed_rules(db, items: list) -> Tuple[int, int]:
    created = 0
    skipped = 0
    for item in items:
        rule_code = str(item.get("rule_code") or "").strip()
        if not rule_code:
            skipped += 1
            continue
        exists = db.query(Rule).filter(Rule.rule_code == rule_code).first()
        if exists is not None:
            skipped += 1
            continue
        db.add(
            Rule(
                rule_code=rule_code,
                name=str(item.get("name") or rule_code),
                business_type=str(item.get("business_type") or "print_cost"),
                rule_type=str(item.get("rule_type") or "spec"),
                content_json=_ensure_json_text(item.get("content") or {}),
                severity=str(item.get("severity") or "error"),
                source=str(item.get("source") or "seed 演示数据"),
                enabled=1,
                created_at=now_iso(),
            )
        )
        created += 1
    db.commit()
    return created, skipped


# ============================ 示例任务 ============================
DEMO_PARAMS = {
    "book_title": "初中语文阅读训练（八年级）",
    "format": "16开",
    "print_sheet": 12,
    "qty": 3000,
    "paper_type": "胶版纸80g",
    "binding": "胶装",
    "color_pages": 4,
    "process": "覆膜",
    "note": "秋季教材配套用书，按四色印刷、封面覆膜测算",
    "rule_codes": ["RULE-FMT-001", "RULE-QTY-004", "RULE-SHEET-002"],
}


def seed_demo_task(db) -> str:
    exists = db.query(BusinessTask).filter(BusinessTask.task_no == DEMO_TASK_NO).first()
    if exists is not None:
        return f"{DEMO_TASK_NO}（已存在，跳过）"

    operator = db.query(User).filter(User.username == "operator").first()
    if operator is None:
        return "跳过（缺少 operator 账号）"

    paper = (
        db.query(Material)
        .filter(Material.material_no == "PAPER-001", Material.version_no == 1)
        .first()
    )

    task = BusinessTask(
        task_no=DEMO_TASK_NO,
        business_type="print_cost",
        title="《初中语文阅读训练（八年级）》印制成本测算",
        goal=(
            "按 16开、12 印张、3000 册、胶版纸80g、胶装、四色、封面覆膜 的参数，"
            "先校验印制参数的合规性，再依据资料库报价测算总成本与单册成本，并给出可追溯依据。"
        ),
        params_json=_ensure_json_text(DEMO_PARAMS),
        status="PENDING",
        revision=1,
        created_by=operator.id,
        created_at=now_iso(),
        updated_at=now_iso(),
    )
    db.add(task)
    db.flush()
    if paper is not None:
        db.add(
            TaskMaterial(
                task_id=task.id,
                material_id=paper.id,
                link_role="paper",
                created_at=now_iso(),
            )
        )
    db.commit()
    return f"{DEMO_TASK_NO}（已创建）"


# ============================ 入口 ============================
def run(verbose: bool = True) -> dict:
    init_db()
    db = SessionLocal()
    summary = {}
    try:
        user_created, user_skipped = seed_users(db)
        paper_created, paper_skipped = seed_materials(db, load_json("paper_quotes.json"), "paper")
        process_created, process_skipped = seed_materials(db, load_json("process_quotes.json"), None)
        rule_created, rule_skipped = seed_rules(db, load_json("rules.json"))
        task_result = seed_demo_task(db)

        summary = {
            "users": (user_created, user_skipped),
            "materials": (paper_created + process_created, paper_skipped + process_skipped),
            "rules": (rule_created, rule_skipped),
            "task": task_result,
            "database": settings.resolved_database_url(),
        }
    finally:
        db.close()

    if verbose:
        print("=" * 60)
        print("[seed] 演示数据初始化完成（幂等：已存在则跳过）")
        print(f"[seed] 账号：新增 {summary['users'][0]}，跳过 {summary['users'][1]}"
              "（operator/123456 张伟，reviewer/123456 李静）")
        print(f"[seed] 资料：新增 {summary['materials'][0]}，跳过 {summary['materials'][1]}")
        print(f"[seed] 规则：新增 {summary['rules'][0]}，跳过 {summary['rules'][1]}")
        print(f"[seed] 示例任务：{summary['task']}")
        print(f"[seed] 数据库：{summary['database']}")
        print("=" * 60)
    return summary


if __name__ == "__main__":
    run()
