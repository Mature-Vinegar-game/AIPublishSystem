# -*- coding: utf-8 -*-
"""丰富资料库/规则库：8 纸张 + 3 工艺 + 2 装订 + 7 规则（幂等，可重复执行）。"""
import json
from pathlib import Path

ROOT = Path(r"F:\deepseek workarea\出版AI系统")
SEED = ROOT / "seed"

PAPER_SRC = "2026 年秋季教材印制纸张报价单（模拟数据，仅用于演示）"
PROC_SRC = "2026 年印后工艺报价单（模拟数据，仅用于演示）"
BIND_SRC = "2026 年装订报价单（模拟数据，仅用于演示）"
RULE_SRC = "出版印制业务规则（模拟数据，2026-11-01）"

NEW_PAPERS = [
    ("PAPER-011", "胶版纸60g", 220.0, 60, ["32开", "大32开"], "薄本工具书正文用纸，克重低"),
    ("PAPER-012", "胶版纸90g", 290.0, 90, ["16开", "32开", "大32开"], "加厚教辅/练习册正文用纸"),
    ("PAPER-013", "铜版纸105g", 370.0, 105, ["16开", "大32开"], "彩色内文轻量选择"),
    ("PAPER-014", "铜版纸250g", 700.0, 250, ["16开"], "高档画册/精装封面，克重高"),
    ("PAPER-015", "轻型纸60g", 270.0, 60, ["32开", "大32开", "16开"], "轻量大众读物，便于携带"),
    ("PAPER-016", "哑粉纸128g", 460.0, 128, ["16开", "大32开"], "不反光彩色画册内文"),
    ("PAPER-017", "双胶纸80g", 280.0, 80, ["16开", "32开", "大32开"], "通用正文用纸，适用面广"),
    ("PAPER-018", "书写纸70g", 210.0, 70, ["32开", "大32开"], "练习本/低克重正文用纸"),
]

NEW_PROCESSES = [
    ("PROC-004", "压纹", 260.0, "封面压纹，按千册计价"),
    ("PROC-005", "击凸", 300.0, "封面击凸，按千册计价"),
    ("PROC-006", "烫银", 320.0, "封面烫银，按千册计价"),
]

NEW_BINDINGS = [
    ("BIND-004", "锁线胶装", 260.0, "锁线+胶装，适合 6 印张以上厚本/精装内芯"),
    ("BIND-005", "线圈装", 200.0, "线圈装订，适合手册/台历，10 印张以内"),
]

NEW_RULES = [
    ("RULE-QTY-016", "印数上限 50000 册", "spec", "warning",
     {"field": "qty", "type": "range", "max": 50000,
      "message": "印数超过 50000 册，建议分批印刷或单独议价", "description": "超大批量按单次排产测算误差大"}),
    ("RULE-QTY-017", "印数低于 1000 册提示", "spec", "warning",
     {"field": "qty", "type": "range", "min": 1000,
      "message": "印数低于 1000 册，固定成本摊薄不足，建议数码印刷或提高印数", "description": "成本测算公式按千册摊薄"}),
    ("RULE-SHEET-018", "印张超过 40 提示分册", "spec", "warning",
     {"field": "print_sheet", "type": "range", "max": 40,
      "message": "印张超过 40，建议拆分为多册或确认装订能力", "description": "常规胶装/锁线单册过厚易散页"}),
    ("RULE-SHEET-019", "印张低于 2 提示薄本", "spec", "warning",
     {"field": "print_sheet", "type": "range", "min": 2,
      "message": "印张低于 2 的薄本建议骑马订或数码印刷", "description": "薄本胶装成本不经济"}),
    ("RULE-BIND-019", "锁线胶装建议不少于 6 印张", "spec", "warning",
     {"field": "binding", "type": "dependency", "when": {"binding": "锁线胶装"},
      "min_print_sheet": 6, "message": "锁线胶装建议不少于 6 印张，过薄可选择胶装/骑马订",
      "description": "锁线工序适合有一定厚度的书刊"}),
    ("RULE-BIND-020", "线圈装印张上限 10", "spec", "error",
     {"field": "binding", "type": "dependency", "when": {"binding": "线圈装"},
      "max_print_sheet": 10, "message": "线圈装最多 10 印张，超过请改胶装/锁线胶装",
      "description": "线圈装订过厚易变形、成本高"}),
    ("RULE-BIND-021", "胶装建议不少于 6 印张", "spec", "warning",
     {"field": "binding", "type": "dependency", "when": {"binding": "胶装"},
      "min_print_sheet": 6, "message": "胶装建议不少于 6 印张，过薄建议骑马订",
      "description": "胶装薄本书脊成型效果差"}),
]


def load(name):
    return json.loads((SEED / name).read_text(encoding="utf-8"))


def save(name, data):
    (SEED / name).write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def main():
    papers = load("paper_quotes.json")
    existing = {p.get("material_no") for p in papers}
    added = 0
    for no, title, price, grammage, fits, note in NEW_PAPERS:
        if no in existing:
            continue
        papers.append({
            "material_no": no, "type": "paper", "title": title, "source": PAPER_SRC,
            "version_no": 1,
            "content": {"price_per_ream": price, "grammage": grammage, "unit": "元/令",
                        "format_fit": fits, "note": note},
        })
        added += 1
    save("paper_quotes.json", papers)
    print("paper added:", added, "total:", len(papers))

    quotes = load("process_quotes.json")
    existing = {q.get("material_no") for q in quotes}
    added = 0
    for no, title, price, note in NEW_PROCESSES:
        if no in existing:
            continue
        quotes.append({
            "material_no": no, "type": "process", "title": title, "source": PROC_SRC,
            "version_no": 1, "content": {"price_per_1000": price, "unit": "元/千册", "note": note},
        })
        added += 1
    for no, title, price, note in NEW_BINDINGS:
        if no in existing:
            continue
        quotes.append({
            "material_no": no, "type": "binding", "title": title, "source": BIND_SRC,
            "version_no": 1, "content": {"price_per_1000": price, "unit": "元/千册", "note": note},
        })
        added += 1
    save("process_quotes.json", quotes)
    print("process/binding added:", added, "total:", len(quotes))

    rules = load("rules.json")
    existing = {r.get("rule_code") for r in rules}
    added = 0
    for code, name, rtype, severity, content in NEW_RULES:
        if code in existing:
            continue
        rules.append({"rule_code": code, "name": name, "business_type": "print_cost",
                      "rule_type": rtype, "severity": severity, "source": RULE_SRC,
                      "content": content})
        added += 1
    save("rules.json", rules)
    print("rules added:", added, "total:", len(rules))


if __name__ == "__main__":
    main()
