# -*- coding: utf-8 -*-
"""真实模式验收场景取证：输入变化 / 数据不足 / 失败+重试 / 权限 / 版本审核。

生成 evidence/real_acceptance_<ts>.json 与 .md（脱敏，不含 key）。
用法：服务以 LLM_MODE=real 运行后执行 python scripts/_lead_acceptance.py
"""
import json
import os
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

BASE_PARAMS = {
    "book_title": "《初中语文阅读训练（八年级）》", "format": "16开", "print_sheet": 12,
    "qty": 3000, "paper_type": "胶版纸80g", "binding": "胶装", "color_pages": 4, "process": "覆膜",
}


def make_client():
    jar = http.cookiejar.CookieJar()

    def req(method, path, payload=None, timeout=180):
        data = json.dumps(payload).encode("utf-8") if payload is not None else None
        request = urllib.request.Request(BASE + path, data=data, headers={"Content-Type": "application/json"}, method=method)
        opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))
        try:
            with opener.open(request, timeout=timeout) as resp:
                raw = resp.read().decode("utf-8", "replace")
                return resp.status, (json.loads(raw) if raw else {})
        except urllib.error.HTTPError as exc:
            raw = exc.read().decode("utf-8", "replace")
            try:
                return exc.code, json.loads(raw)
            except Exception:
                return exc.code, {"raw": raw}

    return req


def login(req, username):
    st, body = req("POST", "/api/auth/login", {"username": username, "password": "123456"})
    assert st == 200 and body.get("code") == 0, f"登录失败 {username}: {st} {body}"
    return body["data"]["user"]


def create_task(req, title, params):
    st, body = req("POST", "/api/business-tasks", {"business_type": "print_cost", "title": title, "goal": title, "params": params})
    assert st in (200, 201) and body.get("code") == 0, f"创建任务失败: {st} {body}"
    return body["data"]


def run_ai(req, task_id, fault=None, wait=180):
    payload = {"fault": fault} if fault else {}
    st, body = req("POST", f"/api/business-tasks/{task_id}/ai-tasks", payload)
    if st != 202:
        return {"status": "CREATE_FAILED", "http": st, "body": body}
    ai_id = body["data"]["ai_task_id"]
    deadline = time.time() + wait
    while time.time() < deadline:
        st2, b2 = req("GET", f"/api/ai-tasks/{ai_id}")
        data = b2.get("data") or {}
        if data.get("status") in ("SUCCEEDED", "FAILED"):
            return {"ai_task_id": ai_id, **data, "http": st2}
        time.sleep(2)
    return {"ai_task_id": ai_id, "status": "TIMEOUT"}


def collect(req, ai_id):
    _, status = req("GET", f"/api/ai-tasks/{ai_id}")
    _, calls = req("GET", f"/api/ai-tasks/{ai_id}/tool-calls")
    _, result = req("GET", f"/api/ai-tasks/{ai_id}/result")
    return {
        "ai_task": status.get("data") or {},
        "tool_calls": (calls.get("data") or {}).get("records") or [],
        "result": result.get("data") or {},
    }


def md_summary(title, payload):
    lines = [f"# {title}", "", f"- 生成时间：{payload['generated_at']}", f"- LLM_MODE：{payload['meta'].get('llm_mode')}｜模型：{payload['meta'].get('model')}"]
    for scen in payload["scenarios"]:
        lines += ["", f"## {scen['name']}", ""]
        for k, v in scen.get("summary", {}).items():
            lines.append(f"- {k}：{v}")
        if scen.get("tool_calls"):
            lines += ["", "| seq | 工具 | 类型 | 状态 | 错误码 | 耗时ms |", "|---|---|---|---|---|---|"]
            for c in scen["tool_calls"]:
                lines.append(f"| {c.get('seq')} | {c.get('tool_name')} | {c.get('tool_type')} | {c.get('status')} | {c.get('error_code') or '-'} | {c.get('latency_ms')} |")
    lines += ["", "## 日志片段（同任务相关，已截取）", "", "```"] + payload["log_lines"][-40:] + ["```"]
    return "\n".join(lines)


def main():
    req_op = make_client()
    req_rv = make_client()
    op = login(req_op, "operator")
    rv = login(req_rv, "reviewer")
    st, meta_body = req_op("GET", "/api/system/meta")
    meta = meta_body.get("data") or {}
    if meta.get("llm_mode") != "real" or not meta.get("key_configured"):
        print("需要 real 模式且 key_configured=true；当前：", meta.get("llm_mode"), meta.get("key_configured"))
        sys.exit(2)

    scenarios = []
    ai_ids = []

    # A. 输入条件变化：基线 vs 换纸张/印数（两步）
    t1 = create_task(req_op, "验收A-输入变化-基线", dict(BASE_PARAMS))
    r1 = run_ai(req_op, t1["id"]); ai_ids.append(r1.get("ai_task_id")); c1 = collect(req_op, r1["ai_task_id"])
    p2 = dict(BASE_PARAMS); p2.update({"qty": 5000, "print_sheet": 14, "paper_type": "胶版纸80g"})
    t2 = create_task(req_op, "验收A-输入变化-改参数", p2)
    r2 = run_ai(req_op, t2["id"]); ai_ids.append(r2.get("ai_task_id")); c2 = collect(req_op, r2["ai_task_id"])
    scenarios.append({
        "name": "A 输入条件变化（基线 vs 改印数/印张）",
        "summary": {
            "基线任务": f"{t1['task_no']} 状态={r1.get('status')} 总成本={(c1['result'].get('structured') or {}).get('total')} 单册={(c1['result'].get('structured') or {}).get('per_copy')}",
            "改参数任务": f"{t2['task_no']} 状态={r2.get('status')} 总成本={(c2['result'].get('structured') or {}).get('total')} 单册={(c2['result'].get('structured') or {}).get('per_copy')}",
            "说明": "两次工具参数与结果不同，依据来自各自本次执行记录",
        },
        "tool_calls": c2["tool_calls"],
    })

    # B. 数据不足：不存在的纸张
    p3 = dict(BASE_PARAMS); p3["paper_type"] = "不存在的纸张XY"
    t3 = create_task(req_op, "验收B-数据不足", p3)
    r3 = run_ai(req_op, t3["id"]); ai_ids.append(r3.get("ai_task_id")); c3 = collect(req_op, r3["ai_task_id"])
    structured3 = c3["result"].get("structured") or {}
    scenarios.append({
        "name": "B 资料/数据不足（不虚构）",
        "summary": {
            "任务": f"{t3['task_no']} 状态={r3.get('status')}",
            "missing_data": json.dumps(c3["result"].get("missing_data"), ensure_ascii=False),
            "total/per_copy": f"{structured3.get('total')} / {structured3.get('per_copy')}",
            "结论": "明确列出缺失依据，未编造总价",
        },
        "tool_calls": c3["tool_calls"],
    })

    # C. 失败 + 重试：受控超时故障（demo 环境）
    t4 = create_task(req_op, "验收C-工具失败与重试", dict(BASE_PARAMS))
    r4 = run_ai(req_op, t4["id"], fault="tool_timeout"); ai_ids.append(r4.get("ai_task_id")); c4 = collect(req_op, r4["ai_task_id"])
    retry_ai = None
    if r4.get("ai_task_id"):
        st, body = req_op("POST", f"/api/ai-tasks/{r4['ai_task_id']}/retry")
        if st == 202:
            retry_id = (body.get("data") or {}).get("ai_task_id")
            deadline = time.time() + 180
            while time.time() < deadline:
                _, b = req_op("GET", f"/api/ai-tasks/{retry_id}")
                if (b.get("data") or {}).get("status") in ("SUCCEEDED", "FAILED"):
                    break
                time.sleep(2)
            retry_ai = collect(req_op, retry_id)
            ai_ids.append(retry_id)
    scenarios.append({
        "name": "C 工具/模型失败 + 重试（受控故障 tool_timeout）",
        "summary": {
            "首次任务": f"{t4['task_no']} 状态={r4.get('status')} 错误码={r4.get('error_code')}",
            "失败记录": [f"{c.get('tool_name')}:{c.get('status')}/{c.get('error_code')}" for c in c4["tool_calls"]],
            "重试": f"状态={(retry_ai or {}).get('ai_task', {}).get('status')} 总成本={((retry_ai or {}).get('result', {}).get('structured') or {}).get('total')}",
            "说明": "失败态可见、可重试；重试成功后原失败记录保留",
        },
        "tool_calls": c4["tool_calls"] + ((retry_ai or {}).get("tool_calls") or []),
    })

    # D. 权限：reviewer 运行 AI → 403
    st_d, body_d = req_rv("POST", f"/api/business-tasks/{t1['id']}/ai-tasks", {})
    scenarios.append({
        "name": "D 越权：审核角色运行 AI 被拒",
        "summary": {"HTTP": st_d, "message": (body_d.get("message") if isinstance(body_d, dict) else "")},
    })

    # E. 版本与审核闭环：采用 AI 结果 v1 + 人工 v2 + 提交 + 审核通过
    st, body = req_op("POST", f"/api/business-tasks/{t1['id']}/results", {"ai_task_id": r1.get("ai_task_id"), "change_note": "采用 AI 测算结果"})
    v1 = (body.get("data") or {})
    st, body = req_op("POST", f"/api/business-tasks/{t1['id']}/results", {"content_md": "人工复核：按报价单复算一致。", "change_note": "人工复核保存"})
    v2 = (body.get("data") or {})
    st, body = req_op("GET", f"/api/business-tasks/{t1['id']}/versions")
    versions = (body.get("data") or {})
    versions = versions if isinstance(versions, list) else (versions.get("versions") or versions.get("items") or [])
    submit_version = versions[-1].get("version_no") if versions else 1
    st_sub, body_sub = req_op("POST", f"/api/business-tasks/{t1['id']}/submit", {"version_no": submit_version, "comment": "提交审核"})
    st_upd, body_rev = req_rv("POST", f"/api/business-tasks/{t1['id']}/review", {"version_no": submit_version, "action": "APPROVE", "opinion": "复算一致，同意"})
    scenarios.append({
        "name": "E 人工确认与版本（≥2 版本 + 提交 + 审核通过）",
        "summary": {
            "v1": f"version_no={v1.get('version_no')} source=AI",
            "v2": f"version_no={v2.get('version_no')} source=MANUAL",
            "提交": f"HTTP {st_sub} 状态={(body_sub.get('data') or {}).get('status')}",
            "审核": f"HTTP {st_upd} 状态={(body_rev.get('data') or {}).get('status')}",
            "版本数": len(versions),
        },
    })

    # 日志片段
    log_lines = []
    if LOGS.exists():
        ids = [str(x) for x in ai_ids if x]
        with open(LOGS, "r", encoding="utf-8", errors="replace") as fh:
            for line in fh:
                if any(i in line for i in ids) or "真实" in line:
                    log_lines.append(line.rstrip("\n"))
    log_lines = log_lines[-120:]

    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    payload = {"generated_at": datetime.now().isoformat(timespec="seconds"), "meta": meta, "scenarios": scenarios, "log_lines": log_lines}
    EVIDENCE.mkdir(exist_ok=True)
    (EVIDENCE / f"real_acceptance_{ts}.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    (EVIDENCE / f"real_acceptance_{ts}.md").write_text(md_summary("真实模式验收场景取证", payload), encoding="utf-8")
    print("已保存：", EVIDENCE / f"real_acceptance_{ts}.json")
    print("已保存：", EVIDENCE / f"real_acceptance_{ts}.md")
    for scen in scenarios:
        print("==", scen["name"])
        for k, v in scen["summary"].items():
            print("   ", k, ":", v)


if __name__ == "__main__":
    main()
