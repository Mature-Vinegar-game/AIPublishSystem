# -*- coding: utf-8 -*-
"""现场演示前一键自检（11/5 彩排、11/6 演示前）。

用法：python scripts/demo_check.py
返回码：0 = 全部通过；1 = 有失败项。
"""
import json
import os
import sys
import urllib.error
import urllib.request
import http.cookiejar
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BASE = os.environ.get("APP_BASE_URL", "http://127.0.0.1:8000")
checks = []


def add(name, ok, detail=""):
    checks.append((name, bool(ok), str(detail)))
    print(("PASS " if ok else "FAIL ") + name + (" | " + str(detail) if detail else ""))


def client():
    jar = http.cookiejar.CookieJar()

    def req(method, path, payload=None, timeout=30):
        data = json.dumps(payload).encode("utf-8") if payload is not None else None
        request = urllib.request.Request(BASE + path, data=data, headers={"Content-Type": "application/json"}, method=method)
        opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))
        try:
            with opener.open(request, timeout=timeout) as resp:
                return resp.status, resp.read().decode("utf-8", "replace")
        except urllib.error.HTTPError as exc:
            return exc.code, exc.read().decode("utf-8", "replace")

    return req


def main():
    # 1 环境与依赖
    add("项目根目录存在 README", (ROOT / "README.md").exists())
    add("环境配置 .env 存在", (ROOT / ".env").exists(), ".env")
    if (ROOT / ".env").exists():
        env = (ROOT / ".env").read_text(encoding="utf-8", errors="replace")
        add("LLM_MODE=real", "LLM_MODE=real" in env)
        add("LLM_API_KEY 非占位符", "PLEASE-REPLACE" not in env and "LLM_API_KEY=sk-" in env)

    # 2 服务与页面
    req = client()
    try:
        st, html = req("GET", "/login")
        add("服务运行 /login 200", st == 200 and "登录" in html, st)
    except Exception as exc:  # noqa: BLE001
        add("服务运行 /login", False, exc)
        summary()
        return

    st, body = req("POST", "/api/auth/login", {"username": "operator", "password": "123456"})
    try:
        login_obj = json.loads(body)
    except Exception:
        login_obj = {}
    add("operator 登录", st == 200 and login_obj.get("code") == 0, st)
    st, body = req("GET", "/api/system/meta")
    try:
        meta = json.loads(body).get("data") or {}
    except Exception:
        meta = {}
    add("真实模式（llm_mode=real）", meta.get("llm_mode") == "real", meta.get("llm_mode"))
    add("Key 已配置", meta.get("key_configured") is True)
    limits = meta.get("limits") or {}
    add("限额展示正确 ", limits.get("max_tool_calls") == 10 and limits.get("ai_run_timeout_s") == 120,
        json.dumps(limits, ensure_ascii=False))
    st, html = req("GET", "/diagrams")
    add("三张图 /diagrams 可访问", st == 200 and "图1 系统功能图" in html)
    st, html = req("GET", "/system")
    add("系统信息页可访问（真实模式自证）", st == 200 and "真实" in html, st)

    # 3 演示数据
    st, body = req("GET", "/api/business-tasks")
    try:
        items = (json.loads(body).get("data") or {}).get("items") or []
    except Exception:
        items = []
    titles = {item.get("title"): item for item in items}
    by_no = {item.get("task_no"): item for item in items}
    demo = by_no.get("BT-DEMO-0001") or {}
    add("BT-DEMO-0001 已完成且≥2 版本", demo.get("status") == "COMPLETED" and int(demo.get("versions_count") or 0) >= 2,
        f"{demo.get('status')} / versions={demo.get('versions_count')}")
    base_task = titles.get("演示-基线待运行") or {}
    add("演示-基线待运行：PENDING 未运行", base_task.get("status") == "PENDING" and not base_task.get("latest_ai_status"),
        f"{base_task.get('task_no')} / {base_task.get('latest_ai_status')}")
    data_task = titles.get("演示-数据不足") or {}
    add("演示-数据不足：已预跑 SUCCEEDED", data_task.get("latest_ai_status") == "SUCCEEDED",
        f"{data_task.get('task_no')} / {data_task.get('latest_ai_status')}")
    fail_task = titles.get("演示-失败重试") or {}
    add("演示-失败重试：已预跑 FAILED", fail_task.get("latest_ai_status") == "FAILED",
        f"{fail_task.get('task_no')} / {fail_task.get('latest_ai_status')}")

    # 4 提交材料
    delivery = ROOT / "交付材料"
    for rel in ["00_材料索引与提交自检.md", "01_可运行程序及源码", "02_需求与设计说明/需求与设计说明.docx",
                "03_数据与运行记录/运行记录_三类脱敏.md", "04_测试与交付材料/测试用例.md",
                "05_小组分工与开发记录/表7_小组分工与开题登记表.docx"]:
        add(f"交付材料：{rel}", (delivery / rel).exists())

    summary()


def summary():
    failed = [c for c in checks if not c[1]]
    print(f"\n===== 自检结果：{len(checks)-len(failed)}/{len(checks)} 通过 =====")
    if failed:
        print("失败项：")
        for name, _, detail in failed:
            print(" -", name, detail)
        sys.exit(1)


if __name__ == "__main__":
    main()
