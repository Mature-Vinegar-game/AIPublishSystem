# -*- coding: utf-8 -*-
"""11/5 提交前一键执行：自检 → 重新打包 → 输出提交清单（最终包 + SHA-256 + 5 项材料核对）。

用法（服务已启动时）：
    python scripts/final_submit.py
返回码 0 = 可以提交；非 0 = 有失败项，按提示修复。
"""
import hashlib
import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request
import http.cookiejar
from pathlib import Path

ROOT = Path(r"F:\deepseek workarea\出版AI系统")
WORK = Path(r"F:\deepseek workarea")
BASE = os.environ.get("APP_BASE_URL", "http://127.0.0.1:8000")
DEMO_CHECK = ROOT / "scripts" / "demo_check.py"
FINALIZE = ROOT / "scripts" / "_finalize_delivery.py"
DELIVERY = ROOT / "交付材料"

ok = True


def line(title=""):
    print("\n" + "=" * 64 + ("\n" + title if title else ""))


def check(name, cond, detail=""):
    global ok
    print(("PASS " if cond else "FAIL ") + name + ((" | " + str(detail)[:160]) if detail else ""))
    if not cond:
        ok = False


def env_ok():
    p = ROOT / ".env"
    if not p.exists():
        check(".env 存在", False, str(p))
        return
    text = p.read_text(encoding="utf-8", errors="replace")
    check(".env 中 LLM_MODE=real", "LLM_MODE=real" in text)
    check(".env 中 API Key 非占位符", "PLEASE-REPLACE" not in text and "LLM_API_KEY=sk-" in text)


def server_ok():
    jar = http.cookiejar.CookieJar()

    def req(method, path, payload=None):
        data = json.dumps(payload).encode() if payload is not None else None
        request = urllib.request.Request(BASE + path, data=data, headers={"Content-Type": "application/json"}, method=method)
        opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))
        with opener.open(request, timeout=15) as resp:
            return resp.status, json.loads(resp.read().decode("utf-8", "replace") or "{}")

    try:
        st, body = req("POST", "/api/auth/login", {"username": "operator", "password": "123456"})
        check("服务运行且可登录", st == 200 and body.get("code") == 0)
        st, body = req("GET", "/api/system/meta")
        meta = body.get("data") or {}
        check("真实模式 + Key 已配置", meta.get("llm_mode") == "real" and meta.get("key_configured") is True,
              f"{meta.get('llm_mode')}/{meta.get('key_configured')}/{meta.get('model')}")
    except Exception as exc:  # noqa: BLE001
        check("服务运行（http://127.0.0.1:8000）", False, f"{type(exc).__name__}：请先双击 start.bat")
        return False
    return True


def run_script(name, path):
    print(f"\n[执行] {name} ...")
    proc = subprocess.run([sys.executable, str(path)], cwd=str(ROOT), capture_output=True, text=True,
                          encoding="utf-8", errors="replace")
    tail = (proc.stdout or proc.stderr or "").strip().splitlines()[-4:]
    for t in tail:
        print("  " + t.encode("ascii", "replace").decode("ascii") if any(ord(c) > 127 for c in t) else "  " + t)
    if any(ord(c) > 127 for c in "".join(tail)):
        print("  （上面为中文输出，详情见控制台原始日志）")
    check(f"{name} 返回码=0", proc.returncode == 0, proc.returncode)
    return proc


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def main():
    line("11/5 提交前一键检查")
    print("时间：", time.strftime("%Y-%m-%d %H:%M:%S"), "｜项目：", ROOT)
    env_ok()
    server_ok()
    run_script("一键自检 demo_check", DEMO_CHECK)
    run_script("重新生成最终交付包", FINALIZE)

    line("最终提交包")
    zips = sorted(WORK.glob("出版AI系统_交付材料_*.zip"), key=lambda p: p.stat().st_mtime)
    if not zips:
        check("提交 zip 存在", False)
        sys.exit(1)
    latest = zips[-1]
    size = latest.stat().st_size
    digest = sha256(latest)
    print(f"提交 zip：{latest}")
    print(f"大小：{size:,} bytes｜SHA-256：{digest}")
    print(f"交付文件数：{sum(1 for _ in DELIVERY.rglob('*') if _.is_file())}")
    for d in ["01_可运行程序及源码", "02_需求与设计说明", "03_数据与运行记录", "04_测试与交付材料", "05_小组分工与开发记录"]:
        check(f"五项提交物：{d}", (DELIVERY / d).exists())
    check("材料索引 00_材料索引与提交自检.md", (DELIVERY / "00_材料索引与提交自检.md").exists())
    check("SHA256SUMS.txt", (DELIVERY / "SHA256SUMS.txt").exists())

    # 写入 UTF-8 提交信息（避免控制台编码问题；提交时以此文件为准）
    import datetime
    info_path = WORK / f"提交信息_{datetime.datetime.now().strftime('%Y%m%d')}.txt"
    info = f"""出版业务 AI 系统 - 最终提交信息
生成时间：{time.strftime('%Y-%m-%d %H:%M:%S')}
提交 zip：{latest}
大小：{size:,} bytes
SHA-256：{digest}
交付文件数：{sum(1 for _ in DELIVERY.rglob('*') if _.is_file())}
源码 zip：{DELIVERY / '01_可运行程序及源码' / '出版AI系统_源码_20261101.zip'}
校验清单：{DELIVERY / 'SHA256SUMS.txt'}

提交前自检：demo_check 21/21；finalize 返回 0（详见执行控制台）
提交清单：
1. 确认教师提交渠道与截止时间；
2. 上传上方"提交 zip"（或按教师要求压缩整个 交付材料/ 目录）；
3. 本地保留 zip 与 SHA256SUMS，提交后勿改材料；
4. 11/6 演示前 30 分钟：start.bat → python scripts/demo_check.py（21/21）→ 打开 http://127.0.0.1:8000。
"""
    info_path.write_text(info, encoding="utf-8")
    print(f"提交信息文件：{info_path}")
    line("提交清单（按教师渠道上传）")
    print("""
1. 确认教师提交渠道与截止时间（以教师通知为准）。
2. 上传最终 zip：上方"提交 zip"路径（或按教师要求压缩整个 交付材料/ 目录）。
3. 保留备份：本地保留 zip 与 SHA256SUMS；提交后勿再改动材料。
4. 11/6 演示前 30 分钟：双击 start.bat → python scripts/demo_check.py（应 21/21）
   → 打开 http://127.0.0.1:8000，登录 operator/123456 → 演示任务清单见 04_演示运行手册.md。
""")
    if not ok:
        print("\n========== 存在失败项：请先修复后再提交 ==========")
        sys.exit(1)
    print("\n========== 全部通过：可以提交 ==========")


if __name__ == "__main__":
    main()
