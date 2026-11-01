# -*- coding: utf-8 -*-
"""使用本机 Edge + Playwright 截取系统真实页面（用于 PPT/交付材料）。

前置：`python -m pip install --user playwright`；本机已安装 Microsoft Edge。
运行：python scripts/_capture_screenshots.py
"""
import sys
from pathlib import Path

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "evidence" / "screenshots"
BASE = "http://127.0.0.1:8000"


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as p:
        browser = p.chromium.launch(
            channel="msedge",
            headless=True,
            args=["--disable-gpu", "--hide-scrollbars"],
        )
        context = browser.new_context(
            viewport={"width": 1440, "height": 960},
            device_scale_factor=1.0,
            locale="zh-CN",
        )
        page = context.new_page()
        page.set_default_timeout(30000)

        def shot(name, full=True):
            path = OUT / name
            page.screenshot(path=str(path), full_page=full)
            print("saved:", path.name, path.stat().st_size)

        # 1 登录页
        page.goto(BASE + "/login", wait_until="networkidle")
        shot("01_login.png")

        # 2 登录（operator）
        page.fill("#username", "operator")
        page.fill("#password", "123456")
        page.click('button[type="submit"]')
        page.wait_for_load_state("networkidle")

        # 3 任务列表 / 新建 / 详情 / 资料 / 规则 / 系统
        pages = [
            ("/tasks", "02_tasks.png"),
            ("/tasks/new", "03_task_new.png"),
            ("/tasks/1", "04_task_detail.png"),
            ("/materials", "05_materials.png"),
            ("/rules", "06_rules.png"),
            ("/system", "07_system.png"),
        ]
        for path, name in pages:
            page.goto(BASE + path, wait_until="networkidle")
            page.wait_for_timeout(800)
            shot(name)

        # 4 详情页关键区块（结果/依据、执行记录、版本、审核）
        page.goto(BASE + "/tasks/1", wait_until="networkidle")
        page.wait_for_timeout(800)
        for selector, name in [
            ("#section-ai", "08_ai_run.png"),
            ("#section-result", "09_result_basis.png"),
            ("#section-tool-calls", "10_tool_calls.png"),
            ("#section-versions", "11_versions.png"),
            ("#section-reviews", "12_review.png"),
        ]:
            element = page.query_selector(selector)
            if element is None:
                print("skip (not found):", selector)
                continue
            element.scroll_into_view_if_needed()
            page.wait_for_timeout(200)
            element.screenshot(path=str(OUT / name))
            print("saved:", name, (OUT / name).stat().st_size)

        # 5 资料库停用按钮（T06 演示入口）：截取带按钮的资料表
        page.goto(BASE + "/materials", wait_until="networkidle")
        page.wait_for_timeout(500)
        table = page.query_selector("#materials-table")
        if table:
            table.screenshot(path=str(OUT / "13_materials_toggle.png"))
            print("saved: 13_materials_toggle.png")

        browser.close()
    print("done")


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:  # noqa: BLE001
        print("FAILED:", type(exc).__name__, exc)
        sys.exit(1)
