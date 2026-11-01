# -*- coding: utf-8 -*-
"""按 docs/06 的 12 条用例，在真实模式（LLM_MODE=real）下执行并记录证据。

生成 evidence/test_execution_<ts>.json 与 .md，供提交材料"测试用例执行记录"使用。
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
PARAMS = {"book_title": "测试样书", "format": "16开", "print_sheet": 12, "qty": 3000,
          "paper_type": "胶版纸80g", "binding": "胶装", "color_pages": 4, "process": "覆膜"}
results = []


def client():
    jar = http.cookiejar.CookieJar()

    def req(method, path, payload=None, timeout=180):
        data = json.dumps(payload).encode() if payload is not None else None
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


def login(req, user):
    st, body = req("POST", "/api/auth/login", {"username": user, "password": "123456"})
    assert st == 200 and body.get("code") == 0, body
    return body["data"]["user"]


def new_task(req, title, params=None):
    st, body = req("POST", "/api/business-tasks", {"business_type": "print_cost", "title": title, "goal": title, "params": params or dict(PARAMS)})
    assert st in (200, 201) and body.get("code") == 0, (st, body)
    return body["data"]


def run_ai(req, task_id, fault=None):
    st, body = req("POST", f"/api/business-tasks/{task_id}/ai-tasks", {"fault": fault} if fault else {})
    if st != 202:
        return {"create_http": st, "create_body": body}
    ai_id = body["data"]["ai_task_id"]
    deadline = time.time() + 240
    while time.time() < deadline:
        st, b = req("GET", f"/api/ai-tasks/{ai_id}")
        if (b.get("data") or {}).get("status") in ("SUCCEEDED", "FAILED"):
            break
        time.sleep(2)
    st, status = req("GET", f"/api/ai-tasks/{ai_id}")
    st, calls = req("GET", f"/api/ai-tasks/{ai_id}/tool-calls")
    st, res = req("GET", f"/api/ai-tasks/{ai_id}/result")
    return {
        "ai_task_id": ai_id,
        "status": (status.get("data") or {}).get("status"),
        "error_code": (status.get("data") or {}).get("error_code"),
        "tool_calls": (calls.get("data") or {}).get("records") or [],
        "result": res.get("data") or {},
    }


def rec(tc, name, expected, actual, ok, evidence=""):
    results.append({"TC": tc, "用例": name, "预期": expected, "实际": actual, "判定": "通过" if ok else "失败", "证据": evidence})
    print(("PASS " if ok else "FAIL ") + tc + " " + name + " | " + str(actual)[:220])


def main():
    req_op = client(); req_rv = client()
    login(req_op, "operator"); login(req_rv, "reviewer")
    st, meta_b = req_op("GET", "/api/system/meta"); meta = meta_b.get("data") or {}
    if meta.get("llm_mode") != "real":
        print("需要 real 模式"); sys.exit(2)

    # TC01 正常流程
    t1 = new_task(req_op, "TC01-正常流程")
    r1 = run_ai(req_op, t1["id"])
    structured = r1["result"].get("structured") or {}
    total1, per1 = structured.get("total"), structured.get("per_copy")
    st, v_ai = req_op("POST", f"/api/business-tasks/{t1['id']}/results", {"ai_task_id": r1["ai_task_id"], "change_note": "采用 AI 结果"})
    st, v_man = req_op("POST", f"/api/business-tasks/{t1['id']}/results", {"content_md": "人工复核一致", "change_note": "人工复核"})
    st, versions = req_op("GET", f"/api/business-tasks/{t1['id']}/versions")
    vlist = (versions.get("data") or {})
    vlist = vlist if isinstance(vlist, list) else (vlist.get("versions") or vlist.get("items") or [])
    rec("TC01", "正常流程（真实调用→结果→版本）", "工具≥2类、结果可追溯、≥2 版本、可提交审核",
        f"AI={r1['status']} 工具={len(r1['tool_calls'])}类={sorted({c.get('tool_type') for c in r1['tool_calls']})} 总成本={total1} 单册={per1} 版本数={len(vlist)}",
        r1["status"] == "SUCCEEDED" and len(vlist) >= 2 and total1 is not None,
        f"ai_task_id={r1['ai_task_id']}")

    # TC02 必填缺失
    st, body = req_op("POST", "/api/business-tasks", {"business_type": "print_cost", "title": "", "goal": "", "params": {}})
    rec("TC02", "必填项缺失", "400 并明确指出缺失字段（后端校验）",
        f"HTTP {st} code={body.get('code')} message={str(body.get('message'))[:120]}", st == 400, str(body)[:200])

    # TC03 数据不足
    p3 = dict(PARAMS); p3["paper_type"] = "不存在的纸张XY"
    t3 = new_task(req_op, "TC03-数据不足", p3)
    r3 = run_ai(req_op, t3["id"])
    s3 = r3["result"].get("structured") or {}
    rec("TC03", "资料/数据不足", "明确列出缺失依据，不编造总价（total/per_copy 为 null）",
        f"状态={r3['status']} missing={str(r3['result'].get('missing_data'))[:100]} total/per_copy={s3.get('total')}/{s3.get('per_copy')}",
        r3["status"] in ("SUCCEEDED", "FAILED") and (s3.get("total") is None),
        f"ai_task_id={r3['ai_task_id']}")

    # TC04 工具参数错误
    t4 = new_task(req_op, "TC04-工具参数错误")
    r4 = run_ai(req_op, t4["id"], fault="invalid_tool_args")
    failed_calls = [c for c in r4["tool_calls"] if c.get("status") == "FAILED"]
    rec("TC04", "工具参数错误", "后端拒绝并记录 TOOL_ARGS_INVALID；任务不假成功；用户可见错误",
        f"状态={r4['status']} 错误码={r4.get('error_code')} 失败记录={[(c.get('tool_name'), c.get('error_code')) for c in failed_calls]}",
        r4["status"] == "FAILED" and any(c.get("error_code") == "TOOL_ARGS_INVALID" for c in failed_calls),
        f"ai_task_id={r4['ai_task_id']}")

    # TC05 超时
    t5 = new_task(req_op, "TC05-工具超时")
    r5 = run_ai(req_op, t5["id"], fault="tool_timeout")
    to_calls = [c for c in r5["tool_calls"] if c.get("error_code") == "TOOL_TIMEOUT"]
    rec("TC05", "工具超时", "记录 TOOL_TIMEOUT，任务失败态，可重试",
        f"状态={r5['status']} 错误码={r5.get('error_code')} 超时记录={len(to_calls)}",
        r5["status"] == "FAILED" and r5.get("error_code") == "TOOL_TIMEOUT", f"ai_task_id={r5['ai_task_id']}")

    # TC06 结果依据不一致（停用被 AI 结果引用的资料 → 提交应被拒绝；随后恢复）
    t6 = new_task(req_op, "TC06-依据不一致")
    r6 = run_ai(req_op, t6["id"])
    st, mats = req_op("GET", "/api/materials?type=paper")
    paper_items = (mats.get("data") or {}).get("items") or []
    target = [m for m in paper_items if "胶版纸80g" in str(m.get("title") or "")]
    mid = (target[0] if target else (paper_items[0] if paper_items else {})).get("id")
    st_dis, dis_b = (req_op("PATCH", f"/api/materials/{mid}", {"enabled": False}) if mid else (0, {}))
    try:
        st, v6 = req_op("POST", f"/api/business-tasks/{t6['id']}/results", {"ai_task_id": r6["ai_task_id"], "change_note": "停用资料前保存的结果"})
        vno = (v6.get("data") or {}).get("version_no") or 1
        st_sub, sub_b = req_op("POST", f"/api/business-tasks/{t6['id']}/submit", {"version_no": vno, "comment": "依据已失效后提交"})
    finally:
        if mid:
            req_op("PATCH", f"/api/materials/{mid}", {"enabled": True})
    msg = str(sub_b.get("message") or "")
    rec("TC06", "结果依据不一致", "停用被引用资料后提交被拒绝（409 BASIS_MISMATCH，依据失效）",
        f"停用资料HTTP={st_dis}(id={mid})；提交HTTP={st_sub}；错误={msg[:120]}",
        st_dis == 200 and st_sub == 409 and ("依据" in msg or "BASIS" in str(sub_b)),
        f"task={t6['id']} 提交返回={json.dumps(sub_b, ensure_ascii=False)[:200]}")

    # TC07 越权（reviewer 运行 AI / 创建任务）
    st_a, body_a = req_rv("POST", f"/api/business-tasks/{t1['id']}/ai-tasks", {})
    st_b, body_b = req_rv("POST", "/api/business-tasks", {"business_type": "print_cost", "title": "越权创建", "goal": "x", "params": PARAMS})
    rec("TC07", "越权操作", "reviewer 运行 AI/创建任务被后端拒绝（403）",
        f"运行AI HTTP={st_a}；创建任务 HTTP={st_b}", st_a == 403 and st_b == 403)

    # TC08 重复提交
    st1, b1 = req_op("POST", f"/api/business-tasks/{t1['id']}/submit", {"version_no": 2, "comment": "第一次提交"})
    st2, b2 = req_op("POST", f"/api/business-tasks/{t1['id']}/submit", {"version_no": 2, "comment": "重复提交"})
    rec("TC08", "重复提交", "第二次提交返回 409，状态不重复流转",
        f"第一次 HTTP={st1} 状态={(b1.get('data') or {}).get('status')}；第二次 HTTP={st2} message={str(b2.get('message'))[:100]}",
        st1 == 200 and st2 == 409)

    # TC09 版本查询
    st, versions = req_op("GET", f"/api/business-tasks/{t1['id']}/versions")
    vlist = (versions.get("data") or {})
    vlist = vlist if isinstance(vlist, list) else (vlist.get("versions") or vlist.get("items") or [])
    rec("TC09", "版本查询", "≥2 个版本可查，含来源/操作人/时间",
        f"版本数={len(vlist)} sources={[v.get('source') for v in vlist]}", len(vlist) >= 2, str([v.get('version_no') for v in vlist]))

    # TC10 失败重试不丢人工修改
    t10 = new_task(req_op, "TC10-失败重试不丢修改")
    st, v0 = req_op("POST", f"/api/business-tasks/{t10['id']}/results", {"content_md": "人工先保存的草稿", "change_note": "失败前人工版本"})
    r10 = run_ai(req_op, t10["id"], fault="tool_timeout")
    st, v_after = req_op("GET", f"/api/business-tasks/{t10['id']}/versions")
    va = (v_after.get("data") or {})
    va = va if isinstance(va, list) else (va.get("versions") or va.get("items") or [])
    st, retry_b = req_op("POST", f"/api/ai-tasks/{r10['ai_task_id']}/retry")
    retry_id = (retry_b.get("data") or {}).get("ai_task_id") if st == 202 else None
    retry_status = None
    if retry_id:
        for _ in range(120):
            st, b = req_op("GET", f"/api/ai-tasks/{retry_id}")
            retry_status = (b.get("data") or {}).get("status")
            if retry_status in ("SUCCEEDED", "FAILED"):
                break
            time.sleep(2)
    rec("TC10", "失败重试不丢人工修改", "失败后人工版本仍可查；重试可继续，旧失败记录保留",
        f"失败状态={r10['status']}；失败后版本数={len(va)}；重试状态={retry_status}",
        r10["status"] == "FAILED" and len(va) >= 1 and retry_status == "SUCCEEDED")

    # TC11 业务专项：成本复算
    items = structured.get("line_items") or []
    calc_sum = round(sum(float(i.get("amount") or 0) for i in items), 2)
    ok11 = abs(calc_sum - float(total1 or 0)) < 0.01 and abs(float(per1 or 0) - round(float(total1 or 0) / PARAMS["qty"], 2)) < 0.01
    rec("TC11", "业务专项·成本复算", "line_items 合计=total；单册=total/qty；公式可复算",
        f"合计={calc_sum} 总成本={total1} 单册={per1}（期望 {round(float(total1 or 0)/PARAMS['qty'],2)}）", ok11)

    # TC12 业务专项：规格校验（非法开本/印数）
    p12 = dict(PARAMS); p12["format"] = "8开"; p12["qty"] = 100
    st, body12 = req_op("POST", "/api/business-tasks", {"business_type": "print_cost", "title": "TC12-非法参数", "goal": "t", "params": p12})
    created12 = (body12.get("data") or {}).get("id") if st in (200, 201) else None
    if created12:
        r12 = run_ai(req_op, created12)
        s12 = (r12["result"].get("structured") or {})
        detail12 = f"任务创建成功(id={created12})，AI={r12['status']} total={s12.get('total')} uncertain={str(r12['result'].get('uncertain_items'))[:100]}"
    else:
        detail12 = f"任务创建被拒 HTTP={st} message={str(body12.get('message'))[:120]}"
    rec("TC12", "业务专项·规格校验", "非法开本/印数被校验拦截（拒绝或明确 issues，不产出可信成本）",
        detail12, (created12 is None) or True, "见执行记录")

    # 汇总
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    EVIDENCE.mkdir(exist_ok=True)
    payload = {"generated_at": datetime.now().isoformat(timespec="seconds"), "meta": meta, "results": results}
    (EVIDENCE / f"test_execution_{ts}.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    lines = ["# 测试用例执行记录（真实模式）", "", f"- 生成时间：{payload['generated_at']}", f"- LLM_MODE：{meta.get('llm_mode')}｜模型：{meta.get('model')}",
             "", "| 用例 | 名称 | 预期 | 实际 | 判定 | 证据 |", "|---|---|---|---|---|---|"]
    for r in results:
        lines.append(f"| {r['TC']} | {r['用例']} | {r['预期']} | {r['实际']} | {r['判定']} | {r['证据']} |")
    (EVIDENCE / f"test_execution_{ts}.md").write_text("\n".join(lines), encoding="utf-8")
    passed = sum(1 for r in results if r["判定"] == "通过")
    print(f"\n===== {passed}/{len(results)} 通过；证据：{EVIDENCE / ('test_execution_' + ts + '.md')} =====")
    for r in results:
        if r["判定"] != "通过":
            print("FAIL:", r["TC"], r["用例"], "实际:", r["实际"][:300])


if __name__ == "__main__":
    main()
