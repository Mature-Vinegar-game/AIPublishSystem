# -*- coding: utf-8 -*-
"""真实模式验收取证脚本（供 Lead 在用户配置 LLM_KEEY 后运行）。

用途：
1) 读取 /api/system/meta，确认真实模式与 key_configured；
2) 运行一次真实模型调用（预置 BT-DEMO-0001 或新建任务），保存：
   - 执行记录（tool_call_records）
   - AI 结果与依据
   - 任务状态与计数
   - 日志片段（logs/app.log 中同 task_id 的行）
3) 生成 `evidence/real_run_<时间戳>.json` 与 Markdown 摘要，供 5 项提交材料使用。

用法：
  $env:PYTHONPATH='' ; python scripts/_lead_real_check.py
前置：服务已用 LLM_MODE=real 启动；.env 中 LLM_API_KEY 已填。
"""
import hashlib
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
import http.cookiejar
from datetime import datetime
from pathlib import Path

BASE = os.environ.get("APP_BASE_URL", "http://127.0.0.1:8000")
ROOT = Path(__file__).resolve().parent.parent
EVIDENCE = ROOT / "evidence"
LOGS = ROOT / "logs" / "app.log"


def req(method, path, payload=None, jar=None):
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    headers = {"Content-Type": "application/json"}
    request = urllib.request.Request(BASE + path, data=data, headers=headers, method=method)
    opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar)) if jar is not None else urllib.request.build_opener()
    try:
        with opener.open(request, timeout=180) as resp:
            raw = resp.read().decode("utf-8", "replace")
            return resp.status, (json.loads(raw) if raw else {})
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8", "replace")
        try:
            return exc.code, json.loads(raw)
        except Exception:
            return exc.code, {"raw": raw}


def main():
    jar = http.cookiejar.CookieJar()
    st, body = req("POST", "/api/auth/login", {"username": "operator", "password": "123456"}, jar)
    if st != 200 or body.get("code") != 0:
        print("登录失败：", st, body)
        sys.exit(1)

    st, body = req("GET", "/api/system/meta", jar=jar)
    meta = (body.get("data") or {})
    print("llm_mode:", meta.get("llm_mode"), "| key_configured:", meta.get("key_configured"), "| model:", meta.get("model"))
    if meta.get("llm_mode") != "real":
        print("[警告] 当前不是 real 模式：请用 LLM_MODE=real 重启服务后再取证。")
    if not meta.get("key_configured"):
        print("[错误] API Key 未配置（.env 仍为占位符），无法进行真实调用取证。")
        sys.exit(2)

    # 找到演示任务
    st, body = req("GET", "/api/business-tasks?keyword=BT-DEMO", jar=jar)
    items = (body.get("data") or {}).get("items") or []
    task = None
    for item in items:
        if item.get("task_no") == "BT-DEMO-0001":
            task = item
            break
    if not task:
        st, body = req("POST", "/api/business-tasks", {
            "business_type": "print_cost",
            "title": "真实调用取证任务",
            "goal": "真实验证检索→校验→计算三工具链",
            "params": {
                "book_title": "真实调用取证样书", "format": "16开", "print_sheet": 12,
                "qty": 3000, "paper_type": "铜版纸157g", "binding": "胶装",
                "color_pages": 4, "process": "覆膜",
            },
        }, jar)
        task = (body.get("data") or {})
    task_id = task.get("id")

    print("使用任务：", task.get("task_no"), "| id=", task_id)
    st, body = req("POST", f"/api/business-tasks/{task_id}/ai-tasks", {}, jar)
    ai_task_id = (body.get("data") or {}).get("ai_task_id")
    print("AI 任务已创建：", st, ai_task_id)
    status = None
    for _ in range(120):
        st, body = req("GET", f"/api/ai-tasks/{ai_task_id}", jar=jar)
        status = (body.get("data") or {}).get("status")
        if status in ("SUCCEEDED", "FAILED"):
            break
        time.sleep(2)
    print("AI 状态：", status)

    st, status_body = req("GET", f"/api/ai-tasks/{ai_task_id}", jar=jar)
    st, calls_body = req("GET", f"/api/ai-tasks/{ai_task_id}/tool-calls", jar=jar)
    st, result_body = req("GET", f"/api/ai-tasks/{ai_task_id}/result", jar=jar)
    calls = ((calls_body.get("data") or {}).get("records") or [])
    result = result_body.get("data") or {}
    ai = status_body.get("data") or {}

    log_lines = []
    if LOGS.exists():
        try:
            with open(LOGS, "r", encoding="utf-8", errors="replace") as fh:
                for line in fh:
                    if str(ai_task_id) in line or str(task_id) in line:
                        log_lines.append(line.rstrip("\n"))
        except Exception as exc:  # noqa: BLE001
            log_lines = [f"读取日志失败：{exc}"]
    log_lines = log_lines[-80:]

    EVIDENCE.mkdir(exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    payload = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "base_url": BASE,
        "system_meta": meta,
        "task": {"id": task_id, "task_no": task.get("task_no"), "title": task.get("title")},
        "ai_task": ai,
        "tool_calls": calls,
        "ai_result": result,
        "log_lines": log_lines,
    }
    out_json = EVIDENCE / f"real_run_{ts}.json"
    out_json.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    # Markdown 摘要（脱敏：不含 key/Authorization）
    lines = [
        f"# 真实调用取证记录（{ts}）",
        "",
        f"- 运行环境：{meta.get('app_env')}｜LLM_MODE：{meta.get('llm_mode')}｜模型：{meta.get('model')}｜key_configured：{meta.get('key_configured')}",
        f"- 业务任务：{task.get('task_no')}（id={task_id}）",
        f"- AI 任务：id={ai_task_id}｜状态：{status}｜计数：{json.dumps(ai.get('counters'), ensure_ascii=False)}",
        f"- 工具调用数：{len(calls)}；类别：{sorted({c.get('tool_type') for c in calls})}",
        "",
        "## 执行记录（工具名 / 类型 / 参数摘要 / 来源ID / 状态 / 耗时ms）",
        "",
        "| seq | 工具 | 类型 | 参数摘要 | 来源ID | 状态 | 错误码 | 耗时ms |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for call in calls:
        args = call.get("args_json") or ""
        try:
            args = json.dumps(json.loads(args), ensure_ascii=False)
        except Exception:
            pass
        source_ids = call.get("source_ids_json") or "[]"
        lines.append(
            f"| {call.get('seq')} | {call.get('tool_name')} | {call.get('tool_type')} | "
            f"{args[:120].replace('|', '/')} | {source_ids[:120]} | {call.get('status')} | "
            f"{call.get('error_code') or '-'} | {call.get('latency_ms')} |"
        )
    structured = result.get("structured") or {}
    lines += [
        "",
        "## 结果摘要",
        "",
        f"- 总成本：{structured.get('total')} 元；单册成本：{structured.get('per_copy')} 元",
        f"- basis_refs：{[b.get('id') for b in (result.get('basis_refs') or [])]}",
        f"- basis_ok：{result.get('basis_ok')}；missing_data：{result.get('missing_data')}",
        "",
        "## 结果正文（Markdown）",
        "",
        result.get("content_md") or "（无）",
        "",
        "## 日志片段（logs/app.log，已截取同任务相关行）",
        "",
        "```",
        "\n".join(log_lines),
        "```",
    ]
    out_md = EVIDENCE / f"real_run_{ts}.md"
    out_md.write_text("\n".join(lines), encoding="utf-8")

    print(f"已保存：{out_json}")
    print(f"已保存：{out_md}")
    print("结论：", "真实调用成功" if status == "SUCCEEDED" else "真实调用失败，请查看 JSON 中 error_code/logs")
    sys.exit(0 if status == "SUCCEEDED" else 3)


if __name__ == "__main__":
    main()
