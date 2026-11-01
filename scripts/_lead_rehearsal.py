# -*- coding: utf-8 -*-
"""11/6 现场验收彩排：按演示路线真实执行一遍（输入变化/失败重试/依据失效/权限/版本/页面）。

生成 evidence/rehearsal_20261101.json 与 .md；不修改预置演示任务（BT-DEMO-0001 等）。
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
EVID = ROOT / "evidence"
BASE_PARAMS = {"book_title": "《初中语文阅读训练（八年级）》", "format": "16开", "print_sheet": 12,
               "qty": 3000, "paper_type": "胶版纸80g", "binding": "胶装", "color_pages": 4, "process": "覆膜"}
results = []


def client():
    jar = http.cookiejar.CookieJar()

    def req(method, path, payload=None, timeout=240):
        data = json.dumps(payload).encode("utf-8") if payload is not None else None
        request = urllib.request.Request(BASE + path, data=data, headers={"Content-Type": "application/json"}, method=method)
        opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))
        try:
            with opener.open(request, timeout=timeout) as resp:
                return resp.status, json.loads(resp.read().decode("utf-8", "replace") or "{}")
        except urllib.error.HTTPError as exc:
            raw = exc.read().decode("utf-8", "replace")
            try:
                return exc.code, json.loads(raw)
            except Exception:
                return exc.code, {"raw": raw}

    return req


def login(req, user):
    st, body = req("POST", "/api/auth/login", {"username": user, "password": "123456"})
    assert st == 200 and body.get("code") == 0, f"login failed {user}: {st} {body}"
    return body["data"]["user"]


def new_task(req, title, params):
    st, body = req("POST", "/api/business-tasks", {"business_type": "print_cost", "title": title, "goal": title, "params": params})
    assert st in (200, 201) and body.get("code") == 0, (st, body)
    return body["data"]


def run_ai(req, task_id, fault=None):
    st, body = req("POST", f"/api/business-tasks/{task_id}/ai-tasks", {"fault": fault} if fault else {})
    if st != 202:
        return {"status": "CREATE_FAILED", "http": st, "body": body}
    ai_id = body["data"]["ai_task_id"]
    deadline = time.time() + 240
    status = None
    while time.time() < deadline:
        st, b = req("GET", f"/api/ai-tasks/{ai_id}")
        status = (b.get("data") or {}).get("status")
        if status in ("SUCCEEDED", "FAILED"):
            break
        time.sleep(2)
    st, b = req("GET", f"/api/ai-tasks/{ai_id}")
    ai = b.get("data") or {}
    st, b = req("GET", f"/api/ai-tasks/{ai_id}/tool-calls")
    calls = (b.get("data") or {}).get("records") or []
    st, b = req("GET", f"/api/ai-tasks/{ai_id}/result")
    res = b.get("data") or {}
    return {"ai_task_id": ai_id, "status": status, "error_code": ai.get("error_code"),
            "counters": ai.get("counters"), "tool_calls": calls, "result": res}


def add(step, detail, ok):
    results.append({"step": step, "detail": detail, "ok": bool(ok)})
    print(("PASS " if ok else "FAIL ") + step + " :: " + str(detail)[:220])


def main():
    op = client()
    rv = client()
    login(op, "operator")
    login(rv, "reviewer")
    st, body = op("GET", "/api/system/meta")
    meta = body.get("data") or {}

    # 1 输入条件变化
    t1 = new_task(op, "彩排-输入变化", dict(BASE_PARAMS))
    r1 = run_ai(op, t1["id"])
    s1 = (r1["result"].get("structured") or {})
    p2 = dict(BASE_PARAMS); p2.update({"qty": 5000, "print_sheet": 14})
    st, body = op("PUT", f"/api/business-tasks/{t1['id']}", {"params": p2, "expected_revision": 1})
    r2 = run_ai(op, t1["id"])
    s2 = (r2["result"].get("structured") or {})
    add("输入条件变化", f"基线 total={s1.get('total')} per={s1.get('per_copy')} → 改参数 total={s2.get('total')} per={s2.get('per_copy')}",
        r1["status"] == "SUCCEEDED" and r2["status"] == "SUCCEEDED" and s1.get("total") != s2.get("total"))

    # 2 版本（采用 AI + 人工）
    st, v1 = op("POST", f"/api/business-tasks/{t1['id']}/results", {"ai_task_id": r2["ai_task_id"], "change_note": "彩排：采用 AI 结果"})
    st, v2 = op("POST", f"/api/business-tasks/{t1['id']}/results", {"content_md": "彩排：人工复核一致", "change_note": "彩排：人工版本"})
    st, body = op("GET", f"/api/business-tasks/{t1['id']}/versions")
    data = body.get("data") or {}
    versions = data if isinstance(data, list) else (data.get("versions") or data.get("items") or [])
    add("人工确认与版本", f"版本数={len(versions)} sources={[v.get('source') for v in versions]}", len(versions) >= 2)

    # 3 失败重试
    t3 = new_task(op, "彩排-失败重试", dict(BASE_PARAMS))
    r3 = run_ai(op, t3["id"], fault="tool_timeout")
    retry_ok = False
    retry_status = None
    if r3.get("ai_task_id"):
        st, body = op("POST", f"/api/ai-tasks/{r3['ai_task_id']}/retry")
        if st == 202:
            rid = (body.get("data") or {}).get("ai_task_id")
            for _ in range(120):
                st, b = op("GET", f"/api/ai-tasks/{rid}")
                retry_status = (b.get("data") or {}).get("status")
                if retry_status in ("SUCCEEDED", "FAILED"):
                    break
                time.sleep(2)
            retry_ok = retry_status == "SUCCEEDED"
    add("工具失败 + 重试", f"首次={r3['status']}/{r3.get('error_code')}；重试={retry_status}",
        r3["status"] == "FAILED" and r3.get("error_code") == "TOOL_TIMEOUT" and retry_ok)

    # 4 依据失效（T06）
    t6 = new_task(op, "彩排-依据失效", dict(BASE_PARAMS))
    r6 = run_ai(op, t6["id"])
    st, body = op("POST", f"/api/business-tasks/{t6['id']}/results", {"ai_task_id": r6["ai_task_id"], "change_note": "彩排：AI 版本"})
    vno = (body.get("data") or {}).get("version_no") or 1
    st, mats = op("GET", "/api/materials?type=paper")
    paper_items = (mats.get("data") or {}).get("items") or []
    target = [m for m in paper_items if "胶版纸80g" in str(m.get("title") or "")]
    mid = (target[0] if target else (paper_items[0] if paper_items else {})).get("id")
    st_dis, _ = op("PATCH", f"/api/materials/{mid}", {"enabled": False}) if mid else (0, {})
    st_sub, sub = op("POST", f"/api/business-tasks/{t6['id']}/submit", {"version_no": vno, "comment": "彩排：依据失效提交"})
    if mid:
        op("PATCH", f"/api/materials/{mid}", {"enabled": True})
    add("依据失效（T06）", f"停用 HTTP={st_dis}；提交 HTTP={st_sub}；返回={str(sub.get('message'))[:100]}",
        st_dis == 200 and st_sub == 409)

    # 5 权限
    st_a, _ = rv("POST", f"/api/business-tasks/{t1['id']}/ai-tasks", {})
    st_b, _ = rv("POST", "/api/business-tasks", {"business_type": "print_cost", "title": "彩排-越权", "goal": "x", "params": BASE_PARAMS})
    add("越权拒绝", f"reviewer 运行AI={st_a}；创建任务={st_b}", st_a == 403 and st_b == 403)

    # 6 页面可达性（HTML 页面不按 JSON 解析）
    def page_status(path):
        try:
            with urllib.request.urlopen(BASE + path, timeout=20) as resp:
                return resp.status
        except urllib.error.HTTPError as exc:
            return exc.code
        except Exception:
            return 0

    pages_ok = True
    page_detail = []
    for path in ["/tasks", f"/tasks/{t1['id']}", "/materials", "/rules", "/system", "/diagrams"]:
        st = page_status(path)
        page_detail.append(f"{path}={st}")
        pages_ok = pages_ok and st == 200
    add("页面可达性", "；".join(page_detail), pages_ok)

    # 汇总证据
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    payload = {"generated_at": datetime.now().isoformat(timespec="seconds"), "meta": meta, "steps": results}
    EVID.mkdir(exist_ok=True)
    (EVID / f"rehearsal_{ts}.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    lines = ["# 现场验收彩排记录（真实模式）", "", f"- 时间：{payload['generated_at']}",
             f"- 环境：LLM_MODE={meta.get('llm_mode')}｜模型={meta.get('model')}｜{BASE}", "", "| 步骤 | 实际结果 | 判定 |", "|---|---|---|"]
    for r in results:
        lines.append(f"| {r['step']} | {r['detail']} | {'通过' if r['ok'] else '失败'} |")
    (EVID / f"rehearsal_{ts}.md").write_text("\n".join(lines), encoding="utf-8")
    passed = sum(1 for r in results if r["ok"])
    print(f"\n===== 彩排 {passed}/{len(results)} 通过；证据：{EVID / ('rehearsal_' + ts + '.md')} =====")
    if passed != len(results):
        sys.exit(1)


if __name__ == "__main__":
    main()
