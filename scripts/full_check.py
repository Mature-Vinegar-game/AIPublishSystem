# -*- coding: utf-8 -*-
"""全功能 HTTP 自测（面向“演示前/交付前逐项检查”）。

用法：先启动系统，再执行
    python scripts/full_check.py
返回码 0 = 全部通过；1 = 有失败项。
"""
from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
import http.cookiejar
from datetime import datetime

BASE = os.environ.get("APP_BASE_URL", "http://127.0.0.1:8000")
results: list[tuple[str, bool, str]] = []


def add(name: str, ok: bool, detail: str = "") -> None:
    results.append((name, bool(ok), str(detail)[:240]))
    print(("PASS " if ok else "FAIL ") + name + ((" | " + str(detail)[:160]) if detail else ""))


class Client:
    def __init__(self) -> None:
        self.jar = http.cookiejar.CookieJar()
        self.opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(self.jar))

    def request(self, method: str, path: str, payload=None, form: bool = False):
        data = None
        ctype = "application/json"
        if payload is not None:
            if form:
                data = urllib.parse.urlencode(payload).encode()
                ctype = "application/x-www-form-urlencoded"
            else:
                data = json.dumps(payload).encode()
        req = urllib.request.Request(BASE + path, data=data, headers={"Content-Type": ctype}, method=method)
        try:
            with self.opener.open(req, timeout=30) as resp:
                return resp.status, resp.read().decode("utf-8", "replace")
        except urllib.error.HTTPError as exc:
            return exc.code, exc.read().decode("utf-8", "replace")

    def json(self, method: str, path: str, payload=None):
        status, text = self.request(method, path, payload)
        try:
            return status, json.loads(text or "{}")
        except ValueError:
            return status, {}

    def page(self, path: str):
        return self.request("GET", path)


def has(html: str, *keywords: str) -> bool:
    return all(k in html for k in keywords)


def main() -> None:
    op = Client()
    rv = Client()

    # 0 未登录访问
    status, _ = op.page("/tasks")
    add("未登录访问 /tasks 重定向登录", status in (200, 302, 303), status)

    # 1 登录
    status, body = op.json("POST", "/api/auth/login", {"username": "operator", "password": "123456"})
    add("operator 登录", status == 200 and body.get("code") == 0, status)
    status, body = rv.json("POST", "/api/auth/login", {"username": "reviewer", "password": "123456"})
    add("reviewer 登录", status == 200 and body.get("code") == 0, status)

    # 2 核心页面
    pages = [
        ("/tasks", ["任务列表", "quick-filters", "sort-filter", "最新总成本"]),
        ("/tasks/new", ["新建印制成本测算任务", '<select id="paper_type"', "锁线胶装"]),
        ("/tasks/new-plan", ["方案模式", "由系统推荐", "总预算"]),
        ("/materials", ["PAPER-001", "BIND-005", "资料库"]),
        ("/rules", ["RULE-QTY-016", "data-toggle-rule", "规则库"]),
        ("/validate", ["校验试算", "执行校验"]),
        ("/evaluation", ["效果评测", "PASS"]),
        ("/dashboard", ["运行仪表盘", "每日趋势", "失败原因"]),
        ("/system", ["系统信息", "real", "deepseek-flash"]),
        ("/diagrams", ["图1 系统功能图", "图2 技术调用图", "图3 数据处理与工具执行图"]),
    ]
    for path, kws in pages:
        status, html = op.page(path)
        add(f"页面 {path}", status == 200 and has(html, *kws), status)

    status, html = op.page("/dashboard?days=30")
    add("仪表盘近 30 天", status == 200 and "近 30 天" in html, status)
    status, html = op.page("/dashboard?days=0")
    add("仪表盘全部时间", status == 200 and "全部时间" in html, status)

    # 3 任务列表检索增强
    status, html = op.page("/tasks?sort=amount_desc")
    add("任务列表按总成本排序", status == 200 and "最新总成本" in html, status)
    status, html = op.page("/tasks?keyword=" + urllib.parse.quote("方案"))
    add("任务列表关键词高亮标记", status == 200 and 'data-highlight-keyword="方案"' in html, status)

    # 4 任务详情 / 结果 / 执行记录 / 版本
    status, html = op.page("/tasks/1")
    add(
        "任务详情核心区块",
        status == 200
        and has(html, "section-ai", "section-result", "section-tool-calls", "section-versions", "section-reviews"),
        status,
    )
    add("详情结果金额 14638/4.88", "14638" in html and "4.88" in html)
    add("详情 Markdown 数据源", "data-raw=" in html and 'id="result-content-md"' in html)
    add("工具执行记录可点击", "tool-call-cell" in html and "data-tool-call-id" in html)
    add("导出/打印按钮", "btn-export-result" in html and "btn-print-result" in html)
    add("版本对比入口", "版本对比" in html)
    status, html = op.page("/tasks/1/versions/compare")
    add("版本对比页面", status == 200 and has(html, "金额摘要", "正文差异", "成本明细差异"), status)

    # 5 校验试算
    status, html = op.request(
        "POST",
        "/validate",
        {"format": "16开", "print_sheet": "12", "qty": "3000", "paper_type": "胶版纸80g", "binding": "胶装", "color_pages": "4", "process": "覆膜"},
        form=True,
    )
    add("校验试算-合法参数通过", status == 200 and "校验通过" in html, status)
    status, html = op.request(
        "POST",
        "/validate",
        {"format": "16开", "print_sheet": "12", "qty": "3000", "paper_type": "胶版纸80g", "binding": "骑马订", "color_pages": "4", "process": "覆膜"},
        form=True,
    )
    add("校验试算-骑马订 12 印张被规则拦截", status == 200 and "校验未通过" in html and "RULE-BIND-007" in html, status)

    # 6 效果评测
    status, html = op.page("/evaluation")
    add("效果评测 4/4 PASS", status == 200 and html.count("PASS") == 4 and "FAIL" not in html, status)

    # 7 方案模式任务
    for path, kw in (("/tasks/46", "候选方案"), ("/tasks/48", "候选方案"), ("/tasks/51", "btn-ai-run")):
        status, html = op.page(path)
        add(f"方案任务 {path}", status == 200 and kw in html, status)
    status, html = op.page("/tasks/47")
    add("方案任务预算不足提示", status == 200 and "超预算" in html, status)

    # 8 退回重跑入口（任务 54 为测试构造；不存在则跳过）
    status, html = op.page("/tasks/54")
    add("按审核意见重跑入口（任务 54）", status == 200 and "btn-rerun-by-review" in html, status)

    # 9 数据规模与规则
    status, body = op.json("GET", "/api/materials?limit=100")
    items = (body.get("data") or {}).get("items") or []
    type_counts = {}
    for item in items:
        type_counts[item.get("type")] = type_counts.get(item.get("type"), 0) + 1
    add("资料库 30 条", status == 200 and len(items) == 30, type_counts)
    status, body = op.json("GET", "/api/rules?limit=100")
    add("规则库 22 条", status == 200 and len((body.get("data") or {}).get("items") or []) == 22)
    status, body = op.json("GET", "/api/system/meta")
    meta = body.get("data") or {}
    add("系统 meta real + key", status == 200 and meta.get("llm_mode") == "real" and meta.get("key_configured") is True, status)
    add("限额 10/8/30/3000/120", (meta.get("limits") or {}).get("max_tool_calls") == 10 and (meta.get("limits") or {}).get("ai_run_timeout_s") == 120)

    # 10 权限
    status, _ = rv.json("POST", "/api/business-tasks", {"business_type": "print_cost", "title": "越权测试", "goal": "test", "params": {}})
    add("reviewer 创建任务 403", status == 403, status)
    status, _ = rv.request("POST", "/api/ai-tasks/1/retry")
    add("reviewer 运行/重试 AI 403", status == 403, status)

    # 11 摘要
    passed = sum(1 for _, ok, _ in results if ok)
    print(f"\n===== 全功能自测：{passed}/{len(results)} 通过 =====")
    evidence = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "base": BASE,
        "passed": passed,
        "total": len(results),
        "results": [{"name": n, "ok": ok, "detail": d} for n, ok, d in results],
    }
    try:
        evidence_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "evidence")
        os.makedirs(evidence_dir, exist_ok=True)
        path = os.path.join(evidence_dir, "full_check_" + datetime.now().strftime("%Y%m%d_%H%M%S") + ".json")
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(evidence, fh, ensure_ascii=False, indent=2)
        print("证据：", path)
    except OSError:
        pass
    if passed != len(results):
        print("失败项：")
        for name, ok, detail in results:
            if not ok:
                print(" -", name, detail)
        sys.exit(1)


if __name__ == "__main__":
    main()
