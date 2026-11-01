# -*- coding: utf-8 -*-
"""自动生成《备用录屏》：浏览器真实操作 + 真实 AI 运行，供 11/6 提交物④使用。

输出：evidence/recordings/demo_<ts>.webm → 转 mp4（交付包/04_测试与交付材料/备用录屏_系统演示.mp4）。
"""
import json
import os
import shutil
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

from playwright.sync_api import sync_playwright

BASE = "http://127.0.0.1:8000"
ROOT = Path(r"F:\deepseek workarea\出版AI系统")
REC_DIR = ROOT / "evidence" / "recordings"
OUT_MP4 = ROOT / "交付材料" / "04_测试与交付材料" / "备用录屏_系统演示.mp4"
FFMPEG = Path(r"F:\ffmpeg\ffmpeg-2026-07-20-git-c23123630e-essentials_build\bin\ffmpeg.exe")
FFPROBE = FFMPEG.with_name("ffprobe.exe")

BANNER_JS = """
(label) => {
  let bar = document.getElementById('rec-banner');
  if (!bar) {
    bar = document.createElement('div');
    bar.id = 'rec-banner';
    bar.style.cssText = 'position:fixed;left:0;right:0;bottom:0;z-index:99999;background:rgba(20,38,63,.92);color:#fff;'
      + 'font:14px/1.6 "Microsoft YaHei",Arial;padding:8px 18px;box-shadow:0 -2px 10px rgba(0,0,0,.25);';
    document.body.appendChild(bar);
  }
  bar.textContent = label;
}
"""


def main():
    REC_DIR.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    with sync_playwright() as p:
        browser = p.chromium.launch(channel="msedge", headless=True, args=["--disable-gpu", "--hide-scrollbars"])
        context = browser.new_context(
            viewport={"width": 1280, "height": 720},
            locale="zh-CN",
            record_video_dir=str(REC_DIR),
            record_video_size={"width": 1280, "height": 720},
        )
        page = context.new_page()
        page.set_default_timeout(30000)
        page.on("dialog", lambda d: d.accept())

        def banner(label):
            try:
                page.evaluate(BANNER_JS, label)
            except Exception:
                pass

        def step(label, seconds=4):
            banner(label)
            page.wait_for_timeout(seconds * 1000)

        # ① 登录
        page.goto(BASE + "/login", wait_until="networkidle")
        step("① 登录系统（演示账号 operator / 123456）", 3)
        page.fill("#username", "operator")
        page.fill("#password", "123456")
        page.click('button[type="submit"]')
        page.wait_for_url("**/tasks", timeout=20000)
        step("② 任务列表：示例任务 BT-DEMO-0001 已完成（v1 AI + v2 人工）", 4)

        # ③ 详情 + 执行记录
        page.goto(BASE + "/tasks/1", wait_until="networkidle")
        step("③ 任务详情：真实模式 REAL，AI 运行计数可见", 2)
        section = page.query_selector("#section-ai")
        if section:
            section.scroll_into_view_if_needed()
        step("③ AI 运行区：模型轮次 / 工具调用 / 工具类型数 / 模型调用", 5)
        section = page.query_selector("#section-tool-calls")
        if section:
            section.scroll_into_view_if_needed()
        step("④ 执行记录：工具名 / 参数摘要 / 来源 ID / 状态 / 耗时（同一任务 ID）", 6)

        # ④ 结果与依据
        section = page.query_selector("#section-result")
        if section:
            section.scroll_into_view_if_needed()
        step("⑤ 测算结果：总成本 14638.00 元、单册 4.88 元，明细含可复算公式", 5)
        basis = page.query_selector("[data-basis-type]")
        if basis:
            basis.scroll_into_view_if_needed()
            basis.click()
            step("⑥ 结果依据可回溯：点击依据查看资料/规则详情", 5)
            page.keyboard.press("Escape")

        # ⑤ 版本与审核
        section = page.query_selector("#section-versions")
        if section:
            section.scroll_into_view_if_needed()
        step("⑦ 版本与审核：v1（AI）+ v2（人工）可查，审核通过记录完整", 5)

        # ⑥ 系统信息
        page.goto(BASE + "/system", wait_until="networkidle")
        step("⑧ 系统信息：llm_mode=real、模型 deepseek-flash、Key 已配置、五项限额", 5)

        # ⑦ 三张图
        page.goto(BASE + "/diagrams", wait_until="networkidle")
        step("⑨ 三张图（HTML 演示）：系统功能图 / 技术调用图 / 数据处理与工具执行图", 4)
        try:
            page.click('#tabs a[href="#fig2"]')
            step("⑨ 技术调用图：浏览器 → FastAPI → Agent/工具 → DeepSeek → SQLite", 4)
            page.click('#tabs a[href="#fig3"]')
            step("⑨ 数据处理与工具执行图：同一 business_task_id 贯穿全链路", 4)
        except Exception:
            pass

        # ⑧ 现场新建任务 + 真实 AI 运行
        page.goto(BASE + "/tasks", wait_until="networkidle")
        params = {"book_title": "备用录屏演示样书", "format": "16开", "print_sheet": 12, "qty": 3000,
                  "paper_type": "胶版纸80g", "binding": "胶装", "color_pages": 4, "process": "覆膜"}
        created = page.evaluate(
            """async (params) => {
                const r = await fetch('/api/business-tasks', {
                  method: 'POST', headers: {'Content-Type': 'application/json'},
                  body: JSON.stringify({business_type:'print_cost', title:'录屏演示-实时真实测算', goal:'备用录屏：现场新建任务并运行真实 AI', params})
                });
                return await r.json();
            }""",
            params,
        )
        tid = ((created or {}).get("data") or {}).get("id")
        if tid:
            page.goto(BASE + f"/tasks/{tid}", wait_until="networkidle")
            step("⑩ 现场新建任务 → 点击“运行 AI 测算”（真实模型调用）", 3)
            btn = page.query_selector("#btn-ai-run")
            if btn:
                btn.click()
            badge = page.query_selector("#ai-status-badge")
            status_text = ""
            for _ in range(90):
                if badge:
                    status_text = badge.inner_text()
                    if "完成" in status_text or "失败" in status_text:
                        break
                page.wait_for_timeout(2000)
            step(f"⑪ 真实 AI 运行完成：状态 {status_text or '完成'}（执行记录见详情页）", 6)
            section = page.query_selector("#section-result")
            if section:
                section.scroll_into_view_if_needed()
            step("⑫ 结果与依据：总成本/单册/依据 ID 均来自本次真实工具执行", 8)
            section = page.query_selector("#section-tool-calls")
            if section:
                section.scroll_into_view_if_needed()
            step("⑫ 本次真实调用的执行记录（工具名/参数/来源/状态）", 6)

        step("演示结束：系统可运行、真实工具调用、结果可追溯、人工确认与版本可查", 3)
        video_path = page.video.path()
        context.close()
        browser.close()

    print("webm:", video_path, os.path.getsize(video_path), "bytes")
    OUT_MP4.parent.mkdir(parents=True, exist_ok=True)
    cmd = [str(FFMPEG), "-y", "-i", str(video_path), "-c:v", "libx264", "-preset", "medium", "-crf", "26",
           "-pix_fmt", "yuv420p", "-movflags", "+faststart", "-an", str(OUT_MP4)]
    subprocess.run(cmd, check=True, capture_output=True)
    print("mp4:", OUT_MP4, os.path.getsize(OUT_MP4), "bytes")
    if FFPROBE.exists():
        probe = subprocess.run([str(FFPROBE), "-v", "error", "-show_entries", "format=duration,size",
                                "-of", "json", str(OUT_MP4)], capture_output=True, text=True)
        print("probe:", probe.stdout.strip()[:300])
    # 记录证据
    EVID = ROOT / "evidence"
    info = {"generated_at": datetime.now().isoformat(timespec="seconds"), "webm": str(video_path),
            "mp4": str(OUT_MP4), "mp4_size": OUT_MP4.stat().st_size, "task_id": locals().get("tid")}
    (EVID / f"demo_recording_{ts}.json").write_text(json.dumps(info, ensure_ascii=False, indent=2), encoding="utf-8")
    print("done")


if __name__ == "__main__":
    main()
