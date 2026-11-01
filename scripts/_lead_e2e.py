# -*- coding: utf-8 -*-
"""Lead 端到端冒烟脚本（Mock 模式）：HTTP 走本地 8000 端口，验证主链路与异常场景。"""
import json
import sys
import time
import urllib.request
import urllib.error
import http.cookiejar

BASE = "http://127.0.0.1:8000"
results = []


def req(method, path, payload=None, jar=None, headers=None):
    url = BASE + path
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    h = {"Content-Type": "application/json", "Accept": "application/json"}
    if headers:
        h.update(headers)
    r = urllib.request.Request(url, data=data, headers=h, method=method)
    opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar)) if jar is not None else urllib.request.build_opener()
    try:
        with opener.open(r, timeout=120) as resp:
            raw = resp.read().decode("utf-8", "replace")
            status = resp.status
    except urllib.error.HTTPError as e:
        raw = e.read().decode("utf-8", "replace")
        status = e.code
    try:
        body = json.loads(raw) if raw else None
    except Exception:
        body = raw[:300]
    return status, body


def check(name, ok, detail=""):
    results.append((name, ok, detail))
    print(("PASS " if ok else "FAIL ") + name + (" :: " + str(detail)[:300] if detail else ""))


def page(path, jar=None, status_expect=200):
    url = BASE + path
    opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar)) if jar is not None else urllib.request.build_opener()
    try:
        with opener.open(urllib.request.Request(url), timeout=30) as resp:
            html = resp.read().decode("utf-8", "replace")
            return resp.status, html
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", "replace")


def main():
    op = http.cookiejar.CookieJar()
    rv = http.cookiejar.CookieJar()

    # 0. 页面可达性
    for p in ["/login"]:
        st, html = page(p, op)
        check(f"GET {p}", st == 200 and "登录" in html, st)

    # 1. 登录
    st, b = req("POST", "/api/auth/login", {"username": "operator", "password": "123456"}, op)
    token = (b or {}).get("data", {}).get("token") if isinstance(b, dict) else None
    check("operator 登录", st == 200 and b.get("code") == 0 and token, (st, str(b)[:200]))
    st, b = req("POST", "/api/auth/login", {"username": "reviewer", "password": "123456"}, rv)
    check("reviewer 登录", st == 200 and b.get("code") == 0, (st, str(b)[:200]))

    # 2. system meta
    st, b = req("GET", "/api/system/meta", jar=op)
    meta = ((b or {}).get("data") or {}) if isinstance(b, dict) else {}
    check("system meta", st == 200 and meta.get("llm_mode") == "mock", (st, meta.get("llm_mode"), meta.get("limits"), str(b)[:200]))

    # 3. 建任务（基线参数）
    params = {"book_title": "印制成本测算样书", "format": "16开", "print_sheet": 12, "qty": 3000,
              "paper_type": "铜版纸157g", "binding": "胶装", "color_pages": 4, "process": "覆膜"}
    st, b = req("POST", "/api/business-tasks", {"business_type": "print_cost", "title": "E2E 基线测算",
                                                "goal": "验证端到端主链路", "params": params}, op)
    d = (b or {}).get("data", {}) if isinstance(b, dict) else {}
    tid = d.get("id")
    check("创建任务", st in (200, 201) and b.get("code") == 0 and tid, (st, str(b)[:300]))
    if not tid:
        summary()
        return

    # 4. 运行 AI（mock）
    st, b = req("POST", f"/api/business-tasks/{tid}/ai-tasks", {}, op)
    aid = ((b or {}).get("data") or {}).get("ai_task_id")
    check("创建 AI 任务", st == 202 and aid, (st, str(b)[:300]))
    if aid:
        for _ in range(60):
            st, b = req("GET", f"/api/ai-tasks/{aid}", jar=op)
            status = ((b or {}).get("data") or {}).get("status")
            if status in ("SUCCEEDED", "FAILED"):
                break
            time.sleep(1)
        check("AI 任务终态", status == "SUCCEEDED", (status, str(b)[:300]))
        st, b = req("GET", f"/api/ai-tasks/{aid}/tool-calls", jar=op)
        data_tc = (b or {}).get("data") or {}
        recs = data_tc if isinstance(data_tc, list) else (data_tc.get("records") or data_tc.get("items") or [])
        types = sorted({r.get("tool_type") for r in recs if isinstance(r, dict)})
        check("执行记录≥2类工具", st == 200 and len(recs) >= 2 and len(types) >= 2, (len(recs), types))
        st, b = req("GET", f"/api/ai-tasks/{aid}/result", jar=op)
        res = ((b or {}).get("data") or {}) if isinstance(b, dict) else {}
        check("AI 结果与依据", st == 200 and res.get("content_md") and res.get("basis_refs"),
              (st, str(res)[:300]))
        print("      结果摘要:", str(res.get("content_md", ""))[:120].replace("\n", " "))
        print("      结构化:", str(res.get("structured", ""))[:200])

    # 5. 采用 AI 结果 -> v1，保存人工版本 -> v2，提交 -> reviewer 审核
    st, b = req("POST", f"/api/business-tasks/{tid}/results",
                {"ai_task_id": aid, "change_note": "采用 AI 结果"}, op)
    check("采用 AI 结果生成版本", st in (200, 201) and b.get("code") == 0, (st, str(b)[:300]))
    st, b = req("POST", f"/api/business-tasks/{tid}/results",
                {"content_md": "人工复核：按报价复算一致。", "change_note": "人工保存"}, op)
    check("保存人工版本", st in (200, 201) and b.get("code") == 0, (st, str(b)[:300]))
    st, b = req("GET", f"/api/business-tasks/{tid}/versions", jar=op)
    data_v = (b or {}).get("data") or {}
    versions = data_v if isinstance(data_v, list) else (data_v.get("versions") or data_v.get("items") or [])
    check("版本≥2", st == 200 and len(versions) >= 2, (st, len(versions)))
    vno = versions[-1].get("version_no") if versions else 1
    st, b = req("POST", f"/api/business-tasks/{tid}/submit", {"version_no": vno, "comment": "提交审核"}, op)
    check("提交审核", st == 200 and b.get("code") == 0, (st, str(b)[:300]))
    st, b = req("POST", f"/api/business-tasks/{tid}/review",
                {"version_no": vno, "action": "APPROVE", "opinion": "同意"}, rv)
    check("reviewer 审核通过", st == 200 and b.get("code") == 0, (st, str(b)[:300]))

    # 6. 越权：reviewer 运行 AI 应 403
    st, b = req("POST", f"/api/business-tasks/{tid}/ai-tasks", {}, rv)
    check("越权 reviewer 运行 AI -> 403", st == 403, (st, str(b)[:200]))

    # 7. 异常场景：数据不足（纸张不存在）
    p2 = dict(params); p2["paper_type"] = "不存在的纸张XY"
    st, b = req("POST", "/api/business-tasks", {"business_type": "print_cost", "title": "E2E 数据不足",
                                                "goal": "验证数据不足", "params": p2}, op)
    tid2 = ((b or {}).get("data") or {}).get("id")
    check("创建数据不足任务", st in (200, 201) and tid2, (st, str(b)[:200]))
    if tid2:
        st, b = req("POST", f"/api/business-tasks/{tid2}/ai-tasks", {}, op)
        aid2 = ((b or {}).get("data") or {}).get("ai_task_id")
        if aid2:
            for _ in range(60):
                st, b = req("GET", f"/api/ai-tasks/{aid2}", jar=op)
                s2 = ((b or {}).get("data") or {}).get("status")
                if s2 in ("SUCCEEDED", "FAILED"):
                    break
                time.sleep(1)
            st, b = req("GET", f"/api/ai-tasks/{aid2}/result", jar=op)
            res2 = ((b or {}).get("data") or {}) if isinstance(b, dict) else {}
            check("数据不足不虚构（missing_data）", bool(res2.get("missing_data")) or s2 == "FAILED",
                  (s2, str(res2.get("missing_data"))[:200]))

    # 8. 故障注入：在新建 PENDING 任务上测试三种 fault
    st, b = req("POST", "/api/business-tasks", {"business_type": "print_cost", "title": "E2E 故障注入",
                                                "goal": "验证受控故障", "params": params}, op)
    tidf = ((b or {}).get("data") or {}).get("id")
    check("创建故障注入任务", st in (200, 201) and tidf, (st, str(b)[:200]))
    for fault in ["invalid_tool_args", "tool_timeout", "model_error"]:
        st, b = req("POST", f"/api/business-tasks/{tidf}/ai-tasks", {"fault": fault}, op)
        aidf = ((b or {}).get("data") or {}).get("ai_task_id")
        ok = st == 202 and bool(aidf)
        statusf = None
        if aidf:
            for _ in range(60):
                st2, b2 = req("GET", f"/api/ai-tasks/{aidf}", jar=op)
                statusf = ((b2 or {}).get("data") or {}).get("status")
                if statusf in ("SUCCEEDED", "FAILED"):
                    break
                time.sleep(1)
        check(f"故障注入 {fault}", ok and statusf == "FAILED", (st, statusf, str(b2)[:150] if aidf else ""))

    # 9. 页面
    for p, kw in [("/tasks", "任务"), ("/tasks/new", "新建"), (f"/tasks/{tid}", "执行记录"),
                  ("/materials", "资料"), ("/rules", "规则")]:
        st, html = page(p, op)
        check(f"页面 GET {p}", st == 200 and kw in html, (st, len(html)))

    summary()


def summary():
    total = len(results)
    passed = sum(1 for _, ok, _ in results if ok)
    print(f"\n===== 汇总: {passed}/{total} 通过 =====")
    if passed != total:
        print("失败项：")
        for name, ok, detail in results:
            if not ok:
                print(" -", name, "::", str(detail)[:400])
        sys.exit(1)


if __name__ == "__main__":
    main()
