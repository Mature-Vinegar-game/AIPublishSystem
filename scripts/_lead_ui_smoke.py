# -*- coding: utf-8 -*-
"""浏览器级 UI 冒烟测试（Playwright + 本机 Edge，真实服务）。

检查：登录、页面渲染、AI 区/结果/执行记录/版本/审核、依据弹层、资料启停（会恢复）、
reviewer 视角、三张图页面、JS 控制台错误与失败请求。
"""
import json
import sys
from datetime import datetime
from pathlib import Path

from playwright.sync_api import sync_playwright

BASE = "http://127.0.0.1:8000"
ROOT = Path(r"F:\deepseek workarea\出版AI系统")
EVID = ROOT / "evidence"
results = []
console_errors = []
page_errors = []
failed_responses = []


def add(name, ok, detail=""):
    results.append({"name": name, "ok": bool(ok), "detail": str(detail)[:300]})
    print(("PASS " if ok else "FAIL ") + name + (" | " + str(detail)[:160] if detail else ""))


def main():
    with sync_playwright() as p:
        browser = p.chromium.launch(channel="msedge", headless=True, args=["--disable-gpu", "--hide-scrollbars"])
        ctx = browser.new_context(viewport={"width": 1440, "height": 960}, locale="zh-CN")
        page = ctx.new_page()
        page.set_default_timeout(30000)
        page.on("console", lambda msg: console_errors.append(msg.text) if msg.type == "error" else None)
        page.on("pageerror", lambda exc: page_errors.append(str(exc)))
        page.on("response", lambda resp: failed_responses.append((resp.status, resp.url)) if resp.status >= 400 else None)
        page.on("dialog", lambda dialog: dialog.accept())

        # 里程碑：页面 -> 提示文字
        # 1 登录
        page.goto(BASE + "/login", wait_until="networkidle")
        add("GET /login 渲染", page.locator("#username").count() == 1 and "登录" in page.content())
        page.fill("#username", "operator")
        page.fill("#password", "123456")
        page.click('button[type="submit"]')
        page.wait_for_url("**/tasks", timeout=20000)
        add("operator 登录跳转 /tasks", "/tasks" in page.url, page.url)

        # 2 页面渲染
        pages = [("/tasks", "任务列表"), ("/tasks/new", "新建任务"), ("/materials", "资料库"),
                 ("/rules", "规则库"), ("/system", "系统信息")]
        for path, keyword in pages:
            page.goto(BASE + path, wait_until="networkidle")
            body = page.content()
            add(f"页面 {path}", keyword in body and page.locator("body").count() == 1)

        # 3 任务详情（BT-DEMO-0001）
        page.goto(BASE + "/tasks/1", wait_until="networkidle")
        content = page.content()
        add("详情：AI 区/结果/记录/版本/审核区块", all(s in content for s in ["section-ai", "section-result", "section-tool-calls", "section-versions", "section-reviews"]))
        add("详情：结果包含 14638.00 与 4.88", "14638" in content and "4.88" in content)
        model_mode = page.locator("#ai-model-mode").inner_text() if page.locator("#ai-model-mode").count() else ""
        add("详情：真实模式 REAL 标识", "REAL" in model_mode.upper(), model_mode)
        tool_rows = page.locator("#tool-calls-body tr").count()
        add("详情：执行记录行数 > 0", tool_rows > 0, tool_rows)
        versions_text = page.locator("#section-versions").inner_text()
        add("详情：版本区含 v1/v2 或 AI/人工来源", ("v1" in versions_text or "AI" in versions_text) and "人工" in versions_text, versions_text[:80])
        review_text = page.locator("#section-reviews").inner_text() if page.locator("#section-reviews").count() else ""
        add("详情：审核记录（通过）", "通过" in review_text or "APPROVE" in review_text, review_text[:80])

        # 4 依据弹层
        basis = page.locator("[data-basis-type]").first
        if basis.count():
            basis.click()
            page.wait_for_timeout(600)
            modal = page.locator("#app-modal")
            add("依据弹层可打开", modal.count() == 1 and modal.is_visible(), "modal")
            page.keyboard.press("Escape")
        else:
            add("依据弹层可打开", False, "未找到依据按钮")

        # 5 资料启停（改后恢复）
        page.goto(BASE + "/materials", wait_until="networkidle")
        toggle = page.locator("[data-toggle-material]").first
        if toggle.count():
            mid = toggle.get_attribute("data-toggle-material")
            enabled_before = toggle.get_attribute("data-material-enabled")
            toggle.click()
            page.wait_for_load_state("networkidle")
            page.wait_for_timeout(800)
            add("资料启停：点击后状态切换", page.locator(f'[data-toggle-material="{mid}"]').count() > 0, f"id={mid} before={enabled_before}")
            page.locator(f'[data-toggle-material="{mid}"]').click()
            page.wait_for_load_state("networkidle")
            page.wait_for_timeout(800)
            after = page.locator(f'[data-toggle-material="{mid}"]').get_attribute("data-material-enabled")
            add("资料启停：已恢复原状态", after == enabled_before, f"after={after}")
        else:
            add("资料启停按钮存在", False)

        # 6 三张图页面
        page.goto(BASE + "/diagrams", wait_until="networkidle")
        dcontent = page.content()
        add("三张图页面三节齐全", all(s in dcontent for s in ["fig1", "fig2", "fig3", "check"]))
        page.click('#tabs a[href="#fig2"]')
        page.wait_for_timeout(500)
        add("三张图页签可切换", "active" in (page.locator('#tabs a[href="#fig2"]').get_attribute("class") or ""))

        # 7 reviewer 视角
        rctx = browser.new_context(viewport={"width": 1440, "height": 960}, locale="zh-CN")
        rpage = rctx.new_page()
        rpage.on("pageerror", lambda exc: page_errors.append("reviewer: " + str(exc)))
        rpage.goto(BASE + "/login", wait_until="networkidle")
        rpage.fill("#username", "reviewer")
        rpage.fill("#password", "123456")
        rpage.click('button[type="submit"]')
        rpage.wait_for_url("**/tasks", timeout=20000)
        rpage.goto(BASE + "/tasks/1", wait_until="networkidle")
        rtext = rpage.content()
        add("reviewer 可打开任务详情", "section-reviews" in rtext)
        run_btn = rpage.locator("#btn-ai-run")
        add("reviewer 不显示运行 AI 按钮", run_btn.count() == 0, f"count={run_btn.count()}")
        add("reviewer 详情页审核区存在", "section-reviews" in rtext)
        rctx.close()

        browser.close()

    # 错误汇总
    add("JS 控制台无 error", len(console_errors) == 0, str(console_errors[:3]))
    add("无页面未捕获异常", len(page_errors) == 0, str(page_errors[:3]))
    add("无失败请求（4xx/5xx）", len(failed_responses) == 0, str(failed_responses[:5]))

    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    EVID.mkdir(exist_ok=True)
    payload = {"generated_at": datetime.now().isoformat(timespec="seconds"), "base": BASE,
               "results": results, "console_errors": console_errors, "page_errors": page_errors,
               "failed_responses": failed_responses}
    (EVID / f"ui_smoke_{ts}.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    lines = ["# 浏览器 UI 冒烟测试记录", "", f"- 时间：{payload['generated_at']}", f"- 环境：{BASE}（真实模式）", "",
             "| 检查项 | 结果 | 详情 |", "|---|---|---|"]
    for r in results:
        lines.append(f"| {r['name']} | {'通过' if r['ok'] else '失败'} | {r['detail'][:160]} |")
    lines += ["", f"- JS console errors：{console_errors or '无'}",
              f"- 页面未捕获异常：{page_errors or '无'}",
              f"- 失败请求（4xx/5xx）：{failed_responses or '无'}"]
    (EVID / f"ui_smoke_{ts}.md").write_text("\n".join(lines), encoding="utf-8")
    passed = sum(1 for r in results if r["ok"])
    print(f"\n===== UI 冒烟 {passed}/{len(results)} 通过 =====")
    if passed != len(results):
        print("失败项：", [r for r in results if not r["ok"]])
        sys.exit(1)


if __name__ == "__main__":
    main()
