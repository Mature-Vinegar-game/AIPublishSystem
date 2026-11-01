# -*- coding: utf-8 -*-
"""同步文档口径：接口 23→24、限额 6/8/30/3000/90 → 10/8/30/3000/120。"""
import io
import sys
from pathlib import Path

ROOT = Path(r"F:\deepseek workarea\出版AI系统")
FILES = [ROOT / "README.md", ROOT / "BUILD_SPEC.md"] + sorted((ROOT / "docs").glob("*.md"))

REPLACEMENTS = [
    ("23 个 REST", "24 个 REST"),
    ("23 个接口", "24 个接口"),
    ("23 接口", "24 接口"),
    ("23 条一致", "24 条一致"),
    ("**23**", "**24**"),
    ("6/8/30s/3000ms/90s", "10/8/30s/3000ms/120s"),
    ("6/8/30/3000/90/10", "10/8/30/3000/120/10"),
    ("MAX_TOOL_CALLS=6", "MAX_TOOL_CALLS=10"),
    ("AI_RUN_TIMEOUT_S=90", "AI_RUN_TIMEOUT_S=120"),
    ("工具 6 次", "工具 10 次"),
    ("工具 ≤6 次", "工具 ≤10 次"),
    ("≤6 次工具调用", "≤10 次工具调用"),
    ("≤8 轮 · ≤6 次", "≤8 轮 · ≤10 次"),
    ("≤6 次", "≤10 次"),
    ("6 次", "10 次"),
    ("90 秒", "120 秒"),
    ("90s", "120s"),
    ("90 s", "120 s"),
    ("≥90s", "≥120s"),
    ("≤90s", "≤120s"),
    ("90 s/任务", "120 s/任务"),
]

changed = []
for path in FILES:
    text = io.open(path, encoding="utf-8").read()
    original = text
    for old, new in REPLACEMENTS:
        text = text.replace(old, new)
    if text != original:
        io.open(path, "w", encoding="utf-8").write(text)
        changed.append(path.name)

print("changed:", len(changed))
for name in changed:
    print(" -", name)
